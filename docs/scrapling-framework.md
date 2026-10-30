# Scrapling 框架模型分析 + 本项目接入说明

> **当前口径更新**：下文主要记录早期框架分析。评论采样现已组合 PTT、LIHKG 和可选 PRAW；
> 普通 Scrapling 页面摘录不能证明是评论或热评，失败时标「未获取」，不再使用动态模板兜底。
> 以 [多方案社区评论抓取](community-comment-strategies.md) 为准。

> 上游项目：[D4Vinci/Scrapling](https://github.com/D4Vinci/Scrapling)（BSD-3-Clause，本文分析基于 **v0.4.15**，
> 即 PyPI `scrapling==0.4.15` / `scrapling[fetchers]`）。
> 本文只做两件事：**① 拆解它的抓取框架模型；② 说明本项目怎么把它接进来，以及为什么这么接。**

## 0. 一页速览

| 问题 | 结论 |
|---|---|
| Scrapling 是什么 | 一个「反检测 + 高并发 + 自适应解析」的 Python 抓取框架：静态引擎（curl_cffi 浏览器 TLS 指纹）、浏览器引擎（Playwright/Patchright）、解析层（lxml + 自适应元素指纹）、Spider 框架（调度 / 限速 / robots / 断点 / 开发缓存）|
| 本项目为什么要它 | 社区模块从 34 源扩到 49 源后，原来的「urllib 逐个 GET + 正则去标签」在**并发、限速、封禁识别、站点改版**四件事上都不够用 |
| 怎么接的 | 把它的**框架模型**移植成零依赖实现 `scrapling_core.py`（类名/方法名与上游一一对应），由 `community_spider.py` 落到 49 个社区源；**装了真 `scrapling` 包时自动把它接成传输层**（curl_cffi TLS 指纹） |
| 为什么不全量依赖上游 | 默认构建走标准库，不要求安装 Scrapling；仅配置 Reddit API 凭据时 CI 可选安装 PRAW |
| 效果 | 原有公开页抓取并发、限速与指纹回捞；不能保证回捞的是评论。失败时标记「未获取」，不编造模板 |

## 1. 上游代码地图（框架层）

```
scrapling/
├── fetchers/                 # 门面：Fetcher / AsyncFetcher / DynamicFetcher / StealthyFetcher + 各 Session
├── engines/
│   ├── static.py             # 静态引擎：请求头指纹、重试、重定向、代理轮换、Retry-After
│   ├── _browsers/            # 浏览器引擎：Playwright / Patchright（反检测、stealth 脚本）
│   └── toolbelt/
│       ├── custom.py         # Response（继承 Selector）+ BaseFetcher 配置
│       ├── convertor.py      # 各引擎响应 → 统一 Response
│       ├── fingerprints.py   # generate_headers：browserforge 生成真实浏览器头
│       ├── proxy_rotation.py # ProxyRotator
│       └── ad_domains.py     # 广告/追踪域名表
├── parser.py                 # Selector / Selectors：css / xpath / find_all / 自适应 relocation
├── core/
│   ├── storage.py            # SQLiteStorageSystem：自适应元素指纹落库
│   ├── utils/_utils.py       # _StorageTools.element_to_dict：指纹字段定义
│   ├── custom_types.py       # TextHandler / TextHandlers / AttributesHandler
│   ├── mixins.py             # SelectorsGeneration：生成 CSS/XPath 选择器
│   └── shell.py              # Convertor：噪音清洗、防提示注入、Markdown/文本抽取
└── spiders/                  # 爬虫框架（本文重点）
    ├── spider.py             # Spider 基类 + 生命周期钩子
    ├── engine.py             # CrawlerEngine：调度主循环、封锁重试、统计
    ├── scheduler.py          # 优先级队列 + URL 指纹去重
    ├── request.py            # Request：url / priority / dont_filter / 指纹
    ├── throttle.py           # AutoThrottle + parse_retry_after
    ├── robotstxt.py          # RobotsTxtManager（protego 解析）
    ├── cache.py              # DevCache：开发模式响应缓存
    ├── checkpoint.py         # CheckpointManager：断点续爬
    ├── session.py            # SessionManager：多会话（静态/动态/stealth）
    └── result.py             # CrawlStats / CrawlResult / ItemList
```

## 2. 关键模型逐个拆

### 2.1 静态引擎：请求头 + 重试 + 封锁识别

```python
# scrapling/engines/static.py（节选）
self._default_retries = kwargs.get("retries", 3)
self._default_retry_delay = kwargs.get("retry_delay", 1)
...
max_retries = max(1, self._get_param(kwargs, "retries", self._default_retries) or 1)
for attempt in range(max_retries):
    ...
    if attempt < max_retries - 1:
        log.error(...)
```

模型要点：
1. **请求头指纹**（`toolbelt/fingerprints.py`）—— 用 browserforge 生成与真实浏览器一致的
   `User-Agent` / `sec-ch-ua*` / `Accept*` 组合，而不是一个写死的 UA 常量；
2. **`retries` 语义**是「最多发几次」，小于 1 也至少发一次；
3. **`stealthy_headers`** 可按请求覆盖（`stealth=False` 时退化为裸请求）。

### 2.2 解析层：Selector + 自适应（这是 Scrapling 最有价值的部分）

```python
# scrapling/core/utils/_utils.py
def element_to_dict(cls, element) -> Dict:
    return {"tag": ..., "attributes": ..., "text": ..., "path": ...,
            "parent_name": ..., "parent_attribs": ..., "parent_text": ...,
            "siblings": (...), "children": (...)}
```

```python
# scrapling/parser.py（节选）
score += 1 if original["tag"] == data["tag"] else 0
if original["text"]:
    score += SequenceMatcher(None, original["text"], data.get("text") or "").ratio()
score += self.__calculate_dict_diff(original["attributes"], data["attributes"])
for attrib in ("class", "id", "href", "src"):
    ...
score += SequenceMatcher(None, original["path"], data["path"]).ratio()
...
return round((score / checks) * 100, 2)
```

模型要点：
* 元素身份**不是**一串 CSS 路径，而是「标签 + 全部属性 + 文本 + 祖先路径 + 父节点特征 + 兄弟/子节点标签」的向量；
* 站点改版后，把候选元素逐个算相似度，取最高的一个，`>= percentage` 才认；
* 指纹存 SQLite（`storage.py`，`UNIQUE(url, identifier)` + WAL），因此可以跨进程/跨次抓取复用；
* 对抓取方而言，这是「**选择器会过期，但元素语义不会过期**」的工程化答案。

### 2.3 爬虫框架：Spider / Engine / Scheduler / Throttle / Robots / Checkpoint

```python
# scrapling/spiders/spider.py（节选）
class Spider(ABC):
    name: Optional[str] = None
    start_urls: list[str] = []
    allowed_domains: Set[str] = set()
    robots_txt_obey: bool = False
    development_mode: bool = False
    concurrent_requests: int = 4
    concurrent_requests_per_domain: int = 0
    download_delay: float = 0.0
    max_blocked_retries: int = 3
    autothrottle_enabled: bool = False
    autothrottle_start_delay: float = 5.0
    autothrottle_max_delay: float = 60.0
    autothrottle_block_backoff: bool = True
```

```python
# scrapling/spiders/throttle.py（逐行语义）
target_delay = latency / self.target_concurrency
new_delay = max((current_delay + target_delay) / 2, target_delay)
if not ok:
    penalty = retry_after if retry_after is not None else current_delay * BLOCK_BACKOFF_FACTOR
    new_delay = max(new_delay, penalty, current_delay)   # 被封锁只会更慢
```

```python
# scrapling/spiders/scheduler.py（去重口径）
fingerprint = request.update_fingerprint(self._include_kwargs, self._include_headers, self._keep_fragments)
if not request.dont_filter and fingerprint in self._seen:
    return False
```

```python
# scrapling/spiders/engine.py（封锁重试口径）
if request._retry_count < self.spider.max_blocked_retries:
    retry_request = request.copy()
    retry_request._retry_count += 1
    retry_request.priority -= 1        # 不立刻重试
    retry_request.dont_filter = True   # 绕过指纹去重
```

模型要点：
* **调度**：`heapq` 优先级队列 + URL 指纹去重，`dont_filter` 用于重试/翻页；
* **限速**：`AutoThrottle` 用「上一次延迟」与「本次响应耗时」做指数平滑（`(current+target)/2`），
  被封锁时用 `Retry-After` 或 ×2 退避，且有地板（`download_delay`）与天花板（`max_delay`）；
* **robots**：按域缓存解析结果，同时读取 `crawl_delay` / `request_rate` 反向抬高爬虫延迟；
* **容错**：单请求异常 → `on_error` 钩子 + 统计里计数，**不中断整轮**；
* **恢复**：`CheckpointManager` 定期把「待抓队列 + 已见指纹集合」落盘（原子替换），下次启动可续爬。

## 3. 本项目的接入方式

### 3.1 三层结构

```
community_data.py        49 源目录 + 真实摘录分析 + offline_dataset()（数据层）
        └── community_spider.py   公开页抽取 + 调用 community_comments.py 评论适配器
                └── scrapling_core.py   Fetcher/Selector/Scheduler/AutoThrottle/Engine…（框架层，纯标准库）
                        └── （可选）真实 scrapling 包：resolve_backend() 命中时只替换传输层
```

### 3.2 与上游的逐项对应

| 上游 | 本项目 | 说明 |
|---|---|---|
| `engines/static.py::Fetcher` | `FetcherSession` / `Fetcher` | urllib 代替 curl_cffi；`stealthy_headers` / `retries` / `retry_delay` / `follow_redirects` / `max_redirects` / `proxy` / `proxy_rotator` 同名同义 |
| `toolbelt/custom.py::Response` | `Response(Selector)` | `status / reason / headers / request_headers / history / body / meta` + 直接 `.css()` |
| `parser.py::Selector` | `Selector` / `Selectors` | CSS 子集（tag / `.cls` / `#id` / `[attr*=v]` / `:nth-of-type` / 后代与子代）、`find_all`、`get_all_text`、`generate_css_selector` |
| `core/storage.py` + `parser.py` 自适应 | `AdaptiveStorage` + `save/relocate/auto_match` | 指纹字段与相似度打分公式逐项对齐（含 `_dict_diff` 的 0.5/0.5 权重） |
| `core/shell.py::Convertor` | `Convertor` | `strip_noise_tags` / `sanitize_for_ai` / `to_markdown` / `extract_content` |
| `spiders/*` | 同名类 | sync + `ThreadPoolExecutor` 代替 anyio；`CrawlStats` 字段名保持一致（`requests_count` / `blocked_requests_count` / `robots_disallowed_count` / `cache_hits` …） |

### 3.3 社区模块怎么用这套模型

1. **一源一请求**：`CommunitySpider.start_requests()` 按 id 生成 `Request`（priority = −id），把
   `key/id/name/ctype` 塞进 `meta`，`parse()` 里按 key 取规则；
2. **抽取**：每源一条 CSS 候选链（`SITE_RULES`）→ 通用容器链 → 通用块链 → **自适应指纹回捞** →
   整页按港股关键词 + 长度打分取最相关片段（噪音正则直接淘汰登录/版权/推广块）；
3. **每域串行**：`concurrent_requests_per_domain = 1`，同一站点不会并发打；
4. **限速**：`AutoThrottle(start=0.5s, max=8s)`，被 429/403 时按 `Retry-After` 退避；
5. **不静默丢源**：跑完后逐条补全缺失源（robots 跳过 / 封锁耗尽）为失败记录，`summary` 只统计
   「拿到活数据」的源，失败源在 `community_data.py` 中标注「未获取」，保留目录数量；
6. **可观测**：`community_data.json.fetch_engine` 记录引擎名、传输层（port/scrapling）、自适应回捞次数、
   封禁拦截次数、耗时；CI 与微信推送状态行都读它。

### 3.4 传输层可替换（真包 / 移植层）

```python
# community_spider.py
def real_scrapling_transport():
    try:
        from scrapling.fetchers import Fetcher
    except Exception:
        return None
    def transport(method, url, headers, timeout, proxy=None, body=None):
        response = Fetcher.get(url, headers=headers, timeout=int(timeout), retries=1, proxy=proxy)
        return response.status, response.reason, dict(response.headers), response.body, url
    return transport
```

* `--backend auto`（默认）：装了 `scrapling[fetchers]` 就用真包（curl_cffi 的 TLS/HTTP2 指纹），否则用移植层；
* `--backend port`：始终用标准库移植层（CI 与单测的确定性路径）；
* `--backend scrapling`：强制真包，未安装时告警并回退；
* 解析 / 自适应 / 限速 / 调度逻辑与传输层解耦，**换传输层不改业务代码**。

## 4. 有意的差异（以及为什么不改）

| 上游行为 | 本项目 | 原因 |
|---|---|---|
| async/await + anyio | 线程池 + 同步 API | 纯标准库；调用方（`community_data.py`）本来就是同步脚本 |
| lxml + cssselect | `html.parser` 自建 DOM + CSS 子集 | CI 零安装；社区抽取只需要 tag/class/id/属性/结构这几类选择器 |
| browserforge 生成头 | 内置 Chrome/Firefox/Edge 三族头模板 | 免依赖；`Sec-Fetch-*`、`sec-ch-ua*` 等关键字段保持一致 |
| protego 解析 robots | 内置极简解析（`*` 段 / Allow 最长匹配 / crawl-delay / request-rate） | 免依赖；口径与 protego 的核心判定一致（更长路径优先） |
| pickle 断点 | JSON 断点（原子替换） | 可审阅、跨版本安全 |
| 浏览器引擎（StealthyFetcher/DynamicFetcher） | 未移植 | `--backend scrapling` 仅替换静态传输层，不会启动浏览器或解决需要 JS / 登录的站点 |

## 5. 回归防线

```bash
python3 scrapling_core.py --self-test          # 移植层自检（26 项断言：CSS/自适应/限速/robots/引擎）
python3 community_spider.py --self-test        # 社区爬虫自检（抽取 / 噪音淘汰 / 封锁降级）
python3 -m unittest tests.test_scrapling_core  # 26 项：FetcherSession/Selector/Adaptive/Scheduler/Engine
python3 -m unittest tests.test_community49     # 14 项：49 源目录 / 类型 / 跨域 / 网页与微信注入 / 框架接入
```

零联网：单测全部用**假传输层**（`FetcherSession(transport=...)`）驱动，CI 不需要外网。

## 6. 参考

* 仓库：<https://github.com/D4Vinci/Scrapling>
* 文档：<https://scrapling.readthedocs.io/en/latest/>
* 许可证：BSD 3-Clause（上游 `LICENSE`）；本项目为**模型移植**（未复制其源码文件，
  实现为本仓库自研纯标准库代码，仅在 API 命名与算法口径上保持一致并注明来源）。
