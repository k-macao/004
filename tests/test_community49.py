#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""49 大社区回归 + 方案 A（严格真实口径 · 零幻觉防线）+ 跨域组合口径 + Scrapling 抓取框架接入。

核心防线：
  1. 接入 Scrapling 代码框架模型（scrapling_core.py / community_spider.py），覆盖全部 49 源，
     且打分器强制要求命中财经/市场主题词并拦截风控/JS 空壳文案；
  2. 严格真实口径（对齐 02 宏观快讯）：
     • 仅 `source="live"`（真实抓到公开讨论片段）展示摘录并由 `sentiment_nlp` 词库实算极性与相关性；
     • 未抓到 / 风控拦截 / 无匹配正文一律标记 `verdict_class="unavailable"`（未获取）、`quant=None`，
       绝不编造热评、绝不预设 10/11/17/11 多空立场、绝不使用 MD5 伪随机指标、绝不声称「综合站内 10 条」；
     • 全失败时 `panorama.scan` 与 `forecast.predict` 均计为数据缺口，不虚增覆盖度、不注入假情绪偏置；
     • `wechat_push.py` 严禁正则篡改旧抓取日期：当天执行过核验放行，旧快照在 `--push` 严格模式下阻断（exit 5）。
  3. 49 源跨域组合与微信单页 95,000 字符门禁保持完好。
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

import build_site as bs            # noqa: E402
import community_data as cd        # noqa: E402
import community_spider as cs      # noqa: E402
import forecast as fc              # noqa: E402
import panorama                    # noqa: E402
import scrapling_core as sc        # noqa: E402
import wechat_push as wp           # noqa: E402

NOW = datetime(2026, 10, 28, 3, 0, 0, tzinfo=timezone.utc)
TODAY = '2026-10-28'

MARKET = {'fetch_date': TODAY, 'quotes': {
    'HSI': {'name': '恒生指数', 'last': 26000, 'prev_close': 25800, 'pct': 0.78, 'as_of': TODAY},
    'HSTECH': {'name': '恒生科技指数', 'last': 5200, 'prev_close': 5150, 'pct': 0.97, 'as_of': TODAY},
    'HSCE': {'name': '恒生中国企业指数', 'last': 9000, 'prev_close': 8940, 'pct': 0.67, 'as_of': TODAY},
    'SPX': {'name': '标普 500', 'last': 6000, 'prev_close': 5990, 'pct': 0.17, 'as_of': '2026-10-27'},
    'NDQ': {'name': '纳斯达克', 'last': 21000, 'prev_close': 20900, 'pct': 0.48, 'as_of': '2026-10-27'},
    'DJI': {'name': '道琼斯', 'last': 43000, 'prev_close': 42900, 'pct': 0.23, 'as_of': '2026-10-27'},
    'GOLD': {'name': '现货黄金', 'last': 2650, 'prev_close': 2640, 'pct': 0.38, 'as_of': '2026-10-27'},
    'WTI': {'name': 'WTI 原油', 'last': 92, 'prev_close': 90, 'pct': 2.2, 'as_of': '2026-10-27'},
    'BRENT': {'name': '布伦特原油', 'last': 95, 'prev_close': 93, 'pct': 2.1, 'as_of': '2026-10-27'},
    'USDCNH': {'name': '美元/离岸人民币', 'last': 6.71, 'prev_close': 6.72, 'pct': -0.15, 'as_of': TODAY},
    'USDCNY': {'name': '美元/在岸人民币', 'last': 6.70, 'prev_close': 6.71, 'pct': -0.15, 'as_of': TODAY},
}}


def _mock_checked_dataset(live_keys=('FUTU', 'XUEQIU', 'REDDIT'), now=NOW):
    """构造一次真实的 49 源核验结果：指定源抓到真实片段，其余源为 HTTP 403 未获取。"""
    live_set = set(live_keys)
    snippets = {
        'FUTU': '港股早盘走强，恒指放量突破，南向资金大幅净买入，科技股领涨回暖。',
        'XUEQIU': '美联储降息节奏不明，港股短线下跌承压，部分获利盘净流出引发担忧。',
        'REDDIT': 'Hang Seng Index valuation remains cheap with strong buyback inflows, though tariff pressure lingers.',
    }

    def transport(method, url, headers, timeout, proxy=None, body=None):
        for comm in cd.COMMUNITIES:
            if comm['url'] == url and comm['key'] in live_set:
                text = snippets.get(comm['key'], f"{comm['name']} 恒指与港股流动性讨论")
                html = f"<html><head><title>{comm['name']}</title></head><body><article><p>{text}</p></article></body></html>"
                return 200, 'OK', {'Content-Type': 'text/html; charset=utf-8'}, html.encode('utf-8'), url
        return 403, 'Forbidden', {'Content-Type': 'text/html'}, b'Access Denied', url

    with tempfile.TemporaryDirectory() as tmp:
        session = sc.FetcherSession(transport=transport, retries=0)
        return cd.generate_all_communities(
            market=MARKET, demo_mode=False, now=now, session=session,
            storage_file=os.path.join(tmp, 'adapt.db'), verbose=False,
            autothrottle=False, max_blocked_retries=0, download_delay=0.0)


class TestCommunityCatalog(unittest.TestCase):
    def test_has_49_sources_with_unique_ids_and_keys(self):
        comms = cd.COMMUNITIES
        self.assertEqual(len(comms), 49, '14 原有 + 20 前次新增 + 15 本次扩容 = 49 源')
        self.assertEqual([c['id'] for c in comms], [f'{i:02d}' for i in range(1, 50)])
        keys = [c['key'] for c in comms]
        self.assertEqual(len(set(keys)), 49, '社区 key 不得重复')
        self.assertEqual(len({c['name'] for c in comms}), 49, '社区名不得重复')
        self.assertEqual(keys[0], 'FUTU')
        self.assertEqual(keys[13], 'FINTWIT')

    def test_15_new_sources_cover_more_languages_and_types_and_no_preset_bias(self):
        new = cd.COMMUNITIES[34:]
        self.assertEqual(len(new), 15, '本次扩容 15 个社区')
        self.assertEqual([c['key'] for c in new][0], 'JISILU')
        self.assertEqual([c['key'] for c in new][-1], 'AASTOCKS')
        english_names = [c for c in new if re.search(r'[A-Za-z]{4,}', c['name'])]
        self.assertGreaterEqual(len(english_names), 4, '扩容里要有足量英文社区')
        types = {c.get('ctype') for c in cd.COMMUNITIES}
        self.assertGreaterEqual(len(types), 30, f'社区类型要足够多样，实际 {len(types)} 类')
        for must in ('中文低风险投资社区', '中文短视频社区', '英文量化社区', '数字资产论坛',
                     '英文基金研究社区', '日文社区', '韩文社区', '德文社区', '香港本地财经社区'):
            self.assertIn(must, types, f'缺少「{must}」这类社区')
        for c in cd.COMMUNITIES:
            self.assertTrue(c.get('ctype'), f"{c['key']} 缺 ctype（社区类型）")
            self.assertTrue(c['url'].startswith('http'), f"{c['key']} 缺可抓取 URL")
            self.assertNotIn('verdict_class', c, f"{c['key']} 目录层不得预设写死多空立场")
            self.assertNotIn('meta_tpl', c, f"{c['key']} 目录层不得写死「综合站内 10 条」虚报模板")

    def test_url_targets_match_declared_communities(self):
        by_key = {c['key']: c['url'] for c in cd.COMMUNITIES}
        self.assertNotIn('998407', by_key['YAHOOJP'], '日本 Yahoo 不得再指向日经 225 代码 998407.O')
        self.assertNotIn('005930', by_key['NAVER'], 'NAVER 不得再指向三星电子 005930')
        self.assertIn('HSI', by_key['NAVER'])
        self.assertNotIn('#HSI', by_key['XUEQIU'], '雪球 URL 不应依赖服务端不可见的客户端 # 锚点')
        self.assertNotIn('%23A%E8%82%A1%23', by_key['WEIBO'], '微博港股观察不应抓纯 #A股# 搜索')

    def test_offline_dataset_is_honestly_unavailable(self):
        d = cd.offline_dataset(MARKET, now=NOW)
        self.assertEqual(d['mode'], 'offline')
        self.assertEqual(d['fetch_date'], TODAY)
        self.assertEqual(len(d['communities']), 49)
        self.assertEqual(d['summary']['ok'], 0)
        self.assertEqual(d['summary']['unavailable'], 49)
        self.assertEqual(len(d['summary']['failed']), 49)
        self.assertTrue(d['fetch_engine']['engine'].startswith('scrapling-core'))
        for c in d['communities']:
            self.assertFalse(c['available'])
            self.assertEqual(c['verdict_class'], 'unavailable')
            self.assertEqual(c['verdict_label'], '未获取')
            self.assertIsNone(c['quant'])
            self.assertEqual(c['verdict'], '')
            self.assertIn('当次未获取', c['quote'])
            self.assertNotIn('综合站内 10 条', c['meta'])
            self.assertNotIn('最新读取', c['meta'])
            self.assertNotIn('抓取核验', c['meta'], '未执行抓取的离线兜底不得伪造核验日期标记')


class TestStrictZeroHallucination(unittest.TestCase):
    """方案 A 反幻觉核心防线：零硬编码旧叙事、零 MD5 伪打分、真实 NLP 自洽、下游隔离、诚实日期门禁。"""

    FORBIDDEN_HALLUCINATIONS = (
        '628.69', '49:51', '4.67%', '中际旭创', '新易盛', '天孚通信',
        '25,124', '26,299', '综合站内 10 条', '综合站内 10 篇', '综合站内 10 套',
        '25440.17',
    )

    def test_codebase_and_template_have_zero_hardcoded_community_hallucinations(self):
        for rel in ('community_data.py', 'build_site.py', 'tools/wechat_push.py', 'report.html'):
            path = os.path.join(REPO_ROOT, rel)
            with open(path, encoding='utf-8') as fh:
                content = fh.read()
            for bad in self.FORBIDDEN_HALLUCINATIONS:
                if bad == '25440.17' and rel == 'build_site.py':
                    # build_site.py 仅在 fmt_last docstring 举例数字格式，检查非注释代码行无写死回退
                    code_lines = '\n'.join(
                        ln for ln in content.splitlines()
                        if not ln.strip().startswith(('#', '"""', "'''", '•')) and '"""' not in ln
                    )
                    self.assertNotIn(bad, code_lines, f'{rel} 代码逻辑不得回退历史点位 {bad}')
                else:
                    self.assertNotIn(bad, content, f'{rel} 仍残留旧幻觉内容: {bad}')

    def test_fmt_hsi_never_falls_back_to_august_price(self):
        empty_hsi = cd.fmt_hsi({})
        self.assertFalse(empty_hsi['available'])
        self.assertEqual(empty_hsi['last'], '—')
        self.assertIsNone(empty_hsi['raw_last'])
        self.assertIsNone(empty_hsi['raw_pct'])

    def test_live_snippet_nlp_is_deterministic_and_self_consistent(self):
        bull = cd.analyze_live_snippet('恒指放量大涨突破，南向资金持续净流入，科技股盈利超预期回暖。')
        bear = cd.analyze_live_snippet('港股跳水大跌破位，外资大幅净流出，业绩暴雷引发恐慌抛售。')
        mixed = cd.analyze_live_snippet('低估值回购利好托底反弹，但关税冲击与获利盘流出令短线承压下跌。')
        neutral = cd.analyze_live_snippet('市场围绕港股通结算机制与指数成分股权重调整规则展开讨论。')

        self.assertEqual(bull['verdict_class'], 'bull')
        self.assertGreaterEqual(bull['quant']['sentiment']['score'], 0.15)
        self.assertEqual(bear['verdict_class'], 'bear')
        self.assertLessEqual(bear['quant']['sentiment']['score'], -0.15)
        self.assertEqual(mixed['verdict_class'], 'mixed')
        self.assertEqual(neutral['verdict_class'], 'neutral')

        # 确定性验证：不依赖日期或 MD5，重复调用结果完全一致，且不再伪造「首发」
        again = cd.analyze_live_snippet('恒指放量大涨突破，南向资金持续净流入，科技股盈利超预期回暖。')
        self.assertEqual(bull, again)
        self.assertNotIn('首发)', bull['quant']['novelty']['display'])

    def test_spider_rejects_antibot_and_non_financial_http_200_pages(self):
        pages = {
            cd.COMMUNITIES[0]['url']: '<html><body><h1>Enable JavaScript and cookies to continue</h1></body></html>',
            cd.COMMUNITIES[1]['url']: '<html><body><article><p>今天的天气真不错，周末大家准备去哪里旅游拍照？</p></article></body></html>',
        }

        def transport(method, url, headers, timeout, proxy=None, body=None):
            body_html = pages.get(url, '<html><body>blocked</body></html>')
            status = 200 if url in pages else 403
            return status, 'OK', {'Content-Type': 'text/html; charset=utf-8'}, body_html.encode('utf-8'), url

        with tempfile.TemporaryDirectory() as tmp:
            session = sc.FetcherSession(transport=transport, retries=0)
            out = cd.generate_all_communities(
                market=MARKET, now=NOW, session=session,
                storage_file=os.path.join(tmp, 'adapt.db'), limit=2, verbose=False,
                autothrottle=False, max_blocked_retries=0, download_delay=0.0)
        self.assertEqual(out['summary']['ok'], 0)
        self.assertEqual(out['communities'][0]['verdict_class'], 'unavailable')
        self.assertEqual(out['communities'][1]['verdict_class'], 'unavailable')

    def test_unavailable_communities_do_not_pollute_panorama_or_forecast(self):
        all_failed = _mock_checked_dataset(live_keys=(), now=NOW)
        self.assertEqual(all_failed['summary']['ok'], 0)

        scan_res = panorama.scan(market=MARKET, macro={}, sentiment={}, community=all_failed, now=NOW)
        self.assertTrue(any('社区研判' in g for g in scan_res['data_gaps']),
                        '49 源全未抓到时，01 全景扫描必须将社区计为数据缺口')
        # 覆盖度按「路数」计：行情可用、其余各路全缺 → 1/路数。
        # 不写死百分比，路由数变化时断言仍成立，但「空壳社区不得虚增覆盖」这条不被放宽。
        expect = round(1.0 / panorama.COVERAGE_ROUTES, 2)
        self.assertEqual(scan_res['coverage'], expect,
                         f'仅行情一路可用时覆盖度必须为 {expect:.0%}，不得因空壳社区虚增')

        fc_res = fc.predict(market=MARKET, macro={}, sentiment={}, community=all_failed, now=NOW)
        self.assertTrue(any('社区研判' in g for g in fc_res['data_gaps']),
                        '49 源全未抓到时，04 AI 预测必须将社区计为数据缺口')
        self.assertEqual(fc_res['coverage'], expect)
        self.assertIsNone(fc_res['signals']['sentiment'].get('z'),
                          '0 个实抓社区时不得向 AI 预测注入虚假负向或正向情绪偏置')
        self.assertEqual(fc_res['signals']['sentiment']['counts'],
                         {'bull': 0, 'bear': 0, 'neutral': 0, 'mixed': 0})


class TestScraplingEngineWiring(unittest.TestCase):
    """抓取层（Scrapling 框架模型）真的接进来了，且覆盖 49 个源。"""

    def test_spider_is_the_fetch_path_and_covers_every_source(self):
        self.assertTrue(issubclass(cs.CommunitySpider, sc.Spider),
                        'CommunitySpider 必须继承移植后的 Spider 基类')
        missing = [c['key'] for c in cd.COMMUNITIES if c['key'] not in cs.SITE_RULES]
        self.assertEqual(missing, [], f'每个社区源都要有抽取规则，缺: {missing}')
        self.assertTrue(cd.ADAPTIVE_DB_DEFAULT.endswith('community_adaptive.db'))
        self.assertIn('.scrapling', cd.ADAPTIVE_DB_DEFAULT, '自适应指纹库要落在忽略目录里')

    def test_community_data_main_uses_the_spider(self):
        """`community_data.py` 的 live 分支必须走 community_spider，且真实片段原样保留。"""
        captured = {}

        def fake_fetch(communities, **kwargs):
            captured['sources'] = list(communities)
            captured['kwargs'] = kwargs
            results = {c['key']: {'key': c['key'], 'name': c['name'], 'status': 200, 'ok': True,
                                  'snippet': f"现场片段 {c['name']} · 恒指 26,012 点南向资金净流入",
                                  'selector': 'h3', 'adaptive': False, 'bytes': 1024}
                       for c in communities}
            stats = {'engine': sc.ENGINE_NAME, 'backend': 'injected', 'sources': len(communities),
                     'ok': len(communities), 'failed': [], 'adaptive_hits': 0,
                     'blocked_requests_count': 0, 'elapsed': 0.1}
            return results, stats

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'community_data.json')
            market = os.path.join(tmp, 'market_data.json')
            with open(market, 'w', encoding='utf-8') as handle:
                json.dump(MARKET, handle, ensure_ascii=False)
            original = cs.fetch_live_snippets
            cs.fetch_live_snippets = fake_fetch
            argv = sys.argv[:]
            sys.argv = ['community_data.py', '--json', out, '--market-data', market]
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    cd.main()
            finally:
                cs.fetch_live_snippets = original
                sys.argv = argv
            with open(out, encoding='utf-8') as handle:
                data = json.load(handle)
        self.assertEqual(len(captured['sources']), 49, '抓取入口必须收到 49 个源')
        self.assertEqual(len(data['communities']), 49)
        self.assertEqual(data['summary']['ok'], 49)
        self.assertTrue(all(c['source'] == 'live' and c['available'] for c in data['communities']))
        self.assertIn('恒指 26,012 点南向资金净流入', data['communities'][0]['quote'], '活数据完整保留进正文')
        self.assertEqual(data['fetch_engine']['backend'], 'injected')


class TestWechat49Sources(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, payload):
        path = os.path.join(self._tmp.name, name)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        return path

    def _render(self, communities, now=NOW):
        env = {
            'MARKET_DATA': self._write('market_data.json', MARKET),
            'COMMUNITY_DATA': self._write('community_data.json', communities),
            'SENTIMENT_DATA': os.path.join(self._tmp.name, 'missing.json'),
            'MACRO_DATA': os.path.join(self._tmp.name, 'missing.json'),
            'MACRO_AUTO_FETCH': '0',
            'FORECAST_HISTORY': os.path.join(self._tmp.name, 'forecast_history.json'),
        }
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                html, _ts, _tsf = wp.build_single_wechat_html(now=now)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return html

    def test_all_49_checked_sources_carry_honest_verification_markers_and_block_stale_dates(self):
        d = _mock_checked_dataset(now=NOW)
        html = self._render(d, now=NOW)
        markers = wp.CHANNEL_READ_RE.findall(html)
        self.assertEqual(len(markers), 49, '当次执行过抓取核验的 49 源均应带核验日期标记')
        self.assertEqual(set(markers), {TODAY})
        self.assertIn('49 大平台', html)
        self.assertIn('南向资金大幅净买入', html, '微信推送必须保留实盘抓到的真实片段')
        self.assertNotIn('跨域配对：', html, '配对交易已下线，推送里不应再出现跨域配对标签')
        self.assertNotIn('◆ AI 量化', html, '配对交易已下线，推送里不应再出现 AI 量化块')
        self.assertLess(len(html), wp.CONTENT_SAFE_LIMIT, '49 源仍须落在推送字符门禁之内')
        for key in ('ZHIHU', 'PTT', 'FTALPHA', 'JISILU', 'NAVER', 'AASTOCKS'):
            src = next(c for c in d['communities'] if c['key'] == key)
            self.assertIn(src['name'], html, f'{key} 必须出现在推送正文里')

        # 关键反作弊断言：旧日期的 community_data.json 渲染到明天时，日期不得被正则洗白，--push 严格模式必须拦截！
        tomorrow = NOW + timedelta(days=1)
        stale_html = self._render(d, now=tomorrow)
        self.assertEqual(set(wp.CHANNEL_READ_RE.findall(stale_html)), {TODAY},
                         '严禁用正则把昨天的抓取日期改写成今天')
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(io.StringIO()):
                wp.assert_fetch_dates_are_today([('title', stale_html)], tomorrow, strict=True)
        self.assertEqual(ctx.exception.code, 5)

    def test_budget_ladder_keeps_every_source_and_keeps_forecast(self):
        d = _mock_checked_dataset(now=NOW)
        html = self._render(d, now=NOW)
        self.assertNotIn('<!--COMMUNITY-SLOT-->', html)
        self.assertNotIn('<!--FORECAST-SLOT-->', html)
        for c in d['communities']:
            self.assertIn(c['name'], html, f"{c['key']} 在预算降级时被整条删掉了")
        self.assertIn('04 / AI 预测', html)

    def test_fewer_sources_still_render_and_use_the_same_structure(self):
        d = _mock_checked_dataset(now=NOW)
        small = dict(d, communities=d['communities'][:3], summary={'ok': 2, 'total': 3, 'failed': ['老虎社区']})
        html = self._render(small, now=NOW)
        self.assertEqual(len(wp.CHANNEL_READ_RE.findall(html)), 3)
        self.assertIn('3 个境内外核心社区信号', html)
        self.assertNotIn('49 大平台', html, '源数文案必须跟着当次数据走')

    def test_expected_channel_count_matches_the_catalog(self):
        self.assertEqual(wp.EXPECTED_CHANNEL_COUNT, len(cd.COMMUNITIES))
        self.assertEqual(wp.EXPECTED_CHANNEL_COUNT, 49)
        parts = [('t', '\n'.join(f'公开页实抓摘录 · 中文社区 · 抓取核验 {TODAY}' for _ in range(49)))]
        wp.assert_fetch_dates_are_today(parts, NOW, strict=True)
        short = [('t', f'公开页实抓摘录 · 中文社区 · 抓取核验 {TODAY}')]
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                wp.assert_fetch_dates_are_today(short, NOW, strict=True)


if __name__ == '__main__':
    unittest.main()
