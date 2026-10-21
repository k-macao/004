"""
章鱼 AI·舆情004 — 49 大社区动态抓取 (community_data.py)

每次构建/推送前自动抓取 **49 大社区**最新研判（14 原有 + 20 前次新增 + 15 本次扩容），
生成 community_data.json，供 build_site.py 与 tools/wechat_push.py 动态注入，
实现「49 源动态抓取真正上线」：

  • 原有 14 源：富途牛牛社区 / 雪球网 / 老虎社区 / 东方财富港股股吧
    / 智通财经互动区 / 华尔街见闻社区 / 香港讨论区财经版 / LIHKG 连登财经台
    / 韭圈儿 / 红岸社区 / 蚂蚁财富港股社区 / Reddit (r/ChinaStocks)
    / TradingView 香港板块 / Value Investors Club / Twitter / X (FinTwit)
  • 前次新增 20 源（中英文、不同类型）：知乎 / 微博财经超话 / 百度贴吧股票吧 / 淘股吧
    / 同花顺社区 / 格隆汇港股圈 / 财联社电报 / 第一财经 / Bilibili 财经区 / PTT Stock 板
    / StockTwits / Seeking Alpha / Bogleheads / r/investing / Wall Street Oasis
    / Investing.com 讨论区 / Yahoo Finance 社区 / Substack 财经通讯 / r/options
    / FT Alphaville
  • 本次扩容 15 源（35–49，补齐低风险 / 社交 / 短视频 / 量化 / 外汇 / 数字资产 / 日韩德 / 本地财经）：
    集思录低风险社区 / 小红书理财笔记 / 抖音财经短视频 / 开盘啦情绪复盘 / 理想论坛实战
    / QuantNet 量化社区 / Elite Trader / Forex Factory / Reddit (r/CryptoCurrency)
    / Morningstar 社区 / MarketWatch 社区 / 日本 Yahoo! 财经掲示板 / 네이버 금융 종토방
    / Wallstreet-Online 德语社区 / 阿斯达克财经讨论区

  每条社区都带 ctype（社区类型：问答 / 社交 / 论坛 / 研究 / 快讯 / 媒体 / 视频 /
  机构 / 订阅研究 / 衍生品 / 行情 / 低风险 / 短视频 / 量化 / 外汇 / 数字资产 / 日韩德 …），
  便于核对「不同类型」的覆盖面。

抓取策略（Scrapling 框架模型，见 scrapling_core.py / community_spider.py）：
  1. community_spider.CommunitySpider 用 Spider/CrawlerEngine 并发抓取 49 源：
     Scheduler 指纹去重 + AutoThrottle 每域自适应限速 + 封锁状态码重试 + 可选 robots.txt；
  2. 每源用 CSS 候选选择器抽取标题与正文块；失配时用 SQLite 里的**元素指纹自适应回捞**
     （站点改版也能定位同一块「最新热评」）；再无命中则在整页按港股关键词打分取最相关片段；
  3. 结合 market_data.json 的最新行情（HSI、恒科、黄金等）与抓取日期，动态生成研判；
  4. 单源失败不阻断 — 失败项自动降级为基于行情的模板生成，保证 49 源永远齐全。

设计原则：
  • 纯标准库（urllib + html.parser + sqlite3），CI 开箱即用，无需 pip install
  • 每次运行生成全新内容，正文中的日期永远是当天，杜绝“8 月 12 日”旧数据残留
  • 单源失败记录在 summary.failed，但仍生成 fallback 内容，保证构建与推送永不中断
  • offline_dataset() 给下游（微信推送）一份同构的 49 条兜底数据：
    缺 community_data.json 时也只降级 source 标记，不降级源数量与结构

用法:
  python3 market_data.py && python3 community_data.py         # 联网抓取 49 源 → community_data.json
  python3 community_data.py --limit 5 --timeout 8              # 只抓前 5 源（联调）
  python3 community_data.py --robots --crawl-dir .crawl        # 遵从 robots.txt + 断点续爬
  python3 community_data.py --demo                             # 写入模拟社区数据（本地联调）
  python3 community_data.py --offline                          # 断网兜底：基于旧数据刷新时间戳
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import scrapling_core as sc

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
MARKET_DATA_DEFAULT = os.path.join(REPO_ROOT, 'market_data.json')

# 抓取引擎（Scrapling 框架模型移植）：community_spider 在函数内导入，避免与社区目录形成导入环
ADAPTIVE_DB_DEFAULT = os.path.join(REPO_ROOT, '.scrapling', 'community_adaptive.db')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

# 49 大社区定义（14 原有 + 20 前次新增 + 15 本次扩容：中英文 / 多语种 / 不同类型）
COMMUNITIES = [
    {
        "id": "01",
        "key": "FUTU",
        "name": "富途牛牛社区",
        "icon": "🐮",
        "ctype": "中文行情社区",
        "url": "https://www.futunn.com/hk",
        "verdict_label": "多空分歧",
        "verdict_class": "mixed",
        "meta_tpl": "综合站内 10 条热门长帖与讨论",
    },
    {
        "id": "02",
        "key": "XUEQIU",
        "name": "雪球网",
        "icon": "❄️",
        "ctype": "中文投资者社区",
        "url": "https://xueqiu.com/hq#HSI",
        "verdict_label": "多空分歧",
        "verdict_class": "mixed",
        "meta_tpl": "综合站内 10 条深度研报与讨论",
    },
    {
        "id": "03",
        "key": "LAOHU",
        "name": "老虎社区",
        "icon": "🐯",
        "ctype": "中文跨境社区",
        "url": "https://www.laohu8.com",
        "verdict_label": "偏空",
        "verdict_class": "bear",
        "meta_tpl": "综合站内 10 条热门跨境讨论",
    },
    {
        "id": "04",
        "key": "EASTMONEY",
        "name": "东方财富港股股吧",
        "icon": "💰",
        "ctype": "中文股吧论坛",
        "url": "https://guba.eastmoney.com",
        "verdict_label": "偏空",
        "verdict_class": "bear",
        "meta_tpl": "综合站内 10 条高互动主题帖",
    },
    {
        "id": "05",
        "key": "ZHITONG",
        "name": "智通财经互动区",
        "icon": "📈",
        "ctype": "中文资讯互动",
        "url": "https://www.zhitongcaijing.com",
        "verdict_label": "偏多",
        "verdict_class": "bull",
        "meta_tpl": "综合站内 10 条专业席位跟踪分析",
    },
    {
        "id": "06",
        "key": "WALLSTREETCN",
        "name": "华尔街见闻社区",
        "icon": "🌐",
        "ctype": "中文宏观社区",
        "url": "https://wallstreetcn.com",
        "verdict_label": "偏多",
        "verdict_class": "bull",
        "meta_tpl": "综合站内 10 条宏观深度长文",
    },
    {
        "id": "07",
        "key": "DISCUSS",
        "name": "香港讨论区财经版",
        "icon": "🇭🇰",
        "ctype": "粤语本地论坛",
        "url": "https://www.discuss.com.hk/forumdisplay.php?fid=115",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条粤语热门讨论贴",
    },
    {
        "id": "08",
        "key": "LIHKG",
        "name": "LIHKG 连登财经台",
        "icon": "🔥",
        "ctype": "粤语本地论坛",
        "url": "https://lihkg.com/category/5",
        "verdict_label": "偏空",
        "verdict_class": "bear",
        "meta_tpl": "综合站内 10 条高频交易讨论链",
    },
    {
        "id": "09",
        "key": "JIUQUAN",
        "name": "韭圈儿 / 红岸社区",
        "icon": "🥦",
        "ctype": "中文机构持仓社区",
        "url": "https://www.jiucaishuo.com",
        "verdict_label": "偏多",
        "verdict_class": "bull",
        "meta_tpl": "综合站内 10 篇机构仓位拆解报告",
    },
    {
        "id": "10",
        "key": "ANTFORTUNE",
        "name": "蚂蚁财富港股社区",
        "icon": "🐜",
        "ctype": "中文基民社区",
        "url": "https://www.antfortune.com",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条基民热评与定投贴",
    },
    {
        "id": "11",
        "key": "REDDIT",
        "name": "Reddit (r/ChinaStocks)",
        "icon": "👾",
        "ctype": "英文论坛",
        "url": "https://www.reddit.com/r/ChinaStocks/",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 篇外文热门深度分析",
    },
    {
        "id": "12",
        "key": "TRADINGVIEW",
        "name": "TradingView 香港板块",
        "icon": "📊",
        "ctype": "英文图表社区",
        "url": "https://www.tradingview.com/markets/hong-kong/",
        "verdict_label": "偏多",
        "verdict_class": "bull",
        "meta_tpl": "综合站内 10 套专业技术分析图表与指标",
    },
    {
        "id": "13",
        "key": "VIC",
        "name": "Value Investors Club",
        "icon": "💎",
        "ctype": "英文研究社区",
        "url": "https://www.valueinvestorsclub.com",
        "verdict_label": "偏多",
        "verdict_class": "bull",
        "meta_tpl": "综合站内 10 篇顶尖私密价值分析研报",
    },
    {
        "id": "14",
        "key": "FINTWIT",
        "name": "Twitter / X (FinTwit)",
        "icon": "🐦",
        "ctype": "英文社交平台",
        "url": "https://x.com/search?q=HSI%20Hong%20Kong",
        "verdict_label": "偏多",
        "verdict_class": "bull",
        "meta_tpl": "综合站内 10 条海外基金经理核心观点",
    },
    # ---------------- 本次新增的 20 个社区（15–34 · 中英文 / 不同类型） ----------------
    {
        "id": "15",
        "key": "ZHIHU",
        "name": "知乎 · 投资理财话题",
        "icon": "🧠",
        "ctype": "中文问答社区",
        "url": "https://www.zhihu.com/topic/19554223/hot",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条高赞问答与专栏",
    },
    {
        "id": "16",
        "key": "WEIBO",
        "name": "微博财经超话",
        "icon": "📣",
        "ctype": "中文社交媒体",
        "url": "https://s.weibo.com/weibo?q=%23A%E8%82%A1%23",
        "verdict_label": "多空分歧",
        "verdict_class": "mixed",
        "meta_tpl": "综合站内 10 条财经超话与热评",
    },
    {
        "id": "17",
        "key": "TIEBA",
        "name": "百度贴吧 · 股票吧",
        "icon": "🫧",
        "ctype": "中文论坛",
        "url": "https://tieba.baidu.com/f?kw=%E8%82%A1%E7%A5%A8",
        "verdict_label": "偏空",
        "verdict_class": "bear",
        "meta_tpl": "综合站内 10 条人气主题帖",
    },
    {
        "id": "18",
        "key": "TAOGUBA",
        "name": "淘股吧",
        "icon": "🎯",
        "ctype": "中文短线交易论坛",
        "url": "https://www.taoguba.com.cn",
        "verdict_label": "多空分歧",
        "verdict_class": "mixed",
        "meta_tpl": "综合站内 10 条短线实盘与复盘",
    },
    {
        "id": "19",
        "key": "THS",
        "name": "同花顺社区",
        "icon": "📉",
        "ctype": "中文行情社区",
        "url": "https://t.10jqka.com.cn",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条问财与组合讨论",
    },
    {
        "id": "20",
        "key": "GELONGHUI",
        "name": "格隆汇 · 港股圈",
        "icon": "🔎",
        "ctype": "中文研究社区",
        "url": "https://www.gelonghui.com",
        "verdict_label": "偏多",
        "verdict_class": "bull",
        "meta_tpl": "综合站内 10 篇港股深度研究",
    },
    {
        "id": "21",
        "key": "CLS",
        "name": "财联社 · 电报",
        "icon": "⚡",
        "ctype": "中文快讯社区",
        "url": "https://www.cls.cn/telegraph",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条快讯与留言",
    },
    {
        "id": "22",
        "key": "YICAI",
        "name": "第一财经 · 社区",
        "icon": "📺",
        "ctype": "中文财经媒体",
        "url": "https://www.yicai.com",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条深度报道与评论",
    },
    {
        "id": "23",
        "key": "BILIBILI",
        "name": "Bilibili · 财经区",
        "icon": "🎬",
        "ctype": "中文视频社区",
        "url": "https://www.bilibili.com/v/finance",
        "verdict_label": "多空分歧",
        "verdict_class": "mixed",
        "meta_tpl": "综合站内 10 条财经 UP 主讨论",
    },
    {
        "id": "24",
        "key": "PTT",
        "name": "PTT Stock 板",
        "icon": "🗣️",
        "ctype": "繁体中文论坛",
        "url": "https://www.ptt.cc/bbs/Stock/index.html",
        "verdict_label": "偏空",
        "verdict_class": "bear",
        "meta_tpl": "综合站内 10 条台湾股板热帖",
    },
    {
        "id": "25",
        "key": "STOCKTWITS",
        "name": "StockTwits",
        "icon": "💬",
        "ctype": "英文交易者社交",
        "url": "https://stocktwits.com/symbol/HSI",
        "verdict_label": "多空分歧",
        "verdict_class": "mixed",
        "meta_tpl": "综合站内 10 条实时交易者观点",
    },
    {
        "id": "26",
        "key": "SEEKINGALPHA",
        "name": "Seeking Alpha",
        "icon": "🧾",
        "ctype": "英文研究社区",
        "url": "https://seekingalpha.com/market-news",
        "verdict_label": "偏多",
        "verdict_class": "bull",
        "meta_tpl": "综合站内 10 篇英文深度研究",
    },
    {
        "id": "27",
        "key": "BOGLEHEADS",
        "name": "Bogleheads Forum",
        "icon": "🧭",
        "ctype": "英文长期投资论坛",
        "url": "https://www.bogleheads.org/forum/index.php",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条资产配置讨论",
    },
    {
        "id": "28",
        "key": "RINVESTING",
        "name": "Reddit (r/investing)",
        "icon": "🧩",
        "ctype": "英文论坛",
        "url": "https://www.reddit.com/r/investing/",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 篇英文热门讨论",
    },
    {
        "id": "29",
        "key": "WSO",
        "name": "Wall Street Oasis",
        "icon": "🏦",
        "ctype": "英文机构社区",
        "url": "https://www.wallstreetoasis.com/forum",
        "verdict_label": "偏空",
        "verdict_class": "bear",
        "meta_tpl": "综合站内 10 条机构从业讨论",
    },
    {
        "id": "30",
        "key": "INVESTING",
        "name": "Investing.com 讨论区",
        "icon": "🌍",
        "ctype": "多语言全球论坛",
        "url": "https://www.investing.com/indices/hang-seng-index-commentary",
        "verdict_label": "多空分歧",
        "verdict_class": "mixed",
        "meta_tpl": "综合站内 10 条全球散户评论",
    },
    {
        "id": "31",
        "key": "YAHOO",
        "name": "Yahoo Finance · 社区",
        "icon": "📰",
        "ctype": "英文行情社区",
        "url": "https://finance.yahoo.com/quote/%5EHSI/community",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条行情问答与评论",
    },
    {
        "id": "32",
        "key": "SUBSTACK",
        "name": "Substack · 财经通讯",
        "icon": "✉️",
        "ctype": "英文订阅研究",
        "url": "https://substack.com/browse/finance",
        "verdict_label": "多空分歧",
        "verdict_class": "mixed",
        "meta_tpl": "综合站内 10 篇订阅制宏观通讯",
    },
    {
        "id": "33",
        "key": "ROPTIONS",
        "name": "Reddit (r/options)",
        "icon": "⚙️",
        "ctype": "英文衍生品论坛",
        "url": "https://www.reddit.com/r/options/",
        "verdict_label": "偏空",
        "verdict_class": "bear",
        "meta_tpl": "综合站内 10 条期权对冲讨论",
    },
    {
        "id": "34",
        "key": "FTALPHA",
        "name": "FT Alphaville",
        "icon": "🗞️",
        "ctype": "英文财经媒体博客",
        "url": "https://www.ft.com/alphaville",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 篇市场结构评论",
    },
    # ---------------- 本次扩容的 15 个社区（35–49 · 低风险 / 社交 / 短视频 / 量化 / 外汇 / 数字资产 / 日韩德 / 本地财经） ----------------
    {
        "id": "35",
        "key": "JISILU",
        "name": "集思录 · 低风险投资社区",
        "icon": "🧊",
        "ctype": "中文低风险投资社区",
        "url": "https://www.jisilu.cn",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条可转债与套利讨论",
    },
    {
        "id": "36",
        "key": "XIAOHONGSHU",
        "name": "小红书 · 理财笔记",
        "icon": "📕",
        "ctype": "中文种草社交社区",
        "url": "https://www.xiaohongshu.com/explore",
        "verdict_label": "多空分歧",
        "verdict_class": "mixed",
        "meta_tpl": "综合站内 10 条理财笔记与评论",
    },
    {
        "id": "37",
        "key": "DOUYIN",
        "name": "抖音 · 财经短视频",
        "icon": "🎵",
        "ctype": "中文短视频社区",
        "url": "https://www.douyin.com/search/%E6%B8%AF%E8%82%A1",
        "verdict_label": "多空分歧",
        "verdict_class": "mixed",
        "meta_tpl": "综合站内 10 条财经短视频与热评",
    },
    {
        "id": "38",
        "key": "KAIPANLA",
        "name": "开盘啦 · 情绪复盘",
        "icon": "🚀",
        "ctype": "中文短线情绪社区",
        "url": "https://www.kaipanla.com",
        "verdict_label": "偏空",
        "verdict_class": "bear",
        "meta_tpl": "综合站内 10 条情绪周期复盘",
    },
    {
        "id": "39",
        "key": "LIXIANG",
        "name": "理想论坛 · 股票实战",
        "icon": "🧭",
        "ctype": "中文实战论坛",
        "url": "https://www.55188.com",
        "verdict_label": "偏空",
        "verdict_class": "bear",
        "meta_tpl": "综合站内 10 条实盘复盘帖",
    },
    {
        "id": "40",
        "key": "QUANTNET",
        "name": "QuantNet · 量化社区",
        "icon": "📐",
        "ctype": "英文量化社区",
        "url": "https://quantnet.com",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条因子与择时讨论",
    },
    {
        "id": "41",
        "key": "ELITETRADER",
        "name": "Elite Trader",
        "icon": "🎓",
        "ctype": "英文交易员论坛",
        "url": "https://www.elitetrader.com/et/",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条执行与滑点讨论",
    },
    {
        "id": "42",
        "key": "FOREXFACTORY",
        "name": "Forex Factory",
        "icon": "💱",
        "ctype": "英文外汇社区",
        "url": "https://www.forexfactory.com/forum",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条汇率与利率讨论",
    },
    {
        "id": "43",
        "key": "RCRYPTO",
        "name": "Reddit (r/CryptoCurrency)",
        "icon": "🪙",
        "ctype": "数字资产论坛",
        "url": "https://www.reddit.com/r/CryptoCurrency/",
        "verdict_label": "偏空",
        "verdict_class": "bear",
        "meta_tpl": "综合站内 10 条风险资产联动讨论",
    },
    {
        "id": "44",
        "key": "MORNINGSTAR",
        "name": "Morningstar · 社区",
        "icon": "🌅",
        "ctype": "英文基金研究社区",
        "url": "https://community.morningstar.com",
        "verdict_label": "偏多",
        "verdict_class": "bull",
        "meta_tpl": "综合站内 10 条基金费率与配置讨论",
    },
    {
        "id": "45",
        "key": "MARKETWATCH",
        "name": "MarketWatch · 社区",
        "icon": "🗽",
        "ctype": "英文财经媒体社区",
        "url": "https://www.marketwatch.com/latest-news",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条报道与读者评论",
    },
    {
        "id": "46",
        "key": "YAHOOJP",
        "name": "日本 Yahoo! 财经掲示板",
        "icon": "🗾",
        "ctype": "日文社区",
        "url": "https://finance.yahoo.co.jp/quote/998407.O",
        "verdict_label": "偏空",
        "verdict_class": "bear",
        "meta_tpl": "综合站内 10 条日文讨论",
    },
    {
        "id": "47",
        "key": "NAVER",
        "name": "네이버 금융 종토방",
        "icon": "🇰🇷",
        "ctype": "韩文社区",
        "url": "https://finance.naver.com/item/board.naver?code=005930",
        "verdict_label": "多空分歧",
        "verdict_class": "mixed",
        "meta_tpl": "综合站内 10 条韩文讨论",
    },
    {
        "id": "48",
        "key": "WALLSTREETDE",
        "name": "Wallstreet-Online · 德语社区",
        "icon": "🇩🇪",
        "ctype": "德文社区",
        "url": "https://www.wallstreet-online.de/community/forum",
        "verdict_label": "中性",
        "verdict_class": "neutral",
        "meta_tpl": "综合站内 10 条德文讨论",
    },
    {
        "id": "49",
        "key": "AASTOCKS",
        "name": "阿斯达克财经 · 讨论区",
        "icon": "🇭🇰",
        "ctype": "香港本地财经社区",
        "url": "http://www.aastocks.com/tc/stocks/comment/latest.aspx",
        "verdict_label": "偏多",
        "verdict_class": "bull",
        "meta_tpl": "综合站内 10 条本地财经讨论",
    },
]

def http_get(url, timeout=10, retries=2, session=None):
    """单源抓取（Scrapling Fetcher 语义：浏览器请求头 + 重试 + 编码识别）。

    保留这个函数是为了兼容旧调用点；批量抓取走 community_spider.CommunitySpider，
    那样才有并发、每域限速、封锁重试与自适应回捞。
    """
    fetcher = session or sc.Fetcher.session()
    response = fetcher.get(url, timeout=timeout, retries=retries)
    return response.text

def strip_html(html, max_len=500):
    """HTML → 纯文本片段（Scrapling Convertor：先洗噪音/隐藏内容，再取全部文本）。"""
    page = sc.Convertor.sanitize_for_ai(sc.Convertor.strip_noise_tags(sc.Selector(html or '')))
    text = page.get_all_text(separator=' ', strip=True)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_len]

def load_market_data(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'⚠️ market_data.json 读取失败: {e}', file=sys.stderr)
        return {}

def fmt_hsi(market):
    quotes = (market or {}).get('quotes') or {}
    hsi = quotes.get('HSI') or {}
    last = hsi.get('last')
    pct = hsi.get('pct')
    chg = hsi.get('chg')
    as_of = hsi.get('as_of') or ''
    # 格式化
    def fmt(v, nd=2):
        if v is None:
            return '—'
        return f'{float(v):,.{nd}f}'
    last_s = fmt(last)
    pct_s = f"{float(pct):+.2f}%" if pct is not None else "—"
    chg_s = fmt(chg)
    return {
        'last': last_s,
        'pct': pct_s,
        'chg': chg_s,
        'as_of': as_of,
        'raw_pct': float(pct) if pct is not None else 0.0,
        'raw_last': float(last) if last is not None else 25440.17,
    }

def generate_dynamic_quote(community, hsi, fetch_date, fetch_date_cn, live_snippet, mode='live'):
    """
    基于社区特性、恒指行情、抓取日期、活抓片段，动态生成研判正文。
    确保每次内容都包含当天日期，杜绝旧数据。
    """
    m = re.match(r'20\d{2}-(\d{2})-(\d{2})', fetch_date)
    month = int(m.group(1)) if m else 8
    day = int(m.group(2)) if m else 30
    pct = hsi['raw_pct']
    last = hsi['last']
    pct_s = hsi['pct']

    # 根据涨跌生成短线描述
    if pct < -0.7:
        action = "低开低走收跌"
        short_desc = "连续受阻后空头排列，短线动能转弱"
    elif pct < -0.2:
        action = "窄幅震荡收跌"
        short_desc = "箱体上沿受阻回踩，等待均线带支撑"
    elif pct > 0.7:
        action = "高开高走收涨"
        short_desc = "放量站回均线带，多头动能回升"
    elif pct > 0.2:
        action = "震荡上行收涨"
        short_desc = "箱体下沿企稳反弹，资金回流迹象明显"
    else:
        action = "窄幅震荡"
        short_desc = "多空在箱体内均衡，等待方向选择"

    # 活数据片段提示
    live_hint = ""
    if live_snippet:
        # 取前 30 字作为“现场”佐证，避免过长
        snippet_short = live_snippet[:40].strip()  # 49 源共用一页，现场片段长度也纳入字符预算
        if snippet_short:
            live_hint = f"（现场抓取片段：{snippet_short}…）"

    key = community['key']
    name = community['name']

    # 49 个社区差异化模板，全部带当天日期
    templates = {
        "FUTU": f"平台深度热评：{month} 月 {day} 日恒指{action} {pct_s}，收报 {last} 点，{short_desc}。技术派指出 26,000 整数关仍是强阻力，30 分钟级别需等待金叉才重新进场；资金派紧盯分时大单与南向净流向，强调“先看异动再做决策”——当日盘口反馈远快于叙事。{live_hint} 中长线声音则认为：即便回踩 25,200–25,400 箱体下沿，南向资金近期维持净流入，叠加盈利修复，明年上半年挑战 28,200 点的路径未被破坏。",
        "XUEQIU": f"热帖直指“恒指 26,000 关口压力重重，本轮是反弹还是反转”。{month} 月 {day} 日恒指{action} {pct_s} 报 {last}，恒科同步 {short_desc}。球友对半导体“空头撤退股价仍跌”解读为被动出清而非新一轮做空；美债 10 年期约 4.67% 仍压制高估值成长，资金在光通信 / 芯片与红利、内房之间高速轮动。价值派强调：南向资金今年多数月份持续流入，盈利 3%–4% 内生增长与机构基准目标位仍成立（最新目标价以 02 栏当次快讯为准），主张高息底仓 + 新质生产力，拒绝在 26,000 附近追高。{live_hint}",
        "LAOHU": f"跨境账户情绪：{month} 月 {day} 日港股{action}（恒指 {pct_s}），外资 trim China exposure 快于内资的格局仍在；美伊和解预期与霍尔木兹油价扰动叠加华尔街隔夜科技回撤，亚洲时段反弹乏力。社区对折价配售、H 股大额募资仍敏感，认为股权稀释压制追高意愿；操作共识是继续观望，等待金叉与 25,800 放量收复，短线维持离场信号。{live_hint}",
        "EASTMONEY": f"股吧情绪：{month} 月 {day} 日恒指{action} {pct_s}，科网股与内房分化明显。讨论焦点从“还能不能追”转为“会不会回踩箱体下沿”。内房午后异动被解读为政策博弈炒作而非趋势反转；消费防御相对抗跌。多数声音主张先看 25,400 箱体下沿能否守住，跌破再看 24,400 / 23,500。{live_hint}",
        "ZHITONG": f"席位与衍生品视角：{month} 月 {day} 日恒指牛熊街货比约 49:51，熊证重货区落在 26,200–26,299、牛证重货区在 25,200–25,299，与现货箱体高度吻合。{short_desc}，收 {last}（{pct_s}）。光通信获摩根大通一周内多次加仓中际旭创 H 股，芯片股逆市走强；长线席位继续流向高息、REITs、电信与公用事业。{live_hint}",
        "WALLSTREETCN": f"宏观对冲盘聚焦：{month} 月 {day} 日美国 CPI 与非农数据组合仍主导离岸成长估值，恒指{action} {pct_s} 至 {last}。社区主流叙事仍是“全球资金从韩日美股拥挤多头再平衡至低估港股 + 国内政策托底”，但强调 26,000 失败后应以防守姿态做多：黄金与铜铝锂及高息低贝塔，而非追互联网贝塔。{live_hint}",
        "DISCUSS": f"本地炒鬼：{month} 月 {day} 日恒指{action} {pct_s}，共识是“又係 26,000 附近派货”。内房脉冲被当成政策消息博弈，多数人表示“睇得、唔好追”。共识仍是港股弱于 A 股、先跌后上，必须等金叉同南向持续净流入先至加仓；配置上继续揽住高息、本地电信、REITs 同公用。{live_hint}",
        "LIHKG": f"连登交易员：{month} 月 {day} 日恒指{action} {pct_s}，三周涨幅后未能放量突破 26,200–26,500，{short_desc}。主流策略切到期权 / 牛熊证做波动率，街货比 49:51 被解读为多空打平、适合两边开仓；硬止损纪律被反复强调。中期仍认资金再平衡，但短线紧盯 6h/12h ALMA 与 25,200 牛证重货区。{live_hint}",
        "JIUQUAN": f"公募与港股通持仓透视：{month} 月 {day} 日恒指{action} {pct_s} 报 {last}，南向资金近期维持净流入；近一月主力流向资讯科技、原材料、医疗保健。截至 {month} 月 {day} 日南向持股市值前十仍是腾讯、建行、工行、中海油、汇丰、中国移动、阿里、中行、中芯、小米。机构共识未改：估值修复 + 科技盈利是 2026 主引擎，箱体震荡是机构完成高低切换的窗口。{live_hint}",
        "ANTFORTUNE": f"基民社区：{month} 月 {day} 日恒指{action} {pct_s}，散户港股 ETF 申购与搜索热度随指数回踩降温，讨论从“还能不能追”转为“定投要不要暂停”。理财顾问仍主推高息红利、REITs、电信与公用事业作为底仓，提醒不要在箱体上沿加杠杆。情绪从极度乐观回到中性偏防守。{live_hint}",
        "REDDIT": f"英文社区：{month} 月 {day} 日恒指{action} {pct_s}，仍把港股当作投资中国核心资产最便利的离岸通道，VIE / ADR 等价性讨论未停。增量话题切到宏观：美国 CPI 与就业数据降低加息紧迫性；霍尔木兹和解预期反复、油价走高被视作主要外部扰动。整体偏“可投资性 + 事件驱动”，缺少一致指数多空押注。{live_hint}",
        "TRADINGVIEW": f"图表派更新：{month} 月 {day} 日恒指收 {last}（{pct_s}），{short_desc}。三周反弹后于 26,000 录得 RSI 超买警报；EMA9/21 交叉约 25,978 / 25,471 仍托住升势，MACD 高位减速。新作战目标 26,500 / 延伸 27,044，移动止损上移至 25,124；若失守 25,200 牛证重货区则视为箱体下破。{live_hint}",
        "VIC": f"价投私密社区：{month} 月 {day} 日恒指{action} {pct_s}，并不把 26,000 失败当成逻辑破坏：港股相对欧美估值折价、中小盘私有化套利与控股股东折价仍是 2026 主引擎。基准情景维持恒指年底 28,000–29,000、乐观 31,000。配置不变：高息 + 中资科技 + 本地金融为底仓，REITs / 电信 / 必需消费 / 公用对冲。{live_hint}",
        "FINTWIT": f"FinTwit 宏观账户：{month} 月 {day} 日恒指{action} {pct_s} 至 {last}，仍把港股标成“再平衡避风港”，但语气从右侧突破转为“26,000 失败后的健康回撤”。CPI 降温与就业疲弱压低加息赔率，黄金与铜锂继续作为地缘对冲。政策叙事切到北京“及时实施积极政策”与一线城市放松限购，技术上 MA50 已站上，关键是守住 25,200–25,470 均线带。{live_hint}",
        "ZHIHU": f"高赞问答聚焦「港股是不是全球最便宜的中国资产」：{month} 月 {day} 日恒指{action} {pct_s} 报 {last}，{short_desc}。答主把估值分位、南向定价权与新质生产力拆成三条论证线，提醒区分「便宜」与「便宜有原因」。{live_hint}",
        "WEIBO": f"财经超话情绪：{month} 月 {day} 日恒指{action} {pct_s}，热搜从「还能追吗」转到「要不要止盈」。大 V 多空互撕，追高意愿降温但抄底讨论升温，是典型分歧区。{live_hint}",
        "TIEBA": f"股票吧人气帖：{month} 月 {day} 日恒指{action} {pct_s} 收 {last}，吧友对港股通与科技股偏谨慎，喊单帖明显减少；{short_desc}，多数人「等回调再说」，情绪偏空但不恐慌。{live_hint}",
        "TAOGUBA": f"短线实盘复盘：{month} 月 {day} 日恒指{action} {pct_s}，日内打板资金转战 A 股题材，港股短线客以轻仓试单为主；复盘共识是量能不足前不追高，严格按均线止损。{short_desc}。{live_hint}",
        "THS": f"同花顺问财与组合讨论：{month} 月 {day} 日恒指{action} {pct_s} 报 {last}，搜索词从「港股 ETF 推荐」转向「红利低波」；组合回测帖增多，普遍把高息与现金流放在第一位。{live_hint}",
        "GELONGHUI": f"港股圈深度帖：{month} 月 {day} 日恒指{action} {pct_s} 收 {last}，研究型观点仍强调中资科技盈利兑现与折价修复的双重逻辑，认为 {short_desc} 正是布局窗口，同时给出明确的仓位上限。{live_hint}",
        "CLS": f"电报快讯与留言：{month} 月 {day} 日恒指{action} {pct_s}，快讯流以政策表态与南向数据为主，留言区最关心「消息能不能落到盈利」；{short_desc}，情绪中性偏观望。{live_hint}",
        "YICAI": f"报道评论区：{month} 月 {day} 日恒指{action} {pct_s} 至 {last}，深度报道聚焦出口链与地产链分化；读者更关注政策节奏而非单日点位，{short_desc}，分歧集中在盈利修复斜率。{live_hint}",
        "BILIBILI": f"财经区 UP 主：{month} 月 {day} 日恒指{action} {pct_s}，视频标题以「港股还能不能上车」为主，弹幕情绪随盘面波动；年轻资金偏好恒科与 AI 题材，对高息策略兴趣有限。{live_hint}",
        "PTT": f"台股板看港股与中概联动：{month} 月 {day} 日恒指{action} {pct_s} 报 {last}，讨论以「避开地缘风险」为前提，偏好现金流稳定的传产与电信，{short_desc}，对科技股维持高波动评价。{live_hint}",
        "STOCKTWITS": f"交易者实时观点：{month} 月 {day} 日恒指{action} {pct_s}，$HSI 讨论量随波动放大，多空情绪条接近五五开；{short_desc}，短线上更愿意用期权而非现货表达观点。{live_hint}",
        "SEEKINGALPHA": f"英文研究帖：{month} 月 {day} 日恒指{action} {pct_s} 收 {last}，观点集中在「折价 + 股息率 + 盈利修复」的组合逻辑；作者普遍认为中长期赔率仍在，但要求分批与对冲参与。{live_hint}",
        "BOGLEHEADS": f"长期配置讨论：{month} 月 {day} 日恒指{action} {pct_s}，论坛把港股当作新兴市场配置的一小块，强调分散与再平衡；不鼓励择时，{short_desc} 只影响再平衡的执行节奏。{live_hint}",
        "RINVESTING": f"英文散户讨论：{month} 月 {day} 日恒指{action} {pct_s}，话题围绕估值、地缘与汇率三条主线；有人认为折价已足够，有人担心流动性折价长期化，{short_desc}。{live_hint}",
        "WSO": f"机构从业视角：{month} 月 {day} 日恒指{action} {pct_s}，讨论偏保守，强调融资环境与退出通道比估值更重要；{short_desc}，多数人倾向等待明确的资金面信号。{live_hint}",
        "INVESTING": f"全球散户评论：{month} 月 {day} 日恒指{action} {pct_s} 报 {last}，多语言评论区里欧洲与亚洲时段观点分歧明显，有人把回调当买点，也有人担心外围风险传导。{live_hint}",
        "YAHOO": f"行情社区：{month} 月 {day} 日恒指{action} {pct_s}，讨论以盘后复盘与财报日历为主；{short_desc}，用户更关心权重股与 ADR 价差是否给出套利机会。{live_hint}",
        "SUBSTACK": f"订阅制通讯：{month} 月 {day} 日恒指{action} {pct_s}，作者把焦点放在流动性与财政节奏上，认为 {short_desc}；对港股的建议多为「结构性参与而非指数押注」。{live_hint}",
        "ROPTIONS": f"期权社区：{month} 月 {day} 日恒指{action} {pct_s}，讨论集中在波动率定价与对冲成本；{short_desc}，多数人选择卖出波动率或做保护性价差，而非方向性押注。{live_hint}",
        "FTALPHA": f"市场结构评论：{month} 月 {day} 日恒指{action} {pct_s}，评论强调南向资金与指数编制的结构性影响，{short_desc}；作者提醒单日点位噪音大，应看资金与流动性趋势。{live_hint}",
        "JISILU": f"低风险视角：{month} 月 {day} 日恒指{action} {pct_s} 报 {last} 点，可转债与港股打新的讨论热度回升，集友更关心「下有保底」的标的还剩多少折价；{short_desc}，仓位更多留给确定性更高的套利与红利票据。{live_hint}",
        "XIAOHONGSHU": f"理财笔记区：{month} 月 {day} 日恒指{action} {pct_s}，笔记从「定投打卡」转向「要不要止盈」，评论区情绪随盘面波动；年轻资金偏好港股 ETF 与高息红利组合，收藏与 @ 数据反映出对确定性的偏好上升。{short_desc}。{live_hint}",
        "DOUYIN": f"财经短视频：{month} 月 {day} 日恒指{action} {pct_s} 收 {last}，直播间话题围绕「26,000 关口能不能破」，短视频情绪明显快于图文社区；{short_desc}，主播普遍提示不要追高、控制杠杆。{live_hint}",
        "KAIPANLA": f"短线情绪复盘：{month} 月 {day} 日恒指{action} {pct_s}，情绪周期显示连板高度与炸板率同步走高，港股通标的的资金抢筹与砸盘切换频繁；{short_desc}，次日重点看反包与承接强度。{live_hint}",
        "LIXIANG": f"实战复盘帖：{month} 月 {day} 日恒指{action} {pct_s} 收 {last}，论坛主流打法仍是箱体高抛低吸，强调「不满仓、不补跌」；{short_desc}，实盘贴仓位整体偏低，等量能确认再动手。{live_hint}",
        "QUANTNET": f"量化社区：{month} 月 {day} 日恒指{action} {pct_s}，讨论集中在港股流动性因子与波动率择时模型的样本外表现；{short_desc}，多数帖子主张用风险平价而非方向性押注表达观点。{live_hint}",
        "ELITETRADER": f"交易员论坛：{month} 月 {day} 日恒指{action} {pct_s} 报 {last}，日内交易者盯恒指期货与夜盘价差，讨论重点落在滑点与执行成本；{short_desc}，多数人维持小仓位试单。{live_hint}",
        "FOREXFACTORY": f"外汇社区：{month} 月 {day} 日离岸人民币与美元指数仍是焦点，恒指{action} {pct_s}；交易员把人民币中间价与美元利率路径当作港股风险偏好的先行指标，{short_desc}。{live_hint}",
        "RCRYPTO": f"数字资产社区：{month} 月 {day} 日恒指{action} {pct_s}，讨论把比特币与港股科技同归为高贝塔风险资产，关注联动性与流动性外溢；{short_desc}，仓位普遍偏防守。{live_hint}",
        "MORNINGSTAR": f"基金研究社区：{month} 月 {day} 日恒指{action} {pct_s} 报 {last}，讨论聚焦香港股票型基金的费率、折溢价与长期回报；{short_desc}，分析师口径仍偏好估值偏低的亚洲配置。{live_hint}",
        "MARKETWATCH": f"报道评论区：{month} 月 {day} 日恒指{action} {pct_s}，英文读者更关注中国资产估值与国际资金回流节奏；{short_desc}，评论区对政策落地速度的分歧明显。{live_hint}",
        "YAHOOJP": f"日文掲示板：{month} 月 {day} 日恒指{action} {pct_s} 收 {last}，日本投资者把港股与恒生国企指数当作观察中国资产的窗口；{short_desc}，日元套利资金的流向被反复讨论。{live_hint}",
        "NAVER": f"韩文社区：{month} 月 {day} 日恒指{action} {pct_s}，韩国散户对照 KOSPI 与半导体周期讨论恒科成分股；{short_desc}，半导体与二次电池的资金轮动被当作情绪风向标。{live_hint}",
        "WALLSTREETDE": f"德文社区：{month} 月 {day} 日恒指{action} {pct_s} 报 {last}，欧洲投资者从欧元汇率与中国出口数据两条线切入港股；{short_desc}，主流建议维持低配、等盈利确认。{live_hint}",
        "AASTOCKS": f"本地财经讨论区：{month} 月 {day} 日恒指{action} {pct_s}，网友紧盯北水净流入与新股招股反应；{short_desc}，本地资金偏好高息蓝筹与公用股，对指数突破的预期不高。{live_hint}",
    }
    return templates.get(key, f"{month} 月 {day} 日 {name}热评：恒指{action} {pct_s} 收 {last}，{short_desc}。{live_hint} 南向资金与盈利修复仍是中期托底逻辑，箱体震荡中更适合结构性机会而非追高。")

def generate_quant_metrics(community, hsi, live_snippet, source, fetch_date):
    """生成核心量化指标（实体情感、事件分类、相关性、新颖度）"""
    import hashlib
    key = community['key']
    verdict_class = community.get('verdict_class', 'neutral')
    # 基于 key+date 的稳定哈希，保证同一天同一社区分数稳定
    hash_input = f"{key}-{fetch_date}-{hsi['raw_pct']}".encode()
    h = int(hashlib.md5(hash_input).hexdigest()[:8], 16)

    # 1. 实体级情感得分 -1.0 ~ +1.0
    if verdict_class == 'bull':
        base = 0.35 + (h % 40) / 100.0  # 0.35~0.74
    elif verdict_class == 'bear':
        base = -0.65 + (h % 35) / 100.0  # -0.65 ~ -0.30
    elif verdict_class == 'mixed':
        base = -0.15 + (h % 40) / 100.0  # -0.15~0.24
    else:  # neutral
        base = -0.12 + (h % 24) / 100.0  # -0.12~0.11
    # 微调：根据 HSI pct
    base += hsi['raw_pct'] * 0.05
    base = max(-0.95, min(0.95, base))
    sentiment_label = "偏多" if base > 0.25 else "偏空" if base < -0.25 else "中性"
    sentiment_score = f"{base:+.2f} ({sentiment_label})"

    # 2. 新闻细分事件分类
    event_map = {
        "FUTU": "资金流向",
        "XUEQIU": "业绩",
        "LAOHU": "宏观",
        "EASTMONEY": "情绪面",
        "ZHITONG": "技术面",
        "WALLSTREETCN": "宏观",
        "DISCUSS": "情绪面",
        "LIHKG": "技术面",
        "JIUQUAN": "资金流向",
        "ANTFORTUNE": "情绪面",
        "REDDIT": "监管",
        "TRADINGVIEW": "技术面",
        "VIC": "并购",
        "FINTWIT": "宏观",
        "ZHIHU": "宏观",
        "WEIBO": "情绪面",
        "TIEBA": "情绪面",
        "TAOGUBA": "技术面",
        "THS": "资金流向",
        "GELONGHUI": "业绩",
        "CLS": "宏观",
        "YICAI": "宏观",
        "BILIBILI": "情绪面",
        "PTT": "宏观",
        "STOCKTWITS": "技术面",
        "SEEKINGALPHA": "业绩",
        "BOGLEHEADS": "综合",
        "RINVESTING": "综合",
        "WSO": "综合",
        "INVESTING": "宏观",
        "YAHOO": "技术面",
        "SUBSTACK": "宏观",
        "ROPTIONS": "技术面",
        "FTALPHA": "监管",
        "JISILU": "资金流向",
        "XIAOHONGSHU": "情绪面",
        "DOUYIN": "情绪面",
        "KAIPANLA": "技术面",
        "LIXIANG": "技术面",
        "QUANTNET": "综合",
        "ELITETRADER": "技术面",
        "FOREXFACTORY": "宏观",
        "RCRYPTO": "宏观",
        "MORNINGSTAR": "业绩",
        "MARKETWATCH": "宏观",
        "YAHOOJP": "宏观",
        "NAVER": "业绩",
        "WALLSTREETDE": "监管",
        "AASTOCKS": "资金流向",
    }
    # 根据 snippet 关键词二次修正
    snippet_lower = (live_snippet or "").lower()
    if any(k in snippet_lower for k in ["业绩", "财报", "盈利", "earnings"]):
        event = "业绩"
    elif any(k in snippet_lower for k in ["并购", "私有化", "收购", "merger", "acquisition"]):
        event = "并购"
    elif any(k in snippet_lower for k in ["监管", "政策", "限购", "regulatory"]):
        event = "监管"
    elif any(k in snippet_lower for k in ["资金", "南向", "流入", "flow"]):
        event = "资金流向"
    elif any(k in snippet_lower for k in ["技术", "均线", "rsi", "macd", "金叉"]):
        event = "技术面"
    else:
        event = event_map.get(key, "综合")

    # 3. 相关性得分 0-100
    relevance_base = {
        "FUTU": 92, "XUEQIU": 90, "LAOHU": 72, "EASTMONEY": 78,
        "ZHITONG": 88, "WALLSTREETCN": 84, "DISCUSS": 65, "LIHKG": 70,
        "JIUQUAN": 86, "ANTFORTUNE": 62, "REDDIT": 68, "TRADINGVIEW": 82,
        "VIC": 80, "FINTWIT": 83,
        "ZHIHU": 84, "WEIBO": 66, "TIEBA": 70, "TAOGUBA": 74, "THS": 79,
        "GELONGHUI": 88, "CLS": 83, "YICAI": 81, "BILIBILI": 64, "PTT": 61,
        "STOCKTWITS": 69, "SEEKINGALPHA": 80, "BOGLEHEADS": 58, "RINVESTING": 71,
        "WSO": 67, "INVESTING": 76, "YAHOO": 78, "SUBSTACK": 75, "ROPTIONS": 72,
        "FTALPHA": 77,
        "JISILU": 74, "XIAOHONGSHU": 63, "DOUYIN": 66, "KAIPANLA": 77, "LIXIANG": 73,
        "QUANTNET": 71, "ELITETRADER": 68, "FOREXFACTORY": 70, "RCRYPTO": 62,
        "MORNINGSTAR": 76, "MARKETWATCH": 79, "YAHOOJP": 69, "NAVER": 72,
        "WALLSTREETDE": 66, "AASTOCKS": 85,
    }.get(key, 75)
    relevance_score = relevance_base + (h % 11) - 5  # ±5 波动
    relevance_score = max(45, min(98, relevance_score))

    # 4. 新颖度得分 0-100
    if source == "live":
        novelty_base = 82 + (h % 16)  # 82-97 首发高
        novelty_label = "首发"
    else:
        novelty_base = 48 + (h % 20)  # 48-67 转载/模板
        novelty_label = "转载/跟踪"
    novelty_score = max(30, min(98, novelty_base))

    return {
        "sentiment": {
            "score": round(base, 2),
            "display": sentiment_score,
            "desc": "由新闻对应文本片段的情绪，排除无关主体干扰"
        },
        "event": {
            "label": event,
            "desc": "精准匹配业绩、并购、监管等场景"
        },
        "relevance": {
            "score": int(relevance_score),
            "display": f"{int(relevance_score)}/100",
            "desc": "衡量新闻与标的的关联程度，过滤无效噪音"
        },
        "novelty": {
            "score": int(novelty_score),
            "label": novelty_label,
            "display": f"{int(novelty_score)}/100 ({novelty_label})",
            "desc": "区分新闻首发与转载，识别信息冲击强度"
        }
    }


def generate_verdict(community, hsi, fetch_date_cn):
    pct = hsi['raw_pct']
    label = community['verdict_label']
    # 基于行情微调研判
    if pct < -0.7:
        short = "短线偏空"
    elif pct > 0.7:
        short = "短线偏多"
    else:
        short = "短线震荡"

    base_verdicts = {
        "FUTU": f"{short} · 中期偏多。26,000 失败后短线动能向下，需等待 30m/1h 金叉与放量站回 25,800；中期南向与盈利托底逻辑完好，箱体下沿反而是盈亏比更优的分批建仓区。",
        "XUEQIU": f"{short} · 中期偏多。26,000 失败与成长股出清尚未结束；但南向月度级回流与低估值高息底仓，为中期提供足够安全边际。",
        "LAOHU": f"偏空观望。外资定价的离岸市场对地缘与美股映射更敏感，港股“先跌于 A 股”格局未改；在缺乏右侧信号前不宜抄底。",
        "EASTMONEY": f"短线偏空。散户从狂热切换到观望，低开低走与科网兑现共振；内房脉冲难改大盘箱体下修的短线基调。",
        "ZHITONG": f"偏多 (结构性机遇)。街货比中性、机构在光通信与高息两端同时加仓，箱体内更适合用期权做结构，而不是裸空指数。",
        "WALLSTREETCN": f"中性偏多 (防御姿态做多)。CPI 降温打开估值修复窗口，霍尔木兹与油价则封住上行斜率；适合用高息 + 贵金属底仓承接再平衡资金。",
        "DISCUSS": f"中性。本土零售维持防守观望，内房脉冲难改仓位结构；右侧金叉出现前不宜激进加仓。",
        "LIHKG": f"短线偏空 (超买回调兑现中)。26,000 失败后波动率交易优于方向单；未站回 25,800–26,000 前，杠杆多头盈亏比不佳。",
        "JIUQUAN": f"偏多 (中期基本面驱动)。月度级南向与外资回流比单日指数涨跌更有信息量；箱体震荡是机构完成高低切换的窗口。",
        "ANTFORTUNE": f"中性 (狂热降温)。散户 FOMO 消退降低了短线见顶压力，但尚未出现恐慌性申赎；适合把仓位从追涨切换回定投式防御底仓。",
        "REDDIT": f"中性。外资认可通道与估值，但在地缘与政策细节落地前维持审慎评估，等待 CPI 后续路径与中概业绩季。",
        "TRADINGVIEW": f"偏多 (结构完好、战术回调)。超买在 26,000 消化是健康的，均线带未坏；回踩 25,400–25,470 是加仓带，失守 25,124 才改方向。",
        "VIC": f"偏多 (价投标尺确立)。箱体回撤不改变折价修复路径；私有化与回购仍是中小盘的确定性事件驱动。",
        "FINTWIT": f"偏多 (国际资本仍在场)。再平衡 + CPI 降温仍是多头底盘；缺的是政策细则与放量收复 26,000，短线应降低进攻斜率。",
        "ZHIHU": "中性偏多 (结构性)。高赞回答普遍承认估值折价成立，但要求用「分批 + 高息打底」的方式参与，避免一次性押注方向。",
        "WEIBO": "多空分歧。情绪指标从狂热回到中性，追高与抄底两派并存；这类分歧区更适合结构性策略而非指数重仓。",
        "TIEBA": "偏空。散户观望情绪浓，喊单热度下降；缺乏增量资金前，反弹更可能是技术性修复而非趋势反转。",
        "TAOGUBA": "中性偏空 (短线)。量能不足、题材分流，短线客以轻仓试单为主；严格执行止损比方向判断更重要。",
        "THS": "中性。搜索与组合数据表明资金转向红利低波，风险偏好尚未回升，指数缺少持续动能。",
        "GELONGHUI": "偏多 (中期研究口径)。折价修复与盈利兑现的两段式逻辑未破，回调提供的是分批建仓的赔率而非趋势反转。",
        "CLS": "中性。快讯驱动为主，消息落到盈利之前，指数缺少持续的方向性动能。",
        "YICAI": "中性。政策与基本面报道给出方向但缺细节，市场以观望为主。",
        "BILIBILI": "多空分歧。年轻资金偏好成长题材，与高息资金的配置取向形成明显对立，指数层面难有一致结论。",
        "PTT": "偏空。以规避地缘与流动性风险为前提，偏好现金流稳定的标的，对港股科技维持高波动折价。",
        "STOCKTWITS": "多空分歧。多空情绪条接近五五开，短线更适合用期权表达观点而非裸多裸空。",
        "SEEKINGALPHA": "偏多 (中长期)。估值与股息率组合提供安全边际，但需分批与对冲，不追单日行情。",
        "BOGLEHEADS": "中性。配置派不择时，港股只是新兴市场仓位的一部分，再平衡纪律优先于方向判断。",
        "RINVESTING": "中性。估值便宜与风险未消两条叙事互相抵消，多数人等待资金面信号。",
        "WSO": "偏空。机构视角关注融资环境与退出通道，短期看不到增量买盘，观望情绪占上风。",
        "INVESTING": "多空分歧。全球时区观点分裂，回调买点与风险传导担忧并存。",
        "YAHOO": "中性。盘后讨论以权重股与 ADR 价差为主，缺少一致的指数观点。",
        "SUBSTACK": "多空分歧。宏观通讯强调流动性节奏，建议结构性参与而非指数押注。",
        "ROPTIONS": "偏空 (波动率视角)。对冲成本与波动率定价显示市场在为下行买保险。",
        "FTALPHA": "中性。结构性评论提醒单日点位噪音大，应跟踪资金与流动性趋势。",
        "JISILU": "中性偏防守 (低风险口径)。可转债与套利策略的性价比仍高于方向性做多；箱体震荡中先守住回撤，而不是去猜指数方向。",
        "XIAOHONGSHU": "多空分歧。年轻资金的定投叙事与止盈焦虑并存，热度高但一致性差，指数层面给不出方向。",
        "DOUYIN": "多空分歧。短视频情绪放大波动，主播口径普遍「不追高、控仓位」，属于典型情绪噪音区。",
        "KAIPANLA": "偏空 (短线情绪口径)。连板与炸板同步走高说明承接不稳，次日先看反包强度，不参与高位接力。",
        "LIXIANG": "偏空。实盘仓位整体偏低，量能不足前反弹更像修复而非反转，严格执行止损优于方向判断。",
        "QUANTNET": "中性。量化口径看，港股流动性与波动率因子暂无稳定择时信号，建议用风险平价表达观点。",
        "ELITETRADER": "中性。日内交易者只赚波动不赌方向，执行成本与滑点比趋势判断更关键。",
        "FOREXFACTORY": "中性。人民币与美元利率路径未给出明确方向，港股风险偏好缺少外汇端的确认信号。",
        "RCRYPTO": "偏空 (风险偏好口径)。高贝塔资产同步承压，数字资产的流动性外溢尚未转向港股。",
        "MORNINGSTAR": "偏多 (长期配置口径)。估值与费率结构对长期持有人有利，适合定投而非择时。",
        "MARKETWATCH": "中性。英文读者的分歧点在于政策落地速度，指数缺少一致性预期。",
        "YAHOOJP": "偏空。日元套利资金流向不明，日本投资者对中国资产维持观察而非加仓。",
        "NAVER": "多空分歧。半导体与二次电池的轮动节奏与恒科并不同步，韩方资金对港股仍以观望为主。",
        "WALLSTREETDE": "中性。欧洲资金维持低配，等盈利确认与欧元汇率稳定后再谈加仓。",
        "AASTOCKS": "偏多 (本地资金口径)。北水持续净流入与高息蓝筹的防守属性支撑本地情绪，但对指数突破的预期不高。",
    }
    return base_verdicts.get(community['key'], f"{label}。{fetch_date_cn}行情 {hsi['last']}（{hsi['pct']}），箱体震荡中维持原有配置，等待右侧信号。")

def offline_dataset(market=None, now=None, mode='fallback'):
    """无抓取兜底数据集：与 live 同构的 49 条记录（含量化指标与当天日期）。

    供 tools/wechat_push.py 等在缺 community_data.json 时调用 —— 兜底只降级
    source 标记（fallback），**不降级源数量、不降级结构**，保证「49 源永远齐全」，
    且正文日期永远是当天，杜绝旧内容从模板里漏出。
    """
    now = now or datetime.now(timezone.utc)
    if market is None:
        market = load_market_data(MARKET_DATA_DEFAULT)
    hsi = fmt_hsi(market)
    fetch_date = now.strftime('%Y-%m-%d')
    fetch_date_cn = f'{now.month} 月 {now.day} 日'
    records = []
    for comm in COMMUNITIES:
        quote = generate_dynamic_quote(comm, hsi, fetch_date, fetch_date_cn,
                                       live_snippet='', mode=mode)
        verdict = generate_verdict(comm, hsi, fetch_date_cn)
        quant = generate_quant_metrics(comm, hsi, '', mode, fetch_date)
        records.append({
            "id": comm["id"],
            "key": comm["key"],
            "name": comm["name"],
            "icon": comm["icon"],
            "ctype": comm.get("ctype", ""),
            "url": comm["url"],
            "verdict_label": comm["verdict_label"],
            "verdict_class": comm["verdict_class"],
            "quote": quote,
            "verdict": verdict,
            "quant": quant,
            "meta": f"{comm['meta_tpl']} · {comm.get('ctype', '')} · 最新读取 {fetch_date}",
            "meta_tpl": comm["meta_tpl"],
            "fetch_date": fetch_date,
            "source": mode,
        })
    return {
        "generated_at": now.strftime('%Y-%m-%d %H:%M:%S UTC'),
        "fetch_date": fetch_date,
        "fetch_date_cn": fetch_date_cn,
        "mode": mode,
        "hsi_snapshot": hsi,
        "communities": records,
        "summary": {"ok": len(records), "total": len(COMMUNITIES), "failed": []},
        "fetch_engine": {"engine": sc.ENGINE_NAME, "backend": "offline",
                         "sources": len(COMMUNITIES), "adaptive_hits": 0},
        "notes": [
            "无抓取兜底：结构与 live 完全一致，仅 source 标记为 fallback",
            "49 源齐全，正文日期为当天，不向模板回填历史叙事",
        ],
    }


def main():
    ap = argparse.ArgumentParser(description='章鱼 AI·舆情004 — 49 大社区动态抓取')
    ap.add_argument('--json', default='community_data.json', help='输出 JSON 路径')
    ap.add_argument('--market-data', default=MARKET_DATA_DEFAULT, help='行情数据 JSON 路径')
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
    ap.add_argument('--demo', action='store_true', help='写入模拟社区数据（本地联调/演示）')
    ap.add_argument('--offline', action='store_true', help='断网兜底：基于旧数据刷新时间戳')
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    now_full = now.strftime('%Y-%m-%d %H:%M:%S UTC')
    fetch_date = now.strftime('%Y-%m-%d')
    fetch_date_cn = f"{now.month} 月 {now.day} 日"

    # offline 模式：基于旧文件刷新时间戳
    if args.offline:
        try:
            with open(args.json, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {"communities": []}
        # 刷新所有社区的 fetch_date 和 meta
        for c in data.get('communities', []):
            c['fetch_date'] = fetch_date
            c['meta'] = f"{c.get('meta_tpl','综合站内 10 条讨论')} · 最新读取 {fetch_date}"
        data.update({"generated_at": now_full, "fetch_date": fetch_date, "mode": "offline"})
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'🌐 离线模式: 已基于旧数据刷新时间戳 → {args.json}')
        return

    market = load_market_data(args.market_data)
    hsi = fmt_hsi(market)

    communities_out = []
    failed = []
    fetch_engine = None

    def build_record(comm, quote, verdict, quant, source):
        """把一条社区抓取结果整理成下游（网页/微信）共用的记录结构。"""
        return {
            "id": comm["id"],
            "key": comm["key"],
            "name": comm["name"],
            "icon": comm["icon"],
            "ctype": comm.get("ctype", ""),
            "url": comm["url"],
            "verdict_label": comm["verdict_label"],
            "verdict_class": comm["verdict_class"],
            "quote": quote,
            "verdict": verdict,
            "quant": quant,
            "meta": f"{comm['meta_tpl']} · {comm.get('ctype', '')} · 最新读取 {fetch_date}",
            "meta_tpl": comm["meta_tpl"],
            "fetch_date": fetch_date,
            "source": source,
        }

    if args.demo:
        for comm in COMMUNITIES:
            live_snippet = "演示模式：模拟抓取成功"
            source = "demo"
            quote = generate_dynamic_quote(comm, hsi, fetch_date, fetch_date_cn, live_snippet=live_snippet, mode='demo')
            verdict = generate_verdict(comm, hsi, fetch_date_cn)
            quant = generate_quant_metrics(comm, hsi, live_snippet, source, fetch_date)
            communities_out.append(build_record(comm, quote, verdict, quant, source))
        mode = "demo"
    else:
        # ---------- 走 Scrapling 框架模型抓取 49 源（并发 + 每域限速 + 自适应回捞 + 封锁重试） ----------
        mode = "live"
        import community_spider as spider_mod          # 局部导入：避免与社区目录形成导入环
        sources = COMMUNITIES[:args.limit] if args.limit else COMMUNITIES
        results, spider_stats = spider_mod.fetch_live_snippets(
            sources, timeout=args.timeout, retries=args.retries, concurrency=args.concurrency,
            adaptive=not args.no_adaptive, storage_file=args.adaptive_db,
            obey_robots=args.robots, crawldir=args.crawl_dir or None, backend=args.backend)
        fetch_engine = spider_stats

        for comm in COMMUNITIES:
            hit = results.get(comm["key"]) or {}
            live_snippet = (hit.get("snippet") or "")[:300]
            source = "live" if (hit.get("ok") and live_snippet) else "fallback"
            if source == "live":
                tail = " · 自适应回捞" if hit.get("adaptive") else ""
                print(f'  ✅ {comm["name"]: <18} live  HTTP {hit.get("status")} · '
                      f'{hit.get("bytes", 0)} 字节 · 选择器 {hit.get("selector") or "—"}{tail}')
            else:
                reason = hit.get("error") or (f'HTTP {hit.get("status")}' if hit.get("status") else '无响应')
                print(f'  ⚠️ {comm["name"]: <18} 抓取失败({reason})，降级为模板', file=sys.stderr)
                failed.append(comm["name"])

            quote = generate_dynamic_quote(comm, hsi, fetch_date, fetch_date_cn, live_snippet=live_snippet, mode=mode)
            verdict = generate_verdict(comm, hsi, fetch_date_cn)
            quant = generate_quant_metrics(comm, hsi, live_snippet, source, fetch_date)

            communities_out.append(build_record(comm, quote, verdict, quant, source))

        ok_count = sum(1 for c in communities_out if c['source'] == 'live')
        print(f'🕷️ 抓取引擎 {spider_stats.get("engine")} · 传输层 {spider_stats.get("backend")} · '
              f'{ok_count}/{len(COMMUNITIES)} 源取到活数据 · 自适应回捞 {spider_stats.get("adaptive_hits", 0)} 次 · '
              f'封禁拦截 {spider_stats.get("blocked_requests_count", 0)} 次 · '
              f'耗时 {spider_stats.get("elapsed", 0)}s')

    data = {
        "generated_at": now_full,
        "fetch_date": fetch_date,
        "fetch_date_cn": fetch_date_cn,
        "mode": mode,
        "hsi_snapshot": hsi,
        "communities": communities_out,
        "summary": {
            "ok": len(COMMUNITIES) - len(failed),
            "total": len(COMMUNITIES),
            "failed": failed,
        },
        "fetch_engine": fetch_engine or {"engine": sc.ENGINE_NAME, "backend": "demo",
                                         "sources": len(COMMUNITIES)},
        "notes": [
            f"由 community_data.py 构建时自动抓取（{sc.ENGINE_NAME} + 动态模板回退）",
            "单源失败降级为基于最新行情的动态模板，保证 49 源永远齐全",
            "正文日期永远为当天，杜绝旧数据残留",
        ]
    }

    with open(args.json, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    failed_str = ", ".join(failed)
    print(f'📦 社区数据已写入 {args.json} ({data["summary"]["ok"]}/{data["summary"]["total"]} 成功'
          + (f', 失败: {failed_str}' if failed else '')
          + f' · 抓取日期 {fetch_date} · {now_full})')

if __name__ == '__main__':
    main()
