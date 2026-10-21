#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 宏观/财经快讯动态抓取 (macro_data.py)
=============================================================

**为什么新增这一层**（2026-09-16 核查结论）：微信推送 `02 / 全球经济与财经动态`
此前不接任何数据源，正文（IMF 2026-07-08 WEO、7 月 29 日 FOMC、8 月 12 日 CPI、
南向 7 月净买入 628.69 亿、各行恒指目标价…）是写死在 tools/wechat_push.py 里的
字面量，每次构建原样重播 —— 结果是「生成时间写当天、正文停在 8 月 12 日」的旧内容。
本层把它改成**真正的数据驱动**：快讯按类别抓取 → macro_data.json → 02 栏逐条渲染，
每条自带发布日期；抓不到就明确写「今日未获取」，绝不再复用历史叙事。

抓取源（全部免密钥，按能力互补，任一失败不影响其余）：
  1. Google News RSS 检索   中文分主题检索（宏观/美联储/港股/大宗/机构观点）
  2. Google News RSS 检索   英文全球宏观（IMF / World Bank / 通胀预测）
  3. 美联储官方 Press Release RSS (federalreserve.gov)  — 利率决议与官方公告第一手
  4. 东方财富 search-api     中文财经快讯检索（JSONP 外壳剥离，与 sentiment_adapters 同源）

分类口径：与 02 栏的五个小节一一对应（macro / fed / hk / commodities / bank_views），
关键词表复用 sentiment_match.py 的日报主题词表语义，避免「推送与网页两套口径」。

时效硬约束（本层的核心价值，不做则退回旧问题）：
  • 只保留 `--days`（默认 7 天）窗口内、且发布日期可解析的条目；
  • 超窗/无日期的条目计数进 summary.stale_dropped / undated_dropped，不进入正文；
  • 某类别窗口内为空 → 该小节渲染为「窗口内无匹配快讯」，由调用方显示为提示而非旧文。

设计约束（与 market_data.py / community_data.py / sentiment_factors.py 一致）：
  • 纯标准库（urllib + xml.etree），GitHub Actions ubuntu-latest 开箱即用；
  • 单源失败不阻断：失败原因写入 sources[].error，09:00 定时推送永不因此中断；
  • 对外展示不带来源名（与舆情层脱敏策略一致），来源仅存 JSON 内部字段供排查；
  • 构建产物 macro_data.json 不入库（见 .gitignore）。

用法:
  python3 macro_data.py                        # 联网抓取 → macro_data.json
  python3 macro_data.py --mock                 # 离线回放 tests/fixtures（本地联调/单测）
  python3 macro_data.py --offline              # 断网兜底：沿用上次结果，仅刷新时间戳
  python3 macro_data.py --days 3 --limit 4     # 收紧时效窗口与每类条数
  python3 macro_data.py --json /tmp/m.json --text   # 打印人读摘要（供 CI Step Summary）
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(REPO_ROOT, 'macro_data.json')
FIXTURE_DIR = os.path.join(REPO_ROOT, 'tests', 'fixtures')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

MAX_AGE_DAYS_DEFAULT = 7
LIMIT_PER_CAT_DEFAULT = 4

# ---------------------------------------------------------------------------
# 02 栏五个小节 → 关键词表。any 命中即候选，require 需同时命中，exclude 一票否决。
# boost 里的权重翻倍：一条「IMF 下调增长预期，关税与航运风险」若按纯命中数会被
# 大宗商品小节抢走（实测 2026-09-16 复现），加权后回到宏观。
# ---------------------------------------------------------------------------
CATEGORIES = [
    {'key': 'bank_views', 'label': '主要国际与中资大行对恒指目标价预测',
     'any': ['目标价', '目标点位', '目标区间', '上调评级', '下调评级', '首次覆盖',
             '大行评级', '研报看好', '维持超配', '上调目标'],
     'require': ['恒指', '恒生', '港股', '国企指数', '恒生科技'],
     'exclude': ['A股', '纳指', '标普'],
     'boost': ['目标价', '目标区间', '上调评级', '下调评级']},
    {'key': 'fed', 'label': '美联储利率路径与离岸流动性',
     'any': ['美联储', 'fomc', '鲍威尔', '降息', '加息', '议息', '点阵图', '美债收益率',
             '杰克逊霍尔', '量化紧缩', '美元指数', '联邦基金'],
     'require': [], 'exclude': [],
     'boost': ['美联储', 'fomc', '鲍威尔', '降息', '议息']},
    {'key': 'hk', 'label': '港股市场 — 恒指与南向资金',
     'any': ['恒生指数', '恒指', '港股', '南向资金', '港股通', '恒生科技', 'AH 溢价',
             'Hibor', '香港交易所', '科网股'],
     'require': [], 'exclude': [],
     'boost': ['恒指', '恒生指数', '南向资金', '港股通']},
    {'key': 'commodities', 'label': '大宗商品与全球供应链风险矩阵',
     'any': ['原油', 'opec', '布伦特', 'wti', '黄金', '白银', '铜价', '铝', '碳酸锂',
             '天然气', '霍尔木兹', '供应链', '航运', '关税'],
     'require': [], 'exclude': [],
     'boost': ['原油', '黄金', 'opec', '铜价', '碳酸锂']},
    {'key': 'macro', 'label': '宏观 — 全球经济增速与主要央行',
     'any': ['imf', '国际货币基金组织', '基金组织', '世界经济展望', '世界银行', '经合组织',
             'gdp', 'cpi', 'ppi', '通胀', '通缩', '衰退', '央行', '财政政策', '刺激',
             '就业', '非农', 'pmi', '地缘', '制裁', '增长预期', '增速', '经济前景'],
     'require': [], 'exclude': [],
     'boost': ['imf', '国际货币基金组织', '世界经济展望', 'gdp', 'cpi', '通胀', '非农',
               '增长预期', '增速', '央行']},
]

CATEGORY_KEYS = [c['key'] for c in CATEGORIES]

# ---------------------------------------------------------------------------
# 抓取源注册表。kind: gnews | rss | em_search
# ---------------------------------------------------------------------------
SOURCES = [
    {'id': 'GN_ZH_MACRO', 'kind': 'gnews', 'lang': 'zh',
     'query': 'IMF OR 世界经济展望 OR 全球经济增长 OR 通胀 OR 主要央行'},
    {'id': 'GN_ZH_FED', 'kind': 'gnews', 'lang': 'zh',
     'query': '美联储 OR FOMC OR 鲍威尔 OR 降息 OR 美债收益率'},
    {'id': 'GN_ZH_HK', 'kind': 'gnews', 'lang': 'zh',
     'query': '恒生指数 OR 港股 OR 南向资金 OR 恒生科技指数'},
    {'id': 'GN_ZH_COMM', 'kind': 'gnews', 'lang': 'zh',
     'query': '原油 OR 黄金 OR OPEC OR 铜价 OR 霍尔木兹'},
    {'id': 'GN_ZH_BANK', 'kind': 'gnews', 'lang': 'zh',
     'query': '大行 恒指 目标价 OR 港股 上调评级'},
    {'id': 'GN_EN_MACRO', 'kind': 'gnews', 'lang': 'en',
     'query': 'IMF World Bank global growth forecast inflation central bank'},
    {'id': 'FED_PRESS', 'kind': 'rss', 'lang': 'en',
     'url': 'https://www.federalreserve.gov/feeds/press_all.xml'},
    {'id': 'EM_SEARCH', 'kind': 'em_search', 'lang': 'zh',
     'queries': ['恒生指数', '美联储', '黄金 原油', 'IMF 世界经济展望']},
]

SOURCE_LABELS = {  # 仅内部字段/日志用；正文不渲染来源名（与舆情层脱敏一致）
    'GN_ZH_MACRO': 'Google News（中文·全球宏观）',
    'GN_ZH_FED': 'Google News（中文·美联储）',
    'GN_ZH_HK': 'Google News（中文·港股）',
    'GN_ZH_COMM': 'Google News（中文·大宗与供应链）',
    'GN_ZH_BANK': 'Google News（中文·大行观点）',
    'GN_EN_MACRO': 'Google News（英文·全球宏观）',
    'FED_PRESS': '美联储官方新闻稿 RSS',
    'EM_SEARCH': '东方财富财经快讯检索',
}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def http_get(url, timeout=10, headers=None):
    """GET → (text, latency_ms)。失败抛异常，由调用方归类到 sources[].error。"""
    hdrs = {'User-Agent': UA, 'Accept': '*/*'}
    hdrs.update(headers or {})
    t0 = time.time()
    with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs),
                                timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode('utf-8', errors='replace'), round((time.time() - t0) * 1000, 1)


def gnews_url(query, lang='zh'):
    """Google News RSS 检索地址（zh 用简体中文站点，en 用美国英文站点）。"""
    hl, gl, ceid = (('zh-CN', 'CN', 'CN:zh-Hans') if lang == 'zh'
                    else ('en-US', 'US', 'US:en'))
    q = urllib.parse.quote(query)
    return (f'https://news.google.com/rss/search?q={q}'
            f'&hl={hl}&gl={gl}&ceid={ceid}&when=3d')


def em_search_url(keyword, page_size=20):
    """东财 search-api JSONP 地址（与 sentiment_adapters._em_news_payload 同参数结构）。"""
    inner = {'uid': '', 'keyword': keyword, 'type': ['cmsArticleWebOld'], 'client': 'web',
             'clientType': 'web', 'clientVersion': 'curr',
             'param': {'cmsArticleWebOld': {'searchScope': 'default', 'sort': 'time',
                                            'pageIndex': 1, 'pageSize': page_size,
                                            'preTag': '', 'postTag': ''}}}
    params = {'cb': 'jQuery_macro', 'param': json.dumps(inner, ensure_ascii=False)}
    return 'https://search-api-web.eastmoney.com/search/jsonp?' + urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# 解析（纯函数，便于离线单测）
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r'<[^>]+>')
_DATE_RE = re.compile(r'(20\d{2})-(\d{2})-(\d{2})')
# 东财 search-api 用 <em> 包裹命中词，若按「标签→空格」清洗，标题会被切成
# "港股 通 净买入" 这种碎词（2026-09-16 单测抓到）。行内标签必须直接删除。
_INLINE_TAG_RE = re.compile(r'</?(?:em|strong|b|i|u|small|mark|font|sup|sub|a|span)\b[^>]*/?>', re.I)
_BLOCK_TAG_RE = re.compile(r'</?(?:p|br|div|li|ul|ol|tr|td|h[1-6])\b[^>]*/?>', re.I)


def strip_html(text):
    """去标签 + 实体 + 折叠空白（行内标签删掉不留空格，块级标签换成空格）。"""
    if not text:
        return ''
    s = _BLOCK_TAG_RE.sub(' ', str(text))
    s = _INLINE_TAG_RE.sub('', s)
    s = _TAG_RE.sub(' ', s)
    for a, b in (('&nbsp;', ' '), ('&amp;', '&'), ('&quot;', '"'),
                 ('&#39;', "'"), ('&lt;', '<'), ('&gt;', '>')):
        s = s.replace(a, b)
    return re.sub(r'\s+', ' ', s).strip()


def to_utc(dt_text):
    """RFC822 / ISO8601 / 裸日期 → aware datetime(UTC)；解析不出返回 None。"""
    if not dt_text:
        return None
    s = str(dt_text).strip()
    try:
        d = parsedate_to_datetime(s)
        if d:
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    m = _DATE_RE.search(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _findtext(node, tag):
    """RSS 里同义标签很多（pubDate/pubdate/link/a href），逐个尝试。"""
    for name in (tag, tag.lower()):
        el = node.find(name)
        if el is not None:
            if el.text and el.text.strip():
                return el.text.strip()
            href = el.get('href')
            if href:
                return href
    return ''


def parse_rss(text):
    """RSS 2.0 文本 → 原始条目列表 [{title,url,published_raw,summary,publisher}]。"""
    if not text:
        return []
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError:
        return []
    out = []
    for item in root.iter('item'):
        title = strip_html(_findtext(item, 'title'))
        if not title:
            continue
        url = _findtext(item, 'link') or _findtext(item, 'guid')
        publisher = ''
        for tag in ('source',):
            p = strip_html(_findtext(item, tag))
            if p:
                publisher = p
                break
        out.append({'title': title,
                    'url': url if url.startswith('http') else '',
                    'published_raw': _findtext(item, 'pubDate'),
                    'summary': strip_html(_findtext(item, 'description'))[:400],
                    'publisher': publisher})
    return out


def split_gnews_title(title):
    """Google News 标题形如「正文标题 — 来源名」→ (标题, 来源名)。"""
    for sep in (' — ', ' – ', ' - '):
        if sep in title:
            head, tail = title.rsplit(sep, 1)
            if head.strip() and len(tail) <= 40:
                return head.strip(), tail.strip()
    return title, ''


def parse_em_search(payload):
    """东财 search-api JSON（已剥壳）→ 原始条目列表。"""
    rows = ((payload or {}).get('result') or {}).get('cmsArticleWebOld') or []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        title = strip_html(r.get('title'))
        if not title:
            continue
        out.append({'title': title,
                    'url': r.get('url') or '',
                    'published_raw': r.get('date') or '',
                    'summary': strip_html(r.get('content'))[:400],
                    'publisher': r.get('mediaName') or ''})
    return out


def strip_jsonp(text):
    """剥离 `jQuery_xxx({...});` 外壳：取第一个 `{` 到最后一个 `}`。

    不能只 rstrip `);` —— 那样会把回调左括号留在串首，json.loads 直接报
    "Expecting value: line 1 column 1"（东财 search-api 每次都挂在这里）。
    """
    t = text or ''
    i, j = t.find('{'), t.rfind('}')
    if i < 0 or j <= i:
        raise ValueError('JSONP 报文中找不到 JSON 主体')
    return json.loads(t[i:j + 1])


# ---------------------------------------------------------------------------
# 归一 / 分类 / 时效过滤（纯函数）
# ---------------------------------------------------------------------------
def normalize(raw_items, source_id, now=None):
    """原始条目 → 统一条目（补 published_date / age_days / lang / source）。"""
    now = now or datetime.now(timezone.utc)
    out = []
    for r in raw_items or []:
        title = (r.get('title') or '').strip()
        if not title:
            continue
        pub = to_utc(r.get('published_raw'))
        # 「标题 — 来源名」是聚合器通用写法，所有源统一剥离后再入库，
        # 否则同一稿件在不同源下标题不同，dedup 会漏（曾导致推送里同条新闻出现两次）。
        dt, tail = split_gnews_title(title)
        out.append({
            'title': dt,
            'publisher': r.get('publisher') or tail or '',
            'url': r.get('url') or '',
            'snippet': r.get('summary') or '',
            'source': source_id,
            'published_at': pub.isoformat() if pub else None,
            'published_date': pub.strftime('%Y-%m-%d') if pub else None,
            'age_days': round((now - pub).total_seconds() / 86400, 2) if pub else None,
        })
    return out


def classify(item):
    """返回 (category, matched_keywords)；无命中 → ('misc', [])。

    归类规则: 加权命中数最高者胜（boost 词 ×2），同分按 CATEGORIES 声明顺序优先。
    纯按声明顺序会让「IMF + 关税」这类宏观头条被大宗商品小节抢走，纯按命中数则
    大行目标价小节会被泛化的港股词吞掉，两者都不符合日报的分栏语义。
    """
    hay = ((item.get('title') or '') + ' ' + (item.get('snippet') or '')).lower()
    best_score, best_key, best_hits = None, 'misc', []
    for idx, cat in enumerate(CATEGORIES):
        if any(k.lower() in hay for k in cat['exclude']):
            continue
        hits = [k for k in cat['any'] if k in hay]
        if not hits:
            continue
        if cat['require'] and not any(k.lower() in hay for k in cat['require']):
            continue
        score = (sum(2 if k in cat['boost'] else 1 for k in hits), -idx)
        if best_score is None or score > best_score:
            best_score, best_key, best_hits = score, cat['key'], hits
    return best_key, best_hits


def filter_stale(items, max_age_days=MAX_AGE_DAYS_DEFAULT, now=None):
    """时效硬过滤：无日期或超窗 → 丢弃并计数（旧内容进不了正文的关键一步）。"""
    now = now or datetime.now(timezone.utc)
    kept, undated, stale = [], 0, 0
    for it in items:
        if not it.get('published_date'):
            undated += 1
            continue
        age = it.get('age_days')
        if age is None:
            pub = to_utc(it.get('published_at'))
            age = (now - pub).total_seconds() / 86400 if pub else None
        if age is None or age > max_age_days or age < -1:
            stale += 1
            continue
        it['age_days'] = round(float(age), 2)
        kept.append(it)
    return kept, {'undated_dropped': undated, 'stale_dropped': stale}


def dedup(items):
    """标题归一化去重（转载/多源同稿只留最新一条）。"""
    seen, out = {}, []
    for it in items:
        k = re.sub(r'[\s\W_]+', '', (it.get('title') or '').lower())[:60]
        if not k:
            continue
        old = seen.get(k)
        if old is None:
            seen[k] = it
            out.append(it)
        elif (it.get('age_days') or 99) < (old.get('age_days') or 99):
            out[out.index(old)] = it
            seen[k] = it
    return out


def rank(items, limit=LIMIT_PER_CAT_DEFAULT):
    """分类 → 组内按「关键词命中数 + 新鲜度」排序取前 limit，附带 score。"""
    buckets = {key: [] for key in CATEGORY_KEYS}
    misc = []
    for it in items:
        cat, hits = classify(it)
        it = dict(it)
        it['category'] = cat
        it['matched'] = hits
        if cat == 'misc':
            misc.append(it)
            continue
        age = it.get('age_days') if it.get('age_days') is not None else 99
        it['score'] = round(len(hits) * 2 + max(0.0, (10 - age)) / 2, 2)
        buckets[cat].append(it)
    for cat in buckets:
        buckets[cat] = sorted(buckets[cat], key=lambda x: (-x['score'], x['age_days'] or 99))[:limit]
    return buckets, misc


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------
def fetch_source(src, timeout=10, fixture_dir=None, now=None):
    """单个源 → (raw_items, meta)。失败抛异常，由 main() 归类为 sources[].error。"""
    sid = src['id']
    t0 = time.time()
    if fixture_dir:                       # --mock：本地回放，保证离线可测
        path = os.path.join(fixture_dir, f'MACRO_{sid}.json')
        if not os.path.exists(path):
            path = os.path.join(fixture_dir, 'MACRO_MIX.json')
        with open(path, encoding='utf-8') as f:
            fixture = json.load(f)
        raws = fixture.get(src.get('fixture_key') or 'items') or []
        raws = _redate_for_demo(raws, now or datetime.now(timezone.utc))
        return raws, {'fixture': os.path.basename(path),
                      'latency_ms': round((time.time() - t0) * 1000, 1)}

    if src['kind'] == 'gnews':
        text, ms = http_get(gnews_url(src['query'], src.get('lang', 'zh')), timeout)
        # 标题/来源剥离与入库归一统一在 normalize() 里做，避免重复处理
        return parse_rss(text), {'latency_ms': ms}
    if src['kind'] == 'rss':
        text, ms = http_get(src['url'], timeout)
        return parse_rss(text), {'latency_ms': ms}
    if src['kind'] == 'em_search':
        merged, lat = [], 0.0
        for kw in src.get('queries') or []:
            try:
                text, ms = http_get(em_search_url(kw), timeout,
                                     headers={'Referer': 'https://so.eastmoney.com/'})
                merged.extend(parse_em_search(strip_jsonp(text)))
                lat += ms
            except Exception as e:  # noqa: BLE001 — 单关键词失败，其余关键词继续
                if not merged:
                    raise
                print(f'  ⚠️ {sid} 关键词「{kw}」失败({e})，跳过', file=sys.stderr)
        return merged, {'latency_ms': round(lat, 1)}
    raise ValueError(f'未知源类型: {src["kind"]}')


def _redate_for_demo(raws, now):
    """mock 回放：把 fixture 里的日期改写成「今天往前第 n 天」，避免演示数据被时效过滤清空。"""
    out = []
    for i, r in enumerate(raws):
        r = dict(r)
        d = (now - timedelta(days=min(i // 2, 4))).strftime('%Y-%m-%d')
        r['published_raw'] = d
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def build(max_age_days=MAX_AGE_DAYS_DEFAULT, limit=LIMIT_PER_CAT_DEFAULT,
          timeout=10, mock=False, quiet=False, deadline=None):
    """抓全部源 → 归一 → 去重 → 时效过滤 → 分类排序，返回 macro_data dict。

    deadline: 可选的墙钟截止时间（time.time() 秒）。构建期由 build_site.py 内联补抓时
              用来给「整轮抓取」设上限（CI 里 8 个源最坏要 8×timeout，必须能掐断），
              超时的源记为 skipped_deadline 而不是失败，其余流程照常产出。
    """
    now = datetime.now(timezone.utc)
    sources_meta, all_items = [], []
    for src in SOURCES:
        sid = src['id']
        meta = {'id': sid, 'label': SOURCE_LABELS.get(sid, sid), 'kind': src['kind'],
                'ok': False, 'items': 0, 'error': None}
        if deadline is not None and time.time() >= deadline:
            meta['error'] = 'skipped_deadline: 超出本轮抓取的墙钟预算，未请求'
            if not quiet:
                print(f'  ⏱️ {sid} 跳过：超出时间预算', file=sys.stderr)
            sources_meta.append(meta)
            continue
        try:
            raws, extra = fetch_source(src, timeout=timeout,
                                       fixture_dir=FIXTURE_DIR if mock else None, now=now)
            items = normalize(raws, sid, now=now)
            all_items.extend(items)
            meta.update({'ok': True, 'items': len(items)})
            meta.update(extra or {})
            if not quiet:
                print(f'  ✅ {sid: <12} {len(items):>3} 条  ({meta.get("latency_ms")} ms)')
        except Exception as e:  # noqa: BLE001 — 单源失败不阻断，与行情/社区/舆情层一致
            meta['error'] = f'{type(e).__name__}: {e}'[:200]
            if not quiet:
                print(f'  ⚠️ {sid} 失败: {meta["error"]}（跳过，不影响其余源）', file=sys.stderr)
        sources_meta.append(meta)

    unique = dedup(all_items)
    kept, dropped = filter_stale(unique, max_age_days=max_age_days, now=now)
    buckets, misc = rank(kept, limit=limit)
    per_cat = {k: len(v) for k, v in buckets.items()}
    dates = [it['published_date'] for it in kept if it.get('published_date')]

    return {
        'generated_at': now.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'fetch_date': now.strftime('%Y-%m-%d'),
        'mode': 'mock' if mock else 'live',
        'window': {'max_age_days': max_age_days,
                   'since': (now - timedelta(days=max_age_days)).strftime('%Y-%m-%d')},
        'categories': {c['key']: {'label': c['label'], 'items': buckets[c['key']]}
                       for c in CATEGORIES},
        'summary': {
            'ok': sum(1 for m in sources_meta if m['ok']),
            'total': len(sources_meta),
            'raw_items': len(all_items),
            'unique_items': len(unique),
            'kept_items': len(kept),
            'per_category': per_cat,
            'misc_dropped': len(misc),
            **dropped,
            'newest': max(dates) if dates else None,
            'oldest': min(dates) if dates else None,
        },
        'sources': sources_meta,
        'notes': [
            f'由 macro_data.py 构建时自动抓取（{len(SOURCES)} 个免密钥公开源），'
            f'只保留 {max_age_days} 天内且发布日期可解析的快讯',
            '02 栏正文由本文件产出驱动：某类别窗口内无快讯时渲染为提示，不复用历史文案',
            '来源名/接口只留在 sources[] 供 CI 排查，对外正文不显示数据来源',
            f'分类口径与 sentiment_match.py 的日报主题词表保持一致（{len(CATEGORIES)} 类）',
        ],
    }


# ---------------------------------------------------------------------------
# 02 栏兜底保障：单一事实源判定「本次到底有没有可用快讯」
#
# 背景（2026-09-16 旧内容事故的延伸排查）：原先只有「macro_data.json 文件读不到」
# 这一种情况会降级为「今日未获取」。但线上更常见的是下面几种"文件在、内容不能用"
# 的形态，它们会静默渲染出一个空壳栏目，页脚还盖着当天时间戳：
#   ① categories 有结构但窗口内 0 条（公开源当次全挂 / 全部超窗被拦截）
#   ② 读到的 JSON 不是 dict（半截文件、写坏的产物）
#   ③ 快照本身是前几天构建的（--offline 沿用旧结果，或 CI 抓取步骤被跳过）
#      —— 这种最危险：条目"看起来"有日期，实际是几天前的旧闻
# 统一在这里判定，网页与微信两端共用，避免两套渲染各写一套口径而漂移。
# ---------------------------------------------------------------------------
SNAPSHOT_MAX_AGE_DAYS = 1      # 快照（generated_at/fetch_date）超过这个天数即视为过期，必须重建


def availability(data, now=None, snapshot_max_age_days=SNAPSHOT_MAX_AGE_DAYS):
    """判定 02 栏这次能不能正常渲染。

    返回 dict:
      ok      bool  True = 有当次可用快讯，正常渲染；False = 走「今日未获取」兜底
      reason  str   机器可读原因：no_file / bad_type / no_items / stale_snapshot
      detail  str   给读者看的一句话，说明这次为什么没有快讯
      kept    int   窗口内条目数
      snapshot_age_days  float|None  快照距今天数（拿不到日期时为 None）
    """
    now = now or datetime.now(timezone.utc)

    # 先判类型再判空：空列表 / 字符串等"解析得出来但结构不对"的产物属于 bad_type，
    # 只有 None 与 {}（文件缺失时各加载器的默认值）才算 no_file。
    if data is not None and not isinstance(data, dict):
        return {'ok': False, 'reason': 'bad_type', 'kept': 0, 'snapshot_age_days': None,
                'detail': 'macro_data.json 内容不是预期的对象结构（产物可能写坏或被截断）'}
    if not data:
        return {'ok': False, 'reason': 'no_file', 'kept': 0, 'snapshot_age_days': None,
                'detail': '未读到 macro_data.json（构建步骤未执行，或产物未生成）'}

    cats = data.get('categories') or {}
    kept = sum(len(((blk or {}).get('items') or [])) for blk in cats.values())
    summ = data.get('summary') or {}
    ok_src, total_src = summ.get('ok'), summ.get('total')

    # 快照时效：优先用 generated_at（精确到秒），退回 fetch_date
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

    if not kept:
        src = (f'当次 {ok_src}/{total_src} 个公开源可用，'
               if ok_src is not None and total_src is not None else '')
        dropped = []
        if summ.get('stale_dropped'):
            dropped.append(f'超窗 {summ["stale_dropped"]} 条')
        if summ.get('undated_dropped'):
            dropped.append(f'无日期 {summ["undated_dropped"]} 条')
        tail = f'（已拦截 {" / ".join(dropped)}）' if dropped else ''
        win = (data.get('window') or {}).get('max_age_days', '—')
        return {'ok': False, 'reason': 'no_items', 'kept': 0, 'snapshot_age_days': age,
                'detail': f'{src}时效窗口（{win} 天）内没有任何可核验的宏观快讯{tail}'}

    if age is not None and age > snapshot_max_age_days:
        return {'ok': False, 'reason': 'stale_snapshot', 'kept': kept, 'snapshot_age_days': age,
                'detail': (f'读到的是 {age:.1f} 天前构建的旧快照（{stamp or data.get("fetch_date")}），'
                           f'超过 {snapshot_max_age_days} 天新鲜度上限 —— '
                           f'当次抓取未执行或失败，{kept} 条旧闻按兜底口径不予展示')}

    return {'ok': True, 'reason': 'ok', 'kept': kept, 'snapshot_age_days': age, 'detail': ''}


# ---------------------------------------------------------------------------
# 构建期自动补抓（2026-09-17 核查结论的长期修复）
#
# 线上事实：`.github/workflows/m.yml` 里从来没有 `macro_data.py` 这一步
# （改动以 docs/macro-ci-workflow.patch 交付，需要有 workflows 权限的账号手工
#   git apply，至今未应用 —— 已用 `gh run view --job` 的 step 列表核实）。
# 于是每次构建都不存在 macro_data.json，02 栏 / 微信 02 栏稳定停在
# 「今日未获取 · 未读到 macro_data.json（no_file）」。
#
# 这里补一条**不依赖 workflow 权限**的通路：build_site.py 与 tools/wechat_push.py
# 本来就在 deploy / wechat / daily 三个 job 里都会跑，让它们在读产物前先调 ensure()
# —— 产物缺失 / 写坏 / 是旧快照时**就地补抓一次并落盘**，与 panorama.py
# 「构建期直接调用、无需改 CI workflow」是同一套思路。
#
# 边界（都很重要，别随手放宽）：
#   • 只有 no_file / bad_type / stale_snapshot 才补抓；no_items 说明当次已经抓过
#     且公开源全挂，再抓一遍只会把构建时间烧掉，保持原判定即可；
#   • 默认只在 CI 里自动补抓（GITHUB_ACTIONS 存在），本地/单测默认不联网，
#     需要时 MACRO_AUTO_FETCH=1 显式打开、MACRO_AUTO_FETCH=0 显式关闭；
#   • 补抓失败绝不抛异常、绝不阻断构建 —— 最坏情况退回原来的「今日未获取」；
#   • 补抓产物一律落盘（哪怕 0 条），页面判定因此从 no_file 变成 no_items，
#     运维能一眼区分「步骤没跑」和「源全挂」。
# ---------------------------------------------------------------------------
AUTO_FETCH_TIMEOUT = 6              # 补抓时的单请求超时（比 CLI 默认 10s 更紧，构建不能等）
AUTO_FETCH_BUDGET_SECONDS = 70      # 补抓整轮的墙钟预算（8 源最坏情况也要能收口）
REFETCH_REASONS = ('no_file', 'bad_type', 'stale_snapshot')


def auto_fetch_enabled(explicit=None):
    """补抓开关：显式入参 > 环境变量 MACRO_AUTO_FETCH > 「在 CI 里就开」。"""
    if explicit is not None:
        return bool(explicit)
    raw = str(os.environ.get('MACRO_AUTO_FETCH', '')).strip().lower()
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
    return data if isinstance(data, dict) else data   # 非 dict 原样返回，让 availability 报 bad_type


def ensure(path=DEFAULT_OUT, max_age_days=MAX_AGE_DAYS_DEFAULT, limit=LIMIT_PER_CAT_DEFAULT,
           timeout=AUTO_FETCH_TIMEOUT, mock=False, quiet=True, now=None, allow_fetch=None,
           budget_seconds=AUTO_FETCH_BUDGET_SECONDS):
    """读取 macro_data.json；不可用且允许联网时就地补抓一次并落盘。

    返回 dict:
      data       产物内容（dict；补抓失败时可能是 0 条的产物或 {}）
      action     fresh | fetched | refetch_unavailable | no_refetch | disabled | error
      reason     最终的 availability().reason
      kept       窗口内条目数
      fetched    本轮是否真的发起了抓取
      elapsed_ms 补抓耗时（未抓取为 0）
    永不抛异常：任何意外都退化成「和调用前一样不可用」，构建照常继续。
    """
    t0 = time.time()
    now = now or datetime.now(timezone.utc)
    data = load_json(path)
    avail = availability(data, now=now)
    out = {'data': data, 'action': 'fresh', 'reason': avail['reason'], 'kept': avail['kept'],
           'fetched': False, 'elapsed_ms': 0, 'path': path}
    if avail['ok']:
        return out

    if avail['reason'] not in REFETCH_REASONS:
        # no_items：当次已经抓过一轮且窗口内 0 条，重复抓取只会拖长构建
        out['action'] = 'no_refetch'
        return out
    if not auto_fetch_enabled(allow_fetch):
        out['action'] = 'disabled'
        return out

    try:
        fresh = build(max_age_days=max_age_days, limit=limit, timeout=timeout, mock=mock,
                      quiet=quiet, deadline=t0 + max(1, int(budget_seconds)))
    except Exception as e:  # noqa: BLE001 — 补抓失败绝不阻断构建
        out['action'] = 'error'
        out['reason'] = avail['reason']
        out['detail'] = f'{type(e).__name__}: {e}'[:200]
        out['elapsed_ms'] = round((time.time() - t0) * 1000, 1)
        print(f'  ⚠️ 宏观快讯构建期补抓失败（{out["detail"]}）→ 维持「今日未获取」兜底',
              file=sys.stderr)
        return out

    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(fresh, f, ensure_ascii=False, indent=2)
    except OSError as e:
        # 落盘失败（只读目录等）不影响本次渲染，只是下次还得再抓
        print(f'  ⚠️ 宏观快讯补抓产物写入失败（{e}）→ 本次仍用内存结果渲染', file=sys.stderr)

    avail2 = availability(fresh, now=now)
    out.update({'data': fresh, 'reason': avail2['reason'], 'kept': avail2['kept'],
                'fetched': True, 'elapsed_ms': round((time.time() - t0) * 1000, 1),
                'action': 'fetched' if avail2['ok'] else 'refetch_unavailable'})
    return out


def text_report(data):
    """人读摘要（CI Step Summary / 终端）。"""
    s = data.get('summary') or {}
    w = data.get('window') or {}
    lines = [f'📰 宏观快讯: 源 {s.get("ok")}/{s.get("total")} 可用 · '
             f'原始 {s.get("raw_items")} → 去重 {s.get("unique_items")} → '
             f'窗口内 {s.get("kept_items")} 条（{w.get("since")} 起 {w.get("max_age_days")} 天）']
    if s.get('stale_dropped') or s.get('undated_dropped'):
        lines.append(f'  🚫 已拦截: 超窗 {s.get("stale_dropped")} 条 · 无日期 {s.get("undated_dropped")} 条')
    for c in CATEGORIES:
        key, label = c['key'], c['label']
        items = ((data.get('categories') or {}).get(key) or {}).get('items') or []
        if not items:
            lines.append(f'  · {label}: 窗口内无匹配快讯（02 栏将显示提示，不回填旧文）')
            continue
        lines.append(f'  · {label}: {len(items)} 条 · 最新 {items[0].get("published_date")}')
        for it in items[:2]:
            lines.append(f'      - [{it.get("published_date")}] {it.get("title")[:64]}')
    failed = [m for m in (data.get('sources') or []) if not m.get('ok')]
    for m in failed:
        lines.append(f'  ⚠️ {m["id"]} 失败: {m.get("error")}')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description='章鱼 AI·舆情004 — 宏观/财经快讯动态抓取（02 栏数据源）')
    ap.add_argument('--json', default=DEFAULT_OUT, help='输出 JSON 路径（默认 macro_data.json）')
    ap.add_argument('--days', type=int, default=MAX_AGE_DAYS_DEFAULT,
                    help=f'时效窗口（天），超出即丢弃；默认 {MAX_AGE_DAYS_DEFAULT}')
    ap.add_argument('--limit', type=int, default=LIMIT_PER_CAT_DEFAULT,
                    help=f'每类最多条数；默认 {LIMIT_PER_CAT_DEFAULT}')
    ap.add_argument('--timeout', type=int, default=10, help='单次请求超时秒数')
    ap.add_argument('--mock', action='store_true', help='离线回放 tests/fixtures（本地/CI 联调）')
    ap.add_argument('--offline', action='store_true', help='断网兜底：读取已有 JSON，仅刷新时间戳')
    ap.add_argument('--text', action='store_true', help='打印人读摘要')
    ap.add_argument('--quiet', action='store_true', help='不打印逐源日志')
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    if args.offline:
        try:
            with open(args.json, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            print(f'⚠️ 离线模式无可用历史数据（{e}）→ 02 栏将显示「今日未获取」', file=sys.stderr)
            data = {'categories': {}, 'summary': {'ok': 0, 'total': len(SOURCES),
                                                   'raw_items': 0, 'unique_items': 0,
                                                   'kept_items': 0, 'per_category': {}},
                    'sources': [], 'window': {'max_age_days': args.days, 'since': None},
                    'notes': ['离线模式且无历史数据：正文按「未获取」降级']}
        data.update({'generated_at': now.strftime('%Y-%m-%d %H:%M:%S UTC'),
                     'fetch_date': now.strftime('%Y-%m-%d'), 'mode': 'offline'})
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'🌐 离线模式: 已沿用上次结果并刷新时间戳 → {args.json}')
        return

    data = build(max_age_days=args.days, limit=args.limit, timeout=args.timeout,
                 mock=args.mock, quiet=args.quiet)
    with open(args.json, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    s = data['summary']
    if not args.quiet:
        print(f'📦 宏观快讯已写入 {args.json} '
              f'（源 {s["ok"]}/{s["total"]} · 正文可用 {s["kept_items"]} 条 · '
              f'窗口 {args.days} 天 · {data["generated_at"]}）')
        if s['kept_items'] == 0:
            print('  ⚠️ 窗口内无可用的宏观快讯 → 02 栏将显示「今日宏观快讯未获取」，'
                  '不复用历史文案（这是预期降级行为）', file=sys.stderr)
    if args.text:
        print(text_report(data))


if __name__ == '__main__':
    main()
