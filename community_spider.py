#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 社区爬虫 (community_spider.py)

用 **Scrapling 框架模型**（见 `scrapling_core.py` 的移植说明）抓取社区模块的每一个源头：

  • Spider / CrawlerEngine   —— 一次跑完所有社区源，线程池并发、每域限速、封锁重试；
  • Scheduler + Request      —— URL 指纹去重、优先级（id 小的先抓，保证「头条源」先出结果）；
  • AutoThrottle             —— 按响应延迟自适应每域间隔，被封锁时按 Retry-After 退避；
  • RobotsTxtManager         —— 可选的 robots.txt 遵从（默认与上游一致：不开启，CI 更快）；
  • DevCache                 —— 开发模式缓存当次响应，离线/重复调试不再重复打站点；
  • CheckpointManager        —— 断点续爬（默认关闭；传 --crawl-dir 才开启）；
  • Selector + Adaptive      —— CSS 候选选择器 + 元素指纹（SQLite）自适应回捞：
                                站点改版后尝试回捞相似页面摘录，不保证是评论；
  • Convertor                —— 噪音/隐藏内容清洗 + HTML→Markdown，抽取干净的活数据片段。

抽取口径（与社区数据层同一套反陈旧口径）：
  1. 先按每个源的 CSS 候选规则取标题 / 正文块；
  2. 若规则失配，走自适应指纹回捞（同一 SQLite 里按社区 key 隔离）；
  3. 再失配，在整页里按「财经/港股关键词命中数 + 文本长度」打分，选最相关的一段作为现场佐证；
  4. 任何一步拿不到命中财经主题词的真实正文（含风控页 / 登录墙 / 空壳页），**绝不编内容**：
     直接标记 `ok=False`（`no_topic_snippet` 或 HTTP 状态码），交由 community_data.py 诚实标注「未获取」。

用法:
  python3 community_spider.py --self-test              # 离线自检（零联网）
  python3 community_spider.py --json spider_out.json   # 联网抓取全部社区源并产出 JSON
  python3 community_spider.py --limit 5 --timeout 8    # 只抓前 5 个源，便于联调
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import scrapling_core as sc

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORAGE = os.path.join(REPO_ROOT, '.scrapling', 'community_adaptive.db')
DEFAULT_CACHE_DIR = os.path.join(REPO_ROOT, '.scrapling', 'dev_cache')

# 与「社区研判」直接相关的财经/市场主题词（覆盖中英日韩德多语种）：
# 候选片段必须至少命中 1 个主题词才允许入库，杜绝把导航栏、登录提示或无关闲聊当成社区热评。
TOPIC_KEYWORDS = (
    # 港股与核心标的（简繁）
    '恒指', '恒生', '港股', '恒科', '国企指数', '南向', '北水', '港元', '港交所',
    '恒生指數', '港股通', '中概', 'adr', '腾讯', '阿里', '美团', '小米', '比亚迪',
    '恆指', '恆生', '比亞迪', '騰訊', '牛熊', '窩輪', '窝轮', '蓝筹', '紅籌', '红筹',
    '指數', '大盤', '股價', '台股', '權值股', '殖利率',
    '資金', '籌碼', '成交量', '投資', '買進', '賣出', '跌停', '漲停',
    # 中文宏观 / 交易 / 资产配置术语
    'A股', '沪深', '美股', '纳指', '标普', '道指', '股票', '股市', '指数', '大盘',
    '基金', 'etf', '可转债', '转债', '套利', '打新', '估值', '股息', '红利', '分红',
    '期权', '期货', '波动率', '利率', '降息', '加息', '通胀', '非农', '美债', '央行', '联储',
    '美元', '人民币', '离岸', '汇率', '外汇', '黄金', '原油', '油价', '大宗',
    '半导体', '芯片', '算力', '财报', '业绩', '回购', '净流入', '净买入', '做多', '做空',
    '加密货币', '比特币',
    # English market / macro / asset terms
    'hang seng', 'hsi', 'hstech', 'hong kong', 'hk stocks', 'china stocks', 'chinese equities',
    'stock', 'stocks', 'equity', 'equities', 'market', 'markets', 'index', 'shares',
    'fed', 'fomc', 'rate cut', 'rate hike', 'yield', 'treasury', 'bond', 'inflation', 'cpi', 'tariff',
    'options', 'volatility', 'vix', 'futures', 'hedging', 'spread', 'quant', 'alpha',
    'forex', 'fx', 'usd', 'cnh', 'cny', 'eur', 'gold', 'crude', 'oil',
    'crypto', 'bitcoin', 'btc', 'eth', 'dividend', 'earnings', 'valuation', 'portfolio', 'fund',
    # 日 / 韩 / 德语财经词
    '항셍', '홍콩', '증시', '주식', '지수', '반도체', '금리', '환율', '코스피',
    '香港株', 'ハンセン', '香港株式', '株価', '株式', '日経', '市場', '金利', '為替', '投資',
    'aktien', 'börse', 'boerse', 'dax', 'zinsen', 'markt', 'anleihen', 'rendite', 'fonds',
)

# 明显不是正文的片段（导航 / 登录 / 版权 / 应用推广 / 反爬与 JS 空壳拦截页）
NOISE_PATTERNS = re.compile(
    r'^(登录|注册|下载|客服|关于我们|版权|免责声明|广告|意见反馈|返回顶部|'
    r'log ?in|sign ?up|sign in|cookie|privacy policy|terms of service|'
    r'おすすめ|ログイン|会員登録|개인정보|로그인|이용약관)\b',
    re.I,
)
NOISE_SUBSTRINGS = (
    'enable javascript', 'javascript is not available', 'javascript is disabled',
    'please enable cookies', 'access denied', 'just a moment', 'cloudflare',
    'verify you are human', 'security check', 'are you a robot', 'captcha',
    '请开启javascript', '请启用javascript', '请登录后查看', '扫码登录',
    '安全验证', '验证码', '访问受限', '请求过于频繁',
)

# 通用抽取阶梯：先正文容器，再文章 / 帖子 / 评论块，最后整页兜底
GENERIC_CONTAINER_SELECTORS = (
    'main', 'article', '#content', '#main', '.content', '.main-content',
    '[role="main"]', '.post-list', '.thread-list', '.topic-list', '.comment-list',
    '#js_content', '.RichText', '.article-content',
)
GENERIC_BLOCK_SELECTORS = (
    'article h2', 'article h3', 'article p',
    'li .title', 'li .content', '.post-title', '.thread-title', '.topic-title',
    'h1', 'h2', 'h3', 'p',
)

# 每个源的**首选**规则（命中即用；失配则自动降级到上面两条通用阶梯 + 自适应回捞）
SITE_RULES = {
    'FUTU': ('h1', '.article-title', '.news-title'),
    'XUEQIU': ('.timeline__item .timeline__item__main', '.status-content', 'article'),
    'LAOHU': ('.post-title', 'article h2', 'h1'),
    'EASTMONEY': ('.article-title', '.zwcontentmain', '.newstext'),
    'ZHITONG': ('.artlist-item', '.content-title', 'h1'),
    'WALLSTREETCN': ('.article__content', '.content-title', 'h1'),
    'DISCUSS': ('.post-title', '.thread-title', 'h1'),
    'LIHKG': ('.thread-title', '.topic-title', 'article'),
    'JIUQUAN': ('.post-title', 'article h2', 'h1'),
    'ANTFORTUNE': ('.fund-title', '.post-title', 'h1'),
    'REDDIT': ('shreddit-post [slot="title"]', 'h1', 'a[slot="title"]'),
    'TRADINGVIEW': ('h1', '.tv-symbol-header__title', '.card-title'),
    'VIC': ('.idea-title', 'article h2', 'h1'),
    'FINTWIT': ('article', 'h2', 'p'),
    'ZHIHU': ('.QuestionHeader-title', '.RichText', 'h1'),
    'WEIBO': ('.card-wrap .content', '.txt', 'h1'),
    'TIEBA': ('.threadlist_title', '.core_title_txt', 'h1'),
    'TAOGUBA': ('.post-title', '.article-title', 'h1'),
    'THS': ('.post-title', '.news-title', 'h1'),
    'GELONGHUI': ('.article-title', '.content-title', 'h1'),
    'CLS': ('.telegraph-content', '.b-c-e6e7e9', 'h1'),
    'YICAI': ('.m-txt', '.article-content', 'h1'),
    'BILIBILI': ('.video-title', '.title', 'h1'),
    'PTT': ('.r-ent .title', '#main-content h1', 'h1'),
    'STOCKTWITS': ('.message-body', '.sentiment', 'h1'),
    'SEEKINGALPHA': ('article h3', '.market-news-item', 'h1'),
    'BOGLEHEADS': ('.topictitle', '.content', 'h1'),
    'RINVESTING': ('shreddit-post [slot="title"]', 'h1', 'a[slot="title"]'),
    'WSO': ('.title', '.topic-title', 'h1'),
    'INVESTING': ('.comment-body', '.article-title', 'h1'),
    'YAHOO': ('.comment-body', 'h1', 'article p'),
    'SUBSTACK': ('.post-title', 'article h2', 'h1'),
    'ROPTIONS': ('shreddit-post [slot="title"]', 'h1', 'a[slot="title"]'),
    'FTALPHA': ('.o-teaser__heading', 'article h2', 'h1'),
    'JISILU': ('.post-title', '.topic-title', 'h1'),
    'XIAOHONGSHU': ('.title', '.note-title', 'h1'),
    'DOUYIN': ('.video-title', '.title', 'h1'),
    'KAIPANLA': ('.title', '.topic-title', 'h1'),
    'LIXIANG': ('.thread-title', '.post-title', 'h1'),
    'QUANTNET': ('.topic-title', '.title', 'h1'),
    'ELITETRADER': ('.topic-title', '.post-title', 'h1'),
    'FOREXFACTORY': ('.thread-title', '.calendar__event-title', 'h1'),
    'RCRYPTO': ('shreddit-post [slot="title"]', 'h1', 'a[slot="title"]'),
    'MORNINGSTAR': ('.topic-title', 'article h2', 'h1'),
    'MARKETWATCH': ('.article__headline', 'article h3', 'h1'),
    'YAHOOJP': ('.title', '.news-title', 'h1'),
    'NAVER': ('.title', '.articleTitle', 'h1'),
    'WALLSTREETDE': ('.thread-title', '.post-title', 'h1'),
    'AASTOCKS': ('.news-title', '.title', 'h1'),
}

TITLE_SELECTORS = ('title', 'meta[property="og:title"]', 'meta[name="twitter:title"]', 'h1')
DESCRIPTION_SELECTORS = ('meta[name="description"]', 'meta[property="og:description"]')

MAX_SNIPPET = 300          # 单个源抽取的现场片段上限（与社区数据层的字符预算一致）


def real_scrapling_transport():
    """把 GitHub 上真实的 `scrapling` 包接成传输层（装了就优先用它做 TLS/指纹层）。

    上游包用 curl_cffi 做浏览器 TLS 指纹，比 urllib 更抗风控；未安装（CI 默认）时
    返回 None，框架自动落到 scrapling_core 的等价移植层 —— 两边 API 与抓取口径一致，
    只是底层传输不同，因此上层解析 / 自适应 / 限速逻辑完全不用改。
    """
    try:
        from scrapling.fetchers import Fetcher  # type: ignore
    except Exception:  # noqa: BLE001 - 未安装或依赖缺失都视为不可用
        return None

    def transport(method, url, headers, timeout, proxy=None, body=None):
        # Redirects are handled by FetcherSession, which can disable them for
        # comment-detail requests. Never let the optional backend bypass that.
        kwargs = {'timeout': max(int(timeout), 1), 'retries': 1, 'follow_redirects': False}
        if proxy:
            kwargs['proxy'] = proxy
        if headers:
            kwargs['headers'] = headers
        response = Fetcher.get(url, **kwargs)
        return (response.status, getattr(response, 'reason', '') or '',
                dict(getattr(response, 'headers', {}) or {}), response.body, url)
    return transport


def resolve_backend(backend: str = 'auto'):
    """返回 (后端名, transport 或 None)：'scrapling' = 真实包，'port' = 标准库移植层。"""
    if backend == 'port':
        return 'port', None
    transport = real_scrapling_transport()
    if transport is not None:
        return 'scrapling', transport
    if backend == 'scrapling':
        print('  ⚠️ 未安装 scrapling 包，回退到标准库移植层（scrapling_core）', file=sys.stderr)
    return 'port', None


def strip_noise(text: str) -> str:
    """清洗文本：去连续空白、去零宽字符、截断到片段预算。"""
    text = sc.Convertor.ZERO_WIDTH.sub('', str(text or ''))
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def count_topic_hits(text: str) -> int:
    """统计候选文本命中的财经/市场主题词个数（大小写不敏感）。"""
    lowered = strip_noise(text).lower()
    if not lowered:
        return 0
    return sum(1 for keyword in TOPIC_KEYWORDS if keyword.lower() in lowered)


def score_fragment(text: str, title: str = '') -> float:
    """给一段候选文本打分：必须命中至少 1 个财经主题词且不含反爬/登录噪音，否则返回负分淘汰。"""
    text = strip_noise(text)
    if len(text) < 6 or NOISE_PATTERNS.match(text):
        return -1.0
    lowered = text.lower()
    if any(bad in lowered for bad in NOISE_SUBSTRINGS):
        return -1.0
    hits = sum(1 for keyword in TOPIC_KEYWORDS if keyword.lower() in lowered)
    if hits <= 0:
        return -0.5
    score = hits * 3.0
    score += min(len(text), MAX_SNIPPET) / 100.0
    if title:
        title_tokens = {token for token in re.split(r'[\s，。,:：、/|]+', title) if len(token) >= 4}
        score += sum(1.5 for token in title_tokens if token in text)
    if len(text) < 40:
        score -= 1.0
    return score


def extract_fragments(response, selectors) -> list:
    """按 CSS 候选规则收集文本块（对应 Scrapling 的 Selector.css + get_all_text）。"""
    fragments = []
    for selector in selectors:
        try:
            found = response.css(selector)
        except sc.SelectorError:
            continue
        for element in found:
            text = strip_noise(element.get_all_text(separator=' ', strip=True))
            if text:
                fragments.append((selector, text))
    return fragments


class KeyedAdaptiveStorage:
    """同一 SQLite 文件里按「社区 key」隔离元素指纹（对应上游 storage 的 url 隔离口径）。"""

    def __init__(self, storage_file: str):
        self.storage_file = storage_file
        self._stores = {}

    def for_key(self, key: str) -> sc.AdaptiveStorage:
        if key not in self._stores:
            self._stores[key] = sc.AdaptiveStorage(self.storage_file, url=key)
        return self._stores[key]

    def close(self) -> None:
        for store in self._stores.values():
            store.close()
        self._stores.clear()


class CommunitySpider(sc.Spider):
    """抓取社区模块的全部源头（对应上游 scrapling.spiders.Spider 的落地用法）。"""

    name = 'community'
    concurrent_requests = 6
    concurrent_requests_per_domain = 1     # 同一站点不并发，避免被风控
    download_delay = 0.15
    request_timeout = 10.0
    request_retries = 2
    max_blocked_retries = 2
    autothrottle_enabled = True
    autothrottle_start_delay = 0.5
    autothrottle_max_delay = 8.0
    robots_txt_obey = False                 # 与上游默认一致；--robots 可开
    fp_include_kwargs = False
    fp_keep_fragments = False

    def __init__(self, communities, storage_file: str = DEFAULT_STORAGE, adaptive: bool = True,
                 crawldir=None, interval: float = 300.0, session=None, verbose: bool = True):
        self.communities = list(communities)
        self.start_urls = [c['url'] for c in self.communities]
        self.adaptive = adaptive
        self.verbose = verbose
        self.pool = KeyedAdaptiveStorage(storage_file) if adaptive else None
        self.results = {}
        super().__init__(crawldir=crawldir, interval=interval, session=session)

    # ---- 请求构造：优先级按 id 顺序，把每个源的元信息挂进 meta ----
    def start_requests(self):
        for community in self.communities:
            yield sc.Request(community['url'], priority=-int(community.get('id', '00') or 0),
                             meta={'key': community.get('key'), 'id': community.get('id'),
                                   'name': community.get('name'), 'ctype': community.get('ctype'),
                                   'url': community['url']})

    # ---- 解析：标题 → 候选规则 → 自适应回捞 → 关键词打分兜底 ----
    def parse(self, response):
        meta = response.request.meta if response.request else {}
        key = meta.get('key') or ''
        name = meta.get('name') or key
        rule = SITE_RULES.get(key, ())
        title = ''
        for selector in TITLE_SELECTORS:
            found = response.css(selector)
            if found:
                candidate = strip_noise(found.first.get_all_text(separator=' ', strip=True))
                if candidate:
                    title = candidate[:120]
                    break
        if not title:
            for selector in DESCRIPTION_SELECTORS:
                found = response.css(selector)
                if found:
                    title = strip_noise(found.first.attrib.get('content') or '')[:120]
                    if title:
                        break

        adaptive_hit = False
        used_selector = ''
        fragments = extract_fragments(response, rule) or extract_fragments(
            response, GENERIC_CONTAINER_SELECTORS) or extract_fragments(response, GENERIC_BLOCK_SELECTORS)
        if not fragments and self.pool is not None:
            # 规则全失配 → 用元素指纹回捞（站点改版场景）
            best_key = f'{key}:snippet'
            store = self.pool.for_key(key)
            original = store.retrieve(best_key)
            if original is not None:
                best, best_score = None, 0.0
                for node in response._root.iter():
                    if node is response._root or node.comment:
                        continue
                    similarity = sc.element_similarity(original, sc.element_to_dict(node))
                    if similarity > best_score:
                        best, best_score = node, similarity
                if best is not None and best_score >= 40:
                    text = strip_noise(sc.Selector(root=best).get_all_text(separator=' ', strip=True))
                    if text:
                        fragments = [(f'adaptive:{round(best_score, 1)}', text)]
                        adaptive_hit = True

        best_text, best_score = '', 0.0
        for selector, text in fragments:
            score = score_fragment(text, title)
            if score > best_score:
                best_text, best_score, used_selector = text, score, selector
        if not best_text:
            whole = strip_noise(response.get_all_text(separator=' ', strip=True))
            for chunk in re.split(r'(?<=[。！？!?])', whole):
                score = score_fragment(chunk, title)
                if score > best_score:
                    best_text, best_score, used_selector = strip_noise(chunk), score, 'body:best-chunk'
        snippet = best_text[:MAX_SNIPPET]
        topic_hits = count_topic_hits(snippet) if snippet else 0
        valid_ok = bool(response.ok and snippet and topic_hits > 0)
        err_reason = ''
        if not response.ok:
            err_reason = f'HTTP {response.status}'
        elif not valid_ok:
            err_reason = 'no_topic_snippet'

        # 抓取成功即更新指纹：下次改版能靠这一次的形态回捞（对应 upstream auto_save 口径）
        if self.pool is not None and valid_ok:
            try:
                node = response._select(used_selector).first if used_selector and not used_selector.startswith(
                    ('adaptive:', 'body:')) else None
                if node is not None and node._root is not None:
                    self.pool.for_key(key).save(node._root, f'{key}:snippet')
            except Exception:  # noqa: BLE001 - 指纹写入失败不影响抓取
                pass

        self.results[key] = {
            'key': key, 'id': meta.get('id', ''), 'name': name, 'ctype': meta.get('ctype', ''),
            'url': response.url, 'status': response.status, 'http_ok': bool(response.ok),
            'ok': valid_ok, 'topic_hits': topic_hits,
            'title': title, 'snippet': snippet if valid_ok else '', 'selector': used_selector if valid_ok else '',
            'adaptive': adaptive_hit and valid_ok, 'bytes': len(response.body or b''),
            'content_kind': 'page_excerpt', 'strategy': 'scrapling_page',
            'error': err_reason,
        }
        yield self.results[key]

    def on_error_hook(self, request, error):  # pragma: no cover - 兼容写法
        pass

    def on_error(self, request, error):
        meta = request.meta or {}
        key = meta.get('key') or ''
        self.results[key] = {
            'key': key, 'id': meta.get('id', ''), 'name': meta.get('name', key),
            'ctype': meta.get('ctype', ''), 'url': request.url, 'status': getattr(error, 'status', 0) or 0, 'ok': False,
            'title': '', 'snippet': '', 'selector': '', 'adaptive': False, 'bytes': 0,
            'error': str(error)[:200],
        }
        if self.verbose:
            print(f'  ⚠️ {meta.get("name") or key: <16} {type(error).__name__}: {str(error)[:120]}',
                  file=sys.stderr)

    def on_blocked(self, request, response):  # pragma: no cover - 观察用
        if self.verbose:
            print(f'  🚧 {request.meta.get("name") or request.url} 被站点阻断（HTTP {response.status}）',
                  file=sys.stderr)

    def on_close(self):
        if self.pool is not None:
            self.pool.close()


def load_communities(limit: int = 0):
    """取社区目录；避免与 community_data.py 形成导入环，这里函数内导入。"""
    import community_data as cd  # 局部导入：community_data 依赖本模块做抓取
    communities = list(cd.COMMUNITIES)
    return communities[:limit] if limit else communities


def fetch_live_snippets(communities=None, limit: int = 0, timeout: float = 10.0, retries: int = 2,
                        concurrency: int = 6, adaptive: bool = True, storage_file: str = DEFAULT_STORAGE,
                        obey_robots: bool = False, crawldir: str = None, session=None,
                        verbose: bool = True, backend: str = 'auto',
                        autothrottle: bool = True, max_blocked_retries: int = 2,
                        download_delay: float = 0.15, now=None, comment_strategies: bool = True,
                        post_limiter=None):
    """跑一轮社区抓取：返回 (结果字典, 统计字典)。

    结果字典形如 {key: {'snippet': ..., 'status': ..., 'adaptive': ..., 'selector': ...}}；
    单源失败不会抛异常，只是该源没有 live 片段（由社区数据层诚实标注未获取）。
    """
    communities = list(communities) if communities else load_communities(limit)
    backend_name, transport = ('injected', None) if session is not None else resolve_backend(backend)
    if session is None:
        # Share one transport with the comment adapters so injected sessions,
        # timeouts and the selected backend apply to every HTTP request.
        session = sc.FetcherSession(transport=transport, timeout=timeout, retries=retries,
                                    retry_delay=0.5, respect_retry_after=True)
    # RSS 优先：有公开 RSS 地址的源先尝试订阅源，成功则跳过网页/APP 公开信息流；
    # 不可用或无 RSS 的源留在 spider_communities 中，由下方的网页爬虫与评论策略处理。
    rss_outcomes = {}
    rss_ok_keys = set()
    if comment_strategies:
        import community_comments as comment_mod
        for community in communities:
            if not comment_mod._rss_feed_url(community):
                continue
            outcome = comment_mod.fetch_rss_feed(
                community, session=session, timeout=timeout, now=now,
                post_limiter=post_limiter)
            rss_outcomes[community.get('key')] = outcome
            if outcome['attempt'].get('status') == 'ok' and outcome['comments']:
                rss_ok_keys.add(community.get('key'))
    spider_communities = [c for c in communities if c.get('key') not in rss_ok_keys]

    spider = CommunitySpider(spider_communities, storage_file=storage_file, adaptive=adaptive,
                             crawldir=crawldir, session=session, verbose=verbose)
    spider.name = 'community'
    spider.request_timeout = timeout
    spider.request_retries = retries
    spider.max_blocked_retries = max_blocked_retries
    spider.autothrottle_enabled = autothrottle
    spider.concurrent_requests = max(1, concurrency)
    spider.robots_txt_obey = obey_robots
    spider.download_delay = download_delay
    # 抓取源列表可能少于并发数，按实际规模收敛并发，避免空转线程
    spider.concurrent_requests = min(spider.concurrent_requests, max(1, len(communities)))
    started = time.time()
    result = spider.crawl()
    # 逐源补全：任何没有产出记录的源（robots 跳过、被封锁且重试耗尽等）也要有一条失败记录，
    # 保证上层拿到的源数与目录一致、逐条可核对，绝不静默丢源。
    for community in communities:
        key = community.get('key')
        if key and key not in spider.results:
            if key in rss_ok_keys:
                continue
            spider.results[key] = {
                'key': key, 'id': community.get('id', ''), 'name': community.get('name', key),
                'ctype': community.get('ctype', ''), 'url': community.get('url', ''), 'status': 0,
                'ok': False, 'title': '', 'snippet': '', 'selector': '', 'adaptive': False,
                'bytes': 0, 'error': 'no_response',
            }
    # Prefer actual, attributable comments over the generic page excerpt. A
    # failed adapter never suppresses an otherwise valid base-page result.
    comment_stats = {'attempted_sources': 0, 'comment_sources': 0, 'matched_comments': 0,
                     'requests_count': 0, 'attempts': {}}
    if comment_strategies:
        try:
            import community_comments as comment_mod
            outcomes, comment_stats = comment_mod.collect_comments(
                spider_communities, session=session, timeout=timeout, retries=retries,
                concurrency=concurrency, obey_robots=obey_robots, autothrottle=autothrottle,
                max_blocked_retries=max_blocked_retries, download_delay=download_delay, now=now,
                post_limiter=post_limiter)
            for key, outcome in outcomes.items():
                if key in rss_ok_keys:
                    continue
                item = spider.results.get(key)
                if item is None:
                    continue
                item['comment_attempt'] = outcome['attempt']
                comments = outcome['comments']
                if not comments:
                    continue
                first = comments[0]
                item.update({
                    'ok': True, 'http_ok': True,
                    # PRAW does not expose HTTP status or response bytes; do not reuse page metadata.
                    'status': first.get('http_status', 0), 'bytes': first.get('response_bytes'),
                    'url': first['url'], 'title': first['post_title'], 'snippet': first['text'],
                    'topic_hits': count_topic_hits(first['post_title'] + ' ' + first['text']),
                    'selector': first['strategy'], 'adaptive': False, 'error': '',
                    'content_kind': first['kind'], 'strategy': first['strategy'],
                    'comments': comments,
                    'matched_comments': outcome['attempt']['matched_comments'],
                })
        except Exception as exc:  # an optional adapter must not interrupt the report
            comment_stats['error'] = type(exc).__name__
            if verbose:
                print(f'  ⚠️ 评论采集适配器失败（{type(exc).__name__}），保留公开页摘录', file=sys.stderr)

    # 把 RSS 成功的源写入结果，优先于网页/APP 公开信息流；这些源未经过 CommunitySpider。
    comm_by_key = {c.get('key'): c for c in communities}
    for key in rss_ok_keys:
        outcome = rss_outcomes[key]
        first = outcome['comments'][0] if outcome['comments'] else {}
        comm = comm_by_key.get(key, {})
        spider.results[key] = {
            'key': key, 'id': comm.get('id', ''), 'name': comm.get('name', key),
            'ctype': comm.get('ctype', ''), 'url': first.get('url', ''),
            'status': first.get('http_status', 200), 'http_ok': True, 'ok': True,
            'topic_hits': count_topic_hits(first.get('post_title', '') + ' ' + first.get('text', '')),
            'title': first.get('post_title', ''), 'snippet': first.get('text', ''),
            'selector': '', 'adaptive': False, 'bytes': first.get('response_bytes', 0),
            'error': '', 'content_kind': 'thread_reply', 'strategy': 'rss_feed',
            'comments': outcome['comments'],
            'matched_comments': outcome['attempt']['matched_comments'],
            'comment_attempt': outcome['attempt'],
        }

    stats = result.stats.to_dict()
    for field in ('requests_count', 'blocked_requests_count', 'robots_disallowed_count',
                  'failed_requests_count', 'response_bytes'):
        stats[field] = stats.get(field, 0) + comment_stats.get(field, 0)
    for status, count in comment_stats.get('response_status_count', {}).items():
        status_counts = stats.setdefault('response_status_count', {})
        status_counts[status] = status_counts.get(status, 0) + count
    elapsed = max(time.time() - started, 0.0)
    stats['elapsed_seconds'] = round(elapsed, 2)
    stats['requests_per_second'] = round(stats['requests_count'] / elapsed, 2) if elapsed else 0
    stats['comment_fetch'] = comment_stats
    stats.update({
        'engine': sc.ENGINE_NAME,
        'backend': backend_name,
        'sources': len(communities),
        'ok': sum(1 for item in spider.results.values() if item.get('ok')),
        'failed': [item['key'] for item in spider.results.values() if not item.get('ok')],
        'adaptive_hits': sum(1 for item in spider.results.values() if item.get('adaptive')),
        'elapsed': round(time.time() - started, 2),
        'robots_obey': obey_robots,
    })
    return spider.results, stats


def _self_test() -> int:
    """离线自检：用假传输层跑完整个 Spider，零联网。"""
    ok = True

    def check(condition, message):
        nonlocal ok
        print(('  ✅ ' if condition else '  ❌ ') + message)
        ok = ok and bool(condition)

    page = """<html><head><title>港股社区 · 恒指 26,000</title>
    <meta name="description" content="恒指今日收报 26,012 点，南向资金净买入 62 亿"></head>
    <body><main>
      <article class="post"><h3 class="title">恒指 26,000 关口压力重重，球友分歧加大</h3>
      <p>南向资金今日净买入 62 亿，港股科技与红利板块轮动。</p></article>
      <div class="copyright">版权声明</div>
    </main><script>var x=1;</script></body></html>"""

    def transport(method, url, headers, timeout, proxy=None, body=None):
        if 'blocked' in url:
            return 429, 'Too Many Requests', {'Retry-After': '1'}, b'blocked', url
        return 200, 'OK', {'Content-Type': 'text/html; charset=utf-8'}, page.encode('utf-8'), url

    session = sc.FetcherSession(transport=transport, retries=1)
    communities = [
        {'id': '01', 'key': 'FUTU', 'name': '富途牛牛社区', 'ctype': '中文行情社区',
         'url': 'https://example.com/forum'},
        {'id': '02', 'key': 'XUEQIU', 'name': '雪球网', 'ctype': '中文投资者社区',
         'url': 'https://blocked.example.com/forum'},
    ]
    storage_file = os.path.join(REPO_ROOT, '.scrapling_test', 'spider.db')
    results, stats = fetch_live_snippets(communities, storage_file=storage_file, session=session,
                                         adaptive=True, verbose=False)
    check(results['FUTU']['ok'] and '恒指' in results['FUTU']['snippet'], 'Spider: 抽取现场片段')
    check(results['FUTU']['title'].startswith('港股社区'), 'Spider: 标题抽取（title 标签）')
    check(stats['sources'] == 2 and stats['engine'].startswith('scrapling-core'), 'Spider: 统计口径')
    check(stats['backend'] == 'injected', 'Spider: 注入会话时标记传输层为 injected')
    check(not results['XUEQIU']['ok'], 'Spider: 被封锁源不抛出、降级为失败项')
    check(score_fragment('版权声明') < 0, '打分器会淘汰导航/版权噪音')
    check(score_fragment('Enable JavaScript and cookies to continue') < 0, '打分器会淘汰 JS/风控空壳文案')
    check(score_fragment('今天的天气不错，适合出去走走看看风景') < 0, '打分器拒绝零财经主题词的无关片段')
    check(score_fragment('恒指今日收报 26,012 点，南向净买入 62 亿') > 5, '打分器偏好主题相关文本')
    print()
    print('✅ community_spider 自检全部通过' if ok else '❌ community_spider 自检存在失败项')
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description='章鱼 AI · 社区爬虫（Scrapling 框架模型）')
    parser.add_argument('--json', default='', help='把抓取结果写入 JSON')
    parser.add_argument('--limit', type=int, default=0, help='只抓前 N 个源（联调用）')
    parser.add_argument('--timeout', type=float, default=10.0, help='单请求超时秒数')
    parser.add_argument('--retries', type=int, default=2, help='单源重试次数')
    parser.add_argument('--concurrency', type=int, default=6, help='并发请求数')
    parser.add_argument('--no-adaptive', action='store_true', help='关闭自适应指纹回捞')
    parser.add_argument('--adapt-db', default=DEFAULT_STORAGE, help='自适应指纹库路径')
    parser.add_argument('--robots', action='store_true', help='遵从 robots.txt')
    parser.add_argument('--crawl-dir', default='', help='开启断点续爬（保存 checkpoint 的目录）')
    parser.add_argument('--backend', default='auto', choices=('auto', 'port', 'scrapling'),
                        help='传输层：auto=装了 scrapling 包就用它，否则用标准库移植层')
    parser.add_argument('--no-comments', action='store_true', help='只抓公开页摘录，关闭多方案评论采样')
    parser.add_argument('--self-test', action='store_true', help='离线自检（零联网）')
    args = parser.parse_args()

    if args.self_test:
        sys.exit(_self_test())

    results, stats = fetch_live_snippets(limit=args.limit, timeout=args.timeout, retries=args.retries,
                                         concurrency=args.concurrency, adaptive=not args.no_adaptive,
                                         storage_file=args.adapt_db, obey_robots=args.robots,
                                         crawldir=args.crawl_dir or None, backend=args.backend,
                                         comment_strategies=not args.no_comments)
    for key, item in results.items():
        flag = '✅' if item.get('ok') else '⚠️'
        status = 'API' if item.get('strategy') == 'reddit_praw' and item.get('ok') else item.get('status', 0)
        print(f"  {flag} {item.get('name') or key: <18} {status: >4} "
              f"{(item.get('selector') or '—')[:28]: <28} {item.get('snippet', '')[:50]}")
    print(f"📦 社区抓取：{stats['ok']}/{stats['sources']} 成功 · 自适应回捞 {stats['adaptive_hits']} 次 · "
          f"耗时 {stats['elapsed']}s · 引擎 {stats['engine']} · 传输层 {stats['backend']}")
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as handle:
            json.dump({'stats': stats, 'results': results}, handle, ensure_ascii=False, indent=2)
        print(f'📝 抓取结果已写入 {args.json}')


if __name__ == '__main__':
    main()
