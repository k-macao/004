# -*- coding: utf-8 -*-
"""舆情/新闻因子接入层测试（聚宽 · 米筐 · 掘金 · 优矿 + 免费兜底源）。

运行：python3 -m unittest discover -s tests -v      （或 python3 tests/test_sentiment.py）
全部用 tests/fixtures 里的录制报文，零联网、零凭据，可在任意 CI 上跑。
覆盖：自建词库规则 → 适配器解析 → 因子合成与降级 → 站点/微信注入 → 探针打分报告。
"""
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sentiment_adapters as ad          # noqa: E402
import sentiment_factors as sf           # noqa: E402
import sentiment_match as smatch         # noqa: E402
import sentiment_nlp as nlp              # noqa: E402
import sentiment_sources as reg          # noqa: E402

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures')


def _load_module(name, relpath):
    """按路径加载 tools/ 下的脚本模块（tools 不是包）。"""
    path = os.path.join(ROOT, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class capture_stdout:
    """捕获 stdout（管线 print 较多，测试里静音）。"""

    def __enter__(self):
        self._buf = io.StringIO()
        self._old = sys.stdout
        sys.stdout = self._buf
        return self._buf

    def __exit__(self, *exc):
        sys.stdout = self._old


class TestNlpRules(unittest.TestCase):
    """自建中文金融词库：极性、否定翻转、程度修饰、风险词。"""

    def test_positive_and_negative(self):
        pos = nlp.score_text('公司业绩大增，净利润同比上涨30%，获多家机构调研推荐')
        neg = nlp.score_text('公司业绩大幅下滑，计提减值并被立案调查，股价跌停')
        self.assertGreater(pos['sentiment'], 0.3, pos)
        self.assertLess(neg['sentiment'], -0.3, neg)
        self.assertGreater(neg['risk_score'], 0, '风险词应计分')

    def test_negation_flips_polarity(self):
        a = nlp.score_text('盈利增长')
        b = nlp.score_text('盈利未增长')
        self.assertGreater(a['sentiment'], 0)
        self.assertLess(b['sentiment'], a['sentiment'], '否定词应显著拉低情感值')

    def test_degree_modifier_scales(self):
        small = nlp.score_text('业绩小幅预增')
        big = nlp.score_text('业绩大幅预增')
        self.assertGreater(abs(big['raw']), abs(small['raw']), '程度副词应改变得分幅度')
        self.assertGreater(big['sentiment'], small['sentiment'], '同一情感词：大幅 > 小幅')
        mild = nlp.score_text('净利小幅下滑')
        severe = nlp.score_text('净利大幅下滑')
        self.assertLess(severe['sentiment'], mild['sentiment'], '负面侧同样应被程度词区分')

    def test_neutral_text(self):
        r = nlp.score_text('今日上午召开董事会会议，审议常规议案')
        self.assertAlmostEqual(r['sentiment'], 0.0, places=6, msg=r)

    def test_time_decay(self):
        old = nlp.score_text('业绩大增', published_at='2026-01-01 09:00:00',
                             ref_time=__import__('datetime').datetime(2026, 9, 1, 9, 0, 0))
        new = nlp.score_text('业绩大增', published_at='2026-09-01 08:30:00',
                             ref_time=__import__('datetime').datetime(2026, 9, 1, 9, 0, 0))
        self.assertLess(old['weight'], new['weight'], '旧消息权重应随时间衰减')

    def test_self_test_script_passes(self):
        p = subprocess.run([sys.executable, os.path.join(ROOT, 'sentiment_nlp.py'), '--self-test'],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class TestRegistry(unittest.TestCase):
    """源注册表：11 个接口全部有适配器，且口径字段齐备。"""

    def test_all_sources_have_adapters_and_fixtures(self):
        for src in reg.SOURCES:
            sid = src['id']
            kind = src['kind']
            self.assertIn(kind, ad.PARSERS, f'{sid} 缺解析器（kind={kind}）')
            self.assertTrue(kind in ad.CALLERS or kind in ad.NO_LIVE_CALLER,
                            f'{sid} 既无 live 调用器也未标注 NO_LIVE_CALLER')
            if src.get('category') != 'none':
                self.assertTrue(os.path.exists(os.path.join(FIXTURES, f'{sid}.json')),
                                f'{sid} 缺 mock 报文')
            for key in ('platform', 'name', 'category', 'access', 'kind', 'endpoint',
                        'update_freq', 'granularity', 'history', 'coverage', 'quota',
                        'cost', 'docs', 'doc_scores', 'requires', 'auth_env'):
                self.assertIn(key, src, f'{sid} 缺字段 {key}')
            self.assertIn(src['mode'] if 'mode' in src else src['access'],
                          ('http_free', 'http', 'python_sdk', 'local_terminal', 'sdk'),
                          f'{sid} 接入方式异常')
            for dim, _w, _n, _d in reg.SCORE_DIMENSIONS:
                self.assertIn(dim, src['doc_scores'], f'{sid} 缺评分维度 {dim}')

    def test_gm_capability_gap_recorded(self):
        gm = reg.get_source('GM_SDK')
        self.assertEqual(gm['category'], 'none', '掘金无舆情接口，必须显式标注能力缺失')
        self.assertIn('verdict_hint', gm, '能力缺失需给出判定依据，避免被依赖缺失淹没')

    def test_watchlist_names_cover_watchlist(self):
        for w in reg.WATCHLIST:
            self.assertIn(w.split('.')[0], reg.WATCHLIST_NAMES, f'{w} 缺中文名映射')


class TestNormSymbol(unittest.TestCase):
    """各平台代码格式差异必须归一，否则个股舆情会串行。"""

    def test_formats(self):
        cases = {'601318': '601318', '601318.SH': '601318', 'SH601318': '601318',
                 '1.601318': '601318', '0.600036': '600036', 'SZ600036': '600036',
                 '300750.XSHE': '300750', '00700.HK': '00700'}
        for raw, want in cases.items():
            self.assertEqual(ad.norm_symbol(raw), want, raw)

    def test_non_codes_rejected(self):
        for raw in ('20260915001', '恒生指数', '', None, 1, 'SH6000'):
            self.assertEqual(ad.norm_symbol(raw), '', f'{raw!r} 不应被当作股票代码')


class TestAdaptersMock(unittest.TestCase):
    """mock 回放：录制报文必须能解析成统一结构。"""

    def test_all_sources_parse(self):
        for sid in reg.ids():
            r = ad.fetch(sid, mode='mock', symbols=['601318.SH', '600036.SZ', '300750.SZ'])
            src = reg.get_source(sid)
            if src.get('category') == 'none':          # 掘金：能力缺失，必须优雅返回空
                self.assertEqual(r['news'], [], sid)
                self.assertEqual(r['series'], [], sid)
                self.assertIn('掘金', (r['meta'].get('note') or '') + (r.get('error') or ''))
                continue
            self.assertTrue(r['ok'], f"{sid} 解析失败: {r.get('error')}")
            self.assertTrue(r['news'] or r['series'], f'{sid} 应至少产出新闻或时序')
            for item in r['news']:
                for key in ('symbol', 'title', 'content', 'published_at', 'source'):
                    self.assertIn(key, item, f'{sid} 新闻字段缺失 {key}')
                self.assertTrue(item['title'], f'{sid} 出现空标题新闻')
                self.assertLessEqual(len(item['symbol'].split('.')[0]), 6, item['symbol'])
            for row in r['series']:
                self.assertIn('date', row)
                self.assertIsNotNone(row.get('value'), f'{sid} 时序值不可为空: {row}')

    def test_native_sentiment_is_preserved(self):
        """米筐/优矿给到的现成情感值必须原样带出，供 aggregate 优先采用。"""
        r = ad.fetch('RQ_SDK', mode='mock')
        self.assertTrue(any(n.get('sentiment') is not None for n in r['news']),
                        'RQ_SDK 应带 sentiment（平台现成因子口径）')

    def test_cli_mock_mode(self):
        p = subprocess.run([sys.executable, os.path.join(ROOT, 'sentiment_adapters.py'),
                            '--mode', 'mock'], capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class TestSentimentFactors(unittest.TestCase):
    """因子合成：写盘结构、降级行为、与站点/微信的联动。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='sent-')
        cls.out = os.path.join(cls.tmp, 'sentiment_data.json')
        cls.hist = os.path.join(cls.tmp, 'sentiment_history.json')
        _hist, _default = sf.HISTORY_PATH, sf.DEFAULT_OUT
        sf.HISTORY_PATH, sf.DEFAULT_OUT = cls.hist, cls.out
        try:
            with capture_stdout() as buf:
                cls.data = sf.run(mode='mock', out_path=cls.out, verbose=False)
        finally:
            sf.HISTORY_PATH, sf.DEFAULT_OUT = _hist, _default
        cls.html = buf.getvalue()

    def test_market_factors_in_range(self):
        m = self.data['market']
        self.assertGreaterEqual(m['news_count'], 10)
        self.assertGreaterEqual(m['sent_temp'], 0)
        self.assertLessEqual(m['sent_temp'], 100)
        self.assertGreaterEqual(m['net_senti'], -1)
        self.assertLessEqual(m['net_senti'], 1)
        self.assertGreaterEqual(m['neg_share'], 0)
        self.assertLessEqual(m['neg_share'], 100)
        self.assertIn(m['label'], ['极度亢奋', '偏热', '中性', '偏冷', '恐慌', '极度悲观'])

    def test_factor_library_filled(self):
        for key, meta in self.data['factors'].items():
            self.assertIn('value', meta, key)
            self.assertIsInstance(meta['value'], (int, float), f'{key} 值应为数值: {meta}')
            self.assertTrue(meta.get('name') and meta.get('definition'), f'{key} 缺口径说明')

    def test_platform_native_vs_self_built_split(self):
        m = self.data['market']
        self.assertGreater(m['platform_native'], 0, '平台现成因子条数应被统计')
        self.assertEqual(m['self_built'], m['news_count'] - m['platform_native'])

    def test_stock_rows_normalized(self):
        syms = [s['symbol'] for s in self.data['stocks']]
        for s in self.data['stocks']:
            self.assertRegex(s['symbol'], r'^\d{5,6}$', f"未归一个股代码: {s}")
            self.assertGreaterEqual(s['news_count'], 0)
        self.assertTrue(len(syms) == 0 or any(s in syms for s in ('601318','600036','300750')), f'expected replacement stocks in syms, got {syms}')
        self.assertNotIn('600519', syms, '指定个股已删除，不应再出现')
        self.assertNotIn('600000', syms)
        self.assertNotIn('000001', syms)
        self.assertNotIn('1', syms, '文章流水号不得被当成分散个股代码')

    def test_matches_align_with_report_targets(self):
        """新功能：采集到的新闻舆情必须匹配到日报标的，且匹配层不含来源信息。"""
        mm = self.data.get('matches') or {}
        self.assertTrue(mm, 'sentiment_data.json 应带 matches（采集 → 匹配层）')
        self.assertEqual(mm['total_news'], self.data['market']['news_count'])
        self.assertGreater(mm['matched_news'], 0, '录制报文里应至少匹配到一个日报标的')
        self.assertEqual(mm['matched_news'] + mm['unmatched'], mm['total_news'])
        keys = {t['key'] for t in mm['all_targets']}
        self.assertTrue({'HSI', 'HSTECH', 'HSCE', 'SPX', 'NDQ', 'DJI',
                         'GOLD', 'WTI', 'USDCNH'} <= keys, '标的键需与行情表对齐')
        for t in mm['all_targets']:
            for banned in ('source', 'platform', 'src', 'media'):
                self.assertNotIn(banned, t, f'匹配层不得携带来源字段 {banned}: {t}')
            if t['hits']:
                self.assertGreater(t['relevance'], 0, t['key'])
                self.assertTrue(t['top_titles'], f"{t['key']} 命中后应给出代表新闻")
                self.assertIn(t['label'], ['极度亢奋', '偏热', '中性', '偏冷', '恐慌', '极度悲观'])
        self.assertTrue(mm['keywords_used'], '应记录本次采集关键词（便于复核匹配口径）')
        # 采集关键词本身必须能命中日报标的，否则「采集 → 匹配」对不上
        for kw in smatch.search_keywords():
            self.assertTrue(any(kw in t['name'] or kw in ' '.join(t['keywords'])
                                for t in smatch.TARGETS), f'采集关键词与标的脱节: {kw}')

    def test_api_eval_embedded_if_available(self):
        ev = self.data.get('api_eval')
        if not os.path.exists(sf.PROBE_REPORT):
            self.assertIsNone(ev)
            return
        self.assertTrue(ev and ev.get('ranking'), '已跑过探针时应把评测矩阵带进日报')
        self.assertTrue(ev.get('generated_at'), '评测时间应可从 meta 读出')

    def test_cli_writes_json_and_degrades(self):
        p = subprocess.run([sys.executable, os.path.join(ROOT, 'sentiment_factors.py'),
                            '--mock', '--json', self.out, '--quiet'],
                           capture_output=True, text=True, cwd=ROOT, timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(self.out, encoding='utf-8') as f:
            json.load(f)

    def test_offline_mode_never_raises(self):
        """断网兜底：无历史文件时也必须产出中性降级结果（且 verbose 打印不能崩）。"""
        p = subprocess.run([sys.executable, os.path.join(ROOT, 'sentiment_factors.py'),
                            '--offline', '--json', os.path.join(self.tmp, 'none.json')],
                           capture_output=True, text=True, cwd=ROOT, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('断网兜底', p.stdout, '--offline 应说明自己用的是兜底数据')

    def test_series_rows_keep_one_calibre(self):
        """指数序列只能放情绪指数：关注度/热度类序列必须走个股热度表，否则口径混淆。"""
        for row in self.data['series']:
            self.assertNotIn('factor', row.get('extra') or {}, row)
            self.assertIn('platform', row, f'指数行需标注来源平台: {row}')
            self.assertTrue(row.get('note'), '不同平台指数口径必须随值标注')
            self.assertLess(abs(row['value']), 1e6)


class TestReportAndPush(unittest.TestCase):
    """站点与微信推送：注入生效 + 缺数据时降级不阻断。"""

    @classmethod
    def setUpClass(cls):
        cls.build_site = _load_module('build_site', 'build_site.py')
        cls.wechat = _load_module('wechat_push', os.path.join('tools', 'wechat_push.py'))
        cls.tmp = tempfile.mkdtemp(prefix='sent-site-')
        cls.sent = os.path.join(cls.tmp, 'sentiment_data.json')
        with open(cls.sent, 'w', encoding='utf-8') as f:
            json.dump({'mode': 'mock', 'fetch_date': '2026-09-16',
                       'market': {'sent_temp': 61.2, 'label': '偏热', 'net_senti': 0.31,
                                  'neg_share': 22.5, 'news_count': 18, 'heat_z': 1.1,
                                  'risk_score': 20.0, 'platform_native': 6, 'self_built': 12,
                                  'events': [{'title': '某公司被立案调查', 'terms': ['立案'],
                                               'risk_score': 30.0}]},
                       'stocks': [{'symbol': '300750', 'name': '宁德时代', 'heat': 98712.0,
                                   'heat_z': 1.3, 'net_senti': 0.2, 'news_count': 3,
                                   'risk_score': 0.0, 'as_of': '2026-09-15'}],
                       'sources': [{'id': 'RQ_SDK', 'platform': '米筐 RiceQuant',
                                    'name': '新闻舆情', 'ok': True, 'mode': 'live',
                                    'news': 4, 'series': 0, 'latest_date': '2026-09-15',
                                    'error': '', 'note': '', 'native_sentiment': 4}],
                       'api_eval': {'generated_at': '2026-09-16 04:00:00 UTC', 'mode': 'mock',
                                    'ranking': [{'id': 'RQ_SDK', 'platform': '米筐 RiceQuant',
                                                 'name': 'news.get_stock_news', 'score': 67,
                                                 'verdict': 'READY_WITH_LICENCE',
                                                 'verdict_label': '可接入（需开权限/付费）'}]},
                       'matches': smatch.build_matches([
                           {'title': '恒指收跌 2.22%，南向资金逆势净流入港股',
                            'published_at': '2026-09-16 08:00:00'},
                           {'title': '现货黄金刷新历史高位，避险资金涌入贵金属',
                            'published_at': '2026-09-16 07:30:00', 'sentiment': 0.8},
                           {'title': '美联储 9 月加息概率下降，CPI 同比回落至 3.4%',
                            'published_at': '2026-09-16 06:00:00'}]),
                       'summary': {'total': 11, 'ok': 10, 'failed': ['GM_SDK']},
                       'series': [{'date': '2026-09-15', 'value': 1.02, 'name': '市场情绪指数'}]},
                      f, ensure_ascii=False)
        with open(os.path.join(cls.tmp, 'empty_market.json'), 'w', encoding='utf-8') as f:
            json.dump({'mode': 'mock', 'market': {}, 'summary': {}}, f)

    def test_sentiment_tokens(self):
        tokens = self.build_site._sentiment_tokens(
            json.load(open(self.sent, encoding='utf-8')), '2026-09-16')
        self.assertEqual(tokens['{{SENT_TEMP}}'], '61.2')
        self.assertEqual(tokens['{{SENT_LABEL}}'], '偏热')
        self.assertIn('10/11', tokens['{{SENT_SOURCE_STATUS}}'])
        # 对外不显示数据来源：状态文案只给降级个数，不列接口 ID / 平台名
        self.assertNotIn('GM_SDK', tokens['{{SENT_STATUS}}'])
        self.assertIn('1 个接口自动降级', tokens['{{SENT_STATUS}}'])
        os.environ['SENTIMENT_SHOW_SOURCE'] = '1'
        try:
            shown = self.build_site._sentiment_tokens(
                json.load(open(self.sent, encoding='utf-8')), '2026-09-16')
            self.assertIn('GM_SDK', shown['{{SENT_STATUS}}'], '内部视图应能看到降级接口 ID')
        finally:
            os.environ.pop('SENTIMENT_SHOW_SOURCE', None)
        for key, val in tokens.items():
            self.assertNotIn('\x00', str(val), key)
            self.assertLess(len(str(val)), 400, f'{key} 过长，可能把原始文本灌进模板')

    def test_tokens_defaults_when_missing(self):
        tokens = self.build_site._sentiment_tokens({}, '2026-09-16')
        self.assertEqual(set(tokens), {'{{SENT_TEMP}}', '{{SENT_LABEL}}', '{{SENT_NET}}',
                                       '{{SENT_NEG}}', '{{SENT_NEWS}}', '{{SENT_HEATZ}}',
                                       '{{SENT_RISK}}', '{{SENT_DATE}}', '{{SENT_MODE}}',
                                       '{{SENT_NATIVE}}', '{{SENT_SOURCE_STATUS}}', '{{SENT_STATUS}}',
                                       '{{SENT_MATCH_HITS}}', '{{SENT_MATCH_RATE}}'})
        self.assertTrue(all(tokens.values()), '占位符默认值不得为空')

    def test_inject_sentiment_replaces_markers(self):
        tpl = ('<!-- SENTIMENT_LIST:BEGIN -->\n<p>舆情因子节点待生成</p>\n'
               '<!-- SENTIMENT_LIST:END -->')
        html = self.build_site.inject_sentiment(
            tpl, self.build_site.build_sentiment_html(json.load(open(self.sent, encoding='utf-8'))))
        self.assertNotIn('舆情因子节点待生成', html)
        self.assertIn('宁德时代', html)
        self.assertNotIn('{{SENT_', html)
        empty = self.build_site.inject_sentiment(tpl, self.build_site.build_sentiment_html({}))
        self.assertTrue(empty and '舆情' in empty, '缺数据时也必须给出降级说明')

    def test_check_cli_passes_on_repo_template(self):
        p = subprocess.run([sys.executable, os.path.join(ROOT, 'build_site.py'), '--check'],
                           capture_output=True, text=True, cwd=ROOT, timeout=180)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn('舆情因子节点待生成', p.stdout)

    def test_wechat_sentiment_loader_degrades(self):
        old = os.environ.get('SENTIMENT_DATA')
        try:
            os.environ['SENTIMENT_DATA'] = os.path.join(self.tmp, 'not-exist.json')
            self.assertEqual(self.wechat.load_sentiment_data(), {})
            os.environ['SENTIMENT_DATA'] = os.path.join(self.tmp, 'broken.json')
            with open(os.environ['SENTIMENT_DATA'], 'w', encoding='utf-8') as f:
                f.write('{oops')
            self.assertEqual(self.wechat.load_sentiment_data(), {})
        finally:
            if old is None:
                os.environ.pop('SENTIMENT_DATA', None)
            else:
                os.environ['SENTIMENT_DATA'] = old

    def test_wechat_render_contains_03b(self):
        html, _ts, _ts_full = self.wechat.build_single_wechat_html()
        self.assertIn('03B /', html)
        self.assertIn('舆情', html)
        self.assertLess(len(html), 95000, '微信单页需保持在安全线内')
        self.assertEqual(html.count('03B / 舆情'), 1, '03B 节点不得重复')


class TestSourceAnonymity(unittest.TestCase):
    """新功能：舆情因子对外输出「不显示数据来源」，且采集结果按日报标的匹配展示。"""

    BLACKLIST = ('聚宽', '米筐', '掘金', '优矿', 'Tushare', '东财', '金十', '数库', 'Chinascope',
                 'JoinQuant', 'RiceQuant', 'Myquant', 'Uqer', '千股千评', 'rqdatac', 'jqdatasdk',
                 'eastmoney', 'jin10', 'RQ_SDK', 'RQ_HTTP', 'UQER_HTTP', 'EM_COMMENT', 'EM_NEWS',
                 'JQ_SDK', 'JQ_HTTP', 'GM_SDK', 'CHINASCOPE', 'JIN10_WEIBO', 'TUSHARE_NEWS',
                 'UQER_TOKEN', 'TUSHARE_TOKEN', '接入评测', '9 阶段', '证券时报网')

    @classmethod
    def setUpClass(cls):
        cls.build_site = _load_module('build_site_anon', 'build_site.py')
        cls.wechat = _load_module('wechat_push_anon', os.path.join('tools', 'wechat_push.py'))
        cls.tmp = tempfile.mkdtemp(prefix='sent-anon-')
        cls.sent = os.path.join(cls.tmp, 'sentiment_data.json')
        # 故意把来源痕迹塞满每个字段：平台名 / 接口 ID / 域名 / 凭据与依赖报错 / 媒体名
        data = {
            'mode': 'mock', 'fetch_date': '2026-09-16',
            'mode_note': '离线回放：米筐 RQ_SDK 与 优矿 Uqer fixtures',
            'market': {'sent_temp': 55.0, 'label': '中性', 'net_senti': 0.1, 'neg_share': 20.0,
                       'pos_share': 50.0, 'news_count': 6, 'heat_z': 0.3, 'risk_score': 30.0,
                       'platform_native': 2, 'self_built': 4,
                       'events': [{'title': '东财快讯：某公司被立案调查', 'terms': ['立案'],
                                   'risk_score': 30.0, 'published_at': '2026-09-15 09:12'}],
                       'top_negative': [{'title': '某银行被罚 79 万元', 'source': '证券时报网',
                                         'sentiment': -0.55, 'published_at': '2026-09-15 08:30',
                                         'hits': {'pos': [], 'neg': ['罚款'], 'risk': ['罚款']}}],
                       'top_positive': [{'title': '多家公司宣布回购增持', 'source': '金十数据',
                                         'sentiment': 0.75, 'published_at': '2026-09-15 16:40',
                                         'hits': {'pos': ['回购'], 'neg': [], 'risk': []}}]},
            'stocks': [{'symbol': '300750', 'name': '宁德时代', 'heat': 98712.0, 'heat_z': 1.3,
                        'net_senti': 0.4, 'news_count': 2, 'risk_score': 0.0,
                        'as_of': '2026-09-15'}],
            'sources': [
                {'id': 'RQ_SDK', 'platform': '米筐 RiceQuant', 'name': 'news.get_stock_news',
                 'ok': False, 'news': 0, 'series': 0, 'latest_date': '',
                 'error': '依赖缺失 rqdatac：pip install rqdatac（私有源）', 'note': ''},
                {'id': 'UQER_HTTP', 'platform': '优矿 Uqer（通联数据）', 'name': 'NewsSentimentIndexGet',
                 'ok': False, 'news': 0, 'series': 0, 'latest_date': '',
                 'error': '缺少 UQER_TOKEN', 'note': ''},
                {'id': 'EM_COMMENT', 'platform': '东方财富（免费公开）', 'name': '千股千评关注指数',
                 'ok': True, 'news': 0, 'series': 9, 'latest_date': '2026-09-16',
                 'error': '', 'note': '热度类因子，无极性', 'native_sentiment': 0}],
            'series': [{'date': '2026-09-15', 'value': 1.02, 'name': '市场情绪指数',
                        'source': 'CHINASCOPE', 'platform': '数库 Chinascope',
                        'note': '数库市场情绪指数，基期 = 1.0'}],
            'api_eval': {'generated_at': '2026-09-16 04:00:00 UTC', 'mode': 'mock',
                         'ranking': [{'id': 'RQ_SDK', 'platform': '米筐 RiceQuant',
                                      'name': 'news.get_stock_news', 'score': 67,
                                      'verdict': 'READY_WITH_LICENCE',
                                      'verdict_label': '可接入（需开权限/付费）'}],
                         'conclusion': ['米筐：唯一"给到即入模"的舆情因子']},
            'matches': smatch.build_matches([
                {'title': '恒指低开低走收跌 2.22%，南向资金仍净流入港股',
                 'published_at': '2026-09-16 08:00:00'},
                {'title': '现货黄金刷新历史高位，避险资金涌入贵金属',
                 'published_at': '2026-09-16 07:30:00', 'sentiment': 0.8},
                {'title': '美联储 9 月加息概率下降，CPI 同比回落',
                 'published_at': '2026-09-16 06:00:00'},
                {'title': '某公司公布季度报表', 'published_at': '2026-09-16 05:00:00'}],
                keywords_used=smatch.search_keywords()),
            'summary': {'total': 11, 'ok': 9, 'failed': ['RQ_SDK', 'UQER_HTTP']},
        }
        with open(cls.sent, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)

    def _web(self):
        with open(self.sent, encoding='utf-8') as f:
            return self.build_site.build_sentiment_html(json.load(f))

    def _push_html(self):
        with mock.patch.dict(os.environ, {'SENTIMENT_DATA': self.sent}), capture_stdout():
            html, _ts, _ts_full = self.wechat.build_single_wechat_html()
        return html

    @staticmethod
    def _sentiment_section(html):
        # Check only 03B, ending at the next heading (04), not at 07: section
        # 05 intentionally names RSS sources such as 金十 and eastmoney.
        # Match heading text immediately after '>' so quoted section references
        # elsewhere in community/news text do not select an unrelated slice.
        i = html.index('>03B / 舆情')
        j = html.index('>04 / AI 预测', i)
        return html[i + 1:j]

    def _push(self):
        return self._sentiment_section(self._push_html())

    def test_web_03b_hides_every_source_trace(self):
        html = self._web()
        for bad in self.BLACKLIST:
            self.assertNotIn(bad, html, f'网页 03B 泄漏来源: {bad}')
        self.assertIn('量化平台', html, '脱敏后应保留中性占位')

    def test_push_03b_hides_every_source_trace(self):
        html = self._push()
        for bad in self.BLACKLIST:
            self.assertNotIn(bad, html, f'微信推送 03B 泄漏来源: {bad}')

    def test_push_03b_scope_with_populated_rss_snapshot(self):
        """CI 有 RSS 产物时，05 栏的公开来源名不属于 03B 脱敏检查范围。"""
        with tempfile.TemporaryDirectory() as tmp:
            rss_path = os.path.join(tmp, 'rssfeeds_data.json')
            with open(rss_path, 'w', encoding='utf-8') as f:
                json.dump(self.wechat.rss_mod.build(mock=True, quiet=True), f, ensure_ascii=False)
            missing = os.path.join(tmp, 'missing.json')
            env = {key: missing for key in ('MARKET_DATA', 'COMMUNITY_DATA',
                                            'MACRO_DATA', 'FORECAST_HISTORY')}
            env['RSSFEEDS_DATA'] = rss_path
            with mock.patch.dict(os.environ, env):
                full_html = self._push_html()
        # The RSS block must actually render its sources, not be hidden or
        # redacted just to make the sentiment anonymity assertion pass.
        self.assertIn('05 / 全球 49 大财经 RSS 情报源', full_html)
        self.assertIn('金十数据快讯', full_html)
        html = self._sentiment_section(full_html)
        self.assertIn('03B / 舆情', html)
        self.assertIn('市场舆情因子读数', html)
        self.assertIn('代表新闻', html)
        self.assertNotIn('04 / AI 预测', html)
        self.assertNotIn('05 / 全球', html)
        for bad in self.BLACKLIST:
            self.assertNotIn(bad, html, f'微信推送 03B 泄漏来源: {bad}')

    def test_eval_matrix_hidden_by_default(self):
        self.assertNotIn('接入评测', self._web())
        self.assertNotIn('接入评测', self._push())

    def test_matched_targets_are_displayed(self):
        web = self._web()
        self.assertIn('标的匹配', web, '网页应渲染「舆情因子 × 日报标的匹配」表')
        for name in ('恒生指数', '现货黄金', '美联储与美元流动性'):
            self.assertIn(name, web, f'匹配表应展示 {name}')
        self.assertIn('匹配率', web)
        push = self._push()
        self.assertIn('🎯', push, '微信推送应逐条展示匹配到的标的')
        self.assertIn('命中', push)
        self.assertIn('代表新闻', push)
        self.assertIn('恒生指数', push)

    def test_env_flags_restore_internal_views(self):
        os.environ['SENTIMENT_SHOW_API_EVAL'] = '1'
        os.environ['SENTIMENT_SHOW_SOURCE'] = '1'
        os.environ['SENTIMENT_DATA'] = self.sent
        try:
            web = self._web()
            self.assertIn('接入评测', web, '开开关后应恢复评测矩阵（内部核对用）')
            self.assertIn('逐源取数明细', web)
            self.assertIn('RQ_SDK', web)
            with capture_stdout():
                push_html, _t, _tf = self.wechat.build_single_wechat_html()
            self.assertIn('9 阶段实测', push_html)
        finally:
            os.environ.pop('SENTIMENT_SHOW_API_EVAL', None)
            os.environ.pop('SENTIMENT_SHOW_SOURCE', None)
            os.environ.pop('SENTIMENT_DATA', None)

    def test_redact_masks_all_known_traces(self):
        dirty = ('米筐 RiceQuant RQ_SDK news.get_stock_news、优矿 Uqer sentimentIndex、聚宽 JQ_HTTP、'
                 '掘金 gm.api、Tushare Pro、东财千股千评、金十微博人气、数库 Chinascope，'
                 '端点 datacenter-web.eastmoney.com，缺少 UQER_TOKEN，依赖缺失 rqdatac')
        clean = smatch.redact(dirty)
        for bad in self.BLACKLIST:
            if bad in ('接入评测', '9 阶段'):
                continue
            self.assertNotIn(bad, clean, f'脱敏残留: {bad} → {clean}')

    def test_status_line_and_summary_are_anonymous(self):
        with open(self.sent, encoding='utf-8') as f:
            data = json.load(f)
        line = smatch.status_line(data)
        self.assertIn('9/11', line)
        self.assertIn('2 个接口自动降级', line)
        for bad in ('RQ_SDK', 'UQER_HTTP', '米筐', '优矿'):
            self.assertNotIn(bad, line)
        c = smatch.collection_summary(data)
        self.assertEqual((c['ok'], c['total'], c['failed_n']), (9, 11, 2))


class TestProbe(unittest.TestCase):
    """接入实测探针：mock 跑通并产出打分矩阵与 Markdown 报告。"""

    def test_mock_probe_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            j, md = os.path.join(d, 'p.json'), os.path.join(d, 'p.md')
            p = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'probe_sentiment_apis.py'),
                                '--mock', '--json', j, '--md', md, '--repeat', '1'],
                               capture_output=True, text=True, cwd=ROOT, timeout=600)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            report = json.load(open(j, encoding='utf-8'))
            self.assertEqual(report['meta']['mode'], 'mock')
            self.assertEqual(len(report['matrix']), len(reg.SOURCES))
            self.assertTrue(report['ranking'], '应产出评分排名')
            for it in report['matrix']:
                self.assertIn(it['verdict'], set(reg.VERDICT_LABEL), it['verdict'])
                self.assertLessEqual(it['score_total'], 100)
                self.assertGreaterEqual(it['score_total'], 0)
            gm = next(x for x in report['matrix'] if x['id'] == 'GM_SDK')
            self.assertEqual(gm['verdict'], 'NOT_SUPPORTED', '掘金必须被判为平台能力缺失')
            text = open(md, encoding='utf-8').read()
            self.assertIn('舆情', text)
            self.assertIn('掘金', text)
            self.assertNotIn('{', text.split('#')[0])       # 顶部不是裸 JSON
            self.assertIsNone(re.search(r'None 分|\bNone\b\s*分', text), '报告不得漏出 None')

    def test_repo_report_and_docs_are_fresh(self):
        doc = os.path.join(ROOT, 'docs', 'sentiment-api-eval.md')
        if not os.path.exists(doc):
            self.skipTest('docs/sentiment-api-eval.md 尚未生成（跑 tools/probe_sentiment_apis.py --mock）')
        text = open(doc, encoding='utf-8').read()
        for plat in ('聚宽', '米筐', '掘金', '优矿'):
            self.assertIn(plat, text, f'评测文档缺 {plat} 口径')
        self.assertIn('自动生成', text)
        for sec in ('结论速览', '能力矩阵', '评分明细', '逐源实测明细'):
            self.assertIn(sec, text, f'评测文档缺章节 {sec}')
        self.assertNotIn('None 分', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
