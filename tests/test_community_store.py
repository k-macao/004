#!/usr/bin/env python3
"""Offline tests for 49 independent databases and static export."""
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import community_data as cd
import community_snapshot as snapshots
import community_store as store

NOW = datetime(2026, 10, 29, 23, 0, tzinfo=timezone.utc)


def live_batch(now, text='港股市场资金流入，估值持续回升。'):
    data = cd.offline_dataset(market={}, now=now)
    data['mode'] = 'live'
    first = cd.COMMUNITIES[0]
    data['communities'][0] = cd.build_live_record(first, text, now.strftime('%Y-%m-%d'))
    data['summary']['ok'] = data['summary']['live_ok'] = 1
    data['summary']['unavailable'] -= 1
    data['summary']['failed'] = [c['name'] for c in data['communities'] if not c['available']]
    data['counts'] = cd.count_verdicts(data['communities'])
    return data


def captured(now=NOW, text='港股市场资金流入，估值持续回升。'):
    data = live_batch(now, text)
    data['snapshot'] = {'version': 1, 'status': 'ok',
                        'attempted_at': now.isoformat(timespec='seconds').replace('+00:00', 'Z'),
                        'interval_seconds': 14400, 'post_interval_seconds': 10}
    return data


class TestCommunityStore(unittest.TestCase):
    def test_independent_files_are_source_identified_and_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            data = captured()
            batch = store.save_snapshot(directory, data)
            filenames = [name for name in os.listdir(directory) if name.endswith('.sqlite3')]
            self.assertEqual(len(filenames), 49)
            self.assertEqual(len(set(filenames)), 49)
            for comm in cd.COMMUNITIES:
                path = os.path.join(directory, store._filename(comm))
                with sqlite3.connect(path) as db:
                    identity = dict(db.execute('select name,value from identity'))
                    payload = json.loads(db.execute('select record_json from captures').fetchone()[0])
                self.assertEqual(identity['key'], comm['key'])
                self.assertEqual(payload['key'], comm['key'])
            loaded = store.load_snapshot(directory)
            self.assertEqual(loaded['store']['batch_id'], batch)
            self.assertEqual(loaded['store']['unavailable_databases'], [])
            self.assertEqual(loaded['communities'], data['communities'])
            self.assertTrue(snapshots.snapshot_is_fresh(loaded, NOW))

    def test_next_batch_keeps_bounded_per_source_history(self):
        with tempfile.TemporaryDirectory() as directory:
            for i in range(4):
                now = NOW + timedelta(hours=4 * i)
                store.save_snapshot(directory, captured(now, f'港股市场资金流入第 {i} 次。'), history_limit=2)
            first = cd.COMMUNITIES[0]
            with sqlite3.connect(os.path.join(directory, store._filename(first))) as db:
                rows = db.execute('select record_json from captures order by rowid').fetchall()
            self.assertEqual(len(rows), 2)
            self.assertIn('第 3 次', json.loads(rows[-1][0])['analysis_input']['snippet'])
            self.assertIn('第 3 次', store.load_snapshot(directory)['communities'][0]['analysis_input']['snippet'])

    def test_interrupted_write_does_not_publish_or_prune_previous_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            old = captured()
            previous = store.save_snapshot(directory, old, history_limit=1)
            real_write = store._write_one
            calls = []
            def fail_midway(*args, **kwargs):
                calls.append(args[1]['key'])
                if len(calls) == 25:
                    raise OSError('simulated disk full')
                return real_write(*args, **kwargs)
            new = captured(NOW + timedelta(hours=4), '港股市场大跌，资金持续流出。')
            with mock.patch.object(store, '_write_one', side_effect=fail_midway):
                with self.assertRaises(OSError):
                    store.save_snapshot(directory, new, history_limit=1)
            actual = store.load_snapshot(directory)
            self.assertEqual(actual['store']['batch_id'], previous)
            self.assertEqual(actual['communities'], old['communities'])
            self.assertEqual(actual['snapshot']['attempted_at'], old['snapshot']['attempted_at'])

    def test_missing_corrupt_and_mixed_batches_are_isolated_not_backfilled(self):
        with tempfile.TemporaryDirectory() as directory:
            old = captured()
            store.save_snapshot(directory, old)
            path = os.path.join(directory, store._filename(cd.COMMUNITIES[0]))
            os.unlink(path)
            actual = store.load_snapshot(directory)
            self.assertFalse(actual['communities'][0]['available'])
            self.assertEqual(actual['communities'][0]['fetch_date'], '')
            self.assertEqual(actual['store']['unavailable_databases'], ['FUTU'])
            self.assertEqual(actual['summary']['ok'], 0)
            self.assertTrue(snapshots.snapshot_is_fresh(actual, NOW))
            with open(path, 'wb') as handle:
                handle.write(b'not sqlite')
            self.assertEqual(store.load_snapshot(directory)['store']['unavailable_databases'], ['FUTU'])
            # A new collection repairs the damaged source file.
            store.save_snapshot(directory, captured(NOW + timedelta(hours=4)))
            self.assertEqual(store.load_snapshot(directory)['store']['unavailable_databases'], [])
            self.assertTrue(store.load_snapshot(directory)['communities'][0]['available'])
            # A valid DB from another community is also never accepted.
            other = os.path.join(directory, store._filename(cd.COMMUNITIES[1]))
            with open(other, 'rb') as source, open(path, 'wb') as dest:
                dest.write(source.read())
            self.assertEqual(store.load_snapshot(directory)['store']['unavailable_databases'], ['FUTU'])

    def test_tampering_is_detected_by_manifest_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            store.save_snapshot(directory, captured())
            path = os.path.join(directory, store._filename(cd.COMMUNITIES[0]))
            with sqlite3.connect(path) as db:
                db.execute("update captures set record_json = replace(record_json, '资金流入', '资金流出')")
            actual = store.load_snapshot(directory)
            self.assertEqual(actual['store']['unavailable_databases'], ['FUTU'])
            self.assertFalse(actual['communities'][0]['available'])

    def test_manifest_is_not_used_as_an_arbitrary_path(self):
        with tempfile.TemporaryDirectory() as directory:
            store.save_snapshot(directory, captured())
            manifest = os.path.join(directory, store.MANIFEST_NAME)
            with open(manifest, encoding='utf-8') as f:
                data = json.load(f)
            data['files']['FUTU'] = '../../elsewhere.db'
            with open(manifest, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            self.assertIsNone(store.load_snapshot(directory))

    def test_manifest_replacement_failure_keeps_previous_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            old = captured()
            batch = store.save_snapshot(directory, old, history_limit=1)
            with mock.patch.object(store.os, 'replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    store.save_snapshot(directory, captured(NOW + timedelta(hours=4)), history_limit=1)
            self.assertEqual(store.load_snapshot(directory)['store']['batch_id'], batch)
            self.assertEqual(store.load_snapshot(directory)['communities'], old['communities'])
            self.assertFalse(any(n.startswith('.community-manifest-') for n in os.listdir(directory)))

    def test_invalid_manifest_never_falls_back_to_old_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = os.path.join(tmp, 'dbs')
            path = os.path.join(tmp, 'community_data.json')
            snapshots.ensure_snapshot(path, now=NOW, fetcher=live_batch, store_dir=directory)
            with open(os.path.join(directory, store.MANIFEST_NAME), 'w', encoding='utf-8') as f:
                f.write('{broken')
            with mock.patch.object(cd, 'generate_all_communities', side_effect=RuntimeError):
                with contextlib.redirect_stderr(io.StringIO()):
                    result = snapshots.ensure_snapshot(path, now=NOW + timedelta(minutes=5),
                                                       fetcher=lambda now: live_batch(now), store_dir=directory)
            self.assertEqual(result['action'], 'refreshed')
            self.assertEqual(result['data']['snapshot']['attempted_at'], '2026-10-29T23:05:00Z')

    def test_fresh_legacy_migrates_without_crawling_then_store_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, directory = os.path.join(tmp, 'community_data.json'), os.path.join(tmp, 'dbs')
            snapshots._write_atomic(path, captured())
            with mock.patch.object(cd, 'generate_all_communities', side_effect=AssertionError('network')):
                migrated = snapshots.ensure_snapshot(path, now=NOW + timedelta(minutes=5), store_dir=directory)
            self.assertEqual(migrated['action'], 'migrated')
            self.assertTrue(migrated['written'])
            self.assertEqual(len([x for x in os.listdir(directory) if x.endswith('.sqlite3')]), 49)
            # A modified JSON export is ignored in favour of the committed DBs.
            snapshots._write_atomic(path, captured(NOW, '港股市场旧数据不应被采用。'))
            reused = snapshots.ensure_snapshot(path, now=NOW + timedelta(minutes=6), store_dir=directory)
            self.assertEqual(reused['action'], 'reused')
            self.assertFalse(reused['written'])
            self.assertIn('资金流入', reused['data']['communities'][0]['analysis_input']['snippet'])
            with open(path, encoding='utf-8') as f:
                self.assertIn('资金流入', json.load(f)['communities'][0]['analysis_input']['snippet'])

    def test_static_export_reanalyzes_inputs_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, path = os.path.join(tmp, 'dbs'), os.path.join(tmp, 'static.json')
            data = captured()
            data['communities'][0]['verdict'] = 'outdated analysis'
            store.save_snapshot(directory, data)
            args = ['community_store.py', '--export', '--dir', directory, '--json', path]
            with mock.patch('sys.argv', args), contextlib.redirect_stdout(io.StringIO()):
                store.main()
            with open(path, encoding='utf-8') as f:
                exported = json.load(f)
            with mock.patch.object(cd.spider_mod, 'fetch_live_snippets', side_effect=AssertionError('network')):
                result = cd.reanalyze_snapshot(exported, now=NOW + timedelta(minutes=5))
            self.assertNotEqual(result['communities'][0]['verdict'], 'outdated analysis')
            self.assertEqual(result['fetch_date'], '2026-10-29')
            self.assertEqual(result['analysis_stats']['reanalyzed'], 1)


if __name__ == '__main__':
    unittest.main()
