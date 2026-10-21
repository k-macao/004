# 🖥️ 极趣墨水屏同步 — E-Ink Sync (10_sync 接口复用)

本功能实现用户需求：

1. **读取 k-macao/10_sync 仓库接口文件**
2. **自动运行任务 yml 时，推送去极趣墨水屏同步**

---

## 📌 接口来源

- 原仓库：[k-macao/10_sync](https://github.com/k-macao/10_sync)
- 接口文件：`main.py` — 包含 Zectrix 推送核心 `push_image()`、字体加载、新闻抓取（财新社/东方财富）与 400×300 渲染
- 本仓库封装：`tools/zectrix_client.py` — 从 `main.py` 提取的可复用客户端，去业务绑定，保留：
  - `BOARD_TITLE`, `HEADER_*`, `SOURCE_LABELS`, `EASTMONEY_*`, `CAIXIN_*`
  - `push_image()` — multipart/form-data 推送到 `https://cloud.zectrix.com/open/v1/devices/{MAC}/display/image`
  - `wrap_text_by_pixels()`, `draw_news_list()`, `dedupe_titles()`, `make_page_header()`, `render_two_pages()`
  - `get_caixin_news()`, `get_eastmoney_news()`, `get_hotlist_data()` — 多源回退 + 离线兜底

## 🚀 新增推送脚本

### `tools/eink_push.py` — 统一入口

支持三种模式：

| 模式 | 说明 | 页布局 |
|------|------|--------|
| `report` | 03 日报浓缩 4 页（默认） | P1 全景扫描 5 大力量 + 做多结论<br/>P2 行情快照<br/>P3 AI 预测<br/>P4 宏观快讯 + 舆情 |
| `news` | 复用 10_sync 财新+东财 4 页 | P1-2 财新社<br/>P3-4 东方财富 |
| `both` | 报告 1-2 + 新闻 3-4 | P1-2 报告<br/>P3-4 东方财富 |

```bash
# 本地预览（无需密钥，生成 page_*.png）
python3 tools/eink_push.py --mode report --dry-run
python3 tools/eink_push.py --mode news --dry-run
python3 tools/eink_push.py --mode both --dry-run

# 真实推送（需配置密钥）
export ZECTRIX_API_KEY=xxx
export ZECTRIX_MAC=AA:BB:CC:DD:EE:FF
python3 tools/eink_push.py --mode report --pages 1,2,3,4
python3 tools/eink_push.py --mode news --source caixin --pages 1,2,3,4
```

### `tools/zectrix_client.py` — 低层客户端

直接复用 10_sync 接口，可被其它脚本 import：

```python
from tools.zectrix_client import push_image, get_caixin_news, FONTS
from PIL import Image, ImageDraw

img = Image.new("1", (400, 300), 255)
draw = ImageDraw.Draw(img)
# ... 绘制 ...
push_image(img, page_id=1, dry_run=False)
```

---

## 🔄 工作流自动同步

已更新 `.github/workflows/m.yml`：

### 触发方式

- `push` 到 `main` → 构建报告 + 微信推送 + 墨水屏同步
- `workflow_dispatch` 手动触发 → 可选 `wechat_push`, `eink_push`, `eink_mode`
- `schedule` 每天 09:00 (北京时间) → 自动推送微信 + 同步墨水屏

### Secrets 配置

在仓库 Settings → Secrets and variables → Actions 中配置：

| Name | 说明 | 获取 |
|------|------|------|
| `ZECTRIX_API_KEY` | 极趣云 API Key | https://cloud.zectrix.com |
| `ZECTRIX_MAC` | 墨水屏 MAC 地址 | 如 `AA:BB:CC:DD:EE:FF` |
| `PUSHPLUS_TOKEN` | 微信推送 Token（已有） | PushPlus |

### Job 结构

| Job | 说明 | E-Ink 步骤 |
|-----|------|------------|
| `deploy` | 构建 Pages 站点 | dry-run 预览，验证渲染链路 |
| `wechat` | 推送微信 + 墨水屏（push/手动） | 真实推送 report/both 模式 |
| `daily` | 定时 09:00 推送微信 + 墨水屏 | 真实推送 report 模式 |
| `eink` | 独立墨水屏同步（新增） | 支持 report/news/both，复用 10_sync 接口 |

每个 E-Ink 步骤逻辑：

```bash
pip install requests pillow
python3 tools/eink_push.py --mode report --pages 1,2,3,4 --dry-run  # 预览
if [ -n "$ZECTRIX_API_KEY" ] && [ -n "$ZECTRIX_MAC" ]; then
  python3 tools/eink_push.py --mode report --pages 1,2,3,4  # 真实推送
fi
```

- 未配置密钥时自动降级为 dry-run，不阻断构建
- 推送失败不影响 Pages 部署（`|| true` 或独立 job）

---

## 🧩 与 03 日报数据的联动

报告模式直接读取 03 已有的动态数据：

- `market_data.json` — 行情快照
- `community_data.json` — 14 社区研判
- `macro_data.json` — 宏观快讯（5 类）
- `sentiment_data.json` — 舆情温度计 + 标的匹配
- `panorama.py` — 01 栏全景扫描（5 大力量）
- `forecast.py` — 04 栏 AI 预测

四路数据缺失时自动降级为「今日未获取」，不回填历史叙事，与网页/微信同一口径。

---

## 📁 文件清单

| 文件 | 说明 |
|------|------|
| `tools/zectrix_client.py` | 从 10_sync 提取的 Zectrix 客户端（推送+渲染+数据源） |
| `tools/eink_push.py` | 统一推送入口（report/news/both 三模式） |
| `.github/workflows/m.yml` | 已集成 E-Ink 同步的 CI 工作流 |
| `docs/eink-sync.md` | 本文档 |

---

## 🔧 本地联调

```bash
# 1. 生成模拟数据（离线）
python3 market_data.py --demo
python3 community_data.py --demo
python3 sentiment_factors.py --mock
python3 macro_data.py --mock

# 2. 预览墨水屏
python3 tools/eink_push.py --mode report --dry-run
python3 tools/eink_push.py --mode news --dry-run
python3 tools/eink_push.py --mode both --dry-run

# 查看生成图片
ls -lh page_*.png
```

---

## 📌 顶栏文案

四页统一显示 **章鱼 AI·舆情004**（与 10_sync 一致，可通过环境变量覆盖）：

```bash
export ZECTRIX_BOARD_TITLE="章鱼 AI·舆情004"
python3 tools/eink_push.py --mode report --title "章鱼 AI·舆情004"
```

如需恢复来源标签/页码，编辑 `tools/zectrix_client.py` 顶部开关：

```python
HEADER_SHOW_SOURCE = False  # True → 追加 ·财新社 / ·东方财富
HEADER_SHOW_PART = False    # True → 追加 (一)/(二)
HEADER_PREFIX = ""          # 填 "◆ " 可加回菱形前缀
```
