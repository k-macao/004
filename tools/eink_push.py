#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 极趣墨水屏同步推送 (E-Ink Sync)

功能：
  1/ 读取 k-macao/10_sync 接口文件（已封装为 tools/zectrix_client.py）
  2/ 自动运行任务 yml 时，推送去极趣墨水屏同步

本文件整合两套推送能力：
  - news 模式：完全复用 10_sync 的 4 页财新+东方财富看板（接口一致）
  - report 模式：把 03 的全景扫描/行情/预测/舆情浓缩成 4 页墨水屏图像

工作流接入：
  - 在 .github/workflows/m.yml 的 deploy/wechat/daily 三个 job 中，
    build_site.py 之后调用本脚本：
      python3 tools/eink_push.py --mode report --pages 1,2,3,4
      python3 tools/eink_push.py --mode news --pages 1,2,3,4
      python3 tools/eink_push.py --mode both --pages 1,2,3,4

环境变量：
  ZECTRIX_API_KEY / ZECTRIX_MAC — 极趣云密钥与设备 MAC（与 10_sync 一致）
  ZECTRIX_BOARD_TITLE — 顶栏文案，默认「章鱼 AI·舆情004」
  ZECTRIX_PAGES — 默认启用页面

离线预览：
  python3 tools/eink_push.py --mode report --dry-run
  python3 tools/eink_push.py --mode news --dry-run
  python3 tools/eink_push.py --mode both --dry-run
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# 10_sync 接口封装
try:
    from tools.zectrix_client import (
        FONTS, BOARD_TITLE as DEFAULT_BOARD_TITLE,
        push_image, wrap_text_by_pixels, dedupe_titles, fit_title,
        source_label, make_page_header, render_two_pages,
        get_hotlist_data, get_eastmoney_news,
        ENABLED_PAGES as DEFAULT_PAGES,
    )
    from tools import zectrix_client as zc
except ImportError:
    # 兼容直接运行
    from zectrix_client import (
        FONTS, BOARD_TITLE as DEFAULT_BOARD_TITLE,
        push_image, wrap_text_by_pixels, dedupe_titles, fit_title,
        source_label, make_page_header, render_two_pages,
        get_hotlist_data, get_eastmoney_news,
        ENABLED_PAGES as DEFAULT_PAGES,
    )
    import zectrix_client as zc

from PIL import Image, ImageDraw

# 03 数据管线
try:
    import vix_daily
    import panorama
    import forecast as forecast_mod
    import macro_data as macro_data_mod
except Exception:
    vix_daily = None
    panorama = None
    forecast_mod = None
    macro_data_mod = None


# ---------------------------------------------------------------------------
# 工具：数据加载（与 wechat_push / build_site 同一套环境变量覆盖）
# ---------------------------------------------------------------------------
def _load_json(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def load_all_data(root=REPO_ROOT):
    market = _load_json(os.environ.get('MARKET_DATA', os.path.join(root, 'market_data.json')))
    community = _load_json(os.environ.get('COMMUNITY_DATA', os.path.join(root, 'community_data.json')))
    macro = _load_json(os.environ.get('MACRO_DATA', os.path.join(root, 'macro_data.json')))
    sentiment = _load_json(os.environ.get('SENTIMENT_DATA', os.path.join(root, 'sentiment_data.json')))
    return market, community, macro, sentiment

def fmt_pct(v):
    try:
        if v is None:
            return "—"
        v = float(v)
        sign = "−" if v < 0 else "+"
        return f"{sign}{abs(v):.2f}%"
    except:
        return "—"

def fmt_price(q):
    try:
        if not q or q.get('last') is None:
            return "—"
        v = float(q['last'])
        nd = q.get('decimals') or 2
        if q.get('name') == '现货黄金' and v >= 1000:
            nd = 0
        return f"{v:,.{nd}f}"
    except:
        return "—"


# ---------------------------------------------------------------------------
# 报告模式：4 页内容构建
# ---------------------------------------------------------------------------
def build_report_pages(market, community, macro, sentiment):
    """
    返回 dict: page_id -> list[str] (每页的文本行)
    Page1: 全景扫描 5 大力量 + 是否可做多
    Page2: 行情快照
    Page3: AI 预测
    Page4: 宏观快讯 + 舆情
    """
    now = datetime.now(timezone.utc)
    pages = {1: [], 2: [], 3: [], 4: []}

    # ---------- Page1 开头：每日 VIX 恐慌指数 ----------
    if vix_daily:
        pages[1].extend(vix_daily.render_plain(vix_daily.analyze(market)))
    else:
        pages[1].append("VIX 模块未加载")

    # ---------- Page1: 全景扫描 ----------
    if panorama:
        try:
            scan = panorama.scan(market=market, macro=macro, sentiment=sentiment, community=community, now=now)
            forces = scan.get('forces') or []
            verdict = scan.get('verdict') or {}
            if forces:
                pages[1].append(f"扫描 {scan.get('scan_date','')} 覆盖{int(scan.get('coverage',0)*100)}%")
                for f in forces[:5]:
                    tier = f.get('tier','')
                    dw = f.get('dir_word','')
                    score = f.get('score','')
                    name = f.get('name','').replace('（','(').replace('）',')')
                    # 简化名称
                    short = name.split('（')[0][:18]
                    pages[1].append(f"{f.get('rank','')}. {short} [{tier}{dw} {score}分]")
                    # 读数截断
                    read = f.get('read','')[:38]
                    if read:
                        pages[1].append(f"  {read}")
                pages[1].append(f"做多: {verdict.get('stance','')} {verdict.get('score_text','')}")
            else:
                pages[1].append("今日未获取足够数据，本栏不编故事")
                pages[1].append(f"缺口: {'; '.join(scan.get('data_gaps',[])[:2])}")
        except Exception as e:
            pages[1].append(f"全景扫描生成失败: {e}")
    else:
        pages[1].append("panorama 模块未加载")

    # ---------- Page2: 行情 ----------
    quotes = (market or {}).get('quotes') or {}
    if quotes:
        fetch_date = market.get('fetch_date') or ''
        pages[2].append(f"行情 {fetch_date} 抓取")
        order = ['VIX','HSI','HSTECH','HSCE','SPX','NDQ','DJI','GOLD','WTI','BRENT','USDCNH','USDCNY']
        for k in order:
            q = quotes.get(k) or {}
            if q.get('last') is None and q.get('pct') is None:
                continue
            name = q.get('name') or k
            last = fmt_price(q)
            pct = fmt_pct(q.get('pct'))
            pages[2].append(f"{name} {last} {pct}")
        summary = (market or {}).get('summary') or {}
        pages[2].append(f"{summary.get('ok',0)}/{summary.get('total',0)} 项同步")
    else:
        pages[2].append("行情未获取")
        pages[2].append("运行 python3 market_data.py")

    # ---------- Page3: AI 预测 ----------
    if forecast_mod:
        try:
            data = forecast_mod.predict(market=market, macro=macro, sentiment=sentiment, community=community, now=now)
            if data.get('available'):
                pages[3].append(f"AI预测 基准{data.get('base_date','')}→目标{data.get('target_date','')}")
                stance = data.get('stance') or {}
                pages[3].append(f"倾向: {stance.get('label','')} {stance.get('z','')}σ")
                pages[3].append(f"{stance.get('summary','')[:40]}")
                for f in (data.get('forecasts') or [])[:6]:
                    pages[3].append(f"{f.get('name','')[:8]} {f.get('dir_word','')} {fmt_pct(f.get('mu_pct'))} 置信{int(f.get('confidence',0)*100)}%")
            else:
                pages[3].append("AI预测 今日未获取")
                pages[3].append(data.get('unavailable_reason','no_quotes'))
        except Exception as e:
            pages[3].append(f"预测生成失败: {e}")
    else:
        pages[3].append("forecast 模块未加载")

    # ---------- Page4: 宏观 + 舆情 ----------
    # 宏观
    if macro and macro.get('categories'):
        cats = macro.get('categories') or {}
        kept = (macro.get('summary') or {}).get('kept_items',0)
        pages[4].append(f"宏观快讯 {kept} 条入库")
        cnt = 0
        for ckey, blk in cats.items():
            if cnt >= 8:
                break
            items = (blk or {}).get('items') or []
            label = (blk or {}).get('label') or ckey
            if not items:
                continue
            pages[4].append(f"◆ {label[:16]}")
            for it in items[:2]:
                if cnt >= 10:
                    break
                ttl = (it.get('title') or '')[:34]
                date = it.get('published_date') or ''
                pages[4].append(f"[{date[5:]}] {ttl}" if date else ttl)
                cnt += 1
    else:
        pages[4].append("宏观快讯 今日未获取")

    # 舆情
    mkt = (sentiment or {}).get('market') or {}
    if mkt:
        pages[4].append(f"舆情温度 {mkt.get('sent_temp','—')} {mkt.get('label','')}")
        pages[4].append(f"净情感 {mkt.get('net_senti','—')} 负面{mkt.get('neg_share','—')}%")
        pages[4].append(f"风险分 {mkt.get('risk_score','—')} 新闻{mkt.get('news_count','—')}条")
    else:
        pages[4].append("舆情因子未生成")

    # 社区（仅统计实盘抓到且具备真实多空判定的源，未获取源不掺入分母或多空计数）
    comms = (community or {}).get('communities') or []
    if comms:
        valid = [c for c in comms if c.get('verdict_class') in ('bull', 'bear', 'neutral', 'mixed')]
        if valid:
            bull = sum(1 for c in valid if c.get('verdict_class') == 'bull')
            bear = sum(1 for c in valid if c.get('verdict_class') == 'bear')
            pages[4].append(f"社区 实抓{len(valid)}/{len(comms)} 多{bull} 空{bear}")
        else:
            pages[4].append(f"社区 当次未抓到实盘热评(0/{len(comms)})")

    return pages

def draw_report_page(page_title, lines, fonts=None):
    fonts = fonts or FONTS
    font_title = fonts["title"]
    font_item = fonts["item"]
    font_small = fonts["small"]

    img = Image.new("1", (400, 300), color=255)
    draw = ImageDraw.Draw(img)

    # 顶栏
    draw.rounded_rectangle([(10, 10), (390, 45)], radius=8, fill=0)
    title_text, title_font, title_y = fit_title(draw, page_title, fonts)
    draw.text((20, title_y), title_text, font=title_font, fill=255)

    y = 55
    line_height = 20
    max_width = 370
    for idx, raw in enumerate(lines):
        # 每行可能需要换行
        wrapped = wrap_text_by_pixels(draw, raw, font_item, max_width=max_width-10) if len(raw) > 0 else [""]
        for wline in wrapped:
            if y + line_height > 295:
                break
            draw.text((12, y), wline, font=font_item, fill=0)
            y += line_height
        if y + line_height > 295:
            break
        # 分隔线每3行
        if idx % 3 == 2 and y < 285:
            draw.line([(12, y), (388, y)], fill=0, width=1)
            y += 2

    return img

def push_report_pages(pages_dict, board_title=None, dry_run=False, enabled_pages=None, api_key=None, mac_address=None):
    board_title = board_title or os.environ.get("ZECTRIX_BOARD_TITLE") or DEFAULT_BOARD_TITLE
    enabled_pages = enabled_pages or os.environ.get("ZECTRIX_PAGES") or DEFAULT_PAGES
    enabled_list = [p.strip() for p in enabled_pages.split(",") if p.strip()]

    part_names = ["一","二","三","四","五","六"]
    results = []
    for pid in [1,2,3,4]:
        if str(pid) not in enabled_list:
            continue
        lines = pages_dict.get(pid) or []
        if not lines:
            lines = [f"Page {pid} 无内容"]
        # 顶栏：统一显示 BOARD_TITLE，带页码后缀可选
        part = part_names[pid-1] if pid-1 < len(part_names) else str(pid)
        header = make_page_header("", part, board_title=board_title)
        # 如果配置为不显示页码，header 就是纯 BOARD_TITLE
        print(f"生成报告 Page {pid}: {header}  共 {len(lines)} 行")
        img = draw_report_page(header, lines, fonts=FONTS)
        ok = push_image(img, pid, dry_run=dry_run, api_key=api_key, mac_address=mac_address, enabled_pages=enabled_pages)
        results.append((pid, ok))
    return results


# ---------------------------------------------------------------------------
# 新闻模式：完全复用 10_sync 逻辑
# ---------------------------------------------------------------------------
def push_news_pages(source="caixin", pages="1,2,3,4", east_column="345", board_title=None, dry_run=False, api_key=None, mac_address=None):
    board_title = board_title or os.environ.get("ZECTRIX_BOARD_TITLE") or DEFAULT_BOARD_TITLE
    enabled_pages = pages
    print(f"📰 新闻模式：source={source} pages={pages} east_column={east_column} board_title={board_title}")

    # 第1-2页
    label1 = source_label(source)
    titles1 = get_hotlist_data(source, page_size=24)
    titles1 = dedupe_titles(titles1)
    print(f"第1-2页 {source} 共 {len(titles1)} 条")
    used_12 = render_two_pages(
        titles1,
        page_ids=["1","2"],
        label=label1,
        part_names=("一","二"),
        dry_run=dry_run,
        board_title=board_title,
        fonts=FONTS,
        enabled_pages=enabled_pages,
    )

    # 第3-4页 东方财富
    # 若 1,2 已是 eastmoney，则换栏目避免重复
    col = east_column
    page_idx = 1
    if source == "eastmoney":
        col = zc.EASTMONEY_ALT_COLUMN or "344"
        page_idx = 2
        print(f"  ℹ️ 第1-2页已是东方财富，第3-4页改用栏目 {col} / page_index={page_idx}")

    raw = get_eastmoney_news(page_size=24, column=col, biz=zc.EASTMONEY_BIZ, page_index=page_idx)
    titles2 = dedupe_titles(raw, exclude=used_12)
    if len(titles2) < 12:
        alt_col = "340" if col != "340" else "345"
        print(f"  ℹ️ 去重后仅 {len(titles2)} 条，尝试栏目 {alt_col} 补齐...")
        more = get_eastmoney_news(page_size=24, column=alt_col, page_index=1)
        titles2 = dedupe_titles(titles2 + more, exclude=used_12)

    label2 = source_label("eastmoney")
    if source == "eastmoney":
        col_labels = {"345": "东财导读", "344": "东财要闻", "340": "东财股市"}
        label2 = col_labels.get(str(col), f"东财{col}")

    print(f"第3-4页 eastmoney 标签={label2} 共 {len(titles2)} 条")
    used_34 = render_two_pages(
        titles2,
        page_ids=["3","4"],
        label=label2,
        part_names=("一","二"),
        dry_run=dry_run,
        board_title=board_title,
        fonts=FONTS,
        enabled_pages=enabled_pages,
    )

    return used_12, used_34


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="章鱼 AI·舆情004 — 极趣墨水屏同步推送")
    p.add_argument("--mode", choices=["report","news","both"], default="report",
                   help="推送模式：report=03日报浓缩4页，news=财新+东方财富4页（复用10_sync），both=报告1-2 + 新闻3-4")
    p.add_argument("--pages", type=str, default=None,
                   help="推送页面，如 1,2,3,4（默认 1,2,3,4）")
    p.add_argument("--source", type=str, default="caixin",
                   help="新闻模式第1-2页源：caixin/eastmoney/zhihu/bilibili/github")
    p.add_argument("--east-column", type=str, default="345",
                   help="东方财富栏目ID（第3-4页）")
    p.add_argument("--title", type=str, default=None,
                   help="覆盖顶栏文案（四页统一），默认 章鱼 AI·舆情004")
    p.add_argument("--dry-run", action="store_true",
                   help="仅本地生成 page_*.png，不推送")
    p.add_argument("--api-key", type=str, default=None, help="Zectrix API Key（覆盖环境变量）")
    p.add_argument("--mac", type=str, default=None, help="Zectrix MAC（覆盖环境变量）")
    return p.parse_args()

def main():
    args = parse_args()
    board_title = args.title or os.environ.get("ZECTRIX_BOARD_TITLE") or DEFAULT_BOARD_TITLE
    pages = args.pages or os.environ.get("ZECTRIX_PAGES") or DEFAULT_PAGES
    api_key = args.api_key or os.environ.get("ZECTRIX_API_KEY")
    mac = args.mac or os.environ.get("ZECTRIX_MAC")

    print(f"🚀 极趣墨水屏同步 — 模式 {args.mode} | 顶栏 {board_title} | 页面 {pages} | dry_run={args.dry_run}")
    if not api_key or not mac:
        if not args.dry_run:
            print("⚠️ 未检测到 ZECTRIX_API_KEY / ZECTRIX_MAC，自动切换为 dry_run 本地预览")
            args.dry_run = True

    if args.mode in ("report","both"):
        print("📊 加载 03 日报数据...")
        market, community, macro, sentiment = load_all_data()
        report_pages = build_report_pages(market, community, macro, sentiment)
        if args.mode == "both":
            # both 模式：报告占 1,2 页，新闻占 3,4 页
            # 只推送报告的 1,2 页
            report_pages_both = {1: report_pages.get(1,[]), 2: report_pages.get(2,[])}
            print("📤 推送报告部分 (Page 1,2)...")
            push_report_pages(report_pages_both, board_title=board_title, dry_run=args.dry_run,
                              enabled_pages="1,2", api_key=api_key, mac_address=mac)
        else:
            print(f"📤 推送报告 4 页 (Page {pages})...")
            push_report_pages(report_pages, board_title=board_title, dry_run=args.dry_run,
                              enabled_pages=pages, api_key=api_key, mac_address=mac)

    if args.mode in ("news","both"):
        if args.mode == "both":
            news_pages = "3,4"
            print(f"📤 推送新闻部分 (Page {news_pages})...")
            # 新闻 both 模式下，1-2 已被报告占用，所以只生成 3-4 的东方财富
            # 为保持四页不重复，这里直接抓东方财富两页
            titles = get_eastmoney_news(page_size=24, column=args.east_column)
            titles = dedupe_titles(titles)
            render_two_pages(titles, page_ids=["3","4"], label=source_label("eastmoney"),
                             part_names=("一","二"), dry_run=args.dry_run,
                             board_title=board_title, fonts=FONTS, enabled_pages=news_pages)
        else:
            print(f"📤 推送新闻 4 页 (财新+东财)...")
            push_news_pages(source=args.source, pages=pages, east_column=args.east_column,
                            board_title=board_title, dry_run=args.dry_run,
                            api_key=api_key, mac_address=mac)

    print("🎉 墨水屏同步任务执行完毕！")
    if args.dry_run:
        print("💡 预览图已生成：page_*.png")
    return 0

if __name__ == "__main__":
    sys.exit(main())
