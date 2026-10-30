#!/usr/bin/env python3
"""Per-community SQLite storage for the static collection and push pipeline.

Each community has its own database. A small manifest commits a complete batch
only after all 49 writes succeed. The JSON export is a disposable static-site
view, never the authoritative database. No network requests are made here.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid

import community_data as cd

DEFAULT_DIRECTORY = 'community_store'
MANIFEST_NAME = 'manifest.json'
STORE_VERSION = 1
HISTORY_LIMIT = 42  # about seven days at a nominal four-hour cadence


class StoreError(ValueError):
    """The store or a source database is malformed."""


def _filename(comm):
    key, ident = comm.get('key'), comm.get('id')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Z0-9_]+', key):
        raise StoreError('invalid community key')
    if not isinstance(ident, str) or not re.fullmatch(r'\d{2}', ident):
        raise StoreError('invalid community id')
    return f'{ident}-{key.lower()}.sqlite3'


def _expected_files():
    mapping = {comm['key']: _filename(comm) for comm in cd.COMMUNITIES}
    if len(mapping) != len(cd.COMMUNITIES) or len(set(mapping.values())) != len(mapping):
        raise StoreError('duplicate community identity')
    return mapping


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _identity(conn, comm, create=False):
    expected = {'key': comm['key'], 'id': comm['id'], 'version': str(STORE_VERSION)}
    if create:
        conn.execute('CREATE TABLE IF NOT EXISTS identity (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
        rows = dict(conn.execute('SELECT name, value FROM identity'))
        if not rows:
            conn.executemany('INSERT INTO identity (name, value) VALUES (?, ?)', expected.items())
            rows = expected
    else:
        rows = dict(conn.execute('SELECT name, value FROM identity'))
    if rows != expected:
        raise StoreError('database identity mismatch')


def _insert(path, comm, batch_id, attempted_at, text, previous_batch, history_limit):
    with closing(sqlite3.connect(path, timeout=30)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        _identity(conn, comm, create=True)
        conn.execute('CREATE TABLE IF NOT EXISTS captures ('
                     'batch_id TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, record_json TEXT NOT NULL)')
        conn.execute('INSERT INTO captures (batch_id, attempted_at, record_json) VALUES (?, ?, ?)',
                     (batch_id, attempted_at, text))
        # Retain the published batch even if a previous interrupted write left
        # many newer, unpublished rows. Publication happens after all writes.
        conn.execute('DELETE FROM captures WHERE batch_id NOT IN '
                     '(SELECT batch_id FROM captures ORDER BY rowid DESC LIMIT ?) AND batch_id != ?',
                     (max(1, int(history_limit)), previous_batch or ''))


def _write_one(path, comm, batch_id, attempted_at, text, previous_batch, history_limit):
    try:
        _insert(path, comm, batch_id, attempted_at, text, previous_batch, history_limit)
        return
    except sqlite3.DatabaseError as exc:
        # Only replace genuinely corrupt/non-SQLite files. A full disk, lock,
        # or permissions failure must abort rather than destroy existing data.
        if getattr(exc, 'sqlite_errorcode', None) not in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
            raise
    except StoreError:
        # A different community's DB in this fixed path is not usable data.
        pass
    fd, tmp = tempfile.mkstemp(prefix='.community-repair-', suffix='.sqlite3', dir=os.path.dirname(path))
    os.close(fd)
    try:
        _insert(tmp, comm, batch_id, attempted_at, text, '', history_limit)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _write_manifest(path, manifest):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix='.community-manifest-', suffix='.json', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_manifest(directory):
    try:
        with open(os.path.join(directory, MANIFEST_NAME), encoding='utf-8') as handle:
            manifest = json.load(handle)
        files = _expected_files()
        if (not isinstance(manifest, dict) or manifest.get('version') != STORE_VERSION
                or not re.fullmatch(r'[0-9a-f]{32}', str(manifest.get('batch_id', '')))
                or manifest.get('files') != files or not isinstance(manifest.get('header'), dict)
                or 'communities' in manifest['header'] or not isinstance(manifest.get('digests'), dict)
                or set(manifest['digests']) != set(files)
                or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
                       for value in manifest['digests'].values())):
            return None
        header = manifest['header']
        if not isinstance(header.get('snapshot'), dict) or not isinstance(header.get('summary'), dict):
            return None
        return manifest
    except (OSError, ValueError, TypeError, StoreError):
        return None


def _valid_header(data):
    # Validate provenance without requiring it to be fresh: expired data can
    # still be exported and labelled as expired, but never with forged dates.
    import community_snapshot as snapshots
    header = data if isinstance(data, dict) else {}
    snap = header.get('snapshot')
    if not isinstance(snap, dict):
        return False
    attempted = snapshots._parse_time(snap.get('attempted_at'))
    if (attempted is None or snap.get('version') != snapshots.SNAPSHOT_VERSION
            or snap.get('interval_seconds') != snapshots.INTERVAL_SECONDS
            or snap.get('post_interval_seconds') != 10
            or (snap.get('status'), header.get('mode')) not in (('ok', 'live'), ('collector_error', 'offline'))
            or header.get('generated_at') != attempted.strftime('%Y-%m-%d %H:%M:%S UTC')
            or header.get('fetch_date') != attempted.strftime('%Y-%m-%d')
            or not isinstance(header.get('summary'), dict)
            or header['summary'].get('total') != len(cd.COMMUNITIES)
            or not isinstance(header.get('notes'), list)
            or any(not isinstance(note, str) for note in header['notes'])):
        return False
    return True


def _valid_record(record, comm, capture_date):
    if not isinstance(record, dict):
        return False
    if any(record.get(field) != comm[field] for field in ('id', 'key', 'name', 'icon', 'ctype', 'url')):
        return False
    if (not isinstance(record.get('available'), bool)
            or not all(isinstance(record.get(field), str) for field in ('meta', 'fetch_date', 'verdict_class'))
            or record['fetch_date'] not in ('', capture_date)):
        return False
    if record['available']:
        inputs = record.get('analysis_input')
        if (not isinstance(inputs, dict) or not isinstance(inputs.get('snippet'), str)
                or not inputs['snippet'].strip() or not isinstance(inputs.get('title'), str)):
            return False
    return True


def save_snapshot(directory, data, history_limit=HISTORY_LIMIT):
    """Commit one complete collection into 49 separate databases.

    The manifest is atomically replaced last; interrupted writes cannot make a
    partial new batch visible. Existing healthy databases are never replaced.
    """
    files = _expected_files()
    records = data.get('communities') if isinstance(data, dict) else None
    snapshot = data.get('snapshot') if isinstance(data, dict) else None
    if not isinstance(records, list) or not isinstance(snapshot, dict) or not _valid_header(data):
        raise StoreError('missing or invalid snapshot provenance')
    attempted_at = snapshot.get('attempted_at')
    capture_date = data.get('fetch_date')
    if not isinstance(attempted_at, str) or not isinstance(capture_date, str):
        raise StoreError('missing capture time')
    by_key = {r.get('key'): r for r in records if isinstance(r, dict)}
    if len(records) != len(files) or set(by_key) != set(files):
        raise StoreError('incomplete community catalog')
    for comm in cd.COMMUNITIES:
        if not _valid_record(by_key[comm['key']], comm, capture_date):
            raise StoreError('invalid community record')
    os.makedirs(directory, exist_ok=True)
    previous = _read_manifest(directory) or {}
    batch_id = uuid.uuid4().hex
    encoded = {key: _encoded(by_key[key]) for key in files}
    for comm in cd.COMMUNITIES:
        key = comm['key']
        _write_one(os.path.join(directory, files[key]), comm, batch_id, attempted_at,
                   encoded[key], previous.get('batch_id'), history_limit)
    header = {key: copy.deepcopy(value) for key, value in data.items() if key != 'communities'}
    # Store diagnostics describe a read, not a new collection.
    header.pop('store', None)
    manifest = {'version': STORE_VERSION, 'batch_id': batch_id, 'header': header,
                'files': files, 'digests': {key: _digest(text) for key, text in encoded.items()}}
    _write_manifest(os.path.join(directory, MANIFEST_NAME), manifest)
    return batch_id


def _read_one(path, comm, batch_id, digest, capture_date, attempted_at):
    # mode=ro prevents a missing database from being silently recreated.
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as conn:
        _identity(conn, comm)
        row = conn.execute('SELECT attempted_at, record_json FROM captures WHERE batch_id = ?',
                           (batch_id,)).fetchone()
    if not row or row[0] != attempted_at or not isinstance(row[1], str) or _digest(row[1]) != digest:
        raise StoreError('missing or mismatched batch')
    record = json.loads(row[1])
    if not _valid_record(record, comm, capture_date):
        raise StoreError('invalid record')
    return record


def load_snapshot(directory):
    """Read the committed batch; isolate an unreadable DB to its own source.

    A missing/invalid manifest returns None. A missing, damaged or mixed-batch
    source becomes 'unavailable', never a stale result from an older batch.
    """
    manifest = _read_manifest(directory)
    if manifest is None:
        return None
    data = copy.deepcopy(manifest['header'])
    if not _valid_header(data):
        return None
    snapshot = data['snapshot']
    attempted_at = snapshot.get('attempted_at')
    capture_date = data.get('fetch_date')
    if not isinstance(attempted_at, str) or not isinstance(capture_date, str):
        return None
    records, issues = [], []
    for comm in cd.COMMUNITIES:
        key = comm['key']
        path = os.path.join(directory, manifest['files'][key])
        try:
            record = _read_one(path, comm, manifest['batch_id'], manifest['digests'][key],
                               capture_date, attempted_at)
        except (OSError, sqlite3.Error, ValueError, TypeError, UnicodeError, StoreError):
            issues.append(key)
            record = cd.build_unavailable_record(
                comm, fetch_date='', reason='社区库文件缺失、损坏或不属于本批次', checked=False)
        records.append(record)
    data['communities'] = records
    if issues:
        ok = sum(item['available'] for item in records)
        summary = data['summary']
        summary.update({'total': len(records), 'ok': ok, 'live_ok': ok,
                        'unavailable': len(records) - ok,
                        'failed': [item['name'] for item in records if not item['available']],
                        'comment_sources': sum(item.get('available') and item.get('content_kind')
                                               in ('comment', 'thread_reply') for item in records),
                        'page_excerpt_sources': sum(item.get('available') and item.get('content_kind')
                                                    == 'page_excerpt' for item in records)})
        data['counts'] = cd.count_verdicts(records)
        data.setdefault('notes', []).append('部分社区库无法读取，本批次对应来源标记未获取，不回退旧数据')
    data['store'] = {'version': STORE_VERSION, 'batch_id': manifest['batch_id'],
                     'sources': len(records), 'unavailable_databases': issues}
    return data


def main():
    parser = argparse.ArgumentParser(description='从 49 个社区数据库导出静态快照（不联网）')
    parser.add_argument('--export', action='store_true', required=True)
    parser.add_argument('--dir', default=DEFAULT_DIRECTORY)
    parser.add_argument('--json', default='community_data.json')
    args = parser.parse_args()
    data = load_snapshot(args.dir)
    if data is None:
        parser.exit(2, '社区数据库清单缺失或损坏，不能导出旧数据\n')
    # Reuse the project's atomic JSON writer and validator, without collecting.
    import community_snapshot as snapshots
    if not snapshots._complete_catalog(data):
        parser.exit(2, '社区数据库导出结果无效\n')
    snapshots._write_atomic(args.json, data)
    print(f"社区库导出：{len(data['communities'])} 源，库异常 {len(data['store']['unavailable_databases'])} 源 → {args.json}")


if __name__ == '__main__':
    main()
