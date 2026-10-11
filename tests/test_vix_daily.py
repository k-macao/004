#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""00 栏「每日 VIX 恐慌指数」离线回归测试。"""
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
for _path in (REPO_ROOT, os.path.join(REPO_ROOT, 'tools')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import build_site as bs             # noqa: E402
import market_data as market_mod    # noqa: E402
import vix_daily                    # noqa: E402
import wechat_push as wp            # noqa: E402

NOW = datetime(2026, 10, 8, 1, 0, tzinfo=timezone.utc)


def market(level=24.60, prev=22.00, as_of='2026-10-07'):
    chg = round(level - prev, 4)
    pct = round(chg / prev * 100, 2)
    return {
        'fetch_date': '2026-10-08',
        'generated_at': '2026-10-08 01:00:00 UTC',
        'mode': 'live',
        'quotes': {
            'VIX': {
                'name': 'VIX 恐慌指数', 'unit': '点', 'decimals': 2,
                'last': level, 'prev_close': prev, 'chg': chg, 'pct': pct,
                'as_of': as_of, 'source': 'yahoo',
            },
        },
        'summary': {'ok': 1, 'total': 12, 'failed': []},
    }


class TestVixModel(unittest.TestCase):
    def test_market_catalog_fetches_vix(self):
        row = [row for row in market_mod.SYMBOLS if row[0] == 'VIX']
        self.assertEqual(1, len(row))
        self.assertEqual('^VIX', row[0][3])
        self.assertIn('VIX', market_mod.DEMO)

    def test_analysis_uses_only_current_quote(self):
        data = vix_daily.analyze(market())
        self.assertTrue(data['available'])
        self.assertEqual('24.60', data['level_text'])
        self.assertEqual('风险定价升温', data['band']['label'])
        self.assertEqual('恐慌定价升温', data['trend']['label'])
        self.assertEqual('2026-10-07', data['as_of'])
        self.assertAlmostEqual(24.60 * (30 / 365) ** 0.5,
                               data['expected_30d_pct'], places=8)

    def test_band_edges_are_explicit(self):
        self.assertEqual('极低波动定价', vix_daily.classify(11.99)['label'])
        self.assertEqual('常态波动定价', vix_daily.classify(12.00)['label'])
        self.assertEqual('风险定价升温', vix_daily.classify(20.00)['label'])
        self.assertEqual('高压恐慌定价', vix_daily.classify(30.00)['label'])
        self.assertEqual('极端压力定价', vix_daily.classify(40.00)['label'])
        web = vix_daily.render_web(vix_daily.analyze(market()))
        # 分档只是本报告的阅读阈值：既不再挂外部来源，也不自称官方评级 / 交易信号
        self.assertIn('12–20 常态', web)
        for bad in ('CTAAgents', 'Cboe 官方评级', 'github.com'):
            self.assertNotIn(bad, web)

    def test_missing_never_becomes_calm_or_zero(self):
        data = vix_daily.analyze({'quotes': {}})
        self.assertFalse(data['available'])
        self.assertFalse(vix_daily.analyze({'quotes': {'VIX': {'last': 0}}})['available'])
        web = vix_daily.render_web(data)
        plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', web))
        self.assertIn('今日未获取', plain)
        self.assertIn('不回填历史点位', plain)
        self.assertNotIn('极低波动定价', plain)
        self.assertNotIn('0.00', plain)

    def test_explanation_is_one_line_and_keeps_q_p_boundary(self):
        """说明文字收成一行：口径 / Q-P 边界保留，长卡片与外部来源不再出现。"""
        web = vix_daily.render_web(vix_daily.analyze(market()))
        plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', web))
        for phrase in ('未来 30 天', '年化', '风险中性', '不是涨跌方向'):
            self.assertIn(phrase, plain)
        self.assertIn('24.60', plain)
        self.assertIn('2026-10-07', plain)
        self.assertNotIn('vix-explain-grid', web)
        self.assertNotIn('vix-source', web)
        for bad in ('CTAAgents', '说明参考', 'Cboe 官方评级'):
            self.assertNotIn(bad, web)


class TestVixWebIntegration(unittest.TestCase):
    def test_template_has_first_section_and_quote_tokens(self):
        with open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8') as handle:
            template = handle.read()
        self.assertIn(bs.VIX_MARK, template)
        self.assertIn('00 / 每日 VIX 恐慌指数', template)
        self.assertIn('{{VIX_LAST}}', template)
        self.assertLess(template.index('id="vix"'), template.index('id="battlefield"'))

    def test_web_injection_is_idempotent(self):
        template = '<div><!-- VIX --></div>'
        block = bs.build_vix_html(market())
        once = bs.inject_vix(template, block)
        twice = bs.inject_vix(once, block)
        self.assertEqual(1, twice.count(bs.VIX_MARK))
        self.assertEqual(1, twice.count(bs.VIX_CLOSE))
        self.assertEqual(1, twice.count('class="vix-panel"'))
        self.assertEqual(once, twice)

    def test_build_tokens_resolve_vix_row(self):
        tokens = bs.build_tokens(market(), NOW)
        self.assertEqual('24.60', tokens['{{VIX_LAST}}'])
        self.assertEqual('+2.60', tokens['{{VIX_CHG}}'])
        self.assertEqual('+11.82%', tokens['{{VIX_PCT}}'])
        self.assertEqual('2026-10-07', tokens['{{VIX_ASOF}}'])


class TestVixWechatIntegration(unittest.TestCase):
    def test_vix_is_the_first_numbered_push_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            market_path = os.path.join(tmp, 'market.json')
            missing = os.path.join(tmp, 'missing.json')
            with open(market_path, 'w', encoding='utf-8') as handle:
                json.dump(market(), handle, ensure_ascii=False)
            env = {
                'MARKET_DATA': market_path,
                'COMMUNITY_DATA': missing,
                'SENTIMENT_DATA': missing,
                'MACRO_DATA': missing,
                'MACRO_AUTO_FETCH': '0',
            }
            old = {key: os.environ.get(key) for key in env}
            os.environ.update(env)
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    rendered, _ts, _tsf = wp.build_single_wechat_html(now=NOW)
            finally:
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertIn('00 / 每日 VIX 恐慌指数', rendered)
        self.assertIn('24.60', rendered)
        self.assertIn('口径', rendered)
        self.assertNotIn('CTAAgents', rendered)
        self.assertLess(rendered.index('00 / 每日 VIX 恐慌指数'),
                        rendered.index('01 / 每日全球全景扫描'))
        self.assertLess(len(rendered), wp.CONTENT_SAFE_LIMIT)


if __name__ == '__main__':
    unittest.main()
