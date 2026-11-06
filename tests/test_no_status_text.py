#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression suite ensuring Web and WeChat outputs omit all status/methodology/unavailable text."""
import contextlib
import html as html_mod
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_site as bs          # noqa: E402
import community_data as cd      # noqa: E402
import macro_data as md          # noqa: E402
import rss_feeds as rf           # noqa: E402
import sentiment_factors as sf   # noqa: E402
import wechat_push as wp         # noqa: E402

BANNED_PHRASES = (
    '今日未获取',
    '未获取',
    '不回填',
    '不编柱',
    '不编故事',
    '本栏不预测',
    '时序不成立',
    '抓取核验',
    '本次读取分析',
    '框架统计',
    '口径：',
    '时效窗口',
    '不显示数据来源',
    '每次构建现抓',
    '以每次重建后的最新一版为准',
    '三路输入信号',
    '见网页 04 节',
    '手动触发微信推送',
    '一对一专属推送',
    'TELEMETRY LIVE',
    'OCTO // COMMAND CENTER',
    'DATA SYNC',
    'INTEL CHANNELS',
    'diverging bar',
    'stacked bar',
    '字符配图',
    'AI 解读未生成',
    '规则判断：',
    '提取信息：',
    '离线回放样本',
    '离线回放摘要',
    '采样评论',
    '评论点赞',
    'DeepSeek 检索兜底',
    'RSSHub 通道',
    '匹配率',
)


def _visible_text(raw_html):
    no_scripts = re.sub(r'<script\b[^>]*>.*?</script>', ' ', raw_html, flags=re.I | re.S)
    no_styles = re.sub(r'<style\b[^>]*>.*?</style>', ' ', no_scripts, flags=re.I | re.S)
    no_comments = re.sub(r'<!--.*?-->', ' ', no_styles, flags=re.S)
    text = html_mod.unescape(re.sub(r'<[^>]+>', ' ', no_comments))
    return re.sub(r'\s+', ' ', text).strip()


class TestNoStatusText(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.now = datetime.now(timezone.utc)
        self.today = self.now.strftime('%Y-%m-%d')

    def _populated_files(self):
        t = self._tmp.name
        market_path = os.path.join(t, 'market_data.json')
        comm_path = os.path.join(t, 'community_data.json')
        sent_path = os.path.join(t, 'sentiment_data.json')
        macro_path = os.path.join(t, 'macro_data.json')
        rss_path = os.path.join(t, 'rssfeeds_data.json')
        hist_path = os.path.join(t, 'forecast_history.json')

        quotes = {
            'VIX': {'name': 'VIX 恐慌指数', 'last': 19.8, 'prev_close': 18.5,
                    'chg': 1.3, 'pct': 7.03, 'as_of': self.today, 'decimals': 2},
            'HSI': {'name': '恒生指数', 'last': 25800.0, 'prev_close': 25500.0,
                    'chg': 300.0, 'pct': 1.18, 'as_of': self.today, 'decimals': 2},
            'HSTECH': {'name': '恒生科技指数', 'last': 5260.0, 'prev_close': 5180.0,
                       'chg': 80.0, 'pct': 1.54, 'as_of': self.today, 'decimals': 2},
            'HSCE': {'name': '恒生中国企业指数', 'last': 9120.0, 'prev_close': 9040.0,
                     'chg': 80.0, 'pct': 0.88, 'as_of': self.today, 'decimals': 2},
            'SPX': {'name': '标普 500', 'last': 6120.0, 'prev_close': 6090.0,
                    'chg': 30.0, 'pct': 0.49, 'as_of': self.today, 'decimals': 2},
            'NDQ': {'name': '纳斯达克', 'last': 21300.0, 'prev_close': 21100.0,
                    'chg': 200.0, 'pct': 0.95, 'as_of': self.today, 'decimals': 2},
            'DJI': {'name': '道琼斯', 'last': 43500.0, 'prev_close': 43400.0,
                    'chg': 100.0, 'pct': 0.23, 'as_of': self.today, 'decimals': 2},
            'GOLD': {'name': '现货黄金', 'last': 2680.0, 'prev_close': 2665.0,
                     'chg': 15.0, 'pct': 0.56, 'as_of': self.today, 'decimals': 2},
            'WTI': {'name': 'WTI 原油', 'last': 74.2, 'prev_close': 75.0,
                    'chg': -0.8, 'pct': -1.07, 'as_of': self.today, 'decimals': 2},
            'BRENT': {'name': '布伦特原油', 'last': 78.1, 'prev_close': 78.8,
                      'chg': -0.7, 'pct': -0.89, 'as_of': self.today, 'decimals': 2},
            'USDCNH': {'name': '美元/离岸人民币', 'last': 7.08, 'prev_close': 7.09,
                       'chg': -0.01, 'pct': -0.14, 'as_of': self.today, 'decimals': 4},
            'USDCNY': {'name': '美元/在岸人民币', 'last': 7.07, 'prev_close': 7.08,
                       'chg': -0.01, 'pct': -0.14, 'as_of': self.today, 'decimals': 4},
        }
        with open(market_path, 'w', encoding='utf-8') as f:
            json.dump({'fetch_date': self.today,
                       'generated_at': self.now.strftime('%Y-%m-%d %H:%M:%S UTC'),
                       'quotes': quotes,
                       'summary': {'ok': len(quotes), 'total': len(quotes), 'failed': []}}, f)

        rec_live = cd.build_live_record(
            cd.COMMUNITIES[0], '港股南向资金持续净流入，科技与高股息板块轮动走强。', self.today)
        rec_unavail = dict(cd.COMMUNITIES[1], available=False, source='unavailable',
                           verdict_class='unavailable', verdict_label='未获取',
                           quote='', verdict='未获取', quant=None,
                           meta=f'HTTP 403 · 抓取核验 {self.today}')
        with open(comm_path, 'w', encoding='utf-8') as f:
            json.dump({'fetch_date': self.today,
                       'generated_at': self.now.strftime('%Y-%m-%d %H:%M:%S UTC'),
                       'mode': 'live',
                       'communities': [rec_live, rec_unavail],
                       'summary': {'ok': 1, 'total': 2, 'failed': [rec_unavail['name']]}},
                      f, ensure_ascii=False)

        sf.run(mode='mock', out_path=sent_path, verbose=False)
        with open(macro_path, 'w', encoding='utf-8') as f:
            json.dump(md.build(mock=True, quiet=True), f, ensure_ascii=False)

        rss_data = rf.build(mock=True, quiet=True)
        # Mark one RSS source unavailable to verify it is silently hidden
        first_group = next(iter(rss_data['groups'].values()))
        dropped_rss_name = first_group['sources'][-1]['name']
        first_group['sources'][-1]['ok'] = False
        first_group['sources'][-1]['items_list'] = []
        first_group['sources'][-1]['items'] = 0
        with open(rss_path, 'w', encoding='utf-8') as f:
            json.dump(rss_data, f, ensure_ascii=False)

        return {
            'market': market_path, 'community': comm_path, 'sentiment': sent_path,
            'macro': macro_path, 'rss': rss_path, 'history': hist_path,
            'live_comm_name': rec_live['name'],
            'unavail_comm_name': rec_unavail['name'],
            'dropped_rss_name': dropped_rss_name,
        }

    def test_populated_web_and_wechat_have_no_status_text_and_keep_substance(self):
        paths = self._populated_files()
        market_p = paths['market']
        comm_p = paths['community']
        sent_p = paths['sentiment']
        macro_p = paths['macro']
        rss_p = paths['rss']
        out_html = os.path.join(self._tmp.name, 'out_populated.html')
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, 'build_site.py'),
             '--template', os.path.join(REPO_ROOT, 'report.html'),
             '--out', out_html,
             '--data', market_p,
             '--community', comm_p,
             '--sentiment', sent_p,
             '--macro', macro_p, '--macro-no-fetch',
             '--rss', rss_p, '--rss-no-fetch',
             '--forecast-no-history'],
            capture_output=True, text=True, timeout=180, cwd=self._tmp.name,
            env={k: v for k, v in os.environ.items()
                 if k not in ('SENTIMENT_SHOW_API_EVAL', 'SENTIMENT_SHOW_SOURCE')})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(out_html, encoding='utf-8') as f:
            web_raw = f.read()
        web_text = _visible_text(web_raw)

        env = {
            'MARKET_DATA': paths['market'],
            'COMMUNITY_DATA': paths['community'],
            'SENTIMENT_DATA': paths['sentiment'],
            'MACRO_DATA': paths['macro'],
            'RSSFEEDS_DATA': paths['rss'],
            'FORECAST_HISTORY': paths['history'],
            'MACRO_AUTO_FETCH': '0',
            'RSSFEEDS_AUTO_FETCH': '0',
        }
        saved = {k: os.environ.get(k) for k in tuple(env) + ('SENTIMENT_SHOW_API_EVAL', 'SENTIMENT_SHOW_SOURCE')}
        os.environ.update(env)
        os.environ.pop('SENTIMENT_SHOW_API_EVAL', None)
        os.environ.pop('SENTIMENT_SHOW_SOURCE', None)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                wx_raw, _ts, _tsf = wp.build_single_wechat_html(now=self.now)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        wx_text = _visible_text(wx_raw)

        for surface, text in (('Web', web_text), ('WeChat', wx_text)):
            for phrase in BANNED_PHRASES:
                self.assertNotIn(phrase, text, f'{surface} 不应输出状态/说明文字: {phrase}')
            # 实质内容、真实日期与风险提示必须保留
            self.assertIn('25,800.00', text)
            self.assertIn('19.80', text)
            self.assertIn(self.today, text)
            self.assertIn('港股南向资金持续净流入', text)
            self.assertIn(paths['live_comm_name'], text)
            self.assertNotIn(paths['unavail_comm_name'], text, f'{surface} 未获取社区源应隐藏')
            self.assertNotIn(paths['dropped_rss_name'], text, f'{surface} 未获取 RSS 源应隐藏')
            self.assertIn('风险提示', text)
            self.assertIn('投资建议', text)

    def test_empty_web_and_wechat_hide_unavailable_without_status_text(self):
        missing = os.path.join(self._tmp.name, 'missing.json')
        out_html = os.path.join(self._tmp.name, 'out_empty.html')
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, 'build_site.py'),
             '--template', os.path.join(REPO_ROOT, 'report.html'),
             '--out', out_html,
             '--data', missing,
             '--community', missing,
             '--sentiment', missing,
             '--macro', missing, '--macro-no-fetch',
             '--rss', missing, '--rss-no-fetch',
             '--forecast-no-history'],
            capture_output=True, text=True, timeout=180, cwd=self._tmp.name)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(out_html, encoding='utf-8') as f:
            web_text = _visible_text(f.read())

        env = {
            'MARKET_DATA': missing,
            'COMMUNITY_DATA': missing,
            'SENTIMENT_DATA': missing,
            'MACRO_DATA': missing,
            'RSSFEEDS_DATA': missing,
            'FORECAST_HISTORY': missing,
            'MACRO_AUTO_FETCH': '0',
            'RSSFEEDS_AUTO_FETCH': '0',
        }
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                wx_raw, _ts, _tsf = wp.build_single_wechat_html(now=self.now)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        wx_text = _visible_text(wx_raw)

        for surface, text in (('Web', web_text), ('WeChat', wx_text)):
            for phrase in BANNED_PHRASES:
                self.assertNotIn(phrase, text, f'{surface} 空数据态不应输出状态/降级文字: {phrase}')
            self.assertIn('风险提示', text)
            self.assertIn('投资建议', text)


if __name__ == '__main__':
    unittest.main()
