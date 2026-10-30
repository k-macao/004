#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_quotes 推送前全来源校验 — 离线单元测试 (注入假探针, 不联网)

核心回归: 2026-09-16 事故 — 恒指当日 +0.19% 被算成 −2.22% 并推送。
该错误内部自洽 (chg/pct 相互一致), 只有交叉来源比对才能发现,
因此校验器必须能凭「来源一致但与流水线矛盾」判定 FAIL 并阻断推送。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verify_quotes import verify_data, verify_item, run_preflight, report_text  # noqa: E402

FD = '2026-09-16'


def q(last, prev, chg=None, pct=None, as_of=FD):
    """构造合法 quote; chg/pct 默认按 last/prev 精确计算。"""
    if chg is None:
        chg = round(last - prev, 4)
    if pct is None:
        pct = round((last - prev) / prev * 100, 2)
    return {'last': last, 'prev_close': prev, 'chg': chg, 'pct': pct, 'as_of': as_of}


def fleet(overrides=None):
    """全部 12 个行情标的一致数据 (HSI 用 2026-09-16 真实值, 其余合成)。"""
    base = {
        'VIX':    q(18.40, 17.90),
        'HSI':    q(24713.78, 24667.24),
        'HSTECH': q(4790.00, 4820.00),
        'HSCE':   q(8206.37, 8369.05),
        'SPX':    q(7585.73, 7619.98),
        'NDQ':    q(25981.57, 26421.41),
        'DJI':    q(52093.11, 52786.07),
        'GOLD':   q(4388.30, 4408.90),
        'WTI':    q(103.68, 100.05),
        'BRENT':  q(107.58, 104.61),
        'USDCNH': q(6.7077, 6.7118),
        'USDCNY': q(6.6955, 6.7105),
    }
    if overrides:
        base.update(overrides)
    return {'fetch_date': FD, 'mode': 'live', 'quotes': base}


def probe(last, pct=None, as_of=FD, source='stooq_hist', family='stooq'):
    return {'source': source, 'family': family, 'last': last, 'pct': pct, 'as_of': as_of}


class TestIncidentRegression(unittest.TestCase):
    """2026-09-16 事故回归: 内部自洽但与全部来源矛盾 → 必须 FAIL 阻断。"""

    def buggy_data(self):
        # chg/pct 彼此一致 (都基于错误的窗口前收 25274.96), 唯有交叉来源能识破
        return fleet({'HSI': q(24713.78, 25274.96)})

    def probes(self):
        return {
            'HSI': [
                lambda: probe(24713.78, pct=0.19, source='yahoo', family='yahoo'),
                lambda: probe(24713.78, pct=0.19),                    # stooq_hist 重算
                lambda: probe(24713.78, source='stooq', family='stooq'),
            ],
        }

    def test_buggy_pct_fails(self):
        report = verify_data(self.buggy_data(), probes_by_key=self.probes(), fetch_date=FD)
        hsi = [it for it in report['items'] if it['key'] == 'HSI'][0]
        self.assertEqual(hsi['verdict'], 'fail')
        self.assertTrue(any(c['severity'] == 'fail' for c in hsi['checks']))
        self.assertEqual(report['overall'], 'fail')

    def test_preflight_blocks_push(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False,
                                         encoding='utf-8') as f:
            json.dump(self.buggy_data(), f)
            path = f.name
        try:
            # 注入探针: monkeypatch default_probes
            import verify_quotes as vq
            orig = vq.default_probes
            vq.default_probes = lambda key, fd=None: (self.probes().get(key) or [])
            try:
                ok = vq.run_preflight(data_path=path)
            finally:
                vq.default_probes = orig
            self.assertFalse(ok, '推送预检必须阻断错误数据')
        finally:
            os.unlink(path)


class TestVIXIncident(unittest.TestCase):
    def test_wrong_vix_change_still_fails_against_yahoo(self):
        # The reported percentage was -49.57%; 34.106 is a synthetic
        # previous close inferred from that number, not the original payload.
        quote = q(17.20, 34.106)
        yahoo = lambda: probe(17.20, pct=1.715, source='yahoo', family='yahoo')
        item = verify_item('VIX', quote, [yahoo], FD)
        self.assertEqual(item['verdict'], 'fail')
        self.assertTrue(any(c['name'] == 'sources' and c['severity'] == 'fail' for c in item['checks']))

    def test_yahoo_probe_pairs_regular_price_with_regular_percent(self):
        import verify_quotes as vq
        meta = {'regularMarketPrice': 333.02, 'regularMarketChangePercent': 1.099,
                'fulldayPrice': 334.32, 'fulldayChangePercent': 1.485,
                'regularMarketTime': 1789522200}
        payload = {'chart': {'result': [{'meta': meta}]}}
        with mock.patch.object(vq, 'http_get', return_value=json.dumps(payload)):
            result = vq.probe_yahoo('TEST')
        self.assertEqual(result['last'], 333.02)
        self.assertEqual(result['pct'], 1.099)
        # If the regular percentage is absent, a different full-day price
        # cannot supply a comparable percentage.
        meta.pop('regularMarketChangePercent')
        with mock.patch.object(vq, 'http_get', return_value=json.dumps(payload)):
            result = vq.probe_yahoo('TEST')
        self.assertIsNone(result['pct'])

    def test_price_only_source_does_not_claim_percentage_is_verified(self):
        quote = q(17.20, 16.91)
        item = verify_item('VIX', quote, [lambda: probe(17.20, source='yahoo', family='yahoo')], FD)
        self.assertEqual(item['verdict'], 'warn')
        self.assertTrue(any(c['name'] == 'percentage_sources' and c['severity'] == 'warn' for c in item['checks']))

    def test_missing_previous_close_is_warn_not_an_invented_change(self):
        quote = {'last': 17.2, 'prev_close': None, 'chg': None, 'pct': None, 'as_of': FD}
        item = verify_item('VIX', quote, [lambda: probe(17.2, pct=1.715, source='yahoo', family='yahoo')], FD)
        self.assertEqual(item['verdict'], 'warn')
        self.assertTrue(any(c['name'] == 'internal_math' and c['severity'] == 'warn' for c in item['checks']))
        quote['pct'] = -49.57
        bad = verify_item('VIX', quote, [], FD)
        self.assertEqual(bad['verdict'], 'fail')
        self.assertTrue(any(c['name'] == 'internal_math' and c['severity'] == 'fail' for c in bad['checks']))


class TestHappyPath(unittest.TestCase):

    def test_all_consistent_pass(self):
        probes = {}
        data = fleet()
        for key, quote in data['quotes'].items():
            probes[key] = [
                lambda qt=quote: probe(qt['last'], pct=qt['pct'], source='yahoo', family='yahoo'),
                lambda qt=quote: probe(qt['last'], pct=qt['pct']),
            ]
        report = verify_data(data, probes_by_key=probes, fetch_date=FD)
        self.assertEqual(report['summary']['fail'], 0)
        self.assertEqual(report['overall'], 'pass')
        text = report_text(report)
        self.assertIn('允许推送', text)

    def test_corrected_hsi_values(self):
        """修复后的恒指 (+46.54/+0.19%) 与来源一致 → item PASS。"""
        data = fleet()
        probes = {'HSI': [lambda: probe(24713.78, pct=0.19, source='yahoo', family='yahoo'),
                          lambda: probe(24713.78, pct=0.19)]}
        hsi = verify_item('HSI', data['quotes']['HSI'], probes['HSI'], FD)
        self.assertEqual(hsi['verdict'], 'pass')


class TestDegradedPaths(unittest.TestCase):

    def test_internal_math_fail(self):
        """涨跌额与 last/prev_close 不自洽 → FAIL (即使无来源)。"""
        bad = q(100.0, 99.0, chg=5.0, pct=5.05)   # 实际应 +1.0/+1.01%
        item = verify_item('HSI', bad, [], FD)
        self.assertEqual(item['verdict'], 'fail')
        self.assertTrue(any(c['name'] == 'internal_math' and c['severity'] == 'fail'
                            for c in item['checks']))

    def test_missing_quote_warn(self):
        item = verify_item('HSI', {'last': None, 'chg': None, 'pct': None, 'as_of': None}, [], FD)
        self.assertEqual(item['verdict'], 'warn')

    def test_insufficient_sources_warn(self):
        item = verify_item('HSTECH', q(4790.0, 4820.0), [], FD)   # 无任何探针可达
        self.assertEqual(item['verdict'], 'warn')
        self.assertTrue(any('insufficient' in c['detail'] for c in item['checks']))

    def test_single_family_disagreement_warn(self):
        """仅 1 个独立来源族不一致 (无主源佐证) → WARN 不阻断。"""
        item = verify_item('HSI', q(24713.78, 24667.24),
                           [lambda: probe(24400.0, pct=-1.27)], FD)
        self.assertEqual(item['verdict'], 'warn')

    def test_two_families_agree_fail(self):
        """≥2 独立来源族彼此一致但与流水线矛盾 → FAIL。"""
        probes = [lambda: probe(24400.0, pct=-1.27, source='stooq', family='stooq'),
                  lambda: probe(24401.0, pct=-1.26, source='ecb', family='ecb')]
        item = verify_item('HSI', q(24713.78, 24667.24), probes, FD)
        self.assertEqual(item['verdict'], 'fail')

    def test_future_asof_fail(self):
        item = verify_item('HSI', q(24713.78, 24667.24, as_of='2026-09-17'), [], FD)
        self.assertEqual(item['verdict'], 'fail')

    def test_stale_asof_fail(self):
        item = verify_item('HSI', q(24713.78, 24667.24, as_of='2026-09-05'), [], FD)
        self.assertEqual(item['verdict'], 'fail')

    def test_cross_date_source_lenient(self):
        """来源日期更新 (新交易日滚动) + 点位偏差 → 从宽 WARN, 不误阻断早间推送。"""
        item = verify_item('HSI', q(24713.78, 24667.24),
                           [lambda: probe(24200.0, as_of='2026-09-17')], FD)
        self.assertEqual(item['verdict'], 'warn')
        self.assertFalse(any(c['severity'] == 'fail' for c in item['checks']))


if __name__ == '__main__':
    unittest.main()
