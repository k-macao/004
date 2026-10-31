# 271— 章鱼 AI·舆情004 (Editorial E-Ink Edition)

自动生成的分析报告站点: <https://k-macao.github.io/03/>

## 🔄 动态报告 — 行情更新与四小时社区快照

报告任务按需更新行情；49 个社区源每四小时采集一次，网页和推送读取同一快照并重新分析。
抓取日期为 UTC 采集批次的启动日期，**不等于内容发布时间，也不保证所有站点可访问或一定是全站最新/最热**；
失败源标记「未获取」，不回填虚构内容。

```bash
python3 market_data.py               # ① 动态抓取行情 → market_data.json (Yahoo→Stooq回退)
python3 verify_quotes.py --json verify_report.json --text   # ①′ 全来源交叉校验 (FAIL=2 阻断推送；网页阶段仅公开报告不阻断)
python3 community_snapshot.py        # ② 到期采集49社区 → community_data.json；四小时内复用，多方案采样、失败标记未获取
python3 macro_data.py                # ②′ 动态抓取宏观/财经快讯 → macro_data.json (网页 02 节 + 微信 02 栏正文；只保留 7 天内、发布日期可解析的条目)
python3 rss_feeds.py                 # ②″ 抓取 49 大财经 RSS 情报源 → rssfeeds_data.json (网页 05 节 + 微信 05 栏；只保留 14 天内条目)
python3 build_site.py                # ③ 动态建站 → report.html (注入行情+社区+宏观快讯+RSS 情报源+日期/时间戳，49源动态注入；
                                     #    同时现算 04 栏 AI 预测(下一交易日) 并把预测写进 forecast_history.json —— 先存档后结算)
python3 tools/wechat_push.py --embed # ④ 内嵌最新推送负载进 report.html
python3 tools/wechat_push.py --push --scheduled   # ⑤ 推送完整报告到微信
```

- **行情源**：Yahoo Finance chart API → Stooq CSV 多源自动回退（纯标准库，CI 无需安装依赖）；行情清单新增 **Cboe VIX（`^VIX`）**，与其它标的一起每日现抓、失败单独降级。
- **社区源**：目录保留 49 源；PTT、LIHKG 与配置了官方 API 权限的 Reddit 有专用评论采样，
  其他源以 Scrapling 公开页摘录回退（**未核验为评论**）。帖详情在同一次采集中共享限速：
  上一帖读取完成后至少等待 10 秒再读下一帖。网络/站点限制会导致部分源不可用；GitHub 定时与
  缓存不保证精确触发，详见 [多方案社区评论抓取](docs/community-comment-strategies.md)。
- **覆盖标的**：VIX 恐慌指数 / 恒指 / 恒生科技 / 恒生国企 / 标普 500 / 纳斯达克 / 道琼斯 / 现货黄金 / WTI / 布伦特 / 美元离岸与在岸人民币（共 12 项实时行情；04 栏仍只预测原有 11 个可配置资产，不把 VIX 点位当作股票方向）。
- **失败降级**：行情缺失显示「—」，社区源抓不到则标记「未获取」；单源失败不阻断构建，
  但严格推送仍受日期与行情校验门禁约束，不能保证定时任务一定成功。
- **02 栏（全球经济与财经动态）已改为快讯驱动**：`macro_data.py` 每次构建现抓 Google News RSS（中/英分主题）+ 美联储官方新闻稿 RSS + 东财财经快讯检索，按「宏观 / 美联储 / 港股 / 大宗商品 / 大行目标价」五类归组后渲染，**每条快讯自带发布日期**；超窗或无日期的条目在数据层就被丢弃（`stale_dropped` / `undated_dropped`）。**修复背景（2026-09-16 核查）**：这一栏原本是写死在 `tools/wechat_push.py` 里的固定文案（IMF 7 月 WEO、7 月 29 日 FOMC、8 月 12 日 CPI、南向 628.69 亿、各行恒指目标价…），构建时原样重播、页脚却盖当天时间戳，导致「生成时间是当天、正文停在 8 月 12 日」；现在抓不到快讯时 02 栏明确显示「今日未获取」+ 实时行情，**绝不回填历史叙事**（回归防线：`tests/test_wechat_push_macro.py` 断言正文不得再出现任何写死的历史事实）。
- **网页 02 节同步接入宏观快讯**：`report.html` 的 02 节（行情快照）新增 `<!-- MACROLIST -->` 占位区，`build_site.py` 从**同一个** `macro_data.json` 注入五类快讯（每条自带发布日期，注入区尾部留 `<!-- /MACROLIST -->` 哨兵保证重复构建幂等）；缺 `macro_data.json` 时网页同样显示「今日未获取」+ `python3 macro_data.py …` 恢复命令，**绝不回填历史叙事** —— 网页与微信推送共用一套数据与时效口径（回归防线同上：`tests/test_wechat_push_macro.py` 同时断言网页注入区不含任何写死的历史事实）。
- **构建期自动补抓（2026-09-17 修复「今日未获取」的长期根因）**：线上核查确认 `.github/workflows/m.yml` 从来没有 `macro_data.py` 这一步（改动此前只以 `docs/macro-ci-workflow.patch` 交付、需要有 `workflows` 权限的账号手工 `git apply`，一直未应用；已用 `gh run view --job` 的 step 列表核实），所以每次构建都不存在 `macro_data.json`，网页 02 节与微信 02 栏**稳定**停在「今日未获取 · `no_file`」。现在有两条独立通路，任一条生效即可产出快讯：
  1. **`build_site.py` / `tools/wechat_push.py` 入口补抓**（不需要 `workflows` 权限，与 `panorama.py`「构建期直接调用、无需改 CI workflow」同一思路）：**CLI 入口**先调 `macro_data.ensure()`，产物**缺失 / 写坏 / 是旧快照**时就地抓一轮并落盘，`deploy`/`wechat`/`daily` 三个 job 全部受益；只在 `no_file`/`bad_type`/`stale_snapshot` 时补抓，`no_items`（当次已抓过但窗口内 0 条）不重复烧网络；补抓失败绝不抛异常、绝不阻断构建。开关：默认**只在 CI 里自动开**（`GITHUB_ACTIONS` 存在），本地/单测默认不联网，`MACRO_AUTO_FETCH=1|0` 显式覆盖，CLI 另有 `--macro-fetch` / `--macro-no-fetch` / `--macro-mock` / `--macro-days` / `--macro-timeout`（`--check` 纯校验模式永不联网）。回归防线：`tests/test_macro_bootstrap.py`。

     ⚠️ **补抓只能在 CLI 入口，不能放进渲染函数**（2026-09-17 二次事故）：第一版把 `ensure()` 放在 `tools/wechat_push.py` 的 `load_macro_data()` 里，而它是 `build_single_wechat_html()` 调用的**库函数**、单测会直接调。CI 里 `GITHUB_ACTIONS=true` 默认开补抓 → 单测真的联网抓到了快讯 → `tests/test_panorama.py::test_wechat_section_degrades_when_all_sources_missing` 这类「四路数据全缺必须降级」的断言当场挂掉，合并后 deploy job 的「🧪 舆情层自检」步骤直接红（本地无外网，所以提交前复现不出来）。现在 `load_macro_data()` 是**纯读取**，补抓只在 `bootstrap_macro_data()` 里、由 `main()` 调用；回归防线为 `test_render_path_never_fetches_even_in_ci`（把 `md.build` 换成「一被调用就 AssertionError」的桩件，断言渲染路径永不触发抓取）。
  2. **workflow 里显式的抓取步骤**（待人工应用一次）：三个 job 各加一步 `macro_data.py --json macro_data.json --days 7 --text`（失败不阻断，逐源摘要写入 Step Summary），作为「补抓通路」之外的一手数据源与可观测入口。Agent 的 GitHub App 缺 `workflows` 权限已**实测确认**（`git push` 直接被远端 reject：`refusing to allow a GitHub App to create or update workflow ... without workflows permission`），因此以 `docs/macro-ci-step-only.patch` 交付，在有权限的账号执行 `git apply docs/macro-ci-step-only.patch` 即可（该补丁**只含快讯步骤**，不含 `verify_quotes.py` 门禁）。
- **推送前行情校验**：`tools/wechat_push.py --push` 默认调用 `verify_quotes.py`；行情校验为 FAIL 时阻断推送，
  WARN 默认仅警告（`--verify-strict` 可收紧）。Yahoo 常规时段价格与同口径涨跌幅配对，窗口前收不能冒充昨收；
  缺少可信前收或同日涨跌幅对照时如实警告，不关闭校验。校验器自身异常沿用既有 fail-open 处理并记录堆栈。
- **日期核验**：抓取过的源保留批次「抓取核验」日期；该日期不代表评论的发布时间。
  `--limit` 未请求的源不会伪造核验标记；手动推送检查 49 条标记及快照时效，允许仍在四小时内的跨午夜快照，
  定时推送对社区核验问题仅警告。
- 本地联调可用 `python3 market_data.py --demo && python3 community_data.py --demo` 生成模拟行情+社区。

## 🌡️ 00 栏：每日 VIX 恐慌指数 (Daily VIX Fear Gauge)

本栏放在网页与微信正文的最开头，每次构建直接读取 `market_data.json → quotes.VIX`，展示
**当次点位 / 较前收涨跌 / 涨跌幅 / 行情日期 / 波动定价分档 / 30 日一标准差粗略幅度**；
极趣墨水屏报告的第一页与 02 行情表也同步带上 VIX。行情缺失时只显示「今日未获取」，
**不回填上一版点位，也不把缺失误写成“平静”**。统一分析与两端渲染集中在 `vix_daily.py`。

口径（页面与推送各印一行，不逐块重复）：

- **定义**：VIX 由一篮子 SPX 虚值期权按模型自由方差法合成，报价为未来 30 天隐含波动率的年化百分数。
- **换算**：`VIX × √(30/365)` 只给未来 30 天的一标准差**幅度**，不给涨跌方向；
  `VIX²` 更接近风险中性 Q 测度的预期方差，不等于现实 P 测度的已实现波动率。
- **分档**：页面 `<12 / 12–20 / 20–30 / 30–40 / ≥40` 只为每日阅读设置，不进入 01 合成分、04 预测或配对推荐。

```bash
python3 market_data.py --demo                    # 生成含 VIX 的 12 项演示行情
python3 -m unittest tests.test_vix_daily         # 定义 / 分档 / 缺失降级 / 网页与微信顺序
```

## 🌍 01 栏：每日全球全景扫描 (Daily Global Panorama Scan)

> 扫一遍今天全球市场，总结推动股价的 5 大力量。重点关注宏观事件、板块轮动、情绪变化。
> 哪些是重点，哪些是噪音。如何利好利空。是否可以做多。

本栏由 `panorama.py` 在**每次构建时现算**，四路当次数据（行情 / 宏观快讯 / 舆情因子 / 社区研判）
合成，**引擎里不存放任何新闻事实、点位与日期**——与 02 / 03B 栏同一套反陈旧口径。

**① 候选力量（取到数据才出现，取不到就不编故事）**

| 力量 | 数据来源 | 归类 |
|---|---|---|
| 港股盘面本身 | 恒指 / 恒科 / 国企当日涨跌 + 港股快讯 | 市场盘面 |
| 全球风险偏好 | 标普 / 纳指 / 道指的隔夜映射 | 市场盘面 |
| 美元利率与离岸流动性 | 联储快讯 + 离岸人民币 + 黄金 | 宏观事件 |
| 宏观事件与全球增速 | 央行 / 经济数据 / 政策类快讯 | 宏观事件 |
| 大宗商品与供应链 | WTI / 布伦特 / 黄金 + 供应链快讯 | 宏观事件 |
| 机构观点 | 大行目标价与配置建议快讯 | 宏观事件 |
| 板块轮动 | 恒科−恒指、纳指−道指的成长/价值价差 | 板块轮动 |
| 情绪变化 | 舆情温度计 + 49 源社区多空家数 | 情绪变化 |

**② 打分与重点 / 噪音判定（显式规则，可复算）**

```
力量分 = 100 × (0.45 × 幅度 + 0.30 × 印证度 + 0.25 × 时效)
  幅度   |涨跌幅| / 该资产的显著性阈值（不同资产波动率不同，阈值分开给）
  印证度 独立证据条数 / 3（多资产或多条快讯互相印证才算高）
  时效   行情当次抓取 = 1.0；快讯当天 1.0，每往前 1 天 −0.15

≥55 重点　35~55 次要　<35 或未过噪音阈值 → 噪音（分数压到 30 以内，且不参与做多结论）
```

噪音阈值举例：恒指 0.35% / 纳指 0.40% / WTI 0.80% / 离岸人民币 0.12%；
成长−价值价差低于 0.30 个百分点即判定「当日无有效轮动」，避免把噪音讲成故事。

**③ 是否可以做多（合成分 + 置信度）**

```
合成分 = Σ(方向 × 力量分 × 权重) / Σ(力量分 × 权重) × 100     # 噪音力量不计入
调整项：突发风险分 ≥60 → −10；负面新闻占比 ≥60% → −5
置信度 = 0.6 × 数据覆盖率 + 0.4 × 有效力量广度

≥25 可以做多　≥10 轻仓试多　−10~10 观望　≤−10 偏防御　≤−25 减仓或对冲
置信度 <0.35 → 直接输出「数据不足 · 不给做多结论」
```

每条结论都挂着推导它的证据（行情读数或当次快讯标题 + 发布日期），并给出恒指前收盘作为
多头有效性参考位。四路数据全缺时本栏渲染为「今日未获取 —— 本栏不编故事」，**绝不回填历史叙事**。
每条力量、每个关注面、以及「是否可以做多」结论后面，另附一条 **AI 量化 · 配对交易**（见下节）。

```bash
python3 panorama.py                 # 读仓库内 4 份 json → 文本摘要
python3 panorama.py --json out.json # 导出结构化扫描结果（便于回测/核对）
python3 panorama.py --self-test     # 规则自检（普涨 / 普跌 / 横盘 / 空数据）
python3 -m unittest tests.test_panorama   # 13 项回归
```

## 🔮 04 栏：AI 预测 · 未来函数 (AI Forecast · Next Session)

> 01~03B 栏回答「今天发生了什么」，本栏回答**下一交易日会怎样**。

由 `forecast.py` 在**每次构建时现算**，四路当次数据（行情 / 宏观快讯 / 舆情因子 / 社区研判）
合成，对 11 个标的逐个给出方向、预期涨跌幅、预测区间、点位区间、置信度与驱动拆解，
外加「明日盘面倾向」和**历史预测命中率回看**。与 01 / 02 / 03B 同一套反陈旧口径：
**引擎里不存放任何点位、日期、新闻事实**，行情缺席就写「今日未获取 —— 本栏不预测」。

### ⚠️ 「未来函数」两种含义，本栏只做第一种

| | 含义 | 本栏 |
|---|---|---|
| ① | **预测未来的函数** —— 用 t 时刻已公开的信息推 t+1 的分布 | ✅ 就是这个 |
| ② | **偷看未来的函数** —— 量化里的 look-ahead bias，回测用了当时拿不到的数据，命中率虚高、上线即失效 | ❌ 四条硬约束钉死 |

四条硬约束（全部有回归测试盯着，见 `tests/test_forecast.py::TestNoLookahead`）：

1. **输入闭合** —— 只读当次构建的四份产物；行情基准日 `base_date` 就是数据里的 `as_of`，引擎里没有任何未来数据入口。
2. **目标日严格在后** —— `target_date = base_date 的下一交易日`（周末顺延），`assert_no_lookahead()` 强制 `target_date > base_date`，不成立就整栏降级、不出预测。
3. **先存档，后结算** —— 预测落盘 `forecast_history.json` 时 `settled=False`；要等**后续某次构建**真的抓到 `as_of == target_date` 的行情，才回填实际涨跌并计分。**当次行情永远结算不了当次预测**，这是结构上的保证，不是约定。
4. **零写死叙事** —— 单测用 tokenize 剥掉注释与文档串后，断言引擎代码里不得出现任何硬编码日期与点位。

### 模型（显式线性合成，逐项可复算）

```
标准化动量  z = 当次涨跌幅 / σ          σ 为该标的的典型日波动（与 quant_pair 共用同一张参数表）
动量项      |z| ≤ 2σ  → +0.30 × z                       延续
            |z| > 2σ  → 超出部分 −0.35 × 超出量           过度延展后的均值回归
联动项      β × carry_z      carry_z = 美股三指均值 / σ(标普)，隔夜映射；美股自身 β=0，不自己预测自己
情绪项      s_senti × 情绪信号          情绪信号 ∈ [−1,+1]（舆情净情感 + 突发风险分 + 社区多空家数）
宏观项      s_macro × 宏观信号          宏观信号 ∈ [−1,+1]（当次快讯标题情感，复用日报同一套中文词库）

μ(σ) = 动量项 + 联动项 + 情绪项 + 宏观项，截断 ±1.5σ
预期涨跌幅 = μ(σ) × σ      预测区间 = 预期涨跌幅 ± 1.0σ      点位区间 = 基准价 × (1 + 区间)

|μ(σ)| < 0.15                 → 震荡（不给方向）
置信度 = 0.30×数据覆盖率 + 0.30×驱动项一致度 + 0.25×信号强度 + 0.15×基准行情完整度
置信度 < 0.35                 → 该标的只报区间、不报方向
明日盘面倾向 = 恒指/恒科/国企按 1.20 / 1.00 / 0.80 加权（再乘各自置信度）的 μ(σ)
```

### 命中率回看（自带成绩单）

结算口径写死在 `settle_history()`：方向预测看符号是否一致；「震荡」看实际涨跌是否在 ±0.5σ 以内
（用 `FLAT_REALIZED` 而不是预测侧的 0.15σ —— 拿 μ 的阈值去卡带噪声的实际收盘等于给自己判不及格）。
另记「实际是否落在预测区间内」与平均绝对误差。**已结算样本不足 10 条时只报样本量、不下命中率结论**；
目标日过去 10 天仍没抓到行情的（假期顺延等）判为**作废**，不计入命中率 —— 宁可样本少，也不硬凑命中。

`forecast_history.json` 是构建产物（在 `.gitignore` 里）。GitHub Actions 每次 run 都是全新 checkout，
**不加缓存的话存档每次为空**，功能不会坏，但「回看」会永远停在「暂无已结算的历史预测」。
打通命中率需要人工应用一次 `docs/forecast-ci-cache.patch`（三个 job 各加一对 `actions/cache` 步骤），
或把 `FORECAST_HISTORY` 指到持久化路径。

### 两端输出与微信字符预算

网页 04 节给完整版（逐标的表 + 驱动拆解 + 三路输入信号 + 回看）。
微信单页有 10 万字符硬上限、95,000 推送门禁，而完整日报各栏本就接近预算上限，
所以 04 栏在推送侧是**预算自适应**的（`tools/wechat_push.py::fit_forecast_block`）：

```
完整版 + 字符配图 → 完整版 → 精简版 + 预测配图 → 精简版 → 一行摘要 → 只留指引
```

落到哪一级由剩余预算决定，但**数字都来自同一份预测**，只是详略不同，两端不会口径打架。
超预算时打印 `✂️ 微信推送 04 栏：…本次采用「…」`，方便在 CI 日志里看到当天发了哪一版。

#### 给 04 栏腾出的字符预算（纯样式瘦身，正文一字未删）

直接挂上 04 栏会把 04 挤成「一行摘要」。与其砍内容，不如先把重复的**标记**削掉 ——
实测全文约四分之一的字符花在逐块重复的内联样式和逐块重复的规则说明上：

| 手段 | 位置 | 说明 |
| --- | --- | --- |
| 字符配图共有样式上提到 `<table>` | `char_charts._rows_html` | 字号/颜色/字体由单元格继承，每个 `<td>` 只留必须逐格不同的几条；柱尾多余空格去掉（表格本就按列对齐） |
| AI 量化块四行要点改 `<br/>` 串接 | `quant_pair.render_wechat` / `render_wechat_list` | 省掉四组重复的 `<div style="margin-bottom:4px;">` |
| 配对口径整篇只印一次 | `quant_pair.rule_note_wechat()` / `rule_note_web()` | 那段规则原先在 20 多个块里逐块重复；现在**网页与微信都只在页脚印一次**（网页由 `{{QUANT_RULE}}` 注入；`render_*(..., show_rule=True)` 可让单独出现的块自带规则） |
| 核心量化指标 / 社区卡片去掉可继承声明 | `tools/wechat_push.py` | 外层已设 `color:#edf2ff`，子元素不必再声明一遍 |

净效果（demo 数据实测）：字符配图 19,408 → 14,935，03 栏 40,556 → 33,020，
**04 栏因此拿到最高一级的「完整版 + 字符配图」（15,221 字符），整页 92,781 < 95,000。**
瘦身前后渲染结果一致，`tests/test_forecast.py` 用两项回归钉住：
规则在微信单页里只能出现一次、04 栏必须拿得到逐标的预测表。

#### 说明文字瘦身：口径只印一次，卡片只留数据

同一句解释原先会在整页重复几十遍（实测「不构成投资建议」61 处、「未校准」62 处、
「规则：价差…」58 处、「由新闻对应文本片段的情绪…」49 处），现在改成「一处口径 + 卡片只留数据」：

| 说明文字 | 原先 | 现在 |
| --- | --- | --- |
| 00 栏 VIX 说明 | 三张说明卡（它测量什么 / 今天怎么读 / 不要误读） | 一行口径（含幅度非方向、Q/P 边界），外部来源与「官方评级」字样一并去掉 |
| 01 栏扫描口径 | 两行长句 | 一行：力量分权重与三档阈值 |
| AI 量化配对规则 | 每块一遍（网页 58 处） | 整页页脚一次（`rule_note_web()` / `{{QUANT_RULE}}`） |
| 48h 三行预测口径 | 每块「（未校准）」「未接实时日历…」 | 页脚口径一次；卡片内只留数值（`OUTLOOK_NOTE`） |
| 三大关注面 / 噪音小节 | 重复力量卡上已有的三行 48h 预测 | `brief=True`：只留策略 / 跨域组合 / 推荐 |
| 核心量化指标口径 | 49 张卡片各印四句（情感 / 事件 / 相关性 / 新颖度） | 03 栏表头统一给一次，卡片只留数值 |
| 字符配图图注 | 附「对照 matplotlib …」的实现说明 | 只留刻度与阈值等读数信息 |

净效果（demo 数据实测）：网页可见正文 91,117 → 72,786 字符（约 −20%），
重复免责句 61 处 → 4 处；回归由 `tests/test_quant_pair.py::TestAnnotationTextIsNotRepeated`
与 `tests/test_vix_daily.py` 钉住（口径只出现一次、卡片不得再铺口径、来源字样不得回流）。

```bash
python3 forecast.py                  # 读仓库内 4 份 json → 文本预测
python3 forecast.py --json out.json  # 导出结构化预测（便于回测/核对）
python3 forecast.py --history        # 预测落盘 + 用当次行情结算已到期的历史预测
python3 forecast.py --review         # 只看历史命中率回看
python3 forecast.py --self-test      # 规则自检（含未来函数污染检测）
python3 -m unittest tests.test_forecast   # 32 项回归
```

建站侧开关：`build_site.py --forecast-history <path>` 指定存档、`--forecast-no-history`
完全不落盘（`--check` 纯校验模式同样不落盘）。推送侧**只读存档**，
落盘只发生在建站那一步 —— 否则 `--dry-run` / `--emit` / `--push` 各跑一次就会把同一条预测重复灌进去。

## 📐 每条内容后的 AI 量化 · 跨域配对交易

网页与微信的**每一条内容后面**都挂一块「AI 量化」：先按正文找到一条量化策略，再给出**恰好两只标的**的组合，最后用当次涨跌幅按该策略做推荐；新增未来 48 小时风险因子、方向/幅度/置信度预测及 48 小时之后综合展望。策略家族是配对交易（相对收益均值回归），规则集中在 `quant_pair.py`，两端渲染共用，不另写一套口径。

### 🧭 本次升级：两标的组合必须是「跨域组合」

域按**资产类别 / 市场**划分（港股 / 美股 / 贵金属 / 能源 / 汇率），两腿必须来自**不同的域**：

```
港股   HSI 恒指 · HSTECH 恒科 · HSCE 国企
美股   SPX 标普 500 · NDQ 纳指 · DJI 道指
贵金属  GOLD 现货黄金
能源   WTI · BRENT
汇率   USDCNH 离岸人民币 · USDCNY 在岸人民币
```

同域价差（恒科/恒指、WTI/布伦特、离岸/在岸人民币）**不再进入策略目录** ——
`DOMAIN` / `is_cross_domain()` / `cross_domain_catalog_errors()` 把这条口径做成可自检的规则，
`select_strategy()` 只在跨域策略里挑，`tape` 兜底也只从跨域组合里取价差最大的一对。
13 条策略全部跨域（组合不重复）：

| 策略 | 跨域组合 | 域对 | 主要主题词 |
|---|---|---|---|
| 港股科技 × 美股科技 | 恒生科技 / 纳斯达克 | 港股 × 美股 | 恒科、科技、半导体、芯片、中概、轮动 |
| 跨市场风险偏好 | 恒指 / 标普 500 | 港股 × 美股 | 美股、隔夜、风险偏好、再平衡 |
| 港股价值 × 美股成长 | 恒生国企 / 纳斯达克 | 港股 × 美股 | 国企、高息、红利、内房、REITs、防御 |
| 美股价值 × 港股科技 | 道琼斯 / 恒生科技 | 美股 × 港股 | 道指、蓝筹、价值股、再通胀 |
| 黄金 × 离岸人民币 | 现货黄金 / USDCNH | 贵金属 × 汇率 | 黄金、美联储、利率、美债 |
| 股金相对价值 | 标普 500 / 现货黄金 | 美股 × 贵金属 | 避险、对冲、地缘、VIX |
| 原油 × 港股价值 | WTI / 恒生国企 | 能源 × 港股 | 原油、油价、OPEC、供应链、输入性 |
| 油价 × 美股成长 | 布伦特 / 纳斯达克 | 能源 × 美股 | 布伦特、运输成本、通胀预期 |
| 避险 × 能源 | 现货黄金 / WTI | 贵金属 × 能源 | 铜锂、稀土、大宗商品 |
| 离岸人民币 × 港股 | USDCNH / 恒指 | 汇率 × 港股 | 人民币、汇差、中间价、贬值 |
| 在岸人民币 × 美股 | USDCNY / 标普 500 | 汇率 × 美股 | 贸易、关税、出口 |
| 港股 × 黄金对冲 | 恒指 / 现货黄金 | 港股 × 贵金属 | 避风港、风险对冲 |
| 汇率 × 能源 | USDCNH / 布伦特 | 汇率 × 能源 | 输入性通胀、进口成本 |

渲染层同步显示域对：网页块 `data-domain-pair="港股 × 美股"` + 「跨域组合：…」，微信块/一行版同样标注；
`python3 quant_pair.py --self-test` 会逐项检查「每条策略都跨域、组合不重复、tape 兜底也跨域」。
49 源社区的默认配对也按社区逐一映射（股票 / 论坛 / 研究 / 期权等不同社区落到不同的跨域组合，避免 49 张卡片共用一对）。

### 🔒 行情不足 → 整段隐藏（不铺「数据不足」）

配对两腿的当次涨跌幅缺任何一条时，本段除了「数据不足 / 不预测方向 / 暂不判断」没有任何信息，
铺在每条内容后面只是噪音 —— 因此**整段不渲染**（网页块 / 微信块 / 迷你版 / 多组列表四处同一口径，
`render_*` 一律返回空串），不是渲染成灰色占位、也不换成一行「数据不足」。

- 判定集中在 `quant_pair.is_hidden(rec)`：`rec['action'] == 'no_data'`（两腿涨跌幅不齐）即隐藏；
  数据层照旧返回 `action='no_data'` / `stance='数据不足'`，构建日志、自检、字符配图与调用方仍可据此判断。
- **同行者一起隐藏**：块的标题、跨域组合、48 小时风险/走势/展望、名录版里的「跨域配对…数据不足」全部不出现
  （配对口径本就不随块出现，整页只在页脚印一次）。
- 构建与推送各自打印一行 `🔒 行情不足：N 处 AI 量化配对已整段隐藏`（`quant_pair.hidden_render_count()` 计数，每次构建归零）。
- 内部排查需要看到这段文案时：`QUANT_SHOW_NO_DATA=1`（默认隐藏；`quant_pair.py --self-test` 覆盖开关的开关）。
- 只有**两腿不齐的那几块**隐藏：行情齐全的内容照旧渲染，所以同一页里通常是「有的隐藏、有的正常」。

> 预测采用可解释的动量收缩 + 配对均值回归启发式集成；波动区间借鉴 GARCH/EWMA 条件波动建模思路。相关背景可参阅 [ARIMA/GARCH 组合管理研究](https://www.diva-portal.org/smash/get/diva2:1895844/FULLTEXT01.pdf) 与 [GARCH 波动预测说明](https://ryanoconnellfinance.com/volatility-estimation-garch/)。**目前不是已拟合的 ARIMA/GARCH/LSTM，也没有在运行时联网调用第三方预测模型**：本模块只有当次涨跌幅和静态典型波动参数，资金流/情绪仅作弱代理，宏观日历未接入；置信度为启发式分数而非校准概率。数据缺失时不猜方向。

```
价差 = 涨跌幅A − 涨跌幅B
z    = 价差 / 残差波动          残差波动 = sqrt(σA² + σB² − 2·ρ·σA·σB)
|z| < 1   观望，不建配对仓
z ≤ −1    做多 A、做空 B（A 相对偏弱）
z ≥ 1     做空 A、做多 B（A 相对偏强）
|z| ≥ 1.8 标准仓，否则轻仓
```

σ / ρ 是策略参数（典型日波动、预设相关系数），不是某一天的行情事实。两腿涨跌幅缺任何一条，就**整段隐藏**（数据层仍标记 `数据不足`，只是不渲染），**不编点位、不回填历史叙事**。

正文里的主题词（原油、黄金、利率、恒科、内房…）用来选哪一对；选不中时，在当次两腿齐全的组合里取价差偏离最大的那一对。频道名只是默认映射，主题词可以覆盖。

覆盖的内容位：01 每条力量 / 关注面 / 做多结论、02 每条宏观快讯与行情快照、03 每个社区卡片、03B 每个标的匹配与风险事件、07 核心结论。微信推送同一批位置。

```bash
python3 quant_pair.py --self-test
python3 -m unittest tests.test_quant_pair
```

## 📡 05 栏：全球 49 大财经 RSS 情报源 (Global 49 Finance RSS Intel Feeds)

新增栏目的数据层是 **`rss_feeds.py`**（唯一事实源）：目录 49 条、抓取、解析、时效过滤、可用性判定与
网页 / 微信两端渲染全在这里，避免两套口径漂移。网页 05 节对应模板占位区 `<!-- RSSFEEDS -->`
（哨兵 `<!-- /RSSFEEDS -->` 保证重复构建幂等），微信推送 05 栏由 `fit_rss_block()` 按剩余预算选详略。

**六大板块与源数（与清单一一对应）**

| 板块 | 源数 | 覆盖 |
|---|---|---|
| 一、国际宏观与顶级政经媒体 | 10 | 经济学人 / FT / WSJ 经济 / 路透 / 彭博 / CNBC / MarketWatch / HBR / NYT / 福布斯商业 |
| 二、美股、科技创新与公司财报 | 10 | WSJ 商业 / ARK / TechCrunch / Barron's / Seeking Alpha / IBD / 雅虎财经 / 福布斯市场 / Benzinga / Motley Fool |
| 三、A 股、港股与大中华区财经 | 11 | 华尔街见闻 / 财联社 / 雪球 / 新浪财经 / 财新 / 东财研报 / 经济观察网 / 巨潮资讯 / 中国证券网 / AASTOCKS / 36 氪 |
| 四、大宗商品、外汇与衍生品 | 8 | 英为财情 / Kitco / Oilprice / FXStreet / DailyFX / 金十 / 汇通网 / Freightos |
| 五、加密货币与 Web3 前沿 | 6 | CoinDesk / Cointelegraph / 律动 / Foresight News / The Block / Bitcoin Magazine |
| 六、顶级量化与独立投资人专栏 | 4 | 霍华德·马克斯（Oaktree）/ Damodaran / QuantStart / Project Syndicate |

**为什么不是照抄 49 个裸域名**
原清单里大量条目只给了站点域名（`economist.com`、`ft.com`、`dj.com`、`rsshub.app` …），域名本身不是订阅地址，
写死成 `https://rsshub.app` 更是同一个地址复制 20 遍。这里逐条落成**可请求的具体端点**，按优先级回退：

1. **原生官方 RSS**（`verified=native`）：如 Economist `finance-and-economics/rss.xml`、CNBC `id/10000664`、
   NYT `Business.xml`、MarketWatch 道琼斯 feed、CoinDesk `arc/outboundfeeds/rss/`、Oilprice `rss/main`；
2. **RSSHub 路由**（`verified=rsshub_doc`，**路径按 RSSHub master 路由表源码逐条核对**）：
   `/wsj/:lang/:category`、`/cls/telegraph`、`/xueqiu/today`、`/wallstreetcn/live/:category/:score`、
   `/eastmoney/report/:category`、`/jin10/:important?`、`/fx678/kx`、`/theblockbeats/:channel?`、
   `/foresightnews/news`、`/oaktree/insights`、`/caixin/latest`、`/sina/finance/rollnews/:lid` 等；
3. **检索载体**（`verified=carrier`）：站点确实没有公开 RSS 的（经济观察网、Freightos 航运指数、
   AASTOCKS、中国证券网、巨潮全市场公告），用 Google News RSS 检索对齐，**如实标注为聚合检索，不冒充原生源**；
4. `verified=needs_check` 的（ARK、Motley Fool、Kitco、QuantStart、Project Syndicate）地址来自公开资料，
   站点改版就可能失效 —— 失效就如实标「未获取」，不猜内容。

RSSHub 是第三方公共服务。**GitHub Actions 的出口 IP 会被 `rsshub.app` 前面的 Cloudflare 直接回 403** ——
实测 run `41168249465` 里 13 条 RSSHub 路线集体「未获取」，而同一条路线在其它出口上是 200 且有最新条目，
说明是**按 IP 拦边缘**，不是路由坏死。所以默认不再只挂官方实例：

| 手段 | 配置 | 行为 |
|---|---|---|
| 多实例轮换 | `RSSHUB_BASE`（逗号分隔，默认 = 官方 + `rsshub.rss.tips` / `rsshub.isrss.com` / `rsshub.cups.moe`，均为探活过的社区镜像） | 403/429 视为边缘拦 IP → **自动换下一个实例**；换成功计 `summary.rsshub.rescued` |
| 本轮熔断 | `RSSHUB_TRIP_AFTER`（默认 3） | 连续多个实例都被拦 → 本轮停止使用 RSSHub，按目录回退原生 RSS / 检索载体，**不让 403 风暴把构建拖死** |
| 换出口 | `RSSHUB_PROXY`（或 `HTTPS_PROXY`） | 走指定出站代理（住宅/其它地区出口）再访问 RSSHub |
| 请求上限 | `RSSHUB_MAX_PER_RUN`（默认 20） | 每轮 RSSHub 请求数上限，避免被公共实例限流拖死构建 |

CI 里用仓库变量 `vars.RSSHUB_BASE` / secret `RSSHUB_PROXY` 注入，可整体覆盖默认链（例如换成自建实例）。

### DeepSeek Search API 检索兜底

有些源的失败是**换节点也救不了**的：RSSHub 路由自身坏死（WSJ 上游 401、雪球要浏览器），
或站点端点已经消失（Seeking Alpha / 福布斯 / Kitco 404、FXStreet / DailyFX 403、QuantStart 503）。
这类源改走 **DeepSeek Search API**（2026-10-17 上线的官方搜索引擎接口）：

| 项 | 值 |
|---|---|
| 端点 | `POST https://api.deepseek.com/v1/tools/search` |
| 请求 | `{query, count, need_content}`，`count` 1–50 |
| 检索式 | `site:<站点> <主题词> lang:<语种> after:<窗口起点>`（如 `site:wsj.com economy markets lang:en after:2026-10-17`） |
| 返回 | `title / url / summary / date(RFC3339) / rerank_score(0–3) / authority(0–4)` |
| 计费 | $0.75 / 1000 次**成功**请求（失败、空结果、超时免费），5 QPS |

用法与环境变量：

- `DEEPSEEK_API_KEY`：key（CI 里读 `secrets.DEEPSEEK_API_KEY`）。**没有 key 就自动跳过**，逐源照旧如实标「未获取」。
- `DEEPSEEK_SEARCH=0`：即使配了 key 也关掉这条兜底。
- `DEEPSEEK_MAX_PER_RUN`（默认 20）：每轮最多调用几次，防失控刷钱（按 20 次算约 $0.015/轮）。
- `DEEPSEEK_MIN_SCORE`（默认 0，即不过滤）：低于该 `rerank_score` 的结果直接丢弃（官方口径 ≥1.5 为高质量）。

**两个检索词字段不能混用**（2026-10-31 事故）：

- `query` 是**老的** `gnews` 检索词。`resolve_endpoints()` 见到它就会**追加一条真实的
  `news.google.com` 端点**，这个源于是可能以 `kind="gnews"` 成功，DeepSeek 兜底根本不触发。
- `deepseek_query` 才是 DeepSeek 兜底的主题词，**不产生任何端点**，只用来拼检索式。

给标了 `deepseek_fallback` 的源写 `query` 会同时造成两件事：CI 有真出口时按 `gnews` 抓到东西
（兜底形同不存在、页面标注失真），而单测在本机没出口时那条端点必然失败、兜底照常触发 ——
就是「本地全绿、CI 红」。`TestDeepSeekSearchFallback.test_marked_sources_do_not_gain_a_gnews_endpoint`
守住这条：标了兜底的源不得出现 `query`，也不得解析出 `news.google.com` 端点。

**口径不变**：只在目录里标了 `deepseek_fallback` 的 **10 个坏死源**上启用；**不替代**已跑通的原生 RSS；
检索回来的条目同样过 14 天时效窗口，`kind="deepseek"` 在页面上如实标注「DeepSeek 检索」，不冒充原生源。

**必须区分两类失败**：`403/429` 是边缘按 IP 拦截，换实例或换出口有意义；而 **`200` + RSSHub 错误页**
（如 WSJ 上游 `401 Unauthorized`、雪球要浏览器）是**路由自身坏死**，换实例无效 —— 这类照旧如实标「未获取」。

**口径（与 02 栏同一套反幻觉原则）**

- **只展示当次真实抓到的条目**：抓不到就整源标「未获取」+ 失败类型（`sources[].error`），
  **绝不回填历史标题、绝不编造「今日重点」**；缺产物时网页 05 节与微信 05 栏都显示「今日未获取」+ 恢复命令。
- **发布日期可解析才进时效窗口**：默认 14 天（`--days` 可收紧），超窗条目在数据层丢弃并计入 `stale_dropped`；
  无日期条目**保留但不冒充最新**（`undated_kept` 计数，渲染永远排在带日期条目之后，明确标「日期未标注」）。
- **不伪造抓取日期**：`generated_at / fetch_date` 取当次真实抓取时刻；快照超过 1 天即判 `stale_snapshot` 走兜底
  （四态：`no_file` / `bad_type` / `no_items` / `stale_snapshot`，与 `macro_data.availability()` 同构）。
- **单源失败不阻断**：49 源逐条 try，失败只标自己；网页 05 节逐源给「当次实抓 · 端点 · 延迟 · 口径说明」。
- **构建期自动补抓**：`build_site.py` 与 `tools/wechat_push.py` 的 **CLI 入口**先调 `rss_feeds.ensure()`，
  产物缺失/写坏/旧快照时就地抓一轮并落盘（`RSSFEEDS_AUTO_FETCH=1|0`，默认只在 CI 里自动开；
  `--rss-fetch` / `--rss-no-fetch` / `--rss-mock` 显式覆盖，`--check` 永不联网）。
  渲染函数（`render_web` / `render_wechat` / `load_rss_feeds`）是**纯读取**，绝不联网 —— 与 02 栏 2026-09-17 事故同一教训。

```bash
python3 rss_feeds.py                      # 抓取 49 源 → rssfeeds_data.json
python3 rss_feeds.py --catalog            # 只打印目录（板块 · 名称 · 端点 · 核验状态，不联网）
python3 rss_feeds.py --mock --text        # 离线回放 + 人读摘要（CI Step Summary 同款）
python3 rss_feeds.py --limit 3 --only CLS,JIN10   # 联调：只抓指定源
python3 rss_feeds.py --self-test          # 目录/解析/时效/兜底/四态自检（不联网）
python3 -m unittest tests.test_rss_feeds  # 32 项回归（零联网）
```

## 🗣️ 社区热评：多项目组合

| 方案 | 覆盖 | 排序与限制 |
|---|---|---|
| [jwlin/ptt-web-crawler](https://github.com/jwlin/ptt-web-crawler) 的论坛结构 | PTT Stock | 本页高推帖的回复；推噓净数属于帖子，**不是回复点赞** |
| [lihkg-crawler](https://github.com/kavv-hub/lihkg-crawler) / [lihkg-api-core](https://github.com/kitce/lihkg-api-core) 的公开接口 | LIHKG 财经台 | 首页样本中最多 3 帖、每帖前 20 楼，按回复点赞排序 |
| [PRAW](https://github.com/praw-dev/praw) | 4 个 Reddit 社区 | 可选官方只读 OAuth；热门帖前 3 个的顶层评论按得分排序 |
| [Scrapling](https://github.com/D4Vinci/Scrapling) 框架模型 | 原有 49 源 | 通用公开页摘录回退，**不声称是评论或热评** |

评论有来源链接、热度口径及真实发布时间；LIHKG / Reddit 仅保留 7 天内的有时间回复，PTT
缺年份时如实显示未知。网页与微信均显示采样范围；排序只在单站样本内进行，不代表全站排行。
详细数据契约、仓库版本与许可证、失败状态及权限配置见
[docs/community-comment-strategies.md](docs/community-comment-strategies.md)。

```bash
python3 community_snapshot.py                      # 到期采样评论 + 公开页摘录（网络需可达）
python3 community_snapshot.py --plan               # 只检查快照是否到期
python3 community_data.py --no-comments            # 调试入口：直接采集，仅公开页摘录
python3 -m unittest tests.test_community_comments tests.test_community_snapshot
```

Reddit 可选：安装 `praw==8.0.3` 并在环境中提供 `REDDIT_CLIENT_ID`、`REDDIT_CLIENT_SECRET`、
`REDDIT_USER_AGENT`。GitHub Actions 的社区采集任务仅在需要采集且三项 Secrets 齐全时安装 PRAW；
缺少权限/依赖不会阻断其他源。不要把凭据提交到仓库。读取频率应符合各平台的政策与配额。

## 🗣️ 49 大社区（14 原有 + 20 前次新增 + 15 本次扩容 · 中英文/日韩德 · 45 类社区）

社区层从 14 源 → 34 源 → **49 源**，每条记录带 `ctype`（社区类型）字段，网页与微信同源同结构；
抓取侧组合多种方案：真实评论 / 热帖回复优先，公开页摘录回退；状态、指标与时间分开标注。
下方扩容和框架段落为历史架构说明；**以 [多方案社区评论抓取](docs/community-comment-strategies.md)
的当前运行口径为准**，不会用行情模板冒充热评。

### 本次扩容 15 源（编号 35–49）

| # | 社区 | 语言 | 类型（`ctype`） |
|---|---|---|---|
| 35 | 集思录 · 低风险投资社区 | 中文 | 中文低风险投资社区 |
| 36 | 小红书 · 理财笔记 | 中文 | 中文种草社交社区 |
| 37 | 抖音 · 财经短视频 | 中文 | 中文短视频社区 |
| 38 | 开盘啦 · 情绪复盘 | 中文 | 中文短线情绪社区 |
| 39 | 理想论坛 · 股票实战 | 中文 | 中文实战论坛 |
| 40 | QuantNet · 量化社区 | 英文 | 英文量化社区 |
| 41 | Elite Trader | 英文 | 英文交易员论坛 |
| 42 | Forex Factory | 英文 | 英文外汇社区 |
| 43 | Reddit (r/CryptoCurrency) | 英文 | 数字资产论坛 |
| 44 | Morningstar · 社区 | 英文 | 英文基金研究社区 |
| 45 | MarketWatch · 社区 | 英文 | 英文财经媒体社区 |
| 46 | 日本 Yahoo! 财经掲示板 | 日文 | 日文社区 |
| 47 | 네이버 금융 종토방 | 韩文 | 韩文社区 |
| 48 | Wallstreet-Online | 德文 | 德文社区 |
| 49 | 阿斯达克财经 · 讨论区 | 繁体中文 | 香港本地财经社区 |

### 抓取口径：Scrapling 框架模型（分析 → 移植 → 落地）

上游 [Scrapling](https://github.com/D4Vinci/Scrapling)（BSD-3-Clause，v0.4.15）用
curl_cffi + lxml + anyio + browserforge + protego 做持久化爬虫；本项目默认路径不要求 pip 安装，
因此把部分**框架模型**移植成纯标准库实现（`scrapling_core.py`），
再由 `community_spider.py` 落到 49 个社区源上：

| Scrapling 上游 | 本项目移植 | 在社区抓取里的作用 |
|---|---|---|
| `Fetcher` / `FetcherSession`（engines/static.py） | `FetcherSession` | 真实浏览器请求头、重试 + 退避、重定向历史、编码识别、代理轮换 |
| `Response`（toolbelt/custom.py） | `Response` | 统一响应体（继承 Selector，直接 `.css()` 解析） |
| `Selector` + `Selectors`（parser.py） | `Selector` / `Selectors` | CSS 候选规则抽标题与正文块；`get_all_text` / `markdown()` |
| `SQLiteStorageSystem` + `auto_match`（core/storage.py、parser.py） | `AdaptiveStorage` | 元素指纹入库；站点改版后尝试按 `difflib` 相似度回捞摘录，不能保证是同一评论 |
| `Spider` / `CrawlerEngine`（spiders/*） | `Spider` / `CrawlerEngine` | 49 源并发抓取、单源失败不阻断、断点续爬 |
| `Scheduler` | `Scheduler` | URL 指纹去重 + 优先级（id 小的源先出结果） |
| `AutoThrottle`（spiders/throttle.py） | `AutoThrottle` | 按响应延迟自适应每域间隔；被封锁按 `Retry-After` / ×2 退避 |
| `RobotsTxtManager`（spiders/robotstxt.py） | `RobotsTxtManager` | `--robots` 时遵从 robots.txt 与 crawl-delay（默认与上游一致：不开） |
| `DevCache` / `CheckpointManager` | `DevCache` / `CheckpointManager` | 开发缓存让本地重复调试不再打站点；`--crawl-dir` 断点续爬 |
| `generate_headers`（toolbelt/fingerprints.py） | `generate_headers` | Chrome/Firefox/Edge 三族 UA + Sec-Fetch-* 全套指纹头 |

装了真实 `scrapling` 包（`pip install "scrapling[fetchers]"`）的机器上，
`community_spider.resolve_backend()` 会自动把上游包接成 **传输层**（curl_cffi 的 TLS 指纹），
解析 / 自适应 / 限速逻辑一字不改；CI 未装包时走 `scrapling_core` 等价移植层，两边同一个口径：

```bash
python3 scrapling_core.py --self-test        # 移植层自检（CSS / 自适应 / 限速 / robots / 指纹）
python3 community_spider.py --self-test      # 爬虫自检（假传输层，零联网）
python3 community_spider.py --limit 5        # 真抓前 5 个源（联调）
python3 community_data.py --backend scrapling --robots --crawl-dir .crawl
```

### 前次新增 20 源（编号 15–34）

| # | 社区 | 语言 | 类型（`ctype`） |
|---|---|---|---|
| 15 | 知乎 · 投资理财话题 | 中文 | 中文问答社区 |
| 16 | 微博财经超话 | 中文 | 中文社交媒体 |
| 17 | 百度贴吧 · 股票吧 | 中文 | 中文论坛 |
| 18 | 淘股吧 | 中文 | 中文短线交易论坛 |
| 19 | 同花顺社区 | 中文 | 中文行情社区 |
| 20 | 格隆汇 · 港股圈 | 中文 | 中文研究社区 |
| 21 | 财联社 · 电报 | 中文 | 中文快讯社区 |
| 22 | 第一财经 · 社区 | 中文 | 中文财经媒体 |
| 23 | Bilibili · 财经区 | 中文 | 中文视频社区 |
| 24 | PTT Stock 板 | 繁体中文 | 繁体中文论坛 |
| 25 | StockTwits | 英文 | 英文交易者社交 |
| 26 | Seeking Alpha | 英文 | 英文研究社区 |
| 27 | Bogleheads Forum | 英文 | 英文长期投资论坛 |
| 28 | Reddit (r/investing) | 英文 | 英文论坛 |
| 29 | Wall Street Oasis | 英文 | 英文机构社区 |
| 30 | Investing.com 讨论区 | 多语言 | 多语言全球论坛 |
| 31 | Yahoo Finance · 社区 | 英文 | 英文行情社区 |
| 32 | Substack · 财经通讯 | 英文 | 英文订阅研究 |
| 33 | Reddit (r/options) | 英文 | 英文衍生品论坛 |
| 34 | FT Alphaville | 英文 | 英文财经媒体博客 |

原有 14 源也补齐了类型：中文行情 / 投资者 / 跨境 / 股吧论坛 / 资讯互动 / 宏观 / 粤语本地论坛 /
机构持仓 / 基民 / 英文论坛 / 英文图表 / 英文研究 / 英文社交。

### 口径：源数不写死，兜底也不缩水

- **网页 03 节**：标题与筛选按钮按读取的 `community_data.json` 重算；缺数据时仍保留完整 49 源目录，
  每源标为「未获取」，不回填静态社区结论。
- **微信推送**：`tools/wechat_push.py` 缺快照时使用 `community_data.offline_dataset()` 生成同构的
  49 条未获取记录，不伪造核验日期或评论。
- **推送前核对**：`EXPECTED_CHANNEL_COUNT = len(community_data.COMMUNITIES)`（= 49）；手动推送检查
  49 条真实核验标记以及四小时快照，允许有效快照跨 UTC 午夜；定时推送对社区问题只告警。

### 微信单页字符预算：49 源怎么塞进 95,000 字符

14 源时 03 栏（完整卡）约 28K 字符；49 源按同样密度要 **94.4K**（见下表「完整版」），加上 01/02/04~07 栏会直接顶穿 100K 硬上限与 95,000 推送门禁。
因此 03 栏继续用 `fit_community_block()` 做**按剩余预算逐级收敛，每一档都保留全部 49 源、跨域配对与抓取标记**：

| 档位 | 每卡内容 | 49 源合计（实测） |
|---|---|---|
| 完整版 | 热评 + 战术研判 + 四行核心量化指标 + 完整 AI 量化块 | 94,420 |
| 标准版 | 热评(220) + 研判(140) + **一行**核心量化指标 + 精简 AI 量化块 | 81,682 |
| 精简版 | 热评(200) + 一行核心量化指标 + 迷你 AI 量化（读数不删，只压样式） | 56,623 |
| 紧凑版 | 热评(150) + 迷你 AI 量化 | 46,053 |
| 名录版 | 一行一名：跨域配对 + 推荐 + 热评(60) + 抓取标记 | 17,194 |

预算里**先给 04 栏（AI 预测）留出「完整版」**，放不下才退到最小的一版——避免 49 源把预测栏挤成一行摘要。
49 源实发通常落在「名录版」（单页实测 72,085 字符，远在 95,000 门禁内），**一源不删**。
CI 日志会打印当天落在哪一档：`✂️ 微信推送 03 栏：49 源社区按剩余预算 … 采用「名录版」`。
迷你 AI 量化（`quant_pair.render_wechat_mini()`）只保留策略 / 跨域两标的 / 推荐与置信度，
48 小时风险与走势三行仍完整保留在**网页版**与其它栏目的完整块里，口径同源、不另写一套。

### 回归防线

```bash
python3 community_data.py --demo              # 49 源离线模板（当天日期）
python3 scrapling_core.py --self-test         # 移植层：CSS / 自适应 / 限速 / robots / 指纹
python3 community_spider.py --self-test       # 爬虫：抽取 / 噪音淘汰 / 封锁降级
python3 -m unittest tests.test_community49    # 14 项：源数 / 类型 / 跨域 / 预算 / 不删源 / 框架接入
python3 -m unittest tests.test_scrapling_core # 26 项：Fetcher / Selector / 自适应 / 引擎
```

## 📊 微信推送的字符配图

配图对照 [matplotlib](https://github.com/matplotlib/matplotlib) 的 Figure / Axes 规矩（[plot types](https://matplotlib.org/stable/plot_types/index.html)）：每张图有标题、刻度、柱端数值；正负用零线分开，构成用一条堆叠柱，不靠颜色区分（微信里颜色会丢）。柱身用方块字形系列 █ ▓ ▒ ░（实心→深→中→浅），情绪序列用 ▁ ▃ ▄ █ 四档高度。

| 推送里的分析 | matplotlib 图种 | 字符图 |
| --- | --- | --- |
| 力量分、做多合成分 | ordered `barh`、发散柱 | 01 栏后面 |
| 当次涨跌幅、配对 z | diverging bar | 02 栏行情快照后面 |
| 宏观各小节条数 | `barh` | 快讯可用时，跟在 02 栏 |
| 社区偏多 / 偏空 / 中性 / 分歧 | stacked bar | 03 栏总览后面 |
| 舆情温度、净情感 | bar / stacked / diverging | 03B 后面 |

数字只来自当次推送同一份行情、社区计数和因子读数。没有数就写「本图不编柱」，不补 0、不回填历史点位。规则在 `char_charts.py`，由 `tools/wechat_push.py` 挂进推送 HTML。

```bash
python3 char_charts.py --self-test
python3 -m unittest tests.test_char_charts
```

## 🛟 02 栏兜底保障 — 「宏观快讯 — 今日未获取」

只要本次拿不到可核验的宏观快讯，02 栏就渲染成下面这段固定文案，**只保留当次实时行情**：

> **◆ 宏观快讯 — 今日未获取**
>
> ⚠️ 未读到 **macro_data.json**：构建步骤 `python3 macro_data.py` 未执行，或公开快讯源当次全部失败。
> 本栏**不再回填历史叙事**（旧文案写死在模板里正是上一版正文长期过期的根因），
> 只保留下方当次抓取的实时行情；宏观结论以每次重建后的最新一版为准。

**四种触发形态**（由 `macro_data.availability()` 统一判定，网页与微信共用同一口径）：

| `reason` | 形态 | 说明 |
|---|---|---|
| `no_file` | 文件读不到 | 构建步骤未执行，或产物未生成（2026-09-17 起：`build_site.py` / `tools/wechat_push.py` 会先走 `macro_data.ensure()` 补抓，正常路径下不该再出现；仍出现说明补抓被关掉了 —— 查 `MACRO_AUTO_FETCH` / `--macro-no-fetch`） |
| `bad_type` | 产物写坏 / 被截断 | 解析出来不是预期的对象结构（此前会让整篇推送崩掉） |
| `no_items` | 窗口内 0 条 | 文件在、结构对，但公开源当次全挂或条目全部超窗被拦截 |
| `stale_snapshot` | 读到旧快照 | `generated_at` 距今超过 `SNAPSHOT_MAX_AGE_DAYS`（默认 1 天），即当次抓取未执行/失败 |

> ⚠️ 修这段兜底之前，**只有 `no_file` 一种会降级**。其余三种会静默渲染出一个空壳栏目，
> 页脚照样盖当天时间戳 —— 和 2026-09-16 那次「页脚写当天、正文停在 8 月」是同一类事故。
> `stale_snapshot` 最危险：条目"看起来"有日期，实际是几天前的旧闻。

**兜底是全栏一致的**：判定为不可用时，快讯条目会在加载处**就地清空**，
而不是只在 02 栏换一段文案 —— 否则旧闻仍会从 01 栏全景扫描的证据链与 07 栏结论里漏出去。
兜底文案下方还会附一行「本次判定」，写明这次为什么没有（含机器可读的 `reason` 与恢复命令），
便于线上判断是抓取没跑、还是源挂了。

## 🗞️ 舆情 / 新闻因子层 — 多量化平台采集 → 日报标的匹配（对外**不显示来源**）

社区热评是"印象"，量化要的是**可回测的因子**。本层从多家量化平台采集**现成的舆情 / 新闻情感因子**，
合成成 5 个可直接入模的因子，并把采集到的每条新闻**匹配到日报正文的行情标的与主题**后展示，
接入同一条日报管线（页面 03B 节 + 微信 03B 节点）：

```bash
python3 tools/probe_sentiment_apis.py --mock     # ① 接口接入实测 + 6 维打分 → api_probe_report.json + docs/sentiment-api-eval.md（内部档案）
python3 sentiment_nlp.py --self-test             # ② 自建中文金融词库自检（否定/程度/风险词规则）
python3 sentiment_match.py --self-test           # ②′ 标的匹配 + 来源脱敏自检（采集关键词是否对得上日报标的）
python3 sentiment_adapters.py --mode mock        # ③ 11 个接口逐个解析校验（零联网）
python3 sentiment_factors.py --mock              # ④ 离线回放：采集 → 合成因子 → 标的匹配 → sentiment_data.json（CI/本地默认用这条）
python3 sentiment_factors.py --live              # ④′ 境内出口 + 凭据的联网实测（优先平台现成因子，缺失时自动降级）
python3 build_site.py                            # ⑤ 建站时把「因子读数 + 标的匹配」注入 report.html 的 <!-- SENTIMENT_LIST --> 标记
```

- **🔒 对外展示策略（本次新增）**：网页与微信推送的 03B 节**不显示数据来源** —— 平台名 / 接口 ID / 域名 / SDK /
  凭据与依赖报错由 `sentiment_match.redact()` 统一遮成「量化平台」，逐源明细表默认不渲染，
  「量化平台现成舆情/新闻因子接入评测（9 阶段实测）」评分矩阵默认隐藏；
  来源明细只保留在内部：`sentiment_data.json`（构建产物，不入库）的 `sources[] / series[].source / news[].source`
  与本节表格、`docs/sentiment-api-eval.md`（内部评测档案）。
  临时恢复内部视图：`SENTIMENT_SHOW_SOURCE=1`（来源明细）、`SENTIMENT_SHOW_API_EVAL=1`（评测矩阵）。
- **🎯 采集 → 匹配 → 显示（本次新增）**：`sentiment_match.py` 用与 `market_data.py` 同一套 key 的标的表
  （恒指 / 恒科 / 国企 / 标普 / 纳指 / 道指 / 黄金 / 原油 / 人民币 + 美联储、内房、高息防御、地缘供应链 4 个主题）
  做关键词匹配（标题命中权重 0.4 / 正文 0.15），复用 `sentiment_nlp.aggregate()` 的同一套因子口径，
  产出 `sentiment_data.json` 的 `matches` 字段：每个标的的命中条数、净情感、负面占比、风险分、舆情温度与代表新闻（不含来源），
  以及整体匹配率与未匹配条数；检索型接口的采集关键词同样取自该表（`search_keywords()`），保证"采回来的"和"要显示的"对得上。

- **因子口径**：`NET_SENTI` 净情感强度 = Σ(情感×权重)/Σ权重 ∈[-1,1]；`NEG_SHARE` 负面舆情占比；
  `NEWS_HEAT_Z` 新闻热度 Z 值（今日条数对近 20 日均值标准化）；`SENT_TEMP` 市场舆情温度计 = 50+35·net+12·tanh(z/2)−18·neg_share ∈[0,100]（≥72 极度亢奋 / ≥60 偏热 / ≥45 中性 / ≥32 偏冷 / ≥20 恐慌）；`EVENT_RISK` 突发事件风险分（立案/处罚/造假/违约…加权，截断 0-100）。
- **打分规则可解释**：命中金融情感词按词权计分 → 程度副词乘权（大幅×1.5 / 小幅×0.7…）→ 否定字（不未无没非失否难）在情感词前 3 字内则极性翻转打 8 折 → 按发布时间 24h 半衰期指数衰减加权；平台已给 `sentiment` 时**优先采用平台口径**（结果里标 `source_provided`），纯标准库、可复现。
- **实测结论（仓库内部档案，详见 `docs/sentiment-api-eval.md`；不在网页/推送中展示）**：

| 平台 | 现成舆情/新闻因子 | 关键接口 | 时效 / 历史 | 口径提醒 |
|---|---|---|---|---|
| 米筐 RQData | ✅ 最贴近"现成因子" | 私有 pip 源装 `rqdatac` + `rqdatac_news` → `rqdatac.news.get_stock_news()` | 日内每 30 分钟；2017 至今 | `news_emotion_indicator`(±1/0) + 正/中/负 weight + `company_relevance` + 公司层情感，拿到即可入模；需商务开舆情数据包 |
| 聚宽 JQData | ⚠️ 名不副实 | `get_factor_values(['VOL5','VOL20','AR','BR','ARBR','ATR14',…])`；舆情仅 `finance.CCTV_NEWS` / 雪球热度 / 百度因子 | 因子 T+1 05:00；热度 03:00；新闻联播 20:30 | 聚宽"情绪因子"= 量价换手类，**不是新闻情感**；试用账号「因子和特色数据：无」；`get_query_count()` 可当配额探针 |
| 掘金量化 | ❌ 不提供 | `gm.api`：`history/current/stk_get_*/fnd_*`（无新闻·无情感） | 行情实时 | 官方 FAQ：指标数据需自行设计实现；SDK 依赖本地掘金终端代理 → CI 完全不可用；舆情必须外挂 |
| 优矿 Uqer | ✅ 免费层最划算 | `DataAPI.NewsSentimentIndexGet→sentimentIndex`、`NewsHeatIndexGet→heatIndex`、`NewsByTickersGet` | 指数日更；新闻 2004-10-28 起 | 现成日频舆情因子，两个数即可入模；站点迭代放缓，字段需实测复核 |
| Tushare Pro | ⚠️ 只给文本 | `POST api.tushare.pro {api_name:'news'/'major_news'/'cctv_news'}` | 准实时；6–8 年 | 需单独开权限；`news` 单次 1500 条；配合自建词库才成因子 |
| 免费兜底 | 热度可用 / 情感弱 | 东财千股千评（关注指数）、金十微博人气、东财 search-api 新闻、数库情绪指数 | 日更 / 小时级 | 东财只有热度无极性；数库端点疑似随官网改版下线，必须降级 |

- **落地建议**：有预算 → 米筐（唯一"给到即入模"）；性价比 → 优矿；零成本 → 东财热度 + Tushare/东财文本 + 自建词库；聚宽只补量价情绪因子；**掘金不承担舆情**。
- **境内出口硬约束**：聚宽 `dataapi.joinquant.com` 等站点对非中国大陆 IP 直接拒绝访问，GitHub 海外 runner 跑不通 → CI 里 `--live` 失败即自动降级 `--mock`（页面与推送显示"降级说明"，绝不阻断 09:00 定时任务）；要拿真实因子请在境内执行器（自建机 / 境内 Actions runner）跑 `--live` 并配好凭据。
- **凭据环境变量**（缺失即跳过该源，不算故障）：`JQ_MOBILE`/`JQ_USERNAME` + `JQ_PASSWORD`（聚宽）、`RQDATA_USER`/`RQDATAC_USER` + `RQDATA_PASSWORD`/`RQDATAC_PASSWORD`、`RQDATA_TOKEN`（米筐）、`UQER_TOKEN`（优矿）、`TUSHARE_TOKEN`（Tushare）、`GM_TOKEN`（掘金，仅行情）；用 `python3 sentiment_sources.py` 查看逐源凭据与依赖状态。
- **CI 接线**：`.github/workflows/m.yml` 的三个 job（deploy / wechat / daily）都需在 `build_site.py` 之前生成 `sentiment_data.json`
  （先 `tools/probe_sentiment_apis.py --mock` 出评测矩阵（内部档案，不对外展示），再 `sentiment_factors.py --live`，取不到数据自动降级 `--mock`）。
  本次新增的 `python3 sentiment_match.py --self-test`（标的匹配 + 来源脱敏自检）需加进 deploy job 的「🧪 舆情层自检」步骤；
  若当前 GitHub 连接未授予 `workflows` 权限、CI 改动无法随 PR 推送，仓库内备好了补丁：`git apply docs/sentiment-ci-workflow.patch`
  后自行提交即可（不改 workflow 也不影响本地/境内执行器跑舆情因子与匹配展示）。
- **单源失败降级**：11 个源任一失败只把自己标成 `ok=false` 并计入 `summary.failed`，其余源继续供数；全源失败时温度计回退 50（中性）并标注 `degraded`，页面与推送照常构建。

## 结构

| 文件 | 说明 |
|---|---|
| `market_data.py` | **动态行情抓取**：多源回退抓取最新行情，生成 `market_data.json`（构建产物，不入库） |
| `sentiment_data.json` / `sentiment_history.json` | 舆情因子当日结果与新闻条数历史（供 `NEWS_HEAT_Z` 基线），均为构建产物，不入库 |
| `macro_data.py` | **宏观/财经快讯抓取**：8 个免密钥公开源（Google News RSS 中英分主题 / 美联储官方 RSS / 东财检索）→ 归一去重 → **时效过滤** → 按 02 栏五个小节归类，生成 `macro_data.json`（构建产物，不入库）；`--days` 收紧窗口、`--mock` 离线回放 `tests/fixtures/MACRO_MIX.json`、`--offline` 沿用上次结果、`--text` 输出 CI Step Summary；单源失败不阻断。另导出 `availability()`（四种不可用形态的统一判定）与 **`ensure()`**（构建期补抓：产物缺失/写坏/旧快照时就地抓一轮并落盘，被 `build_site.py` 与 `tools/wechat_push.py` 调用，因此不改 CI workflow 也能拿到快讯；`MACRO_AUTO_FETCH=1|0` 控制开关，默认仅 CI 内自动开） |
| `macro_data.availability()` | **02 栏兜底口径的单一事实源**：判定本次到底有没有可用快讯，返回 `reason` ∈ `no_file`（文件读不到）/ `bad_type`（产物写坏被截断）/ `no_items`（窗口内 0 条，公开源全挂或全部超窗）/ `stale_snapshot`（读到前几天构建的旧快照，超过 `SNAPSHOT_MAX_AGE_DAYS=1`）。网页与微信两端共用，避免两套渲染各写一套口径而漂移 |
| `tests/test_macro_data.py` | 快讯层测试（16 项，零联网）：RSS/JSONP 解析、跨源去重、分类路由（IMF+关税 必须落宏观而非大宗）、超窗与无日期拦截、`--mock` 输出契约 |
| `tests/test_wechat_push_macro.py` | 宏观快讯渲染回归（推送 02 栏 + 网页 02 节，9 项）：必须渲染当次快讯且每条带日期；**四种不可用形态都要落到同一段「今日未获取」兜底文案**；过期快照不得从 01 / 07 栏漏出；网页 `MACROLIST` 注入幂等；正文/注入区禁止再出现 628.69 亿、8 月 12 日、25,440.17 等写死历史内容 |
| `rss_feeds.py` | **49 大财经 RSS 情报源（05 栏）**：唯一事实源 —— 目录 49 条 / 六大板块 / 每条落成具体可请求端点（原生 RSS → RSSHub 路由 → Google News 检索载体，`verified` 标注核验状态）；抓取（超时、逐端点回退、RSSHub 实例可换、每轮请求上限）、RSS2/Atom/RDF 解析、时效过滤（超窗丢弃、无日期垫底不冒充最新）、`availability()` 四态判定、`ensure()` 构建期补抓（`RSSFEEDS_AUTO_FETCH`）、网页 `render_web()` 与微信 `render_wechat(level)` 两端渲染。`--catalog` / `--mock` / `--offline` / `--only` / `--text` / `--self-test` |
| `docs/rss-49-feeds.md` | **49 源目录与端点核对表**（`python3 rss_feeds.py --catalog-md docs/rss-49-feeds.md` 生成，禁止手改）：六大板块逐源列语种 / 主端点 / 回退端点 / 核验状态 / 口径说明；`tests/test_rss_feeds.py` 断言文档与 `CATALOG` 不漂移 |
| `tests/test_rss_feeds.py` | **05 栏回归（32 项，零联网）**：目录就是那份 49 源清单（10-10-11-8-6-4、编号 1..49、无裸域名、必备源齐全）、三种报文解析与端点回退、超窗丢弃 + 无日期永不冒充最新、四种不可用形态统一兜底（缺产物/写坏/0 条/旧快照，且**不得出现任何历史标题**）、网页 `RSSFEEDS` 注入幂等且不串进 02 节、微信 05 栏预算降级（完整→标准→名录→缩略）**一源不删**且整页仍在 95,000 门禁内、渲染路径绝不联网、`build_site.py` 端到端补抓落盘 |
| `community_data.py` | **社区数据层**：49 源经多方案评论采样与 Scrapling 公开页回退生成记录；失败标为未获取；`reanalyze_snapshot()` 根据已存文本重算，`offline_dataset()` 提供 49 源未获取兜底。 |
| `community_snapshot.py` | **四小时采集快照**：到期采集、原子落盘，失败也限频；页面和推送共用快照，运行期文件不入库。 |
| `sentiment_sources.py` | **接口注册表**：11 个量化平台/公开源舆情·新闻因子的能力口径（端点、字段、时效、历史、额度、成本、局限）+ 因子定义 + 9 个评测阶段与 6 维评分权重；`python3 sentiment_sources.py` 打印清单与凭据/依赖状态 |
| `sentiment_adapters.py` | **接入适配器**：每源一个 `call_*`（live 取数）+ `parse_*`（报文 → 统一结构 `{news, series, meta}`），全部纯标准库；`--mode mock` 用 `tests/fixtures` 录制报文离线校验解析链路 |
| `sentiment_nlp.py` | **自建情感层**：中文金融词库 + 否定/程度修饰 + 时间衰减 → `score_text()`，`aggregate()` 合成 `NET_SENTI / NEG_SHARE / SENT_TEMP / EVENT_RISK`；`--self-test` 自检 |
| `sentiment_match.py` | **匹配 + 脱敏层（新增）**：日报标的/主题关键词表（与 `market_data.py` 同一套 key）、采集关键词 `search_keywords()`、`match_news()/build_matches()` 产出 `matches`、`redact()/status_line()/collection_summary()` 保证对外输出不显示来源；`--self-test` 自检 |
| `sentiment_factors.py` | **因子合成管线**：分层取数（平台现成因子优先 → 免费热度/文本 + 自建词库）→ `sentiment_data.json`（构建产物，不入库）；`--live` / `--mock` / `--offline`，任何源失败都不阻断 |
| `tools/probe_sentiment_apis.py` | **接入实测探针**：依赖→网络→鉴权→取数→字段→时效→覆盖→延迟→额度 9 阶段短路判定 + 100 分制打分 → `api_probe_report.json` 与 `docs/sentiment-api-eval.md` |
| `docs/sentiment-api-eval.md` | **评测矩阵（内部档案）**：结论速览 / 能力矩阵 / 评分明细 / 逐源明细，由探针自动生成；**不在网页与微信推送中展示**（对外不显示来源） |
| `tests/test_community49.py` | **49 源 + 跨域组合 + 框架接入回归（14 项，零联网）**：49 源 id/key/名称唯一、本次扩容 15 源的语种与类型覆盖、每源模板/研判/量化指标齐全且带当次日期、`offline_dataset()` 与 live 同构、抓取入口确实走 `community_spider` 且 49 源逐条有抽取规则、策略目录与 49 张社区卡片全部跨域、网页模板不得再写死「14 平台 / 源数 6-3-3-2」、`{{CD_01}}`–`{{CD_49}}` 占位符齐全、微信推送 49 条「最新读取」标记齐全且仍在 95,000 门禁内、预算降级时**一源不删** |
| `scrapling_core.py` | **Scrapling 框架模型移植（纯标准库，API 与上游一一对应）**：`FetcherSession`/`Response`（浏览器指纹头、重试退避、重定向历史、编码识别、代理轮换）、`Selector`/`Selectors`（CSS 子集、`get_all_text`、HTML→Markdown、选择器生成）、`AdaptiveStorage`（元素指纹入库 + `difflib` 相似度回捞）、`Scheduler`/`AutoThrottle`/`RobotsTxtManager`/`DevCache`/`CheckpointManager`/`Spider`/`CrawlerEngine`/`CrawlStats`。`python3 scrapling_core.py --self-test` |
| `community_spider.py` | **社区爬虫（49 源）**：公开页 CSS 摘录与自适应回捞，调用评论适配器并优先采用有来源的评论样本；失败不编造内容。可用 `--backend scrapling` 切换可选静态传输层。 |
| `community_comments.py` | **多方案评论适配器**：PTT 列表/回复、LIHKG 财经台 JSON 与可选 PRAW Reddit API；限量采样、去重、标注各自的热度、时间和来源。 |
| `tests/test_community_comments.py` | **离线回归**：三种方案、登录/限流与 robots 降级、排序、时效、样本口径、原帖链接与网页/微信渲染。 |
| `tests/test_sentiment.py` | 舆情层测试（38 项，零联网）：`python3 -m unittest discover -s tests`；含「03B 对外输出不得出现任何来源痕迹」与「采集结果必须匹配到日报标的」两类回归 |
| `quant_pair.py` | **AI 量化 · 跨域配对交易**：每条内容后选一条策略、给出**恰好两只跨域标的**（港股 / 美股 / 贵金属 / 能源 / 汇率，同域价差不算组合）、用当次涨跌幅做均值回归推荐；行情不足（两腿涨跌幅不齐）则**整段隐藏**，不渲染「数据不足」段（`is_hidden()` 判定，`QUANT_SHOW_NO_DATA=1` 可临时恢复排查）。策略目录 13 条全部跨域、可自检（`cross_domain_catalog_errors()`）。网页与微信共用（完整 / 精简 / 迷你三档渲染，迷你版供 49 源同一页使用）。`python3 quant_pair.py --self-test` |
| `char_charts.py` | **微信字符配图**：把力量分、涨跌幅、配对 z、社区构成、舆情读数、**AI 预测（预期涨跌幅发散柱 + 置信度柱）与预测命中率回看**画成 matplotlib 同款的柱状/发散/堆叠字符图。缺数据不编柱。`python3 char_charts.py --self-test` |
| `forecast.py` | **04 栏「AI 预测 · 未来函数」推理引擎**：四路当次数据 → 下一交易日逐标的预测（方向 / 预期涨跌幅 / 预测区间 / 点位区间 / 置信度 / 驱动拆解）+ 明日盘面倾向 + 历史命中率回看；「未来函数」只取「预测未来」之义，目标日严格晚于行情基准日、预测**先落盘后结算**，当次行情结构上不可能结算当次预测（杜绝 look-ahead bias）。纯标准库纯函数、不联网，构建期由 `build_site.py` 与 `tools/wechat_push.py` 直接调用（**无需改 CI workflow**）。`python3 forecast.py` 文本摘要、`--json` 导出、`--history` 落盘+结算、`--review` 只看回看、`--self-test` 规则自检 |
| `forecast_history.json` | **AI 预测存档**（构建产物，不入库）：每条预测带 `base_date / target_date / direction / mu_pct / low_pct / high_pct / confidence / settled`；后续构建抓到目标日行情后回填 `actual_pct / hit / in_band / error_pct`。CI 跨运行持久化见 `docs/forecast-ci-cache.patch`（不应用则回看永远 0 样本，功能不受影响）。路径可用 `FORECAST_HISTORY` 覆盖 |
| `tests/test_forecast.py` | 04 栏回归（35 项，零联网）：**未来函数口径**（目标日必须晚于基准日、周末顺延、当次行情结算不了当次预测、只在目标日行情到位时计分、过期判作废不凑命中、同日重建不灌水）、预测跟着数据走（多/空/横盘结论不同、>2σ 均值回归而非外推、美股不通过联动项自己预测自己、驱动项能复算出 μ）、缺数据降级为「本栏不预测」、引擎代码不得写死日期与点位、网页 `FORECAST` 注入幂等、**微信预算自适应且加了 04 栏后仍在推送门禁之内**、**配对规则在微信单页只印一次而非逐块重复**、**压缩既有栏目后 04 栏拿得到逐标的预测表** |
| `docs/forecast-ci-cache.patch` | **待人工应用的 workflow 补丁**（GitHub App 无 `workflows` 权限）：三个 job 各加一对 `actions/cache` restore/save 步骤持久化 `forecast_history.json`，打通 04 栏的命中率回看 |
| `panorama.py` | **01 栏「每日全球全景扫描」推理引擎**：把当次的 `market_data.json` + `macro_data.json` + `sentiment_data.json` + `community_data.json` 合成为**推动股价的 5 大力量**（重点 / 次要 / 噪音 · 利好 / 利空 / 中性 · 0~100 力量分）、三大关注面小结（宏观事件 / 板块轮动 / 情绪变化）与**是否可以做多**的合成分结论，并在每条力量后挂 `quant_pair` 的配对推荐；纯标准库纯函数、不联网不落盘，构建期由 `build_site.py` 与 `tools/wechat_push.py` 直接调用（**因此无需改 CI workflow**）。`python3 panorama.py` 打印文本摘要、`--json` 导出结构化结果、`--self-test` 规则自检 |
| `tests/test_panorama.py` | 01 栏回归（13 项，零联网）：5 大力量与栏目要素齐全、多/空/横盘三种行情结论必须不同、噪音不计入做多合成分、突发风险分下调做多结论、四路数据缺失时降级为「本栏不编故事」、网页 `PANORAMA` 注入幂等、旧栏目名与写死历史内容不得回归 |
| `build_site.py` | **动态建站**：把 `report.html` 模板中的 `{{占位符}}` 替换为最新行情/抓取日期/时间戳（含 03 节社区口径 `{{COMMUNITY_TOTAL}}` / `{{COMMUNITY_TYPE_TOTAL}}` / `{{CF_*}}`：源数、类型数与多空家数现算，缺数据才退回模板 14 源口径），把 `panorama.py` 的全景扫描注入 01 节 `<!-- PANORAMA -->` 占位区（哨兵 `<!-- /PANORAMA -->` 保证幂等），把 `community_data.json` 的 49 条最新研判注入 `<!-- COMMUNITY_LIST -->` 标记，把 `macro_data.json` 的快讯注入 02 节 `<!-- MACROLIST -->` 占位区（缺数据→「今日未获取」，回填哨兵 `<!-- /MACROLIST -->` 保证幂等），并把「因子读数 + 标的匹配（不含来源）」注入 `<!-- SENTIMENT_LIST -->` 标记，并把 `forecast.py` 的下一交易日预测注入 04 节 `<!-- FORECAST -->` 占位区（哨兵 `<!-- /FORECAST -->` 保证幂等，同时把当次预测写进 `forecast_history.json`；`--forecast-no-history` / `--check` 不落盘） |
| `report.html` | 报告**模板源文件**（**电竞指挥中心 × 战术 HUD** · 深色海军蓝底 + 霓虹青 / 能量绿 / 战术紫 / 洋红高亮 · 响应式战情卡片），内含"手动推送"按钮与 01 节 `<!-- PANORAMA -->`、`<!-- COMMUNITY_LIST:BEGIN/END -->`、02 节 `<!-- MACROLIST -->`、04 节 `<!-- FORECAST -->` 动态注入标记；仓库中始终保持模板版本，构建产物不提交（误提交构建产物时 `git checkout -- report.html` 恢复） |
| `tools/wechat_push.py` | 微信推送工具：读取 `market_data.json` + `community_data.json` 双动态数据，转为微信兼容的**深色电竞 HUD 单页内联样式 HTML**，以深海军蓝 + 电光青 / 荧光绿 / 战术紫 / 警戒红呈现，经 PushPlus **一对一**专属推送（仅 Token 本人接收）；**03 栏由 `fit_community_block()` 为 49 源按剩余预算选详略**（完整版 → 标准版 → 精简版 → 紧凑版 → 名录版，每一档都保留全部源、跨域配对与「最新读取」标记）；04 栏由 `fit_forecast_block()` 在「完整版 → 精简版 → 一行摘要 → 只留指引」之间选一版；两者合计仍落在 95,000 推送门禁之内 |
| `.github/workflows/m.yml` | CI：动态抓取行情+校验+社区+宏观快讯 → 动态建站（三注入） → 部署 Pages + 一键触发微信单页推送 + **每天北京时间 09:00 定时自动推送** |
| `docs/macro-ci-workflow.patch` | **待人工应用的 workflow 补丁**（GitHub App 无 `workflows` 权限）：三个 job 各加 `macro_data.py` 抓取步骤（失败不阻断、摘要进 Step Summary）+ `verify_quotes.py` 门禁（deploy 非阻断并公开 `verify_report.json`；wechat/daily FAIL 直接阻断推送） |

## 页面风格系统（电竞指挥中心 × 战术 HUD）

`report.html` 与微信单页推送统一使用面向桌面和移动端的**游戏战情界面**：把市场数据当作实时情报，把章节导航、情绪筛选与推送操作设计成可交互的战术面板；霓虹装饰保持克制，不牺牲正文可读性。微信版全部使用内联样式，适配 PushPlus / 微信阅读。

- **视觉基调**：深色竞技场 / 指挥中心。海军蓝近黑底、细网格与扫描线、切角徽章、发光状态灯和战术卡片，营造电竞 HUD 氛围。
- **字体系统**：标题使用 Rajdhani 的竞速数字感，中文回退到 Noto Sans SC；Space Mono 用于状态、时间戳与数据标签，正文保持清晰易读。
- **电竞配色**：
  - 背景与面板：深海军蓝 `#060811` / `#0d1220`，正文冷白 `#edf2ff`。
  - 主操作与上涨信号：电光青 `#4fe5ff`；重点指标：能量荧光绿 `#b6ff4a`。
  - 战术分组：紫色 `#9673ff`、洋红 `#ff4d9a`；下跌 / 风险信号：警戒红 `#ff6b7d`。
  - 不同颜色同时配合文字标签与边框，不单靠颜色表达多空；键盘焦点、高对比文字与减少动态效果偏好均有支持。
- **交互细节**：顶部 LIVE 状态与 S+ 等级徽章、章节战术导航、按多空立场区分的情报卡片、霓虹筛选按钮和切角推送按钮；手机端自动折叠为双列战情信息。
- **标题与署名**：网页标题为「章鱼 AI·舆情004」，**微信推送标题为「章鱼 AI·舆情004」**（推送卡片顶部大标题同步使用该名称），副标题「全网 AI 调研境内境外数据，由多个大模型混合部署」，**标题去除 pushplus 与时间戳**。正文末尾署名：**作者：章鱼 ai · 仅供参考，分析研究**，并附多模型协同说明。
- **01 节每日全球全景扫描**：栏目为「**每日全球全景扫描** (Daily Global Panorama Scan)」——扫一遍今天全球市场，总结**推动股价的 5 大力量**，重点关注**宏观事件 / 板块轮动 / 情绪变化**，逐条标注**哪些是重点、哪些是噪音**与**如何利好利空**，最后给出**是否可以做多**的结论。**网页与微信推送两端同步呈现**（`report.html` 的 `<!-- PANORAMA -->` 注入区与 `tools/wechat_push.py` 的 `panorama_block`）。

## 微信推送 (PushPlus · 一对一专属单页完整版 · 49 源动态 · 跨域配对)

- **推送标题**：「章鱼 AI·舆情004」。三处保持同步 —— `tools/wechat_push.py` 的 `TITLE` 常量（命令行 `--push` / `--emit` / `--embed`）、`report.html` 内嵌负载 `wechat-parts` 的 `title`、网页按钮 `manualPush()` 的 `pushTitle`；推送卡片顶部大标题亦为同一名称。
- **一对一专属推送**：默认仅推送给 **Token 对应的微信本人**，请求不携带 `topic`，无需创建或关注群组。
- **页面只推一个微信页**：点击"手动推送"立即发送**单页完整微信卡片**，开头 VIX 专栏、既有完整章节与 49 大社区论坛研判一次性送达，无需拆条分发与 15s 等待；49 源按剩余字符预算自动选详略（见下），**任何一档都不会删源**。
- **⏰ 推送前时间核对**：每一次推送前均重新抓取行情+社区数据，并读取当前时间；标题与正文中的"生成时间 / 时间核对"等全部时间戳**实时刷新为最新时间**后再发送（网页按钮与命令行推送均已内置）。
- **📅 推送前频道核验**：检查 49 条「抓取核验」标记是否为当天；这只证明当天执行了抓取检查，
  不证明内容是全站最新。标记缺失或过期时手动推送拒绝，定时推送仅警告。
- **🧪 推送前全来源数据准确性校验** (`verify_quotes.py`)：**每一次推送前**对 `market_data.json` 全部 12 个行情标的（含 VIX）做 **Yahoo 官方口径 × Stooq 实时 × Stooq 历史日线重算 × ECB 汇率** 多来源交叉校验——内部自洽 (涨跌额/涨跌幅 ↔ last/prev_close)、行情日期健全性（不超前/不陈旧）、点位与涨跌幅逐源比对；**≥2 个独立来源族彼此一致但与流水线矛盾、或与主源官方口径矛盾即判定 FAIL 并阻断推送** (CI 中 FAIL 时工作流直接终止，绝不把错误数字推给读者)。单源不可达仅告警不阻断，校验报告公开在 `_site/verify_report.json`。背景：2026-09-16 恒指当日 +0.19% 曾被误算成 −2.22% 并推送，本机制保证同类错误在推送前被拦截。命令行可用 `--skip-verify` 跳过（不推荐）、`--verify-strict` 收紧为 WARN 也阻断。
  > ⚠️ 推送门禁内置于 `wechat_push.py --push`（FAIL 即 exit 5，不依赖任何工作流改动即生效）。CI 侧的
  > fail-fast 校验步骤与 `verify_report.json` 公开因 GitHub App 凭证无 `workflows` 权限，改以补丁交付：
  > `git apply docs/macro-ci-workflow.patch` 后生效（三处：wechat/daily 任务抓取行情后的**阻断式**校验、
  > deploy 任务的 `|| true` **非阻断**校验 + `_site/verify_report.json` 随站点公开）。
- **命令行推送**：`python3 tools/wechat_push.py --push`
- **定时自动推送**：`python3 tools/wechat_push.py --push --scheduled`
- **验证转换效果**：`python3 tools/wechat_push.py --dry-run`
- **重新内嵌内容**：报告更新后，运行 `python3 tools/wechat_push.py --embed`（幂等）。

Token 维护在 `report.html` 的 `PUSHPLUS_TOKEN` 常量中，网页按钮与推送工具共用。群组编码 `PUSHPLUS_TOPIC` 默认为 `''`（一对一专属推送，网页按钮与命令行工具共用该常量）。命令行按 `--topic` > 环境变量 `PUSHPLUS_TOPIC` > 页面常量解析；如环境中曾设置群组编码，请清除该变量，或使用 `--topic ''` 强制一对一（空值也会覆盖环境变量）。

## 🖥️ 极趣墨水屏同步 — E-Ink Sync (新增 · 复用 k-macao/10_sync 接口)

> **本次新增**：读取 `k-macao/10_sync` 仓库接口文件，自动运行任务 yml 时同步推送到极趣墨水屏。

### 接口来源

- 原仓库：[k-macao/10_sync](https://github.com/k-macao/10_sync) — `main.py` 含 Zectrix 推送核心 `push_image()`、400×300 渲染与财新/东财多源回退
- 本仓库封装：`tools/zectrix_client.py` — 从 `main.py` 提取的可复用客户端（推送+渲染+数据源），去业务绑定，保留离线兜底
- 统一入口：`tools/eink_push.py` — 支持 `report`/`news`/`both` 三模式，顶栏统一 **章鱼 AI·舆情004**

```bash
# 本地预览（无需密钥，生成 page_*.png）
python3 tools/eink_push.py --mode report --dry-run   # 03 日报浓缩 4 页：全景/行情/预测/宏观+舆情
python3 tools/eink_push.py --mode news --dry-run     # 复用 10_sync：财新+东方财富 4 页
python3 tools/eink_push.py --mode both --dry-run     # 报告 1-2 + 新闻 3-4

# 真实推送（需配置密钥）
export ZECTRIX_API_KEY=xxx
export ZECTRIX_MAC=AA:BB:CC:DD:EE:FF
python3 tools/eink_push.py --mode report --pages 1,2,3,4
```

### 工作流自动同步

`.github/workflows/m.yml` 已集成 E-Ink 同步（4 个 job）：

| Job | 触发 | E-Ink 动作 |
|-----|------|------------|
| `deploy` | push/main | dry-run 预览，验证渲染链路，产物进 `_site/page_*.png` |
| `wechat` | push / 手动 | 真实推送（`report`/`both` 可选），密钥缺失自动降级 dry-run |
| `daily` | 每天 09:00 北京时间 | 推送微信 + 同步墨水屏（report 模式） |
| `eink` | push / schedule / 手动（新增） | 独立墨水屏同步，支持 report/news/both，复用 10_sync 接口 |

Secrets（仓库 Settings → Secrets and variables → Actions）：

| Name | 说明 | 获取 |
|------|------|------|
| `ZECTRIX_API_KEY` | 极趣云 API Key | https://cloud.zectrix.com |
| `ZECTRIX_MAC` | 墨水屏 MAC | 如 `AA:BB:CC:DD:EE:FF` |

未配置时自动降级为 dry-run，不阻断 Pages 部署与微信推送。

### 报告模式 4 页布局

- **P1** 每日全球全景扫描：5 大力量（重点/次要/噪音·利好/利空·分数）+ 是否可以做多
- **P2** 行情快照：恒指/恒科/国企/标普/纳指/道指/黄金/原油/汇率实时
- **P3** AI 预测 · 未来函数：基准日→目标日、明日盘面倾向、逐标的预测
- **P4** 宏观快讯 + 舆情：5 类快讯标题 + 舆情温度计/风险分/社区多空

数据直接复用 03 已有管线 `market_data.json` / `community_data.json` / `macro_data.json` / `sentiment_data.json` + `panorama.py` / `forecast.py`，缺失自动降级为「今日未获取」，不回填历史叙事。

详见 `docs/eink-sync.md`。

## ⏰ 自动采集与推送

`.github/workflows/m.yml` 保留北京时间每天 09:00 与每五分钟的报告/微信触发，并有独立的每四小时社区采集触发。
同一时刻的四小时触发只检查/更新社区快照，不额外推送；报告任务读取该快照后重新分析并部署或推送。

> GitHub Actions 的定时任务可能延迟或漏跑，并发排队和缓存失效也会影响实际时间，不保证精确到秒。

## 🐛 历史修复记录：红圈旧数据问题（当时为 14 源）

> 以下是当时方案的历史记录；动态模板与刷新旧日期的做法已废弃，当前以严格真实口径为准。

- **问题**：截图红圈显示 14 个社区正文仍是“8 月 12 日”旧数据，仅 `{{CD_xx}}` 日期占位符刷新，社区研判正文未动态。
- **根因**：`report.html` 与 `tools/wechat_push.py` 中社区内容为硬编码静态文本，未接入动态管线。
- **修复**：
  1. 新增 `community_data.py`：14 源每次构建 HTTP GET + 动态模板回退，生成 `community_data.json`，正文日期永远为当天（如 8 月 30 日），包含现场抓取片段。
  2. `build_site.py` 支持双动态：加载 `community_data.json`，通过 `<!-- COMMUNITY_LIST:BEGIN/END -->` 标记动态注入 14 个社区卡片，覆盖旧静态内容。
  3. `tools/wechat_push.py` 支持双动态：优先读取 `community_data.json`，否则回退到动态模板（日期已刷新为当天），并统一刷新“最新读取”日期。
  4. `.github/workflows/m.yml` 增加 `community_data.py` 步骤，CI 每次自动抓取 14 社区。
  5. `report.html` 模板增加注入标记，保留 `{{CD_xx}}` 占位符兼容旧逻辑。
- **验证**：`python3 market_data.py --demo && python3 community_data.py --demo && python3 build_site.py` 后，`report.html` 中 14 个社区正文均为“8 月 30 日”当天，`最新读取 2026-08-30`，微信推送同理。
