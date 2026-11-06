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

from verify_quotes import (  # noqa: E402
    verify_data, verify_item, run_preflight, report_text, _split_pct_gap,
)

FD = '2026-09-16'
CI_FD = '2026-11-06'      # 2026-11-06 VIX 误阻断现场 (CI run 42200783483)


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


class TestPctGapAttribution(unittest.TestCase):
    """涨跌幅偏差归因 (2026-11-06 CI run 42200783483 · VIX 误阻断推送)。

    现场数字: 流水线 VIX last=25.95 pct=−7.91%；yahoo last=26.07 pct=−7.49%。
    两侧反推昨收 28.1790 / 28.1807，相差 0.0018 (0.006%) —— 昨收基准完全一致。
    点位偏差 0.462% 通过 0.5% 闸门，涨跌幅偏差 0.42pp 却撞上 0.35pp 闸门被判
    FAIL 并阻断推送。但 0.42pp 正是那个已被放行的点位偏差的算术必然:
        pct_dev_pp = level_dev_% × (last/prev) = 0.462% × 0.9209 = 0.4258pp
    两道闸门在同一个偏差上互相矛盾，故必须先归因再判罚。
    """

    # CI 现场数字 (原样照抄, 不合成)
    CI_LAST, CI_PCT = 25.95, -7.91
    CI_PREV = 28.18                       # (25.95 − 28.18)/28.18 = −7.9134% → −7.91
    YH_LAST, YH_PCT = 26.07, -7.49

    def ci_quote(self):
        return q(self.CI_LAST, self.CI_PREV, as_of=CI_FD)

    def ci_probes(self):
        return [lambda: probe(self.YH_LAST, pct=self.YH_PCT, as_of=CI_FD,
                              source='yahoo', family='yahoo')]

    def ci_fleet(self):
        """12 个标的 as_of 全部对齐 CI_FD。

        fleet() 默认 as_of=2026-09-16，若直接把 fetch_date 换成 CI_FD，其余 11 个
        标的会因距抓取日期 51 天 > MAX_AGE_DAYS(5) 被判陈旧 FAIL —— 那会掩盖本
        类要验的 VIX 归因行为，端到端断言就成了假失败。
        """
        data = fleet({'VIX': self.ci_quote()})
        data['fetch_date'] = CI_FD
        for qt in data['quotes'].values():
            qt['as_of'] = CI_FD
        return data

    def test_ci_quote_reproduces_the_reported_percentage(self):
        """构造的 quote 必须真的算出 CI 日志里的 −7.91%，否则测试是自说自话。"""
        qt = self.ci_quote()
        self.assertEqual(qt['pct'], self.CI_PCT)
        self.assertEqual(round(qt['last'] - qt['prev_close'], 2), -2.23)

    def test_split_identity_is_exact(self):
        """前收口径效应 + 点位快照效应 ≡ p′ − p (恒等式, 非近似)。"""
        for last, prev, pl, pp in [
            (self.CI_LAST, self.CI_PREV, self.YH_LAST, self.YH_PCT),   # 本次误阻断
            (24713.78, 25274.96, 24713.78, 0.19),                      # 2026-09-16 事故
            (17.20, 34.106, 17.20, 1.715),                             # VIX −49.57% 事故
            (100.0, 100.0, 101.0, 1.0),                                # 纯点位漂移
        ]:
            prev_eff, level_eff = _split_pct_gap(last, prev, pl, pp)
            exact = pp - (last - prev) / prev * 100
            self.assertAlmostEqual(prev_eff + level_eff, exact, delta=1e-3,
                                   msg=f'恒等式不成立: {last}/{prev} vs {pl}/{pp}')

    def test_ci_vix_drift_is_warn_not_fail(self):
        """昨收基准一致的快照漂移 → WARN 放行，不再阻断推送。"""
        item = verify_item('VIX', self.ci_quote(), self.ci_probes(), CI_FD)
        self.assertEqual(item['verdict'], 'warn')
        self.assertFalse(any(c['severity'] == 'fail' for c in item['checks']),
                         '昨收基准一致时不得判 FAIL 阻断推送')
        rec = item['sources'][0]
        self.assertTrue(rec['agree_level'])
        self.assertFalse(rec['agree_pct'])
        self.assertTrue(rec['pct_gap_explained_by_level'])
        # 偏差几乎全部落在点位快照效应上，前收口径效应近零
        self.assertLess(abs(rec['pct_prev_effect_pp']), 0.05)
        self.assertGreater(rec['pct_level_effect_pp'], 0.4)

    def test_explained_gap_stays_visible_in_report(self):
        """放行不等于静默: 分解明细必须出现在人读报告里。"""
        data = self.ci_fleet()
        report = verify_data(data, probes_by_key={'VIX': self.ci_probes()}, fetch_date=CI_FD)
        text = report_text(report)
        self.assertNotIn('已阻断推送', text)
        self.assertIn('前收口径效应', text)
        self.assertIn('点位快照效应', text)
        self.assertIn('非口径矛盾', text)

    def test_preflight_allows_push_on_ci_numbers(self):
        """端到端: CI 现场的 VIX 数字不再让 run_preflight 阻断推送。"""
        import verify_quotes as vq
        data = self.ci_fleet()
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False,
                                         encoding='utf-8') as f:
            json.dump(data, f)
            path = f.name
        try:
            orig = vq.default_probes
            vq.default_probes = lambda key, fd=None: (
                self.ci_probes() if key == 'VIX' else [])
            try:
                ok = vq.run_preflight(data_path=path)
            finally:
                vq.default_probes = orig
            self.assertTrue(ok, '昨收基准一致的快照漂移不应阻断推送')
        finally:
            os.unlink(path)

    def test_prev_close_basis_error_still_fails_when_level_matches(self):
        """归因不得放走真错误: 点位完全一致 (偏差 0%) 但昨收基准错 → 仍 FAIL。

        这是 2026-09-16 恒指事故的形态 —— 点位与来源分毫不差，2.41pp 偏差
        100% 来自前收口径。本例证明修复没有削弱闸门的抓错能力。
        """
        item = verify_item('HSI', q(24713.78, 25274.96, as_of=CI_FD),
                           [lambda: probe(24713.78, pct=0.19, as_of=CI_FD,
                                          source='yahoo', family='yahoo')], CI_FD)
        rec = item['sources'][0]
        self.assertEqual(rec['level_diff_pct'], 0.0)     # 点位毫无偏差
        self.assertTrue(rec['agree_level'])
        self.assertFalse(rec['agree_pct'])
        self.assertFalse(rec.get('pct_gap_explained_by_level'),
                         '前收口径效应超差时不得判定为「已由点位解释」')
        self.assertGreater(abs(rec['pct_prev_effect_pp']), 2.0)
        self.assertEqual(item['verdict'], 'fail')
        self.assertTrue(any(c['name'] == 'sources' and c['severity'] == 'fail'
                            for c in item['checks']))

    def test_explanation_requires_level_agreement(self):
        """点位本身已超差时，即便前收口径效应为 0 也不得豁免 —— 仍按矛盾判罚。"""
        item = verify_item('HSI', q(100.0, 100.0, as_of=CI_FD),
                           [lambda: probe(101.0, pct=1.0, as_of=CI_FD,
                                          source='yahoo', family='yahoo')], CI_FD)
        rec = item['sources'][0]
        self.assertFalse(rec['agree_level'])             # 点位偏差 1% > 0.5%
        self.assertLess(abs(rec['pct_prev_effect_pp']), 1e-6)   # 前收口径完全一致
        self.assertFalse(rec.get('pct_gap_explained_by_level'))
        self.assertEqual(item['verdict'], 'fail')

    def test_attribution_needs_a_usable_prev_close(self):
        """无可信前收时无法归因 → 保持原判罚，不得凭猜测放行。"""
        quote = {'last': 25.95, 'prev_close': None, 'chg': None,
                 'pct': self.CI_PCT, 'as_of': CI_FD}
        item = verify_item('VIX', quote, self.ci_probes(), CI_FD)
        rec = item['sources'][0]
        self.assertNotIn('pct_gap_explained_by_level', rec)
        self.assertTrue(any(c['name'] == 'internal_math' and c['severity'] == 'fail'
                            for c in item['checks']))
        self.assertEqual(item['verdict'], 'fail')

    def test_attribution_works_with_production_probe_shape(self):
        """生产形状是二元组 ('名字', () -> dict)，不是裸可调用对象。

        default_probes() 返回 [('yahoo', lambda: probe_yahoo(...)), ...]，
        其余用例用裸 lambda 注入；本用例按生产原样构造，确认真实路径同样归因，
        避免测试全绿而线上形态另有分支。
        """
        item = verify_item('VIX', self.ci_quote(),
                           [('yahoo', self.ci_probes()[0])], CI_FD)
        rec = item['sources'][0]
        self.assertEqual(rec['source'], 'yahoo')
        self.assertTrue(rec['pct_gap_explained_by_level'])
        self.assertEqual(item['verdict'], 'warn')
        self.assertTrue(any(c['name'] == 'pct' and c['severity'] == 'warn'
                            for c in item['checks']))

    # ---- 归因是逐来源的: 一个来源被豁免不得掩盖另一个来源的真矛盾 ----
    # 基准 quote: last=prev=100 ⇒ pct=0.00，便于把偏差精确指派给某一项成因。
    #   probe(100.4, pct=0.4)  点位差 0.4%(在容差内)、前收效应 0.0pp  → 可豁免的快照漂移
    #   probe(100.0, pct=-2.0) 点位差 0(完全一致)、  前收效应 −2.0pp  → 真前收口径矛盾

    def drift_probe(self, source, family):
        return lambda: probe(100.4, pct=0.4, as_of=CI_FD, source=source, family=family)

    def genuine_probe(self, source, family, pct=-2.0):
        return lambda: probe(100.0, pct=pct, as_of=CI_FD, source=source, family=family)

    def test_explained_drift_does_not_mask_a_genuine_primary_source_conflict(self):
        """主源真矛盾 + 他源已豁免的漂移 → 仍 FAIL，豁免不得掩蔽主源。"""
        item = verify_item('HSI', q(100.0, 100.0, as_of=CI_FD),
                           [self.genuine_probe('yahoo', 'yahoo'),
                            self.drift_probe('stooq_hist', 'stooq')], CI_FD)
        by_fam = {r['family']: r for r in item['sources']}
        self.assertTrue(by_fam['stooq']['pct_gap_explained_by_level'])   # 该源确已豁免
        self.assertFalse(by_fam['yahoo'].get('pct_gap_explained_by_level', False))
        self.assertEqual(item['verdict'], 'fail')
        self.assertTrue(any(c['name'] == 'sources' and c['severity'] == 'fail'
                            and '与主源官方口径矛盾' in c['detail'] for c in item['checks']))

    def test_explained_drift_does_not_mask_two_family_contradiction(self):
        """主源这次是被豁免的一方，但另两族彼此一致地矛盾 → 仍按 ≥2 族判 FAIL。"""
        item = verify_item('HSI', q(100.0, 100.0, as_of=CI_FD),
                           [self.drift_probe('yahoo', 'yahoo'),
                            self.genuine_probe('stooq', 'stooq'),
                            self.genuine_probe('frankfurter', 'ecb', pct=-2.01)], CI_FD)
        by_fam = {r['family']: r for r in item['sources']}
        self.assertTrue(by_fam['yahoo']['pct_gap_explained_by_level'])   # 主源已豁免
        self.assertEqual(item['verdict'], 'fail')
        self.assertTrue(any(c['severity'] == 'fail' and '2 个独立来源族' in c['detail']
                            for c in item['checks']))
        # 豁免仍须留痕，不因整体 FAIL 而被吞掉
        self.assertTrue(any(c['name'] == 'pct' and c['severity'] == 'warn'
                            for c in item['checks']))


if __name__ == '__main__':
    unittest.main()
