#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信推送 02 栏（全球经济与财经动态）回归测试 —— 旧内容事故的长期防线

2026-09-16 核查结论：02 栏曾把 IMF 7 月 WEO、7 月 29 日 FOMC、8 月 12 日 CPI、
南向 7 月净买入 628.69 亿、各行恒指目标价等**新闻事实写死在源码 f-string 里**，
构建时原样重播，页脚却盖当天时间戳（「24h 内最新可读取内容」），因此长期无人发现。
本文件用断言锁死两件事：

  1. 正文里不得再出现任何写死的历史快讯文本 / 历史兜底行情数字；
  2. 快讯缺失时必须显式降级为「今日未获取」，而不是回填旧文。
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_site as bs                       # noqa: E402  网页 02 节 MACROLIST 注入
import macro_data as md                       # noqa: E402
import sentiment_factors as sf                # noqa: E402
import wechat_push as wp                      # noqa: E402

# 曾经写死在推送模板里的历史事实（任何一条重新出现 = 旧文案回归）
STALE_MARKERS = [
    '628.69',                                  # 南向 7 月净买入 628.69 亿港元
    'IMF 更新的《世界经济展望》',
    '9-3 维持联邦基金利率',
    '8 月 12 日',
    '五连阳冲击 26,000',
    '杰克逊霍尔',
    '25,440.17',                               # 写死的恒指点位兜底
    '跌 212.65 点',
    '4,776.44',
    '24h 内最新可读取内容',
]


def _render(env, now=None):
    """在指定数据文件环境下渲染单页推送（屏蔽各源日志）。"""
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            html, _ts, _tsf = wp.build_single_wechat_html(now=now)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    return html


def _no_data_env(tmpdir):
    """把行情/社区/舆情三类数据都指向不存在的文件，让渲染路径确定（不受本地残留 json 影响）。

    MACRO_AUTO_FETCH=0：本文件断言的是「拿不到快讯时的降级形态」，必须锁死构建期
    自动补抓（否则 CI 里 GITHUB_ACTIONS 存在会真的去联网抓一轮，判定就不确定了）。
    """
    missing = os.path.join(tmpdir, 'missing.json')
    return {'MARKET_DATA': missing, 'COMMUNITY_DATA': missing, 'SENTIMENT_DATA': missing,
            'MACRO_DATA': missing, 'MACRO_AUTO_FETCH': '0'}


class TestMacroSection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_wechat_output_uses_esports_palette(self):
        """完整动态微信单页（宏观、舆情、社区、图表与预测）统一使用电竞深色配色。"""
        now = datetime.now(timezone.utc)
        today = now.strftime('%Y-%m-%d')
        paths = {name: os.path.join(self._tmp.name, name) for name in
                 ('market_data.json', 'community_data.json', 'sentiment_data.json',
                  'macro_data.json', 'forecast_history.json')}
        quotes = {
            'HSI': {'name': '恒生指数', 'last': 26000, 'pct': 0.8, 'as_of': today, 'decimals': 2},
            'HSTECH': {'name': '恒生科技指数', 'last': 5200, 'pct': 1.2, 'as_of': today, 'decimals': 2},
            'HSCE': {'name': '恒生中国企业指数', 'last': 9000, 'pct': 0.6, 'as_of': today, 'decimals': 2},
            'SPX': {'name': '标普 500', 'last': 6000, 'pct': 0.4, 'as_of': today, 'decimals': 2},
            'NDQ': {'name': '纳斯达克', 'last': 21000, 'pct': 0.5, 'as_of': today, 'decimals': 2},
            'DJI': {'name': '道琼斯', 'last': 43000, 'pct': 0.3, 'as_of': today, 'decimals': 2},
            'GOLD': {'name': '现货黄金', 'last': 2650, 'pct': -0.2, 'as_of': today, 'decimals': 2},
        }
        with open(paths['market_data.json'], 'w', encoding='utf-8') as f:
            json.dump({'fetch_date': today, 'quotes': quotes,
                       'summary': {'ok': len(quotes), 'total': len(quotes), 'failed': []}}, f)
        with open(paths['community_data.json'], 'w', encoding='utf-8') as f:
            json.dump({'fetch_date': today, 'generated_at': today + ' 08:00:00 UTC',
                       'summary': {'ok': 2, 'total': 2}, 'communities': [
                {'key': 'FUTU', 'id': '01', 'name': '多方情报站', 'icon': '🛰️',
                 'verdict_label': '偏多', 'verdict_class': 'bull',
                 'quote': '恒科走强，北水持续流入', 'verdict': '动能偏强，留意盘中量能确认。'},
                {'key': 'XUEQIU', 'id': '02', 'name': '风险观察站', 'icon': '🔭',
                 'verdict_label': '偏空', 'verdict_class': 'bear',
                 'quote': '短线波动上升，盈利兑现压力仍在', 'verdict': '仓位宜保守，等待风险指标回落。'},
            ]}, f, ensure_ascii=False)
        with open(paths['macro_data.json'], 'w', encoding='utf-8') as f:
            json.dump(md.build(mock=True, quiet=True), f, ensure_ascii=False)
        sf.run(mode='mock', out_path=paths['sentiment_data.json'], verbose=False)

        env = {
            'MARKET_DATA': paths['market_data.json'],
            'COMMUNITY_DATA': paths['community_data.json'],
            'SENTIMENT_DATA': paths['sentiment_data.json'],
            'MACRO_DATA': paths['macro_data.json'],
            'FORECAST_HISTORY': paths['forecast_history.json'],
            'MACRO_AUTO_FETCH': '0',
            'SENTIMENT_SHOW_API_EVAL': '0',
        }
        html = _render(env, now=now)
        for token in ('background:#060811', 'OCTO // COMMAND CENTER', '逐标的预测',
                      'background:#0d1426', 'background:#10172a', 'color:#ff6b7d',
                      'background:#0e1528',
                      '市场舆情因子读数', '宏观快讯', 'color:#4fe5ff', 'color:#b6ff4a'):
            self.assertIn(token, html)
        for legacy in ('background:#eef0f2', 'background:#f8f9fa', 'background:#f4f7f4',
                       'background:#f0f2f0', 'background:#eceef0', 'border:1px solid #d9dce0',
                       'color:#141414', 'color:#007a35', 'color:#39ff14'):
            self.assertNotIn(legacy, html, f'旧浅色或低对比度主题不应出现在微信推送: {legacy}')
        self.assertLess(len(html), wp.CONTENT_SAFE_LIMIT, '全量深色主题仍须满足推送字符安全线')

    def test_push_title_is_consistent(self):
        title = '章鱼 AI·舆情004'
        self.assertEqual(wp.TITLE, title)
        self.assertIn(title, _render(_no_data_env(self._tmp.name)))
        with open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8') as f:
            report = f.read()
        embedded = re.search(
            r'<script id="wechat-parts" type="application/json">(.*?)</script>',
            report, re.S,
        )
        self.assertIsNotNone(embedded)
        payload = json.loads(embedded.group(1))
        self.assertEqual(payload['title'], title)
        for part in payload['parts']:
            self.assertEqual(part['title'], title)
            self.assertIn(title, part['content'])
        self.assertIn("const pushTitle = '" + title + "';", report)
        self.assertIn("part = { title: '" + title + "'", report)
        self.assertIn('<title>' + title + '</title>', report)
        self.assertIn('<h1>' + title + '</h1>', report)
        self.assertNotIn('章鱼 AI 量化策略日报', report)

    def test_eink_board_title_matches(self):
        try:
            import zectrix_client as zc
        except ImportError as exc:            # 墨水屏可选依赖（requests / Pillow）未安装
            self.skipTest(f'墨水屏依赖缺失: {exc}')
        title = '章鱼 AI·舆情004'
        if not os.environ.get('ZECTRIX_BOARD_TITLE'):
            self.assertEqual(zc.BOARD_TITLE, title)
        self.assertTrue(zc.make_page_header('', '').startswith(zc.HEADER_PREFIX + zc.BOARD_TITLE))

    def test_no_hardcoded_history_anywhere(self):
        """快讯未获取时的渲染（最坏情况）也不得出现历史写死文案。"""
        html = _render(_no_data_env(self._tmp.name))
        for bad in STALE_MARKERS:
            self.assertNotIn(bad, html, f'推送正文重新出现写死的历史内容: {bad}')

    def test_missing_macro_degrades_with_explicit_notice(self):
        html = _render(_no_data_env(self._tmp.name))
        self.assertIn('宏观快讯 — 今日未获取', html)
        self.assertIn('macro_data.py', html, '降级提示要直接给出恢复命令')
        self.assertIn('不再回填历史叙事', html)
        self.assertIn('行情未获取', html, '行情缺失应直说，而不是显示兜底点位')
        # 网页 02 节走同一降级方向：缺 macro_data.json 时标注「今日未获取」，不摆历史叙事
        web = bs.build_macro_html({})
        self.assertIn('今日未获取', web)
        self.assertIn('python3 macro_data.py', web)
        self.assertIn('不再回填历史叙事', web)

    def test_macro_items_are_rendered_with_publish_dates(self):
        data = md.build(mock=True, quiet=True)
        path = os.path.join(self._tmp.name, 'macro_data.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        env = _no_data_env(self._tmp.name)
        env['MACRO_DATA'] = path
        html = _render(env)

        titles = [(blk.get('items') or [{}])[0].get('title')
                  for blk in data['categories'].values() if blk.get('items')]
        self.assertGreaterEqual(len(titles), 3, 'mock 回放应至少覆盖 3 个小节')
        for t in titles:
            self.assertIn(wp.re.sub(r'\s+', ' ', t)[:20], wp.re.sub(r'<[^>]+>', ' ', html),
                          f'当次抓到的快讯应渲染进 02 栏: {t}')
        plain = wp.re.sub(r'<[^>]+>', ' ', html)
        self.assertIn('时效窗口', plain)
        self.assertIn('不显示数据来源', plain, '对外沿用舆情层同一脱敏口径')
        self.assertTrue(re.search(r'\[\d{1,2} 月 \d{1,2} 日\]', plain),
                        '每条快讯应自带发布日期，读者才能自行判断时效')
        for bad in STALE_MARKERS:
            self.assertNotIn(bad, html)

        # 网页 02 节（report.html）：build_site 从同一个 macro_data.json 注入 MACROLIST 占位区
        tpl = open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8').read()
        self.assertIn(bs.MACROLIST_MARK, tpl, '仓库模板必须保留 <!-- MACROLIST --> 占位区')
        page = bs.inject_macro_list(tpl, bs.build_macro_html(data))
        self.assertIn(bs.MACROLIST_MARK, page, '占位标记要保留，保证重复构建幂等')
        block = page.split(bs.MACROLIST_MARK, 1)[1].split(bs.MACROLIST_CLOSE, 1)[0]
        plain_block = wp.re.sub(r'\s+', ' ', wp.re.sub(r'<[^>]+>', ' ', block))
        self.assertNotIn('今日未获取', plain_block)
        self.assertIn('时效窗口', plain_block)
        self.assertIn('不显示数据来源', plain_block)
        self.assertTrue(re.search(r'\[\d{1,2} 月 \d{1,2} 日\]', plain_block),
                        '网页每条快讯同样自带发布日期')
        for t in titles:
            self.assertIn(wp.re.sub(r'\s+', ' ', t)[:20], plain_block,
                          f'当次快讯应渲染进网页 02 节: {t}')
        for bad in STALE_MARKERS:
            self.assertNotIn(bad, plain_block, '注入区的快讯只能来自当次抓取')
        # 幂等：以注入结果为模板再注入一次，不得追加出第二份快讯块
        again = bs.inject_macro_list(page, bs.build_macro_html(data))
        self.assertEqual(again.count(bs.MACROLIST_MARK), 1)
        self.assertEqual(again.count(bs.MACROLIST_CLOSE), 1)

    def test_03_overview_counts_are_derived_not_hardcoded(self):
        """社区多空家数必须由当次社区数据推导（此前写死「偏多 6 家」）。"""
        path = os.path.join(self._tmp.name, 'community_data.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'fetch_date': '2026-09-16', 'communities': [
                {'key': 'FUTU', 'id': '01', 'name': '甲', 'icon': '🐮', 'verdict_label': '偏多',
                 'verdict_class': 'bull', 'quote': 'q1', 'verdict': 'v1', 'meta': '综合站内 10 条讨论 · 最新读取 2026-09-16'},
                {'key': 'XUEQIU', 'id': '02', 'name': '乙', 'icon': '❄️', 'verdict_label': '偏空',
                 'verdict_class': 'bear', 'quote': 'q2', 'verdict': 'v2', 'meta': '综合站内 10 条讨论 · 最新读取 2026-09-16'},
                {'key': 'LAOHU', 'id': '03', 'name': '丙', 'icon': '🐯', 'verdict_label': '中性',
                 'verdict_class': 'neutral', 'quote': 'q3', 'verdict': 'v3', 'meta': '综合站内 10 条讨论 · 最新读取 2026-09-16'},
            ]}, f, ensure_ascii=False)
        env = _no_data_env(self._tmp.name)
        env['COMMUNITY_DATA'] = path
        html = wp.re.sub(r'<[^>]+>', ' ', _render(env))
        m = re.search(r'偏多 (\d+) 家 · <.*?>?偏空 (\d+) 家', html)
        self.assertIn('偏多 1 家', html)
        self.assertIn('偏空 1 家', html)
        self.assertIn('中性 1 家', html)
        self.assertIn('3 个境内外核心社区信号', html, '小节标题的源数应随实际社区数变化')
        self.assertNotIn('8 月初五连阳', html)


# 「本次没有可用快讯」的四种形态 —— 原先只有 no_file 一种会降级，其余三种会静默渲染空壳
UNAVAILABLE_CASES = {
    'no_file': None,
    'bad_type': [],
    'no_items': {
        'fetch_date': '2026-09-17', 'generated_at': '2026-09-17 02:00:00 UTC',
        'categories': {'macro': {'label': '宏观 — 全球经济增速与主要央行', 'items': []},
                       'fed': {'label': '美联储利率路径与离岸流动性', 'items': []}},
        'summary': {'ok': 0, 'total': 8, 'kept_items': 0, 'stale_dropped': 12},
        'window': {'max_age_days': 7, 'since': '2026-09-10'},
    },
    'stale_snapshot': {
        'fetch_date': '2026-09-01', 'generated_at': '2026-09-01 00:00:00 UTC',
        'categories': {'macro': {'label': '宏观 — 全球经济增速与主要央行',
                                 'items': [{'title': '陈旧快照里的宏观旧闻 ABCXYZ',
                                            'published_date': '2026-09-01'}]},
                       'hk': {'label': '港股市场 — 恒指与南向资金',
                              'items': [{'title': '陈旧快照里的港股旧闻 ABCXYZ',
                                         'published_date': '2026-09-01'}]}},
        'summary': {'ok': 8, 'total': 8, 'kept_items': 2},
        'window': {'max_age_days': 7, 'since': '2026-08-25'},
    },
}

NOW = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)


class TestMacroAvailabilityFallback(unittest.TestCase):
    """02 栏兜底保障：四种「没有可用快讯」的形态都必须落到同一段「今日未获取」文案。

    此前只有「文件读不到」会降级；文件在但窗口内 0 条、产物写坏、或读到前几天的
    旧快照时，02 栏会渲染成一个空壳（页脚还盖当天时间戳），旧闻还会从 01 / 07 栏漏出。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _macro_path(self, payload):
        if payload is None:
            return os.path.join(self._tmp.name, 'missing.json')
        p = os.path.join(self._tmp.name, 'macro_data.json')
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        return p

    def test_availability_classifies_every_failure_mode(self):
        for reason, payload in UNAVAILABLE_CASES.items():
            with self.subTest(reason=reason):
                a = md.availability(payload if payload is not None else {}, now=NOW)
                self.assertFalse(a['ok'], f'{reason} 必须判定为不可用')
                self.assertEqual(a['reason'], reason)
                self.assertTrue(a['detail'], '必须给出人能看懂的原因')

    def test_availability_accepts_a_fresh_snapshot(self):
        data = md.build(mock=True, quiet=True)
        a = md.availability(data, now=datetime.now(timezone.utc))
        self.assertTrue(a['ok'], f'当次刚构建的快讯应判定为可用: {a}')
        self.assertGreater(a['kept'], 0)

    def test_web_and_wechat_share_one_fallback_copy(self):
        for reason, payload in UNAVAILABLE_CASES.items():
            with self.subTest(reason=reason):
                web = bs.build_macro_html(payload if payload is not None else {}, now=NOW)
                self.assertIn('宏观快讯 — 今日未获取', web)
                self.assertIn('不再回填历史叙事', web)
                self.assertIn('python3 macro_data.py', web)
                self.assertIn('只保留下方当次抓取的实时行情', web)
                self.assertIn('以每次重建后的最新一版为准', web)

                env = _no_data_env(self._tmp.name)
                env['MACRO_DATA'] = self._macro_path(payload)
                wx = _render(env)
                self.assertIn('宏观快讯 — 今日未获取', wx)
                self.assertIn('不再回填历史叙事', wx)
                self.assertIn('python3 macro_data.py', wx)
                self.assertIn('只保留下方当次抓取的实时行情', wx)

    def test_stale_snapshot_never_leaks_into_other_sections(self):
        """过期快照不能只在 02 栏挡住 —— 01 栏证据链与 07 栏结论也必须拿不到旧闻。"""
        payload = UNAVAILABLE_CASES['stale_snapshot']
        env = _no_data_env(self._tmp.name)
        env['MACRO_DATA'] = self._macro_path(payload)
        wx = _render(env)
        self.assertNotIn('ABCXYZ', wx, '整篇推送都不得出现过期快照里的旧闻')

        page = bs.inject_macro_list(
            open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8').read(),
            bs.build_macro_html(payload, now=NOW))
        self.assertNotIn('ABCXYZ', page, '网页 02 节注入区同样不得出现旧闻')

    def test_fallback_states_why_this_time_failed(self):
        """兜底文案要说明「这次为什么没有」，否则线上无从判断是没跑还是源挂了。"""
        cases = {'no_items': '时效窗口', 'stale_snapshot': '旧快照'}
        for reason, needle in cases.items():
            with self.subTest(reason=reason):
                web = bs.build_macro_html(UNAVAILABLE_CASES[reason], now=NOW)
                self.assertIn(needle, web)
                self.assertIn(reason, web, '机器可读的 reason 也要带上，便于排查')


if __name__ == '__main__':
    unittest.main()
