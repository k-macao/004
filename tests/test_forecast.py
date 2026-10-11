#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04 栏「AI 预测 · 未来函数」回归测试 (forecast.py + 网页/微信两端注入)

栏目要求：
    对下一交易日给出逐标的预测（方向 / 预期涨跌幅 / 预测区间 / 点位区间 / 置信度 /
    驱动拆解）与明日盘面倾向，并把每条预测落盘存档、等目标日行情到位后回看命中率。

本文件锁死四件事：
  1. **不是偷看未来的未来函数**（最重要的一条）——
     目标日严格晚于行情基准日；当次行情永远结算不了当次预测；
     只有目标日真实行情到位才计分，过期未结算的判作废而不是硬凑命中。
  2. 结论 100% 由当次数据推导 —— 换一组行情，方向 / 预期 / 倾向必须跟着变；
  3. 数据缺失时显式降级为「今日未获取 · 本栏不预测」，不回填上一版预测、不编点位；
  4. 栏目要素齐全，且网页与微信两端注入幂等、口径一致。
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_site as bs                            # noqa: E402
import char_charts                                 # noqa: E402
import forecast as fc                              # noqa: E402
import macro_data as md                            # noqa: E402
import wechat_push as wp                           # noqa: E402

NOW = datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc)   # 周四
BASE = '2026-09-24'
NEXT = '2026-09-25'                                       # 周五
FRIDAY = '2026-09-25'
MONDAY = '2026-09-28'

# 历史写死内容黑名单（沿用 test_panorama / test_wechat_push_macro 的同一张表）
STALE = ['628.69', '25,440.17', '4,776.44', '五连阳冲击 26,000', '杰克逊霍尔',
         '8 月 12 日', 'IMF 更新的《世界经济展望》']


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _plain(html):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))


def _market(as_of=BASE, **pcts):
    return {'fetch_date': as_of, 'quotes': {
        k: {'name': fc.NAMES[k], 'last': 1000.0 + 10 * i, 'pct': v,
            'as_of': as_of, 'decimals': 2}
        for i, (k, v) in enumerate(pcts.items())}}


BULL = _market(HSI=1.3, HSTECH=2.0, HSCE=1.1, SPX=1.0, NDQ=1.5, DJI=0.7,
               GOLD=-0.5, WTI=-1.2, BRENT=-1.0, USDCNH=-0.3)
BEAR = _market(HSI=-1.5, HSTECH=-2.3, HSCE=-1.2, SPX=-1.2, NDQ=-1.9, DJI=-0.8,
               GOLD=1.1, WTI=2.6, BRENT=2.4, USDCNH=0.4)
FLAT = _market(HSI=0.02, HSTECH=-0.03, HSCE=0.01, SPX=0.02, NDQ=-0.01, DJI=0.03)


# ---------------------------------------------------------------------------
# 1. 未来函数口径：预测未来，不偷看未来
# ---------------------------------------------------------------------------
class TestNoLookahead(unittest.TestCase):
    def test_target_day_is_strictly_after_the_base_day(self):
        d = fc.predict(market=BULL, now=NOW)
        self.assertEqual(d['base_date'], BASE)
        self.assertGreater(d['target_date'], d['base_date'])
        self.assertTrue(d['no_lookahead']['ok'])
        for f in d['forecasts']:
            self.assertGreater(f['target_date'], f['base_date'],
                               '每条预测的目标日都必须晚于它自己的基准日')

    def test_target_day_skips_the_weekend(self):
        self.assertEqual(fc.next_trading_day(FRIDAY), MONDAY)
        self.assertEqual(fc.next_trading_day(BASE), NEXT)
        self.assertEqual(fc.next_trading_day(''), '')
        self.assertEqual(fc.next_trading_day('not-a-date'), '')

    def test_same_day_target_is_flagged_as_contamination(self):
        ok, note = fc.assert_no_lookahead(BASE, BASE)
        self.assertFalse(ok)
        self.assertIn('未来函数污染', note)
        self.assertFalse(fc.assert_no_lookahead(NEXT, BASE)[0], '目标日早于基准日更不允许')
        self.assertFalse(fc.assert_no_lookahead('', NEXT)[0])

    def test_bad_sequence_degrades_instead_of_predicting(self):
        """行情日期解析不出来时宁可不预测，也不产出可能带污染的结论。"""
        broken = {'fetch_date': '', 'quotes': {
            'HSI': {'name': '恒生指数', 'last': 1000.0, 'pct': 1.0, 'as_of': '', 'decimals': 2}}}
        d = fc.predict(market=broken, now=NOW)
        self.assertFalse(d['available'])
        self.assertEqual(d['unavailable_reason'], 'bad_sequence')
        self.assertEqual(d['forecasts'], [])
        self.assertIn('时序不成立', _plain(fc.render_web(d)))

    def test_current_quotes_can_never_settle_current_forecasts(self):
        """全模块最关键的一条：当次行情结算不了当次预测（结构上杜绝 look-ahead）。"""
        d = fc.predict(market=BULL, now=NOW)
        hist = fc.new_history()
        fc.append_forecasts(hist, d)
        self.assertEqual(fc.settle_history(hist, BULL, now=NOW), 0)
        rev = fc.review_history(hist)
        self.assertEqual(rev['settled'], 0)
        self.assertEqual(rev['hits'], 0)
        self.assertIsNone(rev['hit_rate'])
        self.assertEqual(rev['pending'], len(d['forecasts']))

    def test_settles_only_when_the_target_day_quote_actually_arrives(self):
        d = fc.predict(market=BULL, now=NOW)
        hist = fc.new_history()
        fc.append_forecasts(hist, d)

        # 目标日之外的行情不结算（哪怕日期更新）
        other = _market(as_of='2026-09-26', HSI=2.0)
        self.assertEqual(fc.settle_history(hist, other, now=NOW + timedelta(days=2)), 0)

        realized = _market(as_of=NEXT, HSI=0.9, HSTECH=-2.0)
        self.assertEqual(fc.settle_history(hist, realized, now=NOW + timedelta(days=1)), 2)
        rev = fc.review_history(hist)
        self.assertEqual(rev['settled'], 2)
        by = {r['key']: r for r in hist['records'] if r.get('settled')}
        self.assertTrue(by['HSI']['hit'], '预测看涨、实际收涨 → 命中')
        self.assertFalse(by['HSTECH']['hit'], '预测看涨、实际大跌 → 未命中')
        self.assertEqual(by['HSI']['actual_pct'], 0.9)

    def test_expired_forecasts_are_voided_not_counted_as_hits(self):
        d = fc.predict(market=BULL, now=NOW)
        hist = fc.new_history()
        fc.append_forecasts(hist, d)
        late = NOW + timedelta(days=fc.SETTLE_EXPIRE_DAYS + 3)
        fc.settle_history(hist, {'quotes': {}}, now=late)
        self.assertTrue(all(r.get('expired') for r in hist['records']))
        rev = fc.review_history(hist)
        self.assertEqual(rev['settled'], 0)
        self.assertEqual(rev['expired'], len(d['forecasts']))
        self.assertIsNone(rev['hit_rate'], '作废的预测不得凑成命中率')

    def test_small_sample_reports_the_count_not_a_hit_rate_claim(self):
        hist = fc.new_history()
        hist['records'] = [{'key': 'HSI', 'name': '恒生指数', 'settled': True, 'hit': True,
                            'in_band': True, 'error_pct': 0.1, 'target_date': NEXT,
                            'direction': 1, 'sigma': 1.2}]
        rev = fc.review_history(hist)
        self.assertFalse(rev['enough_sample'])
        self.assertIn('样本不足', rev['text'])
        self.assertEqual(rev['settled'], 1)

    def test_rebuilding_the_same_day_does_not_double_count_the_archive(self):
        d = fc.predict(market=BULL, now=NOW)
        hist = fc.new_history()
        fc.append_forecasts(hist, d)
        n1 = len(hist['records'])
        fc.append_forecasts(hist, d)
        self.assertEqual(len(hist['records']), n1, '同一基准日重复构建必须原地覆盖，不灌水')


# ---------------------------------------------------------------------------
# 2. 预测跟着数据走
# ---------------------------------------------------------------------------
class TestForecastEngine(unittest.TestCase):
    def test_direction_follows_the_data_not_a_template(self):
        up = fc.predict(market=BULL, now=NOW)
        down = fc.predict(market=BEAR, now=NOW)
        self.assertEqual(up['stance']['tag'], 'up')
        self.assertEqual(down['stance']['tag'], 'down')
        self.assertGreater(up['headline']['mu_pct'], down['headline']['mu_pct'])
        self.assertEqual(up['headline']['dir_word'], '看涨')
        self.assertEqual(down['headline']['dir_word'], '看跌')

    def test_flat_tape_is_not_forced_into_a_direction(self):
        d = fc.predict(market=FLAT, now=NOW)
        self.assertTrue(all(f['direction'] == 0 for f in d['forecasts']))
        self.assertEqual(d['stance']['tag'], 'flat')
        self.assertEqual(d['stance']['breadth']['up'], 0)
        self.assertEqual(d['stance']['breadth']['down'], 0)

    def test_overstretched_move_mean_reverts_instead_of_extrapolating(self):
        mild = fc.predict(market=_market(HSI=1.0), now=NOW)['forecasts'][0]
        wild = fc.predict(market=_market(HSI=6.0), now=NOW)['forecasts'][0]
        self.assertLess(wild['mu_sigma'], mild['mu_sigma'] * 6,
                        '6% 的单日涨幅不得被线性外推成 6 倍预期')
        term, why = fc._momentum_term(6.0 / fc.SIGMA['HSI'])
        self.assertIn('均值回归', why)
        self.assertLess(term, fc.MOM_CONT * fc.MOM_STRETCH)

    def test_forecast_is_capped_and_band_is_symmetric(self):
        for m in (BULL, BEAR, _market(HSI=9.0, HSTECH=9.0, SPX=9.0, NDQ=9.0, DJI=9.0)):
            for f in fc.predict(market=m, now=NOW)['forecasts']:
                self.assertLessEqual(abs(f['mu_sigma']), fc.MU_CAP + 1e-9)
                self.assertAlmostEqual(f['high_pct'] - f['mu_pct'],
                                       f['mu_pct'] - f['low_pct'], places=2)
                self.assertLess(f['low_pct'], f['high_pct'])

    def test_us_indices_do_not_predict_themselves_through_the_carry_term(self):
        d = fc.predict(market=BULL, now=NOW)
        spx = next(f for f in d['forecasts'] if f['key'] == 'SPX')
        carry = next(x for x in spx['drivers'] if x['name'] == '联动项')
        self.assertEqual(carry['value'], 0.0)
        self.assertIn('不用自己预测自己', carry['why'])

    def test_drivers_sum_to_the_headline_mu(self):
        d = fc.predict(market=BULL, now=NOW)
        f = d['headline']
        total = sum(x['value'] for x in f['drivers'])
        self.assertAlmostEqual(f['mu_sigma'], round(max(-fc.MU_CAP, min(fc.MU_CAP, total)), 3),
                               places=2, msg='驱动拆解必须能复算出合成 μ')

    def test_sentiment_and_macro_move_the_forecast(self):
        sent = {'fetch_date': BASE, 'market': {'net_senti': 0.45, 'news_count': 30,
                                               'risk_score': 5.0, 'neg_share': 10.0}}
        comm = {'fetch_date': BASE, 'communities': [{'verdict_class': 'bull'}] * 11
                + [{'verdict_class': 'bear'}] * 1}
        bare = fc.predict(market=FLAT, now=NOW)['headline']['mu_pct']
        warm = fc.predict(market=FLAT, sentiment=sent, community=comm, now=NOW)['headline']['mu_pct']
        self.assertGreater(warm, bare, '正面情绪必须把预期抬高')

        bearish = {'fetch_date': BASE, 'communities': [{'verdict_class': 'bear'}] * 11}
        cold = fc.predict(market=FLAT, community=bearish, now=NOW)['headline']['mu_pct']
        self.assertLess(cold, bare, '社区一边倒偏空必须把预期压低')

    def test_partial_data_lowers_confidence(self):
        sent = {'fetch_date': BASE, 'market': {'net_senti': 0.2, 'news_count': 20,
                                               'risk_score': 10.0}}
        comm = {'fetch_date': BASE, 'communities': [{'verdict_class': 'bull'}] * 8
                + [{'verdict_class': 'bear'}] * 4}
        macro = md.build(mock=True, quiet=True)
        thin = fc.predict(market=BULL, now=NOW)['stance']['confidence']
        full = fc.predict(market=BULL, macro=macro, sentiment=sent, community=comm,
                          now=NOW)['stance']['confidence']
        self.assertLess(thin, full, '数据覆盖越低，预测置信度必须越低')

    def test_price_band_comes_from_the_live_base_price(self):
        d = fc.predict(market=BULL, now=NOW)
        f = d['headline']
        self.assertIsNotNone(f['price_low'])
        self.assertLess(f['price_low'], f['base_last'] * 1.5)
        self.assertAlmostEqual(f['price_mid'],
                               round(f['base_last'] * (1 + f['mu_pct'] / 100.0), f['decimals']),
                               places=2)
        no_price = {'fetch_date': BASE, 'quotes': {
            'HSI': {'name': '恒生指数', 'last': None, 'pct': 1.0, 'as_of': BASE, 'decimals': 2}}}
        g = fc.predict(market=no_price, now=NOW)['forecasts'][0]
        self.assertIsNone(g['price_low'], '没有基准价就不编点位区间')
        self.assertIsNotNone(g['mu_pct'], '但涨跌幅预测仍可给出')


# ---------------------------------------------------------------------------
# 3. 缺数据就降级，绝不回填
# ---------------------------------------------------------------------------
class TestDegradation(unittest.TestCase):
    def test_no_data_means_no_forecast(self):
        d = fc.predict(now=NOW)
        self.assertFalse(d['available'])
        self.assertEqual(d['forecasts'], [])
        self.assertIsNone(d['headline'])
        self.assertEqual(d['unavailable_reason'], 'no_quotes')
        self.assertEqual(d['coverage'], 0.0)
        self.assertEqual(len(d['data_gaps']), 4)
        for html in (fc.render_web(d), fc.render_wechat(d)):
            plain = _plain(html)
            self.assertIn('本栏不预测', plain)
            self.assertNotIn('{{', html)
            for bad in STALE:
                self.assertNotIn(bad, html)

    def test_all_null_quotes_count_as_a_gap(self):
        nulls = {'fetch_date': BASE, 'quotes': {
            k: {'name': fc.NAMES[k], 'last': None, 'pct': None, 'as_of': None, 'decimals': 2}
            for k in ('HSI', 'HSTECH', 'SPX')}}
        d = fc.predict(market=nulls, now=NOW)
        self.assertFalse(d['available'])
        self.assertTrue(any('行情' in g for g in d['data_gaps']))

    def test_module_contains_no_hardcoded_market_facts(self):
        """预测引擎的可执行代码里不得写死行情/新闻事实与日期（注释、文档串、自检夹具除外）。"""
        import io as _io
        import tokenize
        src = _read(os.path.join(REPO_ROOT, 'forecast.py'))
        src = src.split('def _self_test', 1)[0]         # 自检夹具里的造数不是对外文案
        code = []
        prev = tokenize.INDENT
        for tok in tokenize.generate_tokens(_io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev in (tokenize.INDENT, tokenize.NEWLINE,
                                                        tokenize.NL, tokenize.DEDENT):
                continue                                # 文档字符串
            code.append(tok.string)
            if tok.type not in (tokenize.NL, tokenize.COMMENT):
                prev = tok.type
        body = ' '.join(code)
        for bad in STALE:
            self.assertNotIn(bad, body, f'预测引擎里不得写死历史事实: {bad}')
        self.assertIsNone(re.search(r'20\d{2}-\d{2}-\d{2}', body),
                          '引擎代码不得出现任何硬编码日期（基准日/目标日一律由当次数据带入）')
        self.assertIsNone(re.search(r'\d{2},\d{3}(\.\d+)?', body),
                          '引擎代码不得出现任何硬编码点位')

    def test_history_io_failures_never_break_the_build(self):
        missing = os.path.join(tempfile.gettempdir(), 'forecast-no-such-dir', 'h.json')
        self.assertEqual(fc.load_history(missing), fc.new_history())
        with tempfile.TemporaryDirectory() as tmp:
            broken = os.path.join(tmp, 'broken.json')
            with open(broken, 'w', encoding='utf-8') as f:
                f.write('{ not json at all')
            self.assertEqual(fc.load_history(broken)['records'], [])
            wrong = os.path.join(tmp, 'wrong.json')
            with open(wrong, 'w', encoding='utf-8') as f:
                json.dump([1, 2, 3], f)
            self.assertEqual(fc.load_history(wrong)['records'], [])
        with contextlib.redirect_stderr(io.StringIO()):
            rev = fc.review_from_history(missing, market=BULL, now=NOW, persist=True,
                                         data=fc.predict(market=BULL, now=NOW))
        self.assertIsInstance(rev, dict)


# ---------------------------------------------------------------------------
# 4. 两端注入
# ---------------------------------------------------------------------------
class TestForecastRendering(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.hist = os.path.join(self._tmp.name, 'forecast_history.json')

    def _write(self, name, obj):
        p = os.path.join(self._tmp.name, name)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
        return p

    def test_template_keeps_the_placeholder_and_the_heading(self):
        tpl = _read(os.path.join(REPO_ROOT, 'report.html'))
        self.assertIn(bs.FORECAST_MARK, tpl, '仓库模板必须保留 <!-- FORECAST --> 占位区')
        self.assertIn('AI 预测', tpl, '网页 04 节标题应写明 AI 预测')

    def test_web_section_is_injected_and_idempotent(self):
        tpl = _read(os.path.join(REPO_ROOT, 'report.html'))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            html = bs.build_forecast_html(BULL, {}, {}, {}, now=NOW,
                                          history=True, history_path=self.hist)
        page = bs.inject_forecast(tpl, html)
        block = page.split(bs.FORECAST_MARK, 1)[1].split(bs.FORECAST_CLOSE, 1)[0]
        plain = _plain(block)
        for need in ('明日盘面倾向', '逐标的预测', '驱动拆解', '三路输入信号',
                     '历史预测回看', '预测区间', '置信度'):
            self.assertIn(need, plain, f'网页 04 节缺少栏目要素: {need}')
        self.assertIn(NEXT, block, '注入区必须写明目标日')
        for bad in STALE:
            self.assertNotIn(bad, block, f'04 节只能来自当次数据: {bad}')
        again = bs.inject_forecast(page, html)
        self.assertEqual(again.count(bs.FORECAST_MARK), 1)
        self.assertEqual(again.count(bs.FORECAST_CLOSE), 1)

    def test_build_site_writes_the_archive_once_per_build(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            bs.build_forecast_html(BULL, {}, {}, {}, now=NOW, history=True, history_path=self.hist)
            n1 = len(fc.load_history(self.hist)['records'])
            bs.build_forecast_html(BULL, {}, {}, {}, now=NOW, history=True, history_path=self.hist)
            n2 = len(fc.load_history(self.hist)['records'])
        self.assertEqual(n1, len(BULL['quotes']))
        self.assertEqual(n1, n2, '同一基准日重复构建不得把存档撑大')

    def test_build_site_can_run_without_touching_the_archive(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            html = bs.build_forecast_html(BULL, {}, {}, {}, now=NOW,
                                          history=False, history_path=self.hist)
        self.assertFalse(os.path.exists(self.hist), '--check / --forecast-no-history 不得落盘')
        self.assertIn('明日盘面倾向', _plain(html))

    def test_wechat_section_is_present_and_reads_only(self):
        env = {'MARKET_DATA': self._write('market_data.json', BULL),
               'MACRO_DATA': self._write('macro_data.json', {}),
               'SENTIMENT_DATA': self._write('sentiment_data.json', {}),
               'COMMUNITY_DATA': self._write('community_data.json', {}),
               'FORECAST_HISTORY': self.hist}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                html, _ts, _tsf = wp.build_single_wechat_html(now=NOW)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        plain = _plain(html)
        self.assertIn('AI 预测', plain)
        for need in ('明日盘面倾向', '逐标的预测', '驱动拆解', '历史预测回看'):
            self.assertIn(need, plain, f'微信 04 栏缺少栏目要素: {need}')
        self.assertFalse(os.path.exists(self.hist),
                         '推送路径只读存档；落盘由建站负责，避免 dry-run/emit/push 重复灌水')
        for bad in STALE:
            self.assertNotIn(bad, html.split('AI 预测', 1)[-1].split('07 /', 1)[0])

    def test_wechat_section_degrades_when_quotes_are_missing(self):
        missing = os.path.join(self._tmp.name, 'missing.json')
        env = {k: missing for k in ('MARKET_DATA', 'MACRO_DATA', 'SENTIMENT_DATA',
                                    'COMMUNITY_DATA')}
        env['FORECAST_HISTORY'] = self.hist
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                html, _ts, _tsf = wp.build_single_wechat_html(now=NOW)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        plain = _plain(html)
        self.assertIn('AI 预测', plain, '标题仍在，但正文必须降级')
        self.assertIn('本栏不预测', plain)
        for bad in STALE:
            self.assertNotIn(bad, html)

    def test_wechat_block_degrades_to_fit_the_single_page_budget(self):
        """微信单页有 10 万字符硬上限：04 栏只能用剩余预算，不能把既有栏目挤掉。"""
        d = fc.predict(market=BULL, now=NOW)
        full = fc.render_wechat(d)
        compact = fc.render_wechat(d, compact=True)
        line = fc.render_wechat_line(d)
        self.assertGreater(len(full), len(compact))
        self.assertGreater(len(compact), len(line))
        for html in (full, compact, line):
            self.assertIn('明日盘面倾向' if html is not line else 'AI 预测', _plain(html))
            self.assertNotIn('{{', html)
        # 精简版砍的是详略不是数字：逐标的表仍在，口径不变
        self.assertIn('逐标的预测', _plain(compact))
        self.assertIn(fc.fmt_pct(d['headline']['mu_pct']), compact)
        self.assertIn(fc.fmt_pct(d['headline']['mu_pct']), line)
        self.assertNotIn('◆ 驱动拆解', _plain(compact), '精简版不展开驱动拆解，只指向网页 04 节')
        self.assertIn('见网页 04 节', _plain(compact))

    def test_push_picks_the_richest_block_that_fits(self):
        d = fc.predict(market=BULL, now=NOW)
        slot = wp.FORECAST_SLOT
        roomy = 'x' * 1000 + slot
        with contextlib.redirect_stdout(io.StringIO()):
            rich = wp.fit_forecast_block(roomy, d)
        self.assertIn('驱动拆解', _plain(rich), '预算充足时给完整版')
        self.assertNotIn(slot, rich)

        tight = 'x' * (wp.CONTENT_SAFE_LIMIT - wp.FORECAST_MARGIN - 1500) + slot
        with contextlib.redirect_stdout(io.StringIO()):
            lean = wp.fit_forecast_block(tight, d)
        self.assertNotIn(slot, lean)
        self.assertLess(len(lean), wp.CONTENT_SAFE_LIMIT, '收敛后必须留在安全线内')
        self.assertIn('AI 预测', _plain(lean))

        full_up = 'x' * (wp.CONTENT_SAFE_LIMIT - 10) + slot
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            stub = wp.fit_forecast_block(full_up, d)
        self.assertNotIn(slot, stub)
        self.assertIn('见网页 04 节', _plain(stub), '一点预算都没有时只留指引，不硬塞')

    def test_push_stays_under_the_hard_push_gate(self):
        env = {'FORECAST_HISTORY': self.hist}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                html, _ts, _tsf = wp.build_single_wechat_html(now=NOW)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertNotIn(wp.FORECAST_SLOT, html, '占位槽必须被替换掉')
        self.assertLess(len(html), wp.CONTENT_SAFE_LIMIT,
                        '加了 04 栏之后仍须留在推送门禁之内，否则每日推送会被整条拦下')

    def test_repeated_pair_rule_is_printed_once_not_per_block(self):
        """腾预算的手段之一：配对口径整篇只印一次，不在 20 多个 AI 量化块里重复。"""
        import quant_pair
        # 本测试要数块，因此明确给当日行情：行情不足时配对段一律整段隐藏（另见 test_quant_pair）。
        env = {'FORECAST_HISTORY': self.hist,
               'MARKET_DATA': self._write('market_data.json', BULL)}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                html, _ts, _tsf = wp.build_single_wechat_html(now=NOW)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(html.count(quant_pair.OUTLOOK_NOTE), 1,
                         '配对口径在微信单页里应当只出现一次（文末），其余块共用同一口径')
        self.assertEqual(html.count(quant_pair.RULE.split('|z|', 1)[0]), 1,
                         '规则正文同样只在文末印一次')
        self.assertGreaterEqual(html.count('AI 量化'), 14, '规则去重不得连带把 AI 量化块删掉')
        # 网页版同一口径：卡片不逐块重复，整页由页脚 rule_note_web() 印一次
        head = quant_pair.RULE.split('|z|', 1)[0]
        rec = quant_pair.recommend('恒生科技 恒指',
                                   {'HSTECH': {'pct': 1.4}, 'NDQ': {'pct': 0.2}})
        self.assertNotIn(head, quant_pair.render_web(rec), '网页卡片不再逐块铺规则')
        self.assertIn(head, quant_pair.rule_note_web(), '口径挪到页脚统一给一次')
        self.assertIn(head, quant_pair.render_web(rec, show_rule=True))
        self.assertNotIn(head, quant_pair.render_wechat(rec))
        self.assertIn(head, quant_pair.render_wechat(rec, show_rule=True))
        # 行情不足（两腿不齐）时整段隐藏，规则说明也不跟着漏出来
        no_data = quant_pair.recommend('恒生科技 恒指', {'HSTECH': {'pct': 1.4}})
        self.assertEqual(quant_pair.render_web(no_data), '')
        self.assertEqual(quant_pair.render_wechat(no_data, show_rule=True), '')

    def test_trimming_left_room_for_a_real_forecast_block(self):
        """压缩既有栏目的目的：微信 04 栏要拿到逐标的预测，而不是退成一行摘要。"""
        # 本测试期待真实预测，因此明确提供当日行情；无行情生产路径仍须降级为「今日未获取」。
        missing = os.path.join(self._tmp.name, 'missing.json')
        env = {
            'FORECAST_HISTORY': self.hist,
            'MARKET_DATA': self._write('market_data.json', BULL),
            'COMMUNITY_DATA': missing,
            'SENTIMENT_DATA': missing,
            'MACRO_DATA': missing,
        }
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                html, _ts, _tsf = wp.build_single_wechat_html(now=NOW)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        sec = html.split('04 / AI 预测', 1)[1].split('07 /', 1)[0]
        plain = _plain(sec)
        self.assertIn('逐标的预测', plain, '预算应当够放下逐标的预测表，而不是只剩一行摘要')
        self.assertIn('明日盘面倾向', plain)

    def test_char_charts_never_invent_bars(self):
        empty_html, empty_plain = char_charts.forecast_chart({})
        self.assertIn('不编柱', empty_html)
        self.assertNotIn('\u2588', empty_plain)
        d = fc.predict(market=BULL, now=NOW)
        html, plain = char_charts.forecast_chart(d)
        self.assertIn('恒指', plain)
        self.assertIn('明日倾向', plain)
        rev_html, rev_plain = char_charts.forecast_review_chart(fc.empty_review())
        self.assertIn('不编柱', rev_html)
        self.assertNotIn('\u2588', rev_plain)


if __name__ == '__main__':
    unittest.main()
