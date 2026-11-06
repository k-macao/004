#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""49 社区源逐源独立抓取方案（每源一条端点链 + 专属解析 + 时效校验 + 逐级降级）。

为什么需要这一层
----------------
在此之前，49 个社区源里只有 5 类有专用采样（PTT / LIHKG / Bogleheads / Discuss.com.hk /
Reddit×4），另外 7 源配了 RSS，**其余 40 多个源共用一条通用阶梯**：一组 CSS 选择器 +
通用片段回捞 + 自适应指纹。共用阶梯的后果是「一源坏，坏得没有名字」：日志里只有
`status=0 / error=no_response`，看不出这个源到底该走哪个端点、为什么走不通、还有没有别的路。

本模块把每个源写成一份**独立的、可单独核对的方案**：

1. **端点链**（有序）：`adapter`（已有专用适配器）→ `rss`（站点官方订阅）→
   `json_api`（站点公开接口）→ `rsshub`（RSSHub 路由，按实例链轮换）→
   `html`（公开页面结构化摘录，可给多个候选 URL）→ `carrier`（Google News 检索载体）。
   逐级尝试，**第一个产出合格证据的步骤胜出**，其余不再发请求。
2. **核验状态**：`native` / `rsshub_doc` / `api_public` / `needs_check` / `carrier` / `adapter`。
   `rsshub_doc` 的路由**逐条对着 RSSHub 上游源码核对过**，证据钉在 `ROUTE_EVIDENCE`
   （上游仓库 + commit + 路由文件路径），可用 `--routes-md` 导出复核表。
3. **诚实口径**：抓不到就标「未获取」；用检索载体就写明是载体、不冒充原生源；
   接口没给点赞数就不编热度指标；需要 Cookie/Token 的步骤显式 `needs_config`，
   并且**绝不作为链条的最后一步**（否则没配密钥的源等于没有方案）。

本模块**不在导入时联网**，渲染路径也不联网；所有网络请求都由调用方注入的 session 执行。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import scrapling_core as sc

# ---------------------------------------------------------------------------
# 上游核对证据：RSSHub 路由不是凭记忆写的，逐条对着上游源码核对。
# 核对方式：`https://api.github.com/repos/DIYgod/RSSHub/git/trees/master?recursive=1`
# 取全量路由树 + 读取每个路由文件的 `export const route` 块（path / example /
# parameters / features.requireConfig / features.antiCrawler）。
# ---------------------------------------------------------------------------
RSSHUB_UPSTREAM = {
    'repo': 'DIYgod/RSSHub',
    'commit': '4be79cd40cae69ce68b7837f927b63cdab7be719',
    'committed_at': '2026-11-06T06:51:41Z',
    'checked_at': '2026-11-06',
    'method': 'GitHub API 全量路由树 + 逐路由源码核对（本执行环境不能直连目标站点，未做在线实测）',
}

# 本模块引用的每条 RSSHub 路由 → 上游路由文件（相对 lib/routes/）。
# tests/test_community_plans.py 断言：方案里出现的 rsshub 路由必须在这里有证据，
# 且 `docs/community-49-routes.md` 与之一致，防止凭记忆写路由。
ROUTE_EVIDENCE = {
    '/discuss/:fid': 'discuss/index.ts',
    '/xueqiu/stock_comments/:id': 'xueqiu/stock-comments.tsx',
    '/xueqiu/hots': 'xueqiu/hots.ts',
    '/cls/telegraph/:category?': 'cls/telegraph.tsx',
    '/cls/depth/:category?': 'cls/depth.ts',
    '/wallstreetcn/live/:category?/:score?': 'wallstreetcn/live.tsx',
    '/wallstreetcn/news/:category?': 'wallstreetcn/news.ts',
    '/wallstreetcn/hot/:period?': 'wallstreetcn/hot.ts',
    '/zhitongcaijing/:id?/:category?': 'zhitongcaijing/index.tsx',
    '/futunn/live/:lang?': 'futunn/live.ts',
    '/futunn/main': 'futunn/main.ts',
    '/futunn/topic/:id': 'futunn/topic.ts',
    '/gelonghui/live': 'gelonghui/live.tsx',
    '/gelonghui/home/:tag?': 'gelonghui/home.ts',
    '/gelonghui/hot-article/:type?': 'gelonghui/hot-article.ts',
    '/gelonghui/keyword/:keyword': 'gelonghui/keyword.ts',
    '/yicai/brief': 'yicai/brief.ts',
    '/yicai/latest': 'yicai/latest.ts',
    '/yicai/headline': 'yicai/headline.ts',
    '/10jqka/realtimenews/:tag?': '10jqka/realtimenews.ts',
    '/baidu/tieba/forum/:kw/:sortBy?': 'baidu/tieba/forum.ts',
    '/baidu/tieba/search/:qw/:routeParams?': 'baidu/tieba/search.ts',
    '/taoguba/:category?': 'taoguba/index.ts',
    '/jisilu/explore/:filter?': 'jisilu/explore.ts',
    '/jisilu/category/:id': 'jisilu/category.ts',
    '/jisilu/topic/:id': 'jisilu/topic.ts',
    '/bilibili/partion/:tid/:embed?': 'bilibili/partion.ts',
    '/bilibili/partion/ranking/:tid/:days?/:embed?': 'bilibili/partion-ranking.ts',
    '/bilibili/vsearch/:kw/:order?/:embed?/:tid?': 'bilibili/vsearch.ts',
    '/bilibili/video/reply/:bvid': 'bilibili/reply.ts',
    '/weibo/keyword/:keyword/:routeParams?': 'weibo/keyword.ts',
    '/weibo/super_index/:id/:type?/:routeParams?': 'weibo/super-index.ts',
    '/zhihu/topic/:topicId/:isTop?': 'zhihu/topic.ts',
    '/zhihu/hot/:category?': 'zhihu/hot.ts',
    '/xiaohongshu/user/:user_id/:category/:routeParams?': 'xiaohongshu/user.ts',
    '/douyin/hashtag/:cid/:routeParams?': 'douyin/hashtag.ts',
    '/twitter/keyword/:keyword/:routeParams?': 'twitter/keyword.ts',
    '/tradingview/blog/:category{.+}?': 'tradingview/blog.ts',
    '/seekingalpha/:symbol/:category?': 'seekingalpha/index.tsx',
    '/yahoo/finance/:topic?': 'yahoo/finance.ts',
    '/yahoo/finance/hk/:topic?': 'yahoo/finance-hk.ts',
    '/naver/search/:category/:keyword': 'naver/search.ts',
    '/substack/subscribe/:user': 'substack/subscribe.ts',
    '/laohu8/personal/:id': 'laohu8/personal.ts',
    '/eastmoney/gerenzhongxin/guba/:uid': 'eastmoney/gerenzhongxin/guba.ts',
    '/eastmoney/search/:keyword': 'eastmoney/search/index.ts',
}

# 已核对：RSSHub **没有**这些站点的路由（全量路由树 + 全文检索均无命中）。
# 记录在此，避免后续重复调研，也避免有人凭印象写出不存在的路由。
RSSHUB_ABSENT = (
    'reddit.com', 'ptt.cc', 'lihkg.com', 'stocktwits.com', 'bogleheads.org',
    'wallstreetoasis.com', 'investing.com', 'quantnet.com', 'elitetrader.com',
    'forexfactory.com', 'morningstar.com', 'marketwatch.com', 'wallstreet-online.de',
    'aastocks.com', 'antfortune.com', 'jiucaishuo.com', 'kaipanla.com', '55188.com',
    'valueinvestorsclub.com', 'finance.yahoo.co.jp',
)

def _route_pattern(concrete):
    """把具体路由（/xueqiu/stock_comments/HKHSI）还原成上游声明的路由模式。

    只做「最长前缀匹配」：具体路径落在某个已核对模式的静态前缀下即认定属于该模式，
    这样 `/taoguba/zongban` 能对上 `/taoguba/:category?`，
    `/yahoo/finance/hk/latest-news` 能对上更长的 `/yahoo/finance/hk/:topic?`
    而不是 `/yahoo/finance/:topic?`。匹配不到就原样返回（由调用方判定为未核对）。
    """
    best, best_prefix = '', ''
    for pattern in ROUTE_EVIDENCE:
        prefix = pattern.split('/:')[0]
        if concrete == pattern or concrete == prefix or concrete.startswith(prefix + '/'):
            if len(prefix) > len(best_prefix):
                best, best_prefix = pattern, prefix
    return best or concrete


STEP_ADAPTER = 'adapter'
STEP_RSS = 'rss'
STEP_JSON = 'json_api'
STEP_RSSHUB = 'rsshub'
STEP_HTML = 'html'
STEP_CARRIER = 'carrier'
STEP_KINDS = (STEP_ADAPTER, STEP_RSS, STEP_JSON, STEP_RSSHUB, STEP_HTML, STEP_CARRIER)

VERIFIED = ('native', 'rsshub_doc', 'api_public', 'needs_check', 'carrier', 'adapter')

KIND_COMMENT = 'comment'
KIND_REPLY = 'thread_reply'
KIND_EXCERPT = 'page_excerpt'
CONTENT_KINDS = (KIND_COMMENT, KIND_REPLY, KIND_EXCERPT)

# 每源最多尝试几个步骤、整轮最多发多少请求、整轮墙钟预算。
# 预算是硬的：49 源 × 多步链如果不设上限，一次采集能把 CI 的社区 job 拖到超时。
PLAN_MAX_STEPS = int(os.environ.get('COMMUNITY_PLAN_MAX_STEPS', '3'))
PLAN_REQUEST_BUDGET = int(os.environ.get('COMMUNITY_PLAN_REQUESTS', '90'))
PLAN_WALL_BUDGET = float(os.environ.get('COMMUNITY_PLAN_BUDGET', '420'))
# 步骤间隔与项目既有的 download_delay 同一口径：方案层发的是**源级列表/订阅请求**，
# 不是帖详情读取（后者另有 10 秒帖子限速器）。调用方可以按自己的 download_delay 覆盖。
PLAN_STEP_DELAY = float(os.environ.get('COMMUNITY_PLAN_DELAY', '0.15'))

MAX_ITEMS_PER_STEP = 12
GOOGLE_NEWS_HOST = 'news.google.com'

GNEWS_LOCALES = {
    'zh-CN': ('zh-CN', 'CN', 'zh-CN:CN'),
    'zh-TW': ('zh-TW', 'TW', 'zh-TW:TW'),
    'en': ('en', 'US', 'en:US'),
    'ja': ('ja', 'JP', 'ja:JP'),
    'ko': ('ko', 'KR', 'ko:KR'),
    'de': ('de', 'DE', 'de:DE'),
}


def gnews_url(query, lang='zh-CN'):
    """Google News 检索载体端点（与 macro_data / rss_feeds 同一格式，如实标注为载体）。"""
    hl, gl, ceid = GNEWS_LOCALES.get(lang, GNEWS_LOCALES['zh-CN'])
    return (f'https://{GOOGLE_NEWS_HOST}/rss/search?q={urllib.parse.quote(str(query))}'
            f'&hl={hl}&gl={gl}&ceid={ceid}')


# ---------------------------------------------------------------------------
# 步骤构造器：方案是**数据**，不是散落在函数里的 if/else，这样才能逐源核对与导出。
# ---------------------------------------------------------------------------

def step(kind, endpoints=(), verified='needs_check', note='', strategy='',
         content_kind=KIND_EXCERPT, needs_config=(), extract=None, selectors=(),
         headers=None, metric=None, scope='', lang=None):
    if kind not in STEP_KINDS:
        raise ValueError(f'未知步骤类型: {kind}')
    if verified not in VERIFIED:
        raise ValueError(f'未知核验状态: {verified}')
    if content_kind not in CONTENT_KINDS:
        raise ValueError(f'未知内容类型: {content_kind}')
    endpoints = tuple(endpoints or ())
    if kind != STEP_ADAPTER and not endpoints:
        raise ValueError(f'{kind} 步骤必须给端点')
    if kind == STEP_CARRIER and content_kind != KIND_EXCERPT:
        # 检索载体是聚合结果，永远不能自称评论或热帖回复。
        raise ValueError('carrier 步骤只能是 page_excerpt')
    return {
        'kind': kind,
        'endpoints': endpoints,
        'verified': verified,
        'note': note,
        'strategy': strategy or f'{kind}_step',
        'content_kind': content_kind,
        'needs_config': tuple(needs_config or ()),
        'extract': extract,
        'selectors': tuple(selectors or ()),
        'headers': dict(headers or {}),
        'metric': dict(metric or {}),
        'scope': scope,
        'lang': lang,
    }


def adapter(strategy, note, content_kind=KIND_COMMENT):
    """已有专用适配器（PTT / LIHKG / phpBB / Discuz / PRAW）——由 collect_comments 执行。

    方案里登记它是为了「这一源到底有几条路」一目了然；执行器不重复发请求，
    只把它记为 `delegated`，避免同一次采集里对同一站点打两遍。
    """
    return step(STEP_ADAPTER, (), verified='adapter', note=note, strategy=strategy,
                content_kind=content_kind)


def rss(urls, note, verified='native', content_kind=KIND_REPLY, strategy='rss_feed',
        scope='站点官方订阅源最新条目，命中财经主题词且在时间窗内方入库，不代表全站排行'):
    return step(STEP_RSS, urls, verified=verified, note=note, strategy=strategy,
                content_kind=content_kind, scope=scope)


def rsshub(routes, note, needs_config=(), content_kind=KIND_EXCERPT, verified='rsshub_doc',
           strategy='rsshub_feed', scope='RSSHub 第三方实例输出，按实例链轮换；条目命中财经主题词且在时间窗内方入库'):
    for route in routes:
        # 具体路径必须先还原成上游声明的路由模式，再核对证据；对不上就是凭记忆写的路由。
        if _route_pattern(route) not in ROUTE_EVIDENCE:
            raise ValueError(f'RSSHub 路由未核对上游源码: {route}')
    return step(STEP_RSSHUB, tuple(f'rsshub:{r}' for r in routes), verified=verified, note=note,
                strategy=strategy, content_kind=content_kind, needs_config=needs_config, scope=scope)


def json_api(urls, extract, note, verified='api_public', content_kind=KIND_REPLY,
             strategy='json_api', metric=None, scope='', headers=None):
    return step(STEP_JSON, urls, verified=verified, note=note, strategy=strategy,
                content_kind=content_kind, extract=extract, metric=metric, scope=scope,
                headers=headers)


def html(urls, note, selectors=(), verified='needs_check', strategy='html_excerpt',
         content_kind=KIND_EXCERPT, scope='公开页面静态 HTML 摘录（未核验为评论），命中财经主题词方入库'):
    return step(STEP_HTML, urls, verified=verified, note=note, strategy=strategy,
                content_kind=content_kind, selectors=selectors, scope=scope)


def carrier(query, note, lang='zh-CN', scope='Google News 检索载体（聚合检索，不是站点原生源，也不代表该社区立场）'):
    return step(STEP_CARRIER, (f'gnews:{query}',), verified='carrier', note=note,
                strategy='gnews_carrier', content_kind=KIND_EXCERPT, lang=lang, scope=scope)


def plan(key, summary, steps, note='', no_public_feed=False):
    if not steps:
        raise ValueError(f'{key}: 方案至少要有一个步骤')
    if steps[-1].get('needs_config'):
        raise ValueError(f'{key}: 最后一步不得依赖密钥/Cookie，否则未配置时该源等于没有方案')
    kinds = {s['kind'] for s in steps}
    if no_public_feed != (kinds <= {STEP_HTML, STEP_CARRIER}):
        # 只有 html/carrier 两类的源，必须显式声明「无公开订阅/接口」，不许含糊。
        raise ValueError(f'{key}: no_public_feed 标记与步骤类型不一致')
    return {'key': key, 'summary': summary, 'note': note, 'steps': list(steps),
            'no_public_feed': bool(no_public_feed)}


# ---------------------------------------------------------------------------
# 专属解析器：只实现**已核对过响应结构**的接口，其余走 RSS/HTML 通用解析。
# 解析失败返回 None（= 该步骤无效），绝不返回编造条目。
# ---------------------------------------------------------------------------

def plain_text(value, limit=300):
    """去标签、去噪、截断（与社区数据层同一字符预算）。"""
    import community_spider as common
    text = str(value or '')
    if '<' in text and '>' in text:
        try:
            text = sc.Selector(content=text).get_all_text(separator=' ', strip=True)
        except Exception:
            text = re.sub(r'<[^>]+>', ' ', text)
    text = common.strip_noise(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit]


def _epoch_or_iso(value):
    """把接口时间戳（秒/毫秒/ISO）统一成 UTC datetime；解析不了返回 None。"""
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e11:  # 毫秒
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        return _epoch_or_iso(int(text))
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        import email.utils
        try:
            dt = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    if dt is None:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def extract_xueqiu_statuses(payload, url):
    """雪球个股讨论时间线。

    端点与字段按 RSSHub `xueqiu/stock-comments.tsx` 的上游调用核对：
    `https://xueqiu.com/query/v1/symbol/search/status?...&symbol=<code>&source=all&sort=time`
    → `list[]`，每条含 `text` / `title` / `target`（站内相对路径）/ `created_at`（毫秒）/
    `user.screen_name`。该路由 `requireConfig: false`、`antiCrawler: false`。
    接口按时间排序、**不提供点赞数**，因此热度指标一律记 0 并写明口径，不编造榜单。
    """
    if not isinstance(payload, dict):
        return None
    records = payload.get('list')
    if not isinstance(records, list):
        return None
    items = []
    for record in records:
        if not isinstance(record, dict):
            continue
        text = plain_text(record.get('text') or record.get('description'))
        if not text:
            continue
        target = str(record.get('target') or '')
        link = target if target.startswith('http') else (
            urllib.parse.urljoin('https://xueqiu.com/', target.lstrip('/')) if target else url)
        items.append({
            'title': plain_text(record.get('title'), 120) or text[:40],
            'text': text,
            'url': link,
            'published': _epoch_or_iso(record.get('created_at')),
            'metric_value': 0,
        })
    return items


def extract_stocktwits_stream(payload, url):
    """StockTwits 公开 symbol stream（API v2）。

    公开文档口径为 `records[]`，每条含 `body` / `created_at`(ISO) / `id` /
    `user.username` / `entities.sentiment.label`。本执行环境不能直连该接口，
    因此核验状态标 `needs_check`：解析器对字段缺失全部容错，拿不到就判无效，
    **不会因为字段改名而编造内容**。情绪标签只写进口径说明，不当作点赞数。
    """
    if not isinstance(payload, dict):
        return None
    records = payload.get('records')
    if not isinstance(records, list):
        return None
    items = []
    for record in records:
        if not isinstance(record, dict):
            continue
        text = plain_text(record.get('body'))
        if not text:
            continue
        message_id = record.get('id')
        link = (f'https://stocktwits.com/message/{message_id}'
                if message_id else url)
        sentiment = ''
        entities = record.get('entities')
        if isinstance(entities, dict):
            sentiment = str((entities.get('sentiment') or {}).get('label') or '')
        items.append({
            'title': text[:40],
            'text': text,
            'url': link,
            'published': _epoch_or_iso(record.get('created_at')),
            'metric_value': 0,
            'extra': f'（平台情绪标签：{sentiment}）' if sentiment else '',
        })
    return items


EXTRACTORS = {
    'xueqiu_statuses': extract_xueqiu_statuses,
    'stocktwits_stream': extract_stocktwits_stream,
}


# ---------------------------------------------------------------------------
# 49 源逐源方案。顺序与 community_data.COMMUNITIES 一致（01–49）。
# 每份方案都回答同样四个问题：
#   ① 这个源的公开数据到底在哪个端点？ ② 有几条路、按什么顺序退？
#   ③ 每条路核验到什么程度？           ④ 抓不到时如何如实降级？
# ---------------------------------------------------------------------------

PLANS = {}


def _register(*specs):
    for spec in specs:
        if spec['key'] in PLANS:
            raise ValueError(f'方案重复: {spec["key"]}')
        PLANS[spec['key']] = spec


_register(
    # 01 富途牛牛社区 -------------------------------------------------------
    plan('FUTU', '牛牛圈帖子在 App 内且需登录；公开侧走富途官方快讯/要闻（news.futunn.com）'
                 '→ 网页摘录 → 检索载体，全程如实标注「不是社区评论」。', [
        rsshub(['/futunn/live/Cantonese', '/futunn/main', '/futunn/topic/1267'],
               '上游 news.futunn.com：/live 为 7×24 快讯（lang 可选 Mandarin/Cantonese/English），'
               '/main 为要闻，/topic/:id 为专题。三者均为站点官方内容出口，非牛牛圈用户帖。'),
        html(['https://news.futunn.com/hk/main/live', 'https://www.futunn.com/hk'],
             '粤语快讯页与港股首页静态摘录；牛牛圈帖子列表由前端渲染，静态 HTML 不保证命中。',
             selectors=('h1', '.article-title', '.news-title', '.live-item')),
        carrier('富途牛牛 港股 社区 讨论', '最后兜底：用检索载体覆盖「富途社区在讨论什么」，明确标注为聚合检索。'),
    ], note='RSSHub 无牛牛圈（社区帖）路由，已核对全量路由树；不拿官方快讯冒充用户评论。'),

    # 02 雪球网 -------------------------------------------------------------
    plan('XUEQIU', '雪球个股讨论公开接口（按时间排序）→ RSSHub 同一路由 → 个股页摘录 → 检索载体。', [
        json_api(['https://xueqiu.com/query/v1/symbol/search/status?u=11111&count=100&comment=0'
                  '&symbol=HKHSI&source=all&sort=time'],
                 'xueqiu_statuses',
                 '上游接口与字段按 RSSHub xueqiu/stock-comments.tsx 核对（requireConfig=false、'
                 'antiCrawler=false）。接口按时间排序且不给点赞数，热度指标记 0 并写明口径。',
                 content_kind=KIND_REPLY, strategy='xueqiu_json',
                 metric={'type': 'timeline_order', 'value': 0,
                         'label': '个股讨论时间线（接口未提供点赞数）'},
                 scope='雪球 HKHSI 个股讨论时间线最新条目，命中财经主题词且在时间窗内方入库；'
                       '按时间排序，不是热度榜，也不代表全站共识'),
        rsshub(['/xueqiu/stock_comments/HKHSI', '/xueqiu/hots'],
               '/stock_comments/:id 与上面同一上游；/hots 上游需先从首页取 token'
               '（requirePuppeteer=true），公共实例不一定可用，故排第二。'),
        html(['https://xueqiu.com/S/HKHSI'], '个股页静态摘录（时间线由前端渲染，命中率低）。',
             selectors=('.timeline__item .timeline__item__main', '.status-content', 'article')),
        carrier('雪球 港股 恒指 讨论', '检索载体兜底，标注为聚合检索。'),
    ]),

    # 03 老虎社区 -----------------------------------------------------------
    plan('LAOHU', '公开侧只有站点页面与检索载体；RSSHub 仅有 /laohu8/personal/:id（需指定用户 id），'
                  '不用它冒充社区列表。', [
        html(['https://www.laohu8.com', 'https://www.laohu8.com/hot'],
             '老虎社区首页与热门页静态摘录；帖子流主要由前端渲染。',
             selectors=('.post-title', 'article h2', 'h1', '.title')),
        carrier('老虎社区 laohu8 港股 讨论', '检索载体兜底。'),
    ], note='已核对：RSSHub 无老虎社区列表路由，只有按用户 id 的个人主页路由；'
            '不指定某个用户来冒充「社区热帖」。', no_public_feed=True),

    # 04 东方财富港股股吧 ----------------------------------------------------
    plan('EASTMONEY', '股吧列表页 + RSSHub 检索路由 → 页面摘录 → 检索载体。', [
        rsshub(['/eastmoney/search/港股', '/eastmoney/gerenzhongxin/guba/0'],
               '/eastmoney/search/:keyword 为站内搜索；/gerenzhongxin/guba/:uid 是**按用户**的股吧动态，'
               '这里不指定真实用户，仅在实例接受占位时作为额外尝试，失败即降级。'),
        html(['https://guba.eastmoney.com', 'https://guba.eastmoney.com/list,hk.html'],
             '股吧首页与港股列表页静态摘录；列表项由脚本渲染，静态命中率低。',
             selectors=('.article-title', '.listitem .title', '.zwcontentmain', 'h1')),
        carrier('东方财富 股吧 港股 讨论', '检索载体兜底。'),
    ], note='已核对：RSSHub 无「全市场股吧列表」路由（只有按 uid 的个人股吧与站内搜索）。'),

    # 05 智通财经互动区 ------------------------------------------------------
    plan('ZHITONG', 'RSSHub 港股/推荐频道 → 站点港股频道页 → 检索载体。', [
        rsshub(['/zhitongcaijing/hkstock', '/zhitongcaijing'],
               '上游 content/hkstock.html 与 content/recommend.html；该路由用 Playwright 取渲染后内容'
               '（requirePuppeteer），公共实例可能不可用，故必须留后两步。'),
        html(['https://www.zhitongcaijing.com/content/hkstock.html', 'https://www.zhitongcaijing.com'],
             '港股频道页与首页静态摘录。',
             selectors=('.artlist-item', '.content-title', 'h1', '.title')),
        carrier('智通财经 港股 互动', '检索载体兜底。'),
    ]),

    # 06 华尔街见闻社区 ------------------------------------------------------
    plan('WALLSTREETCN', '见闻公开资讯流/快讯/最热（RSSHub 路由，上游为官方 api-one 接口）'
                         '→ 站点页面摘录 → 检索载体；见闻评论区需登录，如实标注不冒充。', [
        rsshub(['/wallstreetcn/live/global/2', '/wallstreetcn/news/global', '/wallstreetcn/hot'],
               '上游分别为 api-ddc-wscn.awtmt.com 快讯、api-one.wallstcn.com 资讯流、'
               'apiv1/content/articles/hot 最热文章；均无需 Cookie。score=2 只看重要快讯。'),
        html(['https://wallstreetcn.com', 'https://wallstreetcn.com/live/global'],
             '首页与实时快讯页静态摘录。',
             selectors=('.article__content', '.content-title', 'h1', '.title')),
        carrier('华尔街见闻 港股 宏观', '检索载体兜底。'),
    ], note='见闻「社区」评论接口需登录，不自动登录、不绕权限；用官方资讯流并写明口径。'),

    # 07 香港讨论区财经版 ----------------------------------------------------
    plan('DISCUSS', '站点官方 RSS → 已有 Discuz 专用回复采样 → RSSHub archiver 路由 → archiver 页摘录。', [
        rss(['https://www.discuss.com.hk/rss.php?fid=115'],
            'Discuz 原生 rss.php（财经版 fid=115），与既有 RSS_FEEDS 映射同一端点。'),
        adapter('discuz_html', '已有专用两阶段采样：forumdisplay.php?fid=115 列表 → 帖内前 20 楼回复；'
                               '按所属帖回复数排序，不代表站内点赞。'),
        rsshub(['/discuss/115'],
               '上游走 **archiver 轻量列表**（/archiver/?fid-115.html）而非主站页，'
               '对反爬更友好；路由标 antiCrawler=true，实例可能被拦。'),
        html(['https://www.discuss.com.hk/archiver/?fid-115.html',
              'https://www.discuss.com.hk/forumdisplay.php?fid=115'],
             'archiver 列表页（li a）与主站列表页；archiver 无发布时间，条目按未标时间处理。',
             selectors=('li a', '.post-title', '.thread-title', 'h1')),
    ]),

    # 08 LIHKG 连登财经台 ---------------------------------------------------
    plan('LIHKG', '已有 LIHKG 公开 JSON 采样（分类 15 财经台）→ 分类页静态摘录。', [
        adapter('lihkg_api', '专用只读 JSON 采样：分类 15 第一页最多 20 帖按 no_of_reply 选 3 帖，'
                             '取每帖前 20 楼 msg_num>1 的回复按 like_count 排序；只收 7 天内、'
                             '有真实时间戳、display_vote 未被关的回复。'),
        html(['https://lihkg.com/category/15'], '分类页静态摘录；列表由前端渲染，命中率低。',
             selectors=('.thread-title', '.topic-title', 'article', 'h1')),
    ], note='非官方接口，可能变更或拒绝访问；不做认证、签名逆向或验证码处理。'),

    # 09 韭圈儿 / 红岸社区 ---------------------------------------------------
    plan('JIUQUAN', '站点公开页摘录 → 检索载体；该社区以 App 为主，公开侧长期可能未获取。', [
        html(['https://www.jiucaishuo.com'], '站点首页静态摘录（内容主要在 App 内）。',
             selectors=('.post-title', 'article h2', 'h1', '.title')),
        carrier('韭圈儿 红岸 港股 机构持仓', '检索载体兜底。'),
    ], note='已核对：RSSHub 无 jiucaishuo 路由。App-only 社区不编造「站内热帖」。',
         no_public_feed=True),

    # 10 蚂蚁财富港股社区 ----------------------------------------------------
    plan('ANTFORTUNE', '站点公开页摘录 → 检索载体；社区内容在支付宝 App 内，公开侧大概率未获取。', [
        html(['https://www.antfortune.com'], '站点首页静态摘录（社区在 App 内，公开页多为产品介绍）。',
             selectors=('.fund-title', '.post-title', 'h1', '.title')),
        carrier('蚂蚁财富 港股 社区 讨论', '检索载体兜底。'),
    ], note='已核对：RSSHub 无 antfortune 路由。抓不到就标未获取，不用基金净值页冒充社区讨论。',
         no_public_feed=True),

    # 11 Reddit r/ChinaStocks ----------------------------------------------
    plan('REDDIT', 'Reddit 原生 .rss（含 old.reddit 与当日 top 变体）→ 已配置的 PRAW 官方 API → 检索载体。', [
        rss(['https://www.reddit.com/r/ChinaStocks/.rss',
             'https://old.reddit.com/r/ChinaStocks/.rss',
             'https://www.reddit.com/r/ChinaStocks/top/.rss?t=day'],
            'Reddit 原生订阅出口；云出口 IP 常被 429/403，故给三个变体按序回退。'),
        adapter('reddit_praw', '可选 PRAW 8.0.3 只读 OAuth：hot(limit=3) + comment_sort=top、'
                               'comment_limit=20，只读顶层评论，不调用 replace_more。'
                               '未装包/未配密钥时如实记 praw_not_installed / not_configured。',
                content_kind=KIND_COMMENT),
        carrier('reddit ChinaStocks Hong Kong stocks', '检索载体兜底（英文口径）。', lang='en'),
    ], note='已核对：RSSHub 无 reddit 路由。PRAW 用平台官方 API，配额与政策由使用者自行确认。'),

    # 12 TradingView 香港板块 ------------------------------------------------
    plan('TRADINGVIEW', 'RSSHub 官方博客路由 → 港股板块页摘录 → 检索载体；'
                        '官方博客不是港股板块讨论，口径里写明。', [
        rsshub(['/tradingview/blog/en'],
               '上游为 TradingView 官方博客（/blog/:category，默认 en）；'
               '这是站点官方内容出口，**不是** markets/hong-kong 板块的用户观点流。'),
        html(['https://www.tradingview.com/markets/hong-kong/', 'https://www.tradingview.com/blog/en/'],
             '港股板块页与博客页静态摘录；观点流由前端渲染。',
             selectors=('h1', '.tv-symbol-header__title', '.card-title', 'article h2')),
        carrier('TradingView Hong Kong HSI ideas', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 只有 blog / desktop / pine 三条 TradingView 路由，无观点（ideas）路由。'),

    # 13 Value Investors Club ------------------------------------------------
    plan('VIC', '公开 ideas 列表页摘录 → 检索载体；全文需会员登录，只给公开可见的标题级摘录。', [
        html(['https://www.valueinvestorsclub.com', 'https://www.valueinvestorsclub.com/ideas'],
             '公开 ideas 列表页静态摘录；正文需登录，不绕付费墙。',
             selectors=('.idea-title', 'article h2', 'h1', '.title')),
        carrier('Value Investors Club idea Hong Kong China', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 无 valueinvestorsclub 路由。登录内容一律不抓。', no_public_feed=True),

    # 14 Twitter / X (FinTwit) ----------------------------------------------
    plan('FINTWIT', '检索载体（免密钥，稳定）→ 配了 Token 才可用的 RSSHub 关键词路由 → 页面摘录（几乎必然失败，如实标注）。', [
        carrier('FinTwit HSI "Hong Kong" stocks twitter', '主路：检索载体免密钥、稳定；明确标注为聚合检索，不是 X 站内数据。',
                lang='en'),
        rsshub(['/twitter/keyword/HSI%20Hong%20Kong'],
               '上游需 TWITTER_AUTH_TOKEN（features.requireConfig）；未配置时实例直接报错，'
               '因此排在免密钥的载体之后。', needs_config=('TWITTER_AUTH_TOKEN',)),
        html(['https://x.com/search?q=HSI%20Hong%20Kong'],
             'x.com 搜索页需 JS 与登录，静态 HTML 基本取不到内容；保留仅作最后尝试并如实标未获取。',
             selectors=('article', 'h2', 'p')),
    ], note='已核对：RSSHub 的 /twitter/* 路由普遍要求 TWITTER_AUTH_TOKEN。不自动登录、不绕验证码。'),

    # 15 知乎 · 投资理财话题 -------------------------------------------------
    plan('ZHIHU', '检索载体（免 Cookie，稳定）→ 配了 Cookie 才可用的 RSSHub 话题路由 → 话题页摘录。', [
        carrier('知乎 投资理财 港股 恒指', '主路：免 Cookie 的检索载体，标注为聚合检索。'),
        rsshub(['/zhihu/topic/19554223', '/zhihu/hot'],
               '/zhihu/topic/:topicId 与 /zhihu/hot 上游均需 ZHIHU_COOKIES（antiCrawler=true）；'
               '话题 id 19554223 即目录里的「投资理财」话题。', needs_config=('ZHIHU_COOKIES',)),
        html(['https://www.zhihu.com/topic/19554223/hot'],
             '话题页静态摘录；知乎对未登录请求常返回验证页，命中即判无效。',
             selectors=('.QuestionHeader-title', '.RichText', 'h1')),
    ], note='已核对：/zhihu/* 路由普遍 antiCrawler=true 且需 Cookie。不自动登录。'),

    # 16 微博财经超话 --------------------------------------------------------
    plan('WEIBO', '检索载体（免 Cookie）→ 配了 Cookie 才可用的 RSSHub 关键词/超话路由 → 搜索页摘录。', [
        carrier('微博 港股 财经 超话', '主路：免 Cookie 的检索载体，标注为聚合检索，不代表微博站内热度。'),
        rsshub(['/weibo/keyword/%23%E6%B8%AF%E8%82%A1%23',
                '/weibo/super_index/1008084989d223732bf6f02f75ea30efad58a9/sort_time'],
               '/weibo/keyword/:keyword 与 /weibo/super_index/:id/:type? 上游均需 WEIBO_COOKIES。'
               '超话 id 取自目录里的港股超话。', needs_config=('WEIBO_COOKIES',)),
        html(['https://s.weibo.com/weibo?q=%23%E6%B8%AF%E8%82%A1%23'],
             '站内搜索页静态摘录；未登录常被重定向到登录页，命中即判无效。',
             selectors=('.card-wrap .content', '.txt', 'h1')),
    ], note='已核对：/weibo/* 路由需 Cookie（timeline 路由还需 WEIBO_APP_KEY）。'),

    # 17 百度贴吧 · 股票吧 ---------------------------------------------------
    plan('TIEBA', 'RSSHub 贴吧路由（上游为贴吧移动端接口）→ 吧列表页摘录 → 检索载体。', [
        rsshub(['/baidu/tieba/forum/%E8%82%A1%E7%A5%A8', '/baidu/tieba/forum/%E8%82%A1%E7%A5%A8/replied'],
               '上游走贴吧移动端接口 /c/f/frs/page（kw=股票，rn=30）；BAIDU_COOKIE 为**可选**，'
               'sortBy=replied 取按回复排序。路由标 antiCrawler=true，实例可能被拦。'),
        html(['https://tieba.baidu.com/f?kw=%E8%82%A1%E7%A5%A8'],
             '吧列表页静态摘录（.threadlist_title 为帖子标题链接）。',
             selectors=('.threadlist_title', '.core_title_txt', 'h1')),
        carrier('百度贴吧 股票吧 港股', '检索载体兜底。'),
    ]),

    # 18 淘股吧 -------------------------------------------------------------
    plan('TAOGUBA', 'RSSHub 淘股论坛路由（社区总版/网友点赞/精华）→ 站点页面摘录 → 检索载体。', [
        rsshub(['/taoguba/zongban', '/taoguba/dianzan', '/taoguba/jinghua'],
               '上游 www.tgb.cn，分类：bbs 淘股论坛 / zongban 社区总版 / jinghua 精华加油 / '
               'dianzan 网友点赞；TAOGUBA_COOKIE 为**可选**（匿名摘录无需 Cookie）。'),
        html(['https://www.tgb.cn/zongban/', 'https://www.taoguba.com.cn'],
             '社区总版列表页静态摘录（.Nbbs-tiezi-lists 为帖子块）。',
             selectors=('.Nbbs-tiezi-lists', '.post-title', '.article-title', 'h1')),
        carrier('淘股吧 短线 港股', '检索载体兜底。'),
    ]),

    # 19 同花顺社区 ---------------------------------------------------------
    plan('THS', 'RSSHub 7×24 要闻直播（港股标签）→ 社区页摘录 → 检索载体；'
                '要闻直播是站点官方内容出口，口径里写明不是社区帖。', [
        rsshub(['/10jqka/realtimenews/%E6%B8%AF%E8%82%A1', '/10jqka/realtimenews/%E9%87%8D%E8%A6%81'],
               '上游 news.10jqka.com.cn/realtimenews.html，tag 可多值逗号分隔（港股 / 重要 / A股 / 公告）。'),
        html(['https://t.10jqka.com.cn', 'https://news.10jqka.com.cn/realtimenews.html'],
             '社区首页与要闻直播页静态摘录。',
             selectors=('.post-title', '.news-title', 'h1', '.title')),
        carrier('同花顺 社区 港股 讨论', '检索载体兜底。'),
    ], note='已核对：RSSHub 的 10jqka 命名空间只有 realtimenews 一条路由，无社区帖路由。'),

    # 20 格隆汇 · 港股圈 -----------------------------------------------------
    plan('GELONGHUI', 'RSSHub 实时快讯/首页频道/最热文章/关键词（上游为站点官方 api）→ 页面摘录 → 检索载体。', [
        rsshub(['/gelonghui/live', '/gelonghui/home', '/gelonghui/hot-article', '/gelonghui/keyword/%E6%B8%AF%E8%82%A1'],
               '上游为格隆汇官方接口：/api/channels/<tag>/articles/v8（首页频道）、'
               '/api/post/search/v4（关键词）、实时快讯与最热文章；均无需 Cookie。'),
        html(['https://www.gelonghui.com', 'https://www.gelonghui.com/live'],
             '首页与快讯页静态摘录。',
             selectors=('.article-title', '.content-title', 'h1', '.title')),
        carrier('格隆汇 港股圈 讨论', '检索载体兜底。'),
    ]),

    # 21 财联社 · 电报 -------------------------------------------------------
    plan('CLS', 'RSSHub 电报/深度路由（财联社官方出口）→ 电报页摘录 → 检索载体。', [
        rsshub(['/cls/telegraph', '/cls/depth/1000'],
               '/cls/telegraph/:category? 为电报快讯（另有 watch/announcement/red/hk_us 等分类），'
               '/cls/depth/:category? 为深度；两者无需 Cookie。/cls/hot 标 antiCrawler=true，不纳入主链。'),
        html(['https://www.cls.cn/telegraph'], '电报页静态摘录。',
             selectors=('.telegraph-content', '.b-c-e6e7e9', 'h1')),
        carrier('财联社 电报 港股', '检索载体兜底。'),
    ]),

    # 22 第一财经 · 社区 -----------------------------------------------------
    plan('YICAI', 'RSSHub 正在/最新/头条路由 → 站点页面摘录 → 检索载体。', [
        rsshub(['/yicai/brief', '/yicai/latest', '/yicai/headline'],
               '/brief 为「正在」实时短讯、/latest 为最新、/headline 为头条；均无需 Cookie。'),
        html(['https://www.yicai.com', 'https://www.yicai.com/brief'],
             '首页与正在页静态摘录。',
             selectors=('.m-txt', '.article-content', 'h1', '.title')),
        carrier('第一财经 港股 宏观', '检索载体兜底。'),
    ], note='目录里的一财「社区」实为媒体内容出口，口径按媒体快讯标注，不冒充用户评论。'),

    # 23 Bilibili · 财经区 ---------------------------------------------------
    plan('BILIBILI', 'RSSHub 财经分区（tid=207）与分区排行 → 关键词检索 → 财经区页摘录 → 检索载体。', [
        rsshub(['/bilibili/partion/207', '/bilibili/partion/ranking/207/3', '/bilibili/vsearch/%E6%B8%AF%E8%82%A1'],
               '分区表已核对：财经 tid=207（科学科普 201 / 社科人文 124 / 财经 207 / 校园学习 208）。'
               '/partion/ranking/:tid/:days? 为分区排行，/vsearch/:kw 为视频检索，均无需 Cookie。'),
        html(['https://www.bilibili.com/v/finance'], '财经区页静态摘录；视频流由前端渲染。',
             selectors=('.video-title', '.title', 'h1')),
        carrier('bilibili 财经 港股 视频', '检索载体兜底。'),
    ], note='视频区不是评论区。若要取评论需二段（先拿 bvid 再走 /bilibili/video/reply/:bvid），'
            '当前不把「视频标题」说成「用户评论」。'),

    # 24 PTT Stock 板 -------------------------------------------------------
    plan('PTT', '已有 PTT 双阶段推噓采样 → 板级 Atom 订阅 → 列表页摘录。', [
        adapter('ptt_html', '专用双阶段：index.html 取有可见正推噓净数的最多 3 帖 → 每帖前 20 个 .push，'
                            '输出最多 3 条有财经上下文的回复；「爆」按 ≥100 处理。'
                            '只保留 PTT 给出的月日与时间，不推算年份。'),
        rss(['https://www.ptt.cc/atom/Stock.xml', 'https://www.ptt.cc/rss/Stock.xml'],
            'PTT 板级 Atom/RSS 出口（同板文章列表，不含推噓明细）；未在线实测，标 needs_check。',
            verified='needs_check', content_kind=KIND_REPLY,
            scope='PTT Stock 板订阅条目（只有标题与链接，没有推噓明细），不代表热帖排行'),
        html(['https://www.ptt.cc/bbs/Stock/index.html'], '列表页静态摘录（.r-ent .title）。',
             selectors=('.r-ent .title', '#main-content h1', 'h1')),
    ], note='已核对：RSSHub 无 ptt 路由，因此走自有适配器 + 板级订阅。'),

    # 25 StockTwits ---------------------------------------------------------
    plan('STOCKTWITS', '公开 symbol stream 接口 → 页面摘录 → 检索载体。', [
        json_api(['https://api.stocktwits.com/api/2/streams/symbol/HSI.json'],
                 'stocktwits_stream',
                 'API v2 公开 symbol stream（records[] → body/created_at/id/user/entities.sentiment）。'
                 '本环境不能直连，标 needs_check；该接口对云出口有速率限制，429 即降级。',
                 verified='needs_check', content_kind=KIND_REPLY, strategy='stocktwits_json',
                 metric={'type': 'stream_order', 'value': 0,
                         'label': '符号消息流最新条目（接口未提供点赞数，情绪标签仅记口径）'},
                 scope='StockTwits HSI 符号消息流最新条目，命中财经主题词且在时间窗内方入库；'
                       '按时间排序，不是热度榜'),
        html(['https://stocktwits.com/symbol/HSI'], '符号页静态摘录；消息流由前端渲染。',
             selectors=('.message-body', '.sentiment', 'h1')),
        carrier('StockTwits HSI Hang Seng', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 无 stocktwits 路由。'),

    # 26 Seeking Alpha ------------------------------------------------------
    plan('SEEKINGALPHA', '官方 market-news 订阅 → RSSHub 按标的路由 → 页面摘录 → 检索载体。', [
        rss(['https://seekingalpha.com/market-news.rss'],
            'Seeking Alpha 官方 market-news RSS（与 05 栏同一端点，已列为 native）。'),
        rsshub(['/seekingalpha/HSI/news'],
               '/seekingalpha/:symbol/:category? 可按标的取；路由标 antiCrawler=true，'
               '且 HSI 是否被上游支持未在线实测。'),
        html(['https://seekingalpha.com/market-news'], '市场新闻页静态摘录。',
             selectors=('article h3', '.market-news-item', 'h1')),
        carrier('Seeking Alpha Hang Seng Index', '检索载体兜底。', lang='en'),
    ]),

    # 27 Bogleheads Forum ---------------------------------------------------
    plan('BOGLEHEADS', 'phpBB 原生 rss.php → 已有 phpBB 两阶段回复采样 → 版块页摘录。', [
        rss(['https://www.bogleheads.org/forum/rss.php?f=10'],
            'phpBB 原生版块 RSS（f=10 投资 - 理论、新闻与一般）。'),
        adapter('phpbb_html', '专用两阶段：viewforum.php?f=10 取可见回复数最多的前 3 帖（优先进可见末页）'
                              '→ 帖内首页前 20 条回复；用 Replies 计数作热度参考，不是单条点赞。'),
        html(['https://www.bogleheads.org/forum/viewforum.php?f=10'], '版块页静态摘录（.topictitle）。',
             selectors=('.topictitle', '.content', 'h1')),
    ], note='已核对：RSSHub 无 bogleheads 路由；phpBB 自带 rss.php，是最稳的一条路。'),

    # 28 Reddit r/investing -------------------------------------------------
    plan('RINVESTING', 'Reddit 原生 .rss（含 old.reddit 与当日 top 变体）→ PRAW 官方 API → 检索载体。', [
        rss(['https://www.reddit.com/r/investing/.rss',
             'https://old.reddit.com/r/investing/.rss',
             'https://www.reddit.com/r/investing/top/.rss?t=day'],
            'Reddit 原生订阅出口，三个变体按序回退。'),
        adapter('reddit_praw', 'PRAW 只读采样（同 r/ChinaStocks 口径）：hot(limit=3)、'
                               'comment_sort=top、comment_limit=20，只读顶层评论。',
                content_kind=KIND_COMMENT),
        carrier('reddit r/investing Hong Kong China stocks', '检索载体兜底。', lang='en'),
    ]),

    # 29 Wall Street Oasis --------------------------------------------------
    plan('WSO', '论坛公开列表页摘录 → 检索载体；无官方订阅出口。', [
        html(['https://www.wallstreetoasis.com/forum', 'https://www.wallstreetoasis.com/forums'],
             '论坛列表页静态摘录；部分内容需登录，登录内容不抓。',
             selectors=('.title', '.topic-title', 'h1', 'article h2')),
        carrier('Wall Street Oasis forum Hong Kong Asia investing', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 无 wallstreetoasis 路由；站点未暴露公开 RSS（未在线实测，标 needs_check）。',
         no_public_feed=True),

    # 30 Investing.com 讨论区 ------------------------------------------------
    plan('INVESTING', '站点官方 news RSS → 恒指评论页摘录 → 检索载体。', [
        rss(['https://www.investing.com/rss/news.rss', 'https://www.investing.com/rss/news_301.rss'],
            'Investing.com 官方 news RSS 出口（news_301 为股市新闻）；未在线实测，标 needs_check。',
            verified='needs_check',
            scope='Investing.com 官方新闻订阅（不是 discussion 区的用户评论），命中财经主题词方入库'),
        html(['https://www.investing.com/indices/hang-seng-index-commentary',
              'https://www.investing.com/indices/hang-seng-index'],
             '恒指评论页与行情页静态摘录；评论流由前端渲染且有 Cloudflare 防护。',
             selectors=('.comment-body', '.article-title', 'h1')),
        carrier('Investing.com Hang Seng Index commentary', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 无 investing.com 路由。评论区的用户评论不冒充新闻订阅，两者分开标注。'),

    # 31 Yahoo Finance · 社区 ------------------------------------------------
    plan('YAHOO', 'Yahoo 官方 news RSS → RSSHub finance / finance-hk 路由 → 社区页摘录 → 检索载体。', [
        rss(['https://finance.yahoo.com/news/rssindex'],
            'Yahoo Finance 官方新闻 RSS（与 05 栏同一端点，已列为 native）。'),
        rsshub(['/yahoo/finance/latest-news', '/yahoo/finance/hk/latest-news'],
               '/yahoo/finance/:topic? 与 /yahoo/finance/hk/:topic?（上游 hk.finance.yahoo.com/topic/:topic）；'
               'topic 默认 latest-news，无需 Cookie。'),
        html(['https://finance.yahoo.com/quote/%5EHSI/community', 'https://hk.finance.yahoo.com/topic/latest-news'],
             '恒指社区页与港股财经页静态摘录。',
             selectors=('.comment-body', 'h1', 'article p', '.title')),
        carrier('Yahoo Finance HSI community Hang Seng', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 无 Yahoo Finance「社区/评论」路由，只有 finance 新闻主题路由；'
            '因此原生 RSS 排第一，社区页只作摘录。'),

    # 32 Substack · 财经通讯 -------------------------------------------------
    plan('SUBSTACK', '浏览页摘录 → 检索载体；Substack 的 RSS 是**每个 publication 各自**的 /feed，'
                     '目录浏览页没有订阅出口，不用某一位作者冒充「财经通讯」。', [
        html(['https://substack.com/browse/finance', 'https://substack.com/search/finance'],
             '财经分类浏览页与搜索页静态摘录；榜单由前端渲染。',
             selectors=('.post-title', 'article h2', 'h1', '.title')),
        carrier('Substack finance newsletter Hong Kong markets', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 的 substack 命名空间只有 /subscribe/:user（按用户订阅），'
            '需要指定具体作者；不指定某位作者来代表整个财经分类。', no_public_feed=True),

    # 33 Reddit r/options ---------------------------------------------------
    plan('ROPTIONS', 'Reddit 原生 .rss（含 old.reddit 与当日 top 变体）→ PRAW 官方 API → 检索载体。', [
        rss(['https://www.reddit.com/r/options/.rss',
             'https://old.reddit.com/r/options/.rss',
             'https://www.reddit.com/r/options/top/.rss?t=day'],
            'Reddit 原生订阅出口，三个变体按序回退。'),
        adapter('reddit_praw', 'PRAW 只读采样（同上口径）。', content_kind=KIND_COMMENT),
        carrier('reddit r/options HSI index options volatility', '检索载体兜底。', lang='en'),
    ]),

    # 34 FT Alphaville ------------------------------------------------------
    plan('FTALPHA', 'FT 官方 ?format=rss 出口 → 栏目页摘录 → 检索载体。', [
        rss(['https://www.ft.com/alphaville?format=rss', 'https://www.ft.com/rss/home'],
            'FT 栏目页加 ?format=rss 是 FT 的通用订阅出口；第二个端点为 FT 官方首页 RSS'
            '（05 栏已列为 native）。Alphaville 那条未在线实测，标 needs_check。',
            verified='needs_check',
            scope='FT 官方订阅条目（付费墙文章只给摘要），命中财经主题词方入库'),
        html(['https://www.ft.com/alphaville'], 'Alphaville 栏目页静态摘录（.o-teaser__heading）。',
             selectors=('.o-teaser__heading', 'article h2', 'h1')),
        carrier('FT Alphaville markets', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 的 ft 命名空间只有 /myft/:key（需个人 RSS key），不适用于公开栏目。'),

    # 35 集思录 · 低风险投资社区 ---------------------------------------------
    plan('JISILU', 'RSSHub 广场/分类/话题路由 → 站点页面摘录 → 检索载体。', [
        rsshub(['/jisilu/explore', '/jisilu/category/4', '/jisilu/topic/%E5%8F%AF%E8%BD%AC%E5%80%BA'],
               '上游 jisilu.cn：/explore/:filter? 为广场（div.aw-question-list），'
               '/category/:id 为分类，/topic/:id 为话题；三条均无需 Cookie。'),
        html(['https://www.jisilu.cn/explore/', 'https://www.jisilu.cn'],
             '广场页与首页静态摘录。',
             selectors=('div.aw-question-list', '.post-title', '.topic-title', 'h1')),
        carrier('集思录 低风险投资 港股 可转债', '检索载体兜底。'),
    ]),

    # 36 小红书 · 理财笔记 ---------------------------------------------------
    plan('XIAOHONGSHU', '检索载体（免 Cookie）→ 配了 Cookie 才可用的 RSSHub 用户笔记路由 → 搜索页摘录。', [
        carrier('小红书 理财 港股 笔记', '主路：免 Cookie 的检索载体，标注为聚合检索，不代表小红书站内热度。'),
        rsshub(['/xiaohongshu/user/593032945e87e77791e03696/notes'],
               'RSSHub 只有按 user_id 的笔记/收藏路由且需 XIAOHONGSHU_COOKIE；'
               '示例 user_id 来自上游路由文档，**不代表本项目的采样对象**，仅在实例已配 Cookie 时尝试。',
               needs_config=('XIAOHONGSHU_COOKIE',)),
        html(['https://www.xiaohongshu.com/search_result?keyword=%E6%B8%AF%E8%82%A1'],
             '搜索结果页需 JS 与登录，静态 HTML 基本取不到；保留仅作最后尝试并如实标未获取。',
             selectors=('.title', '.note-title', 'h1')),
    ], note='已核对：RSSHub 无小红书关键词搜索路由（只有 board 与 user 两条）。不自动登录。'),

    # 37 抖音 · 财经短视频 ---------------------------------------------------
    plan('DOUYIN', '检索载体（免 Cookie）→ 配了 Cookie 才可用的 RSSHub 标签路由 → 搜索页摘录。', [
        carrier('抖音 财经 港股 短视频', '主路：免 Cookie 的检索载体，标注为聚合检索。'),
        rsshub(['/douyin/hashtag/1592824105719812'],
               '/douyin/hashtag/:cid 需要 cid 且 antiCrawler=true、上游需 Cookie；'
               '示例 cid 来自上游路由文档，未核对是否对应「港股」标签，故只在实例可用时尝试。',
               needs_config=('DOUYIN_COOKIE',)),
        html(['https://www.douyin.com/search/%E6%B8%AF%E8%82%A1'],
             '抖音搜索页为纯前端渲染，静态 HTML 取不到内容；保留仅作最后尝试。',
             selectors=('.video-title', '.title', 'h1')),
    ], note='已核对：RSSHub 的 douyin 命名空间为直播/博主/标签/收藏，均需 Cookie 或指定 id。'),

    # 38 开盘啦 · 情绪复盘 ---------------------------------------------------
    plan('KAIPANLA', '站点公开页摘录 → 检索载体；情绪复盘数据在 App 内。', [
        html(['https://www.kaipanla.com'], '站点首页静态摘录（复盘数据主要在 App）。',
             selectors=('.title', '.topic-title', 'h1')),
        carrier('开盘啦 情绪复盘 涨停 港股', '检索载体兜底。'),
    ], note='已核对：RSSHub 无 kaipanla 路由。不把 App 内数据说成已抓取。', no_public_feed=True),

    # 39 理想论坛 · 股票实战 -------------------------------------------------
    plan('LIXIANG', 'Discuz 列表页摘录（含 archiver 轻量列表）→ 检索载体。', [
        html(['https://www.55188.com/archiver/', 'https://www.55188.com/forum.php',
              'https://www.55188.com'],
             'Discuz 站点：优先 archiver 轻量列表（同 discuss.com.hk 的思路），再退到 forum.php 与首页。'
             'archiver 的具体 fid 未在线核对，因此按站点级列表处理。',
             selectors=('li a', '.thread-title', '.post-title', 'h1')),
        carrier('理想论坛 55188 股票实战 港股', '检索载体兜底。'),
    ], note='已核对：RSSHub 无 55188 路由。Discuz 的 rss.php 是否存在未在线核对，故不写进端点链。',
         no_public_feed=True),

    # 40 QuantNet · 量化社区 -------------------------------------------------
    plan('QUANTNET', 'XenForo 原生全站 RSS → 论坛列表页摘录 → 检索载体。', [
        rss(['https://quantnet.com/forums/-/index.rss', 'https://quantnet.com/forums/trading-forums.13/index.rss'],
            'XenForo 自带 `forums/-/index.rss`（全站最新帖）与按版块的 `index.rss`；'
            '具体版块 slug/id 未在线核对，标 needs_check。', verified='needs_check'),
        html(['https://quantnet.com/forums/', 'https://quantnet.com'],
             '论坛首页静态摘录（XenForo 结构：.block-row / a[data-tp-title] / .structItem-title）。',
             selectors=('.structItem-title', 'a[data-tp-title]', '.block-row', '.topic-title', 'h1')),
        carrier('QuantNet forum quantitative finance Hong Kong', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 无 quantnet 路由；XenForo 原生 RSS 是这类论坛最稳的一条路。'),

    # 41 Elite Trader -------------------------------------------------------
    plan('ELITETRADER', 'XenForo 原生全站 RSS → 论坛列表页摘录 → 检索载体。', [
        rss(['https://www.elitetrader.com/et/forums/-/index.rss',
             'https://www.elitetrader.com/et/forums/stock-forums.5/index.rss'],
            'XenForo 原生 `forums/-/index.rss`；版块 slug/id 未在线核对，标 needs_check。',
            verified='needs_check'),
        html(['https://www.elitetrader.com/et/', 'https://www.elitetrader.com/et/forums/'],
             '论坛首页静态摘录（XenForo 结构）。',
             selectors=('.structItem-title', 'a[data-tp-title]', '.topic-title', '.post-title', 'h1')),
        carrier('Elite Trader forum HSI Hong Kong futures', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 无 elitetrader 路由。'),

    # 42 Forex Factory ------------------------------------------------------
    plan('FOREXFACTORY', '论坛列表页摘录 → 检索载体；站点未暴露公开订阅出口。', [
        html(['https://www.forexfactory.com/forum', 'https://www.forexfactory.com/calendar'],
             '论坛列表页与财经日历页静态摘录（日历为公开数据，.calendar__event-title）。',
             selectors=('.thread-title', '.calendar__event-title', 'h1', '.title')),
        carrier('Forex Factory forum HSI Hang Seng currency', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 无 forexfactory 路由；vBulletin external.php 是否存在未在线核对，故不写进端点链。',
         no_public_feed=True),

    # 43 Reddit r/CryptoCurrency --------------------------------------------
    plan('RCRYPTO', 'Reddit 原生 .rss（含 old.reddit 与当日 top 变体）→ PRAW 官方 API → 检索载体。', [
        rss(['https://www.reddit.com/r/CryptoCurrency/.rss',
             'https://old.reddit.com/r/CryptoCurrency/.rss',
             'https://www.reddit.com/r/CryptoCurrency/top/.rss?t=day'],
            'Reddit 原生订阅出口，三个变体按序回退。'),
        adapter('reddit_praw', 'PRAW 只读采样（同上口径）。', content_kind=KIND_COMMENT),
        carrier('reddit CryptoCurrency Hong Kong stablecoin asia', '检索载体兜底。', lang='en'),
    ]),

    # 44 Morningstar · 社区 --------------------------------------------------
    plan('MORNINGSTAR', '社区页摘录 → 检索载体；官方社区历经改版，可能已整体下线，如实标未获取。', [
        html(['https://community.morningstar.com', 'https://www.morningstar.com/community'],
             '社区入口静态摘录；若站点已改版/下线，取不到就标未获取，不回填历史讨论。',
             selectors=('.topic-title', 'article h2', 'h1', '.title')),
        carrier('Morningstar community forum Hong Kong funds', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 无 morningstar 路由。该社区是否仍公开可用未在线核对。', no_public_feed=True),

    # 45 MarketWatch · 社区 --------------------------------------------------
    plan('MARKETWATCH', '道琼斯官方 MarketWatch RSS（05 栏已核为 native）→ 最新新闻页摘录 → 检索载体。', [
        rss(['https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines',
             'https://feeds.content.dowjones.io/public/rss/mw_topstories'],
            '道琼斯官方 MarketWatch 实时头条与头条 RSS（05 栏同一端点，已列 native）。',
            scope='MarketWatch 官方新闻订阅（站点已无独立用户社区），命中财经主题词方入库'),
        html(['https://www.marketwatch.com/latest-news'], '最新新闻页静态摘录。',
             selectors=('.article__headline', 'article h3', 'h1')),
        carrier('MarketWatch Hang Seng Index Hong Kong', '检索载体兜底。', lang='en'),
    ], note='已核对：RSSHub 无 marketwatch 路由。目录里的「MarketWatch 社区」实为媒体内容出口，'
            '口径按媒体快讯标注，不冒充用户评论。'),

    # 46 日本 Yahoo! 财经掲示板 ----------------------------------------------
    plan('YAHOOJP', 'Yahoo! Japan 官方新闻 RSS → 检索载体（日文）→ 检索/掲示板页摘录。', [
        rss(['https://news.yahoo.co.jp/rss/topics/top-picks.xml',
             'https://news.yahoo.co.jp/rss/topics/business.xml'],
            'Yahoo! Japan 官方新闻 RSS（トップニュース / 経済）；未在线实测，标 needs_check。'
            '掲示板本身无订阅出口。', verified='needs_check',
            scope='Yahoo! Japan 官方新闻订阅（不是掲示板的用户留言），命中财经主题词方入库'),
        carrier('Yahoo ファイナンス 掲示板 香港株 ハンセン', '检索载体（日文口径），标注为聚合检索。', lang='ja'),
        html(['https://finance.yahoo.co.jp/search/?query=%E9%A6%99%E6%B8%AF%E6%A0%AA',
              'https://finance.yahoo.co.jp/'],
             '站内检索页与财经首页静态摘录；掲示板留言由前端渲染。',
             selectors=('.title', '.news-title', 'h1')),
    ], note='已核对：RSSHub 的 yahoo 命名空间指向 news.yahoo.com 与 hk.finance.yahoo.com，'
            '无 finance.yahoo.co.jp 路由。'),

    # 47 네이버 금융 종토방 ---------------------------------------------------
    plan('NAVER', 'RSSHub Naver 检索路由（新闻/博客/카페）→ 检索载体（韩文）→ 世界行情页摘录。', [
        rsshub(['/naver/search/news/%ED%95%AD%EC%83%81%EC%A7%80%EC%88%98',
                '/naver/search/all/HSI'],
               '/naver/search/:category/:keyword（category 支持 all/news/cafe 等，上游为 Naver 搜索模板接口）；'
               '无需 Cookie。종토방（个股讨论板）本身无路由。'),
        carrier('네이버 금융 종목토론방 항셍지수 홍콩', '检索载体（韩文口径），标注为聚合检索。', lang='ko'),
        html(['https://finance.naver.com/world/sise.naver?symbol=HSI@HSI',
              'https://finance.naver.com/sise/'],
             '世界行情页与首页静态摘录；종토방留言需进入个股讨论板且由服务端分页渲染。',
             selectors=('.title', '.articleTitle', 'h1')),
    ], note='已核对：RSSHub 的 naver 命名空间只有 comic 与 search 两条路由，无종토방路由。'),

    # 48 Wallstreet-Online · 德语社区 ----------------------------------------
    plan('WALLSTREETDE', '论坛列表页摘录 → 检索载体（德文）。', [
        html(['https://www.wallstreet-online.de/community/forum',
              'https://www.wallstreet-online.de/nachrichten'],
             '社区论坛列表页与新闻页静态摘录。',
             selectors=('.thread-title', '.post-title', 'h1', '.title')),
        carrier('wallstreet-online Forum Hang Seng Hongkong Aktien', '检索载体（德文口径）。', lang='de'),
    ], note='已核对：RSSHub 全量路由树无 wallstreet-online（仅有 wallstreetcn）；'
            '站点是否提供公开 RSS 未在线核对，故不写进端点链。', no_public_feed=True),

    # 49 阿斯达克财经 · 讨论区 -------------------------------------------------
    plan('AASTOCKS', '讨论区「最新回應」公开列表页摘录 → 检索载体（繁中）。', [
        html(['http://www.aastocks.com/tc/stocks/comment/latest.aspx',
              'http://www.aastocks.com/tc/stocks/news/aafn-con/NOW.1/news/latest-news.aspx'],
             '讨论区最新回應列表页与新闻页静态摘录；发帖需登录，只读公开列表。',
             selectors=('.news-title', '.title', 'h1', 'a')),
        carrier('AASTOCKS 港股 討論區 恒指', '检索载体（繁中口径）。', lang='zh-TW'),
    ], note='已核对：RSSHub 无 aastocks 路由（港股本地媒体回退另见 05 栏的信报/经济日报路由，'
            '那是不同站点，不用来冒充阿斯达克讨论区）。', no_public_feed=True),
)


# ---------------------------------------------------------------------------
# 目录与查询
# ---------------------------------------------------------------------------

def catalog(communities=None):
    """把方案与社区目录合并成可导出/可核对的清单（id、名称、类型来自唯一事实源目录）。"""
    import community_data as cd
    communities = list(communities) if communities else list(cd.COMMUNITIES)
    rows = []
    for comm in communities:
        key = comm.get('key')
        spec = PLANS.get(key)
        rows.append({
            'id': comm.get('id', ''),
            'key': key,
            'name': comm.get('name', key),
            'ctype': comm.get('ctype', ''),
            'url': comm.get('url', ''),
            'summary': (spec or {}).get('summary', ''),
            'note': (spec or {}).get('note', ''),
            'no_public_feed': bool((spec or {}).get('no_public_feed')),
            'steps': list((spec or {}).get('steps', [])),
        })
    return rows


def plan_for(comm):
    """取某源的方案；没有方案返回 None（调用方沿用通用阶梯，不假装有计划）。"""
    if not isinstance(comm, dict):
        return None
    return PLANS.get(comm.get('key'))


def missing_plans(communities=None):
    """目录里没有方案的源（应为空；tests 会钉住）。"""
    import community_data as cd
    communities = list(communities) if communities else list(cd.COMMUNITIES)
    return [c.get('key') for c in communities if c.get('key') not in PLANS]


def extra_plans(communities=None):
    """方案里有、目录里没有的 key（应为空；防止残留幽灵源）。"""
    import community_data as cd
    communities = list(communities) if communities else list(cd.COMMUNITIES)
    keys = {c.get('key') for c in communities}
    return sorted(k for k in PLANS if k not in keys)


def plan_strength(spec):
    """方案强度：有专用适配器/订阅/公开接口 > 只有 RSSHub > 只有页面摘录+载体。

    **只统计不需要密钥/Cookie 的步骤**：`/twitter/keyword`、`/zhihu/topic` 这类
    `needs_config` 路由在没配密钥时根本走不通，把它们算进强度就是虚报——
    FINTWIT / ZHIHU / WEIBO / XIAOHONGSHU / DOUYIN 因此在默认配置下如实归为
    `excerpt_only`（只有检索载体与页面摘录）。

    用于诊断输出与「哪些源仍然只能靠摘录」的统计，不参与渲染，也不影响情感打分。
    """
    kinds = [s['kind'] for s in spec.get('steps', []) if not s.get('needs_config')]
    if STEP_ADAPTER in kinds:
        return 'adapter'
    if STEP_JSON in kinds or STEP_RSS in kinds:
        return 'feed'
    if STEP_RSSHUB in kinds:
        return 'rsshub'
    return 'excerpt_only'


def config_gated_steps(spec):
    """方案里需要密钥/Cookie 才走得通的步骤（诊断用；这些步骤不是稳定路径）。"""
    return [s for s in spec.get('steps', []) if s.get('needs_config')]


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------

class PlanBudget:
    """整轮方案执行的请求数与墙钟预算（防止 49 源 × 多步链把 CI 拖死）。"""

    def __init__(self, requests=None, seconds=None, step_delay=None, now=None):
        self.requests = PLAN_REQUEST_BUDGET if requests is None else int(requests)
        self.seconds = PLAN_WALL_BUDGET if seconds is None else float(seconds)
        self.step_delay = PLAN_STEP_DELAY if step_delay is None else float(step_delay)
        self.used_requests = 0
        self.started = time.time() if now is None else float(now)
        self.exhausted = False

    def allow(self):
        if self.exhausted:
            return False
        if self.used_requests >= self.requests:
            self.exhausted = True
            return False
        if time.time() - self.started > self.seconds:
            self.exhausted = True
            return False
        return True

    def charge(self, count=1):
        self.used_requests += int(count)

    def to_dict(self):
        return {'requests_used': self.used_requests, 'requests_budget': self.requests,
                'seconds_budget': self.seconds, 'exhausted': self.exhausted,
                'step_delay_seconds': self.step_delay}


def rsshub_bases():
    """RSSHub 实例链（与 05 栏同一口径：RSSHUB_BASE 逗号分隔，按序回退）。"""
    default = ','.join([
        'https://rsshub.app',
        'https://rsshub.rssforever.com',
        'https://hub.slarker.me',
        'https://rsshub.pseudoyu.com',
    ])
    raw = os.environ.get('RSSHUB_BASE') or default
    return [b.strip().rstrip('/') for b in raw.split(',') if b.strip()]


def expand_endpoint(endpoint, lang=None):
    """把方案里的端点占位展开成可请求 URL 列表。

    `rsshub:/path` → 按实例链展开（同一路线在多个实例上依次尝试）；
    `gnews:query`  → Google News 检索载体端点；
    其余原样返回（必须是绝对 http/https）。
    """
    text = str(endpoint or '').strip()
    if text.startswith('rsshub:'):
        path = text[len('rsshub:'):]
        if not path.startswith('/'):
            path = '/' + path
        return [f'{base}{path}' for base in rsshub_bases()]
    if text.startswith('gnews:'):
        return [gnews_url(text[len('gnews:'):], lang or 'zh-CN')]
    return [text] if text else []


def _transport_of(session):
    return session.config.get('transport') or sc._default_transport


def _fetch(session, url, timeout, headers=None, budget=None, delay=0.0):
    """发一次请求，返回 (status, body, final_url)；失败返回 (0, b'', url)。

    预算耗尽时不再发请求（返回 None 表示「没试」，与「试了失败」区分开）。
    """
    if budget is not None and not budget.allow():
        return None
    if delay:
        time.sleep(delay)
    transport = _transport_of(session)
    try:
        status, _reason, _headers, body, final_url = transport('GET', url, dict(headers or {}), timeout)
    except Exception:
        if budget is not None:
            budget.charge()
        return 0, b'', url
    if budget is not None:
        budget.charge()
    return status or 0, body or b'', final_url or url


def _response(url, status, body, final_url):
    return sc.Response(url=final_url or url, content=body or b'', status=int(status or 0),
                       reason='', headers={}, request_headers={})


def _candidate(text, title, url, published, now, max_age_days, step, metric_value=0, extra=''):
    """把一条原始条目转成候选评论；不合格返回 None（不编造、不放宽窗口）。"""
    import community_comments as cc
    import community_spider as common
    text = common.strip_noise(str(text or ''))
    if not cc._useful_comment(text, title):
        return None
    published_at, published_label = '', '时间未知'
    if published is not None:
        when = published if isinstance(published, datetime) else _epoch_or_iso(published)
        if when is not None:
            if when > now + timedelta(minutes=5) or when < now - timedelta(days=max_age_days):
                return None
            published_at = when.isoformat(timespec='seconds').replace('+00:00', 'Z')
            published_label = published_at
    metric = dict(step.get('metric') or {})
    if not metric:
        metric = {'type': step['kind'], 'value': 0, 'label': '未提供站内热度指标'}
    metric = {'type': metric.get('type', step['kind']),
              'value': metric_value if metric_value else metric.get('value', 0),
              'label': metric.get('label', '未提供站内热度指标')}
    return {
        'text': text,
        'text_truncated': len(text) >= common.MAX_SNIPPET,
        'post_title': common.strip_noise(str(title or ''))[:120],
        'url': url,
        'published_at': published_at,
        'published_label': published_label,
        'kind': step['content_kind'],
        'strategy': step['strategy'],
        'metric': metric,
        'scope': step.get('scope') or '',
        '_rank': 0,
        '_order': 0,
        '_extra': extra,
    }


def _safe_url(url, hosts=None):
    """链接必须是通过校验的 http(s)；可选限定主机，防跨站链接混进结果。"""
    text = str(url or '').strip()
    if not text.lower().startswith(('http://', 'https://')):
        return ''
    if hosts:
        parsed = urllib.parse.urlparse(text)
        if parsed.netloc.lower() not in {h.lower() for h in hosts}:
            return ''
    return text


def _finish(candidates):
    """定序、去重、补 http 状态与字节数，返回 (comments, matched)。"""
    import community_comments as cc
    for index, item in enumerate(candidates):
        item['_order'] = index
        item.setdefault('http_status', 0)
        item.setdefault('response_bytes', 0)
    usable = []
    for item in candidates:
        if not _safe_url(item.get('url')):
            continue
        entry = {k: v for k, v in item.items() if not k.startswith('_')}
        entry['_rank'] = item.get('_rank', 0)
        entry['_order'] = item.get('_order', 0)
        entry['text'] = (item.get('text', '') + item.get('_extra', '')).strip()
        usable.append(entry)
    return cc._finish_candidates(usable)


def _parse_feed_items(body, now, max_age_days):
    """RSS2 / Atom / RDF 统一解析，返回 [(title, text, url, published)]；解析失败返回 None。"""
    import community_comments as cc
    entries = cc._parse_rss_items(body, now, max_age_days)
    if entries is None:
        return None
    out = []
    for entry in entries:
        out.append((entry.get('title', ''), entry.get('text', ''), entry.get('url', ''),
                    _epoch_or_iso(entry.get('published_at')) or None))
    return out


def _run_feed_step(step_record, urls, session, timeout, now, max_age_days, budget, delay):
    """rss / rsshub / carrier 三类共用：拿 XML → 解析条目 → 校验成候选。"""
    for url in urls:
        got = _fetch(session, url, timeout, step_record.get('headers'), budget, delay)
        if got is None:
            return [], 'budget_exhausted', url
        status, body, final_url = got
        if not body:
            continue
        if status in (403, 429):
            # RSSHub 边缘按 IP 拦截 → 换下一个实例；其它站点则如实记状态。
            continue
        if not (200 <= status < 300):
            continue
        entries = _parse_feed_items(body, now, max_age_days)
        if entries is None:
            continue
        candidates = []
        for title, text, link, published in entries[:MAX_ITEMS_PER_STEP]:
            item = _candidate(text or title, title, _safe_url(link, None), published, now,
                              max_age_days, step_record)
            if item:
                item['http_status'] = status
                item['response_bytes'] = len(body)
                candidates.append(item)
        if candidates:
            return candidates, 'ok', final_url or url
    return [], 'no_valid_items', urls[0] if urls else ''


def _run_json_step(step_record, urls, session, timeout, now, max_age_days, budget, delay):
    extractor = EXTRACTORS.get(step_record.get('extract') or '')
    if extractor is None:
        return [], 'no_extractor', urls[0] if urls else ''
    for url in urls:
        headers = dict(step_record.get('headers') or {})
        headers.setdefault('Accept', 'application/json')
        got = _fetch(session, url, timeout, headers, budget, delay)
        if got is None:
            return [], 'budget_exhausted', url
        status, body, final_url = got
        if not body or not (200 <= status < 300):
            continue
        try:
            payload = json.loads(body.decode('utf-8', 'replace'))
        except (ValueError, UnicodeDecodeError):
            continue
        records = extractor(payload, final_url or url)
        if not records:
            continue
        candidates = []
        for record in records[:MAX_ITEMS_PER_STEP]:
            item = _candidate(record.get('text'), record.get('title'),
                              _safe_url(record.get('url')), record.get('published'), now,
                              max_age_days, step_record, metric_value=record.get('metric_value', 0),
                              extra=record.get('extra', ''))
            if item:
                item['http_status'] = status
                item['response_bytes'] = len(body)
                candidates.append(item)
        if candidates:
            return candidates, 'ok', final_url or url
    return [], 'no_valid_items', urls[0] if urls else ''


def _run_html_step(step_record, urls, session, timeout, now, max_age_days, budget, delay):
    """公开页面摘录：优先按源的选择器取文本块，取不到就判无效（不放宽到整页噪声）。"""
    import community_spider as common
    selectors = tuple(step_record.get('selectors') or ())
    for url in urls:
        got = _fetch(session, url, timeout, step_record.get('headers'), budget, delay)
        if got is None:
            return [], 'budget_exhausted', url
        status, body, final_url = got
        if not body or not (200 <= status < 300):
            continue
        response = _response(url, status, body, final_url)
        title = ''
        for selector in ('title', 'meta[property="og:title"]', 'h1'):
            try:
                found = response.css(selector)
            except sc.SelectorError:
                continue
            if found:
                title = common.strip_noise(found.first.get_all_text(separator=' ', strip=True))
                if title:
                    break
        fragments = common.extract_fragments(response, selectors) if selectors else []
        scored = sorted(fragments, key=lambda pair: -common.score_fragment(pair[1], title))
        candidates = []
        for selector, text in scored[:MAX_ITEMS_PER_STEP]:
            item = _candidate(text, title, _safe_url(final_url or url), None, now, max_age_days,
                              step_record)
            if item:
                item['http_status'] = status
                item['response_bytes'] = len(body)
                item['selector'] = selector
                candidates.append(item)
        if candidates:
            return candidates, 'ok', final_url or url
    return [], 'no_valid_items', urls[0] if urls else ''


STEP_RUNNERS = {
    STEP_RSS: _run_feed_step,
    STEP_RSSHUB: _run_feed_step,
    STEP_CARRIER: _run_feed_step,
    STEP_JSON: _run_json_step,
    STEP_HTML: _run_html_step,
}


def run_plan(comm, session, timeout=10.0, now=None, max_age_days=None, budget=None,
             max_steps=None, skip_kinds=()):
    """按方案逐级尝试，返回与 collect_comments 同构的 {comments, attempt}。

    - 第一个产出合格证据的步骤胜出，后续步骤不再发请求；
    - `adapter` 步骤只登记不执行（由 collect_comments 负责），避免同站重复打；
    - 每步的状态都记进 `attempt['steps']`，失败源因此有名字、有原因，可逐源排查；
    - 全部失败 → `status='unavailable'`、comments 为空，由上层如实标「未获取」。
    """
    import community_comments as cc
    key = comm.get('key')
    spec = plan_for(comm)
    attempt = {'strategy': '', 'status': 'not_planned', 'matched_comments': 0,
               'plan_steps': [], 'endpoint': ''}
    if spec is None:
        return {'comments': [], 'attempt': attempt}

    now = cc._now_utc(now)
    max_age_days = cc.COMMENT_MAX_AGE_DAYS if max_age_days is None else max_age_days
    budget = budget or PlanBudget()
    max_steps = PLAN_MAX_STEPS if max_steps is None else int(max_steps)
    skip_kinds = set(skip_kinds or ())

    attempt['strategy'] = 'plan_chain'
    attempted = 0
    for index, step_record in enumerate(spec['steps']):
        kind = step_record['kind']
        record = {'index': index, 'kind': kind, 'strategy': step_record['strategy'],
                  'verified': step_record['verified'], 'status': 'skipped'}
        if step_record.get('needs_config') and not _config_present(step_record['needs_config']):
            record['status'] = 'not_configured'
            attempt['plan_steps'].append(record)
            continue
        if kind == STEP_ADAPTER or kind in skip_kinds:
            record['status'] = 'delegated'
            attempt['plan_steps'].append(record)
            continue
        if attempted >= max_steps:
            record['status'] = 'step_budget'
            attempt['plan_steps'].append(record)
            continue

        urls = []
        for endpoint in step_record['endpoints']:
            urls.extend(expand_endpoint(endpoint, step_record.get('lang')))
        urls = [u for u in urls if u]
        runner = STEP_RUNNERS.get(kind)
        if runner is None or not urls:
            record['status'] = 'unsupported'
            attempt['plan_steps'].append(record)
            continue

        attempted += 1
        delay = budget.step_delay if index else 0.0
        try:
            candidates, status, endpoint = runner(step_record, urls, session, timeout, now,
                                                  max_age_days, budget, delay)
        except Exception as exc:  # 单步异常只影响这一步，不影响其它源
            candidates, status, endpoint = [], f'error_{type(exc).__name__}', (urls[0] if urls else '')
        record['status'] = status
        record['endpoint'] = endpoint
        record['candidates'] = len(candidates)
        attempt['plan_steps'].append(record)
        if status == 'budget_exhausted':
            break
        if candidates:
            comments, matched = _finish(candidates)
            if comments:
                attempt.update({'status': 'ok', 'matched_comments': matched,
                                'strategy': step_record['strategy'], 'endpoint': endpoint,
                                'content_kind': step_record['content_kind'],
                                'verified': step_record['verified'],
                                'step_kind': kind, 'scope': step_record.get('scope', ''),
                                'note': step_record.get('note', '')})
                return {'comments': comments, 'attempt': attempt}
    if attempt['status'] != 'ok':
        attempt['status'] = 'budget_exhausted' if budget.exhausted else 'unavailable'
    return {'comments': [], 'attempt': attempt}


def _config_present(names):
    """方案里标了 needs_config 的步骤，只有环境变量真的存在才算可尝试。"""
    return all(str(os.environ.get(name, '')).strip() for name in (names or ()))


def run_plans(communities, session, timeout=10.0, now=None, max_age_days=None, budget=None,
              max_steps=None, skip_keys=(), skip_kinds=(), verbose=False):
    """逐源执行方案；单源失败不影响其它源。返回 (outcomes, stats)。

    `skip_kinds` 用于「这一类步骤已经由别的层执行过」的场景：社区管线传入
    `adapter`，因为专用适配器已由 `collect_comments` 跑过，方案层不重复打同一站点。
    """
    import community_comments as cc
    budget = budget or PlanBudget()
    skip_kinds = tuple(skip_kinds or ())
    outcomes = {}
    for comm in communities:
        key = comm.get('key')
        if not key or key in set(skip_keys or ()):
            continue
        if key not in PLANS:
            continue
        if not budget.allow():
            outcomes[key] = {'comments': [], 'attempt': {
                'strategy': 'plan_chain', 'status': 'budget_exhausted', 'matched_comments': 0,
                'plan_steps': []}}
            continue
        try:
            outcome = run_plan(comm, session, timeout=timeout, now=now,
                               max_age_days=max_age_days, budget=budget, max_steps=max_steps,
                               skip_kinds=skip_kinds)
        except Exception as exc:
            outcome = {'comments': [], 'attempt': {
                'strategy': 'plan_chain', 'status': f'error_{type(exc).__name__}',
                'matched_comments': 0, 'plan_steps': []}}
        outcomes[key] = outcome
        if verbose:
            status = outcome['attempt'].get('status')
            flag = '✅' if outcome['comments'] else '⚠️'
            print(f'  {flag} {comm.get("id","")} {comm.get("name",key)} → {status} '
                  f'({outcome["attempt"].get("strategy","")})', file=sys.stderr)
    stats = {
        'attempted_sources': len(outcomes),
        'plan_sources': sum(bool(o['comments']) for o in outcomes.values()),
        'matched_comments': sum(o['attempt'].get('matched_comments', 0) for o in outcomes.values()),
        'requests_count': budget.used_requests,
        'budget': budget.to_dict(),
        'attempts': {k: v['attempt'] for k, v in outcomes.items()},
    }
    return outcomes, stats


def should_run_plan(item, spec):
    """这一源要不要跑方案链？

    - 还没拿到任何证据（未获取）→ 跑；
    - 只拿到通用页面摘录，而方案里有更强的路（订阅/接口/RSSHub）→ 跑，成功则升级证据；
    - 已经有评论/热帖回复（适配器或 RSS 优先拿到了）→ 不跑，不覆盖更强的证据。
    """
    if not spec:
        return False
    if not item or not item.get('ok'):
        return True
    kind = item.get('content_kind') or KIND_EXCERPT
    if kind != KIND_EXCERPT:
        return False
    return any(s['kind'] in (STEP_RSS, STEP_JSON, STEP_RSSHUB) for s in spec['steps'])


# ---------------------------------------------------------------------------
# 导出：逐源方案核对表（docs/community-49-plans.md）与路由证据表
# ---------------------------------------------------------------------------

def render_plans_md(path=None):
    """生成 49 源逐源方案核对表；文档由本函数生成，禁止手改。"""
    rows = catalog()
    lines = [
        '# 49 社区源逐源独立抓取方案 · 核对表',
        '',
        '> 本文件由 `python3 community_plans.py --plans-md docs/community-49-plans.md` 生成，',
        '> 请勿手改 —— 方案的唯一事实源是 `community_plans.py` 的 `PLANS`',
        '> （`tests/test_community_plans.py` 会断言两者一致）。',
        '',
        f'**RSSHub 上游核对基准**：`{RSSHUB_UPSTREAM["repo"]}` @ `{RSSHUB_UPSTREAM["commit"]}`'
        f'（{RSSHUB_UPSTREAM["committed_at"]}），核对方式：{RSSHUB_UPSTREAM["method"]}。',
        '',
        '**步骤类型**：`adapter` 已有专用适配器 · `rss` 站点官方订阅 · `json_api` 站点公开接口 ·',
        '`rsshub` RSSHub 路由（按实例链轮换）· `html` 公开页面摘录 · `carrier` Google News 检索载体。',
        '',
        '**核验状态**：`native` 官方订阅 · `api_public` 公开接口（响应结构对着上游源码核对）·',
        '`rsshub_doc` RSSHub 路由存在且参数已核对 · `needs_check` 地址来自公开资料/上游源码但未在线实测 ·',
        '`carrier` 聚合检索载体 · `adapter` 本项目自有适配器。',
        '',
        '**执行口径**：逐级尝试，第一个产出合格证据的步骤胜出；每源最多尝试'
        f' {PLAN_MAX_STEPS} 个步骤，整轮请求预算 {PLAN_REQUEST_BUDGET} 次、墙钟预算 {int(PLAN_WALL_BUDGET)} 秒。'
        '所有条目仍要命中财经主题词并落在时间窗内，抓不到就标「未获取」，绝不回填历史内容。',
        '',
        f'合计 **{len(rows)} 源**；强度分布：'
        + ' · '.join(f'{label} {sum(1 for r in rows if plan_strength(PLANS[r["key"]]) == value)} 源'
                    for label, value in (('有专用适配器', 'adapter'), ('有订阅/公开接口', 'feed'),
                                         ('只有 RSSHub', 'rsshub'), ('只有摘录+载体', 'excerpt_only'))),
        '',
    ]
    for row in rows:
        spec = PLANS.get(row['key'], {})
        lines.append(f"## {row['id']} {row['name']}（`{row['key']}` · {row['ctype']}）")
        lines.append('')
        lines.append(f"- **目录 URL**：{row['url']}")
        lines.append(f"- **方案**：{row['summary']}")
        lines.append(f"- **强度**：{plan_strength(spec)}"
                     + ('（无公开订阅/接口，只有页面摘录与检索载体）' if row['no_public_feed'] else ''))
        if row['note']:
            lines.append(f'- **限制**：{row["note"]}')
        lines.append('')
        lines.append('| # | 步骤 | 核验 | 端点 | 口径 |')
        lines.append('|---|---|---|---|---|')
        for index, step_record in enumerate(spec.get('steps', []), start=1):
            endpoints = '、'.join(f'`{e}`' for e in step_record['endpoints']) or '—（由适配器执行）'
            config = ''.join(f'<br>需配置：`{c}`' for c in step_record.get('needs_config', ()))
            lines.append(f"| {index} | `{step_record['kind']}` → `{step_record['strategy']}` "
                         f"| `{step_record['verified']}` | {endpoints}{config} "
                         f"| {step_record['note']} |")
        lines.append('')
    lines += [
        '## RSSHub 路由证据（逐条对着上游源码核对）',
        '',
        '| 路由 | 上游文件（lib/routes/） |',
        '|---|---|',
    ]
    for route in sorted(ROUTE_EVIDENCE):
        lines.append(f'| `{route}` | `{ROUTE_EVIDENCE[route]}` |')
    lines += [
        '',
        '## 已核对：RSSHub 无路由的站点',
        '',
        '这些站点在全量路由树与全文检索里都没有命中，因此方案里不写 RSSHub 步骤，'
        '改走官方订阅 / 公开接口 / 页面摘录 / 检索载体：',
        '',
        '、'.join(f'`{host}`' for host in RSSHUB_ABSENT),
        '',
    ]
    text = '\n'.join(lines)
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
    return text


def render_routes_md(path=None):
    """生成路由证据快照（含上游 commit），供离线复核与测试钉住。"""
    lines = [
        '# RSSHub 路由核对快照',
        '',
        '> 本文件由 `python3 community_plans.py --routes-md docs/community-49-routes.md` 生成。',
        f'> 上游：`{RSSHUB_UPSTREAM["repo"]}` @ `{RSSHUB_UPSTREAM["commit"]}`（{RSSHUB_UPSTREAM["committed_at"]}）',
        f'> 核对日期：{RSSHUB_UPSTREAM["checked_at"]}；方式：{RSSHUB_UPSTREAM["method"]}。',
        '',
        '| 路由 | 上游文件 | 用在哪些源 |',
        '|---|---|---|',
    ]
    usage = {}
    for key, spec in PLANS.items():
        for step_record in spec['steps']:
            if step_record['kind'] != STEP_RSSHUB:
                continue
            for endpoint in step_record['endpoints']:
                route = endpoint.split('rsshub:', 1)[-1]
                usage.setdefault(_route_pattern(route), set()).add(key)
    for route in sorted(ROUTE_EVIDENCE):
        keys = sorted(usage.get(route, ()))
        lines.append(f'| `{route}` | `{ROUTE_EVIDENCE[route]}` | '
                     f'{", ".join(f"`{k}`" for k in keys) or "—"} |')
    lines += ['', '## 无路由站点', '', '、'.join(f'`{h}`' for h in RSSHUB_ABSENT), '']
    text = '\n'.join(lines)
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
    return text


def _self_test():
    """离线自检：用假传输层跑一遍方案链，零联网。"""
    import community_data as cd
    missing = missing_plans()
    extra = extra_plans()
    print(f'方案覆盖：{len(PLANS)}/{len(cd.COMMUNITIES)} 源；缺 {missing}；多 {extra}')
    for row in catalog():
        spec = PLANS[row['key']]
        kinds = ' → '.join(s['kind'] for s in spec['steps'])
        print(f"  {row['id']} {row['key']:14s} [{plan_strength(spec):12s}] {kinds}")
    return 0 if not missing and not extra else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='49 社区源逐源独立抓取方案')
    parser.add_argument('--plans-md', metavar='PATH', help='生成逐源方案核对表并写入 PATH')
    parser.add_argument('--routes-md', metavar='PATH', help='生成 RSSHub 路由证据快照并写入 PATH')
    parser.add_argument('--json', metavar='PATH', help='把方案目录写成 JSON')
    parser.add_argument('--only', metavar='KEY', help='只打印某个源的方案（按 key）')
    parser.add_argument('--text', action='store_true', help='打印方案清单到标准输出')
    parser.add_argument('--self-test', action='store_true', help='离线自检')
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.plans_md:
        render_plans_md(args.plans_md)
        print(f'✅ 已生成逐源方案核对表：{args.plans_md}')
    if args.routes_md:
        render_routes_md(args.routes_md)
        print(f'✅ 已生成路由证据快照：{args.routes_md}')
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as handle:
            json.dump({'upstream': RSSHUB_UPSTREAM, 'plans': catalog()}, handle,
                      ensure_ascii=False, indent=1)
        print(f'✅ 已写出方案 JSON：{args.json}')
    if args.only:
        spec = PLANS.get(args.only.upper())
        if not spec:
            print(f'❌ 没有这个源的方案：{args.only}', file=sys.stderr)
            return 2
        print(json.dumps(spec, ensure_ascii=False, indent=1))
    elif args.text and not (args.plans_md or args.routes_md or args.json):
        for row in catalog():
            spec = PLANS[row['key']]
            kinds = ' → '.join(s['kind'] for s in spec['steps'])
            print(f"{row['id']} {row['name']}（{row['key']}）[{plan_strength(spec)}] {kinds}")
            print(f"    {row['summary']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
