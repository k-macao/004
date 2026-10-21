#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 量化平台「现成舆情/新闻因子」API 接入实测工具
=====================================================

对 sentiment_sources.SOURCES 里的每个数据源，按 9 个阶段逐项实测并给出接入判定：

  deps 依赖 → net 网络可达 → auth 鉴权 → fetch 取数 → fields 字段完整度
  → fresh 时效 → cover 覆盖率 → latency 延迟 → quota 额度

输出三份产物（构建产物默认不入库，评测文档 docs/sentiment-api-eval.md 入库）：
  • api_probe_report.json        机器可读全量结果（sentiment_factors.py 会带进日报）
  • docs/sentiment-api-eval.md   人类可读评测报告（能力矩阵 + 实测明细 + 落地方案）
  • 控制台表格                    跑完直接看

两种模式
  --live  真实调用（必须：① 境内出口 —— 聚宽/米筐等会按 IP 拒绝境外访问；
                        ② 凭据走环境变量，本工具只检测是否存在，绝不打印/落盘）
  --mock  用 tests/fixtures 录制报文回放，验证解析链路 + 输出"文档基线"评测
          （境外 CI / 无凭据环境下唯一可靠的跑法，评测结论会明确标注"未实测"）

用法:
  python3 tools/probe_sentiment_apis.py --mock
  python3 tools/probe_sentiment_apis.py --live --only RQ_SDK,UQER_HTTP --repeat 3
  python3 tools/probe_sentiment_apis.py --live --json api_probe_report.json --md docs/sentiment-api-eval.md
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import sentiment_adapters as ad          # noqa: E402
import sentiment_sources as reg          # noqa: E402

DEFAULT_JSON = os.path.join(REPO_ROOT, 'api_probe_report.json')
DEFAULT_MD = os.path.join(REPO_ROOT, 'docs', 'sentiment-api-eval.md')

# 舆情/新闻因子的"关键字段"（缺失即判 fields 阶段不通过）
CRITICAL_FIELDS = {
    'RQ_SDK': ['title', 'sentiment', 'published_at'],
    'RQ_HTTP': ['title', 'sentiment', 'published_at'],
    'UQER_HTTP': ['value'],
    'CHINASCOPE': ['value'],
    'EM_COMMENT': ['value'],
    'JIN10_WEIBO': ['value'],
    'EM_NEWS': ['title', 'published_at'],
    'TUSHARE_NEWS': ['title', 'published_at'],
    'JQ_HTTP': ['value'],
    'JQ_SDK': ['value'],
    'GM_SDK': [],
}

FRESH_DAYS = {'live': 3, 'mock': 3}


def _st(name, ok, detail):
    """单个阶段结果。"""
    return {'stage': name, 'ok': bool(ok), 'detail': detail}


def probe_source(sid, mode, repeat=3, timeout=8, watchlist=None):
    """对单个源跑完 9 个阶段，返回评测条目（含打分与判定）。"""
    src = reg.get_source(sid) or {}
    stages = []

    ok, detail = ad.check_deps(sid)
    stages.append(_st('deps', ok, detail))
    if not ok:
        # 能力缺失属产品事实（如掘金无舆情接口）时，即便本机没装 SDK 也照实给结论
        hint = src.get('verdict_hint')
        if hint:
            return _finish(sid, src, mode, stages, verdict=hint,
                           note='依据官方文档判定（不依赖本机 SDK）：' + (src.get('notes') or '')[:120])
        return _finish(sid, src, mode, stages, verdict='UNKNOWN',
                       note='运行环境缺依赖，未实测；在有 SDK 的机器上重跑 --live')

    if mode == 'live':
        ok, detail = ad.check_net(sid, timeout=min(timeout, 6))
        stages.append(_st('net', ok, detail))
        if not ok:
            return _finish(sid, src, mode, stages, verdict='NET_BLOCKED',
                           note='出口受限：境内数据站按 IP 拒绝境外访问，须在境内执行器（自建 runner/云函数）实测')
    else:
        stages.append(_st('net', True, 'mock 模式跳过网络检测（未产生任何请求）'))

    ok, detail = ad.check_auth(sid)
    stages.append(_st('auth', ok, detail))
    if mode == 'live' and not ok:
        return _finish(sid, src, mode, stages, verdict='NO_CREDENTIAL',
                       note='未配置凭据环境变量，无法实测鉴权与取数')

    # 取数（repeat 次测延迟；首次结果用于字段/时效/覆盖率判定）
    res, latencies = None, []
    for i in range(max(1, repeat)):
        r = ad.fetch(sid, mode=mode, symbols=_symbols(src, watchlist), days=3, timeout=timeout)
        latencies.extend(r.get('latencies') or [])
        if res is None or r['ok']:
            res = r
    res = res or ad.result(sid)
    stages.append(_st('fetch', res['ok'],
                      f"news={len(res['news'])} series={len(res['series'])} 行"
                      if res['ok'] else f"取数失败（{res['stage']}）：{(res['error'] or '')[:150]}"))
    if res['stage'] == 'fetch' and not res['ok'] and res['error']:
        verdict = 'NOT_SUPPORTED' if src.get('verdict_hint') else 'UNKNOWN'
        if 'Permission' in (res['error'] or '') or '权限' in (res['error'] or ''):
            verdict = 'READY_WITH_LICENCE'
        return _finish(sid, src, mode, stages, verdict=verdict, note=res['error'][:220])
    if not res['ok']:
        return _finish(sid, src, mode, stages,
                       verdict=src.get('verdict_hint') or 'NOT_SUPPORTED',
                       note=(res.get('meta') or {}).get('note') or res['error'] or '无返回数据')

    # fields 字段完整度
    need = CRITICAL_FIELDS.get(sid, [])
    have = set(res['fields'])
    missing = [f for f in need if f not in have]
    rows = res['news'] + res['series']
    nonnull = sum(1 for r in rows for f in (need or ['value']) if r.get(f) not in (None, ''))
    ratio = round(nonnull / max(1, len(rows) * len(need or ['value'])), 3)
    stages.append(_st('fields', not missing and ratio > 0.5,
                      f'关键字段 {need or "—"} 缺失 {missing or "无"}；非空率 {ratio:.0%}'))

    # fresh 时效
    latest = res['latest_date']
    fresh_ok, fresh_detail = False, '无日期字段'
    if latest:
        try:
            days = (datetime.now(timezone.utc).date() -
                    datetime.strptime(latest[:10], '%Y-%m-%d').date()).days
            fresh_ok = days <= FRESH_DAYS.get(mode, 3)
            fresh_detail = f'最新记录 {latest}（距今 {days} 天，阈值 ≤{FRESH_DAYS.get(mode,3)} 天）'
        except ValueError:
            fresh_detail = f'日期格式异常: {latest}'
    if mode == 'mock':
        fresh_ok = True
        fresh_detail += ' · mock 模式按录制日期判定，实际时效需 --live 复核'
    stages.append(_st('fresh', fresh_ok, fresh_detail))

    # cover 覆盖率
    want = _symbols(src, watchlist)
    got = {str(r.get('symbol') or (r.get('extra') or {}).get('symbol') or
               (r.get('extra') or {}).get('secID') or '').split('.')[0] for r in rows}
    got = {g for g in got if g}
    hit = len(got) if got else len(rows)
    cover = round(min(1.0, hit / max(1, len(want))), 3)
    stages.append(_st('cover', cover > 0,
                      f'抽样 {len(want)} 个标的/关键词 → 命中 {len(got) or hit}（覆盖率 {cover:.0%}）'
                      if want else f'市场级接口，返回 {len(rows)} 行'))

    # latency
    lat_sorted = sorted(latencies or [res['latency_ms']])
    p50 = lat_sorted[len(lat_sorted) // 2] if lat_sorted else 0.0
    p95 = lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * 0.95))] if lat_sorted else 0.0
    lat_ok = mode == 'mock' or (p95 and p95 <= 6000)
    stages.append(_st('latency', lat_ok,
                      f'p50 {p50:.0f}ms / p95 {p95:.0f}ms'
                      + ('（mock 记录为 0，不代表线上延迟）' if mode == 'mock' else '')))

    # quota
    q = res.get('quota') or {}
    stages.append(_st('quota', True, q.get('note') or src.get('quota') or '未见明确限额，上线前压测确认'))

    native = sum(1 for n in res['news'] if n.get('sentiment') is not None)
    if src.get('verdict_hint'):
        verdict = src['verdict_hint']
    elif native or src.get('kind') in ('uqer_http', 'chinascope_http'):
        verdict = 'READY' if not src.get('auth_env') else 'READY_WITH_LICENCE'
    elif src.get('kind') in ('em_datacenter', 'jin10_weibo'):
        verdict = 'PARTIAL'
    else:
        verdict = 'PARTIAL'
    if native:
        head = f'平台直接给出情感/情绪字段（{native} 条可原生入模）'
    elif src.get('kind') in ('uqer_http', 'chinascope_http'):
        head = '平台直接给出日频情绪指数（市场级现成因子，无需自建 NLP）'
    elif src.get('kind') in ('em_datacenter', 'jin10_weibo'):
        head = '只有热度/关注度、无极性，需配自建词库（sentiment_nlp）补足情感维度'
    elif src.get('kind') == 'jq_http':
        head = '聚宽"情绪因子"为量价口径（换手/AR-BR/ATR），新闻侧仅文本，需自建打分'
    else:
        head = '给到的是原始新闻文本，需自建词库（sentiment_nlp）打分'
    note = f'{head}；本次取数 {len(rows)} 行'
    return _finish(sid, src, mode, stages, verdict=verdict, note=note,
                   stats={'news': len(res['news']), 'series': len(res['series']),
                          'native_sentiment': native, 'latest_date': latest,
                          'p50_ms': round(p50, 1), 'p95_ms': round(p95, 1)})


def _symbols(src, watchlist):
    if not watchlist:
        watchlist = list(getattr(reg, 'WATCHLIST', []) or []) or []
    kind = src.get('kind')
    if kind in ('em_datacenter', 'em_search_api'):
        return [w.split('.')[0] for w in watchlist]
    if src.get('id', '').startswith(('JQ', 'RQ')):
        return [f"{c}.{('XSHG' if ex == 'SH' else 'XSHE')}" for c, ex in
                (w.split('.') for w in watchlist)]
    return list(watchlist)


def _score(src, stages, mode):
    """文档基线分 + 实测校正（未实测的维度保持基线并标注）。"""
    base = dict(src.get('doc_scores') or {k: 0 for k, _, _, _ in reg.SCORE_DIMENSIONS})
    by = {s['stage']: s for s in stages}
    tested = mode == 'live'
    note = []
    if not by.get('fetch', {'ok': True})['ok']:
        base['ready_factor'] = min(base.get('ready_factor', 0), 4)
        base['stability'] = 0 if tested else base.get('stability', 0)
        note.append('取数未通过')
    if not by.get('fresh', {'ok': True})['ok']:
        base['timeliness'] = max(0, base.get('timeliness', 0) - 8)
        note.append('时效不达标')
    if not by.get('latency', {'ok': True})['ok']:
        base['stability'] = max(0, base.get('stability', 0) - 5)
        note.append('延迟偏高')
    if not by.get('auth', {'ok': True})['ok']:
        base['cost'] = max(0, base.get('cost', 0) - 6)
        note.append('权限未开通')
    total = sum(base.get(k, 0) for k, _, _, _ in reg.SCORE_DIMENSIONS)
    cap = {d: w for d, w, _, _ in reg.SCORE_DIMENSIONS}
    base = {k: max(0, min(v, cap[k])) for k, v in base.items()}
    total = sum(base.values())
    return base, round(total, 1), ('实测' if tested else '文档基线（未实测）') + \
        (('；' + '，'.join(note)) if note else '')


def _finish(sid, src, mode, stages, verdict='UNKNOWN', note='', stats=None):
    scores, total, score_note = _score(src, stages, mode)
    passed = sum(1 for s in stages if s['ok'])
    return {
        'id': sid, 'platform': src.get('platform', ''), 'name': src.get('name', ''),
        'category': src.get('category', ''), 'access': src.get('access', ''),
        'mode': mode, 'stages': stages, 'stages_passed': f'{passed}/{len(reg.PROBE_STAGES)}',
        'verdict': verdict, 'verdict_label': reg.VERDICT_LABEL.get(verdict, verdict),
        'scores': scores, 'score_total': total, 'score_note': score_note,
        'update_freq': src.get('update_freq', ''), 'history': src.get('history', ''),
        'coverage': src.get('coverage', ''), 'quota': src.get('quota', ''),
        'cost': src.get('cost', ''), 'licence_note': src.get('licence_note', ''),
        'confidence': src.get('confidence', ''), 'notes': src.get('notes', ''),
        'docs': src.get('docs', []), 'stats': stats or {}, 'summary': note,
    }


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------
def _w(s):
    """显示宽度（CJK 记 2 列），用于控制台对齐。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in str(s))


def _pad(s, width):
    s = str(s)
    return s + ' ' * max(0, width - _w(s))


def console_table(items):
    cols = [('源', 14), ('平台', 24), ('阶段', 10), ('判定', 30), ('评分', 6), ('关键说明', 0)]
    head = ''.join(_pad(n, w) for n, w in cols)
    lines = [head, '-' * _w(head)]
    for it in sorted(items, key=lambda x: -x['score_total']):
        lines.append(_pad(it['id'], 14) + _pad(it['platform'], 24) + _pad(it['stages_passed'], 10)
                     + _pad(it['verdict_label'], 30) + _pad(it['score_total'], 6)
                     + (it['summary'] or '')[:52])
    return '\n'.join(lines)


def render_markdown(items, meta):
    """生成评测报告（docs/sentiment-api-eval.md）。"""
    ranked = sorted(items, key=lambda x: -x['score_total'])
    live = meta['mode'] == 'live'
    L = []
    A = L.append
    A('# 量化平台「现成舆情 / 新闻因子」API 接入评测')
    A('')
    A(f"> 生成时间：{meta['generated_at']}　·　运行模式：**{'联网实测 live' if live else '离线回放 mock（文档基线）'}**　"
      f"·　数据源：{len(items)} 个　·　由 `tools/probe_sentiment_apis.py` 自动生成，请勿手工编辑")
    A('')
    A('> **对外展示策略（内部档案）**：本文件不在网页与微信推送中展示 —— 03B 节**不渲染**该评测矩阵，'
      '且**不显示数据来源平台**（平台名 / 接口 ID / 域名 / 凭据与依赖提示由 `sentiment_match.redact()` '
      '统一遮成「量化平台」，逐源明细表默认不出）。对外只展示采集合成后的因子读数与'
      '「舆情因子 × 日报标的」匹配结果。临时恢复内部视图：'
      '`SENTIMENT_SHOW_API_EVAL=1`（评测矩阵）、`SENTIMENT_SHOW_SOURCE=1`（来源明细）。')
    A('')
    A('## 一、结论速览')
    A('')
    A('| 排名 | 平台 / 接口 | 现成因子 | 时效 | 评分 | 接入判定 |')
    A('|---:|---|---|---|---:|---|')
    for i, it in enumerate(ranked, 1):
        A(f"| {i} | **{it['platform']}** · {it['name']} | {it['category']} | "
          f"{it['update_freq']} | {it['score_total']} | {it['verdict_label']} |")
    A('')
    A('**推荐接入顺序（本日报场景）**')
    A('')
    top = [x for x in ranked if x['verdict'] in ('READY', 'READY_WITH_LICENCE', 'PARTIAL')]
    for i, it in enumerate(top[:5], 1):
        A(f"{i}. `{it['id']}`（{it['platform']}）— {(it['notes'] or '').split('；')[0][:80]}")
    A('')
    A('**三条硬约束（实测踩坑，直接影响架构选择）**')
    A('')
    A('1. **境内出口**：聚宽/米筐等站点按 IP 拒绝境外访问，GitHub 海外 runner 直连必失败 → '
      '舆情抓取放境内执行器，海外 runner 只做建站与推送；')
    A('2. **聚宽权限**：试用账号「因子和特色数据 = 无」，情绪因子 / 雪球热度 / 新闻联播属特色数据，'
      '需标准版以上（2 亿条/天）；且官方已标注旧版 HTTP 接口「不再维护，随时可能下线」；')
    A('3. **掘金无舆情**：`gm.api` 只有行情/财务/成分/日历/估值，且 SDK 依赖本地掘金终端代理（CI 不可用）'
      '—— 舆情必须外挂，掘金只当行情与执行通道。')
    A('')
    A('## 二、能力矩阵')
    A('')
    A('| 源 | 平台 | 接入方式 | 关键字段 / 方法 | 更新频率 | 历史 | 额度 | 成本与权限 |')
    A('|---|---|---|---|---|---|---|---|')
    for it in ranked:
        src = reg.get_source(it['id']) or {}
        fields = []
        for k, v in (src.get('sentiment_fields') or {}).items():
            if isinstance(v, list):
                fields.append(f"{k}: {', '.join(map(str, v))}")
        A(f"| `{it['id']}` | {it['platform']} | {it['access']} | "
          f"{'<br>'.join(fields)[:220] or '—'} | {src.get('update_freq','')} | "
          f"{src.get('history','')} | {src.get('quota','')} | {src.get('cost','')} |")
    A('')
    A('## 三、评分明细（100 分制）')
    A('')
    dims = reg.SCORE_DIMENSIONS
    A('| 源 | ' + ' | '.join(f'{name}({w})' for _k, w, name, _d in dims) + ' | 合计 | 依据 |')
    A('|---|' + '---:|' * (len(dims) + 2))
    for it in ranked:
        A(f"| `{it['id']}` | " +
          ' | '.join(str(it['scores'].get(k, 0)) for k, _, _, _ in dims) +
          f" | **{it['score_total']}** | {it['score_note']} |")
    A('')
    A('维度定义：')
    for key, weight, name, desc in dims:
        A(f"- **{name}**（{weight} 分）：{desc}")
    A('')
    A('## 四、逐源实测明细')
    A('')
    for it in ranked:
        A(f"### `{it['id']}` · {it['platform']} — {it['verdict_label']}（{it['score_total']} 分）")
        A('')
        A(f"- 接口：{it['name']}")
        A(f"- 端点 / 调用：`{(reg.get_source(it['id']) or {}).get('endpoint') or 'SDK 调用'}` "
          f"· 方法：{', '.join((reg.get_source(it['id']) or {}).get('methods') or [])[:180]}")
        A(f"- 本次结果：{it['summary']}")
        if it['stats']:
            st = it['stats']
            A(f"- 取样统计：新闻 {st.get('news', 0)} 条 / 序列 {st.get('series', 0)} 行 / "
              f"平台原生情感 {st.get('native_sentiment', 0)} 条 / 最新日期 {st.get('latest_date') or '—'} / "
              f"p50 {st.get('p50_ms', 0)}ms · p95 {st.get('p95_ms', 0)}ms")
        A('- 阶段明细：')
        A('')
        A('| 阶段 | 结果 | 说明 |')
        A('|---|:--:|---|')
        by = {s['stage']: s for s in it['stages']}
        for key, name, desc in reg.PROBE_STAGES:
            s = by.get(key)
            mark = '✅' if (s and s['ok']) else ('⚪' if not s else '❌')
            A(f"| {name} | {mark} | {(s['detail'] if s else desc)} |")
        A('')
        if it.get('licence_note'):
            A(f"- ⚠️ 权限提示：{it['licence_note']}")
        if it.get('notes'):
            A(f"- 说明：{it['notes']}")
        if it.get('docs'):
            A('- 文档：' + ' · '.join(f"<{d}>" for d in it['docs']))
        A('')
    A('## 五、落地接入方案（本仓库已实现）')
    A('')
    A('```')
    A('sentiment_sources.py    注册表：源、字段、鉴权 env、额度、口径（唯一事实源）')
    A('sentiment_adapters.py   适配器：live/mock 同一套解析；live 走 urllib，SDK 走反射导入')
    A('sentiment_nlp.py        自建情感层：词库 + 否定/程度 + 风险词 + 时间衰减 → 舆情因子')
    A('sentiment_factors.py    分层取数 + 因子合成 → sentiment_data.json（供建站与推送）')
    A('tools/probe_sentiment_apis.py  本工具：9 阶段实测 + 打分 + 报告')
    A('```')
    A('')
    A('**分层降级（保证 09:00 定时任务永不断供）**')
    A('')
    A('| 层 | 数据源 | 产出 | 失败时 |')
    A('|---|---|---|---|')
    A('| T0 | 米筐 `news.get_stock_news` / 优矿 `sentimentIndex` | 个股级现成情感因子 | 下探 T1 |')
    A('| T1 | 东财千股千评 / 金十微博人气 | 关注度、热度 Z 值 | 下探 T2 |')
    A('| T2 | 东财 search-api / Tushare news / 聚宽新闻联播 | 新闻文本 → 自建词库打分 | 下探 T3 |')
    A('| T3 | 数库市场情绪指数 / 聚宽情绪因子（VOL、AR/BR）对照 | 市场级温度计 | 沿用上次结果并标注降级 |')
    A('')
    A('**因子口径**（`sentiment_sources.FACTOR_LIBRARY`，与日报正文一致）')
    A('')
    A('| 因子 | 名称 | 单位 | 定义 | 首选源 |')
    A('|---|---|---|---|---|')
    for k, v in reg.FACTOR_LIBRARY.items():
        A(f"| `{k}` | {v['name']} | {v['unit']} | {v['definition']} | {v['best_source']} |")
    A('')
    A('**上线三步**')
    A('')
    A('1. 境内机器（或境内自建 runner）配置凭据环境变量后跑 `--live`，把 `api_probe_report.json` 结论落到本报告；')
    A('2. 权限确认：米筐/聚宽/优矿任一开通即接 T0；未开通则只上 T1+T2（免费源 + 自建词库），'
      '因子留 `platform_native=0` 标记，回测时单独分组；')
    A('3. 先跑 20 个交易日影子模式：只记录因子值不下单，核对 `NEWS_HEAT_Z` 与真实涨停/跌停事件的相关性，'
      '再进选股（舆情因子单独 IC 通常 0.02–0.05，须与量价/基本面合成）。')
    A('')
    A('## 六、需复核清单（confidence = needs_check）')
    A('')
    for it in ranked:
        src = reg.get_source(it['id']) or {}
        if src.get('confidence') != 'needs_check':
            continue
        A(f"- `{it['id']}` {it['platform']}：{src.get('licence_note') or src.get('notes') or ''}")
    A('')
    A('## 七、风险提示')
    A('')
    A('- 舆情/新闻因子含大量转载与标题党，**极性误判集中在反问句、引述与"否认传闻"类表述**，'
      '实盘前需对高风险词命中项做人工抽检；')
    A('- 免费公开接口（东财、金十、数库）无 SLA、可能随时改路径或加签名，必须保留降级与本地缓存；')
    A('- 转载第三方新闻正文仅用于内部研究，不对外再分发；抓取须遵守各站 robots 与频率自律；')
    A('- 本报告仅为数据接口评测，不构成投资建议。')
    A('')
    if not live:
        A('> 🔎 **本次为 mock 运行**：评分依据公开文档基线与录制报文，接口连通性、真实延迟、'
          '权限状态尚未实测；在境内有权环境执行 `python3 tools/probe_sentiment_apis.py --live` '
          '即可把「文档基线」升级为「实测」。')
        A('')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='章鱼 AI·舆情004 — 量化平台舆情/新闻因子 API 接入实测')
    ap.add_argument('--live', action='store_true', help='真实调用（需境内出口 + 凭据）')
    ap.add_argument('--mock', '--demo', dest='mock', action='store_true',
                    help='录制报文回放 + 文档基线评测（默认）')
    ap.add_argument('--only', default='', help='逗号分隔源 id，如 RQ_SDK,UQER_HTTP')
    ap.add_argument('--skip', default='', help='逗号分隔要跳过的源 id')
    ap.add_argument('--repeat', type=int, default=3, help='每源取数次数（用于延迟统计）')
    ap.add_argument('--timeout', type=float, default=8.0, help='单次请求超时（秒）')
    ap.add_argument('--watchlist', default=','.join(getattr(reg, 'WATCHLIST', []) or []), help='抽样标的（默认使用注册表 WATCHLIST）')
    ap.add_argument('--json', dest='json_out', default=DEFAULT_JSON, help='结果 JSON 输出路径')
    ap.add_argument('--md', dest='md_out', default=DEFAULT_MD, help='评测 Markdown 输出路径')
    ap.add_argument('--fail-on-blocked', action='store_true',
                    help='live 模式下出现 NET_BLOCKED 即以非零退出（CI 门禁用）')
    args = ap.parse_args()

    mode = 'live' if args.live else 'mock'
    only = [x.strip().upper() for x in args.only.split(',') if x.strip()]
    skip = {x.strip().upper() for x in args.skip.split(',') if x.strip()}
    watchlist = [x.strip() for x in args.watchlist.split(',') if x.strip()]

    targets = [sid for sid in reg.ids() if (not only or sid in only) and sid not in skip]
    print(f'🔬 舆情/新闻因子 API 接入实测 · 模式={"联网 live" if mode == "live" else "离线 mock"} '
          f'· 源 {len(targets)} 个 · 每源取数 {args.repeat} 次\n')
    items = []
    for sid in targets:
        print(f'— {sid}')
        items.append(probe_source(sid, mode, repeat=args.repeat, timeout=args.timeout,
                                  watchlist=watchlist))
        it = items[-1]
        print(f"   {it['verdict_label']:<22} {it['stages_passed']} 阶段 · "
              f"{it['score_total']} 分 · {it['summary'][:70]}")

    meta = {'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            'mode': mode, 'repeat': args.repeat, 'sources': len(items),
            'note': ('境内出口 + 凭据实测' if mode == 'live'
                     else '离线回放：解析链路已验证；连通性/延迟/权限未实测')}
    ranking = [{'id': x['id'], 'platform': x['platform'], 'name': x.get('name', ''),
                'endpoint': x.get('endpoint', ''), 'mode': x.get('mode', ''),
                'score': x['score_total'], 'verdict': x['verdict'],
                'verdict_label': x['verdict_label']}
               for x in sorted(items, key=lambda y: -y['score_total'])]
    conclusion = _conclusion(items, mode)
    report = {'meta': meta, 'matrix': items, 'ranking': ranking, 'conclusion': conclusion,
              'score_dimensions': [{'key': k, 'weight': w, 'name': n, 'desc': d}
                                   for k, w, n, d in reg.SCORE_DIMENSIONS],
              'stages': [{'key': k, 'name': n, 'desc': d} for k, n, d in reg.PROBE_STAGES]}

    with open(args.json_out, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    md_dir = os.path.dirname(os.path.abspath(args.md_out))
    if md_dir:
        os.makedirs(md_dir, exist_ok=True)
    with open(args.md_out, 'w', encoding='utf-8') as f:
        f.write(render_markdown(items, meta))

    print('\n' + console_table(items))
    print('\n📌 结论：')
    for line in conclusion:
        print(f'   · {line}')
    print(f"\n✅ 已写出 {os.path.relpath(args.json_out, REPO_ROOT)} 与 "
          f"{os.path.relpath(args.md_out, REPO_ROOT)}")

    if args.fail_on_blocked and any(i['verdict'] == 'NET_BLOCKED' for i in items):
        sys.exit(4)


def _conclusion(items, mode):
    by = {i['id']: i for i in items}
    out = []
    if items:
        best = max(items, key=lambda x: x['score_total'])
        out.append(f"工程性价比最优（综合评分）：{best['platform']} · {best['name']}"
                   f"（{best['score_total']} 分，{best['verdict_label']}）—— 免鉴权、可日更、零成本")
        quality = [i for i in items if i['scores'].get('ready_factor', 0) >= 20]
        if quality:
            q = max(quality, key=lambda x: x['scores'].get('ready_factor', 0))
            out.append(f"因子质量最优（现成因子度 {q['scores']['ready_factor']}/30）："
                       f"{q['platform']} · {q['name']}（{q['verdict_label']}）—— 给到即可入模，但需授权")
    if 'GM_SDK' in by:
        out.append('掘金：无舆情/新闻因子接口（平台能力缺失，非故障），只作行情与执行通道')
    if 'RQ_SDK' in by:
        out.append('米筐：唯一"给到即入模"的舆情因子（三档情感权重 + 公司相关度），但需商务开权限 + 私有 pip 源')
    if 'JQ_SDK' in by or 'JQ_HTTP' in by:
        out.append('聚宽：情绪因子是量价口径，新闻侧只有文本；试用账号无因子权限，付费才可用；旧版 HTTP 接口官方已标注不再维护')
    if 'UQER_HTTP' in by:
        out.append('优矿：sentimentIndex/heatIndex 是现成日频舆情因子，有免费层，性价比最高（需复核账号现状）')
    out.append('无预算方案：东财热度 + 东财/Tushare 新闻文本 + 自建词库（sentiment_nlp），市场级温度计可日更')
    if mode == 'mock':
        out.append('本次为 mock：解析链路与报告管线已验证，真实连通/延迟/权限需在有凭据的境内机器跑 --live')
    return out


if __name__ == '__main__':
    main()
