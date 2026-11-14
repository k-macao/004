#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未获取源的多方案备选提取（救援层）。

前面三层（RSS 优先 → 专用适配器/通用摘录 → `community_plans` 逐源方案链）都没拿到内容的源，
在这里**逐源再走一条备选链**，第一个产出合格证据的方案胜出：

1. `plan_rest`     —— 该源方案链里因 `PLAN_MAX_STEPS` 没轮到的剩余步骤（不重复已试过的步骤）；
2. `reader_excerpt`—— 公开阅读器代理（r.jina.ai）渲染该源页面后的正文摘录，针对 JS 空壳页；
3. `gnews_site_carrier` —— Google News `site:` 检索载体；
4. `bing_news_carrier`  —— Bing 新闻 RSS `site:` 检索载体（与 Google 互为备份）；
5. `gnews_name_carrier` —— Google News 按社区名称检索载体。

口径与方案层完全一致，绝不放宽：
- 条目必须命中财经主题词；带时间的条目必须落在时间窗内；链接必须是 http(s)；
- 备选方案的证据一律是 `page_excerpt`（未核验为评论），检索载体如实标注为聚合检索；
- 全部失败仍然是「未获取」，不编造、不回填；
- 有独立的请求数/墙钟预算，单步、单源、整层异常都不打断整轮采集。

本执行环境不能直连目标站点，备选端点**未做在线实测**（`needs_check` / `carrier`），
逻辑由 `tests/test_community_rescue.py` 用模拟报文覆盖。
"""
import os
import re
import sys
import urllib.parse

import community_plans as p

RESCUE_REQUEST_BUDGET = int(os.environ.get('COMMUNITY_RESCUE_REQUESTS', '150'))
RESCUE_WALL_BUDGET = float(os.environ.get('COMMUNITY_RESCUE_BUDGET', '300'))
RESCUE_TIMEOUT = float(os.environ.get('COMMUNITY_RESCUE_TIMEOUT', '8'))
READER_BASE = os.environ.get('COMMUNITY_READER_BASE', 'https://r.jina.ai/').strip()

STRATEGY_READER = 'reader_excerpt'
STRATEGY_GNEWS_SITE = 'gnews_site_carrier'
STRATEGY_BING = 'bing_news_carrier'
STRATEGY_GNEWS_NAME = 'gnews_name_carrier'
GENERIC_STRATEGIES = (STRATEGY_READER, STRATEGY_GNEWS_SITE, STRATEGY_BING, STRATEGY_GNEWS_NAME)

_NAME_TERMS = {'zh-CN': '股市', 'zh-TW': '股市', 'en': 'stocks', 'ja': '株',
               'ko': '주식', 'de': 'Aktien'}
_BING_MARKETS = {'zh-CN': 'zh-CN', 'zh-TW': 'zh-HK', 'en': 'en-US', 'ja': 'ja-JP',
                 'ko': 'ko-KR', 'de': 'de-DE'}
# 阅读器代理拿回来的若是风控/登录页，整页作废（不从拦截页里捞「正文」）。
_BLOCK_MARKERS = ('just a moment', 'access denied', 'attention required', 'captcha',
                  'verify you are human', 'enable javascript', '安全验证', '访问验证',
                  '请完成验证', '滑动验证', '人机验证')


def enabled():
    return str(os.environ.get('COMMUNITY_RESCUE', '1')).strip().lower() not in (
        '0', 'false', 'no', 'off')


def source_lang(comm):
    """该源的检索语言：优先沿用方案里载体步骤的语言，否则按社区类型推断。"""
    spec = p.plan_for(comm) or {}
    for step_record in spec.get('steps', ()):
        if step_record['kind'] == p.STEP_CARRIER and step_record.get('lang'):
            return step_record['lang']
    ctype = str(comm.get('ctype') or '')
    if '日文' in ctype:
        return 'ja'
    if '韩文' in ctype:
        return 'ko'
    if '德文' in ctype:
        return 'de'
    if any(x in ctype for x in ('英文', '数字资产', '多语言')):
        return 'en'
    if any(x in ctype for x in ('繁体', '粤语', '香港')):
        return 'zh-TW'
    return 'zh-CN'


def site_target(comm):
    """`site:` 检索目标：主机名；Reddit 这类多社区同域的站点带上社区路径。"""
    parsed = urllib.parse.urlparse(str(comm.get('url') or ''))
    host = (parsed.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    if not host:
        return ''
    match = re.match(r'/(r/[A-Za-z0-9_]+)', parsed.path or '')
    return f'{host}/{match.group(1)}' if match else host


def search_name(comm):
    name = str(comm.get('name') or '').split(' · ')[0]
    return re.sub(r'\s+', ' ', re.sub(r'[()（）]', ' ', name)).strip()


def bing_news_url(query, lang='zh-CN'):
    market = _BING_MARKETS.get(lang, 'zh-CN')
    return ('https://www.bing.com/news/search?q=' + urllib.parse.quote(str(query))
            + f'&format=rss&setmkt={market}')


def reader_url(url):
    return READER_BASE.rstrip('/') + '/' + str(url or '').strip()


def generic_steps(comm):
    """与站点无关、按目录字段现拼的备选步骤（每源最多 4 条）。"""
    lang = source_lang(comm)
    target = site_target(comm)
    name = search_name(comm)
    url = str(comm.get('url') or '').strip()
    steps = []
    if url.lower().startswith(('http://', 'https://')) and READER_BASE:
        steps.append(p.step(
            p.STEP_HTML, (reader_url(url),), verified='needs_check', strategy=STRATEGY_READER,
            note='公开阅读器代理渲染后的页面正文，针对 JS 渲染空壳页',
            scope='第三方阅读器代理渲染的公开页面摘录（未核验为评论），命中财经主题词方入库'))
    if target:
        steps.append(p.step(
            p.STEP_CARRIER, (f'gnews:site:{target}',), verified='carrier', lang=lang,
            strategy=STRATEGY_GNEWS_SITE, note='Google News 按站点域名检索',
            scope='Google News 站点检索载体（聚合检索，不是站点原生源，也不代表该社区立场）'))
        steps.append(p.step(
            p.STEP_CARRIER, (bing_news_url(f'site:{target}', lang),), verified='carrier',
            lang=lang, strategy=STRATEGY_BING, note='Bing 新闻 RSS 按站点域名检索',
            scope='Bing 新闻检索载体（聚合检索，不是站点原生源，也不代表该社区立场）'))
    if name:
        query = f'"{name}" {_NAME_TERMS.get(lang, "股市")}'
        steps.append(p.step(
            p.STEP_CARRIER, (f'gnews:{query}',), verified='carrier', lang=lang,
            strategy=STRATEGY_GNEWS_NAME, note='Google News 按社区名称检索',
            scope='Google News 名称检索载体（聚合检索，不是站点原生源，也不代表该社区立场）'))
    return steps


def _pending_plan_steps(comm, plan_attempt):
    """方案链里还没真正执行过的步骤（适配器与缺配置的步骤不在救援范围）。"""
    spec = p.plan_for(comm)
    if not spec:
        return []
    done = {}
    for record in (plan_attempt or {}).get('plan_steps') or ():
        if isinstance(record, dict) and 'index' in record:
            done[record['index']] = record.get('status')
    pending = []
    for index, step_record in enumerate(spec['steps']):
        if step_record['kind'] == p.STEP_ADAPTER:
            continue
        if step_record.get('needs_config') and not p._config_present(step_record['needs_config']):
            continue
        # 未登记（方案层没跑/预算耗尽）或因步数上限没轮到 → 待试；其余状态说明已经试过。
        if done.get(index, 'step_budget') in ('step_budget', 'skipped', 'budget_exhausted'):
            pending.append(step_record)
    return pending


def catalog(communities=None):
    """逐源备选方案清单（供文档与自检）：[{key, name, schemes: [strategy...]}]。"""
    import community_data as cd
    rows = []
    for comm in communities or cd.COMMUNITIES:
        planned = [s['strategy'] for s in (p.plan_for(comm) or {}).get('steps', ())
                   if s['kind'] != p.STEP_ADAPTER]
        generic = [s['strategy'] for s in generic_steps(comm)]
        rows.append({'key': comm.get('key'), 'name': comm.get('name'),
                     'plan_steps': planned, 'generic': generic})
    return rows


_MD_IMAGE = re.compile(r'!\[[^\]]*\]\([^)]*\)')
_MD_LINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')
_MD_MARKS = re.compile(r'^[\s#>*\-+|_`=~]+|[\s|*_`]+$')


def _reader_fragments(body):
    """阅读器代理返回的 Markdown → (title, [正文段落])；HTML/拦截页返回 ('', [])。"""
    text = body.decode('utf-8', 'replace') if isinstance(body, bytes) else str(body or '')
    head = text[:400].lower()
    if '<html' in head or '<!doctype' in head:
        return '', []   # 代理没渲染成功，原样吐回了 HTML：不当正文用
    title = ''
    match = re.search(r'^Title:\s*(.+)$', text, re.M)
    if match:
        title = match.group(1).strip()
    marker = text.find('Markdown Content:')
    content = text[marker + len('Markdown Content:'):] if marker >= 0 else text
    probe = (title + ' ' + content[:600]).lower()
    if any(mark in probe for mark in _BLOCK_MARKERS):
        return '', []
    fragments, seen = [], set()
    for line in content.splitlines():
        line = _MD_LINK.sub(r'\1', _MD_IMAGE.sub(' ', line))
        line = re.sub(r'https?://\S+', ' ', line)
        line = re.sub(r'\s+', ' ', _MD_MARKS.sub('', line)).strip()
        if len(line) < 12 or line in seen:
            continue
        seen.add(line)
        fragments.append(line)
    return title, fragments


def _run_reader_step(step_record, urls, session, timeout, now, max_age_days, budget, delay,
                     source_url=''):
    import community_spider as common
    for url in urls:
        got = p._fetch(session, url, timeout, {'Accept': 'text/plain'}, budget, delay)
        if got is None:
            return [], 'budget_exhausted', url
        status, body, _final = got
        if not body or not (200 <= status < 300):
            continue
        title, fragments = _reader_fragments(body)
        scored = sorted(fragments, key=lambda text: -common.score_fragment(text, title))
        candidates = []
        for text in scored[:p.MAX_ITEMS_PER_STEP]:
            item = p._candidate(text[:common.MAX_SNIPPET], title, p._safe_url(source_url), None,
                                now, max_age_days, step_record)
            if item:
                item['http_status'] = status
                item['response_bytes'] = len(body)
                candidates.append(item)
        if candidates:
            return candidates, 'ok', url
    return [], 'no_valid_items', urls[0] if urls else ''


def _run_step(step_record, comm, session, timeout, now, max_age_days, budget, tried):
    urls = []
    for endpoint in step_record['endpoints']:
        urls.extend(p.expand_endpoint(endpoint, step_record.get('lang')))
    urls = [u for u in urls if u and u not in tried]
    if not urls:
        return [], 'already_tried', ''
    tried.update(urls)
    delay = budget.step_delay
    if step_record['strategy'] == STRATEGY_READER:
        return _run_reader_step(step_record, urls, session, timeout, now, max_age_days, budget,
                                delay, source_url=comm.get('url'))
    runner = p.STEP_RUNNERS.get(step_record['kind'])
    if runner is None:
        return [], 'unsupported', urls[0]
    return runner(step_record, urls, session, timeout, now, max_age_days, budget, delay)


def rescue_source(comm, session, timeout=RESCUE_TIMEOUT, now=None, max_age_days=None,
                  budget=None, plan_attempt=None, plan_rest=True):
    """对一个未获取源依次尝试全部备选方案，返回 {comments, attempt}（与 run_plan 同构）。"""
    import community_comments as cc
    now = cc._now_utc(now)
    max_age_days = cc.COMMENT_MAX_AGE_DAYS if max_age_days is None else max_age_days
    budget = budget or p.PlanBudget(requests=RESCUE_REQUEST_BUDGET, seconds=RESCUE_WALL_BUDGET)
    attempt = {'strategy': 'rescue_chain', 'status': 'unavailable', 'matched_comments': 0,
               'rescue_steps': [], 'endpoint': ''}
    tried = {str(r.get('endpoint')) for r in (plan_attempt or {}).get('plan_steps') or ()
             if isinstance(r, dict) and r.get('endpoint')
             and r.get('status') not in ('step_budget', 'skipped', 'budget_exhausted')}
    chain = [('plan_rest', s) for s in _pending_plan_steps(comm, plan_attempt)] if plan_rest else []
    chain += [('generic', s) for s in generic_steps(comm)]
    for origin, step_record in chain:
        record = {'origin': origin, 'kind': step_record['kind'],
                  'strategy': step_record['strategy'], 'verified': step_record['verified']}
        try:
            candidates, status, endpoint = _run_step(
                step_record, comm, session, timeout, now, max_age_days, budget, tried)
        except Exception as exc:  # 单步异常只影响这一步
            candidates, status, endpoint = [], f'error_{type(exc).__name__}', ''
        record.update({'status': status, 'endpoint': endpoint, 'candidates': len(candidates)})
        attempt['rescue_steps'].append(record)
        if status == 'budget_exhausted':
            break
        if not candidates:
            continue
        comments, matched = p._finish(candidates)
        if comments:
            attempt.update({'status': 'ok', 'matched_comments': matched,
                            'strategy': step_record['strategy'], 'endpoint': endpoint,
                            'content_kind': step_record['content_kind'],
                            'verified': step_record['verified'], 'step_kind': step_record['kind'],
                            'scope': step_record.get('scope', ''), 'origin': origin})
            return {'comments': comments, 'attempt': attempt}
    if budget.exhausted:
        attempt['status'] = 'budget_exhausted'
    return {'comments': [], 'attempt': attempt}


def run_rescue(communities, session, timeout=RESCUE_TIMEOUT, now=None, max_age_days=None,
               budget=None, plan_attempts=None, verbose=False, plan_rest=True):
    """逐源救援；返回 (outcomes, stats)。单源失败不影响其它源。"""
    budget = budget or p.PlanBudget(requests=RESCUE_REQUEST_BUDGET, seconds=RESCUE_WALL_BUDGET)
    plan_attempts = plan_attempts or {}
    outcomes = {}
    for comm in communities:
        key = comm.get('key')
        if not key:
            continue
        if not budget.allow():
            outcomes[key] = {'comments': [], 'attempt': {
                'strategy': 'rescue_chain', 'status': 'budget_exhausted',
                'matched_comments': 0, 'rescue_steps': []}}
            continue
        try:
            outcome = rescue_source(comm, session, timeout=timeout, now=now,
                                    max_age_days=max_age_days, budget=budget,
                                    plan_attempt=plan_attempts.get(key), plan_rest=plan_rest)
        except Exception as exc:
            outcome = {'comments': [], 'attempt': {
                'strategy': 'rescue_chain', 'status': f'error_{type(exc).__name__}',
                'matched_comments': 0, 'rescue_steps': []}}
        outcomes[key] = outcome
        if verbose:
            attempt = outcome['attempt']
            flag = '🛟' if outcome['comments'] else '⚠️'
            print(f'  {flag} {comm.get("id", "")} {comm.get("name", key)} 备选提取 → '
                  f'{attempt.get("status")}（{attempt.get("strategy", "")} · '
                  f'试 {len(attempt.get("rescue_steps") or [])} 套）', file=sys.stderr)
    stats = {
        'attempted_sources': len(outcomes),
        'rescued_sources': sum(bool(o['comments']) for o in outcomes.values()),
        'rescued_keys': [k for k, o in outcomes.items() if o['comments']],
        'matched_comments': sum(o['attempt'].get('matched_comments', 0) for o in outcomes.values()),
        'requests_count': budget.used_requests,
        'budget': budget.to_dict(),
        'attempts': {k: v['attempt'] for k, v in outcomes.items()},
    }
    return outcomes, stats


def _self_test():
    import community_data as cd
    rows = catalog()
    assert len(rows) == len(cd.COMMUNITIES)
    thin = [r['key'] for r in rows if len(r['plan_steps']) + len(r['generic']) < 4]
    assert not thin, f'备选方案不足 4 套的源: {thin}'
    print(f'✅ {len(rows)} 源均有 ≥4 套可执行方案（方案链 + 备选链），'
          f'最少 {min(len(r["plan_steps"]) + len(r["generic"]) for r in rows)} 套')
    return 0


if __name__ == '__main__':
    sys.exit(_self_test())
