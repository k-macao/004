#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 量化平台舆情/新闻因子适配器 (sentiment_adapters.py)
==========================================================================

把 聚宽 / 米筐 / 掘金 / 优矿 / Tushare / 东财 / 金十 / 数库 的舆情·新闻接口，
统一封装成同一套「取数 → 归一化」调用，供 sentiment_factors.py（产出因子）与
tools/probe_sentiment_apis.py（接入实测）共同复用。

三种运行模式
  • live  真实调用远端接口（需要凭据 + 境内出口；境外 IP 会被聚宽等站点直接拒绝）
  • mock  读取 tests/fixtures/<ID>.json 录制报文，走与 live 完全相同的解析代码
          —— 用于本地/CI 离线验证解析与因子链路，不产生任何网络请求
  • off   直接返回 skipped（配合 --offline 断网兜底，仅刷新时间戳）

统一返回结构 Result（dict）:
  {
    'source': 'RQ_SDK', 'ok': bool, 'mode': 'live|mock|off',
    'stage': 'deps|net|auth|fetch|none',      # 失败停在哪一阶段
    'error': str|None,
    'news': [ {symbol,title,content,published_at,source,url,sentiment,relevance} ],
    'series': [ {date, value, extra} ],        # 市场级/日频指数序列
    'fields': [ 观测到的字段名 ],
    'latest_date': 'YYYY-MM-DD'|None,
    'latency_ms': float, 'latencies': [float],
    'quota': {'note': str, 'remaining': int|None},
    'meta': { 任意补充说明 },
  }

约束：纯标准库（urllib/json/gzip），GitHub Actions ubuntu-latest 开箱即用；
SDK 类源用 importlib 反射调用，未安装时只记 deps 失败，不抛异常、不阻断构建。
"""
import gzip
import io
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import sentiment_sources as reg

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DIR = os.environ.get('SENTIMENT_FIXTURES', os.path.join(REPO_ROOT, 'tests', 'fixtures'))

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
DEFAULT_TIMEOUT = 10


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _now():
    return datetime.now(timezone.utc)


def result(source_id, **kw):
    out = {'source': source_id, 'ok': False, 'mode': 'live', 'stage': 'none', 'error': None,
           'news': [], 'series': [], 'fields': [], 'latest_date': None,
           'latency_ms': 0.0, 'latencies': [], 'quota': {'note': '', 'remaining': None},
           'meta': {}}
    out.update(kw)
    return out


def _decompress(raw):
    """自动解 gzip/zlib 响应体（部分接口即便未声明也会返回压缩内容）。"""
    if raw[:2] == b'\x1f\x8b':
        try:
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except OSError:
            return raw
    if raw[:1] == b'\x78':
        try:
            import zlib
            return zlib.decompress(raw)
        except (OSError, zlib.error):
            return raw
    return raw


def http_request(url, method='GET', body=None, headers=None, timeout=DEFAULT_TIMEOUT,
                 json_body=None):
    """发一次请求，返回 (text, elapsed_ms)。body/json_body 二选一。"""
    hdrs = {'User-Agent': UA, 'Accept': '*/*', 'Accept-Encoding': 'gzip, deflate',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'}
    hdrs.update(headers or {})
    data = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode('utf-8')
        hdrs.setdefault('Content-Type', 'application/json')
    elif body is not None:
        data = body.encode('utf-8') if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    t0 = time.monotonic()
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = _decompress(resp.read())
    return raw.decode('utf-8', errors='replace'), (time.monotonic() - t0) * 1000.0


def tcp_check(host, port=443, timeout=6):
    """只做 DNS + TCP 连接探测，用于把"平台故障"与"本地出口受限"区分开。"""
    import socket
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.monotonic() - t0) * 1000, 1), None
    except Exception as e:  # noqa: BLE001 — 探针需要吞掉一切网络异常并归类
        return False, round((time.monotonic() - t0) * 1000, 1), f'{type(e).__name__}: {str(e)[:160]}'


def norm_symbol(raw):
    """把各平台五花八门的证券代码统一成 6 位数字码（A 股）。

    支持 601318 / 601318.SH / SH601318 / 1.601318（东财 secid）/ 601318.XSHG（聚宽）
    / 00700.HK（港股保留 5 位）。认不出来的（如文章流水号 20260915001）返回 ''，
    避免把非代码字段当股票代码串进个股舆情。
    """
    s = str(raw or '').strip().upper()
    if not s:
        return ''
    if '.' in s:                                     # 601318.SH / 1.601318 / 00700.HK
        seg = [p for p in s.split('.') if p]
        cand = [p for p in seg if p.isdigit() and len(p) in (5, 6)]
        s = cand[-1] if cand else ''
    elif s[:2] in ('SH', 'SZ', 'BJ', 'HK') and s[2:].isdigit():   # SH601318 / SZ600036
        s = s[2:]
    return s if s.isdigit() and len(s) in (5, 6) else ''


def _norm_news(symbol, title, content, published_at, source, url, sentiment=None,
               relevance=None, extra=None):
    item = {'symbol': norm_symbol(symbol), 'title': (title or '').strip(),
            'content': (content or '').strip(), 'published_at': published_at or '',
            'source': source or '', 'url': url or ''}
    if sentiment is not None:
        item['sentiment'] = sentiment
    if relevance is not None:
        item['relevance'] = relevance
    if extra:
        item['extra'] = extra
    return item


def _latest_date(res):
    dates = [s.get('date') for s in res['series'] if s.get('date')]
    dates += [n.get('published_at', '')[:10] for n in res['news'] if n.get('published_at')]
    dates = [d for d in dates if d and len(d) >= 10]
    return max(dates)[:10] if dates else None


def _finalize(res, latencies):
    res['latencies'] = [round(x, 1) for x in latencies] if latencies else []
    if latencies:
        res['latency_ms'] = round(sum(latencies) / len(latencies), 1)
    res['latest_date'] = _latest_date(res)
    res['fields'] = sorted({k for row in (res['news'] + res['series']) for k in row})
    res['ok'] = bool(res['news'] or res['series'])
    if res['ok'] and res['stage'] != 'fetch':
        res['stage'] = 'fetch'
    return res


def load_fixture(source_id):
    """读取录制报文：tests/fixtures/<ID>.json；找不到返回 None。"""
    path = os.path.join(FIXTURE_DIR, f'{source_id}.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 解析器（live / mock 共用，保证离线测试覆盖真实代码路径）
# ---------------------------------------------------------------------------
def parse_jq_csv(text):
    """JQData HTTP 返回 CSV 文本 → 行列表（首行为表头）。"""
    lines = [ln for ln in (text or '').replace('\r', '').splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    head = lines[0].split(',')
    rows = []
    for ln in lines[1:]:
        cells = ln.split(',')
        rows.append(dict(zip(head, cells[:len(head)])))
    return rows


def parse_jq_http(payload):
    """聚宽 HTTP：{factor_rows} → 因子序列（不是新闻，故只产 series，不产 news）。"""
    rows = payload.get('get_factor_values') or payload.get('rows') or []
    series = []
    for r in rows:
        date = r.get('date') or r.get('trade_date') or ''
        for k, v in r.items():
            if k in ('date', 'code', 'trading_date'):
                continue
            try:
                series.append({'date': str(date)[:10], 'value': float(v), 'extra': {'factor': k, 'code': r.get('code', '')}})
            except (TypeError, ValueError):
                continue
    news = []
    for r in payload.get('cctv_news') or payload.get('news') or []:
        news.append(_norm_news('', r.get('title'), r.get('content') or r.get('title'),
                               r.get('day') or r.get('date'), r.get('source', 'CCTV'), ''))
    factors = [f.get('factor_code') for f in (payload.get('all_factors') or [])
               if isinstance(f, dict)]
    return {'series': series, 'news': news,
            'meta': {'factor_total': len(factors),
                     'emotion_factors': [f for f in factors if f][:0] or
                                        [f for f in (payload.get('emotion_factor_codes') or [])]},
            'quota': {'note': 'get_token 当日有效；试用 100 万条/天，因子与特色数据需付费版',
                      'remaining': payload.get('query_count_left')}}


def parse_rq_news(payload):
    """米筐 news.get_stock_news → DataFrame.to_dict('records') 形态。

    平台已给出现成情感字段，这里直接折算成单一 sentiment 数值（平台优先原则）：
      sentiment = emotion_indicator × 极性置信度
    其中置信度取 max(positive_weight, negative_weight)（新闻层），
    并按 company_relevance 降权（相关度为 0 时由 sentiment_nlp 统一再降到 0.35）。
    """
    rows = payload.get('records') or payload.get('data') or []
    news = []
    for r in rows:
        ind = r.get('news_emotion_indicator')
        pos = r.get('news_positive_weight') or 0.0
        neg = r.get('news_negative_weight') or 0.0
        senti = None
        if ind is not None:
            try:
                senti = round(float(ind) * max(float(pos), float(neg), 0.5), 4)
            except (TypeError, ValueError):
                senti = float(ind)
        news.append(_norm_news(r.get('order_book_id') or r.get('symbol'), r.get('title'),
                               r.get('title'), r.get('original_time') or r.get('datetime'),
                               r.get('source'), r.get('url'), sentiment=senti,
                               relevance=r.get('company_relevance'),
                               extra={'news_id': r.get('news_id'),
                                      'company_emotion_indicator': r.get('company_emotion_indicator')}))
    return {'news': news, 'series': [],
            'meta': {'field_check': [k for k in ('news_emotion_indicator', 'news_positive_weight',
                                                  'news_negative_weight', 'company_relevance')
                                     if rows and k in rows[0]]}}


def parse_uqer(payload):
    """优矿 DataAPI：sentimentIndex / heatIndex 即日频现成舆情因子。"""
    series = []
    for r in payload.get('sentiment') or []:
        series.append({'date': _uqer_date(r.get('tradeDate')),
                       'value': _f(r.get('sentimentIndex')),
                       'extra': {'kind': 'sentimentIndex', 'secID': r.get('secID')}})
    for r in payload.get('heat') or []:
        series.append({'date': _uqer_date(r.get('tradeDate')),
                       'value': _f(r.get('heatIndex')),
                       'extra': {'kind': 'heatIndex', 'secID': r.get('secID')}})
    news = []
    for r in payload.get('news') or []:
        news.append(_norm_news(r.get('secID'), r.get('title'), r.get('content') or r.get('title'),
                               r.get('publishTime'), r.get('source'), r.get('url')))
    return {'series': [s for s in series if s['value'] is not None], 'news': news,
            'meta': {'note': 'sentimentIndex ∈ [-1,1] 为当日关联新闻情感均值；heatIndex 为新闻热度'}}


def parse_tushare(payload):
    """Tushare Pro：{data:{fields:[...], items:[[...]]}} → news 列表。"""
    data = payload.get('data') or {}
    fields = data.get('fields') or []
    items = data.get('items') or []
    news = []
    for row in items:
        r = dict(zip(fields, row))
        news.append(_norm_news(r.get('ts_code') or '', r.get('title'), r.get('content'),
                               r.get('datetime') or r.get('pub_time'),
                               r.get('src') or r.get('src_site'), ''))
    return {'news': news, 'series': [], 'meta': {'api_name': payload.get('api_name', 'news')}}


def parse_chinascope(payload):
    """数库 A 股新闻情绪指数：[{tradeDate, maIndex1, marketClose}]（akshare 同源口径）。"""
    rows = payload if isinstance(payload, list) else (payload.get('data') or [])
    series = []
    for r in rows:
        v = _f(r.get('maIndex1', r.get('市场情绪指数')))
        if v is None:
            continue
        series.append({'date': str(r.get('tradeDate') or r.get('日期'))[:10], 'value': v,
                       'extra': {'hs300': _f(r.get('marketClose', r.get('沪深300指数')))}})
    return {'news': [], 'series': series,
            'meta': {'note': '市场级情绪指数，基期=1.0；>1 偏乐观。<1 偏悲观'}}


def parse_em_comment(payload):
    """东财 千股千评：result.data → 全市场热度快照 + 个股关注指数序列。"""
    result_node = payload.get('result') or {}
    rows = result_node.get('data') or payload.get('data') or []
    series, news = [], []
    for r in rows:
        date = str(r.get('TRADE_DATE') or '')[:10]
        sym = r.get('SECURITY_CODE') or r.get('symbol') or ''
        for key, label in (('MARKET_FOCUS', '关注指数'), ('ORG_PARTICIPATE', '机构参与度'),
                           ('TOTAL_SCORE', '综合得分')):
            v = _f(r.get(key))
            if v is not None:
                series.append({'date': date, 'value': v,
                               'extra': {'factor': label, 'symbol': sym,
                                         'name': r.get('SECURITY_NAME_ABBR', '')}})
        if r.get('SECURITY_NAME_ABBR') and not sym:
            continue
    return {'news': news, 'series': series,
            'meta': {'rows': len(rows), 'pages': result_node.get('pages'),
                     'note': '热度/关注度类因子，无情感极性，需与新闻文本层结合'}}


def parse_em_news(payload):
    """东财 search-api JSONP → result.cmsArticleWebOld 新闻列表。"""
    node = payload.get('result') or {}
    rows = node.get('cmsArticleWebOld') or node.get('news') or []
    news = []
    for r in rows:
        title = _strip_tag(r.get('title', ''))
        content = _strip_tag(r.get('content', '') or '')
        news.append(_norm_news(r.get('secid') or r.get('code') or '', title, content,
                               r.get('date'), r.get('mediaName'), r.get('url') or ''))
    return {'news': news, 'series': [], 'meta': {'hits_total': node.get('hitsTotal') or len(news)}}


def parse_jin10(payload):
    """金十 微博舆情报告：data 列表 → 个股微博人气排行指数（小时级热度）。"""
    rows = payload.get('data') or []
    series = []
    for r in rows:
        v = _f(r.get('rank_value', r.get('index', r.get('hot'))))
        if v is None:
            continue
        series.append({'date': str(payload.get('date') or _now().strftime('%Y-%m-%d')), 'value': v,
                       'extra': {'symbol': norm_symbol(r.get('symbol') or r.get('code')),
                                 'name': r.get('name') or '', 'kind': '微博人气指数',
                                 'timescale': payload.get('timescale', 'CNHOUR12')}})
    return {'news': [], 'series': series, 'meta': {'rows': len(rows),
                                                   'note': '小时级热度，仅有无极性'}}


def parse_gm(payload):
    """掘金：能力缺失不是故障 —— 返回空结果并携带说明，由评测层判 NOT_SUPPORTED。"""
    return {'news': [], 'series': [],
            'meta': {'note': (payload or {}).get('note') or '掘金数据 API 不提供舆情/新闻接口'},
            'symbols_ok': (payload or {}).get('symbols_ok')}


PARSERS = {
    'jq_http': parse_jq_http, 'jq_sdk': parse_jq_http,
    'rq_sdk': parse_rq_news, 'rq_http': parse_rq_news,
    'uqer_http': parse_uqer, 'tushare_http': parse_tushare,
    'chinascope_http': parse_chinascope, 'em_datacenter': parse_em_comment,
    'em_search_api': parse_em_news, 'jin10_weibo': parse_jin10,
    'gm_sdk': parse_gm,
}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _uqer_date(d):
    s = str(d or '')
    if len(s) == 8 and s.isdigit():
        return f'{s[:4]}-{s[4:6]}-{s[6:8]}'
    return s[:10]


def _strip_tag(s):
    return re.sub(r'</?em>', '', s or '').strip()


# ---------------------------------------------------------------------------
# live 调用（每个 kind 一个函数；失败一律抛异常，由 fetch()/probe_stage_* 归类到 stage）
# ---------------------------------------------------------------------------
def _env(names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def creds(source_id):
    """按注册表取凭据（值只在本进程内使用，绝不写盘、不打日志）。"""
    src = reg.get_source(source_id) or {}
    out = {}
    for i, group in enumerate(src.get('auth_env') or []):
        names = list(group) if isinstance(group, (list, tuple)) else [group]
        out[f'p{i}'] = _env(names)
        out[f'names{i}'] = '/'.join(names)
    return out


def _jq_http_token(timeout):
    c = creds('JQ_HTTP')
    mob, pwd = c.get('p0'), c.get('p1')
    if not mob or not pwd:
        raise PermissionError('缺少 JQ_MOBILE/JQ_USERNAME + JQ_PASSWORD（聚宽账号体系）')
    text, ms = http_request('https://dataapi.joinquant.com/apis', method='POST',
                            json_body={'method': 'get_token', 'mob': mob, 'pwd': pwd},
                            timeout=timeout)
    tok = text.strip().strip('"')
    if not tok or 'error' in tok.lower() or len(tok) < 16:
        raise RuntimeError(f'get_token 失败: {tok[:120]}')
    return tok, ms


def call_jq_http(source, timeout, symbols, factors, days):
    """聚宽 HTTP：先 get_token（当日有效），再取因子值。官方已标注该 HTTP 接口不再维护。"""
    token, t0 = _jq_http_token(timeout)
    end = _now().strftime('%Y-%m-%d')
    lat, rows = [t0], []
    for sym in symbols[:3]:
        text, ms = http_request('https://dataapi.joinquant.com/apis', method='POST',
                                json_body={'method': 'get_factor_values', 'token': token,
                                           'code': sym, 'columns': ','.join(factors),
                                           'date': end, 'end_date': end}, timeout=timeout)
        lat.append(ms)
        rows.extend(parse_jq_csv(text))
    payload = {'get_factor_values': rows}
    try:  # 配额探针：get_query_count 直接返回剩余条数
        text, ms = http_request('https://dataapi.joinquant.com/apis', method='POST',
                                json_body={'method': 'get_query_count', 'token': token},
                                timeout=timeout)
        lat.append(ms)
        payload['query_count_left'] = int(re.sub(r'\D', '', text) or 0) or None
    except Exception:  # noqa: BLE001 — 配额查询失败不影响取数结论
        payload['query_count_left'] = None
    return payload, lat


def call_jq_sdk(source, timeout, symbols, factors, days):
    import jqdatasdk  # noqa: WPS433 — 可选依赖，缺失时由 deps 阶段短路
    c = creds('JQ_SDK')
    if not c.get('p0') or not c.get('p1'):
        raise PermissionError('缺少 JQ_MOBILE/JQ_USERNAME + JQ_PASSWORD')
    jqdatasdk.auth(c['p0'], c['p1'])
    from datetime import timedelta
    end = _now().date()
    start = end - timedelta(days=max(1, days))
    data = jqdatasdk.get_factor_values(securities=symbols[:50], factors=factors,
                                       start_date=str(start), end_date=str(end))
    rows = []
    for fname, df in (data or {}).items():
        for idx, row in df.iterrows():
            for code, val in row.items():
                rows.append({'date': str(idx)[:10], 'code': code, fname: val})
    left = None
    try:
        left = jqdatasdk.get_query_count()
    except Exception:  # noqa: BLE001
        pass
    return {'get_factor_values': rows, 'query_count_left': left}, []


def call_rq_sdk(source, timeout, symbols, factors, days):
    import rqdatac  # noqa: WPS433
    import rqdatac_news  # noqa: F401,WPS433 — 必须显式导入才会挂载 rqdatac.news 命名空间
    from datetime import timedelta
    c = creds('RQ_SDK')
    if not rqdatac.init(c.get('p0'), c.get('p1')) and not (c.get('p0') and c.get('p1')):
        raise PermissionError('缺少 RQDATA_USER/RQDATAC_USER + RQDATA_PASSWORD（或本地 license）')
    end = _now().date()
    start = end - timedelta(days=max(1, days))
    df = rqdatac.news.get_stock_news(symbols[:5], start_date=str(start), end_date=str(end))
    records = json.loads(df.to_json(orient='records', date_format='iso')) if df is not None and len(df) else []
    return {'records': records}, []


def call_gm_sdk(source, timeout, symbols, factors, days):
    """掘金：只做「连通 + token 有效性」验证；舆情能力缺失是产品事实，不是故障。"""
    import gm  # noqa: WPS433
    tok = _env(['GM_TOKEN'])
    if not tok:
        raise PermissionError('缺少 GM_TOKEN（掘金终端 → 系统设置 → 密钥管理）')
    gm.set_token(tok)
    infos = gm.get_symbol_infos(instruments=symbols[:3])
    if infos is None:
        raise RuntimeError('get_symbol_infos 返回空：掘金终端未运行或未联网')
    return {'note': '掘金数据 API 覆盖行情/财务/成分/日历/估值，不提供新闻与舆情因子；'
                    '如需舆情需自建或外部数据回灌', 'symbols_ok': len(infos)}, []


def call_uqer_http(source, timeout, symbols, factors, days):
    """优矿 DataAPI HTTP：POST {token, 参数}；端点/字段需按有权账号实测复核。"""
    tok = _env(['UQER_TOKEN'])
    if not tok:
        raise PermissionError('缺少 UQER_TOKEN')
    from datetime import timedelta
    end = _now().date().strftime('%Y%m%d')
    start = (_now().date() - timedelta(days=max(1, days))).strftime('%Y%m%d')
    out, lat = {}, []
    for method, key in (('NewsSentimentIndexGet', 'sentiment'), ('NewsHeatIndexGet', 'heat'),
                        ('NewsByTickersGet', 'news')):
        payload = {'token': tok, 'beginDate': start, 'endDate': end}
        if key == 'news':
            payload['ticker'] = symbols[0] if symbols else ''
        try:
            text, ms = http_request(f'https://api.uqer.cn/data/{method}', method='POST',
                                    json_body=payload, timeout=timeout)
            lat.append(ms)
            out[key] = json.loads(text).get('data') or []
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            out.setdefault('_errors', {})[method] = f'{type(e).__name__}: {str(e)[:100]}'
    return out, lat


def call_tushare(source, timeout, symbols, factors, days):
    """Tushare Pro：POST {api_name, token, params, fields} → JSON（自建情感层的文本进料口）。"""
    tok = _env(['TUSHARE_TOKEN'])
    if not tok:
        raise PermissionError('缺少 TUSHARE_TOKEN')
    from datetime import timedelta
    end = _now().strftime('%Y-%m-%d %H:%M:%S')
    start = (_now() - timedelta(days=max(1, days))).strftime('%Y-%m-%d %H:%M:%S')
    body = {'api_name': 'news', 'token': tok,
            'params': {'src': os.environ.get('TUSHARE_NEWS_SRC', 'cls'),
                       'start_date': start, 'end_date': end},
            'fields': 'datetime,title,content,src'}
    text, ms = http_request('http://api.tushare.pro', method='POST', json_body=body, timeout=timeout)
    payload = json.loads(text)
    if payload.get('code') not in (0, None):
        raise RuntimeError(f"Tushare 返回错误: {(payload.get('msg') or '')[:160]}")
    payload['api_name'] = 'news'
    return payload, [ms]


def call_chinascope(source, timeout, symbols, factors, days):
    """数库 A 股新闻情绪指数（免费公开，境外/境内均可达，但站方改版风险高）。"""
    url = 'https://www.chinascope.com/inews/senti/index?' + urllib.parse.urlencode({'period': 'YEAR'})
    text, ms = http_request(url, timeout=timeout)
    return json.loads(text), [ms]


def call_em_comment(source, timeout, symbols, factors, days):
    """东财 千股千评：全市场热度快照（一次分页），可取关注指数/机构参与度/综合得分。"""
    params = {
        'sortColumns': 'SECURITY_CODE', 'sortTypes': '1', 'pageSize': '100', 'pageNumber': '1',
        'reportName': 'RPT_DMSK_TS_STOCKNEW',
        'quoteColumns': 'f2~01~SECURITY_CODE~CLOSE_PRICE,f3~01~SECURITY_CODE~CHANGE_RATE',
        'columns': 'ALL', 'filter': '', 'source': 'WEB', 'client': 'WEB',
    }
    url = 'https://datacenter-web.eastmoney.com/api/data/v1/get?' + urllib.parse.urlencode(params)
    text, ms = http_request(url, timeout=timeout, headers={'Referer': 'https://data.eastmoney.com/'})
    return json.loads(text), [ms]


# 关键词检索型源：一次构建按日报标的跑多个关键词，保证「采集 → 匹配」覆盖日报正文
EM_NEWS_MAX_KEYWORDS = 3


def _em_news_payload(kw, timeout):
    """单个关键词 → (search-api JSONP 报文, 本次延迟 ms)。"""
    inner = {'uid': '', 'keyword': kw, 'type': ['cmsArticleWebOld'], 'client': 'web',
             'clientType': 'web', 'clientVersion': 'curr',
             'param': {'cmsArticleWebOld': {'searchScope': 'default', 'sort': 'default',
                                            'pageIndex': 1, 'pageSize': 20,
                                            'preTag': '<em>', 'postTag': '</em>'}}}
    cb = 'jQuery_sentiment'
    params = {'cb': cb, 'param': json.dumps(inner, ensure_ascii=False)}
    url = 'https://search-api-web.eastmoney.com/search/jsonp?' + urllib.parse.urlencode(params)
    text, ms = http_request(url, timeout=timeout, headers={'Referer': 'https://so.eastmoney.com/'})
    body = re.sub(r'^[^(]*\(', '', text.strip())[:-1]
    return json.loads(body), ms


def _em_news_merge(base, extra):
    """多关键词报文合并（按 url/标题去重）；任一报文结构异常时保守返回已有结果。"""
    if not base:
        return extra
    if not extra:
        return base
    rows_b = ((base.get('result') or {}).get('cmsArticleWebOld'))
    rows_e = ((extra.get('result') or {}).get('cmsArticleWebOld'))
    if rows_b is None or rows_e is None:
        return base
    seen, merged = set(), list(rows_b)
    for r in rows_b:
        seen.add(r.get('url') or r.get('title') or '')
    for r in rows_e:
        k = r.get('url') or r.get('title') or ''
        if k in seen:
            continue
        seen.add(k)
        merged.append(r)
    out = dict(base)
    res = dict(out.get('result') or {})
    res['cmsArticleWebOld'] = merged
    res['hitsTotal'] = len(merged)
    out['result'] = res
    return out


def call_em_news(source, timeout, symbols, factors, days):
    """关键词 → 新闻检索（本项目自建情感层的主力免费文本源）。

    symbols 允许传多个关键词（见 sentiment_match.search_keywords()）：逐个检索后合并去重，
    使采集面覆盖日报标的（恒指/港股/黄金/原油/美联储…），下游才能做「匹配显示」。
    单个关键词失败不影响其余关键词。
    """
    kws = [str(k).strip() for k in (symbols or []) if str(k).strip()][:EM_NEWS_MAX_KEYWORDS] \
        or ['恒生指数']
    payload, lats, used = None, [], []
    for kw in kws:
        try:
            p, ms = _em_news_payload(kw, timeout)
        except Exception:  # noqa: BLE001 — 单关键词失败跳过，其余关键词继续
            if payload is None and kw == kws[0]:
                raise                      # 第一个关键词就失败 → 交给 fetch() 归类到对应阶段
            continue
        payload = _em_news_merge(payload, p)
        lats.append(ms)
        used.append(kw)
    if payload is None:                      # 理论上不可达（首词失败已抛出），保守给空报文
        payload = {'result': {'cmsArticleWebOld': [], 'hitsTotal': 0}}
    payload['keywords'] = used or kws
    return payload, (lats or [0.0])


def call_jin10(source, timeout, symbols, factors, days):
    """金十数据中心 微博舆情报告（小时级热度，需带 akshare 同款请求头）。"""
    params = {'timescale': os.environ.get('JIN10_TIMESCALE', 'CNHOUR12'),
              '_': str(int(time.time() * 1000))}
    url = 'https://datacenter-api.jin10.com/weibo/list?' + urllib.parse.urlencode(params)
    hdrs = {'x-app-id': 'rU6QIu7JHe2gOUeR', 'x-version': '1.0.0', 'x-csrf-token': '',
            'origin': 'https://datacenter.jin10.com', 'referer': 'https://datacenter.jin10.com/market'}
    text, ms = http_request(url, timeout=timeout, headers=hdrs)
    payload = json.loads(text)
    payload.setdefault('timescale', params['timescale'])
    payload.setdefault('date', _now().strftime('%Y-%m-%d'))
    return payload, [ms]


CALLERS = {
    'jq_http': call_jq_http, 'jq_sdk': call_jq_sdk, 'rq_sdk': call_rq_sdk,
    'gm_sdk': call_gm_sdk, 'uqer_http': call_uqer_http, 'tushare_http': call_tushare,
    'chinascope_http': call_chinascope, 'em_datacenter': call_em_comment,
    'em_search_api': call_em_news, 'jin10_weibo': call_jin10,
}

# 无公开 HTTP 端点，或需合同开通的源 —— 探测时直接给出说明而非报错
NO_LIVE_CALLER = {'rq_http': '需机构合同提供端点与鉴权头，签约后再填 live 调用；当前仅按文档基线评估'}


# ---------------------------------------------------------------------------
# 对外统一入口
# ---------------------------------------------------------------------------
def fetch(source_id, mode='live', symbols=None, factors=None, days=3, timeout=DEFAULT_TIMEOUT):
    """取一次数（live / mock / off），返回统一 Result。任何异常都归类到 stage 字段，不外抛。"""
    src = reg.get_source(source_id)
    if not src:
        return result(source_id, stage='deps', error=f'未注册的数据源: {source_id}')
    if mode == 'off':
        return result(source_id, mode=mode, stage='none',
                      error='offline：跳过取数，沿用上次 sentiment_data.json')
    if mode == 'mock':
        payload = load_fixture(source_id)
        if payload is None:
            return result(source_id, mode=mode, stage='fetch',
                          error=f'缺少录制报文 tests/fixtures/{source_id}.json')
        res = result(source_id, mode=mode, stage='fetch',
                     meta={'fixture': True})
        parsed = PARSERS[src['kind']](payload.get('payload', payload))
        res.update(parsed)
        res['meta'].update({'note': (parsed.get('meta') or {}).get('note', ''),
                            'live_note': 'mock 模式：报文回放，未产生网络请求'})
        res['quota'] = parsed.get('quota') or {'note': '', 'remaining': None}
        return _finalize(res, [0.0])

    kind = src['kind']
    if kind in NO_LIVE_CALLER:
        return result(source_id, mode=mode, stage='auth', error=NO_LIVE_CALLER[kind],
                      meta={'note': NO_LIVE_CALLER[kind]})
    caller = CALLERS.get(kind)
    if caller is None:
        return result(source_id, mode=mode, stage='deps', error=f'未实现适配器: {kind}')

    symbols = symbols or _default_symbols(src)
    factors = factors or _jq_emotion_factors()
    try:
        payload, lat = caller(src, timeout, symbols, factors, days)
    except ImportError as e:
        return result(source_id, mode=mode, stage='deps',
                      error=f'依赖缺失 {e.name}：pip install {" ".join(src.get("requires") or [])}')
    except (PermissionError, ValueError) as e:
        return result(source_id, mode=mode, stage='auth', error=str(e)[:240])
    except urllib.error.HTTPError as e:
        return result(source_id, mode=mode, stage='net',
                      error=f'HTTP {e.code} {e.reason}（境外 IP 常被境内数据站直接拒绝，需在境内出口运行）')
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as e:
        return result(source_id, mode=mode, stage='net',
                      error=f'{type(e).__name__}: {str(e)[:160]}（出口受限或站点不可达）')
    except json.JSONDecodeError as e:
        return result(source_id, mode=mode, stage='fetch', error=f'响应非 JSON: {str(e)[:120]}')
    except Exception as e:  # noqa: BLE001 — SDK 异常类型五花八门，统一归到 fetch 阶段
        return result(source_id, mode=mode, stage='fetch', error=f'{type(e).__name__}: {str(e)[:200]}')

    res = result(source_id, mode=mode, stage='fetch', meta={})
    try:
        parsed = PARSERS[kind](payload)
    except Exception as e:  # noqa: BLE001
        res['error'] = f'解析失败 {type(e).__name__}: {str(e)[:160]}'
        return _finalize(res, lat)
    res.update(parsed)
    res['meta'].setdefault('live', True)
    return _finalize(res, lat)


def _default_symbols(src):
    """按数据源市场给默认样本：A 股用沪深样本，港股日报场景用恒生指数关键词。"""
    cov = (src or {}).get('coverage') or ''
    if 'A 股' in cov or '沪深' in cov:
        # 使用注册表 WATCHLIST，已删除指定个股
        wl = list(getattr(reg, 'WATCHLIST', []) or [])
        if not wl:
            return []
        if 'JQ' in (src.get('id') or ''):
            return [f"{c}.{('XSHG' if ex == 'SH' else 'XSHE')}" for c, ex in
                    (w.split('.') for w in wl)]
        return wl
    return ['恒生指数']


def _jq_emotion_factors():
    src = reg.get_source('JQ_HTTP') or {}
    return list((src.get('sentiment_fields') or {}).get('因子库·情绪类') or ['VOL5', 'AR', 'BR'])


def check_deps(source_id):
    """阶段 0：依赖可用性。"""
    d = reg.deps_status().get(source_id, {'modules': [], 'missing': []})
    ok = not d['missing']
    hint = ''
    src = reg.get_source(source_id) or {}
    if not ok and src.get('pip_index'):
        hint = f"；需私有源：pip install -i {src['pip_index']} {' '.join(d['missing'])}"
    return ok, (f"依赖齐备: {', '.join(d['modules']) or '无第三方依赖（纯标准库）'}{hint}"
                if ok else f"缺包: {', '.join(d['missing'])}{hint}")


def check_net(source_id, timeout=6):
    """阶段 1：网络可达性（DNS + TCP，不做业务调用）。"""
    src = reg.get_source(source_id) or {}
    ep = src.get('endpoint')
    if not ep:
        return True, 'SDK 专用源（无独立 HTTP 端点，可达性并入 auth/fetch）'
    host = urllib.parse.urlparse(ep).hostname or ''
    port = urllib.parse.urlparse(ep).port or (443 if ep.startswith('https') else 80)
    ok, ms, err = tcp_check(host, port, timeout)
    return ok, (f'{host}:{port} TCP 可达（{ms}ms）' if ok else f'{host}:{port} 不可达 — {err}')


def check_auth(source_id):
    """阶段 2：凭据是否存在（不发起登录，登录在 fetch 里一起做）。"""
    st = reg.credential_status().get(source_id, {'required': False, 'ready': True, 'missing': []})
    if not st['required']:
        return True, '免鉴权（公开接口）'
    if st['ready']:
        return True, f"已配置 {'、'.join(st['present'])}"
    return False, f"缺凭据环境变量: {'、'.join(st['missing'])}（在 CI Secrets/本地 .env 配置，勿写入仓库）"


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='章鱼 AI·舆情004 — 舆情/新闻因子适配器自检')
    ap.add_argument('--mode', choices=['live', 'mock', 'off'], default='mock')
    ap.add_argument('--source', default='', help='只测某个源 id（默认全测）')
    args = ap.parse_args()
    default_syms = list(getattr(reg, 'WATCHLIST', []) or []) or ['恒生指数']
    for sid in ([args.source] if args.source else reg.ids()):
        r = fetch(sid, mode=args.mode, symbols=default_syms)
        print(f"{sid:<14} ok={str(r['ok']):<5} stage={r['stage']:<6} "
              f"news={len(r['news']):<3} series={len(r['series']):<4} latest={r['latest_date']} "
              f"err={(r['error'] or '')[:70]}")
