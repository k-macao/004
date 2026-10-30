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
- **公开页回退**：49 源仍走原有 CSS / 通用片段 / 自适应规则；普通页面命中财经主题只算
  `page_excerpt`，即便出现在“热评与公开摘录”栏目也不称为评论。

每个源输出最多 3 条去重后的符合条件样本。`matched_comments` 是**本轮已读取样本里**符合条件
的去重数量，不是站内总评论数；每条含帖子标题、文本、原帖/评论链接、发布时间或未知标记、
指标名称/数值及采样范围。仅 PTT 和 LIHKG 的采样请求经过项目爬虫的每域并发上限和退避；
PRAW 使用其自身 API 限速。主题可以在回复或所属帖子标题中，但词库情感只对回复文本打分。
列表页、帖页和评论采样都有上限，可能遗漏更热或更相关的内容。

## 3. 四小时快照、帖子间隔与重新分析

- `community_snapshot.py` 是常规入口：首次采集后，将 49 源结果写入 `community_data.json`；
  距上次采集尝试**未满四小时**时复用，满四小时后下一次运行才重新采集。时间由快照中的 UTC
  `snapshot.attempted_at` 判断，不靠文件修改时间；写入采用同目录临时文件和原子替换。
  采集器整体异常时写入 49 源「未获取」而不是把旧数据冒充新数据；失败尝试同样等待四小时，
  避免五分钟报告循环持续请求目标网站。单个站点失败时其它成功结果仍保留。
- GitHub Actions 使用跨运行的社区任务互斥锁，恢复/保存 Actions cache，并通过本次运行的
  artifact 分发给部署和微信任务。`0 */4 * * *` 是独立的仅采集触发，不在同一时刻额外推送；
  原有的每五分钟和每日报告触发仍检查快照时效并负责发布。GitHub 定时任务可能延迟或漏跑、
  并发队列最多保留一个待运行任务，cache 也可能失效或被逐出，因此不是精确到秒的调度保证；
  快照丢失后下一次运行会重新采集。Actions cache 还受分支访问范围限制，不同分支的手动运行
  不一定共享最新快照；独立本地进程之间也没有跨进程锁。
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
# 常规入口：到期才采集，并默认检查 robots；随后分别读取快照进行分析。
python3 community_snapshot.py
python3 build_site.py
python3 tools/wechat_push.py --dry-run

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

`fetch_date` /「抓取核验」是 **UTC 采集批次启动日期**，不是帖子发布时间；批次跨午夜时后续请求
可能在次日完成。仅 LIHKG / Reddit 的
`published_at` 是平台提供的实际时间。`--robots` 适用于本项目的 HTTP 爬虫（含 PTT/LIHKG
子请求），不会控制 PRAW 的官方 API；现有 robots 解析器是简化实现，robots 文件取不到时
保持项目原有的放行行为，**不能代替对目标网站条款和授权的审查**。不自动登录、解决验证码、
绕过付费墙或在访问被拒后切换代理。评论链接在输出前经过 HTTP(S) 校验并转义。

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
