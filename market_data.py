#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 动态行情抓取 (market_data.py)

每次构建/推送前自动抓取最新行情，生成 market_data.json，
供 build_site.py 与 tools/wechat_push.py 动态注入，实现「动态抓取真正上线」：

  • 港股: 恒生指数 / 恒生科技指数 / 恒生中国企业指数
  • 美股: 标普 500 / 纳斯达克 / 道琼斯
  • 波动率: Cboe VIX 恐慌指数（标普 500 未来 30 天隐含波动率年化报价）
  • 商品: 现货黄金 / WTI 原油 / 布伦特原油
  • 汇率: 美元兑离岸人民币 / 在岸人民币

抓取源（按优先级逐源回退）：
  1. Yahoo Finance chart API  (query1.finance.yahoo.com) — 指数/期货/汇率全覆盖
  2. Stooq CSV 行情            (stooq.com)               — 无需密钥，稳定

设计原则：
  • 单品失败不阻断 —— 失败项 last=null，页面与推送降级显示 "—"，并在 summary 中记录，
    保证每天 09:00 定时任务与推送永不因个别源故障而中断。
  • 纯标准库（urllib），GitHub Actions ubuntu-latest 开箱即用，无需 pip install。

用法:
  python3 market_data.py                      # 联网抓取 → market_data.json
  python3 market_data.py --demo               # 写入一组模拟行情（本地联调/演示）
  python3 market_data.py --offline            # 断网兜底：基于旧数据刷新时间戳
  python3 market_data.py --json out.json --timeout 10
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ---------------------------------------------------------------------------
# 行情清单: (key, 中文名, 单位, Yahoo 代码, Stooq 代码, 展示小数位)
# ---------------------------------------------------------------------------
SYMBOLS = [
    ('VIX',    'VIX 恐慌指数',     '点',        '^VIX',   '^vix',   2),
    ('HSI',    '恒生指数',         '点',        '^HSI',   '^hsi',   2),
    ('HSTECH', '恒生科技指数',     '点',        '^HSTECH', None,    2),
    ('HSCE',   '恒生中国企业指数', '点',        '^HSCE',  '^hsc',   2),
    ('SPX',    '标普 500',         '点',        '^GSPC',  '^spx',   2),
    ('NDQ',    '纳斯达克',         '点',        '^IXIC',  '^ndq',   2),
    ('DJI',    '道琼斯',           '点',        '^DJI',   '^dji',   2),
    ('GOLD',   '现货黄金',         '美元/盎司', 'GC=F',   'xauusd', 2),
    ('WTI',    'WTI 原油',         '美元/桶',   'CL=F',   'cl.f',   2),
    ('BRENT',  '布伦特原油',       '美元/桶',   'BZ=F',   'br.f',   2),
    ('USDCNH', '美元/离岸人民币',  '',          'CNH=X',  'usdcnh', 4),
    ('USDCNY', '美元/在岸人民币',  '',          'CNY=X',  'usdcny', 4),
]

YAHOO_BASE = 'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d'
STOOQ_BASE = 'https://stooq.com/q/l/?s={sym}&f=sdc&h&e=csv'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

# 演示数据: key -> (last, chg, pct, as_of) —— 与当前报告风格一致，便于本地联调
DEMO = {
    'VIX':    (21.40,      1.25,   6.20, '2026-08-28'),
    'HSI':    (25440.17, -212.65, -0.83, '2026-08-28'),
    'HSTECH': (4776.44,  -47.77,  -0.99, '2026-08-28'),
    'HSCE':   (9012.34,  -60.11,  -0.66, '2026-08-28'),
    'SPX':    (6120.45,   15.20,   0.25, '2026-08-28'),
    'NDQ':    (19980.12,  60.30,   0.30, '2026-08-28'),
    'DJI':    (41500.88, -120.44, -0.29, '2026-08-28'),
    'GOLD':   (4400.50,   25.10,   0.57, '2026-08-28'),
    'WTI':    (82.70,      0.90,   1.10, '2026-08-28'),
    'BRENT':  (89.05,      0.85,   0.96, '2026-08-28'),
    'USDCNH': (7.1820,    -0.0050, -0.07, '2026-08-28'),
    'USDCNY': (7.1750,    -0.0040, -0.06, '2026-08-28'),
}


def http_get(url, timeout=10):
    """GET 请求，带浏览器 UA，返回文本；失败抛异常由调用方兜底。"""
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': '*/*',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')


def _finite_number(value):
    """Reject malformed, non-finite and boolean numbers from market feeds."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def yahoo_regular_pct(meta, last):
    """Percentage paired with regularMarketPrice; full-day may be after-hours."""
    pct = _finite_number(meta.get('regularMarketChangePercent'))
    if pct is not None and pct > -100:
        return pct
    full_price = _finite_number(meta.get('fulldayPrice'))
    full_pct = _finite_number(meta.get('fulldayChangePercent'))
    if (full_price is not None and last is not None and
            math.isclose(full_price, last, rel_tol=1e-6, abs_tol=1e-8) and
            full_pct is not None and full_pct > -100):
        return full_pct
    return None


def _market_timezone(meta):
    name = meta.get('exchangeTimezoneName')
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            pass
    offset = _finite_number(meta.get('gmtoffset'))
    if offset is not None and abs(offset) < 86400:
        return timezone(timedelta(seconds=offset))
    return timezone.utc


def _daily_previous_close(result, meta):
    """Find the most recent daily close before the quote's exchange-local day."""
    stamps = result.get('timestamp') or []
    closes = (((result.get('indicators') or {}).get('quote') or [{}])[0].get('close')) or []
    market_ts = _finite_number(meta.get('regularMarketTime'))
    if not stamps or len(stamps) != len(closes) or market_ts is None:
        return None
    tz = _market_timezone(meta)
    try:
        day = datetime.fromtimestamp(market_ts, tz).date()
    except (ValueError, OverflowError, OSError):
        return None
    candidates = []
    for stamp, close in zip(stamps, closes):
        stamp, close = _finite_number(stamp), _finite_number(close)
        if stamp is None or close is None or close <= 0:
            continue
        try:
            bar_day = datetime.fromtimestamp(stamp, tz).date()
        except (ValueError, OverflowError, OSError):
            continue
        if bar_day < day and (day - bar_day).days <= 10:
            candidates.append((stamp, close))
    return max(candidates, default=(None, None))[1]


def parse_yahoo_chart(payload):
    """Parse regular-market price and a defensible previous-session close.

    A full-day change may refer to a different price than regularMarketPrice.
    Check candidates against the paired regular-market percentage when present;
    otherwise use a dated daily candle. Never treat a 5-day window's
    chartPreviousClose as yesterday's close. If the reference is ambiguous,
    keep the price but return prev_close=None rather than inventing a change.
    """
    try:
        result = ((payload or {}).get('chart') or {}).get('result')
    except AttributeError:
        return None
    if not result:
        return None
    row = result[0]
    meta = row.get('meta') or {}
    last = _finite_number(meta.get('regularMarketPrice'))
    if last is None:
        return None

    ts = _finite_number(meta.get('regularMarketTime'))
    if ts is None:
        stamps = row.get('timestamp') or []
        ts = _finite_number(stamps[-1]) if stamps else None
    try:
        as_of = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d') if ts else None
    except (ValueError, OverflowError, OSError):
        as_of = None

    pct = yahoo_regular_pct(meta, last)
    regular_change = _finite_number(meta.get('regularMarketChange'))
    full_change = _finite_number(meta.get('fulldayChange'))
    full_price = _finite_number(meta.get('fulldayPrice'))
    regular_prev = last - regular_change if regular_change is not None else None
    full_prev = (last - full_change if full_change is not None and
                 (full_price is None or math.isclose(full_price, last, rel_tol=1e-6, abs_tol=1e-8))
                 else None)
    candle_prev = _daily_previous_close(row, meta)
    candidates = [v for v in (regular_prev, full_prev, candle_prev) if v is not None and v > 0]
    prev = None
    if last > 0 and pct is not None:
        # Preserve precise price changes/candles only when they agree with the
        # regular-market percentage, which is rounded to several decimals.
        prev = next((v for v in candidates if abs((last - v) / v * 100 - pct) <= 0.02), None)
        if prev is None:
            prev = last / (1 + pct / 100)
    elif last > 0:
        # A same-price fullday change is usable without a percentage; without
        # fulldayPrice it must be corroborated instead of assumed to match.
        usable = [regular_prev, full_prev if full_price is not None else None, candle_prev]
        prev = next((v for v in usable if v is not None and v > 0), None)
    return (last, prev, as_of)


def fetch_yahoo(yahoo_sym, timeout=10):
    """Yahoo Finance chart API → (last, prev_close, as_of) 或 None。"""
    if not yahoo_sym:
        return None
    url = YAHOO_BASE.format(sym=urllib.parse.quote(yahoo_sym, safe=''))
    raw = http_get(url, timeout=timeout)
    data = json.loads(raw)
    return parse_yahoo_chart(data)


def fetch_stooq(stooq_sym, timeout=10):
    """Stooq CSV 行情 → (last, prev_close, as_of) 或 None（stooq 只有收盘价，prev=None）。"""
    if not stooq_sym:
        return None
    url = STOOQ_BASE.format(sym=urllib.parse.quote(stooq_sym, safe=''))
    raw = http_get(url, timeout=timeout)
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    if len(lines) < 2 or 'No data' in raw:
        return None
    # 首行可能是表头(Symbol,Date,Close)或直接数据；兼容两种
    row = None
    for ln in lines:
        if ln.lower().startswith('symbol'):
            continue
        row = ln.split(',')
        break
    if not row or len(row) < 3:
        return None
    try:
        last = float(row[2])
    except (ValueError, IndexError):
        return None
    as_of = row[1] if re.match(r'20\d{2}-\d{2}-\d{2}', row[1]) else None
    return (last, None, as_of)


def make_quote(key, name, unit, decimals, last, prev, as_of, source):
    """组装单条行情 dict；无 prev 时 chg/pct 为 None。"""
    chg = pct = None
    if last is not None and prev is not None and prev != 0:
        chg = round(last - prev, 4)
        pct = round((last - prev) / prev * 100, 2)
    return {
        'name': name,
        'unit': unit,
        'decimals': decimals,
        'last': last,
        'prev_close': prev,
        'chg': chg,
        'pct': pct,
        'as_of': as_of,
        'source': source,
    }


# 多源校正阈值: 主源与副源收盘价相对偏差超过该百分比(%)即判为不一致并告警
CROSS_CHECK_TOL_PCT = 0.3


def cross_check_stooq(quotes, timeout=10):
    """多源校正: 对 Yahoo 已成功的标的用 Stooq 收盘价做水平交叉校验（非回退）。

    与「Yahoo 失败才用 Stooq」的故障回退不同, 这里 Yahoo 成功也会再取 Stooq 收盘
    比对价格水平; 偏差 > CROSS_CHECK_TOL_PCT 时打告警并在 quote.cross_check 标记。
    Stooq 仅提供收盘价(无前收), 故只校验点位水平, 不校验涨跌幅。
    任何失败静默跳过, 不阻断构建。返回 (checked, agreed)。
    """
    tol = float(os.environ.get('CROSS_CHECK_TOL_PCT', str(CROSS_CHECK_TOL_PCT)))
    checked = agreed = 0
    for key, name, _unit, _ysym, stooq_sym, dec in SYMBOLS:
        q = quotes.get(key) or {}
        if q.get('source') != 'yahoo' or not stooq_sym or q.get('last') is None:
            continue
        try:
            r = fetch_stooq(stooq_sym, timeout)
        except Exception:  # noqa: BLE001 - 校验失败不影响主数据
            continue
        if not r or r[0] is None:
            continue
        s_last, _s_prev, s_asof = r
        s_last = float(s_last)
        rel = abs(s_last - float(q['last'])) / float(q['last']) * 100
        checked += 1
        agree = rel <= tol
        agreed += 1 if agree else 0
        q['cross_check'] = {
            'source2': 'stooq',
            'last2': round(s_last, dec),
            'as_of2': s_asof,
            'rel_diff_pct': round(rel, 3),
            'agree': agree,
        }
        if not agree:
            print(f'  ⚠️ 多源校正: {name} Yahoo {q["last"]:,.{dec}f} vs '
                  f'Stooq {s_last:,.{dec}f} 偏差 {rel:.2f}% (> {tol}%)', file=sys.stderr)
    return checked, agreed


def main():
    ap = argparse.ArgumentParser(description='章鱼 AI·舆情004 — 动态行情抓取')
    ap.add_argument('--json', default='market_data.json', help='输出 JSON 路径')
    ap.add_argument('--timeout', type=int, default=10, help='单次请求超时秒数')
    ap.add_argument('--demo', action='store_true', help='写入模拟行情（本地联调/演示）')
    ap.add_argument('--offline', action='store_true',
                    help='断网兜底：读取已有 JSON，仅刷新时间戳')
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    now_full = now.strftime('%Y-%m-%d %H:%M:%S UTC')
    fetch_date = now.strftime('%Y-%m-%d')

    if args.offline:
        # 基于旧数据刷新时间戳，页面仍可构建（行情显示旧值并标注 offline）
        try:
            with open(args.json, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {'quotes': {}}
        data.update({'generated_at': now_full, 'fetch_date': fetch_date, 'mode': 'offline'})
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'🌐 离线模式: 已基于旧数据刷新时间戳 → {args.json}')
        return

    quotes = {}
    failed = []
    ck_checked = ck_agreed = 0

    if args.demo:
        for key, name, unit, _y, _s, dec in SYMBOLS:
            if key in DEMO:
                last, chg, _pct, as_of = DEMO[key]
                quotes[key] = make_quote(key, name, unit, dec, last, round(last - chg, 4), as_of, 'demo')
            else:
                quotes[key] = make_quote(key, name, unit, dec, None, None, None, None)
        mode = 'demo'
    else:
        mode = 'live'
        for key, name, unit, yahoo_sym, stooq_sym, dec in SYMBOLS:
            q = None
            src = None
            try:
                r = fetch_yahoo(yahoo_sym, args.timeout)
                if r:
                    last, prev, as_of = r
                    q = make_quote(key, name, unit, dec, last, prev, as_of, 'yahoo')
                    src = 'yahoo'
            except Exception as e:  # noqa: BLE001 - 单品失败不阻断
                print(f'  ⚠️ {name}: Yahoo 失败({e}); 尝试 Stooq…', file=sys.stderr)
            if q is None:
                try:
                    r = fetch_stooq(stooq_sym, args.timeout)
                    if r:
                        last, _prev, as_of = r
                        q = make_quote(key, name, unit, dec, last, None, as_of, 'stooq')
                        src = 'stooq'
                except Exception as e:  # noqa: BLE001
                    print(f'  ⚠️ {name}: Stooq 失败({e}); 降级为 "—"', file=sys.stderr)
            if q is None:
                q = make_quote(key, name, unit, dec, None, None, None, None)
                failed.append(name)
            else:
                print(f'  ✅ {name: <8} {src: <6} '
                      f'{q["last"]:,.{q["decimals"]}f}  {q["pct"]}%  ({q["as_of"]})')
            quotes[key] = q
            time.sleep(0.2)

        # 多源校正: Yahoo 成功项再用 Stooq 收盘价交叉比对（失败不影响主数据）
        ck_checked = ck_agreed = 0
        try:
            ck_checked, ck_agreed = cross_check_stooq(quotes, args.timeout)
            if ck_checked:
                print(f'  🔁 多源校正: Stooq 交叉比对 {ck_checked} 项, 一致 {ck_agreed} 项')
        except Exception as e:  # noqa: BLE001
            print(f'  ⚠️ 多源校正异常(跳过): {e}', file=sys.stderr)

    data = {
        'generated_at': now_full,
        'fetch_date': fetch_date,
        'mode': mode,
        'quotes': quotes,
        'summary': {
            'ok': len(SYMBOLS) - len(failed),
            'total': len(SYMBOLS),
            'failed': failed,
            'cross_check': {'checked': ck_checked, 'agree': ck_agreed},
        },
        'notes': [
            '由 market_data.py 构建时自动抓取 (Yahoo Finance 主源 → Stooq 回退 + 交叉校正)',
            '涨跌基于与常规时段价格匹配的变动数据或前一交易日日线；前收不可靠时不显示涨跌，'
            '不使用 range=5d 的窗口前收 chartPreviousClose',
            '多源校正: Yahoo 成功项用 Stooq 收盘价水平比对, 偏差 > '
            f'{CROSS_CHECK_TOL_PCT}% 记入 cross_check.disagree 并告警',
            '单品抓取失败降级显示 "—"，不阻断构建与推送',
        ],
    }
    with open(args.json, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    failed_str = ", ".join(failed)
    print(f'📦 行情已写入 {args.json} '
          f'({data["summary"]["ok"]}/{data["summary"]["total"]} 成功'
          + (f', 失败: {failed_str}' if failed else '')
          + f' · 抓取日期 {fetch_date} · {now_full})')


if __name__ == '__main__':
    main()
