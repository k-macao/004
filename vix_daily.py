#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日 VIX 恐慌指数：统一分析与网页 / 微信渲染。

本模块只解释当次 ``market_data.json`` 里的 ``quotes.VIX``，不联网、不缓存、
也不回填历史读数：

* VIX 是 SPX 期权市场对未来 30 天隐含波动率的风险中性定价，以年化百分数表示；
* ``VIX × √(30/365)`` 只给未来 30 天的一标准差幅度，不给涨跌方向；
* ``VIX²`` 更接近风险中性 Q 测度下的预期方差，不等于现实 P 测度下的已实现波动率；
* 展示分档只为本报告每日阅读设置，不参与 01 合成分 / 04 预测。
"""

import html
import math

# 展示层实践分档。上界采用左闭右开：12.00 属于“常态”，20.00 属于“升温”。
# 这些阈值只帮助阅读，不参与 panorama / forecast。
DISPLAY_BANDS = (
    (12.0, 'very-low', '极低波动定价', '期权保险价格处于低位；低读数不代表尾部风险消失。'),
    (20.0, 'normal', '常态波动定价', '市场仍在常态波动区间内，继续结合日变动与期限结构观察。'),
    (30.0, 'elevated', '风险定价升温', '期权市场正在提高未来波动与下行保险的定价。'),
    (40.0, 'high', '高压恐慌定价', '短端风险保险明显变贵，仓位与流动性风险需要优先检查。'),
    (math.inf, 'extreme', '极端压力定价', '市场处于极端波动定价区，单点读数的方向预测价值反而有限。'),
)

MINUS = '\u2212'


def _number(value):
    """有限实数 → float；布尔值、空值与 NaN/Inf → None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def classify(level):
    """返回展示分档 dict；无有效正数时返回 None。"""
    value = _number(level)
    if value is None or value <= 0:
        return None
    for upper, code, label, explanation in DISPLAY_BANDS:
        if value < upper:
            return {
                'code': code,
                'label': label,
                'explanation': explanation,
                'upper': None if math.isinf(upper) else upper,
            }
    return None  # pragma: no cover — DISPLAY_BANDS 的最后一档为 inf


def _signed(value, suffix='', digits=2):
    value = _number(value)
    if value is None:
        return '—'
    sign = MINUS if value < 0 else '+'
    return f'{sign}{abs(value):,.{digits}f}{suffix}'


def _trend(change):
    value = _number(change)
    if value is None:
        return {'code': 'unknown', 'label': '日变动未获取', 'sentence': '较前收变化未获取'}
    if value > 0:
        return {'code': 'up', 'label': '恐慌定价升温', 'sentence': '较前收上升'}
    if value < 0:
        return {'code': 'down', 'label': '恐慌定价降温', 'sentence': '较前收回落'}
    return {'code': 'flat', 'label': '恐慌定价持平', 'sentence': '较前收持平'}


def analyze(market):
    """把 ``market_data`` 转成每日 VIX 展示模型。

    缺失时明确返回 ``available=False``；函数从不使用默认点位，也不读取其它文件。
    ``expected_30d_pct`` 是把年化百分数按 ``sqrt(30/365)`` 粗略缩放得到的
    30 日一标准差幅度，仅用于解释单位，不是价格方向预测。
    """
    market = market if isinstance(market, dict) else {}
    quotes = market.get('quotes') if isinstance(market.get('quotes'), dict) else {}
    quote = quotes.get('VIX') if isinstance(quotes.get('VIX'), dict) else {}
    level = _number(quote.get('last'))
    if level is None or level <= 0:
        return {
            'available': False,
            'reason': 'missing_quote',
            'as_of': quote.get('as_of') or '',
        }

    change = _number(quote.get('chg'))
    pct = _number(quote.get('pct'))
    band = classify(level)
    trend = _trend(change)
    expected_30d = level * math.sqrt(30.0 / 365.0)
    # 仪表上限只为视觉定位；>=50 全部贴到右端，不截断真实文本读数。
    gauge_pct = max(0.0, min(100.0, level / 50.0 * 100.0))
    return {
        'available': True,
        'level': level,
        'level_text': f'{level:,.2f}',
        'change': change,
        'change_text': _signed(change),
        'pct': pct,
        'pct_text': _signed(pct, '%'),
        'as_of': quote.get('as_of') or '',
        'source': quote.get('source') or '',
        'band': band,
        'trend': trend,
        'expected_30d_pct': expected_30d,
        'expected_30d_text': f'±{expected_30d:.2f}%',
        'gauge_pct': gauge_pct,
    }


def _web_explanation(data):
    """网页端一行口径；无行情时也保留定义，不伪造当日解读。"""
    if data.get('available'):
        conversion = (f'当前 {data["level_text"]} → 年化 {data["level_text"]}%，'
                      f'按 √时间折算未来 30 天幅度约 '
                      f'<strong>{data["expected_30d_text"]}</strong>（幅度，不是涨跌方向）。')
    else:
        conversion = '当次行情未获取，不展示点位、日变动与幅度换算。'
    return (f'<div class="vix-note">口径：{conversion}'
            'VIX 由一篮子 SPX 虚值期权合成，是未来 30 天隐含波动率的年化定价；'
            'VIX² 更接近风险中性 Q 测度的预期方差，不等于现实 P 测度的已实现波动或崩盘概率。</div>')


def render_web(data):
    """渲染网页版 00 栏（无状态/口径/未获取说明，无可用数据时不渲染）。"""
    data = data if isinstance(data, dict) else {'available': False}
    if not data.get('available'):
        return ''

    band = data['band'] or {'code': 'unknown', 'label': '', 'explanation': ''}
    trend = data['trend'] or {'code': 'unknown', 'label': '', 'sentence': ''}
    has_trend = trend.get('code') not in (None, 'unknown')
    as_of_raw = (data.get('as_of') or '').strip()
    change_span = (
        f'\n    <span class="vix-change vix-{html.escape(trend.get("code") or "flat", quote=True)}">'
        f'{data["change_text"]} / {data["pct_text"]}</span>'
        if has_trend else ''
    )
    marker = f'{data["gauge_pct"]:.2f}%'
    head_bits = [html.escape(band['label'])] if band.get('label') else []
    if has_trend and trend.get('label'):
        head_bits.append(html.escape(trend['label']))
    head_txt = (' · '.join(head_bits) + '。') if head_bits else ''
    summary = f'{head_txt}{html.escape(band.get("explanation") or "")}'
    meta_bits = []
    if as_of_raw:
        meta_bits.append(f'行情日期 {html.escape(as_of_raw)}')
    if data.get('expected_30d_text'):
        meta_bits.append(f'30 天隐含波动 {html.escape(data["expected_30d_text"])}')
    if has_trend and trend.get('sentence'):
        meta_bits.append(html.escape(trend['sentence']))
    meta_span = f'<span>{" · ".join(meta_bits)}</span>' if meta_bits else ''
    return f'''<section class="vix-panel" data-vix-state="{html.escape(band.get("code") or "unknown", quote=True)}" aria-label="每日 VIX 恐慌指数">
  <div class="vix-live-row">
    <span class="vix-symbol">CBOE VIX</span>
    <strong class="vix-value">{data["level_text"]}</strong>{change_span}
    <span class="vix-chip">{html.escape(band["label"])}</span>
  </div>
  <div class="vix-gauge" role="img" aria-label="VIX 分档仪表，当前 {data['level_text']}">
    <div class="vix-gauge-track"><i style="left:{marker}"></i></div>
    <div class="vix-gauge-labels"><span>&lt;12 极低</span><span>12–20 常态</span><span>20–30 升温</span><span>30–40 高压</span><span>≥40 极端</span></div>
  </div>
  <div class="vix-summary"><strong>今日判读：</strong>{summary}{meta_span}</div>
</section>'''


def render_wechat(data, cyan='#4fe5ff', danger='#ff6b7d', neon='#b6ff4a'):
    """渲染微信内联样式版（无状态/口径/未获取说明，无可用数据时不渲染）。"""
    data = data if isinstance(data, dict) else {'available': False}
    if not data.get('available'):
        return ''

    shell = ('background:#10172a;color:#dce5fb;border:1px solid #2b3855;'
             f'border-left:3px solid {cyan};border-radius:5px;padding:12px 14px;'
             'margin:10px 0;font-size:12px;line-height:1.82;')
    meta = 'color:#9aa6c3;font-size:10px;line-height:1.7;'
    trend = data['trend'] or {'code': 'unknown', 'label': ''}
    has_trend = trend.get('code') not in (None, 'unknown')
    band = data['band'] or {'label': '', 'explanation': ''}
    trend_color = danger if trend.get('code') == 'up' else (neon if trend.get('code') == 'down' else cyan)
    change_html = (
        f'　<span style="color:{trend_color};font-weight:700;">{data["change_text"]} / {data["pct_text"]}</span>'
        if has_trend else ''
    )
    badge_txt = html.escape(band['label'])
    if has_trend and trend.get('label'):
        badge_txt = f'{badge_txt} · {html.escape(trend["label"])}' if badge_txt else html.escape(trend['label'])
    meta_bits = []
    as_of_raw = (data.get('as_of') or '').strip()
    if as_of_raw:
        meta_bits.append(f'行情日期 {html.escape(as_of_raw)}')
    if data.get('expected_30d_text'):
        meta_bits.append(f'30 天隐含波动 {html.escape(data["expected_30d_text"])}')
    meta_html = f'<span style="{meta}">{" · ".join(meta_bits)}</span>' if meta_bits else ''
    return (f'<div style="{shell}">'
            f'<div style="color:{cyan};font-weight:700;font-size:13px;">◆ CBOE VIX　'
            f'<strong style="color:#edf2ff;font-size:22px;">{data["level_text"]}</strong>{change_html}</div>'
            f'<div style="margin:5px 0 7px;"><strong style="background:#07101b;color:{trend_color};'
            'padding:1px 6px;border:1px solid #435d55;">'
            f'{badge_txt}</strong>　'
            f'{html.escape(band.get("explanation") or "")}</div>'
            f'{meta_html}</div>')


def render_plain(data):
    """墨水屏 / 日志用两行纯文本。"""
    data = data if isinstance(data, dict) else {'available': False}
    if not data.get('available'):
        return ['VIX 今日未获取（不回填历史读数）']
    band = data.get('band') or {}
    trend = data.get('trend') or {}
    return [
        f'VIX {data["level_text"]} {data["change_text"]}/{data["pct_text"]} · {band.get("label", "未分档")}',
        f'30日波动幅度约{data["expected_30d_text"]} · {trend.get("label", "")} · {data.get("as_of") or "日期未获取"}',
    ]
