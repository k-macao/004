#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
01 栏「每日全球全景扫描」回归测试 (panorama.py + 网页/微信两端注入)

栏目要求（替换原「底层模型与全景推理机制 Multi-Model Alliance」）：
    扫一遍今天全球市场，总结推动股价的 5 大力量。重点关注宏观事件、板块轮动、情绪变化。
    哪些是重点，哪些是噪音。如何利好利空。是否可以做多。

本文件锁死三件事（与 02 栏同一套反陈旧防线）：
  1. 结论 100% 由当次数据推导 —— 换一组行情，力量排序 / 利好利空 / 做多结论必须跟着变；
  2. 数据缺失时显式降级为「未获取」，不回填任何历史叙事与兜底点位；
  3. 栏目要素齐全 —— 5 大力量、重点/噪音判定、如何利好利空、是否可以做多，
     且旧栏目标题「底层模型与全景推理机制 / Multi-Model Alliance」不得再出现。
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

import build_site as bs                            # noqa: E402
import macro_data as md                            # noqa: E402
import panorama                                    # noqa: E402
import wechat_push as wp                           # noqa: E402

NOW = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)

# 被替换掉的旧栏目名：网页 / 微信任意一端重新出现即判定回归
OLD_COLUMN = ['底层模型与全景推理机制', 'Multi-Model Alliance']
# 历史写死内容（沿用 test_wechat_push_macro.py 的同一张黑名单）：01 栏不得回填
STALE = ['628.69', '25,440.17', '五连阳冲击 26,000', '杰克逊霍尔', '8 月 12 日', 'IMF 更新的《世界经济展望》']
FORBIDDEN = OLD_COLUMN + STALE


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _section_01(html, begin, end):
    """截出 01 栏正文（网页按注入哨兵、微信按栏目标题到 02 栏标题）。"""
    return html.split(begin, 1)[1].split(end, 1)[0] if begin in html and end in html else ''


def _quotes(**pcts):
    """按给定涨跌幅造一组行情（点位无关紧要，规则只看 pct 与 as_of）。"""
    names = {'HSI': ('恒生指数', 25700.0), 'HSTECH': ('恒生科技指数', 5900.0),
             'HSCE': ('恒生中国企业指数', 9100.0), 'SPX': ('标普 500', 6300.0),
             'NDQ': ('纳斯达克', 20800.0), 'DJI': ('道琼斯', 42800.0),
             'GOLD': ('现货黄金', 4300.0), 'WTI': ('WTI 原油', 78.0),
             'BRENT': ('布伦特原油', 82.0), 'USDCNH': ('美元/离岸人民币', 7.12)}
    out = {}
    for k, p in pcts.items():
        name, last = names[k]
        out[k] = {'name': name, 'last': last, 'prev_close': last / (1 + p / 100.0),
                  'chg': last - last / (1 + p / 100.0), 'pct': p,
                  'as_of': '2026-09-17', 'decimals': 4 if k == 'USDCNH' else 2}
    return {'fetch_date': '2026-09-17', 'quotes': out}


BULL = _quotes(HSI=1.7, HSTECH=2.6, HSCE=1.5, SPX=1.2, NDQ=1.7, DJI=0.8,
               GOLD=-0.6, WTI=-2.0, BRENT=-1.8, USDCNH=-0.35)
BEAR = _quotes(HSI=-2.0, HSTECH=-3.1, HSCE=-1.8, SPX=-1.5, NDQ=-2.1, DJI=-1.1,
               GOLD=1.3, WTI=3.4, BRENT=3.1, USDCNH=0.45)
FLAT = _quotes(HSI=0.02, HSTECH=0.05, HSCE=0.01, SPX=0.04, NDQ=-0.03, DJI=0.06,
               GOLD=0.05, WTI=0.1, BRENT=0.09, USDCNH=0.01)


class TestPanoramaRules(unittest.TestCase):
    def test_five_forces_and_required_sections(self):
        d = panorama.scan(market=BULL, now=NOW)
        self.assertEqual(len(d['forces']), 5, '栏目要求固定输出「推动股价的 5 大力量」')
        self.assertEqual([f['rank'] for f in d['forces']], [1, 2, 3, 4, 5])
        for f in d['forces']:
            self.assertIn(f['tier'], ('重点', '次要', '噪音'), '每条力量必须标注重点/次要/噪音')
            self.assertIn(f['dir_word'], ('利好', '利空', '中性'), '每条力量必须标注如何利好利空')
            self.assertTrue(f['impact'], '每条力量必须给出利好利空的传导路径')
            self.assertTrue(f['evidence'], '每条力量必须挂当次证据')
        self.assertEqual({fc['category'] for fc in d['focus']},
                         {'宏观事件', '板块轮动', '情绪变化'},
                         '重点关注面必须逐面给结论')
        self.assertIn('can_long', d['verdict'])

    def test_direction_follows_the_data_not_a_template(self):
        bull = panorama.scan(market=BULL, now=NOW)['verdict']
        bear = panorama.scan(market=BEAR, now=NOW)['verdict']
        flat = panorama.scan(market=FLAT, now=NOW)['verdict']
        self.assertGreater(bull['long_score'], 25)
        self.assertEqual(bull['can_long'], 'yes')
        self.assertLess(bear['long_score'], -25)
        self.assertEqual(bear['can_long'], 'no')
        self.assertTrue(-10 < flat['long_score'] < 10, '横盘日必须落在观望区间')
        self.assertNotEqual(bull['stance'], bear['stance'])

    def test_noise_is_filtered_and_excluded_from_the_long_call(self):
        d = panorama.scan(market=FLAT, now=NOW)
        noisy = [f for f in d['all_forces'] if f['tier'] == '噪音']
        self.assertTrue(noisy, '全部低于阈值的波动必须被判为噪音')
        for f in noisy:
            self.assertLessEqual(f['score'], panorama.NOISE_CAP,
                                 '噪音力量的展示分必须压在噪音上限内')
        # 噪音不进入合成分：把噪音方向强行翻正也不应改变结论
        for f in d['all_forces']:
            if f['tier'] == '噪音':
                f['direction'] = 1
        self.assertTrue(-10 < d['verdict']['long_score'] < 10)

    def test_rotation_reads_growth_vs_value_spread(self):
        growth = panorama.scan(market=BULL, now=NOW)
        rot = next(f for f in growth['all_forces'] if f['key'] == 'rotation')
        self.assertEqual(rot['direction'], 1, '恒科强于恒指 → 成长端轮动')
        value = _quotes(HSI=1.2, HSTECH=-0.4, NDQ=0.1, DJI=1.1)
        rot2 = next(f for f in panorama.scan(market=value, now=NOW)['all_forces']
                    if f['key'] == 'rotation')
        self.assertEqual(rot2['direction'], -1, '恒科弱于恒指 → 价值/防御端轮动')

    def test_oil_and_gold_spike_is_bearish_for_equities(self):
        c = next(f for f in panorama.scan(market=BEAR, now=NOW)['all_forces']
                 if f['key'] == 'commodities')
        self.assertEqual(c['direction'], -1)
        c2 = next(f for f in panorama.scan(market=BULL, now=NOW)['all_forces']
                  if f['key'] == 'commodities')
        self.assertEqual(c2['direction'], 1, '油价回落压低输入性通胀 → 利好权益')

    def test_sentiment_force_uses_live_factors_and_community(self):
        sent = {'fetch_date': '2026-09-17',
                'market': {'sent_temp': 75.0, 'label': '极度亢奋', 'net_senti': 0.5,
                           'neg_share': 10.0, 'news_count': 40, 'heat_z': 1.2, 'risk_score': 5.0}}
        comm = {'fetch_date': '2026-09-17', 'communities':
                [{'verdict_class': 'bull'}] * 9 + [{'verdict_class': 'bear'}] * 2}
        f = next(x for x in panorama.scan(market=FLAT, sentiment=sent, community=comm,
                                          now=NOW)['all_forces'] if x['key'] == 'sentiment')
        self.assertEqual(f['direction'], 1)
        self.assertNotEqual(f['tier'], '噪音', '温度计显著偏离中性时不应被当成噪音')
        self.assertIn('75.0', json.dumps(f, ensure_ascii=False))

    def test_high_risk_score_downgrades_the_long_call(self):
        base = {'fetch_date': '2026-09-17',
                'market': {'sent_temp': 55.0, 'label': '中性', 'net_senti': 0.1,
                           'neg_share': 30.0, 'news_count': 20, 'heat_z': 0.1, 'risk_score': 5.0}}
        risky = json.loads(json.dumps(base))
        risky['market']['risk_score'] = 80.0
        a = panorama.scan(market=BULL, sentiment=base, now=NOW)['verdict']
        b = panorama.scan(market=BULL, sentiment=risky, now=NOW)['verdict']
        self.assertLess(b['long_score'], a['long_score'], '突发风险分高时必须下调做多合成分')
        self.assertTrue(any('风险分' in x for x in b['adjust']))


class TestPanoramaDegradation(unittest.TestCase):
    def test_no_data_means_no_story(self):
        d = panorama.scan(now=NOW)
        self.assertEqual(d['forces'], [])
        self.assertEqual(d['verdict']['can_long'], 'unknown')
        self.assertEqual(d['coverage'], 0.0)
        # 五路数据源：行情 / 宏观 / 舆情 / 社区 + RSS 49 源（第 5 路）。
        # RSS 也必须记缺口 —— 否则「05 节没跑」时覆盖度反而虚高。
        self.assertEqual(len(d['data_gaps']), panorama.COVERAGE_ROUTES)
        self.assertTrue(any('RSS' in g for g in d['data_gaps']),
                        'RSS 49 源缺产物必须记一条缺口')
        for html in (panorama.render_web(d), panorama.render_wechat(d)):
            self.assertIn('未获取', html)
            self.assertIn('不回填历史叙事', html)

    def test_all_null_quotes_count_as_a_gap_not_as_coverage(self):
        """抓取失败最常见的形态是「文件在、值全 null」，不能被当成 100% 覆盖。"""
        nulls = {'fetch_date': '2026-09-17', 'quotes': {
            k: {'name': k, 'last': None, 'prev_close': None, 'chg': None,
                'pct': None, 'as_of': None, 'decimals': 2}
            for k in ('HSI', 'HSTECH', 'SPX', 'NDQ', 'DJI')}}
        d = panorama.scan(market=nulls, now=NOW)
        self.assertLess(d['coverage'], 1.0)
        self.assertTrue(any('行情' in g for g in d['data_gaps']))
        self.assertEqual(d['verdict']['can_long'], 'unknown', '没有盘面读数就不能给做多结论')
        self.assertEqual(d['verdict']['score_text'], '合成分不适用',
                         '结论不成立时不得展示会被误读为信号的合成分')

    def test_single_soft_signal_is_not_enough_for_a_long_call(self):
        """只剩情绪面一条软信号时，宁可不给结论，也不能推成「可以做多」。"""
        sent = {'fetch_date': '2026-09-17',
                'market': {'sent_temp': 80.0, 'label': '极度亢奋', 'net_senti': 0.6,
                           'neg_share': 8.0, 'news_count': 50, 'heat_z': 1.5, 'risk_score': 0.0}}
        d = panorama.scan(sentiment=sent, now=NOW)
        self.assertEqual(len([f for f in d['all_forces'] if f['tier'] != '噪音']), 1)
        self.assertEqual(d['verdict']['can_long'], 'unknown')

    def test_breadth_damping_caps_thin_evidence(self):
        thin = _quotes(HSI=1.8, HSTECH=2.6)          # 只有港股盘面 + 轮动两条
        full = panorama.scan(market=BULL, now=NOW)['verdict']['long_score']
        d = panorama.scan(market=thin, now=NOW)['verdict']
        self.assertLess(abs(d['long_score']), abs(full), '证据面窄时合成分必须被阻尼')
        self.assertTrue(any('广度阻尼' in a for a in d['adjust']))

    def test_partial_data_lowers_confidence(self):
        full_sent = {'fetch_date': '2026-09-17',
                     'market': {'sent_temp': 60.0, 'label': '偏热', 'net_senti': 0.2,
                                'neg_share': 20.0, 'news_count': 30, 'heat_z': 0.4,
                                'risk_score': 10.0}}
        comm = {'fetch_date': '2026-09-17', 'communities': [{'verdict_class': 'bull'}] * 8}
        macro = md.build(mock=True, quiet=True)
        partial = panorama.scan(market=BULL, now=NOW)['verdict']['confidence']
        full = panorama.scan(market=BULL, macro=macro, sentiment=full_sent,
                             community=comm, now=NOW)['verdict']['confidence']
        self.assertLess(partial, full, '数据覆盖越低，结论置信度必须越低')

    def test_module_contains_no_hardcoded_market_facts(self):
        """推理引擎的可执行代码里不得写死任何行情/新闻事实（注释与文档字符串除外）。"""
        import io as _io
        import tokenize
        src = _read(os.path.join(REPO_ROOT, 'panorama.py'))
        src = src.split('def _self_test', 1)[0]           # 自检夹具里的造数不是对外文案
        code = []
        prev = tokenize.INDENT
        for tok in tokenize.generate_tokens(_io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev in (tokenize.INDENT, tokenize.NEWLINE,
                                                        tokenize.NL, tokenize.DEDENT):
                continue                                   # 文档字符串
            code.append(tok.string)
            if tok.type not in (tokenize.NL, tokenize.COMMENT):
                prev = tok.type
        body = ' '.join(code)
        for bad in FORBIDDEN:
            self.assertNotIn(bad, body, f'推理引擎里不得写死历史事实/旧栏目名: {bad}')
        self.assertIsNone(re.search(r'20\d{2}-\d{2}-\d{2}', body),
                          '引擎代码不得出现任何硬编码日期（时效一律由当次数据带入）')
        self.assertIsNone(re.search(r'\d{2},\d{3}(\.\d+)?', body),
                          '引擎代码不得出现任何硬编码点位')


class TestPanoramaRendering(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.macro = md.build(mock=True, quiet=True)
        self.sent = {'fetch_date': '2026-09-17',
                     'market': {'sent_temp': 58.0, 'label': '中性', 'net_senti': 0.18,
                                'neg_share': 25.0, 'news_count': 26, 'heat_z': 0.3,
                                'risk_score': 12.0}}
        self.comm = {'fetch_date': '2026-09-17', 'communities':
                     [{'verdict_class': 'bull'}] * 7 + [{'verdict_class': 'bear'}] * 3
                     + [{'verdict_class': 'neutral'}] * 4}

    def _write(self, name, obj):
        p = os.path.join(self._tmp.name, name)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
        return p

    def test_web_section_is_injected_and_idempotent(self):
        tpl = _read(os.path.join(REPO_ROOT, 'report.html'))
        self.assertIn(bs.PANORAMA_MARK, tpl, '仓库模板必须保留 <!-- PANORAMA --> 占位区')
        self.assertIn('每日全球全景扫描', tpl, '网页 01 节标题应为新栏目名')
        for bad in OLD_COLUMN:
            self.assertNotIn(bad, tpl, f'模板里不得残留旧栏目名: {bad}')

        html = bs.build_panorama_html(BULL, self.macro, self.sent, self.comm, now=NOW)
        page = bs.inject_panorama(tpl, html)
        block = _section_01(page, bs.PANORAMA_MARK, bs.PANORAMA_CLOSE)
        plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', block))
        for need in ('推动股价的 5 大力量', '哪些是噪音', '如何利好利空', '是否可以做多',
                     '宏观事件', '板块轮动', '情绪变化'):
            self.assertIn(need, plain, f'网页 01 节缺少栏目要素: {need}')
        for bad in FORBIDDEN:
            self.assertNotIn(bad, block, f'01 节注入区只能来自当次数据: {bad}')
        again = bs.inject_panorama(page, html)
        self.assertEqual(again.count(bs.PANORAMA_MARK), 1)
        self.assertEqual(again.count(bs.PANORAMA_CLOSE), 1)

    def test_wechat_section_replaces_old_column(self):
        env = {'MARKET_DATA': self._write('market_data.json', BULL),
               'MACRO_DATA': self._write('macro_data.json', self.macro),
               'SENTIMENT_DATA': self._write('sentiment_data.json', self.sent),
               'COMMUNITY_DATA': self._write('community_data.json', self.comm)}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                html, _ts, _tsf = wp.build_single_wechat_html(now=NOW)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

        plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))
        self.assertIn('每日全球全景扫描', plain)
        for need in ('推动股价的 5 大力量', '哪些是噪音', '如何利好利空', '是否可以做多',
                     '宏观事件', '板块轮动', '情绪变化'):
            self.assertIn(need, plain, f'微信 01 栏缺少栏目要素: {need}')
        for bad in OLD_COLUMN:
            self.assertNotIn(bad, html, f'微信推送重新出现旧栏目名: {bad}')
        sec01 = _section_01(html, '每日全球全景扫描', '02 / 全球经济与财经动态')
        self.assertTrue(sec01, '应能从推送正文中截出 01 栏')
        for bad in STALE:
            self.assertNotIn(bad, sec01, f'01 栏只能写当次数据: {bad}')
        self.assertNotIn('章鱼 AI 量化策略六大打造步骤', plain, '01 节量化策略说明块已删除，不应再出现')

    def test_wechat_section_degrades_when_all_sources_missing(self):
        missing = os.path.join(self._tmp.name, 'missing.json')
        env = {k: missing for k in ('MARKET_DATA', 'MACRO_DATA', 'SENTIMENT_DATA', 'COMMUNITY_DATA')}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                html, _ts, _tsf = wp.build_single_wechat_html(now=NOW)
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))
        self.assertIn('每日全球全景扫描', plain, '标题仍在，但正文必须降级')
        self.assertIn('本栏不编故事', plain)
        for bad in OLD_COLUMN:
            self.assertNotIn(bad, html)
        sec01 = _section_01(html, '每日全球全景扫描', '02 / 全球经济与财经动态')
        for bad in STALE:
            self.assertNotIn(bad, sec01, '降级态更不允许回填历史叙事')


if __name__ == '__main__':
    unittest.main()
