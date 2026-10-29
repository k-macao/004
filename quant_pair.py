#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — AI 量化 · 配对交易 (quant_pair.py)

挂在每一条内容后面：先按正文找到一条量化策略，再给出恰好两只标的的**跨域组合**，
最后用当次涨跌幅按该策略的规则给出推荐。

「跨域组合」是硬口径：两腿必须来自不同的域（港股 / 美股 / 贵金属 / 能源 / 汇率），
同域价差（恒科/恒指、WTI/布伦特、离岸/在岸人民币）一律不作为本栏的组合 ——
策略目录 `STRATEGIES` 全部跨域，`select_strategy()` 也只在跨域策略里挑，
`cross_domain_catalog_errors()` 把这条口径做成可自检的规则。

策略家族只有「配对交易 / 相对收益均值回归」：
    价差 = 涨跌幅A − 涨跌幅B
    z    = 价差 / 残差波动
    残差波动 = sqrt(σA² + σB² − 2·ρ·σA·σB)
    |z| < 1     观望，不建配对仓
    z ≤ −1      做多 A、做空 B（A 相对偏弱，等待回归）
    z ≥ 1       做空 A、做多 B（A 相对偏强，等待回归）

σ 与 ρ 是策略参数（典型日波动、预设相关系数），不是某一天的行情事实。
两腿涨跌幅缺任何一条，就输出「数据不足」，不编方向、不回填历史点位 ——
并且**整段不渲染**（见下方「行情不足 → 整段隐藏」）：与其把一段「数据不足 / 不预测方向」
铺在每条内容后面，不如什么都不放。数据层照旧返回 action='no_data'，供调用方判断。

纯标准库、纯函数、不联网。网页与微信共用本模块，避免两套口径漂移。

用法:
  python3 quant_pair.py --self-test
  python3 quant_pair.py --text "原油与人民币" --quotes market_data.json
"""
import argparse
import html
import json
import math
import os
import sys

MINUS = '\u2212'

# 策略参数：典型单日波动（百分点），不是写死的行情事实
DAILY_SIGMA = {
    'HSI': 1.20, 'HSTECH': 1.80, 'HSCE': 1.30,
    'SPX': 0.90, 'NDQ': 1.20, 'DJI': 0.80,
    'GOLD': 0.90, 'WTI': 1.80, 'BRENT': 1.60,
    'USDCNH': 0.25, 'USDCNY': 0.20,
}

NAMES = {
    'HSI': '恒生指数', 'HSTECH': '恒生科技指数', 'HSCE': '恒生中国企业指数',
    'SPX': '标普 500', 'NDQ': '纳斯达克', 'DJI': '道琼斯',
    'GOLD': '现货黄金', 'WTI': 'WTI 原油', 'BRENT': '布伦特原油',
    'USDCNH': '美元/离岸人民币', 'USDCNY': '美元/在岸人民币',
}

# ---------------------------------------------------------------------------
# 跨域组合 (cross-domain pair)
# ---------------------------------------------------------------------------
# 每条内容后面的 AI 量化必须给出**恰好两只标的、且两腿分属不同域**：
#   域 = 资产类别 / 市场，不是「同一市场里的不同板块」。
#     港股   HSI / HSTECH / HSCE
#     美股   SPX / NDQ / DJI
#     贵金属  GOLD
#     能源   WTI / BRENT
#     汇率   USDCNH / USDCNY
# 因此 HSTECH/HSI（同属港股）、WTI/BRENT（同属能源）、USDCNH/USDCNY（同属汇率）
# 这类「同域价差」不再作为策略目录里的组合 —— 跨域组合才是本栏的口径。
DOMAIN = {
    'HSI': 'HK', 'HSTECH': 'HK', 'HSCE': 'HK',
    'SPX': 'US', 'NDQ': 'US', 'DJI': 'US',
    'GOLD': 'METAL',
    'WTI': 'ENERGY', 'BRENT': 'ENERGY',
    'USDCNH': 'FX', 'USDCNY': 'FX',
}
DOMAIN_LABEL = {
    'HK': '港股', 'US': '美股', 'METAL': '贵金属', 'ENERGY': '能源', 'FX': '汇率',
}
CROSS_DOMAIN_NOTE = '跨域规则：两腿必须来自不同域（港股 / 美股 / 贵金属 / 能源 / 汇率），不做同域价差。'

# 正文没有主题词时，用频道名落到默认配对；主题词命中仍可覆盖
NAME_TO_HINT = (
    ('富途', 'FUTU'), ('雪球', 'XUEQIU'), ('老虎', 'LAOHU'),
    ('东方财富', 'EASTMONEY'), ('智通', 'ZHITONG'), ('华尔街见闻', 'WALLSTREETCN'),
    ('香港讨论区', 'DISCUSS'), ('连登', 'LIHKG'), ('LIHKG', 'LIHKG'),
    ('韭圈', 'JIUQUAN'), ('蚂蚁财富', 'ANTFORTUNE'), ('Reddit', 'REDDIT'),
    ('TradingView', 'TRADINGVIEW'), ('Value Investors', 'VIC'),
    ('FinTwit', 'FINTWIT'), ('Twitter', 'FINTWIT'),
    # 前次新增的 20 个社区（34 → 49 源口径）
    ('知乎', 'ZHIHU'), ('微博', 'WEIBO'), ('百度贴吧', 'TIEBA'), ('贴吧', 'TIEBA'),
    ('淘股吧', 'TAOGUBA'), ('同花顺', 'THS'), ('格隆汇', 'GELONGHUI'),
    ('财联社', 'CLS'), ('第一财经', 'YICAI'), ('Bilibili', 'BILIBILI'), ('哔哩哔哩', 'BILIBILI'),
    ('PTT', 'PTT'), ('StockTwits', 'STOCKTWITS'), ('Seeking Alpha', 'SEEKINGALPHA'),
    ('Bogleheads', 'BOGLEHEADS'), ('r/investing', 'RINVESTING'),
    ('Wall Street Oasis', 'WSO'), ('Investing.com', 'INVESTING'),
    ('Yahoo Finance', 'YAHOO'), ('Substack', 'SUBSTACK'), ('r/options', 'ROPTIONS'),
    ('Alphaville', 'FTALPHA'),
    # 本次扩容的 15 个社区（49 源口径）
    ('集思录', 'JISILU'), ('小红书', 'XIAOHONGSHU'), ('抖音', 'DOUYIN'),
    ('开盘啦', 'KAIPANLA'), ('理想论坛', 'LIXIANG'), ('QuantNet', 'QUANTNET'),
    ('Elite Trader', 'ELITETRADER'), ('Forex Factory', 'FOREXFACTORY'),
    ('CryptoCurrency', 'RCRYPTO'), ('Morningstar', 'MORNINGSTAR'),
    ('MarketWatch', 'MARKETWATCH'), ('Yahoo! 财经', 'YAHOOJP'), ('NAVER', 'NAVER'),
    ('네이버', 'NAVER'), ('Wallstreet-Online', 'WALLSTREETDE'),
    ('阿斯达克', 'AASTOCKS'), ('AASTOCKS', 'AASTOCKS'),
)

HINT_WEIGHT = 4
KEYWORD_WEIGHT = 6
Z_ENTRY = 1.0
Z_STRONG = 1.8

# 口径说明整页只印一次（网页在页脚、微信在文末），不再逐块重复：
#   ① RULE 是配对规则，② OUTLOOK_NOTE 是 48h 三行预测的口径边界。
RULE = ('两腿须跨域；价差 = 涨跌幅A − 涨跌幅B，z = 价差 / 残差波动'
        '（σ 为策略参数里的典型日波动）。|z|<1 观望，z≤−1 做多A做空B，z≥1 做空A做多B。'
        '行情不足整段隐藏，不构成投资建议。')
OUTLOOK_NOTE = '48h 风险因子 / 走势 / 展望三行均为规则化推导，置信度未校准。'

# 每条策略恰好两只标的、且两腿跨域（见上方 DOMAIN）。hints 是栏目/频道/力量 key，
# keywords 从正文里找策略。同一个「域对」可以有多条策略，但组合本身不重复。
STRATEGIES = [
    {
        'id': 'hk_tech_us_tech',
        'name': '港股科技 × 美股科技',
        'leg_a': 'HSTECH', 'leg_b': 'NDQ', 'rho': 0.55,
        'hints': ['rotation', 'hk', 'HSTECH', 'FUTU', 'ZHITONG', 'TRADINGVIEW',
                  'JIUQUAN', 'sentiment', 'ZHIHU', 'BILIBILI', 'TAOGUBA',
                  'LIHKG', 'DOUYIN', 'KAIPANLA', 'NAVER', 'RCRYPTO'],
        'keywords': ['恒科', '恒生科技', '科网', '科技股', '成长', '轮动', '半导体',
                     '芯片', '光通信', '互联网', '腾讯', '阿里', '小米', '美团',
                     '中概', 'ADR', '费城半导体', '新质生产力'],
        'logic': '恒生科技与纳指同受全球成长因子驱动、却分处两个市场，残差偏离后做均值回归。',
    },
    {
        'id': 'cross_market',
        'name': '跨市场风险偏好配对',
        'leg_a': 'HSI', 'leg_b': 'SPX', 'rho': 0.45,
        'hints': ['macro', 'macro_growth', 'hk_tape', 'LAOHU', 'REDDIT',
                  'WALLSTREETCN', 'bank_views', 'institution', 'WSO', 'INVESTING',
                  'YAHOO', 'QUANTNET', 'ELITETRADER', 'MARKETWATCH'],
        'keywords': ['美股', '隔夜', '风险偏好', '再平衡', '外围', '标普'],
        'logic': '恒指与标普 500 是跨市场风险偏好的两端（港股 × 美股），相对收益偏离后回归。',
    },
    {
        'id': 'hk_value_us_growth',
        'name': '港股价值 × 美股成长',
        'leg_a': 'HSCE', 'leg_b': 'NDQ', 'rho': 0.58,
        'hints': ['HKPROP', 'DEFENSE', 'EASTMONEY', 'ANTFORTUNE', 'VIC', 'DISCUSS',
                  'XUEQIU', 'HSCE', 'GELONGHUI', 'TIEBA', 'PTT',
                  'JISILU', 'XIAOHONGSHU', 'LIXIANG', 'MORNINGSTAR', 'YAHOOJP', 'AASTOCKS'],
        'keywords': ['内房', '地产', '高息', '红利', '股息', 'REITs', '公用', '电信',
                     '防御', '国企', '恒生国企', '红筹', '中资股', '南向', '权重',
                     '银行股', '金融', '价值股'],
        'logic': '恒生国企与纳指是价值—成长的两端（港股 × 美股），相对强弱偏离后做配对回归。',
    },
    {
        'id': 'us_value_hk_tech',
        'name': '美股价值 × 港股科技',
        'leg_a': 'DJI', 'leg_b': 'HSTECH', 'rho': 0.42,
        'hints': ['DJI', 'global_risk', 'us_value', 'THS'],
        'keywords': ['道指', '道琼斯', '蓝筹', '价值股', '传统经济', '再通胀'],
        'logic': '道指的成熟价值与恒生科技的成长弹性（美股 × 港股），跨域相对收益偏离后回归。',
    },
    {
        'id': 'gold_fx',
        'name': '黄金 × 离岸人民币',
        'leg_a': 'GOLD', 'leg_b': 'USDCNH', 'rho': 0.20,
        'hints': ['fed', 'fed_liquidity', 'GOLD', 'USDCNH', 'FINTWIT', 'RINVESTING'],
        'keywords': ['黄金', '金价', '贵金属', '美联储', 'FOMC', '加息', '降息',
                     '利率', '美债', '实际利率'],
        'logic': '黄金（贵金属）与离岸人民币（汇率）同受美元流动性影响，跨域相对收益偏离后回归。',
    },
    {
        'id': 'risk_hedge',
        'name': '股金相对价值配对',
        'leg_a': 'SPX', 'leg_b': 'GOLD', 'rho': -0.10,
        'hints': ['risk_hedge', 'GEO', 'geo', 'STOCKTWITS', 'SEEKINGALPHA'],
        'keywords': ['避险', '对冲', '地缘', '战争', '风险事件', 'vix'],
        'logic': '标普 500（美股）与黄金（贵金属）是风险资产与避险资产的经典跨域配对，偏离后回归。',
    },
    {
        'id': 'oil_hk',
        'name': '原油 × 港股价值',
        'leg_a': 'WTI', 'leg_b': 'HSCE', 'rho': 0.22,
        'hints': ['commodities', 'WTI', 'energy', 'CLS', 'SUBSTACK'],
        'keywords': ['原油', '油价', 'WTI', '石油', 'OPEC', '欧佩克', '霍尔木兹',
                     '供应链', '输入性', '能源'],
        'logic': 'WTI（能源）与恒生国企（港股）代表油价冲击与中资价值两端，跨域价差偏离后回归。',
    },
    {
        'id': 'oil_us',
        'name': '油价与美股成长配对',
        'leg_a': 'BRENT', 'leg_b': 'NDQ', 'rho': 0.18,
        'hints': ['BRENT'],
        'keywords': ['布伦特', '航空', '运输成本', '通胀预期'],
        'logic': '布伦特（能源）与纳指（美股）是成本冲击与成长估值的两端，跨域相对收益偏离后回归。',
    },
    {
        'id': 'gold_oil',
        'name': '避险与能源配对',
        'leg_a': 'GOLD', 'leg_b': 'WTI', 'rho': 0.25,
        'hints': ['gold_oil', 'copper', 'ROPTIONS'],
        'keywords': ['铜锂', '稀土', '铜铝', '大宗商品', '商品'],
        'logic': '黄金（贵金属）与 WTI（能源）是两条商品链，跨域相对收益偏离后做均值回归。',
    },
    {
        'id': 'cny_hk',
        'name': '离岸人民币 × 港股',
        'leg_a': 'USDCNH', 'leg_b': 'HSI', 'rho': 0.30,
        'hints': ['cny_hk', 'MACROVOICES', 'WEIBO', 'FOREXFACTORY', 'WALLSTREETDE'],
        'keywords': ['离岸人民币', '人民币', 'CNH', 'CNY', '汇差', '中间价',
                     '汇率', '贬值', '升值'],
        'logic': '离岸人民币（汇率）与恒指（港股）同受离岸流动性与风险偏好驱动，跨域价差偏离后回归。',
    },
    {
        'id': 'cny_us',
        'name': '在岸人民币 × 美股',
        'leg_a': 'USDCNY', 'leg_b': 'SPX', 'rho': 0.25,
        'hints': ['cny_us', 'YICAI'],
        'keywords': ['在岸人民币', '贸易', '关税', '出口', '汇率战'],
        'logic': '在岸人民币（汇率）与标普 500（美股）代表贸易条件与风险资产两端，跨域偏离后回归。',
    },
    {
        'id': 'hk_metal',
        'name': '港股 × 黄金对冲配对',
        'leg_a': 'HSI', 'leg_b': 'GOLD', 'rho': 0.15,
        'hints': ['hk_gold', 'BOGLEHEADS'],
        'keywords': ['避风港', '风险对冲', '金价与港股'],
        'logic': '恒指（港股）与黄金（贵金属）是风险资产与避险资产的跨域配对，相对收益偏离后回归。',
    },
    {
        'id': 'fx_energy',
        'name': '汇率 × 能源配对',
        'leg_a': 'USDCNH', 'leg_b': 'BRENT', 'rho': 0.10,
        'hints': ['fx_energy', 'fx_oil', 'FTALPHA'],
        'keywords': ['输入性通胀', '进口成本', '油价与人民币'],
        'logic': '离岸人民币（汇率）与布伦特（能源）是进口成本链的两端，跨域相对收益偏离后回归。',
    },
]

_BY_ID = {s['id']: s for s in STRATEGIES}


def _esc(t):
    return html.escape(str(t if t is not None else ''), quote=False)


def _num(v):
    if v is None or v == '':
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(n) or math.isinf(n):
        return None
    return n


def normalize_quotes(quotes):
    """接受 market_data.json 根对象，或 quotes 字典。缺省返回空表。"""
    if not isinstance(quotes, dict):
        return {}
    if isinstance(quotes.get('quotes'), dict):
        quotes = quotes['quotes']
    out = {}
    for key, q in quotes.items():
        if not isinstance(q, dict):
            continue
        out[key] = {
            'name': q.get('name') or NAMES.get(key, key),
            'pct': _num(q.get('pct')),
            'as_of': q.get('as_of') or '',
        }
    return out


def _fmt_pct(v):
    if v is None:
        return '—'
    sign = MINUS if v < 0 else '+'
    return f'{sign}{abs(v):.2f}%'


def _fmt_pp(v):
    sign = MINUS if v < 0 else '+'
    return f'{sign}{abs(v):.2f}'


def strategy_domains(strategy):
    """返回 (域A, 域B)：港股 HK / 美股 US / 贵金属 METAL / 能源 ENERGY / 汇率 FX。"""
    return DOMAIN[strategy['leg_a']], DOMAIN[strategy['leg_b']]


def is_cross_domain(strategy):
    """两腿是否跨域 —— 本栏的硬口径：同域价差（恒科/恒指、WTI/布伦特…）不算组合。"""
    a, b = strategy_domains(strategy)
    return a != b


def domain_pair_label(strategy):
    """'港股 × 美股' 这样的域对标签，用于渲染与核对。"""
    a, b = strategy_domains(strategy)
    return f'{DOMAIN_LABEL[a]} × {DOMAIN_LABEL[b]}'


def cross_domain_catalog_errors():
    """策略目录自检：返回违反跨域口径的说明列表（空列表 = 全部合格）。"""
    bad = []
    seen = set()
    for st in STRATEGIES:
        if not is_cross_domain(st):
            bad.append(f"{st['id']} 两腿同域（{domain_pair_label(st)}）")
        pair = (st['leg_a'], st['leg_b'])
        if pair in seen:
            bad.append(f"{st['id']} 组合重复（{pair[0]}/{pair[1]}）")
        seen.add(pair)
    return bad


def pair_sigma(strategy):
    sa = DAILY_SIGMA[strategy['leg_a']]
    sb = DAILY_SIGMA[strategy['leg_b']]
    rho = max(-0.95, min(0.95, float(strategy['rho'])))
    var = sa * sa + sb * sb - 2.0 * rho * sa * sb
    return math.sqrt(max(var, 1e-6))


def _keyword_hits(strategy, text):
    if not text:
        return []
    low = text.lower()
    hits = []
    for kw in strategy['keywords']:
        if kw.lower() in low and kw not in hits:
            hits.append(kw)
    return hits


def _hint_from_text(text, hint):
    if hint:
        return hint
    blob = text or ''
    for name, key in NAME_TO_HINT:
        if name in blob:
            return key
    return None


def _score_strategy(strategy, text, hint):
    hits = _keyword_hits(strategy, text)
    score = KEYWORD_WEIGHT * len(hits)
    if hint and hint == strategy['id']:
        score += 12
    elif hint and (hint in strategy['hints'] or hint in (strategy['leg_a'], strategy['leg_b'])):
        score += HINT_WEIGHT
    return score, hits


def _z(strategy, quotes):
    qa = quotes.get(strategy['leg_a']) or {}
    qb = quotes.get(strategy['leg_b']) or {}
    pa, pb = qa.get('pct'), qb.get('pct')
    if pa is None or pb is None:
        return None
    return (pa - pb) / pair_sigma(strategy)


def select_strategy(text='', quotes=None, hint=None):
    """返回 (strategy, hits, reason)。reason ∈ keyword / hint / tape。"""
    quotes = normalize_quotes(quotes)
    hint = _hint_from_text(text, hint)
    if hint == 'tape':
        hint = None
        force_tape = True
    else:
        force_tape = False

    # 跨域口径：候选集只保留两腿分属不同域的策略（目录自检保证非空）。
    catalog = [s for s in STRATEGIES if is_cross_domain(s)] or list(STRATEGIES)
    ranked = []
    for s in catalog:
        score, hits = _score_strategy(s, text, hint)
        ranked.append((score, hits, s))
    best = max(x[0] for x in ranked) if ranked else 0
    if force_tape or best <= 0:
        tradable = [s for s in catalog if _z(s, quotes) is not None]
        if tradable:
            tradable.sort(key=lambda s: (-abs(_z(s, quotes)), s['id']))
            return tradable[0], [], 'tape'
        return catalog[0], [], 'tape'

    cands = [x for x in ranked if x[0] == best]
    cands.sort(key=lambda x: (-abs(_z(x[2], quotes) or 0.0), x[2]['id']))
    score, hits, strategy = cands[0]
    reason = 'keyword' if hits else 'hint'
    return strategy, hits, reason


def _leg(quotes, key):
    q = quotes.get(key) or {}
    return {'key': key, 'name': q.get('name') or NAMES.get(key, key), 'pct': q.get('pct')}


def _forecast_outlook(strategy, leg_a, leg_b, z):
    """Explainable short-horizon ensemble; not a live external model/API call.

    Uses a shrinkage blend of one-session tape momentum and pair mean-reversion,
    with strategy volatility parameters as a proxy because this module has no price history.
    """
    pa, pb = leg_a.get('pct'), leg_b.get('pct')
    if pa is None or pb is None:
        return {
            'risk': '行情缺失；宏观/资金流/情绪无数据。',
            'short': '数据不足，不预测方向/幅度/置信度。',
            'long': '数据不足，暂不判断。',
        }
    sigma = pair_sigma(strategy)
    spread_z = z or 0.0
    # Mix a damped tape signal with relative-value reversion; conservative shrinkage
    drift = 0.20 * (pa + pb) - 0.15 * spread_z * sigma
    vol_48h = math.sqrt(2.0) * math.sqrt(DAILY_SIGMA[strategy['leg_a']]**2 + DAILY_SIGMA[strategy['leg_b']]**2) / 2
    projected = max(-2.0 * vol_48h, min(2.0 * vol_48h, drift * 2.0))
    confidence = min(0.62, 0.35 + min(abs(spread_z), 2.0) * 0.08)
    direction = '偏上涨' if projected > 0.08 else ('偏下跌' if projected < -0.08 else '区间震荡')
    strength = '高' if abs(spread_z) >= 1.8 else ('中' if abs(spread_z) >= 1 else '低')
    risk = (f'波动{strength}（48h代理±{vol_48h:.2f}%）；资金流/情绪弱代理：{direction}；'
            '宏观未接日历。')
    short = (f'48h{direction}，中枢{_fmt_pct(projected)}，区间±{vol_48h:.2f}%，'
             f'置信度{confidence:.0%}。')
    long_dir = '中性偏多' if (pa + pb) > 0.2 else ('中性偏空' if (pa + pb) < -0.2 else '中性')
    long = f'48h后：{long_dir}（低置信度）。'
    return {'risk': risk, 'short': short, 'long': long}


def recommend(text='', quotes=None, hint=None):
    """为一条内容找到配对策略，给出两只标的，并按规则推荐。

    永远返回恰好两腿。行情不全时 action='no_data'，不编方向。
    """
    quotes = normalize_quotes(quotes)
    strategy, hits, reason = select_strategy(text, quotes, hint=hint)
    leg_a = _leg(quotes, strategy['leg_a'])
    leg_b = _leg(quotes, strategy['leg_b'])
    sigma = pair_sigma(strategy)
    pa, pb = leg_a['pct'], leg_b['pct']
    if pa is None or pb is None:
        spread = z = None
        action = 'no_data'
    else:
        spread = pa - pb
        z = spread / sigma
        if abs(z) < Z_ENTRY:
            action = 'wait'
        elif z <= -Z_ENTRY:
            action = 'long_a_short_b'
        else:
            action = 'short_a_long_b'

    a_name, b_name = leg_a['name'], leg_b['name']
    if action == 'no_data':
        stance = '数据不足'
        signal = f'{a_name} / {b_name} 当次涨跌幅不完整，配对价差无法计算。'
        recommendation = '不给出方向。两腿行情补齐后按同一规则复算，不回填历史点位。'
        confidence = 0.0
    elif action == 'wait':
        stance = '观望'
        signal = (f'{a_name} {_fmt_pct(pa)} · {b_name} {_fmt_pct(pb)} · '
                  f'相对价差 {_fmt_pp(spread)} 个百分点 · z={z:+.2f}。')
        recommendation = (f'价差未越过 1σ（残差阈值 {sigma:.2f} 个百分点），不建配对仓。')
        confidence = 0.38
    else:
        side = '轻仓' if abs(z) < Z_STRONG else '标准仓'
        signal = (f'{a_name} {_fmt_pct(pa)} · {b_name} {_fmt_pct(pb)} · '
                  f'相对价差 {_fmt_pp(spread)} 个百分点 · z={z:+.2f}。')
        if action == 'long_a_short_b':
            stance = f'做多{a_name} / 做空{b_name}'
            recommendation = f'{side}做多 {a_name}、做空 {b_name}。A 相对偏弱，等待价差回归。'
        else:
            stance = f'做空{a_name} / 做多{b_name}'
            recommendation = f'{side}做空 {a_name}、做多 {b_name}。A 相对偏强，等待价差回归。'
        confidence = 0.61 if abs(z) < Z_STRONG else 0.76
    if reason == 'tape':
        confidence = min(confidence, 0.50)
        why = ('正文未指向特定配对，在当次两腿齐全的跨域组合里取价差偏离最大者'
               f'（{domain_pair_label(strategy)}）。')
    elif hits:
        why = f'正文命中「{"、".join(hits[:3])}」，选定本跨域组合（{domain_pair_label(strategy)}）。'
    else:
        why = f'按本条内容的主题映射选定本跨域组合（{domain_pair_label(strategy)}）。'

    outlook = _forecast_outlook(strategy, leg_a, leg_b, z)
    return {
        'outlook': outlook,
        'strategy_id': strategy['id'],
        'strategy_name': strategy['name'],
        'family': '配对交易',
        'method': '相对收益均值回归',
        'logic': strategy['logic'],
        'action_id': strategy['id'],
        'cross_domain': True,
        'domain_a': DOMAIN[strategy['leg_a']],
        'domain_b': DOMAIN[strategy['leg_b']],
        'domain_pair': domain_pair_label(strategy),
        'leg_a': leg_a,
        'leg_b': leg_b,
        'pair_label': f'{a_name} / {b_name}',
        'spread': None if spread is None else round(spread, 4),
        'sigma': round(sigma, 4),
        'z': None if z is None else round(z, 2),
        'action': action,
        'stance': stance,
        'signal': signal,
        'recommendation': recommendation,
        'why': why,
        'rule': RULE,
        'confidence': round(confidence, 2),
        'hits': hits,
        'select_reason': reason,
    }


# ---------------------------------------------------------------------------
# 行情不足 → 整段隐藏（默认；网页 / 微信 / 迷你三档同一口径）
# ---------------------------------------------------------------------------
# 两腿涨跌幅缺任何一条时，本段除了「数据不足 / 不预测方向 / 暂不判断」没有任何信息，
# 铺在每条内容后面只是噪音 —— 因此**整段不渲染**（不是渲染成灰色占位）。
# 数据层不受影响：recommend() 照旧返回 action='no_data' 与 stance='数据不足'，
# 构建日志、自检、图表与调用方照旧可以据此判断；只是不再印到页面上。
# 内部排查需要看到这段文案时：QUANT_SHOW_NO_DATA=1
SHOW_NO_DATA_ENV = 'QUANT_SHOW_NO_DATA'


def show_no_data():
    """行情不足时是否仍渲染「数据不足」段：默认 False（整段隐藏）。"""
    return str(os.environ.get(SHOW_NO_DATA_ENV, '')).strip().lower() in ('1', 'true', 'yes', 'on')


def is_hidden(rec):
    """本条配对是否整段隐藏（rec 为空、或行情不足且未开排查开关时均为 True）。

    所有渲染函数对隐藏的 rec 一律返回空串；调用方（构建日志、名录版等）也可以据此判断。
    """
    if not rec:
        return True
    return rec.get('action') == 'no_data' and not show_no_data()


_HIDDEN_RENDERS = [0]          # 本次进程里被整段隐藏的处数（构建/推送日志用）


def hidden_render_count():
    return _HIDDEN_RENDERS[0]


def reset_hidden_render_count():
    _HIDDEN_RENDERS[0] = 0


def _gate(rec):
    """渲染入口统一闸门：True 表示不产出任何内容（行情不足即隐藏，并计数）。"""
    if not rec:
        return True
    if is_hidden(rec):
        _HIDDEN_RENDERS[0] += 1
        return True
    return False


def rule_note_web(label='AI 量化 · 配对交易'):
    """整页只印一次的配对口径（页脚），各卡片不再逐块重复。"""
    return f'{label}口径（全文各块共用）：{_esc(RULE)} {_esc(OUTLOOK_NOTE)}'


def render_web(rec, compact=False, note='', brief=False, show_rule=False):
    """网页版。compact=True 时收成一行，仍包含策略 / 标的组合 / 推荐。

    brief=True 只留策略 / 跨域组合 / 推荐（三大关注面与噪音小节已在上面出现过
    同一组标的与三行预测，不再重复铺一遍）。

    口径与免责说明默认不逐块重复：整页由 rule_note_web() 在页脚印一次
    （show_rule=True 可让单独出现的块自带规则）。

    行情不足（两腿涨跌幅不齐）时整段隐藏：返回空串，不渲染「数据不足」段。
    """
    if _gate(rec):
        return ''
    note_html = f'<div class="ai-quant-meta">{_esc(note)}</div>' if note else ''
    if compact:
        tail = ('' if brief else
                ' · 风险因子(48h)：' + _esc(rec['outlook']['risk'])
                + ' · 走势预测(48h)：' + _esc(rec['outlook']['short'])
                + ' · 未来展望：' + _esc(rec['outlook']['long']))
        return (
            '<div class="ai-quant ai-quant-compact" data-ai-quant="1">'
            '<strong>◆ AI 量化</strong> · 策略：' + _esc(rec['strategy_name'])
            + ' · 跨域组合：' + _esc(rec.get('domain_pair', '')) + ' · ' + _esc(rec['pair_label'])
            + ' · 推荐：' + _esc(rec['stance']) + '。' + _esc(rec['recommendation'])
            + tail
            + (f' <span class="ai-quant-meta">{_esc(note)}</span>' if note else '')
            + '</div>'
        )
    conf = int(round((rec.get('confidence') or 0) * 100))
    return (
        '<div class="ai-quant" data-ai-quant="1" data-strategy="' + _esc(rec['strategy_id']) + '"'
        ' data-domain-pair="' + _esc(rec.get('domain_pair', '')) + '">\n'
        '  <div class="ai-quant-title">◆ AI 量化 · 配对交易'
        '<span>跨域组合 · 对冲策略</span></div>\n'
        '  <ul class="ai-quant-list">\n'
        f'    <li><strong>策略：</strong>{_esc(rec["strategy_name"])}'
        f'（{_esc(rec["family"])} · {_esc(rec["method"])} · 跨域）</li>\n'
        f'    <li><strong>跨域组合：</strong>{_esc(rec.get("domain_pair", ""))} · '
        f'{_esc(rec["pair_label"])}</li>\n'
        f'    <li><strong>当次信号：</strong>{_esc(rec["signal"])}</li>\n'
        f'    <li><strong>推荐：</strong>{_esc(rec["stance"])}。{_esc(rec["recommendation"])}'
        f'（置信度 {conf}%）</li>\n'
        '  </ul>\n'
        f'  <div class="ai-quant-meta">{_esc(rec["why"])}</div>\n'
        + (f'  <div class="ai-quant-meta">口径：{_esc(rec["rule"])}</div>\n' if show_rule else '')
        + '  <ul class="ai-quant-list ai-quant-forecast">'
        f'<li><strong>风险因子预测 · 未来48小时：</strong>{_esc(rec["outlook"]["risk"])}</li>'
        f'<li><strong>走势预测 · 未来48小时：</strong>{_esc(rec["outlook"]["short"])}</li>'
        f'<li><strong>未来预测 · 48小时之后：</strong>{_esc(rec["outlook"]["long"])}</li>'
        '</ul>'
        f'{note_html}'
        '</div>'
    )


def render_web_list(recs, note=''):
    """一条内容里有多组标的时，合成一块 AI 量化，每组一行。

    口径与免责说明同样不在这里重复（整页由 rule_note_web() 印一次）。
    行情不足的那些条先被整段隐藏；全部隐藏时本块不产出内容。
    """
    recs = [r for r in (recs or []) if r and not _gate(r)]
    if not recs:
        return ''
    items = []
    for r in recs:
        items.append(
            '<li><strong>策略：</strong>' + _esc(r['strategy_name'])
            + ' · <strong>跨域组合：</strong>' + _esc(r.get('domain_pair', '')) + ' · ' + _esc(r['pair_label'])
            + ' · <strong>推荐：</strong>' + _esc(r['stance'])
            + '。' + _esc(r['recommendation'])
            + '<br/><strong>风险因子预测·未来48小时：</strong>' + _esc(r['outlook']['risk'])
            + '<br/><strong>走势预测·未来48小时：</strong>' + _esc(r['outlook']['short'])
            + '<br/><strong>未来预测·48小时之后：</strong>' + _esc(r['outlook']['long']) + '</li>'
        )
    tail = f'<div class="ai-quant-meta">{_esc(note)}</div>' if note else ''
    return (
        '<div class="ai-quant" data-ai-quant="1">\n'
        '  <div class="ai-quant-title">◆ AI 量化 · 配对交易<span>两标的组合</span></div>\n'
        '  <ul class="ai-quant-list">\n    ' + '\n    '.join(items) + '\n  </ul>\n'
        f'{tail}</div>'
    )


# ---------------------------------------------------------------------------
# 微信版的样式常量
# ---------------------------------------------------------------------------
# 微信单页有 10 万字符硬上限、95,000 推送门禁。整篇推送里挂着 50 块 AI 量化，
# 重复的内联样式与逐块重复的规则说明加起来是全文最大的一笔开销（实测占约四分之一）。
# 所以微信版做了两件纯样式/排版的瘦身：
#   ① 四行要点从四个 <div> 合成一段 <br/> 分隔的文本，省掉四组重复 style；
#   ② 规则说明不再逐块重复 —— 由 rule_note_wechat() 在推送里整篇只印一次
#      （show_rule=True 可让单独出现的块自带规则）。
# 网页版同样只在页脚印一次（rule_note_web()），口径与免责说明不再逐卡重复。
# AI 量化及栏目内容：统一采用中性灰度字体与弱对比边框/底色，减弱视觉权重，避免喧宾夺主。
_WX_BOX = ('background:#0d111c;color:#808080;border:1px solid #232730;border-left:2px solid #4d4d4d;'
           'border-radius:4px;padding:8px 10px;margin-top:8px;font-size:10.5px;line-height:1.66')
_WX_COMPACT = ('background:#0d111c;color:#808080;border:1px solid #22262f;border-radius:3px;'
               'padding:5px 8px;margin-top:6px;font-size:10.5px;line-height:1.62')
_WX_TITLE = 'color:#8e8e8e;font-weight:600;font-size:11px'
_WX_CHIP = ('background:#181b22;color:#858585;border:1px solid #2e323b;'
            'font-size:9.5px;font-weight:500;padding:1px 5px;margin-left:4px')
_WX_META = 'color:#6e6e6e;font-size:10px;margin-top:5px'


def rule_note_wechat(label='AI 量化 · 配对交易'):
    """整篇推送只印一次的配对口径说明（各处 AI 量化块共用同一口径）。"""
    return (f'<div style="{_WX_META}">{_esc(label)}口径（全文各块共用）：{_esc(RULE)} '
            f'{_esc(OUTLOOK_NOTE)}</div>')


def render_wechat(rec, compact=False, note='', show_rule=False, brief=False):
    """微信版：全内联样式，口径与网页版一致。

    show_rule 默认关闭：口径说明由 rule_note_wechat() 在整篇里印一次，
    避免同一段规则在 20 多个块里重复（微信单页字符预算很紧）。
    brief=True 只留策略 / 跨域组合 / 推荐（与网页端同一口径）。

    与网页版同一闸门：行情不足（两腿涨跌幅不齐）时整段隐藏，返回空串。
    """
    if _gate(rec):
        return ''
    note_html = (f'<div style="color:#6e6e6e;font-size:10px;margin-top:4px;">{_esc(note)}</div>'
                 if note else '')
    if compact:
        tail = ('' if brief else
                ' · 风险因子(48h)：' + _esc(rec['outlook']['risk'])
                + ' · 走势预测(48h)：' + _esc(rec['outlook']['short'])
                + ' · 未来展望：' + _esc(rec['outlook']['long']))
        return (
            f'<div style="{_WX_COMPACT}">'
            '<strong style="color:#8e8e8e;">◆ AI 量化</strong> · 策略：' + _esc(rec['strategy_name'])
            + ' · 跨域组合：' + _esc(rec.get('domain_pair', '')) + ' · ' + _esc(rec['pair_label'])
            + ' · 推荐：' + _esc(rec['stance']) + '。' + _esc(rec['recommendation'])
            + tail
            + ((' · ' + _esc(note)) if note else '')
            + '</div>'
        )
    conf = int(round((rec.get('confidence') or 0) * 100))
    return (
        f'<div style="{_WX_BOX}">'
        f'<div style="{_WX_TITLE}">◆ AI 量化 · 配对交易'
        f'<span style="{_WX_CHIP}">跨域组合 · 对冲策略</span></div>'
        f'◦ <strong>策略：</strong>{_esc(rec["strategy_name"])}'
        f'（{_esc(rec["family"])} · {_esc(rec["method"])} · 跨域）<br/>'
        f'◦ <strong>跨域组合：</strong>{_esc(rec.get("domain_pair", ""))} · '
        f'{_esc(rec["pair_label"])}<br/>'
        f'◦ <strong>当次信号：</strong>{_esc(rec["signal"])}<br/>'
        f'◦ <strong>推荐：</strong>{_esc(rec["stance"])}。{_esc(rec["recommendation"])}'
        f'（置信度 {conf}%）<br/>'
        f'◦ <strong>风险因子预测·未来48小时：</strong>{_esc(rec["outlook"]["risk"])}<br/>'
        f'◦ <strong>走势预测·未来48小时：</strong>{_esc(rec["outlook"]["short"])}<br/>'
        f'◦ <strong>未来预测·48小时之后：</strong>{_esc(rec["outlook"]["long"])}'
        f'<div style="{_WX_META}">{_esc(rec["why"])}</div>'
        + (f'<div style="{_WX_META}">口径：{_esc(rec["rule"])}</div>' if show_rule else '')
        + f'{note_html}</div>'
    )


_WX_MINI = ('background:#0d111c;color:#7d7d7d;border:1px solid #22262f;'
            'border-radius:3px;padding:4px 7px;margin-top:5px;font-size:10px;line-height:1.58')


def render_wechat_mini(rec):
    """微信「超紧凑」版：一行给出策略 / 两标的跨域组合 / 推荐与置信度。

    48 小时风险与走势三行仍完整保留在**网页版**与其它栏目的完整块里；
    社区从 14 源扩到 49 源后，同一段三行预测在一页里要重复 40 多次，
    因此在预算吃紧的社区区块用这一版，口径与完整版完全一致（同一份 rec）。
    行情不足时与完整版一样整段隐藏（迷你版也不留「数据不足」字样）。
    """
    if _gate(rec):
        return ''
    conf = int(round((rec.get('confidence') or 0) * 100))
    z = rec.get('z')
    zs = f'z={z:+.2f} · ' if z is not None else ''
    return (
        f'<div style="{_WX_MINI}">'
        f'◆ AI 量化｜{_esc(rec["strategy_name"])}｜跨域 {_esc(rec.get("domain_pair", ""))}｜'
        f'{_esc(rec["pair_label"])}｜推荐：{_esc(rec["stance"])}（{zs}置信度 {conf}%）</div>'
    )


def render_wechat_list(recs, note=''):
    """多组标的合成一块微信版；行情不足的那些条整段隐藏，全隐藏则不出块。"""
    recs = [r for r in (recs or []) if r and not _gate(r)]
    if not recs:
        return ''
    rows = '<br/>'.join(
        '◦ <strong>策略：</strong>' + _esc(r['strategy_name'])
        + ' · <strong>跨域组合：</strong>' + _esc(r.get('domain_pair', '')) + ' · ' + _esc(r['pair_label'])
        + ' · <strong>推荐：</strong>' + _esc(r['stance'])
        + '。' + _esc(r['recommendation'])
        + '<br/>风险因子预测·未来48小时：' + _esc(r['outlook']['risk'])
        + '<br/>走势预测·未来48小时：' + _esc(r['outlook']['short'])
        + '<br/>未来预测·48小时之后：' + _esc(r['outlook']['long'])
        for r in recs
    )
    note_html = (f'<div style="color:#6e6e6e;font-size:10px;margin-top:4px;">{_esc(note)}</div>'
                 if note else '')
    return (
        f'<div style="{_WX_BOX}">'
        f'<div style="{_WX_TITLE}">◆ AI 量化 · 配对交易'
        f'<span style="{_WX_CHIP}">两标的组合</span></div>'
        + rows + note_html + '</div>'
    )


def _self_test():
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(('  ✅ ' if cond else '  ❌ ') + msg)
        ok = ok and bool(cond)

    empty = recommend('随便一条没有主题的内容', {})
    check(empty['action'] == 'no_data', '无行情：不给方向')
    check(empty['leg_a']['key'] and empty['leg_b']['key'], '无行情：仍然给出两只标的')
    check(empty['leg_a']['key'] != empty['leg_b']['key'], '两腿不是同一只')
    check('%' not in empty['signal'], '无行情：信号里不编涨跌幅')
    check('25,440' not in json.dumps(empty, ensure_ascii=False), '无行情：不回填历史点位')
    check(is_hidden(empty), '行情不足：is_hidden 判为整段隐藏')
    for blob in (render_web(empty), render_web(empty, compact=True),
                 render_wechat(empty), render_wechat(empty, compact=True),
                 render_wechat_mini(empty),
                 render_web_list([empty]), render_wechat_list([empty])):
        check(blob == '', '行情不足：整段隐藏（不渲染「数据不足」段）')

    quotes = {'quotes': {
        'HSTECH': {'name': '恒生科技指数', 'pct': 3.0},
        'HSI': {'name': '恒生指数', 'pct': 0.2},
        'WTI': {'name': 'WTI 原油', 'pct': 2.4},
        'BRENT': {'name': '布伦特原油', 'pct': 0.3},
        'GOLD': {'name': '现货黄金', 'pct': 0.4},
        'USDCNH': {'name': '美元/离岸人民币', 'pct': -0.05},
        'USDCNY': {'name': '美元/在岸人民币', 'pct': -0.04},
        'SPX': {'name': '标普 500', 'pct': 0.2},
        'NDQ': {'name': '纳斯达克', 'pct': 0.3},
        'DJI': {'name': '道琼斯', 'pct': 0.1},
        'HSCE': {'name': '恒生中国企业指数', 'pct': 0.4},
    }}
    growth = recommend('恒生科技相对恒指的成长轮动', quotes, hint='rotation')
    check(growth['strategy_id'] == 'hk_tech_us_tech',
          f'成长轮动选中港股科技×美股科技（{growth["strategy_id"]}）')
    check(growth['cross_domain'] and growth['domain_pair'] == '港股 × 美股',
          f'渲染前就是跨域组合（{growth["domain_pair"]}）')
    check(growth['action'] == 'short_a_long_b', '港股科技明显强于纳指 → 空A多B')
    check('做多' in growth['stance'] and '做空' in growth['stance'], '推荐同时给出多空两腿')

    flat = recommend('恒生科技 纳指', {
        'HSTECH': {'pct': 0.05}, 'NDQ': {'pct': 0.04},
    }, hint='hk_tech_us_tech')
    check(flat['action'] == 'wait', '价差在 1σ 内 → 观望')

    oil = recommend('富途社区在讨论原油、WTI 与炼厂价差', quotes, hint='FUTU')
    check(oil['strategy_id'] == 'oil_hk', f'主题词覆盖频道默认（{oil["strategy_id"]}）')
    check(oil['domain_pair'] == '能源 × 港股', '原油主题落到能源×港股的跨域组合')

    fed = recommend('美联储利率路径与黄金', quotes, hint='fed')
    check(fed['strategy_id'] == 'gold_fx', f'利率/黄金选中黄金×离岸人民币（{fed["strategy_id"]}）')
    check(fed['domain_pair'] == '贵金属 × 汇率', '黄金主题落到贵金属×汇率的跨域组合')

    tape = recommend('没有任何主题词的一段盘面描述', quotes, hint='tape')
    check(tape['cross_domain'] and tape['leg_a']['key'] != tape['leg_b']['key'],
          'tape 兜底也必须是跨域两只标的')

    check(not is_hidden(oil), '行情齐全：正常渲染')
    check('数据不足' not in render_web(oil) and '数据不足' not in render_wechat(oil),
          '行情齐全：不出现「数据不足」字样')
    web, wx = render_web(oil), render_wechat(oil)
    for blob in (web, wx, render_web(oil, compact=True), render_wechat(oil, compact=True)):
        check('AI 量化' in blob and '跨域组合' in blob and '推荐' in blob, '渲染包含跨域组合要素')
    wx_all = (wx + render_wechat(oil, compact=True, note='附注')
              + render_wechat_mini(oil) + render_wechat_list([oil], note='附注')
              + rule_note_wechat())
    check(all(loud not in wx_all for loud in ('#4fe5ff', '#ff4d9a', '#b6ff4a', '#dce5fb', '#6f55b8')),
          'AI 量化及栏目内容采用中性灰度字体，不再使用高亮霓虹色')
    check('color:#808080' in wx and 'color:#8e8e8e' in wx, 'AI 量化微信块标题与正文均为灰度减弱显示')
    check('data-ai-quant="1"' in web and 'data-domain-pair=' in web, '网页块可被计数与核对域对')
    check(not cross_domain_catalog_errors(),
          '策略目录自检：每条策略都跨域、组合不重复'
          + (f'（{cross_domain_catalog_errors()}）' if cross_domain_catalog_errors() else ''))
    check(all(is_cross_domain(st) for st in STRATEGIES), '策略目录里不存在同域价差组合')
    check(all(DOMAIN[st['leg_a']] in DOMAIN_LABEL and DOMAIN[st['leg_b']] in DOMAIN_LABEL
              for st in STRATEGIES), '每条策略的域都有中文标签')
    os.environ[SHOW_NO_DATA_ENV] = '1'          # 内部排查：临时恢复「数据不足」段
    try:
        restored = render_web(empty)
        restored_shown = not is_hidden(empty)
    finally:
        os.environ.pop(SHOW_NO_DATA_ENV, None)
    check('数据不足' in restored and restored_shown,
          f'{SHOW_NO_DATA_ENV}=1 可临时恢复「数据不足」段（默认隐藏），关闭开关后仍整段隐藏')
    check(render_web(empty) == '', '开关关闭后回到整段隐藏')
    print('\n' + ('✅ quant_pair 自检全部通过' if ok else '❌ quant_pair 自检存在失败项'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description='AI 量化 · 跨域配对交易（为一条内容选策略并给出两只跨域标的）')
    ap.add_argument('--text', default='', help='内容正文，用来选策略')
    ap.add_argument('--hint', default='', help='主题 key（如 rotation / commodities / FUTU）')
    ap.add_argument('--quotes', default='', help='market_data.json 路径，缺省则只选策略不给方向')
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()
    if args.self_test:
        sys.exit(_self_test())
    quotes = {}
    if args.quotes and os.path.exists(args.quotes):
        with open(args.quotes, encoding='utf-8') as f:
            quotes = json.load(f)
    rec = recommend(args.text, quotes, hint=args.hint or None)
    print(json.dumps(rec, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
