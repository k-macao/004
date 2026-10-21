#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 舆情/新闻因子管线 (sentiment_factors.py)
=============================================================

把「量化平台现成舆情因子 + 自建情感打分层」的结果统一成 sentiment_data.json，
供 build_site.py（网页 03B 节）与 tools/wechat_push.py（微信推送）动态注入。

采集回来的新闻舆情会经 sentiment_match.py **匹配到日报标的与主题**（恒指/恒科/国企/标普/纳指/
道指/黄金/原油/人民币 + 美联储、内房、高息防御、地缘供应链），结果写入 `matches` 字段；
对外输出（网页 + 微信推送）**不显示数据来源**：平台名/接口 ID/凭据与依赖提示只留在
本文件产出的 `sources[]` 等内部字段与 docs/sentiment-api-eval.md 里。

取数优先级（高优先者可用即覆盖低优先者，全部失败仍产出，绝不阻断 09:00 推送）：
  T0 现成因子  RQ_SDK / RQ_HTTP（米筐新闻情绪）→ UQER_HTTP（优矿 sentimentIndex/heatIndex）
  T1 热度因子  EM_COMMENT（东财千股千评关注指数）→ JIN10_WEIBO（金十微博人气）
  T2 文本自建  EM_NEWS / TUSHARE_NEWS / JQ_SDK(新闻联播) → sentiment_nlp 词库打分
  T3 市场级    CHINASCOPE（数库 A 股新闻情绪指数）
  兜底        上次 sentiment_data.json（仅刷新时间戳）

用法:
  python3 sentiment_factors.py                 # 联网实测（需境内出口 + 凭据）
  python3 sentiment_factors.py --mock          # 用 tests/fixtures 录制报文回放（离线演示/CI）
  python3 sentiment_factors.py --offline       # 断网兜底：沿用上次结果，只刷新时间戳
  python3 sentiment_factors.py --sources RQ_SDK,UQER_HTTP --watchlist 601318.SH,600036.SZ
  python3 sentiment_factors.py --json /tmp/s.json --timeout 8 --quiet

设计约束（与 market_data.py / community_data.py 一致）：
  • 纯标准库；单源失败不阻断，失败原因写入 sources[].error 并在页面标注；
  • 凭据只从环境变量读，任何情况下都不写盘、不打日志；
  • 构建产物 sentiment_data.json / sentiment_history.json 不入库（见 .gitignore）。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import sentiment_adapters as ad
import sentiment_match as smatch
import sentiment_nlp as nlp
import sentiment_sources as reg

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(REPO_ROOT, 'sentiment_data.json')
HISTORY_PATH = os.path.join(REPO_ROOT, 'sentiment_history.json')
PROBE_REPORT = os.path.join(REPO_ROOT, 'api_probe_report.json')

# 取数优先级（同层内从左到右）
PRIORITY = [
    ['RQ_SDK', 'RQ_HTTP', 'UQER_HTTP'],        # T0 平台现成舆情因子
    ['EM_COMMENT', 'JIN10_WEIBO'],             # T1 热度/关注度因子
    ['EM_NEWS', 'TUSHARE_NEWS', 'JQ_SDK'],     # T2 文本 + 自建情感层
    ['CHINASCOPE', 'JQ_HTTP'],                 # T3 市场级/情绪因子对照
]

WATCHLIST_DEFAULT = list(reg.WATCHLIST)          # 样本池集中在注册表，便于 CLI 与 CI 复用

# 各家"情绪指数"口径不同，必须随值一起标注，否则页面读数会误导
# 口径说明对外展示，因此写成不含平台名的中性表述（来源仍记录在 series[].source 内部字段）
INDEX_CALIBRE = {
    'UQER_HTTP': '平台现成情感指数 ∈ [-1, 1]，当日关联新闻情感均值',
    'CHINASCOPE': '市场情绪指数，基期 = 1.0，>1 偏乐观、<1 偏悲观',
}


def _now():
    return datetime.now(timezone.utc)


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        print(f'⚠️ {os.path.basename(path)} 读取失败: {e}', file=sys.stderr)
        return default if default is not None else {}


def source_order(only=None):
    """按优先级展开成扁平列表；only 给定时只保留这些源（保持优先级顺序）。"""
    flat = [s for tier in PRIORITY for s in tier]
    for extra in reg.ids():
        if extra not in flat:
            flat.append(extra)
    if only:
        want = {x.strip().upper() for x in only if x.strip()}
        flat = [s for s in flat if s in want]
    return flat


def collect(mode, only=None, watchlist=None, timeout=ad.DEFAULT_TIMEOUT, verbose=True):
    """逐源取数，返回 (每源结果列表, 合并后的 news/series)。"""
    watchlist = watchlist or WATCHLIST_DEFAULT
    results, news, series, heat_snap = [], [], [], {}
    quota_notes = {}
    for sid in source_order(only):
        src = reg.get_source(sid) or {}
        symbols = _symbols_for(src, watchlist)
        r = ad.fetch(sid, mode=mode, symbols=symbols, days=3, timeout=timeout)
        brief = {'id': sid, 'platform': src.get('platform', ''), 'name': src.get('name', ''),
                 'ok': bool(r['ok']), 'mode': r['mode'], 'stage': r['stage'],
                 'news': len(r['news']), 'series': len(r['series']),
                 'latest_date': r['latest_date'], 'latency_ms': r['latency_ms'],
                 'error': r['error'], 'note': (r.get('meta') or {}).get('note', ''),
                 'native_sentiment': sum(1 for n in r['news'] if n.get('sentiment') is not None),
                 'category': src.get('category', '')}
        results.append(brief)
        if verbose:
            flag = '✔' if brief['ok'] else '✘'
            why = brief['error'] or ('平台无该能力（见注册表判定）' if 'none' in (brief['category'] or '')
                                     else '无有效数据行')
            print(f"  {flag} {sid:<13} {brief['platform']:<18} "
                  f"news={brief['news']:<4} series={brief['series']:<5} "
                  f"latest={brief['latest_date'] or '—'} "
                  f"{'' if brief['ok'] else '· ' + why[:64]}")
        if not r['ok']:
            continue
        news.extend(r['news'])
        for row in r['series']:
            row['source'] = sid          # 标记来源，便于页面区分不同平台的指数口径
        series.extend(r['series'])
        if sid == 'EM_COMMENT' or src.get('kind') == 'em_datacenter':
            for row in r['series']:
                key = (row.get('extra') or {}).get('factor')
                if key == '关注指数':
                    sym = ad.norm_symbol((row.get('extra') or {}).get('symbol', ''))
                    heat_snap.setdefault(sym, {'name': (row.get('extra') or {}).get('name', ''),
                                               'heat': row['value'], 'date': row['date']})
        if (r.get('quota') or {}).get('note'):
            quota_notes[sid] = r['quota']['note']
    return results, news, series, heat_snap, quota_notes


def _symbols_for(src, watchlist):
    """把统一 watchlist / 日报标的关键词映射成各平台的取数入参。

    关键词检索型源（em_search_api）改用 sentiment_match.search_keywords()：
    采集面与日报正文标的对齐（恒指 / 港股 / 黄金 / 原油 / 美联储…），
    采集回来的新闻才可能与 02 节行情标的「匹配显示」。
    """
    cov = src.get('coverage') or ''
    kind = src.get('kind')
    if kind == 'em_search_api':
        return smatch.search_keywords()
    if kind == 'em_datacenter':
        return [w.split('.')[0] for w in watchlist]
    if 'A 股' in cov or '沪深' in cov:
        if src.get('id', '').startswith('JQ'):
            return [f"{c}.{('XSHG' if ex == 'SH' else 'XSHE')}" for c, ex in
                    (w.split('.') for w in watchlist)]
        if src.get('id', '').startswith('RQ'):
            return [f"{c}.{('XSHG' if ex == 'SH' else 'XSHE')}" for c, ex in
                    (w.split('.') for w in watchlist)]
        return list(watchlist)
    return smatch.search_keywords()[:1] or ['恒生指数']


def _history_counts(today_count, path=None, keep=20):
    """把当日新闻条数追加进本地历史，返回近 N 日条数（供热度 Z 值）。"""
    path = path or HISTORY_PATH          # 运行时读取，便于测试 / CI 覆写 HISTORY_PATH
    hist = load_json(path, {'counts': {}})
    counts = hist.get('counts') or {}
    day = _now().strftime('%Y-%m-%d')
    counts[day] = today_count
    days = sorted(counts)[-keep:]
    out = [counts[d] for d in days[:-1]]           # 不含当日，避免自相关
    hist['counts'] = {d: counts[d] for d in days}
    hist['updated_at'] = _now().strftime('%Y-%m-%d %H:%M:%S UTC')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(hist, f, ensure_ascii=False, indent=1)
    except OSError as e:
        print(f'⚠️ 历史缓存写入失败（不影响本次产出）: {e}', file=sys.stderr)
    return out


def build_factors(results, news, series, heat_snap, quota_notes, mode, watchlist):
    """合并成最终 sentiment_data.json 结构。"""
    hist = _history_counts(len(news)) if news else []
    market = nlp.aggregate(news, history_counts=hist) if news else nlp.aggregate([], history_counts=[])
    if not news:  # 全源失败：温度计退回中性并明确标注
        market = nlp.aggregate([], history_counts=[])
        market['degraded'] = True

    # 个股级：热度快照（东财/金十）+ 该股票新闻情感（自建或平台原生）
    watch_codes = {w.split('.')[0] for w in watchlist}
    per_symbol_news = {}
    for it in news:
        sym = ad.norm_symbol(it.get('symbol'))
        if sym and sym not in watch_codes:
            # 平台返回的代码不在 watchlist（或只是序号/流水号列）→ 回落标题匹配
            sym = next((c for c in sorted(watch_codes) if c in str(it.get('title') or '')), '')
        if sym:
            it['symbol'] = sym                      # 回写规范代码，下游统一按 6 位码展示
            per_symbol_news.setdefault(sym, []).append(it)
    syms = list(dict.fromkeys(list(heat_snap) + list(per_symbol_news) +
                              [w.split('.')[0] for w in watchlist]))
    heats = [heat_snap.get(s, {}).get('heat') for s in syms]
    z = nlp.zscore([h if isinstance(h, (int, float)) else None for h in heats])
    stocks = []
    for s, zz in zip(syms, z):
        items = per_symbol_news.get(s, [])
        agg = nlp.aggregate(items) if items else nlp.aggregate([])
        stocks.append({
            'symbol': s,
            'name': heat_snap.get(s, {}).get('name') or reg.WATCHLIST_NAMES.get(s, ''),
            'native': sum(1 for x in items if x.get('sentiment') is not None),
            'heat': heat_snap.get(s, {}).get('heat'),
            'heat_z': round(zz, 3),
            'news_count': agg['news_count'],
            'net_senti': agg['net_senti'],
            'neg_share': agg['neg_share'],
            'risk_score': agg['risk_score'],
            'as_of': heat_snap.get(s, {}).get('date') or _now().strftime('%Y-%m-%d'),
        })
    stocks.sort(key=lambda x: (-(x['heat'] or 0), -x['news_count']))

    # 日频情绪指数序列（数库/优矿）→ 直接可画的因子曲线
    # 只收"情绪指数"口径的序列；关注度/热度类序列（东财关注指数、金十微博人气、聚宽热度）
    # 走个股热度表展示，避免把 98712 这种量级值和 1.03 这种基期指数混进同一条曲线
    idx_series = []
    for row in series:
        extra = row.get('extra') or {}
        kind = extra.get('kind')
        if extra.get('factor') or kind not in (None, 'sentimentIndex') or row.get('value') is None:
            continue
        sid = row.get('source', '')
        idx_series.append({
            'date': row['date'], 'value': round(float(row['value']), 4),
            'name': '新闻情感指数' if kind == 'sentimentIndex' else '市场情绪指数',
            'source': sid,
            'platform': (reg.get_source(sid) or {}).get('platform', '').split('（')[0],
            'note': INDEX_CALIBRE.get(sid, '基期 = 1.0，>1 偏乐观'),
        })
    idx_series.sort(key=lambda r: (r['date'], r['name']))

    # 采集 → 匹配：把新闻舆情按关键词对齐到日报标的与主题（02 节行情标的同一套 key），
    # 输出层不含任何来源信息（平台名/接口 ID 只留在 sources[] 内部字段里）
    matches = smatch.build_matches(news, series=idx_series,
                                   keywords_used=smatch.search_keywords())

    ok = [r for r in results if r['ok']]
    failed = [r['id'] for r in results if not r['ok']]
    factors = {}
    for key, meta in reg.FACTOR_LIBRARY.items():
        val = {'SENT_TEMP': market['sent_temp'], 'NET_SENTI': market['net_senti'],
               'NEWS_HEAT_Z': market['heat_z'], 'NEG_SHARE': round(market['neg_share'] * 100, 2),
               'EVENT_RISK': market['risk_score']}.get(key)
        factors[key] = {'name': meta['name'], 'unit': meta['unit'], 'value': val,
                        'definition': meta['definition'], 'best_source': meta['best_source']}

    out = {
        'generated_at': _now().strftime('%Y-%m-%d %H:%M:%S UTC'),
        'fetch_date': _now().strftime('%Y-%m-%d'),
        'mode': mode,
        'mode_note': {'live': '联网实测：优先平台现成因子，缺失时以免费源 + 自建词库补齐',
                      'mock': '离线回放：使用 tests/fixtures 录制报文，未产生网络请求（演示/CI 冒烟）',
                      'offline': '断网兜底：沿用上次 sentiment_data.json，仅刷新时间戳'}.get(mode, ''),
        'market': {
            'sent_temp': market['sent_temp'],
            'label': nlp.label(market['sent_temp']),
            'net_senti': market['net_senti'],
            'neg_share': round(market['neg_share'] * 100, 2),
            'pos_share': round(market['pos_share'] * 100, 2),
            'news_count': market['news_count'],
            'heat_z': market['heat_z'],
            'risk_score': market['risk_score'],
            'platform_native': market.get('platform_native', 0),
            'self_built': max(0, market['news_count'] - market.get('platform_native', 0)),
            'top_negative': market['top_negative'],
            'top_positive': market['top_positive'],
            'events': market['events'],
            'degraded': bool(market.get('degraded')),
        },
        'factors': factors,
        'series': idx_series[-30:],
        'stocks': stocks[:10],
        'matches': matches,
        'watchlist': watchlist,
        'sources': results,
        'quota_notes': quota_notes,
        'summary': {'total': len(results), 'ok': len(ok), 'failed': failed,
                    'usable_news': len(news), 'usable_series': len(series),
                    'ready_platforms': sorted({r['platform'] for r in ok})},
    }
    # 若已跑过接入实测（tools/probe_sentiment_apis.py），把结论矩阵一并带进日报
    probe = load_json(PROBE_REPORT, {})
    if probe.get('matrix'):
        pm = probe.get('meta') or {}
        out['api_eval'] = {'generated_at': pm.get('generated_at') or probe.get('generated_at'),
                           'mode': pm.get('mode') or probe.get('mode'),
                           'note': pm.get('note', ''),
                           'matrix': probe['matrix'], 'ranking': probe.get('ranking'),
                           'conclusion': probe.get('conclusion')}
    return out


def refresh_timestamps(prev, mode='offline'):
    """--offline：保留上次因子数值，仅刷新时间与模式标注，并记录降级来源。"""
    if not prev:
        return None
    out = dict(prev)
    out['generated_at'] = _now().strftime('%Y-%m-%d %H:%M:%S UTC')
    out['fetch_date'] = _now().strftime('%Y-%m-%d')
    out['mode'] = mode
    out['mode_note'] = '断网兜底：沿用上次抓取结果（因子数值未刷新），仅刷新时间戳'
    out.setdefault('market', {})['degraded'] = True
    src = prev.get('summary') or {}
    out['summary'] = dict(src)
    out['summary']['offline_from'] = prev.get('generated_at') or '未知'
    return out


def run(mode='live', only=None, watchlist=None, timeout=ad.DEFAULT_TIMEOUT,
        out_path=DEFAULT_OUT, verbose=True):
    if verbose:
        label = {'live': '联网实测', 'mock': '离线回放（fixtures）',
                 'off': '断网兜底（沿用上次结果）', 'offline': '断网兜底'}.get(mode, mode)
        print(f'🗞️  舆情/新闻因子接入 — {label}')
    prev = load_json(out_path, {})
    if mode == 'off':
        data = refresh_timestamps(prev) or build_factors([], [], [], {}, {}, 'off', watchlist or WATCHLIST_DEFAULT)
    else:
        try:
            results, news, series, heat, quota = collect(mode, only, watchlist, timeout, verbose)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001 — 管线任何意外都不能阻断构建
            print(f'⚠️ 取数管线异常，降级为上次结果: {type(e).__name__}: {e}', file=sys.stderr)
            results, news, series, heat, quota = [], [], [], {}, {}
        data = build_factors(results, news, series, heat, quota, mode, watchlist or WATCHLIST_DEFAULT)
        if not data['sources'] and prev:
            data = refresh_timestamps(prev, mode='offline') or data

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if verbose:
        m = data.get('market') or {}
        s = data.get('summary') or {}
        print(f"\n📊 舆情因子: 温度计 {m.get('sent_temp')}（{m.get('label')}）"
              f" · 净情感 {m.get('net_senti'):+.3f} · 负面占比 {m.get('neg_share')}%"
              f" · 新闻 {m.get('news_count')} 条 · 风险分 {m.get('risk_score')}")
        print(f"   数据源: {s.get('ok')}/{s.get('total')} 可用"
              + (f" · 失败: {'、'.join(s.get('failed') or [])}" if s.get('failed') else ''))
        print(f"   平台现成情感条数: {m.get('platform_native')} · 自建词库打分: {m.get('self_built')}")
        mm = data.get('matches') or {}
        if mm.get('total_news'):
            tops = '、'.join(f"{t['name']}({t['hits']})" for t in (mm.get('targets') or [])[:6])
            print(f"   标的匹配: {mm.get('matched_news')}/{mm.get('total_news')} 条命中日报标的"
                  f"（匹配率 {(mm.get('coverage') or 0) * 100:.1f}%）· {tops or '无命中'}"
                  f" · 对外不显示数据来源")
        print(f"   已写入 {os.path.relpath(out_path, REPO_ROOT)}")
    return data


def main():
    ap = argparse.ArgumentParser(description='章鱼 AI·舆情004 — 量化平台舆情/新闻因子接入管线')
    ap.add_argument('--live', action='store_true', help='联网实测（默认；需凭据 + 境内出口）')
    ap.add_argument('--mock', '--demo', dest='mock', action='store_true',
                    help='用 tests/fixtures 录制报文回放（离线演示 / CI 冒烟）')
    ap.add_argument('--offline', action='store_true', help='断网兜底：仅刷新上次结果的时间戳')
    ap.add_argument('--sources', default='', help='逗号分隔，仅测指定源（如 RQ_SDK,UQER_HTTP）')
    ap.add_argument('--watchlist', default=','.join(WATCHLIST_DEFAULT), help='个股舆情样本代码')
    ap.add_argument('--json', dest='out', default=DEFAULT_OUT, help='输出 JSON 路径')
    ap.add_argument('--timeout', type=float, default=ad.DEFAULT_TIMEOUT, help='单源超时（秒）')
    ap.add_argument('--quiet', action='store_true', help='安静模式')
    args = ap.parse_args()

    mode = 'off' if args.offline else ('mock' if args.mock else 'live')
    data = run(mode=mode, only=[x for x in args.sources.split(',') if x.strip()],
               watchlist=[x for x in args.watchlist.split(',') if x.strip()],
               timeout=args.timeout, out_path=args.out, verbose=not args.quiet)
    ok = (data.get('summary') or {}).get('ok') or 0
    # 永不因个别源失败而退出非零；只有在完全没有任何数据且无历史可兜底时才告警
    if not ok and not (data.get('market') or {}).get('news_count'):
        print('⚠️ 舆情因子本次无有效数据，页面与推送将显示降级说明（构建继续）', file=sys.stderr)


if __name__ == '__main__':
    main()
