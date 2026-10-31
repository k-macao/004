#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""49 大财经 RSS 情报源（05 节）回归测试 —— 零联网。

锁死四件事：
  1. **目录就是用户那份 49 源清单**：49 条 / 6 大板块 / 编号 1..49 / key 唯一，
     板块源数与清单一致（10-10-11-8-6-4），每个源都有可用端点或检索载体；
  2. **反幻觉口径**：超窗条目入库前丢弃、无日期条目永远垫底、缺产物时网页 05 节
     与微信 05 栏都显示「今日未获取」，且**不得出现任何历史标题**；
  3. **两端同源**：网页 05 节与微信 05 栏读同一份 rssfeeds_data.json，
     `MACROLIST` / `RSSFEEDS` 注入幂等（重复构建不追加）；
  4. **渲染路径绝不联网**：补抓只在 CLI 入口（bootstrap_rss_feeds / build_site main），
     单测里 GITHUB_ACTIONS=true 也不得触发抓取。
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
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_site as bs                 # noqa: E402
import rss_feeds as rf                  # noqa: E402
import wechat_push as wp                # noqa: E402

NOW = datetime(2026, 10, 31, 6, 0, 0, tzinfo=timezone.utc)
TODAY = '2026-10-31'

MARKET = {'fetch_date': TODAY, 'quotes': {
    'HSI': {'name': '恒生指数', 'last': 26000, 'prev_close': 25800, 'pct': 0.78, 'as_of': TODAY},
    'HSTECH': {'name': '恒生科技指数', 'last': 5200, 'prev_close': 5150, 'pct': 0.97, 'as_of': TODAY},
    'HSCE': {'name': '恒生中国企业指数', 'last': 9000, 'prev_close': 8940, 'pct': 0.67, 'as_of': TODAY},
    'SPX': {'name': '标普 500', 'last': 6000, 'prev_close': 5990, 'pct': 0.17, 'as_of': '2026-10-30'},
    'NDQ': {'name': '纳斯达克', 'last': 21000, 'prev_close': 20900, 'pct': 0.48, 'as_of': '2026-10-30'},
    'DJI': {'name': '道琼斯', 'last': 43000, 'prev_close': 42900, 'pct': 0.23, 'as_of': '2026-10-30'},
    'GOLD': {'name': '现货黄金', 'last': 2650, 'prev_close': 2640, 'pct': 0.38, 'as_of': '2026-10-30'},
    'WTI': {'name': 'WTI 原油', 'last': 92, 'prev_close': 90, 'pct': 2.2, 'as_of': '2026-10-30'},
}}

# 05 节绝不允许出现的历史/写死内容（与 02 栏同一反陈旧基线 + 05 栏自己的哨兵）
STALE_MARKERS = ['628.69', '8 月 12 日', '25,440.17', '今日重点：']


def _mock_data(now=NOW):
    """离线回放产物（mock 分支读 tests/fixtures/RSSFEEDS_MIX.json）。"""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return rf.build(mock=True, quiet=True)


def _unavailable_payload():
    """当次抓过但窗口内 0 条 —— availability() 判为 no_items。"""
    return {
        'generated_at': NOW.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'fetch_date': TODAY, 'mode': 'live',
        'groups': {k: {'label': rf.GROUP_LABELS[k], 'desc': rf.GROUP_DESCS[k], 'sources': []}
                   for k in rf.GROUP_LABELS},
        'summary': {'ok': 0, 'total': len(rf.CATALOG), 'items': 0},
        'window': {'max_age_days': 14, 'since': '2026-10-17'},
    }


def _stale_payload():
    old = (NOW - timedelta(days=9)).strftime('%Y-%m-%d %H:%M:%S UTC')
    return {'generated_at': old, 'fetch_date': old[:10], 'mode': 'live',
            'groups': {'macro': {'label': '一、国际宏观与顶级政经媒体', 'sources': [
                {'id': 1, 'key': 'ECONOMIST', 'name': '经济学人', 'ok': True, 'items': 1,
                 'items_list': [{'title': '九天前的旧闻 ABCXYZ', 'published_date': old[:10]}]}]}},
            'summary': {'ok': 1, 'total': 49, 'items': 1}}


def _plain(html):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))


class TestCatalog(unittest.TestCase):
    """目录 = 用户清单：49 条、六大板块、每条落成具体端点。"""

    def test_catalog_self_check_is_clean(self):
        self.assertEqual(rf.catalog_errors(), [], '目录结构问题必须为零')
        self.assertEqual(len(rf.CATALOG), 49)

    def test_group_counts_match_the_user_list(self):
        for key, expect in (('macro', 10), ('us', 10), ('cn', 11),
                            ('cmdty', 8), ('crypto', 6), ('masters', 4)):
            self.assertEqual(sum(1 for c in rf.CATALOG if c['group'] == key), expect, key)
        self.assertEqual(sum(rf.GROUP_EXPECTED.values()), 49)

    def test_every_source_has_a_real_target_and_honest_kind(self):
        for c in rf.CATALOG:
            urls = rf.resolve_endpoints(c)
            self.assertTrue(urls, f'{c["key"]} 没有任何可请求端点')
            for u in urls:
                self.assertTrue(u.startswith('http'), f'{c["key"]} 端点非 http：{u}')
            self.assertIn(c['verified'], ('rsshub_doc', 'native', 'needs_check', 'carrier'))
            if c['verified'] == 'carrier':
                self.assertTrue(c.get('query'), f'{c["key"]} 用检索载体必须带检索词')
                self.assertIn('news.google.com', ' '.join(urls),
                              f'{c["key"]} 检索载体应落到 Google News RSS')
            if c['verified'] == 'rsshub_doc':
                self.assertTrue(any(u.startswith('rsshub:') for u in c['endpoints']),
                                f'{c["key"]} 标注 rsshub_doc 却没有 RSSHub 路由')

    def test_no_bare_domain_placeholders(self):
        """原清单里的 `https://economist.com` 这类裸域名不能当订阅地址写死。"""
        for c in rf.CATALOG:
            for ep in c['endpoints']:
                self.assertNotRegex(ep, r'^https?://[^/]+/?$',
                                    f'{c["key"]} 端点仍是裸域名：{ep}')

    def test_required_sources_from_the_list_are_present(self):
        names = ' '.join(c['name'] for c in rf.CATALOG)
        for must in ('经济学人', 'FT 全球经济', 'WSJ 经济板块', '路透社', '彭博社', 'CNBC',
                     'ARK Invest', "Barron's", 'Seeking Alpha', "Investor's Business Daily",
                     '华尔街见闻', '财联社', '雪球', '财新网', '东方财富研报', '巨潮资讯',
                     'AASTOCKS', '36 氪', '英为财情', 'Kitco', 'Oilprice', 'FXStreet',
                     'DailyFX', '金十数据', '汇通网', 'Freightos', 'CoinDesk', 'Cointelegraph',
                     '律动 BlockBeats', 'Foresight News', 'The Block', 'Bitcoin Magazine',
                     '霍华德·马克斯', 'Aswath Damodaran', 'QuantStart', 'Project Syndicate'):
            self.assertIn(must, names, f'清单里的「{must}」缺失')

    def test_rsshub_base_is_overridable(self):
        with mock.patch.dict(os.environ, {'RSSHUB_BASE': 'https://hub.example.com,https://b.example'}):
            urls = rf.resolve_endpoints(rf.CATALOG_BY_KEY['CLS'])
        self.assertIn('https://hub.example.com/cls/telegraph', urls)
        self.assertIn('https://b.example/cls/telegraph', urls)


class TestCatalogDoc(unittest.TestCase):
    """docs/rss-49-feeds.md 是目录的渲染产物，不能与 CATALOG 漂移。"""

    DOC = os.path.join(REPO_ROOT, 'docs', 'rss-49-feeds.md')

    def test_doc_is_up_to_date(self):
        with open(self.DOC, encoding='utf-8') as f:
            current = f.read()
        self.assertEqual(current.rstrip('\n'), rf.render_catalog_md().rstrip('\n'),
                         'docs/rss-49-feeds.md 与 CATALOG 不一致 —— '
                         '重跑 python3 rss_feeds.py --catalog-md docs/rss-49-feeds.md')

    def test_doc_lists_every_source_with_its_primary_endpoint(self):
        with open(self.DOC, encoding='utf-8') as f:
            doc = f.read()
        for c in rf.CATALOG:
            self.assertIn(f'| {c["id"]} | {c["name"]} |', doc, f'{c["key"]} 缺行')
            for ep in (c.get('endpoints') or [])[:1]:
                self.assertIn(f'`{ep}`', doc, f'{c["key"]} 主端点缺失')


class TestParsingAndStaleness(unittest.TestCase):
    def test_parses_rss2_atom_and_rdf(self):
        rss2 = ('<rss version="2.0"><channel><item><title>恒指收涨 — 智通财经</title>'
                '<link>https://e.com/1</link><pubDate>Fri, 30 Oct 2026 03:00:00 +0000</pubDate>'
                '<description>&lt;p&gt;南向资金净流入&lt;/p&gt;</description></item></channel></rss>')
        atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title>'
                '<link href="https://e.com/2"/><published>2026-10-30T03:00:00Z</published>'
                '<summary>S</summary></entry></feed>')
        rdf = ('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
               'xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
               '<item><title>R</title><link>https://e.com/3</link>'
               '<dc:date>2026-10-29T10:00:00Z</dc:date></item></rdf:RDF>')
        a, b, c = rf.parse_feed(rss2), rf.parse_feed(atom), rf.parse_feed(rdf)
        self.assertEqual(a[0]['url'], 'https://e.com/1')
        self.assertEqual(a[0]['summary'], '南向资金净流入')
        self.assertEqual(b[0]['url'], 'https://e.com/2')
        self.assertEqual(rf.to_utc(c[0]['published_raw']).strftime('%Y-%m-%d'), '2026-10-29')
        self.assertEqual(rf.parse_feed('<broken'), [])

    def test_stale_dropped_and_undated_never_looks_newest(self):
        items = [{'title': '旧', 'published_date': '2026-01-01', 'age_days': 300.0},
                 {'title': '无日期', 'published_date': None, 'age_days': None},
                 {'title': '新', 'published_date': '2026-10-30', 'age_days': 1.0}]
        kept, dropped = rf.filter_stale(items, max_age_days=14, now=NOW)
        self.assertEqual(dropped['stale_dropped'], 1)
        self.assertEqual(dropped['undated_kept'], 1)
        self.assertEqual([i['title'] for i in rf.sort_items(kept)], ['新', '无日期'])

    def test_publisher_suffix_split_and_dedup(self):
        self.assertEqual(rf.split_publisher_suffix('恒指收涨 182 点 — 智通财经'),
                         ('恒指收涨 182 点', '智通财经'))
        a = rf.normalize([{'title': '同一稿件 — 财联社', 'published_raw': TODAY}],
                         rf.CATALOG_BY_KEY['CLS'], now=NOW)
        b = rf.normalize([{'title': '同一稿件', 'published_raw': TODAY}],
                         rf.CATALOG_BY_KEY['XUEQIU'], now=NOW)
        self.assertEqual(len(rf.dedup_titles(a + b)), 1)

    def test_fetch_source_falls_back_through_endpoints(self):
        """第一个端点失败 → 试回退端点；全失败才抛异常并保留逐次错误。"""
        seen = []

        def fake_get(url, timeout=10):
            seen.append(url)
            if 'rss.html' in url:
                return ('<rss><channel><item><title>CNBC 宏观</title>'
                        '<link>https://e.com/x</link>'
                        f'<pubDate>{NOW.strftime("%a, %d %b %Y %H:%M:%S +0000")}</pubDate>'
                        '</item></channel></rss>', 5.0)
            raise OSError('blocked')

        src = {'id': 6, 'key': 'CNBC', 'name': 'CNBC 宏观', 'lang': 'en', 'group': 'macro',
               'endpoints': ['https://www.cnbc.com/broken.xml',
                             'https://www.cnbc.com/id/10000664/device/rss/rss.html']}
        with mock.patch.object(rf, 'http_get', fake_get):
            items, meta = rf.fetch_source(src, now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(meta['endpoint'], 'https://www.cnbc.com/id/10000664/device/rss/rss.html')
        self.assertEqual(len(seen), 2, '第一个端点失败后必须回退到第二个')
        self.assertTrue(meta['attempts'], '失败的那次请求要留在 attempts 里可追溯')

    def test_fetch_source_raises_with_reasons_when_all_endpoints_fail(self):
        src = {'id': 6, 'key': 'CNBC', 'name': 'CNBC', 'lang': 'en', 'group': 'macro',
               'endpoints': ['https://a.invalid/x', 'https://b.invalid/y']}
        with mock.patch.object(rf, 'http_get', side_effect=OSError('blocked')):
            with self.assertRaises(RuntimeError) as ctx:
                rf.fetch_source(src, now=NOW)
        self.assertIn('a.invalid', str(ctx.exception))
        self.assertIn('b.invalid', str(ctx.exception))

    def test_build_mock_contract(self):
        data = _mock_data()
        self.assertEqual(data['mode'], 'mock')
        self.assertEqual(data['summary']['ok'], 49)
        self.assertGreater(data['summary']['items'], 0)
        self.assertEqual(set(data['groups']), set(rf.GROUP_LABELS))
        for gkey, g in data['groups'].items():
            for s in g['sources']:
                self.assertTrue(s['ok'], s['key'])
                for it in s['items_list']:
                    self.assertRegex(it['published_date'], r'^20\d{2}-\d{2}-\d{2}$')
                    self.assertLessEqual(it['age_days'], data['window']['max_age_days'])
                    self.assertEqual(it['source'], s['key'])

    def test_deadline_skips_remaining_sources(self):
        data = rf.build(mock=True, quiet=True, deadline=1.0)
        self.assertEqual(data['summary']['ok'], 0)
        self.assertTrue(all((m.get('error') or '').startswith('skipped_deadline')
                            for g in data['groups'].values() for m in g['sources']))


class TestAvailability(unittest.TestCase):
    def test_four_unavailable_shapes(self):
        self.assertEqual(rf.availability({})['reason'], 'no_file')
        self.assertEqual(rf.availability([])['reason'], 'bad_type')
        self.assertEqual(rf.availability(_unavailable_payload(), now=NOW)['reason'], 'no_items')
        expired = rf.availability(_stale_payload(), now=NOW)
        self.assertEqual(expired['reason'], 'stale_snapshot')
        self.assertGreater(expired['snapshot_age_days'], 1)
        self.assertEqual(rf.availability(_mock_data(), now=NOW)['reason'], 'ok')


class TestWebSection(unittest.TestCase):
    def test_template_keeps_the_rss_placeholder(self):
        with open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8') as f:
            tpl = f.read()
        self.assertIn(bs.RSS_MARK, tpl, '模板必须保留 <!-- RSSFEEDS --> 占位区')
        self.assertIn('05 / 全球 49 大财经 RSS 情报源', tpl)
        self.assertIn('href="#rss"', tpl, '导航应能跳到 05 节')
        self.assertLess(tpl.index(bs.RSS_MARK), tpl.index('07 / 核心结论'),
                        '05 节应排在 07 结论之前')

    def test_renders_all_49_sources_with_dates(self):
        html = html_mod.unescape(rf.render_web(_mock_data()))
        for c in rf.CATALOG:
            self.assertIn(c['name'], html, f'{c["key"]} 必须出现在 05 节')
        self.assertRegex(html, r'\[\d{1,2} 月 \d{1,2} 日\]', '每条应带发布日期')
        self.assertIn('六大板块', html)
        for label in rf.GROUP_LABELS.values():
            self.assertIn(label, html)

    def test_unavailable_degrades_without_backfilling(self):
        html = rf.render_web({})
        plain = _plain(html)
        self.assertIn('今日未获取', plain)
        self.assertIn('rssfeeds_data.json', plain)
        self.assertIn('不回填历史标题', plain)
        for bad in STALE_MARKERS:
            self.assertNotIn(bad, html)
        for c in rf.CATALOG:
            self.assertNotIn(c['name'], html, '缺产物时不得凭空列出源内标题')

    def test_do_not_show_stale_snapshot_items(self):
        html = rf.render_web(_stale_payload())
        self.assertNotIn('九天前的旧闻 ABCXYZ', html)
        self.assertIn('旧快照', html)

    def test_injection_is_idempotent(self):
        with open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8') as f:
            tpl = f.read()
        page = bs.inject_rss_feeds(tpl, rf.render_web(_mock_data()))
        self.assertEqual(page.count(bs.RSS_MARK), 1)
        self.assertEqual(page.count(bs.RSS_CLOSE), 1)
        again = bs.inject_rss_feeds(page, rf.render_web(_mock_data()))
        self.assertEqual(again.count(bs.RSS_MARK), 1)
        self.assertEqual(again.count(bs.RSS_CLOSE), 1)
        rss_block = again.split(bs.RSS_MARK, 1)[1].split(bs.RSS_CLOSE, 1)[0]
        mac_block = again.split(bs.MACROLIST_MARK, 1)[1].split(bs.MACROLIST_CLOSE, 1)[0]
        self.assertNotIn('RSS 情报源', mac_block, '05 节内容不得串进 02 节')


class TestWechatSection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _env(self, rss_payload=None):
        market = os.path.join(self._tmp.name, 'market_data.json')
        with open(market, 'w', encoding='utf-8') as f:
            json.dump(MARKET, f, ensure_ascii=False)
        if rss_payload is None:
            rss = os.path.join(self._tmp.name, 'missing.json')
        else:
            rss = os.path.join(self._tmp.name, 'rssfeeds_data.json')
            with open(rss, 'w', encoding='utf-8') as f:
                json.dump(rss_payload, f, ensure_ascii=False)
        missing = os.path.join(self._tmp.name, 'missing.json')
        return {'MARKET_DATA': market, 'COMMUNITY_DATA': missing,
                'SENTIMENT_DATA': missing, 'MACRO_DATA': missing,
                'RSSFEEDS_DATA': rss, 'FORECAST_HISTORY': missing,
                'MACRO_AUTO_FETCH': '0', 'RSSFEEDS_AUTO_FETCH': '0'}

    def _render(self, rss_payload):
        env = self._env(rss_payload)
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                html, _ts, _tsf = wp.build_single_wechat_html(now=NOW)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return html

    def test_live_snapshot_renders_every_source_and_stays_within_the_gate(self):
        html = self._render(_mock_data())
        self.assertIn('05 / 全球 49 大财经 RSS 情报源', html)
        rss_part = html_mod.unescape(
            html.split('05 / 全球 49 大财经 RSS 情报源', 1)[1].split('07 / 核心结论', 1)[0])
        for c in rf.CATALOG:
            self.assertIn(c['name'], rss_part, f'{c["key"]} 必须出现在微信 05 栏')
        self.assertIn('RSS 情报源', html)
        self.assertLess(len(html), wp.CONTENT_SAFE_LIMIT,
                        '整个单页（含 00/01/02/03/04/05/07）仍须落在推送字符门禁内')

    def test_empty_snapshot_degrades_and_never_backfills(self):
        html = self._render(None)
        rss_part = html.split('05 / 全球 49 大财经 RSS 情报源', 1)[1].split('07 / 核心结论', 1)[0]
        plain = _plain(rss_part)
        self.assertIn('今日未获取', plain)
        for bad in STALE_MARKERS:
            self.assertNotIn(bad, rss_part)
        self.assertNotIn('Central banks face a harder landing', html,
                         '缺产物时不得把历史标题塞进推送')

    def test_stale_snapshot_is_not_pushed(self):
        html = self._render(_stale_payload())
        self.assertNotIn('九天前的旧闻 ABCXYZ', html)
        rss_part = html.split('05 / 全球 49 大财经 RSS 情报源', 1)[1].split('07 / 核心结论', 1)[0]
        self.assertIn('今日未获取', _plain(rss_part))

    def test_budget_ladder_keeps_every_source(self):
        data = _mock_data()
        for level in ('full', 'standard', 'roster', 'compact'):
            block = html_mod.unescape(rf.render_wechat(data, level=level))
            for c in rf.CATALOG:
                self.assertIn(c['name'], block,
                              f'预算降级到 {level} 也不能删源：{c["key"]}')

    def test_render_path_never_fetches_even_in_ci(self):
        """渲染路径（库函数）在任何环境下都不得联网抓 49 源。"""
        env = {**self._env(None), 'RSSFEEDS_AUTO_FETCH': '1', 'GITHUB_ACTIONS': 'true'}
        # Restore GITHUB_ACTIONS too; leaving it set enables live bootstrap in
        # unrelated tests that run later in the same process.
        with (mock.patch.dict(os.environ, env),
              mock.patch.object(rf, 'http_get', side_effect=AssertionError('渲染路径不得联网')) as fetch):
            self.assertEqual(wp.load_rss_feeds(), {})
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                html, _t, _tf = wp.build_single_wechat_html(now=NOW)
        fetch.assert_not_called()
        self.assertIn('今日未获取', _plain(html))


class TestBootstrapAndBuildSite(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, 'rssfeeds_data.json')

    def test_ensure_fetches_only_when_needed(self):
        with mock.patch.object(rf, 'build', wraps=rf.build) as spy:
            res = rf.ensure(self.path, mock=True, allow_fetch=True, now=NOW, quiet=True)
        self.assertEqual(res['action'], 'fetched')     # no_file → 补抓
        self.assertEqual(spy.call_count, 1)
        self.assertTrue(os.path.exists(self.path))
        with mock.patch.object(rf, 'build') as spy2:
            res2 = rf.ensure(self.path, mock=True, allow_fetch=True, now=NOW, quiet=True)
        self.assertEqual(res2['action'], 'fresh')      # 新鲜产物 → 不再抓
        self.assertEqual(spy2.call_count, 0)

    def test_ensure_never_fetches_when_no_items(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(_unavailable_payload(), f, ensure_ascii=False)
        with mock.patch.object(rf, 'build') as spy:
            res = rf.ensure(self.path, allow_fetch=True, now=NOW, quiet=True)
        self.assertEqual(res['action'], 'no_refetch')
        self.assertEqual(spy.call_count, 0, 'no_items 说明当次已抓过，不应重复烧网络')

    def test_ensure_crash_never_propagates(self):
        with mock.patch.object(rf, 'build', side_effect=RuntimeError('模拟抓取崩溃')):
            res = rf.ensure(self.path, allow_fetch=True, now=NOW, quiet=True)
        self.assertEqual(res['action'], 'error')
        self.assertFalse(os.path.exists(self.path), '崩溃时不得留下半截产物')

    def test_wechat_cli_bootstrap_writes_product(self):
        # bootstrap_rss_feeds 走真实抓取分支，离线环境必然全失败；
        # 这里把 build 换成「离线回放」，只验证「入口补抓 → 落盘 → 纯读取读得到」这条链路。
        real_build = rf.build

        def fake_build(**kw):
            kw.pop('deadline', None)
            return real_build(mock=True, quiet=True)

        with mock.patch.dict(os.environ, {'RSSFEEDS_DATA': self.path, 'RSSFEEDS_AUTO_FETCH': '1'}):
            with mock.patch.object(rf, 'build', side_effect=fake_build) as spy:
                res = wp.bootstrap_rss_feeds()
            data = wp.load_rss_feeds()
        self.assertEqual(res['action'], 'fetched')
        self.assertEqual(spy.call_count, 1)
        self.assertGreater(res['items'], 0)
        self.assertEqual(rf.availability(data)['reason'], 'ok',
                         '入口补抓后，纯读取的 load_rss_feeds 应能读到同一份产物')

    def test_build_site_renders_rss_section_from_bootstrap(self):
        out = os.path.join(self._tmp.name, 'out.html')
        missing = os.path.join(self._tmp.name, 'missing.json')
        p = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, 'build_site.py'),
             '--template', os.path.join(REPO_ROOT, 'report.html'), '--out', out,
             '--data', missing, '--community', missing, '--sentiment', missing,
             '--macro', missing, '--rss', self.path,
             '--macro-no-fetch', '--rss-mock', '--rss-fetch', '--forecast-no-history'],
            capture_output=True, text=True, timeout=240, cwd=self._tmp.name,
            env=dict(os.environ, RSSFEEDS_AUTO_FETCH='0'))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.exists(self.path), '构建期补抓应把产物落到 --rss 指定路径')
        with open(out, encoding='utf-8') as f:
            page = f.read()
        block = html_mod.unescape(page.split(bs.RSS_MARK, 1)[1].split(bs.RSS_CLOSE, 1)[0])
        self.assertNotIn('今日未获取', block)
        for c in rf.CATALOG:
            self.assertIn(c['name'], block)
        self.assertIn('离线回放样本（非实盘）', block)

    def test_build_site_check_mode_never_fetches(self):
        missing = os.path.join(self._tmp.name, 'missing.json')
        p = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, 'build_site.py'),
             '--check', '--template', os.path.join(REPO_ROOT, 'report.html'),
             '--data', missing, '--community', missing, '--sentiment', missing,
             '--macro', missing, '--rss', self.path],
            capture_output=True, text=True, timeout=180, cwd=self._tmp.name,
            env=dict(os.environ, GITHUB_ACTIONS='true', RSSFEEDS_AUTO_FETCH=''))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(os.path.exists(self.path), '--check 是纯校验，绝不联网补抓')


if __name__ == '__main__':
    unittest.main()
