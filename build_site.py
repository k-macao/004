#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 动态建站 (build_site.py)

读取 market_data.json + community_data.json (+ sentiment_data.json + macro_data.json)，
把 report.html 模板中的 {{占位符}} 替换为最新抓取数据，并动态注入 00 节每日 VIX 恐慌指数、
01 节每日全球全景扫描、49 大社区最新研判（14 原有 + 20 前次新增 + 15 本次扩容）、
02 节宏观/财经快讯与「舆情因子接入实测」区块，
同时在每个社区卡片后追加核心量化指标（实体级情感、事件分类、相关性、新颖度），
生成最终 report.html（页面源文件，供 GitHub Pages 部署与 wechat_push.py 内嵌）。

占位符规则:
  {{TS_FULL}}             构建时间戳（秒级 UTC）
  {{QUOTE_DATE_CN}}       恒指最新行情日期，如 "8 月 28 日"
  {{HSI_LAST}} {{HSI_CHG}} {{HSI_PCT}} {{HSI_ASOF}}   各行情标的（见 market_data.py）
  {{VIX_LAST}} {{VIX_CHG}} {{VIX_PCT}} {{VIX_ASOF}}   每日 VIX 恐慌指数
  {{GOLD_LAST}} {{WTI_LAST}} {{BRENT_LAST}} …         同上，全量标的
  {{CD_01}} .. {{CD_49}}  49 大社区「最新读取」日期（取抓取日，即当天）
  {{COMMUNITY_TOTAL}} / {{COMMUNITY_TYPE_TOTAL}} / {{CF_*}}  03 节社区源数、类型数与多空家数（现算）
  {{FETCH_STATUS}}        数据源同步状态文案
  {{COMMUNITY_FETCH_STATUS}}  社区抓取状态文案

社区动态注入:
  - 若存在 community_data.json，则解析其中 49 条社区数据，生成最新社区 HTML 列表，
    替换模板中 <!-- COMMUNITY_LIST:BEGIN --> ... <!-- COMMUNITY_LIST:END --> 之间的内容
  - 若不存在，则保留模板原有静态社区内容（仅日期占位符会被刷新），保证向后兼容

每日 VIX 注入 (00 节):
  - 由 vix_daily.py 读取当次 quotes.VIX，统一生成点位、日变动、实践分档、30 日幅度换算与一行 Q/P 口径
  - VIX 缺失时明确显示「今日未获取」，绝不回填历史点位，也不把缺失解释成低恐慌

全景扫描注入 (01 节):
  - 由 panorama.py 用当次四路数据（行情 / 宏观快讯 / 舆情因子 / 社区研判）现算：
    推动股价的 5 大力量（重点 / 次要 / 噪音 · 利好 / 利空 · 0~100 力量分）、
    宏观事件 / 板块轮动 / 情绪变化三大关注面、以及「是否可以做多」的合成分结论，
    注入模板中 <!-- PANORAMA --> 占位处（尾部留 <!-- /PANORAMA --> 哨兵保证幂等）
  - 四路数据全缺时渲染为「今日未获取 —— 本栏不编故事」，绝不回填历史叙事

宏观快讯注入 (02 节):
  - 若存在 macro_data.json（macro_data.py 构建时现抓），则渲染各分类快讯（每条自带发布日期）
    注入模板中 <!-- MACROLIST --> 占位处（也兼容 <!-- MACROLIST:BEGIN/END --> 成对标记）
  - 若不存在或窗口内无快讯，则显示「今日未获取」+ 恢复命令，绝不回填历史叙事
    （2026-09-16 旧内容事故根因：正文写死 8 月 12 日等旧事实，时效由 macro_data.py 保证）

AI 预测注入 (04 节):
  - 由 forecast.py 用当次四路数据现算「下一交易日」的逐标的预测：方向 / 预期涨跌幅 /
    预测区间 / 点位区间 / 置信度 / 驱动拆解，外加明日盘面倾向与历史命中率回看，
    注入模板中 <!-- FORECAST --> 占位处（尾部留 <!-- /FORECAST --> 哨兵保证幂等）
  - 「未来函数」只取「预测未来」之义：目标日严格晚于行情基准日，预测先落盘
    forecast_history.json、等目标日行情到位后才结算计分，绝不用当次行情给当次预测打分
  - 行情缺席时渲染为「今日未获取 —— 本栏不预测」，与 01 / 02 / 03B 同一反陈旧口径

量化指标注入:
  - 每条社区数据可携带 quant 字段，包含 sentiment/event/relevance/novelty
  - build_community_html 会在 AI 研判后追加 quant-metrics 区块

用法:
  python3 market_data.py && python3 community_data.py && python3 macro_data.py && python3 build_site.py
                                                       # 常规构建（行情+社区+宏观快讯动态）
  python3 build_site.py --check                        # 只校验占位符是否齐全，不写文件
  python3 build_site.py --data market_data.json --community community_data.json --out report.html

注意: 仓库中 report.html 始终保持「模板版本」（含 {{占位符}}）；构建产物不提交。
      若本地误提交了构建产物，构建会明确报错，恢复: git checkout -- report.html
"""
import argparse
import html as html_mod
import json
import os
import re
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import sentiment_match as smatch                           # noqa: E402  采集→匹配→脱敏展示层
import vix_daily                                           # noqa: E402  00 节「每日 VIX 恐慌指数」统一口径
import panorama                                            # noqa: E402  01 节「每日全球全景扫描」推理引擎
import macro_data as macro_data_mod                        # noqa: E402  02 节快讯可用性判定（兜底口径单一事实源）
import community_data as community_mod                     # noqa: E402  03 节 49 源严格真实口径与离线降级目录
import quant_pair                                          # noqa: E402  每条内容后的 AI 量化配对
import forecast as forecast_mod                            # noqa: E402  04 节「AI 预测 · 未来函数」推理引擎

try:
    # 单一事实源：是否对外展示「量化平台现成舆情/新闻因子接入评测（9 阶段实测）」区块
    from sentiment_sources import show_api_eval           # noqa: E402
except Exception:                                          # 注册表缺失/异常时保持默认：隐藏
    def show_api_eval():
        return str(os.environ.get('SENTIMENT_SHOW_API_EVAL', '')).strip().lower() \
            in ('1', 'true', 'yes', 'on')

# 行情占位符规则: key -> (中文名, 小数位组)
QUOTE_KEYS = ['VIX', 'HSI', 'HSTECH', 'HSCE', 'SPX', 'NDQ', 'DJI', 'GOLD', 'WTI', 'BRENT', 'USDCNH', 'USDCNY']
FX_KEYS = {'USDCNH', 'USDCNY'}

MINUS = '\u2212'  # U+2212 真正的减号

COMMUNITY_LIST_BEGIN = '<!-- COMMUNITY_LIST:BEGIN -->'
COMMUNITY_LIST_END = '<!-- COMMUNITY_LIST:END -->'
SENTIMENT_LIST_BEGIN = '<!-- SENTIMENT_LIST:BEGIN -->'
SENTIMENT_LIST_END = '<!-- SENTIMENT_LIST:END -->'
# 02 节宏观快讯占位区（主标记单点；成对标记与结束哨兵用于幂等重建）
MACROLIST_MARK = '<!-- MACROLIST -->'
MACROLIST_BEGIN = '<!-- MACROLIST:BEGIN -->'
MACROLIST_END = '<!-- MACROLIST:END -->'
MACROLIST_CLOSE = '<!-- /MACROLIST -->'
# 00 节每日 VIX 恐慌指数占位区（同一套单标记 + 结束哨兵，保证重复构建幂等）
VIX_MARK = '<!-- VIX -->'
VIX_BEGIN = '<!-- VIX:BEGIN -->'
VIX_END = '<!-- VIX:END -->'
VIX_CLOSE = '<!-- /VIX -->'
# 01 节每日全球全景扫描占位区（写法与 MACROLIST 一致：单标记 + 结束哨兵，保证重复构建幂等）
PANORAMA_MARK = '<!-- PANORAMA -->'
PANORAMA_BEGIN = '<!-- PANORAMA:BEGIN -->'
PANORAMA_END = '<!-- PANORAMA:END -->'
PANORAMA_CLOSE = '<!-- /PANORAMA -->'
# 04 节 AI 预测（未来函数）占位区（同一套单标记 + 结束哨兵写法，保证重复构建幂等）
FORECAST_MARK = '<!-- FORECAST -->'
FORECAST_BEGIN = '<!-- FORECAST:BEGIN -->'
FORECAST_END = '<!-- FORECAST:END -->'
FORECAST_CLOSE = '<!-- /FORECAST -->'
VERDICT_BEGIN = '<!-- VERDICT:BEGIN -->'
VERDICT_END = '<!-- VERDICT:END -->'
# 行情快照与 07 结论是模板静态块，构建时在标记处补上 AI 量化（重复构建只替换标记，不追加）；
# 行情不足时这些标记一律替换为空串（整段隐藏），不留「数据不足」占位
AI_QUANT_QUOTES = '<!-- AI_QUANT:QUOTES -->'
AI_QUANT_VERDICT = '<!-- AI_QUANT:VERDICT -->'

def fmt_last(q, nd=None):
    """最新价 → "25,440.17"；缺失 → "—"。GOLD 且 >=1000 时取整数。"""
    if not q or q.get('last') is None:
        return '\u2014'
    v = float(q['last'])
    if nd is None:
        nd = q.get('decimals') or 2
    if q.get('name') == '现货黄金' and v >= 1000:
        nd = 0
    return f'{v:,.{nd}f}'


def fmt_chg(q):
    """涨跌额 → "+212.65" / "−212.65"；缺失 → "—"。"""
    if not q or q.get('chg') is None:
        return '\u2014'
    v = float(q['chg'])
    sign = MINUS if v < 0 else '+'
    nd = q.get('decimals') or 2
    return f'{sign}{abs(v):,.{nd}f}'


def fmt_pct(q):
    """涨跌幅 → "+0.25%" / "−0.83%"；缺失 → "—"。"""
    if not q or q.get('pct') is None:
        return '\u2014'
    v = float(q['pct'])
    sign = MINUS if v < 0 else '+'
    return f'{sign}{abs(v):,.2f}%'


def fmt_asof(q):
    """行情日期 → "2026-08-28"；缺失 → "—"。"""
    return (q.get('as_of') or '\u2014') if q else '\u2014'


def quote_date_cn(q):
    """恒指行情日期 → "8 月 28 日"；缺失 → "最新交易日"。"""
    a = (q or {}).get('as_of') or ''
    m = re.match(r'20\d{2}-(\d{2})-(\d{2})', a)
    if not m:
        return '最新交易日'
    return f'{int(m.group(1))} 月 {int(m.group(2))} 日'


def build_tokens(data, now, community_data=None, sentiment_data=None):
    quotes = (data or {}).get('quotes') or {}
    tokens = {}
    for k in QUOTE_KEYS:
        q = quotes.get(k)
        tokens[f'{{{{{k}_LAST}}}}'] = fmt_last(q)
        tokens[f'{{{{{k}_CHG}}}}'] = fmt_chg(q)
        tokens[f'{{{{{k}_PCT}}}}'] = fmt_pct(q)
        tokens[f'{{{{{k}_ASOF}}}}'] = fmt_asof(q)

    tokens['{{TS_FULL}}'] = now.strftime('%Y-%m-%d %H:%M:%S UTC')
    tokens['{{FETCH_DATE}}'] = data.get('fetch_date', now.strftime('%Y-%m-%d'))
    tokens['{{QUOTE_DATE_CN}}'] = quote_date_cn(quotes.get('HSI'))
    tokens['{{HSI_CHG_DESC}}'] = _chg_desc(quotes.get('HSI'))

    # 49 大社区「最新读取」日期 = 社区抓取日（若有社区数据则取社区的 fetch_date，否则取行情的 fetch_date）
    if community_data and community_data.get('fetch_date'):
        cd = community_data.get('fetch_date')
    else:
        cd = tokens['{{FETCH_DATE}}']
    for i in range(1, 50):   # 49 大社区（14 原有 + 20 前次新增 + 15 本次扩容）
        tokens[f'{{{{CD_{i:02d}}}}}'] = cd

    tokens['{{QUANT_RULE}}'] = quant_pair.rule_note_web()
    tokens['{{FETCH_STATUS}}'] = _fetch_status(data)
    tokens['{{COMMUNITY_FETCH_STATUS}}'] = _community_fetch_status(community_data)
    tokens.update(_community_tokens(community_data))
    tokens.update(_sentiment_tokens(sentiment_data, tokens['{{FETCH_DATE}}']))
    return tokens


def _community_tokens(community_data):
    """03 节标题 / 筛选按钮的社区口径 token（源数、类型数、实抓多空家数与未获取家数）。

    严格真实口径：不再回退「14 平台 / 偏多 6 家」等历史静态常数；仅统计真实抓到片段且判定为
    bull/bear/neutral/mixed 的源，未获取源单独计入 `CF_UNAVAILABLE`，绝不掺入多空分母。
    """
    comms = [c for c in ((community_data or {}).get('communities') or []) if isinstance(c, dict)]
    catalog = comms if comms else list(community_mod.COMMUNITIES)
    counts = {'bull': 0, 'bear': 0, 'neutral': 0, 'mixed': 0, 'unavailable': 0}
    if comms:
        for c in comms:
            k = c.get('verdict_class') or 'unavailable'
            if k in ('bull', 'bear', 'neutral', 'mixed'):
                counts[k] += 1
            else:
                counts['unavailable'] += 1
    else:
        counts['unavailable'] = len(catalog)
    types = {c.get('ctype') for c in catalog if c.get('ctype')}
    live_ok = counts['bull'] + counts['bear'] + counts['neutral'] + counts['mixed']
    return {
        '{{COMMUNITY_TOTAL}}': str(len(catalog)),
        '{{COMMUNITY_LIVE_OK}}': str(live_ok),
        '{{COMMUNITY_TYPE_TOTAL}}': str(len(types)) if types else '—',
        '{{CF_BULL}}': str(counts['bull']),
        '{{CF_BEAR}}': str(counts['bear']),
        '{{CF_NEUTRAL}}': str(counts['neutral']),
        '{{CF_MIXED}}': str(counts['mixed']),
        '{{CF_UNAVAILABLE}}': str(counts['unavailable']),
    }


def _sentiment_tokens(s, fallback_date):
    """03B 节舆情因子占位符（数据缺失一律降级为 "—"，不阻断构建）。"""
    m = (s or {}).get('market') or {}
    sm = (s or {}).get('summary') or {}

    def num(v, nd=2, suffix=''):
        try:
            return f'{float(v):,.{nd}f}{suffix}'
        except (TypeError, ValueError):
            return '\u2014'

    def signed(v, nd=3, suffix=''):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return '\u2014'
        return f'{MINUS if v < 0 else "+"}{abs(v):,.{nd}f}{suffix}'

    return {
        '{{SENT_TEMP}}': num(m.get('sent_temp'), 1),
        '{{SENT_LABEL}}': m.get('label') or '\u2014',
        '{{SENT_NET}}': signed(m.get('net_senti')),
        '{{SENT_NEG}}': num(m.get('neg_share'), 1, '%'),
        '{{SENT_NEWS}}': num(m.get('news_count'), 0),
        '{{SENT_HEATZ}}': signed(m.get('heat_z'), 2, 'σ'),
        '{{SENT_RISK}}': num(m.get('risk_score'), 0),
        '{{SENT_DATE}}': (s or {}).get('fetch_date') or fallback_date,
        '{{SENT_MODE}}': {'live': '联网实测', 'mock': '离线回放（fixtures）',
                          'off': '断网兜底', 'offline': '断网兜底'}.get((s or {}).get('mode'), '未生成'),
        '{{SENT_NATIVE}}': num(m.get('platform_native'), 0),
        '{{SENT_SOURCE_STATUS}}': (f"量化平台接口 {sm.get('ok')}/{sm.get('total')} 可用"
                                   if sm.get('total') else '舆情因子数据未生成'),
        '{{SENT_MATCH_HITS}}': num((s or {}).get('matches', {}).get('matched_news'), 0),
        '{{SENT_MATCH_RATE}}': num(((s or {}).get('matches', {}).get('coverage') or 0) * 100, 1, '%'),
        '{{SENT_STATUS}}': _sentiment_status(s),
    }


def _sentiment_status(s):
    if not s:
        return ('未找到 sentiment_data.json —— 运行 <code>python3 sentiment_factors.py --live</code>'
                '（境内出口 + 凭据）或 <code>--mock</code>（离线回放）后重建即可注入本节点')
    sm = s.get('summary') or {}
    ok, total, failed = sm.get('ok'), sm.get('total'), sm.get('failed') or []
    gen = s.get('generated_at') or ''
    txt = f'量化平台舆情/新闻因子接口 {ok}/{total} 可用 · 生成于 {gen}'
    if failed:
        # 对外不显示数据来源：只给降级个数，不列接口 ID / 平台名
        txt += (f' · 降级源：{"、".join(failed)}' if smatch.show_source()
                else f' · {len(failed)} 个接口自动降级（不阻断构建与推送）')
    if (s.get('market') or {}).get('degraded'):
        txt += ' · 本次为降级结果（因子数值未刷新，已标注）'
    return txt


def _chg_desc(q):
    """恒指涨跌描述 → "跌 212.65 点" / "涨 15.20 点"；缺失 → "涨跌数据暂缺"。"""
    if not q or q.get('chg') is None:
        return '涨跌数据暂缺'
    v = float(q['chg'])
    verb = '跌' if v < 0 else '涨'
    nd = q.get('decimals') or 2
    return f'{verb} {abs(v):,.{nd}f} 点'


def _fetch_status(data):
    summary = (data or {}).get('summary') or {}
    ok, total, failed = summary.get('ok'), summary.get('total'), summary.get('failed') or []
    gen = (data or {}).get('generated_at') or ''
    if ok is None:
        return f'未找到 market_data.json，请先运行 python3 market_data.py（抓取于 {gen}）'
    if total == ok:
        return f'{ok}/{total} 项行情源全部同步成功 · 抓取于 {gen}'
    names = '、'.join(failed)
    return f'{ok}/{total} 项同步成功，{names} 暂缺（源不可达，已降级显示 —）· 抓取于 {gen}'


def _community_fetch_status(cdata):
    if not cdata:
        return '未找到 community_data.json，03 节 49 源标注「未获取」（请先运行 python3 community_data.py）'
    summary = cdata.get('summary') or {}
    ok = summary.get('ok')
    total = summary.get('total') or len(cdata.get('communities') or []) or len(community_mod.COMMUNITIES)
    gen = cdata.get('generated_at') or ''
    fetch_date = cdata.get('fetch_date') or ''
    mode = cdata.get('mode') or ''
    coverage = (f' · 采样评论/热帖回复 {summary.get("comment_sources", 0)} 源'
                f'，普通页摘录 {summary.get("page_excerpt_sources", 0)} 源（非热评）'
                if 'comment_sources' in summary else '')
    if mode == 'demo':
        return f'本地演示模式（{total} 条演示样本，非实盘抓取）· 生成于 {gen} · 样本日期 {fetch_date}'
    if ok is None:
        return f'社区数据已加载 · 生成于 {gen} · 核验日期 {fetch_date}'
    if total == ok and total > 0:
        return f'{ok}/{total} 个社区源实抓成功{coverage} · 抓取于 {gen} · 核验日期 {fetch_date}'
    if ok == 0:
        return (f'0/{total} 个社区实抓成功（受站点风控或未检出财经讨论正文，已全部如实标注「未获取」，'
                f'不编造热评）· 抓取于 {gen} · 核验日期 {fetch_date}')
    return (f'{ok}/{total} 个社区实抓成功，其余 {total - ok} 个如实标注「未获取」（不回填模板）{coverage}'
            f' · 抓取于 {gen} · 核验日期 {fetch_date}')


def _macro_status(mdata):
    """02 节宏观快讯状态文案（对外不显示数据来源，只给可用源数与时效应答）。"""
    if not mdata or not mdata.get('categories'):
        return '未找到 macro_data.json，02 节显示「今日未获取」（请先运行 python3 macro_data.py）'
    summ = mdata.get('summary') or {}
    win = mdata.get('window') or {}
    txt = (f'{summ.get("kept_items", 0)} 条入库 · 公开源 {summ.get("ok", 0)}/{summ.get("total", 0)} 可用 · '
           f'时效窗口 {win.get("max_age_days", "—")} 天（{win.get("since") or "—"} 起）· '
           f'抓取于 {mdata.get("generated_at") or "—"}')
    if not (summ.get('kept_items') or 0):
        txt += ' · 窗口内无可核验快讯 → 02 节标注未获取，不回填旧文'
    return txt


def substitute(template, tokens):
    out = template
    for token, value in tokens.items():
        out = out.replace(token, value)
    return out


def find_leftovers(html):
    return sorted(set(re.findall(r'\{\{\s*[A-Za-z0-9_]+\s*\}\}', html)))


def build_quant_html(quant):
    """构建核心量化指标 HTML（网页版）—— 每条新闻/社区卡片后追加。

    四项指标的口径说明（情感 / 事件 / 相关性 / 新颖度）在 03 栏表头统一给出一次，
    卡片里只留数值与标签，避免 49 张卡片把同四句话重复 49 遍。
    """
    if not quant:
        return ""
    sentiment = quant.get('sentiment', {})
    event = quant.get('event', {})
    relevance = quant.get('relevance', {})
    novelty = quant.get('novelty', {})

    s_display = sentiment.get('display', '—')
    e_label = event.get('label', '综合')
    r_display = relevance.get('display', '—')
    n_display = novelty.get('display', '—')

    return (
        f'  <div class="quant-metrics">\n'
        f'    <div class="quant-metrics-title">◆ 核心量化指标</div>\n'
        f'    <ul class="quant-metrics-list">\n'
        f'      <li><strong>实体级情感得分：</strong>{s_display}</li>\n'
        f'      <li><strong>新闻细分事件分类：</strong>{e_label}</li>\n'
        f'      <li><strong>相关性得分：</strong>{r_display}</li>\n'
        f'      <li><strong>新颖度得分：</strong>{n_display}</li>\n'
        f'    </ul>\n'
        f'  </div>'
    )


def build_ai_quant_html(text, market=None, hint=None, compact=False, note=''):
    """一条内容 → 一条 AI 量化配对（网页版）。

    行情不全（两腿涨跌幅不齐）时**整段隐藏** —— 返回空串，不渲染「数据不足」段；
    数据层照旧返回 action='no_data'（quant_pair.is_hidden 可判），只是不进页面。
    """
    rec = quant_pair.recommend(text or '', market, hint=hint)
    return quant_pair.render_web(rec, compact=compact, note=note)


def community_evidence_html(c):
    """Render bounded comment samples and safe provenance links (never HTML from the source)."""
    comments = [x for x in (c.get('comments') or []) if isinstance(x, dict)][:3]
    url = community_mod.safe_source_url(c.get('source_url'))
    link = (f'<a href="{html_mod.escape(url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">查看原帖/原页</a>') if url else ''
    if not comments:
        return f'  <div class="pub-meta">{link}</div>\n' if link else ''
    first = comments[0]
    details = [first.get('published_label') or '发布时间未知', first.get('scope') or '有限样本']
    if c.get('matched_comments') is not None:
        details.append(f"本次匹配 {c.get('matched_comments')} 条，展示 {len(comments)} 条")
    detail_html = _esc(' · '.join(str(x) for x in details if x))
    parts = [f'  <div class="pub-meta">{detail_html}{" · " + link if link else ""}</div>\n']
    if len(comments) > 1:
        parts.append('  <div class="pub-meta">其他采样回复：</div>\n  <ul class="pixel-list">\n')
        for item in comments[1:]:
            text = _esc((item.get('text') or '') + ('…' if item.get('text_truncated') else ''))
            title = _esc(item.get('post_title') or '')
            metric = _esc((item.get('metric') or {}).get('label') or '热度未知')
            published = _esc(item.get('published_label') or '发布时间未知')
            item_url = community_mod.safe_source_url(item.get('url'))
            item_link = (f' · <a href="{html_mod.escape(item_url, quote=True)}" target="_blank" '
                         f'rel="noopener noreferrer">原帖</a>') if item_url else ''
            parts.append(f'    <li>【{title}】{text} <span class="pub-meta">'
                         f'{metric} · {published}{item_link}</span></li>\n')
        parts.append('  </ul>\n')
    return ''.join(parts)


def build_community_html(communities, market=None):
    """根据 community_data.json 生成 49 个社区的 HTML 列表（实抓源展示摘录与词库指标，未获取源诚实标注且不编造指标）。"""
    html_parts = []
    for c in communities:
        icon = c.get('icon', '📌')
        cid = c.get('id', '01')
        name = _esc(c.get('name', '未知社区'))
        vclass = c.get('verdict_class') or 'unavailable'
        label = _esc(c.get('verdict_label') or ('未获取' if vclass == 'unavailable' else '中性'))
        is_avail = bool(c.get('available', vclass != 'unavailable')) and vclass != 'unavailable'
        quote = _esc(c.get('quote', ''))
        verdict = _esc(c.get('verdict', ''))
        ctype = c.get('ctype', '')
        quant = c.get('quant') if is_avail else None
        default_meta = (f"公开页实抓摘录 · {ctype} · 抓取核验 {c.get('fetch_date', '')}"
                        if is_avail else f"当次未获取 · {ctype}")
        meta = _esc(c.get('meta') or default_meta)

        if is_avail:
            quote_label = {'comment': '采样评论', 'thread_reply': '热帖回复',
                           'demo': '演示样本'}.get(c.get('content_kind'), '公开页摘录（未核验为评论）')
            evidence_html = community_evidence_html(c)
            quant_html = build_quant_html(quant)
            event = ((quant or {}).get('event') or {}).get('label') or ''
            ai_html = build_ai_quant_html(
                f"{c.get('name', '')} {ctype} {c.get('quote', '')} {event}",
                market, hint=c.get('key'))
            verdict_block = (
                f'  <div class="pub-verdict"><strong>▶ 词库极性与摘录说明：</strong>{verdict}</div>\n'
                if verdict else ''
            )
            quant_line = f'{quant_html}\n' if quant_html else ''
            ai_line = f'{ai_html}\n' if ai_html else ''
            article = (
                f'<article class="pub-card" data-verdict="{_esc(vclass)}">\n'
                f'  <div class="pub-card-head"><span class="pub-name">{icon} {cid}. {name}</span><span class="pub-chip">{label}</span></div>\n'
                f'  <p class="pub-quote"><strong>{quote_label}：</strong>{quote}</p>\n'
                f'{evidence_html}'
                f'{verdict_block}'
                f'{quant_line}'
                f'{ai_line}'
                f'  <div class="pub-meta">{meta}</div>\n'
                f'</article>'
            )
        else:
            ai_html = build_ai_quant_html(
                f"{c.get('name', '')} {ctype}", market, hint=c.get('key'),
                note='社区热评未获取，配对仅依当次实时行情计算；行情不全时整段隐藏。')
            ai_line = f'{ai_html}\n' if ai_html else ''
            article = (
                f'<article class="pub-card" data-verdict="unavailable">\n'
                f'  <div class="pub-card-head"><span class="pub-name">{icon} {cid}. {name}</span><span class="pub-chip">{label}</span></div>\n'
                f'  <p class="pub-quote"><strong>抓取状态：</strong>{quote}</p>\n'
                f'{ai_line}'
                f'  <div class="pub-meta">{meta}</div>\n'
                f'</article>'
            )
        html_parts.append(article)
    return "\n".join(html_parts)


def ensure_community_ai_quant(html, market=None):
    """社区列表若仍是模板静态卡片（没有动态注入），也在每条 </article> 前补上 AI 量化。"""
    if COMMUNITY_LIST_BEGIN not in html or COMMUNITY_LIST_END not in html:
        return html
    pre, rest = html.split(COMMUNITY_LIST_BEGIN, 1)
    mid, post = rest.split(COMMUNITY_LIST_END, 1)

    def repl(m):
        block = m.group(0)
        if 'class="ai-quant"' in block:
            return block
        text = re.sub(r'<[^>]+>', ' ', block)
        insert = build_ai_quant_html(text, market)
        return block.replace('</article>', insert + '\n</article>', 1)

    mid = re.sub(r'<article\b.*?</article>', repl, mid, flags=re.S)
    return pre + COMMUNITY_LIST_BEGIN + mid + COMMUNITY_LIST_END + post


def inject_community_list(template, community_html):
    """将社区 HTML 注入到模板的 COMMUNITY_LIST 标记之间"""
    if COMMUNITY_LIST_BEGIN in template and COMMUNITY_LIST_END in template:
        pattern = re.compile(re.escape(COMMUNITY_LIST_BEGIN) + r'.*?' + re.escape(COMMUNITY_LIST_END), re.S)
        replacement = f"{COMMUNITY_LIST_BEGIN}\n{community_html}\n{COMMUNITY_LIST_END}"
        new_html, count = pattern.subn(replacement, template)
        if count:
            print(f'  🧩 已动态注入 {community_html.count("<article")} 个社区卡片（标记替换）')
            return new_html
    m = re.search(r'(<div id="communityList">)(.*?)(</div>\s*<!-- 04)', template, re.S)
    if m:
        new_block = m.group(1) + "\n" + COMMUNITY_LIST_BEGIN + "\n" + community_html + "\n" + COMMUNITY_LIST_END + "\n" + m.group(3)
        new_html = template[:m.start()] + new_block + template[m.end():]
        print(f'  🧩 已动态注入 {community_html.count("<article")} 个社区卡片（兼容旧模板）')
        return new_html
    print('  ⚠️ 未找到社区列表标记，跳过动态注入（将保留模板原有社区内容）', file=sys.stderr)
    return template


def _md_cn(date_str):
    """'2026-09-16' → '9 月 16 日'；异常输入原样返回。"""
    m = re.match(r'20\d{2}-(\d{2})-(\d{2})$', str(date_str or ''))
    if not m:
        return date_str or '日期未标注'
    return f'{int(m.group(1))} 月 {int(m.group(2))} 日'


def build_macro_unavailable_html(avail, market=None):
    """02 节兜底区块 —— 本次没有可用快讯时的唯一出口（网页版）。

    四种触发形态（reason）：
      no_file        macro_data.json 读不到（构建步骤未执行 / 产物未生成）
      bad_type       产物写坏、被截断，解析出来不是预期结构
      no_items       文件在、结构对，但时效窗口内 0 条（公开源当次全挂或全部超窗）
      stale_snapshot 读到的是前几天构建的旧快照（当次抓取未执行或失败）

    无论哪种，都只说明「这次没有」，绝不回填任何历史叙事。
    """
    reason = avail.get('reason')
    detail = avail.get('detail') or ''
    return (
        '<div class="pub-sub">◆ 宏观快讯 — 今日未获取</div>\n'
        '<div style="font-size:11.5px;line-height:1.8;color:#0a0a0a;">\n'
        '  ⚠️ 未读到 <strong>macro_data.json</strong>：构建步骤 '
        '<code style="background:#eceef0;padding:1px 4px;">python3 macro_data.py</code> 未执行，'
        '或公开快讯源当次全部失败。<br/>\n'
        '  本栏<strong>不再回填历史叙事</strong>（旧文案写死在模板里正是上一版正文长期过期的根因），'
        '只保留下方当次抓取的实时行情；宏观结论以每次重建后的最新一版为准。\n'
        + (f'  <div class="pub-meta" style="margin-top:6px;">本次判定：{_esc(detail)}'
           f'（{_esc(reason)}）· 恢复命令：'
           '<code style="background:#eceef0;padding:1px 4px;">'
           'python3 macro_data.py --json macro_data.json --days 7 --text</code></div>\n'
           if detail else '')
        + '</div>\n'
        + build_ai_quant_html('宏观快讯未获取 港股行情', market, hint='hk_tape',
                              note='快讯缺失，配对只使用当次行情；行情也不全时不给方向。')
    )


def build_macro_html(macro_data, now=None, market=None):
    """02 节宏观/财经快讯区块（macro_data.json 驱动，条目自带发布日期）。

    对外**不显示数据来源**（与舆情层同一脱敏口径，只给公开源可用数）；
    抓不到快讯时显示「今日未获取」+ 恢复命令，绝不回填历史叙事
    （2026-09-16 旧内容事故根因：正文写死 8 月 12 日等旧事实）。

    兜底口径由 `macro_data.availability()` 统一判定（网页/微信共用同一事实源），
    覆盖四种「本次没有可用快讯」的形态：文件缺失、产物写坏、窗口内 0 条、快照过期。
    """
    avail = macro_data_mod.availability(macro_data, now=now)
    if not avail['ok']:
        return build_macro_unavailable_html(avail)
    # 走到这里 availability 已保证 macro_data 是 dict 且窗口内有条目
    cats = (macro_data or {}).get('categories') or {}
    summ = (macro_data or {}).get('summary') or {}
    win = (macro_data or {}).get('window') or {}
    mode = (macro_data or {}).get('mode') or ''

    note = (
        f'宏观快讯 {summ.get("kept_items", 0)} 条入库 · 时效窗口 {win.get("max_age_days", "—")} 天'
        f'（{win.get("since") or "—"} 起）· 公开源 {summ.get("ok", 0)}/{summ.get("total", 0)} 可用'
        + (f' · 已拦截超窗 {summ.get("stale_dropped", 0)} 条 / 无日期 {summ.get("undated_dropped", 0)} 条'
           if (summ.get('stale_dropped') or summ.get('undated_dropped')) else '')
        + f' · 抓取于 {macro_data.get("generated_at") or "—"}'
        + (f' · 模式 {mode}' if mode and mode != 'live' else '')
        + ' · 不显示数据来源（与舆情层同一脱敏口径）'
    )
    parts = ['<div class="pub-sub">◆ 宏观快讯 — 每次构建现抓 · 发布日期见每条前缀</div>',
             f'<div class="pub-meta" style="margin:2px 0 8px;">{_esc(note)}</div>']

    empty_labels = []
    for ckey, blk in cats.items():
        label = (blk or {}).get('label') or ckey
        items = (blk or {}).get('items') or []
        if not items:
            empty_labels.append(label.replace(' — ', '·').split('（')[0])
            continue
        rows = []
        for it in items:
            raw_title = (it.get('title') or '').strip()
            raw_snip = (it.get('snippet') or '').strip()
            ttl = _esc(raw_title)
            snip = _esc(raw_snip[:72])
            ai = build_ai_quant_html(f'{label} {raw_title} {raw_snip}', market, hint=ckey, compact=True)
            rows.append(
                f'  <li><strong style="color:#000;">[{_esc(_md_cn(it.get("published_date")))}]</strong> {ttl}'
                + (f' <span style="color:var(--muted);font-size:11px;">{snip}</span>' if snip else '')
                + ai
                + '</li>'
            )
        parts.append(f'<div class="pub-sub" style="margin-top:10px;">◆ {_esc(label)}</div>')
        parts.append('<ul class="pixel-list" style="margin:6px 0 0;">\n' + '\n'.join(rows) + '\n</ul>')

    if not (summ.get('kept_items') or 0):
        parts.append(
            '<div style="background:#eceef0;border-left:3px solid #141414;border-radius:4px;'
            'padding:8px 10px;margin:8px 0;font-size:11.5px;color:#0a0a0a;">'
            f'⚠️ 当次 {summ.get("ok", 0)}/{summ.get("total", 0)} 个公开源可用，窗口（'
            f'{win.get("max_age_days", "—")} 天）内没有任何可核验的宏观快讯 —— '
            '本栏不复用任何历史叙事，宏观结论请以行情快照与 03 / 03B 节当次数据为准。</div>'
        )
    elif empty_labels:
        parts.append(
            '<div class="pub-meta" style="margin-top:8px;">窗口内无匹配快讯的小节：'
            + _esc('、'.join(empty_labels)) + '（按时效留空，不回填旧文）</div>'
        )
    return '\n'.join(parts)


def inject_macro_list(template, macro_html):
    """把宏观快讯注入模板 02 节的 MACROLIST 标记处（照 community / sentiment 的注入写法）。

    主标记为 `<!-- MACROLIST -->`；兼容 `<!-- MACROLIST:BEGIN/END -->` 成对标记；
    注入时在内容尾部留下 `<!-- /MACROLIST -->` 哨兵，重复构建时原地替换而非追加。
    """
    block = f'{MACROLIST_MARK}\n{macro_html}\n{MACROLIST_CLOSE}'
    if MACROLIST_BEGIN in template and MACROLIST_END in template:
        pattern = re.compile(re.escape(MACROLIST_BEGIN) + r'.*?' + re.escape(MACROLIST_END), re.S)
        new_html, count = pattern.subn(lambda _m: block, template, count=1)
        if count:
            print('  📰 已动态注入 02 节宏观快讯（成对标记替换）')
            return new_html
    if MACROLIST_MARK in template:
        if MACROLIST_CLOSE in template:
            pattern = re.compile(re.escape(MACROLIST_MARK) + r'.*?' + re.escape(MACROLIST_CLOSE), re.S)
            new_html, count = pattern.subn(lambda _m: block, template, count=1)
            if count:
                print('  📰 已动态注入 02 节宏观快讯（单标记替换）')
                return new_html
        print('  📰 已动态注入 02 节宏观快讯（单标记插入）')
        return template.replace(MACROLIST_MARK, block, 1)
    print('  ⚠️ 未找到 MACROLIST 标记，跳过宏观快讯注入', file=sys.stderr)
    return template


def build_vix_html(market_data):
    """00 节「每日 VIX 恐慌指数」：只读当次行情，统一解释口径与缺失降级。"""
    data = vix_daily.analyze(market_data)
    if data.get('available'):
        print(f'  🌡️ 已动态注入 00 节 VIX：{data["level_text"]} · '
              f'{data["band"]["label"]} · {data["trend"]["label"]} · '
              f'行情日期 {data.get("as_of") or "—"}')
    else:
        print('  🌡️ 00 节 VIX 降级为「今日未获取」—— 不回填历史读数')
    return vix_daily.render_web(data)


def inject_vix(template, vix_html):
    """把 VIX 注入模板 00 节；支持成对标记与单标记，重复构建原地替换。"""
    block = f'{VIX_MARK}\n{vix_html}\n{VIX_CLOSE}'
    if VIX_BEGIN in template and VIX_END in template:
        pattern = re.compile(re.escape(VIX_BEGIN) + r'.*?' + re.escape(VIX_END), re.S)
        new_html, count = pattern.subn(lambda _m: block, template, count=1)
        if count:
            return new_html
    if VIX_MARK in template:
        if VIX_CLOSE in template:
            pattern = re.compile(re.escape(VIX_MARK) + r'.*?' + re.escape(VIX_CLOSE), re.S)
            new_html, count = pattern.subn(lambda _m: block, template, count=1)
            if count:
                return new_html
        return template.replace(VIX_MARK, block, 1)
    print('  ⚠️ 未找到 VIX 标记，跳过 00 节每日 VIX 恐慌指数注入', file=sys.stderr)
    return template


def build_panorama_html(market_data, macro_data, sentiment_data, community_data, now=None):
    """01 节「每日全球全景扫描」：四路当次数据 → 5 大推动力量 / 噪音清单 / 做多结论。

    推理规则集中在 panorama.py（可单测、可回测），本函数只负责取数与渲染；
    四路数据全缺时渲染为「今日未获取」，与 02 / 03B 节同一反陈旧口径。
    """
    scan = panorama.scan(market=market_data, macro=macro_data,
                         sentiment=sentiment_data, community=community_data, now=now)
    print(f'  🌍 已动态注入 01 节全景扫描：{len(scan["forces"])} 大力量 · '
          f'噪音 {len(scan["noise"])} 项 · 覆盖 {int(scan["coverage"] * 100)}% · '
          f'做多合成分 {scan["verdict"]["long_score"]:+.1f}（{scan["verdict"]["stance"]}）')
    return panorama.render_web(scan)


def inject_panorama(template, panorama_html):
    """把全景扫描注入模板 01 节的 PANORAMA 标记处（与 inject_macro_list 同一幂等写法）。"""
    block = f'{PANORAMA_MARK}\n{panorama_html}\n{PANORAMA_CLOSE}'
    if PANORAMA_BEGIN in template and PANORAMA_END in template:
        pattern = re.compile(re.escape(PANORAMA_BEGIN) + r'.*?' + re.escape(PANORAMA_END), re.S)
        new_html, count = pattern.subn(lambda _m: block, template, count=1)
        if count:
            return new_html
    if PANORAMA_MARK in template:
        if PANORAMA_CLOSE in template:
            pattern = re.compile(re.escape(PANORAMA_MARK) + r'.*?' + re.escape(PANORAMA_CLOSE), re.S)
            new_html, count = pattern.subn(lambda _m: block, template, count=1)
            if count:
                return new_html
        return template.replace(PANORAMA_MARK, block, 1)
    print('  ⚠️ 未找到 PANORAMA 标记，跳过 01 节全景扫描注入', file=sys.stderr)
    return template


def build_forecast_html(market_data, macro_data, sentiment_data, community_data,
                        now=None, history=True, history_path=None):
    """04 节「AI 预测 · 未来函数」：四路当次数据 → 下一交易日逐标的预测 + 历史命中率回看。

    推理规则集中在 forecast.py（可单测、可回测），本函数只负责取数、存档与渲染。

    「未来函数」在这里只取「预测未来」的含义，绝不是量化里那个偷看未来的 bug：
      • 输入只有当次四份产物，目标日 = 行情基准日的下一交易日，时序由引擎强制校验；
      • 当次预测先落盘 forecast_history.json（settled=False），
        要等后续某次构建真的抓到目标日行情才结算计分 —— 当次行情永远结算不了当次预测。
    存档 IO 任何故障都只影响「回看」这一小块，不阻断构建（history=False 可完全关掉）。
    """
    path = history_path or forecast_mod.default_history_path()
    review = forecast_mod.review_from_history(path, market=market_data, now=now)
    data = forecast_mod.predict(market=market_data, macro=macro_data,
                                sentiment=sentiment_data, community=community_data,
                                now=now, review=review)
    if history:
        data['review'] = forecast_mod.review_from_history(
            path, market=market_data, now=now, persist=True, data=data)
    rev = data['review']
    if data.get('available'):
        print(f'  🔮 已动态注入 04 节 AI 预测：{len(data["forecasts"])} 个标的 · '
              f'基准日 {data["base_date"]} → 目标日 {data["target_date"]} · '
              f'明日倾向 {data["stance"]["label"]}（{data["stance"]["z"]:+.2f}σ）· '
              f'回看已结算 {rev.get("settled", 0)} 条 / 待结算 {rev.get("pending", 0)} 条')
    else:
        print(f'  🔮 04 节 AI 预测降级为「今日未获取」（{data.get("unavailable_reason")}）—— '
              '没有基准行情就不预测，不回填上一版预测')
    return forecast_mod.render_web(data)


def inject_forecast(template, forecast_html):
    """把 AI 预测注入模板 04 节的 FORECAST 标记处（与 inject_panorama 同一幂等写法）。"""
    block = f'{FORECAST_MARK}\n{forecast_html}\n{FORECAST_CLOSE}'
    if FORECAST_BEGIN in template and FORECAST_END in template:
        pattern = re.compile(re.escape(FORECAST_BEGIN) + r'.*?' + re.escape(FORECAST_END), re.S)
        new_html, count = pattern.subn(lambda _m: block, template, count=1)
        if count:
            return new_html
    if FORECAST_MARK in template:
        if FORECAST_CLOSE in template:
            pattern = re.compile(re.escape(FORECAST_MARK) + r'.*?' + re.escape(FORECAST_CLOSE), re.S)
            new_html, count = pattern.subn(lambda _m: block, template, count=1)
            if count:
                return new_html
        return template.replace(FORECAST_MARK, block, 1)
    print('  ⚠️ 未找到 FORECAST 标记，跳过 04 节 AI 预测注入', file=sys.stderr)
    return template


def build_sentiment_html(s, market=None):
    """03B / 舆情·新闻因子节点（温度计 + 标的匹配 + 个股热度 + 风险事件 + 情感样本 + 采集概况）。

    对外输出**不显示数据来源**：平台名 / 接口 ID / 域名 / SDK / 凭据与依赖提示由
    `sentiment_match.redact()` 统一遮成「量化平台」，逐源明细表默认不渲染
    （内部核对需临时显示时设 `SENTIMENT_SHOW_SOURCE=1`）。
    「量化平台现成舆情 / 新闻因子接入评测（9 阶段实测）」评分矩阵默认隐藏
    （`SENTIMENT_SHOW_API_EVAL=1` 可临时恢复），结论仍完整保留在 `docs/sentiment-api-eval.md`。
    """
    esc = _esc
    anon = not smatch.show_source()

    def clean(v, limit=None):
        """对外文案：脱敏（不显示来源）→ 截断 → HTML 转义。"""
        t = smatch.redact(v) if anon else str(v if v is not None else '')
        if limit and len(t) > limit:
            t = t[:limit].rstrip(' ·、/，,') + '…'
        return esc(t)

    if not s:
        return ('<div class="pub-box"><div class="pub-sub">◆ 舆情 / 新闻因子节点待生成</div>'
                '本节点由 <code>sentiment_factors.py</code> 动态注入：'
                '<code>python3 sentiment_factors.py --live</code>（境内出口 + 凭据）实测取数，'
                '或 <code>python3 sentiment_factors.py --mock</code> 用录制报文离线回放。'
                + ('<div class="pub-meta">接入评测与打分由 <code>tools/probe_sentiment_apis.py</code> 生成，'
                   '结论详见 <code>docs/sentiment-api-eval.md</code>。</div>' if show_api_eval() else '')
                + build_ai_quant_html('舆情因子未获取', market, hint='sentiment',
                                      note='因子节点未生成，配对只使用当次行情。')
                + '</div>')

    m = s.get('market') or {}
    sm = s.get('summary') or {}
    parts = []

    def card(title, val, desc):
        return (f'<div class="stat-card"><div class="stat-title">{esc(title)}</div>'
                f'<div class="stat-val">{val}</div><div class="stat-desc">{desc}</div></div>')

    cards = [
        card('市场舆情温度计 SENT_TEMP', f"{m.get('sent_temp', '—')} <span style=\"font-size:12px\">{esc(m.get('label') or '')}</span>",
             '净情感 ±35 分 · 热度异动 ±12 分 · 负面占比最多扣 18 分；&gt;65 亢奋、&lt;35 恐慌'),
        card('净情感强度 NET_SENTI', _f2(m.get('net_senti'), '+.3f'),
             f"负面占比 {_f2(m.get('neg_share'), '.1f')}% · 正面 {_f2(m.get('pos_share'), '.1f')}%"),
        card('新闻热度 Z 值 NEWS_HEAT_Z', _f2(m.get('heat_z'), '+.2f') + ' σ',
             f"本次关联新闻 {m.get('news_count', 0)} 条（对比近 20 日均值）"),
        card('突发事件风险分 EVENT_RISK', _f2(m.get('risk_score'), '.0f'),
             '监管/诉讼/违约/退市/减持等高风险词加权命中，单日取最大值'),
        card('量化平台接口可用性', f"{sm.get('ok', '—')}/{sm.get('total', '—')}",
             f"模式：{clean(s.get('mode_note'), 44) or '—'}"),
        card('平台现成因子占比', f"{m.get('platform_native', 0)} / {m.get('news_count', 0)}",
             '带原生情感字段者直接采用平台口径，其余由自建中文金融词库打分'),
    ]
    parts.append('<div class="stat-grid">' + ''.join(cards) + '</div>')
    parts.append(build_ai_quant_html(
        f"市场舆情 {m.get('label') or ''} 净情感 {m.get('net_senti')} 风险 {m.get('risk_score')}",
        market, hint='sentiment'))

    # ---- 采集 → 匹配：新闻舆情按关键词对齐到日报标的与主题（对外不显示来源） ----
    mm = s.get('matches') or {}
    tgts = mm.get('targets') or []
    if tgts:
        rows = []
        for t in tgts[:12]:
            top = (t.get('top_titles') or [{}])[0]
            rows.append(f"<tr><td>{esc(t.get('name', ''))}</td>"
                        f"<td>{t.get('hits', 0)}</td>"
                        f"<td class=\"q-pct\">{_f2(t.get('net_senti'), '+.3f')}</td>"
                        f"<td>{_f2(t.get('neg_share'), '.1f')}%</td>"
                        f"<td>{_f2(t.get('risk_score'), '.0f')}</td>"
                        f"<td>{esc(t.get('label') or '—')} {_f2(t.get('sent_temp'), '.1f')}</td>"
                        f"<td>{clean(top.get('title'), 34) or '—'}</td></tr>")
        parts.append(
            '<div class="pub-box"><div class="pub-sub">◆ 舆情因子 × 日报标的匹配'
            '（多平台采集后自动对齐 · 不显示数据来源）</div>'
            f'<div style="font-size:11.5px;color:#333;margin-bottom:6px;">本次共采集 '
            f'<strong>{mm.get("total_news", 0)}</strong> 条新闻舆情，其中 '
            f'<strong>{mm.get("matched_news", 0)}</strong> 条匹配到本页 02 节行情标的与日报主题'
            f'（匹配率 {_f2((mm.get("coverage") or 0) * 100, ".1f")}%）· 采集关键词 '
            f'{esc("、".join(mm.get("keywords_used") or []) or "—")}</div>'
            '<table class="quote-table"><tr><th>标的 / 主题</th><th>命中</th><th>净情感</th>'
            '<th>负面占比</th><th>风险分</th><th>舆情温度</th><th>代表新闻（不含来源）</th></tr>'
            + ''.join(rows) + '</table>'
            + (f'<div class="pub-meta">未匹配 {mm.get("unmatched", 0)} 条（与日报标的相关性不足，'
               '只计入市场级读数，不进标的表）。</div>' if mm.get('unmatched') else '')
            + '<div class="pub-meta">匹配规则：标题命中权重 0.4 / 正文 0.15，净情感与风险分复用同一套'
              '自建中文金融词库口径（平台已给原生情感字段时优先采用平台口径）。</div>'
            + quant_pair.render_web_list([
                quant_pair.recommend(
                    f"{t.get('name', '')} {((t.get('top_titles') or [{}])[0]).get('title', '')}",
                    market, hint=t.get('key'))
                for t in tgts])
            + '</div>')

    # ---- 平台接入评测（9 阶段实测）矩阵：默认对外隐藏 ----
    ev = s.get('api_eval') or {}
    if ev.get('ranking') and not show_api_eval():
        # 探针照常跑、api_eval 照常写入 sentiment_data.json，只是不渲染给读者；
        # 结论仍完整保留在 docs/sentiment-api-eval.md（内部评测档案）。
        print(f'  🔒 03B「量化平台现成舆情 / 新闻因子接入评测（9 阶段实测）」已隐藏'
              f'（{len(ev["ranking"])} 个源的评分矩阵未渲染；结论见 docs/sentiment-api-eval.md，'
              f'需展示时设 SENTIMENT_SHOW_API_EVAL=1）')
    elif ev.get('ranking'):
        rows = []
        for r in ev['ranking']:
            rows.append(f"<tr><td>{esc(r.get('platform', ''))}</td>"
                        f"<td><code>{esc(r.get('id', ''))}</code> "
                        f"{esc(r.get('name') or '')}</td>"
                        f"<td>{esc(r.get('verdict_label', ''))}</td>"
                        f"<td class=\"q-pct\">{r.get('score')}</td></tr>")
        parts.append(
            '<div class="pub-box"><div class="pub-sub">◆ 量化平台现成舆情 / 新闻因子接入评测</div>'
            f'<div style="font-size:11.5px;color:#333;margin-bottom:6px;">评测生成于 '
            f'{esc(ev.get("generated_at") or "—")} · 模式 '
            f'{esc("联网实测" if ev.get("mode") == "live" else "文档基线（离线回放，未实测连通性与权限）")}'
            ' · 9 阶段：依赖 → 网络 → 鉴权 → 取数 → 字段 → 时效 → 覆盖 → 延迟 → 额度</div>'
            '<table class="quote-table"><tr><th>平台</th><th>接口</th><th>判定</th><th>评分</th></tr>'
            + ''.join(rows) + '</table>'
            + ('<ul class="pixel-list">' + ''.join(
                f'<li>{esc(line)}</li>' for line in (ev.get('conclusion') or [])[:6]) + '</ul>')
            + '<div class="pub-meta">完整能力矩阵、阶段明细与上线方案见 <code>docs/sentiment-api-eval.md</code>'
              '（由 <code>tools/probe_sentiment_apis.py</code> 生成）。</div></div>')

    stocks = (s.get('stocks') or [])[:8]
    if stocks:
        rows = []
        for st in stocks:
            heat = st.get('heat')
            rows.append(f"<tr><td>{esc(st.get('symbol', ''))} {esc(st.get('name') or '')}</td>"
                        f"<td>{'—' if heat is None else format(float(heat), ',.0f')}</td>"
                        f"<td class=\"q-pct\">{st.get('heat_z')}</td>"
                        f"<td>{st.get('net_senti')}</td>"
                        f"<td>{st.get('news_count')}</td>"
                        f"<td>{st.get('risk_score')}</td></tr>")
        parts.append(
            '<div class="pub-box"><div class="pub-sub">◆ 个股舆情热度与情感（关注度因子 + 情感因子）</div>'
            '<table class="quote-table"><tr><th>标的</th><th>关注指数</th><th>热度 Z</th>'
            '<th>净情感</th><th>新闻数</th><th>风险分</th></tr>' + ''.join(rows) + '</table>'
            '<div class="pub-meta">关注指数为量化平台现成热度类因子（小时/日频，无极性）；'
            '净情感来自平台原生情感字段或自建中文金融词库打分。</div>'
            + quant_pair.render_web_list([
                quant_pair.recommend(
                    f"{st.get('name') or ''} {st.get('symbol') or ''}", market, hint=st.get('symbol'))
                for st in stocks])
            + '</div>')

    events = (m.get('events') or [])[:5]
    if events:
        lis = ''.join(
            f"<li><strong>{clean(e.get('title'), 60)}</strong> — 命中 "
            f"{esc('、'.join(e.get('terms') or []))} · 风险分 {e.get('risk_score')}"
            f"{' · ' + esc(str(e.get('published_at'))[:16]) if e.get('published_at') else ''}</li>"
            for e in events)
        parts.append('<div class="pub-box"><div class="pub-sub">◆ 舆情风险事件（EVENT_RISK 命中明细）</div>'
                     f'<ul class="pixel-list">{lis}</ul></div>')
    top_neg = (m.get('top_negative') or [])[:2]
    top_pos = (m.get('top_positive') or [])[:2]
    if top_neg or top_pos:
        def li(x, tag):
            hits = (x.get('hits') or {})
            kw = '、'.join((hits.get('neg') or [])[:3] + (hits.get('pos') or [])[:3])
            return (f"<li><span style=\"background:#000;color:#39ff14;font-size:10px;\""
                    f"padding:1px 6px;margin-right:6px;\">{tag}</span>{clean(x.get('title'), 60)}"
                    f"<span style=\"color:#7d838b;\">"
                    f" · {esc(str(x.get('published_at') or ''))[:16]} · 情感 {x.get('sentiment')}"
                    f" · 命中 {esc(kw)}</span></li>")
        parts.append('<div class="pub-box"><div class="pub-sub">◆ 情感样本极值（可回溯：命中词与权重 · 不含来源）</div>'
                     '<ul class="pixel-list">' + ''.join(li(x, '负面') for x in top_neg)
                     + ''.join(li(x, '正面') for x in top_pos) + '</ul>'
                     + build_ai_quant_html(
                         ' '.join((x.get('title') or '') for x in (top_neg + top_pos)),
                         market, hint='sentiment')
                     + '</div>')

    srcs = s.get('sources') or []
    if srcs and not anon:
        rows = []
        for r in srcs:
            ok = '✅' if r.get('ok') else '⚠️'
            rows.append(f"<tr><td><code>{esc(r.get('id', ''))}</code></td>"
                        f"<td>{esc(r.get('platform', ''))}</td>"
                        f"<td>{ok} {esc(reg_verdict_hint(r))}</td>"
                        f"<td>{r.get('news', 0)} / {r.get('series', 0)}</td>"
                        f"<td>{esc(r.get('latest_date') or '—')}</td>"
                        f"<td class=\"q-off\">{esc((r.get('error') or r.get('note') or '')[:60])}</td></tr>")
        parts.append(
            '<div class="pub-box"><div class="pub-sub">◆ 逐源取数明细（本次构建 · 内部视图）</div>'
            '<table class="quote-table"><tr><th>接口</th><th>平台</th><th>状态</th>'
            '<th>新闻/序列</th><th>最新日期</th><th>说明</th></tr>' + ''.join(rows) + '</table>'
            '<div class="pub-meta">单源失败自动降级、不阻断构建与推送；失败原因如实标注在此表。'
            '（对外默认隐藏，本表由 SENTIMENT_SHOW_SOURCE=1 开启）</div></div>')
    elif srcs:
        c = smatch.collection_summary(s)
        parts.append(
            '<div class="pub-box"><div class="pub-sub">◆ 本次采集概况（多量化平台合并 · 不显示来源）</div>'
            f'量化平台接口 <strong>{c["ok"]}/{c["total"]}</strong> 可用 · 采集新闻 '
            f'<strong>{c["news"]}</strong> 条 · 指数序列 {c["series"]} 行 · 平台现成因子 '
            f'{c["native"]} 条 · 自建词库打分 {c["self_built"]} 条'
            + (f' · <strong>{c["failed_n"]}</strong> 个接口本次自动降级（单接口失败不阻断构建与推送）'
               if c['failed_n'] else ' · 全部接口取数成功')
            + f'<div class="pub-meta">数据日期 {esc(c["date"] or "—")} · 模式 {clean(c["mode_note"], 60) or "—"}'
              ' · 全源不可用时温度计回退中性 50 并标注降级；来源明细仅保留在内部构建产物中。</div></div>')

    series = (s.get('series') or [])[-10:]
    if series:
        lis = ''.join(
            f"<li>{esc(r.get('date'))} · {clean(r.get('name'), 24)}"
            f"{'（' + clean(r.get('platform') or r.get('source'), 20) + '）' if not anon else ''}"
            f" <strong>{r.get('value')}</strong>"
            f' <span class="pub-meta">{clean(r.get("note"), 60)}</span></li>'
            for r in reversed(series))
        parts.append('<div class="pub-box"><div class="pub-sub">◆ 市场级新闻情绪指数序列（平台现成因子 · 日频 · 末 10 期）</div>'
                     f'<ul class="pixel-list">{lis}</ul>'
                     '<div class="pub-meta">不同接口的指数口径不同（基期 = 1.0 型 vs [-1,1] 情感均值型），'
                     '跨源不可直接比较；关注度/热度类序列见上方个股热度表。</div></div>')
    return '\n'.join(parts)


def reg_verdict_hint(r):
    if r.get('ok'):
        native = r.get('native_sentiment') or 0
        return f"取数成功（原生情感 {native} 条）" if native else '取数成功（文本→自建打分）'
    return '降级'


def _f2(v, fmt='.2f'):
    try:
        return format(float(v), fmt)
    except (TypeError, ValueError):
        return '\u2014'


def _esc(t):
    import html
    return html.escape(str(t if t is not None else ''), quote=False)


def fill_ai_quant_markers(html, market=None):
    """把模板里的行情快照 / 07 结论标记换成当次配对推荐。标记缺失则原样返回。"""
    if AI_QUANT_QUOTES in html:
        html = html.replace(
            AI_QUANT_QUOTES,
            build_ai_quant_html(
                '行情快照 恒生指数 恒生科技 标普 纳斯达克 道琼斯 黄金 原油 人民币',
                market, hint='tape'),
            1)
    if AI_QUANT_VERDICT in html:
        html = html.replace(
            AI_QUANT_VERDICT,
            build_ai_quant_html('核心结论 港股 配置 黄金 原油 防御', market, hint='hk_tape'),
            1)
    return html


def build_verdict_html(data, macro_data, community_data):
    """07 节核心结论（网页版）：逐条挂当次实时行情、当次快讯与实抓社区计数，杜绝写死历史点位或箱体。"""
    quotes = (data or {}).get('quotes') or {}
    hsi = quotes.get('HSI') or {}
    hstech = quotes.get('HSTECH') or {}
    gold = quotes.get('GOLD') or {}

    if hsi.get('last') is not None:
        hk_tape = (f"{quote_date_cn(hsi)}恒指 {fmt_last(hsi)}（{fmt_pct(hsi)}），"
                   f"恒科 {fmt_last(hstech)}（{fmt_pct(hstech)}）")
    else:
        hk_tape = "当次恒指与恒科实时行情暂缺（未回填历史点位）"

    def top_macro(cat):
        items = (((macro_data or {}).get('categories') or {}).get(cat) or {}).get('items') or []
        if not items:
            return '当次快讯窗口内没有覆盖该主题的报道（按时效留空，不回填旧闻）'
        it = items[0]
        d = _md_cn(it.get('published_date') or '')
        return f"[{_esc(d)}] {_esc(it.get('title') or '')}"

    comms = (community_data or {}).get('communities') or []
    counts = {'bull': 0, 'bear': 0, 'neutral': 0, 'mixed': 0, 'unavailable': 0}
    if comms:
        for c in comms:
            vc = (c or {}).get('verdict_class') or 'unavailable'
            if vc in ('bull', 'bear', 'neutral', 'mixed'):
                counts[vc] += 1
            else:
                counts['unavailable'] += 1
    else:
        counts['unavailable'] = len(community_mod.COMMUNITIES)
    live_n = counts['bull'] + counts['bear'] + counts['neutral'] + counts['mixed']
    total_n = len(comms) if comms else len(community_mod.COMMUNITIES)
    if live_n > 0:
        comm_line = (f"当次实抓 {live_n}/{total_n} 个社区源：偏多 {counts['bull']} 家 · "
                     f"偏空 {counts['bear']} 家 · 中性 {counts['neutral']} 家 · "
                     f"多空分歧 {counts['mixed']} 家（其余 {counts['unavailable']} 家未获取，不计入分母）")
    else:
        comm_line = f"当次 0/{total_n} 个社区源抓到可核验热评（全部标注「未获取」，不编造多空家数）"

    gold_str = f"现货黄金 {fmt_last(gold)}（{fmt_pct(gold)}）" if gold.get('last') is not None else "现货黄金暂缺"
    return (
        '      <ul class="pixel-list">\n'
        f'        <li><strong>港股盘面</strong>：{_esc(hk_tape)}；{top_macro("hk")}；</li>\n'
        f'        <li><strong>宏观与离岸流动性</strong>：{top_macro("macro")}；美联储与汇率面：{top_macro("fed")}；</li>\n'
        f'        <li><strong>大宗与机构跟踪</strong>：{_esc(gold_str)}；商品面：{top_macro("commodities")}；机构观点：{top_macro("bank_views")}；</li>\n'
        f'        <li><strong>社区实抓情绪面</strong>：{_esc(comm_line)}。</li>\n'
        '      </ul>\n'
        '      <div class="pub-meta" style="margin-top:6px;">注：本节结论逐条对应当次实时行情、时效窗口内快讯与实抓社区片段，不预设技术箱体或历史均线点位。</div>'
    )


def inject_verdict(template, verdict_html):
    if VERDICT_BEGIN in template and VERDICT_END in template:
        pattern = re.compile(re.escape(VERDICT_BEGIN) + r'.*?' + re.escape(VERDICT_END), re.S)
        new_html, count = pattern.subn(f"{VERDICT_BEGIN}\n{verdict_html}\n{VERDICT_END}", template)
        if count:
            return new_html
    return template


def inject_sentiment(template, block_html):
    """把舆情节点注入模板 SENTIMENT_LIST 标记之间（无标记则跳过，保证向后兼容）。"""
    if SENTIMENT_LIST_BEGIN in template and SENTIMENT_LIST_END in template:
        pattern = re.compile(re.escape(SENTIMENT_LIST_BEGIN) + r'.*?' + re.escape(SENTIMENT_LIST_END), re.S)
        new_html, count = pattern.subn(f"{SENTIMENT_LIST_BEGIN}\n{block_html}\n{SENTIMENT_LIST_END}", template)
        if count:
            print('  🗞️  已动态注入 03B 舆情因子节点')
            return new_html
    print('  ⚠️ 未找到 SENTIMENT_LIST 标记，跳过舆情节点注入', file=sys.stderr)
    return template


def main():
    ap = argparse.ArgumentParser(description='章鱼 AI·舆情004 — 动态建站（行情+社区+舆情三动态+量化指标+跨域配对）')
    ap.add_argument('--data', default='market_data.json', help='行情数据 JSON 路径')
    ap.add_argument('--community', default='community_data.json', help='社区数据 JSON 路径')
    ap.add_argument('--sentiment', default='sentiment_data.json',
                    help='舆情因子数据 JSON 路径（sentiment_factors.py 生成）')
    ap.add_argument('--macro', default='macro_data.json',
                    help='宏观/财经快讯 JSON 路径（macro_data.py 生成，02 节 MACROLIST 注入源）')
    ap.add_argument('--macro-days', type=int, default=macro_data_mod.MAX_AGE_DAYS_DEFAULT,
                    help='快讯时效窗口（天），构建期补抓时使用')
    ap.add_argument('--macro-timeout', type=int, default=macro_data_mod.AUTO_FETCH_TIMEOUT,
                    help='构建期补抓的单请求超时秒数')
    ap.add_argument('--macro-no-fetch', action='store_true',
                    help='禁用构建期自动补抓（产物缺失时直接显示「今日未获取」）')
    ap.add_argument('--macro-fetch', action='store_true',
                    help='强制开启构建期自动补抓（本地默认不联网，CI 默认开）')
    ap.add_argument('--macro-mock', action='store_true',
                    help='构建期补抓走离线回放 tests/fixtures（联调用）')
    ap.add_argument('--forecast-history', default=None,
                    help='AI 预测存档路径（默认 forecast_history.json；FORECAST_HISTORY 可覆盖）')
    ap.add_argument('--forecast-no-history', action='store_true',
                    help='不写预测存档、不结算历史预测（04 节仍照常预测，只是没有命中率回看）')
    ap.add_argument('--template', default='report.html', help='模板文件路径')
    ap.add_argument('--out', default='report.html', help='输出文件路径')
    ap.add_argument('--check', action='store_true', help='只校验占位符，不写文件')
    args = ap.parse_args()

    if not os.path.exists(args.template):
        print(f'错误: 找不到模板 {args.template}', file=sys.stderr)
        sys.exit(1)

    with open(args.template, encoding='utf-8') as f:
        template = f.read()

    leftovers = find_leftovers(template)
    if (not leftovers and COMMUNITY_LIST_BEGIN not in template
            and SENTIMENT_LIST_BEGIN not in template and MACROLIST_MARK not in template
            and MACROLIST_BEGIN not in template and VIX_MARK not in template
            and VIX_BEGIN not in template and PANORAMA_MARK not in template
            and PANORAMA_BEGIN not in template and FORECAST_MARK not in template
            and FORECAST_BEGIN not in template):
        print(f'错误: {args.template} 中没有 {{占位符}}，疑似已构建过的产物。\n'
              f'仓库中的 report.html 应保持模板版本；恢复: git checkout -- report.html',
              file=sys.stderr)
        sys.exit(2)

    data = {}
    if os.path.exists(args.data):
        try:
            with open(args.data, encoding='utf-8') as f:
                data = json.load(f)
        except ValueError as e:
            print(f'警告: {args.data} 解析失败({e})，将全部按缺失处理', file=sys.stderr)
    else:
        print(f'警告: 未找到 {args.data}，行情将全部显示 "—"；请先运行 python3 market_data.py',
              file=sys.stderr)

    community_data = {}
    if os.path.exists(args.community):
        try:
            with open(args.community, encoding='utf-8') as f:
                community_data = json.load(f)
        except ValueError as e:
            print(f'警告: {args.community} 解析失败({e})，社区将标记为未获取', file=sys.stderr)
    else:
        print(f'警告: 未找到 {args.community}，社区将标记为未获取；请先运行 python3 community_data.py',
              file=sys.stderr)

    sentiment_data = {}
    if os.path.exists(args.sentiment):
        try:
            with open(args.sentiment, encoding='utf-8') as f:
                sentiment_data = json.load(f)
        except ValueError as e:
            print(f'警告: {args.sentiment} 解析失败({e})，舆情因子节点显示降级说明', file=sys.stderr)
    else:
        print('提示: 未找到 sentiment_data.json，03B 舆情因子节点显示降级说明；'
              '可先运行 python3 sentiment_factors.py --mock 生成', file=sys.stderr)

    now = datetime.now(timezone.utc)
    quant_pair.reset_hidden_render_count()      # 行情不足的隐藏处数：本次构建单独计数

    # 02 节快讯产物：缺失 / 写坏 / 旧快照时**就地补抓一次**（构建期兜底）。
    # 线上 workflow 至今没有 macro_data.py 这一步（补丁待有 workflows 权限的账号应用），
    # 这里在 build_site 内部补上通路，deploy / wechat / daily 三个 job 都自动受益。
    # --check 是纯校验模式，绝不联网。
    macro_allow = False if (args.check or args.macro_no_fetch) else (
        True if args.macro_fetch else None)
    macro_res = macro_data_mod.ensure(
        args.macro, max_age_days=args.macro_days, timeout=args.macro_timeout,
        mock=args.macro_mock, quiet=False, now=now, allow_fetch=macro_allow)
    macro_data = macro_res['data'] or {}
    if not isinstance(macro_data, dict):
        print(f'警告: {args.macro} 结构异常（{type(macro_data).__name__}），'
              '02 节走「今日未获取」兜底', file=sys.stderr)
        macro_data = {}
    if macro_res['action'] == 'fetched':
        print(f'  🔄 构建期补抓宏观快讯成功：{macro_res["kept"]} 条入库 '
              f'（{macro_res["elapsed_ms"]} ms）→ {args.macro}')
    elif macro_res['action'] == 'refetch_unavailable':
        print(f'  ⚠️ 构建期补抓宏观快讯：{macro_res["elapsed_ms"]} ms 后仍无可用条目'
              f'（{macro_res["reason"]}）→ 02 节显示「今日未获取」，不回填历史叙事')
    elif macro_res['action'] == 'no_refetch':
        print('  ℹ️ 宏观快讯当次已抓取过但窗口内 0 条，跳过重复补抓')
    elif macro_res['action'] == 'disabled':
        print('提示: 未找到 macro_data.json，02 节宏观快讯显示「今日未获取」；'
              '可先运行 python3 macro_data.py --mock 生成，'
              '或用 --macro-fetch / MACRO_AUTO_FETCH=1 开启构建期补抓', file=sys.stderr)

    # 兜底保障（单一拦截点，与微信推送同一口径）：快讯不可用时就地清空条目，
    # 保证 02 节兜底文案之外，01 节全景扫描也拿不到旧闻当证据。
    macro_avail = macro_data_mod.availability(macro_data, now=now)
    if not macro_avail['ok'] and (macro_data or {}).get('categories'):
        macro_data = dict(macro_data)
        macro_data['categories'] = {k: {'label': (v or {}).get('label') or k, 'items': []}
                                    for k, v in (macro_data.get('categories') or {}).items()}
        macro_data['summary'] = dict(macro_data.get('summary') or {}, kept_items=0)
        macro_data['unavailable'] = macro_avail
        print(f'  🛟 宏观快讯判定为不可用（{macro_avail["reason"]}），已清空条目走兜底：'
              f'{macro_avail["detail"]}')

    tokens = build_tokens(data, now, community_data, sentiment_data)

    if community_data and community_data.get('communities'):
        community_html = build_community_html(community_data['communities'], market=data)
        template = inject_community_list(template, community_html)
    else:
        print('  ℹ️ 未读到有效 community_data.json，03 节按严格真实口径注入 49 源「未获取」状态（不保留旧静态卡片）')
        offline_comms = community_mod.offline_dataset(market=data, now=now)['communities']
        template = inject_community_list(template, build_community_html(offline_comms, market=data))
    template = ensure_community_ai_quant(template, market=data)

    template = inject_vix(template, build_vix_html(data))
    template = inject_panorama(template, build_panorama_html(
        data, macro_data, sentiment_data, community_data, now=now))
    template = inject_macro_list(template, build_macro_html(macro_data, now=now, market=data))
    template = inject_sentiment(template, build_sentiment_html(sentiment_data, market=data))
    template = inject_forecast(template, build_forecast_html(
        data, macro_data, sentiment_data, community_data, now=now,
        history=not (args.check or args.forecast_no_history),
        history_path=args.forecast_history))
    template = inject_verdict(template, build_verdict_html(data, macro_data, community_data))
    template = fill_ai_quant_markers(template, market=data)

    missing = sorted(set(find_leftovers(template)) - set(tokens))
    if missing:
        missing_str = ", ".join(missing)
        print(f'错误: 模板中存在未定义的占位符: {missing_str}', file=sys.stderr)
        sys.exit(3)

    built = substitute(template, tokens)
    leftover_after = find_leftovers(built)
    if leftover_after:
        leftover_str = ", ".join(leftover_after)
        print(f'错误: 替换后仍有残留占位符: {leftover_str}', file=sys.stderr)
        sys.exit(4)

    if args.check:
        print(f'✔ 校验通过: {len(tokens)} 个占位符均可解析，无残留。')
        if community_data:
            comm_count = len(community_data.get("communities", []))
            print(f'  社区: {comm_count} 个源已加载')
        return

    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(built)
    replaced = sum(template.count(t) for t in tokens)
    ts_full_token = tokens["{{TS_FULL}}"]
    fetch_status_token = tokens["{{FETCH_STATUS}}"]
    community_status_token = tokens["{{COMMUNITY_FETCH_STATUS}}"]
    print(f'🏗️  已构建: {args.template} → {args.out} '
          f'(替换 {replaced} 处占位符 · {ts_full_token})')
    print(f'   行情状态: {fetch_status_token}')
    print(f'   社区状态: {community_status_token}')
    print(f"   舆情状态: {tokens.get('{{SENT_SOURCE_STATUS}}')} · 温度计 {tokens.get('{{SENT_TEMP}}')}"
          f"（{tokens.get('{{SENT_LABEL}}')}）· {tokens.get('{{SENT_MODE}}')}")
    macro_summ = (macro_data or {}).get('summary') or {}
    print(f"   宏观快讯: {_macro_status(macro_data)}"
          + (f" · 入库 {macro_summ.get('kept_items')} 条" if macro_data else ''))
    hidden_n = quant_pair.hidden_render_count()
    if hidden_n:
        print(f'  🔒 行情不足：{hidden_n} 处 AI 量化配对已整段隐藏（两腿涨跌幅不齐，'
              '不渲染「数据不足」，也不编方向）')


if __name__ == '__main__':
    main()
