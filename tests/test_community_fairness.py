#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取逻辑审查后的四项修正的离线回归：

① 每源定额预算（后面的源不被饿死）+ RSSHub 实例数上限与健康记忆；
② 方案链 / 备选层 / RSS 优先与基础爬虫同一套 robots 判定；
③ 订阅条目标为 `feed_item`，不再冒充热帖回复；
④ 页面外壳与过短片段不展示、不计入多空。
"""
import collections
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'tests'), os.path.join(ROOT, 'tools')]

import build_site as bs
import community_comments as cc
import community_data as cd
import community_judge as cj
import community_plans as cp
import community_rescue as cr
import community_spider as cs
from test_community_plans import GOOD_RSS, comm, session_for

NOW = datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc)
BODY = '恒指今日放量上涨 2.1%，南向资金净流入 80 亿港元，科技股领涨，估值修复还没结束。'
SHELL = '首页 | 行情 | 资讯 | 社区 | 交易 | 下载APP | 登录 股票'
DENY_ALL = 'User-agent: *\nDisallow: /\n'


def fetch(communities, pages, calls=None, tmp=None, **kwargs):
    kwargs.setdefault('comment_strategies', False)
    kwargs.setdefault('obey_robots', False)
    return cs.fetch_live_snippets(
        communities, session=session_for(pages, calls), timeout=5, retries=0, concurrency=6,
        adaptive=False, now=NOW, verbose=False, download_delay=0,
        storage_file=os.path.join(tmp or tempfile.mkdtemp(), 'a.db'),
        post_limiter=cc.PostInterval(interval=0), **kwargs)


class TestPerSourceBudget(unittest.TestCase):
    def setUp(self):
        cp.reset_rsshub_health()
        self.addCleanup(cp.reset_rsshub_health)

    def test_no_source_is_starved_when_everything_fails(self):
        """49 源全部失败的最坏情况：每个源都把方案链与 4 套通用备选试到，不取决于排序。"""
        _results, stats = fetch(cd.COMMUNITIES, {})
        plan = stats['plan_fetch']['attempts']
        rescue = stats['rescue_fetch']['attempts']
        self.assertEqual(len(plan), 49)
        self.assertEqual(collections.Counter(a['status'] for a in plan.values()), {'unavailable': 49})
        self.assertEqual(collections.Counter(a['status'] for a in rescue.values()), {'unavailable': 49})
        for community in cd.COMMUNITIES:
            key = community['key']
            self.assertTrue([s for s in plan[key]['plan_steps']
                             if s['status'] not in ('delegated', 'not_configured')], key)
            tried = [s for s in rescue[key]['rescue_steps']
                     if s['origin'] == 'generic' and s['status'] == 'no_valid_items']
            self.assertEqual(len(tried), 4, f'{key} 的通用备选没有试完')
        self.assertFalse(stats['plan_fetch']['budget']['exhausted'])
        self.assertFalse(stats['rescue_fetch']['budget']['exhausted'])

    def test_one_source_cannot_spend_more_than_its_quota(self):
        calls = []
        budget = cp.PlanBudget(step_delay=0, source_requests=3)
        cp.run_plans([comm('GELONGHUI'), comm('CLS')], session_for({}, calls), now=NOW, budget=budget)
        self.assertEqual(budget.used_requests, 6)
        outcomes, _ = cp.run_plans([comm('GELONGHUI')], session_for({}), now=NOW,
                                   budget=cp.PlanBudget(step_delay=0, source_requests=3))
        steps = outcomes['GELONGHUI']['attempt']['plan_steps']
        self.assertEqual(steps[-1]['status'], 'source_budget')
        self.assertEqual(outcomes['GELONGHUI']['attempt']['status'], 'unavailable',
                         '单源定额用完不是整轮预算耗尽')

    def test_fair_share_when_global_budget_is_tight(self):
        budget = cp.PlanBudget(requests=4, step_delay=0, source_requests=8)
        outcomes, _ = cp.run_plans([comm('GELONGHUI'), comm('FUTU'), comm('CLS'), comm('LAOHU')],
                                   session_for({}), now=NOW, budget=budget)
        for key, outcome in outcomes.items():
            self.assertTrue(outcome['attempt']['plan_steps'], f'{key} 一步都没轮到')

    def test_rsshub_instances_are_capped_and_bad_ones_demoted(self):
        bases = 'https://a.example,https://b.example,https://c.example,https://d.example'
        with mock.patch.dict(os.environ, {'RSSHUB_BASE': bases}):
            self.assertEqual(cp.expand_endpoint('rsshub:/x'),
                             ['https://a.example/x', 'https://b.example/x'])
            cp.note_rsshub('https://a.example/x', False)
            cp.note_rsshub('https://a.example/y', False)
            cp.note_rsshub('https://c.example/x', True)
            self.assertEqual(cp.expand_endpoint('rsshub:/x'),
                             ['https://c.example/x', 'https://b.example/x'])
            step = cp.PLANS['GELONGHUI']['steps'][0]
            self.assertLessEqual(len(cp.step_urls(step)), cp.PLAN_STEP_URLS)

    def test_rescue_runs_generic_schemes_before_leftover_plan_steps(self):
        outcome = cr.rescue_source(comm('XUEQIU'), session_for({}), now=NOW,
                                   budget=cp.PlanBudget(step_delay=0))
        origins = [s['origin'] for s in outcome['attempt']['rescue_steps']]
        self.assertEqual(origins[:4], ['generic'] * 4)
        self.assertIn('plan_rest', origins[4:])


class TestRobotsEverywhere(unittest.TestCase):
    def test_denied_site_only_uses_search_carriers(self):
        calls = []
        pages = {'https://www.laohu8.com/robots.txt': DENY_ALL}
        results, stats = fetch([comm('LAOHU')], pages, calls, obey_robots=True)
        direct = [u for u in calls if 'laohu8.com' in u and not u.endswith('/robots.txt')
                  and 'news.google.com' not in u and 'bing.com' not in u]
        self.assertEqual(direct, [], '被 robots 拒绝后不得再直连或经阅读器代理抓取')
        plan = {s['strategy']: s['status'] for s in stats['plan_fetch']['attempts']['LAOHU']['plan_steps']}
        self.assertEqual(plan['html_excerpt'], 'robots_disallowed')
        rescue = {s['strategy']: s['status']
                  for s in stats['rescue_fetch']['attempts']['LAOHU']['rescue_steps']}
        self.assertEqual(rescue[cr.STRATEGY_READER], 'robots_disallowed')
        self.assertEqual(rescue[cr.STRATEGY_GNEWS_SITE], 'no_valid_items', '检索载体照常可用')
        self.assertFalse(results['LAOHU']['ok'])

    def test_carrier_can_still_rescue_a_denied_site(self):
        pages = {'https://www.laohu8.com/robots.txt': DENY_ALL,
                 cr.bing_news_url('site:laohu8.com', 'zh-CN'): GOOD_RSS}
        results, _ = fetch([comm('LAOHU')], pages, obey_robots=True)
        self.assertEqual(results['LAOHU']['strategy'], cr.STRATEGY_BING)

    def test_without_obey_robots_nothing_changes(self):
        calls = []
        fetch([comm('LAOHU')], {'https://www.laohu8.com/robots.txt': DENY_ALL}, calls)
        self.assertNotIn('https://www.laohu8.com/robots.txt', calls)
        self.assertIn(cr.reader_url('https://www.laohu8.com'), calls)

    def test_rss_first_respects_robots(self):
        feed = cd.RSS_FEEDS['BOGLEHEADS']
        calls = []
        pages = {'https://www.bogleheads.org/robots.txt': 'User-agent: *\nDisallow: /forum/rss.php\n',
                 feed: GOOD_RSS}
        fetch([comm('BOGLEHEADS')], pages, calls, obey_robots=True, comment_strategies=True,
              plan_strategies=False)
        self.assertNotIn(feed, calls)


class TestFeedItemsAreNotReplies(unittest.TestCase):
    def test_rss_first_is_labelled_feed_item(self):
        feed = cd.RSS_FEEDS['BOGLEHEADS']
        results, _ = fetch([comm('BOGLEHEADS')], {feed: GOOD_RSS}, comment_strategies=True)
        hit = results['BOGLEHEADS']
        self.assertEqual(hit['content_kind'], 'feed_item')
        record = cd.build_live_record(comm('BOGLEHEADS'), hit['snippet'], fetch_date='2026-11-06',
                                      title=hit['title'], evidence=hit)
        self.assertIn('实抓订阅条目', record['meta'])
        self.assertNotIn('热帖回复', record['meta'])
        self.assertNotIn('热帖回复', record['verdict'])
        self.assertEqual(record['analysis_input']['title'], '')

    def test_plan_rss_steps_default_to_feed_item_and_summary_separates_them(self):
        for key, spec in cp.PLANS.items():
            for step in spec['steps']:
                if step['kind'] == cp.STEP_RSS:
                    self.assertEqual(step['content_kind'], cp.KIND_FEED, key)
        self.assertEqual(cj.judge_item('恒指南向資金淨流入', title='恒指南向資金淨流入',
                                       kind='feed_item')['content'], 'headline')


class TestShellContentIsFiltered(unittest.TestCase):
    def record(self, snippet, comments=None, key='FUTU', **evidence):
        evidence['comments'] = comments or []
        return cd.build_live_record(comm(key), snippet, fetch_date='2026-11-06', evidence=evidence)

    def test_shell_only_page_is_not_content_and_falls_through(self):
        page = f'<html><head><title>老虎社区</title></head><body><p>{SHELL}</p></body></html>'
        results, _ = fetch([comm('LAOHU')], {'https://www.laohu8.com': page})
        hit = results['LAOHU']
        self.assertFalse(hit['ok'])
        self.assertEqual(hit['error'], 'shell_content')
        self.assertIn('外壳', cd.format_unavailable_reason(hit))
        self.assertTrue(hit['rescue_attempt']['rescue_steps'], '外壳页的源要继续走备选方案')
        pages = {'https://www.laohu8.com': page, cr.bing_news_url('site:laohu8.com', 'zh-CN'): GOOD_RSS}
        results, _ = fetch([comm('LAOHU')], pages)
        self.assertTrue(results['LAOHU']['ok'], '备选方案拿到内容后顶替外壳')

    def test_plan_candidates_reject_shell_text(self):
        step = cp.html(('https://x.test/',), 'n')
        self.assertIsNone(cp._candidate(SHELL, '', 'https://x.test/', None, NOW, 7, step))
        self.assertIsNotNone(cp._candidate(BODY, '', 'https://x.test/', None, NOW, 7, step))

    def test_render_time_filter_hides_promotes_and_recounts(self):
        shell = self.record(SHELL, key='FUTU')
        good = self.record(BODY, key='XUEQIU', content_kind='comment')
        mixed = self.record(SHELL, key='LAOHU', comments=[
            {'text': SHELL, 'post_title': ''},
            {'text': '港股怎么', 'post_title': '', 'kind': 'comment'},
            {'text': BODY, 'post_title': '', 'kind': 'comment', 'strategy': 'x'}])
        data = {'communities': [shell, good, mixed],
                'summary': {'ok': 3, 'live_ok': 3, 'unavailable': 46, 'total': 49, 'failed': []}}
        out = cd.reanalyze_snapshot(data)
        a, b, c = out['communities']
        self.assertFalse(a['available'])
        self.assertEqual(a['verdict_class'], 'unavailable')
        self.assertEqual(a['filtered']['content'], 'shell')
        self.assertTrue(b['available'])
        self.assertTrue(c['available'] and c['promoted'])
        self.assertTrue(c['quote'].startswith('恒指今日放量上涨'))
        self.assertEqual(c['verdict_class'], 'bull', '换了引文后立场按新引文重算')
        self.assertEqual(len(c['comments']), 1)
        self.assertEqual(out['counts']['unavailable'], 1)
        self.assertEqual(out['counts']['bull'], 2)
        self.assertEqual((out['summary']['ok'], out['summary']['live_ok']), (2, 3))
        self.assertEqual(out['judge_summary']['filtered']['hidden_sources'], ['FUTU'])
        self.assertEqual(out['judge_summary']['filtered']['promoted_sources'], ['LAOHU'])
        self.assertTrue(data['communities'][0]['available'], '不改原始快照')
        html = bs.build_community_html(out['communities'])
        self.assertEqual(html.count('<article'), 2)
        self.assertNotIn('下载APP', html)
        again = cd.reanalyze_snapshot(out)
        self.assertEqual(again['counts'], out['counts'], '重复渲染结果不变')

    def test_headlines_and_legacy_records_are_kept(self):
        headline = self.record('恒指估值有支撑', key='FUTU')
        legacy = {'key': 'XUEQIU', 'id': '02', 'name': '乙', 'available': True, 'quote': 'q2',
                  'verdict_class': 'bear', 'verdict_label': '偏空'}
        out = cd.reanalyze_snapshot({'communities': [headline, legacy]})
        self.assertTrue(all(r['available'] for r in out['communities']))
        self.assertEqual(out['communities'][0]['judge']['content'], 'headline')


if __name__ == '__main__':
    unittest.main()
