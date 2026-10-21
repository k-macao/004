#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 舆情/新闻因子「采集 → 匹配 → 展示」层 (sentiment_match.py)
================================================================================

本模块实现两件事，都是**面向对外输出（网页 03B 节 + 微信推送 03B 节）**的：

1) 采集对齐 + 匹配显示
   • `search_keywords()` 给关键词检索型接口提供与日报正文一致的关键词
     （恒生指数 / 港股 / 黄金 / 原油 / 美联储 / 人民币…），
     保证采回来的新闻舆情能和日报标的对得上，而不是各说各话；
   • `match_news()` / `build_matches()` 把每条新闻按关键词匹配到日报标的与主题
     （恒指 · 恒科 · 国企 · 标普 · 纳指 · 道指 · 黄金 · 原油 · 人民币
      + 美联储与美元流动性 · 内房与政策 · 高息防御 · 地缘与供应链 四个主题），
     并复用 `sentiment_nlp.aggregate()` 的同一套因子口径
     （净情感 / 负面占比 / 风险分 / 温度计），产出可直接渲染的匹配结果。

2) 来源脱敏（不显示来源）
   • `redact()` 把平台名 / 接口 ID / 域名 / SDK 名 / 凭据与依赖提示统一遮成「量化平台」；
   • `collection_summary()` / `status_line()` 给出**匿名聚合**的采集概况
     （N 个接口可用、新闻 X 条、平台现成因子 Y 条、Z 个自动降级），
     不再逐源列出接口 ID、平台名与失败原因；
   • 来源明细仍完整保留在内部：`sentiment_data.json`（构建产物，不入库）的
     `sources[] / series[].source / news[].source`，以及内部评测档案
     `docs/sentiment-api-eval.md`。需要临时对外显示来源时设 `SENTIMENT_SHOW_SOURCE=1`。

用法:
  python3 sentiment_match.py                # 打印标的关键词表 + 脱敏自检
  python3 sentiment_match.py --self-test    # 自检（匹配命中 / 脱敏黑名单 / 匿名聚合）
"""
import re
import sys
from datetime import datetime, timezone

import sentiment_nlp as nlp
import sentiment_sources as reg

# ---------------------------------------------------------------------------
# 匹配标的：前 10 个与 market_data.py 的 SYMBOLS 一一对应（行情表 ↔ 舆情表可对齐），
# 后 4 个是日报正文反复出现的主题层（无行情代码，但同样需要舆情读数）
# ---------------------------------------------------------------------------
TARGETS = [
    {'key': 'HSI', 'name': '恒生指数', 'kind': 'index', 'unit': '点',
     'keywords': ['恒生指数', '恒指', '港股', 'H股', 'H 股', '南向', '恒生', '港交所']},
    {'key': 'HSTECH', 'name': '恒生科技指数', 'kind': 'index', 'unit': '点',
     'keywords': ['恒生科技', '恒科', '科网', '科技股', '互联网龙头', '腾讯', '阿里',
                  '美团', '小米', '中芯', '半导体', '芯片', '光通信', '人工智能', 'AI']},
    {'key': 'HSCE', 'name': '恒生中国企业指数', 'kind': 'index', 'unit': '点',
     'keywords': ['国企指数', '恒生国企', '中资股', 'H股', 'H 股', '央企', '红筹']},
    {'key': 'SPX', 'name': '标普 500', 'kind': 'index', 'unit': '点',
     'keywords': ['标普', '标普500', '标普 500', 'S&P', '美股', '美国股市']},
    {'key': 'NDQ', 'name': '纳斯达克', 'kind': 'index', 'unit': '点',
     'keywords': ['纳斯达克', '纳指', 'Nasdaq', '美股科技', '费城半导体']},
    {'key': 'DJI', 'name': '道琼斯', 'kind': 'index', 'unit': '点',
     'keywords': ['道琼斯', '道指', 'Dow', '蓝筹']},
    {'key': 'GOLD', 'name': '现货黄金', 'kind': 'commodity', 'unit': '美元/盎司',
     'keywords': ['黄金', '金价', '贵金属', '白银', '避险', '金银']},
    {'key': 'WTI', 'name': '原油（WTI / 布伦特）', 'kind': 'commodity', 'unit': '美元/桶',
     'keywords': ['原油', '油价', 'WTI', '布伦特', 'Brent', '石油', '霍尔木兹',
                  'OPEC', '欧佩克', '天然气']},
    {'key': 'USDCNH', 'name': '美元 / 人民币', 'kind': 'fx', 'unit': '',
     'keywords': ['人民币', '离岸', '在岸', 'CNH', '汇率', '美元指数', '外汇', '中间价']},
    {'key': 'FED', 'name': '美联储与美元流动性', 'kind': 'theme', 'unit': '',
     'keywords': ['美联储', 'FOMC', '联储', '加息', '降息', '利率', 'CPI', '通胀',
                  '非农', '就业', '美债', '鲍威尔', '杰克逊霍尔', '流动性']},
    {'key': 'HKPROP', 'name': '内房与政策博弈', 'kind': 'theme', 'unit': '',
     'keywords': ['内房', '地产', '楼市', '房策', '限购', '公积金', '政策', '政治局',
                  '刺激', '化债', '城投']},
    {'key': 'DEFENSE', 'name': '高息与防御底仓', 'kind': 'theme', 'unit': '',
     'keywords': ['高息', '红利', '派息', '股息', 'REITs', '电信', '公用事业',
                  '银行股', '保险', '中特估']},
    {'key': 'GEO', 'name': '地缘与供应链风险', 'kind': 'theme', 'unit': '',
     'keywords': ['地缘', '关税', '制裁', '出口管制', '中东', '伊朗', '以色列',
                  '供应链', '稀土', '锂', '铜', '铝', '航运']},
]

# 采集关键词（关键词检索型接口用）：主标的在前，保证「采集 → 匹配」对得上日报正文
SEARCH_KEYWORDS = ['恒生指数', '港股', '黄金', '原油', '美联储', '人民币', '内房']

# ---------------------------------------------------------------------------
# 来源脱敏：对外输出一律不显示数据来源（平台名 / 接口 ID / 域名 / SDK / 凭据提示）
# ---------------------------------------------------------------------------
REPL = '量化平台'

PLATFORM_TERMS = [
    # 量化平台中文名
    '聚宽', '米筐', '掘金量化', '掘金', '优矿', '通联数据', '数库', '金十数据', '金十',
    '东方财富', '东财', '千股千评',
    # 平台/接口英文名与 SDK
    'JoinQuant', 'JQData', 'jqdatasdk', 'RiceQuant', 'RQData', 'rqdatac', 'rqdatac_news',
    'Myquant', 'gm.api', 'Uqer', 'DataAPI', 'Tushare Pro', 'Tushare', 'tushare',
    'Chinascope', 'chinascope', 'Jin10', 'akshare',
    # 域名 / 端点（内部工程细节，不外露）
    'datacenter-web.eastmoney.com', 'search-api-web.eastmoney.com', 'eastmoney.com',
    'datacenter-api.jin10.com', 'jin10.com', 'api.ricequant.com', 'ricequant.com',
    'dataapi.joinquant.com', 'joinquant.com', 'api.tushare.pro', 'tushare.pro',
    'www.chinascope.com', 'chinascope.com.cn', 'uqer.datayes.com',
    # 凭据 / 依赖类提示（对外无意义且暴露实现）
    'RQDATA_TOKEN', 'RQDATAC_USER', 'RQDATAC_PASSWORD', 'RQDATA_USER', 'RQDATA_PASSWORD',
    'UQER_TOKEN', 'TUSHARE_TOKEN', 'JQ_MOBILE', 'JQ_USERNAME', 'JQ_PASSWORD', 'GM_TOKEN',
    'pip install', '私有 pip 源',
]

# 接口 ID（EM_COMMENT / RQ_SDK / JQ_HTTP …）与常见字段名，按整词遮掉
_ID_RE = re.compile(r'\b(?:' + '|'.join(re.escape(i) for i in reg.ids()) + r')\b')
_FIELD_RE = re.compile(r'\b(?:news\.get_stock_news|get_stock_news|NewsSentimentIndexGet|'
                       r'NewsHeatIndexGet|NewsByTickersGet|get_factor_values|sentimentIndex|'
                       r'heatIndex|finance\.CCTV_NEWS|CCTV_NEWS)\b')
_CRED_RE = re.compile(r'缺少\s*[A-Z][A-Z0-9_/]*(?:TOKEN|USER|PASSWORD|MOBILE|USERNAME)'
                      r'|缺凭据环境变量[:：][^<。，;；]*')
_DEP_RE = re.compile(r'依赖缺失[^<。，;；]*')


def show_source():
    """是否对外显示数据来源（默认 False＝隐藏；SENTIMENT_SHOW_SOURCE=1 临时恢复）。"""
    return reg.show_source()


def redact(text, repl=REPL):
    """把任何来源痕迹遮成「量化平台」，并折叠重复词，保证对外输出不显示来源。"""
    out = str(text or '')
    if not out:
        return out
    out = _CRED_RE.sub('', out)
    out = _DEP_RE.sub('', out)
    out = _ID_RE.sub(repl, out)
    out = _FIELD_RE.sub(repl + '接口', out)
    for term in sorted(PLATFORM_TERMS, key=len, reverse=True):
        out = out.replace(term, repl)
    out = re.sub(r'(量化平台)(?:\s*[/、·,，]?\s*|接口)?(?=量化平台)', r'\1', out)
    out = re.sub(r'(量化平台){2,}', repl, out)
    out = re.sub(r'量化平台接口接口', '量化平台接口', out)
    return out.strip(' ·、/，,')


# ---------------------------------------------------------------------------
# 匹配：新闻/序列 → 日报标的与主题
# ---------------------------------------------------------------------------
def _now():
    return datetime.now(timezone.utc)


def targets():
    return [dict(t) for t in TARGETS]


def target_keys():
    return [t['key'] for t in TARGETS]


def search_keywords(limit=3):
    """关键词检索型接口的采集关键词（默认取前 3 个，避免请求数膨胀）。"""
    try:
        n = max(1, int(limit))
    except (TypeError, ValueError):
        n = 3
    return list(SEARCH_KEYWORDS[:n])


def _hits(text, keywords):
    return [k for k in keywords if k and k in text]


def match_item(item, tgts=None):
    """单条新闻 → [(target_key, relevance, 命中关键词)]；标题命中权重高于正文命中。"""
    tgts = tgts if tgts is not None else TARGETS
    title = str(item.get('title') or '')
    body = str(item.get('content') or '')[:500]
    out = []
    for t in tgts:
        th = _hits(title, t['keywords'])
        bh = _hits(body, t['keywords']) if body else []
        if not th and not bh:
            continue
        rel = min(1.0, 0.4 * len(th) + 0.15 * len(bh))
        out.append((t['key'], round(rel, 3), sorted(set(th + bh))))
    return out


def match_news(news, tgts=None, ref_time=None):
    """按标的聚合匹配结果（因子口径与 sentiment_nlp.aggregate 完全一致）。

    返回全部标的（含 hits=0），字段一律**不含来源信息**。
    """
    tgts = tgts if tgts is not None else TARGETS
    ref_time = ref_time or _now()
    buckets = {t['key']: [] for t in tgts}
    rels = {t['key']: [] for t in tgts}
    terms = {t['key']: {} for t in tgts}
    for it in (news or []):
        for key, rel, hit_terms in match_item(it, tgts):
            buckets[key].append(it)
            rels[key].append(rel)
            for w in hit_terms:
                terms[key][w] = terms[key].get(w, 0) + 1

    out = []
    for t in tgts:
        items = buckets[t['key']]
        agg = nlp.aggregate(items, ref_time=ref_time)
        top = sorted((x for x in (agg['top_negative'] + agg['top_positive'])),
                     key=lambda x: -abs(x.get('sentiment') or 0))[:2]
        out.append({
            'key': t['key'],
            'name': t['name'],
            'kind': t['kind'],
            'unit': t.get('unit', ''),
            'hits': len(items),
            'relevance': round(sum(rels[t['key']]) / len(rels[t['key']]), 3) if rels[t['key']] else 0.0,
            'net_senti': agg['net_senti'],
            'neg_share': round(agg['neg_share'] * 100, 2),
            'pos_share': round(agg['pos_share'] * 100, 2),
            'risk_score': agg['risk_score'],
            'sent_temp': agg['sent_temp'],
            'label': nlp.label(agg['sent_temp']),
            'platform_native': agg['platform_native'],
            'latest': max((str(x.get('published_at') or x.get('date') or '')[:10]
                           for x in items), default=''),
            'top_terms': [w for w, _ in sorted(terms[t['key']].items(),
                                               key=lambda kv: -kv[1])[:4]],
            'top_titles': [{'title': (x.get('title') or '')[:60],
                            'sentiment': x.get('sentiment'),
                            'published_at': str(x.get('published_at') or '')[:16]}
                           for x in top],
            'events': [{'title': (e.get('title') or '')[:60], 'terms': e.get('terms'),
                        'risk_score': e.get('risk_score')} for e in agg['events'][:2]],
        })
    return out


def build_matches(news, series=None, keywords_used=None, ref_time=None):
    """采集结果 → 对外展示用的匹配层（sentiment_data.json 的 `matches` 字段）。"""
    ref_time = ref_time or _now()
    news = list(news or [])
    per = match_news(news, ref_time=ref_time)
    matched_ids = set()
    for it in news:
        if match_item(it):
            matched_ids.add(id(it))
    matched = len(matched_ids)
    total = len(news)
    return {
        'generated_at': ref_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'total_news': total,
        'matched_news': matched,
        'unmatched': total - matched,
        'coverage': round(matched / total, 3) if total else 0.0,
        'targets': [t for t in per if t['hits']],
        'all_targets': per,
        'keywords_used': list(keywords_used or search_keywords()),
        'series_rows': len(series or []),
        'anonymous': not show_source(),   # 对外展示层：不显示数据来源
    }


# ---------------------------------------------------------------------------
# 匿名聚合概况（替代「逐源取数明细」表，不暴露接口 ID / 平台名 / 失败原因）
# ---------------------------------------------------------------------------
def collection_summary(s):
    """把 sentiment_data.json 的采集结果压成一段不含来源信息的概况。"""
    s = s or {}
    sm = s.get('summary') or {}
    m = s.get('market') or {}
    srcs = s.get('sources') or []
    ok_n = sm.get('ok') if sm.get('ok') is not None else sum(1 for r in srcs if r.get('ok'))
    total = sm.get('total') if sm.get('total') is not None else len(srcs)
    failed = sm.get('failed') or [r.get('id') for r in srcs if not r.get('ok')]
    return {
        'total': total or 0,
        'ok': ok_n or 0,
        'failed_n': len(failed or []),
        'news': m.get('news_count', 0),
        'series': len(s.get('series') or []),
        'native': m.get('platform_native', 0),
        'self_built': m.get('self_built', max(0, (m.get('news_count') or 0)
                                              - (m.get('platform_native') or 0))),
        'date': s.get('fetch_date') or '',
        'mode': s.get('mode') or '',
        'mode_note': redact(s.get('mode_note') or ''),
        'degraded': bool(m.get('degraded')),
    }


def status_line(s):
    """对外状态文案：只给数量与降级个数，不给接口 ID / 平台名 / 报错细节。"""
    c = collection_summary(s)
    if not c['total']:
        return '舆情因子数据未生成'
    txt = f"量化平台接口 {c['ok']}/{c['total']} 可用 · 采集新闻 {c['news']} 条"
    if c['native']:
        txt += f"（平台现成因子 {c['native']} 条 · 自建词库打分 {c['self_built']} 条）"
    if c['failed_n']:
        txt += f" · {c['failed_n']} 个接口自动降级（不阻断构建与推送）"
    if c['degraded']:
        txt += ' · 全源不可用时温度计回退中性'
    return txt


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def _self_test():
    fails = []

    def chk(cond, msg):
        if not cond:
            fails.append(msg)

    demo = [
        {'title': '恒指低开低走收跌 2.22%，南向资金仍净流入', 'content': '港股科网普跌，腾讯阿里走弱',
         'published_at': _now().strftime('%Y-%m-%d %H:%M:%S')},
        {'title': '现货黄金刷新历史高位，避险资金涌入贵金属', 'content': '金价站上 4,400 美元',
         'published_at': _now().strftime('%Y-%m-%d %H:%M:%S'), 'sentiment': 0.8},
        {'title': '美联储 9 月加息概率下降，CPI 同比回落至 3.4%', 'content': '美债收益率走低',
         'published_at': _now().strftime('%Y-%m-%d %H:%M:%S')},
        {'title': '某公司公布季度报表', 'content': '营收增长', 'published_at': ''},
    ]
    mm = build_matches(demo, series=[{'date': '2026-09-15', 'value': 1.02}])
    chk(mm['total_news'] == 4, '匹配层应看到全部 4 条新闻')
    chk(mm['matched_news'] == 3, f"应匹配 3 条，实际 {mm['matched_news']}")
    chk(mm['unmatched'] == 1, '与日报标无关的新闻应计入 unmatched')
    by_key = {t['key']: t for t in mm['all_targets']}
    chk(by_key['HSI']['hits'] >= 1 and by_key['HSTECH']['hits'] >= 1, '恒指/恒科应命中港股新闻')
    chk(by_key['GOLD']['hits'] == 1, '黄金主题应命中 1 条')
    chk(by_key['FED']['hits'] == 1, '美联储主题应命中 1 条')
    chk(by_key['GOLD']['net_senti'] > 0, '带平台原生情感的新闻应沿用平台口径（正值）')
    chk(all('source' not in t and 'platform' not in t for t in mm['all_targets']),
        '匹配结果不得携带来源字段')
    chk(set(target_keys()) >= {'HSI', 'HSTECH', 'HSCE', 'SPX', 'NDQ', 'DJI',
                               'GOLD', 'WTI', 'USDCNH'},
        '标的键需与 market_data.py 的行情键对齐')

    dirty = ('米筐 RiceQuant RQ_SDK news.get_stock_news 与 优矿 Uqer sentimentIndex、聚宽 JQ_HTTP、'
             '掘金 gm.api、Tushare Pro、东财千股千评、金十微博人气、数库 Chinascope，'
             '端点 datacenter-web.eastmoney.com，缺少 UQER_TOKEN，依赖缺失 rqdatac')
    clean = redact(dirty)
    for bad in ('米筐', 'RiceQuant', 'RQ_SDK', '优矿', 'Uqer', '聚宽', 'JQ_HTTP', '掘金',
                'Tushare', '东财', '千股千评', '金十', '数库', 'Chinascope', 'eastmoney',
                'UQER_TOKEN', 'rqdatac', 'sentimentIndex', 'get_stock_news'):
        chk(bad not in clean, f'脱敏后仍残留来源痕迹: {bad} → {clean}')
    chk(REPL in clean, '脱敏后应保留中性占位「量化平台」')

    s = {'summary': {'total': 11, 'ok': 10, 'failed': ['GM_SDK']},
         'market': {'news_count': 19, 'platform_native': 5, 'self_built': 14},
         'series': [{'date': '2026-09-15', 'value': 1.02}],
         'sources': [{'id': 'GM_SDK', 'platform': '掘金量化 Myquant', 'ok': False}],
         'fetch_date': '2026-09-16', 'mode': 'mock', 'mode_note': '离线回放'}
    line = status_line(s)
    chk('10/11' in line and 'GM_SDK' not in line and '掘金' not in line,
        f'状态文案应匿名: {line}')
    c = collection_summary(s)
    chk(c['failed_n'] == 1 and c['native'] == 5, '匿名聚合概况数量应正确')

    return fails


def table():
    lines = [f"{'KEY':<10}{'标的 / 主题':<24}{'类型':<10}关键词"]
    lines.append('-' * 96)
    for t in TARGETS:
        lines.append(f"{t['key']:<10}{t['name']:<24}{t['kind']:<10}"
                     f"{'、'.join(t['keywords'][:8])}{'…' if len(t['keywords']) > 8 else ''}")
    lines.append('-' * 96)
    lines.append(f"采集关键词（检索型接口）: {'、'.join(SEARCH_KEYWORDS)}")
    lines.append(f"对外显示数据来源: {'是' if show_source() else '否（默认隐藏，SENTIMENT_SHOW_SOURCE=1 可临时恢复）'}")
    lines.append(f"对外显示接入评测（9 阶段实测）矩阵: "
                 f"{'是' if reg.show_api_eval() else '否（默认隐藏，SENTIMENT_SHOW_API_EVAL=1 可临时恢复；结论见 docs/sentiment-api-eval.md）'}")
    return '\n'.join(lines)


if __name__ == '__main__':
    print(table())
    if '--self-test' in sys.argv:
        bad = _self_test()
        if bad:
            print('\n❌ 匹配/脱敏自检未通过:')
            for b in bad:
                print('   ·', b)
            sys.exit(1)
        print('\n✅ 匹配 + 脱敏自检全部通过（标的对齐 / 因子口径 / 来源不外露）')
