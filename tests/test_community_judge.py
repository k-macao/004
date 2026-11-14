#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐社区逐条内容判定（是否正文 / 是否有信号）的离线回归。"""
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'tools')]

import build_site as bs
import community_data as cd
import community_judge as cj
import wechat_push as wp

BODY_SIGNAL = '恒指今日放量上涨 2.1%，南向资金净流入 80 亿港元，科技股领涨，估值修复还没结束。'
COMM = cd.COMMUNITIES[0]


class TestContent(unittest.TestCase):
    def content(self, text, **kw):
        return cj.judge_item(text, **kw)['content']

    def test_body(self):
        self.assertEqual(self.content(BODY_SIGNAL, kind='comment'), 'body')
        self.assertEqual(self.content(BODY_SIGNAL), 'body')
        self.assertEqual(self.content(
            'The market looks stretched here. I expect a pullback before earnings next week.',
            kind='comment'), 'body')
        self.assertEqual(self.content('感觉要跌，先跑了', kind='comment'), 'body', '短评论也是原话')

    def test_page_shell(self):
        for text in ('首页 | 行情 | 资讯 | 社区 | 交易 | 下载APP | 登录',
                     '©2026 富途 版权所有 隐私政策 用户协议 股票 基金',
                     '老虎社区为您提供港股美股实时行情与投资者交流平台，股票讨论尽在老虎社区',
                     'Sign in to view stocks. Privacy Policy and Terms of Service apply.'):
            judge = cj.judge_item(text)
            self.assertEqual(judge['content'], 'shell', text)
            self.assertEqual(judge['signal'], 'none', '外壳里出现财经词也不读信号')
            self.assertFalse(judge['usable'])

    def test_headline(self):
        title = 'Fed rate cut hopes lift Hang Seng as tech stocks rally'
        self.assertEqual(self.content(title, title=title), 'headline')
        self.assertEqual(self.content(BODY_SIGNAL, strategy='gnews_site_carrier'), 'headline',
                         '检索载体的条目永远不算社区正文')
        self.assertEqual(self.content(BODY_SIGNAL, strategy='bing_news_carrier'), 'headline')
        self.assertEqual(self.content('美股三大指数收盘涨跌不一'), 'headline')

    def test_fragment(self):
        self.assertEqual(self.content('港股怎么看', kind='comment'), 'fragment')
        self.assertEqual(self.content('港股。'), 'fragment')
        self.assertEqual(self.content(''), 'fragment')


class TestSignal(unittest.TestCase):
    def test_signal_needs_subject_and_direction(self):
        judge = cj.judge_item(BODY_SIGNAL, kind='comment')
        self.assertEqual((judge['signal'], judge['direction']), ('signal', 'bull'))
        self.assertTrue(judge['usable'])
        self.assertGreaterEqual(judge['strength'], 55)
        bear = cj.judge_item('港股今天大跌，恒指跌破两万四，外资持续净流出，风险很大。', kind='comment')
        self.assertEqual((bear['signal'], bear['direction']), ('signal', 'bear'))

    def test_question_without_direction_is_weak(self):
        judge = cj.judge_item('今天大盘怎么走？有人知道美联储几点开会吗', kind='thread_reply')
        self.assertEqual(judge['signal'], 'weak')
        self.assertEqual(judge['direction'], 'neutral')
        self.assertFalse(judge['usable'])
        self.assertLessEqual(judge['strength'], 35)

    def test_no_market_subject_is_noise(self):
        judge = cj.judge_item('今天天气真好，中午和同事去吃了拉面，味道非常不错。', kind='comment')
        self.assertTrue(judge['is_body'])
        self.assertEqual(judge['signal'], 'none')

    def test_headline_can_carry_signal_but_is_not_usable(self):
        judge = cj.judge_item(BODY_SIGNAL, strategy='gnews_carrier')
        self.assertEqual(judge['signal'], 'signal')
        self.assertFalse(judge['usable'], '标题有信号也不算社区正文')

    def test_deterministic_and_explained(self):
        a, b = cj.judge_item(BODY_SIGNAL), cj.judge_item(BODY_SIGNAL)
        self.assertEqual(a, b)
        self.assertGreaterEqual(len(a['reasons']), 2)


class TestDataset(unittest.TestCase):
    def record(self, snippet, comments=None, **evidence):
        evidence.setdefault('comments', comments or [])
        return cd.build_live_record(COMM, snippet, fetch_date='2026-11-14', evidence=evidence)

    def test_every_record_and_every_comment_is_judged(self):
        comments = [
            {'text': BODY_SIGNAL, 'post_title': '', 'kind': 'comment', 'strategy': 'x'},
            {'text': '今天大盘怎么走？有人知道美联储几点开会吗', 'post_title': '', 'kind': 'comment'},
            {'text': '港股怎么看', 'post_title': '', 'kind': 'comment'},
        ]
        rec = self.record(BODY_SIGNAL, comments, content_kind='comment', strategy='x')
        self.assertTrue(rec['judge']['usable'])
        self.assertEqual(rec['comments'][0]['judge'], rec['judge'])
        self.assertEqual(rec['comments'][1]['judge']['signal'], 'weak')
        self.assertEqual(rec['comments'][2]['judge']['content'], 'fragment')

    def test_summary_and_unavailable(self):
        shell = self.record('首页 | 行情 | 资讯 | 社区 | 交易 | 下载APP | 登录 股票')
        good = self.record(BODY_SIGNAL, content_kind='comment')
        gone = cd.build_unavailable_record(COMM, fetch_date='2026-11-14')
        data = {'communities': [shell, good, gone]}
        summary = cj.judge_dataset(data)
        self.assertEqual(summary['sources_total'], 3)
        self.assertEqual(summary['sources_available'], 2)
        self.assertEqual(summary['content']['shell'], 1)
        self.assertEqual(summary['usable_sources'], 1)
        self.assertEqual(summary['no_body_sources'], [COMM['key']])
        self.assertNotIn('judge', gone)
        self.assertEqual(data['judge_summary'], summary)
        table = cj.render_text(data)
        self.assertIn('未获取，无内容可判', table)
        self.assertIn('| 页面外壳 |', cj.render_md(data))

    def test_old_snapshot_is_judged_at_render_time_without_changing_verdict(self):
        good = self.record(BODY_SIGNAL, content_kind='comment')
        before = (good['verdict_class'], good['quant']['sentiment']['score'])
        legacy = copy.deepcopy(good)
        legacy.pop('judge')
        out = cd.reanalyze_snapshot({'communities': [legacy]})
        rec = out['communities'][0]
        self.assertTrue(rec['judge']['usable'])
        self.assertEqual((rec['verdict_class'], rec['quant']['sentiment']['score']), before,
                         '判定只是标注，不改变原有立场与得分')
        self.assertIn('judge_summary', out)

    def test_demo_dataset_is_fully_judged(self):
        data = cd.reanalyze_snapshot(cd.generate_all_communities(market={}, demo_mode=True))
        self.assertEqual(data['judge_summary']['sources_available'], len(cd.COMMUNITIES))
        self.assertTrue(all(c.get('judge') for c in data['communities']))


class TestRender(unittest.TestCase):
    def test_tag_text(self):
        self.assertEqual(cj.tag(cj.judge_item('首页 | 行情 | 资讯 | 社区 | 交易 | 登录')), '页面外壳 · 无信号')
        self.assertRegex(cj.tag(cj.judge_item(BODY_SIGNAL, kind='comment')),
                         r'^正文 · 有信号 · 偏多 · 强度 \d+$')
        self.assertEqual(cj.tag(None), '')
        self.assertEqual(cj.tag({'content': 'bogus'}), '')

    def test_web_and_wechat_show_tag(self):
        rec = cd.build_live_record(COMM, BODY_SIGNAL, fetch_date='2026-11-14',
                                   evidence={'content_kind': 'comment'})
        web = bs.build_community_html([rec])
        self.assertIn('judge-tag', web)
        self.assertIn('正文 · 有信号 · 偏多', web)
        self.assertNotIn('href', web)
        card = dict(rec, vclass=rec['verdict_class'], label=rec['verdict_label'])
        self.assertIn('正文 · 有信号 · 偏多', wp.community_card(card, level='full'))
        self.assertNotIn('有信号', wp.community_card(card, level='compact'))
        rec.pop('judge')
        self.assertNotIn('judge-tag', bs.build_community_html([rec]), '没有判定就不渲染标签')


if __name__ == '__main__':
    unittest.main()
