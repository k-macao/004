#!/usr/bin/env python3
"""Offline tests for the four-hour community snapshot and render-time analysis."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import build_site as bs
import community_data as cd
import community_snapshot as snapshots
from tools import wechat_push as wp

NOW = datetime(2026, 10, 29, 23, 0, tzinfo=timezone.utc)


def fake_live(now):
    data = cd.offline_dataset(market={}, now=now)
    data['mode'] = 'live'
    return data


class TestSnapshotCadence(unittest.TestCase):
    def test_collect_once_and_reuse_until_four_hours(self):
        calls = []
        def fetch(now):
            calls.append(now)
            return fake_live(now)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'community.json')
            first = snapshots.ensure_snapshot(path, now=NOW, fetcher=fetch)
            with open(path, encoding='utf-8') as handle:
                original = handle.read()
            reused = snapshots.ensure_snapshot(path, now=NOW + timedelta(hours=3, minutes=59), fetcher=fetch)
            self.assertEqual(reused['action'], 'reused')
            with open(path, encoding='utf-8') as handle:
                self.assertEqual(handle.read(), original)
            self.assertEqual(len(calls), 1)
            again = snapshots.ensure_snapshot(path, now=NOW + timedelta(hours=4), fetcher=fetch)
            self.assertEqual(again['action'], 'refreshed')
            self.assertEqual(len(calls), 2)
            self.assertEqual(first['data']['snapshot']['interval_seconds'], 14400)
            self.assertEqual(first['data']['snapshot']['post_interval_seconds'], 10)
            self.assertEqual(first['data']['snapshot']['attempted_at'], '2026-10-29T23:00:00Z')
            self.assertEqual(again['data']['snapshot']['attempted_at'], '2026-10-30T03:00:00Z')

    def test_corrupt_partial_and_future_cache_trigger_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'community.json')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('{broken')
            result = snapshots.ensure_snapshot(path, now=NOW, fetcher=fake_live)
            self.assertEqual(result['action'], 'refreshed')
            snapshot = result['data']
            self.assertFalse(snapshots.snapshot_is_fresh(snapshot, now=NOW - timedelta(seconds=1)))
            snapshot['communities'][0]['name'] = None
            self.assertFalse(snapshots.snapshot_is_fresh(snapshot, now=NOW))
            snapshot['communities'][0]['name'] = cd.COMMUNITIES[0]['name']
            snapshot['fetch_date'] = '2026-10-28'
            self.assertFalse(snapshots.snapshot_is_fresh(snapshot, now=NOW))
            snapshot['fetch_date'] = '2026-10-29'
            snapshot['snapshot']['post_interval_seconds'] = 0
            self.assertFalse(snapshots.snapshot_is_fresh(snapshot, now=NOW))
            snapshot['snapshot']['post_interval_seconds'] = 10
            snapshot['communities'].pop()
            self.assertFalse(snapshots.snapshot_is_fresh(snapshot, now=NOW))

    def test_collector_error_is_honest_and_is_not_retried_every_five_minutes(self):
        calls = []
        def broken(now):
            calls.append(now)
            raise RuntimeError('do not publish exception content')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'community.json')
            with contextlib.redirect_stderr(io.StringIO()):
                result = snapshots.ensure_snapshot(path, now=NOW, fetcher=broken)
            self.assertEqual(result['action'], 'collector_error')
            self.assertEqual(result['data']['mode'], 'offline')
            self.assertEqual(result['data']['summary']['ok'], 0)
            self.assertTrue(all(not c['available'] and not c['fetch_date']
                                for c in result['data']['communities']))
            self.assertNotIn('do not publish exception', json.dumps(result['data']))
            self.assertIn('采集任务异常', bs._community_fetch_status(result['data']))
            self.assertTrue(all('可能已请求' in c['meta'] for c in result['data']['communities']))
            self.assertEqual(snapshots.ensure_snapshot(path, now=NOW + timedelta(minutes=5),
                                                       fetcher=broken)['action'], 'reused')
            self.assertEqual(len(calls), 1)

    def test_real_generator_creates_complete_snapshot_then_reuses_it(self):
        first = cd.COMMUNITIES[0]
        hits = {first['key']: {'ok': True, 'snippet': '港股资金流入，市场估值回升。',
                               'title': '市场讨论', 'status': 200}}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'community.json')
            with mock.patch.object(cd.spider_mod, 'fetch_live_snippets', return_value=(hits, {})) as spider:
                result = snapshots.ensure_snapshot(
                    path, now=NOW,
                    fetcher=lambda now: cd.generate_all_communities(market={}, now=now, verbose=False))
                self.assertEqual(result['action'], 'refreshed')
                self.assertEqual(len(result['data']['communities']), 49)
                self.assertEqual(result['data']['summary']['ok'], 1)
                self.assertTrue(snapshots.snapshot_is_fresh(result['data'], now=NOW))
                reused = snapshots.ensure_snapshot(path, now=NOW + timedelta(minutes=5))
                self.assertEqual(reused['action'], 'reused')
                self.assertEqual(spider.call_count, 1)
            refreshed_view = cd.reanalyze_snapshot(reused['data'], now=NOW + timedelta(minutes=5))
            self.assertEqual(refreshed_view['analysis_stats']['reanalyzed'], 1)
            self.assertEqual(refreshed_view['fetch_date'], '2026-10-29')

    def test_atomic_write_failure_keeps_previous_file_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'community.json')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('old file')
            with (mock.patch.object(snapshots.os, 'replace', side_effect=OSError('disk error')),
                  self.assertRaises(OSError)):
                snapshots._write_atomic(path, {'new': True})
            with open(path, encoding='utf-8') as handle:
                self.assertEqual(handle.read(), 'old file')
            self.assertEqual(os.listdir(tmp), ['community.json'])

    def test_sdk_install_is_only_attempted_for_a_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'community.json')
            argv = ['community_snapshot.py', '--json', path, '--install-praw-if-configured']
            with (mock.patch.dict(os.environ, {'GITHUB_OUTPUT': ''}),
                  mock.patch('sys.argv', argv),
                  mock.patch.object(snapshots, '_install_optional_praw') as install,
                  mock.patch.object(cd, 'generate_all_communities',
                                    side_effect=lambda market, now, obey_robots: fake_live(now)) as collect,
                  contextlib.redirect_stdout(io.StringIO())):
                snapshots.main()
                snapshots.main()
            self.assertEqual(install.call_count, 1)
            self.assertEqual(collect.call_count, 1)

    def test_sdk_install_failure_preserves_other_sources_and_hides_secrets(self):
        env = {'REDDIT_CLIENT_ID': 'test-id', 'REDDIT_CLIENT_SECRET': 'secret-value',
               'REDDIT_USER_AGENT': 'test-agent'}
        stderr = io.StringIO()
        with (mock.patch.dict(os.environ, env, clear=True),
              mock.patch.object(snapshots.subprocess, 'run',
                                side_effect=snapshots.subprocess.CalledProcessError(1, 'pip')) as install,
              contextlib.redirect_stderr(stderr)):
            snapshots._install_optional_praw()
        self.assertEqual(install.call_count, 1)
        self.assertIn('PRAW 安装失败', stderr.getvalue())
        self.assertNotIn('secret-value', stderr.getvalue())

    def test_plan_output_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'missing.json')
            output = os.path.join(tmp, 'github-output')
            with (mock.patch.dict(os.environ, {'GITHUB_OUTPUT': output}),
                  mock.patch('sys.argv', ['community_snapshot.py', '--plan', '--json', path]),
                  contextlib.redirect_stdout(io.StringIO())):
                snapshots.main()
            with open(output, encoding='utf-8') as handle:
                self.assertIn('refresh=true', handle.read())
            self.assertFalse(os.path.exists(path))


class TestRenderTimeAnalysis(unittest.TestCase):
    def test_reanalyze_uses_saved_comment_only_and_preserves_capture_time(self):
        comm = cd.COMMUNITIES[0]
        text = '市场保持稳定，成交量尚需继续观察。'
        evidence = {'content_kind': 'comment', 'strategy': 'reddit_praw',
                    'comments': [{'metric': {'label': '评论得分 1'}}]}
        record = cd.build_live_record(comm, text, '2026-10-29',
                                      title='bullish rally gains', evidence=evidence)
        record['verdict'] = 'obsolete analysis'
        record['verdict_class'] = 'bull'
        snapshot = {'generated_at': '2026-10-29 23:00:00 UTC', 'fetch_date': '2026-10-29',
                    'mode': 'live', 'communities': [record]}
        new = cd.reanalyze_snapshot(snapshot, now=NOW + timedelta(hours=1))
        self.assertEqual(new['generated_at'], snapshot['generated_at'])
        self.assertEqual(new['fetch_date'], '2026-10-29')
        self.assertEqual(new['analysis_at'], '2026-10-30 00:00:00 UTC')
        self.assertNotEqual(new['communities'][0]['verdict'], 'obsolete analysis')
        self.assertEqual(new['communities'][0]['analysis_input']['title'], '')
        self.assertEqual(snapshot['communities'][0]['verdict'], 'obsolete analysis')
        expected = cd.analyze_live_snippet(text, title='', ctype=comm['ctype'],
                                           peer_snippets=[text], content_kind='comment')
        self.assertEqual(new['communities'][0]['quant']['sentiment']['score'],
                         expected['quant']['sentiment']['score'])

    def test_legacy_snapshot_is_not_falsely_labelled_reanalyzed(self):
        record = cd.build_live_record(cd.COMMUNITIES[0], '港股市场继续观察。', '2026-10-29')
        record.pop('analysis_input')
        data = {'mode': 'live', 'generated_at': '2026-10-29 23:00:00 UTC',
                'communities': [record], 'summary': {'ok': 1, 'total': 1}}
        result = cd.reanalyze_snapshot(data, now=NOW + timedelta(hours=1))
        self.assertEqual(result['communities'][0]['verdict'], record['verdict'])
        self.assertNotIn('analysis_at', result)
        self.assertEqual(result['analysis_stats'], {'reanalyzed': 0, 'legacy_retained': 1})
        self.assertIn('旧格式保留原分析', bs._community_fetch_status(result))

    def test_wechat_reloads_and_scores_without_network(self):
        record = cd.build_live_record(cd.COMMUNITIES[0], '港股资金流入，市场估值回升。', '2026-10-29')
        data = {'generated_at': '2026-10-29 23:00:00 UTC', 'fetch_date': '2026-10-29',
                'mode': 'live', 'communities': [record], 'summary': {'ok': 1, 'total': 1}}
        with (mock.patch.object(wp, 'load_community_data', return_value=data),
              mock.patch.object(wp, 'load_market_data', return_value={}),
              mock.patch.object(wp, 'load_sentiment_data', return_value={}),
              mock.patch.object(wp, 'load_macro_data', return_value={}),
              mock.patch.object(cd, 'analyze_live_snippet', wraps=cd.analyze_live_snippet) as analyze,
              mock.patch.object(cd.spider_mod, 'fetch_live_snippets', side_effect=AssertionError('network')),
              contextlib.redirect_stdout(io.StringIO())):
            html, _, _ = wp.build_single_wechat_html(now=NOW + timedelta(hours=1))
        self.assertGreater(analyze.call_count, 0)
        self.assertIn('2026-10-29', html)
        self.assertIn('本次读取分析', html)

    def test_page_builder_loads_snapshot_and_reanalyzes_without_collecting(self):
        record = cd.build_live_record(cd.COMMUNITIES[0], '港股资金流入，市场估值回升。', '2026-10-29')
        data = {'generated_at': '2026-10-29 23:00:00 UTC', 'fetch_date': '2026-10-29',
                'mode': 'live', 'communities': [record], 'summary': {'ok': 1, 'total': 1}}
        with tempfile.TemporaryDirectory() as tmp:
            community_file = os.path.join(tmp, 'community.json')
            out = os.path.join(tmp, 'report.html')
            with open(community_file, 'w', encoding='utf-8') as handle:
                json.dump(data, handle)
            args = ['build_site.py', '--community', community_file, '--out', out,
                    '--data', os.path.join(tmp, 'market.json'), '--macro', os.path.join(tmp, 'macro.json'),
                    '--sentiment', os.path.join(tmp, 'sentiment.json'), '--macro-no-fetch',
                    '--forecast-no-history']
            with (mock.patch('sys.argv', args),
                  mock.patch.object(cd, 'analyze_live_snippet', wraps=cd.analyze_live_snippet) as analyze,
                  mock.patch.object(cd.spider_mod, 'fetch_live_snippets', side_effect=AssertionError('network')),
                  contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO())):
                bs.main()
            self.assertGreater(analyze.call_count, 0)
            with open(out, encoding='utf-8') as handle:
                html = handle.read()
            self.assertIn('本次读取分析', html)
            with open(community_file, encoding='utf-8') as handle:
                self.assertNotIn('analysis_at', json.load(handle))

    def test_four_hour_snapshot_can_cross_midnight_without_rewriting_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'community.json')
            snapshot = snapshots.ensure_snapshot(path, now=NOW, fetcher=fake_live)['data']
        parts = [('report', '\n'.join('公开页实抓摘录 · 抓取核验 2026-10-29' for _ in range(49)))]
        with contextlib.redirect_stdout(io.StringIO()):
            wp.assert_fetch_dates_are_today(parts, NOW + timedelta(hours=2), snapshot=snapshot)
        forged = [('report', '\n'.join('公开页实抓摘录 · 抓取核验 2026-10-30' for _ in range(49)))]
        with (self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO())):
            wp.assert_fetch_dates_are_today(forged, NOW + timedelta(hours=2), snapshot=snapshot)
        with (self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO())):
            wp.assert_fetch_dates_are_today(parts, NOW + timedelta(hours=4), snapshot=snapshot)


if __name__ == '__main__':
    unittest.main()
