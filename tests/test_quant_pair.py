#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 量化 · 跨域配对回归：策略选择、两标的跨域组合、当次行情推荐、网页/微信都挂在内容后面。"""
import os
import sys
import unittest
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_site as bs          # noqa: E402
import panorama                  # noqa: E402
import quant_pair                # noqa: E402
import wechat_push as wp         # noqa: E402

STALE = ['628.69', '25,440.17', '4,776.44', '8 月 12 日', '杰克逊霍尔']


def _quotes(**pcts):
    return {'quotes': {k: {'name': quant_pair.NAMES.get(k, k), 'pct': v} for k, v in pcts.items()}}


class TestPairEngine(unittest.TestCase):
    def test_always_two_distinct_legs(self):
        rec = quant_pair.recommend('没有主题的一段话', {})
        self.assertNotEqual(rec['leg_a']['key'], rec['leg_b']['key'])
        self.assertEqual(rec['family'], '配对交易')
        self.assertIn('/', rec['pair_label'])

    def test_every_recommendation_is_cross_domain(self):
        """每内容两标的、跨域组合：任何主题 / 任何兜底路径都不许给出同域价差。"""
        quotes = _quotes(HSTECH=1.2, HSI=0.3, HSCE=-0.4, SPX=0.5, NDQ=1.1, DJI=0.2,
                         GOLD=-0.6, WTI=2.4, BRENT=1.8, USDCNH=0.1, USDCNY=0.05)
        themes = ['原油与霍尔木兹', '美联储降息与美债', '内房与高息', '恒生科技轮动',
                  '道指蓝筹', '离岸人民币汇差', '铜锂稀土', '避险对冲', '没有任何主题词',
                  'A股与港股的估值差', '地缘供应链']
        for text in themes:
            for hint in (None, 'tape', 'sentiment'):
                rec = quant_pair.recommend(text, quotes, hint=hint)
                da, db = rec['domain_a'], rec['domain_b']
                self.assertNotEqual(
                    da, db,
                    f'「{text}」(hint={hint}) 给出了同域组合：{rec["pair_label"]} ({rec["domain_pair"]})')
                self.assertTrue(rec['cross_domain'])
                self.assertNotEqual(rec['leg_a']['key'], rec['leg_b']['key'])
                self.assertIn(rec['leg_a']['key'], quant_pair.DOMAIN)
                self.assertIn(rec['leg_b']['key'], quant_pair.DOMAIN)

    def test_catalog_is_cross_domain_and_deduplicated(self):
        self.assertEqual(quant_pair.cross_domain_catalog_errors(), [])
        self.assertTrue(all(quant_pair.is_cross_domain(s) for s in quant_pair.STRATEGIES))
        pairs = {(s['leg_a'], s['leg_b']) for s in quant_pair.STRATEGIES}
        self.assertEqual(len(pairs), len(quant_pair.STRATEGIES))

    def test_missing_quotes_do_not_invent_a_direction(self):
        rec = quant_pair.recommend('原油与炼厂开工', {})
        self.assertEqual(rec['action'], 'no_data')
        self.assertEqual(rec['stance'], '数据不足')
        self.assertNotIn('%', rec['signal'])
        blob = rec['recommendation'] + rec['signal'] + rec['why']
        for bad in STALE:
            self.assertNotIn(bad, blob)

    def test_theme_picks_the_pair_and_quote_picks_the_side(self):
        quotes = _quotes(HSTECH=0.2, HSCE=0.2, WTI=2.4, BRENT=0.2, GOLD=0.1,
                         USDCNH=0.0, SPX=0.3, NDQ=3.2)
        oil = quant_pair.recommend('富途在讨论原油与炼厂价差', quotes, hint='FUTU')
        self.assertEqual(oil['strategy_id'], 'oil_hk')
        self.assertEqual((oil['leg_a']['key'], oil['leg_b']['key']), ('WTI', 'HSCE'))
        self.assertEqual(oil['domain_pair'], '能源 × 港股')
        self.assertEqual(oil['action'], 'short_a_long_b')
        self.assertIn('做空', oil['stance'])
        self.assertIn('做多', oil['stance'])

        weak = quant_pair.recommend('恒生科技相对纳指的成长轮动',
                                    _quotes(HSTECH=-1.6, NDQ=0.4), hint='rotation')
        self.assertEqual(weak['strategy_id'], 'hk_tech_us_tech')
        self.assertEqual(weak['domain_pair'], '港股 × 美股')
        self.assertEqual(weak['action'], 'long_a_short_b')

    def test_small_spread_is_a_wait(self):
        rec = quant_pair.recommend('恒生科技 纳斯达克',
                                   _quotes(HSTECH=0.05, NDQ=0.04), hint='hk_tech_us_tech')
        self.assertEqual(rec['action'], 'wait')
        self.assertEqual(rec['stance'], '观望')
        self.assertIn('不建配对仓', rec['recommendation'])

    def test_renders_name_the_strategy_the_pair_and_the_call(self):
        rec = quant_pair.recommend('美联储利率与黄金', _quotes(GOLD=1.2, USDCNH=-0.3), hint='fed')
        self.assertEqual(rec['strategy_id'], 'gold_fx')
        for blob in (quant_pair.render_web(rec), quant_pair.render_wechat(rec),
                     quant_pair.render_web(rec, compact=True),
                     quant_pair.render_wechat(rec, compact=True),
                     quant_pair.render_wechat_mini(rec)):
            self.assertIn('AI 量化', blob)
            self.assertIn('跨域', blob)
            self.assertIn(rec['strategy_name'], blob)
            self.assertIn('推荐', blob)
        self.assertIn('跨域组合', quant_pair.render_web(rec))
        self.assertIn('data-ai-quant="1"', quant_pair.render_web(rec))
        self.assertIn('data-domain-pair="贵金属 × 汇率"', quant_pair.render_web(rec))
        for label in ('风险因子预测', '走势预测', '未来预测'):
            self.assertIn(label, quant_pair.render_web(rec))
            self.assertIn(label, quant_pair.render_wechat(rec))
        self.assertIn('置信度', rec['outlook']['short'])
        self.assertIn('宏观', quant_pair.recommend('原油与供应', {})['outlook']['risk'])

    def test_mini_render_keeps_pair_and_stance(self):
        """超紧凑版（49 源同一页时用）也必须保留策略 / 跨域两标的 / 推荐。"""
        rec = quant_pair.recommend('美联储利率与黄金', _quotes(GOLD=1.2, USDCNH=-0.3), hint='fed')
        mini = quant_pair.render_wechat_mini(rec)
        self.assertIn(rec['pair_label'], mini)
        self.assertIn(rec['domain_pair'], mini)
        self.assertIn(rec['stance'], mini)
        self.assertLess(len(mini), 400, '超紧凑版要真的紧凑')


class TestAttachedAfterContent(unittest.TestCase):
    def test_each_community_card_gets_a_block(self):
        communities = [
            {'id': '01', 'key': 'FUTU', 'icon': '🐮', 'name': '富途牛牛社区',
             'verdict_label': '偏多', 'verdict_class': 'bull',
             'quote': '讨论原油与炼厂价差', 'verdict': '观望', 'meta': '最新读取 2026-09-24'},
            {'id': '02', 'key': 'WALLSTREETCN', 'icon': '🌐', 'name': '华尔街见闻社区',
             'verdict_label': '中性', 'verdict_class': 'neutral',
             'quote': '美联储利率与黄金', 'verdict': '防守', 'meta': '最新读取 2026-09-24'},
        ]
        html = bs.build_community_html(communities, market=_quotes(
            WTI=1.5, HSCE=0.2, GOLD=0.8, USDCNH=-0.2, HSI=0.3, SPX=0.5))
        self.assertEqual(html.count('class="ai-quant"'), 2)
        self.assertIn('原油 × 港股价值', html)
        self.assertIn('黄金 × 离岸人民币', html)
        self.assertIn('跨域组合', html)
        for dp in ('能源 × 港股', '贵金属 × 汇率'):
            self.assertIn(dp, html)

    def test_panorama_force_cards_carry_the_block(self):
        market = {'fetch_date': '2026-09-17', 'quotes': {
            'HSI': {'name': '恒生指数', 'last': 26000, 'prev_close': 25700, 'pct': 1.2, 'as_of': '2026-09-17'},
            'HSTECH': {'name': '恒生科技指数', 'pct': 2.4, 'as_of': '2026-09-17'},
            'SPX': {'name': '标普 500', 'pct': 0.4, 'as_of': '2026-09-16'},
            'NDQ': {'name': '纳斯达克', 'pct': 0.8, 'as_of': '2026-09-16'},
            'DJI': {'name': '道琼斯', 'pct': 0.2, 'as_of': '2026-09-16'},
            'WTI': {'name': 'WTI 原油', 'pct': 1.5, 'as_of': '2026-09-16'},
            'BRENT': {'name': '布伦特原油', 'pct': 0.4, 'as_of': '2026-09-16'},
        }}
        d = panorama.scan(market=market)
        self.assertTrue(d['forces'])
        self.assertTrue(all(f.get('ai_quant') for f in d['forces']))
        self.assertEqual(len(d['forces'][0]['ai_quant']['pair_label'].split(' / ')), 2)
        web = panorama.render_web(d)
        wx = panorama.render_wechat(d)
        self.assertGreaterEqual(web.count('AI 量化'), len(d['forces']))
        self.assertGreaterEqual(wx.count('AI 量化'), len(d['forces']))
        for bad in STALE:
            self.assertNotIn(bad, web)
            self.assertNotIn(bad, wx)

    def test_macro_items_and_quote_marker_get_a_block(self):
        import macro_data as md
        data = md.build(mock=True, quiet=True)
        full = _quotes(WTI=1.2, BRENT=0.1, HSI=-0.4, HSCE=0.2, HSTECH=0.6, GOLD=0.3,
                       SPX=0.4, NDQ=0.5, DJI=0.1, USDCNH=-0.1, USDCNY=-0.05)
        html = bs.build_macro_html(data, market=full)
        self.assertGreaterEqual(html.count('AI 量化'), 3)
        self.assertIn('跨域组合', html)
        for bad in STALE:
            self.assertNotIn(bad, html)
        # 行情不足：两腿不齐的那些条目**整段隐藏**（不是渲染成「数据不足」），其余照常
        thin = bs.build_macro_html(data, market=_quotes(WTI=1.2, BRENT=0.1, HSI=-0.4, HSCE=0.2))
        self.assertGreaterEqual(thin.count('AI 量化'), 1)
        self.assertLess(thin.count('AI 量化'), html.count('AI 量化'))
        self.assertNotIn('数据不足', thin, '行情不足时不得再铺「数据不足」段')
        tpl = '<div><!-- AI_QUANT:QUOTES --></div><!-- AI_QUANT:VERDICT -->'
        filled = bs.fill_ai_quant_markers(
            tpl, market=_quotes(HSI=0.2, HSCE=-0.8, SPX=0.3, NDQ=0.4))
        self.assertNotIn('AI_QUANT:', filled)
        self.assertGreaterEqual(filled.count('AI 量化'), 2)
        # 只有同域两腿（恒指 / 恒生国企）时给不出跨域组合 → 两个标记都整段隐藏
        hidden = bs.fill_ai_quant_markers(tpl, market=_quotes(HSI=0.2, HSCE=-0.8))
        self.assertNotIn('AI_QUANT:', hidden)
        self.assertNotIn('AI 量化', hidden)
        self.assertNotIn('数据不足', hidden)

    def test_wechat_fallback_hides_the_block_when_quotes_are_missing(self):
        """行情不足：49 张社区卡照旧齐全，但每张卡的 AI 量化段整段隐藏。"""
        missing = os.path.join(REPO_ROOT, 'tests', 'fixtures', 'no-such-quant.json')
        saved = {}
        keys = ('MARKET_DATA', 'COMMUNITY_DATA', 'SENTIMENT_DATA', 'MACRO_DATA', 'MACRO_AUTO_FETCH')
        for k in keys:
            saved[k] = os.environ.get(k)
        os.environ.update({k: missing for k in keys[:4]})
        os.environ['MACRO_AUTO_FETCH'] = '0'
        try:
            html, _ts, _tsf = wp.build_single_wechat_html()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertIn(f'{len(wp.community_mod.COMMUNITIES)} 源动态抓取已上线', html)
        self.assertNotIn('数据不足', html, '行情不足时整段隐藏，不再铺「数据不足」段')
        self.assertNotIn('◆ AI 量化 · 配对交易', html, '块标题也不渲染')
        self.assertNotIn('◆ AI 量化｜', html, '迷你版同样整段隐藏（名录版也不写「数据不足」）')
        self.assertLess(len(html), 95000)
        for bad in STALE:
            self.assertNotIn(bad, html)

    def test_wechat_blocks_come_back_once_the_quotes_are_there(self):
        """同一份 49 源：行情齐全时每张卡照旧各挂一块（两标的跨域组合）。"""
        market = _quotes(HSI=0.2, HSTECH=1.1, HSCE=-0.4, SPX=0.3, NDQ=0.6, DJI=0.1,
                         GOLD=0.4, WTI=1.3, BRENT=0.9, USDCNH=-0.1, USDCNY=-0.05)
        d = wp.community_mod.offline_dataset(market)
        # community_card 读的是卡片的展示字段（label / vclass），与 offline_dataset 的
        # verdict_label / verdict_class 同名不同键，这里按 build_single_wechat_html 的映射对齐
        communities = [{'icon': c.get('icon'), 'id': c.get('id'), 'name': c.get('name'),
                        'label': c.get('verdict_label'), 'vclass': c.get('verdict_class'),
                        'quote': c.get('quote'), 'verdict': c.get('verdict'),
                        'quant': c.get('quant'), 'meta': c.get('meta'), 'key': c.get('key')}
                       for c in d['communities']]
        html = wp.community_section(communities, 'full', quotes=market)
        self.assertEqual(html.count('◆ AI 量化'), len(communities), '每张卡各挂一块')
        self.assertNotIn('数据不足', html)
        self.assertEqual(html.count('跨域组合：'), len(communities))
        self.assertEqual(html.count('推荐：'), len(communities))


class TestAnnotationTextIsNotRepeated(unittest.TestCase):
    """说明文字瘦身：同一句话整页只印一次（口径挪到页脚 / 表头），卡片只留数据。"""

    def _page(self):
        with open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8') as fh:
            template = fh.read()
        now = datetime(2026, 10, 8, 1, 0, tzinfo=timezone.utc)
        data = {'fetch_date': '2026-10-08',
                **_quotes(HSTECH=1.4, NDQ=0.2, GOLD=1.2, USDCNH=-0.3, HSI=0.6, SPX=0.4)}
        return bs.substitute(template, bs.build_tokens(data, now))

    def test_pair_rule_is_printed_once_in_the_page_footer(self):
        page = self._page()
        head = quant_pair.RULE.split('|z|', 1)[0]
        self.assertEqual(1, page.count(head), '配对口径整页只印一次（页脚）')
        self.assertEqual(1, page.count(quant_pair.OUTLOOK_NOTE),
                         '48h 预测口径同样只印一次')

    def test_quant_metric_glosses_are_printed_once_in_the_section_head(self):
        cards = bs.build_quant_html({
            'sentiment': {'display': '+0.11 (中性)', 'desc': '由新闻对应文本片段的情绪，排除无关主体干扰'},
            'event': {'label': '资金流向', 'desc': '精准匹配业绩、并购、监管等场景'},
            'relevance': {'display': '97/100', 'desc': '衡量新闻与标的的关联程度，过滤无效噪音'},
            'novelty': {'display': '58/100 (转载/跟踪)', 'desc': '区分新闻首发与转载，识别信息冲击强度'},
        })
        self.assertIn('+0.11 (中性)', cards)
        for gloss in ('排除无关主体干扰', '过滤无效噪音', '识别信息冲击强度'):
            self.assertNotIn(gloss, cards, '四项指标口径只在 03 栏表头给一次')
        with open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8') as fh:
            self.assertIn('指标口径', fh.read())

    def test_cards_drop_the_strategy_logic_gloss(self):
        rec = quant_pair.recommend('美联储利率与黄金', _quotes(GOLD=1.2, USDCNH=-0.3), hint='fed')
        self.assertIn(rec['why'], quant_pair.render_web(rec))
        self.assertNotIn(rec['logic'], quant_pair.render_web(rec))
        self.assertNotIn(rec['logic'], quant_pair.render_wechat(rec))

    def test_brief_compact_keeps_strategy_and_call_only(self):
        rec = quant_pair.recommend('恒生科技 恒指', _quotes(HSTECH=1.4, NDQ=0.2))
        brief = quant_pair.render_web(rec, compact=True, brief=True)
        self.assertIn('跨域组合', brief)
        self.assertIn('推荐', brief)
        self.assertNotIn('风险因子', brief)
        self.assertNotIn('风险因子', quant_pair.render_wechat(rec, compact=True, brief=True))


class TestGrayscaleMutedDisplay(unittest.TestCase):
    """AI 量化标题与栏目内容统一采用中性灰度字体、减弱显示，不使用霓虹高亮抢夺主文视线。"""

    LOUD_TOKENS = (
        'var(--cyan)', 'var(--pink)', 'var(--neon)', 'var(--violet)', 'var(--danger)',
        '#4fe5ff', '#ff4d9a', '#b6ff4a', '#9673ff', '#cbd6ee', '#dce5fb', '#6f55b8',
    )

    def test_web_ai_quant_css_uses_grayscale_and_muted_weight(self):
        import re
        with open(os.path.join(REPO_ROOT, 'report.html'), encoding='utf-8') as fh:
            html = fh.read()
        m = re.search(r'\.ai-quant\s*\{.*?(?=\.pixel-list\s*\{)', html, re.S)
        self.assertIsNotNone(m, 'report.html 应包含 .ai-quant 样式段')
        css_block = m.group(0)
        for loud in self.LOUD_TOKENS:
            self.assertNotIn(loud, css_block, f'.ai-quant 样式不得再使用高亮色 {loud}')
        for hex_color in re.findall(r'#([0-9a-fA-F]{6})\b', css_block):
            r, g, b = hex_color[0:2].lower(), hex_color[2:4].lower(), hex_color[4:6].lower()
            self.assertTrue(r == g == b, f'.ai-quant 色值 #{hex_color} 应为纯中性灰度 (R=G=B)')
        self.assertIn('.ai-quant strong', css_block, '栏目内加粗标签也应纳入灰度减弱控制')
        self.assertIn('.ai-quant .ai-quant-list li', css_block, '列表项权重高于 .pixel-list li，避免回退高亮')
        self.assertIn('class="ai-quant-rule"', html)

    def test_wechat_ai_quant_renders_use_grayscale_and_no_neon_accents(self):
        rec = quant_pair.recommend('美联储利率与黄金', _quotes(GOLD=1.2, USDCNH=-0.3), hint='fed')
        variants = [
            quant_pair.render_wechat(rec, note='附注说明', show_rule=True),
            quant_pair.render_wechat(rec, compact=True, note='附注说明'),
            quant_pair.render_wechat_mini(rec),
            quant_pair.render_wechat_list([rec], note='附注说明'),
            quant_pair.rule_note_wechat(),
        ]
        for blob in variants:
            self.assertTrue(blob)
            for loud in self.LOUD_TOKENS:
                self.assertNotIn(loud, blob, f'微信端 AI 量化不得包含高亮色 {loud}')
        self.assertIn('color:#808080', variants[0])
        self.assertIn('color:#8e8e8e', variants[0])
        self.assertIn('color:#808080', variants[1])
        self.assertIn('color:#7d7d7d', variants[2])

        roster_card = wp.community_card(
            {'id': '01', 'key': 'FUTU', 'icon': '🐮', 'name': '富途牛牛社区',
             'label': '偏多', 'vclass': 'bull', 'available': True,
             'quote': '讨论原油与炼厂价差', 'meta': '抓取核验 2026-10-29'},
            level='roster',
            quotes=_quotes(WTI=1.5, HSCE=0.2),
        )
        self.assertIn('color:#7d7d7d">跨域配对：', roster_card)


if __name__ == '__main__':
    unittest.main()
