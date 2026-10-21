#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 自建舆情/新闻情感打分层 (sentiment_nlp.py)
================================================================

为什么需要这一层：
  实测口径下，聚宽只给「量价情绪因子 + 雪球热度 + 新闻文本」，掘金完全没有舆情接口，
  Tushare / 东财给的是原始新闻文本 —— 也就是说，除了米筐、优矿，
  其余数据源都必须自己把「文本」变成「因子」。本模块就是这段加工逻辑：
  纯标准库、无第三方依赖、结果确定可复现（便于回测与单元测试）。

因子口径（与 sentiment_sources.FACTOR_LIBRARY 一一对应）：
  NET_SENTI   净情感强度   = Σ(情感分 × 权重) / Σ权重           ∈ [-1, 1]
  NEG_SHARE   负面舆情占比 = 负向条数 / 全部条数                 ∈ [0, 1]
  NEWS_HEAT_Z 新闻热度 Z   = (今日条数 - 近史均值) / 标准差
  SENT_TEMP   市场舆情温度计 = 50 + 35·net + 12·tanh(z/2) - 18·neg_share ∈ [0, 100]
  EVENT_RISK  突发事件风险分 = 高风险词命中加权，截断到 [0, 100]

打分规则（可解释、可回溯，不做黑箱）：
  1) 命中情感词 → 按词权计分；程度副词（大幅/显著/轻微…）乘权；
  2) 否定词（不/未/无/没/非/失/否/难）出现在情感词前 3 字内 → 极性翻转并打 0.8 折；
  3) 风险词（立案/调查/处罚/违规/退市/爆雷/违约/减持/质押/诉讼/冻结…）计入 EVENT_RISK；
  4) 每条新闻按发布时间做指数衰减（半衰期默认 24h ≈ 1 个交易日），越新权重越高；
  5) 数据源若给了相关度（米筐 company_relevance）则作为乘子，未提供时按 1.0。

原则：平台有现成因子就用平台的 —— item 里带 sentiment 字段时直接采用，本层只做衰减与加权。

用法:
  python3 sentiment_nlp.py --text "某公司因财务违规被立案调查，股价大跌"
  python3 sentiment_nlp.py --json tests/fixtures/em_news.json
  python3 sentiment_nlp.py --self-test
"""
import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 词库（中文金融语境；每词 (词, 权重)。刻意保持小而准，避免过拟合与噪声）
# ---------------------------------------------------------------------------
POSITIVE = {
    '超预期': 1.0, '好于预期': 1.0, '超出预期': 1.0, '业绩大增': 1.0, '净利增长': 0.9,
    '净利润增长': 0.9, '扭亏': 1.0, '盈利提升': 0.8, '中标': 0.8, '签约': 0.6,
    '获批': 0.8, '通过审批': 0.8, '增持': 0.7, '回购': 0.8, '分红': 0.5, '派息': 0.4,
    '涨停': 1.0, '大涨': 0.9, '领涨': 0.8, '走强': 0.7, '反弹': 0.6, '新高': 0.9,
    '突破': 0.6, '增长': 0.5, '扩张': 0.5, '放量上行': 0.8, '净流入': 0.6, '上调': 0.7,
    '增持评级': 0.9, '利好': 1.0, '复苏': 0.7, '改善': 0.7, '景气': 0.7, '订单饱满': 0.9,
    '毛利率提升': 0.9, '降本增效': 0.6, '回购注销': 1.0, '股权激励': 0.5,
    '重组完成': 0.6, '并表': 0.4, '供不应求': 0.8, '提价': 0.7, '涨价': 0.7,
    '创新高': 0.9, '超额收益': 0.8, '转正': 0.6, '回暖': 0.7,
    # 高频方向词（新闻标题里最常见，权重刻意压低：'成本上涨'这类反例靠低权重降噪）
    '上涨': 0.4, '走高': 0.5, '攀升': 0.6, '飙升': 0.9, '暴涨': 1.0, '大涨': 0.9,
    '预增': 0.9, '扭亏为盈': 1.0, '高增长': 0.7, '翻倍': 0.8, '上修': 0.7, '收复': 0.4,
    '买入评级': 0.8, '上调目标价': 0.8, '放量上涨': 0.7, '订单增加': 0.6, '量产': 0.4,
    '提速': 0.5, '投产': 0.4,
}

NEGATIVE = {
    '不及预期': -1.0, '低于预期': -1.0, '业绩下滑': -1.0, '净利下滑': -0.9, '亏损': -0.9,
    '预亏': -1.0, '跌停': -1.0, '大跌': -0.9, '领跌': -0.8, '走弱': -0.7, '跳水': -0.9,
    '新低': -0.9, '破位': -0.8, '减持': -0.7, '质押': -0.5, '商誉减值': -1.0, '资产减值': -0.9,
    '下调': -0.7, '看空': -0.7, '卖出评级': -0.9, '利空': -1.0, '退市': -1.0, '摘牌': -1.0,
    '爆雷': -1.0, '违约': -1.0, '逾期': -0.9, '流动性危机': -1.0, '资金链断裂': -1.0,
    '停产': -0.8, '关停': -0.7, '召回': -0.8, '事故': -0.8, '诉讼': -0.8, '仲裁': -0.5,
    '立案': -1.0, '调查': -0.6, '处罚': -0.9, '罚款': -0.8, '警示函': -0.8, '监管': -0.3,
    '违规': -0.9, '造假': -1.0, '虚增': -1.0, '被ST': -1.0, '停牌': -0.5, '破发': -0.7,
    '净流出': -0.6, '缩量下跌': -0.7, '跌价': -0.6, '产能过剩': -0.7, '价格战': -0.6,
    '毛利率下滑': -0.8, '库存积压': -0.6, '商誉': -0.2,
    # 高频方向词（与正面侧对称补齐，否则"股价下跌"会被判成中性）
    '下跌': -0.5, '走低': -0.5, '回落': -0.4, '下挫': -0.6, '暴跌': -1.0, '重挫': -0.8,
    '闪崩': -0.9, '预减': -0.9, '下滑': -0.6, '负增长': -0.8, '亏损扩大': -1.0,
    '由盈转亏': -0.9, '承压': -0.5, '疲软': -0.5, '疲弱': -0.5, '低迷': -0.5,
    '下修': -0.6, '下调目标价': -0.7, '缩量下行': -0.5, '放量下跌': -0.6, '减持计划': -0.6,
    '破净': -0.4, '减少': -0.4,
}

# 高风险突发事件词（进入 EVENT_RISK，与情感极性独立计权）
RISK_TERMS = {
    '立案': 30, '立案侦查': 40, '证监会': 12, '交易所问询': 18, '问询函': 18, '关注函': 12,
    '处罚': 25, '罚款': 20, '顶格处罚': 35, '财务造假': 40, '虚增利润': 38, '资金占用': 30,
    '退市': 35, '暂停上市': 32, '摘牌': 32, '被ST': 22, '爆雷': 30, '债务违约': 32,
    '到期未兑付': 32, '控股股东减持': 15, '质押平仓': 28, '平仓': 22, '冻结': 20,
    '拘传': 30, '留置': 32, '逮捕': 32, '诉讼': 15, '仲裁': 8, '停产': 14, '安全事故': 22,
    '召回': 16, '业绩预亏': 20, '商誉减值': 22, '审计非标': 26, '无法表示意见': 28,
    '保留意见': 20, '下调评级': 14, '评级下调': 14, '评级列入观察': 12, '做空报告': 24,
}

INTENSIFIERS = {'大幅': 1.5, '显著': 1.4, '明显': 1.3, '严重': 1.5, '剧烈': 1.5,
                '小幅': 0.7, '轻微': 0.6, '略微': 0.6, '持续': 1.2, '再': 1.1, '双': 1.1,
                '重大': 1.4, '巨额': 1.4, '再度': 1.2, '急速': 1.3, '微幅': 0.6,
                '温和': 0.8, '略有': 0.6, '有限': 0.7, '继续': 1.15}

NEGATIONS = ('不', '未', '无', '没', '非', '失', '否', '难')

_WORD_CLASS = {}
for _w, _s in POSITIVE.items():
    _WORD_CLASS[_w] = _s
for _w, _s in NEGATIVE.items():
    _WORD_CLASS[_w] = _s

# 长词优先匹配，保证"业绩不及预期"不会被"预期"抢走
_ALL_TERMS = sorted(set(list(_WORD_CLASS) + list(RISK_TERMS)), key=len, reverse=True)
_SCAN_RE = re.compile('|'.join(re.escape(w) for w in _ALL_TERMS))


def parse_time(value):
    """宽松解析新闻时间戳 → aware datetime(UTC)；失败返回 None。"""
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip().replace('/', '-').replace('T', ' ')
    s = re.sub(r'(\.\d+)', '', s)              # 去掉小数秒
    s = re.sub(r'(Z|[+-]\d{2}:?\d{2})$', '', s)  # 去掉时区后缀，统一按 UTC 处理
    for fmt, cut in (('%Y-%m-%d %H:%M:%S', 19), ('%Y-%m-%d %H:%M', 16),
                     ('%Y-%m-%d', 10), ('%Y%m%d %H:%M:%S', 19), ('%Y%m%d', 8)):
        try:
            return datetime.strptime(s[:cut], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def time_weight(ts, half_life_hours=24.0, ref_time=None):
    """指数时间衰减：半衰期默认 24h（约 1 个交易日），越新权重越高。"""
    if ts is None or not half_life_hours or half_life_hours <= 0:
        return 1.0
    ref = ref_time or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    age_h = max(0.0, (ref - ts).total_seconds() / 3600.0)
    return round(math.exp(-0.6931471805599453 * age_h / half_life_hours), 6)


def score_text(text, half_life_hours=24.0, ref_time=None, published_at=None):
    """对单条标题/正文打情感分。

    返回 dict:
      sentiment  ∈ [-1, 1]  归一化情感净值
      raw        float      加权命中分之和（未归一化，保留可解释性）
      positives  [(词, 分)]  命中的正面词
      negatives  [(词, 分)]  命中的负面词
      risk_score 0-100      突发事件风险分
      risk_terms [词]       命中的风险词
      weight     float      时间衰减权重（0,1]
    """
    text = (text or '').strip()
    out = {'sentiment': 0.0, 'raw': 0.0, 'positives': [], 'negatives': [],
           'risk_score': 0.0, 'risk_terms': [], 'weight': 1.0, 'source_provided': False}
    if not text:
        return out

    raw = 0.0
    risk = 0.0
    seen_pos, seen_neg = {}, {}
    for m in _SCAN_RE.finditer(text):
        w = m.group(0)
        window = text[max(0, m.start() - 3):m.start()]
        negated = any(ch in window for ch in NEGATIONS)
        emph = 1.0
        cand = [k for e, k in INTENSIFIERS.items() if e in window]
        if cand:
            # 取偏离 1.0 最多的那个：放大词（大幅 1.5）与削弱词（小幅 0.7）都能生效
            emph = max(cand, key=lambda k: abs(k - 1.0))
        if w in RISK_TERMS:
            risk += RISK_TERMS[w]
        if w in _WORD_CLASS:
            v = _WORD_CLASS[w] * emph
            if negated:
                v = -v * 0.8
            raw += v
            (seen_pos if v > 0 else seen_neg)[w] = round(v, 4)

    n = len(seen_pos) + len(seen_neg)
    senti = 0.0 if n == 0 else max(-1.0, min(1.0, raw / n))
    out.update({
        'sentiment': round(senti, 4),
        'raw': round(raw, 4),
        'positives': sorted(seen_pos.items(), key=lambda kv: -abs(kv[1])),
        'negatives': sorted(seen_neg.items(), key=lambda kv: -abs(kv[1])),
        'risk_score': round(min(100.0, risk), 2),
        'risk_terms': [w for w in RISK_TERMS if w in text],
        'weight': time_weight(parse_time(published_at), half_life_hours, ref_time),
    })
    return out


def _relevance(item):
    """米筐 company_relevance 等相关度字段 → 权重乘子（缺省 1.0，为 0 时降权到 0.35）。"""
    r = item.get('relevance')
    try:
        r = float(r)
    except (TypeError, ValueError):
        return 1.0
    if r <= 0:
        return 0.35
    return min(1.0, max(0.05, r)) if r <= 1.0 else 1.0


def _brief(sc):
    it = sc['item']
    return {
        'title': (it.get('title') or '').strip()[:120],
        'source': it.get('source') or '',
        'published_at': it.get('published_at') or '',
        'sentiment': sc['sentiment'],
        'weight': sc['weight'],
        'hits': {'pos': [w for w, _ in sc['positives']][:4],
                 'neg': [w for w, _ in sc['negatives']][:4],
                 'risk': sc['risk_terms'][:4]},
    }


def _heat_z(count, history_counts):
    if not history_counts:
        return 0.0
    hist = [float(c) for c in history_counts if c is not None]
    if len(hist) < 3:
        return 0.0
    mean = sum(hist) / len(hist)
    var = sum((h - mean) ** 2 for h in hist) / len(hist)
    sd = math.sqrt(var)
    if sd < 1e-9:
        return 0.0 if abs(count - mean) < 1e-9 else (2.0 if count > mean else -2.0)
    return (count - mean) / sd


def aggregate(items, half_life_hours=24.0, ref_time=None, history_counts=None):
    """把若干条新闻聚合成一组舆情因子（日报 03B 节与回测共用同一实现）。

    items: [{'title', 'content'?, 'published_at'?, 'relevance'?, 'sentiment'?, 'source'?}]
      item 已带 sentiment（米筐/优矿等平台直接给的现成因子值）时优先采用平台口径。
    history_counts: 近 N 日新闻条数，用于算热度 Z 值。
    """
    ref_time = ref_time or datetime.now(timezone.utc)
    scored = []
    for it in items or []:
        text = f"{it.get('title') or ''} {it.get('content') or ''}".strip()
        sc = score_text(text, half_life_hours=half_life_hours, ref_time=ref_time,
                        published_at=it.get('published_at') or it.get('original_time')
                        or it.get('date'))
        own = it.get('sentiment')
        if own is not None:
            try:
                sc['sentiment'] = round(max(-1.0, min(1.0, float(own))), 4)
                sc['source_provided'] = True
            except (TypeError, ValueError):
                pass
        sc['weight'] = round(sc['weight'] * _relevance(it), 6)
        sc['item'] = it
        scored.append(sc)

    n = len(scored)
    if n == 0:
        return {'news_count': 0, 'net_senti': 0.0, 'neg_share': 0.0, 'pos_share': 0.0,
                'neu_share': 0.0, 'heat_z': 0.0, 'risk_score': 0.0, 'sent_temp': 50.0,
                'weighted_senti_sum': 0.0, 'weight_total': 0.0, 'platform_native': 0,
                'top_negative': [], 'top_positive': [], 'events': []}

    wsum = sum(s['weight'] for s in scored) or 1.0
    net = sum(s['sentiment'] * s['weight'] for s in scored) / wsum
    neg_n = sum(1 for s in scored if s['sentiment'] < -0.05)
    pos_n = sum(1 for s in scored if s['sentiment'] > 0.05)
    risk = max((s['risk_score'] for s in scored), default=0.0)
    heat_z = _heat_z(n, history_counts)
    temp = 50.0 + 35.0 * net + 12.0 * math.tanh(heat_z / 2.0) - 18.0 * (neg_n / n)
    temp = max(0.0, min(100.0, temp))

    top_neg = sorted((s for s in scored if s['sentiment'] < 0),
                     key=lambda s: s['sentiment'] * s['weight'])[:3]
    top_pos = sorted((s for s in scored if s['sentiment'] > 0),
                     key=lambda s: -s['sentiment'] * s['weight'])[:3]
    events = [{'title': s['item'].get('title', ''), 'terms': s['risk_terms'],
               'risk_score': s['risk_score'],
               'published_at': s['item'].get('published_at', '')}
              for s in sorted(scored, key=lambda x: -x['risk_score']) if s['risk_score'] > 0][:5]

    return {
        'news_count': n,
        'net_senti': round(net, 4),
        'neg_share': round(neg_n / n, 4),
        'pos_share': round(pos_n / n, 4),
        'neu_share': round((n - neg_n - pos_n) / n, 4),
        'heat_z': round(heat_z, 3),
        'risk_score': round(risk, 2),
        'sent_temp': round(temp, 2),
        'weighted_senti_sum': round(sum(s['sentiment'] * s['weight'] for s in scored), 4),
        'weight_total': round(wsum, 4),
        'platform_native': sum(1 for s in scored if s['source_provided']),
        'top_negative': [_brief(s) for s in top_neg],
        'top_positive': [_brief(s) for s in top_pos],
        'events': events,
    }


def zscore(values):
    """总体 z-score（个股横截面标准化复用）。"""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return [0.0 for _ in values]
    mean = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    if sd < 1e-12:
        return [0.0 for v in values]
    return [0.0 if v is None else round((v - mean) / sd, 4) for v in values]


def label(temp):
    """舆情温度计 → 中文标签（与日报正文口径一致）。"""
    if temp is None:
        return '数据暂缺'
    if temp >= 72:
        return '极度亢奋'
    if temp >= 60:
        return '偏热'
    if temp >= 45:
        return '中性'
    if temp >= 32:
        return '偏冷'
    if temp >= 20:
        return '恐慌'
    return '极度悲观'


def _self_test():
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    checks = [
        ('公司业绩超预期，净利大增', lambda r: r['sentiment'] > 0.5),
        ('因财务违规被证监会立案调查，股价跌停', lambda r: r['sentiment'] < -0.5 and r['risk_score'] >= 40),
        ('业绩不及预期', lambda r: r['sentiment'] < -0.5),
        ('业绩并未不及预期', lambda r: r['sentiment'] > 0),        # 否定翻转
        ('今日市场平稳运行，指数小幅收涨', lambda r: abs(r['sentiment']) <= 1.0),
        ('', lambda r: r['sentiment'] == 0.0),
    ]
    bad = 0
    for text, ok in checks:
        r = score_text(text)
        passed = bool(ok(r))
        bad += 0 if passed else 1
        print(f"  {'✔' if passed else '✘'} {text or '(空串)'}")
        print(f"      sentiment={r['sentiment']:+.3f} risk={r['risk_score']:.1f} "
              f"pos={[w for w, _ in r['positives']]} neg={[w for w, _ in r['negatives']]}")

    agg = aggregate([
        {'title': '龙头业绩超预期，获上调评级', 'published_at': stamp},
        {'title': '某公司债务违约，控股股东股份被冻结', 'published_at': '2026-09-14 09:00:00'},
        {'title': '行业价格战延续，毛利率下滑', 'published_at': stamp},
        {'title': '公司公告：拟回购注销 1 亿元', 'published_at': stamp, 'sentiment': 0.62},
    ], history_counts=[8, 10, 9, 11, 7])
    print(f"\n  聚合因子: net={agg['net_senti']:+.3f} neg_share={agg['neg_share']:.2f} "
          f"heat_z={agg['heat_z']:+.2f} temp={agg['sent_temp']:.1f}({label(agg['sent_temp'])}) "
          f"risk={agg['risk_score']:.0f} 平台原生因子条数={agg['platform_native']}")
    assert 0 <= agg['sent_temp'] <= 100, '温度计越界'
    assert agg['platform_native'] == 1, '平台现成因子应被采用'
    assert json.dumps(agg, ensure_ascii=False), '聚合结果必须可序列化'
    print('  ✔ 自检通过' if bad == 0 else f'  ✘ {bad} 项未通过')
    return bad


def main():
    ap = argparse.ArgumentParser(description='章鱼 AI·舆情004 — 自建舆情情感打分层')
    ap.add_argument('--text', help='对单条文本打分')
    ap.add_argument('--json', help='对 JSON（新闻数组或 {"items": [...]}）聚合打分')
    ap.add_argument('--half-life', type=float, default=24.0, help='时间衰减半衰期（小时），0 = 不衰减')
    ap.add_argument('--self-test', action='store_true', help='运行内置规则自检')
    args = ap.parse_args()

    if args.self_test:
        sys.exit(1 if _self_test() else 0)
    if args.json:
        with open(args.json, encoding='utf-8') as f:
            data = json.load(f)
        items = data.get('items') if isinstance(data, dict) else data
        print(json.dumps(aggregate(items or [], half_life_hours=args.half_life),
                         ensure_ascii=False, indent=2))
        return
    if args.text:
        print(json.dumps(score_text(args.text, half_life_hours=args.half_life),
                         ensure_ascii=False, indent=2))
        return
    ap.print_help()


if __name__ == '__main__':
    main()
