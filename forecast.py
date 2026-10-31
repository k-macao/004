#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — 04 栏「AI 预测 · 未来函数」推理引擎 (forecast.py)

栏目定位：
    前面 01~03B 栏回答的都是「今天发生了什么」。本栏回答**下一交易日会怎样**——
    对 11 个标的逐个给出方向（看涨 / 看跌 / 震荡）、预期涨跌幅、预测区间与点位区间、
    置信度与驱动拆解，并把每一条预测**落盘存档**，下次构建用实际行情自动回看命中率。

────────────────────────────────────────────────────────────────────────────
⚠️ 「未来函数」的两种含义，本栏只做第一种
────────────────────────────────────────────────────────────────────────────
  ①「预测未来的函数」= 本栏做的事：用 t 时刻**已经公开**的信息，推 t+1 的分布。
  ②「偷看未来的函数」= 量化里的 look-ahead bias，是 bug：回测时用了当时拿不到的数据，
     命中率虚高，上线即失效。

  本引擎用四条硬约束把自己钉死在 ①：
    1. **输入闭合**：只读当次构建的 market / macro / sentiment / community 四份产物，
       行情基准日 base_date 就是数据里的 as_of，引擎内不存在任何未来数据入口。
    2. **目标日严格在后**：target_date = base_date 的下一交易日（周末顺延），
       `assert_no_lookahead()` 强制 target_date > base_date，违反即判定为未来函数污染。
    3. **先存档，后结算**：预测落盘 forecast_history.json 时 settled=False；
       只有等到后续某次构建真的抓到 as_of == target_date 的行情，才回填实际涨跌并计分。
       **绝不用当次行情给当次预测打分**（那正是 look-ahead 的典型形态）。
    4. **零写死叙事**：与 01/02/03B 栏同一口径 —— 引擎里没有任何点位、日期、新闻事实；
       数据缺失就降级为「今日未获取 · 本栏不预测」，绝不回填历史预测充数。

────────────────────────────────────────────────────────────────────────────
预测模型（显式线性合成，可复算、可逐项拆解）
────────────────────────────────────────────────────────────────────────────
    标准化动量  z = 当次涨跌幅 / σ          σ 为该标的的典型日波动（策略参数）
    动量项      |z| ≤ 2σ  → +0.30 × z                延续
                |z| > 2σ  → 越界部分反向 −0.35 × 超出量   过度延展后的均值回归
    联动项      β × carry_z                carry_z = 美股三指均值 / σ(标普)，隔夜映射
    情绪项      s_senti × 情绪信号          情绪信号 ∈ [−1, +1]（舆情因子 + 社区多空）
    宏观项      s_macro × 宏观信号          宏观信号 ∈ [−1, +1]（当次快讯标题情感）

    μ(σ) = 动量项 + 联动项 + 情绪项 + 宏观项      截断到 ±1.5σ
    预期涨跌幅 = μ(σ) × σ
    预测区间   = 预期涨跌幅 ± 1.0σ（约 1 倍标准差，非置信区间保证）

    |μ(σ)| < 0.15  → 震荡（不给方向）
    置信度 = 0.30 × 数据覆盖率 + 0.30 × 驱动项一致度 + 0.25 × 信号强度 + 0.15 × 基准行情完整度
    置信度 < 0.35  → 该标的只报区间、不报方向

用法:
  python3 forecast.py                     # 读仓库内 4 份 json → 文本预测
  python3 forecast.py --json out.json     # 导出结构化预测（便于回测）
  python3 forecast.py --history           # 预测落盘 + 用当次行情结算历史预测
  python3 forecast.py --review            # 只看历史命中率回看
  python3 forecast.py --self-test         # 规则自检（含未来函数污染检测）
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import sentiment_nlp as nlp                  # 复用日报同一套中文金融情感词库
except Exception:                                # pragma: no cover - 词库缺失时宏观项降级为 0
    nlp = None

import rss_feeds as rss_mod                      # 第 5 路数据源：49 源 RSS 的可用率
from panorama import COVERAGE_ROUTES, _coverage_of as panorama_coverage  # 覆盖度同一口径

MINUS = '\u2212'
DASH = '\u2014'

# ---------------------------------------------------------------------------
# 模型参数（策略参数，不是任何一天的行情事实）
# ---------------------------------------------------------------------------
# 典型日波动 σ（百分点）与标的中文名：本模块自有（原配对交易模块已删除）。
# 这是**策略参数**（按资产类别给的波动量级），不是任何一天的行情事实。
SIGMA = {
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

# 预测顺序：港股（本报告落点）→ 美股 → 商品 → 汇率
ORDER = ['HSI', 'HSTECH', 'HSCE', 'SPX', 'NDQ', 'DJI', 'GOLD', 'WTI', 'BRENT', 'USDCNH', 'USDCNY']

# 各标的显示小数位（行情缺 decimals 时的兜底）
DECIMALS = {'USDCNH': 4, 'USDCNY': 4}

# β：对「全球风险偏好」（美股三指均值）的隔夜敏感度。
# 美股自身 β=0 —— 它就是信号来源，给它 β 等于让预测自己证明自己。
BETA_GLOBAL = {
    'HSI': 0.55, 'HSTECH': 0.75, 'HSCE': 0.50,
    'SPX': 0.0, 'NDQ': 0.0, 'DJI': 0.0,
    'GOLD': -0.12, 'WTI': 0.22, 'BRENT': 0.20,
    'USDCNH': -0.16, 'USDCNY': -0.12,
}
# 对情绪信号的敏感度：港股成长端最敏感，避险资产与汇率取负号
SENTI_SENS = {
    'HSI': 0.35, 'HSTECH': 0.50, 'HSCE': 0.30,
    'SPX': 0.18, 'NDQ': 0.24, 'DJI': 0.14,
    'GOLD': -0.14, 'WTI': 0.10, 'BRENT': 0.10,
    'USDCNH': -0.14, 'USDCNY': -0.10,
}
# 对宏观快讯信号的敏感度：宏观是慢变量，系数整体小于情绪
MACRO_SENS = {
    'HSI': 0.22, 'HSTECH': 0.26, 'HSCE': 0.20,
    'SPX': 0.16, 'NDQ': 0.18, 'DJI': 0.14,
    'GOLD': -0.10, 'WTI': 0.14, 'BRENT': 0.14,
    'USDCNH': -0.10, 'USDCNY': -0.08,
}

MOM_CONT = 0.30        # 动量延续系数（|z| 未超延展阈值时）
MOM_STRETCH = 2.0      # 超过 2σ 视为过度延展，超出部分转为均值回归
MOM_REVERT = 0.35      # 过度延展部分的回归系数
MU_CAP = 1.5           # 预期涨跌幅上限（σ 倍数）—— 单日预测不允许给出极端值
BAND_K = 1.0           # 预测区间半宽 = BAND_K × σ
FLAT_Z = 0.15          # |μ(σ)| 低于该值判「震荡」，不给方向
FLAT_REALIZED = 0.5    # 结算口径：实际涨跌幅在 ±0.5σ 以内才算「震荡兑现」
MIN_CONF = 0.35        # 置信度低于该值只报区间、不报方向

# 明日盘面倾向的加权（本报告以港股为落点）
STANCE_WEIGHT = {'HSI': 1.20, 'HSTECH': 1.00, 'HSCE': 0.80}

HISTORY_VERSION = 1
HISTORY_LIMIT = 600            # 存档上限，超出按 made_on 从旧到新裁剪
SETTLE_EXPIRE_DAYS = 10        # 目标日过去这么多天仍未结算 → 判为「未结算·作废」，不计入命中率
MIN_REVIEW_SAMPLE = 10         # 样本不足这个数只报样本量，不下命中率结论

RULE = ('口径：μ(σ) = 动量 + 联动 + 情绪 + 宏观项，截断 ±1.5σ；预期涨跌幅 = μ(σ) × σ，'
        '区间 = ±1.0σ；|μ(σ)| < 0.15 判震荡。规则化推导，不构成投资建议。')

LOOKAHEAD_NOTE = ('未来函数口径：只用基准日及之前的公开数据推下一交易日，预测先落盘、后结算，'
                  '不用当次行情给当次预测打分。')


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _num(v):
    if isinstance(v, bool):
        return None
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _clip01(v):
    return max(0.0, min(1.0, v))


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _is_date(s):
    s = str(s or '')
    if len(s) != 10 or s[4] != '-' or s[7] != '-':
        return False
    try:
        datetime.strptime(s, '%Y-%m-%d')
    except ValueError:
        return False
    return True


def _to_date(s):
    return datetime.strptime(s, '%Y-%m-%d').date() if _is_date(s) else None


def fmt_pct(v, dash=DASH):
    v = _num(v)
    if v is None:
        return dash
    return f"{MINUS if v < 0 else '+'}{abs(v):,.2f}%"


def fmt_price(v, nd=2, dash=DASH):
    v = _num(v)
    return dash if v is None else f'{v:,.{nd}f}'


def fmt_signed(v, nd=2, unit='', dash=DASH):
    """带符号数值，负号一律用 U+2212 真减号（与全站一致）。"""
    v = _num(v)
    if v is None:
        return dash
    return f"{MINUS if v < 0 else '+'}{abs(v):.{nd}f}{unit}"


def md_cn(date_str):
    """'2026-09-24' → '9 月 24 日'；无法解析时给出中性文案。"""
    return (f'{int(str(date_str)[5:7])} 月 {int(str(date_str)[8:10])} 日'
            if _is_date(date_str) else (date_str or '日期未标注'))


def next_trading_day(date_str):
    """下一交易日：自然日 +1，落在周六/周日则顺延到周一。

    只跳周末 —— 各市场的节假日表不在当次数据里，引擎不猜；
    真遇上假期，该条预测会在 SETTLE_EXPIRE_DAYS 后判为「未结算·作废」而不是硬凑一个命中。
    """
    d = _to_date(date_str)
    if d is None:
        return ''
    d += timedelta(days=1)
    while d.weekday() >= 5:                      # 5=周六 6=周日
        d += timedelta(days=1)
    return d.strftime('%Y-%m-%d')


def assert_no_lookahead(base_date, target_date):
    """未来函数污染检测：目标日必须严格晚于基准日。返回 (ok, 说明)。"""
    b, t = _to_date(base_date), _to_date(target_date)
    if b is None or t is None:
        return False, '基准日或目标日缺失，无法核验时序'
    if t <= b:
        return False, f'目标日 {target_date} 未晚于基准日 {base_date} —— 判定为未来函数污染'
    return True, f'目标日 {target_date} 晚于基准日 {base_date}，时序成立'


# ---------------------------------------------------------------------------
# 三路信号：全球联动 / 情绪 / 宏观
# ---------------------------------------------------------------------------
def _pct(quotes, key):
    return _num((quotes.get(key) or {}).get('pct'))


def global_carry(quotes):
    """美股三指均值 / σ(标普) → 隔夜风险偏好信号（σ 倍数），截断 ±3。"""
    pcts = [p for p in (_pct(quotes, k) for k in ('SPX', 'NDQ', 'DJI')) if p is not None]
    if not pcts:
        return None, {'n': 0, 'avg': None, 'text': '美股三指当次未获取，联动项按 0 处理'}
    avg = sum(pcts) / len(pcts)
    z = _clamp(avg / SIGMA['SPX'], -3.0, 3.0)
    return z, {'n': len(pcts), 'avg': round(avg, 3), 'z': round(z, 3),
               'text': f'美股 {len(pcts)} 指均值 {fmt_pct(avg)}（{fmt_signed(z, 2, "σ")}），作为下一交易日的隔夜映射'}


def sentiment_signal(sentiment, community):
    """舆情因子 + 社区多空家数 → [−1, +1] 情绪信号。两路都缺则返回 None。"""
    bits, parts = [], []
    m = (sentiment or {}).get('market') or {}
    net = _num(m.get('net_senti'))
    if net is not None and _num(m.get('news_count')):
        bits.append(_clamp(net * 2.0, -1.0, 1.0))
        parts.append(f'舆情净情感 {fmt_signed(net, 3)}')
    risk = _num(m.get('risk_score'))
    if risk is not None and risk > 0:
        bits.append(_clamp(-(risk - 40.0) / 60.0, -1.0, 0.25))
        parts.append(f'突发风险分 {risk:.0f}')

    counts = {'bull': 0, 'bear': 0, 'neutral': 0, 'mixed': 0}
    for c in ((community or {}).get('communities') or []):
        k = (c or {}).get('verdict_class')
        if k in counts:
            counts[k] += 1
    total = sum(counts.values())
    if total:
        breadth = (counts['bull'] - counts['bear']) / float(total)
        bits.append(_clamp(breadth * 1.5, -1.0, 1.0))
        parts.append(f"社区偏多 {counts['bull']} / 偏空 {counts['bear']}（{total} 源）")

    if not bits:
        return None, {'text': '舆情因子与社区研判当次均未获取，情绪项按 0 处理', 'counts': counts}
    sig = _clamp(sum(bits) / len(bits), -1.0, 1.0)
    return sig, {'signal': round(sig, 3), 'counts': counts,
                 'text': '、'.join(parts) + f' → 情绪信号 {fmt_signed(sig)}'}


def macro_signal(macro):
    """当次宏观快讯标题情感 → [−1, +1]。没有词库或没有快讯则返回 None。"""
    titles = []
    for blk in ((macro or {}).get('categories') or {}).values():
        for it in ((blk or {}).get('items') or []):
            t = (it.get('title') or '').strip()
            if t:
                titles.append(t)
    if not titles or not nlp:
        return None, {'n': len(titles),
                      'text': ('当次宏观快讯窗口内无条目，宏观项按 0 处理' if not titles
                               else '情感词库不可用，宏观项按 0 处理')}
    vals = []
    for t in titles:
        try:
            vals.append(float(nlp.score_text(t).get('sentiment') or 0.0))
        except Exception:                        # pragma: no cover - 单条打分失败跳过
            continue
    if not vals:
        return None, {'n': len(titles), 'text': '宏观快讯标题均未打出情感分，宏观项按 0 处理'}
    sig = _clamp(sum(vals) / len(vals) * 2.0, -1.0, 1.0)
    return sig, {'n': len(titles), 'signal': round(sig, 3),
                 'text': f'当次 {len(titles)} 条宏观快讯标题情感均值 → 宏观信号 {fmt_signed(sig)}'}


# ---------------------------------------------------------------------------
# 单标的预测
# ---------------------------------------------------------------------------
def _momentum_term(z):
    """动量延续 + 过度延展后的均值回归。返回 (贡献, 文案)。"""
    if abs(z) <= MOM_STRETCH:
        return MOM_CONT * z, f'当次动量 {fmt_signed(z, 2, "σ")} 未超 {MOM_STRETCH:.0f}σ 延展阈值，按延续处理'
    sign = 1.0 if z > 0 else -1.0
    over = abs(z) - MOM_STRETCH
    val = sign * (MOM_CONT * MOM_STRETCH - MOM_REVERT * over)
    return val, (f'当次动量 {fmt_signed(z, 2, "σ")} 超出 {MOM_STRETCH:.0f}σ 延展阈值 {over:.2f}σ，'
                 f'超出部分按均值回归反向计入')


def forecast_symbol(key, quotes, carry_z, senti, macro_s, coverage):
    """对单个标的给出下一交易日预测；当次行情缺失则返回 None（不预测）。"""
    q = quotes.get(key) or {}
    pct = _num(q.get('pct'))
    last = _num(q.get('last'))
    if pct is None and last is None:
        return None

    sigma = SIGMA.get(key) or 1.0
    drivers = []
    mu = 0.0

    if pct is not None:
        z = _clamp(pct / sigma, -4.0, 4.0)
        val, why = _momentum_term(z)
        mu += val
        drivers.append({'name': '动量项', 'value': round(val, 4), 'why': why})
    else:
        drivers.append({'name': '动量项', 'value': 0.0,
                        'why': '当次只取到点位、没有涨跌幅，动量项按 0 处理'})

    beta = BETA_GLOBAL.get(key, 0.0)
    if carry_z is not None and beta:
        val = beta * carry_z
        mu += val
        drivers.append({'name': '联动项', 'value': round(val, 4),
                        'why': f'对全球风险偏好的敏感度 β={fmt_signed(beta)} × 隔夜信号 {fmt_signed(carry_z, 2, "σ")}'})
    elif beta:
        drivers.append({'name': '联动项', 'value': 0.0, 'why': '美股三指当次未获取，联动项按 0 处理'})
    else:
        drivers.append({'name': '联动项', 'value': 0.0,
                        'why': '本标的是隔夜信号的来源之一，β 设为 0，不用自己预测自己'})

    s_sens = SENTI_SENS.get(key, 0.0)
    if senti is not None:
        val = s_sens * senti
        mu += val
        drivers.append({'name': '情绪项', 'value': round(val, 4),
                        'why': f'情绪敏感度 {fmt_signed(s_sens)} × 情绪信号 {fmt_signed(senti)}'})
    else:
        drivers.append({'name': '情绪项', 'value': 0.0, 'why': '舆情与社区当次均未获取，情绪项按 0 处理'})

    m_sens = MACRO_SENS.get(key, 0.0)
    if macro_s is not None:
        val = m_sens * macro_s
        mu += val
        drivers.append({'name': '宏观项', 'value': round(val, 4),
                        'why': f'宏观敏感度 {fmt_signed(m_sens)} × 宏观信号 {fmt_signed(macro_s)}'})
    else:
        drivers.append({'name': '宏观项', 'value': 0.0, 'why': '当次宏观快讯无条目，宏观项按 0 处理'})

    mu = _clamp(mu, -MU_CAP, MU_CAP)
    mu_pct = mu * sigma
    band = BAND_K * sigma
    low_pct, high_pct = mu_pct - band, mu_pct + band

    # 驱动项一致度：非零项里与合成方向同号的权重占比
    nz = [d['value'] for d in drivers if abs(d['value']) > 1e-9]
    if nz and abs(mu) > 1e-9:
        same = sum(abs(v) for v in nz if (v > 0) == (mu > 0))
        agreement = _clip01(same / sum(abs(v) for v in nz))
    else:
        agreement = 0.0

    base_ok = 1.0 if (pct is not None and last is not None) else 0.5
    strength = _clip01(abs(mu) / 0.5)
    confidence = round(_clip01(0.30 * coverage + 0.30 * agreement
                               + 0.25 * strength + 0.15 * base_ok), 2)

    if abs(mu) < FLAT_Z:
        direction, dir_word = 0, '震荡'
    elif confidence < MIN_CONF:
        direction, dir_word = 0, '方向存疑'
    else:
        direction = 1 if mu > 0 else -1
        dir_word = '看涨' if direction > 0 else '看跌'

    nd = int(_num(q.get('decimals')) or DECIMALS.get(key, 2))
    price_low = price_high = price_mid = None
    if last is not None:
        price_low = last * (1 + low_pct / 100.0)
        price_high = last * (1 + high_pct / 100.0)
        price_mid = last * (1 + mu_pct / 100.0)

    return {
        'key': key,
        'name': q.get('name') or NAMES.get(key, key),
        'base_last': last,
        'base_pct': pct,
        'decimals': nd,
        'sigma': sigma,
        'mu_sigma': round(mu, 3),
        'mu_pct': round(mu_pct, 3),
        'low_pct': round(low_pct, 3),
        'high_pct': round(high_pct, 3),
        'price_mid': None if price_mid is None else round(price_mid, nd),
        'price_low': None if price_low is None else round(price_low, nd),
        'price_high': None if price_high is None else round(price_high, nd),
        'direction': direction,
        'dir_word': dir_word,
        'confidence': confidence,
        'agreement': round(agreement, 2),
        'drivers': drivers,
        'range_text': (f'预期 {fmt_pct(mu_pct)}，区间 {fmt_pct(low_pct)} ~ {fmt_pct(high_pct)}'
                       + (f'（点位 {fmt_price(price_low, nd)} ~ {fmt_price(price_high, nd)}）'
                          if price_low is not None else '')),
    }


# ---------------------------------------------------------------------------
# 主预测
# ---------------------------------------------------------------------------
def predict(market=None, macro=None, sentiment=None, community=None, rss=None,
            now=None, review=None):
    """五路当次数据 → 下一交易日预测。纯函数，无 IO，不联网。

    rss 是**第 5 路**（49 源 RSS 情报源的可用率）。它与 01 栏共用同一套覆盖度口径，
    只是折算成置信度而不是结论 —— 05 节大面积未获取时，预测置信度必须同步下调。
    """
    now = now or datetime.now(timezone.utc)
    market, macro = market or {}, macro or {}
    sentiment, community = sentiment or {}, community or {}
    quotes = {k: v for k, v in (market.get('quotes') or {}).items() if isinstance(v, dict)}

    # ---------- 覆盖度与数据缺口（与 01 栏同一套口径） ----------
    gaps = []
    live_quotes = sum(1 for q in quotes.values() if _num(q.get('pct')) is not None)
    if not live_quotes:
        gaps.append('行情（market_data.json）未获取或全部为空值 —— 没有基准就没有预测')
    macro_items_n = sum(len(((blk or {}).get('items') or []))
                        for blk in ((macro.get('categories') or {}).values()))
    if not macro_items_n:
        gaps.append('宏观快讯（macro_data.json）窗口内无条目 —— 宏观项按 0 处理')
    if not ((sentiment or {}).get('market') or {}).get('news_count'):
        gaps.append('舆情因子（sentiment_data.json）未获取 —— 情绪项只用社区多空家数')
    valid_comm_n = sum(1 for c in ((community or {}).get('communities') or [])
                       if (c or {}).get('verdict_class') in ('bull', 'bear', 'neutral', 'mixed'))
    if not valid_comm_n:
        gaps.append('社区研判（community_data.json）未获取 —— 多空家数无法统计')
    pre_gaps = len(gaps)                     # 前四路的缺口数，RSS 由 rss_sig 单独表达
    rss_sig = rss_mod.coverage_signal(rss)
    if not rss_sig['available']:
        gaps.append(rss_sig['why'])
    coverage = panorama_coverage(pre_gaps, rss_sig)
    if rss_sig['available'] and rss_sig['ratio'] < 0.999:
        # 覆盖度被 RSS 拉低时必须说清是谁拉低的（与 01 栏同一口径）。
        gaps.append(rss_sig['why'])

    # ---------- 基准日与目标日（未来函数的时序底线） ----------
    base_date = ''
    for k in ORDER:
        as_of = (quotes.get(k) or {}).get('as_of')
        if _is_date(as_of):
            base_date = as_of
            break
    if not base_date and _is_date(market.get('fetch_date')):
        base_date = market['fetch_date']
    target_date = next_trading_day(base_date)
    ok_seq, seq_note = assert_no_lookahead(base_date, target_date)

    out = {
        'generated_at': now.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'run_date': now.strftime('%Y-%m-%d'),
        'base_date': base_date,
        'target_date': target_date,
        'horizon': '下一交易日',
        'coverage': coverage,
        'data_gaps': gaps,
        'no_lookahead': {'ok': ok_seq, 'note': seq_note},
        'rule': RULE,
        'lookahead_note': LOOKAHEAD_NOTE,
        'forecasts': [],
        'headline': None,
        'signals': {},
        'stance': {},
        'review': review or empty_review(),
        'available': False,
        'unavailable_reason': '',
    }

    if not live_quotes:
        out['unavailable_reason'] = 'no_quotes'
        return out
    if not ok_seq:
        out['unavailable_reason'] = 'bad_sequence'
        return out

    carry_z, carry_meta = global_carry(quotes)
    senti, senti_meta = sentiment_signal(sentiment, community)
    macro_s, macro_meta = macro_signal(macro)
    out['signals'] = {
        'carry': dict(carry_meta, value=carry_z),
        'sentiment': dict(senti_meta, value=senti),
        'macro': dict(macro_meta, value=macro_s),
    }

    forecasts = []
    for key in ORDER:
        f = forecast_symbol(key, quotes, carry_z, senti, macro_s, coverage)
        if f:
            f['target_date'] = target_date
            f['base_date'] = (quotes.get(key) or {}).get('as_of') or base_date
            forecasts.append(f)
    out['forecasts'] = forecasts
    out['available'] = bool(forecasts)
    if not forecasts:
        out['unavailable_reason'] = 'no_symbols'
        return out

    by_key = {f['key']: f for f in forecasts}
    out['headline'] = by_key.get('HSI') or forecasts[0]

    # ---------- 明日盘面倾向（港股加权，置信度为权重的一部分） ----------
    num = den = 0.0
    for k, w in STANCE_WEIGHT.items():
        f = by_key.get(k)
        if not f:
            continue
        ww = w * max(0.1, f['confidence'])
        num += f['mu_sigma'] * ww
        den += ww
    stance_z = (num / den) if den else 0.0
    up = sum(1 for f in forecasts if f['direction'] > 0)
    down = sum(1 for f in forecasts if f['direction'] < 0)
    flat = len(forecasts) - up - down
    conf_avg = round(sum(f['confidence'] for f in forecasts) / len(forecasts), 2)

    if den == 0:
        label, tag = '港股三指当次未获取 · 不给明日倾向', 'unknown'
    elif conf_avg < MIN_CONF:
        label, tag = '置信度不足 · 只报区间不报方向', 'unknown'
    elif stance_z >= 0.45:
        label, tag = '偏多（预期高于典型日波动的一半）', 'up'
    elif stance_z >= FLAT_Z:
        label, tag = '弱偏多（幅度有限，宜右侧确认）', 'up'
    elif stance_z > -FLAT_Z:
        label, tag = '震荡（方向不明，等待驱动）', 'flat'
    elif stance_z > -0.45:
        label, tag = '弱偏空（回踩概率偏高）', 'down'
    else:
        label, tag = '偏空（预期跌幅超过典型日波动的一半）', 'down'

    out['stance'] = {
        'z': round(stance_z, 3), 'label': label, 'tag': tag,
        'breadth': {'up': up, 'down': down, 'flat': flat, 'total': len(forecasts)},
        'confidence': conf_avg,
        'summary': (f'{len(forecasts)} 个标的中看涨 {up} / 看跌 {down} / 震荡 {flat}，'
                    f'港股加权倾向 {fmt_signed(stance_z, 2, "σ")}，平均置信度 {int(conf_avg * 100)}%'),
    }

    hl = out['headline']
    return out


# ---------------------------------------------------------------------------
# 预测存档与命中率回看（先存档、后结算 —— 这是不偷看未来的关键）
# ---------------------------------------------------------------------------
def empty_review():
    return {'settled': 0, 'hits': 0, 'hit_rate': None, 'in_band': 0, 'band_rate': None,
            'pending': 0, 'expired': 0, 'by_symbol': [], 'enough_sample': False,
            'text': '暂无已结算的历史预测 —— 命中率要等预测到期、且抓到目标日实际行情后才产生。'}


def new_history():
    return {'version': HISTORY_VERSION, 'records': []}


def load_history(path):
    if not path or not os.path.exists(path):
        return new_history()
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return new_history()
    if not isinstance(data, dict) or not isinstance(data.get('records'), list):
        return new_history()
    data.setdefault('version', HISTORY_VERSION)
    data['records'] = [r for r in data['records'] if isinstance(r, dict)]
    return data


def save_history(path, history):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=1)
        return True
    except OSError as e:
        print(f'  ⚠️ AI 预测存档写入失败（{e}）→ 本次不影响预测输出', file=sys.stderr)
        return False


def settle_history(history, market, now=None):
    """用当次行情结算**历史**预测：只结算目标日 == 当次行情日期的那些条目。

    这是全模块最关键的一段：当次预测的 target_date 必然晚于当次行情日期，
    因此永远不可能被这一步结算 —— 结构上杜绝「用未来数据给自己打分」。
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    quotes = {k: v for k, v in ((market or {}).get('quotes') or {}).items() if isinstance(v, dict)}
    settled_now = 0
    for r in history.get('records') or []:
        if r.get('settled'):
            continue
        q = quotes.get(r.get('key')) or {}
        as_of = q.get('as_of')
        actual = _num(q.get('pct'))
        if _is_date(as_of) and as_of == r.get('target_date') and actual is not None:
            direction = r.get('direction') or 0
            if direction == 0:
                # 「震荡」的兑现口径：实际波动没超过半个典型日波动才算说对了。
                # 用 FLAT_REALIZED 而不是预测侧的 FLAT_Z —— 预测阈值卡的是 μ，
                # 实际收盘天然带噪声，拿 μ 的阈值去卡实际值等于给自己判不及格。
                hit = abs(actual) < FLAT_REALIZED * (_num(r.get('sigma')) or 1.0)
            else:
                hit = (actual > 0) == (direction > 0) and abs(actual) > 1e-9
            r['settled'] = True
            r['actual_pct'] = round(actual, 3)
            r['hit'] = bool(hit)
            r['in_band'] = bool(_num(r.get('low_pct')) is not None
                                and r['low_pct'] <= actual <= r['high_pct'])
            r['error_pct'] = round(actual - (_num(r.get('mu_pct')) or 0.0), 3)
            r['settled_on'] = now.strftime('%Y-%m-%d')
            settled_now += 1
            continue
        t = _to_date(r.get('target_date'))
        if t is not None and (today - t).days > SETTLE_EXPIRE_DAYS:
            r['expired'] = True                  # 假期顺延 / 目标日行情始终没抓到 → 作废，不硬凑
    return settled_now


def review_history(history):
    """已结算记录 → 命中率回看。未结算与作废的**不计分**。"""
    recs = history.get('records') or []
    settled = [r for r in recs if r.get('settled')]
    pending = [r for r in recs if not r.get('settled') and not r.get('expired')]
    expired = [r for r in recs if r.get('expired') and not r.get('settled')]
    rev = empty_review()
    rev['pending'] = len(pending)
    rev['expired'] = len(expired)
    if not settled:
        return rev

    hits = sum(1 for r in settled if r.get('hit'))
    band = sum(1 for r in settled if r.get('in_band'))
    rev.update({
        'settled': len(settled),
        'hits': hits,
        'hit_rate': round(hits / len(settled), 3),
        'in_band': band,
        'band_rate': round(band / len(settled), 3),
        'enough_sample': len(settled) >= MIN_REVIEW_SAMPLE,
    })
    per = {}
    for r in settled:
        k = r.get('key')
        per.setdefault(k, {'key': k, 'name': r.get('name') or NAMES.get(k, k),
                           'n': 0, 'hits': 0, 'abs_err': 0.0})
        per[k]['n'] += 1
        per[k]['hits'] += 1 if r.get('hit') else 0
        per[k]['abs_err'] += abs(_num(r.get('error_pct')) or 0.0)
    for v in per.values():
        v['hit_rate'] = round(v['hits'] / v['n'], 3)
        v['mae_pct'] = round(v['abs_err'] / v['n'], 3)
        v.pop('abs_err', None)
    rev['by_symbol'] = sorted(per.values(), key=lambda v: (-v['n'], v['key']))
    dates = sorted({r.get('target_date') for r in settled if _is_date(r.get('target_date'))})
    rev['span'] = {'from': dates[0] if dates else '', 'to': dates[-1] if dates else ''}
    if rev['enough_sample']:
        rev['text'] = (f"已结算 {rev['settled']} 条：方向命中 {hits} 条"
                       f"（{rev['hit_rate'] * 100:.1f}%），实际落在预测区间内 {band} 条"
                       f"（{rev['band_rate'] * 100:.1f}%）；待结算 {rev['pending']} 条。")
    else:
        rev['text'] = (f"已结算 {rev['settled']} 条（方向命中 {hits} 条），"
                       f"样本不足 {MIN_REVIEW_SAMPLE} 条，只报样本不下命中率结论；"
                       f"待结算 {rev['pending']} 条。")
    return rev


def append_forecasts(history, data, limit=HISTORY_LIMIT):
    """把当次预测写进存档（settled=False）。同一基准日重复构建时原地覆盖，不灌水。"""
    recs = [r for r in (history.get('records') or [])]
    base = data.get('base_date')
    keys = {f['key'] for f in (data.get('forecasts') or [])}
    recs = [r for r in recs
            if not (r.get('base_date') == base and r.get('key') in keys and not r.get('settled'))]
    for f in data.get('forecasts') or []:
        recs.append({
            'made_on': data.get('run_date'), 'made_at': data.get('generated_at'),
            'key': f['key'], 'name': f['name'],
            'base_date': f.get('base_date') or base, 'target_date': f['target_date'],
            'base_last': f['base_last'], 'base_pct': f['base_pct'], 'sigma': f['sigma'],
            'direction': f['direction'], 'dir_word': f['dir_word'],
            'mu_pct': f['mu_pct'], 'low_pct': f['low_pct'], 'high_pct': f['high_pct'],
            'confidence': f['confidence'], 'settled': False,
        })
    recs.sort(key=lambda r: (str(r.get('target_date') or ''), str(r.get('key') or '')))
    history['records'] = recs[-limit:]
    history['version'] = HISTORY_VERSION
    return history


def default_history_path(root=REPO_ROOT):
    return os.environ.get('FORECAST_HISTORY', os.path.join(root, 'forecast_history.json'))


def review_from_history(path=None, market=None, now=None, persist=False, data=None):
    """读存档 → 用当次行情结算历史预测 → 命中率回看（可选把当次预测写回存档）。

    任何 IO 故障都退化为空回看，绝不阻断构建。
    """
    path = path or default_history_path()
    try:
        history = load_history(path)
        settled_now = settle_history(history, market, now=now)
        rev = review_history(history)
        rev['settled_this_run'] = settled_now
        if persist and data:
            append_forecasts(history, data)
        if persist:
            rev['saved'] = save_history(path, history)
        rev['path'] = path
        return rev
    except Exception as e:                       # noqa: BLE001 - 回看失败绝不阻断预测
        print(f'  ⚠️ AI 预测回看异常（{type(e).__name__}: {e}）→ 本次不展示命中率', file=sys.stderr)
        return empty_review()


# ---------------------------------------------------------------------------
# 渲染：网页（report.html 的 class 体系）与微信（全内联样式）共用同一份预测
# ---------------------------------------------------------------------------
def _esc(t):
    import html
    return html.escape(str(t if t is not None else ''), quote=False)


UNAVAILABLE_TEXT = {
    'no_quotes': ('当次行情（market_data.json）未获取或全部为空值 —— '
                  '没有基准价就没有预测，本栏不猜方向、不回填上一版预测。'),
    'bad_sequence': ('行情基准日缺失或目标日未能排到基准日之后 —— '
                     '时序不成立时宁可不预测，也不产出可能带未来函数污染的结论。'),
    'no_symbols': '当次没有任何标的同时取到点位与涨跌幅，本栏不预测。',
}


def _unavailable_line(data):
    reason = data.get('unavailable_reason') or 'no_quotes'
    return UNAVAILABLE_TEXT.get(reason, UNAVAILABLE_TEXT['no_quotes'])


def _dir_chip(f):
    return f"{f['dir_word']} · {fmt_pct(f['mu_pct'])} · 置信度 {int(f['confidence'] * 100)}%"


def render_web(data):
    """04 节网页版 HTML（沿用 report.html 的 .pub-* / .pixel-list / .quant-box 类）。"""
    head = (f'<div class="pub-meta" style="margin:0 0 8px;">'
            f'预测生成 {_esc(data["generated_at"])} · 基准日 {_esc(data["base_date"] or "未获取")} · '
            f'目标日 {_esc(data["target_date"] or "未获取")}（{_esc(data["horizon"])}） · '
            f'数据覆盖 {int(data["coverage"] * 100)}% · {_esc(data["lookahead_note"])}</div>')

    if not data.get('available'):
        return '\n'.join([
            head,
            '<div class="pub-sub">◆ 今日未获取 —— 本栏不预测</div>',
            f'<div style="font-size:12px;line-height:1.8;">{_esc(_unavailable_line(data))}'
            '恢复命令：<code>python3 market_data.py &amp;&amp; python3 forecast.py</code>。</div>',
            _review_web(data.get('review') or empty_review()),
        ])

    st = data['stance']
    parts = [head]
    parts.append('<div class="pub-sub">◆ 明日盘面倾向（港股加权）</div>')
    parts.append(
        '<div class="quant-box">'
        f'<div style="font-size:13px;font-weight:700;color:#000;">结论：{_esc(st["label"])}'
        f'　<span style="background:#000;color:#39ff14;padding:1px 6px;font-size:12px;">'
        f'{_esc(fmt_signed(st["z"], 2, "σ"))} · 置信度 {int(st["confidence"] * 100)}%</span></div>'
        f'<div style="margin-top:6px;">{_esc(st["summary"])}</div>'
        f'<div class="pub-meta" style="margin-top:6px;">时序核验：{_esc(data["no_lookahead"]["note"])}</div>'
        '</div>')

    parts.append('<div class="pub-sub" style="margin-top:12px;">'
                 f'◆ 逐标的预测 · 目标日 {_esc(md_cn(data["target_date"]))}</div>')
    rows = ['<table class="quote-table">',
            '<tr><th>标的</th><th>基准</th><th>方向</th><th>预期涨跌幅</th>'
            '<th>预测区间</th><th>点位区间</th><th>置信度</th></tr>']
    for f in data['forecasts']:
        nd = f['decimals']
        rows.append(
            f'<tr><td>{_esc(f["name"])}</td>'
            f'<td>{_esc(fmt_price(f["base_last"], nd))}（{_esc(fmt_pct(f["base_pct"]))}）</td>'
            f'<td>{_esc(f["dir_word"])}</td>'
            f'<td class="q-pct">{_esc(fmt_pct(f["mu_pct"]))}</td>'
            f'<td class="q-pct">{_esc(fmt_pct(f["low_pct"]))} ~ {_esc(fmt_pct(f["high_pct"]))}</td>'
            f'<td>{_esc(fmt_price(f["price_low"], nd))} ~ {_esc(fmt_price(f["price_high"], nd))}</td>'
            f'<td>{int(f["confidence"] * 100)}%</td></tr>')
    rows.append('</table>')
    parts.append('\n'.join(rows))

    hl = data['headline']
    parts.append('<div class="pub-sub" style="margin-top:12px;">◆ 驱动拆解（以'
                 + _esc(hl['name']) + '为例，逐项可复算）</div>')
    parts.append('<ul class="pixel-list">' + ''.join(
        f'<li><strong>{_esc(d["name"])}：</strong>{_esc(fmt_signed(d["value"], 3, "σ"))}　{_esc(d["why"])}</li>'
        for d in hl['drivers']) + '</ul>')
    parts.append(f'<div class="pub-meta">合成 μ = {_esc(fmt_signed(hl["mu_sigma"], 3, "σ"))} × σ {hl["sigma"]:.2f} '
                 f'= {_esc(fmt_pct(hl["mu_pct"]))}；{_esc(hl["range_text"])}。'
                 f'驱动项一致度 {int(hl["agreement"] * 100)}%。</div>')

    sig = data['signals']
    parts.append('<div class="pub-sub" style="margin-top:12px;">◆ 三路输入信号</div>')
    parts.append('<ul class="pixel-list">' + ''.join(
        f'<li><strong>{label}：</strong>{_esc((sig.get(k) or {}).get("text") or "未获取")}</li>'
        for k, label in (('carry', '全球联动'), ('sentiment', '情绪'), ('macro', '宏观')))
        + '</ul>')

    parts.append(_review_web(data.get('review') or empty_review()))
    if data['data_gaps']:
        parts.append('<div class="pub-meta" style="margin-top:8px;">数据缺口：'
                     + _esc('；'.join(data['data_gaps'])) + '（缺口项一律按 0 处理，不回填旧预测）</div>')
    parts.append(f'<div class="pub-meta" style="margin-top:6px;">{_esc(data["rule"])}</div>')
    return '\n'.join(parts)


def _review_web(rev):
    rows = ''.join(
        f'<li><strong>{_esc(v["name"])}</strong>：{v["n"]} 条已结算 · 方向命中 '
        f'{v["hit_rate"] * 100:.0f}% · 平均绝对误差 {v["mae_pct"]:.2f} 个百分点</li>'
        for v in (rev.get('by_symbol') or [])[:6])
    return ('<div class="pub-sub" style="margin-top:12px;">◆ 历史预测回看（先存档 · 后结算）</div>'
            f'<div style="font-size:12px;line-height:1.8;">{_esc(rev.get("text") or "")}</div>'
            + (f'<ul class="pixel-list">{rows}</ul>' if rows else '')
            + '<div class="pub-meta">只对目标日已抓到实际行情的预测计分；'
              f'逾期 {SETTLE_EXPIRE_DAYS} 天未结算的作废，不计入命中率。</div>')


def _wechat_table(data, green):
    """逐标的预测表：微信里表格比一堆带边框的 div 省一大半字符，信息量不变。"""
    th = (f'padding:3px 5px;font-size:10px;color:#07101b;background:{green};'
          'text-align:left;white-space:nowrap;')
    td = ('padding:3px 5px;font-size:10.5px;color:#dce5fb;'
          'border-bottom:1px solid #2b3855;white-space:nowrap;')
    rows = ['<table style="width:100%;border-collapse:collapse;margin:4px 0;'
            'background:#0d1426;color:#dce5fb;border:1px solid #2b3855;">'
            f'<tr><th style="{th}">标的</th><th style="{th}">方向</th>'
            f'<th style="{th}">预期</th><th style="{th}">区间</th>'
            f'<th style="{th}">点位区间</th><th style="{th}">置信</th></tr>']
    for f in data['forecasts']:
        nd = f['decimals']
        price = (f'{fmt_price(f["price_low"], nd)}~{fmt_price(f["price_high"], nd)}'
                 if f['price_low'] is not None else DASH)
        rows.append(
            f'<tr><td style="{td}">{_esc(f["name"])}</td>'
            f'<td style="{td}">{_esc(f["dir_word"])}</td>'
            f'<td style="{td}">{_esc(fmt_pct(f["mu_pct"]))}</td>'
            f'<td style="{td}">{_esc(fmt_pct(f["low_pct"]))}~{_esc(fmt_pct(f["high_pct"]))}</td>'
            f'<td style="{td}">{_esc(price)}</td>'
            f'<td style="{td}">{int(f["confidence"] * 100)}%</td></tr>')
    rows.append('</table>')
    return ''.join(rows)


def render_wechat(data, neon='#b6ff4a', green='#4fe5ff', ink='#edf2ff', compact=False):
    """04 栏微信版 HTML（全内联样式，口径与网页版完全一致）。

    compact=True 时收敛为「倾向 + 逐标的表 + 回看一行」，砍掉驱动拆解 / 三路信号 ——
    微信单页有 10 万字符硬上限，推送侧按剩余预算选版本（见 tools/wechat_push.py）。
    完整拆解在网页 04 节，两版数字同源，不会出现口径漂移。
    """
    def sub(t):
        return f'<div style="color:{green};font-weight:700;font-size:13px;margin:10px 0 4px;">{t}</div>'

    def meta(t):
        return f'<div style="color:#9aa6c3;font-size:10.5px;margin-top:6px;line-height:1.7;">{t}</div>'

    head = (f'预测生成 {_esc(data["generated_at"])} · 基准日 '
            f'{_esc(data["base_date"] or "未获取")} · 目标日 '
            f'{_esc(data["target_date"] or "未获取")}（{_esc(data["horizon"])}） · '
            f'数据覆盖 {int(data["coverage"] * 100)}%')
    out = [f'<div style="font-size:11.5px;line-height:1.85;color:{ink};">'
           + meta(head + (' · ' + _esc(data['lookahead_note']) if not compact else ''))]

    if not data.get('available'):
        out.append(sub('◆ 今日未获取 —— 本栏不预测') + _esc(_unavailable_line(data)))
        if not compact:
            out.append(_review_wechat(data.get('review') or empty_review(), green))
        out.append('</div>')
        return ''.join(out)

    st = data['stance']
    out.append(sub('◆ 明日盘面倾向（港股加权）'))
    out.append(f'<strong style="background:#101b2c;color:{neon};font-weight:700;padding:2px 6px;border:1px solid #435d31;">'
               f'{_esc(st["label"])} · {_esc(fmt_signed(st["z"], 2, "σ"))} · '
               f'置信度 {int(st["confidence"] * 100)}%</strong>'
               f'<br/>{_esc(st["summary"])}')
    if not compact:
        out.append(meta('时序核验：' + _esc(data['no_lookahead']['note'])))

    out.append(sub(f'◆ 逐标的预测 · 目标日 {_esc(md_cn(data["target_date"]))}'))
    out.append(_wechat_table(data, green))

    if compact:
        rev = data.get('review') or empty_review()
        out.append(meta('历史预测回看（先存档 · 后结算）：' + _esc(rev.get('text') or '')
                        + '　完整驱动拆解与三路输入信号见网页 04 节。'))
        out.append('</div>')
        return ''.join(out)

    hl = data['headline']
    out.append(sub(f'◆ 驱动拆解（以{_esc(hl["name"])}为例）'))
    out.append(''.join(f'· <strong>{_esc(d["name"])}</strong> {_esc(fmt_signed(d["value"], 3, "σ"))}　{_esc(d["why"])}<br/>'
                       for d in hl['drivers']))
    out.append(f'合成 μ = {_esc(fmt_signed(hl["mu_sigma"], 3, "σ"))} × σ {hl["sigma"]:.2f} = '
               f'{_esc(fmt_pct(hl["mu_pct"]))}，驱动项一致度 {int(hl["agreement"] * 100)}%。')

    sig = data['signals']
    out.append(sub('◆ 三路输入信号'))
    out.append(''.join(
        f'· <strong>{label}</strong>：{_esc((sig.get(k) or {}).get("text") or "未获取")}<br/>'
        for k, label in (('carry', '全球联动'), ('sentiment', '情绪'), ('macro', '宏观'))))

    out.append(_review_wechat(data.get('review') or empty_review(), green))
    if data['data_gaps']:
        out.append(meta('数据缺口：' + _esc('；'.join(data['data_gaps'])) + '（按 0 处理，不回填旧预测）'))
    out.append(meta(_esc(data['rule'])))
    out.append('</div>')
    return ''.join(out)


def render_wechat_line(data, green='#4fe5ff'):
    """预算实在不够时的最后一版：一行摘要，宁可少说，也不删到让人误读。"""
    if not data.get('available'):
        return (f'<div style="color:{green};font-weight:700;font-size:12px;margin:8px 0 2px;">'
                f'◆ AI 预测 · 未来函数</div><div style="font-size:11px;line-height:1.8;">'
                f'{_esc(_unavailable_line(data))}</div>')
    st = data['stance']
    hl = data['headline']
    return (f'<div style="color:{green};font-weight:700;font-size:12px;margin:8px 0 2px;">'
            f'◆ AI 预测 · 未来函数 · 目标日 {_esc(md_cn(data["target_date"]))}</div>'
            f'<div style="font-size:11px;line-height:1.8;">明日盘面倾向：{_esc(st["label"])}'
            f'（{_esc(fmt_signed(st["z"], 2, "σ"))} · 置信度 {int(st["confidence"] * 100)}%）；'
            f'{_esc(hl["name"])} {_esc(hl["dir_word"])} {_esc(fmt_pct(hl["mu_pct"]))}，'
            f'区间 {_esc(fmt_pct(hl["low_pct"]))} ~ {_esc(fmt_pct(hl["high_pct"]))}。'
            f'{_esc(st["summary"])}　'
            '本条为微信单页字符预算内的摘要版，完整逐标的预测与驱动拆解见网页 04 节。</div>')


def _review_wechat(rev, green='#4fe5ff'):
    head = (f'<div style="color:{green};font-weight:700;font-size:13px;margin:10px 0 4px;">'
            '◆ 历史预测回看（先存档 · 后结算）</div>')
    rows = ''.join(
        f'· <strong>{_esc(v["name"])}</strong> {v["n"]} 条已结算 · 命中 '
        f'{v["hit_rate"] * 100:.0f}% · 平均绝对误差 {v["mae_pct"]:.2f} 个百分点<br/>'
        for v in (rev.get('by_symbol') or [])[:6])
    return (head + _esc(rev.get('text') or '') + '<br/>' + rows
            + f'<div style="color:#9aa6c3;font-size:10.5px;margin-top:4px;line-height:1.7;">'
              '只对目标日已抓到实际行情的预测计分，不用当次行情给当次预测打分。</div>')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def predict_from_files(root=REPO_ROOT, now=None, history=False):
    """按仓库约定路径读五份 json（环境变量可覆盖，与建站/推送一致）→ 预测。"""
    market = _load(os.environ.get('MARKET_DATA', os.path.join(root, 'market_data.json')))
    macro = _load(os.environ.get('MACRO_DATA', os.path.join(root, 'macro_data.json')))
    sentiment = _load(os.environ.get('SENTIMENT_DATA', os.path.join(root, 'sentiment_data.json')))
    community = _load(os.environ.get('COMMUNITY_DATA', os.path.join(root, 'community_data.json')))
    rss = _load(os.environ.get('RSSFEEDS_DATA', os.path.join(root, 'rssfeeds_data.json')))
    # 先用当次行情结算历史预测（只会碰到目标日已到的旧预测），再算当次预测
    rev = review_from_history(default_history_path(root), market=market, now=now)
    data = predict(market=market, macro=macro, sentiment=sentiment, community=community,
                   rss=rss, now=now, review=rev)
    if history:
        data['review'] = review_from_history(default_history_path(root), market=market,
                                             now=now, persist=True, data=data)
    return data


def text_report(d):
    lines = [f'🔮 AI 预测 · 基准日 {d["base_date"] or "未获取"} → 目标日 '
             f'{d["target_date"] or "未获取"}（{d["horizon"]}） · 覆盖 {int(d["coverage"] * 100)}%',
             f'   时序核验: {d["no_lookahead"]["note"]}']
    if not d.get('available'):
        lines.append('   ⚠️ ' + _unavailable_line(d))
    else:
        st = d['stance']
        lines.append(f'   ▶ 明日盘面倾向: {st["label"]} · {fmt_signed(st["z"], 2, "σ")} · '
                     f'置信度 {int(st["confidence"] * 100)}%')
        lines.append(f'     {st["summary"]}')
        for f in d['forecasts']:
            nd = f['decimals']
            price = (f'  点位 {fmt_price(f["price_low"], nd)}~{fmt_price(f["price_high"], nd)}'
                     if f['price_low'] is not None else '')
            lines.append(f'   · {f["name"]: <10} {f["dir_word"]: <4} {fmt_pct(f["mu_pct"]): >9}'
                         f'  区间 {fmt_pct(f["low_pct"])}~{fmt_pct(f["high_pct"])}{price}'
                         f'  置信度 {int(f["confidence"] * 100)}%')
    lines.append('   ◆ 回看: ' + ((d.get('review') or {}).get('text') or ''))
    for g in d.get('data_gaps') or []:
        lines.append(f'   ⚠️ {g}')
    return '\n'.join(lines)


def _self_test():                                # pragma: no cover - 供 CLI 自检使用
    now = datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc)
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(('  ✅ ' if cond else '  ❌ ') + msg)
        ok = ok and bool(cond)

    base = now.strftime('%Y-%m-%d')
    prev = (now - timedelta(days=1)).strftime('%Y-%m-%d')

    def mk(**pcts):
        return {'fetch_date': base, 'quotes': {
            k: {'name': NAMES[k], 'last': 100.0 * (1 + i), 'pct': v,
                'as_of': base, 'decimals': 2}
            for i, (k, v) in enumerate(pcts.items())}}

    empty = predict(now=now)
    check(not empty['available'] and empty['forecasts'] == [],
          '空数据：不产出任何预测，本栏降级为「今日未获取」')
    check(empty['unavailable_reason'] == 'no_quotes', '空数据：降级原因标注为 no_quotes')

    bull = mk(HSI=1.2, HSTECH=1.9, HSCE=1.0, SPX=1.0, NDQ=1.5, DJI=0.7)
    d = predict(market=bull, now=now)
    check(d['available'] and len(d['forecasts']) == 6, f'普涨行情：6 个标的全部给出预测')
    check(d['stance']['tag'] == 'up', f'普涨行情：明日倾向 {d["stance"]["label"]}')
    check(d['target_date'] > d['base_date'], '目标日严格晚于基准日（未来函数时序底线）')
    check(d['no_lookahead']['ok'], '时序核验通过')

    bear = mk(HSI=-1.5, HSTECH=-2.2, HSCE=-1.3, SPX=-1.2, NDQ=-1.8, DJI=-0.9)
    d2 = predict(market=bear, now=now)
    check(d2['stance']['tag'] == 'down', f'普跌行情：明日倾向 {d2["stance"]["label"]}')
    check(d['headline']['mu_pct'] > d2['headline']['mu_pct'],
          '同一引擎在多空两组行情上给出相反预期 —— 结论来自数据而非模板')

    stretched = mk(HSI=5.0, HSTECH=6.0, HSCE=4.5)
    d3 = predict(market=stretched, now=now)
    hsi3 = next(f for f in d3['forecasts'] if f['key'] == 'HSI')
    check(hsi3['mu_sigma'] < (5.0 / SIGMA['HSI']) * MOM_CONT,
          '过度延展（>2σ）的涨幅被均值回归压低，而不是线性外推')

    flat = mk(HSI=0.02, HSTECH=0.03, HSCE=0.01)
    d4 = predict(market=flat, now=now)
    check(all(f['direction'] == 0 for f in d4['forecasts']), '横盘行情：全部判为震荡，不硬给方向')

    # 周末顺延
    fri = '2026-09-25'                           # 周五（自检夹具，不是行情事实）
    check(next_trading_day(fri) == '2026-09-28', '目标日遇周末自动顺延到周一')
    check(assert_no_lookahead(base, base)[0] is False, '目标日 == 基准日时判定为未来函数污染')

    # 存档 / 结算：当次预测绝不能被当次行情结算
    hist = new_history()
    append_forecasts(hist, d)
    settled = settle_history(hist, bull, now=now)
    check(settled == 0, '当次行情不会结算当次预测（结构上杜绝 look-ahead）')
    check(review_history(hist)['settled'] == 0 and review_history(hist)['pending'] > 0,
          '未结算的预测只计入待结算，不计入命中率')

    # 到了目标日，用那天的真实行情结算
    realized = {'fetch_date': d['target_date'], 'quotes': {
        'HSI': {'name': NAMES['HSI'], 'last': 100.0, 'pct': 0.8,
                'as_of': d['target_date'], 'decimals': 2}}}
    settled2 = settle_history(hist, realized, now=now + timedelta(days=1))
    rev = review_history(hist)
    check(settled2 == 1 and rev['settled'] == 1, '目标日行情到位后，对应预测被结算并计分')
    check(rev['hits'] == 1, '方向一致 → 记为命中')

    # 方向相反必须判不命中（命中判定不能只会说「对」）
    hist_miss = new_history()
    append_forecasts(hist_miss, d)
    settle_history(hist_miss, {'quotes': {'HSI': dict(
        realized['quotes']['HSI'], pct=-1.5)}}, now=now + timedelta(days=1))
    rev_miss = review_history(hist_miss)
    check(rev_miss['settled'] == 1 and rev_miss['hits'] == 0, '方向相反 → 记为未命中')
    check(rev['enough_sample'] is False and '样本不足' in rev['text'],
          f'样本不足 {MIN_REVIEW_SAMPLE} 条时只报样本，不下命中率结论')

    old = new_history()
    old['records'] = [{'key': 'HSI', 'name': NAMES['HSI'], 'base_date': prev,
                       'target_date': (now - timedelta(days=30)).strftime('%Y-%m-%d'),
                       'direction': 1, 'settled': False}]
    settle_history(old, {'quotes': {}}, now=now)
    check(old['records'][0].get('expired') is True,
          f'目标日过去超过 {SETTLE_EXPIRE_DAYS} 天仍未结算 → 判作废，不硬凑命中')

    for dd in (empty, d, d2, d3, d4):
        hw, hx = render_web(dd), render_wechat(dd)
        check('{{' not in hw and '{{' not in hx, '渲染输出无残留占位符')
    print('\n' + ('✅ forecast 自检全部通过' if ok else '❌ forecast 自检存在失败项'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description='AI 预测 · 未来函数（04 栏推理引擎）')
    ap.add_argument('--json', help='把预测结果写入指定 JSON 文件')
    ap.add_argument('--history', action='store_true',
                    help='把当次预测写入存档，并用当次行情结算已到期的历史预测')
    ap.add_argument('--review', action='store_true', help='只输出历史命中率回看')
    ap.add_argument('--self-test', action='store_true', help='规则自检（含未来函数污染检测）')
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    if args.review:
        market = _load(os.environ.get('MARKET_DATA', os.path.join(REPO_ROOT, 'market_data.json')))
        rev = review_from_history(default_history_path(), market=market)
        print('🔮 AI 预测回看: ' + (rev.get('text') or ''))
        for v in (rev.get('by_symbol') or []):
            print(f"   · {v['name']: <10} {v['n']} 条 · 命中 {v['hit_rate'] * 100:.0f}% · "
                  f"MAE {v['mae_pct']:.2f}pp")
        return 0
    d = predict_from_files(history=args.history)
    print(text_report(d))
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        print(f'\n💾 已写入 {args.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
