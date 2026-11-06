# 社区论坛评论：多项目组合与运行口径

本项目保留 49 个社区目录，但**不是 49 个平台都已接通热评接口**。这次将专用评论采样与既有
Scrapling 公开页抓取组合：优先使用可核验的评论或热帖回复；没有评论时保留真实页面摘录，标明
「未核验为评论」；页面也不可用时显示「未获取」，绝不编造或回填行情模板。多空词库只评价选中的
一条片段，**不代表社区共识**。

## 1. GitHub 项目调研与采用范围

| GitHub 项目与参考版本 | 许可证（上游声明） | 本项目如何使用 | 限制 |
|---|---|---|---|
| [D4Vinci/Scrapling](https://github.com/D4Vinci/Scrapling)（已有框架模型；[v0.4.15](https://github.com/D4Vinci/Scrapling/tree/v0.4.15)） | BSD-3-Clause | `scrapling_core.py` 的调度、选择器、限速模型及可选真实 Scrapling 传输层；所有 49 源保留公开页摘录回退 | 静态 HTML 不是评论证据；不承诺支持需 JS、登录或付费的页面。真实包仅替换静态传输，不会自动启动浏览器 |
| [jwlin/ptt-web-crawler](https://github.com/jwlin/ptt-web-crawler/tree/7fe6191b451ebba4c668db1afc23b4ac99f81494) | MIT | 参考 PTT 列表与 `.push` 的结构，独立实现双阶段抓取 | 仅 Stock 板当前列表；PTT 对单条回复没有独立点赞指标，不能冒充热评排行 |
| [kavv-hub/lihkg-crawler](https://github.com/kavv-hub/lihkg-crawler/tree/3f00f01c1c677597748de156d7e88dcad1923b9d) + [kitce/lihkg-api-core](https://github.com/kitce/lihkg-api-core/tree/ec64fe081332d22511376731d4b72343c6a9e708) | MIT / MIT | 参考公开列表、帖子与回复字段，独立实现只读 JSON 采样；财经台分类为 **15** | 非官方、可能变更或拒绝访问；没有认证、签名逆向或验证码处理 |
| [praw-dev/praw](https://github.com/praw-dev/praw/tree/v8.0.3) | BSD-2-Clause | 可选安装真正的 PRAW 8.0.3，用用户获准的只读 OAuth 查询 4 个 Reddit 社区 | Reddit API 可能需要申请与平台批准；SDK、凭据、权限或网络缺失时不伪造结果 |
| [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | 上游当前为 **Non-Commercial Learning License 1.1**，仅非商业学习/研究 | 仅调研其多平台评论能力，**未安装、复制或调用** | 支持的平台往往需要登录状态，且非商业限制不适合默认纳入。若另行使用，须自行审查上游许可证及平台授权 |
| [mikwielgus/forum-dl](https://github.com/mikwielgus/forum-dl/tree/f2168a89fc2a8b713626700d1e638016ff67623d) | MIT | 参考 phpBB 列表页 `.topictitle` 与帖子 `.post` / `.content` 的选择器，独立实现 Bogleheads 论坛的 html 采样；未复制上游代码 | 仅访问老旧 phpBB 暴露的公开页面；未登录、不绕过验证码或付费墙 |
| [isdrchan/discuz-crawler](https://github.com/isdrchan/discuz-crawler) | 仓库未声明许可证 | 参考 Discuz 帖子回复 `postmessage_` / `pid` 的抓取结构，独立实现 Discuss.com.hk 财经板 html 采样；未安装上游包 | 非官方、可能变更或拒绝访问；Discuss.com.hk 对自动抓取的限制未知，仅读取首页列表 |
| [TUVIMEN/forumscraper](https://github.com/TUVIMEN/forumscraper) | GNU GPL-3.0 | 仅调研其通用论坛抓取能力，**未安装、复制或调用** | 强 copyleft 不适合默认纳入；未在本项目使用 |
| [zaxtyson/discuz](https://github.com/zaxtyson/discuz) | 仓库未声明许可证 | 仅调研其 Discuz 帖子/评论字段，**未安装、复制或调用** | 旧临时项目，未在本项目使用 |

适配器没有把这些项目的代码复制进来；PRAW 是可选的外部 SDK。项目许可证与被采集平台的
服务条款、数据使用权限是两回事；使用者须分别确认。上述接口在本执行环境不能直接访问，
代码和降级流程通过模拟报文测试，**不等于已在线验证当前平台可用**。

## 2. 样本与热度定义

- **PTT Stock**：扫描当前列表页中有可见正推噓净数的帖子，按该数字选最多 3 帖；「爆」表示
  **至少 100**，不是精确的 100。每帖最多检查前 20 个 `.push`，输出最多 3 条有财经上下文、
  内容足够长的回复；排序依据是**所属帖子**的净推噓数，同帖回复无点赞排序。仅保留 PTT 给出的
  月日与时间，年份、时区未知，不推算为当前年份或保证新鲜度。原帖标题可提供主题上下文。
- **LIHKG 财经台**：取分类 15 的第一页最多 20 个帖子，按 `no_of_reply` 选最多 3 帖，取每帖
  第一页前 20 楼中 `msg_num > 1` 的回复，按该回复的 `like_count` 排序。只接受有实际时间戳、
  在抓取时刻前 7 天内的回复；原帖、明确标记 `display_vote=false`、无时间、过期或字段缺失的记录
  不参与排名。关于 `display_vote` 的判断仅依据非官方社区脚本（例如 [LIHKG Reborn](https://github.com/DeckryZ/LIHKG_Reborn/blob/6d5b45084c272cec7a2a20f5dbebbfe73b2cb68d/lihkg.user.js#L164-L167)
  的显示投票数逻辑），非官方承诺；这里保守跳过，不更改平台返回的标记。
- **Reddit / PRAW**：`r/ChinaStocks`、`r/investing`、`r/options`、`r/CryptoCurrency` 分别取
  `hot(limit=3)`；每帖设置 `comment_sort='top'` 和 `comment_limit=20`，只读取顶层评论，
  不调用 `replace_more`。仅接受有真实时间与未隐藏得分的 7 天内评论；按单个 subreddit 的实际 score
  排序，不跨站比较，也不声称是全站 Top。未来超过 5 分钟的时间戳会被丢弃。
- **Bogleheads 论坛 (phpBB)**：访问 `viewforum.php?f=10`「投资 - 理论、新闻与一般」板块，取
  可见回复数最多的前 3 帖；再进入帖可见页取首页前 20 条回复。用 `Replies` 计数作为热度
  参考，不是单条回复的点赞；不抓取作者名，不跨站比较。列表页若给出分页链接，优先进入可见
  末页，避免只读早年长话题的首页。回复本身嵌入引用、脚本、样式与隐藏块时只保留作者直文。
- **Discuss.com.hk 财经板 (Discuz)**：访问 `forumdisplay.php?fid=115`，取可见回复数最多的前 3
  帖；进入帖子取首页前 20 条回复。同样按所属帖回复数排序，不代表站内评论点赞或社区共识。
- **公开页回退**：49 源仍走原有 CSS / 通用片段 / 自适应规则；普通页面命中财经主题只算
  `page_excerpt`，即便出现在“热评与公开摘录”栏目也不称为评论。

每个源输出最多 3 条去重后的符合条件样本。`matched_comments` 是**本轮已读取样本里**符合条件
的去重数量，不是站内总评论数；每条含帖子标题、文本、原帖/评论链接、发布时间或未知标记、
指标名称/数值及采样范围。链接只存在于**数据层**（`comments[].url` / `source_url`）供离线
核查，**内容层不渲染**：网页与微信都不产生 `<a href>`，也不出现「查看原帖/原页」「原帖」
字样（回归防线：`tests/test_content_links_hidden.py`）。仅 PTT、LIHKG、Bogleheads 和
Discuss.com.hk 的采样请求经过项目爬虫的每域并发上限和退避；PRAW 使用其自身 API 限速。
主题可以在回复或所属帖子标题中，但词库情感只对回复文本打分。列表页、帖页和评论采样都有
上限，可能遗漏更热或更相关的内容。

## RSS 优先信息流（49 源）

49 个社区源采集时优先尝试公开 RSS 订阅；RSS 地址由 `community_data.RSS_FEEDS` 映射（Reddit 4
个社区、Bogleheads、Discuss.com.hk、Seeking Alpha 等），未列出的源视为无 RSS，直接回退到
网站/APP 公开信息流。

- RSS 条目必须命中财经/市场主题词并落在采集时刻前 7 天窗口内，否则跳过；不编造标题、链接或发布时间。
- RSS 不可用（无地址、网络错误、XML 解析失败、无命中条目）时，立即回退到现有的公开网页摘录与评论策略（PTT/LIHKG/Bogleheads/Discuss/Reddit PRAW），单源失败不影响其他源。
- RSS 成功的源不再触发 HTML 页面抓取与评论采样，直接使用 RSS 条目作为该源的实抓片段（`content_kind` 为 `thread_reply`，`strategy` 为 `rss_feed`）。
- 同一采集进程内，RSS 请求与 PTT/LIHKG 的帖子详情读取共享串行限速器：上一次帖子/源读取结束后至少等待 10 秒才开始下一次。

## 逐源独立抓取方案（49 源各一条端点链）

`community_plans.py` 的 `PLANS` 是 49 源逐源方案的唯一事实源。它解决的是「共用一条通用阶梯」
带来的问题：以前只有 5 类源有专用采样、7 源配了 RSS，其余 40 多个源共用 CSS 选择器 + 通用片段
回捞，失败时日志只有 `status=0`，看不出该源到底该走哪个端点、还有没有别的路。

每份方案是一条**有序端点链**，第一个产出合格证据的步骤胜出，其余不再发请求：

`adapter`（已有专用适配器，只登记不重复打）→ `rss`（站点官方订阅）→ `json_api`（站点公开接口）→
`rsshub`（RSSHub 路由，按实例链轮换）→ `html`（公开页面结构化摘录，可多候选 URL）→
`carrier`（Google News 检索载体）。

### 执行与预算

- 每源最多尝试 `PLAN_MAX_STEPS`（默认 3）个步骤；整轮请求预算 `PLAN_REQUEST_BUDGET`（默认 90）、
  墙钟预算 `PLAN_WALL_BUDGET`（默认 420 秒），可用 `COMMUNITY_PLAN_MAX_STEPS` /
  `COMMUNITY_PLAN_REQUESTS` / `COMMUNITY_PLAN_BUDGET` / `COMMUNITY_PLAN_DELAY` 覆盖。
  预算耗尽时该源记 `budget_exhausted`，**不是**「抓不到」，两者在诊断里分开。
- 步骤之间的间隔**沿用调用方的 `download_delay`**（默认 0.15 秒，`COMMUNITY_PLAN_DELAY` 可覆盖）。
  这一层发的是**源级列表/订阅请求**，与既有爬虫同一套礼貌口径；它与「帖详情读取至少间隔 10 秒」的
  帖子限速器不是同一口径（同上文「来源列表页…不受这一帖子间隔约束」），
  `adapter` 步骤由 `collect_comments` 执行，仍走原有 10 秒帖子间隔。
- 开关：`fetch_live_snippets(..., plan_strategies=False)` 或 `community_data.py --no-plans`
  可整层关闭（与 `--no-comments` 对称），关闭后回到「RSS 优先 + 专用适配器 + 通用摘录」的原有行为。
- **只补强，不降级**：`should_run_plan()` 决定要不要跑方案链 —— 未获取的源跑；只有通用页面摘录
  （`page_excerpt`）而方案里有更强的路（`rss` / `json_api` / `rsshub`）时跑，成功则升级证据；
  已经有评论或热帖回复（`comment` / `thread_reply`）时**不跑也不覆盖**。
- 单步异常只影响这一步，单源异常只影响这一源；方案层整体异常时保留既有结果并记
  `plan_fetch.error`，不打断整轮采集。

### 端点核验口径（未做在线实测）

本项目的执行环境只能出网到 GitHub / PyPI / npm，**不能直连目标站点**，因此端点是这样核对的：
用 GitHub API 取 `DIYgod/RSSHub` 全量路由树，再逐条读取路由源码的 `export const route` 块
（`path` / `example` / `parameters` / `features.requireConfig` / `features.antiCrawler`），
基准 commit 钉在 `RSSHUB_UPSTREAM`。核验状态如实分级：

| 状态 | 含义 |
|---|---|
| `native` | 站点官方订阅出口 |
| `api_public` | 站点公开接口，且**响应结构对着上游调用源码核对过** |
| `rsshub_doc` | RSSHub 路由存在，参数与是否需要配置已核对 |
| `needs_check` | 地址来自公开资料或上游源码，但未在线实测，可能已失效 |
| `carrier` | Google News 检索载体，明确是聚合检索，不是站点原生源 |
| `adapter` | 本项目自有适配器（见上文各专用采样口径） |

`needs_check` 不是免责声明，而是**待实测清单**：在有外网的环境跑一次真实采集，
把状态改成 `native` / `api_public` 或把端点换掉，都应当同步更新 `PLANS` 与生成文档。

三条已核对出来的硬结论（写进方案，不靠猜）：

1. `RSSHUB_ABSENT` 里的 20 个站点在全量路由树与全文检索里都没有命中，方案里**不写 RSSHub 步骤**；
   `tests/test_community_plans.py` 钉住 25 个源不得出现 `rsshub` 步骤，防止凭印象写出不存在的路由。
2. 需要 Cookie/Token 的路由（X / 知乎 / 微博 / 小红书 / 抖音）显式 `needs_config`，
   排在免密钥的检索载体之后，且**绝不作为链条最后一步**；未配置时记 `not_configured` 且不发请求。
   `plan_strength()` 也只统计不需要密钥的步骤，因此这 5 源在默认配置下如实归为 `excerpt_only`。
3. 只有 `html` + `carrier` 两级的源必须显式标 `no_public_feed=True`（构造期强校验），
   避免「看起来有方案、其实只能靠摘录」被含糊过去。

### 诊断字段

- 各源 `plan_attempt`：`status`（`ok` / `unavailable` / `budget_exhausted` / `error_*`）、
  胜出的 `strategy` 与 `endpoint`、`content_kind`、`verified`、`step_kind`，以及 `plan_steps[]`
  ——每一步的 `index` / `kind` / `strategy` / `verified` / `status` / `endpoint` / `candidates`。
  失败源因此有名字、有原因、有「试到哪一级」，可逐源排查。
- `fetch_engine.plan_fetch`：`attempted_sources`、`plan_sources`（方案链救回的源数）、
  `matched_comments`、`requests_count` 与 `budget`（用量、预算、是否耗尽、步骤间隔）。
- `plan_fetch` 的请求数已并入 `fetch_engine.requests_count`。

```bash
python3 community_plans.py --self-test                          # 覆盖率 + 逐源端点链 + 强度（离线）
python3 community_plans.py --only XUEQIU                        # 单个源的完整方案
python3 community_plans.py --plans-md docs/community-49-plans.md # 重新生成逐源核对表
python3 community_plans.py --routes-md docs/community-49-routes.md
python3 -m unittest tests.test_community_plans                  # 47 项回归，零联网
```

`docs/community-49-plans.md` 与 `docs/community-49-routes.md` 都由代码生成，**禁止手改**；
测试会断言文档与 `PLANS` 注册表逐字一致，改了方案忘了重新生成就会红。

## 3. 四小时快照、帖子间隔与重新分析

- `community_snapshot.py` 是常规入口：首次采集后，把 49 源结果分别写入各自的 SQLite 数据库
 （`community_store/01-futu.sqlite3` … `49-aastocks.sqlite3`，`id-key` 命名），再从 49 个库导出
  静态 JSON 视图 `community_data.json`。距上次采集尝试**未满四小时**时复用，满四小时后下一次
  运行才重新采集。时间由快照中的 UTC `snapshot.attempted_at` 判断，不靠文件修改时间；写入
  采用每库独立事务 + 批次清单 `manifest.json` 的原子替换，只有全部 49 个库写入成功后新批次
  才可见。采集器整体异常时写入 49 源「未获取」而不是把旧数据冒充新数据；失败尝试同样等待
  四小时，避免五分钟报告循环持续请求目标网站。单个站点失败时其它成功结果仍保留。
- GitHub Actions 使用跨运行的社区任务互斥锁，恢复/保存 `community_store/` 到 Actions cache，
  并通过 artifact 分发给部署和微信任务；渲染/推送时用 `community_store.py --export` 从库重新
  生成静态 JSON，不联网再抓取。所有定时触发（`*/5`、`17 */2`、`0 */4`、`0 1`）在检查/复用四小时社区快照后
  均执行报告构建、Pages 发布与定时推送，并在 `pages-schedule-loop` 中串行排队，不再按 `github.event.schedule`
  跳过 `0 */4 * * *`（GitHub Actions 对公共仓库高频 `*/5` 会大幅限流，若跳过 `0 */4` 会导致数小时不构建新报告）。GitHub 定时任务可能延迟或漏跑、
  并发队列最多保留一个待运行任务，cache 也可能失效或被逐出，因此不是精确到秒的调度保证；
  快照丢失后下一次运行会重新采集。Actions cache 还受分支访问范围限制，不同分支的手动运行
  不一定共享最新快照；独立本地进程之间也没有跨进程锁。Actions cache 不是永久数据库，仅用于
  运行间传递本次批次；`community_store/` 不提交到仓库。
- 同一采集进程内，PTT/LIHKG 的帖子详情请求和 PRAW 的逐帖评论读取共享串行限速器，
  **上一次帖子读取结束后至少等待 10 秒才开始下一次**，即使来自不同站点或上次出错。
  PTT/LIHKG 底层传输重试也会限速；
  PRAW 内部 HTTP 重试由 SDK 自己控制，不能承诺每个内部请求都间隔 10 秒。来源列表页、robots
  请求及 49 源公开首页回退不是帖子详情，不受这一帖子间隔约束，仍使用各自原有的限速和退避。
  开启 PRAW 时这些等待会增加采集耗时。
- `build_site.py` 和 `tools/wechat_push.py` 在各自读取快照后对保存的文本重新计算情感和量化，
  不重新联网抓社区；帖子标题不会代替回复作者投票。页面显示原始采集时间及本次分析时间，
  `fetch_date` 不因重算而改变。跨 UTC 午夜但仍在四小时有效期内的快照，严格推送检查可以使用
  原采集日期；过期、格式错误或正文日期与快照不一致时严格模式拒绝推送，定时模式沿用警告。
  旧格式若没有 `analysis_input`，则保留
  原分析、无法重新计算。评论的 7 天窗口按采集时刻检查，快照有效期间不会重新采样。

## 4. 使用与诊断

```bash
# 常规入口：到期才采集，并默认检查 robots；再从 49 个社区库导出静态 JSON；随后分析。
python3 community_snapshot.py
python3 community_store.py --export --dir community_store --json community_data.json
python3 build_site.py --community community_data.json
python3 tools/wechat_push.py --dry-run

# 从已有库导出静态 JSON，不联网再抓取（页面/推送前重新分析用）。
python3 community_store.py --export --dir community_store --json community_data.json

# 仅检查是否到期；排查时才手动绕过四小时缓存。
python3 community_snapshot.py --plan
python3 community_snapshot.py --force
# 底层调试命令会直接联网采集，不受四小时缓存限制。
python3 community_data.py --limit 24 --timeout 8 --robots
python3 community_data.py --no-comments

# 离线测试，不访问平台
python3 -m unittest tests.test_community_comments tests.test_community_snapshot
python3 -m unittest discover -s tests
```

`community_data.json` 中：

- `snapshot.attempted_at`、`interval_seconds`、`post_interval_seconds` 与 `status` 记录本轮采集尝试和策略；
  各源 `analysis_input` 保存重算所需的实际文本；`analysis_at` 与 `analysis_stats` 反映
  页面/推送读取时的实际重算情况（没有可重算文本时不显示分析时间）。
- `summary.comment_sources` / `summary.page_excerpt_sources` 分别计数评论/热帖回复源、普通页摘录源；
  `summary.ok` 仍是两者之和，**不是热评源数量**。
- 每条源的 `content_kind` 为 `comment`、`thread_reply`、`page_excerpt` 或演示模式的 `demo`；
  `strategy` 标明 `lihkg_api` / `ptt_html` / `reddit_praw` / `scrapling_page`，`source_url` 是原帖或原页。
- `comments[]` 包含 `text`、`text_truncated`、`post_title`、`url`、`published_at` / `published_label`、
  `metric`、`scope`；文本最长 300 字，截断时显示省略号。PTT 的 `published_at` 为空，不凭空补年份。
  `comment_attempt` 记录该源适配器状态。
- `fetch_engine.comment_fetch.attempts` 给出每源 `ok`、`partial`、`no_comments`、
  `not_configured`、`praw_not_installed`、`invalid_response`、`robots_disallowed`、
  `http_403` / `http_429` 等状态及样本数。HTTP/解析/权限失败不会覆盖成功的页面摘录。
  `requests_count_excludes_praw=true` 表示 SDK 内部请求不计入爬虫请求数。

`community_store/` 中每个社区一个 SQLite 库（`01-futu.sqlite3` … `49-aastocks.sqlite3`），
  每库只包含当前已提交批次的那条记录；`manifest.json` 记录批次 id、49 个库文件名与摘要。
  导出静态 JSON 时，任何缺失、损坏或不属于当前批次的库只让对应源标记「未获取」，不回退
  旧数据；所有 49 个库写入成功后批次才对外可见。

`fetch_date` /「抓取核验」是 **UTC 采集批次启动日期**，不是帖子发布时间；批次跨午夜时后续请求
可能在次日完成。仅 LIHKG / Reddit 的
`published_at` 是平台提供的实际时间。`--robots` 适用于本项目的 HTTP 爬虫（含 PTT/LIHKG
子请求），不会控制 PRAW 的官方 API；现有 robots 解析器是简化实现，robots 文件取不到时
保持项目原有的放行行为，**不能代替对目标网站条款和授权的审查**。不自动登录、解决验证码、
绕过付费墙或在访问被拒后切换代理。评论链接在写入数据层前经过 HTTP(S) 校验（`safe_source_url`
拒绝非 http(s)、含凭据或含控制字符的地址）；**内容层不再渲染任何链接**，因此校验过的 URL
只留在 `community_data.json` 里供核查，不会进入网页或微信正文。

## 5. 可选 Reddit 配置

本地安装 `python3 -m pip install 'praw==8.0.3'`，用符合 Reddit 政策、已获准的 API 应用设置
环境变量 `REDDIT_CLIENT_ID`、`REDDIT_CLIENT_SECRET` 和 `REDDIT_USER_AGENT`（应是可识别的
应用 UA）。无需在此项目存储 Reddit 用户名或密码。不要把密钥写进文件、日志、仓库或聊天。
缺少任一变量时显示 `not_configured`，没安装包显示 `praw_not_installed`；仍可使用其他方案。

`.github/workflows/m.yml` 的独立 `community_snapshot` job 从同名 GitHub Actions Secrets 读取配置；
**需要采集且三项都存在才安装 PRAW**。安装失败仍运行其余抓取，没有配置 Secrets 时保持纯标准库。
部署和微信任务只读取同一快照，不再各自抓取。启用前应核对 Reddit 与其他平台的访问政策、配额
和实际运行次数。

输出会进入 `community_data.json`、网页和微信，可能被公开发布；只抓取有权使用的公开内容。
适配器不导出作者名或 PTT IP，但评论文本本身可能包含个人信息，不能把这一点视作完整匿名化。
