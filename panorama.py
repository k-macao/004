#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 01 栏「每日全球全景扫描」推理引擎 (panorama.py)

栏目定位（本栏已替换掉原先那段只讲「多模型协同」的静态说明文字）：
    扫一遍今天全球市场，总结推动股价的 5 大力量。重点关注宏观事件、板块轮动、情绪变化。
    哪些是重点，哪些是噪音。如何利好利空。是否可以做多。

设计原则（与 02 / 03 / 03B 栏同一套反陈旧口径）：
  • **零写死叙事**：本模块不存放任何新闻事实、点位、日期。所有结论都由当次构建的
    market_data.json / macro_data.json / sentiment_data.json / community_data.json 推导；
    数据缺失就降级为「未获取」，绝不回填历史文案（2026-09-16 旧内容事故的长期防线）。
  • **可复算**：力量排序、重点/噪音判定、利好利空、做多结论全部是显式打分规则，
    每条结论都挂着推导它的证据（行情读数或当次快讯标题 + 发布日期）。
  • **纯标准库 + 纯函数**：不联网、不落盘，构建期由 build_site.py / tools/wechat_push.py
    直接调用，因此不需要改 CI workflow（GitHub App 无 workflows 权限）。

打分口径（三因子加权，全部 0~1 归一后放大到 0~100）：
    score = 100 × (0.45 × 幅度 mag + 0.30 × 印证度 corr + 0.25 × 时效 fresh)
      mag   行情用 |涨跌幅| / 该资产的显著性阈值；快讯用命中条数密度
      corr  独立证据条数 / 3（多资产或多条快讯互相印证才算高）
      fresh 行情为当次抓取 = 1.0；快讯按发布日衰减（当天 1.0，每往前 1 天 −0.15）
    方向 direction ∈ {+1 利好, 0 中性, −1 利空}，由价格方向与标题情感（sentiment_nlp）加权得出。

重点 / 噪音判定：
    score ≥ 55            → 重点（主推动力量）
    35 ≤ score < 55        → 次要（有信息量但不足以改变仓位）
    score < 35 或 幅度低于噪音阈值且只有单一证据 → 噪音（当日不参与决策）

用法:
  python3 panorama.py                 # 读取仓库内 4 份 json（缺哪份就降级哪块）→ 文本摘要
  python3 panorama.py --json out.json # 导出结构化扫描结果（便于回测/核对）
  python3 panorama.py --self-test     # 规则自检（含极端行情与空数据）
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import sentiment_nlp as nlp                       # 复用日报同一套中文情感词库
except Exception:                                     # pragma: no cover - 词库缺失时降级为纯行情推导
    nlp = None

import quant_pair                                     # 每条力量后的 AI 量化配对（网页/微信同一口径）

MINUS = '\u2212'

# ---------------------------------------------------------------------------
# 噪音阈值：单日涨跌幅绝对值低于该值 = 日内噪音（不同资产波动率不同，阈值分开给）
# 显著性阈值：达到该值即视为「幅度满分」，用于把涨跌幅归一到 0~1
# ---------------------------------------------------------------------------
NOISE_PCT = {
    'HSI': 0.35, 'HSTECH': 0.50, 'HSCE': 0.35,
    'SPX': 0.30, 'NDQ': 0.40, 'DJI': 0.30,
    'GOLD': 0.40, 'WTI': 0.80, 'BRENT': 0.80,
    'USDCNH': 0.12, 'USDCNY': 0.12,
}
FULL_PCT = {
    'HSI': 1.6, 'HSTECH': 2.4, 'HSCE': 1.6,
    'SPX': 1.2, 'NDQ': 1.6, 'DJI': 1.2,
    'GOLD': 1.6, 'WTI': 3.0, 'BRENT': 3.0,
    'USDCNH': 0.5, 'USDCNY': 0.5,
}
# 轮动价差阈值（百分点）：低于此值视为「无有效轮动」，避免把噪音讲成故事
ROTATION_TOL = 0.30

# 力量在「是否可以做多」合成分里的权重（本报告以港股为落点，故港股盘面权重最高）
FORCE_WEIGHT = {
    'hk_tape': 1.20,
    'global_risk': 1.00,
    'fed_liquidity': 1.00,
    'macro_growth': 0.90,
    'sentiment': 0.90,
    'commodities': 0.70,
    'rotation': 0.80,
    'institution': 0.50,
}

TIER_KEY = {'重点': 0, '次要': 1, '噪音': 2}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def fmt_pct(v, dash='\u2014'):
    """0.83 → '+0.83%'；−0.83 → '−0.83%'（U+2212 真减号，与全站一致）。"""
    v = _num(v)
    if v is None:
        return dash
    return f"{MINUS if v < 0 else '+'}{abs(v):,.2f}%"


def fmt_last(q, dash='\u2014'):
    v = _num((q or {}).get('last'))
    if v is None:
        return dash
    nd = int((q or {}).get('decimals') or 2)
    if (q or {}).get('name') == '现货黄金' and v >= 1000:
        nd = 0
    return f'{v:,.{nd}f}'


def md_cn(date_str):
    """'2026-09-17' → '9 月 17 日'；无法解析时原样返回。"""
    if date_str and re.match(r'20\d{2}-\d{2}-\d{2}$', str(date_str)):
        return f'{int(date_str[5:7])} 月 {int(date_str[8:10])} 日'
    return date_str or '日期未标注'


def _clip01(v):
    return max(0.0, min(1.0, v))


def _freshness(date_str, now):
    """当天 1.0，每往前 1 天 −0.15，最低 0.25；无日期按 0.4 处理（时效不可核验）。"""
    if not date_str or not re.match(r'20\d{2}-\d{2}-\d{2}$', str(date_str)):
        return 0.4
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.4
    age = max(0, (now.date() - d.date()).days)
    return _clip01(1.0 - 0.15 * age) if age <= 5 else 0.25


def _score(mag, corr, fresh):
    return round(100.0 * (0.45 * _clip01(mag) + 0.30 * _clip01(corr) + 0.25 * _clip01(fresh)), 1)


NOISE_CAP = 30.0   # 未过噪音阈值的力量，分数一律压到 30 以下，避免出现「噪音 63 分」的自相矛盾展示


def _tier(score, weak):
    """weak=True 表示幅度未过噪音阈值且证据单一 → 直接进噪音清单。"""
    if weak or score < 35:
        return '噪音'
    return '重点' if score >= 55 else '次要'


def _apply_noise_cap(score, weak):
    """噪音力量的展示分与决策分一并压到 NOISE_CAP 以下（比例压缩，保留组内相对顺序）。"""
    return round(min(score, NOISE_CAP * (0.5 + 0.5 * min(1.0, score / 100.0))), 1) if weak else score


def _title_senti(titles):
    """用日报同一套中文词库给快讯标题打情感；无词库时返回 0（不臆测方向）。"""
    if not nlp or not titles:
        return 0.0
    vals = []
    for t in titles:
        try:
            vals.append(float(nlp.score_text(t).get('sentiment') or 0.0))
        except Exception:                                   # pragma: no cover
            continue
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def _dir_word(direction):
    return {1: '利好', -1: '利空', 0: '中性'}[direction]


# ---------------------------------------------------------------------------
# 数据读取（全部可缺失）
# ---------------------------------------------------------------------------
def _pct(quotes, key):
    return _num((quotes.get(key) or {}).get('pct'))


def _items(macro, cat):
    return ((macro.get('categories') or {}).get(cat) or {}).get('items') or []


def _evidence_from_items(items, limit=3):
    out = []
    for it in items[:limit]:
        ttl = (it.get('title') or '').strip()
        if not ttl:
            continue
        out.append({'kind': 'news', 'date': it.get('published_date') or '',
                    'text': ttl, 'detail': (it.get('snippet') or '').strip()[:60]})
    return out


def _quote_ev(quotes, key, label):
    q = quotes.get(key) or {}
    if q.get('pct') is None and q.get('last') is None:
        return None
    return {'kind': 'quote', 'date': q.get('as_of') or '', 'symbol': key,
            'text': f"{label} {fmt_last(q)}（{fmt_pct(q.get('pct'))}）",
            'pct': _num(q.get('pct'))}


# ---------------------------------------------------------------------------
# 五大力量候选：每个 builder 返回 force dict 或 None（数据不足即不编故事）
# ---------------------------------------------------------------------------
def _force_hk_tape(quotes, macro, now):
    evs = [e for e in (_quote_ev(quotes, 'HSI', '恒指'),
                       _quote_ev(quotes, 'HSTECH', '恒科'),
                       _quote_ev(quotes, 'HSCE', '恒生国企')) if e]
    news = _evidence_from_items(_items(macro, 'hk'), 2)
    if not evs and not news:
        return None
    pcts = [e['pct'] for e in evs if e.get('pct') is not None]
    avg = sum(pcts) / len(pcts) if pcts else None
    mag = _clip01(abs(avg) / FULL_PCT['HSI']) if avg is not None else 0.0
    weak = (avg is not None and abs(avg) < NOISE_PCT['HSI'] and len(pcts) <= 1 and not news)
    fresh = 1.0 if evs else _freshness((news[0].get('date') if news else ''), now)
    corr = (len(pcts) + len(news)) / 3.0
    senti = _title_senti([n['text'] for n in news])
    direction = 0
    if avg is not None and abs(avg) >= NOISE_PCT['HSI']:
        direction = 1 if avg > 0 else -1
    elif senti:
        direction = 1 if senti > 0.12 else (-1 if senti < -0.12 else 0)
    read_bits = []
    if avg is not None:
        read_bits.append(f'港股三大指数当日均值 {fmt_pct(avg)}'
                         + ('（幅度低于 %.2f%% 噪音阈值，属日内扰动）' % NOISE_PCT['HSI']
                            if abs(avg) < NOISE_PCT['HSI'] else '（已越过噪音阈值，属有效方向）'))
    if news:
        read_bits.append(f"当次港股快讯 {len(_items(macro, 'hk'))} 条，最新一条 {md_cn(news[0]['date'])}")
    return {
        'key': 'hk_tape', 'name': '港股盘面本身（恒指 / 恒科 / 国企的当日方向）',
        'category': '市场盘面', 'direction': direction,
        'score': _score(mag, corr, fresh), 'weak': weak,
        'evidence': evs + news,
        'read': '；'.join(read_bits) or '港股行情与快讯当次均未获取，本力量不参与排序。',
        'impact': ('利好方向：指数站上前收盘且恒科同向放大，说明离岸资金在加仓而非减仓；'
                   '利空方向：恒科弱于恒指说明成长端在失血，反弹多为权重护盘。'),
    }


def _force_global_risk(quotes, now):
    evs = [e for e in (_quote_ev(quotes, 'SPX', '标普'),
                       _quote_ev(quotes, 'NDQ', '纳指'),
                       _quote_ev(quotes, 'DJI', '道指')) if e]
    pcts = [e['pct'] for e in evs if e.get('pct') is not None]
    if not pcts:
        return None
    avg = sum(pcts) / len(pcts)
    same_dir = len({1 if p > 0 else (-1 if p < 0 else 0) for p in pcts}) == 1
    mag = _clip01(abs(avg) / FULL_PCT['SPX'])
    weak = abs(avg) < NOISE_PCT['SPX'] and not same_dir
    corr = (len(pcts) / 3.0) * (1.0 if same_dir else 0.6)
    direction = 0 if abs(avg) < NOISE_PCT['SPX'] else (1 if avg > 0 else -1)
    return {
        'key': 'global_risk', 'name': '全球风险偏好（美股三大指数对港股的隔夜映射）',
        'category': '市场盘面', 'direction': direction,
        'score': _score(mag, corr, 1.0), 'weak': weak,
        'evidence': evs,
        'read': (f'美股三指均值 {fmt_pct(avg)}，'
                 + ('三指同向，隔夜风险偏好一致' if same_dir else '三指分歧，属结构性行情而非系统性方向')
                 + f'；对港股的映射优先看纳指（{fmt_pct(_pct(quotes, "NDQ"))}）与恒科的联动。'),
        'impact': ('利好方向：美股同向上涨会抬升港股开盘的风险预算，恒科弹性最大；'
                   '利空方向：美股杀估值时离岸资金撤退更快，港股通常先跌于 A 股。'),
    }


def _force_fed_liquidity(quotes, macro, now):
    news = _evidence_from_items(_items(macro, 'fed'), 3)
    fx = _quote_ev(quotes, 'USDCNH', '美元/离岸人民币')
    gold = _quote_ev(quotes, 'GOLD', '黄金')
    evs = [e for e in (fx, gold) if e] + news
    if not evs:
        return None
    fx_pct = _num(fx.get('pct')) if fx else None
    senti = _title_senti([n['text'] for n in news])
    # 离岸人民币升值（USDCNH 下跌）= 离岸流动性宽松 → 利好港股
    direction = 0
    if fx_pct is not None and abs(fx_pct) >= NOISE_PCT['USDCNH']:
        direction = -1 if fx_pct > 0 else 1
    if senti:
        s_dir = 1 if senti > 0.12 else (-1 if senti < -0.12 else 0)
        direction = s_dir if direction == 0 else (direction if s_dir in (0, direction) else 0)
    mag = _clip01(abs(fx_pct) / FULL_PCT['USDCNH']) if fx_pct is not None else _clip01(len(news) / 3.0)
    weak = (fx_pct is not None and abs(fx_pct) < NOISE_PCT['USDCNH'] and not news)
    fresh = _freshness(news[0]['date'], now) if news else 1.0
    corr = (len(news) + (1 if fx else 0) + (1 if gold else 0)) / 3.0
    bits = []
    if fx:
        bits.append(f"离岸人民币 {fmt_last(quotes.get('USDCNH'))}（{fmt_pct(fx_pct)}，"
                    + ('贬值压缩港股流动性' if (fx_pct or 0) > 0 else '升值释放离岸流动性') + '）')
    if gold:
        bits.append(f"黄金 {fmt_last(quotes.get('GOLD'))}（{fmt_pct(gold.get('pct'))}）"
                    f"反映的实际利率与避险定价")
    if news:
        bits.append(f"当次利率与流动性快讯 {len(_items(macro, 'fed'))} 条，最新 {md_cn(news[0]['date'])}"
                    f"，标题情感均值 {senti:+.2f}")
    if fx_pct is not None and abs(fx_pct) < NOISE_PCT['USDCNH']:
        bits.append(f'汇率波动 {fmt_pct(fx_pct)} 未过 {NOISE_PCT["USDCNH"]:.2f}% 噪音阈值，'
                    f'方向以快讯情感为准')
    return {
        'key': 'fed_liquidity', 'name': '美元利率与离岸流动性（联储路径 → 人民币 → 港股水位）',
        'category': '宏观事件', 'direction': direction,
        'score': _score(mag, corr, fresh), 'weak': weak, 'evidence': evs,
        'read': '；'.join(bits),
        'impact': ('利好方向：降息预期升温 + 人民币企稳，港股分母端改善，高估值成长弹性最大；'
                   '利空方向：美元与离岸人民币同步走强会抽离港元流动性，高息股相对抗跌。'),
    }


def _force_macro_growth(macro, now):
    items = _items(macro, 'macro')
    if not items:
        return None
    news = _evidence_from_items(items, 3)
    senti = _title_senti([n['text'] for n in news])
    direction = 1 if senti > 0.12 else (-1 if senti < -0.12 else 0)
    fresh = _freshness(news[0]['date'], now) if news else 0.4
    mag = _clip01(len(items) / 4.0) * (0.5 + 0.5 * min(1.0, abs(senti) / 0.4))
    corr = len(items) / 3.0
    weak = len(items) <= 1 and abs(senti) < 0.12
    return {
        'key': 'macro_growth', 'name': '宏观事件与全球增速（央行 / 经济数据 / 政策）',
        'category': '宏观事件', 'direction': direction,
        'score': _score(mag, corr, fresh), 'weak': weak, 'evidence': news,
        'read': (f'当次窗口内宏观快讯 {len(items)} 条，标题情感均值 {senti:+.2f}'
                 f'（{"偏正面" if senti > 0.12 else "偏负面" if senti < -0.12 else "中性、无一致方向"}）；'
                 f'最新一条发布于 {md_cn(news[0]["date"]) if news else "—"}。'),
        'impact': ('利好方向：增长与政策组合改善会先修复估值再修复盈利，指数级机会；'
                   '利空方向：数据走弱叠加政策空窗，指数上方空间被压缩，只剩结构性行情。'),
    }


def _force_commodities(quotes, macro, now):
    evs = [e for e in (_quote_ev(quotes, 'WTI', 'WTI 原油'),
                       _quote_ev(quotes, 'BRENT', '布伦特'),
                       _quote_ev(quotes, 'GOLD', '黄金')) if e]
    news = _evidence_from_items(_items(macro, 'commodities'), 2)
    if not evs and not news:
        return None
    oil = [e['pct'] for e in evs if e.get('symbol') in ('WTI', 'BRENT') and e.get('pct') is not None]
    gold_pct = next((e['pct'] for e in evs if e.get('symbol') == 'GOLD'), None)
    oil_avg = sum(oil) / len(oil) if oil else None
    # 油价与金价同涨 = 地缘/成本冲击，对权益是利空；油价回落 = 通胀压力缓解，利好
    direction = 0
    if oil_avg is not None and abs(oil_avg) >= NOISE_PCT['WTI']:
        direction = -1 if oil_avg > 0 else 1
        if gold_pct is not None and oil_avg > 0 and gold_pct > NOISE_PCT['GOLD']:
            direction = -1
    mag = _clip01(abs(oil_avg) / FULL_PCT['WTI']) if oil_avg is not None else _clip01(len(news) / 3.0)
    weak = (oil_avg is not None and abs(oil_avg) < NOISE_PCT['WTI'] and not news)
    corr = (len(oil) + (1 if gold_pct is not None else 0) + len(news)) / 3.0
    fresh = 1.0 if evs else _freshness(news[0]['date'], now)
    bits = []
    if oil_avg is not None:
        bits.append(f'油价均值 {fmt_pct(oil_avg)}'
                    + ('（未过 %.1f%% 噪音阈值）' % NOISE_PCT['WTI'] if abs(oil_avg) < NOISE_PCT['WTI'] else ''))
    if gold_pct is not None:
        bits.append(f'黄金 {fmt_pct(gold_pct)}（避险 / 实际利率读数）')
    if news:
        bits.append(f'供应链与商品快讯 {len(_items(macro, "commodities"))} 条')
    return {
        'key': 'commodities', 'name': '大宗商品与供应链（输入性成本 / 地缘溢价）',
        'category': '宏观事件', 'direction': direction,
        'score': _score(mag, corr, fresh), 'weak': weak, 'evidence': evs + news,
        'read': '；'.join(bits) or '商品行情与快讯当次未获取。',
        'impact': ('利好方向：油价回落压低输入性通胀，航空、物流与下游制造的毛利预期修复；'
                   '利空方向：油金同涨通常对应地缘风险溢价，权益整体杀估值、只有能源与黄金股受益。'),
    }


def _force_rotation(quotes, now):
    hk_tech = _pct(quotes, 'HSTECH')
    hk = _pct(quotes, 'HSI')
    ndq = _pct(quotes, 'NDQ')
    dji = _pct(quotes, 'DJI')
    spreads = []
    if hk_tech is not None and hk is not None:
        spreads.append(('港股内部：恒科 − 恒指', hk_tech - hk))
    if ndq is not None and dji is not None:
        spreads.append(('美股内部：纳指 − 道指', ndq - dji))
    if not spreads:
        return None
    avg = sum(s for _, s in spreads) / len(spreads)
    mag = _clip01(abs(avg) / 1.2)
    weak = abs(avg) < ROTATION_TOL
    corr = len(spreads) / 2.0
    direction = 0 if weak else (1 if avg > 0 else -1)
    if weak:
        label = f'两地成长 − 价值价差均值 {avg:+.2f} 个百分点，低于 {ROTATION_TOL:.2f} 的轮动阈值 → 当日无有效轮动，属噪音'
    elif avg > 0:
        label = f'资金向成长/科技端集中（价差均值 {avg:+.2f} 个百分点）'
    else:
        label = f'资金向价值/高息防御端切换（价差均值 {avg:+.2f} 个百分点）'
    return {
        'key': 'rotation', 'name': '板块轮动（成长 vs 价值的当日资金方向）',
        'category': '板块轮动', 'direction': direction,
        'score': _score(mag, corr, 1.0), 'weak': weak,
        'evidence': [{'kind': 'spread', 'date': '', 'text': f'{n} = {v:+.2f} 个百分点'} for n, v in spreads],
        'read': label,
        'impact': ('成长端领先时，做多应放在算力、半导体、平台经济等高 beta；'
                   '价值端领先时，仓位应偏向高息、公用事业与资源，指数多为权重护盘而非普涨。'),
    }


def _force_sentiment(sent, community, now):
    m = (sent or {}).get('market') or {}
    counts = _community_counts(community)
    have_sent = bool(m.get('news_count'))
    have_comm = sum(counts.values()) > 0
    if not have_sent and not have_comm:
        return None
    evs = []
    temp = _num(m.get('sent_temp'))
    net = _num(m.get('net_senti'))
    neg = _num(m.get('neg_share'))
    risk = _num(m.get('risk_score')) or 0.0
    heat_z = _num(m.get('heat_z'))
    if have_sent:
        evs.append({'kind': 'factor', 'date': (sent or {}).get('fetch_date', ''),
                    'text': (f"舆情温度计 {temp}（{m.get('label', '—')}）· 净情感 {net} · "
                             f"负面占比 {neg}% · 热度 {heat_z}σ · 风险分 {risk}")})
    if have_comm:
        evs.append({'kind': 'community', 'date': (community or {}).get('fetch_date', ''),
                    'text': (f"社区多空：偏多 {counts['bull']} / 偏空 {counts['bear']} / "
                             f"中性 {counts['neutral']} / 分歧 {counts['mixed']}（共 {sum(counts.values())} 源）")})
    # 方向：温度计偏离 50 与社区多空差共同决定
    d_sent = 0 if temp is None else (1 if temp >= 58 else (-1 if temp <= 42 else 0))
    diff = counts['bull'] - counts['bear']
    tot = max(1, sum(counts.values()))
    d_comm = 1 if diff / tot >= 0.2 else (-1 if diff / tot <= -0.2 else 0)
    direction = d_sent if d_comm == 0 else (d_comm if d_sent == 0 else (d_sent if d_sent == d_comm else 0))
    mag_parts = []
    if temp is not None:
        mag_parts.append(_clip01(abs(temp - 50) / 22.0))
    if have_comm:
        mag_parts.append(_clip01(abs(diff) / max(3.0, tot * 0.5)))
    mag = sum(mag_parts) / len(mag_parts) if mag_parts else 0.0
    weak = mag < 0.2 and risk < 40
    corr = (int(have_sent) + int(have_comm) + (1 if risk >= 40 else 0)) / 3.0
    bits = []
    if have_sent:
        bits.append(f"{m.get('news_count')} 条新闻合成的温度计 {temp}（{m.get('label', '—')}）"
                    f"，负面占比 {neg}%，突发风险分 {risk}")
    if have_comm:
        bits.append(f"{sum(counts.values())} 个社区研判净多空 {diff:+d} 家")
    if not bits:
        bits.append('舆情与社区两路当次均未获取')
    return {
        'key': 'sentiment', 'name': '情绪变化（舆情温度计 + 多平台社区多空）',
        'category': '情绪变化', 'direction': direction,
        'score': _score(mag, corr, 1.0), 'weak': weak, 'evidence': evs,
        'read': '；'.join(bits) + ('；风险分 ≥ 40 表示当次存在可核验的突发事件，需单独排查' if risk >= 40 else ''),
        'impact': ('利好方向：温度计回到中性偏上且社区净多空转正，说明抛压释放完毕，回调是加仓点；'
                   '利空方向：温度计 ≥ 72 的亢奋与 ≤ 32 的恐慌都属反向信号，前者追多风险最大。'),
        'risk_score': risk,
        'neg_share': neg,
        'counts': counts,
    }


def _force_institution(macro, now):
    items = _items(macro, 'bank_views')
    if not items:
        return None
    news = _evidence_from_items(items, 2)
    senti = _title_senti([n['text'] for n in news])
    direction = 1 if senti > 0.12 else (-1 if senti < -0.12 else 0)
    fresh = _freshness(news[0]['date'], now) if news else 0.4
    return {
        'key': 'institution', 'name': '机构观点（大行目标价与配置建议）',
        'category': '宏观事件', 'direction': direction,
        'score': _score(_clip01(len(items) / 4.0), len(items) / 3.0, fresh),
        'weak': len(items) <= 1 and abs(senti) < 0.12, 'evidence': news,
        'read': f'当次窗口内机构观点 {len(items)} 条，最新 {md_cn(news[0]["date"]) if news else "—"}。',
        'impact': '利好方向：上调目标价通常滞后于价格，只能作为佐证；利空方向：集体下调往往对应资金已先行离场。',
    }


def _community_counts(community):
    counts = {'bull': 0, 'bear': 0, 'neutral': 0, 'mixed': 0}
    for c in ((community or {}).get('communities') or []):
        k = (c or {}).get('verdict_class')
        if k in counts:
            counts[k] += 1
    return counts


# ---------------------------------------------------------------------------
# 主扫描
# ---------------------------------------------------------------------------
def scan(market=None, macro=None, sentiment=None, community=None, now=None, top_n=5):
    """把四路当次数据合成为「每日全球全景扫描」结论。纯函数，无 IO。"""
    now = now or datetime.now(timezone.utc)
    market, macro = market or {}, macro or {}
    sentiment, community = sentiment or {}, community or {}
    quotes = market.get('quotes') or {}

    builders = [
        _force_hk_tape(quotes, macro, now),
        _force_global_risk(quotes, now),
        _force_fed_liquidity(quotes, macro, now),
        _force_macro_growth(macro, now),
        _force_rotation(quotes, now),
        _force_sentiment(sentiment, community, now),
        _force_commodities(quotes, macro, now),
        _force_institution(macro, now),
    ]
    forces = [f for f in builders if f]
    for f in forces:
        f['score'] = _apply_noise_cap(f['score'], f.get('weak'))
        f['tier'] = _tier(f['score'], f.get('weak'))
        f['dir_word'] = _dir_word(f['direction'])
        f['weight'] = FORCE_WEIGHT.get(f['key'], 0.5)

    forces.sort(key=lambda f: (TIER_KEY[f['tier']], -f['score']))
    top = forces[:top_n]
    for i, f in enumerate(top, 1):
        f['rank'] = i
    noise = [f for f in forces if f['tier'] == '噪音' and f not in top]

    # 每条力量后面挂一条 AI 量化配对：策略从本条证据里找，方向只看当次两腿涨跌幅。
    for f in forces:
        f['ai_quant'] = quant_pair.recommend(
            f"{f.get('name', '')} {f.get('category', '')} {f.get('read', '')} {f.get('impact', '')}",
            quotes, hint=f.get('key'))

    # ---------- 覆盖度与数据缺口 ----------
    # 只认「真的有值」的数据：文件存在但全是 null（抓取失败的常见形态）一律计为缺口，
    # 否则会出现「覆盖 100% 却一条行情都没有」的假高置信度。
    gaps = []
    live_quotes = sum(1 for q in quotes.values() if (q or {}).get('pct') is not None)
    if not live_quotes:
        gaps.append('行情（market_data.json）未获取或全部为空值 —— 盘面方向与轮动判断全部缺席')
    macro_items_n = sum(len(((blk or {}).get('items') or []))
                        for blk in ((macro.get('categories') or {}).values()))
    if not macro_items_n:
        gaps.append('宏观快讯（macro_data.json）窗口内无条目 —— 宏观事件一栏不回填历史叙事')
    if not ((sentiment or {}).get('market') or {}).get('news_count'):
        gaps.append('舆情因子（sentiment_data.json）未获取 —— 情绪读数以社区研判替代')
    if not ((community or {}).get('communities')):
        gaps.append('社区研判（community_data.json）未获取 —— 多空家数无法统计')
    coverage = round((4 - len(gaps)) / 4.0, 2)

    # ---------- 做多合成分 ----------
    num = den = 0.0
    for f in forces:
        if f['tier'] == '噪音':
            continue                                    # 噪音不参与决策，这是本栏的硬规则
        num += f['direction'] * f['score'] * f['weight']
        den += f['score'] * f['weight']
    raw = (num / den * 100.0) if den else 0.0

    # 广度阻尼：只有 1~2 条有效力量时不允许给出满仓级别的合成分 —— 单一证据不构成全景。
    effective_n = sum(1 for f in forces if f['tier'] != '噪音')
    breadth_damp = min(1.0, effective_n / 3.0)
    adjust = []
    if breadth_damp < 1.0:
        adjust.append(f'有效力量仅 {effective_n} 条（<3），合成分按广度阻尼 ×{breadth_damp:.2f}')
        raw *= breadth_damp
    sf = next((f for f in forces if f['key'] == 'sentiment'), None)
    if sf:
        if (sf.get('risk_score') or 0) >= 60:
            raw -= 10
            adjust.append(f"突发风险分 {sf['risk_score']} ≥ 60，合成分 −10")
        if (sf.get('neg_share') or 0) >= 60:
            raw -= 5
            adjust.append(f"负面新闻占比 {sf['neg_share']}% ≥ 60%，合成分 −5")
    if coverage < 1.0:
        adjust.append(f'数据覆盖 {int(coverage * 100)}%，结论置信度同步下调')
    long_score = round(max(-100.0, min(100.0, raw)), 1)

    def score_text_of(v, can):
        """结论为 unknown 时不展示合成分 —— 避免读者把不成立的分数当信号。"""
        return '合成分不适用' if can == 'unknown' else f'合成分 {v:+.1f}'

    effective = [f for f in forces if f['tier'] != '噪音']
    breadth = len(effective) / 5.0
    confidence = round(_clip01(coverage * 0.6 + breadth * 0.4), 2)

    # 做多结论的三道硬门槛：要有盘面、要有至少 2 条互相独立的有效力量、置信度不能太低。
    # 「今天能不能做多」是交易决策，单一软信号（比如只剩情绪面）不足以支撑，宁可不给结论。
    if not live_quotes:
        stance, can_long = '行情未获取 · 无盘面读数，不给做多结论', 'unknown'
    elif len(effective) < 2 or confidence < 0.45:
        stance, can_long = '有效力量不足 · 不给做多结论', 'unknown'
    elif long_score >= 25:
        stance, can_long = '可以做多（趋势跟随，分批建仓）', 'yes'
    elif long_score >= 10:
        stance, can_long = '可轻仓试多（右侧确认后再加）', 'partial'
    elif long_score > -10:
        stance, can_long = '不宜追多 · 等待方向确认', 'wait'
    elif long_score > -25:
        stance, can_long = '偏防御 · 不建议新开多单', 'no'
    else:
        stance, can_long = '空头占优 · 多单应减仓或对冲', 'no'

    # ---------- 三大关注面小结（宏观事件 / 板块轮动 / 情绪变化，栏目硬性要求逐面给结论） ----------
    focus = []
    for cat in ('宏观事件', '板块轮动', '情绪变化'):
        members = [f for f in forces if f['category'] == cat]
        if not members:
            focus.append({'category': cat, 'direction': 0, 'dir_word': '未获取',
                          'summary': '当次四路数据里没有可核验的证据，本面留空（不回填历史叙事）。',
                          'lead': ''})
            continue
        lead = max(members, key=lambda f: (TIER_KEY[f['tier']] * -1, f['score']))
        eff = [f for f in members if f['tier'] != '噪音']
        net = sum(f['direction'] * f['score'] for f in eff)
        d = 0 if not eff or abs(net) < 1e-9 else (1 if net > 0 else -1)
        noise_n = len(members) - len(eff)
        focus.append({
            'category': cat, 'direction': d, 'dir_word': _dir_word(d), 'lead': lead['name'],
            'summary': (f'{len(members)} 项证据（有效 {len(eff)} / 噪音 {noise_n}），合力{_dir_word(d)}；'
                        f'主导项「{lead["name"]}」{lead["tier"]}·{lead["score"]} 分 —— {lead["read"]}'),
        })

    hsi = quotes.get('HSI') or {}
    trigger = []
    if hsi.get('last') is not None and hsi.get('prev_close') is not None:
        trigger.append(f"多头有效性参考位：恒指前收盘 {float(hsi['prev_close']):,.2f}（当日 {fmt_last(hsi)}），"
                       f"失守前收盘即视为当日多头逻辑未成立")
    bulls = [f['name'] for f in effective if f['direction'] > 0]
    bears = [f['name'] for f in effective if f['direction'] < 0]

    verdict_ai = quant_pair.recommend(
        ' '.join([stance] + bulls + bears), quotes, hint='hk_tape')

    return {
        'generated_at': now.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'scan_date': now.strftime('%Y-%m-%d'),
        'quote_date': hsi.get('as_of') or (market.get('fetch_date') or ''),
        'pair_quotes': {k: {'name': (q or {}).get('name') or '',
                            'pct': _num((q or {}).get('pct')),
                            'as_of': (q or {}).get('as_of') or ''}
                        for k, q in quotes.items() if isinstance(q, dict)},
        'forces': top,
        'all_forces': forces,
        'noise': noise,
        'focus': focus,
        'coverage': coverage,
        'data_gaps': gaps,
        'verdict': {
            'long_score': long_score,
            'stance': stance,
            'can_long': can_long,
            'confidence': confidence,
            'bulls': bulls,
            'bears': bears,
            'adjust': adjust,
            'trigger': trigger,
            'score_text': score_text_of(long_score, can_long),
            'rule': ('合成分 = Σ(方向 × 力量分 × 权重) / Σ(力量分 × 权重)，噪音不计入；'
                     '≥25 可做多 / ≥10 轻仓 / −10~10 观望 / ≤−10 防御'),
            'ai_quant': verdict_ai,
        },
    }


# ---------------------------------------------------------------------------
# 渲染：网页（report.html 的 class 体系）与微信（全内联样式）共用同一份扫描结果
# ---------------------------------------------------------------------------
def _esc(t):
    import html
    return html.escape(str(t if t is not None else ''), quote=False)


def _ev_line(e):
    d = f"[{md_cn(e['date'])}] " if e.get('date') else ''
    tail = f"　{e['detail']}" if e.get('detail') else ''
    return _esc(d + e.get('text', '') + tail)


HEAD_NOTE = ('口径：力量分 = 45% 幅度 + 30% 印证度 + 25% 时效；'
             '≥55 重点 / 35~55 次要 / <35 或未过噪音阈值均计噪音。')

_FOCUS_HINT = {
    '宏观事件': 'macro_growth',
    '板块轮动': 'rotation',
    '情绪变化': 'sentiment',
    '是否可以做多': 'hk_tape',
}


def _force_ai(force, data):
    """力量上已挂的配对结果优先；旧结果缺字段时按当次行情现算。"""
    aq = (force or {}).get('ai_quant')
    if aq:
        return aq
    return quant_pair.recommend(
        f"{(force or {}).get('name', '')} {(force or {}).get('category', '')} "
        f"{(force or {}).get('read', '')} {(force or {}).get('impact', '')}",
        (data or {}).get('pair_quotes'),
        hint=(force or {}).get('key'))


def _focus_ai(fc, data):
    return quant_pair.recommend(
        f"{fc.get('category', '')} {fc.get('summary', '')} {fc.get('lead', '')}",
        (data or {}).get('pair_quotes'),
        hint=_FOCUS_HINT.get(fc.get('category')))


def render_web(data):
    """01 栏网页版 HTML（沿用 report.html 的 .pub-* / .pixel-list 类）。"""
    v = data['verdict']
    parts = [f'<div class="pub-meta" style="margin:0 0 8px;">'
             f'扫描时间 {_esc(data["generated_at"])} · 行情日期 {_esc(data["quote_date"] or "未获取")} · '
             f'数据覆盖 {int(data["coverage"] * 100)}% · {_esc(HEAD_NOTE)}</div>']

    if not data['forces']:
        parts.append('<div class="pub-sub">◆ 今日未获取足够数据 —— 本栏不编故事</div>'
                     '<div style="font-size:12px;line-height:1.8;">四路数据当次全部缺失，'
                     '本栏<strong>不回填历史叙事</strong>。恢复命令：'
                     '<code>python3 market_data.py &amp;&amp; python3 macro_data.py &amp;&amp; '
                     'python3 community_data.py</code>。</div>')
        return '\n'.join(parts)

    parts.append('<div class="pub-sub">◆ 推动股价的 5 大力量（按当次证据强度排序）</div>')
    for f in data['forces']:
        evs = ''.join(f'<li>{_ev_line(e)}</li>' for e in f['evidence'][:4])
        parts.append(
            '<div class="pub-card" data-force="' + _esc(f['key']) + '">'
            f'<div class="pub-card-head"><span class="pub-name">{f["rank"]}. {_esc(f["name"])}</span>'
            f'<span class="pub-chip">{_esc(f["tier"])} · {_esc(f["dir_word"])} · {f["score"]} 分</span></div>'
            f'<p class="pub-quote"><strong>归类：</strong>{_esc(f["category"])}　'
            f'<strong>当次读数：</strong>{_esc(f["read"])}</p>'
            + (f'<ul class="pixel-list" style="margin:8px 0 0;">{evs}</ul>' if evs else '')
            + f'<div class="pub-verdict"><strong style="color:#000;">▶ 如何利好利空：</strong>{_esc(f["impact"])}</div>'
            + quant_pair.render_web(_force_ai(f, data))
            + '</div>')

    parts.append('<div class="pub-sub" style="margin-top:12px;">◆ 三大关注面 · 宏观事件 / 板块轮动 / 情绪变化</div>')
    focus_bits = []
    for fc in data['focus']:
        focus_bits.append(
            f'<li><strong>{_esc(fc["category"])}（{_esc(fc["dir_word"])}）</strong>　{_esc(fc["summary"])}'
            + quant_pair.render_web(_focus_ai(fc, data), compact=True, brief=True)
            + '</li>')
    parts.append('<ul class="pixel-list">' + ''.join(focus_bits) + '</ul>')

    noise = data['noise']
    parts.append('<div class="pub-sub" style="margin-top:12px;">◆ 哪些是噪音（当日不参与决策）</div>')
    if noise:
        parts.append('<ul class="pixel-list">' + ''.join(
            f'<li><strong>{_esc(n["name"])}</strong>（{n["score"]} 分）—— {_esc(n["read"])}'
            + quant_pair.render_web(_force_ai(n, data), compact=True, brief=True,
                                    note='噪音段：配对只作对照，不作为做多依据。')
            + '</li>'
            for n in noise) + '</ul>')
    else:
        parts.append('<div style="font-size:12px;">当次没有被判为噪音的力量：'
                     '所有取到的信号都越过了各自的噪音阈值，说明今天是「有方向」的一天。</div>')

    conf_pct = int(v['confidence'] * 100)
    reasons = []
    if v['bulls']:
        reasons.append('偏多力量：' + '、'.join(_esc(b) for b in v['bulls']))
    if v['bears']:
        reasons.append('偏空力量：' + '、'.join(_esc(b) for b in v['bears']))
    parts.append(
        '<div class="pub-sub" style="margin-top:12px;">◆ 是否可以做多</div>'
        '<div class="quant-box">'
        f'<div style="font-size:13px;font-weight:700;color:#000;">结论：{_esc(v["stance"])}'
        f'　<span style="background:#000;color:#39ff14;padding:1px 6px;font-size:12px;">'
        f'{_esc(v["score_text"])} · 置信度 {conf_pct}%</span></div>'
        + ''.join(f'<div style="margin-top:6px;">{r}</div>' for r in reasons)
        + ''.join(f'<div style="margin-top:6px;">{_esc(t)}</div>' for t in v['trigger'])
        + ''.join(f'<div class="pub-meta" style="margin-top:4px;">调整项：{_esc(a)}</div>' for a in v['adjust'])
        + f'<div class="pub-meta" style="margin-top:8px;">{_esc(v["rule"])}'
          '（规则化推导，不构成投资建议）</div>'
        + quant_pair.render_web(v.get('ai_quant') or _focus_ai(
            {'category': '是否可以做多', 'summary': v.get('stance') or ''}, data),
            note='配对推荐独立于上方做多合成分，两腿行情不全时不给方向。')
        + '</div>')

    if data['data_gaps']:
        parts.append('<div class="pub-meta" style="margin-top:8px;">数据缺口：'
                     + _esc('；'.join(data['data_gaps'])) + '（缺口部分按空值处理，不回填旧文）</div>')
    return '\n'.join(parts)


def render_wechat(data, neon='#b6ff4a', green='#4fe5ff', ink='#edf2ff'):
    """01 栏微信版 HTML（电竞 HUD 深色面板，全内联样式）。"""
    def sub(t):
        return f'<div style="color:{green};font-weight:700;font-size:13px;margin-bottom:6px;">{t}</div>'

    def meta(t):
        return f'<div style="color:#9aa6c3;font-size:10px;line-height:1.7;margin-top:6px;">{t}</div>'

    v = data['verdict']
    out = [f'<div style="background:#0d1426;border:1px solid #2b3855;border-radius:4px;'
           f'padding:14px 16px;margin:10px 0;font-size:12px;line-height:1.85;color:{ink};">'
           + meta(f'扫描时间 {_esc(data["generated_at"])} · 行情日期 {_esc(data["quote_date"] or "未获取")} · '
                  f'数据覆盖 {int(data["coverage"] * 100)}% · {_esc(HEAD_NOTE)}')]

    if not data['forces']:
        out.append(sub('◆ 今日未获取足够数据 —— 本栏不编故事')
                   + '四路数据当次全部缺失，本栏<strong>不回填历史叙事</strong>；'
                     '恢复后重建即可恢复完整扫描。</div>')
        return ''.join(out)

    out.append(sub('◆ 推动股价的 5 大力量（按当次证据强度排序）'))
    for f in data['forces']:
        evs = ''.join('<br/>· ' + _ev_line(e) for e in f['evidence'][:3])
        out.append(
            f'<div style="border:1px solid #26334e;border-left:3px solid {green};background:#121b30;'
            f'color:{ink};border-radius:3px;padding:8px 10px;margin:8px 0;font-size:11.5px;line-height:1.75;">'
            f'<strong style="color:{ink};">{f["rank"]}. {_esc(f["name"])}</strong>'
            f'<strong style="background:#07101b;color:{neon};font-size:10px;padding:1px 5px;margin-left:4px;">'
            f'{_esc(f["tier"])} · {_esc(f["dir_word"])} · {f["score"]} 分</strong>'
            f'<br/><strong>归类：</strong>{_esc(f["category"])}　<strong>当次读数：</strong>{_esc(f["read"])}'
            + evs
            + f'<br/><strong style="color:{green};">如何利好利空：</strong>{_esc(f["impact"])}'
            + quant_pair.render_wechat(_force_ai(f, data))
            + '</div>')

    out.append('<br/>' + sub('◆ 三大关注面 · 宏观事件 / 板块轮动 / 情绪变化'))
    out.append(''.join(
        f'· <strong>{_esc(fc["category"])}（{_esc(fc["dir_word"])}）</strong>　{_esc(fc["summary"])}'
        + quant_pair.render_wechat(_focus_ai(fc, data), compact=True, brief=True)
        + '<br/>'
        for fc in data['focus']))

    out.append('<br/>' + sub('◆ 哪些是噪音（当日不参与决策）'))
    if data['noise']:
        out.append(''.join(f'· <strong>{_esc(n["name"])}</strong>（{n["score"]} 分）—— {_esc(n["read"])}<br/>'
                           for n in data['noise']))
    else:
        out.append('当次没有被判为噪音的力量：取到的信号都越过了各自的噪音阈值。<br/>')

    out.append('<br/>' + sub('◆ 是否可以做多'))
    out.append(f'<strong style="background:#101b2c;color:{neon};font-weight:700;padding:2px 6px;border:1px solid #435d31;">'
               f'{_esc(v["stance"])} · {_esc(v["score_text"])} · 置信度 {int(v["confidence"] * 100)}%</strong>')
    if v['bulls']:
        out.append('<br/><strong>偏多力量：</strong>' + '、'.join(_esc(b) for b in v['bulls']))
    if v['bears']:
        out.append('<br/><strong>偏空力量：</strong>' + '、'.join(_esc(b) for b in v['bears']))
    for t in v['trigger']:
        out.append('<br/>' + _esc(t))
    for a in v['adjust']:
        out.append('<br/><span style="color:#9aa6c3;font-size:10.5px;">调整项：' + _esc(a) + '</span>')
    out.append(meta(_esc(v['rule']) + '（规则化推导，不构成投资建议）'))
    out.append(quant_pair.render_wechat(v.get('ai_quant') or _focus_ai(
        {'category': '是否可以做多', 'summary': v.get('stance') or ''}, data),
        note='配对推荐独立于上方做多合成分，两腿行情不全时不给方向。'))
    if data['data_gaps']:
        out.append(meta('数据缺口：' + _esc('；'.join(data['data_gaps'])) + '（按空值处理，不回填旧文）'))
    out.append('</div>')
    return ''.join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def scan_from_files(root=REPO_ROOT, now=None):
    """按仓库约定路径读取四份 json（环境变量可覆盖，与推送/建站一致）。"""
    return scan(
        market=_load(os.environ.get('MARKET_DATA', os.path.join(root, 'market_data.json'))),
        macro=_load(os.environ.get('MACRO_DATA', os.path.join(root, 'macro_data.json'))),
        sentiment=_load(os.environ.get('SENTIMENT_DATA', os.path.join(root, 'sentiment_data.json'))),
        community=_load(os.environ.get('COMMUNITY_DATA', os.path.join(root, 'community_data.json'))),
        now=now)


def text_report(d):
    lines = [f'🌍 每日全球全景扫描 · {d["scan_date"]} · 覆盖 {int(d["coverage"] * 100)}%',
             f'   {HEAD_NOTE}']
    for f in d['forces']:
        lines.append(f'  {f["rank"]}. [{f["tier"]}/{f["dir_word"]}/{f["score"]}] {f["name"]}')
        lines.append(f'       {f["read"]}')
    for fc in d.get('focus') or []:
        lines.append(f'  · {fc["category"]}（{fc["dir_word"]}）: {fc["summary"]}')
    if d['noise']:
        lines.append('  噪音: ' + '；'.join(f'{n["name"]}({n["score"]})' for n in d['noise']))
    v = d['verdict']
    lines.append(f'  ▶ 是否可以做多: {v["stance"]} · {v["score_text"]} · 置信度 {v["confidence"]}')
    for g in d['data_gaps']:
        lines.append(f'  ⚠️ {g}')
    return '\n'.join(lines)


def _self_test():
    now = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(('  ✅ ' if cond else '  ❌ ') + msg)
        ok = ok and bool(cond)

    empty = scan(now=now)
    check(empty['forces'] == [] and empty['verdict']['can_long'] == 'unknown',
          '空数据：不给做多结论，且不产出任何力量')
    check(empty['coverage'] == 0.0 and len(empty['data_gaps']) == 4, '空数据：覆盖度 0 且四项缺口全部标注')
    check(all(fc['dir_word'] == '未获取' for fc in empty['focus']), '空数据：三大关注面全部标注「未获取」')

    bull_market = {'fetch_date': '2026-09-17', 'quotes': {
        'HSI': {'name': '恒生指数', 'last': 26100.0, 'prev_close': 25700.0, 'chg': 400.0,
                'pct': 1.56, 'as_of': '2026-09-17', 'decimals': 2},
        'HSTECH': {'name': '恒生科技指数', 'last': 6100.0, 'pct': 2.4, 'as_of': '2026-09-17', 'decimals': 2},
        'HSCE': {'name': '恒生中国企业指数', 'last': 9300.0, 'pct': 1.5, 'as_of': '2026-09-17', 'decimals': 2},
        'SPX': {'name': '标普 500', 'last': 6400.0, 'pct': 1.1, 'as_of': '2026-09-16', 'decimals': 2},
        'NDQ': {'name': '纳斯达克', 'last': 21000.0, 'pct': 1.6, 'as_of': '2026-09-16', 'decimals': 2},
        'DJI': {'name': '道琼斯', 'last': 43000.0, 'pct': 0.7, 'as_of': '2026-09-16', 'decimals': 2},
        'GOLD': {'name': '现货黄金', 'last': 4200.0, 'pct': -0.5, 'as_of': '2026-09-16', 'decimals': 2},
        'WTI': {'name': 'WTI 原油', 'last': 70.0, 'pct': -1.8, 'as_of': '2026-09-16', 'decimals': 2},
        'BRENT': {'name': '布伦特原油', 'last': 74.0, 'pct': -1.6, 'as_of': '2026-09-16', 'decimals': 2},
        'USDCNH': {'name': '美元/离岸人民币', 'last': 7.05, 'pct': -0.3, 'as_of': '2026-09-17', 'decimals': 4},
    }}
    d = scan(market=bull_market, now=now)
    check(len(d['forces']) == 5, f'普涨行情：恰好给出 5 大力量（实得 {len(d["forces"])}）')
    check(d['verdict']['long_score'] > 25 and d['verdict']['can_long'] == 'yes',
          f'普涨行情：合成分 {d["verdict"]["long_score"]:+.1f} → 可以做多')
    check(any(f['key'] == 'rotation' and f['direction'] == 1 for f in d['all_forces']),
          '普涨行情：识别出资金向成长端轮动')
    check({fc['category'] for fc in d['focus']} == {'宏观事件', '板块轮动', '情绪变化'},
          '三大关注面固定输出（宏观事件 / 板块轮动 / 情绪变化）')
    check('26,100' not in json.dumps(d, ensure_ascii=False) or d['quote_date'] == '2026-09-17',
          '行情日期取自当次数据，而非模块内写死')

    flat = {'fetch_date': '2026-09-17', 'quotes': {
        'HSI': {'name': '恒生指数', 'last': 25700.0, 'prev_close': 25695.0, 'chg': 5.0,
                'pct': 0.02, 'as_of': '2026-09-17', 'decimals': 2},
        'HSTECH': {'name': '恒生科技指数', 'last': 5900.0, 'pct': 0.05, 'as_of': '2026-09-17', 'decimals': 2},
        'SPX': {'name': '标普 500', 'last': 6300.0, 'pct': 0.04, 'as_of': '2026-09-16', 'decimals': 2},
        'NDQ': {'name': '纳斯达克', 'last': 20800.0, 'pct': -0.03, 'as_of': '2026-09-16', 'decimals': 2},
        'DJI': {'name': '道琼斯', 'last': 42800.0, 'pct': 0.06, 'as_of': '2026-09-16', 'decimals': 2},
    }}
    d2 = scan(market=flat, now=now)
    check(any(f['tier'] == '噪音' for f in d2['all_forces']), '横盘行情：低于阈值的波动被判为噪音')
    check(-10 < d2['verdict']['long_score'] < 10, '横盘行情：合成分落在观望区间')

    bear = {'fetch_date': '2026-09-17', 'quotes': {
        'HSI': {'name': '恒生指数', 'last': 25000.0, 'prev_close': 25500.0, 'chg': -500.0,
                'pct': -1.96, 'as_of': '2026-09-17', 'decimals': 2},
        'HSTECH': {'name': '恒生科技指数', 'last': 5600.0, 'pct': -3.0, 'as_of': '2026-09-17', 'decimals': 2},
        'SPX': {'name': '标普 500', 'last': 6100.0, 'pct': -1.4, 'as_of': '2026-09-16', 'decimals': 2},
        'NDQ': {'name': '纳斯达克', 'last': 20000.0, 'pct': -2.0, 'as_of': '2026-09-16', 'decimals': 2},
        'DJI': {'name': '道琼斯', 'last': 42000.0, 'pct': -1.0, 'as_of': '2026-09-16', 'decimals': 2},
        'WTI': {'name': 'WTI 原油', 'last': 88.0, 'pct': 3.2, 'as_of': '2026-09-16', 'decimals': 2},
        'BRENT': {'name': '布伦特原油', 'last': 92.0, 'pct': 3.0, 'as_of': '2026-09-16', 'decimals': 2},
        'GOLD': {'name': '现货黄金', 'last': 4500.0, 'pct': 1.2, 'as_of': '2026-09-16', 'decimals': 2},
        'USDCNH': {'name': '美元/离岸人民币', 'last': 7.25, 'pct': 0.4, 'as_of': '2026-09-17', 'decimals': 4},
    }}
    d3 = scan(market=bear, now=now)
    check(d3['verdict']['long_score'] <= -25 and d3['verdict']['can_long'] == 'no',
          f'普跌行情：合成分 {d3["verdict"]["long_score"]:+.1f} → 不建议做多')
    check(any(f['key'] == 'commodities' and f['direction'] == -1 for f in d3['all_forces']),
          '普跌行情：油金同涨被判为利空权益')

    # 渲染层不得抛异常，且不得出现未替换的占位
    for dd in (empty, d, d2, d3):
        html_w, html_x = render_web(dd), render_wechat(dd)
        check('{{' not in html_w and '{{' not in html_x, '渲染输出无残留占位符')
    print('\n' + ('✅ panorama 自检全部通过' if ok else '❌ panorama 自检存在失败项'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description='每日全球全景扫描（01 栏推理引擎）')
    ap.add_argument('--json', help='把扫描结果写入指定 JSON 文件')
    ap.add_argument('--self-test', action='store_true', help='规则自检')
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    d = scan_from_files()
    print(text_report(d))
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        print(f'\n💾 已写入 {args.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
