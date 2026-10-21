#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极趣墨水屏 Zectrix 客户端 — 从 k-macao/10_sync 接口文件提取并适配

本模块为 10_sync/main.py 的可复用核心，去除了业务绑定，可被 03 报告推送与新闻推送共用：

- 字体加载：优先 font.ttf（仓库根 / tools/ / /tmp/10_sync/），缺失时回退系统字体或 PIL 默认
- 推送接口：push_image() — 与 10_sync 完全一致的 multipart/form-data 调用
- 渲染辅助：wrap_text_by_pixels, draw_news_list, dedupe_titles, make_page_header 等
- 数据源：get_caixin_news, get_eastmoney_news, get_hotlist_data — 保留 10_sync 原始多源回退逻辑

来源：https://github.com/k-macao/10_sync — main.py (BOARD_TITLE, HEADER_* 开关, PUSH_URL, FONT_PATH 等)
"""

import os
import re
import json
import time
import random
import requests
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# 配置（与 10_sync 保持一致，允许环境变量覆盖）
# ---------------------------------------------------------------------------
BOARD_TITLE = os.environ.get("ZECTRIX_BOARD_TITLE", "章鱼 AI·舆情004")
HEADER_SHOW_SOURCE = False
HEADER_SHOW_PART = False
HEADER_PREFIX = ""

ENABLED_PAGES = os.environ.get("ZECTRIX_PAGES", "1,2,3,4")

# 东方财富 / 财新 配置（与 10_sync 同步）
EASTMONEY_COLUMN = "345"
EASTMONEY_BIZ = "web_news_col"
EASTMONEY_PAGE_SIZE = 24
EASTMONEY_ALT_COLUMN = "344"
CAIXIN_PAGE_SIZE = 24

SOURCE_LABELS = {
    "caixin": "财新社",
    "eastmoney": "东方财富",
    "zhihu": "知乎热榜",
    "bilibili": "B站热搜",
    "github": "GitHub",
}

# 密钥（与 10_sync 一致：从环境变量读取）
API_KEY = os.environ.get("ZECTRIX_API_KEY")
MAC_ADDRESS = os.environ.get("ZECTRIX_MAC")

# ---------------------------------------------------------------------------
# 字体加载 — 多路径尝试，兼容 03 仓库无 font.ttf 的情况
# ---------------------------------------------------------------------------
def _find_font_path():
    candidates = [
        "font.ttf",
        "tools/font.ttf",
        os.path.join(os.path.dirname(__file__), "..", "font.ttf"),
        "/tmp/10_sync/font.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

FONT_PATH = _find_font_path()

def load_fonts():
    """加载字体，返回 dict。缺失中文字体时回退到 PIL 默认（英文正常，中文为方框但不崩）。"""
    fp = FONT_PATH
    try:
        if fp:
            return {
                "huge": ImageFont.truetype(fp, 65),
                "title": ImageFont.truetype(fp, 24),
                "item": ImageFont.truetype(fp, 18),
                "small": ImageFont.truetype(fp, 14),
                "tiny": ImageFont.truetype(fp, 11),
                "48": ImageFont.truetype(fp, 48),
                "36": ImageFont.truetype(fp, 36),
                "20": ImageFont.truetype(fp, 20),
                "16": ImageFont.truetype(fp, 16),
                "12": ImageFont.truetype(fp, 12),
                "path": fp,
            }
    except Exception as e:
        print(f"⚠️ 字体加载失败 {fp}: {e}，回退到默认字体", flush=True)

    # 回退：PIL 默认字体（不支持中文但保证不崩）
    default = ImageFont.load_default()
    print("⚠️ 未找到可用中文字体，使用 PIL 默认字体（中文可能显示为方框，建议上传 font.ttf）", flush=True)
    return {
        "huge": default,
        "title": default,
        "item": default,
        "small": default,
        "tiny": default,
        "48": default,
        "36": default,
        "20": default,
        "16": default,
        "12": default,
        "path": None,
    }

FONTS = load_fonts()

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://finance.eastmoney.com/'
}
HEADERS_CAIXIN = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.caixin.com/',
    'Accept': 'application/json, text/plain, */*',
}

# ---------------------------------------------------------------------------
# 推送核心（与 10_sync/main.py 完全一致）
# ---------------------------------------------------------------------------
def push_image(img, page_id, dry_run=False, api_key=None, mac_address=None, enabled_pages=None):
    enabled_pages = enabled_pages or ENABLED_PAGES
    if str(page_id) not in enabled_pages:
        print(f"⏩ Page {page_id} 未启用，跳过推送。")
        return True
    filename = f"page_{page_id}.png"
    # 确保图像为 1-bit 或 RGB，Zectrix 接受 PNG
    if img.mode != "1":
        # 转为 1-bit 时保留清晰度
        pass
    img.save(filename)
    print(f"💾 Page {page_id} 已保存为 {filename}")
    if dry_run:
        print(f"🔍 dry_run 模式：Page {page_id} 仅本地预览，不推送到 Zectrix")
        return True

    ak = api_key or API_KEY or os.environ.get("ZECTRIX_API_KEY")
    mac = mac_address or MAC_ADDRESS or os.environ.get("ZECTRIX_MAC")
    if not ak or not mac:
        print(f"⚠️ 未配置 ZECTRIX_API_KEY / ZECTRIX_MAC，Page {page_id} 仅本地保存，跳过推送。")
        return False

    api_headers = {"X-API-Key": ak}
    push_url = f"https://cloud.zectrix.com/open/v1/devices/{mac}/display/image"
    try:
        with open(filename, "rb") as f:
            files = {"images": (filename, f, "image/png")}
            data = {"dither": "true", "pageId": str(page_id)}
            res = requests.post(push_url, headers=api_headers, files=files, data=data, timeout=15)
            print(f"✅ Page {page_id} 推送成功: {res.status_code} - {res.text[:200]}")
            return res.status_code in (200, 201, 204)
    except Exception as e:
        print(f"❌ Page {page_id} 推送失败: {e}")
        return False

def wrap_text_by_pixels(draw, text, font, max_width):
    lines = []
    current_line = ""
    for char in text:
        test_line = current_line + char
        try:
            w = draw.textlength(test_line, font=font)
        except AttributeError:
            w = draw.textbbox((0, 0), test_line, font=font)[2]
        if w <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = char
    if current_line:
        lines.append(current_line)
    return lines

def fit_title(draw, text, fonts=None, max_width=360, box_top=10, box_height=35):
    """顶栏标题自适应：先逐级缩小字号完整显示，最小字号仍放不下才截断。"""
    fonts = fonts or FONTS
    font = fonts["title"]
    path = fonts.get("path")
    if path:
        for size in range(24, 13, -1):
            try:
                candidate = ImageFont.truetype(path, size)
                if draw.textlength(text, font=candidate) <= max_width:
                    return text, candidate, box_top + max(0, (box_height - size) // 2)
                font = candidate
            except Exception:
                break
    fitted = text
    try:
        while draw.textlength(fitted, font=font) > max_width and len(fitted) > 4:
            fitted = fitted[:-1]
        if fitted != text:
            fitted = fitted[:-1] + "…"
    except Exception:
        fitted = text[:14]
    return fitted, font, 15 if font is fonts["title"] else box_top + 8

def draw_news_list(draw, page_title, items, start_idx, fonts=None):
    fonts = fonts or FONTS
    font_title = fonts["title"]
    font_item = fonts["item"]
    font_small = fonts["small"]

    draw.rounded_rectangle([(10, 10), (390, 45)], radius=8, fill=0)
    title_text, title_font, title_y = fit_title(draw, page_title, fonts)
    draw.text((20, title_y), title_text, font=title_font, fill=255)

    y, last_idx = 55, start_idx
    item_gap = 12
    line_height = 23
    for i in range(start_idx, len(items)):
        lines = wrap_text_by_pixels(draw, items[i], font_item, max_width=340)
        required_h = len(lines) * line_height
        if y + required_h > 295:
            break
        current_num = i + 1
        draw.rounded_rectangle([(10, y), (36, y + 24)], radius=6, fill=0)
        num_x = 18 if current_num < 10 else 11
        draw.text((num_x, y + 3), str(current_num), font=font_small, fill=255)
        curr_y = y + 1
        for line in lines:
            draw.text((45, curr_y), line, font=font_item, fill=0)
            curr_y += line_height
        y += max(24, required_h) + item_gap
        last_idx = i + 1
        if y < 290:
            draw.line([(45, y - item_gap / 2), (380, y - item_gap / 2)], fill=0, width=1)
    return last_idx

def dedupe_titles(titles, exclude=None):
    exclude = set(exclude or [])
    seen = set()
    out = []
    for t in titles:
        t = (t or "").strip()
        if not t or t in seen or t in exclude:
            continue
        seen.add(t)
        out.append(t)
    return out

def source_label(source, override=None):
    if override:
        return override
    return SOURCE_LABELS.get(source, source or "资讯")

def make_page_header(label, part, board_title=None):
    main = (board_title or BOARD_TITLE or "").strip() or "章鱼 AI·舆情004"
    text = f"{HEADER_PREFIX}{main}"
    label = (label or "").strip()
    if HEADER_SHOW_SOURCE and label and label != main:
        text = f"{text}·{label}"
    if HEADER_SHOW_PART and part:
        text = f"{text} ({part})"
    return text

def render_two_pages(titles, page_ids, label, part_names=("一", "二"), dry_run=False, board_title=None, fonts=None, enabled_pages=None):
    fonts = fonts or FONTS
    used = []
    if not page_ids:
        return used
    next_s = 0
    enabled = [str(p) for p in page_ids if str(p) in (enabled_pages or ENABLED_PAGES)]
    if not enabled:
        return used

    for i, pid in enumerate(enabled):
        part = part_names[i] if i < len(part_names) else str(i + 1)
        page_title = make_page_header(label, part, board_title=board_title)
        print(f"生成 Page {pid}: {page_title}  [条目起点 index={next_s}]")
        img = Image.new("1", (400, 300), color=255)
        start_index = next_s
        if i == 0:
            start_index = 0
        end_index = draw_news_list(ImageDraw.Draw(img), page_title, titles, start_index, fonts=fonts)
        if end_index <= start_index and start_index < len(titles):
            print(f"  ⚠️ Page {pid} 从 index={start_index} 起已无足够条目可画")
        used.extend(titles[start_index:end_index])
        next_s = end_index
        push_image(img, pid, dry_run=dry_run, enabled_pages=enabled_pages or ENABLED_PAGES)
    return used

# ---------------------------------------------------------------------------
# 数据源（直接复用 10_sync 的多源回退逻辑，含离线兜底）
# ---------------------------------------------------------------------------
def get_caixin_news(page_size=20):
    titles = []
    print(f"正在从 财新社 获取数据 (目标 {page_size} 条)...")
    timestamp = str(int(time.time() * 1000))
    api_candidates = [
        f"https://gateway.caixin.com/api/dataplatform/scroll/index?count={page_size}&_={timestamp}",
        f"https://mapiv5.caixin.com/m/api/getWapIndexListByPage?page=1&count={page_size}&callback=&_={timestamp}",
        f"https://gateway.caixin.com/api/extapi/homeInterface.jsp?subject=100990318;100990314;100990311&start=0&count={page_size}&type=2&_={timestamp}",
        f"https://gateway.caixin.com/api/dataplatform/scroll/index?count={page_size}",
        f"https://mapiv5.caixin.com/m/api/getWapIndexListByPage?page=1",
    ]

    for url in api_candidates:
        try:
            print(f"  尝试 API: {url[:80]}...")
            res = requests.get(url, headers=HEADERS_CAIXIN, timeout=10)
            text = res.text.strip()
            if not text:
                continue
            if text.startswith("jQuery") or (text.startswith("(") and text.endswith(")")) or "callback" in text[:30].lower():
                s = text.find("{")
                e = text.rfind("}")
                if s != -1 and e != -1 and e > s:
                    text = text[s:e+1]
            try:
                data = json.loads(text)
            except:
                continue

            if not isinstance(data, dict):
                continue

            if "data" in data:
                d = data["data"]
                if isinstance(d, dict):
                    article_list = None
                    if "articleList" in d and isinstance(d["articleList"], list):
                        article_list = d["articleList"]
                    elif "list" in d and isinstance(d["list"], list):
                        article_list = d["list"]
                    elif "datas" in d and isinstance(d["datas"], list):
                        article_list = d["datas"]
                    if article_list:
                        for item in article_list:
                            if not isinstance(item, dict):
                                continue
                            t = item.get("title") or item.get("desc") or item.get("TITLE") or ""
                            t = str(t).strip()
                            if t:
                                t = re.sub(r"\s+", " ", t)
                                titles.append(t)
                        if len(titles) >= 5:
                            print(f"  ✅ API 成功获取 {len(titles)} 条")
                            break
                elif isinstance(d, list):
                    for item in d:
                        if isinstance(item, dict):
                            t = item.get("title") or item.get("desc") or ""
                            t = str(t).strip()
                            if t:
                                titles.append(re.sub(r"\s+", " ", t))
                    if len(titles) >= 5:
                        print(f"  ✅ API 成功获取 {len(titles)} 条 (list)")
                        break

            if "list" in data and isinstance(data["list"], list) and len(titles) < 5:
                for item in data["list"]:
                    if isinstance(item, dict):
                        t = item.get("title","").strip()
                        if t:
                            titles.append(t)
                if titles:
                    break

            if len(titles) >= 5:
                break
        except Exception as e:
            print(f"  ⚠️ API 尝试失败: {e}")
            continue

    if len(titles) < 5:
        print("  API 未获取到足够数据，尝试 HTML 抓取 https://www.caixin.com/ ...")
        try:
            html = requests.get("https://www.caixin.com/", headers=HEADERS_CAIXIN, timeout=10).text
            patterns = [
                r'<a[^>]*href="https?://www\.caixin\.com/[^"]*"[^>]*>([^<]{8,80})</a>',
                r'<a[^>]*href="//www\.caixin\.com/[^"]*"[^>]*>([^<]{8,80})</a>',
                r'"title"\s*:\s*"([^"]{8,80})"',
            ]
            seen_tmp = set()
            for pat in patterns:
                matches = re.findall(pat, html)
                for m in matches:
                    t = re.sub(r"<.*?>", "", m).strip()
                    t = re.sub(r"\s+", " ", t)
                    if len(t) < 8:
                        continue
                    if t in ["财新网", "财新网 - 财新网", "登录", "订阅", "下载"] or "Copyright" in t:
                        continue
                    if t not in seen_tmp:
                        seen_tmp.add(t)
                        titles.append(t)
                    if len(titles) >= page_size:
                        break
                if len(titles) >= page_size:
                    break
            print(f"  HTML 抓取获得 {len(titles)} 条")
        except Exception as e:
            print(f"  HTML 抓取失败: {e}")

    if len(titles) < 5:
        print("  ⚠️ 仍未获取到数据，使用内置财新社示例数据兜底（离线预览）")
        sample = [
            "财新中国制造业PMI回落至49.5 需求端承压明显",
            "中央政治局会议定调下半年经济 稳增长信号明确",
            "美联储降息预期升温 全球市场震荡分化加剧",
            "央行公开市场净投放3000亿元 流动性保持充裕",
            "沪指重返3100点 机构看好科技成长主线",
            "财政部拟发行超长期特别国债 支持重大项目建设",
            "新能源车出口创单月新高 欧洲市场成主要增量",
            "证监会：加大对财务造假打击力度 坚持零容忍",
            "地方化债进度加快 特殊再融资债券发行提速",
            "医疗反腐持续深入 多家药企主动下调药品价格",
            "离岸人民币汇率升破7.1关口 创近半年新高",
            "香港金管局推进数字港元试点 涵盖零售支付场景",
            "监管发文规范私募行业 强化信息披露与托管要求",
            "万科中报：销售额下滑但经营性现金流改善",
            "碧桂园境外债务重组取得进展 债权人达成初步共识",
            "新一轮稳外贸政策发布 助力外贸企业拓市场",
            "统计局解读7月CPI：食品价格季节性上涨",
            "上海自贸区发布多项制度创新 扩大金融开放",
            "AI大模型商业化提速 多家科技巨头加码布局",
            "OpenAI发布新一代多模态模型 能力大幅提升",
        ]
        titles = sample[:page_size]

    seen = set()
    uniq = []
    for t in titles:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq[:page_size]

def get_eastmoney_news(page_size=20, column=None, biz=None, page_index=1):
    column = column or EASTMONEY_COLUMN
    biz = biz or EASTMONEY_BIZ
    page_index = int(page_index or 1)
    titles = []
    print(f"正在从 东方财富 获取数据 (column={column}, biz={biz}, page_index={page_index})...")
    timestamp = str(int(time.time() * 1000))
    req_trace_base = str(int(time.time()*1000)) + str(random.randint(100,999))
    api_candidates = [
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz={biz}&column={column}&order=1&needInteractData=0&page_index={page_index}&page_size={page_size}&req_trace={req_trace_base}&fields=code,showTime,title,mediaName,summary,image,url,uniqueUrl",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz={biz}&column={column}&order=1&needInteractData=0&page_index={page_index}&page_size={page_size}&req_trace={timestamp}",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news&column=24&order=1&page_index={page_index}&page_size={page_size}&req_trace={timestamp}",
        f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col&column=344&order=1&needInteractData=0&page_index={page_index}&page_size={page_size}&req_trace={timestamp}",
    ]
    if column != "345":
        api_candidates.append(f"https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col&column=345&order=1&needInteractData=0&page_index={page_index}&page_size={page_size}&req_trace={timestamp}")

    for url in api_candidates:
        try:
            print(f"  尝试 API: {url[:80]}...")
            res = requests.get(url, headers=HEADERS, timeout=10)
            text = res.text.strip()
            if text.startswith("jQuery") or text.startswith("(") or "callback" in text[:20]:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1:
                    text = text[start:end+1]
                else:
                    s = text.find("(")
                    e = text.rfind(")")
                    if s != -1 and e != -1:
                        text = text[s+1:e]
            data = json.loads(text)
            if isinstance(data, dict):
                if data.get("code") == "1" and isinstance(data.get("data"), dict):
                    lst = data["data"].get("list") or data["data"].get("newsList") or []
                    for item in lst:
                        t = item.get("title") or item.get("TITLE") or ""
                        t = t.strip()
                        if t:
                            t = re.sub(r"\s+", " ", t)
                            titles.append(t)
                    if len(titles) >= 5:
                        print(f"  ✅ API 成功获取 {len(titles)} 条")
                        break
                elif isinstance(data.get("data"), list):
                    for item in data["data"]:
                        t = item.get("title", "").strip()
                        if t:
                            titles.append(t)
                    if titles:
                        break
            if len(titles) >= 5:
                break
        except Exception as e:
            print(f"  ⚠️ API 尝试失败: {e}")
            continue

    if len(titles) < 5:
        print("  API 未获取到足够数据，尝试 HTML 抓取 https://finance.eastmoney.com/ ...")
        try:
            html = requests.get("https://finance.eastmoney.com/", headers=HEADERS, timeout=10).text
            pattern1 = r'<a[^>]*href="https?://finance\.eastmoney\.com/a/[^"]*"[^>]*>([^<]{5,80})</a>'
            matches = re.findall(pattern1, html)
            seen = set()
            for m in matches:
                t = re.sub(r"<.*?>", "", m).strip()
                t = re.sub(r"\s+", " ", t)
                if len(t) >= 5 and t not in seen:
                    seen.add(t)
                    titles.append(t)
                if len(titles) >= page_size:
                    break
            print(f"  HTML 抓取1 获得 {len(titles)} 条")
        except Exception as e:
            print(f"  HTML 抓取1 失败: {e}")

    if len(titles) < 5:
        print("  ⚠️ 仍未获取到数据，使用内置东方财富示例数据兜底（离线预览）")
        sample = [
            "宇树科技：网上发行最终中签率0.0181%",
            "央行印发《中国人民银行“十五五”改革发展规划》",
            "《煤炭工业发展“十五五”规划》印发：到2030年大型现代化煤矿产能比重提升至87%",
            "美股三大指数震荡整理 国际油价大涨",
            "高盛研判中国AI股：近期回调已释放核心风险 建议多元布局四大主线",
            "江波龙：半年度净利润105.77亿元 同比增长71528.66% 拟回购股份",
            "8月10日东方财富财经晚报（附新闻联播）",
            "12天11板爱丽家居：股价11个交易日涨185.56% 明起停牌核查",
            "阿里云计划将全球数据中心产能提升两倍以上",
            "年内最贵新股频准激光中签号出炉：共有6423个",
            "4800亿龙头迎利好！CRO概念股梳理",
            "景林最新美股持仓曝光！英伟达等惨遭清仓",
            "越跌越买！央行加速抄底黄金 单月增持19.9吨",
        ]
        col_shift = {"345": 0, "344": 8, "340": 16}.get(str(column), 0)
        start = col_shift + max(0, (page_index - 1) * 10)
        start = start % max(1, len(sample) - 8)
        titles = sample[start:start + page_size]
        if len(titles) < page_size:
            titles = titles + sample[: page_size - len(titles)]

    seen = set()
    uniq = []
    for t in titles:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq[:page_size]

def get_hotlist_data(source, page_size=20):
    if source == "eastmoney":
        return get_eastmoney_news(page_size=page_size, column=EASTMONEY_COLUMN, biz=EASTMONEY_BIZ)
    elif source == "caixin":
        return get_caixin_news(page_size=page_size)
    else:
        # 其它源（zhihu/bilibili/github）沿用 10_sync 原逻辑简化版
        print(f"正在从 {source} 获取数据...")
        try:
            if source == "zhihu":
                url = "https://api.zhihu.com/topstory/hot-list"
                res = requests.get(url, headers=HEADERS, timeout=10).json()
                titles = [item['target']['title'] for item in res['data']]
            elif source == "bilibili":
                url = "https://api.bilibili.com/x/web-interface/wbi/search/square?limit=20"
                res = requests.get(url, headers=HEADERS, timeout=10).json()
                titles = [item['show_name'] for item in res['data']['trending']['list']]
            elif source == "github":
                from datetime import datetime, timedelta
                date_str = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
                url = f"https://api.github.com/search/repositories?q=stars:>500+created:>{date_str}&sort=stars&order=desc"
                res = requests.get(url, headers=HEADERS, timeout=10).json()
                titles = [f"{item['full_name']}: {(item['description'][:50] if item['description'] else 'No desc')}" for item in res['items']]
            else:
                titles = [f"不支持的数据源 {source}"]
        except Exception as e:
            print(f"获取失败: {e}")
            titles = [f"数据获取失败 {source}"] * 10
        return titles[:page_size]
