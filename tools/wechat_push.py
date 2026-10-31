#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 微信推送工具 (一对一专属推送 · 单页详尽完整版 · 49 源动态抓取)

将 report.html 转换为微信 (PushPlus HTML 模板) 兼容的内联样式 HTML，
生成 wechat.json 供网页按钮使用，并可直接推送至 PushPlus。

核心特点:
  • 一对一专属推送: 默认 PUSHPLUS_TOPIC=''，仅 Token 对应的微信本人接收。
  • 单页完整推送: 每次只推一条完整微信卡片 (单页全文)，解除 19,000 限制 (上限 100,000 字符)，无需分条分发与等待。
  • 社区每四小时采集一次；推送前读取快照并重新分析，保留真实采集时间，严格模式检查 49 源与时效。
  • 00 栏每日 VIX 恐慌指数: 当次抓取 Cboe VIX 点位与日变动，按期权市场未来 30 天隐含波动率
    的正确口径解释；明确区分风险中性 Q 测度与现实 P 测度，缺行情时不回填历史读数。
  • 01 栏每日全球全景扫描: 由 panorama.py 在推送前现算 —— 推动股价的 5 大力量（重点/次要/噪音 ·
    利好/利空 · 0~100 力量分）、宏观事件/板块轮动/情绪变化三大关注面、以及「是否可以做多」的
    合成分结论；四路数据全缺时降级为「本栏不编故事」，不回填历史叙事。
  • 04 栏 AI 预测（未来函数）: 由 forecast.py 在推送前现算下一交易日的逐标的预测（方向 / 预期涨跌幅 /
    预测区间 / 点位区间 / 置信度 / 驱动拆解）与明日盘面倾向；目标日严格晚于行情基准日，
    预测先落盘 forecast_history.json、等目标日行情到位才结算命中率，绝不用当次行情给当次预测打分；
    行情缺席时降级为「今日未获取 —— 本栏不预测」，不回填上一版预测。
  • 全板块 AI 深度详尽分析: 宏观、利率、港股资金流、49 大社区论坛逐一展开长文深度战术研判。
  • 电竞指挥中心 × 战术 HUD 风格：深海军蓝底 + 冷白正文，电光青 / 荧光绿 / 战术紫 / 警戒红分层强调；
    多空卡片按信号着色，所有样式均内联以适配 PushPlus / 微信阅读。

动态抓取管线 (行情 + 社区 + 舆情因子 + 宏观快讯 四路动态):
  python3 market_data.py && python3 community_snapshot.py && python3 macro_data.py && python3 build_site.py
      # ① 抓行情、按四小时更新社区快照、抓宏观快讯 → ② 读取快照分析并建站 (report.html)
      # macro_data.py 是微信 02 栏「全球经济与财经动态」的唯一文案来源：
      # 只渲染 7 天窗口内、发布日期可解析的快讯；抓不到就显示「今日未获取」，不回填历史叙事
      # 兜底口径由 macro_data.availability() 统一判定，覆盖四种形态：文件缺失 / 产物写坏 /
      # 窗口内 0 条 / 读到前几天的旧快照；不可用时条目就地清空，旧闻不会从 01、07 栏漏出
  python3 tools/wechat_push.py --embed                       # ③ 把最新内容内嵌进 report.html
  python3 tools/wechat_push.py --push --scheduled            # ④ 推送 (正文自动注入最新行情/社区/抓取日期)

用法:
  python3 tools/wechat_push.py --emit _site/wechat.json     # 只生成微信版 JSON
  python3 tools/wechat_push.py --embed                       # 把推送内容内嵌进 report.html
  python3 tools/wechat_push.py --push                        # 直接推送到微信 (一对一专属, 严格日期校验)
  python3 tools/wechat_push.py --push --scheduled            # 每天 09:00 (北京时间) 定时推送 (宽松日期校验)
  python3 tools/wechat_push.py --dry-run                     # 验证转换效果与字数统计

Token 解析顺序: --token 参数 > 环境变量 PUSHPLUS_TOKEN > report.html 内的 PUSHPLUS_TOKEN 常量
群组编码解析顺序: --topic 参数 > 环境变量 PUSHPLUS_TOPIC > report.html 内的 PUSHPLUS_TOPIC 常量 (默认空字符串，即一对一专属推送；显式 --topic '' 可覆盖环境变量)
"""
import argparse
import html as html_mod
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import sentiment_match as smatch                          # noqa: E402  采集→匹配→脱敏展示层
import vix_daily                                          # noqa: E402  00 栏「每日 VIX 恐慌指数」统一口径
import panorama                                           # noqa: E402  01 栏「每日全球全景扫描」推理引擎
import macro_data as macro_data_mod                       # noqa: E402  02 栏快讯可用性判定（兜底口径单一事实源）
import community_data as community_mod                    # noqa: E402  49 大社区兜底数据集（缺 community_data.json 时同构生成）
import community_snapshot as community_snapshot_mod        # noqa: E402  四小时快照时效
import quant_pair                                         # noqa: E402  每条内容后的 AI 量化配对
import char_charts                                        # noqa: E402  推送字符配图（matplotlib 图种的字符版）
import forecast as forecast_mod                           # noqa: E402  04 栏「AI 预测 · 未来函数」推理引擎
import rss_feeds as rss_mod                               # noqa: E402  05 栏「49 大财经 RSS 情报源」目录/抓取/兜底

try:
    # 单一事实源：是否对外展示「量化平台现成舆情/新闻因子接入评测（9 阶段实测）」区块
    from sentiment_sources import show_api_eval           # noqa: E402
except Exception:                                          # 注册表缺失/异常时保持默认：隐藏
    def show_api_eval():
        return str(os.environ.get('SENTIMENT_SHOW_API_EVAL', '')).strip().lower() \
            in ('1', 'true', 'yes', 'on')

SOURCE_HTML = os.path.join(REPO_ROOT, 'report.html')
PAGES_URL = 'https://k-macao.github.io/03/'
PUSH_URL = 'https://www.pushplus.plus/send'
TITLE = '章鱼 AI·舆情004'
# 解除限制，支持 100,000 字符
CONTENT_LIMIT = 100000
CONTENT_SAFE_LIMIT = 95000
MAX_PUSH_RETRIES = 3
# 49 大社区（14 原有 + 20 前次新增 + 15 本次扩容）：推送前逐频道核对「最新读取」标记的期望条数
# 直接跟社区目录走：目录扩容后这里无需再手工改数字，避免「兜底 34 源 / 目录 49 源」口径打架。
EXPECTED_CHANNEL_COUNT = len(community_mod.COMMUNITIES)

MINUS = '\u2212'  # U+2212 真正的减号，与全文风格一致

# 49 大社区逐频道「抓取核验 YYYY-MM-DD」标记（用于推送前逐条核对是否确系当天执行过抓取检查，绝不篡改旧日期）
CHANNEL_READ_RE = re.compile(
    r'(?:实抓采样评论|实抓热帖回复|公开页实抓摘录|当次未获取|综合站内)[^<]*?(?:抓取核验|最新读取)\s+(20\d{2}-\d{2}-\d{2})'
)


def load_market_data():
    """读取 market_data.py 生成的 market_data.json（构建时动态抓取的最新行情）。"""
    path = os.environ.get('MARKET_DATA', os.path.join(REPO_ROOT, 'market_data.json'))
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        print(f'⚠️ 警告: market_data.json 读取失败，使用内置兜底数据: {e}', file=sys.stderr)
        return {}


def load_community_data():
    """读取 community_data.py 生成的 community_data.json（49 大社区动态抓取）。

    路径可用环境变量 COMMUNITY_DATA 覆盖；文件缺失/损坏时返回 {}，
    此时正文按严格真实口径将 49 源全部标注为「未获取」（不伪造热评或抓取核验日期）。
    """
    path = os.environ.get('COMMUNITY_DATA', os.path.join(REPO_ROOT, 'community_data.json'))
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        print(f'⚠️ 警告: community_data.json 读取失败，49 源按未获取降级: {e}', file=sys.stderr)
        return {}


def load_sentiment_data():
    """读取 sentiment_factors.py 生成的 sentiment_data.json（量化平台舆情/新闻因子）。

    路径可用环境变量 SENTIMENT_DATA 覆盖；缺失/损坏时返回 {}，
    此时 03B 节点降级为一行说明，不影响推送（与行情、社区相同的容错策略）。
    """
    path = os.environ.get('SENTIMENT_DATA', os.path.join(REPO_ROOT, 'sentiment_data.json'))
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        print(f'⚠️ 警告: sentiment_data.json 读取失败，舆情因子节点降级: {e}', file=sys.stderr)
        return {}



def macro_data_path():
    """02 栏快讯产物路径（环境变量 MACRO_DATA 可覆盖）。"""
    return os.environ.get('MACRO_DATA', os.path.join(REPO_ROOT, 'macro_data.json'))


def bootstrap_macro_data(allow_fetch=None):
    """**只在 CLI 入口调用**：产物缺失/写坏/旧快照时就地补抓一次（构建期兜底）。

    为什么不放进 load_macro_data()：那是渲染路径上的库函数，单测会直接调
    build_single_wechat_html() 来断言「四路数据全缺时必须降级」。若补抓藏在里面，
    CI 里（GITHUB_ACTIONS=true 默认开补抓）单测就会真的联网抓一轮并拿到快讯，
    「本栏不编故事」这类降级断言随即挂掉 —— 2026-09-17 合并后 deploy job 的
    「🧪 舆情层自检」步骤就是这样红的（本地无外网所以复现不出来）。
    联网属于入口的副作用，不属于渲染函数。
    """
    res = macro_data_mod.ensure(macro_data_path(), allow_fetch=allow_fetch)
    if res['action'] == 'fetched':
        print(f'  🔄 微信推送：构建期补抓宏观快讯 {res["kept"]} 条 '
              f'（{res["elapsed_ms"]} ms）→ {res["path"]}', file=sys.stderr)
    elif res['action'] == 'refetch_unavailable':
        print(f'  ⚠️ 微信推送：宏观快讯补抓后仍无可用条目（{res["reason"]}）', file=sys.stderr)
    return res


def load_macro_data():
    """读取 macro_data.py 生成的 macro_data.json（02 栏宏观/财经快讯，每次构建现抓）。

    路径可用环境变量 MACRO_DATA 覆盖。**本函数是纯读取，绝不联网**（补抓在
    bootstrap_macro_data() 里、只由 CLI 入口触发，见该函数注释）。
    缺失/损坏时返回 {}，此时 02 栏渲染为「今日宏观快讯未获取」+ 实时行情快照 ——
    绝不回填历史文案（2026-09-16 旧内容事故根因）。
    """
    path = macro_data_path()
    data = macro_data_mod.load_json(path)
    avail = macro_data_mod.availability(data)
    if not avail['ok']:
        if avail['reason'] == 'no_file':
            print('  ⚠️ 微信推送：未找到 macro_data.json → 02 栏降级为「快讯未获取 + 实时行情」',
                  file=sys.stderr)
        else:
            # bad_type / no_items / stale_snapshot：交给 02 栏兜底文案处理，
            # 绝不让错误类型流进渲染层（会整篇推送崩掉）。
            print(f'  ⚠️ 微信推送：宏观快讯不可用（{avail["reason"]}）'
                  f' → 02 栏走「今日未获取」兜底', file=sys.stderr)
        return {}
    return data


def rss_feeds_path():
    """05 栏 RSS 情报源产物路径（环境变量 RSSFEEDS_DATA 可覆盖）。"""
    return os.environ.get('RSSFEEDS_DATA', os.path.join(REPO_ROOT, 'rssfeeds_data.json'))


def bootstrap_rss_feeds(allow_fetch=None):
    """**只在 CLI 入口调用**：产物缺失/写坏/旧快照时就地补抓一次（与 02 栏同一策略）。

    与 bootstrap_macro_data 同样的理由：联网是入口的副作用，不能藏在渲染函数里，
    否则 CI 里单元测试会真的联网抓 49 个源，降级断言就不确定了。
    """
    res = rss_mod.ensure(rss_feeds_path(), allow_fetch=allow_fetch)
    if res['action'] == 'fetched':
        print(f'  🔄 微信推送：构建期补抓 RSS 情报源 {res["items"]} 条 '
              f'（{res["elapsed_ms"]} ms）→ {res["path"]}', file=sys.stderr)
    elif res['action'] == 'refetch_unavailable':
        print(f'  ⚠️ 微信推送：RSS 情报源补抓后仍无可用条目（{res["reason"]}）', file=sys.stderr)
    return res


def load_rss_feeds():
    """读取 rssfeeds_data.json（05 栏 49 大财经 RSS 情报源）。**纯读取，绝不联网**。

    缺失/写坏/旧快照一律返回 {}，05 栏渲染为「今日未获取」——
    不回填历史标题、不编造「今日重点」（与 02 栏同一反陈旧口径）。
    """
    path = rss_feeds_path()
    data = rss_mod.load_json(path)
    avail = rss_mod.availability(data)
    if not avail['ok']:
        if avail['reason'] == 'no_file':
            print('  ⚠️ 微信推送：未找到 rssfeeds_data.json → 05 栏降级为「今日未获取」',
                  file=sys.stderr)
        else:
            print(f'  ⚠️ 微信推送：RSS 情报源不可用（{avail["reason"]}）'
                  f' → 05 栏走「今日未获取」兜底', file=sys.stderr)
        return {}
    return data


def fit_rss_block(html, rss_data):
    """把 05 栏按剩余字符预算放进微信单页（详 → 简 → 名录；每一版都保留全部 49 源）。

    目录是固定的 49 条，逐源一行本来就短；这里仍走预算收敛，避免 03/04 栏先吃掉预算时
    把 05 栏挤到超线。任何一版都含 49 源与可用/未获取标记，只是详略不同。
    """
    if RSS_SLOT not in html:
        return html
    budget = CONTENT_SAFE_LIMIT - (len(html) - len(RSS_SLOT)) - RSS_MARGIN
    for level, label in (('full', '完整版'), ('standard', '标准版'),
                         ('roster', '名录版'), ('compact', '缩略版')):
        block = wechat_box(rss_mod.render_wechat(rss_data, level=level)) if rss_data else \
            wechat_box(rss_mod.render_wechat({}, level=level))
        if len(block) <= budget:
            if level != 'full':
                print(f'  ✂️ 微信推送 05 栏：剩余预算 {budget} 字符，本次采用「{label}」'
                      '（每一版都保留全部 49 源）')
            return html.replace(RSS_SLOT, block, 1)
    block = wechat_box(rss_mod.render_wechat(rss_data, level='compact'))
    print('  ⚠️ 微信推送 05 栏：剩余预算不足以放下名录版，仍以「缩略版」发出 49 源',
          file=sys.stderr)
    return html.replace(RSS_SLOT, block, 1)


def _esc_w(t):
    return html_mod.escape(str(t if t is not None else ''), quote=False)


def quant_html_inline(quant):
    if not quant:
        return ""
    s = quant.get('sentiment', {})
    e = quant.get('event', {})
    r = quant.get('relevance', {})
    n = quant.get('novelty', {})
    # 四行要点用 <br/> 串起来而不是四个带 style 的 <div>：微信单页字符预算很紧，
    # 49 张卡片 × 4 组重复样式是一笔白花的开销，显示效果一致。
    return (
        f'<div style="background:{WECHAT_PANEL_SOFT};color:{WECHAT_TEXT_SOFT};border:1px dashed {WECHAT_VIOLET};border-radius:4px;'
        f'padding:9px 11px;margin-top:9px;font-size:11px;line-height:1.7">'
        f'<div style="color:{WECHAT_CYAN};font-weight:700;font-size:12px">◆ 核心量化指标</div>'
        f'◦ <strong>实体级情感得分：</strong>{s.get("display","—")}<br/>'
        f'◦ <strong>新闻细分事件分类：</strong>{e.get("label","综合")}<br/>'
        f'◦ <strong>相关性得分：</strong>{r.get("display","—")}<br/>'
        f'◦ <strong>新颖度得分：</strong>{n.get("display","—")}'
        f'</div>'
    )


def quant_metrics_line(quant):
    """核心量化指标压成一行（社区「标准版」用）：四个读数一个不少，只压样式。"""
    if not quant:
        return ''
    s = (quant.get('sentiment') or {}).get('display', '—')
    e = (quant.get('event') or {}).get('label', '综合')
    r = (quant.get('relevance') or {}).get('display', '—')
    n = (quant.get('novelty') or {}).get('display', '—')
    return (
        f'<div style="background:{WECHAT_PANEL_SOFT};border:1px dashed {WECHAT_VIOLET};border-radius:3px;'
        f'padding:5px 8px;margin-top:6px;font-size:10.5px;color:{WECHAT_TEXT_SOFT};">'
        f'◆ 核心量化指标：情感 {s} · 事件 {e} · 相关性 {r} · 新颖度 {n}</div>'
    )


def _clip_text(text, limit):
    text = str(text or '')
    if len(text) <= limit:
        return text
    return text[:limit].rstrip(' ·、/，,') + '…'


def community_evidence_wechat(c, level='full'):
    """Compact, escaped provenance; longer tiers also retain additional sample replies."""
    comments = [x for x in (c.get('comments') or []) if isinstance(x, dict)][:3]
    url = community_mod.safe_source_url(c.get('source_url'))
    link = (f'<a href="{html_mod.escape(url, quote=True)}" style="color:{WECHAT_CYAN};">原帖/原页</a>'
            if url else '')
    if not comments:
        return f'<div style="font-size:10px">{link}</div>' if link else ''
    first = comments[0]
    details = [first.get('published_label') or '发布时间未知', first.get('scope') or '有限样本']
    if c.get('matched_comments') is not None:
        visible = len(comments) if level in ('full', 'standard') else 1
        details.append(f"匹配 {c.get('matched_comments')} 条，展示 {visible} 条")
    line = _esc_w(' · '.join(str(x) for x in details if x))
    result = f'<div style="color:{WECHAT_MUTED};font-size:10px;">{line}{" · " + link if link else ""}</div>'
    if level in ('full', 'standard'):
        for item in comments[1:]:
            metric = (item.get('metric') or {}).get('label') or '热度未知'
            text = _clip_text(item.get('text') or '', 160)
            if item.get('text_truncated') and not text.endswith('…'):
                text += '…'
            title = _clip_text(item.get('post_title') or '', 60)
            published = item.get('published_label') or '发布时间未知'
            item_url = community_mod.safe_source_url(item.get('url'))
            item_link = (f' · <a href="{html_mod.escape(item_url, quote=True)}" '
                         f'style="color:{WECHAT_CYAN};">原帖</a>') if item_url else ''
            result += (f'<div style="font-size:10.5px;line-height:1.7">· 【{_esc_w(title)}】{_esc_w(text)} '
                       f'<span style="color:{WECHAT_MUTED};">{_esc_w(metric)} · {_esc_w(published)}{item_link}</span></div>')
    return result


def community_card(c, level='full', quotes=None):
    """一张社区卡片（严格真实口径：实抓源展示真实摘录与词库指标，未获取源如实标注且绝不编造研判或指标）。"""
    icon = c.get('icon', '📌')
    no = c.get('id', '01')
    raw_name = c.get('name', '未知社区')
    name = _esc_w(raw_name)
    vclass = c.get('vclass') or c.get('verdict_class') or 'unavailable'
    is_avail = bool(c.get('available', vclass != 'unavailable')) and vclass != 'unavailable'
    label = _esc_w(c.get('label') or c.get('verdict_label') or ('未获取' if not is_avail else '中性'))
    raw_quote = c.get('quote') or ''
    raw_verdict = c.get('verdict') or ''
    ctype = c.get('ctype') or ''
    quant = (c.get('quant') or None) if is_avail else None
    meta = _esc_w(c.get('meta') or '')
    signal = {'bull': WECHAT_CYAN, 'bear': WECHAT_DANGER,
              'neutral': WECHAT_VIOLET, 'mixed': WECHAT_NEON,
              'unavailable': '#7f8ea8'}.get(vclass, '#7f8ea8')
    if is_avail:
        rec = quant_pair.recommend(f'{raw_name} {ctype} {raw_quote} {raw_verdict}', quotes or {}, hint=c.get('key'))
        ai_compact = quant_pair.render_wechat(rec, compact=True)
    else:
        rec = quant_pair.recommend(f'{raw_name} {ctype}', quotes or {}, hint=c.get('key'))
        ai_compact = quant_pair.render_wechat(
            rec, compact=True, note='社区热评未获取，配对仅依当次实时行情计算')

    chip = (f'<span style="background:{signal};color:#07101b;font-size:10px;font-weight:700;'
            f'padding:2px 6px;margin-left:4px;border-radius:2px">{label}</span>')
    head = (f'<div style="color:{WECHAT_TEXT};font-weight:700;font-size:13px">{icon} {no}. {name} '
            f'{chip}</div>')
    meta_html = f'<div style="color:{WECHAT_MUTED};font-size:10px;margin-top:6px;">{meta}</div>'
    evidence_html = community_evidence_wechat(c, level) if is_avail else ''
    quote_label = {'comment': '采样评论', 'thread_reply': '热帖回复',
                   'demo': '演示样本'}.get(c.get('content_kind'), '公开页摘录（未核验为评论）')

    if level == 'roster':
        pair = (f'{rec.get("domain_pair", "")}｜{rec.get("pair_label", "")}｜{rec.get("stance", "")}')
        pair_html = ('' if quant_pair.is_hidden(rec)
                     else f' · <span style="color:#7d7d7d">跨域配对：{pair}</span>')
        clip_limit = 140 if is_avail else 36
        prefix = quote_label + '：' if is_avail else ''
        return (
            f'<div style="border-bottom:1px solid #1b2540;padding:5px 0;font-size:11px;color:{WECHAT_TEXT_SOFT};">'
            f'{icon} <strong style="color:{WECHAT_TEXT};">{no}. {name}</strong>【{label}】'
            f'{pair_html} · {prefix}{_esc_w(_clip_text(raw_quote, clip_limit))}'
            f'{evidence_html}<div style="color:{WECHAT_MUTED};font-size:9.5px;">{meta}</div></div>'
        )

    q_label = quote_label + '：' if is_avail else '抓取状态：'
    if level == 'compact':
        body = (f'<div style="margin-top:6px;line-height:1.8">'
                f'<strong style="color:{WECHAT_CYAN};">{q_label}</strong>{_esc_w(_clip_text(raw_quote, 160))}</div>')
    elif level == 'compact_plus':
        body = (f'<div style="margin-top:6px;line-height:1.8">'
                f'<strong style="color:{WECHAT_CYAN};">{q_label}</strong>{_esc_w(_clip_text(raw_quote, 220))}</div>')
    elif level == 'standard':
        v_box = (
            f'<div style="background:{WECHAT_PANEL_SOFT};border:1px solid #24314b;border-left:3px solid {signal};'
            f'border-radius:3px;padding:8px 10px;margin-top:8px;font-size:11.5px;color:{WECHAT_TEXT_SOFT};line-height:1.7">'
            f'<strong style="color:{WECHAT_CYAN};">▶ 词库极性与摘录说明：</strong>{_esc_w(_clip_text(raw_verdict, 150))}</div>'
            if (is_avail and raw_verdict) else ''
        )
        body = (f'<div style="margin-top:6px;line-height:1.8">'
                f'<strong style="color:{WECHAT_CYAN};">{q_label}</strong>{_esc_w(_clip_text(raw_quote, 240))}</div>'
                f'{v_box}')
    else:  # full
        v_box = (
            f'<div style="background:{WECHAT_PANEL_SOFT};border:1px solid #24314b;border-left:3px solid {signal};'
            f'border-radius:3px;padding:8px 10px;margin-top:8px;font-size:11.5px;color:{WECHAT_TEXT_SOFT};line-height:1.7">'
            f'<strong style="color:{WECHAT_CYAN};">▶ 词库极性与摘录说明：</strong>{_esc_w(raw_verdict)}</div>'
            if (is_avail and raw_verdict) else ''
        )
        body = (f'<div style="margin-top:6px;line-height:1.8">'
                f'<strong style="color:{WECHAT_CYAN};">{q_label}</strong>{_esc_w(raw_quote)}</div>'
                f'{v_box}')

    quant_html = quant_html_inline(quant) if level == 'full' else (
        quant_metrics_line(quant) if level in ('standard', 'compact_plus') else '')
    ai_html = ai_compact if level in ('full', 'standard') else quant_pair.render_wechat_mini(rec)
    return (
        f'<div style="background:{WECHAT_PANEL};color:{WECHAT_TEXT_SOFT};border:1px solid {WECHAT_BORDER};'
        f'border-left:3px solid {signal};border-radius:4px;padding:12px 14px;margin:10px 0;font-size:12px">'
        f'{head}{body}{evidence_html}{quant_html}{ai_html}{meta_html}</div>'
    )


def community_section(communities, level='full', quotes=None):
    return '\n'.join(community_card(c, level, quotes) for c in communities)


# 03 栏在正文里的占位槽：49 源社区按剩余预算选详略等级（完整 → 标准 → 紧凑 → 名录）
COMMUNITY_SLOT = '<!--COMMUNITY-SLOT-->'
COMMUNITY_MARGIN = 1500

# 04 栏在正文里的占位槽：正文先留槽，量完其余部分再决定这一栏能放多详尽的一版。
FORECAST_SLOT = '<!--FORECAST-SLOT-->'
# 预算留白：给日期替换、内嵌脚本等后续步骤留出余量，别把安全线吃满
FORECAST_MARGIN = 1200

# 05 栏（49 大财经 RSS 情报源）在正文里的占位槽：正文先留槽，量完其余部分再决定详略。
RSS_SLOT = '<!--RSS-SLOT-->'
RSS_MARGIN = 1200
# PushPlus 使用全内联样式，配色和网页报告共享电竞 HUD 色板。
WECHAT_BG = '#060811'
WECHAT_PANEL = '#10172a'
WECHAT_PANEL_SOFT = '#121b30'
WECHAT_TEXT = '#edf2ff'
WECHAT_TEXT_SOFT = '#c1cce4'
WECHAT_MUTED = '#9aa6c3'
WECHAT_BORDER = '#2b3855'
WECHAT_CYAN = '#4fe5ff'
WECHAT_GREEN = WECHAT_CYAN  # 保留旧参数名，供预测/全景渲染器作强调色使用
WECHAT_NEON = '#b6ff4a'
WECHAT_VIOLET = '#9673ff'
WECHAT_PINK = '#ff4d9a'
WECHAT_DANGER = '#ff6b7d'
WECHAT_INK = WECHAT_TEXT


def wechat_box(inner):
    """微信推送中统一使用的深色 HUD 面板外框。"""
    return (f'<div style="background:{WECHAT_PANEL};color:{WECHAT_TEXT};'
            f'border:1px solid {WECHAT_BORDER};border-radius:4px;'
            f'padding:14px 16px;margin:10px 0;font-size:12px;line-height:1.85;">{inner}</div>')


def forecast_block_candidates(fc_data, neon=WECHAT_NEON, green=WECHAT_GREEN, ink=WECHAT_INK):
    """04 栏的候选版本（从详到略）。拆成函数是为了让 03 栏知道「最少要给 04 留多少」。"""
    box = wechat_box
    full = box(forecast_mod.render_wechat(fc_data, neon=neon, green=green, ink=ink))
    charts = char_charts.forecast_chart(fc_data)[0]
    if (fc_data.get('review') or {}).get('settled'):
        charts += char_charts.forecast_review_chart(fc_data['review'])[0]
    return [
        ('完整版 + 字符配图', full + '\n  ' + charts),
        ('完整版', full),
        ('精简版 + 预测配图', box(forecast_mod.render_wechat(
            fc_data, neon=neon, green=green, ink=ink, compact=True))
            + '\n  ' + char_charts.forecast_chart(fc_data)[0]),
        ('精简版', box(forecast_mod.render_wechat(
            fc_data, neon=neon, green=green, ink=ink, compact=True))),
        ('一行摘要', box(forecast_mod.render_wechat_line(fc_data, green=green))),
    ]


def fit_community_block(html, communities, fc_data=None, quotes=None, rss_min=0):
    """把 49 源社区塞进微信单页的剩余预算里（与 04 栏同一套「按预算收敛」思路）。

    社区从 14 源扩到 49 源之后，逐条完整卡片会直接顶穿 100K 硬上限，
    因此 03 栏按剩余预算逐级收敛（每一档都保留全部 49 源、都带跨域配对与抓取标记）：

        完整版（热评 + 研判 + 四行量化指标 + 完整 AI 量化）
          → 标准版（热评 + 研判 + 一行量化指标 + 精简 AI 量化）
            → 紧凑版（热评截断 + 精简 AI 量化）
              → 名录版（一行一名：跨域配对 + 推荐 + 抓取标记）

    预算里先给 04 栏留出**最小可用**的一版（一行摘要 + 余量），否则 49 源会把预测栏挤没；
    真正发哪一版预测由随后的 fit_forecast_block() 按实际剩余预算决定。
    """
    if COMMUNITY_SLOT not in html:
        return html
    base = len(html) - len(COMMUNITY_SLOT)
    reserves = [0]
    if fc_data is not None:
        try:
            candidates = forecast_block_candidates(fc_data)
            # 优先给 04 栏留「完整版」，放不下再退到最小一版（一行摘要）
            full_fc = next((len(b) for lbl, b in candidates if lbl == '完整版'), 0)
            # 05 栏（49 大财经 RSS 情报源）也要占预算：按「最简一版」预留，
            # 否则 03 栏把预算吃满后 05 栏只能超线或被挤掉。
            reserves = [full_fc + rss_min, min(len(b) for _lbl, b in candidates) + rss_min]
        except Exception:
            reserves = [0]
    levels = [('完整版', 'full'), ('标准版', 'standard'), ('精简版', 'compact_plus'),
              ('紧凑版', 'compact'), ('名录版', 'roster')]
    for reserve in reserves:
        budget = CONTENT_SAFE_LIMIT - base - reserve - COMMUNITY_MARGIN
        for label, level in levels:
            block = community_section(communities, level, quotes=quotes)
            if len(block) <= budget:
                if level != 'full':
                    print(f'  ✂️ 微信推送 03 栏：{len(communities)} 源社区按剩余预算 '
                          f'{budget} 字符采用「{label}」（每一版都含全部源与跨域配对，仅详略不同；'
                          f'04 栏预留 {reserve} 字符）')
                return html.replace(COMMUNITY_SLOT, block, 1)
    block = community_section(communities, 'roster', quotes=quotes)
    print(f'  ⚠️ 微信推送 03 栏：预算不足以放下完整名录，仍以「名录版」发出 '
          f'{len(communities)} 源（每源保留跨域配对与抓取标记）', file=sys.stderr)
    return html.replace(COMMUNITY_SLOT, block, 1)


def fit_forecast_block(html, fc_data, neon=WECHAT_NEON, green=WECHAT_GREEN, ink=WECHAT_INK,
                       rss_min=0):
    """把 04 栏塞进微信单页剩余的字符预算里。

    微信单页有 CONTENT_LIMIT 硬上限、CONTENT_SAFE_LIMIT 推送门禁，超了整条推送会被直接拦下。
    04 栏不能把 00 VIX 与其它既有栏目挤掉，所以这里按**剩余预算**逐级收敛：

        完整版（逐标的表 + 驱动拆解 + 三路信号 + 回看 + 两张字符配图）
          → 精简版（倾向 + 逐标的表 + 回看一行）
            → 一行摘要（倾向 + 龙头标的区间）
              → 只留一句说明，指向网页 04 节

    不管落到哪一级，**数字都来自同一份预测**，只是详略不同 —— 不会出现两端口径打架。
    """
    if FORECAST_SLOT not in html:
        return html
    budget = (CONTENT_SAFE_LIMIT - (len(html) - len(FORECAST_SLOT)) - FORECAST_MARGIN - rss_min)
    candidates = forecast_block_candidates(fc_data, neon=neon, green=green, ink=ink)
    for label, block in candidates:
        if len(block) <= budget:
            if label != '完整版 + 字符配图':
                print(f'  ✂️ 微信推送 04 栏：单页剩余预算 {budget} 字符，'
                      f'本次采用「{label}」（完整逐标的拆解见网页 04 节）')
            return html.replace(FORECAST_SLOT, block, 1)
    print('  ⚠️ 微信推送 04 栏：剩余预算不足以放下任何一版预测，本栏只留指引，'
          '完整内容见网页 04 节', file=sys.stderr)
    return html.replace(
        FORECAST_SLOT,
        '<div style="font-size:11px;line-height:1.8;color:#9aa6c3;">'
        'AI 预测（未来函数）本次因微信单页字符预算不足未随推送发出，完整内容见网页 04 节。</div>',
        1)


def build_single_wechat_html(now=None):
    """构建单页完整的微信 HTML 推送卡片。

    动态数据:
      - market_data.json: 行情数字、行情快照
      - community_data.json: 四小时采集快照，读取后基于原文重新分析。
      文件缺失时标为未获取，不伪造核验日期或论坛评论。
    """
    now = now or datetime.now(timezone.utc)
    ts = now.strftime('%Y-%m-%d %H:%M UTC')
    ts_full = now.strftime('%Y-%m-%d %H:%M:%S UTC')
    quant_pair.reset_hidden_render_count()      # 行情不足的隐藏处数：本次构建单独计数

    GR = WECHAT_CYAN
    NEON = WECHAT_NEON
    INK = WECHAT_TEXT

    # ---------- 动态行情注入 (market_data.json) ----------
    _md = load_market_data()
    _quotes = _md.get('quotes') or {}
    _fetch_date = _md.get('fetch_date') or now.strftime('%Y-%m-%d')

    # ---------- 动态社区注入 (community_data.json) ----------
    _cd = community_mod.reanalyze_snapshot(load_community_data(), now=now)
    # ---------- 动态舆情因子注入 (sentiment_data.json) ----------
    _sd = load_sentiment_data()
    # ---------- 动态宏观/财经快讯注入 (macro_data.json) — 02 栏唯一文案来源 ----------
    _xd = load_macro_data()
    # ---------- 动态 RSS 情报源注入 (rssfeeds_data.json) — 05 栏唯一文案来源 ----------
    _rss = load_rss_feeds()
    # 兜底保障（单一拦截点）：快讯不可用时把条目**就地清空**，而不是只在 02 栏渲染兜底文案。
    # 否则一份过期快照仍会从 07 栏结论、01 栏全景扫描的证据链里漏出去 —— 兜底必须是全栏一致的。
    _xd_avail = macro_data_mod.availability(_xd, now=now)
    if not _xd_avail['ok'] and (_xd.get('categories') or {}):
        _xd = dict(_xd)
        _xd['categories'] = {k: {'label': (v or {}).get('label') or k, 'items': []}
                             for k, v in (_xd.get('categories') or {}).items()}
        _xd['summary'] = dict(_xd.get('summary') or {}, kept_items=0)
        _xd['unavailable'] = _xd_avail
        print(f'  🛟 微信推送：宏观快讯判定为不可用（{_xd_avail["reason"]}），'
              f'已清空条目以免旧闻从 01/07 栏漏出；{_xd_avail["detail"]}')
    _communities_raw = _cd.get('communities') or []
    # 社区抓取日期优先取社区数据的 fetch_date，否则取行情的 fetch_date
    _community_fetch_date = _cd.get('fetch_date') or _fetch_date
    # 如果社区数据存在，用社区的 fetch_date 覆盖行情的 fetch_date 用于统一日期显示
    if _cd.get('fetch_date'):
        _fetch_date = _cd.get('fetch_date')

    def qq(key, fb='\u2014'):
        """最新价，缺失用兜底值。现货黄金 >=1000 时取整数千分位。"""
        q = _quotes.get(key)
        if not q or q.get('last') is None:
            return fb
        v = float(q['last'])
        nd = int(q.get('decimals') or 2)
        if q.get('name') == '现货黄金' and v >= 1000:
            nd = 0
        return f'{v:,.{nd}f}'

    def pct(key, fb='\u2014'):
        """涨跌幅，如 '−0.83%' / '+0.25%'。"""
        q = _quotes.get(key)
        if not q or q.get('pct') is None:
            return fb
        v = float(q['pct'])
        sign = MINUS if v < 0 else '+'
        return f'{sign}{abs(v):,.2f}%'

    def chg_desc(fb=''):
        """恒指涨跌描述，如 '跌 182.40 点'；缺失返回 fb（默认空串，不再兜底历史数字）。"""
        q = _quotes.get('HSI')
        if not q or q.get('chg') is None:
            return fb
        v = float(q['chg'])
        verb = '跌' if v < 0 else '涨'
        return f'{verb} {abs(v):,.2f} 点'

    def dq(fb=''):
        """恒指行情日期，如 '8 月 28 日'；缺失返回 ''，由调用方省略日期而不是回填旧日期。"""
        a = (_quotes.get('HSI') or {}).get('as_of') or ''
        m = re.match(r'20\d{2}-(\d{2})-(\d{2})', a)
        return fb if not m else f'{int(m.group(1))} 月 {int(m.group(2))} 日'

    def asof(key, fb='\u2014'):
        """行情日期 YYYY-MM-DD。"""
        return (_quotes.get(key) or {}).get('as_of') or fb

    def fetch_status():
        """数据源同步状态文案。"""
        s = _md.get('summary') or {}
        ok, total, failed = s.get('ok'), s.get('total'), s.get('failed') or []
        gen = _md.get('generated_at') or ''
        if ok is None:
            return f'抓取于 {gen}'
        if total == ok:
            return f'{ok}/{total} 项行情同步成功'
        failed_str = "、".join(failed) if failed else ""
        return f'{ok}/{total} 项同步成功（{failed_str} 降级为 —）'

    def community_fetch_status():
        """社区抓取状态文案（严格真实口径：如实披露实抓成功数与未获取数）"""
        if not _cd:
            return '未读到 community_data.json，49 源全部标注「未获取」（不编造热评）'
        s = _cd.get('summary') or {}
        ok, total = s.get('ok'), s.get('total') or len(_communities_raw)
        gen = _cd.get('generated_at') or ''
        mode = _cd.get('mode') or ''
        coverage = (f' · 采样评论/热帖回复 {s.get("comment_sources", 0)} 源'
                    f'，普通页摘录 {s.get("page_excerpt_sources", 0)} 源（非热评）'
                    if 'comment_sources' in s else '')
        stats = _cd.get('analysis_stats') or {}
        if _cd.get('analysis_at'):
            coverage += f' · 本次读取分析 {_cd["analysis_at"]}（重算 {stats.get("reanalyzed", 0)} 源）'
        if stats.get('legacy_retained'):
            coverage += f' · {stats["legacy_retained"]} 源旧格式保留原分析'
        if mode == 'demo':
            return f'本地演示模式（{total} 条演示样本，非实盘抓取）· 生成于 {gen}'
        if mode == 'offline':
            reason = ('采集任务异常，结果不可用' if (_cd.get('snapshot') or {}).get('status') == 'collector_error'
                      else '离线兜底，未读取到社区数据')
            return f'{reason} · {total} 源均未获取，不伪造单源核验 · 记录于 {gen}'
        if ok is None:
            return f'社区 {len(_communities_raw)} 源已加载 · 生成于 {gen}'
        if ok == 0:
            return f'0/{total} 个社区实抓成功（未获取源均已标注，绝不回填模板）· 核验于 {gen}'
        if ok == total:
            return f'{ok}/{total} 个社区源实抓成功{coverage} · 抓取于 {gen}'
        return f'{ok}/{total} 个社区实抓成功，{total - ok} 个标注「未获取」{coverage} · 抓取于 {gen}'

    def key(t):
        return (f'<strong style="background:#101b2c;color:{NEON};font-weight:700;'
                f'padding:1px 5px;border:1px solid #435d31;border-radius:2px;">{t}</strong>')

    def h(t):
        return (f'<div style="color:{INK};font-family:\'Rajdhani\',\'Noto Sans SC\',\'Microsoft YaHei\',sans-serif;font-size:16px;'
                f'font-weight:700;border-left:4px solid {GR};border-bottom:1px solid {WECHAT_BORDER};'
                f'background:#0a1020;padding:7px 10px;margin:24px 0 10px;">{t}</div>')

    def sub(t):
        return f'<div style="color:{GR};font-weight:700;font-size:13px;margin-bottom:6px;">{t}</div>'

    def box(inner):
        return (f'<div style="background:{WECHAT_PANEL};color:{WECHAT_TEXT_SOFT};'
                f'border:1px solid {WECHAT_BORDER};border-radius:4px;'
                f'padding:14px 16px;margin:10px 0;font-size:12px;line-height:1.85;">{inner}</div>')

    # ---------- 动态社区列表（49 源：14 原有 + 20 前次新增 + 15 本次扩容，中英文/多语种/不同类型） ----------
    # 缺 community_data.json 时走 community_data.offline_dataset()：同一个模板引擎现算 49 条，
    # 结构与 live 完全一致（只把 source 标记为 fallback），不再在推送工具里另写一份兜底文案 ——
    # 两处各写一套正是「兜底 14 源」与「动态 49 源」口径打架的根源。
    def community_record(c):
        """community_data.json（或离线「未获取」数据集）→ 渲染层统一的记录结构。

        严格真实口径：绝不覆盖或篡改每条记录原有的 meta 核验日期，也绝不为未获取源伪造 quant 指标。
        """
        vclass = c.get('verdict_class') or 'unavailable'
        is_avail = bool(c.get('available', vclass != 'unavailable')) and vclass != 'unavailable'
        ctype = c.get('ctype', '')
        fdate = c.get('fetch_date') or ''
        if c.get('meta'):
            meta = c['meta']
        elif is_avail and fdate:
            meta = f"公开页实抓摘录 · {ctype} · 抓取核验 {fdate}"
        elif fdate:
            meta = f"当次未获取 · {ctype} · 抓取核验 {fdate}"
        else:
            meta = f"未执行社区抓取 · {ctype}"
        return {
            'icon': c.get('icon', '📌'),
            'id': c.get('id', '01'),
            'key': c.get('key', ''),
            'name': c.get('name', '未知社区'),
            'available': is_avail,
            'label': c.get('verdict_label') or ('未获取' if not is_avail else '中性'),
            'vclass': vclass,
            'quote': c.get('quote', ''),
            'verdict': c.get('verdict', '') if is_avail else '',
            'quant': c.get('quant') if is_avail else None,
            'meta': meta,
            'ctype': ctype,
            'content_kind': c.get('content_kind'),
            'source_url': c.get('source_url', ''),
            'comments': c.get('comments') or [],
            'matched_comments': c.get('matched_comments'),
        }

    communities = []
    if _communities_raw:
        communities = [community_record(c) for c in _communities_raw]
        _live_cnt = sum(1 for c in communities if c.get('available'))
        print(f'  🧩 微信推送：已加载 {len(communities)} 个社区目录源'
              f'（实抓可用 {_live_cnt} 个 · 未获取 {len(communities) - _live_cnt} 个）')
    else:
        _fb = community_mod.offline_dataset(_md, now=now)
        communities = [community_record(c) for c in _fb.get('communities', [])]
        print(f'  ⚠️ 微信推送：未找到 community_data.json，49 源按严格真实口径全部标注为「未获取」（不伪造热评或核验日期）')

    # ---------- 03B 舆情/新闻因子节点（sentiment_data.json 动态注入） ----------
    def sentiment_block():
        if not _sd:
            return box('<strong style="color:#edf2ff;">AI 多空总览统计</strong> 舆情因子节点待生成：'
                       '在境内出口执行 <strong>python3 sentiment_factors.py --live</strong>'
                       '（或 <strong>--mock</strong> 离线回放）后重建，'
                       '即可注入「标的匹配 + 因子读数」（对外不显示数据来源）。'
                       + quant_pair.render_wechat(quant_pair.recommend(
                           '舆情因子未获取', _quotes, hint='sentiment'),
                           note='因子节点未生成，配对只使用当次行情。')) + char_charts.sentiment_chart(_sd)[0]
        m = _sd.get('market') or {}
        mm = _sd.get('matches') or {}
        anon = not smatch.show_source()
        rows = []

        def clean(v, limit=0):
            """对外文案：脱敏（不显示数据来源）→ 截断。"""
            t = smatch.redact(v) if anon else str(v if v is not None else '')
            return (t[:limit].rstrip(' ·、/，,') + '…') if limit and len(t) > limit else t

        rows.append(
            '<div style="background:#10172a;border:2px solid #2b3855;border-left:3px solid #4fe5ff;'
            'border-radius:6px;padding:12px 14px;margin:10px 0;font-size:12px;color:#edf2ff;line-height:1.85;">'
            + sub('◆ 市场舆情因子读数（多量化平台合并采集 · 可回测口径 · 不显示数据来源）')
            + key(f"舆情温度计 {m.get('sent_temp', '—')} · {m.get('label', '—')}")
            + f"　净情感 {m.get('net_senti', '—')}　负面占比 {m.get('neg_share', '—')}%"
              f"　热度 {m.get('heat_z', '—')}σ　风险分 {m.get('risk_score', '—')}"
              f"<br/>采集新闻 <strong>{m.get('news_count', 0)}</strong> 条，其中平台现成因子 "
              f"<strong>{m.get('platform_native', 0)}</strong> 条、自建词库打分 "
              f"<strong>{m.get('self_built', 0)}</strong> 条"
              f"<br/>匹配日报标的 <strong>{mm.get('matched_news', 0)}</strong> 条"
              f"（匹配率 {(mm.get('coverage') or 0) * 100:.1f}%）· 采集关键词 "
              f"{'、'.join(mm.get('keywords_used') or []) or '—'}"
              f"<br/><span style=\"color:#9aa6c3;font-size:10px;\">数据日期 {_sd.get('fetch_date', '—')} · "
              + smatch.status_line(_sd)
              + f" · {'离线回放（fixtures）' if _sd.get('mode') == 'mock' else '联网实测'}</span>"
              + quant_pair.render_wechat(quant_pair.recommend(
                  f"市场舆情 {m.get('label') or ''} 净情感 {m.get('net_senti')}",
                  _quotes, hint='sentiment'))
              + '</div>')

        def row(icon, title, body):
            return ('<div style="background:#10172a;border:1px solid #2b3855;border-radius:6px;'
                    'padding:9px 12px;margin:8px 0;font-size:11.5px;color:#edf2ff;line-height:1.8;">'
                    f'<strong style="color:#edf2ff;">{icon} {title}</strong>　{body}</div>')

        for t in (mm.get('targets') or [])[:8]:
            top = (t.get('top_titles') or [{}])[0]
            rec = quant_pair.recommend(
                f"{t.get('name', '')} {top.get('title') or ''}", _quotes, hint=t.get('key'))
            rows.append(row('🎯', f"{t.get('name', '')}　命中 {t.get('hits', 0)} 条",
                            f"净情感 {t.get('net_senti')}　负面 {t.get('neg_share')}%"
                            f"　风险 {t.get('risk_score')}　温度 {t.get('sent_temp')}·{t.get('label', '')}"
                            + (f"<br/><span style=\"color:#9aa6c3;font-size:10.5px;\">代表新闻："
                               f"{clean(top.get('title'), 46)}</span>" if top.get('title') else '')
                            + quant_pair.render_wechat(rec, compact=True)))
        for st in (_sd.get('stocks') or [])[:5]:
            rec = quant_pair.recommend(
                f"{st.get('name') or ''} {st.get('symbol') or ''}", _quotes, hint=st.get('symbol'))
            rows.append(row(st.get('symbol', ''),
                            f"{st.get('name') or st.get('symbol')}",
                            f"关注指数 {'—' if st.get('heat') is None else format(float(st['heat']), ',.0f')}"
                            f"　热度Z {st.get('heat_z')}　净情感 {st.get('net_senti')}"
                            f"　新闻 {st.get('news_count')} 条　风险 {st.get('risk_score')}"
                            + quant_pair.render_wechat(rec, compact=True)))
        for e in (m.get('events') or [])[:3]:
            rec = quant_pair.recommend(e.get('title') or '', _quotes, hint='sentiment')
            rows.append(row('⚠️', '风险事件',
                            f"{clean(e.get('title'), 60)}　命中 {'、'.join(e.get('terms') or [])}"
                            f"（{e.get('risk_score')} 分）"
                            + quant_pair.render_wechat(rec, compact=True)))
        ev = _sd.get('api_eval') or {}
        if ev.get('ranking') and not show_api_eval():
            # 按要求对外隐藏：微信推送不再展示平台接入评测（9 阶段实测）评分方框，
            # 探针照常跑、结论仍完整保留在 docs/sentiment-api-eval.md。
            print('  🔒 微信推送 03B：「量化平台现成舆情/新闻因子接入评测（9 阶段实测）」已隐藏'
                  f'（{len(ev["ranking"])} 个源的评分未渲染；结论见 docs/sentiment-api-eval.md，'
                  '需展示时设 SENTIMENT_SHOW_API_EVAL=1）')
        elif ev.get('ranking'):
            cells = '<br/>' + '<br/>'.join(
                f"· <strong>{r.get('platform', '').split('（')[0]}</strong>·"
                f"{(r.get('name') or r.get('id', ''))[:26]}　{r.get('verdict_label')}"
                f"　<strong style=\"color:#4fe5ff;\">{r.get('score')}</strong> 分" for r in ev['ranking'][:6])
            rows.append(box(sub('◆ 量化平台现成舆情/新闻因子接入评测（9 阶段实测）') + cells
                            + f"<br/><span style=\"color:#9aa6c3;font-size:10px;\">评测生成于 "
                              f"{ev.get('generated_at', '—')}；完整矩阵与上线方案见 docs/sentiment-api-eval.md"
                              f"；掘金无舆情接口（平台能力缺失，非故障）</span>"))
        return '\n'.join(rows) + char_charts.sentiment_chart(_sd)[0]



    # ---------- 02 栏：宏观/财经快讯（macro_data.json 驱动，条目自带发布日期）----------
    # 2026-09-16 修复：本栏曾把「IMF 7 月 WEO / 7 月 29 日 FOMC / 8 月 12 日 CPI /
    # 南向资金历史单月统计 / 各行旧恒指目标价」等新闻句子写死在源码里，构建时原样重播，
    # 于是出现「页脚写当天时间、正文停在 8 月 12 日」。现在 02 栏只渲染当次抓到的快讯；
    # 抓不到就明确标注「未获取」+ 实时行情，绝不回填历史叙事（时效由 macro_data.py 保证）。
    def md_cn(date_str):
        """'2026-09-16' → '9 月 16 日'；异常输入原样返回。"""
        if date_str and re.match(r'20\d{2}-\d{2}-\d{2}$', date_str):
            return f'{int(date_str[5:7])} 月 {int(date_str[8:10])} 日'
        return date_str or '日期未标注'

    def macro_items(cat):
        return ((_xd.get('categories') or {}).get(cat) or {}).get('items') or []

    def macro_top(cat, fallback='当次快讯窗口内没有覆盖该主题的报道'):
        items = macro_items(cat)
        if not items:
            return fallback
        it = items[0]
        d = it.get('published_date') or ''
        return (('（' + md_cn(d) + '）' if d else '') + (it.get('title') or '').strip())

    def macro_live_quotes():
        """行情快照 —— 与快讯并排展示的另一条动态链路（market_data.json）。"""
        return (sub('◆ 行情快照 (Live Quotes · 构建时自动抓取)') +
                'VIX 恐慌指数 <b>' + qq('VIX') + '</b>（' + pct('VIX') + '）<br/>' +
                '恒指 <b>' + qq('HSI') + '</b>（' + pct('HSI') + '）· 恒科 <b>' + qq('HSTECH') + '</b>（' +
                pct('HSTECH') + '）· 恒生国企 ' + qq('HSCE') + '<br/>' +
                '标普 ' + qq('SPX') + '（' + pct('SPX') + '）· 纳指 ' + qq('NDQ') + '（' + pct('NDQ') + '）· ' +
                '道指 ' + qq('DJI') + '<br/>' +
                '黄金 <b>' + qq('GOLD') + '</b> 美元/盎司 · WTI ' + qq('WTI') + ' · 布伦特 ' + qq('BRENT') +
                ' · 美元/离岸人民币 ' + qq('USDCNH') + '<br/>' +
                '<span style="color:#9aa6c3;font-size:10px;">行情日期 ' + asof('HSI') +
                ' · Yahoo Finance / Stooq 多源回退 · ' + fetch_status() + ' · ' + community_fetch_status() + '</span>')

    def hsi_brief():
        """恒指一句话摘要：有价才写，缺价就直说（不再用 8 月兜底数补位）。"""
        bits = []
        for lbl, k in (('恒指', 'HSI'), ('恒科', 'HSTECH'), ('恒生国企', 'HSCE')):
            v = qq(k, '')
            if not v:
                continue
            seg = f'{lbl} ' + key(v)
            # 注意: chg_desc() 只描述恒指（它读的是 _quotes['HSI']），别套到恒科/国企上
            detail = [x for x in ((chg_desc() if k == 'HSI' else ''), pct(k, '')) if x]
            if detail:
                seg += '（' + ' / '.join(detail) + '）'
            bits.append(seg)
        if not bits:
            return '行情未获取（market_data.json 缺失或行情源全部失败），本栏不显示任何价格数字。'
        d = dq()
        head = f'{d} 收盘口径 · ' if d else '行情日期未获取 · '
        return head + '，'.join(bits) + '。'

    def macro_block():
        cats = _xd.get('categories') or {}
        summ = _xd.get('summary') or {}
        win = _xd.get('window') or {}
        mode = _xd.get('mode') or ''
        # 兜底口径与网页 02 节共用 macro_data.availability()：不只是「文件读不到」，
        # 还覆盖产物写坏、窗口内 0 条、以及读到前几天旧快照（当次抓取未执行/失败）。
        avail = macro_data_mod.availability(_xd, now=now)
        if not avail['ok']:
            print(f'  🛟 微信推送 02 栏：快讯不可用（{avail["reason"]}）→ 走「今日未获取」兜底，'
                  f'只保留当次实时行情；{avail["detail"]}')
            return (sub('◆ 宏观快讯 — 今日未获取') +
                    '⚠️ 未读到 <strong>macro_data.json</strong>：构建步骤 '
                    f'<code style="background:#151e33;color:{WECHAT_CYAN};border:1px solid {WECHAT_BORDER};padding:1px 4px;">python3 macro_data.py</code> '
                    '未执行，或公开快讯源当次全部失败。<br/>'
                    '本栏<strong>不再回填历史叙事</strong>，只保留下方当次抓取的实时行情。'
                    + (f'<br/><span style="color:#9aa6c3;font-size:10px;">本次判定：{avail["detail"]}'
                       f'（{avail["reason"]}）</span>' if avail.get('detail') else '')
                    + '<br/><br/>' +
                    sub('◆ 港股市场 — 当次行情口径') + hsi_brief() + '<br/><br/>' + macro_live_quotes()
                    + quant_pair.render_wechat(quant_pair.recommend(
                        '宏观快讯未获取 港股行情', _quotes, hint='hk_tape'),
                        note='快讯缺失，配对只使用当次行情；行情也不全时不给方向。')) + char_charts.quotes_chart(_quotes)[0] + char_charts.pairs_chart(_quotes)[0]

        parts = []
        empty_labels = []
        for ckey, blk in cats.items():
            label = (blk or {}).get('label') or ckey
            items = (blk or {}).get('items') or []
            if not items:
                empty_labels.append(label.replace(' — ', '·').split('（')[0])
                continue
            rows = []
            for it in items:
                ttl = (it.get('title') or '').strip()
                sn = (it.get('snippet') or '').strip()
                rows.append('<br/>· <strong style="color:#4fe5ff;">[' + md_cn(it.get('published_date')) + ']</strong> '
                            + ttl
                            + (('　<span style="color:#9aa6c3;font-size:10.5px;">' + sn[:68] + '</span>') if sn else '')
                            + quant_pair.render_wechat(quant_pair.recommend(
                                f'{label} {ttl} {sn}', _quotes, hint=ckey), compact=True))
            parts.append(sub('◆ ' + label) + ''.join(rows) + '<br/><br/>')

        note = ('<span style="color:#9aa6c3;font-size:10px;">'
                f'宏观快讯 {summ.get("kept_items", 0)} 条入库 · 时效窗口 {win.get("max_age_days", "—")} 天'
                f'（{win.get("since") or "—"} 起）· 公开源 {summ.get("ok", 0)}/{summ.get("total", 0)} 可用'
                + (f' · 已拦截超窗 {summ.get("stale_dropped", 0)} 条 / 无日期 {summ.get("undated_dropped", 0)} 条'
                   if (summ.get("stale_dropped") or summ.get("undated_dropped")) else '')
                + f' · 抓取于 {_xd.get("generated_at") or "—"}'
                + (f' · 模式 {mode}' if mode and mode != 'live' else '')
                + ' · 不显示数据来源（与舆情层同一脱敏口径）</span>')

        warn = ''
        if not (summ.get('kept_items') or 0):
            warn = ('<div style="background:#151e33;border-left:3px solid #ff6b7d;border-radius:4px;'
                    'padding:8px 10px;margin:8px 0;font-size:11.5px;color:#dce5fb;">'
                    f'⚠️ 当次 {summ.get("ok", 0)}/{summ.get("total", 0)} 个公开源可用，窗口（'
                    f'{win.get("max_age_days", "—")} 天）内没有任何可核验的宏观快讯 —— '
                    '本栏不复用任何历史叙事，宏观结论请以行情快照与 03/03B 节当次数据为准。</div>')
        elif empty_labels:
            warn = ('<br/><span style="color:#9aa6c3;font-size:10px;">窗口内无匹配快讯的小节：'
                    + '、'.join(empty_labels) + '（按时效留空，不回填旧文）</span>')
        return (sub('◆ 宏观快讯 — 每次构建现抓 · 发布日期见每条前缀') + note + '<br/><br/>'
                + sub('◆ 港股市场 — 当次行情口径') + hsi_brief() + '<br/><br/>'
                + ''.join(parts) + macro_live_quotes() + warn + char_charts.quotes_chart(_quotes)[0] + char_charts.pairs_chart(_quotes)[0] + char_charts.macro_counts_chart(_xd)[0])

    def verdict_block():
        """07 结论：逐条挂当次快讯/行情与真实社区抓取计数，不再写死历史事实或掺入未获取源。"""
        live_comm_n = sum(community_counts.values())
        unavail_n = max(0, len(communities) - live_comm_n)
        if live_comm_n > 0:
            vc_note = (f'社区快照实抓 {live_comm_n}/{len(communities)} 源：'
                       f'偏多 {community_counts["bull"]} 家 · 偏空 {community_counts["bear"]} 家'
                       f' · 中性 {community_counts["neutral"]} 家 · 分歧 {community_counts["mixed"]} 家'
                       f'（另有 {unavail_n} 家未获取，不计入分母）')
        else:
            vc_note = f'社区快照中 0/{len(communities)} 源有可用片段（全部如实标注「未获取」，不编造多空家数）'
        rows = [
            '• <strong>全球宏观面</strong>：' + macro_top('macro') + '；<br/>',
            '• <strong>美联储与离岸流动性</strong>：' + macro_top('fed') + '；<br/>',
            '• <strong>港股市场面</strong>：' + hsi_brief() + ' ' + macro_top('hk') + '；<br/>',
            '• <strong>大宗商品与供应链</strong>：' + macro_top('commodities') + '；<br/>',
            '• <strong>机构观点（恒指目标价）</strong>：' + macro_top('bank_views') + '；<br/>',
            '• <strong>情绪与社区面</strong>：' + vc_note + '；<br/>',
            '<span style="color:#9aa6c3;font-size:10px;">结论逐条对应上方当次快讯、实时行情快照与实抓社区片段，'
            '历史点位/均线读数不写入模板；如需回看前几日版本，以 GitHub Pages 历史构建为准。</span>',
        ]
        rows.append(quant_pair.render_wechat(quant_pair.recommend(
            hsi_brief() + ' ' + macro_top('commodities') + ' ' + macro_top('fed'),
            _quotes, hint='hk_tape')))
        return ''.join(rows)

    # ---------- 03 首段：多空统计与主线说明严格按当次实抓源推导（未获取源不掺入多空分母） ----------
    community_counts = {'bull': 0, 'bear': 0, 'neutral': 0, 'mixed': 0}
    community_unavail_n = 0
    for _c in communities:
        if (_c.get('vclass') or '') in community_counts:
            community_counts[_c['vclass']] += 1
        else:
            community_unavail_n += 1
    community_live_n = sum(community_counts.values())
    community_overview_line = (
        key(f'偏多 {community_counts["bull"]} 家')
        + f' · <strong style="color:{WECHAT_DANGER};font-weight:700;">偏空 {community_counts["bear"]} 家</strong>'
        + f' · <strong style="color:{WECHAT_VIOLET};font-weight:700;">中性 {community_counts["neutral"]} 家</strong> · '
        + key(f'多空分歧 {community_counts["mixed"]} 家')
        + f' · <span style="color:#9aa6c3;">未获取 {community_unavail_n} 家</span>'
        + f'（当次实抓 {community_live_n}/{len(communities)} 源参与多空统计，未获取源不编造立场）')
    macro_freshness_note = (
        (f'{(_xd.get("summary") or {}).get("newest")} 最新一条 · 窗口 {(_xd.get("window") or {}).get("max_age_days")} 天'
         if (_xd.get('summary') or {}).get('kept_items') else '未获取（02 栏已标注，未回填旧文）')
    )

    # ---------- 00 栏：每日 VIX 恐慌指数（当次行情 + 统一研究口径） ----------
    _vix = vix_daily.analyze(_md)
    vix_block = vix_daily.render_wechat(_vix, cyan=GR, danger=WECHAT_DANGER, neon=NEON)
    if _vix.get('available'):
        print(f'  🌡️ 微信推送 00 栏：VIX {_vix["level_text"]} · '
              f'{_vix["band"]["label"]} · {_vix["trend"]["label"]} · '
              f'行情日期 {_vix.get("as_of") or "—"}')
    else:
        print('  🌡️ 微信推送 00 栏：VIX 今日未获取 —— 不回填历史读数')

    # ---------- 01 栏：每日全球全景扫描（四路当次数据现算，零写死叙事） ----------
    _scan = panorama.scan(market=_md, macro=_xd, sentiment=_sd, community=_cd, now=now)
    panorama_block = panorama.render_wechat(_scan, neon=NEON, green=GR, ink=INK)
    print(f'  🌍 微信推送 01 栏：全景扫描输出 {len(_scan["forces"])} 大力量 · '
          f'噪音 {len(_scan["noise"])} 项 · 覆盖 {int(_scan["coverage"] * 100)}% · '
          f'做多合成分 {_scan["verdict"]["long_score"]:+.1f}（{_scan["verdict"]["stance"]}）')

    # 开头先给一条极简多空轴：让读者先看到方向与合成分，再进入长篇证据链。
    # 轴的分数直接来自同一份全景扫描，不另造一套多空口径。
    fig_long_short = char_charts.long_short_chart(_scan.get('verdict'))[0]
    fig_forces = char_charts.forces_chart(_scan)[0]
    fig_community = char_charts.community_chart(community_counts)[0]

    # ---------- 04 栏：AI 预测 · 未来函数（下一交易日，先存档后结算） ----------
    # 与网页 04 节共用 forecast.py 同一套规则与同一份存档，避免两端口径漂移。
    # 推送路径**只读不写**存档：落盘由 build_site.py 在建站时完成，
    # 否则 --dry-run / --emit / --push 各跑一次就会把同一条预测重复灌进存档。
    _fc_review = forecast_mod.review_from_history(
        forecast_mod.default_history_path(), market=_md, now=now)
    _fc = forecast_mod.predict(market=_md, macro=_xd, sentiment=_sd, community=_cd,
                               now=now, review=_fc_review)
    if _fc.get('available'):
        print(f'  🔮 微信推送 04 栏：AI 预测 {len(_fc["forecasts"])} 个标的 · '
              f'基准日 {_fc["base_date"]} → 目标日 {_fc["target_date"]} · '
              f'明日倾向 {_fc["stance"]["label"]}（{_fc["stance"]["z"]:+.2f}σ）· '
              f'回看已结算 {(_fc.get("review") or {}).get("settled", 0)} 条')
    else:
        print(f'  🔮 微信推送 04 栏：AI 预测降级为「今日未获取」'
              f'（{_fc.get("unavailable_reason")}）—— 不回填上一版预测')

    community_thread_line = (
        hsi_brief() + ' '
        + macro_top('hk', '宏观快讯窗口内无港股条目')
        + f'；社区快照中可用实抓样本 {community_live_n}/{len(communities)} 源'
          '（仅读取的实抓片段参与词库极性与相关性重算，未获取源一律标注「未获取」，绝不编造热评）。')

    community_overview_html = (
        f'<div style="background:#0d1426;border:1px solid #2b3855;border-left:3px solid {WECHAT_CYAN};'
        'border-radius:4px;padding:10px 12px;margin:0 0 16px;font-size:12px;line-height:1.85;">'
        f'<strong style="color:#edf2ff;font-size:13px;">AI 多空总览统计</strong> — 综合 {len(communities)} 个境内外核心社区信号：<br/>'
        + community_overview_line + '</div>'
    )

    html = f'''<div style="background:{WECHAT_BG};color:{WECHAT_TEXT};font-family:'黑体','SimHei','PingFang SC','Hiragino Sans GB','Microsoft YaHei','Noto Sans SC',sans-serif;font-size:12px;line-height:1.85;padding:16px 12px;">

  <!-- 顶部电竞 HUD 标题 -->
  <div style="background:#10152b;background-image:linear-gradient(110deg,#091021 0%,#10152b 58%,#21132e 100%);border-bottom:3px solid {WECHAT_CYAN};padding:16px 12px 14px;margin:0 -12px 16px;">
    <div style="color:{WECHAT_CYAN};font-family:'Space Mono','Noto Sans SC','Microsoft YaHei',sans-serif;font-size:9px;font-weight:700;letter-spacing:1.2px;margin-bottom:8px;">OCTO // COMMAND CENTER · LIVE DATA LINK</div>
    <div style="color:{WECHAT_TEXT};font-family:'Rajdhani','Noto Sans SC','Microsoft YaHei',sans-serif;font-size:22px;font-weight:700;letter-spacing:1px;line-height:1.35;">{TITLE}</div>
    <div style="color:{WECHAT_TEXT_SOFT};font-size:13px;margin-top:6px;font-family:'PingFang SC','Microsoft YaHei','Noto Sans SC',sans-serif;">全网 AI 调研境内境外数据 · 多模型混合部署 · 将市场信号编译为可执行战术</div>
  </div>

  {h('00 / 每日 VIX 恐慌指数 (Daily VIX Fear Gauge · 未来 30 天隐含波动率)')}
  {vix_block}

  {fig_long_short}
  {community_overview_html}

  {h('01 / 每日全球全景扫描 (Daily Global Panorama Scan · 5 大推动力量 · 每次构建现算)')}
  {panorama_block}
  {fig_forces}

  {h('02 / 全球经济与财经动态 (Global Macro & HK Battlefield · 快讯每次构建现抓)')}
  {box(macro_block())}

  {h(f'03 / 社区论坛热评与公开摘录 ({len(communities)} 大平台 · 严格真实口径 · 含跨域配对)')}
  {box(
    '<strong style="color:#edf2ff;">盘面与抓取口径</strong>：' + community_thread_line + '<br/>' +
    f'<span style="color:#9aa6c3;font-size:10px;">社区抓取日期 {_community_fetch_date} · {community_fetch_status()} · {len(communities)} 源动态抓取已上线（仅展示真实公开摘录，失败源诚实标注未获取）</span><br/>'
    '<span style="color:#9aa6c3;font-size:10px;">指标口径：情感 = 实抓片段金融词库净值（零哈希伪随机）· 事件 = 片段关键词实配 · 相关性 = 核心词与财经主题词命中密度 · 新颖度 = 批内去重独立度（无首发时间链时不伪造首发/转载）；未获取源不生成指标。</span>'
    + quant_pair.render_wechat(quant_pair.recommend(community_thread_line, _quotes, hint='hk_tape')) + fig_community)}

  {COMMUNITY_SLOT}

  {h('03B / 舆情·新闻因子：多平台采集 → 标的匹配 (Sentiment & News Factor Bench · 不显示数据来源)')}
  {sentiment_block()}

  {h('04 / AI 预测 · 未来函数 (AI Forecast · Next Session · 先存档后结算)')}
  {FORECAST_SLOT}

  {h('05 / 全球 49 大财经 RSS 情报源 (Global 49 Finance RSS Intel Feeds · 六大板块 · 逐源核验)')}
  {RSS_SLOT}

  {h('07 / 核心结论与资产配置提示 (Boss Verdict & Strategic Allocation)')}
  {box(verdict_block())}
  <div style="background:#151e33;border:1px solid #2b3855;border-left:3px solid {WECHAT_DANGER};border-radius:4px;padding:10px 14px;margin-top:10px;font-size:12px;color:#c1cce4;line-height:1.8;">
    <strong style="color:{WECHAT_DANGER};">⚠️ 风险提示与免责声明：</strong>本报告所有内容仅供信息交流与学习参考，不构成任何形式的投资建议或操作指引。资本市场有风险，投资决策需谨慎。数据来源于公开网络信息，可能存在延迟或统计误差，实际投资操作前请务必核实最新实时市场数据。
  </div>

  <!-- 底部作者与结语 -->
  <div style="background:#07101b;border-top:4px solid {NEON};padding:16px 12px 10px;margin:20px -12px 0;font-size:12px;color:#b7c2dd;line-height:1.9;">
    <strong style="color:{NEON};font-size:13px;">作者：章鱼 ai&nbsp;&nbsp;仅供参考，分析研究</strong><br/>
    全网境内外为你寻找蛛丝马迹 — 提供全景视野分析，由多模型协同推理决策。<br/>
    <span style="color:#9aa6c3;font-size:10px;">生成时间：{ts_full} · 社区读取四小时采集快照并重新分析（采集时间见 03 栏）· 宏观快讯时效：{macro_freshness_note} · 字符配图与正文使用同一份读取数据 · 100K 完整单页版</span><br/>
    {quant_pair.rule_note_wechat()}
  </div>

</div>'''
    # 03 栏（49 源社区）与 04 栏按微信单页剩余字符预算各自选详略版本：
    # 先按「给 04 栏留出最小一版」的预算填充社区，再由 fit_forecast_block() 用剩下的预算选预测版本。
    _rss_min = len(wechat_box(rss_mod.render_wechat(_rss, level='compact')))
    html = fit_community_block(html, communities, fc_data=_fc, quotes=_quotes,
                               rss_min=_rss_min)
    html = fit_forecast_block(html, _fc, rss_min=_rss_min)
    html = fit_rss_block(html, _rss)
    _hidden_n = quant_pair.hidden_render_count()
    if _hidden_n:
        print(f'  🔒 行情不足：{_hidden_n} 处 AI 量化配对已整段隐藏（两腿涨跌幅不齐，'
              '不渲染「数据不足」，也不编方向）')

    # 严格真实口径：不再使用正则强行把正文中的旧日期替换为今天，保留真实抓取核验日期供门禁校验
    return html.strip(), ts, ts_full


def extract_fetch_dates(text):
    """抽出正文中「抓取核验 YYYY-MM-DD」（兼容旧「最新读取 YYYY-MM-DD」）的真实日期。"""
    return sorted(set(re.findall(r'(?:抓取核验|最新读取)\s+(20\d{2}-\d{2}-\d{2})', text)))


def assert_fetch_dates_are_today(parts, now, strict=True, snapshot=None):
    """Verify 49 capture markers; a valid four-hour snapshot may cross UTC midnight."""
    today = now.strftime('%Y-%m-%d')
    reads = []
    for title, content in parts:
        reads.extend(CHANNEL_READ_RE.findall(content))
    failed = False
    if len(reads) != EXPECTED_CHANNEL_COUNT:
        failed = True
        msg = (f'仅找到 {len(reads)}/{EXPECTED_CHANNEL_COUNT} 条频道「抓取核验」标记，'
               '请先运行 python3 community_snapshot.py 完成采集后再推送')
        if strict:
            print(f'错误: {msg}。', file=sys.stderr)
            sys.exit(5)
        print(f'⚠️ 警告: {msg}，定时自动推送继续执行(如需严格校验请改用 --push)。')
    has_snapshot = isinstance(snapshot, dict) and isinstance(snapshot.get('snapshot'), dict)
    fresh_snapshot = has_snapshot and community_snapshot_mod.snapshot_is_fresh(snapshot, now=now)
    if has_snapshot and not fresh_snapshot:
        failed = True
        msg = '社区采集快照已超过四小时、时间无效或结构不完整'
        if strict:
            print(f'错误: {msg}，请先重新采集。', file=sys.stderr)
            sys.exit(5)
        print(f'⚠️ 警告: {msg}，定时推送继续执行并保留原采集时间。')
    snapshot_dates_match = (fresh_snapshot and snapshot.get('mode') == 'live'
                            and set(reads) == {snapshot.get('fetch_date')})
    if fresh_snapshot and snapshot.get('mode') == 'live' and not snapshot_dates_match:
        failed = True
        msg = '频道核验日期与社区快照采集日期不一致'
        if strict:
            print(f'错误: {msg}，请重新生成推送内容。', file=sys.stderr)
            sys.exit(5)
        print(f'⚠️ 警告: {msg}，定时推送继续执行。')
    stale = sorted({d for d in reads if d != today})
    # A snapshot taken shortly before UTC midnight is still fresh; never
    # rewrite its capture date, and require all 49 markers to match it.
    cross_midnight_fresh = snapshot_dates_match
    if stale and not cross_midnight_fresh:
        failed = True
        stale_str = ", ".join(stale)
        msg = f'频道抓取核验日期 {stale_str} 不是当天 {today}'
        if strict:
            print(f'错误: {msg}，请重新抓取后再推送（已禁用正则改写旧日期）。', file=sys.stderr)
            sys.exit(5)
        print(f'⚠️ 警告: {msg}，定时自动推送继续执行(如需严格校验请改用 --push)。')
    if failed:
        print(f'📅 频道核验检查: {len(reads)}/{EXPECTED_CHANNEL_COUNT} 条标记，未全部确认为当天 {today}')
    elif stale and cross_midnight_fresh:
        print(f'📅 频道核验检查: {len(reads)}/{EXPECTED_CHANNEL_COUNT} 条来自四小时内快照（采集日期 {snapshot.get("fetch_date")}），允许推送')
    else:
        print(f'📅 频道核验检查: {len(reads)}/{EXPECTED_CHANNEL_COUNT} 条均已于 {today} 完成抓取核验，允许推送')

def build_articles(source_html=SOURCE_HTML, now=None):
    """返回 (parts, ts, ts_full): parts 为 [(title, content)] 包含 1 条单页完整推送。"""
    content, ts, ts_full = build_single_wechat_html(now)
    title = TITLE
    parts = [(title, content)]
    return parts, ts, ts_full

EMBED_BEGIN = '<!-- WECHAT-EMBED:BEGIN -->'
EMBED_END = '<!-- WECHAT-EMBED:END -->'

def embed_into_html(source_html, payload):
    """把单页推送负载以 JSON 形式内嵌进 report.html (幂等)。"""
    with open(source_html, encoding='utf-8') as f:
        html = f.read()
    js = json.dumps(payload, ensure_ascii=False, indent=1).replace('</', '<\\/')
    block = f'{EMBED_BEGIN}\n<script id="wechat-parts" type="application/json">\n{js}\n</script>\n{EMBED_END}'
    pattern = re.compile(re.escape(EMBED_BEGIN) + r'.*?' + re.escape(EMBED_END), re.S)
    if pattern.search(html):
        html = pattern.sub(lambda _: block, html)
    else:
        anchor = html.find('\n<script>')
        if anchor < 0:
            anchor = html.rfind('</body>')
        html = html[:anchor + 1] + block + '\n' + html[anchor + 1:]
    with open(source_html, 'w', encoding='utf-8') as f:
        f.write(html)
    return len(js)

def find_token(source_html, arg_token=None):
    if arg_token:
        return arg_token
    env_token = os.environ.get('PUSHPLUS_TOKEN', '').strip()
    if env_token:
        return env_token
    if os.path.exists(source_html):
        html = open(source_html, encoding='utf-8').read()
        m = re.search(r"PUSHPLUS_TOKEN\s*=\s*'([0-9a-f]+)'", html)
        if m:
            return m.group(1)
    return ''

def find_topic(source_html, arg_topic=None):
    """群组编码: 默认留空（一对一）；显式空参数/环境变量可覆盖后续配置。"""
    if arg_topic is not None:
        return arg_topic.strip()
    if 'PUSHPLUS_TOPIC' in os.environ:
        return os.environ['PUSHPLUS_TOPIC'].strip()
    if os.path.exists(source_html):
        with open(source_html, encoding='utf-8') as f:
            html = f.read()
        m = re.search(r"PUSHPLUS_TOPIC\s*=\s*'([0-9A-Za-z_.\-]*)'", html)
        if m:
            return m.group(1).strip()
    return ''

def push_to_wechat(title, content, token, topic='', retries=MAX_PUSH_RETRIES):
    body = {
        'token': token,
        'title': title[:100],
        'content': content,
        'template': 'html',
    }
    if topic:
        body['topic'] = topic
    payload = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        PUSH_URL, data=payload,
        headers={'Content-Type': 'application/json; charset=utf-8'},
        method='POST')
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
        except (urllib.error.URLError, TimeoutError, OSError,
                http.client.HTTPException) as e:
            # http.client.HTTPException: IncompleteRead/BadStatusLine 等不继承 OSError,
            # 不捕获会让整个推送进程以未捕获异常 (exit 1) 崩溃
            if attempt < retries:
                print(f'网络异常, {3 * attempt}s 后重试({attempt}/{retries}): {e}', file=sys.stderr)
                time.sleep(3 * attempt)
                continue
            return {'code': -1, 'msg': '网络错误', 'raw': str(e)}
        try:
            return json.loads(raw)
        except ValueError:
            return {'code': -1, 'msg': '非 JSON 响应', 'raw': raw[:500]}
    return {'code': -1, 'msg': '网络错误'}

def run_push_preflight(strict=False, timeout=8, report_path=None):
    """推送前全来源数据准确性校验 (verify_quotes.py)。

    对 market_data.json 全部标的做 Yahoo×Stooq×ECB 多源交叉校验:
      返回 True  = 数据可信, 允许推送
      返回 False = 校验 FAIL (或 strict 下 WARN), 调用方必须中止推送
    模块不可用等基础设施异常时放行 (不因校验器自身故障阻断业务), 但打印告警。
    """
    try:
        import verify_quotes as vq  # noqa: PLC0415 — 延迟导入, 离线环境也可跳过
    except Exception as e:  # noqa: BLE001
        print(f'⚠️ 校验模块不可用, 跳过推送前预检: {e}', file=sys.stderr)
        return True
    data_path = os.environ.get('MARKET_DATA', os.path.join(REPO_ROOT, 'market_data.json'))
    try:
        return vq.run_preflight(data_path=data_path, timeout=timeout,
                                strict=strict, report_path=report_path)
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — 校验器自身故障不阻断业务, 但必须留完整堆栈
        import traceback
        print('⚠️ 校验器自身异常 (fail-open 不阻断推送), 完整堆栈:', file=sys.stderr)
        traceback.print_exc()
        sys.stderr.flush()
        return True


def main():
    ap = argparse.ArgumentParser(description='章鱼 AI·舆情004 — 微信推送工具 (一对一专属推送 · 单页详尽完整版 · 49 源动态)')
    ap.add_argument('--source', default=SOURCE_HTML, help='报告 HTML 文件路径')
    ap.add_argument('--emit', metavar='PATH', help='写出 wechat.json 的路径')
    ap.add_argument('--embed', action='store_true',
                    help='把单页推送负载内嵌进 report.html (供页面按钮直接读取)')
    ap.add_argument('--push', action='store_true', help='推送到 PushPlus (默认一对一专属单页)')
    ap.add_argument('--scheduled', action='store_true',
                    help='定时自动推送模式: 抓取日期非当天仅警告不阻断 (供每天 09:00 定时任务使用)')
    ap.add_argument('--token', default='', help='PushPlus token (可选)')
    ap.add_argument('--topic', default=None, help="PushPlus 群组编码 (默认留空，一对一；显式 --topic '' 强制一对一)")
    ap.add_argument('--dry-run', action='store_true', help='只转换, 打印字数统计与预览')
    ap.add_argument('--skip-verify', action='store_true',
                    help='跳过推送前全来源数据校验 (不推荐; 校验 FAIL 默认阻断推送)')
    ap.add_argument('--verify-strict', action='store_true',
                    help='严格校验: 多源校验出现 WARN 也阻断推送 (默认仅 FAIL 阻断)')
    ap.add_argument('--macro-no-fetch', action='store_true',
                    help='禁用构建期宏观快讯自动补抓（缺产物时 02 栏直接显示「今日未获取」）')
    ap.add_argument('--rss-no-fetch', action='store_true',
                    help='禁用构建期 RSS 情报源自动补抓（缺产物时 05 栏直接显示「今日未获取」）')
    args = ap.parse_args()

    # 构建期补抓只在 CLI 入口做（渲染函数保持纯读取，单测才不会被迫联网 —— 见
    # bootstrap_macro_data() 注释）。deploy/wechat/daily 三个 job 都从这里进来。
    bootstrap_macro_data(allow_fetch=False if args.macro_no_fetch else None)
    bootstrap_rss_feeds(allow_fetch=False if args.rss_no_fetch else None)

    parts, ts, ts_full = build_articles(args.source)
    print(f'⏰ 时间核对: {ts_full} — 已按当前最新时间生成, 正文全部时间戳已刷新')
    fetch_dates = extract_fetch_dates(parts[0][1])
    fetch_dates_str = ", ".join(fetch_dates) if fetch_dates else "(未标注)"
    print(f'📅 抓取日期: {fetch_dates_str}')
    if args.push:
        # 严格模式接受跨午夜但未过期的四小时快照；定时模式对社区日期/时效仅警告。
        assert_fetch_dates_are_today(parts, datetime.now(timezone.utc), strict=not args.scheduled,
                                     snapshot=load_community_data())

    if args.push and not args.skip_verify:
        # 🧪 推送前全来源数据准确性校验: Yahoo×Stooq×ECB 多源交叉验证行情数字,
        #    FAIL 时立即退出 (exit 5), 绝不把错误数据推给读者。
        ok = run_push_preflight(strict=args.verify_strict,
                                report_path=os.path.join(REPO_ROOT, 'verify_report.json'))
        if not ok:
            print('错误: 推送前数据校验未通过, 已阻断推送 (如需强制推送请加 --skip-verify)。',
                  file=sys.stderr)
            sys.exit(5)
    print(f'转换完成: 共 {len(parts)} 条消息 (单页完整版, 上限 {CONTENT_LIMIT}/条, 安全线 {CONTENT_SAFE_LIMIT})')
    for i, (t, c) in enumerate(parts, 1):
        print(f'  [{i}/{len(parts)}] {len(c)} 字符  {t}')
        if len(c) > CONTENT_SAFE_LIMIT:
            print(f'错误: 第 {i} 条超过安全长度 {len(c)} > {CONTENT_SAFE_LIMIT}', file=sys.stderr)
            sys.exit(2)

    topic = find_topic(args.source, args.topic)
    payload = {
        'title': parts[0][0],
        'parts': [{'title': t, 'content': c} for t, c in parts],
        'pages_url': PAGES_URL,
        'generated_at': ts,
        'mode': 'one-to-many' if topic else 'one-to-one',
    }

    if args.emit:
        out_path = os.path.abspath(args.emit)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f'已写出: {args.emit}')

    if args.embed:
        n = embed_into_html(args.source, payload)
        print(f'已内嵌: {args.source} ({n} 字符 JSON)')

    if args.push:
        token = find_token(args.source, args.token)
        if not token:
            print('错误: 未找到 PushPlus token', file=sys.stderr)
            sys.exit(3)
        mode = f'一对多 (群组 {topic})' if topic else '一对一专属推送 (Token 本人)'
        print(f'推送模式: {mode} · 单页完整微信卡片 (十万字符级无压缩深度报告)')
        print(f'⏰ 推送前时间核对: {ts_full} — 确认正文时间戳为最新时间后开始发送')
        failed = 0
        for i, (t, c) in enumerate(parts, 1):
            if i > 1:
                time.sleep(15)
            result = push_to_wechat(t, c, token, topic)
            print(f'PushPlus 响应 [{i}/{len(parts)}]:', json.dumps(result, ensure_ascii=False))
            if result.get('code') != 200:
                failed += 1
        if failed:
            sys.exit(4)

    if args.dry_run or (not args.emit and not args.push and not args.embed):
        print('--- 第 1 条正文预览 (前 800 字符) ---')
        print(parts[0][1][:800])

if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — 任何未捕获异常打印完整堆栈 (exit 6), 便于 CI 排障
        import traceback
        print('💥 推送流程发生未捕获异常 (exit 6), 完整堆栈:', file=sys.stderr)
        traceback.print_exc()
        sys.stderr.flush()
        sys.exit(6)
