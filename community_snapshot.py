#!/usr/bin/env python3
"""Four-hour community collector shared by page and push jobs.

The CLI stores each community in its own SQLite database and exports a static
JSON view. Capture timestamps, not file mtimes, determine reuse. GitHub Actions
serializes collection and distributes the databases to render/push jobs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import community_data as cd

INTERVAL_SECONDS = 4 * 60 * 60
SNAPSHOT_VERSION = 1


def _utc(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return _utc(parsed) if parsed.tzinfo is not None else None


def _complete_catalog(data):
    if not isinstance(data, dict) or not isinstance(data.get('communities'), list):
        return False
    records = data['communities']
    if any(not isinstance(item, dict) for item in records):
        return False
    expected = {item['key'] for item in cd.COMMUNITIES}
    actual = [item.get('key') for item in records]
    if (len(actual) != len(expected) or any(not isinstance(key, str) for key in actual)
            or set(actual) != expected):
        return False
    for item in records:
        if (not all(isinstance(item.get(k), str) for k in ('id', 'key', 'name', 'icon', 'ctype',
                                                           'url', 'meta', 'fetch_date', 'verdict_class'))
                or not isinstance(item.get('available'), bool)):
            return False
        if item['available']:
            inputs = item.get('analysis_input')
            if (not isinstance(inputs, dict) or not isinstance(inputs.get('snippet'), str)
                    or not inputs['snippet'].strip() or not isinstance(inputs.get('title'), str)):
                return False
    summary = data.get('summary')
    return (isinstance(summary, dict) and summary.get('total') == len(expected)
            and summary.get('ok') == sum(item['available'] for item in records))


def _capture_dates_match(data, attempted):
    capture_date = attempted.strftime('%Y-%m-%d')
    return (data.get('generated_at') == attempted.strftime('%Y-%m-%d %H:%M:%S UTC')
            and data.get('fetch_date') == capture_date
            and all(item.get('fetch_date') in ('', capture_date) for item in data['communities']))


def snapshot_is_fresh(data, now=None, interval=INTERVAL_SECONDS):
    """A valid recent attempt is reusable, including an honestly failed one."""
    if not _complete_catalog(data):
        return False
    snapshot = data.get('snapshot')
    if (not isinstance(snapshot, dict) or snapshot.get('version') != SNAPSHOT_VERSION
            or snapshot.get('interval_seconds') != INTERVAL_SECONDS
            or snapshot.get('post_interval_seconds') != 10):
        return False
    status = snapshot.get('status')
    if (status, data.get('mode')) not in (('ok', 'live'), ('collector_error', 'offline')):
        return False
    attempted = _parse_time(snapshot.get('attempted_at'))
    if attempted is None:
        return False
    if not _capture_dates_match(data, attempted):
        return False
    age = (_utc(now) - attempted).total_seconds()
    return 0 <= age < interval


def read_snapshot(path):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return None


def _write_atomic(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.community-snapshot-', suffix='.json', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _stored_or_legacy(path, store_dir):
    """Prefer committed databases; only migrate JSON when no manifest exists."""
    if store_dir is not None:
        import community_store as store
        existing = store.load_snapshot(store_dir)
        if existing is not None:
            return existing, 'store'
        # A damaged manifest is not a license to resurrect an older JSON file.
        if os.path.exists(os.path.join(store_dir, store.MANIFEST_NAME)):
            return None, 'invalid_store'
    return read_snapshot(path), 'legacy'


def ensure_snapshot(path, now=None, fetcher=None, force=False, store_dir=None):
    """Reuse a fresh snapshot or perform one bounded collection.

    If store_dir is supplied, databases are authoritative and JSON is an
    atomic static export. Fresh legacy JSON is migrated without recrawling.
    On collection failure, write an explicit offline batch for four hours.
    """
    now = _utc(now)
    existing, origin = _stored_or_legacy(path, store_dir)
    if not force and snapshot_is_fresh(existing, now=now):
        if store_dir is not None:
            if origin == 'legacy':
                import community_store as store
                store.save_snapshot(store_dir, existing)
                existing = store.load_snapshot(store_dir)
                _write_atomic(path, existing)
                return {'action': 'migrated', 'written': True, 'data': existing}
            # Recreate the disposable JSON export even if it is missing or
            # older than the database batch. Never recrawl while fresh.
            _write_atomic(path, existing)
        return {'action': 'reused', 'written': False, 'data': existing}
    attempted_at = now.isoformat(timespec='seconds').replace('+00:00', 'Z')
    try:
        if fetcher is None:
            data = cd.generate_all_communities(market={}, now=now, obey_robots=True)
        else:
            data = fetcher(now)
        if (not _complete_catalog(data) or data.get('mode') != 'live'
                or not _capture_dates_match(data, now)):
            raise ValueError('collector did not return a complete current live catalog')
        status = 'ok'
        error = ''
    except Exception as exc:  # one failed collection must not invent/refresh old data
        data = cd.offline_dataset(market={}, now=now, mode='offline')
        for item in data['communities']:
            item['meta'] = f"采集任务异常（结果不可用，部分来源可能已请求）· {item['ctype']}"
            item['quote'] = '采集任务异常，本源结果不可用——不编造热评，不回填历史剧本。'
        data['notes'] = ['采集任务异常，49 源结果不可用；不假定任何单源已成功或未被请求',
                         '不编造热评、不预设多空家数、不伪造抓取核验日期']
        status = 'collector_error'
        error = type(exc).__name__
        print(f'⚠️ 社区采集任务失败（{error}），本周期标记为未获取', file=sys.stderr)
    data['snapshot'] = {
        'version': SNAPSHOT_VERSION,
        'status': status,
        'attempted_at': attempted_at,
        'interval_seconds': INTERVAL_SECONDS,
        'post_interval_seconds': 10,
    }
    if error:
        data['snapshot']['error_type'] = error
    if store_dir is not None:
        import community_store as store
        store.save_snapshot(store_dir, data)
        data = store.load_snapshot(store_dir)
        if not snapshot_is_fresh(data, now=now):
            raise store.StoreError('committed community batch cannot be read')
    _write_atomic(path, data)
    return {'action': 'refreshed' if status == 'ok' else 'collector_error',
            'written': True, 'data': data}


def _set_output(name, value):
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a', encoding='utf-8') as handle:
            handle.write(f'{name}={value}\n')


def _install_optional_praw():
    """Install the optional SDK only on a real collection, never on cache reuse."""
    keys = ('REDDIT_CLIENT_ID', 'REDDIT_CLIENT_SECRET', 'REDDIT_USER_AGENT')
    if not all(os.environ.get(key, '').strip() for key in keys):
        return
    try:
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'praw==8.0.3'],
                       check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        print('⚠️ PRAW 安装失败，保留其他来源', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description='49 源社区采集快照：每四小时最多更新一次')
    parser.add_argument('--json', default='community_data.json', help='静态 JSON 导出路径')
    parser.add_argument('--store-dir', default=None,
                        help='社区数据库目录（默认与 JSON 同目录下的 community_store）')
    parser.add_argument('--plan', action='store_true', help='仅检查缓存是否需要更新，不抓取')
    parser.add_argument('--force', action='store_true', help='手动强制采集，绕过四小时缓存')
    parser.add_argument('--install-praw-if-configured', action='store_true',
                        help='需要采集且凭据齐全时安装可选 PRAW（CI 使用）')
    args = parser.parse_args()
    store_dir = args.store_dir or os.path.join(os.path.dirname(os.path.abspath(args.json)), 'community_store')
    if args.plan:
        existing, _ = _stored_or_legacy(args.json, store_dir)
        refresh = args.force or not snapshot_is_fresh(existing)
        _set_output('refresh', str(refresh).lower())
        print('社区快照：' + ('需要更新' if refresh else '四小时内已采集，复用'))
        return

    def fetch(now):
        if args.install_praw_if_configured:
            _install_optional_praw()
        return cd.generate_all_communities(market={}, now=now, obey_robots=True)

    result = ensure_snapshot(args.json, force=args.force, fetcher=fetch, store_dir=store_dir)
    _set_output('written', str(result['written']).lower())
    print(f"社区快照：{result['action']} · {result['data']['snapshot']['attempted_at']} · {store_dir} → {args.json}")


if __name__ == '__main__':
    main()
