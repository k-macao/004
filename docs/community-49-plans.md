# 49 社区源逐源独立抓取方案 · 核对表

> 本文件由 `python3 community_plans.py --plans-md docs/community-49-plans.md` 生成，
> 请勿手改 —— 方案的唯一事实源是 `community_plans.py` 的 `PLANS`
> （`tests/test_community_plans.py` 会断言两者一致）。

**RSSHub 上游核对基准**：`DIYgod/RSSHub` @ `4be79cd40cae69ce68b7837f927b63cdab7be719`（2026-11-06T06:51:41Z），核对方式：GitHub API 全量路由树 + 逐路由源码核对（本执行环境不能直连目标站点，未做在线实测）。

**步骤类型**：`adapter` 已有专用适配器 · `rss` 站点官方订阅 · `json_api` 站点公开接口 ·
`rsshub` RSSHub 路由（按实例链轮换）· `html` 公开页面摘录 · `carrier` Google News 检索载体。

**核验状态**：`native` 官方订阅 · `api_public` 公开接口（响应结构对着上游源码核对）·
`rsshub_doc` RSSHub 路由存在且参数已核对 · `needs_check` 地址来自公开资料/上游源码但未在线实测 ·
`carrier` 聚合检索载体 · `adapter` 本项目自有适配器。

**执行口径**：逐级尝试，第一个产出合格证据的步骤胜出；每源最多尝试 3 个步骤，整轮请求预算 90 次、墙钟预算 420 秒。所有条目仍要命中财经主题词并落在时间窗内，抓不到就标「未获取」，绝不回填历史内容。

合计 **49 源**；强度分布：有专用适配器 8 源 · 有订阅/公开接口 10 源 · 只有 RSSHub 14 源 · 只有摘录+载体 17 源

## 01 富途牛牛社区（`FUTU` · 中文行情社区）

- **目录 URL**：https://www.futunn.com/hk
- **方案**：牛牛圈帖子在 App 内且需登录；公开侧走富途官方快讯/要闻（news.futunn.com）→ 网页摘录 → 检索载体，全程如实标注「不是社区评论」。
- **强度**：rsshub
- **限制**：RSSHub 无牛牛圈（社区帖）路由，已核对全量路由树；不拿官方快讯冒充用户评论。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/futunn/live/Cantonese`、`rsshub:/futunn/main`、`rsshub:/futunn/topic/1267` | 上游 news.futunn.com：/live 为 7×24 快讯（lang 可选 Mandarin/Cantonese/English），/main 为要闻，/topic/:id 为专题。三者均为站点官方内容出口，非牛牛圈用户帖。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://news.futunn.com/hk/main/live`、`https://www.futunn.com/hk` | 粤语快讯页与港股首页静态摘录；牛牛圈帖子列表由前端渲染，静态 HTML 不保证命中。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:富途牛牛 港股 社区 讨论` | 最后兜底：用检索载体覆盖「富途社区在讨论什么」，明确标注为聚合检索。 |

## 02 雪球网（`XUEQIU` · 中文投资者社区）

- **目录 URL**：https://xueqiu.com/S/HKHSI
- **方案**：雪球个股讨论公开接口（按时间排序）→ RSSHub 同一路由 → 个股页摘录 → 检索载体。
- **强度**：feed

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `json_api` → `xueqiu_json` | `api_public` | `https://xueqiu.com/query/v1/symbol/search/status?u=11111&count=100&comment=0&symbol=HKHSI&source=all&sort=time` | 上游接口与字段按 RSSHub xueqiu/stock-comments.tsx 核对（requireConfig=false、antiCrawler=false）。接口按时间排序且不给点赞数，热度指标记 0 并写明口径。 |
| 2 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/xueqiu/stock_comments/HKHSI`、`rsshub:/xueqiu/hots` | /stock_comments/:id 与上面同一上游；/hots 上游需先从首页取 token（requirePuppeteer=true），公共实例不一定可用，故排第二。 |
| 3 | `html` → `html_excerpt` | `needs_check` | `https://xueqiu.com/S/HKHSI` | 个股页静态摘录（时间线由前端渲染，命中率低）。 |
| 4 | `carrier` → `gnews_carrier` | `carrier` | `gnews:雪球 港股 恒指 讨论` | 检索载体兜底，标注为聚合检索。 |

## 03 老虎社区（`LAOHU` · 中文跨境社区）

- **目录 URL**：https://www.laohu8.com
- **方案**：公开侧只有站点页面与检索载体；RSSHub 仅有 /laohu8/personal/:id（需指定用户 id），不用它冒充社区列表。
- **强度**：excerpt_only（无公开订阅/接口，只有页面摘录与检索载体）
- **限制**：已核对：RSSHub 无老虎社区列表路由，只有按用户 id 的个人主页路由；不指定某个用户来冒充「社区热帖」。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `html` → `html_excerpt` | `needs_check` | `https://www.laohu8.com`、`https://www.laohu8.com/hot` | 老虎社区首页与热门页静态摘录；帖子流主要由前端渲染。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:老虎社区 laohu8 港股 讨论` | 检索载体兜底。 |

## 04 东方财富港股股吧（`EASTMONEY` · 中文股吧论坛）

- **目录 URL**：https://guba.eastmoney.com
- **方案**：股吧列表页 + RSSHub 检索路由 → 页面摘录 → 检索载体。
- **强度**：rsshub
- **限制**：已核对：RSSHub 无「全市场股吧列表」路由（只有按 uid 的个人股吧与站内搜索）。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/eastmoney/search/港股`、`rsshub:/eastmoney/gerenzhongxin/guba/0` | /eastmoney/search/:keyword 为站内搜索；/gerenzhongxin/guba/:uid 是**按用户**的股吧动态，这里不指定真实用户，仅在实例接受占位时作为额外尝试，失败即降级。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://guba.eastmoney.com`、`https://guba.eastmoney.com/list,hk.html` | 股吧首页与港股列表页静态摘录；列表项由脚本渲染，静态命中率低。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:东方财富 股吧 港股 讨论` | 检索载体兜底。 |

## 05 智通财经互动区（`ZHITONG` · 中文资讯互动）

- **目录 URL**：https://www.zhitongcaijing.com
- **方案**：RSSHub 港股/推荐频道 → 站点港股频道页 → 检索载体。
- **强度**：rsshub

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/zhitongcaijing/hkstock`、`rsshub:/zhitongcaijing` | 上游 content/hkstock.html 与 content/recommend.html；该路由用 Playwright 取渲染后内容（requirePuppeteer），公共实例可能不可用，故必须留后两步。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://www.zhitongcaijing.com/content/hkstock.html`、`https://www.zhitongcaijing.com` | 港股频道页与首页静态摘录。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:智通财经 港股 互动` | 检索载体兜底。 |

## 06 华尔街见闻社区（`WALLSTREETCN` · 中文宏观社区）

- **目录 URL**：https://wallstreetcn.com
- **方案**：见闻公开资讯流/快讯/最热（RSSHub 路由，上游为官方 api-one 接口）→ 站点页面摘录 → 检索载体；见闻评论区需登录，如实标注不冒充。
- **强度**：rsshub
- **限制**：见闻「社区」评论接口需登录，不自动登录、不绕权限；用官方资讯流并写明口径。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/wallstreetcn/live/global/2`、`rsshub:/wallstreetcn/news/global`、`rsshub:/wallstreetcn/hot` | 上游分别为 api-ddc-wscn.awtmt.com 快讯、api-one.wallstcn.com 资讯流、apiv1/content/articles/hot 最热文章；均无需 Cookie。score=2 只看重要快讯。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://wallstreetcn.com`、`https://wallstreetcn.com/live/global` | 首页与实时快讯页静态摘录。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:华尔街见闻 港股 宏观` | 检索载体兜底。 |

## 07 香港讨论区财经版（`DISCUSS` · 粤语本地论坛）

- **目录 URL**：https://www.discuss.com.hk/forumdisplay.php?fid=115
- **方案**：站点官方 RSS → 已有 Discuz 专用回复采样 → RSSHub archiver 路由 → archiver 页摘录。
- **强度**：adapter

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `native` | `https://www.discuss.com.hk/rss.php?fid=115` | Discuz 原生 rss.php（财经版 fid=115），与既有 RSS_FEEDS 映射同一端点。 |
| 2 | `adapter` → `discuz_html` | `adapter` | —（由适配器执行） | 已有专用两阶段采样：forumdisplay.php?fid=115 列表 → 帖内前 20 楼回复；按所属帖回复数排序，不代表站内点赞。 |
| 3 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/discuss/115` | 上游走 **archiver 轻量列表**（/archiver/?fid-115.html）而非主站页，对反爬更友好；路由标 antiCrawler=true，实例可能被拦。 |
| 4 | `html` → `html_excerpt` | `needs_check` | `https://www.discuss.com.hk/archiver/?fid-115.html`、`https://www.discuss.com.hk/forumdisplay.php?fid=115` | archiver 列表页（li a）与主站列表页；archiver 无发布时间，条目按未标时间处理。 |

## 08 LIHKG 连登财经台（`LIHKG` · 粤语本地论坛）

- **目录 URL**：https://lihkg.com/category/15
- **方案**：已有 LIHKG 公开 JSON 采样（分类 15 财经台）→ 分类页静态摘录。
- **强度**：adapter
- **限制**：非官方接口，可能变更或拒绝访问；不做认证、签名逆向或验证码处理。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `adapter` → `lihkg_api` | `adapter` | —（由适配器执行） | 专用只读 JSON 采样：分类 15 第一页最多 20 帖按 no_of_reply 选 3 帖，取每帖前 20 楼 msg_num>1 的回复按 like_count 排序；只收 7 天内、有真实时间戳、display_vote 未被关的回复。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://lihkg.com/category/15` | 分类页静态摘录；列表由前端渲染，命中率低。 |

## 09 韭圈儿 / 红岸社区（`JIUQUAN` · 中文机构持仓社区）

- **目录 URL**：https://www.jiucaishuo.com
- **方案**：站点公开页摘录 → 检索载体；该社区以 App 为主，公开侧长期可能未获取。
- **强度**：excerpt_only（无公开订阅/接口，只有页面摘录与检索载体）
- **限制**：已核对：RSSHub 无 jiucaishuo 路由。App-only 社区不编造「站内热帖」。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `html` → `html_excerpt` | `needs_check` | `https://www.jiucaishuo.com` | 站点首页静态摘录（内容主要在 App 内）。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:韭圈儿 红岸 港股 机构持仓` | 检索载体兜底。 |

## 10 蚂蚁财富港股社区（`ANTFORTUNE` · 中文基民社区）

- **目录 URL**：https://www.antfortune.com
- **方案**：站点公开页摘录 → 检索载体；社区内容在支付宝 App 内，公开侧大概率未获取。
- **强度**：excerpt_only（无公开订阅/接口，只有页面摘录与检索载体）
- **限制**：已核对：RSSHub 无 antfortune 路由。抓不到就标未获取，不用基金净值页冒充社区讨论。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `html` → `html_excerpt` | `needs_check` | `https://www.antfortune.com` | 站点首页静态摘录（社区在 App 内，公开页多为产品介绍）。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:蚂蚁财富 港股 社区 讨论` | 检索载体兜底。 |

## 11 Reddit (r/ChinaStocks)（`REDDIT` · 英文论坛）

- **目录 URL**：https://www.reddit.com/r/ChinaStocks/
- **方案**：Reddit 原生 .rss（含 old.reddit 与当日 top 变体）→ 已配置的 PRAW 官方 API → 检索载体。
- **强度**：adapter
- **限制**：已核对：RSSHub 无 reddit 路由。PRAW 用平台官方 API，配额与政策由使用者自行确认。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `native` | `https://www.reddit.com/r/ChinaStocks/.rss`、`https://old.reddit.com/r/ChinaStocks/.rss`、`https://www.reddit.com/r/ChinaStocks/top/.rss?t=day` | Reddit 原生订阅出口；云出口 IP 常被 429/403，故给三个变体按序回退。 |
| 2 | `adapter` → `reddit_praw` | `adapter` | —（由适配器执行） | 可选 PRAW 8.0.3 只读 OAuth：hot(limit=3) + comment_sort=top、comment_limit=20，只读顶层评论，不调用 replace_more。未装包/未配密钥时如实记 praw_not_installed / not_configured。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:reddit ChinaStocks Hong Kong stocks` | 检索载体兜底（英文口径）。 |

## 12 TradingView 香港板块（`TRADINGVIEW` · 英文图表社区）

- **目录 URL**：https://www.tradingview.com/markets/hong-kong/
- **方案**：RSSHub 官方博客路由 → 港股板块页摘录 → 检索载体；官方博客不是港股板块讨论，口径里写明。
- **强度**：rsshub
- **限制**：已核对：RSSHub 只有 blog / desktop / pine 三条 TradingView 路由，无观点（ideas）路由。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/tradingview/blog/en` | 上游为 TradingView 官方博客（/blog/:category，默认 en）；这是站点官方内容出口，**不是** markets/hong-kong 板块的用户观点流。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://www.tradingview.com/markets/hong-kong/`、`https://www.tradingview.com/blog/en/` | 港股板块页与博客页静态摘录；观点流由前端渲染。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:TradingView Hong Kong HSI ideas` | 检索载体兜底。 |

## 13 Value Investors Club（`VIC` · 英文研究社区）

- **目录 URL**：https://www.valueinvestorsclub.com
- **方案**：公开 ideas 列表页摘录 → 检索载体；全文需会员登录，只给公开可见的标题级摘录。
- **强度**：excerpt_only（无公开订阅/接口，只有页面摘录与检索载体）
- **限制**：已核对：RSSHub 无 valueinvestorsclub 路由。登录内容一律不抓。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `html` → `html_excerpt` | `needs_check` | `https://www.valueinvestorsclub.com`、`https://www.valueinvestorsclub.com/ideas` | 公开 ideas 列表页静态摘录；正文需登录，不绕付费墙。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:Value Investors Club idea Hong Kong China` | 检索载体兜底。 |

## 14 Twitter / X (FinTwit)（`FINTWIT` · 英文社交平台）

- **目录 URL**：https://x.com/search?q=HSI%20Hong%20Kong
- **方案**：检索载体（免密钥，稳定）→ 配了 Token 才可用的 RSSHub 关键词路由 → 页面摘录（几乎必然失败，如实标注）。
- **强度**：excerpt_only
- **限制**：已核对：RSSHub 的 /twitter/* 路由普遍要求 TWITTER_AUTH_TOKEN。不自动登录、不绕验证码。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `carrier` → `gnews_carrier` | `carrier` | `gnews:FinTwit HSI "Hong Kong" stocks twitter` | 主路：检索载体免密钥、稳定；明确标注为聚合检索，不是 X 站内数据。 |
| 2 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/twitter/keyword/HSI%20Hong%20Kong`<br>需配置：`TWITTER_AUTH_TOKEN` | 上游需 TWITTER_AUTH_TOKEN（features.requireConfig）；未配置时实例直接报错，因此排在免密钥的载体之后。 |
| 3 | `html` → `html_excerpt` | `needs_check` | `https://x.com/search?q=HSI%20Hong%20Kong` | x.com 搜索页需 JS 与登录，静态 HTML 基本取不到内容；保留仅作最后尝试并如实标未获取。 |

## 15 知乎 · 投资理财话题（`ZHIHU` · 中文问答社区）

- **目录 URL**：https://www.zhihu.com/topic/19554223/hot
- **方案**：检索载体（免 Cookie，稳定）→ 配了 Cookie 才可用的 RSSHub 话题路由 → 话题页摘录。
- **强度**：excerpt_only
- **限制**：已核对：/zhihu/* 路由普遍 antiCrawler=true 且需 Cookie。不自动登录。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `carrier` → `gnews_carrier` | `carrier` | `gnews:知乎 投资理财 港股 恒指` | 主路：免 Cookie 的检索载体，标注为聚合检索。 |
| 2 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/zhihu/topic/19554223`、`rsshub:/zhihu/hot`<br>需配置：`ZHIHU_COOKIES` | /zhihu/topic/:topicId 与 /zhihu/hot 上游均需 ZHIHU_COOKIES（antiCrawler=true）；话题 id 19554223 即目录里的「投资理财」话题。 |
| 3 | `html` → `html_excerpt` | `needs_check` | `https://www.zhihu.com/topic/19554223/hot` | 话题页静态摘录；知乎对未登录请求常返回验证页，命中即判无效。 |

## 16 微博财经超话（`WEIBO` · 中文社交媒体）

- **目录 URL**：https://s.weibo.com/weibo?q=%23%E6%B8%AF%E8%82%A1%23
- **方案**：检索载体（免 Cookie）→ 配了 Cookie 才可用的 RSSHub 关键词/超话路由 → 搜索页摘录。
- **强度**：excerpt_only
- **限制**：已核对：/weibo/* 路由需 Cookie（timeline 路由还需 WEIBO_APP_KEY）。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `carrier` → `gnews_carrier` | `carrier` | `gnews:微博 港股 财经 超话` | 主路：免 Cookie 的检索载体，标注为聚合检索，不代表微博站内热度。 |
| 2 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/weibo/keyword/%23%E6%B8%AF%E8%82%A1%23`、`rsshub:/weibo/super_index/1008084989d223732bf6f02f75ea30efad58a9/sort_time`<br>需配置：`WEIBO_COOKIES` | /weibo/keyword/:keyword 与 /weibo/super_index/:id/:type? 上游均需 WEIBO_COOKIES。超话 id 取自目录里的港股超话。 |
| 3 | `html` → `html_excerpt` | `needs_check` | `https://s.weibo.com/weibo?q=%23%E6%B8%AF%E8%82%A1%23` | 站内搜索页静态摘录；未登录常被重定向到登录页，命中即判无效。 |

## 17 百度贴吧 · 股票吧（`TIEBA` · 中文论坛）

- **目录 URL**：https://tieba.baidu.com/f?kw=%E8%82%A1%E7%A5%A8
- **方案**：RSSHub 贴吧路由（上游为贴吧移动端接口）→ 吧列表页摘录 → 检索载体。
- **强度**：rsshub

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/baidu/tieba/forum/%E8%82%A1%E7%A5%A8`、`rsshub:/baidu/tieba/forum/%E8%82%A1%E7%A5%A8/replied` | 上游走贴吧移动端接口 /c/f/frs/page（kw=股票，rn=30）；BAIDU_COOKIE 为**可选**，sortBy=replied 取按回复排序。路由标 antiCrawler=true，实例可能被拦。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://tieba.baidu.com/f?kw=%E8%82%A1%E7%A5%A8` | 吧列表页静态摘录（.threadlist_title 为帖子标题链接）。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:百度贴吧 股票吧 港股` | 检索载体兜底。 |

## 18 淘股吧（`TAOGUBA` · 中文短线交易论坛）

- **目录 URL**：https://www.taoguba.com.cn
- **方案**：RSSHub 淘股论坛路由（社区总版/网友点赞/精华）→ 站点页面摘录 → 检索载体。
- **强度**：rsshub

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/taoguba/zongban`、`rsshub:/taoguba/dianzan`、`rsshub:/taoguba/jinghua` | 上游 www.tgb.cn，分类：bbs 淘股论坛 / zongban 社区总版 / jinghua 精华加油 / dianzan 网友点赞；TAOGUBA_COOKIE 为**可选**（匿名摘录无需 Cookie）。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://www.tgb.cn/zongban/`、`https://www.taoguba.com.cn` | 社区总版列表页静态摘录（.Nbbs-tiezi-lists 为帖子块）。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:淘股吧 短线 港股` | 检索载体兜底。 |

## 19 同花顺社区（`THS` · 中文行情社区）

- **目录 URL**：https://t.10jqka.com.cn
- **方案**：RSSHub 7×24 要闻直播（港股标签）→ 社区页摘录 → 检索载体；要闻直播是站点官方内容出口，口径里写明不是社区帖。
- **强度**：rsshub
- **限制**：已核对：RSSHub 的 10jqka 命名空间只有 realtimenews 一条路由，无社区帖路由。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/10jqka/realtimenews/%E6%B8%AF%E8%82%A1`、`rsshub:/10jqka/realtimenews/%E9%87%8D%E8%A6%81` | 上游 news.10jqka.com.cn/realtimenews.html，tag 可多值逗号分隔（港股 / 重要 / A股 / 公告）。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://t.10jqka.com.cn`、`https://news.10jqka.com.cn/realtimenews.html` | 社区首页与要闻直播页静态摘录。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:同花顺 社区 港股 讨论` | 检索载体兜底。 |

## 20 格隆汇 · 港股圈（`GELONGHUI` · 中文研究社区）

- **目录 URL**：https://www.gelonghui.com
- **方案**：RSSHub 实时快讯/首页频道/最热文章/关键词（上游为站点官方 api）→ 页面摘录 → 检索载体。
- **强度**：rsshub

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/gelonghui/live`、`rsshub:/gelonghui/home`、`rsshub:/gelonghui/hot-article`、`rsshub:/gelonghui/keyword/%E6%B8%AF%E8%82%A1` | 上游为格隆汇官方接口：/api/channels/<tag>/articles/v8（首页频道）、/api/post/search/v4（关键词）、实时快讯与最热文章；均无需 Cookie。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://www.gelonghui.com`、`https://www.gelonghui.com/live` | 首页与快讯页静态摘录。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:格隆汇 港股圈 讨论` | 检索载体兜底。 |

## 21 财联社 · 电报（`CLS` · 中文快讯社区）

- **目录 URL**：https://www.cls.cn/telegraph
- **方案**：RSSHub 电报/深度路由（财联社官方出口）→ 电报页摘录 → 检索载体。
- **强度**：rsshub

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/cls/telegraph`、`rsshub:/cls/depth/1000` | /cls/telegraph/:category? 为电报快讯（另有 watch/announcement/red/hk_us 等分类），/cls/depth/:category? 为深度；两者无需 Cookie。/cls/hot 标 antiCrawler=true，不纳入主链。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://www.cls.cn/telegraph` | 电报页静态摘录。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:财联社 电报 港股` | 检索载体兜底。 |

## 22 第一财经 · 社区（`YICAI` · 中文财经媒体）

- **目录 URL**：https://www.yicai.com
- **方案**：RSSHub 正在/最新/头条路由 → 站点页面摘录 → 检索载体。
- **强度**：rsshub
- **限制**：目录里的一财「社区」实为媒体内容出口，口径按媒体快讯标注，不冒充用户评论。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/yicai/brief`、`rsshub:/yicai/latest`、`rsshub:/yicai/headline` | /brief 为「正在」实时短讯、/latest 为最新、/headline 为头条；均无需 Cookie。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://www.yicai.com`、`https://www.yicai.com/brief` | 首页与正在页静态摘录。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:第一财经 港股 宏观` | 检索载体兜底。 |

## 23 Bilibili · 财经区（`BILIBILI` · 中文视频社区）

- **目录 URL**：https://www.bilibili.com/v/finance
- **方案**：RSSHub 财经分区（tid=207）与分区排行 → 关键词检索 → 财经区页摘录 → 检索载体。
- **强度**：rsshub
- **限制**：视频区不是评论区。若要取评论需二段（先拿 bvid 再走 /bilibili/video/reply/:bvid），当前不把「视频标题」说成「用户评论」。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/bilibili/partion/207`、`rsshub:/bilibili/partion/ranking/207/3`、`rsshub:/bilibili/vsearch/%E6%B8%AF%E8%82%A1` | 分区表已核对：财经 tid=207（科学科普 201 / 社科人文 124 / 财经 207 / 校园学习 208）。/partion/ranking/:tid/:days? 为分区排行，/vsearch/:kw 为视频检索，均无需 Cookie。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://www.bilibili.com/v/finance` | 财经区页静态摘录；视频流由前端渲染。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:bilibili 财经 港股 视频` | 检索载体兜底。 |

## 24 PTT Stock 板（`PTT` · 繁体中文论坛）

- **目录 URL**：https://www.ptt.cc/bbs/Stock/index.html
- **方案**：已有 PTT 双阶段推噓采样 → 板级 Atom 订阅 → 列表页摘录。
- **强度**：adapter
- **限制**：已核对：RSSHub 无 ptt 路由，因此走自有适配器 + 板级订阅。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `adapter` → `ptt_html` | `adapter` | —（由适配器执行） | 专用双阶段：index.html 取有可见正推噓净数的最多 3 帖 → 每帖前 20 个 .push，输出最多 3 条有财经上下文的回复；「爆」按 ≥100 处理。只保留 PTT 给出的月日与时间，不推算年份。 |
| 2 | `rss` → `rss_feed` | `needs_check` | `https://www.ptt.cc/atom/Stock.xml`、`https://www.ptt.cc/rss/Stock.xml` | PTT 板级 Atom/RSS 出口（同板文章列表，不含推噓明细）；未在线实测，标 needs_check。 |
| 3 | `html` → `html_excerpt` | `needs_check` | `https://www.ptt.cc/bbs/Stock/index.html` | 列表页静态摘录（.r-ent .title）。 |

## 25 StockTwits（`STOCKTWITS` · 英文交易者社交）

- **目录 URL**：https://stocktwits.com/symbol/HSI
- **方案**：公开 symbol stream 接口 → 页面摘录 → 检索载体。
- **强度**：feed
- **限制**：已核对：RSSHub 无 stocktwits 路由。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `json_api` → `stocktwits_json` | `needs_check` | `https://api.stocktwits.com/api/2/streams/symbol/HSI.json` | API v2 公开 symbol stream（records[] → body/created_at/id/user/entities.sentiment）。本环境不能直连，标 needs_check；该接口对云出口有速率限制，429 即降级。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://stocktwits.com/symbol/HSI` | 符号页静态摘录；消息流由前端渲染。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:StockTwits HSI Hang Seng` | 检索载体兜底。 |

## 26 Seeking Alpha（`SEEKINGALPHA` · 英文研究社区）

- **目录 URL**：https://seekingalpha.com/market-news
- **方案**：官方 market-news 订阅 → RSSHub 按标的路由 → 页面摘录 → 检索载体。
- **强度**：feed

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `native` | `https://seekingalpha.com/market-news.rss` | Seeking Alpha 官方 market-news RSS（与 05 栏同一端点，已列为 native）。 |
| 2 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/seekingalpha/HSI/news` | /seekingalpha/:symbol/:category? 可按标的取；路由标 antiCrawler=true，且 HSI 是否被上游支持未在线实测。 |
| 3 | `html` → `html_excerpt` | `needs_check` | `https://seekingalpha.com/market-news` | 市场新闻页静态摘录。 |
| 4 | `carrier` → `gnews_carrier` | `carrier` | `gnews:Seeking Alpha Hang Seng Index` | 检索载体兜底。 |

## 27 Bogleheads Forum（`BOGLEHEADS` · 英文长期投资论坛）

- **目录 URL**：https://www.bogleheads.org/forum/index.php
- **方案**：phpBB 原生 rss.php → 已有 phpBB 两阶段回复采样 → 版块页摘录。
- **强度**：adapter
- **限制**：已核对：RSSHub 无 bogleheads 路由；phpBB 自带 rss.php，是最稳的一条路。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `native` | `https://www.bogleheads.org/forum/rss.php?f=10` | phpBB 原生版块 RSS（f=10 投资 - 理论、新闻与一般）。 |
| 2 | `adapter` → `phpbb_html` | `adapter` | —（由适配器执行） | 专用两阶段：viewforum.php?f=10 取可见回复数最多的前 3 帖（优先进可见末页）→ 帖内首页前 20 条回复；用 Replies 计数作热度参考，不是单条点赞。 |
| 3 | `html` → `html_excerpt` | `needs_check` | `https://www.bogleheads.org/forum/viewforum.php?f=10` | 版块页静态摘录（.topictitle）。 |

## 28 Reddit (r/investing)（`RINVESTING` · 英文论坛）

- **目录 URL**：https://www.reddit.com/r/investing/
- **方案**：Reddit 原生 .rss（含 old.reddit 与当日 top 变体）→ PRAW 官方 API → 检索载体。
- **强度**：adapter

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `native` | `https://www.reddit.com/r/investing/.rss`、`https://old.reddit.com/r/investing/.rss`、`https://www.reddit.com/r/investing/top/.rss?t=day` | Reddit 原生订阅出口，三个变体按序回退。 |
| 2 | `adapter` → `reddit_praw` | `adapter` | —（由适配器执行） | PRAW 只读采样（同 r/ChinaStocks 口径）：hot(limit=3)、comment_sort=top、comment_limit=20，只读顶层评论。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:reddit r/investing Hong Kong China stocks` | 检索载体兜底。 |

## 29 Wall Street Oasis（`WSO` · 英文机构社区）

- **目录 URL**：https://www.wallstreetoasis.com/forum
- **方案**：论坛公开列表页摘录 → 检索载体；无官方订阅出口。
- **强度**：excerpt_only（无公开订阅/接口，只有页面摘录与检索载体）
- **限制**：已核对：RSSHub 无 wallstreetoasis 路由；站点未暴露公开 RSS（未在线实测，标 needs_check）。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `html` → `html_excerpt` | `needs_check` | `https://www.wallstreetoasis.com/forum`、`https://www.wallstreetoasis.com/forums` | 论坛列表页静态摘录；部分内容需登录，登录内容不抓。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:Wall Street Oasis forum Hong Kong Asia investing` | 检索载体兜底。 |

## 30 Investing.com 讨论区（`INVESTING` · 多语言全球论坛）

- **目录 URL**：https://www.investing.com/indices/hang-seng-index-commentary
- **方案**：站点官方 news RSS → 恒指评论页摘录 → 检索载体。
- **强度**：feed
- **限制**：已核对：RSSHub 无 investing.com 路由。评论区的用户评论不冒充新闻订阅，两者分开标注。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `needs_check` | `https://www.investing.com/rss/news.rss`、`https://www.investing.com/rss/news_301.rss` | Investing.com 官方 news RSS 出口（news_301 为股市新闻）；未在线实测，标 needs_check。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://www.investing.com/indices/hang-seng-index-commentary`、`https://www.investing.com/indices/hang-seng-index` | 恒指评论页与行情页静态摘录；评论流由前端渲染且有 Cloudflare 防护。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:Investing.com Hang Seng Index commentary` | 检索载体兜底。 |

## 31 Yahoo Finance · 社区（`YAHOO` · 英文行情社区）

- **目录 URL**：https://finance.yahoo.com/quote/%5EHSI/community
- **方案**：Yahoo 官方 news RSS → RSSHub finance / finance-hk 路由 → 社区页摘录 → 检索载体。
- **强度**：feed
- **限制**：已核对：RSSHub 无 Yahoo Finance「社区/评论」路由，只有 finance 新闻主题路由；因此原生 RSS 排第一，社区页只作摘录。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `native` | `https://finance.yahoo.com/news/rssindex` | Yahoo Finance 官方新闻 RSS（与 05 栏同一端点，已列为 native）。 |
| 2 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/yahoo/finance/latest-news`、`rsshub:/yahoo/finance/hk/latest-news` | /yahoo/finance/:topic? 与 /yahoo/finance/hk/:topic?（上游 hk.finance.yahoo.com/topic/:topic）；topic 默认 latest-news，无需 Cookie。 |
| 3 | `html` → `html_excerpt` | `needs_check` | `https://finance.yahoo.com/quote/%5EHSI/community`、`https://hk.finance.yahoo.com/topic/latest-news` | 恒指社区页与港股财经页静态摘录。 |
| 4 | `carrier` → `gnews_carrier` | `carrier` | `gnews:Yahoo Finance HSI community Hang Seng` | 检索载体兜底。 |

## 32 Substack · 财经通讯（`SUBSTACK` · 英文订阅研究）

- **目录 URL**：https://substack.com/browse/finance
- **方案**：浏览页摘录 → 检索载体；Substack 的 RSS 是**每个 publication 各自**的 /feed，目录浏览页没有订阅出口，不用某一位作者冒充「财经通讯」。
- **强度**：excerpt_only（无公开订阅/接口，只有页面摘录与检索载体）
- **限制**：已核对：RSSHub 的 substack 命名空间只有 /subscribe/:user（按用户订阅），需要指定具体作者；不指定某位作者来代表整个财经分类。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `html` → `html_excerpt` | `needs_check` | `https://substack.com/browse/finance`、`https://substack.com/search/finance` | 财经分类浏览页与搜索页静态摘录；榜单由前端渲染。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:Substack finance newsletter Hong Kong markets` | 检索载体兜底。 |

## 33 Reddit (r/options)（`ROPTIONS` · 英文衍生品论坛）

- **目录 URL**：https://www.reddit.com/r/options/
- **方案**：Reddit 原生 .rss（含 old.reddit 与当日 top 变体）→ PRAW 官方 API → 检索载体。
- **强度**：adapter

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `native` | `https://www.reddit.com/r/options/.rss`、`https://old.reddit.com/r/options/.rss`、`https://www.reddit.com/r/options/top/.rss?t=day` | Reddit 原生订阅出口，三个变体按序回退。 |
| 2 | `adapter` → `reddit_praw` | `adapter` | —（由适配器执行） | PRAW 只读采样（同上口径）。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:reddit r/options HSI index options volatility` | 检索载体兜底。 |

## 34 FT Alphaville（`FTALPHA` · 英文财经媒体博客）

- **目录 URL**：https://www.ft.com/alphaville
- **方案**：FT 官方 ?format=rss 出口 → 栏目页摘录 → 检索载体。
- **强度**：feed
- **限制**：已核对：RSSHub 的 ft 命名空间只有 /myft/:key（需个人 RSS key），不适用于公开栏目。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `needs_check` | `https://www.ft.com/alphaville?format=rss`、`https://www.ft.com/rss/home` | FT 栏目页加 ?format=rss 是 FT 的通用订阅出口；第二个端点为 FT 官方首页 RSS（05 栏已列为 native）。Alphaville 那条未在线实测，标 needs_check。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://www.ft.com/alphaville` | Alphaville 栏目页静态摘录（.o-teaser__heading）。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:FT Alphaville markets` | 检索载体兜底。 |

## 35 集思录 · 低风险投资社区（`JISILU` · 中文低风险投资社区）

- **目录 URL**：https://www.jisilu.cn
- **方案**：RSSHub 广场/分类/话题路由 → 站点页面摘录 → 检索载体。
- **强度**：rsshub

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/jisilu/explore`、`rsshub:/jisilu/category/4`、`rsshub:/jisilu/topic/%E5%8F%AF%E8%BD%AC%E5%80%BA` | 上游 jisilu.cn：/explore/:filter? 为广场（div.aw-question-list），/category/:id 为分类，/topic/:id 为话题；三条均无需 Cookie。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://www.jisilu.cn/explore/`、`https://www.jisilu.cn` | 广场页与首页静态摘录。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:集思录 低风险投资 港股 可转债` | 检索载体兜底。 |

## 36 小红书 · 理财笔记（`XIAOHONGSHU` · 中文种草社交社区）

- **目录 URL**：https://www.xiaohongshu.com/search_result?keyword=%E6%B8%AF%E8%82%A1
- **方案**：检索载体（免 Cookie）→ 配了 Cookie 才可用的 RSSHub 用户笔记路由 → 搜索页摘录。
- **强度**：excerpt_only
- **限制**：已核对：RSSHub 无小红书关键词搜索路由（只有 board 与 user 两条）。不自动登录。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `carrier` → `gnews_carrier` | `carrier` | `gnews:小红书 理财 港股 笔记` | 主路：免 Cookie 的检索载体，标注为聚合检索，不代表小红书站内热度。 |
| 2 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/xiaohongshu/user/593032945e87e77791e03696/notes`<br>需配置：`XIAOHONGSHU_COOKIE` | RSSHub 只有按 user_id 的笔记/收藏路由且需 XIAOHONGSHU_COOKIE；示例 user_id 来自上游路由文档，**不代表本项目的采样对象**，仅在实例已配 Cookie 时尝试。 |
| 3 | `html` → `html_excerpt` | `needs_check` | `https://www.xiaohongshu.com/search_result?keyword=%E6%B8%AF%E8%82%A1` | 搜索结果页需 JS 与登录，静态 HTML 基本取不到；保留仅作最后尝试并如实标未获取。 |

## 37 抖音 · 财经短视频（`DOUYIN` · 中文短视频社区）

- **目录 URL**：https://www.douyin.com/search/%E6%B8%AF%E8%82%A1
- **方案**：检索载体（免 Cookie）→ 配了 Cookie 才可用的 RSSHub 标签路由 → 搜索页摘录。
- **强度**：excerpt_only
- **限制**：已核对：RSSHub 的 douyin 命名空间为直播/博主/标签/收藏，均需 Cookie 或指定 id。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `carrier` → `gnews_carrier` | `carrier` | `gnews:抖音 财经 港股 短视频` | 主路：免 Cookie 的检索载体，标注为聚合检索。 |
| 2 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/douyin/hashtag/1592824105719812`<br>需配置：`DOUYIN_COOKIE` | /douyin/hashtag/:cid 需要 cid 且 antiCrawler=true、上游需 Cookie；示例 cid 来自上游路由文档，未核对是否对应「港股」标签，故只在实例可用时尝试。 |
| 3 | `html` → `html_excerpt` | `needs_check` | `https://www.douyin.com/search/%E6%B8%AF%E8%82%A1` | 抖音搜索页为纯前端渲染，静态 HTML 取不到内容；保留仅作最后尝试。 |

## 38 开盘啦 · 情绪复盘（`KAIPANLA` · 中文短线情绪社区）

- **目录 URL**：https://www.kaipanla.com
- **方案**：站点公开页摘录 → 检索载体；情绪复盘数据在 App 内。
- **强度**：excerpt_only（无公开订阅/接口，只有页面摘录与检索载体）
- **限制**：已核对：RSSHub 无 kaipanla 路由。不把 App 内数据说成已抓取。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `html` → `html_excerpt` | `needs_check` | `https://www.kaipanla.com` | 站点首页静态摘录（复盘数据主要在 App）。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:开盘啦 情绪复盘 涨停 港股` | 检索载体兜底。 |

## 39 理想论坛 · 股票实战（`LIXIANG` · 中文实战论坛）

- **目录 URL**：https://www.55188.com
- **方案**：Discuz 列表页摘录（含 archiver 轻量列表）→ 检索载体。
- **强度**：excerpt_only（无公开订阅/接口，只有页面摘录与检索载体）
- **限制**：已核对：RSSHub 无 55188 路由。Discuz 的 rss.php 是否存在未在线核对，故不写进端点链。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `html` → `html_excerpt` | `needs_check` | `https://www.55188.com/archiver/`、`https://www.55188.com/forum.php`、`https://www.55188.com` | Discuz 站点：优先 archiver 轻量列表（同 discuss.com.hk 的思路），再退到 forum.php 与首页。archiver 的具体 fid 未在线核对，因此按站点级列表处理。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:理想论坛 55188 股票实战 港股` | 检索载体兜底。 |

## 40 QuantNet · 量化社区（`QUANTNET` · 英文量化社区）

- **目录 URL**：https://quantnet.com
- **方案**：XenForo 原生全站 RSS → 论坛列表页摘录 → 检索载体。
- **强度**：feed
- **限制**：已核对：RSSHub 无 quantnet 路由；XenForo 原生 RSS 是这类论坛最稳的一条路。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `needs_check` | `https://quantnet.com/forums/-/index.rss`、`https://quantnet.com/forums/trading-forums.13/index.rss` | XenForo 自带 `forums/-/index.rss`（全站最新帖）与按版块的 `index.rss`；具体版块 slug/id 未在线核对，标 needs_check。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://quantnet.com/forums/`、`https://quantnet.com` | 论坛首页静态摘录（XenForo 结构：.block-row / a[data-tp-title] / .structItem-title）。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:QuantNet forum quantitative finance Hong Kong` | 检索载体兜底。 |

## 41 Elite Trader（`ELITETRADER` · 英文交易员论坛）

- **目录 URL**：https://www.elitetrader.com/et/
- **方案**：XenForo 原生全站 RSS → 论坛列表页摘录 → 检索载体。
- **强度**：feed
- **限制**：已核对：RSSHub 无 elitetrader 路由。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `needs_check` | `https://www.elitetrader.com/et/forums/-/index.rss`、`https://www.elitetrader.com/et/forums/stock-forums.5/index.rss` | XenForo 原生 `forums/-/index.rss`；版块 slug/id 未在线核对，标 needs_check。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://www.elitetrader.com/et/`、`https://www.elitetrader.com/et/forums/` | 论坛首页静态摘录（XenForo 结构）。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:Elite Trader forum HSI Hong Kong futures` | 检索载体兜底。 |

## 42 Forex Factory（`FOREXFACTORY` · 英文外汇社区）

- **目录 URL**：https://www.forexfactory.com/forum
- **方案**：论坛列表页摘录 → 检索载体；站点未暴露公开订阅出口。
- **强度**：excerpt_only（无公开订阅/接口，只有页面摘录与检索载体）
- **限制**：已核对：RSSHub 无 forexfactory 路由；vBulletin external.php 是否存在未在线核对，故不写进端点链。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `html` → `html_excerpt` | `needs_check` | `https://www.forexfactory.com/forum`、`https://www.forexfactory.com/calendar` | 论坛列表页与财经日历页静态摘录（日历为公开数据，.calendar__event-title）。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:Forex Factory forum HSI Hang Seng currency` | 检索载体兜底。 |

## 43 Reddit (r/CryptoCurrency)（`RCRYPTO` · 数字资产论坛）

- **目录 URL**：https://www.reddit.com/r/CryptoCurrency/
- **方案**：Reddit 原生 .rss（含 old.reddit 与当日 top 变体）→ PRAW 官方 API → 检索载体。
- **强度**：adapter

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `native` | `https://www.reddit.com/r/CryptoCurrency/.rss`、`https://old.reddit.com/r/CryptoCurrency/.rss`、`https://www.reddit.com/r/CryptoCurrency/top/.rss?t=day` | Reddit 原生订阅出口，三个变体按序回退。 |
| 2 | `adapter` → `reddit_praw` | `adapter` | —（由适配器执行） | PRAW 只读采样（同上口径）。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:reddit CryptoCurrency Hong Kong stablecoin asia` | 检索载体兜底。 |

## 44 Morningstar · 社区（`MORNINGSTAR` · 英文基金研究社区）

- **目录 URL**：https://community.morningstar.com
- **方案**：社区页摘录 → 检索载体；官方社区历经改版，可能已整体下线，如实标未获取。
- **强度**：excerpt_only（无公开订阅/接口，只有页面摘录与检索载体）
- **限制**：已核对：RSSHub 无 morningstar 路由。该社区是否仍公开可用未在线核对。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `html` → `html_excerpt` | `needs_check` | `https://community.morningstar.com`、`https://www.morningstar.com/community` | 社区入口静态摘录；若站点已改版/下线，取不到就标未获取，不回填历史讨论。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:Morningstar community forum Hong Kong funds` | 检索载体兜底。 |

## 45 MarketWatch · 社区（`MARKETWATCH` · 英文财经媒体社区）

- **目录 URL**：https://www.marketwatch.com/latest-news
- **方案**：道琼斯官方 MarketWatch RSS（05 栏已核为 native）→ 最新新闻页摘录 → 检索载体。
- **强度**：feed
- **限制**：已核对：RSSHub 无 marketwatch 路由。目录里的「MarketWatch 社区」实为媒体内容出口，口径按媒体快讯标注，不冒充用户评论。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `native` | `https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines`、`https://feeds.content.dowjones.io/public/rss/mw_topstories` | 道琼斯官方 MarketWatch 实时头条与头条 RSS（05 栏同一端点，已列 native）。 |
| 2 | `html` → `html_excerpt` | `needs_check` | `https://www.marketwatch.com/latest-news` | 最新新闻页静态摘录。 |
| 3 | `carrier` → `gnews_carrier` | `carrier` | `gnews:MarketWatch Hang Seng Index Hong Kong` | 检索载体兜底。 |

## 46 日本 Yahoo! 财经掲示板（`YAHOOJP` · 日文社区）

- **目录 URL**：https://finance.yahoo.co.jp/search/?query=%E9%A6%99%E6%B8%AF%E6%A0%AA
- **方案**：Yahoo! Japan 官方新闻 RSS → 检索载体（日文）→ 检索/掲示板页摘录。
- **强度**：feed
- **限制**：已核对：RSSHub 的 yahoo 命名空间指向 news.yahoo.com 与 hk.finance.yahoo.com，无 finance.yahoo.co.jp 路由。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rss` → `rss_feed` | `needs_check` | `https://news.yahoo.co.jp/rss/topics/top-picks.xml`、`https://news.yahoo.co.jp/rss/topics/business.xml` | Yahoo! Japan 官方新闻 RSS（トップニュース / 経済）；未在线实测，标 needs_check。掲示板本身无订阅出口。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:Yahoo ファイナンス 掲示板 香港株 ハンセン` | 检索载体（日文口径），标注为聚合检索。 |
| 3 | `html` → `html_excerpt` | `needs_check` | `https://finance.yahoo.co.jp/search/?query=%E9%A6%99%E6%B8%AF%E6%A0%AA`、`https://finance.yahoo.co.jp/` | 站内检索页与财经首页静态摘录；掲示板留言由前端渲染。 |

## 47 네이버 금융 종토방（`NAVER` · 韩文社区）

- **目录 URL**：https://finance.naver.com/world/sise.naver?symbol=HSI@HSI
- **方案**：RSSHub Naver 检索路由（新闻/博客/카페）→ 检索载体（韩文）→ 世界行情页摘录。
- **强度**：rsshub
- **限制**：已核对：RSSHub 的 naver 命名空间只有 comic 与 search 两条路由，无종토방路由。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `rsshub` → `rsshub_feed` | `rsshub_doc` | `rsshub:/naver/search/news/%ED%95%AD%EC%83%81%EC%A7%80%EC%88%98`、`rsshub:/naver/search/all/HSI` | /naver/search/:category/:keyword（category 支持 all/news/cafe 等，上游为 Naver 搜索模板接口）；无需 Cookie。종토방（个股讨论板）本身无路由。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:네이버 금융 종목토론방 항셍지수 홍콩` | 检索载体（韩文口径），标注为聚合检索。 |
| 3 | `html` → `html_excerpt` | `needs_check` | `https://finance.naver.com/world/sise.naver?symbol=HSI@HSI`、`https://finance.naver.com/sise/` | 世界行情页与首页静态摘录；종토방留言需进入个股讨论板且由服务端分页渲染。 |

## 48 Wallstreet-Online · 德语社区（`WALLSTREETDE` · 德文社区）

- **目录 URL**：https://www.wallstreet-online.de/community/forum
- **方案**：论坛列表页摘录 → 检索载体（德文）。
- **强度**：excerpt_only（无公开订阅/接口，只有页面摘录与检索载体）
- **限制**：已核对：RSSHub 全量路由树无 wallstreet-online（仅有 wallstreetcn）；站点是否提供公开 RSS 未在线核对，故不写进端点链。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `html` → `html_excerpt` | `needs_check` | `https://www.wallstreet-online.de/community/forum`、`https://www.wallstreet-online.de/nachrichten` | 社区论坛列表页与新闻页静态摘录。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:wallstreet-online Forum Hang Seng Hongkong Aktien` | 检索载体（德文口径）。 |

## 49 阿斯达克财经 · 讨论区（`AASTOCKS` · 香港本地财经社区）

- **目录 URL**：http://www.aastocks.com/tc/stocks/comment/latest.aspx
- **方案**：讨论区「最新回應」公开列表页摘录 → 检索载体（繁中）。
- **强度**：excerpt_only（无公开订阅/接口，只有页面摘录与检索载体）
- **限制**：已核对：RSSHub 无 aastocks 路由（港股本地媒体回退另见 05 栏的信报/经济日报路由，那是不同站点，不用来冒充阿斯达克讨论区）。

| # | 步骤 | 核验 | 端点 | 口径 |
|---|---|---|---|---|
| 1 | `html` → `html_excerpt` | `needs_check` | `http://www.aastocks.com/tc/stocks/comment/latest.aspx`、`http://www.aastocks.com/tc/stocks/news/aafn-con/NOW.1/news/latest-news.aspx` | 讨论区最新回應列表页与新闻页静态摘录；发帖需登录，只读公开列表。 |
| 2 | `carrier` → `gnews_carrier` | `carrier` | `gnews:AASTOCKS 港股 討論區 恒指` | 检索载体（繁中口径）。 |

## RSSHub 路由证据（逐条对着上游源码核对）

| 路由 | 上游文件（lib/routes/） |
|---|---|
| `/10jqka/realtimenews/:tag?` | `10jqka/realtimenews.ts` |
| `/baidu/tieba/forum/:kw/:sortBy?` | `baidu/tieba/forum.ts` |
| `/baidu/tieba/search/:qw/:routeParams?` | `baidu/tieba/search.ts` |
| `/bilibili/partion/:tid/:embed?` | `bilibili/partion.ts` |
| `/bilibili/partion/ranking/:tid/:days?/:embed?` | `bilibili/partion-ranking.ts` |
| `/bilibili/video/reply/:bvid` | `bilibili/reply.ts` |
| `/bilibili/vsearch/:kw/:order?/:embed?/:tid?` | `bilibili/vsearch.ts` |
| `/cls/depth/:category?` | `cls/depth.ts` |
| `/cls/telegraph/:category?` | `cls/telegraph.tsx` |
| `/discuss/:fid` | `discuss/index.ts` |
| `/douyin/hashtag/:cid/:routeParams?` | `douyin/hashtag.ts` |
| `/eastmoney/gerenzhongxin/guba/:uid` | `eastmoney/gerenzhongxin/guba.ts` |
| `/eastmoney/search/:keyword` | `eastmoney/search/index.ts` |
| `/futunn/live/:lang?` | `futunn/live.ts` |
| `/futunn/main` | `futunn/main.ts` |
| `/futunn/topic/:id` | `futunn/topic.ts` |
| `/gelonghui/home/:tag?` | `gelonghui/home.ts` |
| `/gelonghui/hot-article/:type?` | `gelonghui/hot-article.ts` |
| `/gelonghui/keyword/:keyword` | `gelonghui/keyword.ts` |
| `/gelonghui/live` | `gelonghui/live.tsx` |
| `/jisilu/category/:id` | `jisilu/category.ts` |
| `/jisilu/explore/:filter?` | `jisilu/explore.ts` |
| `/jisilu/topic/:id` | `jisilu/topic.ts` |
| `/laohu8/personal/:id` | `laohu8/personal.ts` |
| `/naver/search/:category/:keyword` | `naver/search.ts` |
| `/seekingalpha/:symbol/:category?` | `seekingalpha/index.tsx` |
| `/substack/subscribe/:user` | `substack/subscribe.ts` |
| `/taoguba/:category?` | `taoguba/index.ts` |
| `/tradingview/blog/:category{.+}?` | `tradingview/blog.ts` |
| `/twitter/keyword/:keyword/:routeParams?` | `twitter/keyword.ts` |
| `/wallstreetcn/hot/:period?` | `wallstreetcn/hot.ts` |
| `/wallstreetcn/live/:category?/:score?` | `wallstreetcn/live.tsx` |
| `/wallstreetcn/news/:category?` | `wallstreetcn/news.ts` |
| `/weibo/keyword/:keyword/:routeParams?` | `weibo/keyword.ts` |
| `/weibo/super_index/:id/:type?/:routeParams?` | `weibo/super-index.ts` |
| `/xiaohongshu/user/:user_id/:category/:routeParams?` | `xiaohongshu/user.ts` |
| `/xueqiu/hots` | `xueqiu/hots.ts` |
| `/xueqiu/stock_comments/:id` | `xueqiu/stock-comments.tsx` |
| `/yahoo/finance/:topic?` | `yahoo/finance.ts` |
| `/yahoo/finance/hk/:topic?` | `yahoo/finance-hk.ts` |
| `/yicai/brief` | `yicai/brief.ts` |
| `/yicai/headline` | `yicai/headline.ts` |
| `/yicai/latest` | `yicai/latest.ts` |
| `/zhihu/hot/:category?` | `zhihu/hot.ts` |
| `/zhihu/topic/:topicId/:isTop?` | `zhihu/topic.ts` |
| `/zhitongcaijing/:id?/:category?` | `zhitongcaijing/index.tsx` |

## 已核对：RSSHub 无路由的站点

这些站点在全量路由树与全文检索里都没有命中，因此方案里不写 RSSHub 步骤，改走官方订阅 / 公开接口 / 页面摘录 / 检索载体：

`reddit.com`、`ptt.cc`、`lihkg.com`、`stocktwits.com`、`bogleheads.org`、`wallstreetoasis.com`、`investing.com`、`quantnet.com`、`elitetrader.com`、`forexfactory.com`、`morningstar.com`、`marketwatch.com`、`wallstreet-online.de`、`aastocks.com`、`antfortune.com`、`jiucaishuo.com`、`kaipanla.com`、`55188.com`、`valueinvestorsclub.com`、`finance.yahoo.co.jp`
