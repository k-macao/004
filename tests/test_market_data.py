#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_data.parse_yahoo_chart 单元测试（离线 · 使用真实 API 抓取报文）

背景（2026-09-16 修复）:
  Yahoo chart API range=5d 时 meta.chartPreviousClose 是「5 日窗口前收」
  （约 5 个交易日前收盘），不能当作「前一交易日收盘」计算当日涨跌。
  2026-09-16 实例: 恒指收 24,713.78，chartPreviousClose=25,274.96（9/9 收盘），
  误算 −561.18/−2.22%；真实当日 +46.54/+0.19%（前收 24,667.24，etnet/富途多源印证）。
"""
import unittest
from datetime import datetime, timezone

from market_data import parse_yahoo_chart, make_quote
from verify_quotes import verify_item

# ---- 真实报文（2026-09-16 11:10 UTC 抓自 query1.finance.yahoo.com, 仅保留解析所需字段）----

HSI_5D = {
    'chart': {'result': [{
        'meta': {
            'regularMarketTime': 1789546148,          # 2026-09-16 16:09 HKT（收盘后）
            'regularMarketPrice': 24713.78,
            'fulldayChange': 46.539,                  # Yahoo 官方当日涨跌额
            'regularMarketChangePercent': 0.189,
            'chartPreviousClose': 25274.96,           # ⚠️ 9/9 收盘（窗口前收），非昨收
        },
        'timestamp': [1789003800, 1789090200, 1789349400, 1789435800, 1789522200],
        'indicators': {'quote': [{'close': [24954.470703125, 24805.630859375,
                                            24917.599609375, 24667.240234375,
                                            24713.779296875]}]},
    }], 'error': None},
}

GSPC_5D = {
    'chart': {'result': [{
        'meta': {
            'regularMarketTime': 1789505608,          # 2026-09-15 收盘
            'regularMarketPrice': 7585.73,
            'fulldayChange': -34.25,
            'regularMarketChangePercent': -0.449,
            'chartPreviousClose': 7673.52,            # 9/8 收盘（窗口前收）
        },
        'timestamp': [1788960600, 1789047000, 1789133400, 1789392600, 1789479000],
        'indicators': {'quote': [{'close': [7636.35986328125, 7591.7001953125,
                                            7656.97998046875, 7619.97998046875,
                                            7585.72998046875]}]},
    }], 'error': None},
}


def _legacy(meta_overrides, closes):
    """构造无 fulldayChange 的旧式报文。"""
    result = {
        'meta': dict({'regularMarketPrice': closes[-1],
                      'regularMarketTime': 1789522200}, **meta_overrides),
        # 每条日线都带与 regularMarketTime 对齐的时间戳；不能用 1970 年的假时间戳推断前收。
        'timestamp': [1789522200 - 86400 * (len(closes) - i - 1) for i in range(len(closes))],
        'indicators': {'quote': [{'close': list(closes)}]},
    }
    return {'chart': {'result': [result], 'error': None}}


class TestParseYahooChart(unittest.TestCase):

    def test_hsi_daily_change_not_5day(self):
        """恒指: prev 应为 9/15 收盘 24,667.24 → +46.54/+0.19%（而非 −561.18/−2.22%）。"""
        r = parse_yahoo_chart(HSI_5D)
        self.assertIsNotNone(r)
        last, prev, as_of = r
        self.assertAlmostEqual(last, 24713.78, places=2)
        self.assertAlmostEqual(prev, 24667.24, places=2)   # 非 25274.96（5 日窗口前收）
        self.assertEqual(as_of, '2026-09-16')
        q = make_quote('HSI', '恒生指数', '点', 2, last, prev, as_of, 'yahoo')
        self.assertAlmostEqual(q['chg'], 46.54, places=2)
        self.assertAlmostEqual(q['pct'], 0.19, places=2)   # Yahoo 官方 +0.189%
        self.assertLess(q['pct'], 0.2)                     # 不是 5 日累计 −2.22%

    def test_gspc_daily_change(self):
        """标普 500: 9/15 当日 −34.25/−0.45%（而非窗口前收算出的 −87.79/−1.14%）。"""
        r = parse_yahoo_chart(GSPC_5D)
        last, prev, as_of = r
        self.assertAlmostEqual(prev, 7619.98, places=2)
        self.assertEqual(as_of, '2026-09-15')
        q = make_quote('SPX', '标普 500', '点', 2, last, prev, as_of, 'yahoo')
        self.assertAlmostEqual(q['chg'], -34.25, places=2)
        self.assertAlmostEqual(q['pct'], -0.45, places=2)

    def test_fallback_closes_array(self):
        """无 fulldayChange 时回退日线 closes[-2]。"""
        payload = _legacy({'chartPreviousClose': 9999.0},
                          [25000.0, 24800.0, 24667.24, 24713.78])
        last, prev, _ = parse_yahoo_chart(payload)
        self.assertAlmostEqual(last, 24713.78, places=2)
        self.assertAlmostEqual(prev, 24667.24, places=2)   # 用 closes[-2], 非 9999

    def test_window_previous_close_is_never_assumed_to_be_previous_session(self):
        """单根 K 线缺少可信前收：窗口前收不等于昨收，宁可不显示涨跌。"""
        payload = _legacy({'chartPreviousClose': 25274.96, 'previousClose': 25274.96}, [24713.78])
        _last, prev, _ = parse_yahoo_chart(payload)
        self.assertIsNone(prev)

    def test_vix_inconsistent_full_day_change_uses_regular_market_percentage(self):
        """复现用户日志中的差值；这是合成报文，不声称是当时的 Yahoo 原始响应。"""
        payload = _legacy({
            'regularMarketPrice': 17.20, 'regularMarketChangePercent': 1.715,
            'fulldayPrice': 17.20, 'fulldayChange': -16.906,
            'fulldayChangePercent': 1.715, 'chartPreviousClose': 34.106,
        }, [17.30, 16.91, 17.20])
        last, prev, as_of = parse_yahoo_chart(payload)
        quote = make_quote('VIX', 'VIX 恐慌指数', '点', 2, last, prev, as_of, 'yahoo')
        self.assertAlmostEqual(prev, 16.91, places=2)
        self.assertAlmostEqual(quote['pct'], 1.715, delta=0.01)
        self.assertGreater(quote['chg'], 0)
        probe = {'source': 'yahoo', 'family': 'yahoo', 'last': 17.20, 'pct': 1.715, 'as_of': as_of}
        item = verify_item('VIX', quote, [lambda: probe], as_of)
        self.assertEqual(item['verdict'], 'pass')

    def test_extended_full_day_price_is_not_mixed_with_regular_price(self):
        """全日价格不同时，full-day 涨跌额不能从 regularMarketPrice 减去。"""
        payload = _legacy({
            'regularMarketPrice': 333.02, 'regularMarketChangePercent': 1.099,
            'fulldayPrice': 334.32, 'fulldayChange': 4.89,
            'fulldayChangePercent': 1.485,
        }, [330.0, 329.40, 333.02])
        last, prev, _ = parse_yahoo_chart(payload)
        quote = make_quote('SPX', '标普', '点', 2, last, prev, '2026-09-16', 'yahoo')
        self.assertAlmostEqual(prev, 329.40, places=2)
        self.assertAlmostEqual(quote['pct'], 1.10, places=2)

    def test_last_historical_bar_is_previous_close_if_today_bar_missing(self):
        payload = _legacy({}, [17.00, 16.91])
        result = payload['chart']['result'][0]
        result['meta']['regularMarketPrice'] = 17.20
        result['timestamp'] = [1789349400, 1789435800]  # 9/14, 9/15；行情日期为 9/16
        _last, prev, _ = parse_yahoo_chart(payload)
        self.assertAlmostEqual(prev, 16.91, places=2)

    def test_ambiguous_or_bad_reference_is_not_published(self):
        payload = _legacy({
            'regularMarketPrice': 17.2, 'fulldayPrice': 18.0,
            'fulldayChangePercent': 1.715, 'chartPreviousClose': 34.106,
        }, [17.2])
        result = payload['chart']['result'][0]
        result.pop('timestamp')
        _last, prev, _ = parse_yahoo_chart(payload)
        self.assertIsNone(prev)

    def test_invalid_percent_does_not_raise_or_create_infinite_previous_close(self):
        for value in (-100, float('nan'), float('inf'), 'not a number'):
            with self.subTest(value=value):
                payload = _legacy({'regularMarketChangePercent': value}, [17.2])
                self.assertIsNone(parse_yahoo_chart(payload)[1])

    def test_none_inputs(self):
        self.assertIsNone(parse_yahoo_chart(None))
        self.assertIsNone(parse_yahoo_chart({}))
        self.assertIsNone(parse_yahoo_chart({'chart': {'result': [], 'error': None}}))


if __name__ == '__main__':
    unittest.main()
