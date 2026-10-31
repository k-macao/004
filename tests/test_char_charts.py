#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信字符配图：图种对齐 matplotlib，数字只来自当次推送，缺数据不编柱。"""
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import char_charts          # noqa: E402
import wechat_push as wp    # noqa: E402

STALE = ['628.69', '25,440.17', '4,776.44', '8 月 12 日', '杰克逊霍尔']


class TestChartGeometry(unittest.TestCase):
    def test_diverging_bar_keeps_sign_on_its_own_side_of_zero(self):
        pos = char_charts.diverging_bar(1.2, 1.6)
        neg = char_charts.diverging_bar(-0.8, 1.6)
        mid = pos.index('|')
        self.assertIn(char_charts.POS, pos[mid + 1:])
        self.assertNotIn(char_charts.NEG, pos[mid + 1:])
        self.assertIn(char_charts.NEG, neg[:neg.index('|')])
        self.assertNotIn(char_charts.POS, neg[:neg.index('|')])
        self.assertEqual(set(char_charts.diverging_bar(0, 1.6)), set('| '))

    def test_stacked_bar_preserves_share(self):
        bar, total = char_charts.stacked_bar([(char_charts.FULL, 6), (char_charts.DARK, 3), (char_charts.MID, 3), (char_charts.LIGHT, 2)], 24)
        self.assertEqual(len(bar), 24)
        self.assertEqual(total, 14)
        self.assertGreater(bar.count(char_charts.FULL), bar.count(char_charts.DARK))
        self.assertNotIn(' ', bar)

    def test_missing_quotes_do_not_invent_bars(self):
        html, plain = char_charts.quotes_chart({})
        self.assertIn('不编柱', html)
        self.assertNotIn('%', plain)
        for bad in STALE:
            self.assertNotIn(bad, html)

    def test_live_quotes_are_labeled_on_the_bar(self):
        html, plain = char_charts.quotes_chart({
            'HSI': {'pct': 1.25}, 'WTI': {'pct': -0.4}, 'GOLD': {'pct': None},
        })
        self.assertIn('+1.25%', plain)
        self.assertIn('-0.40%', plain)
        self.assertNotIn('黄金', plain)
        self.assertIn('diverging barh', html)
        self.assertIn('<table', html)

    def test_community_composition_uses_the_same_counts(self):
        _html, plain = char_charts.community_chart(
            {'bull': 6, 'bear': 3, 'neutral': 3, 'mixed': 2})
        self.assertIn('共 14 家', plain)
        self.assertIn('偏多6', plain.replace(' ', ''))
        self.assertIn('分歧2', plain.replace(' ', ''))

    def test_opening_long_short_axis_is_data_driven(self):
        _html, plain = char_charts.long_short_chart({
            'long_score': -18.5, 'stance': '偏防御'})
        self.assertIn('空头', plain)
        self.assertIn('多', plain)
        self.assertIn('|', plain)
        self.assertIn('-18.5', plain)
        self.assertIn('固定刻度', plain)

    def test_opening_long_short_axis_does_not_turn_missing_into_zero(self):
        html, plain = char_charts.long_short_chart({'long_score': 0.0, 'can_long': 'unknown'})
        self.assertIn('不把缺失数据画成中性 0', plain)
        self.assertIn('不编柱', html)
        self.assertNotIn('0.0', plain)


class TestPushFigures(unittest.TestCase):
    def _render(self, extra_env=None):
        saved = {}
        keys = ('MARKET_DATA', 'COMMUNITY_DATA', 'SENTIMENT_DATA', 'MACRO_DATA', 'MACRO_AUTO_FETCH')
        for k in keys:
            saved[k] = os.environ.get(k)
        missing = os.path.join(REPO_ROOT, 'tests', 'fixtures', 'no-such-chart.json')
        os.environ.update({k: missing for k in keys[:4]})
        os.environ['MACRO_AUTO_FETCH'] = '0'
        if extra_env:
            os.environ.update(extra_env)
        try:
            html, _ts, _tsf = wp.build_single_wechat_html()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return html

    def test_fallback_push_has_a_figure_and_does_not_invent_bars(self):
        html = self._render()
        self.assertGreaterEqual(html.count('字符配图'), 4)
        self.assertIn('不编柱', html)
        self.assertIn('stacked bar', html)
        self.assertEqual(html.count('03B /'), 1)
        self.assertLess(len(html), 95000)
        for bad in STALE:
            self.assertNotIn(bad, html)

    def test_quote_figure_uses_the_push_snapshot(self):
        # 快照里同时给出港股、美股与商品三类标的，覆盖多序列的字符柱。
        # 配对交易（含配对 z 图）已下线，此处不再需要跨域两腿。
        payload = {'fetch_date': '2026-09-24', 'quotes': {
            'HSI': {'name': '恒生指数', 'pct': 1.2, 'as_of': '2026-09-24'},
            'HSTECH': {'name': '恒生科技指数', 'pct': -0.8, 'as_of': '2026-09-24'},
            'SPX': {'name': '标普 500', 'pct': 0.4, 'as_of': '2026-09-24'},
            'NDQ': {'name': '纳斯达克', 'pct': 1.6, 'as_of': '2026-09-24'},
            'WTI': {'name': 'WTI 原油', 'pct': 2.4, 'as_of': '2026-09-24'},
            'GOLD': {'name': '现货黄金', 'pct': -0.3, 'as_of': '2026-09-24'},
        }}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'market_data.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f)
            html = self._render({'MARKET_DATA': path})
        self.assertIn('+1.20%', html)
        self.assertIn('-0.80%', html)
        self.assertIn('diverging bar', html)
        self.assertNotIn('z=', html, '配对交易已下线，推送里不应再出现配对 z 分数')
        self.assertLess(len(html), 95000)
        for bad in STALE:
            self.assertNotIn(bad, html)


if __name__ == '__main__':
    unittest.main()
