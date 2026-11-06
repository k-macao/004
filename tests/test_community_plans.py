#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""49 社区源逐源独立抓取方案的离线回归（零联网）。

钉住的不变量分四层：
1. **注册表完整性**：49 源一一对应、每源一条有序端点链、最后一步不依赖密钥；
2. **诚实口径**：检索载体永不冒充评论、无路由的站点绝不写 RSSHub 步骤、
   `needs_config` 的步骤不算稳定路径、每条端点都带口径说明；
3. **执行器行为**：逐级降级、时效窗口、主题过滤、预算与步数上限、
   失败即「未获取」而不是编造、渲染与目录路径绝不联网；
4. **管线接入**：只补强不降级（已有评论证据不被较弱摘录覆盖）、
   单源失败不影响其它源、统计里能看到逐源方案的可观测信息。
"""
import importlib
import json
import os
import socket
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT]

import community_comments as cc
import community_data as cd
import community_plans as cp
import community_spider as cs
import scrapling_core as sc

NOW = datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc)
RECENT = 'Thu, 05 Nov 2026 03:00:00 +0000'
STALE = 'Mon, 01 Sep 2026 03:00:00 +0000'
FUTURE = (NOW + timedelta(days=3)).strftime('%a, %d %b %Y %H:%M:%S +0000')

PLANS_MD = os.path.join(ROOT, 'docs', 'community-49-plans.md')
ROUTES_MD = os.path.join(ROOT, 'docs', 'community-49-routes.md')

# 已核对 RSSHub 无路由的源：方案里绝不允许出现 rsshub 步骤（凭印象写路由就是造假）。
NO_RSSHUB_KEYS = (
    'REDDIT', 'RINVESTING', 'ROPTIONS', 'RCRYPTO', 'PTT', 'LIHKG', 'BOGLEHEADS',
    'STOCKTWITS', 'WSO', 'INVESTING', 'QUANTNET', 'ELITETRADER', 'FOREXFACTORY',
    'MORNINGSTAR', 'MARKETWATCH', 'WALLSTREETDE', 'AASTOCKS', 'YAHOOJP', 'LAOHU',
    'JIUQUAN', 'ANTFORTUNE', 'KAIPANLA', 'LIXIANG', 'VIC', 'SUBSTACK',
)
ADAPTER_KEYS = {'PTT': 'ptt_html', 'LIHKG': 'lihkg_api', 'BOGLEHEADS': 'phpbb_html',
                'DISCUSS': 'discuz_html', 'REDDIT': 'reddit_praw', 'RINVESTING': 'reddit_praw',
                'ROPTIONS': 'reddit_praw', 'RCRYPTO': 'reddit_praw'}


def rss_xml(items, title='社区订阅'):
    body = ''.join(
        f'<item><title>{t}</title><link>{link}</link><pubDate>{date}</pubDate>'
        f'<description>{text}</description></item>' for t, link, date, text in items)
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{title}</title>{body}</channel></rss>'


GOOD_RSS = rss_xml([
    ('恒指南向資金淨流入', 'https://example.com/a/1', RECENT, '港股估值有支撐，南向資金持續淨流入。'),
    ('港股成交量放大', 'https://example.com/a/2', RECENT, '港股成交量放大，恒指短線波動加劇。'),
])


def transport_for(pages, calls=None):
    """假传输层：只有登记过的 URL 有内容，其余一律 404；记录调用顺序。"""
    def transport(method, url, headers, timeout, proxy=None, body=None):
        if calls is not None:
            calls.append(url)
        val = pages.get(url, (404, 'not found'))
        status, data = val if isinstance(val, tuple) else (200, val)
        raw = data if isinstance(data, bytes) else (
            json.dumps(data, ensure_ascii=False).encode() if isinstance(data, dict) else str(data).encode())
        return status, 'OK', {'Content-Type': 'text/html; charset=utf-8'}, raw, url
    return transport


def session_for(pages, calls=None):
    return sc.FetcherSession(transport=transport_for(pages, calls), timeout=5, retries=0)


def comm(key):
    return next(c for c in cd.COMMUNITIES if c['key'] == key)


def all_endpoints(spec):
    out = []
    for step in spec['steps']:
        out.extend(step['endpoints'])
    return out


class TestRegistryCovers49Sources(unittest.TestCase):
    """① 49 源一一对应，每源一份独立方案。"""

    def test_every_catalog_source_has_exactly_one_plan(self):
        self.assertEqual(len(cp.PLANS), 49, '49 源必须逐源有方案')
        self.assertEqual(cp.missing_plans(), [], '有源没有方案')
        self.assertEqual(cp.extra_plans(), [], '方案里有目录外的幽灵源')
        self.assertEqual(sorted(cp.PLANS), sorted(c['key'] for c in cd.COMMUNITIES))

    def test_catalog_rows_join_plan_with_source_of_truth_metadata(self):
        rows = cp.catalog()
        self.assertEqual(len(rows), 49)
        self.assertEqual([r['id'] for r in rows], [f'{i:02d}' for i in range(1, 50)])
        by_key = {c['key']: c for c in cd.COMMUNITIES}
        for row in rows:
            source = by_key[row['key']]
            self.assertEqual(row['name'], source['name'], '名称必须取自目录唯一事实源')
            self.assertEqual(row['ctype'], source['ctype'])
            self.assertEqual(row['url'], source['url'])
            self.assertTrue(row['summary'], f"{row['key']} 方案缺一句话口径")
            self.assertGreaterEqual(len(row['steps']), 2, f"{row['key']} 至少要有两级路")

    def test_each_plan_is_an_ordered_chain_of_known_step_kinds(self):
        for key, spec in cp.PLANS.items():
            kinds = [s['kind'] for s in spec['steps']]
            for kind in kinds:
                self.assertIn(kind, cp.STEP_KINDS, f'{key} 出现未知步骤类型 {kind}')
            self.assertEqual(kinds[0], kinds[0].strip())
            self.assertNotEqual(kinds[-1], cp.STEP_ADAPTER,
                                f'{key} 最后一步不能是「交给适配器」，否则适配器失败时没有兜底')

    def test_every_endpoint_is_a_resolvable_absolute_target(self):
        for key, spec in cp.PLANS.items():
            for endpoint in all_endpoints(spec):
                self.assertTrue(
                    endpoint.startswith(('http://', 'https://', 'rsshub:', 'gnews:')),
                    f'{key} 端点不是可解析目标（禁止裸域名）: {endpoint}')
                self.assertNotIn('localhost', endpoint, f'{key} 端点指向本机: {endpoint}')
                self.assertNotIn('127.0.0.1', endpoint, f'{key} 端点指向本机: {endpoint}')
                self.assertNotIn('example.com', endpoint, f'{key} 端点是占位域名: {endpoint}')
                self.assertNotIn('TODO', endpoint, f'{key} 端点未填: {endpoint}')

    def test_no_endpoint_leaks_credentials(self):
        for key, spec in cp.PLANS.items():
            for endpoint in all_endpoints(spec):
                lowered = endpoint.lower()
                for banned in ('password=', 'passwd=', 'secret=', 'api_key=', 'apikey=',
                               'access_token=', 'bearer '):
                    self.assertNotIn(banned, lowered, f'{key} 端点里出现凭据字样: {endpoint}')
                userinfo = endpoint.split('//', 1)[-1].split('/', 1)[0]
                self.assertNotIn('@', userinfo, f'{key} 端点内嵌 userinfo: {endpoint}')

    def test_expanded_endpoints_are_all_absolute_http(self):
        with mock.patch.dict(os.environ, {'RSSHUB_BASE': 'https://a.example/,https://b.example'}):
            urls = cp.expand_endpoint('rsshub:/xueqiu/hots')
        self.assertEqual(urls, ['https://a.example/xueqiu/hots', 'https://b.example/xueqiu/hots'])
        self.assertEqual(cp.expand_endpoint('gnews:港股'), [cp.gnews_url('港股', 'zh-CN')])
        self.assertTrue(cp.expand_endpoint('https://x.test/a')[0].startswith('https://'))
        self.assertEqual(cp.expand_endpoint(''), [])


class TestHonestyGuards(unittest.TestCase):
    """② 诚实口径：不许把弱证据说成强证据，不许凭印象写路由。"""

    def test_last_step_never_requires_secrets(self):
        for key, spec in cp.PLANS.items():
            self.assertFalse(spec['steps'][-1].get('needs_config'),
                             f'{key} 最后一步依赖密钥，未配置时该源等于没有方案')
            config_free = [s for s in spec['steps'] if not s.get('needs_config')]
            self.assertTrue(config_free, f'{key} 所有步骤都要密钥')

    def test_config_gated_steps_declare_their_env_names(self):
        gated = 0
        for key, spec in cp.PLANS.items():
            for step in spec['steps']:
                if step.get('needs_config'):
                    gated += 1
                    for name in step['needs_config']:
                        self.assertRegex(name, r'^[A-Z][A-Z0-9_]*$',
                                         f'{key} 的环境变量名不规范: {name}')
                    self.assertIn(step['kind'], (cp.STEP_RSSHUB, cp.STEP_JSON, cp.STEP_HTML))
        self.assertGreaterEqual(gated, 5, 'X/知乎/微博/小红书/抖音等源本就需要密钥，必须显式标注')

    def test_carrier_steps_never_claim_to_be_comments(self):
        carriers = 0
        for key, spec in cp.PLANS.items():
            for step in spec['steps']:
                if step['kind'] == cp.STEP_CARRIER:
                    carriers += 1
                    self.assertEqual(step['content_kind'], cp.KIND_EXCERPT,
                                     f'{key} 检索载体不得自称评论/热帖回复')
                    self.assertEqual(step['strategy'], 'gnews_carrier')
                    self.assertEqual(step['verified'], 'carrier')
                    self.assertTrue(step['endpoints'][0].startswith('gnews:'))
                    self.assertIn('载体', step['scope'], f'{key} 载体步骤的口径要写明是聚合检索')
        self.assertGreaterEqual(carriers, 30, '绝大多数源都应有检索载体兜底')

    def test_verified_labels_and_content_kinds_are_within_enum(self):
        for key, spec in cp.PLANS.items():
            for step in spec['steps']:
                self.assertIn(step['verified'], cp.VERIFIED, f'{key} 核验状态非法')
                self.assertIn(step['content_kind'], cp.CONTENT_KINDS, f'{key} 内容类型非法')
                self.assertTrue(step['note'], f'{key} 的 {step["kind"]} 步骤缺口径说明')

    def test_no_public_feed_flag_matches_actual_step_kinds(self):
        flagged = 0
        for key, spec in cp.PLANS.items():
            kinds = {s['kind'] for s in spec['steps']}
            only_weak = kinds <= {cp.STEP_HTML, cp.STEP_CARRIER}
            self.assertEqual(spec['no_public_feed'], only_weak,
                             f'{key} 的 no_public_feed 标记与步骤类型不一致')
            flagged += 1 if only_weak else 0
        self.assertGreaterEqual(flagged, 8, 'App-only / 无订阅出口的源必须如实标出来')

    def test_sources_verified_to_have_no_rsshub_route_never_use_one(self):
        for key in NO_RSSHUB_KEYS:
            spec = cp.PLANS[key]
            kinds = {s['kind'] for s in spec['steps']}
            self.assertNotIn(cp.STEP_RSSHUB, kinds,
                             f'{key} 已核对 RSSHub 无路由，方案里却写了 RSSHub 步骤')

    def test_rsshub_steps_only_use_routes_pinned_to_upstream_source(self):
        used = set()
        for key, spec in cp.PLANS.items():
            for step in spec['steps']:
                if step['kind'] != cp.STEP_RSSHUB:
                    continue
                self.assertEqual(step['verified'], 'rsshub_doc', f'{key} 的 RSSHub 步骤要标 rsshub_doc')
                for endpoint in step['endpoints']:
                    route = endpoint.split('rsshub:', 1)[-1]
                    pattern = cp._route_pattern(route)
                    self.assertIn(pattern, cp.ROUTE_EVIDENCE,
                                  f'{key} 使用了未核对上游源码的路由: {route}')
                    used.add(pattern)
        self.assertGreaterEqual(len(used), 25, 'RSSHub 路线应是多数中文源的主路')
        self.assertIn('/xueqiu/stock_comments/:id', used)
        self.assertIn('/discuss/:fid', used)

    def test_upstream_evidence_is_pinned_to_a_real_commit(self):
        up = cp.RSSHUB_UPSTREAM
        self.assertEqual(up['repo'], 'DIYgod/RSSHub')
        self.assertRegex(up['commit'], r'^[0-9a-f]{40}$', '必须钉住完整 commit sha')
        self.assertRegex(up['committed_at'], r'^\d{4}-\d{2}-\d{2}T')
        self.assertIn('不能直连目标站点', up['method'], '必须写明未做在线实测')
        for route, path in cp.ROUTE_EVIDENCE.items():
            self.assertTrue(route.startswith('/'), f'路由写法异常: {route}')
            self.assertTrue(path.endswith(('.ts', '.tsx')), f'证据文件写法异常: {path}')
            self.assertNotIn('lib/routes/', path, '证据路径应相对 lib/routes/')

    def test_route_pattern_resolution_prefers_the_longest_static_prefix(self):
        self.assertEqual(cp._route_pattern('/xueqiu/stock_comments/HKHSI'), '/xueqiu/stock_comments/:id')
        self.assertEqual(cp._route_pattern('/taoguba/zongban'), '/taoguba/:category?')
        self.assertEqual(cp._route_pattern('/yahoo/finance/hk/latest-news'), '/yahoo/finance/hk/:topic?')
        self.assertEqual(cp._route_pattern('/yahoo/finance/latest-news'), '/yahoo/finance/:topic?')
        self.assertEqual(cp._route_pattern('/bilibili/partion/ranking/207/3'),
                         '/bilibili/partion/ranking/:tid/:days?/:embed?')
        self.assertEqual(cp._route_pattern('/nope/does-not-exist'), '/nope/does-not-exist')

    def test_adapter_steps_only_for_sources_that_really_have_adapters(self):
        for key, spec in cp.PLANS.items():
            adapters = [s for s in spec['steps'] if s['kind'] == cp.STEP_ADAPTER]
            if key in ADAPTER_KEYS:
                self.assertEqual(len(adapters), 1, f'{key} 应登记且只登记一个专用适配器')
                self.assertEqual(adapters[0]['strategy'], ADAPTER_KEYS[key])
                self.assertFalse(adapters[0]['endpoints'], '适配器步骤不重复发请求')
            else:
                self.assertEqual(adapters, [], f'{key} 没有专用适配器，不得登记 adapter 步骤')

    def test_reddit_sources_keep_native_rss_and_official_api(self):
        for key in ('REDDIT', 'RINVESTING', 'ROPTIONS', 'RCRYPTO'):
            spec = cp.PLANS[key]
            self.assertEqual(spec['steps'][0]['kind'], cp.STEP_RSS, f'{key} 原生 .rss 应排第一')
            urls = spec['steps'][0]['endpoints']
            self.assertTrue(any(u.endswith('/.rss') for u in urls), f'{key} 缺原生 .rss 端点')
            self.assertTrue(any('old.reddit.com' in u for u in urls), f'{key} 缺 old.reddit 回退')
            self.assertEqual(spec['steps'][1]['strategy'], 'reddit_praw')

    def test_plan_strength_ignores_config_gated_steps(self):
        # X / 知乎 / 微博 / 小红书 / 抖音：唯一像样的路都要密钥，默认配置下只能算摘录级。
        for key in ('FINTWIT', 'ZHIHU', 'WEIBO', 'XIAOHONGSHU', 'DOUYIN'):
            self.assertEqual(cp.plan_strength(cp.PLANS[key]), 'excerpt_only',
                             f'{key} 的强度被 needs_config 步骤虚报了')
            self.assertTrue(cp.config_gated_steps(cp.PLANS[key]), f'{key} 应显式列出受密钥限制的步骤')
        self.assertEqual(cp.plan_strength(cp.PLANS['XUEQIU']), 'feed')
        self.assertEqual(cp.plan_strength(cp.PLANS['PTT']), 'adapter')
        self.assertEqual(cp.plan_strength(cp.PLANS['FUTU']), 'rsshub')
        self.assertEqual(cp.plan_strength(cp.PLANS['AASTOCKS']), 'excerpt_only')

    def test_registry_rejects_bad_plans_at_construction_time(self):
        with self.assertRaises(ValueError):
            cp.step('carrier', ('gnews:x',), verified='carrier', content_kind=cp.KIND_COMMENT)
        with self.assertRaises(ValueError):
            cp.step('rss', (), verified='native')
        with self.assertRaises(ValueError):
            cp.rsshub(['/made/up/route'], '凭印象写的路由')
        with self.assertRaises(ValueError):
            cp.plan('X', 's', [cp.step(cp.STEP_RSSHUB, ('rsshub:/xueqiu/hots',), needs_config=('A',))])
        with self.assertRaises(ValueError):
            cp.plan('X', 's', [])


class TestExecutorDegradation(unittest.TestCase):
    """③ 执行器：逐级降级、时效与主题过滤、预算、失败即未获取。"""

    def test_falls_through_to_next_step_when_first_step_yields_nothing(self):
        pages = {'https://rsshub.app/cls/telegraph': (500, 'boom'),
                 'https://www.cls.cn/telegraph':
                     '<html><body><div class="telegraph-content">港股電報：恒指南向資金淨流入放大。</div></body></html>'}
        calls = []
        outcome = cp.run_plan(comm('CLS'), session_for(pages, calls), now=NOW,
                              budget=cp.PlanBudget(step_delay=0))
        self.assertEqual(outcome['attempt']['status'], 'ok')
        self.assertEqual(outcome['attempt']['strategy'], 'html_excerpt')
        self.assertTrue(outcome['comments'])
        statuses = {s['kind']: s['status'] for s in outcome['attempt']['plan_steps']}
        self.assertEqual(statuses['rsshub'], 'no_valid_items')
        self.assertEqual(statuses['html'], 'ok')

    def test_first_working_endpoint_inside_a_step_wins_and_stops(self):
        pages = {'https://rsshub.app/gelonghui/live': (403, 'blocked by edge'),
                 'https://rsshub.rssforever.com/gelonghui/live': GOOD_RSS}
        calls = []
        with mock.patch.dict(os.environ, {'RSSHUB_BASE': 'https://rsshub.app,https://rsshub.rssforever.com'}):
            outcome = cp.run_plan(comm('GELONGHUI'), session_for(pages, calls), now=NOW,
                                  budget=cp.PlanBudget(step_delay=0))
        self.assertEqual(outcome['attempt']['status'], 'ok')
        self.assertEqual(outcome['attempt']['strategy'], 'rsshub_feed')
        self.assertIn('rsshub.rssforever.com', outcome['attempt']['endpoint'])
        self.assertEqual(len([c for c in calls if 'gelonghui' in c]), 2, '403 后换实例，命中即停')

    def test_all_steps_failing_is_reported_as_unavailable_not_fabricated(self):
        calls = []
        outcome = cp.run_plan(comm('AASTOCKS'), session_for({}, calls), now=NOW,
                              budget=cp.PlanBudget(step_delay=0))
        self.assertEqual(outcome['comments'], [])
        self.assertEqual(outcome['attempt']['status'], 'unavailable')
        self.assertEqual(outcome['attempt']['matched_comments'], 0)
        self.assertTrue(calls, '确实尝试过，而不是凭空放弃')
        for step in outcome['attempt']['plan_steps']:
            self.assertEqual(step['status'], 'no_valid_items')

    def test_stale_and_future_items_are_rejected(self):
        stale = rss_xml([('港股南向資金淨流入', 'https://example.com/s/1', STALE, '港股估值有支撐，南向資金淨流入。')])
        future = rss_xml([('港股南向資金淨流入', 'https://example.com/f/1', FUTURE, '港股估值有支撐，南向資金淨流入。')])
        for key, pages in (('CLS', {'https://rsshub.app/cls/telegraph': stale}),
                           ('YICAI', {'https://rsshub.app/yicai/brief': future})):
            outcome = cp.run_plan(comm(key), session_for(pages), now=NOW,
                                  budget=cp.PlanBudget(step_delay=0))
            for step in outcome['attempt']['plan_steps']:
                if step['kind'] == cp.STEP_RSSHUB:
                    self.assertEqual(step['status'], 'no_valid_items', f'{key} 超窗/未来条目必须被拒')
            self.assertNotEqual(outcome['attempt'].get('strategy'), 'rsshub_feed')

    def test_non_financial_items_are_rejected(self):
        noise = rss_xml([('今天天气不错适合散步', 'https://example.com/n/1', RECENT, '完全无关的生活闲聊内容。')])
        outcome = cp.run_plan(comm('TAOGUBA'), session_for({'https://rsshub.app/taoguba/zongban': noise}),
                              now=NOW, budget=cp.PlanBudget(step_delay=0))
        steps = {s['kind']: s['status'] for s in outcome['attempt']['plan_steps']}
        self.assertEqual(steps['rsshub'], 'no_valid_items')

    def test_needs_config_step_is_skipped_and_chain_continues(self):
        """载体 404 → 需 Cookie 的 RSSHub 步骤如实记 not_configured（不发请求）→ 落到页面摘录。"""
        zhihu_page = 'https://www.zhihu.com/topic/19554223/hot'
        pages = {zhihu_page: '<html><body><div class="RichText">'
                             '港股恒指估值有支撑，南向资金持续净流入。</div></body></html>',
                 'https://rsshub.app/zhihu/topic/19554223': GOOD_RSS}
        calls = []
        env = {k: v for k, v in os.environ.items() if k != 'ZHIHU_COOKIES'}
        with mock.patch.dict(os.environ, env, clear=True):
            outcome = cp.run_plan(comm('ZHIHU'), session_for(pages, calls), now=NOW,
                                  budget=cp.PlanBudget(step_delay=0))
        statuses = {s['kind']: s['status'] for s in outcome['attempt']['plan_steps']}
        self.assertEqual(statuses['carrier'], 'no_valid_items')
        self.assertEqual(statuses['rsshub'], 'not_configured', '没配 Cookie 就不该假装尝试')
        self.assertEqual(statuses['html'], 'ok')
        self.assertFalse(any('rsshub' in c for c in calls), '未配置的步骤不得发请求')
        self.assertEqual(outcome['attempt']['status'], 'ok')
        self.assertEqual(outcome['attempt']['strategy'], 'html_excerpt')
        self.assertEqual(outcome['comments'][0]['kind'], cp.KIND_EXCERPT, '页面摘录不得自称评论')

    def test_config_gated_step_runs_when_env_is_present(self):
        pages = {'https://rsshub.app/zhihu/topic/19554223': GOOD_RSS}
        with mock.patch.dict(os.environ, {'ZHIHU_COOKIES': 'z_c0=fake',
                                          'RSSHUB_BASE': 'https://rsshub.app'}):
            outcome = cp.run_plan(comm('ZHIHU'), session_for(pages), now=NOW,
                                  budget=cp.PlanBudget(step_delay=0))
        statuses = {s['kind']: s['status'] for s in outcome['attempt']['plan_steps']}
        self.assertEqual(statuses['rsshub'], 'ok')

    def test_adapter_step_is_delegated_without_duplicating_requests(self):
        calls = []
        outcome = cp.run_plan(comm('LIHKG'), session_for({}, calls), now=NOW,
                              budget=cp.PlanBudget(step_delay=0),
                              skip_kinds=(cp.STEP_ADAPTER,))
        statuses = {s['kind']: s['status'] for s in outcome['attempt']['plan_steps']}
        self.assertEqual(statuses['adapter'], 'delegated')
        self.assertEqual(statuses['html'], 'no_valid_items', '适配器之外的那一级仍要自己试')
        self.assertFalse(any('api_v2' in c for c in calls),
                         'LIHKG 的 JSON 采样由适配器负责，方案层不得重复打同一接口')

    def test_ptt_chain_moves_past_the_delegated_adapter(self):
        pages = {'https://www.ptt.cc/atom/Stock.xml': rss_xml([
            ('[討論] 港股與美股走勢', 'https://www.ptt.cc/bbs/Stock/M.1.A.html', RECENT,
             '恒指估值有支撐，南向資金持續流入。')])}
        calls = []
        outcome = cp.run_plan(comm('PTT'), session_for(pages, calls), now=NOW,
                              budget=cp.PlanBudget(step_delay=0), skip_kinds=(cp.STEP_ADAPTER,))
        statuses = {s['kind']: s['status'] for s in outcome['attempt']['plan_steps']}
        self.assertEqual(statuses['adapter'], 'delegated')
        self.assertEqual(statuses['rss'], 'ok', '适配器之外还有板级订阅这一级')
        self.assertEqual(outcome['attempt']['strategy'], 'rss_feed')
        self.assertTrue(any('atom/Stock.xml' in c for c in calls))

    def test_step_budget_caps_attempts_and_wall_budget_stops_requests(self):
        outcome = cp.run_plan(comm('FUTU'), session_for({}), now=NOW,
                              budget=cp.PlanBudget(step_delay=0), max_steps=1)
        attempted = [s for s in outcome['attempt']['plan_steps'] if s['status'] not in ('skipped', 'delegated')]
        self.assertEqual(len([s for s in attempted if s['status'] != 'step_budget']), 1)
        self.assertTrue(any(s['status'] == 'step_budget' for s in outcome['attempt']['plan_steps']))

        budget = cp.PlanBudget(requests=1, step_delay=0)
        outcome = cp.run_plan(comm('FUTU'), session_for({}), now=NOW, budget=budget)
        self.assertTrue(budget.exhausted)
        self.assertEqual(outcome['attempt']['status'], 'budget_exhausted')
        self.assertLessEqual(budget.used_requests, 2)

    def test_json_step_produces_honest_metric_and_rejects_malformed_payload(self):
        payload = {'list': [
            {'text': '<p>港股恒指估值有支撑，南向资金持续净流入。</p>', 'title': '恒指讨论',
             'target': '/1234/5678', 'created_at': int(NOW.timestamp() * 1000) - 3600,
             'user': {'screen_name': 'someone', 'id': 1234}},
            {'text': '完全无关的生活闲聊', 'title': '', 'target': '/9/9',
             'created_at': int(NOW.timestamp() * 1000)},
        ]}
        api = ('https://xueqiu.com/query/v1/symbol/search/status?u=11111&count=100&comment=0'
               '&symbol=HKHSI&source=all&sort=time')
        calls = []
        outcome = cp.run_plan(comm('XUEQIU'), session_for({api: payload}, calls), now=NOW,
                             budget=cp.PlanBudget(step_delay=0))
        self.assertEqual(outcome['attempt']['status'], 'ok')
        self.assertEqual(outcome['attempt']['strategy'], 'xueqiu_json')
        first = outcome['comments'][0]
        self.assertEqual(first['kind'], cp.KIND_REPLY)
        self.assertEqual(first['url'], 'https://xueqiu.com/1234/5678', '站内相对路径要补成绝对链接')
        self.assertTrue(first['published_at'].endswith('Z'))
        self.assertEqual(first['metric']['value'], 0, '接口没给点赞数，热度指标必须是 0')
        self.assertIn('未提供点赞数', first['metric']['label'])
        self.assertEqual(len(outcome['comments']), 1, '非财经条目必须被剔除')
        self.assertEqual(calls[0], api, '第一步命中即停，不再打后面的端点')

        for bad in ({'nope': 1}, [], 'not-json', {'list': 'not-a-list'}):
            self.assertIsNone(cp.extract_xueqiu_statuses(bad, api) if isinstance(bad, (dict, list))
                              else None, '结构不符时必须判无效而不是编造')
        self.assertIsNone(cp.extract_stocktwits_stream({'records': 'x'}, 'https://stocktwits.com'))
        self.assertIsNone(cp.extract_stocktwits_stream({}, 'https://stocktwits.com'))
        self.assertEqual(cp.extract_stocktwits_stream({'records': []}, 'https://stocktwits.com'), [])

    def test_stocktwits_stream_tolerates_missing_fields_without_fabricating(self):
        payload = {'records': [
            {'body': 'HSI Hang Seng index market volatility is elevated today',
             'created_at': (NOW - timedelta(hours=2)).isoformat().replace('+00:00', 'Z'),
             'id': 999, 'user': {'username': 'trader'},
             'entities': {'sentiment': {'label': 'Bearish'}}},
            {'body': 'no link no time', 'id': None},
        ]}
        items = cp.extract_stocktwits_stream(payload, 'https://stocktwits.com/symbol/HSI')
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['url'], 'https://stocktwits.com/message/999')
        self.assertIn('Bearish', items[0]['extra'])
        self.assertEqual(items[1]['url'], 'https://stocktwits.com/symbol/HSI', '没有 id 就用页面链接，不编造')

    def test_run_plans_isolates_single_source_failure_and_reports_budget(self):
        def fake_run_plan(comm, *args, **kwargs):
            if comm['key'] == 'YICAI':
                raise RuntimeError('adapter exploded')
            return {'comments': [], 'attempt': {'strategy': 'plan_chain', 'status': 'unavailable',
                                                'matched_comments': 0, 'plan_steps': []}}

        targets = [comm('CLS'), comm('YICAI'), comm('AASTOCKS')]
        budget = cp.PlanBudget(step_delay=0)
        with mock.patch.object(cp, 'run_plan', side_effect=fake_run_plan):
            outcomes, stats = cp.run_plans(targets, session_for({}), now=NOW, budget=budget)
        self.assertEqual(len(outcomes), 3, '一源异常不得吞掉其它源')
        self.assertEqual(outcomes['YICAI']['attempt']['status'], 'error_RuntimeError')
        self.assertEqual(outcomes['YICAI']['comments'], [], '异常源不得留下半截证据')
        self.assertEqual(outcomes['CLS']['attempt']['status'], 'unavailable')
        self.assertEqual(stats['attempted_sources'], 3)
        self.assertEqual(stats['plan_sources'], 0)
        self.assertIn('budget', stats)
        self.assertIn('attempts', stats)

    def test_run_plans_skips_sources_without_a_plan_or_in_skip_list(self):
        outcomes, stats = cp.run_plans([comm('CLS')], session_for({}), now=NOW,
                                       skip_keys=('CLS',), budget=cp.PlanBudget(step_delay=0))
        self.assertEqual(outcomes, {})
        self.assertEqual(stats['attempted_sources'], 0)

    def test_should_run_plan_only_upgrades_and_never_downgrades(self):
        spec = cp.PLANS['CLS']
        self.assertTrue(cp.should_run_plan({'ok': False}, spec), '未获取的源要跑方案')
        self.assertTrue(cp.should_run_plan(None, spec))
        self.assertTrue(cp.should_run_plan({'ok': True, 'content_kind': 'page_excerpt'}, spec),
                        '只有通用摘录时，方案里更强的路应当再试')
        self.assertFalse(cp.should_run_plan({'ok': True, 'content_kind': 'comment'}, spec),
                         '已有评论证据不得被方案覆盖')
        self.assertFalse(cp.should_run_plan({'ok': True, 'content_kind': 'thread_reply'}, spec))
        self.assertFalse(cp.should_run_plan({'ok': True}, None), '没有方案就不跑')
        weak = cp.PLANS['AASTOCKS']  # 只有 html + carrier：已经是摘录，不必再跑一遍
        self.assertFalse(cp.should_run_plan({'ok': True, 'content_kind': 'page_excerpt'}, weak))

    def test_deterministic_same_input_same_output(self):
        pages = {'https://rsshub.app/cls/telegraph': GOOD_RSS}
        first = cp.run_plan(comm('CLS'), session_for(pages), now=NOW, budget=cp.PlanBudget(step_delay=0))
        second = cp.run_plan(comm('CLS'), session_for(pages), now=NOW, budget=cp.PlanBudget(step_delay=0))
        self.assertEqual(first['comments'], second['comments'])
        self.assertEqual(first['attempt']['strategy'], second['attempt']['strategy'])

    def test_comment_text_is_truncated_to_the_shared_budget(self):
        long_text = '港股恒指南向资金净流入' * 60
        payload = {'list': [{'text': long_text, 'title': '恒指', 'target': '/1/1',
                             'created_at': int(NOW.timestamp() * 1000)}]}
        api = ('https://xueqiu.com/query/v1/symbol/search/status?u=11111&count=100&comment=0'
               '&symbol=HKHSI&source=all&sort=time')
        outcome = cp.run_plan(comm('XUEQIU'), session_for({api: payload}), now=NOW,
                              budget=cp.PlanBudget(step_delay=0))
        first = outcome['comments'][0]
        self.assertLessEqual(len(first['text']), cs.MAX_SNIPPET + 40)
        self.assertTrue(first['text_truncated'])


class TestNoNetworkOnReadPaths(unittest.TestCase):
    """④ 目录/导出/渲染路径绝不联网，导入也不开 socket。"""

    def test_import_opens_no_sockets(self):
        real = socket.socket

        def guard(*args, **kwargs):
            raise AssertionError('导入 community_plans 时不得建立网络连接')

        with mock.patch.object(socket, 'socket', guard):
            importlib.reload(cp)
        self.assertEqual(len(cp.PLANS), 49)
        with mock.patch.object(socket, 'socket', real):
            importlib.reload(cp)

    def test_catalog_and_doc_rendering_never_fetch(self):
        def guard(*args, **kwargs):
            raise AssertionError('目录与文档渲染路径不得联网')

        with mock.patch.object(sc, '_default_transport', guard):
            rows = cp.catalog()
            plans_text = cp.render_plans_md()
            routes_text = cp.render_routes_md()
            for row in rows:
                cp.plan_strength(cp.PLANS[row['key']])
        self.assertEqual(len(rows), 49)
        self.assertIn('49 社区源逐源独立抓取方案', plans_text)
        self.assertIn(cp.RSSHUB_UPSTREAM['commit'], plans_text)
        self.assertIn('RSSHub 路由核对快照', routes_text)
        self.assertIn('RSSHub 路由证据', plans_text)

    def test_generated_docs_are_in_sync_with_the_registry(self):
        for path, renderer in ((PLANS_MD, cp.render_plans_md), (ROUTES_MD, cp.render_routes_md)):
            self.assertTrue(os.path.exists(path), f'缺少生成文档 {path}')
            with open(path, encoding='utf-8') as handle:
                on_disk = handle.read()
            self.assertEqual(on_disk, renderer(),
                             f'{os.path.basename(path)} 与 PLANS 注册表漂移，请重新生成')

    def test_plans_doc_lists_every_source_with_its_id(self):
        with open(PLANS_MD, encoding='utf-8') as handle:
            text = handle.read()
        for source in cd.COMMUNITIES:
            self.assertIn(f"## {source['id']} {source['name']}", text,
                          f"文档缺少 {source['id']} {source['name']}")
        self.assertIn('已核对：RSSHub 无路由的站点', text)

    def test_routes_doc_marks_unused_evidence_honestly(self):
        with open(ROUTES_MD, encoding='utf-8') as handle:
            text = handle.read()
        self.assertIn('| `/xueqiu/stock_comments/:id` |', text)
        self.assertIn('—', text, '未被任何源使用的证据行要如实标「—」')


class TestPipelineIntegration(unittest.TestCase):
    """⑤ 接入 fetch_live_snippets：只补强、不降级、可观测。"""

    def test_plan_rescues_a_source_the_generic_spider_could_not_read(self):
        carrier = cp.gnews_url('财联社 电报 港股', 'zh-CN')
        pages = {carrier: GOOD_RSS}
        session = session_for(pages)
        results, stats = cs.fetch_live_snippets([comm('CLS')], session=session, timeout=5,
                                                retries=0, concurrency=1, adaptive=False,
                                                obey_robots=False, now=NOW, verbose=False)
        item = results['CLS']
        self.assertTrue(item['ok'], '方案链应把抓不到的源救回来')
        self.assertEqual(item['strategy'], 'gnews_carrier')
        self.assertEqual(item['content_kind'], cp.KIND_EXCERPT)
        self.assertIn('plan_attempt', item)
        self.assertIn('plan_fetch', stats)
        self.assertEqual(stats['plan_fetch']['plan_sources'], 1)

    def test_weaker_plan_excerpt_never_overrides_adapter_comment_evidence(self):
        pages = {'https://www.cls.cn/telegraph': '<html><body><p>港股電報恒指南向資金淨流入。</p></body></html>'}
        session = session_for(pages)
        evidence = {'comments': [{'text': '港股估值有支撐，南向資金持續流入。', 'text_truncated': False,
                                  'post_title': '恒指討論', 'url': 'https://www.ptt.cc/bbs/Stock/M.1.A.html',
                                  'published_at': '', 'published_label': '10/30 09:45',
                                  'kind': 'comment', 'strategy': 'ptt_html',
                                  'metric': {'type': 'thread_net_pushes', 'value': 100, 'label': '≥100'},
                                  'scope': 'PTT Stock 板'}],
                    'attempt': {'strategy': 'ptt_html', 'status': 'ok', 'matched_comments': 1}}
        with mock.patch.object(cc, 'collect_comments', return_value=({'PTT': evidence}, {})):
            results, _stats = cs.fetch_live_snippets([comm('PTT')], session=session, timeout=5,
                                                     retries=0, concurrency=1, adaptive=False,
                                                     obey_robots=False, now=NOW, verbose=False)
        item = results['PTT']
        self.assertEqual(item['strategy'], 'ptt_html', '适配器证据必须保留')
        self.assertEqual(item['content_kind'], 'comment')
        self.assertEqual(item['comments'][0]['text'], '港股估值有支撐，南向資金持續流入。')

    def test_plan_layer_failure_does_not_break_the_round(self):
        session = session_for({})
        with mock.patch.object(cp, 'run_plans', side_effect=RuntimeError('plan layer down')):
            results, stats = cs.fetch_live_snippets([comm('CLS')], session=session, timeout=5,
                                                    retries=0, concurrency=1, adaptive=False,
                                                    obey_robots=False, now=NOW, verbose=False)
        self.assertIn('CLS', results, '方案层异常也要保留逐源记录')
        self.assertEqual(stats['plan_fetch'].get('error'), 'RuntimeError')
        self.assertFalse(results['CLS']['ok'])

    def test_plan_layer_can_be_switched_off_and_behaves_as_before(self):
        """`plan_strategies=False` 时整层不跑：不发方案请求，统计如实归零。"""
        carrier = cp.gnews_url('财联社 电报 港股', 'zh-CN')
        pages = {carrier: GOOD_RSS}
        calls = []
        session = session_for(pages, calls)
        results, stats = cs.fetch_live_snippets([comm('CLS')], session=session, timeout=5,
                                                retries=0, concurrency=1, adaptive=False,
                                                obey_robots=False, now=NOW, verbose=False,
                                                plan_strategies=False)
        self.assertFalse(results['CLS']['ok'], '关掉方案层就应回到「未获取」，不得偷偷补数据')
        self.assertNotIn('plan_attempt', results['CLS'])
        self.assertNotIn(carrier, calls, '关掉方案层就不得发任何方案请求')
        self.assertEqual(calls, ['https://www.cls.cn/telegraph'],
                         '只剩通用爬虫对目录 URL 的那一次请求')
        self.assertEqual(stats['plan_fetch']['attempted_sources'], 0)
        self.assertEqual(stats['plan_fetch']['requests_count'], 0)

    def test_plan_step_delay_follows_the_caller_download_delay(self):
        """步间隔沿用调用方的 download_delay，不另立一套礼貌口径。"""
        captured = {}
        real = cp.PlanBudget

        def spy(*args, **kwargs):
            captured.update(kwargs)
            return real(*args, **kwargs)

        with mock.patch.object(cp, 'PlanBudget', side_effect=spy):
            cs.fetch_live_snippets([comm('CLS')], session=session_for({}), timeout=5, retries=0,
                                   concurrency=1, adaptive=False, obey_robots=False, now=NOW,
                                   verbose=False, download_delay=0.42)
        self.assertEqual(captured.get('step_delay'), 0.42)

    def test_offline_dataset_still_reports_all_49_as_unavailable(self):
        data = cd.offline_dataset({'quotes': {}}, now=NOW)
        self.assertEqual(len(data['communities']), 49)
        self.assertEqual(data['summary']['ok'], 0, '离线兜底不得因为多了方案层就假装有数据')


if __name__ == '__main__':
    unittest.main(verbosity=2)
