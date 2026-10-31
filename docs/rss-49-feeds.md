# 49 大财经 RSS 情报源 · 目录与端点核对表

> 本文件由 `python3 rss_feeds.py --catalog-md docs/rss-49-feeds.md` 生成，
> 请勿手改 —— 目录的唯一事实源是 `rss_feeds.py` 的 `CATALOG`（`tests/test_rss_feeds.py` 会断言两者一致）。

**核验状态**：`native` 站点官方公开 RSS · `rsshub_doc` RSSHub 官方路由表里有此路由（路径按路由源码核对）·
`needs_check` 地址来自公开资料、站点改版可能失效 · `carrier` 站点无公开 RSS，用 Google News 检索载体（如实标注，不冒充原生源）。

**端点优先级**：按列表顺序回退；`rsshub:/xxx` 会按 `RSSHUB_BASE` 的实例链展开（官方 `rsshub.app` + 3 个探活镜像，逗号分隔、按序回退）。

**RSSHub 出口说明**：GitHub Actions 的出口 IP 会被 `rsshub.app` 前的 Cloudflare 直接回 403（同一路线在其它出口是 200），因此默认挂多实例轮换：403/429 视为「边缘按 IP 拦截」→ 自动换下一个实例；连续多个实例被拦则本轮熔断，05 节按目录回退原生 RSS / 检索载体，不会把构建拖死。换出口的另一种方式是用 `RSSHUB_PROXY` 指定出站代理。注意区分：403 可换实例救回；**200 + RSSHub 错误页**（上游 401、需要浏览器）属路由自身坏死，换实例无效。

合计 **49 源**，六大板块：一、国际宏观与顶级政经媒体 10 源 · 二、美股、科技创新与公司财报 10 源 · 三、A 股、港股与大中华区财经 11 源 · 四、大宗商品、外汇与衍生品 8 源 · 五、加密货币与 Web3 前沿 6 源 · 六、顶级量化与独立投资人专栏 4 源

## 一、国际宏观与顶级政经媒体（10 源）

_全球央行动态、利率走向与跨国经济周期_

| # | 源 | 语种 | 主端点 | 回退端点 | 核验 | 说明 |
|---|---|---|---|---|---|---|
| 1 | 经济学人 · 金融与经济 | 英 | `https://www.economist.com/finance-and-economics/rss.xml` | `rsshub:/economist/finance-and-economics` | `native` | 《经济学人》官方「Finance & economics」板块 RSS，全球经济大势定调。 |
| 2 | FT 全球经济 | 英 | `https://www.ft.com/rss/home` | `rsshub:/ft/home` | `native` | 英国《金融时报》官方 RSS 首页源（含全球经济/市场要闻）；付费墙文章只给摘要。 |
| 3 | WSJ 经济板块 | 英 | `rsshub:/wsj/en-us/economy` | `rsshub:/wsj/zh-cn/economy` | `rsshub_doc` | RSSHub 路由 /wsj/:lang/:category（lang=en-us，category=economy，见官方路由表）。 |
| 4 | 路透社全球财经 | 英 | `rsshub:/reuters/business` | `rsshub:/reuters/world/us` | `rsshub_doc` | RSSHub 有 /reuters/:category/:topic 路由（分类随站点改版变动）；不可用时回退 Google News 路透检索，如实标注为聚合检索。 |
| 5 | 彭博社市场分析 | 英 | `https://feeds.bloomberg.com/markets/news.rss` | `rsshub:/bloomberg/markets` | `native` | 彭博官方 markets 分站 RSS（RSSHub /bloomberg/:site 同源，作回退）。 |
| 6 | CNBC 宏观 | 英 | `https://www.cnbc.com/id/10000664/device/rss/rss.html` | — | `native` | CNBC 官方 Economy 板块 RSS（id 10000664）。 |
| 7 | MarketWatch 宏观 | 英 | `https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines` | `https://feeds.content.dowjones.io/public/rss/mw_topstories` | `native` | 道琼斯官方 MarketWatch 实时头条 RSS。 |
| 8 | 哈佛商业评论 | 英 | `https://feeds.hbr.org/harvardbusiness` | `rsshub:/hbr/topic/Leadership/From%20the%20Store` | `native` | HBR 官方订阅源；RSSHub /hbr/topic/:topic 已不再提供 Popular 流（路由说明）。 |
| 9 | 纽约时报财金 | 英 | `https://rss.nytimes.com/services/xml/rss/nyt/Business.xml` | `rsshub:/nytimes/rss/Business` | `native` | NYT 官方 Business RSS（RSSHub /nytimes/rss/:cat 同源）。 |
| 10 | 福布斯商业 | 英 | `https://www.forbes.com/business/feed/` | — | `native` | Forbes 官方 business 频道 RSS（单端点，改版即失效 → DeepSeek 检索兜底）。 |

## 二、美股、科技创新与公司财报（10 源）

_美股大盘、明星科技股、创新逻辑与个股研报_

| # | 源 | 语种 | 主端点 | 回退端点 | 核验 | 说明 |
|---|---|---|---|---|---|---|
| 11 | WSJ 全美商业 | 英 | `rsshub:/wsj/en-us/business` | `rsshub:/wsj/zh-cn/business` | `rsshub_doc` | RSSHub /wsj/en-us/business（官方路由表 category=business）。 |
| 12 | ARK Invest（方舟投资） | 英 | `https://ark-invest.com/feed/` | `https://ark-invest.com/feed/?post_type=research` | `needs_check` | ARK 官方站 RSS（WordPress feed）；站点改版会失效，失败如实标未获取。 |
| 13 | TechCrunch 创投 | 英 | `https://techcrunch.com/feed/` | `https://techcrunch.com/category/venture/feed/` | `native` | TechCrunch 官方 RSS（创投分类另有 category/venture/feed/）。 |
| 14 | Barron's（巴伦周刊） | 简中 | `rsshub:/barronschina` | `rsshub:/barronschina/1` | `rsshub_doc` | RSSHub /barronschina/:id（默认快讯栏目；原版 barrons.com 无公开 RSS）。 |
| 15 | Seeking Alpha 美股选股 | 英 | `https://seekingalpha.com/market-news.rss` | `rsshub:/seekingalpha/TSM/news` | `native` | Seeking Alpha 官方 market-news RSS；RSSHub /seekingalpha/:symbol/:category 可按个股取。 |
| 16 | Investor's Business Daily | 英 | `https://www.investors.com/feed/` | — | `native` | IBD 官方站点 RSS（CAN SLIM 与市场趋势分析栏目）。 |
| 17 | 雅虎财经美股 | 英 | `https://finance.yahoo.com/news/rssindex` | `rsshub:/yahoo/finance/latest-news` | `native` | Yahoo Finance 官方新闻 RSS；RSSHub /yahoo/finance/:topic 可换主题。 |
| 18 | 福布斯市场 | 英 | `https://www.forbes.com/markets/feed/` | — | `native` | Forbes 官方 markets 频道 RSS（2026-10-31 起该路径返回 404，原生源失效时走 DeepSeek 检索兜底）。 |
| 19 | Benzinga 美股新闻 | 英 | `https://www.benzinga.com/feed` | `https://www.benzinga.com/feed/rss` | `native` | Benzinga 官方新闻 RSS（盘前盘后异动）。 |
| 20 | The Motley Fool（傻瓜投资） | 英 | `https://www.fool.com/feeds/index.aspx` | — | `needs_check` | Motley Fool 老式 RSS 出口（feeds/index.aspx）；失效时如实标未获取。 |

## 三、A 股、港股与大中华区财经（11 源）

_国内政策、板块轮动、港股外资动向与公司公告_

| # | 源 | 语种 | 主端点 | 回退端点 | 核验 | 说明 |
|---|---|---|---|---|---|---|
| 21 | 华尔街见闻实时 | 简中 | `rsshub:/wallstreetcn/live/global/2` | `rsshub:/wallstreetcn/news/global` | `rsshub_doc` | RSSHub /wallstreetcn/live/:category/:score（score=2 只看重要）与 /news/:category。 |
| 22 | 财联社电报 | 简中 | `rsshub:/cls/telegraph` | `rsshub:/cls/depth/1000` | `rsshub_doc` | RSSHub /cls/telegraph（另有 watch/announcement/red/hk_us 等分类）。 |
| 23 | 雪球今日话题 | 简中 | `rsshub:/xueqiu/today` | `rsshub:/xueqiu/hots`<br>`rsshub:/xueqiu/topic` | `rsshub_doc` | RSSHub /xueqiu/today（路由标记 antiCrawler，需实例可拿 Cookie；2026-10-31 实测 4 个公共实例全 403/超时 → DeepSeek 检索兜底）。 |
| 24 | 新浪财经聚合 | 简中 | `rsshub:/sina/finance/rollnews` | `rsshub:/sina/finance/china` | `rsshub_doc` | RSSHub /sina/finance/rollnews/:lid（默认 2519 财经滚动）。 |
| 25 | 财新网经济 | 简中 | `rsshub:/caixin/latest` | `rsshub:/caixin/economy/economy` | `rsshub_doc` | RSSHub /caixin/latest（另有 /caixin/:column/:category，column=economy 为经济板块）；付费内容只做更新提醒。 |
| 26 | 东方财富研报 | 简中 | `rsshub:/eastmoney/report/strategyreport` | `rsshub:/eastmoney/report/macresearch` | `rsshub_doc` | RSSHub /eastmoney/report/:category（strategyreport 策略 / macresearch 宏观 / industry 行业 / stock 个股）。 |
| 27 | 经济观察网 | 简中 | `gnews` 检索：经济观察网 宏观 行业 调查 | — | `carrier` | 经济观察网无公开 RSS，改用 Google News 中文检索载体（如实标注为聚合检索）。 |
| 28 | 巨潮资讯公告 | 简中 | `rsshub:/cninfo/announcement/szse/000002/gssz0000002/category_ndbg_szsh` | — | `rsshub_doc` | RSSHub /cninfo/announcement/:column/:code/:orgId 是**逐只股票**的公告路由；全市场公告用检索载体兜底，避免把单只个股公告冒充全市场监控。 |
| 29 | 中国证券网（上证报） | 简中 | `rsshub:/stcn/article/list/yw` | — | `carrier` | RSSHub 无 cnstock 路由；证券时报网 /stcn/article/list/yw 作同城财经媒体回退，正文如实标注每条条目的来源。 |
| 30 | 阿斯达克财经（AASTOCKS） | 繁中 | `rsshub:/hkej/index` | `rsshub:/hket/sran001` | `carrier` | RSSHub 无 aastocks 路由；港股本地媒体回退用信报 /hkej、香港经济日报 /hket。 |
| 31 | 36 氪（一级市场） | 简中 | `https://36kr.com/feed` | `rsshub:/36kr/newsflashes` | `native` | 36 氪官方 RSS（新经济/投融资快讯）；RSSHub /36kr/newsflashes 作回退。 |

## 四、大宗商品、外汇与衍生品（8 源）

_黄金、原油、美元指数与大宗供应链_

| # | 源 | 语种 | 主端点 | 回退端点 | 核验 | 说明 |
|---|---|---|---|---|---|---|
| 32 | 英为财情全球行情 | 英 | `https://www.investing.com/rss/news_25.rss` | `https://www.investing.com/rss/commodities_Opinion.rss` | `native` | Investing.com 官方分类 RSS（news_25 经济指标；商品/外汇各有对应编号）。 |
| 33 | Kitco 黄金 | 英 | `https://www.kitco.com/rss/KitcoNews.xml` | `https://www.kitco.com/rss/` | `needs_check` | Kitco 官方新闻 RSS（贵金属行情与分析）；站点改版会换路径。 |
| 34 | Oilprice 原油 | 英 | `https://oilprice.com/rss/main` | — | `native` | Oilprice.com 官方主源（油价、OPEC 政策与地缘风险）。 |
| 35 | FXStreet 外汇 | 英 | `https://www.fxstreet.com/rss/news` | — | `native` | FXStreet 官方新闻 RSS（主要货币对与技术分析）。 |
| 36 | DailyFX | 英 | `https://www.dailyfx.com/feeds/all` | — | `native` | DailyFX 官方全站 RSS（汇市与大宗商品预警）。 |
| 37 | 金十数据快讯 | 简中 | `rsshub:/jin10` | `rsshub:/jin10/1` | `rsshub_doc` | RSSHub /jin10/:important?（留空=全部快讯，传任意值只看重要）。 |
| 38 | 汇通网 | 简中 | `rsshub:/fx678/kx` | — | `rsshub_doc` | RSSHub /fx678/kx（7×24 小时快讯）。 |
| 39 | Freightos 航运指数 | 英 | `gnews` 检索：Freightos Baltic Index container freight rates | — | `carrier` | Freightos Baltic Index 是数据产品、无公开 RSS；用 Google News 检索载体追踪运价报道。 |

## 五、加密货币与 Web3 前沿（6 源）

_比特币、以太坊行情、监管政策与链上数据_

| # | 源 | 语种 | 主端点 | 回退端点 | 核验 | 说明 |
|---|---|---|---|---|---|---|
| 40 | CoinDesk | 英 | `https://www.coindesk.com/arc/outboundfeeds/rss/` | `rsshub:/coindesk/news` | `native` | CoinDesk 官方 Arc RSS（RSSHub /coindesk/news 同源，作回退）。 |
| 41 | Cointelegraph | 英 | `https://cointelegraph.com/rss` | — | `native` | Cointelegraph 官方 RSS。 |
| 42 | 律动 BlockBeats | 简中 | `rsshub:/theblockbeats/newsflash` | `rsshub:/theblockbeats/article` | `rsshub_doc` | RSSHub /theblockbeats/:channel?/:original?（默认 newsflash 快讯）。 |
| 43 | Foresight News | 简中 | `rsshub:/foresightnews/news` | `rsshub:/foresightnews` | `rsshub_doc` | RSSHub /foresightnews/news（快讯）、/foresightnews/article（文章）、/（精选）。 |
| 44 | The Block | 英 | `https://www.theblock.co/rss.xml` | `rsshub:/theblock/category/crypto-ecosystems` | `native` | The Block 官方 rss.xml；RSSHub /theblock/category/:category 可换分类。 |
| 45 | Bitcoin Magazine | 英 | `https://bitcoinmagazine.com/feed` | — | `native` | Bitcoin Magazine 官方 RSS（比特币生态与 Layer2）。 |

## 六、顶级量化与独立投资人专栏（4 源）

_顶尖投资人的长期生存法则与研究框架_

| # | 源 | 语种 | 主端点 | 回退端点 | 核验 | 说明 |
|---|---|---|---|---|---|---|
| 46 | 霍华德·马克斯备忘录（橡树资本） | 英 | `rsshub:/oaktree/insights` | — | `rsshub_doc` | RSSHub /oaktree/insights（Howard Marks 备忘录与橡树洞见）。 |
| 47 | Aswath Damodaran（估值大师） | 英 | `https://aswathdamodaran.blogspot.com/feeds/posts/default?alt=rss` | `https://aswathdamodaran.blogspot.com/feeds/posts/default` | `native` | Blogspot 官方 Atom/RSS 出口（纽约大学估值教授博客）。 |
| 48 | QuantStart（量化交易） | 英 | `https://www.quantstart.com/feed/` | `https://www.quantstart.com/articles/feed/` | `needs_check` | QuantStart 站点 RSS（算法、回测与数学模型）；站点改版会失效。 |
| 49 | Project Syndicate（世界报业辛迪加） | 英 | `https://www.project-syndicate.org/rss` | — | `needs_check` | Project Syndicate 官方 RSS（诺奖级经济学家专栏）。 |

## 用法

```bash
python3 rss_feeds.py                     # 抓取 49 源 → rssfeeds_data.json
python3 rss_feeds.py --catalog           # 终端打印目录
python3 rss_feeds.py --catalog-md docs/rss-49-feeds.md   # 重新生成本文件
python3 rss_feeds.py --only CLS,JIN10 --text             # 联调：只抓指定源
python3 rss_feeds.py --self-test          # 目录/解析/时效/兜底自检（不联网）
```

