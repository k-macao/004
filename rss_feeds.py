#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 49 大财经 RSS 情报源（数据层 + 抓取 + 渲染）
==================================================================

本文件是**新增栏目「05 / 全球 49 大财经 RSS 情报源」的唯一事实源**：
目录（49 条，六大板块）、抓取、解析、归一、时效过滤、可用性判定与两端渲染
（网页 05 节 + 微信推送 05 栏）全部在这里，避免两套口径漂移。

口径与 02 宏观快讯完全一致（同一套反幻觉原则）
------------------------------------------------
  1. **只展示当次真实抓到的条目**：`source="live"` 才带正文；抓不到就整源标
     「未获取」+ 失败原因，**绝不回填历史标题、绝不编造「今日重点」**；
  2. **发布日期可解析才入库**：超窗（默认 14 天）条目在数据层丢弃并计数
     （`stale_dropped`）；无日期条目**不当最新**（只在源内 `undated` 计数，
     渲染时永远排在带日期条目之后，并明确标「日期未标注」）；
  3. **不伪造抓取日期**：`generated_at / fetch_date` 一律取当次抓取的真实时刻；
     快照超过 1 天（`SNAPSHOT_MAX_AGE_DAYS`）即判定 `stale_snapshot`，走兜底；
  4. **RSSHub 实例是第三方公共服务**：默认 `https://rsshub.app`，可用环境变量
     `RSSHUB_BASE`（逗号分隔多个实例）覆盖；实例不可达时按顺序回退到原生 RSS
     或 Google News 检索载体，并在 `sources[].error` 里如实记录失败原因。

为什么不是 49 个「裸域名」
--------------------------
原始清单里大部分条目只给了站点域名（economist.com / ft.com / dj.com /
rsshub.app …），域名本身不是订阅地址。这里逐条落成**可用的具体端点**：
原生官方 RSS 优先 → RSSHub 路由（路径按 RSSHub master 路由表核对过）→
该站没有公开 RSS 时用 Google News 检索载体（`kind="gnews"`，如实标注为聚合检索，
不声称是站点原生订阅源）。逐条核验状态写在 `verified` 字段：
  `rsshub_doc`  RSSHub 官方路由表中确有该路由（路径/参数按路由源码核对）
  `native`      站点官方公开 RSS 地址
  `needs_check` 地址来自公开资料，站点改版可能失效（抓取失败会如实标未获取）
  `carrier`     站点未提供公开 RSS，用 Google News 检索作为载体

用法
----
  python3 rss_feeds.py                      # 抓取 49 源 → rssfeeds_data.json
  python3 rss_feeds.py --limit 3 --text     # 只抓前 3 源并打印人读摘要（联调）
  python3 rss_feeds.py --mock               # 离线回放 tests/fixtures（不联网）
  python3 rss_feeds.py --offline            # 断网兜底：沿用上次结果，仅刷新时间戳
  python3 rss_feeds.py --self-test          # 目录自检（49 源 / 唯一性 / 分组 / 端点）
  python3 rss_feeds.py --catalog            # 只打印目录（板块 · 名称 · 端点 · 核验状态）
"""
from __future__ import annotations

import argparse
import concurrent.futures
import html as html_mod
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(REPO_ROOT, 'rssfeeds_data.json')
FIXTURE_DIR = os.path.join(REPO_ROOT, 'tests', 'fixtures')
FIXTURE_NAME = 'RSSFEEDS_MIX.json'

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

MAX_AGE_DAYS_DEFAULT = 14        # 情报源里含专栏/研报，窗口比 02 快讯（7 天）宽一档
LIMIT_PER_SOURCE_DEFAULT = 3     # 每个源最多留 3 条，49 源同页才不会把正文顶爆
SNAPSHOT_MAX_AGE_DAYS = 1        # 快照新鲜度上限，与 macro_data 同一口径

# RSSHub 实例：第三方公共服务，可用环境变量覆盖（逗号分隔，按顺序回退）
#
# 为什么默认挂一串而不是只用 rsshub.app：
#   GitHub Actions 的出口 IP 段会被 rsshub.app 前面的 Cloudflare 直接回 403
#   （实测 CI run 41168249465：13 条 rsshub.app 路线全 403，而同一路线在
#   本机 / 独立出口上 200 且有最新条目 —— 说明是**按 IP 拦边缘**，不是路由坏死）。
#   实测要注意区分两种失败：
#     · 403 是 CF 拦 IP                → 换实例就能救（这里要解决的）
#     · 200 + RSSHub 错误页（如 WSJ 上游 401、雪球要浏览器）→ 换实例救不了，属路由自身
#   下面每个实例都是探活过 /test/cache 的，顺序＝优先级，并保留 rsshub.app 兜底。
DEFAULT_RSSHUB_BASE = ','.join([
    'https://rsshub.app',              # 官方（非机房 IP 出口最快）
    'https://rsshub.rss.tips',         # 🇺🇸 AboutRSS，实测 /test/cache + /cls/telegraph 正常
    'https://rsshub.isrss.com',        # 🇺🇸 isRSS，实测同上
    'https://rsshub.cups.moe',         # 🇺🇸 FunnyCups，实测 /test/cache 正常
])
# 并发抓取线程数：49 源全是网络 I/O，串行时一条慢源就能吃掉整轮预算
# （实测 CI：路由超时 3 次 × 10s 的源排在前面时，整步被 timeout 300 掐断，
#  31 源根本没发出请求）。默认 8 路，单源最长耗时 ≈ timeout × 端点链长度。
RSS_CONCURRENCY = int(os.environ.get('RSS_CONCURRENCY', '8') or 8)
RSSHUB_MAX_PER_RUN = int(os.environ.get('RSSHUB_MAX_PER_RUN', '20') or 20)
# 连续失败多少个不同实例后，本轮直接放弃 RSSHub（避免 49 源 × N 实例 的 403 风暴把构建拖死）
RSSHUB_TRIP_AFTER = int(os.environ.get('RSSHUB_TRIP_AFTER', '3') or 3)

# ---------------------------------------------------------------------------
# 六大板块（与用户清单的分节一一对应）
# ---------------------------------------------------------------------------
GROUPS = [
    ('macro', '一、国际宏观与顶级政经媒体', '全球央行动态、利率走向与跨国经济周期'),
    ('us', '二、美股、科技创新与公司财报', '美股大盘、明星科技股、创新逻辑与个股研报'),
    ('cn', '三、A 股、港股与大中华区财经', '国内政策、板块轮动、港股外资动向与公司公告'),
    ('cmdty', '四、大宗商品、外汇与衍生品', '黄金、原油、美元指数与大宗供应链'),
    ('crypto', '五、加密货币与 Web3 前沿', '比特币、以太坊行情、监管政策与链上数据'),
    ('masters', '六、顶级量化与独立投资人专栏', '顶尖投资人的长期生存法则与研究框架'),
]
GROUP_LABELS = {k: label for k, label, _desc in GROUPS}
GROUP_DESCS = {k: desc for k, _label, desc in GROUPS}
GROUP_EXPECTED = {'macro': 10, 'us': 10, 'cn': 11, 'cmdty': 8, 'crypto': 6, 'masters': 4}

KIND_LABELS = {'native': '原生 RSS', 'rsshub': 'RSSHub 路由', 'gnews': '检索载体',
               'deepseek': 'DeepSeek 检索'}

# DeepSeek Search API（2026-10-17 上线）：官方自建搜索引擎的原始 JSON 接口。
#   POST https://api.deepseek.com/v1/tools/search
#   {query, count, need_content, ori_question, localization}
#   → results[{title,url,snippet,summary,content,date(RFC3339),rerank_score(0-3),authority(0-4)}]
# 用途定位：**只给「RSS 路由自身坏死 / 站点端点消失」的源做检索兜底**，
# 不用它替代已跑通的原生 RSS（拿搜索结果冒充订阅源既更贵也更差）。
DEEPSEEK_SEARCH_URL = 'https://api.deepseek.com/v1/tools/search'
DEEPSEEK_TIMEOUT = int(os.environ.get('DEEPSEEK_TIMEOUT', '20') or 20)
# 低质量结果门槛：官方口径 rerank_score ≥ 1.5 视为高质量；0 表示不过滤。
DEEPSEEK_MIN_SCORE = float(os.environ.get('DEEPSEEK_MIN_SCORE', '0') or 0)
# 每轮最多调用几次（省钱 + 避开 5 QPS）：失败/空结果免费，只有成功才计费。
DEEPSEEK_MAX_PER_RUN = int(os.environ.get('DEEPSEEK_MAX_PER_RUN', '20') or 20)


def _rsshub(path):
    """RSSHub 路由占位（抓取时按 RSSHUB_BASE 展开，便于自建实例）。"""
    return f'rsshub:/{path.lstrip("/")}'


# ---------------------------------------------------------------------------
# 目录：49 条。endpoints 按优先级排列（占位符 `rsshub:` 开头的是 RSSHub 路由）
#   verified: rsshub_doc / native / needs_check / carrier
# ---------------------------------------------------------------------------
CATALOG = [
    # ---------------- 一、国际宏观与顶级政经媒体（1-10） ----------------
    {'id': 1, 'key': 'ECONOMIST', 'name': '经济学人 · 金融与经济', 'group': 'macro', 'lang': 'en',
     'site': 'https://www.economist.com/finance-and-economics',
     'endpoints': ['https://www.economist.com/finance-and-economics/rss.xml',
                   _rsshub('/economist/finance-and-economics')],
     'verified': 'native',
     'note': '《经济学人》官方「Finance & economics」板块 RSS，全球经济大势定调。'},
    {'id': 2, 'key': 'FT', 'name': 'FT 全球经济', 'group': 'macro', 'lang': 'en',
     'site': 'https://www.ft.com/global-economy',
     'endpoints': ['https://www.ft.com/rss/home', _rsshub('/ft/home')],
     'verified': 'native',
     'note': '英国《金融时报》官方 RSS 首页源（含全球经济/市场要闻）；付费墙文章只给摘要。'},
    {'id': 3, 'key': 'WSJ_ECON', 'name': 'WSJ 经济板块', 'group': 'macro', 'lang': 'en',
     'deepseek_query': 'economy markets',
     'deepseek_fallback': True,
     'site': 'https://www.wsj.com/news/economy',
     'endpoints': [_rsshub('/wsj/en-us/economy'), _rsshub('/wsj/zh-cn/economy')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub 路由 /wsj/:lang/:category（lang=en-us，category=economy，见官方路由表）。'},
    {'id': 4, 'key': 'REUTERS', 'name': '路透社全球财经', 'group': 'macro', 'lang': 'en',
     'site': 'https://www.reuters.com/business/',
     'query': 'Reuters business markets economy central bank',
     'endpoints': [_rsshub('/reuters/business'), _rsshub('/reuters/world/us')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub 有 /reuters/:category/:topic 路由（分类随站点改版变动）；'
             '不可用时回退 Google News 路透检索，如实标注为聚合检索。'},
    {'id': 5, 'key': 'BLOOMBERG', 'name': '彭博社市场分析', 'group': 'macro', 'lang': 'en',
     'site': 'https://www.bloomberg.com/markets',
     'endpoints': ['https://feeds.bloomberg.com/markets/news.rss', _rsshub('/bloomberg/markets')],
     'verified': 'native',
     'note': '彭博官方 markets 分站 RSS（RSSHub /bloomberg/:site 同源，作回退）。'},
    {'id': 6, 'key': 'CNBC', 'name': 'CNBC 宏观', 'group': 'macro', 'lang': 'en',
     'site': 'https://www.cnbc.com/economy/',
     'endpoints': ['https://www.cnbc.com/id/10000664/device/rss/rss.html'],
     'verified': 'native',
     'note': 'CNBC 官方 Economy 板块 RSS（id 10000664）。'},
    {'id': 7, 'key': 'MARKETWATCH', 'name': 'MarketWatch 宏观', 'group': 'macro', 'lang': 'en',
     'site': 'https://www.marketwatch.com/',
     'endpoints': ['https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines',
                   'https://feeds.content.dowjones.io/public/rss/mw_topstories'],
     'verified': 'native',
     'note': '道琼斯官方 MarketWatch 实时头条 RSS。'},
    {'id': 8, 'key': 'HBR', 'name': '哈佛商业评论', 'group': 'macro', 'lang': 'en',
     'site': 'https://hbr.org/',
     'endpoints': ['https://feeds.hbr.org/harvardbusiness', _rsshub('/hbr/topic/Leadership/From%20the%20Store')],
     'verified': 'native',
     'note': 'HBR 官方订阅源；RSSHub /hbr/topic/:topic 已不再提供 Popular 流（路由说明）。'},
    {'id': 9, 'key': 'NYT', 'name': '纽约时报财金', 'group': 'macro', 'lang': 'en',
     'site': 'https://www.nytimes.com/section/business',
     'endpoints': ['https://rss.nytimes.com/services/xml/rss/nyt/Business.xml',
                   _rsshub('/nytimes/rss/Business')],
     'verified': 'native',
     'note': 'NYT 官方 Business RSS（RSSHub /nytimes/rss/:cat 同源）。'},
    {'id': 10, 'key': 'FORBES_BIZ', 'name': '福布斯商业', 'group': 'macro', 'lang': 'en',
     'site': 'https://www.forbes.com/business/',
     'endpoints': ['https://www.forbes.com/business/feed/'],
     'verified': 'native',
     'note': 'Forbes 官方 business 频道 RSS。'},

    # ---------------- 二、美股、科技创新与公司财报（11-20） ----------------
    {'id': 11, 'key': 'WSJ_BIZ', 'name': 'WSJ 全美商业', 'group': 'us', 'lang': 'en',
     'deepseek_query': 'business companies',
     'deepseek_fallback': True,
     'site': 'https://www.wsj.com/news/business',
     'endpoints': [_rsshub('/wsj/en-us/business'), _rsshub('/wsj/zh-cn/business')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /wsj/en-us/business（官方路由表 category=business）。'},
    {'id': 12, 'key': 'ARK', 'name': 'ARK Invest（方舟投资）', 'group': 'us', 'lang': 'en',
     'site': 'https://ark-invest.com/',
     'endpoints': ['https://ark-invest.com/feed/', 'https://ark-invest.com/feed/?post_type=research'],
     'verified': 'needs_check',
     'note': 'ARK 官方站 RSS（WordPress feed）；站点改版会失效，失败如实标未获取。'},
    {'id': 13, 'key': 'TECHCRUNCH', 'name': 'TechCrunch 创投', 'group': 'us', 'lang': 'en',
     'site': 'https://techcrunch.com/category/venture/',
     'endpoints': ['https://techcrunch.com/feed/',
                   'https://techcrunch.com/category/venture/feed/'],
     'verified': 'native',
     'note': 'TechCrunch 官方 RSS（创投分类另有 category/venture/feed/）。'},
    {'id': 14, 'key': 'BARRONS', 'name': "Barron's（巴伦周刊）", 'group': 'us', 'lang': 'zh-CN',
     'site': 'https://www.barronschina.com.cn/',
     'endpoints': [_rsshub('/barronschina'), _rsshub('/barronschina/1')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /barronschina/:id（默认快讯栏目；原版 barrons.com 无公开 RSS）。'},
    {'id': 15, 'key': 'SEEKINGALPHA', 'name': 'Seeking Alpha 美股选股', 'group': 'us', 'lang': 'en',
     'deepseek_query': 'market news stocks',
     'deepseek_fallback': True,
     'site': 'https://seekingalpha.com/market-news',
     'endpoints': ['https://seekingalpha.com/market-news.rss',
                   _rsshub('/seekingalpha/TSM/news')],
     'verified': 'native',
     'note': 'Seeking Alpha 官方 market-news RSS；RSSHub /seekingalpha/:symbol/:category 可按个股取。'},
    {'id': 16, 'key': 'IBD', 'name': "Investor's Business Daily", 'group': 'us', 'lang': 'en',
     'site': 'https://www.investors.com/',
     'endpoints': ['https://www.investors.com/feed/'],
     'verified': 'native',
     'note': 'IBD 官方站点 RSS（CAN SLIM 与市场趋势分析栏目）。'},
    {'id': 17, 'key': 'YAHOO_FIN', 'name': '雅虎财经美股', 'group': 'us', 'lang': 'en',
     'site': 'https://finance.yahoo.com/',
     'endpoints': ['https://finance.yahoo.com/news/rssindex',
                   _rsshub('/yahoo/finance/latest-news')],
     'verified': 'native',
     'note': 'Yahoo Finance 官方新闻 RSS；RSSHub /yahoo/finance/:topic 可换主题。'},
    {'id': 18, 'key': 'FORBES_MKT', 'name': '福布斯市场', 'group': 'us', 'lang': 'en',
     'site': 'https://www.forbes.com/markets/',
     'endpoints': ['https://www.forbes.com/markets/feed/'],
     'verified': 'native',
     'note': 'Forbes 官方 markets 频道 RSS。'},
    {'id': 19, 'key': 'BENZINGA', 'name': 'Benzinga 美股新闻', 'group': 'us', 'lang': 'en',
     'site': 'https://www.benzinga.com/',
     'endpoints': ['https://www.benzinga.com/feed', 'https://www.benzinga.com/feed/rss'],
     'verified': 'native',
     'note': 'Benzinga 官方新闻 RSS（盘前盘后异动）。'},
    {'id': 20, 'key': 'FOOL', 'name': 'The Motley Fool（傻瓜投资）', 'group': 'us', 'lang': 'en',
     'site': 'https://www.fool.com/',
     'endpoints': ['https://www.fool.com/feeds/index.aspx'],
     'verified': 'needs_check',
     'note': 'Motley Fool 老式 RSS 出口（feeds/index.aspx）；失效时如实标未获取。'},

    # ---------------- 三、A 股、港股与大中华区财经（21-31） ----------------
    {'id': 21, 'key': 'WALLSTREETCN', 'name': '华尔街见闻实时', 'group': 'cn', 'lang': 'zh-CN',
     'site': 'https://wallstreetcn.com/live/global',
     'endpoints': [_rsshub('/wallstreetcn/live/global/2'), _rsshub('/wallstreetcn/news/global')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /wallstreetcn/live/:category/:score（score=2 只看重要）与 /news/:category。'},
    {'id': 22, 'key': 'CLS', 'name': '财联社电报', 'group': 'cn', 'lang': 'zh-CN',
     'site': 'https://www.cls.cn/telegraph',
     'endpoints': [_rsshub('/cls/telegraph'), _rsshub('/cls/depth/1000')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /cls/telegraph（另有 watch/announcement/red/hk_us 等分类）。'},
    {'id': 23, 'key': 'XUEQIU', 'name': '雪球今日话题', 'group': 'cn', 'lang': 'zh-CN',
     'deepseek_query': '今日话题 港股 A股',
     'deepseek_fallback': True,
     'site': 'https://xueqiu.com/today',
     'endpoints': [_rsshub('/xueqiu/today'), _rsshub('/xueqiu/hots')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /xueqiu/today（路由标记 antiCrawler，需实例可拿 Cookie；失败如实标未获取）。'},
    {'id': 24, 'key': 'SINA_FIN', 'name': '新浪财经聚合', 'group': 'cn', 'lang': 'zh-CN',
     'deepseek_query': '财经 滚动新闻',
     'deepseek_fallback': True,
     'site': 'https://finance.sina.com.cn/',
     'endpoints': [_rsshub('/sina/finance/rollnews'), _rsshub('/sina/finance/china')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /sina/finance/rollnews/:lid（默认 2519 财经滚动）。'},
    {'id': 25, 'key': 'CAIXIN', 'name': '财新网经济', 'group': 'cn', 'lang': 'zh-CN',
     'site': 'https://www.caixin.com/',
     'endpoints': [_rsshub('/caixin/latest'), _rsshub('/caixin/economy/economy')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /caixin/latest（另有 /caixin/:column/:category，column=economy 为经济板块）；'
             '付费内容只做更新提醒。'},
    {'id': 26, 'key': 'EASTMONEY_RPT', 'name': '东方财富研报', 'group': 'cn', 'lang': 'zh-CN',
     'site': 'https://data.eastmoney.com/report/',
     'endpoints': [_rsshub('/eastmoney/report/strategyreport'),
                   _rsshub('/eastmoney/report/macresearch')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /eastmoney/report/:category（strategyreport 策略 / macresearch 宏观 / '
             'industry 行业 / stock 个股）。'},
    {'id': 27, 'key': 'EEO', 'name': '经济观察网', 'group': 'cn', 'lang': 'zh-CN',
     'site': 'http://www.eeo.com.cn/',
     'query': '经济观察网 宏观 行业 调查',
     'endpoints': [],
     'verified': 'carrier',
     'note': '经济观察网无公开 RSS，改用 Google News 中文检索载体（如实标注为聚合检索）。'},
    {'id': 28, 'key': 'CNINFO', 'name': '巨潮资讯公告', 'group': 'cn', 'lang': 'zh-CN',
     'site': 'http://www.cninfo.com.cn/',
     'query': '巨潮资讯 上市公司 公告',
     'endpoints': [_rsshub('/cninfo/announcement/szse/000002/gssz0000002/category_ndbg_szsh')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /cninfo/announcement/:column/:code/:orgId 是**逐只股票**的公告路由；'
             '全市场公告用检索载体兜底，避免把单只个股公告冒充全市场监控。'},
    {'id': 29, 'key': 'CNSTOCK', 'name': '中国证券网（上证报）', 'group': 'cn', 'lang': 'zh-CN',
     'site': 'https://www.cnstock.com/',
     'query': '中国证券网 证监会 上证报 政策',
     'endpoints': [_rsshub('/stcn/article/list/yw')],
     'verified': 'carrier',
     'note': 'RSSHub 无 cnstock 路由；证券时报网 /stcn/article/list/yw 作同城财经媒体回退，'
             '正文如实标注每条条目的来源。'},
    {'id': 30, 'key': 'AASTOCKS', 'name': '阿斯达克财经（AASTOCKS）', 'group': 'cn', 'lang': 'zh-HK',
     'site': 'http://www.aastocks.com/',
     'query': '阿斯达克 港股 资金流向',
     'endpoints': [_rsshub('/hkej/index'), _rsshub('/hket/sran001')],
     'verified': 'carrier',
     'note': 'RSSHub 无 aastocks 路由；港股本地媒体回退用信报 /hkej、香港经济日报 /hket。'},
    {'id': 31, 'key': 'KR36', 'name': '36 氪（一级市场）', 'group': 'cn', 'lang': 'zh-CN',
     'site': 'https://36kr.com/',
     'endpoints': ['https://36kr.com/feed', _rsshub('/36kr/newsflashes')],
     'verified': 'native',
     'note': '36 氪官方 RSS（新经济/投融资快讯）；RSSHub /36kr/newsflashes 作回退。'},

    # ---------------- 四、大宗商品、外汇与衍生品（32-39） ----------------
    {'id': 32, 'key': 'INVESTING', 'name': '英为财情全球行情', 'group': 'cmdty', 'lang': 'en',
     'site': 'https://cn.investing.com/news/',
     'endpoints': ['https://www.investing.com/rss/news_25.rss',
                   'https://www.investing.com/rss/commodities_Opinion.rss'],
     'verified': 'native',
     'note': 'Investing.com 官方分类 RSS（news_25 经济指标；商品/外汇各有对应编号）。'},
    {'id': 33, 'key': 'KITCO', 'name': 'Kitco 黄金', 'group': 'cmdty', 'lang': 'en',
     'deepseek_query': 'gold silver news',
     'deepseek_fallback': True,
     'site': 'https://www.kitco.com/news/',
     'endpoints': ['https://www.kitco.com/rss/KitcoNews.xml', 'https://www.kitco.com/rss/'],
     'verified': 'needs_check',
     'note': 'Kitco 官方新闻 RSS（贵金属行情与分析）；站点改版会换路径。'},
    {'id': 34, 'key': 'OILPRICE', 'name': 'Oilprice 原油', 'group': 'cmdty', 'lang': 'en',
     'site': 'https://oilprice.com/',
     'endpoints': ['https://oilprice.com/rss/main'],
     'verified': 'native',
     'note': 'Oilprice.com 官方主源（油价、OPEC 政策与地缘风险）。'},
    {'id': 35, 'key': 'FXSTREET', 'name': 'FXStreet 外汇', 'group': 'cmdty', 'lang': 'en',
     'deepseek_query': 'forex news analysis',
     'deepseek_fallback': True,
     'site': 'https://www.fxstreet.com/news',
     'endpoints': ['https://www.fxstreet.com/rss/news'],
     'verified': 'native',
     'note': 'FXStreet 官方新闻 RSS（主要货币对与技术分析）。'},
    {'id': 36, 'key': 'DAILYFX', 'name': 'DailyFX', 'group': 'cmdty', 'lang': 'en',
     'deepseek_query': 'forex market news',
     'deepseek_fallback': True,
     'site': 'https://www.dailyfx.com/',
     'endpoints': ['https://www.dailyfx.com/feeds/all'],
     'verified': 'native',
     'note': 'DailyFX 官方全站 RSS（汇市与大宗商品预警）。'},
    {'id': 37, 'key': 'JIN10', 'name': '金十数据快讯', 'group': 'cmdty', 'lang': 'zh-CN',
     'site': 'https://www.jin10.com/',
     'endpoints': [_rsshub('/jin10'), _rsshub('/jin10/1')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /jin10/:important?（留空=全部快讯，传任意值只看重要）。'},
    {'id': 38, 'key': 'FX678', 'name': '汇通网', 'group': 'cmdty', 'lang': 'zh-CN',
     'deepseek_query': '快讯 外汇 商品',
     'deepseek_fallback': True,
     'site': 'https://www.fx678.com/',
     'endpoints': [_rsshub('/fx678/kx')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /fx678/kx（7×24 小时快讯）。'},
    {'id': 39, 'key': 'FREIGHTOS', 'name': 'Freightos 航运指数', 'group': 'cmdty', 'lang': 'en',
     'site': 'https://fbx.freightos.com/',
     'query': 'Freightos Baltic Index container freight rates',
     'endpoints': [],
     'verified': 'carrier',
     'note': 'Freightos Baltic Index 是数据产品、无公开 RSS；用 Google News 检索载体追踪运价报道。'},

    # ---------------- 五、加密货币与 Web3 前沿（40-45） ----------------
    {'id': 40, 'key': 'COINDESK', 'name': 'CoinDesk', 'group': 'crypto', 'lang': 'en',
     'site': 'https://www.coindesk.com/',
     'endpoints': ['https://www.coindesk.com/arc/outboundfeeds/rss/', _rsshub('/coindesk/news')],
     'verified': 'native',
     'note': 'CoinDesk 官方 Arc RSS（RSSHub /coindesk/news 同源，作回退）。'},
    {'id': 41, 'key': 'COINTELEGRAPH', 'name': 'Cointelegraph', 'group': 'crypto', 'lang': 'en',
     'site': 'https://cointelegraph.com/',
     'endpoints': ['https://cointelegraph.com/rss'],
     'verified': 'native',
     'note': 'Cointelegraph 官方 RSS。'},
    {'id': 42, 'key': 'BLOCKBEATS', 'name': '律动 BlockBeats', 'group': 'crypto', 'lang': 'zh-CN',
     'site': 'https://www.theblockbeats.info/',
     'endpoints': [_rsshub('/theblockbeats/newsflash'), _rsshub('/theblockbeats/article')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /theblockbeats/:channel?/:original?（默认 newsflash 快讯）。'},
    {'id': 43, 'key': 'FORESIGHT', 'name': 'Foresight News', 'group': 'crypto', 'lang': 'zh-CN',
     'site': 'https://foresightnews.pro/',
     'endpoints': [_rsshub('/foresightnews/news'), _rsshub('/foresightnews')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /foresightnews/news（快讯）、/foresightnews/article（文章）、/（精选）。'},
    {'id': 44, 'key': 'THEBLOCK', 'name': 'The Block', 'group': 'crypto', 'lang': 'en',
     'site': 'https://www.theblock.co/',
     'endpoints': ['https://www.theblock.co/rss.xml',
                   _rsshub('/theblock/category/crypto-ecosystems')],
     'verified': 'native',
     'note': 'The Block 官方 rss.xml；RSSHub /theblock/category/:category 可换分类。'},
    {'id': 45, 'key': 'BITCOINMAG', 'name': 'Bitcoin Magazine', 'group': 'crypto', 'lang': 'en',
     'site': 'https://bitcoinmagazine.com/',
     'endpoints': ['https://bitcoinmagazine.com/feed'],
     'verified': 'native',
     'note': 'Bitcoin Magazine 官方 RSS（比特币生态与 Layer2）。'},

    # ---------------- 六、顶级量化与独立投资人专栏（46-49） ----------------
    {'id': 46, 'key': 'OAKTREE', 'name': '霍华德·马克斯备忘录（橡树资本）', 'group': 'masters',
     'lang': 'en', 'site': 'https://www.oaktreecapital.com/insights',
     'endpoints': [_rsshub('/oaktree/insights')],
     'verified': 'rsshub_doc',
     'note': 'RSSHub /oaktree/insights（Howard Marks 备忘录与橡树洞见）。'},
    {'id': 47, 'key': 'DAMODARAN', 'name': 'Aswath Damodaran（估值大师）', 'group': 'masters',
     'lang': 'en', 'site': 'https://aswathdamodaran.blogspot.com/',
     'endpoints': ['https://aswathdamodaran.blogspot.com/feeds/posts/default?alt=rss',
                   'https://aswathdamodaran.blogspot.com/feeds/posts/default'],
     'verified': 'native',
     'note': 'Blogspot 官方 Atom/RSS 出口（纽约大学估值教授博客）。'},
    {'id': 48, 'key': 'QUANTSTART', 'name': 'QuantStart（量化交易）', 'group': 'masters',
     'deepseek_query': 'quantitative trading algorithms',
     'deepseek_fallback': True,
     'lang': 'en', 'site': 'https://www.quantstart.com/',
     'endpoints': ['https://www.quantstart.com/feed/', 'https://www.quantstart.com/articles/feed/'],
     'verified': 'needs_check',
     'note': 'QuantStart 站点 RSS（算法、回测与数学模型）；站点改版会失效。'},
    {'id': 49, 'key': 'PROJECT_SYNDICATE', 'name': 'Project Syndicate（世界报业辛迪加）', 'group': 'masters',
     'lang': 'en', 'site': 'https://www.project-syndicate.org/',
     'endpoints': ['https://www.project-syndicate.org/rss'],
     'verified': 'needs_check',
     'note': 'Project Syndicate 官方 RSS（诺奖级经济学家专栏）。'},
]

CATALOG_BY_KEY = {c['key']: c for c in CATALOG}
CATALOG_BY_ID = {c['id']: c for c in CATALOG}


# ---------------------------------------------------------------------------
# 目录自检（测试与 CI 复用，不联网）
# ---------------------------------------------------------------------------
def catalog_errors():
    """返回目录结构问题列表（空列表 = 通过）。"""
    errs = []
    if len(CATALOG) != 49:
        errs.append(f'源数与清单不符：{len(CATALOG)} != 49')
    ids = [c.get('id') for c in CATALOG]
    keys = [c.get('key') for c in CATALOG]
    names = [c.get('name') for c in CATALOG]
    if len(set(ids)) != len(ids):
        errs.append('id 重复')
    if len(set(keys)) != len(keys):
        errs.append('key 重复')
    if len(set(names)) != len(names):
        errs.append('name 重复')
    if sorted(ids) != list(range(1, 50)):
        errs.append('id 必须是 1..49 且连续')
    for group, expect in GROUP_EXPECTED.items():
        got = sum(1 for c in CATALOG if c.get('group') == group)
        if got != expect:
            errs.append(f'板块 {group} 源数 {got} != 清单 {expect}')
    for c in CATALOG:
        tag = c.get('key') or c.get('id')
        if c.get('group') not in GROUP_LABELS:
            errs.append(f'{tag}: 未知板块 {c.get("group")}')
        if c.get('verified') not in ('rsshub_doc', 'native', 'needs_check', 'carrier'):
            errs.append(f'{tag}: verified 取值非法 {c.get("verified")}')
        if not c.get('note'):
            errs.append(f'{tag}: 缺少口径说明 note')
        if not c.get('endpoints'):
            # 无端点的源必须带检索 query，否则无从抓取
            if not c.get('query'):
                errs.append(f'{tag}: 既无端点也无检索词')
        for ep in c.get('endpoints') or []:
            if ep.startswith('rsshub:'):
                if not re.match(r'^rsshub:/?[\w\-./%]+$', ep):
                    errs.append(f'{tag}: RSSHub 路由格式异常 {ep}')
            elif not ep.startswith('http'):
                errs.append(f'{tag}: 端点必须是 http(s) 或 rsshub: 占位 {ep}')
        if c.get('verified') == 'rsshub_doc' and not any(
                e.startswith('rsshub:') for e in (c.get('endpoints') or [])):
            errs.append(f'{tag}: 标注 rsshub_doc 但目录里没有 RSSHub 路由')
    return errs


# ---------------------------------------------------------------------------
# 端点解析 / HTTP
# ---------------------------------------------------------------------------
def rsshub_bases():
    """RSSHub 实例列表（环境变量 RSSHUB_BASE 可逗号分隔多个，按顺序回退）。"""
    raw = os.environ.get('RSSHUB_BASE') or DEFAULT_RSSHUB_BASE
    bases = [b.strip().rstrip('/') for b in raw.split(',') if b.strip()]
    return bases or ['https://rsshub.app']


def rsshub_proxy():
    """可选出站代理：RSSHUB_PROXY / HTTPS_PROXY / HTTP_PROXY（单个 URL）。

    走代理是「换出口」的另一条路，适合能用住宅/其它地区出口的环境。
    """
    for key in ('RSSHUB_PROXY', 'HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy'):
        val = str(os.environ.get(key) or '').strip()
        if val:
            return val
    return ''


def _is_rsshub_url(url):
    """该 URL 是不是某个 RSSHub 实例上的路由。"""
    return any(url.startswith(base + '/') or url == base for base in rsshub_bases())


def _instance_of(url):
    """该 URL 属于哪个 RSSHub 实例（取最长前缀，避免子路径误判）。"""
    match = ''
    for base in rsshub_bases():
        if (url == base or url.startswith(base + '/')) and len(base) > len(match):
            match = base
    return match


def _is_edge_block(err):
    """异常文本是否属于「边缘/CDN 按 IP 拦截」（403/429），换实例或换出口有意义。"""
    text = str(err)
    return ('403' in text or '429' in text
            or 'Forbidden' in text or 'Too Many Requests' in text)


def deepseek_key():
    """DeepSeek API Key（DEEPSEEK_API_KEY）；没有就返回空串。"""
    return str(os.environ.get('DEEPSEEK_API_KEY') or '').strip()


def deepseek_enabled():
    """DeepSeek 检索兜底是否可用：有 key 且没被显式关掉。"""
    if str(os.environ.get('DEEPSEEK_SEARCH', '')).strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    return bool(deepseek_key())


def _http_post_json(url, payload, headers=None, timeout=20, proxy=None):
    """POST JSON → dict。与 http_get 同一套「自己建 opener」的做法（不吃全局代理设置）。"""
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST', headers={
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'User-Agent': UA,
        **(headers or {}),
    })
    proxy_url = proxy if proxy is not None else rsshub_proxy()
    if proxy_url:
        handlers = [urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})]
    else:
        handlers = [urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode('utf-8', errors='replace') or '{}')


def build_deepseek_query(src, now=None):
    """把目录条目拼成检索式：锁定站点 + 语言 + 时效，尽量贴近「订阅这个板块」。

    site: 取站点主机名（保留可能的子域路径语义）；after: 收紧到时效窗口起点，
    避免把几个月前的旧闻当今日情报；lang: 让中英文站点各走各的语言。
    """
    now = now or datetime.now(timezone.utc)
    site = ''
    raw_site = str(src.get('site') or '')
    if raw_site:
        parsed = urllib.parse.urlparse(raw_site if '//' in raw_site else 'https://' + raw_site)
        site = (parsed.hostname or '').lower()
    if site.startswith('www.'):
        site = site[4:]
    parts = []
    if site:
        parts.append(f'site:{site}')
    topic = str(src.get('deepseek_query') or '').strip()
    if topic:
        parts.append(topic)
    lang = str(src.get('lang') or '').lower()
    if lang.startswith('zh'):
        parts.append('lang:zh')
    elif lang:
        parts.append('lang:en')
    since = (now - timedelta(days=MAX_AGE_DAYS_DEFAULT)).strftime('%Y-%m-%d')
    parts.append(f'after:{since}')
    return ' '.join(parts)[:128]


def deepseek_search(query, count=10, now=None, timeout=None, proxy=None, key=None):
    """调 DeepSeek Search API → 归一化后的条目列表（与 parse_feed 的产物同构）。

    失败抛异常，由 fetch_source 归类；**没有 key 时抛 RuntimeError**（不算错误配置，
    只是这条兜底路没开，渲染层会照常标「未获取」）。
    """
    api_key = key if key is not None else deepseek_key()
    if not api_key:
        raise RuntimeError('未配置 DEEPSEEK_API_KEY，DeepSeek 检索兜底不可用')
    payload = {'query': query[:128], 'count': max(1, min(50, int(count))),
               'need_content': False}
    body = _http_post_json(DEEPSEEK_SEARCH_URL, payload,
                           headers={'Authorization': f'Bearer {api_key}'},
                           timeout=timeout or DEEPSEEK_TIMEOUT, proxy=proxy)
    results = body.get('results') or []
    out = []
    for r in results:
        title = str(r.get('title') or '').strip()
        url = str(r.get('url') or '').strip()
        if not title or not url:
            continue
        score = r.get('rerank_score')
        try:
            score = float(score) if score is not None else None
        except (TypeError, ValueError):
            score = None
        # 低分结果直接丢：宁缺毋滥，避免把 SEO 垃圾当情报
        if DEEPSEEK_MIN_SCORE and score is not None and score < DEEPSEEK_MIN_SCORE:
            continue
        out.append({
            'title': title,
            'url': url,
            'summary': str(r.get('summary') or r.get('snippet') or '').strip(),
            'publisher': str(r.get('site_name') or '').strip(),
            'published_raw': r.get('date') or '',
            'rerank_score': score,
            'authority': r.get('authority'),
        })
    return out


def gnews_url(query, lang='en'):
    """Google News RSS 检索地址（与 macro_data.gnews_url 同一写法，便于复用同款解析）。"""
    if lang.lower().startswith('zh'):
        hl, gl, ceid = 'zh-CN', 'CN', 'CN:zh-Hans'
    else:
        hl, gl, ceid = 'en-US', 'US', 'US:en'
    return (f'https://news.google.com/rss/search?q={urllib.parse.quote(query)}'
            f'&hl={hl}&gl={gl}&ceid={ceid}&when=7d')


def resolve_endpoints(src):
    """把目录条目展开成真正要请求的 URL 列表（rsshub: 占位按实例展开）。"""
    out = []
    for ep in src.get('endpoints') or []:
        if ep.startswith('rsshub:'):
            path = ep[len('rsshub:'):]
            for base in rsshub_bases():
                out.append(base + path)
        else:
            out.append(ep)
    if src.get('query'):
        out.append(gnews_url(src['query'], src.get('lang', 'en')))
    return out


def http_get(url, timeout=10, proxy=None):
    """GET → (text, latency_ms)。失败抛异常，由调用方记进 sources[].error。

    proxy: HTTP(S) 代理 URL；None 时回落到 rsshub_proxy() 的环境变量配置。
           自己显式建 opener，避免走 urllib 的全局 ProxyHandler（受 *_PROXY 环境
           变量隐式影响，测试里不好锁）。
    """
    t0 = time.time()
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    })
    proxy_url = proxy if proxy is not None else rsshub_proxy()
    handlers = []
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))   # 显式不走代理
    opener = urllib.request.build_opener(*handlers)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
    ctype = resp.headers.get('Content-Type') or ''
    charset = ctype.split('charset=')[-1].strip().strip('"\'') if 'charset=' in ctype else 'utf-8'
    try:
        text = raw.decode(charset or 'utf-8', errors='replace')
    except LookupError:
        text = raw.decode('utf-8', errors='replace')
    return text, round((time.time() - t0) * 1000, 1)


# ---------------------------------------------------------------------------
# 解析（RSS 2.0 / RSS 1.0 RDF / Atom，按本地名匹配，兼容命名空间与 CDATA）
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r'<[^>]+>')
_BLOCK_TAG_RE = re.compile(r'</?(?:p|br|div|li|ul|ol|tr|td|h[1-6]|figure|figcaption)\b[^>]*/?>', re.I)
_INLINE_TAG_RE = re.compile(r'</?(?:em|strong|b|i|u|small|mark|font|sup|sub|a|span)\b[^>]*/?>', re.I)


def strip_html(text):
    """去标签 + 实体 + 折叠空白（行内标签不留空格，块级换空格），与 macro_data 同一口径。"""
    if not text:
        return ''
    s = _BLOCK_TAG_RE.sub(' ', str(text))
    s = _INLINE_TAG_RE.sub('', s)
    s = _TAG_RE.sub(' ', s)
    for a, b in (('&nbsp;', ' '), ('&amp;', '&'), ('&quot;', '"'), ('&#39;', "'"),
                 ('&lt;', '<'), ('&gt;', '>'), ('&#8217;', '’'), ('&hellip;', '…')):
        s = s.replace(a, b)
    return re.sub(r'\s+', ' ', s).strip()


def to_utc(dt_text):
    """RFC822 / ISO8601 / 裸日期 → aware datetime(UTC)；解析不出返回 None。"""
    if not dt_text:
        return None
    s = str(dt_text).strip()
    try:
        d = parsedate_to_datetime(s)
        if d:
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    m = re.search(r'(20\d{2})-(\d{2})-(\d{2})', s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _local(tag):
    return tag.split('}')[-1] if '}' in tag else tag


def _child_text(node, names):
    """按本地名取子元素文本（含 href 属性回退），names 按优先级排列。"""
    for child in list(node):
        if _local(child.tag) in names:
            if child.text and child.text.strip():
                return child.text.strip()
            href = child.attrib.get('href')
            if href:
                return href
    return ''


def parse_feed(text):
    """RSS/Atom/RDF 报文 → 原始条目 [{title,url,publisher,published_raw,summary}]。"""
    if not text:
        return []
    body = text.strip()
    # 部分站点在 XML 前带 BOM/空白/注释，先定位第一个 '<'
    i = body.find('<')
    if i > 0:
        body = body[i:]
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []

    nodes = [n for n in root.iter() if _local(n.tag) in ('item', 'entry')]
    out = []
    for node in nodes:
        title = strip_html(_child_text(node, ('title',)))
        if not title:
            continue
        url = _child_text(node, ('link', 'guid', 'id'))
        if url and not url.startswith('http'):
            url = ''
        published_raw = _child_text(node, ('pubDate', 'published', 'updated', 'date',
                                           'dc:date', 'issued', 'created'))
        summary = strip_html(_child_text(node, ('description', 'summary', 'content',
                                               'encoded', 'content:encoded')))[:400]
        publisher = strip_html(_child_text(node, ('source', 'author', 'creator')))
        out.append({'title': title, 'url': url or '',
                    'published_raw': published_raw,
                    'summary': summary, 'publisher': publisher})
    return out


# ---------------------------------------------------------------------------
# 归一 / 时效过滤 / 排序
# ---------------------------------------------------------------------------
def split_publisher_suffix(title):
    """聚合器标题形如「正文 — 来源名」→ (标题, 来源名)。"""
    for sep in (' — ', ' – ', ' - ', ' | '):
        if sep in title:
            head, tail = title.rsplit(sep, 1)
            if head.strip() and 0 < len(tail) <= 40:
                return head.strip(), tail.strip()
    return title, ''


def normalize(raw_items, source, now=None, limit=LIMIT_PER_SOURCE_DEFAULT):
    """原始条目 → 统一条目（补 published_date / age_days / source / kind）。"""
    now = now or datetime.now(timezone.utc)
    out = []
    for r in raw_items or []:
        raw_title = (r.get('title') or '').strip()
        if not raw_title:
            continue
        title, tail = split_publisher_suffix(raw_title)
        pub = to_utc(r.get('published_raw'))
        out.append({
            'title': title[:180],
            'publisher': (r.get('publisher') or tail or '')[:80],
            'url': r.get('url') or '',
            'snippet': (r.get('summary') or '')[:240],
            'source': source['key'],
            'source_id': source['id'],
            'published_at': pub.isoformat() if pub else None,
            'published_date': pub.strftime('%Y-%m-%d') if pub else None,
            'age_days': round((now - pub).total_seconds() / 86400, 2) if pub else None,
        })
    return out[:limit] if limit else out


def filter_stale(items, max_age_days=MAX_AGE_DAYS_DEFAULT, now=None):
    """超窗条目丢弃并计数；无日期条目**保留但不当最新**（另行计数）。"""
    now = now or datetime.now(timezone.utc)
    kept, stale, undated = [], 0, 0
    for it in items:
        if not it.get('published_date'):
            undated += 1
            kept.append(it)
            continue
        age = it.get('age_days')
        if age is None:
            pub = to_utc(it.get('published_at'))
            age = (now - pub).total_seconds() / 86400 if pub else None
        if age is None or age > max_age_days or age < -1:
            stale += 1
            continue
        it['age_days'] = round(float(age), 2)
        kept.append(it)
    return kept, {'stale_dropped': stale, 'undated_kept': undated}


def sort_items(items):
    """带日期的按新→旧；无日期的永远垫底（不得冒充最新）。"""
    return sorted(items, key=lambda x: (
        x.get('published_date') is None,
        -(x.get('age_days') if x.get('age_days') is not None else 1e9),
    ))


def dedup_titles(items):
    seen, out = set(), []
    for it in items:
        k = re.sub(r'[\s\W_]+', '', (it.get('title') or '').lower())[:60]
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------
class _NoLock:
    """串行调用时的空锁（把「可选并发」写成同一份代码）。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_NO_LOCK = _NoLock()


def fetch_source(src, timeout=10, mock=False, now=None, limit=LIMIT_PER_SOURCE_DEFAULT,
                 rsshub_budget=None, rsshub_state=None, quiet=False, deepseek_budget=None,
                 lock=None):
    """单个源 → (items, meta)。全部端点失败抛异常，由调用方归类为 sources[].error。"""
    now = now or datetime.now(timezone.utc)
    meta = {'endpoint': '', 'kind': '', 'latency_ms': 0.0, 'attempts': []}
    t0 = time.time()

    if mock:
        path = os.path.join(FIXTURE_DIR, FIXTURE_NAME)
        with open(path, encoding='utf-8') as f:
            fixture = json.load(f)
        raw = (fixture.get('sources') or {}).get(src['key']) or []
        raw = _redate_for_demo(raw, now)
        meta.update({'endpoint': f'fixture:{FIXTURE_NAME}', 'kind': 'fixture',
                     'latency_ms': round((time.time() - t0) * 1000, 1)})
        return normalize(raw, src, now=now, limit=limit), meta

    errors = []
    for url in resolve_endpoints(src):
        is_rsshub = _is_rsshub_url(url)
        if is_rsshub and rsshub_budget is not None:
            with (lock or _NO_LOCK):
                over = rsshub_budget[0] <= 0
            if over:
                errors.append(f'{url}: 本轮 RSSHub 请求数已达上限，跳过')
                continue
        if is_rsshub and rsshub_state is not None:
            with (lock or _NO_LOCK):
                tripped = bool(rsshub_state.get('tripped'))
            if tripped:
                errors.append(f'{url}: 本轮 RSSHub 已熔断（多实例均被拦），跳过')
                continue
        try:
            text, ms = http_get(url, timeout)
            if is_rsshub and rsshub_budget is not None:
                with (lock or _NO_LOCK):
                    rsshub_budget[0] -= 1
            raw = parse_feed(text)
            if not raw:
                raise ValueError(f'报文非 RSS/Atom 或没有条目（{len(text)} 字节）')
            meta.update({'endpoint': url, 'kind': _kind_of(url, src),
                         'latency_ms': round((time.time() - t0) * 1000, 1)})
            meta['attempts'] = errors
            # 记下「这次是靠换实例救回来的」，便于排查 IP 拦截。
            # 注意不清空 failed_bases：那是本轮「出口被拦」的累计证据，清掉就再也
            # 攒不到熔断阈值；单个源救回来 ≠ 出口重新可信。
            if is_rsshub and errors and rsshub_state is not None:
                with (lock or _NO_LOCK):
                    rsshub_state['rescued'] = rsshub_state.get('rescued', 0) + 1
            return normalize(raw, src, now=now, limit=limit), meta
        except Exception as e:  # noqa: BLE001 — 端点失败继续试下一个，全部失败才抛
            errors.append(f'{url}: {type(e).__name__}: {e}'[:220])
            # 边缘按 IP 拦截（403/429）才计入熔断：这是「这个出口不让 RSSHub 进」，
            # 换实例/换出口可能有用。路由自身坏死（200 错误页、400/404/500）换实例
            # 也没用，不计入 —— 否则 WSJ 上游 401 这类会把整条 RSSHub 通道误判死。
            if is_rsshub and rsshub_state is not None and _is_edge_block(e):
                inst = _instance_of(url)
                with (lock or _NO_LOCK):
                    blocked = rsshub_state.setdefault('failed_bases', [])
                    if inst and inst not in blocked:
                        blocked.append(inst)
                    tripped_now = len(blocked) >= rsshub_state.get('trip_after', 3)
                    if tripped_now:
                        rsshub_state['tripped'] = True
                if tripped_now:
                    if not quiet:
                        print(f'  ⚠️ RSSHub：已有 {len(blocked)} 个实例被边缘按 IP 拦截（403/429），'
                              '本轮停止使用 RSSHub → 05 节按目录回退原生 RSS / 检索载体',
                              file=sys.stderr)
    # ---- 检索兜底：真实端点全挂了（路由坏死 / 站点端点消失 / 被边缘拦）时，
    # 用 DeepSeek Search API 按 site: 锁定站点检索，抢救「今天这个源说了什么」。
    # 只对目录里显式声明了 query 的源启用（catalog['deepseek_fallback']），
    # 且必须有 key —— 没有就照旧如实标「未获取」，绝不伪造。
    if src.get('deepseek_fallback') and deepseek_enabled():
        with (lock or _NO_LOCK):
            ds_left = None if deepseek_budget is None else deepseek_budget[0]
        if ds_left is not None and ds_left <= 0:
            errors.append('DeepSeek 检索：本轮调用次数已达上限，跳过')
        else:
            try:
                query = build_deepseek_query(src, now=now)
                raw = deepseek_search(query, count=max(5, limit * 3), now=now)
                if not raw:
                    raise ValueError('检索无结果')
                if deepseek_budget is not None:
                    with (lock or _NO_LOCK):
                        deepseek_budget[0] -= 1
                meta.update({'endpoint': f'{DEEPSEEK_SEARCH_URL}?q={query}',
                             'kind': 'deepseek',
                             'latency_ms': round((time.time() - t0) * 1000, 1)})
                meta['attempts'] = errors + [f'DeepSeek 检索式: {query}']
                if rsshub_state is not None:
                    with (lock or _NO_LOCK):
                        rsshub_state['deepseek_rescued'] = \
                            rsshub_state.get('deepseek_rescued', 0) + 1
                return normalize(raw, src, now=now, limit=limit), meta
            except Exception as e:  # noqa: BLE001 — 兜底也失败就照旧标未获取
                errors.append(f'DeepSeek 检索: {type(e).__name__}: {e}'[:220])
    raise RuntimeError('; '.join(errors)[:400] or '无可用端点')


def _kind_of(url, src):
    if url.startswith(tuple(rsshub_bases())):
        return 'rsshub'
    if src.get('query') and 'news.google.com' in url:
        return 'gnews'
    if 'news.google.com' in url:
        return 'gnews'
    return 'native'


def _redate_for_demo(raws, now):
    """mock 回放：日期改写成「今天往前第 n 天」，避免演示数据被时效窗口清空。"""
    out = []
    for i, r in enumerate(raws):
        r = dict(r)
        r['published_raw'] = (now - timedelta(days=min(i, 6))).strftime('%Y-%m-%d %H:%M:%S')
        out.append(r)
    return out


def build(max_age_days=MAX_AGE_DAYS_DEFAULT, limit=LIMIT_PER_SOURCE_DEFAULT, timeout=10,
          mock=False, quiet=False, deadline=None, only=None, rsshub_max=RSSHUB_MAX_PER_RUN,
          deepseek_max=DEEPSEEK_MAX_PER_RUN):
    """抓全部源 → 归一 → 时效过滤 → 分板块，返回 rssfeeds data dict。

    deadline: 可选墙钟截止（秒）；超时的源记 skipped_deadline，不把构建拖死。
    only:     可选 key 列表（联调用，例如 --limit 3）。
    """
    now = datetime.now(timezone.utc)
    targets = [c for c in CATALOG if (not only or c['key'] in only)]
    groups = {k: {'label': label, 'desc': GROUP_DESCS[k], 'sources': []} for k, label, _d in GROUPS}
    total_kept = total_stale = total_undated = 0
    # 本轮 RSSHub 通道状态：多实例轮换 + 连续被拦熔断（跨源共享，避免 403 风暴）
    rsshub_state = {'failed_bases': [], 'tripped': False, 'rescued': 0,
                    'deepseek_rescued': 0, 'trip_after': max(1, int(RSSHUB_TRIP_AFTER))}
    deepseek_budget = [max(0, int(deepseek_max))] if not mock else None

    lock = threading.Lock() if (not mock and RSS_CONCURRENCY > 1) else None

    def run_one(src, rsshub_budget):
        """抓一个源 → (meta, kept)。异常在内部归类，绝不外抛（与串行版同一语义）。"""
        meta = {'id': src['id'], 'key': src['key'], 'name': src['name'], 'lang': src['lang'],
                'site': src.get('site', ''), 'catalog_kind': src.get('verified'),
                'note': src.get('note', ''), 'ok': False, 'kind': '', 'endpoint': '',
                'items': 0, 'error': None, 'latency_ms': 0.0}
        if deadline is not None and time.time() >= deadline:
            meta['error'] = 'skipped_deadline: 超出本轮抓取的墙钟预算，未请求'
            if not quiet:
                print(f'  ⏱️ {src["key"]} 跳过：超出时间预算', file=sys.stderr)
            return meta, []
        try:
            items, fmeta = fetch_source(src, timeout=timeout, mock=mock, now=now,
                                        limit=limit, rsshub_budget=rsshub_budget,
                                        rsshub_state=rsshub_state, quiet=quiet,
                                        deepseek_budget=deepseek_budget, lock=lock)
            kept, dropped = filter_stale(items, max_age_days=max_age_days, now=now)
            kept = sort_items(dedup_titles(kept))
            meta.update({k: v for k, v in fmeta.items() if k != 'attempts'})
            meta.update({'ok': True, 'items': len(kept),
                         'attempts': fmeta.get('attempts') or []})
            if not quiet:
                print(f'  ✅ {src["key"]: <16} {len(kept):>2} 条  [{meta["kind"]}] {meta["endpoint"]}')
            return meta, kept, (len(kept), dropped['stale_dropped'], dropped['undated_kept'])
        except Exception as e:  # noqa: BLE001 — 单源失败不阻断（与行情/社区/快讯同一策略）
            meta['error'] = f'{type(e).__name__}: {e}'[:400]
            if not quiet:
                print(f'  ⚠️ {src["key"]} 失败: {meta["error"][:160]}（跳过，不影响其余源）',
                      file=sys.stderr)
            return meta, [], (0, 0, 0)

    # RSSHub 请求预算每源独立一份（与串行版口径一致：--rsshub-max 是「单源上限」）
    budgets = [None if mock else [rsshub_max] for _ in targets]
    workers = 1 if mock else max(1, min(int(RSS_CONCURRENCY or 1), len(targets)))
    if workers > 1:
        # 并发只在网络 I/O 上并行；共享的熔断/预算状态经同一把锁读写。
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_one, src, budgets[i]) for i, src in enumerate(targets)]
            results = [f.result() for f in futures]
    else:
        results = [run_one(src, budgets[i]) for i, src in enumerate(targets)]

    for src, res in zip(targets, results):
        meta = res[0]
        kept = res[1] if len(res) > 1 else []
        stat = res[2] if len(res) > 2 else (0, 0, 0)
        total_kept += stat[0]
        total_stale += stat[1]
        total_undated += stat[2]
        groups[src['group']]['sources'].append({**meta, 'items_list': kept})

    ok = sum(1 for g in groups.values() for s in g['sources'] if s['ok'])
    kinds = {}
    for g in groups.values():
        for s in g['sources']:
            if s['ok']:
                kinds[s['kind']] = kinds.get(s['kind'], 0) + 1
    return {
        'generated_at': now.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'fetch_date': now.strftime('%Y-%m-%d'),
        'mode': 'mock' if mock else 'live',
        'window': {'max_age_days': max_age_days,
                   'since': (now - timedelta(days=max_age_days)).strftime('%Y-%m-%d')},
        'limit_per_source': limit,
        'groups': groups,
        'summary': {
            'ok': ok,
            'total': len(targets),
            'catalog_total': len(CATALOG),
            'items': total_kept,
            'stale_dropped': total_stale,
            'undated_kept': total_undated,
            'by_kind': kinds,
            'deepseek': {'enabled': deepseek_enabled(),
                         'rescued': int(rsshub_state.get('deepseek_rescued', 0)),
                         'calls_left': int(deepseek_budget[0]) if deepseek_budget else 0},
            'rsshub': {'instances': len(rsshub_bases()),
                       'tripped': bool(rsshub_state['tripped']),
                       'rescued': int(rsshub_state.get('rescued', 0)),
                       'proxy': bool(rsshub_proxy())},
            'per_group': {k: {'ok': sum(1 for s in v['sources'] if s['ok']),
                              'total': len(v['sources']),
                              'items': sum(len(s['items_list']) for s in v['sources'])}
                          for k, v in groups.items()},
        },
        'notes': [
            f'由 rss_feeds.py 构建时抓取（目录 {len(CATALOG)} 源：原生 RSS / RSSHub 路由 / '
            f'Google News 检索载体），只保留 {max_age_days} 天内且发布日期可解析的条目',
            '单源失败只标「未获取」，不回填历史标题；无日期条目不当最新，渲染时明确标注',
            'RSSHub 是第三方公共服务：默认按 RSSHUB_BASE 的实例链轮换（官方 + 3 个探活镜像），'
            '实例被边缘按 IP 拦截（403/429）时自动换下一个；连续多个实例被拦即本轮熔断，'
            '按目录顺序回退原生 RSS / 检索载体，不会把构建拖死',
            '对外不展示失败堆栈；逐源原因只留在 sources[].error 供 CI 排查',
        ],
    }


# ---------------------------------------------------------------------------
# 可用性判定（与 macro_data.availability 同构：网页 05 节与微信 05 栏共用一个事实源）
# ---------------------------------------------------------------------------
def availability(data, now=None, snapshot_max_age_days=SNAPSHOT_MAX_AGE_DAYS):
    """判定 05 节这次能不能正常渲染。

    reason: no_file / bad_type / no_items / stale_snapshot / ok
    """
    now = now or datetime.now(timezone.utc)
    if data is not None and not isinstance(data, dict):
        return {'ok': False, 'reason': 'bad_type', 'items': 0, 'snapshot_age_days': None,
                'detail': 'rssfeeds_data.json 内容不是预期的对象结构（产物可能写坏或被截断）'}
    if not data:
        return {'ok': False, 'reason': 'no_file', 'items': 0, 'snapshot_age_days': None,
                'detail': '未读到 rssfeeds_data.json（构建步骤未执行，或产物未生成）'}

    groups = data.get('groups') or {}
    items = sum(len(((s or {}).get('items_list') or [])) for g in groups.values()
                for s in ((g or {}).get('sources') or []))
    summ = data.get('summary') or {}
    ok_src, total_src = summ.get('ok'), summ.get('total')

    age = None
    stamp = (data.get('generated_at') or '').strip()
    m = re.match(r'(20\d{2}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})', stamp)
    if m:
        try:
            built = datetime.strptime(f'{m.group(1)} {m.group(2)}',
                                      '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            age = (now - built).total_seconds() / 86400
        except ValueError:
            age = None
    if age is None and re.match(r'20\d{2}-\d{2}-\d{2}$', str(data.get('fetch_date') or '')):
        built = datetime.strptime(data['fetch_date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        age = (now - built).total_seconds() / 86400
    age = round(age, 2) if age is not None else None

    if not items:
        src = (f'当次 {ok_src}/{total_src} 个源可用，'
               if ok_src is not None and total_src is not None else '')
        return {'ok': False, 'reason': 'no_items', 'items': 0, 'snapshot_age_days': age,
                'detail': f'{src}窗口内没有任何可核验的 RSS 条目'}

    if age is not None and age > snapshot_max_age_days:
        return {'ok': False, 'reason': 'stale_snapshot', 'items': items, 'snapshot_age_days': age,
                'detail': (f'读到的是 {age:.1f} 天前构建的旧快照'
                           f'（{stamp or data.get("fetch_date")}），超过 '
                           f'{snapshot_max_age_days} 天新鲜度上限 —— 当次抓取未执行或失败，'
                           f'{items} 条旧条目按兜底口径不予展示')}

    return {'ok': True, 'reason': 'ok', 'items': items, 'snapshot_age_days': age, 'detail': ''}


# ---------------------------------------------------------------------------
# 构建期补抓（与 macro_data.ensure 同一套策略，不依赖 workflow 权限）
# ---------------------------------------------------------------------------
AUTO_FETCH_TIMEOUT = 6
# 构建期补抓的墙钟预算：50s 覆盖「并发 8 路 × 单源 6s 超时」的常态；原先 90s 会在
# CI 里把 49 源拖成 31 源「未请求」的假缺口（2026-10-31 两次运行的线上事实）。
AUTO_FETCH_BUDGET_SECONDS = 50
REFETCH_REASONS = ('no_file', 'bad_type', 'stale_snapshot')


def auto_fetch_enabled(explicit=None):
    """显式入参 > 环境变量 RSSFEEDS_AUTO_FETCH > 「在 CI 里就开」（与 02 栏同口径）。"""
    if explicit is not None:
        return bool(explicit)
    raw = str(os.environ.get('RSSFEEDS_AUTO_FETCH', '')).strip().lower()
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    if raw in ('0', 'false', 'no', 'off'):
        return False
    return bool(os.environ.get('GITHUB_ACTIONS'))


def load_json(path=DEFAULT_OUT):
    """安全读产物：不存在/读不出 → {}；非 dict 原样返回交给 availability 报 bad_type。"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def ensure(path=DEFAULT_OUT, max_age_days=MAX_AGE_DAYS_DEFAULT, limit=LIMIT_PER_SOURCE_DEFAULT,
           timeout=AUTO_FETCH_TIMEOUT, mock=False, quiet=True, now=None, allow_fetch=None,
           budget_seconds=AUTO_FETCH_BUDGET_SECONDS):
    """读取 rssfeeds_data.json；不可用且允许联网时就地补抓一次并落盘。永不抛异常。"""
    t0 = time.time()
    now = now or datetime.now(timezone.utc)
    data = load_json(path)
    avail = availability(data, now=now)
    out = {'data': data if isinstance(data, dict) else {}, 'action': 'fresh',
           'reason': avail['reason'], 'items': avail['items'], 'fetched': False,
           'elapsed_ms': 0, 'path': path}
    if avail['ok']:
        return out
    if avail['reason'] not in REFETCH_REASONS:
        out['action'] = 'no_refetch'      # no_items：当次已抓过但窗口内 0 条
        return out
    if not auto_fetch_enabled(allow_fetch):
        out['action'] = 'disabled'
        return out

    try:
        fresh = build(max_age_days=max_age_days, limit=limit, timeout=timeout, mock=mock,
                      quiet=quiet, deadline=t0 + max(1, int(budget_seconds)))
    except Exception as e:  # noqa: BLE001 — 补抓失败绝不阻断构建
        out.update({'action': 'error', 'detail': f'{type(e).__name__}: {e}'[:200],
                    'elapsed_ms': round((time.time() - t0) * 1000, 1)})
        print(f'  ⚠️ RSS 情报源构建期补抓失败（{out["detail"]}）→ 维持「今日未获取」兜底',
              file=sys.stderr)
        return out

    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(fresh, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f'  ⚠️ RSS 情报源补抓产物写入失败（{e}）→ 本次仍用内存结果渲染', file=sys.stderr)

    avail2 = availability(fresh, now=now)
    out.update({'data': fresh, 'reason': avail2['reason'], 'items': avail2['items'],
                'fetched': True, 'elapsed_ms': round((time.time() - t0) * 1000, 1),
                'action': 'fetched' if avail2['ok'] else 'refetch_unavailable'})
    return out


# ---------------------------------------------------------------------------
# 文本摘要
# ---------------------------------------------------------------------------
def text_report(data):
    """CI Step Summary 用的人读摘要。"""
    lines = [f'📡 RSS 情报源 {data.get("fetch_date") or "—"} · '
             f'{data.get("generated_at") or "—"} · 模式 {data.get("mode") or "—"}']
    summ = data.get('summary') or {}
    lines.append(f'  源 {summ.get("ok", 0)}/{summ.get("total", 0)} 可用 · '
                 f'条目 {summ.get("items", 0)} 条 · 拦截超窗 {summ.get("stale_dropped", 0)} 条 · '
                 f'无日期保留 {summ.get("undated_kept", 0)} 条 · 按类型 {summ.get("by_kind") or {}}')
    ds = summ.get('deepseek') or {}
    if ds.get('enabled') or ds.get('rescued'):
        state = '已启用' if ds.get('enabled') else '未启用（无 DEEPSEEK_API_KEY）'
        lines.append(f'  ◆ DeepSeek 检索兜底 {state} · 本轮救回 {ds.get("rescued", 0)} 源 · '
                     f'剩余调用额度 {ds.get("calls_left", 0)}')
    rh = summ.get('rsshub') or {}
    if rh:
        state = '已熔断（多实例被拦 → 只走原生/检索回退）' if rh.get('tripped') else '正常'
        lines.append(f'  ◆ RSSHub 通道 {state} · 实例 {rh.get("instances", 0)} 个 · '
                     f'换实例救回 {rh.get("rescued", 0)} 源'
                     + (' · 经代理出口' if rh.get('proxy') else ''))
    for gkey, group in (data.get('groups') or {}).items():
        srcs = group.get('sources') or []
        ok = [s for s in srcs if s.get('ok')]
        lines.append(f'  ◆ {group.get("label") or gkey} —— {len(ok)}/{len(srcs)} 源可用')
        for s in srcs:
            if s.get('ok'):
                top = (s.get('items_list') or [{}])[0]
                lines.append(f'    ✅ {s["id"]:>2}. {s["name"]} [{s.get("kind")}] '
                             f'{s.get("items", 0)} 条 · 最新 '
                             f'{(top.get("published_date") or "日期未标注")}：'
                             f'{(top.get("title") or "")[:60]}')
            else:
                lines.append(f'    ⚠️ {s["id"]:>2}. {s["name"]} 未获取：'
                             f'{(s.get("error") or "")[:110]}')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# 框架级统计：RSS 49 源作为「第 5 路数据源」并入 01/04 的覆盖度
# ---------------------------------------------------------------------------
def stats(data):
    """把 05 节产物压成一组框架级统计（战情面板 + 覆盖度都用同一份）。

    「可用率」用 catalog_total 做分母：total 可能因为 --group 只抓了一部分而变小，
    拿它当分母会算出「抓 1 个源成功 1 个 = 100% 覆盖」这种假高覆盖。
    """
    summ = (data or {}).get('summary') or {}
    ok = int(summ.get('ok') or 0)
    total = int(summ.get('total') or 0)
    catalog_total = int(summ.get('catalog_total') or total or len(CATALOG))
    denom = max(1, catalog_total)
    by_kind = dict(summ.get('by_kind') or {})
    ds = summ.get('deepseek') or {}
    rh = summ.get('rsshub') or {}
    win = (data or {}).get('window') or {}
    return {
        'ok': ok,
        'total': total,
        'catalog_total': catalog_total,
        'failed': max(0, catalog_total - ok),
        'items': int(summ.get('items') or 0),
        'stale_dropped': int(summ.get('stale_dropped') or 0),
        'undated_kept': int(summ.get('undated_kept') or 0),
        'by_kind': by_kind,
        'native': int(by_kind.get('native') or 0),
        'rsshub': int(by_kind.get('rsshub') or 0),
        'gnews': int(by_kind.get('gnews') or 0),
        'deepseek': int(by_kind.get('deepseek') or 0),
        'deepseek_enabled': bool(ds.get('enabled')),
        'deepseek_rescued': int(ds.get('rescued') or 0),
        'deepseek_calls_left': int(ds.get('calls_left') or 0),
        'rsshub_instances': int(rh.get('instances') or 0),
        'rsshub_tripped': bool(rh.get('tripped')),
        'rsshub_rescued': int(rh.get('rescued') or 0),
        'rsshub_proxy': bool(rh.get('proxy')),
        'max_age_days': win.get('max_age_days'),
        'since': win.get('since') or '',
        'mode': (data or {}).get('mode') or '',
        'generated_at': (data or {}).get('generated_at') or '',
        # 覆盖率的分母是**目录全量**，不是本次实际抓取数：漏抓的源也必须算进分母，
        # 否则「少抓几个源」反而能把覆盖率刷回 100%。
        'ratio': round(ok / denom, 3),
    }


def coverage_signal(data):
    """第 5 路数据源的覆盖信号，喂给 panorama / forecast 的覆盖度计算。

    返回 {'available': bool, 'ratio': float, 'ok'/'total'/'items', 'why': str}。
    available=False 表示「这一路根本没拿到产物」—— 与「拿到了但质量差」必须分开，
    否则 05 节没跑起来的时候，覆盖度反而会因为它没记缺口而虚高。
    """
    if not isinstance(data, dict) or not data:
        return {'available': False, 'ratio': 0.0, 'ok': 0, 'total': 0, 'items': 0,
                'why': 'RSS 情报源（rssfeeds_data.json）未获取 —— 05 节的 49 源没有任何当次读数'}
    avail = availability(data)
    if not avail.get('ok'):
        return {'available': False, 'ratio': 0.0, 'ok': 0, 'total': 0, 'items': 0,
                'why': (f"RSS 情报源（rssfeeds_data.json）不可用（{avail.get('reason')}）"
                        ' —— 05 节的 49 源没有任何当次读数')}
    st = stats(data)
    return {'available': True, 'ratio': st['ratio'], 'ok': st['ok'],
            'total': st['catalog_total'], 'items': st['items'],
            'why': (f"RSS 情报源 {st['ok']}/{st['catalog_total']} 源可用"
                    f"（{int(st['ratio'] * 100)}%），{st['failed']} 源未获取")}


# ---------------------------------------------------------------------------
# 渲染 · 网页（05 节）
# ---------------------------------------------------------------------------
def _esc(t):
    return html_mod.escape(str(t if t is not None else ''), quote=True)


def _md_cn(date_str):
    """'2026-10-31' → '10 月 31 日'；异常输入原样返回。"""
    m = re.match(r'(20\d{2})-(\d{2})-(\d{2})$', str(date_str or ''))
    return f'{int(m.group(2))} 月 {int(m.group(3))} 日' if m else (date_str or '日期未标注')


KIND_BADGE = {'native': '原生 RSS', 'rsshub': 'RSSHub', 'gnews': '检索载体',
              'deepseek': 'DeepSeek 检索', 'fixture': '离线回放', '': '未获取'}


def build_unavailable_html(avail):
    """05 节兜底：只说明「这次没有」，绝不回填历史标题。"""
    reason = avail.get('reason') or 'no_file'
    detail = avail.get('detail') or ''
    return (
        '<div class="pub-sub">◆ 49 大财经 RSS 情报源 — 今日未获取</div>\n'
        '<div style="font-size:11.5px;line-height:1.8;color:#0a0a0a;">\n'
        '  ⚠️ 未读到 <strong>rssfeeds_data.json</strong>：抓取步骤 '
        '<code style="background:#eceef0;padding:1px 4px;">python3 rss_feeds.py</code> '
        '未执行，或当次 49 源全部不可达。<br/>\n'
        '  本栏<strong>不回填历史标题、不编造「今日重点」</strong>；恢复后每次重建都会重新抓取。\n'
        + (f'  <div class="pub-meta" style="margin-top:6px;">本次判定：{_esc(detail)}'
           f'（{_esc(reason)}）· 恢复命令：'
           '<code style="background:#eceef0;padding:1px 4px;">'
           'python3 rss_feeds.py --json rssfeeds_data.json --text</code></div>\n' if detail else '')
        + '</div>\n'
    )


def render_stats_web(data):
    """05 节的框架级统计面板：49 源作为「第 5 路数据源」并入覆盖度后，把账摊开。

    只展示已发生的读数，不做任何估算；DeepSeek / RSSHub 兜底没启用时如实标「未启用」，
    而不是省略 —— 省略会让读者以为这一路压根不存在。
    """
    st = stats(data)
    pct = int(round(st['ratio'] * 100))
    cells = [
        ('可用源', f"{st['ok']}/{st['catalog_total']}", f'{pct}%'),
        ('入库条目', f"{st['items']}", '条'),
        ('拦截超窗', f"{st['stale_dropped']}", '条'),
        ('无日期保留', f"{st['undated_kept']}", '条'),
        ('原生 RSS', f"{st['native']}", '源'),
        ('RSSHub 路由', f"{st['rsshub']}", '源'),
        ('检索载体', f"{st['gnews']}", '源'),
        ('DeepSeek 检索', f"{st['deepseek']}", '源'),
    ]
    cards = ''.join(
        f'<div style="flex:1 1 92px;min-width:92px;border:1px solid var(--border);'
        f'padding:6px 8px;background:#fff;">'
        f'<div style="font-family:var(--mono);font-size:9.5px;color:var(--muted);'
        f'letter-spacing:.04em;">{_esc(label)}</div>'
        f'<div style="font-family:var(--mono);font-size:14px;font-weight:700;">{_esc(value)}'
        f'<span style="font-size:9.5px;font-weight:400;color:var(--muted);margin-left:3px;">'
        f'{_esc(unit)}</span></div></div>'
        for label, value, unit in cells)

    ds_note = ('已启用' if st['deepseek_enabled'] else '未启用（无 DEEPSEEK_API_KEY）')
    ds_note += f" · 本轮救回 {st['deepseek_rescued']} 源"
    if st['deepseek_enabled']:
        ds_note += f" · 剩余额度 {st['deepseek_calls_left']}"
    rh_note = ('已熔断（多实例被拦）' if st['rsshub_tripped'] else '正常')
    rh_note += f" · 实例 {st['rsshub_instances']} 个 · 换实例救回 {st['rsshub_rescued']} 源"
    if st['rsshub_proxy']:
        rh_note += ' · 经代理出口'

    return (
        '<div style="margin:0 0 10px;">'
        f'<div class="pub-meta" style="margin:0 0 5px;font-weight:700;">'
        f'◆ 框架统计 · RSS 49 源已并入 01/04 的覆盖度（第 5 路数据源）</div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:5px;">{cards}</div>'
        f'<div class="pub-meta" style="margin:5px 0 0;">'
        f'◆ DeepSeek 检索兜底 {_esc(ds_note)}</div>'
        f'<div class="pub-meta" style="margin:2px 0 0;">'
        f'◆ RSSHub 通道 {_esc(rh_note)}</div>'
        '</div>')


def rss_status_line(data):
    """战情面板用的一行式统计（HUD 空间小，只放最关键的三项）。"""
    if not isinstance(data, dict) or not data or not availability(data).get('ok'):
        return 'RSS 49 源未获取 · 05 节走兜底'
    st = stats(data)
    extra = f" · DeepSeek 救回 {st['deepseek_rescued']} 源" if st['deepseek_rescued'] else ''
    extra += f" · RSSHub 救回 {st['rsshub_rescued']} 源" if st['rsshub_rescued'] else ''
    return (f"RSS {st['ok']}/{st['catalog_total']} 源（{int(st['ratio'] * 100)}%）"
            f" · {st['items']} 条{extra}")


def render_web(data):
    """网页 05 节：六大板块 × 49 源，逐源给状态 + 当次条目（日期 + 标题 + 摘要）。"""
    avail = availability(data)
    if not avail['ok']:
        return build_unavailable_html(avail)

    summ = data.get('summary') or {}
    win = data.get('window') or {}
    note = (f'49 源 · 当次可用 {summ.get("ok", 0)}/{summ.get("total", 0)} · '
            f'入库 {summ.get("items", 0)} 条 · 时效窗口 {win.get("max_age_days", "—")} 天'
            f'（{win.get("since") or "—"} 起）· 已拦截超窗 {summ.get("stale_dropped", 0)} 条'
            f' · 抓取于 {data.get("generated_at") or "—"}'
            + (' · 离线回放样本（非实盘）' if data.get('mode') == 'mock' else ''))
    parts = ['<div class="pub-box">',
             '<div class="pub-sub">◆ 全球 49 大财经 RSS 情报源 · 六大板块 · 逐源核验状态</div>',
             f'<div class="pub-meta" style="margin:2px 0 10px;">{_esc(note)}</div>',
             render_stats_web(data),
             '<div class="pub-meta" style="margin:0 0 10px;">'
             '口径：原生 RSS / RSSHub 路由（第三方公共实例，失败按目录顺序回退）/ '
             'Google News 检索载体（站点无公开订阅源时使用，如实标注，不冒充原生源）。'
             '每条自带发布日期；抓不到的源标「未获取」并给出失败类型，不回填历史标题；'
             '无日期条目只标注「日期未标注」，永远不排在带日期条目之前。</div>']

    for gkey, group in (data.get('groups') or {}).items():
        srcs = group.get('sources') or []
        ok_n = sum(1 for s in srcs if s.get('ok'))
        item_n = sum(len(s.get('items_list') or []) for s in srcs)
        parts.append(
            f'<div class="pub-sub" style="margin-top:14px;">◆ {_esc(group.get("label") or gkey)}'
            f' <span style="font-family:var(--mono);font-size:10.5px;color:var(--muted);">'
            f'可用 {ok_n}/{len(srcs)} 源 · {item_n} 条</span></div>')
        if group.get('desc'):
            parts.append(f'<div class="pub-meta" style="margin:0 0 8px;">{_esc(group["desc"])}</div>')
        rows = []
        for s in srcs:
            badge = KIND_BADGE.get(s.get('kind') or '', '未获取')
            head = (f'<div class="pub-card-head" style="margin-bottom:4px;">'
                    f'<span class="pub-name">{s.get("id")}. {_esc(s.get("name"))}</span>'
                    f'<span class="pub-chip">{_esc(badge)}</span></div>')
            if s.get('ok'):
                items = s.get('items_list') or []
                if not items:
                    body = ('<p class="pub-quote" style="color:var(--muted);">'
                            '当次抓取成功，但时效窗口内没有可核验条目（不回填旧标题）。</p>')
                else:
                    lis = []
                    for it in items:
                        date = it.get('published_date')
                        date_html = (f'<strong style="color:var(--cyan);">[{_esc(_md_cn(date))}]</strong>'
                                     if date else
                                     '<span style="color:var(--muted);">[日期未标注]</span>')
                        title = _esc(it.get('title'))
                        url = it.get('url') or ''
                        title_html = (f'<a href="{_esc(url)}" target="_blank" '
                                      f'rel="noopener noreferrer">{title}</a>') if url.startswith('http') else title
                        snip = (f' <span style="color:var(--muted);font-size:11px;">'
                                f'{_esc((it.get("snippet") or "")[:70])}</span>'
                                if it.get('snippet') else '')
                        pub = (f' <span style="color:var(--muted);font-size:10.5px;">'
                               f'· {_esc(it.get("publisher"))}</span>' if it.get('publisher') else '')
                        lis.append(f'    <li>{date_html} {title_html}{pub}{snip}</li>')
                    body = '<ul class="pixel-list" style="margin:4px 0 0;">\n' + '\n'.join(lis) + '\n  </ul>'
                meta = (f'<div class="pub-meta">当次实抓 · 端点 {_esc((s.get("endpoint") or "")[:90])}'
                        f' · {float(s.get("latency_ms") or 0):.0f} ms · {_esc(s.get("note") or "")}</div>')
            else:
                body = ('<p class="pub-quote" style="color:var(--muted);">'
                        '当次未获取 —— 站点风控/实例限流/端点变更均可能导致；'
                        '本栏不回填历史标题，也不伪造「今日重点」。</p>')
                meta = (f'<div class="pub-meta">未获取原因：{_esc((s.get("error") or "")[:200])}'
                        f' · {_esc(s.get("note") or "")}</div>')
            rows.append(f'<article class="pub-card">{head}{body}{meta}</article>')
        parts.append('\n'.join(rows))
    parts.append('</div>')
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# 渲染 · 微信（05 栏，纯内联样式的紧凑清单，按预算逐级收敛且一源不删）
# ---------------------------------------------------------------------------
def wechat_levels(data):
    """微信 05 栏的候选渲染版本（从详到略）。每一版都保留全部 49 源与可用/未获取标记。"""
    return [render_wechat(data, level=lvl)
            for lvl in ('full', 'standard', 'roster', 'compact')]


def render_wechat(data, level='standard', cyan='#4fe5ff', text='#edf2ff',
                  muted='#9aa6c3', danger='#ff6b7d'):
    """微信 05 栏：逐源一行（状态 + 最新条目），按 level 决定详略。

    level:
      full      六大板块分组 + 每源标题 + 摘要 + 另 N 条（最详，网页版信息量的压缩）
      standard  六大板块分组 + 逐源一行（日期 + 标题 + 载体标记）
      roster    板块内每源只留「编号 名称 — 日期 标题」（不带载体标记）
      compact   不分板块、逐源一行（编号 名称 · 日期 标题），正文最短但**一源不删**，
                供 03/04 栏吃掉预算时兜底

    任何一版都不回填历史标题：未获取源只写「未获取」(+ 一句失败类型)；
    无日期条目写「日期未标注」，绝不冒充最新。
    """
    avail = availability(data)
    head = (f'<div style="color:{cyan};font-weight:700;font-size:13px;margin-bottom:4px;">'
            '◆ 全球 49 大财经 RSS 情报源 · 六大板块</div>')
    if not avail['ok']:
        return (head + f'<div style="color:{muted};font-size:11px;line-height:1.8;">'
                f'⚠️ 今日未获取：{_esc(avail.get("detail") or avail.get("reason") or "")}'
                '　本栏不回填历史标题，也不编造「今日重点」；恢复后每次构建重新抓取。</div>')

    summ = data.get('summary') or {}
    win = data.get('window') or {}
    note = (f'<div style="color:{muted};font-size:10px;line-height:1.7;">'
            f'49 源 · 可用 {summ.get("ok", 0)}/{summ.get("total", 0)} · 入库 '
            f'{summ.get("items", 0)} 条 · 窗口 {win.get("max_age_days", "—")} 天 · '
            f'抓取于 {_esc(data.get("generated_at") or "—")}'
            + (' · 离线回放样本' if data.get('mode') == 'mock' else '')
            + '　逐源核验：原生 RSS / RSSHub 路由 / 检索载体 / DeepSeek 检索兜底；'
              '失败源如实标「未获取」</div>')
    compact = (level == 'compact')
    rows = [head if not compact else
            f'<div style="color:{cyan};font-weight:700;font-size:12px;margin-bottom:3px;">'
            '◆ 49 大财经 RSS 情报源（缩略版）</div>',
            note]
    for gkey, group in (data.get('groups') or {}).items():
        srcs = group.get('sources') or []
        ok_n = sum(1 for s in srcs if s.get('ok'))
        if not compact:
            rows.append(f'<div style="color:{cyan};font-size:11.5px;font-weight:700;'
                        f'margin:8px 0 2px;">{_esc(group.get("label") or gkey)}'
                        f'（{ok_n}/{len(srcs)}）</div>')
        for s in srcs:
            name = _esc(s.get('name'))
            if not s.get('ok'):
                if level in ('roster', 'compact'):
                    rows.append(f'<div style="color:{muted};font-size:10.5px;line-height:1.65;">'
                                f'{s.get("id")}. {name} — 未获取</div>')
                else:
                    rows.append(f'<div style="color:{muted};font-size:10.5px;line-height:1.7;">'
                                f'{s.get("id")}. {name} — '
                                f'<span style="color:{danger};">未获取</span>'
                                f'（{_esc((s.get("error") or "")[:60])}）</div>')
                continue
            items = s.get('items_list') or []
            top = items[0] if items else None
            if not top:
                rows.append(f'<div style="color:{muted};font-size:10.5px;line-height:1.65;">'
                            f'{s.get("id")}. {name} — 抓取成功但窗口内 0 条</div>')
                continue
            d = top.get('published_date')
            date_txt = _md_cn(d) if d else '日期未标注'
            if compact:
                rows.append(f'<div style="color:{text};font-size:10px;line-height:1.6;">'
                            f'{s.get("id")}. {name} · {_esc(date_txt)} · '
                            f'{_esc((top.get("title") or "")[:26])}</div>')
                continue
            if level == 'roster':
                title = _esc((top.get('title') or '')[:34])
                rows.append(f'<div style="color:{text};font-size:10.5px;line-height:1.65;">'
                            f'{s.get("id")}. {name} <span style="color:{cyan};">[{_esc(date_txt)}]</span> '
                            f'{title}</div>')
                continue
            extra = ''
            if level == 'full':
                extra = (f'<br/><span style="color:{muted};font-size:9.5px;">　'
                         f'{_esc((top.get("snippet") or "")[:56])}</span>'
                         if top.get('snippet') else '')
                if len(items) > 1:
                    extra += (f'<br/><span style="color:{muted};font-size:9.5px;">　另 '
                              f'{len(items) - 1} 条：'
                              + ' / '.join(_esc((i.get('title') or '')[:22]) for i in items[1:3])
                              + '</span>')
            rows.append(f'<div style="color:{text};font-size:10.5px;line-height:1.7;">'
                        f'{s.get("id")}. {name} '
                        f'<span style="color:{cyan};">[{_esc(date_txt)}]</span> '
                        f'{_esc((top.get("title") or "")[:40])}'
                        f'<span style="color:{muted};font-size:9.5px;"> · '
                        f'{_esc(KIND_BADGE.get(s.get("kind") or "", "未获取"))}</span>{extra}</div>')
    return '\n'.join(rows)


# ---------------------------------------------------------------------------
# 自检 / CLI
# ---------------------------------------------------------------------------
def self_test():
    """离线自检：目录结构 + 解析 + 时效 + 渲染（零联网）。"""
    problems = list(catalog_errors())
    # 解析：RSS2 / Atom / RDF 三种报文
    rss2 = ('<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
            '<item><title>恒指收涨 — 智通财经</title><link>https://e.com/1</link>'
            '<pubDate>Fri, 30 Oct 2026 03:00:00 +0000</pubDate>'
            '<description>&lt;p&gt;南向资金净流入&lt;/p&gt;</description></item>'
            '</channel></rss>')
    atom = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>a</title>'
            '<entry><title>Atom 条目</title><link href="https://e.com/2"/>'
            '<published>2026-10-30T03:00:00Z</published><summary>摘要</summary></entry></feed>')
    rdf = ('<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
           'xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<item><title>RDF 条目</title><link>https://e.com/3</link>'
           '<dc:date>2026-10-29T10:00:00Z</dc:date></item></rdf:RDF>')
    a, b, c = parse_feed(rss2), parse_feed(atom), parse_feed(rdf)
    if len(a) != 1 or a[0]['url'] != 'https://e.com/1':
        problems.append('RSS2 解析异常')
    if '南向资金净流入' not in a[0]['summary']:
        problems.append('RSS2 摘要去标签异常')
    if len(b) != 1 or b[0]['published_raw'] != '2026-10-30T03:00:00Z':
        problems.append('Atom 解析异常（link href / published）')
    if len(c) != 1 or to_utc(c[0]['published_raw']) is None:
        problems.append('RDF/dc:date 解析异常')
    if parse_feed('<not xml') != []:
        problems.append('坏报文未安全返回空列表')
    # 时效：超窗丢弃、无日期保留但不当最新
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    items = [
        {'title': '新', 'published_date': '2026-10-31', 'age_days': 0.1},
        {'title': '旧', 'published_date': '2026-01-01', 'age_days': 300.0},
        {'title': '无日期', 'published_date': None, 'age_days': None},
    ]
    kept, dropped = filter_stale(items, max_age_days=14, now=now)
    if [i['title'] for i in sort_items(kept)] != ['新', '无日期']:
        problems.append('时效过滤/无日期垫底排序异常')
    if dropped != {'stale_dropped': 1, 'undated_kept': 1}:
        problems.append('时效过滤计数异常')
    # 渲染：缺产物 → 兜底，且不出现任何历史标题
    html = render_web({})
    if '今日未获取' not in html or 'rssfeeds_data.json' not in html:
        problems.append('缺产物时 05 节兜底文案异常')
    # 可用性判定：四种形态
    if availability({})['reason'] != 'no_file':
        problems.append('availability(no_file) 判定异常')
    if availability({'groups': {}, 'summary': {}})['reason'] != 'no_items':
        problems.append('availability(no_items) 判定异常')
    if availability([])['reason'] != 'bad_type':
        problems.append('availability(bad_type) 判定异常')
    old = {'generated_at': '2020-01-01 00:00:00 UTC',
           'groups': {'macro': {'sources': [{'items_list': [{'title': 'x'}]}]}},
           'summary': {'ok': 1, 'total': 1}}
    if availability(old, now=now)['reason'] != 'stale_snapshot':
        problems.append('availability(stale_snapshot) 判定异常')
    if problems:
        print('❌ 自检未通过：')
        for p in problems:
            print('  -', p)
        return 1
    print(f'✅ 自检通过：目录 {len(CATALOG)} 源 / {len(GROUPS)} 板块 '
          f'（{", ".join(f"{k}:{v}" for k, v in GROUP_EXPECTED.items())}）· '
          f'解析 3 种报文 · 时效与无日期垫底 · 05 节兜底 · 可用性四态')
    return 0


def print_catalog():
    for gkey, label, desc in GROUPS:
        print(f'\n=== {label} —— {desc}')
        for c in CATALOG:
            if c['group'] != gkey:
                continue
            eps = c.get('endpoints') or []
            show = eps[0] if eps else f'gnews:{c.get("query")}'
            print(f'  {c["id"]:>2}. {c["name"]:<28} [{c["verified"]:<11}] {show}')
            for extra in eps[1:]:
                print(f'      ↳ 回退 {extra}')


def render_catalog_md():
    """把目录渲染成人读 Markdown（docs/rss-49-feeds.md 的唯一生成器，禁止手改）。"""
    lines = [
        '# 49 大财经 RSS 情报源 · 目录与端点核对表',
        '',
        '> 本文件由 `python3 rss_feeds.py --catalog-md docs/rss-49-feeds.md` 生成，',
        '> 请勿手改 —— 目录的唯一事实源是 `rss_feeds.py` 的 `CATALOG`（`tests/test_rss_feeds.py` 会断言两者一致）。',
        '',
        '**核验状态**：`native` 站点官方公开 RSS · `rsshub_doc` RSSHub 官方路由表里有此路由（路径按路由源码核对）·',
        '`needs_check` 地址来自公开资料、站点改版可能失效 · `carrier` 站点无公开 RSS，用 Google News 检索载体（如实标注，不冒充原生源）。',
        '',
        '**端点优先级**：按列表顺序回退；`rsshub:/xxx` 会按 `RSSHUB_BASE` 的实例链展开'
        '（官方 `rsshub.app` + 3 个探活镜像，逗号分隔、按序回退）。',
        '',
        '**RSSHub 出口说明**：GitHub Actions 的出口 IP 会被 `rsshub.app` 前的 Cloudflare 直接回 403'
        '（同一路线在其它出口是 200），因此默认挂多实例轮换：403/429 视为「边缘按 IP 拦截」→ 自动换下一个实例；'
        '连续多个实例被拦则本轮熔断，05 节按目录回退原生 RSS / 检索载体，不会把构建拖死。'
        '换出口的另一种方式是用 `RSSHUB_PROXY` 指定出站代理。'
        '注意区分：403 可换实例救回；**200 + RSSHub 错误页**（上游 401、需要浏览器）属路由自身坏死，换实例无效。',
        '',
        f'合计 **{len(CATALOG)} 源**，六大板块：'
        + ' · '.join(f'{GROUP_LABELS[g]} {GROUP_EXPECTED[g]} 源' for g, _l, _d in GROUPS),
        '',
    ]
    for gkey, label, desc in GROUPS:
        lines += [f'## {label}（{GROUP_EXPECTED[gkey]} 源）', '', f'_{desc}_', '',
                  '| # | 源 | 语种 | 主端点 | 回退端点 | 核验 | 说明 |', '|---|---|---|---|---|---|---|']
        for c in CATALOG:
            if c['group'] != gkey:
                continue
            eps = c.get('endpoints') or []
            if not eps:
                main_ep = f'`gnews` 检索：{c.get("query")}'
                rest = '—'
            else:
                main_ep = f'`{eps[0]}`'
                rest = '<br>'.join(f'`{e}`' for e in eps[1:]) or '—'
            lang = {'en': '英', 'zh-CN': '简中', 'zh-HK': '繁中'}.get(c['lang'], c['lang'])
            lines.append(f'| {c["id"]} | {c["name"]} | {lang} | {main_ep} | {rest} | '
                         f'`{c["verified"]}` | {c["note"]} |')
        lines.append('')
    lines += ['## 用法', '', '```bash',
              'python3 rss_feeds.py                     # 抓取 49 源 → rssfeeds_data.json',
              'python3 rss_feeds.py --catalog           # 终端打印目录',
              'python3 rss_feeds.py --catalog-md docs/rss-49-feeds.md   # 重新生成本文件',
              'python3 rss_feeds.py --only CLS,JIN10 --text             # 联调：只抓指定源',
              'python3 rss_feeds.py --self-test          # 目录/解析/时效/兜底自检（不联网）',
              '```', '']
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(
        description='章鱼 AI·舆情004 — 49 大财经 RSS 情报源抓取（05 节数据源）')
    ap.add_argument('--json', default=DEFAULT_OUT, help='输出 JSON 路径（默认 rssfeeds_data.json）')
    ap.add_argument('--days', type=int, default=MAX_AGE_DAYS_DEFAULT,
                    help=f'时效窗口（天），超出即丢弃；默认 {MAX_AGE_DAYS_DEFAULT}')
    ap.add_argument('--limit', type=int, default=LIMIT_PER_SOURCE_DEFAULT,
                    help=f'单源最多保留条数；默认 {LIMIT_PER_SOURCE_DEFAULT}')
    ap.add_argument('--only', default='', help='只抓指定 key（逗号分隔，联调用）')
    ap.add_argument('--timeout', type=int, default=10, help='单次请求超时秒数')
    ap.add_argument('--budget', type=int, default=0,
                    help='本轮墙钟预算（秒）：到点后剩余源记 skipped_deadline 但照常写出 JSON；0=不限')
    ap.add_argument('--mock', action='store_true', help='离线回放 tests/fixtures（本地/CI 联调）')
    ap.add_argument('--offline', action='store_true', help='断网兜底：读取已有 JSON，仅刷新时间戳')
    ap.add_argument('--text', action='store_true', help='打印人读摘要')
    ap.add_argument('--catalog', action='store_true', help='只打印 49 源目录（不联网）')
    ap.add_argument('--catalog-md', metavar='PATH',
                    help='把目录写成 Markdown（docs/rss-49-feeds.md 的生成器，不联网）')
    ap.add_argument('--self-test', action='store_true', help='离线自检（不联网）')
    ap.add_argument('--quiet', action='store_true', help='不打印逐源日志')
    args = ap.parse_args()

    if args.self_test:
        sys.exit(self_test())
    if args.catalog:
        print_catalog()
        return
    if args.catalog_md:
        out = os.path.abspath(args.catalog_md)
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        with open(out, 'w', encoding='utf-8') as f:
            f.write(render_catalog_md() + '\n')
        print(f'📄 已写出目录 Markdown：{args.catalog_md}（{len(CATALOG)} 源 · {len(GROUPS)} 板块）')
        return

    now = datetime.now(timezone.utc)
    if args.offline:
        try:
            with open(args.json, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            print(f'⚠️ 离线模式无可用历史数据（{e}）→ 05 节将显示「今日未获取」', file=sys.stderr)
            data = {'groups': {}, 'summary': {'ok': 0, 'total': len(CATALOG), 'items': 0},
                    'sources': [], 'window': {'max_age_days': args.days, 'since': None},
                    'notes': ['离线模式且无历史数据：正文按「未获取」降级']}
        data.update({'generated_at': now.strftime('%Y-%m-%d %H:%M:%S UTC'),
                     'fetch_date': now.strftime('%Y-%m-%d'), 'mode': 'offline'})
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'🌐 离线模式: 已沿用上次结果并刷新时间戳 → {args.json}')
        return

    only = [k.strip() for k in args.only.split(',') if k.strip()] or None
    if only:
        unknown = [k for k in only if k not in CATALOG_BY_KEY]
        if unknown:
            print(f'错误: 未知源 key {unknown}（用 --catalog 查看目录）', file=sys.stderr)
            sys.exit(2)
    if args.mock and only is None:
        only = None  # mock 一遍全目录，输出同构
    t_start = time.time()
    data = build(max_age_days=args.days, limit=args.limit, timeout=args.timeout,
                 mock=args.mock, quiet=args.quiet, only=only,
                 deadline=(t_start + args.budget) if args.budget > 0 else None)
    with open(args.json, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    s = data['summary']
    if not args.quiet:
        print(f'📡 RSS 情报源已写入 {args.json} '
              f'（源 {s["ok"]}/{s["total"]} · 条目 {s["items"]} 条 · '
              f'窗口 {args.days} 天 · {data["generated_at"]}）')
        if s['items'] == 0:
            print('  ⚠️ 窗口内没有任何可核验条目 → 05 节将显示「今日未获取」，'
                  '不回填历史标题（这是预期降级行为）', file=sys.stderr)
    if args.text:
        print(text_report(data))


if __name__ == '__main__':
    main()
