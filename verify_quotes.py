#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 推送前全来源数据准确性校验 (verify_quotes.py)

在微信推送 (tools/wechat_push.py --push) 之前，对 market_data.json 中
全部行情标的做多来源交叉校验，只有数据可信才允许推送：

  校验来源（每标的尽量覆盖多个独立源）:
    1. Yahoo chart API  — 主源官方口径复验 (meta.fulldayChangePercent 当日涨跌)
    2. Stooq 实时报价   — 独立源点位比对 (stooq.com/q/l/)
    3. Stooq 历史日线   — 独立源重算涨跌额/涨跌幅 (close[-1] vs close[-2])
    4. Frankfurter/ECB  — 美元兑在岸人民币的独立第三源 (USDCNY)

  校验项:
    • internal_math  涨跌额/涨跌幅与 last/prev_close 自洽 (四舍五入容差内)
    • as_of_sanity   行情日期不超前、不陈旧 (> VERIFY_MAX_AGE_DAYS 天判定 FAIL)
    • level          点位与各来源比对 (相对偏差容差 VERIFY_LEVEL_TOL_PCT)
    • pct            涨跌幅与来源比对 (绝对偏差容差 VERIFY_PCT_TOL_PP)

  判定规则:
    • FAIL: 主源官方口径互相矛盾；≥2 个独立来源族彼此一致但与流水线不一致；
            内部数学不自洽；行情日期超前/陈旧
    • WARN: 仅 1 个独立来源族不一致；数据缺失；独立来源不足；来源日期晚于流水线
            (盘后滚动到新交易日属正常，仅提示)
    • PASS: 其余情况。跨日期的来源比对从宽 (只比点位、WARN 封顶)，避免
            早上 09:00 推送时新交易日已开盘造成的误报。

  退出码: 0 = 通过 (PASS/WARN)；2 = 校验失败 (FAIL，阻断推送)；3 = 数据文件缺失/损坏

用法:
  python3 verify_quotes.py --data market_data.json --json verify_report.json --text
  python3 tools/wechat_push.py --push          # 内部自动先跑本模块预检，FAIL 阻断
  python3 tools/wechat_push.py --push --skip-verify    # 跳过预检 (不推荐)

背景: 2026-09-16 事故 — Yahoo range=5d 的 chartPreviousClose 被误当昨收，
恒指当日 +0.19% 被算成 −2.22% 并推送。本模块保证同类错误在推送前被拦截。
"""
import argparse
import json
import os
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from market_data import YAHOO_BASE, http_get, SYMBOLS   # noqa: E402  复用 UA/超时/标的清单

STOOQ_L_BASE = 'https://stooq.com/q/l/?s={sym}&f=sdc&h&e=csv'
STOOQ_HIST_BASE = 'https://stooq.com/q/d/l/?s={sym}&d1={d1}&d2={d2}&i=d'
FRANKFURTER_BASE = 'https://api.frankfurter.app/latest?from=USD&to=CNY'

# 容差 (可用环境变量覆盖)
LEVEL_TOL_PCT = float(os.environ.get('VERIFY_LEVEL_TOL_PCT', '0.5'))    # 点位相对偏差 %
PCT_TOL_PP = float(os.environ.get('VERIFY_PCT_TOL_PP', '0.35'))         # 涨跌幅绝对偏差 (百分点)
MAX_AGE_DAYS = int(os.environ.get('VERIFY_MAX_AGE_DAYS', '5'))          # 行情日期最大陈旧天数

VERDICT_ORDER = {'pass': 0, 'warn': 1, 'fail': 2}


def _iso(d):
    """'YYYY-MM-DD' → datetime.date；解析失败返回 None。"""
    if not d:
        return None
    try:
        return datetime.strptime(str(d)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _sym_of(key):
    """标的 key → (yahoo_sym, stooq_sym, 中文名)。"""
    for k, name, _unit, ysym, ssym, _dec in SYMBOLS:
        if k == key:
            return ysym, ssym, name
    return None, None, key


# ---------------------------------------------------------------------------
# 探针: 每个函数返回 {'source','family','last','pct','as_of'} 或 None
# family 用于独立来源族聚合 (yahoo / stooq / ecb)
# ---------------------------------------------------------------------------

def probe_yahoo(yahoo_sym, timeout=8):
    """主源官方口径复验: regularMarketPrice + fulldayChangePercent(官方当日涨跌)。"""
    if not yahoo_sym:
        return None
    url = YAHOO_BASE.format(sym=urllib.parse.quote(yahoo_sym, safe=''))
    meta = ((json.loads(http_get(url, timeout=timeout)).get('chart') or {})
            .get('result') or [{}])[0].get('meta') or {}
    last = meta.get('regularMarketPrice')
    if last is None:
        return None
    ts = meta.get('regularMarketTime')
    as_of = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d') if ts else None
    pct = meta.get('fulldayChangePercent')
    if pct is None:
        pct = meta.get('regularMarketChangePercent')
    return {'source': 'yahoo', 'family': 'yahoo', 'last': float(last),
            'pct': float(pct) if pct is not None else None, 'as_of': as_of}


def probe_stooq_current(stooq_sym, timeout=8):
    """独立源实时报价: stooq.com/q/l/ → (Symbol, Date, Close)。"""
    if not stooq_sym:
        return None
    url = STOOQ_L_BASE.format(sym=urllib.parse.quote(stooq_sym, safe=''))
    raw = http_get(url, timeout=timeout)
    rows = [ln.strip().split(',') for ln in raw.strip().splitlines() if ln.strip()]
    for row in rows:
        if row and row[0].lower() == 'symbol':
            continue
        if len(row) >= 3:
            try:
                last = float(row[2])
            except ValueError:
                return None
            return {'source': 'stooq', 'family': 'stooq', 'last': last,
                    'pct': None, 'as_of': row[1] if _iso(row[1]) else None}
    return None


def probe_stooq_hist(stooq_sym, timeout=8, fetch_date=None):
    """独立源历史日线重算: close[-1] 与 close[-2] → 当日涨跌额/涨跌幅。"""
    if not stooq_sym:
        return None
    ref = _iso(fetch_date) or datetime.now(timezone.utc).date()
    d1 = (ref - timedelta(days=14)).strftime('%Y%m%d')
    d2 = (ref + timedelta(days=1)).strftime('%Y%m%d')
    url = STOOQ_HIST_BASE.format(sym=urllib.parse.quote(stooq_sym, safe=''), d1=d1, d2=d2)
    raw = http_get(url, timeout=timeout)
    series = []
    for ln in raw.strip().splitlines():
        row = ln.strip().split(',')
        if len(row) < 5 or row[0].lower() == 'date':
            continue
        d = _iso(row[0])
        if not d:
            continue
        try:
            series.append((d, float(row[4])))
        except ValueError:
            continue
    if not series:
        return None
    series.sort(key=lambda x: x[0])
    last_d, last = series[-1]
    prev = series[-2][1] if len(series) >= 2 else None
    pct = round((last - prev) / prev * 100, 2) if (prev not in (None, 0)) else None
    return {'source': 'stooq_hist', 'family': 'stooq', 'last': last,
            'pct': pct, 'as_of': last_d.strftime('%Y-%m-%d')}


def probe_frankfurter(timeout=8):
    """独立第三源 (ECB 参考汇率): 仅 USDCNY。日期通常为 T-1，只参与点位比对。"""
    d = json.loads(http_get(FRANKFURTER_BASE, timeout=timeout))
    rate = (d.get('rates') or {}).get('CNY')
    if rate is None:
        return None
    return {'source': 'frankfurter', 'family': 'ecb', 'last': float(rate),
            'pct': None, 'as_of': d.get('date')}


def default_probes(key, fetch_date=None):
    """按标的组装默认探针列表 (惰性调用, 每个为 () -> dict|None)。"""
    ysym, ssym, _name = _sym_of(key)
    probes = []
    if ysym:
        probes.append(('yahoo', lambda: probe_yahoo(ysym)))
    if ssym:
        probes.append(('stooq', lambda: probe_stooq_current(ssym)))
        probes.append(('stooq_hist', lambda: probe_stooq_hist(ssym, fetch_date=fetch_date)))
    if key == 'USDCNY':
        probes.append(('frankfurter', lambda: probe_frankfurter()))
    return probes


# ---------------------------------------------------------------------------
# 校验核心
# ---------------------------------------------------------------------------

def _ck(name, severity, detail):
    return {'name': name, 'severity': severity, 'detail': detail}


def verify_item(key, quote, probe_fns, fetch_date, level_tol=None, pct_tol=None):
    """校验单个标的 → item dict (verdict: pass/warn/fail)。probe_fns 可为空列表(离线)。"""
    level_tol = LEVEL_TOL_PCT if level_tol is None else level_tol
    pct_tol = PCT_TOL_PP if pct_tol is None else pct_tol
    _ys, _ss, name = _sym_of(key)
    quote = quote or {}
    checks, sources = [], []

    last = quote.get('last')
    if last is None:
        checks.append(_ck('present', 'warn', '行情缺失 (构建时已降级显示 —)'))
        return {'key': key, 'name': name, 'verdict': 'warn', 'checks': checks, 'sources': []}
    last = float(last)
    chg, pct = quote.get('chg'), quote.get('pct')
    as_of = quote.get('as_of')
    item = {'key': key, 'name': name, 'last': last, 'chg': chg, 'pct': pct,
            'as_of': as_of, 'checks': checks, 'sources': sources}

    # ① 内部自洽: chg/pct 与 last/prev_close 互相印证
    prev = quote.get('prev_close')
    if prev not in (None, 0):
        prev = float(prev)
        exp_chg = last - prev
        if chg is None or abs(exp_chg - float(chg)) > 0.011:
            checks.append(_ck('internal_math', 'fail',
                              f'涨跌额不自洽: last({last}) − prev_close({prev}) = {exp_chg:.4f}, 记录为 {chg}'))
        elif pct is not None:
            exp_pct = round(exp_chg / prev * 100, 2)
            if abs(exp_pct - float(pct)) > 0.011:
                checks.append(_ck('internal_math', 'fail',
                                  f'涨跌幅不自洽: 应为 {exp_pct}%, 记录为 {pct}%'))
        if not checks or checks[-1]['name'] != 'internal_math':
            checks.append(_ck('internal_math', 'pass', '涨跌额/涨跌幅与 last/prev_close 自洽'))
    else:
        checks.append(_ck('internal_math', 'info', '无 prev_close 字段, 跳过内部自洽校验 (建议重新生成 market_data.json)'))

    # ② 行情日期健全性: 不超前、不陈旧
    aq = _iso(as_of)
    fd = _iso(fetch_date)
    if aq and fd:
        if aq > fd:
            checks.append(_ck('as_of_sanity', 'fail', f'行情日期 {as_of} 超前于抓取日期 {fetch_date}'))
        elif (fd - aq).days > MAX_AGE_DAYS:
            checks.append(_ck('as_of_sanity', 'fail', f'行情日期 {as_of} 陈旧 (距抓取日期 {(fd - aq).days} 天 > {MAX_AGE_DAYS})'))
        else:
            checks.append(_ck('as_of_sanity', 'pass', f'行情日期 {as_of} 正常'))
    else:
        checks.append(_ck('as_of_sanity', 'warn', f'行情日期缺失或无法解析: {as_of}'))

    # ③ 多来源比对
    same_date, cross_date = [], []
    for entry in probe_fns:
        if isinstance(entry, tuple) and len(entry) == 2:
            pname, fn = entry
        else:                                   # 容忍裸函数 (测试注入便捷写法)
            fn = entry
            pname = getattr(fn, '__name__', 'probe')
        try:
            p = fn()
        except Exception as e:  # noqa: BLE001 — 单源失败不阻断
            sources.append({'source': pname, 'reachable': False, 'error': str(e)[:120]})
            continue
        if not p or p.get('last') is None:
            sources.append({'source': pname, 'reachable': False, 'error': 'no_data'})
            continue
        pl, pp, pa = float(p['last']), p.get('pct'), p.get('as_of')
        diff = abs(pl - last) / last * 100 if last else None
        same = (pa is None) or (aq is None) or (pa == as_of)
        rec = {'source': p.get('source', pname), 'family': p.get('family', pname),
               'reachable': True, 'last': pl, 'pct': pp, 'as_of': pa,
               'level_diff_pct': round(diff, 3) if diff is not None else None,
               'same_date': same}
        # 涨跌幅比对 (仅同日期且来源提供 pct 时)
        if pp is not None and pct is not None:
            rec['pct_diff_pp'] = round(abs(pp - float(pct)), 3)
            rec['agree_pct'] = rec['pct_diff_pp'] <= pct_tol
        # 点位比对
        rec['agree_level'] = (rec['level_diff_pct'] is not None and rec['level_diff_pct'] <= level_tol)
        if same:
            rec['strict'] = True
            same_date.append(rec)
        else:
            rec['strict'] = False
            cross_date.append(rec)
        sources.append(rec)

    if not any(s.get('reachable') for s in sources):
        checks.append(_ck('sources', 'warn', '所有校验来源均不可达, 无法交叉确认 (insufficient_sources)'))
    else:
        # 同日期来源: 严格模式 — 来源族彼此一致但与流水线矛盾 → FAIL
        dis_fams = sorted({r['family'] for r in same_date
                           if r.get('agree_level') is False or r.get('agree_pct') is False})
        if dis_fams:
            if 'yahoo' in dis_fams:
                det = [r for r in same_date if r['family'] == 'yahoo'][0]
                checks.append(_ck('sources', 'fail',
                                  f'与主源官方口径矛盾 (yahoo: last={det["last"]}, pct={det.get("pct")})'))
            elif len(dis_fams) >= 2:
                checks.append(_ck('sources', 'fail',
                                  f'{len(dis_fams)} 个独立来源族 ({", ".join(dis_fams)}) 彼此一致但与流水线矛盾'))
            else:
                checks.append(_ck('sources', 'warn', f'独立来源族 {dis_fams[0]} 与流水线不一致 (单源, 待复核)'))
        else:
            ok_n = len([r for r in same_date if r.get('agree_level')])
            checks.append(_ck('sources', 'pass',
                              f'{ok_n} 个同日期来源比对一致' if ok_n else '来源可达但无同日期数据可比'))
        # 跨日期来源: 从宽 — 只比点位, WARN 封顶 (避免新交易日开盘误报)
        for r in cross_date:
            if r.get('agree_level') is False:
                checks.append(_ck('sources', 'warn',
                                  f'跨日期来源 {r["source"]}({r["as_of"]}) 点位偏差 {r["level_diff_pct"]}% (从宽不阻断)'))
            elif r['as_of'] and aq and _iso(r['as_of']) > aq:
                checks.append(_ck('sources', 'info',
                                  f'来源 {r["source"]} 已更新至 {r["as_of"]} (流水线 {as_of}, 新交易日滚动属正常)'))

    verdict = 'pass'
    for c in checks:
        v = c['severity']
        if v == 'fail' or VERDICT_ORDER.get(v, 0) > VERDICT_ORDER[verdict]:
            verdict = v if v in VERDICT_ORDER else verdict
    item['verdict'] = verdict
    return item


def verify_data(data, probes_by_key=None, fetch_date=None, timeout=8,
                level_tol=None, pct_tol=None):
    """校验 market_data dict → 完整报告。probes_by_key 供测试注入假探针。"""
    quotes = (data or {}).get('quotes') or {}
    fetch_date = fetch_date or (data or {}).get('fetch_date') \
        or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    items = []
    for key, _name, _unit, _ys, _ss, _dec in SYMBOLS:
        fns = []
        if probes_by_key is not None:
            fns = probes_by_key.get(key, [])
        else:
            fns = default_probes(key, fetch_date)
        items.append(verify_item(key, quotes.get(key), fns, fetch_date,
                                 level_tol=level_tol, pct_tol=pct_tol))
    counts = {'pass': 0, 'warn': 0, 'fail': 0}
    for it in items:
        counts[it['verdict']] += 1
    overall = 'fail' if counts['fail'] else ('warn' if counts['warn'] else 'pass')
    return {
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
        'fetch_date': fetch_date,
        'tolerances': {'level_pct': level_tol or LEVEL_TOL_PCT,
                       'pct_pp': pct_tol or PCT_TOL_PP,
                       'max_age_days': MAX_AGE_DAYS},
        'overall': overall,
        'summary': counts,
        'items': items,
    }


# ---------------------------------------------------------------------------
# 输出 / CLI / 预检入口
# ---------------------------------------------------------------------------

ICON = {'pass': '✅', 'warn': '⚠️', 'fail': '❌'}


def report_text(report):
    """报告 → 人读文本 (用于 CI 日志与 GITHUB_STEP_SUMMARY)。"""
    lines = [
        f"🧪 推送前全来源数据校验 — fetch_date={report['fetch_date']} · "
        f"容差: 点位 {report['tolerances']['level_pct']}% / 涨跌幅 {report['tolerances']['pct_pp']}pp",
    ]
    for it in report['items']:
        last = it.get('last')
        seg = (f"{ICON[it['verdict']]} {it['key']:<7} {it.get('name') or '':<8} ")
        seg += (f"{last:,.2f}" if last is not None else "—")
        if it.get('pct') is not None:
            seg += f" ({it['pct']:+.2f}%)"
        ok_src = [s for s in it.get('sources', []) if s.get('reachable')]
        seg += f" — {it['verdict'].upper()}"
        if ok_src:
            agree = len([s for s in ok_src if s.get('agree_level')])
            seg += f" · 来源 {agree}/{len(ok_src)} 一致"
        elif it.get('sources'):
            seg += " · 来源不可达"
        bad = [c for c in it['checks'] if c['severity'] in ('fail', 'warn')]
        if bad:
            seg += " · " + "; ".join(c['detail'] for c in bad)
        lines.append('  ' + seg)
    s = report['summary']
    overall = report['overall']
    tail = '允许推送' if overall != 'fail' else '已阻断推送 (FAIL)'
    lines.append(f"总体: {overall.upper()} — pass {s['pass']} / warn {s['warn']} / fail {s['fail']} · {tail}")
    return '\n'.join(lines)


def run_preflight(data_path=None, timeout=8, strict=False, report_path=None):
    """微信推送预检入口: 返回 True=允许推送 / False=阻断。失败细节打印到 stderr。"""
    data_path = data_path or os.environ.get(
        'MARKET_DATA', os.path.join(REPO_ROOT, 'market_data.json'))
    if not os.path.exists(data_path):
        print(f'❌ 数据校验预检: {data_path} 不存在, 无法验证数据准确性 → 阻断推送', file=sys.stderr)
        return False
    try:
        with open(data_path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f'❌ 数据校验预检: {data_path} 读取失败: {e} → 阻断推送', file=sys.stderr)
        return False
    mode = (data or {}).get('mode')
    if mode in ('demo', 'offline'):
        print(f'⚠️ 数据校验预检: market_data.json 为 {mode} 模式 (非实时抓取), 仅做离线自检',
              file=sys.stderr)
    report = verify_data(data, timeout=timeout)
    if report_path:
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f'⚠️ 校验报告写入失败: {e}', file=sys.stderr)
    print(report_text(report))
    blocking = report['overall'] == 'fail' or (strict and report['overall'] == 'warn')
    if blocking:
        print('🚫 数据准确性校验未通过 → 阻断推送 (详情见上方 ❌/⚠️ 条目)', file=sys.stderr)
    else:
        print('✅ 数据准确性校验通过 → 允许推送')
    return not blocking


def main():
    ap = argparse.ArgumentParser(description='章鱼 AI·舆情004 — 推送前全来源数据准确性校验')
    ap.add_argument('--data', default=os.path.join(REPO_ROOT, 'market_data.json'),
                    help='待校验的 market_data.json 路径')
    ap.add_argument('--json', dest='report_json', default=None,
                    help='校验报告 JSON 输出路径 (如 verify_report.json)')
    ap.add_argument('--text', action='store_true', help='打印人读文本报告')
    ap.add_argument('--timeout', type=int, default=8, help='单探针超时秒数')
    ap.add_argument('--strict', action='store_true', help='WARN 也视为不通过')
    args = ap.parse_args()

    try:
        with open(args.data, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f'❌ 数据文件缺失或损坏: {args.data} ({e})', file=sys.stderr)
        sys.exit(3)

    report = verify_data(data, timeout=args.timeout)
    if args.report_json:
        with open(args.report_json, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    if args.text:
        print(report_text(report))
    s = report['summary']
    print(f"🧪 校验完成: {report['overall'].upper()} (pass {s['pass']} / warn {s['warn']} / fail {s['fail']})")
    sys.exit(2 if (report['overall'] == 'fail' or (args.strict and report['overall'] == 'warn')) else 0)


if __name__ == '__main__':
    main()
