#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 49 大社区实盘摘录与严格真实口径 (community_data.py)

对齐 `02 / 宏观快讯` 的反幻觉原则（方案 A · 严格真实口径）：
  1. **只展示实盘抓到的公开讨论片段**：当 `community_spider.py` 成功抓取页面且片段命中财经/市场主题词时，
     `source="live"`、`available=True`，正文仅保留清洗后的真实公开摘录，绝不拼接写死的 K 线剧本、
     虚构牛熊街货比、美债收益率或历史点位；
  2. **多空倾向与核心量化指标 100% 由真实文本计算**：复用 `sentiment_nlp.py` 金融词库（并补齐英/繁/日/韩/德
     常见多空极性词）对实抓片段现算情感净值、`verdict_class`（偏多 / 偏空 / 中性 / 多空分歧）、事件分类、
     主题词相关性与当次批内去重独立度；彻底移除预设平台多空标签与 MD5 伪随机打分；
  3. **未抓到即诚实标注「未获取」**：站点风控（HTTP 403/429）、登录墙、纯前端空壳或未命中财经主题词时，
     一律输出 `available=False`、`verdict_class="unavailable"`、`verdict_label="未获取"`、`quant=None`，
     不计入多空家数统计，也不向 `01 全景扫描` / `04 AI 预测` 注入虚假情绪覆盖度；
  4. **拒绝虚报样本量与篡改抓取日期**：不再虚报多帖综合样本量或私密研报数量，实抓源如实标注单页公开摘录字数，
     未获取源如实标注原因与核验日期。

用法:
  python3 market_data.py && python3 community_data.py         # 联网抓取 49 源 → community_data.json
  python3 community_data.py --limit 5 --timeout 8             # 只抓前 5 源（联调）
  python3 community_data.py --demo                            # 本地演示模式（显式标注演示样本，非实盘）
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import community_spider as spider_mod
import scrapling_core as sc
import sentiment_nlp as snlp

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
MARKET_DATA_DEFAULT = os.path.join(REPO_ROOT, 'market_data.json')
ADAPTIVE_DB_DEFAULT = os.path.join(REPO_ROOT, '.scrapling', 'community_adaptive.db')

MINUS = '\u2212'

VALID_VERDICT_CLASSES = ('bull', 'bear', 'neutral', 'mixed')
UNAVAILABLE_VERDICT_CLASS = 'unavailable'
UNAVAILABLE_VERDICT_LABEL = '未获取'

VERDICT_LABELS = {
    'bull': '偏多',
    'bear': '偏空',
    'neutral': '中性',
    'mixed': '多空分歧',
    'unavailable': UNAVAILABLE_VERDICT_LABEL,
}

# 各源优先 RSS 地址（空表示该源无公开 RSS，直接回退到网站/APP 公开信息流）。
# 仅列出有明确 RSS 订阅地址的源；其余 49 源中未列出者统一视为「无 RSS」，
# 采集时优先尝试 RSS，不可用再回退到公开网页摘录与评论策略。
RSS_FEEDS = {
    'REDDIT': 'https://www.reddit.com/r/ChinaStocks/.rss',
    'RINVESTING': 'https://www.reddit.com/r/investing/.rss',
    'ROPTIONS': 'https://www.reddit.com/r/options/.rss',
    'RCRYPTO': 'https://www.reddit.com/r/CryptoCurrency/.rss',
    'BOGLEHEADS': 'https://www.bogleheads.org/forum/rss.php?f=10',
    'DISCUSS': 'https://www.discuss.com.hk/rss.php?fid=115',
    'SEEKINGALPHA': 'https://seekingalpha.com/market-news.rss',
}

# 49 大社区目录（保留完整 49 源编号与多语种/多类型覆盖；修正错配 URL，移除预设多空立场与「10 条」虚报口径）
COMMUNITIES = [
    {"id": "01", "key": "FUTU", "name": "富途牛牛社区", "icon": "🐮", "ctype": "中文行情社区",
     "url": "https://www.futunn.com/hk"},
    {"id": "02", "key": "XUEQIU", "name": "雪球网", "icon": "❄️", "ctype": "中文投资者社区",
     "url": "https://xueqiu.com/S/HKHSI"},
    {"id": "03", "key": "LAOHU", "name": "老虎社区", "icon": "🐯", "ctype": "中文跨境社区",
     "url": "https://www.laohu8.com"},
    {"id": "04", "key": "EASTMONEY", "name": "东方财富港股股吧", "icon": "💰", "ctype": "中文股吧论坛",
     "url": "https://guba.eastmoney.com"},
    {"id": "05", "key": "ZHITONG", "name": "智通财经互动区", "icon": "📈", "ctype": "中文资讯互动",
     "url": "https://www.zhitongcaijing.com"},
    {"id": "06", "key": "WALLSTREETCN", "name": "华尔街见闻社区", "icon": "🌐", "ctype": "中文宏观社区",
     "url": "https://wallstreetcn.com"},
    {"id": "07", "key": "DISCUSS", "name": "香港讨论区财经版", "icon": "🇭🇰", "ctype": "粤语本地论坛",
     "url": "https://www.discuss.com.hk/forumdisplay.php?fid=115"},
    {"id": "08", "key": "LIHKG", "name": "LIHKG 连登财经台", "icon": "🔥", "ctype": "粤语本地论坛",
     "url": "https://lihkg.com/category/15"},
    {"id": "09", "key": "JIUQUAN", "name": "韭圈儿 / 红岸社区", "icon": "🥦", "ctype": "中文机构持仓社区",
     "url": "https://www.jiucaishuo.com"},
    {"id": "10", "key": "ANTFORTUNE", "name": "蚂蚁财富港股社区", "icon": "🐜", "ctype": "中文基民社区",
     "url": "https://www.antfortune.com"},
    {"id": "11", "key": "REDDIT", "name": "Reddit (r/ChinaStocks)", "icon": "👾", "ctype": "英文论坛",
     "url": "https://www.reddit.com/r/ChinaStocks/"},
    {"id": "12", "key": "TRADINGVIEW", "name": "TradingView 香港板块", "icon": "📊", "ctype": "英文图表社区",
     "url": "https://www.tradingview.com/markets/hong-kong/"},
    {"id": "13", "key": "VIC", "name": "Value Investors Club", "icon": "💎", "ctype": "英文研究社区",
     "url": "https://www.valueinvestorsclub.com"},
    {"id": "14", "key": "FINTWIT", "name": "Twitter / X (FinTwit)", "icon": "🐦", "ctype": "英文社交平台",
     "url": "https://x.com/search?q=HSI%20Hong%20Kong"},
    {"id": "15", "key": "ZHIHU", "name": "知乎 · 投资理财话题", "icon": "🧠", "ctype": "中文问答社区",
     "url": "https://www.zhihu.com/topic/19554223/hot"},
    {"id": "16", "key": "WEIBO", "name": "微博财经超话", "icon": "📣", "ctype": "中文社交媒体",
     "url": "https://s.weibo.com/weibo?q=%23%E6%B8%AF%E8%82%A1%23"},
    {"id": "17", "key": "TIEBA", "name": "百度贴吧 · 股票吧", "icon": "🫧", "ctype": "中文论坛",
     "url": "https://tieba.baidu.com/f?kw=%E8%82%A1%E7%A5%A8"},
    {"id": "18", "key": "TAOGUBA", "name": "淘股吧", "icon": "🎯", "ctype": "中文短线交易论坛",
     "url": "https://www.taoguba.com.cn"},
    {"id": "19", "key": "THS", "name": "同花顺社区", "icon": "📉", "ctype": "中文行情社区",
     "url": "https://t.10jqka.com.cn"},
    {"id": "20", "key": "GELONGHUI", "name": "格隆汇 · 港股圈", "icon": "🔎", "ctype": "中文研究社区",
     "url": "https://www.gelonghui.com"},
    {"id": "21", "key": "CLS", "name": "财联社 · 电报", "icon": "⚡", "ctype": "中文快讯社区",
     "url": "https://www.cls.cn/telegraph"},
    {"id": "22", "key": "YICAI", "name": "第一财经 · 社区", "icon": "📺", "ctype": "中文财经媒体",
     "url": "https://www.yicai.com"},
    {"id": "23", "key": "BILIBILI", "name": "Bilibili · 财经区", "icon": "🎬", "ctype": "中文视频社区",
     "url": "https://www.bilibili.com/v/finance"},
    {"id": "24", "key": "PTT", "name": "PTT Stock 板", "icon": "🗣️", "ctype": "繁体中文论坛",
     "url": "https://www.ptt.cc/bbs/Stock/index.html"},
    {"id": "25", "key": "STOCKTWITS", "name": "StockTwits", "icon": "💬", "ctype": "英文交易者社交",
     "url": "https://stocktwits.com/symbol/HSI"},
    {"id": "26", "key": "SEEKINGALPHA", "name": "Seeking Alpha", "icon": "🧾", "ctype": "英文研究社区",
     "url": "https://seekingalpha.com/market-news"},
    {"id": "27", "key": "BOGLEHEADS", "name": "Bogleheads Forum", "icon": "🧭", "ctype": "英文长期投资论坛",
     "url": "https://www.bogleheads.org/forum/index.php"},
    {"id": "28", "key": "RINVESTING", "name": "Reddit (r/investing)", "icon": "🧩", "ctype": "英文论坛",
     "url": "https://www.reddit.com/r/investing/"},
    {"id": "29", "key": "WSO", "name": "Wall Street Oasis", "icon": "🏦", "ctype": "英文机构社区",
     "url": "https://www.wallstreetoasis.com/forum"},
    {"id": "30", "key": "INVESTING", "name": "Investing.com 讨论区", "icon": "🌍", "ctype": "多语言全球论坛",
     "url": "https://www.investing.com/indices/hang-seng-index-commentary"},
    {"id": "31", "key": "YAHOO", "name": "Yahoo Finance · 社区", "icon": "📰", "ctype": "英文行情社区",
     "url": "https://finance.yahoo.com/quote/%5EHSI/community"},
    {"id": "32", "key": "SUBSTACK", "name": "Substack · 财经通讯", "icon": "✉️", "ctype": "英文订阅研究",
     "url": "https://substack.com/browse/finance"},
    {"id": "33", "key": "ROPTIONS", "name": "Reddit (r/options)", "icon": "⚙️", "ctype": "英文衍生品论坛",
     "url": "https://www.reddit.com/r/options/"},
    {"id": "34", "key": "FTALPHA", "name": "FT Alphaville", "icon": "🗞️", "ctype": "英文财经媒体博客",
     "url": "https://www.ft.com/alphaville"},
    {"id": "35", "key": "JISILU", "name": "集思录 · 低风险投资社区", "icon": "🧊", "ctype": "中文低风险投资社区",
     "url": "https://www.jisilu.cn"},
    {"id": "36", "key": "XIAOHONGSHU", "name": "小红书 · 理财笔记", "icon": "📕", "ctype": "中文种草社交社区",
     "url": "https://www.xiaohongshu.com/search_result?keyword=%E6%B8%AF%E8%82%A1"},
    {"id": "37", "key": "DOUYIN", "name": "抖音 · 财经短视频", "icon": "🎵", "ctype": "中文短视频社区",
     "url": "https://www.douyin.com/search/%E6%B8%AF%E8%82%A1"},
    {"id": "38", "key": "KAIPANLA", "name": "开盘啦 · 情绪复盘", "icon": "🚀", "ctype": "中文短线情绪社区",
     "url": "https://www.kaipanla.com"},
    {"id": "39", "key": "LIXIANG", "name": "理想论坛 · 股票实战", "icon": "🧭", "ctype": "中文实战论坛",
     "url": "https://www.55188.com"},
    {"id": "40", "key": "QUANTNET", "name": "QuantNet · 量化社区", "icon": "📐", "ctype": "英文量化社区",
     "url": "https://quantnet.com"},
    {"id": "41", "key": "ELITETRADER", "name": "Elite Trader", "icon": "🎓", "ctype": "英文交易员论坛",
     "url": "https://www.elitetrader.com/et/"},
    {"id": "42", "key": "FOREXFACTORY", "name": "Forex Factory", "icon": "💱", "ctype": "英文外汇社区",
     "url": "https://www.forexfactory.com/forum"},
    {"id": "43", "key": "RCRYPTO", "name": "Reddit (r/CryptoCurrency)", "icon": "🪙", "ctype": "数字资产论坛",
     "url": "https://www.reddit.com/r/CryptoCurrency/"},
    {"id": "44", "key": "MORNINGSTAR", "name": "Morningstar · 社区", "icon": "🌅", "ctype": "英文基金研究社区",
     "url": "https://community.morningstar.com"},
    {"id": "45", "key": "MARKETWATCH", "name": "MarketWatch · 社区", "icon": "🗽", "ctype": "英文财经媒体社区",
     "url": "https://www.marketwatch.com/latest-news"},
    {"id": "46", "key": "YAHOOJP", "name": "日本 Yahoo! 财经掲示板", "icon": "🗾", "ctype": "日文社区",
     "url": "https://finance.yahoo.co.jp/search/?query=%E9%A6%99%E6%B8%AF%E6%A0%AA"},
    {"id": "47", "key": "NAVER", "name": "네이버 금융 종토방", "icon": "🇰🇷", "ctype": "韩文社区",
     "url": "https://finance.naver.com/world/sise.naver?symbol=HSI@HSI"},
    {"id": "48", "key": "WALLSTREETDE", "name": "Wallstreet-Online · 德语社区", "icon": "🇩🇪", "ctype": "德文社区",
     "url": "https://www.wallstreet-online.de/community/forum"},
    {"id": "49", "key": "AASTOCKS", "name": "阿斯达克财经 · 讨论区", "icon": "🇭🇰", "ctype": "香港本地财经社区",
     "url": "http://www.aastocks.com/tc/stocks/comment/latest.aspx"},
]

# 多语种极性补充词（与 sentiment_nlp 中文金融词库联合计分，使英文/繁体/日韩德公开片段同样按真实文本定性）
MULTILINGUAL_POS_TERMS = {
    '利好': 1.0, '反弹': 0.9, '回暖': 0.9, '走强': 1.0, '看多': 1.1, '做多': 1.0,
    '净流入': 1.0, '净买入': 1.1, '突破': 1.0, '修复': 0.8, '托底': 0.8, '领涨': 1.1,
    'bullish': 1.2, 'rally': 1.1, 'gain': 0.8, 'gains': 0.8, 'surge': 1.3, 'rebound': 1.0,
    'outperform': 1.1, 'upgrade': 1.1, 'buyback': 1.0, 'inflow': 1.0, 'inflows': 1.0,
    'undervalued': 1.0, 'upside': 0.9, 'breakout': 1.0, 'beat': 0.9, 'recovery': 0.9,
    '上漲': 0.9, '大漲': 1.3, '走強': 1.0, '淨流入': 1.0, '淨買入': 1.1, '回暖': 0.9, '反彈': 0.9,
    '上昇': 0.9, '反発': 0.9, '買い': 0.8, '상승': 0.9, '반등': 0.9, '매수': 0.8, 'steigend': 0.9, 'rallye': 1.1,
}
MULTILINGUAL_NEG_TERMS = {
    '利空': 1.0, '跳水': 1.3, '破位': 1.1, '走弱': 1.0, '承压': 0.9, '看空': 1.1,
    '净流出': 1.0, '抛售': 1.2, '暴雷': 1.4, '恐慌': 1.2,
    'bearish': 1.2, 'selloff': 1.3, 'sell-off': 1.3, 'plunge': 1.4, 'slump': 1.2, 'drop': 0.9,
    'decline': 0.9, 'downside': 1.0, 'outflow': 1.0, 'outflows': 1.0, 'downgrade': 1.1,
    'recession': 1.3, 'tariff': 0.9, 'default': 1.4, 'weak': 0.8, 'pressure': 0.7, 'miss': 0.9,
    '下跌': 0.9, '大跌': 1.3, '走弱': 1.0, '淨流出': 1.0, '跳水': 1.3, '承壓': 0.9, '破位': 1.1,
    '下落': 0.9, '反落': 0.9, '売り': 0.8, '하락': 0.9, '약세': 0.9, '매도': 0.8, 'fallend': 0.9, 'abverkauf': 1.2,
}

# 核心港股/主线标的词（用于实算相关性得分）
CORE_HK_TERMS = (
    '恒指', '恒生', '港股', '恒科', '国企指数', '南向', '北水', '港元', '港交所', '港股通',
    '恆指', '恆生', '恒生指數', '中概', '腾讯', '騰訊', '阿里', '美团', '小米', '比亚迪', '比亞迪',
    'hang seng', 'hsi', 'hstech', 'hong kong', 'hk stocks', 'china stocks',
    '香港株', 'ハンセン', '항셍', '홍콩',
)

# 真实事件场景关键词映射（按命中数择优，未命中则如实标注「市场讨论」）
EVENT_RULES = (
    ('业绩/财报', ('业绩', '财报', '季报', '年报', '盈利', '营收', '净利润', '指引', 'earnings', 'revenue', 'guidance', 'profit')),
    ('并购/资本运作', ('并购', '收购', '重组', '私有化', '回购', '分拆', '配股', 'IPO', '招股', 'merger', 'buyback', 'acquisition', 'ipo')),
    ('宏观/政策', ('美联储', '联储', '央行', '降息', '加息', '通胀', '非农', '关税', '政策', '监管', 'fomc', 'fed', 'cpi', 'tariff', 'policy')),
    ('资金流向', ('南向', '北水', '净买入', '净流入', '净流出', '外资', '主力', '仓位', '申购', 'inflow', 'outflow', 'etf', 'fund flow')),
    ('衍生品/波动率', ('期权', '牛熊', '窝轮', '窩輪', '波动率', '期货', '对冲', '做空', 'options', 'volatility', 'vix', 'futures', 'hedging')),
    ('估值与配置', ('估值', '股息', '红利', '分红', '折价', '定投', '可转债', '套利', '配置', 'valuation', 'dividend', 'yield', 'allocation')),
)


def load_market_data(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def fmt_hsi(market):
    """格式化恒生指数当次快照；缺失时返回 None，绝不回填历史默认点位。"""
    q = ((market or {}).get('quotes') or {}).get('HSI') or {}
    last = q.get('last')
    pct = q.get('pct')
    chg = q.get('chg')
    available = (last is not None and pct is not None)
    last_str = f"{float(last):,.2f}" if last is not None else "—"
    if pct is not None:
        pv = float(pct)
        pct_str = f"{MINUS if pv < 0 else '+'}{abs(pv):.2f}%"
    else:
        pct_str = "—"
    if chg is not None:
        cv = float(chg)
        chg_str = f"{'跌' if cv < 0 else '涨'} {abs(cv):,.2f} 点"
    else:
        chg_str = "—"
    return {
        "available": available,
        "last": last_str,
        "pct": pct_str,
        "chg": chg_str,
        "as_of": q.get('as_of') or '',
        "raw_pct": float(pct) if pct is not None else None,
        "raw_last": float(last) if last is not None else None,
    }


def _char_bigrams(text: str) -> set[str]:
    cleaned = re.sub(r'\s+', '', str(text or '').lower())
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def _batch_uniqueness(snippet: str, peer_snippets) -> int | None:
    """计算当次实抓片段与同批其他实抓片段的去重独立度（0–100）；单条或无对照时返回 None。"""
    peers = [p for p in (peer_snippets or []) if p and p != snippet]
    if not peers:
        return None
    mine = _char_bigrams(snippet)
    if not mine:
        return None
    max_sim = 0.0
    for peer in peers:
        other = _char_bigrams(peer)
        if not other:
            continue
        union = len(mine | other)
        if union:
            sim = len(mine & other) / union
            if sim > max_sim:
                max_sim = sim
    return max(0, min(100, round((1.0 - max_sim) * 100)))


def _detect_event(text: str) -> dict:
    lowered = text.lower()
    best_label = '市场讨论'
    best_terms: list[str] = []
    for label, terms in EVENT_RULES:
        matched = [t for t in terms if t.lower() in lowered]
        if len(matched) > len(best_terms):
            best_label = label
            best_terms = matched
    return {
        "label": best_label,
        "matched_terms": best_terms[:5],
        "desc": "按样本关键词匹配事件场景；未命中特定事件词时归为市场讨论",
    }


def analyze_live_snippet(snippet: str, title: str = '', ctype: str = '', peer_snippets=None,
                         content_kind: str = 'page_excerpt') -> dict:
    """对样本文本执行确定性词库情感与相关性计算（零 MD5 伪随机、零写死标签）。"""
    combined = spider_mod.strip_noise(f"{title} {snippet}" if title and title not in snippet else snippet)
    nlp_res = snlp.score_text(combined)
    pos_hits = list(nlp_res.get('pos_hits') or [])
    neg_hits = list(nlp_res.get('neg_hits') or [])
    pos_extra = 0.0
    neg_extra = 0.0
    lowered = combined.lower()

    for term, w in MULTILINGUAL_POS_TERMS.items():
        if re.search(rf'(?<![a-z]){re.escape(term)}(?![a-z])', lowered) if term.isascii() else (term in combined):
            if term not in pos_hits:
                pos_hits.append(term)
            pos_extra += w
    for term, w in MULTILINGUAL_NEG_TERMS.items():
        if re.search(rf'(?<![a-z]){re.escape(term)}(?![a-z])', lowered) if term.isascii() else (term in combined):
            if term not in neg_hits:
                neg_hits.append(term)
            neg_extra += w

    base_score = float(nlp_res.get('score') or 0.0)
    if pos_extra or neg_extra:
        extra_total = pos_extra + neg_extra + 1.5
        extra_score = (pos_extra - neg_extra) / extra_total
        score = max(-1.0, min(1.0, round(base_score * 0.65 + extra_score * 0.65, 2)
                              if base_score != 0 else round(extra_score, 2)))
    else:
        score = round(base_score, 2)

    # 判定四分类立场（偏多 / 偏空 / 中性 / 多空分歧），与情感得分严格自洽
    if pos_hits and neg_hits and abs(score) < 0.35:
        vclass = 'mixed'
    elif score >= 0.15:
        vclass = 'bull'
    elif score <= -0.15:
        vclass = 'bear'
    else:
        vclass = 'neutral'
    vlabel = VERDICT_LABELS[vclass]

    senti_word = '积极' if score >= 0.15 else ('消极' if score <= -0.15 else ('多空交织' if (pos_hits and neg_hits) else '中性'))
    sign = '+' if score >= 0 else MINUS
    senti_display = f"{sign}{abs(score):.2f} ({senti_word})"

    core_hits = [kw for kw in CORE_HK_TERMS if kw.lower() in lowered]
    topic_hits = spider_mod.count_topic_hits(combined)
    rel_score = min(100, len(core_hits) * 28 + topic_hits * 14 + (10 if len(snippet) >= 40 else 0))
    rel_display = f"{rel_score}/100（核心词 {len(core_hits)} · 主题词 {topic_hits}）"

    uniq = _batch_uniqueness(snippet, peer_snippets)
    if uniq is not None:
        nov_obj = {
            "score": uniq,
            "label": "批内独立度",
            "display": f"{uniq}/100 (批内独立度 · 无首发时间链)",
            "desc": "样本不含跨站发布时间链，仅计算当次批内去重独立度，不伪造首发/转载",
        }
    else:
        nov_obj = {
            "score": None,
            "label": "未评级",
            "display": "— (无首发时间链，不伪造首发/转载)",
            "desc": "样本不含首发时间链，不伪造首发/转载评分",
        }

    event_obj = _detect_event(combined)
    quant = {
        "sentiment": {
            "score": score,
            "label": senti_word,
            "display": senti_display,
            "pos_hits": pos_hits[:6],
            "neg_hits": neg_hits[:6],
            "desc": "基于样本文本的金融词库净值（零哈希伪随机）",
        },
        "event": event_obj,
        "relevance": {
            "score": rel_score,
            "core_hits": len(core_hits),
            "topic_hits": topic_hits,
            "display": rel_display,
            "desc": "按样本命中的港股核心词与财经主题词密度实算",
        },
        "novelty": nov_obj,
    }

    evidence_parts = []
    if pos_hits:
        evidence_parts.append(f"正向词「{'、'.join(pos_hits[:3])}」")
    if neg_hits:
        evidence_parts.append(f"负向词「{'、'.join(neg_hits[:3])}」")
    sample_label = {'comment': '本条采样评论', 'thread_reply': '本条热帖回复',
                    'demo': '演示文本'}.get(content_kind, '本次公开页摘录')
    limitation = '仅用于演示，不代表实盘抓取。' if content_kind == 'demo' else '仅反映局部信息，不外推全站共识。'
    if evidence_parts:
        verdict = (f"{vlabel}（词库净值 {senti_display}，命中{'与'.join(evidence_parts)}）。"
                   f"以上分析仅基于{sample_label}（{event_obj['label']}）；{limitation}")
    else:
        verdict = (f"{vlabel}（词库净值 {senti_display}，片段命中 {topic_hits} 个财经主题词但无显著多空极性词）。"
                   f"以上分析仅基于{sample_label}；{limitation}")

    return {
        "verdict_class": vclass,
        "verdict_label": vlabel,
        "verdict": verdict,
        "quant": quant,
    }


def format_unavailable_reason(hit: dict | None) -> str:
    hit = hit or {}
    status = int(hit.get('status') or 0)
    err = str(hit.get('error') or '').strip()
    if status in (401, 403, 429, 451, 503):
        return f"站点风控或访问受限 HTTP {status}"
    if status >= 400:
        return f"站点返回 HTTP {status}"
    if err == 'robots_disallowed':
        return "robots.txt 不允许抓取"
    if err == 'no_topic_snippet':
        return "公开页未检出财经讨论正文（动态渲染空壳或登录墙）"
    if err and err != 'no_response':
        return "网络连接受限或请求超时"
    return "未抓到可核验的公开讨论片段"


def build_unavailable_record(comm: dict, fetch_date: str = '', reason: str = '未抓到可核验的公开讨论片段',
                             checked: bool = True) -> dict:
    """构建诚实的「未获取」社区记录：不伪造热评、不预设多空立场、不生成伪量化指标。"""
    ctype = comm.get('ctype', '')
    if checked and fetch_date:
        meta = f"当次未获取（{reason}）· {ctype} · 抓取核验 {fetch_date}"
        source = 'unavailable'
    else:
        meta = f"未执行社区抓取（{reason}）· {ctype}"
        source = 'offline'
    return {
        "id": comm["id"],
        "key": comm["key"],
        "name": comm["name"],
        "icon": comm["icon"],
        "ctype": ctype,
        "url": comm["url"],
        "available": False,
        "verdict_label": UNAVAILABLE_VERDICT_LABEL,
        "verdict_class": UNAVAILABLE_VERDICT_CLASS,
        "quote": f"当次未获取（{reason}）—— 本栏仅展示实盘抓到的公开讨论片段，不编造热评，不回填历史剧本。",
        "verdict": "",
        "quant": None,
        "meta": meta,
        "fetch_date": fetch_date,
        "source": source,
    }


def safe_source_url(url) -> str:
    """Only allow normal HTTP(S) links in generated HTML; never embed credentials."""
    url = str(url or '').strip()
    if not url or any(ch.isspace() or ord(ch) < 32 for ch in url):
        return ''
    try:
        parsed = urlparse(url)
        if (parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            return ''
        _ = parsed.port  # reject malformed ports
    except ValueError:
        return ''
    return url


def build_live_record(comm: dict, snippet: str, fetch_date: str, title: str = '',
                      selector: str = '', adaptive: bool = False, peer_snippets=None,
                      source: str = 'live', evidence: dict | None = None) -> dict:
    """用真实抓取到的公开片段构建社区记录，保留评论的出处和排序口径。"""
    evidence = evidence or {}
    clean_snip = spider_mod.strip_noise(snippet)[:300]
    clean_title = spider_mod.strip_noise(title)[:80]
    if clean_title and clean_title not in clean_snip and clean_title != comm.get('name'):
        quote = f"【{clean_title}】{clean_snip}"
    else:
        quote = clean_snip

    kind = evidence.get('content_kind') or ('demo' if source == 'demo' else 'page_excerpt')
    # A parent headline can provide topic context, but must not vote on behalf
    # of the author of the sampled reply. For comments score only their text.
    analysis_title = '' if kind in ('comment', 'thread_reply') else clean_title
    analysis = analyze_live_snippet(clean_snip, title=analysis_title,
                                    ctype=comm.get('ctype', ''), peer_snippets=peer_snippets,
                                    content_kind=kind)
    ctype = comm.get('ctype', '')
    comments = evidence.get('comments') or []
    first = comments[0] if comments and isinstance(comments[0], dict) else {}
    if first.get('text_truncated'):
        quote += '…'
    if source == 'demo':
        meta = f"演示样本（非实盘抓取 · {len(clean_snip)} 字）· {ctype} · 样本生成 {fetch_date}"
    else:
        adapt_note = " · 指纹自适应回捞" if adaptive else ""
        if kind in ('comment', 'thread_reply') and first:
            label = '实抓采样评论' if kind == 'comment' else '实抓热帖回复'
            metric = (first.get('metric') or {}).get('label') or '热度未知'
            meta = (f"{label}（{len(clean_snip)} 字 · {metric}）"
                    f"· {ctype} · 抓取核验 {fetch_date}")
        else:
            meta = (f"公开页实抓摘录（{len(clean_snip)} 字{adapt_note} · 未核验为评论）"
                    f"· {ctype} · 抓取核验 {fetch_date}")

    return {
        "id": comm["id"],
        "key": comm["key"],
        "name": comm["name"],
        "icon": comm["icon"],
        "ctype": ctype,
        "url": comm["url"],
        "available": True,
        "verdict_label": analysis["verdict_label"],
        "verdict_class": analysis["verdict_class"],
        "quote": quote,
        "verdict": analysis["verdict"],
        "quant": analysis["quant"],
        "meta": meta,
        "selector": selector,
        "content_kind": kind,
        # Retain the actual scoring inputs so later renders can re-analyse the
        # saved snapshot without recrawling or accidentally scoring a headline.
        "analysis_input": {"snippet": clean_snip, "title": analysis_title},
        "strategy": evidence.get('strategy') or ('demo' if source == 'demo' else 'scrapling_page'),
        "source_url": safe_source_url(evidence.get('url')) if source == 'live' else '',
        "comments": comments,
        "matched_comments": evidence.get('matched_comments', 0),
        "comment_attempt": evidence.get('comment_attempt') or {},
        "fetch_date": fetch_date,
        "source": source,
    }


def reanalyze_snapshot(data, now=None):
    """Re-score saved inputs for this render; never rewrite the capture time.

    Old snapshots without analysis_input remain readable and retain their
    original analysis rather than fabricating text or fetching the network.
    """
    if not isinstance(data, dict) or not isinstance(data.get('communities'), list):
        return data
    result = copy.deepcopy(data)
    records = result['communities']
    peers = [c['analysis_input']['snippet'] for c in records
             if isinstance(c, dict) and c.get('available') and isinstance(c.get('analysis_input'), dict)
             and isinstance(c['analysis_input'].get('snippet'), str) and c['analysis_input']['snippet'].strip()]
    reanalyzed = legacy = 0
    for record in records:
        if not isinstance(record, dict) or not record.get('available'):
            continue
        inputs = record.get('analysis_input')
        if not isinstance(inputs, dict) or not isinstance(inputs.get('snippet'), str) or not inputs['snippet'].strip():
            legacy += 1
            continue
        kind = record.get('content_kind') or 'page_excerpt'
        # Headline text must not vote on behalf of a sampled comment author.
        title = inputs.get('title') if isinstance(inputs.get('title'), str) else ''
        if kind in ('comment', 'thread_reply'):
            title = ''
        analysis = analyze_live_snippet(inputs['snippet'], title=title, ctype=record.get('ctype', ''),
                                        peer_snippets=peers, content_kind=kind)
        record.update(analysis)
        reanalyzed += 1
    result['counts'] = count_verdicts([r for r in records if isinstance(r, dict)])
    result['analysis_stats'] = {'reanalyzed': reanalyzed, 'legacy_retained': legacy}
    if reanalyzed:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        result['analysis_at'] = now.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    else:
        result.pop('analysis_at', None)
    return result


def _demo_snippet(comm: dict, hsi: dict, idx: int) -> str:
    """本地 `--demo` 联调样本（显式前缀标注非实盘，且零历史写死点位，经同一真实词库打分）。"""
    tape = f"恒指当次读数 {hsi['last']}（{hsi['pct']}）" if hsi.get('available') else "恒指实时行情待注入"
    samples = (
        f"[演示样本 · 非实盘抓取] {tape}，港股科技与南向资金净流入回暖，估值修复带动偏多讨论。",
        f"[演示样本 · 非实盘抓取] {tape}，美联储利率路径与汇率波动压制风险偏好，短线偏空承压。",
        f"[演示样本 · 非实盘抓取] {tape}，围绕股息红利配置与指数期货波动率的客观讨论为主。",
        f"[演示样本 · 非实盘抓取] {tape}，回购利好支撑低估值反弹，但获利盘流出令多空分歧加剧。",
    )
    return samples[idx % len(samples)]


def generate_all_communities(market=None, demo_mode: bool = False, now=None,
                             timeout: float = 10.0, retries: int = 2, concurrency: int = 6,
                             adaptive: bool = True, storage_file: str = ADAPTIVE_DB_DEFAULT,
                             obey_robots: bool = False, crawldir: str | None = None,
                             backend: str = 'auto', limit: int = 0, session=None,
                             verbose: bool = True, autothrottle: bool = True,
                             max_blocked_retries: int = 2, download_delay: float = 0.15,
                             comment_strategies: bool = True, post_limiter=None) -> dict:
    """运行 49 源抓取并构建完整 community_data 数据集（实抓入库，失败诚实降级为 unavailable）。"""
    now = now or datetime.now(timezone.utc)
    if market is None:
        market = load_market_data(MARKET_DATA_DEFAULT)
    hsi = fmt_hsi(market)
    fetch_date = now.strftime('%Y-%m-%d')
    fetch_date_cn = f"{now.month} 月 {now.day} 日"

    if demo_mode:
        demo_snips = [_demo_snippet(c, hsi, i) for i, c in enumerate(COMMUNITIES)]
        communities_out = [
            build_live_record(c, demo_snips[i], fetch_date=fetch_date,
                              peer_snippets=demo_snips, source='demo')
            for i, c in enumerate(COMMUNITIES)
        ]
        counts = count_verdicts(communities_out)
        return {
            "generated_at": now.strftime('%Y-%m-%d %H:%M:%S UTC'),
            "fetch_date": fetch_date,
            "fetch_date_cn": fetch_date_cn,
            "mode": "demo",
            "hsi_snapshot": hsi,
            "communities": communities_out,
            "counts": counts,
            "summary": {
                "ok": len(communities_out),
                "live_ok": 0,
                "unavailable": 0,
                "total": len(COMMUNITIES),
                "failed": [],
            },
            "fetch_engine": {
                "engine": sc.ENGINE_NAME,
                "backend": "demo",
                "sources": len(COMMUNITIES),
                "adaptive_hits": 0,
            },
            "notes": [
                "演示模式（--demo）：仅用于本地排版与管道联调，已显式标注非实盘抓取",
                "多空立场与量化指标均由 sentiment_nlp 词库对样本实算，零 MD5 伪随机",
            ],
        }

    sources = COMMUNITIES[:limit] if limit else COMMUNITIES
    results, spider_stats = spider_mod.fetch_live_snippets(
        sources, timeout=timeout, retries=retries, concurrency=concurrency,
        adaptive=adaptive, storage_file=storage_file, obey_robots=obey_robots,
        crawldir=crawldir, session=session, verbose=verbose, backend=backend,
        autothrottle=autothrottle, max_blocked_retries=max_blocked_retries,
        download_delay=download_delay, now=now, comment_strategies=comment_strategies,
        post_limiter=post_limiter)

    live_snippets = [
        spider_mod.strip_noise((results.get(c["key"]) or {}).get("snippet") or "")[:300]
        for c in COMMUNITIES
        if (results.get(c["key"]) or {}).get("ok") and (results.get(c["key"]) or {}).get("snippet")
    ]

    communities_out = []
    failed = []
    ok_count = 0
    checked_keys = {c['key'] for c in sources}

    for comm in COMMUNITIES:
        hit = results.get(comm["key"]) or {}
        live_snippet = spider_mod.strip_noise(hit.get("snippet") or "")[:300]
        if hit.get("ok") and live_snippet:
            ok_count += 1
            rec = build_live_record(
                comm, live_snippet, fetch_date=fetch_date,
                title=hit.get("title") or "", selector=hit.get("selector") or "",
                adaptive=bool(hit.get("adaptive")), peer_snippets=live_snippets,
                source='live', evidence=hit)
            communities_out.append(rec)
            if verbose:
                tail = " · 自适应回捞" if hit.get("adaptive") else ""
                transport_label = 'PRAW API' if hit.get('strategy') == 'reddit_praw' else f'HTTP {hit.get("status")}'
                print(f'  ✅ {comm["name"]: <18} live  {transport_label} · '
                      f'{len(live_snippet)} 字 · 立场 {rec["verdict_label"]}{tail}')
        else:
            checked = comm['key'] in checked_keys
            reason = format_unavailable_reason(hit) if checked else '调试限制未请求此源'
            failed.append(comm["name"])
            record = build_unavailable_record(
                comm, fetch_date=fetch_date if checked else '', reason=reason, checked=checked)
            record['comment_attempt'] = hit.get('comment_attempt') or {}
            communities_out.append(record)
            if verbose:
                print(f'  ⚠️ {comm["name"]: <18} 未获取（{reason}）—— 不编造热评', file=sys.stderr)

    counts = count_verdicts(communities_out)
    return {
        "generated_at": now.strftime('%Y-%m-%d %H:%M:%S UTC'),
        "fetch_date": fetch_date,
        "fetch_date_cn": fetch_date_cn,
        "mode": "live",
        "hsi_snapshot": hsi,
        "communities": communities_out,
        "counts": counts,
        "summary": {
            "ok": ok_count,
            "live_ok": ok_count,
            "comment_sources": sum(c.get('content_kind') in ('comment', 'thread_reply') for c in communities_out),
            "page_excerpt_sources": sum(c.get('available') and c.get('content_kind') == 'page_excerpt'
                                        for c in communities_out),
            "unavailable": len(COMMUNITIES) - ok_count,
            "total": len(COMMUNITIES),
            "failed": failed,
        },
        "fetch_engine": spider_stats,
        "notes": [
            "严格真实口径：仅 source=live 的采样评论或公开页摘录参与多空计数",
            "评论按单站有限样本排序；PTT 为高推帖回复而非按回复点赞排序，普通页面摘录未核验为评论",
            "未抓到或风控拦截源一律标记 verdict_class=unavailable（未获取），不回填模板、不编造指标",
        ],
    }


def count_verdicts(communities) -> dict:
    counts = {'bull': 0, 'bear': 0, 'neutral': 0, 'mixed': 0, 'unavailable': 0}
    for c in (communities or []):
        vc = (c or {}).get('verdict_class') or 'unavailable'
        if vc in counts:
            counts[vc] += 1
        else:
            counts['unavailable'] += 1
    return counts


def offline_dataset(market=None, now=None, mode='offline') -> dict:
    """无 `community_data.json` 时的诚实降级数据集：49 源全部标注「未获取」（ok=0），不伪造热评或当天核验标记。"""
    now = now or datetime.now(timezone.utc)
    if market is None:
        market = load_market_data(MARKET_DATA_DEFAULT)
    hsi = fmt_hsi(market)
    fetch_date = now.strftime('%Y-%m-%d')
    fetch_date_cn = f'{now.month} 月 {now.day} 日'
    records = [
        build_unavailable_record(
            comm, fetch_date='', reason='未读到 community_data.json，未执行社区抓取', checked=False)
        for comm in COMMUNITIES
    ]
    return {
        "generated_at": now.strftime('%Y-%m-%d %H:%M:%S UTC'),
        "fetch_date": fetch_date,
        "fetch_date_cn": fetch_date_cn,
        "mode": mode,
        "hsi_snapshot": hsi,
        "communities": records,
        "counts": count_verdicts(records),
        "summary": {
            "ok": 0,
            "live_ok": 0,
            "unavailable": len(COMMUNITIES),
            "total": len(COMMUNITIES),
            "failed": [c["name"] for c in COMMUNITIES],
        },
        "fetch_engine": {
            "engine": sc.ENGINE_NAME,
            "backend": "offline",
            "sources": len(COMMUNITIES),
            "adaptive_hits": 0,
        },
        "notes": [
            "缺省降级：未读到 community_data.json，49 源诚实标记为「未获取」（ok=0）",
            "不编造热评、不预设多空家数、不伪造抓取核验日期",
        ],
    }


def main():
    ap = argparse.ArgumentParser(description='章鱼 AI·舆情004 — 49 大社区实盘抓取（严格真实口径）')
    ap.add_argument('--json', default='community_data.json', help='输出 JSON 路径')
    ap.add_argument('--market-data', '--market', dest='market_data', default=MARKET_DATA_DEFAULT,
                    help='行情数据 JSON 路径')
    ap.add_argument('--timeout', type=float, default=10, help='单次请求超时秒数')
    ap.add_argument('--retries', type=int, default=2, help='单源重试次数（Scrapling Fetcher 语义）')
    ap.add_argument('--concurrency', type=int, default=6, help='并发抓取源数（Spider 线程池）')
    ap.add_argument('--limit', type=int, default=0, help='只抓前 N 个源（联调用）')
    ap.add_argument('--adaptive-db', default=ADAPTIVE_DB_DEFAULT, help='元素指纹库（自适应回捞）路径')
    ap.add_argument('--no-adaptive', action='store_true', help='关闭自适应指纹回捞')
    ap.add_argument('--robots', action='store_true', help='遵从 robots.txt（含 crawl-delay）')
    ap.add_argument('--crawl-dir', default='', help='开启断点续爬：checkpoint 落盘目录')
    ap.add_argument('--backend', default='auto', choices=('auto', 'port', 'scrapling'),
                    help='传输层：auto=装了 scrapling 包就用真包，否则用标准库移植层')
    ap.add_argument('--no-comments', action='store_true', help='关闭多方案评论采样，仅保留公开页摘录')
    ap.add_argument('--demo', action='store_true', help='写入显式标注的本地演示数据（非实盘抓取）')
    ap.add_argument('--offline', action='store_true', help='断网降级：写出诚实「未获取」结构，不篡改旧日期')
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    market = load_market_data(args.market_data)

    if args.offline:
        out = offline_dataset(market=market, now=now, mode='offline')
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f'🌐 离线降级模式: 49 源已诚实标记为「未获取」（不篡改旧抓取日期）→ {args.json}')
        return

    out = generate_all_communities(
        market=market, demo_mode=args.demo, now=now,
        timeout=args.timeout, retries=args.retries, concurrency=args.concurrency,
        adaptive=not args.no_adaptive, storage_file=args.adaptive_db,
        obey_robots=args.robots, crawldir=args.crawl_dir or None,
        backend=args.backend, limit=args.limit, verbose=True, comment_strategies=not args.no_comments)

    with open(args.json, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    s = out['summary']
    if out['mode'] == 'demo':
        print(f'📝 社区数据已写入 {args.json}：演示记录 {s["ok"]}/{s["total"]} · 非实盘抓取')
    else:
        print(f'📝 社区数据已写入 {args.json}：实抓成功 {s["ok"]}/{s["total"]} · '
              f'未获取 {s["unavailable"]} · 模式 {out["mode"]}')


if __name__ == '__main__':
    main()
