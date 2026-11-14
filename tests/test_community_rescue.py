#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未获取源多方案备选提取（community_rescue）的离线回归（零联网，模拟报文）。"""
import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'tests')]

import community_data as cd
import community_plans as cp
import community_rescue as cr
import community_spider as cs
from test_community_plans import GOOD_RSS, RECENT, STALE, comm, rss_xml, session_for

NOW = datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc)

READER_OK = (
    'Title: 老虎社区 - 港股讨论\n\nURL Source: https://www.laohu8.com\n\nMarkdown Content:\n'
    '# 热门\n\n[登录](https://www.laohu8.com/login)\n\n'
    '港股今日放量上涨，恒指重上两万五，南向资金持续净流入科技股。\n\n'
    '![img](https://x/y.png)\n')
READER_BLOCKED = 'Title: Just a moment...\n\nMarkdown Content:\n港股今日放量上涨，恒指重上两万五，请完成验证。\n'


def fetch(keys, pages, calls=None, **kwargs):
    return cs.fetch_live_snippets([comm(k) for k in keys], session=session_for(pages, calls),
                                  timeout=5, retries=0, concurrency=1, adaptive=False,
                                  obey_robots=False, now=NOW, verbose=False, download_delay=0,
                                  **kwargs)


class TestCatalog(unittest.TestCase):
    def test_every_source_has_multiple_fallback_schemes(self):
        rows = cr.catalog()
        self.assertEqual(len(rows), len(cd.COMMUNITIES))
        for row in rows:
            self.assertGreaterEqual(len(row['generic']), 3, row['key'])
            self.assertGreaterEqual(len(row['plan_steps']) + len(row['generic']), 4, row['key'])

    def test_generic_steps_never_claim_comments(self):
        for community in cd.COMMUNITIES:
            for step in cr.generic_steps(community):
                self.assertEqual(step['content_kind'], cp.KIND_EXCERPT)
                self.assertIn(step['strategy'], cr.GENERIC_STRATEGIES)
                self.assertTrue(step['scope'])

    def test_site_target_keeps_subreddit_path(self):
        self.assertEqual(cr.site_target(comm('REDDIT')), 'reddit.com/r/ChinaStocks')
        self.assertEqual(cr.site_target(comm('LAOHU')), 'laohu8.com')
        self.assertEqual(cr.source_lang(comm('BOGLEHEADS')), 'en')
        self.assertEqual(cr.source_lang(comm('LIHKG')), 'zh-TW')


class TestRescueChain(unittest.TestCase):
    def test_all_schemes_fail_stays_unavailable_and_records_each_attempt(self):
        results, stats = fetch(['LAOHU'], {})
        item = results['LAOHU']
        self.assertFalse(item['ok'])
        steps = item['rescue_attempt']['rescue_steps']
        self.assertEqual([s['strategy'] for s in steps], list(cr.GENERIC_STRATEGIES))
        self.assertTrue(all(s['candidates'] == 0 for s in steps))
        self.assertEqual(stats['rescue_fetch']['rescued_sources'], 0)

    def test_reader_proxy_rescues_js_shell_page(self):
        pages = {cr.reader_url('https://www.laohu8.com'): READER_OK}
        results, stats = fetch(['LAOHU'], pages)
        item = results['LAOHU']
        self.assertTrue(item['ok'])
        self.assertEqual(item['strategy'], cr.STRATEGY_READER)
        self.assertEqual(item['content_kind'], 'page_excerpt')
        self.assertIn('南向资金', item['snippet'])
        self.assertNotIn('http', item['snippet'])
        self.assertEqual(item['url'], 'https://www.laohu8.com', '出处是原站，不是代理地址')
        self.assertEqual(stats['rescue_fetch']['rescued_keys'], ['LAOHU'])

    def test_reader_block_page_and_raw_html_are_rejected(self):
        for body in (READER_BLOCKED, '<!DOCTYPE html><html><body>港股今日放量上涨，恒指重上两万五。</body></html>'):
            results, _ = fetch(['LAOHU'], {cr.reader_url('https://www.laohu8.com'): body})
            self.assertFalse(results['LAOHU']['ok'])

    def test_falls_through_to_bing_when_google_is_empty(self):
        bing = cr.bing_news_url('site:laohu8.com', 'zh-CN')
        calls = []
        results, _ = fetch(['LAOHU'], {bing: GOOD_RSS}, calls)
        item = results['LAOHU']
        self.assertTrue(item['ok'])
        self.assertEqual(item['strategy'], cr.STRATEGY_BING)
        self.assertEqual(item['content_kind'], 'page_excerpt', '检索载体永不冒充评论')
        self.assertLess(calls.index(cp.gnews_url('site:laohu8.com', 'zh-CN')), calls.index(bing))
        name_query = [u for u in calls if '%22' in u]
        self.assertFalse(name_query, '已救回就不再发后续方案的请求')

    def test_stale_or_offtopic_items_are_not_accepted(self):
        bing = cr.bing_news_url('site:laohu8.com', 'zh-CN')
        stale = rss_xml([('恒指南向資金淨流入', 'https://example.com/a/1', STALE, '港股估值有支撐。')])
        offtopic = rss_xml([('今晚吃什么', 'https://example.com/a/2', RECENT, '附近新开了一家拉面店味道不错。')])
        for body in (stale, offtopic):
            results, _ = fetch(['LAOHU'], {bing: body})
            self.assertFalse(results['LAOHU']['ok'])

    def test_runs_plan_steps_left_over_by_step_cap(self):
        # XUEQIU 方案有 4 个可执行步骤，方案层只跑前 3 个；第 4 步（载体）由救援层接着跑。
        spec = cp.PLANS['XUEQIU']
        last = spec['steps'][-1]
        url = cp.expand_endpoint(last['endpoints'][0], last.get('lang'))[0]
        results, _ = fetch(['XUEQIU'], {url: GOOD_RSS})
        item = results['XUEQIU']
        self.assertTrue(item['ok'])
        self.assertEqual(item['rescue_attempt']['origin'], 'plan_rest')
        self.assertEqual(item['strategy'], last['strategy'])

    def test_does_not_touch_sources_that_already_have_content(self):
        carrier = cp.gnews_url('财联社 电报 港股', 'zh-CN')
        calls = []
        results, stats = fetch(['CLS'], {carrier: GOOD_RSS}, calls)
        self.assertTrue(results['CLS']['ok'])
        self.assertNotIn('rescue_attempt', results['CLS'])
        self.assertEqual(stats['rescue_fetch']['attempted_sources'], 0)
        self.assertFalse([u for u in calls if 'bing.com' in u or 'r.jina.ai' in u])

    def test_switches(self):
        pages = {cr.reader_url('https://www.laohu8.com'): READER_OK}
        results, _ = fetch(['LAOHU'], pages, rescue_strategies=False)
        self.assertFalse(results['LAOHU']['ok'])
        os.environ['COMMUNITY_RESCUE'] = '0'
        try:
            results, _ = fetch(['LAOHU'], pages)
        finally:
            del os.environ['COMMUNITY_RESCUE']
        self.assertFalse(results['LAOHU']['ok'])

    def test_budget_exhaustion_is_reported_not_faked(self):
        budget = cp.PlanBudget(requests=1, seconds=60, step_delay=0)
        outcomes, stats = cr.run_rescue([comm('LAOHU'), comm('JIUQUAN')], session_for({}),
                                        now=NOW, budget=budget)
        self.assertEqual(outcomes['JIUQUAN']['attempt']['status'], 'budget_exhausted')
        self.assertEqual(stats['requests_count'], 1)

    def test_dataset_reports_fallback_count_for_unavailable_source(self):
        data = cd.generate_all_communities(market={}, now=NOW, session=session_for({}), limit=1,
                                           verbose=False, retries=0, download_delay=0,
                                           comment_strategies=False)
        record = data['communities'][0]
        self.assertFalse(record['available'])
        self.assertIn('备选方案均未取到', record['meta'])
        self.assertTrue(record['rescue_attempt']['rescue_steps'])


if __name__ == '__main__':
    unittest.main()
