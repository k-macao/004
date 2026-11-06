# 完整 CI 工作流副本与手工同步

GitHub App 如果没有 `workflows` 权限，不能推送 `.github/workflows/*`。本目录保留可直接复制的完整副本；**副本必须与实际工作流逐字一致**，不是只同步步骤名称的旧版模板。

| 文件 | 目标路径 | 用途 |
|---|---|---|
| `m.yml` | `.github/workflows/m.yml` | 当前四 job 工作流的完整副本：社区采集、Pages 部署、微信推送、定时推送 |
| `github_workflow_new.yml` | `.github_workflow_new.yml` | 历史整合版快照，不是当前部署入口；不要用根目录这份旧快照覆盖 `m.yml` |

## 同步与校验

修改实际工作流后，同步完整副本，不能只改其中一份：

```bash
cp .github/workflows/m.yml docs/ci49/m.yml
python3 -m unittest tests.test_workflows_valid -v
cmp .github/workflows/m.yml docs/ci49/m.yml
```

没有工作流写权限时，可由有权限的 maintainer 反向应用已审核的副本：

```bash
cp docs/ci49/m.yml .github/workflows/m.yml
python3 -m unittest tests.test_workflows_valid -v
```

不要用旧补丁或历史快照覆盖当前文件。当前工作流已经包含共享社区快照、RSS 抓取和 DeepSeek 环境变量；历史补丁不一定包含这些后续改动。

## 2026-11-05 部署中断修复

实际工作流的 `wechat`、`daily` job 已增加 `DEEPSEEK_API_KEY`，但 `m.yml` 的完整副本漏了这两行。`deploy` 在构建前运行全量单测，逐字一致性检查因此失败，Pages 构建与上传被跳过；线上继续展示上一次成功发布的旧 HTML。

本次补齐副本中的两处环境变量，保留自检门禁，不通过删测试或跳过校验来让 CI 变绿。回归测试同时检查：

- 实际工作流与完整副本一致，所有 step 均有 `run` 或 `uses`；
- `wechat`、`daily` 都保留 DeepSeek 密钥映射，不写入任何密钥值；
- 全量单测仍位于报告构建、产物上传和 Pages 部署之前；
- 报告生成时间来自本次构建，原始抓取/行情日期不被改写；公开链接指向本仓库的 `/004/`。

合并修复后，需要一次成功的 `main` 工作流运行才会替换线上旧产物。GitHub 的 `schedule` 是尽力触发，不保证标称的五分钟间隔；可在 Actions 手动运行工作流恢复发布，是否推送微信由 `wechat_push` 输入控制。

## 2026-11-06 定时运行跳过构建修复

线上排查（如运行 `42138849627`、`42093752563`、`42022119408`）发现：原工作流假设 `0 */4 * * *` 与 `*/5 * * * *` 会在同一分钟双触发，因而在 `deploy` 与 `daily` 上加了 `github.event.schedule != '0 */4 * * *'`。实际 GitHub Actions 对公共仓库高频 `*/5 * * * *` 大幅限流，并不会在 `0 */4 * * *` 触发时刻同时跑 `*/5`，导致约四成定时运行只跑 `community_snapshot` 就将 `deploy` 和 `daily` 标为 `skipped`（即使当次刚抓完 49 源新社区数据也不发布），公开页 `扫描时间`（`data_time`）数小时不更新。

修复内容：

- 移除 `deploy` 与 `daily` 上排除 `0 */4 * * *` 的 `if` 条件，任何 `schedule` 触发均完整执行构建、Pages 部署与定时推送；
- 新增 `17 */2 * * *` 错峰定时兜底，避免整点拥堵并缩短 `*/5` 被限流时的最大刷新间隔；
- 所有 `schedule` 事件统一归入 `pages-schedule-loop` 并发组（`cancel-in-progress: false`），串行排队不撞车 Pages 环境；
- 同步 `docs/ci49/m.yml` 并新增 `TestScheduledRunsNeverSkipBuild` 回归测试。
