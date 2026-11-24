#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 方程式新闻（EquationNews）全球快讯（06 栏数据源）
====================================================================

在 05 栏（49 大财经 RSS 情报源）之后新增的一栏：接入**方程式新闻**的公开快讯，
把「交易所公告 / 加密重大行情 / 美股异动 / 宏观快讯」这类**秒级-分钟级**的
消息流纳入日报，与 05 栏的小时级 RSS 形成时效互补。

端点（公开、只读、免密钥）
  • MCP 连接器：https://api.eqn.news/public/mcp
    —— Streamable HTTP 传输，工具 list_news / get_news，供 Claude / Codex /
    Cursor 这类 AI 助手对话时直连（`--via mcp` 可让本模块也走这条协议）。
  • REST 轮询：https://api.eqn.news/public/v1/news
    —— 官方口径「程序定时拉取请用 REST」：参数与返回字段和 MCP 完全一致，更快。
       本模块默认走 REST；两端只是传输不同，归一化后的产物结构相同。
  • 频道表：https://api.eqn.news/public/v1/channels（15 个频道 key）
  • RSS：https://api.eqn.news/public/rss（主频道中文，最近 50 条）

反陈旧口径（与 02 / 05 栏同一套纪律）
  • 只展示当次真实抓到的条目：抓不到就整栏留空，**不回填历史标题**；
  • 时效窗口（默认 7 天）外的条目在数据层丢弃并计数；无日期条目保留但永远垫底；
  • 快照超过 1 天判 stale_snapshot，网页与微信两端共用 availability() 一个事实源；
  • 构建期补抓只在 CLI 入口（ensure），渲染函数纯读取、绝不联网；
  • 单频道失败不阻断构建；AI 摘要是上游生成的，页面如实标注「AI 生成」。

用法:
  python3 eqn_news.py                    # 抓取 → eqn_news_data.json
  python3 eqn_news.py --mock --text      # 离线回放 tests/fixtures（CI / 联调）
  python3 eqn_news.py --self-test        # 离线自检（零联网）
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(REPO_ROOT, 'eqn_news_data.json')
FIXTURE_DIR = os.path.join(REPO_ROOT, 'tests', 'fixtures')
FIXTURE_NAME = 'EQN_NEWS_MIX.json'

# --- 端点 ---------------------------------------------------------------
# MCP 连接器地址（用户指定的端点）：AI 助手直连用；本模块也可 --via mcp 走它。
MCP_URL = 'https://api.eqn.news/public/mcp'
API_BASE = os.environ.get('EQN_API_BASE', 'https://api.eqn.news/public/v1')
NEWS_URL = f'{API_BASE}/news'
CHANNELS_URL = f'{API_BASE}/channels'
RSS_URL = 'https://api.eqn.news/public/rss'
DOCS_URL = 'https://eqn.news/api-docs'

USER_AGENT = ('Mozilla/5.0 (compatible; OctopusSentiment004/1.0; '
              '+https://k-macao.github.io/004/)')

MAX_AGE_DAYS_DEFAULT = 7        # 与 02 栏同一档窗口；快讯流默认留一周
LIMIT_DEFAULT = 30              # 单次入库上限（接口本身 1–50）
LIMIT_MAX = 50
SNAPSHOT_MAX_AGE_DAYS = 1       # 快照新鲜度上限，与 macro_data / rss_feeds 同口径

DEFAULT_CHANNEL = os.environ.get('EQN_CHANNEL', 'main')   # 频道 key（见 CHANNELS_URL）

AUTO_FETCH_TIMEOUT = 6          # 构建期补抓的单请求超时
AUTO_FETCH_BUDGET_SECONDS = 20  # 构建期补抓的墙钟预算（单频道一次请求，足够）
REFETCH_REASONS = ('no_file', 'bad_type', 'stale_snapshot')


class EqnError(RuntimeError):
    """方程式新闻接口调用失败（网络 / 报文 / 协议）。"""


# ---------------------------------------------------------------------------
# 取数：REST（默认，官方推荐的程序轮询通道）
# ---------------------------------------------------------------------------
def http_get(url, timeout=10, headers=None):
    req = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept': 'application/json',
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            ctype = resp.headers.get('Content-Type', '')
    except urllib.error.HTTPError as e:
        raise EqnError(f'HTTP {e.code}: {url}') from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise EqnError(f'{type(e).__name__}: {url} — {e}') from e
    if 'json' not in ctype.lower():
        # 线上偶发 HTML 错误页：当作失败，让上层降级，绝不把错误页解析成条目
        raise EqnError(f'非 JSON 响应（{ctype or "未知类型"}）: {url}')
    try:
        return json.loads(body.decode('utf-8'))
    except ValueError as e:
        raise EqnError(f'响应不是合法 JSON: {url} — {e}') from e


def rest_url(channel='main', limit=LIMIT_DEFAULT, query='', coin='', base=NEWS_URL):
    params = []
    ch = (channel or 'main').strip() or 'main'
    if ch and ch != 'all':
        params.append(('channel', ch))
    if query:
        params.append(('query', query))
    if coin:
        params.append(('coin', coin.upper()))
    params.append(('limit', str(max(1, min(int(limit), LIMIT_MAX)))))
    return f'{base}?{urllib.parse.urlencode(params)}'


def fetch_rest(channel='main', limit=LIMIT_DEFAULT, query='', coin='',
               timeout=10, base=NEWS_URL):
    """GET /public/v1/news → 原始报文（含 items / next_cursor）。"""
    payload = http_get(rest_url(channel=channel, limit=limit, query=query,
                                coin=coin, base=base), timeout=timeout)
    if not isinstance(payload, dict):
        raise EqnError('REST 报文不是对象')
    if isinstance(payload.get('error'), str):
        raise EqnError(f"接口报错: {payload['error']}")
    if not isinstance(payload.get('items'), list):
        raise EqnError('REST 报文缺 items 列表')
    return payload


# ---------------------------------------------------------------------------
# 取数：MCP（Streamable HTTP · JSON-RPC 2.0）
# ---------------------------------------------------------------------------
def _parse_sse(text):
    """从 SSE 报文里取出最后一个 JSON data 块（MCP streamable-http 的返回形态之一）。"""
    out = None
    for line in (text or '').splitlines():
        line = line.strip()
        if line.startswith('data:'):
            chunk = line[5:].strip()
            if chunk:
                try:
                    out = json.loads(chunk)
                except ValueError:
                    continue
    return out


def _rpc_post(url, payload, timeout=10, session_id=None):
    """POST 一段 JSON-RPC；兼容 application/json 与 text/event-stream 两种返回。"""
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json, text/event-stream'}
    if session_id:
        headers['Mcp-Session-Id'] = session_id
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                 headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            sid = resp.headers.get('Mcp-Session-Id') or session_id
            body = resp.read().decode('utf-8', 'replace')
            status = getattr(resp, 'status', 200)
    except urllib.error.HTTPError as e:
        raise EqnError(f'MCP HTTP {e.code}: {url}') from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise EqnError(f'MCP {type(e).__name__}: {url} — {e}') from e
    msg = None
    try:
        msg = json.loads(body)
    except ValueError:
        msg = _parse_sse(body)
    return status, sid, msg


def fetch_mcp(arguments=None, url=MCP_URL, timeout=10):
    """走 MCP 协议调 list_news（与 REST 同参同返回）。

    最小实现：initialize（拿会话）→ notifications/initialized → tools/call。
    仅用于「让 AI 助手与本模块用同一入口」的场景；程序轮询仍推荐 REST。
    """
    args = {k: v for k, v in (arguments or {}).items() if v not in (None, '')}
    _status, sid, init = _rpc_post(url, {
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                   'clientInfo': {'name': 'octopus-sentiment-004', 'version': '1.0'}},
    }, timeout=timeout)
    if not isinstance(init, dict) or init.get('error'):
        raise EqnError(f'MCP initialize 失败: {init}')
    try:
        _rpc_post(url, {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                  timeout=timeout, session_id=sid)
    except EqnError:
        pass  # 部分实现不接受通知，忽略；tools/call 才是关键路径
    _status, _sid, res = _rpc_post(url, {
        'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
        'params': {'name': 'list_news', 'arguments': args},
    }, timeout=timeout, session_id=sid)
    if not isinstance(res, dict) or res.get('error'):
        raise EqnError(f'MCP tools/call 失败: {res}')
    content = ((res.get('result') or {}).get('content') or [])
    text = content[0].get('text') if content and isinstance(content[0], dict) else None
    try:
        payload = json.loads(text) if isinstance(text, str) else text
    except ValueError as e:
        raise EqnError(f'MCP 返回不是合法 JSON: {e}') from e
    if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
        raise EqnError('MCP 返回缺 items 列表')
    return payload


def fetch_news(channel='main', limit=LIMIT_DEFAULT, query='', coin='', timeout=10,
               via='rest'):
    """统一取数入口：via='rest'（默认）| 'mcp'，返回同构原始报文。"""
    args = {'channel': channel, 'limit': max(1, min(int(limit), LIMIT_MAX))}
    if query:
        args['query'] = query
    if coin:
        args['coin'] = coin.upper()
    if via == 'mcp':
        return fetch_mcp(args, timeout=timeout)
    return fetch_rest(channel=channel, limit=limit, query=query, coin=coin,
                      timeout=timeout)


# ---------------------------------------------------------------------------
# 归一化 / 时效
# ---------------------------------------------------------------------------
def parse_time(raw):
    """ISO8601（可带 Z / 时区）→ UTC datetime；解析不了返回 None。"""
    s = str(raw or '').strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        m = re.match(r'(20\d{2})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})', s)
        if not m:
            return None
        try:
            dt = datetime(*map(int, m.groups()), tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ai_parts(raw):
    """把上游 ai 字段拆成渲染可用的中文片段（缺失一律留空，不编造）。"""
    ai = raw.get('ai') or {}
    if not isinstance(ai, dict):
        ai = {}
    summary = []
    pts = ((ai.get('summary') or {}).get('points') or [])
    if isinstance(pts, list):
        for p in pts:
            if not isinstance(p, dict):
                continue
            txt = (p.get('zh') or p.get('en') or '').strip()
            if txt:
                summary.append(txt)
    ov = ai.get('overview') or {}
    overview, changes, status = '', '', None
    if isinstance(ov, dict):
        status = ov.get('status')
        lang = ov.get('zh') if isinstance(ov.get('zh'), dict) else ov.get('en')
        if isinstance(lang, dict):
            paras = [(p.get('text') or '').strip() for p in (lang.get('paragraphs') or [])
                     if isinstance(p, dict)]
            overview = '\n'.join(t for t in paras if t)
        changes = (ov.get('changes') or '').strip() or ''
    return {'ai_summary': summary, 'ai_overview': overview, 'ai_changes': changes,
            'ai_overview_status': status, 'ai_notice': (ai.get('notice') or '').strip()}


def normalize(payload, now=None):
    """原始报文 → 统一条目结构（新→旧；无日期垫底）。

    条目字段：no / time / published_date / age_days / channels / source /
    title / title_zh / title_en / coins / url / source_url /
    ai_summary[] / ai_overview / ai_overview_status / ai_changes / ai_notice
    """
    now = now or datetime.now(timezone.utc)
    items = []
    for raw in (payload or {}).get('items') or []:
        if not isinstance(raw, dict):
            continue
        dt = parse_time(raw.get('time'))
        zh = str(raw.get('zh') or '').strip()
        en = str(raw.get('en') or '').strip()
        ai = _ai_parts(raw)
        channels = [str(c) for c in (raw.get('channels') or []) if str(c).strip()]
        coins = [str(c).upper() for c in (raw.get('coins') or []) if str(c).strip()]
        items.append({
            'no': raw.get('no'),
            'time': str(raw.get('time') or '').strip(),
            'published_at': dt.strftime('%Y-%m-%dT%H:%M:%SZ') if dt else None,
            'published_date': dt.strftime('%Y-%m-%d') if dt else None,
            'age_days': round((now - dt).total_seconds() / 86400, 3) if dt else None,
            'channels': channels,
            'source': str(raw.get('source') or '').strip(),
            'title': zh or en,
            'title_zh': zh,
            'title_en': en,
            'coins': coins,
            'url': str(raw.get('url') or '').strip(),
            'source_url': str(raw.get('source_url') or '').strip(),
            **ai,
        })
    dated = sorted((i for i in items if i['published_at']),
                   key=lambda i: i['published_at'], reverse=True)
    undated = [i for i in items if not i['published_at']]
    return dated + undated


def filter_stale(items, max_age_days=MAX_AGE_DAYS_DEFAULT, now=None):
    """时效过滤：超窗丢弃；无日期保留但垫底、计数。"""
    now = now or datetime.now(timezone.utc)
    kept, stale = [], 0
    for it in items:
        age = it.get('age_days')
        if age is None:
            kept.append(it)          # 无日期：保留，normalize 已垫底
        elif age <= max_age_days:
            kept.append(it)
        else:
            stale += 1
    return kept, {'stale_dropped': stale, 'undated_kept': sum(1 for i in kept if i['age_days'] is None)}


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------
def _load_fixture():
    path = os.path.join(FIXTURE_DIR, FIXTURE_NAME)
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def build(channel=DEFAULT_CHANNEL, limit=LIMIT_DEFAULT, max_age_days=MAX_AGE_DAYS_DEFAULT,
          timeout=10, mock=False, quiet=False, via='rest', query='', coin='',
          deadline=None):
    """抓一轮 → 归一 → 时效过滤，返回 eqn_news data dict。

    deadline: 可选墙钟截止（epoch 秒）；到点直接记失败，不把构建拖死。
    """
    now = datetime.now(timezone.utc)
    limit = max(1, min(int(limit), LIMIT_MAX))
    meta = {'ok': False, 'endpoint': '', 'error': None, 'latency_ms': 0.0}
    t0 = time.time()
    try:
        if deadline is not None and time.time() >= deadline:
            raise EqnError('超出本轮抓取的墙钟预算，未请求')
        if mock:
            payload = _load_fixture()
            # 离线回放：把 _age_hours 改写成「当前往前第 n 小时」，
            # 避免静态样本过一段时间后被时效窗口清空（与 rss_feeds._redate_for_demo 同思路）。
            for raw in payload.get('items') or []:
                age_h = raw.pop('_age_hours', None)
                if age_h is not None:
                    raw['time'] = (now - timedelta(hours=int(age_h))).strftime(
                        '%Y-%m-%dT%H:%M:%S.000Z')
            meta.update({'endpoint': f'fixture:{FIXTURE_NAME}', 'via': 'fixture'})
        else:
            payload = fetch_news(channel=channel, limit=limit, query=query, coin=coin,
                                 timeout=timeout, via=via)
            meta.update({'endpoint': NEWS_URL if via != 'mcp' else MCP_URL, 'via': via})
        meta['ok'] = True
    except Exception as e:  # noqa: BLE001 — 单频道失败不阻断构建
        meta['error'] = f'{type(e).__name__}: {e}'[:400]
        payload = {'items': []}
    meta['latency_ms'] = round((time.time() - t0) * 1000, 1)

    items = normalize(payload, now=now)
    kept, dropped = filter_stale(items, max_age_days=max_age_days, now=now)
    with_ai = sum(1 for i in kept if i.get('ai_summary') or i.get('ai_overview'))
    coin_count = {}
    for i in kept:
        for c in i.get('coins') or []:
            coin_count[c] = coin_count.get(c, 0) + 1
    top_coins = [c for c, _n in sorted(coin_count.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]

    result = {
        'generated_at': now.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'fetch_date': now.strftime('%Y-%m-%d'),
        'mode': 'mock' if mock else 'live',
        'channel': channel,
        'via': meta.get('via') or via,
        'limit': limit,
        'window': {'max_age_days': max_age_days,
                   'since': (now - timedelta(days=max_age_days)).strftime('%Y-%m-%d')},
        'items': kept,
        'source': {'ok': bool(meta['ok']), 'endpoint': meta.get('endpoint') or '',
                   'latency_ms': meta['latency_ms'], 'error': meta['error']},
        'summary': {
            'ok': 1 if meta['ok'] else 0,
            'total': 1,
            'items': len(kept),
            'with_ai': with_ai,
            'stale_dropped': dropped['stale_dropped'],
            'undated_kept': dropped['undated_kept'],
            'top_coins': top_coins,
        },
        'notes': [
            '由 eqn_news.py 构建时抓取（方程式新闻 EquationNews 公开接口，免密钥）：'
            f'频道 {channel} · 单轮最多 {limit} 条 · 只保留 {max_age_days} 天内条目',
            f'MCP 连接器 {MCP_URL}（AI 助手直连）与 REST 轮询 {NEWS_URL} 参数/字段一致，'
            '本模块默认走 REST，--via mcp 可切换',
            '条目里的 AI 摘要 / AI 总览由上游生成，可能有错，仅供参考；页面如实标注「AI 生成」',
            '抓不到就整栏留空，不回填历史标题；AI 内容缺失的条目照常展示原文，不硬凑摘要',
        ],
    }
    if not quiet:
        if meta['ok']:
            print(f'  ✅ EQN {channel:<10} {len(kept):>2} 条  [{meta.get("via")}] '
                  f'{meta.get("endpoint")} · {meta["latency_ms"]}ms')
        else:
            print(f'  ⚠️ EQN {channel} 失败: {meta["error"][:160]}（06 栏将留空，不影响其余栏目）',
                  file=sys.stderr)
    return result


# ---------------------------------------------------------------------------
# 可用性判定（与 macro_data / rss_feeds 同构：网页 06 节与微信 06 栏共用）
# ---------------------------------------------------------------------------
def availability(data, now=None, snapshot_max_age_days=SNAPSHOT_MAX_AGE_DAYS):
    """reason: no_file / bad_type / no_items / stale_snapshot / ok"""
    now = now or datetime.now(timezone.utc)
    if data is not None and not isinstance(data, dict):
        return {'ok': False, 'reason': 'bad_type', 'items': 0, 'snapshot_age_days': None,
                'detail': 'eqn_news_data.json 内容不是预期的对象结构（产物可能写坏或被截断）'}
    if not data:
        return {'ok': False, 'reason': 'no_file', 'items': 0, 'snapshot_age_days': None,
                'detail': '未读到 eqn_news_data.json（构建步骤未执行，或产物未生成）'}

    items = data.get('items') or []
    summ = data.get('summary') or {}
    ok_src, total_src = summ.get('ok'), summ.get('total')

    age = None
    stamp = (data.get('generated_at') or '').strip()
    m = re.match(r'(20\d{2}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})', stamp)
    if m:
        try:
            built = datetime.strptime(f'{m.group(1)} {m.group(2)}',
                                      '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            age = (now - built).total_seconds() / 86400
        except ValueError:
            age = None
    if age is None and re.match(r'20\d{2}-\d{2}-\d{2}$', str(data.get('fetch_date') or '')):
        built = datetime.strptime(data['fetch_date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        age = (now - built).total_seconds() / 86400
    age = round(age, 2) if age is not None else None

    if not items:
        src = (f'当次 {ok_src}/{total_src} 个频道可用，'
               if ok_src is not None and total_src is not None else '')
        dropped = []
        if summ.get('stale_dropped'):
            dropped.append(f'超窗 {summ["stale_dropped"]} 条')
        tail = f'（已拦截 {" / ".join(dropped)}）' if dropped else ''
        win = (data.get('window') or {}).get('max_age_days', '—')
        return {'ok': False, 'reason': 'no_items', 'items': 0, 'snapshot_age_days': age,
                'detail': f'{src}时效窗口（{win} 天）内没有任何可核验的快讯{tail}'}

    if age is not None and age > snapshot_max_age_days:
        return {'ok': False, 'reason': 'stale_snapshot', 'items': len(items),
                'snapshot_age_days': age,
                'detail': (f'读到的是 {age:.1f} 天前构建的旧快照'
                           f'（{stamp or data.get("fetch_date")}），超过 '
                           f'{snapshot_max_age_days} 天新鲜度上限 —— '
                           f'{len(items)} 条旧闻按兜底口径不予展示')}

    return {'ok': True, 'reason': 'ok', 'items': len(items), 'snapshot_age_days': age, 'detail': ''}


# ---------------------------------------------------------------------------
# 构建期补抓（与 macro_data.ensure 同一套策略，不依赖 workflow 权限）
# ---------------------------------------------------------------------------
def auto_fetch_enabled(explicit=None):
    """补抓开关：显式入参 > 环境变量 EQN_AUTO_FETCH > 「在 CI 里就开」。"""
    if explicit is not None:
        return bool(explicit)
    raw = str(os.environ.get('EQN_AUTO_FETCH', '')).strip().lower()
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    if raw in ('0', 'false', 'no', 'off'):
        return False
    return bool(os.environ.get('GITHUB_ACTIONS'))


def load_json(path=DEFAULT_OUT):
    """安全读产物：不存在 / 读不出 / 不是对象 → 返回 {}，交给 availability() 判原因。"""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data


def ensure(path=DEFAULT_OUT, channel=DEFAULT_CHANNEL, limit=LIMIT_DEFAULT,
           max_age_days=MAX_AGE_DAYS_DEFAULT, timeout=AUTO_FETCH_TIMEOUT, mock=False,
           quiet=True, now=None, allow_fetch=None, via='rest', budget_seconds=AUTO_FETCH_BUDGET_SECONDS):
    """读取 eqn_news_data.json；不可用且允许联网时就地补抓一次并落盘。

    返回 dict: data / action(fresh|fetched|refetch_unavailable|no_refetch|disabled|error)
    / reason / items(=kept) / fetched / elapsed_ms / path
    永不抛异常：任何意外都退化成「和调用前一样不可用」，构建照常继续。
    """
    t0 = time.time()
    now = now or datetime.now(timezone.utc)
    data = load_json(path)
    avail = availability(data, now=now)
    out = {'data': data, 'action': 'fresh', 'reason': avail['reason'], 'items': avail['items'],
           'kept': avail['items'], 'fetched': False, 'elapsed_ms': 0, 'path': path}
    if avail['ok']:
        return out
    if avail['reason'] not in REFETCH_REASONS:
        out['action'] = 'no_refetch'
        return out
    if not auto_fetch_enabled(allow_fetch):
        out['action'] = 'disabled'
        return out

    try:
        fresh = build(channel=channel, limit=limit, max_age_days=max_age_days,
                      timeout=timeout, mock=mock, quiet=quiet, via=via,
                      deadline=t0 + max(1, int(budget_seconds)))
    except Exception as e:  # noqa: BLE001 — 补抓失败绝不阻断构建
        out['action'] = 'error'
        out['reason'] = avail['reason']
        out['detail'] = f'{type(e).__name__}: {e}'[:200]
        out['elapsed_ms'] = round((time.time() - t0) * 1000, 1)
        print(f'  ⚠️ 方程式快讯构建期补抓失败（{out["detail"]}）→ 维持「今日未获取」兜底',
              file=sys.stderr)
        return out

    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(fresh, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f'  ⚠️ 方程式快讯补抓产物写入失败（{e}）→ 本次仍用内存结果渲染', file=sys.stderr)

    avail2 = availability(fresh, now=now)
    out.update({'data': fresh, 'reason': avail2['reason'], 'items': avail2['items'],
                'kept': avail2['items'], 'fetched': True,
                'elapsed_ms': round((time.time() - t0) * 1000, 1),
                'action': 'fetched' if avail2['ok'] else 'refetch_unavailable'})
    return out


# ---------------------------------------------------------------------------
# 渲染 · 网页（06 节，沿用 report.html 既有 CSS 类）
# ---------------------------------------------------------------------------
def _esc(t):
    return (str(t if t is not None else '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _fmt_time(iso):
    """UTC ISO → 「MM-DD HH:mm」（页面统一 UTC 口径）。"""
    dt = parse_time(iso)
    return dt.strftime('%m-%d %H:%M') if dt else ''


def _clip(text, limit):
    t = str(text or '').strip()
    return t if len(t) <= limit else t[:limit].rstrip() + '…'


def _coins_html(coins):
    if not coins:
        return ''
    chip = ' '.join(f'<span style="color:var(--violet);font-size:10.5px;">${_esc(c)}</span>'
                    for c in coins[:6])
    return f' <span style="font-size:10.5px;">{chip}</span>'


def _ai_line(item, limit=150):
    """AI 摘要行：优先结构化要点，其次 AI 总览首段；都没有就空。"""
    parts = item.get('ai_summary') or []
    text = '；'.join(parts) if parts else ''
    if not text:
        ov = str(item.get('ai_overview') or '').strip()
        text = ov.split('\n')[0] if ov else ''
    if not text:
        return ''
    return ('<div class="pub-quote" style="margin:4px 0 2px;font-size:11px;'
            'line-height:1.7;border-left:2px solid var(--violet);padding-left:8px;">'
            '<strong style="color:var(--violet);">🤖 AI 摘要</strong> '
            f'{_esc(_clip(text, limit))}'
            '<span style="color:var(--muted);">（AI 生成，仅供参考）</span></div>')


def render_web(data):
    """网页 06 节：仅展示当次条目与 AI 摘要；缺数据直接留空（不输出状态文案）。"""
    if not availability(data).get('ok'):
        return ''
    items = data.get('items') or []
    if not items:
        return ''
    head = (f'<div class="pub-sub">◆ 方程式新闻 · 最新快讯 · 频道 {_esc(data.get("channel") or "")}'
            f' · {_esc(data.get("generated_at") or "")}</div>')
    lis = []
    for it in items:
        date_chip = ''
        if it.get('published_at'):
            date_chip = (f'<strong style="color:var(--cyan);">[{_esc(_fmt_time(it["published_at"]))}]</strong> ')
        lis.append(f'    <li>{date_chip}{_esc(it.get("title") or "")}'
                   f'{_coins_html(it.get("coins") or [])}{_ai_line(it)}</li>')
    return ('<div class="pub-box">\n' + head + '\n'
            '<ul class="pixel-list" style="margin:6px 0 0;">\n' + '\n'.join(lis)
            + '\n  </ul>\n</div>')


# ---------------------------------------------------------------------------
# 渲染 · 微信（06 栏，纯内联样式，仅渲染实质内容）
# ---------------------------------------------------------------------------
def render_wechat(data, level='standard', cyan='#4fe5ff', text='#edf2ff',
                  muted='#9aa6c3', violet='#9673ff'):
    """微信 06 栏：level full / standard / compact，缺数据直接返回空串。"""
    if not availability(data).get('ok'):
        return ''
    items = data.get('items') or []
    if not items:
        return ''
    head = (f'<div style="color:{cyan};font-weight:700;font-size:{11 if level == "compact" else 12}px;'
            f'margin-bottom:4px;">◆ 方程式新闻 · 最新快讯 · '
            f'{_esc(data.get("channel") or "")}</div>')
    rows = [head]
    for it in items:
        tm = _fmt_time(it.get('published_at')) or ''
        chip = f'<span style="color:{cyan};">[{tm}]</span> ' if tm else ''
        coins = ''
        if it.get('coins'):
            coins = ' ' + ' '.join(f'<span style="color:{violet};">${_esc(c)}</span>'
                                   for c in it['coins'][:4])
        if level == 'compact':
            rows.append(f'<div style="color:{text};font-size:10px;line-height:1.6;">'
                        f'{chip}{_esc(_clip(it.get("title") or "", 30))}</div>')
            continue
        title = _clip(it.get('title') or '', 52 if level == 'full' else 40)
        rows.append(f'<div style="color:{text};font-size:10.5px;line-height:1.7;">'
                    f'{chip}{_esc(title)}{coins}</div>')
        if level == 'full':
            ai = _ai_wechat(it)
            if ai:
                rows.append(f'<div style="color:{muted};font-size:9.5px;line-height:1.6;">'
                            f'　🤖 AI 摘要：{ai}'
                            f'<span style="opacity:.75;">（AI 生成，仅供参考）</span></div>')
    return '\n'.join(rows)


def _ai_wechat(item, limit=110):
    parts = item.get('ai_summary') or []
    t = '；'.join(parts) if parts else ''
    if not t:
        ov = str(item.get('ai_overview') or '').strip()
        t = ov.split('\n')[0] if ov else ''
    return _esc(_clip(t, limit))


# ---------------------------------------------------------------------------
# 自检 / 人读摘要 / CLI
# ---------------------------------------------------------------------------
def self_test():
    """离线自检：归一 / 时效 / 四态 / 渲染纯净（零联网）。"""
    problems = []
    now = datetime(2026, 11, 23, 20, 0, tzinfo=timezone.utc)

    # 归一化：新→旧、无日期垫底、AI 摘要提取
    payload = {
        'items': [
            {'no': 2, 'time': '2026-11-23T10:00:00.000Z', 'channels': ['main'],
             'source': 'Binance EN', 'zh': '币安将上线 XYZ', 'en': 'Binance Will List XYZ',
             'coins': ['XYZ'], 'url': 'https://eqn.news/main/2',
             'source_url': 'https://www.binance.com/x',
             'ai': {'summary': {'type': 'listing', 'points': [{'zh': '11 月 20 日开放现货交易'}]},
                    'overview': None, 'notice': 'AI-generated'}},
            {'no': 1, 'time': '2026-11-22T10:00:00.000Z', 'channels': ['main', 'binance'],
             'source': 'EquationNews', 'zh': '', 'en': 'Older headline', 'coins': [],
             'url': 'https://eqn.news/main/1', 'source_url': None, 'ai': None},
            {'no': 3, 'time': None, 'channels': [], 'source': '', 'zh': '无日期条目',
             'en': '', 'coins': [], 'url': '', 'source_url': '', 'ai': None},
        ],
        'next_cursor': 'x',
    }
    items = normalize(payload, now=now)
    if [i['no'] for i in items] != [2, 1, 3]:
        problems.append('归一化排序异常（新→旧、无日期垫底）')
    if items[0]['title'] != '币安将上线 XYZ' or items[0]['coins'] != ['XYZ']:
        problems.append('归一化字段异常（title / coins）')
    if items[1]['title'] != 'Older headline':
        problems.append('中文缺失时应回退英文标题')
    if items[0]['ai_summary'] != ['11 月 20 日开放现货交易']:
        problems.append('AI 摘要提取异常')

    # 时效：超窗丢弃、无日期保留垫底
    kept, dropped = filter_stale(items, max_age_days=7, now=now)
    if len(kept) != 3 or dropped['stale_dropped'] != 0:
        problems.append('时效过滤（窗口内）异常')
    kept2, dropped2 = filter_stale(items, max_age_days=0, now=now)
    if [i['no'] for i in kept2] != [3] or dropped2['stale_dropped'] != 2:
        problems.append('时效过滤（全部超窗 / 无日期垫底）异常')

    # REST URL 拼装
    u = rest_url(channel='main', limit=5)
    if 'channel=main' not in u or 'limit=5' not in u:
        problems.append('REST URL 拼装异常')
    if 'channel=' in rest_url(channel='all', limit=5):
        problems.append('channel=all 不应拼进查询串')

    # 渲染：缺产物 → 直接留空，无任何状态/说明文字
    if render_web({}) != '' or render_wechat({}) != '':
        problems.append('缺产物时 06 节应直接留空')
    if availability({})['reason'] != 'no_file':
        problems.append('availability(no_file) 判定异常')
    if availability({'items': [], 'summary': {}, 'window': {}})['reason'] != 'no_items':
        problems.append('availability(no_items) 判定异常')
    if availability([])['reason'] != 'bad_type':
        problems.append('availability(bad_type) 判定异常')
    old = {'generated_at': '2020-01-01 00:00:00 UTC', 'fetch_date': '2020-01-01',
           'items': [{'title': 'x'}], 'summary': {'ok': 1, 'total': 1}}
    if availability(old, now=now)['reason'] != 'stale_snapshot':
        problems.append('availability(stale_snapshot) 判定异常')

    mock = build(mock=True, quiet=True)
    web = render_web(mock)
    wx_full = render_wechat(mock, level='full')
    wx_compact = render_wechat(mock, level='compact')
    if '方程式新闻' not in web or 'AI 摘要' not in web:
        problems.append('网页 06 节渲染缺板块头 / AI 摘要')
    if '<a href' in web or 'href' in web or 'http' in web:
        problems.append('网页 06 节不得出现链接（内容层隐藏外链）')
    if not wx_full or 'AI 摘要' not in wx_full:
        problems.append('微信 06 栏（full）缺内容')
    if not wx_compact or len(wx_compact) >= len(wx_full):
        problems.append('微信 06 栏缩略版未收敛')
    for banned in ('未获取', '不回填', '时效窗口', '口径', '抓取核验'):
        if banned in web or banned in wx_full:
            problems.append(f'06 节渲染出现状态文案：{banned}')
    if problems:
        print('❌ 自检未通过：')
        for p in problems:
            print('  -', p)
        return 1
    print(f'✅ 自检通过：归一 / 时效 / REST+MCP 双通道 / 可用性四态 / '
          f'06 节纯净渲染（{fmt_items(mock)} 条 · 含 AI {mock["summary"]["with_ai"]} 条）')
    return 0


def fmt_items(data):
    return len(data.get('items') or [])


def text_report(data):
    s = data.get('summary') or {}
    lines = [f'📰 方程式快讯: 频道 {data.get("channel")} · '
             f'入库 {s.get("items")} 条（含 AI 摘要 {s.get("with_ai")} 条）· '
             f'窗口 {((data.get("window") or {}).get("max_age_days"))} 天 · '
             f'{data.get("generated_at")}']
    if s.get('stale_dropped'):
        lines.append(f'  🚫 已拦截: 超窗 {s["stale_dropped"]} 条')
    if s.get('top_coins'):
        lines.append('  🔁 高频币种: ' + ' '.join(f'${c}' for c in s['top_coins']))
    for it in (data.get('items') or [])[:5]:
        d = it.get('published_date') or '日期未标注'
        lines.append(f'  - [{d}] {_clip(it.get("title") or "", 64)}')
    src = data.get('source') or {}
    if src.get('error'):
        lines.append(f'  ⚠️ 本轮失败: {src["error"]}')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(
        description='章鱼 AI·舆情004 — 方程式新闻（EquationNews）全球快讯抓取（06 栏数据源）')
    ap.add_argument('--json', default=DEFAULT_OUT, help='输出 JSON 路径（默认 eqn_news_data.json）')
    ap.add_argument('--channel', default=DEFAULT_CHANNEL,
                    help=f'频道 key（默认 {DEFAULT_CHANNEL}；all=全部频道，见 {CHANNELS_URL}）')
    ap.add_argument('--limit', type=int, default=LIMIT_DEFAULT,
                    help=f'单轮最多条数（1–{LIMIT_MAX}）；默认 {LIMIT_DEFAULT}')
    ap.add_argument('--days', type=int, default=MAX_AGE_DAYS_DEFAULT,
                    help=f'时效窗口（天），超出即丢弃；默认 {MAX_AGE_DAYS_DEFAULT}')
    ap.add_argument('--query', default='', help='关键词检索（近 30 天）')
    ap.add_argument('--coin', default='', help='按币种过滤，如 BTC')
    ap.add_argument('--via', choices=('rest', 'mcp'), default='rest',
                    help='取数通道：rest（默认，官方推荐轮询）| mcp（Streamable HTTP 协议）')
    ap.add_argument('--timeout', type=int, default=10, help='单次请求超时秒数')
    ap.add_argument('--mock', action='store_true', help='离线回放 tests/fixtures（本地/CI 联调）')
    ap.add_argument('--offline', action='store_true', help='断网兜底：读取已有 JSON，仅刷新时间戳')
    ap.add_argument('--text', action='store_true', help='打印人读摘要')
    ap.add_argument('--self-test', action='store_true', help='离线自检（不联网）')
    ap.add_argument('--quiet', action='store_true', help='不打印逐条日志')
    args = ap.parse_args()

    if args.self_test:
        sys.exit(self_test())

    now = datetime.now(timezone.utc)
    if args.offline:
        try:
            with open(args.json, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            print(f'⚠️ 离线模式无可用历史数据（{e}）→ 06 栏将留空', file=sys.stderr)
            data = {'channel': args.channel, 'items': [],
                    'summary': {'ok': 0, 'total': 1, 'items': 0},
                    'window': {'max_age_days': args.days, 'since': None},
                    'notes': ['离线模式且无历史数据：正文按「未获取」降级']}
        data.update({'generated_at': now.strftime('%Y-%m-%d %H:%M:%S UTC'),
                     'fetch_date': now.strftime('%Y-%m-%d'), 'mode': 'offline'})
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'🌐 离线模式: 已沿用上次结果并刷新时间戳 → {args.json}')
        return

    data = build(channel=args.channel, limit=args.limit, max_age_days=args.days,
                 timeout=args.timeout, mock=args.mock, quiet=args.quiet, via=args.via,
                 query=args.query, coin=args.coin)
    with open(args.json, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    s = data['summary']
    if not args.quiet:
        print(f'📰 方程式快讯已写入 {args.json} '
              f'（频道 {args.channel} · 入库 {s["items"]} 条 · 含 AI {s["with_ai"]} 条 · '
              f'窗口 {args.days} 天 · {data["generated_at"]}）')
        if s['items'] == 0:
            print('  ⚠️ 窗口内没有任何可核验快讯 → 06 栏将留空，不回填历史标题'
                  '（这是预期降级行为）', file=sys.stderr)
    if args.text:
        print(text_report(data))


if __name__ == '__main__':
    main()
