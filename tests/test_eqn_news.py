#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
06 栏「方程式新闻（EquationNews）全球快讯」回归测试（零联网）
==============================================================

锁死这条新栏目的口径：
  • 归一化：新→旧、无日期垫底、中文缺失回退英文、AI 摘要/总览提取；
  • 时效：超窗丢弃并计数，无日期保留但垫底；
  • 取数通道：REST URL 拼装正确；MCP（JSON-RPC over streamable HTTP）解包正确；
  • 可用性四态与 02 / 05 栏同构（no_file / bad_type / no_items / stale_snapshot）；
  • 渲染纯净：缺数据直接留空、不出现状态文案、内容层零链接；
  • 构建期补抓边界：默认只在 CI 开、no_items 不重复烧网络、失败不抛异常；
  • 管线接入：网页注入幂等且位置在 05 之后、07 之前；微信单页仍在门禁内。
"""
import contextlib
import html as html_mod
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_site as bs          # noqa: E402
import eqn_news as eq            # noqa: E402
import wechat_push as wp         # noqa: E402

NOW = datetime(2026, 11, 23, 20, 0, tzinfo=timezone.utc)
BANNED = ('今日未获取', '未获取', '不回填', '时效窗口', '抓取核验', '口径：', '本栏不预测')


def _mock_data():
    return eq.build(mock=True, quiet=True)


class TestNormalize(unittest.TestCase):
    def test_newest_first_undated_last_and_ai_extraction(self):
        payload = {'items': [
            {'no': 1, 'time': '2026-11-22T10:00:00.000Z', 'channels': ['main'],
             'source': 'Binance EN', 'zh': '', 'en': 'Older headline', 'coins': [],
             'url': 'https://eqn.news/main/1', 'source_url': None, 'ai': None},
            {'no': 2, 'time': '2026-11-23T10:00:00.000Z', 'channels': ['main', 'binance'],
             'source': 'Binance EN', 'zh': '币安将上线 XYZ', 'en': 'Binance Will List XYZ',
             'coins': ['XYZ'], 'url': 'https://eqn.news/main/2',
             'source_url': 'https://www.binance.com/x',
             'ai': {'summary': {'type': 'listing',
                                'points': [{'zh': '11 月 20 日开放现货交易'},
                                           {'en': 'only-en point'}]},
                    'overview': None, 'notice': 'AI-generated'}},
            {'no': 3, 'time': None, 'channels': [], 'source': '', 'zh': '无日期条目',
             'en': '', 'coins': [], 'url': '', 'source_url': '', 'ai': None},
        ]}
        items = eq.normalize(payload, now=NOW)
        self.assertEqual([i['no'] for i in items], [2, 1, 3])
        self.assertEqual(items[0]['title'], '币安将上线 XYZ')
        self.assertEqual(items[0]['title_en'], 'Binance Will List XYZ')
        self.assertEqual(items[0]['coins'], ['XYZ'])
        self.assertEqual(items[0]['published_date'], '2026-11-23')
        self.assertEqual(items[0]['ai_summary'][0], '11 月 20 日开放现货交易')
        self.assertEqual(items[1]['title'], 'Older headline', '中文缺失应回退英文')

    def test_overview_fallback_and_missing_ai(self):
        raw = {'no': 9, 'time': '2026-11-23T09:00:00Z', 'zh': '标题', 'en': '',
               'ai': {'summary': None,
                      'overview': {'status': 'ready',
                                   'zh': {'paragraphs': [{'text': '第一段'},
                                                         {'text': '第二段'}]},
                                   'changes': '更新了一点'}}}
        item = eq.normalize({'items': [raw]}, now=NOW)[0]
        self.assertEqual(item['ai_summary'], [])
        self.assertIn('第一段', item['ai_overview'])
        self.assertEqual(item['ai_changes'], '更新了一点')
        self.assertEqual(item['ai_overview_status'], 'ready')
        bare = eq.normalize({'items': [{'no': 10, 'time': '2026-11-23T09:00:00Z',
                                        'zh': 't', 'ai': None}]}, now=NOW)[0]
        self.assertEqual(bare['ai_summary'], [])
        self.assertEqual(bare['ai_overview'], '')

    def test_filter_stale_drops_out_of_window_keeps_undated_at_bottom(self):
        items = [
            {'title': '新', 'age_days': 0.1},
            {'title': '旧', 'age_days': 30.0},
            {'title': '无日期', 'age_days': None},
        ]
        kept, dropped = eq.filter_stale(items, max_age_days=7, now=NOW)
        self.assertEqual([i['title'] for i in kept], ['新', '无日期'])
        self.assertEqual(dropped, {'stale_dropped': 1, 'undated_kept': 1})


class TestEndpoints(unittest.TestCase):
    def test_rest_url_params(self):
        u = eq.rest_url(channel='main', limit=5)
        self.assertIn('channel=main', u)
        self.assertIn('limit=5', u)
        self.assertNotIn('channel=', eq.rest_url(channel='all', limit=5))
        u2 = eq.rest_url(channel='tradfi', limit=3, query='关税', coin='btc')
        self.assertIn('channel=tradfi', u2)
        self.assertIn('query=%E5%85%B3%E7%A8%8E', u2)
        self.assertIn('coin=BTC', u2)

    def test_limit_clamped_to_api_max(self):
        self.assertIn('limit=50', eq.rest_url(limit=999))
        self.assertIn('limit=1', eq.rest_url(limit=0))

    def test_mcp_client_unwraps_tools_call(self):
        """用桩件替掉 HTTP：验证 initialize → notifications → tools/call 的解包路径。"""
        calls = []

        def fake_post(url, payload, timeout=10, session_id=None):
            calls.append(payload.get('method'))
            if payload.get('method') == 'initialize':
                return 200, 'sess-1', {'jsonrpc': '2.0', 'id': 1, 'result': {}}
            if payload.get('method') == 'notifications/initialized':
                return 202, 'sess-1', None
            return 200, 'sess-1', {'jsonrpc': '2.0', 'id': 2, 'result': {
                'content': [{'type': 'text', 'text': json.dumps(
                    {'items': [{'no': 1}], 'next_cursor': 'c'})}]}}

        orig = eq._rpc_post
        eq._rpc_post = fake_post
        try:
            payload = eq.fetch_mcp({'channel': 'main', 'limit': 20})
        finally:
            eq._rpc_post = orig
        self.assertEqual(calls, ['initialize', 'notifications/initialized', 'tools/call'])
        self.assertEqual(payload['items'], [{'no': 1}])

    def test_mcp_sse_response_is_parsed(self):
        sse = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}\n\n'
        msg = eq._parse_sse(sse)
        self.assertEqual(msg['result']['content'], [])


class TestAvailability(unittest.TestCase):
    def test_four_states(self):
        self.assertEqual(eq.availability({})['reason'], 'no_file')
        self.assertEqual(eq.availability([])['reason'], 'bad_type')
        self.assertEqual(eq.availability({'items': [], 'summary': {}, 'window': {}})
                         ['reason'], 'no_items')
        old = {'generated_at': '2020-01-01 00:00:00 UTC', 'fetch_date': '2020-01-01',
               'items': [{'title': 'x'}], 'summary': {'ok': 1, 'total': 1}}
        self.assertEqual(eq.availability(old, now=NOW)['reason'], 'stale_snapshot')
        fresh = _mock_data()
        self.assertTrue(eq.availability(fresh, now=NOW)['ok'])

    def test_mock_build_counts(self):
        data = _mock_data()
        s = data['summary']
        self.assertEqual(s['stale_dropped'], 1, '样本里那条 260 小时前的应被拦截')
        self.assertEqual(s['undated_kept'], 1, '无日期样本应保留并垫底')
        self.assertEqual(s['with_ai'], 3)
        self.assertEqual(data['mode'], 'mock')


class TestRender(unittest.TestCase):
    def test_empty_when_unavailable(self):
        for bad in ({}, {'items': [], 'summary': {}}, []):
            self.assertEqual(eq.render_web(bad), '')
            self.assertEqual(eq.render_wechat(bad), '')

    def test_web_has_substance_no_links_no_status_text(self):
        html = eq.render_web(_mock_data())
        self.assertIn('方程式新闻', html)
        self.assertIn('AI 摘要', html)
        self.assertIn('$ACE', html)
        self.assertNotIn('<a href', html)
        self.assertNotIn('href', html)
        self.assertNotIn('http', html)
        for phrase in BANNED:
            self.assertNotIn(phrase, html)

    def test_wechat_levels_converge_and_keep_titles(self):
        full = eq.render_wechat(_mock_data(), level='full')
        compact = eq.render_wechat(_mock_data(), level='compact')
        self.assertIn('AI 摘要', full)
        self.assertNotIn('AI 摘要', compact)
        self.assertLess(len(compact), len(full))
        data = _mock_data()
        for it in data['items']:
            if it.get('title'):
                self.assertIn(it['title'][:10], full)
                self.assertIn(it['title'][:10], compact)
        for phrase in BANNED:
            self.assertNotIn(phrase, full)

    def test_fixture_path_in_product(self):
        data = _mock_data()
        self.assertIn('EQN_NEWS_MIX.json', data['source']['endpoint'])


class TestEnsure(unittest.TestCase):
    """构建期补抓边界（与 macro/rss 同一套策略）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, 'eqn_news_data.json')
        self._saved = {k: os.environ.get(k) for k in ('EQN_AUTO_FETCH', 'GITHUB_ACTIONS')}
        os.environ.pop('EQN_AUTO_FETCH', None)
        os.environ.pop('GITHUB_ACTIONS', None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_fresh_product_not_refetched(self):
        data = _mock_data()
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        orig = eq.build
        eq.build = lambda **kw: (_ for _ in ()).throw(AssertionError('不得重复抓取'))
        try:
            res = eq.ensure(self.path, now=NOW)
        finally:
            eq.build = orig
        self.assertEqual(res['action'], 'fresh')

    def test_disabled_by_default_locally(self):
        res = eq.ensure(self.path, now=NOW)
        self.assertEqual(res['action'], 'disabled')
        self.assertFalse(os.path.exists(self.path))

    def test_ci_enabled_and_mock_replay(self):
        os.environ['GITHUB_ACTIONS'] = 'true'
        res = eq.ensure(self.path, mock=True, quiet=True, now=NOW)
        self.assertEqual(res['action'], 'fetched')
        self.assertTrue(res['fetched'])
        self.assertTrue(os.path.exists(self.path), '补抓产物应落盘')

    def test_env_flag_overrides_ci(self):
        os.environ['GITHUB_ACTIONS'] = 'true'
        os.environ['EQN_AUTO_FETCH'] = '0'
        res = eq.ensure(self.path, now=NOW)
        self.assertEqual(res['action'], 'disabled')

    def test_no_items_not_refetched(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'generated_at': NOW.strftime('%Y-%m-%d %H:%M:%S UTC'),
                       'fetch_date': NOW.strftime('%Y-%m-%d'), 'items': [],
                       'summary': {'ok': 0, 'total': 1, 'items': 0}}, f, ensure_ascii=False)
        orig = eq.build
        eq.build = lambda **kw: (_ for _ in ()).throw(AssertionError('不得重复抓取'))
        try:
            res = eq.ensure(self.path, allow_fetch=True, now=NOW)
        finally:
            eq.build = orig
        self.assertEqual(res['action'], 'no_refetch')

    def test_fetch_failure_never_raises(self):
        def boom(**kw):
            raise RuntimeError('网络挂了')
        orig = eq.build
        eq.build = boom
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                res = eq.ensure(self.path, allow_fetch=True, now=NOW)
        finally:
            eq.build = orig
        self.assertEqual(res['action'], 'error')
        self.assertFalse(res['fetched'])


class TestPipeline(unittest.TestCase):
    """网页 / 微信两端的接入形态。"""

    def test_template_marker_between_05_and_07(self):
        with open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8') as f:
            tpl = f.read()
        self.assertIn(bs.EQN_MARK, tpl)
        self.assertLess(tpl.index(bs.RSS_CLOSE), tpl.index(bs.EQN_MARK),
                        '06 节必须在 05 节之后')
        self.assertLess(tpl.index(bs.EQN_MARK), tpl.index('07 / 核心结论'))
        self.assertIn('href="#eqn"', tpl)

    def test_web_injection_idempotent(self):
        data = _mock_data()
        with open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8') as f:
            tpl = f.read()
        once = bs.inject_eqn(tpl, bs.build_eqn_html(data))
        twice = bs.inject_eqn(once, bs.build_eqn_html(data))
        self.assertEqual(once.count(bs.EQN_MARK), 1)
        self.assertEqual(once.count(bs.EQN_CLOSE), 1)
        self.assertEqual(once, twice, '重复注入必须原地替换（幂等）')
        block = once.split(bs.EQN_MARK, 1)[1].split(bs.EQN_CLOSE, 1)[0]
        self.assertIn('方程式新闻', block)
        self.assertNotIn('RSS 情报源', block, '06 节内容不得串进 05 节')

    def test_web_injection_clears_when_unavailable(self):
        with open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8') as f:
            tpl = f.read()
        out = bs.inject_eqn(tpl, bs.build_eqn_html({}))
        block = out.split(bs.EQN_MARK, 1)[1].split(bs.EQN_CLOSE, 1)[0].strip()
        self.assertEqual(block, '')

    def test_wechat_section_and_budget_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            market = os.path.join(tmp, 'market.json')
            with open(market, 'w', encoding='utf-8') as f:
                json.dump({'fetch_date': NOW.strftime('%Y-%m-%d'),
                           'generated_at': NOW.strftime('%Y-%m-%d %H:%M:%S UTC'),
                           'quotes': {'HSI': {'name': '恒生指数', 'last': 25000.0,
                                               'prev_close': 24900.0, 'chg': 100.0,
                                               'pct': 0.4, 'as_of': NOW.strftime('%Y-%m-%d'),
                                               'decimals': 2}},
                           'summary': {'ok': 1, 'total': 1, 'failed': []}}, f, ensure_ascii=False)
            eqn_path = os.path.join(tmp, 'eqn.json')
            with open(eqn_path, 'w', encoding='utf-8') as f:
                json.dump(_mock_data(), f, ensure_ascii=False)
            env = {'MARKET_DATA': market, 'COMMUNITY_DATA': os.path.join(tmp, 'missing.json'),
                   'SENTIMENT_DATA': os.path.join(tmp, 'missing.json'),
                   'MACRO_DATA': os.path.join(tmp, 'missing.json'),
                   'RSSFEEDS_DATA': os.path.join(tmp, 'missing.json'),
                   'EQN_DATA': eqn_path,
                   'FORECAST_HISTORY': os.path.join(tmp, 'hist.json'),
                   'MACRO_AUTO_FETCH': '0', 'RSSFEEDS_AUTO_FETCH': '0',
                   'EQN_AUTO_FETCH': '0'}
            saved = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            try:
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    html, _ts, _tsf = wp.build_single_wechat_html(now=NOW)
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        self.assertIn('06 / 方程式新闻 · 全球快讯', html)
        sec = html.split('06 / 方程式新闻 · 全球快讯', 1)[1].split('07 / 核心结论', 1)[0]
        self.assertIn('方程式新闻', sec)
        self.assertNotIn('eqn.news', html, '内容层不得出现链接/域名')
        self.assertLess(len(html), wp.CONTENT_SAFE_LIMIT, '加了 06 栏仍须落在推送门禁内')
        for phrase in BANNED:
            self.assertNotIn(phrase, html)

    def test_cli_mock_writes_product(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'eqn.json')
            p = subprocess.run(
                [sys.executable, os.path.join(REPO_ROOT, 'eqn_news.py'),
                 '--mock', '--json', out, '--quiet'],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            with open(out, encoding='utf-8') as f:
                data = json.load(f)
            self.assertEqual(data['mode'], 'mock')
            self.assertTrue(eq.availability(data, now=NOW)['ok'])


if __name__ == '__main__':
    unittest.main()
