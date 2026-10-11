#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字符配图：把 matplotlib 的 Axes 译成等宽字符，挂在微信推送里表达当次数据分析。

对照 Matplotlib 3.11（https://github.com/matplotlib/matplotlib ，plot types
https://matplotlib.org/stable/plot_types/index.html ）和 Quick start 里的
Figure 解剖：每张图有标题、轴刻度、柱端数值（bar_label）。不靠颜色区分序列
——微信里颜色会丢，字符本身要能读。

  Axes.barh / ordered bar     类别比较，按数值从大到小
  diverging bar               有正负的比较，零线居中（画廊常见配图）
  bar stacked                 构成：一条柱按占比堆叠，配图例
  axvline 阈值                决策线写在图注里（配对 |z|=1，做多 ±10 / +25）

柱身用方块字形系列 █ ▓ ▒ ░（实心→深→中→浅，按灰度区分序列），情绪序列用 ▁ ▃ ▄ █ 四档高度。
缺数据只留「不编柱」，不补 0、不回填历史点位。纯标准库。
"""
import html
import unicodedata

import quant_pair

BAR_W = 16
HALF = 8
LABEL_W = 12  # 显示列，汉字算 2

QUOTE_ORDER = (
    ('HSI', '恒指'), ('HSTECH', '恒科'), ('HSCE', '国企'),
    ('SPX', '标普'), ('NDQ', '纳指'), ('DJI', '道指'),
    ('GOLD', '黄金'), ('WTI', 'WTI'), ('BRENT', '布油'),
    ('USDCNH', '离岸'), ('USDCNY', '在岸'),
)

# 字形系列：实心 → 深 → 中 → 浅，序列靠灰度区分（微信丢颜色也能读）
FULL, DARK, MID, LIGHT = '\u2588', '\u2593', '\u2592', '\u2591'   # █ ▓ ▒ ░
SPARK = ('\u2581', '\u2583', '\u2584', '\u2588')                  # ▁ ▃ ▄ █
POS, NEG = FULL, DARK

DIR_FILL = {1: FULL, -1: DARK, 0: LIGHT}
COMMUNITY_PARTS = (
    ('bull', '偏多', FULL),
    ('bear', '偏空', DARK),
    ('neutral', '中性', MID),
    ('mixed', '分歧', LIGHT),
)

DIR_WORD = {1: '利好', -1: '利空', 0: '中性'}


def _width(text):
    w = 0
    for ch in str(text):
        w += 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
    return w


def _clip(text, width):
    out = []
    w = 0
    for ch in str(text or ''):
        cw = 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
        if w + cw > width:
            break
        out.append(ch)
        w += cw
    return ''.join(out), w


def _pad(text, width):
    s, w = _clip(text, width)
    return s + ' ' * (width - w)


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _fmt(v, digits=2):
    return f'{v:+.{digits}f}'


def pos_bar(value, scale, width=BAR_W, fill=FULL):
    """0 到 scale 的横向柱。刻度固定，不按当日最大值拉伸。"""
    if scale <= 0:
        return ' ' * width
    frac = max(0.0, min(float(scale), float(value))) / float(scale)
    n = int(round(frac * width))
    return fill * n + ' ' * (width - n)


def diverging_bar(value, limit, half=HALF):
    """零线居中。正值在 | 右侧用 █，负值在左侧用 ▓。"""
    limit = abs(float(limit)) or 1.0
    v = max(-limit, min(limit, float(value)))
    n = int(round(abs(v) / limit * half))
    if v > 0:
        left, right = ' ' * half, POS * n + ' ' * (half - n)
    elif v < 0:
        left, right = ' ' * (half - n) + NEG * n, ' ' * half
    else:
        left, right = ' ' * half, ' ' * half
    return left + '|' + right


def stacked_bar(parts, width=24):
    """parts: [(glyph, count), ...]。最大余数法，保证格子数等于 width。"""
    total = sum(max(0, int(c)) for _, c in parts)
    if total <= 0 or width <= 0:
        return '', 0
    raw = [max(0, int(c)) / total * width for _, c in parts]
    cells = [int(x) for x in raw]
    remain = width - sum(cells)
    order = sorted(range(len(parts)), key=lambda i: (raw[i] - cells[i], -i), reverse=True)
    for i in order:
        if remain <= 0:
            break
        if parts[i][1] <= 0:
            continue
        cells[i] += 1
        remain -= 1
    # 余数若还在（全是 0 已在上面返回），按顺序补到有计数的格子上
    i = 0
    guard = 0
    while remain > 0 and guard < width * 2:
        if parts[i % len(parts)][1] > 0:
            cells[i % len(parts)] += 1
            remain -= 1
        i += 1
        guard += 1
    return ''.join(g * n for (g, _), n in zip(parts, cells)), total


# 微信单页有 10 万字符硬上限，而字符配图是逐行渲染的：每行三个 <td> 各带一串内联样式，
# 行数一多，重复的样式字符串比图本身还贵（实测 6 张图里约七成字符是重复的 style）。
# 所以共有属性（字号 / 颜色 / 字体）全部提到 <table> 上让单元格继承，
# 每个 <td> 只留必须逐格不同的那几条 —— 纯样式瘦身，图长什么样一格没变。
_FIG_TABLE = ('width:100%;border-collapse:collapse;margin:8px 0 0;background:#0e1528;'
              'border:1px solid #2b3855;border-left:3px solid #4fe5ff;font-size:11px;color:#dce5fb')
_FIG_CAP = 'text-align:left;font-weight:700;font-size:12px;color:#4fe5ff;padding:8px 10px 2px'
_FIG_LABEL = 'padding:1px 8px;white-space:nowrap'
_FIG_BAR = "padding:1px 0;font:12px Consolas,Menlo,'Courier New',monospace;white-space:pre;color:#c1cce4"
_FIG_VALUE = 'padding:1px 8px;text-align:right;white-space:nowrap'
_FIG_EMPTY = 'padding:4px 10px 8px'
_FIG_NOTE = 'padding:2px 10px 8px;font-size:10px;color:#9aa6c3'


def _rows_html(title, rows, note):
    """微信用表格装字符柱：标签、柱、数值分列，不靠中文等宽。"""
    body = [
        f'<table class="char-fig" style="{_FIG_TABLE}">',
        f'<caption style="{_FIG_CAP}">◆ 字符配图 · ' + html.escape(title) + '</caption>',
    ]
    if not rows:
        body.append(
            f'<tr><td style="{_FIG_EMPTY}">'
            '（当次没有可画的数，本图不编柱，不回填历史点位）</td></tr>')
    else:
        for label, bar, value in rows:
            # 柱尾的空格只是把单元格撑宽，表格已经按列对齐，去掉不影响观感
            body.append(
                f'<tr><td style="{_FIG_LABEL}">{html.escape(str(label))}</td>'
                f'<td style="{_FIG_BAR}">{html.escape(str(bar).rstrip())}</td>'
                f'<td style="{_FIG_VALUE}">{html.escape(str(value))}</td></tr>')
    if note:
        body.append(f'<tr><td colspan="3" style="{_FIG_NOTE}">' + html.escape(note) + '</td></tr>')
    body.append('</table>')
    return ''.join(body)


def _plain(title, rows, note):
    lines = ['◆ ' + title]
    if not rows:
        lines.append('（当次没有可画的数，本图不编柱，不回填历史点位）')
    for label, bar, value in rows:
        lines.append(f'{_pad(label, LABEL_W)} {bar}  {value}')
    if note:
        lines.append(note)
    return '\n'.join(lines)


def _quote_pcts(quotes):
    raw = quotes or {}
    if isinstance(raw, dict) and isinstance(raw.get('quotes'), dict):
        raw = raw['quotes']
    out = []
    if not isinstance(raw, dict):
        return out
    for key, short in QUOTE_ORDER:
        q = raw.get(key) or {}
        pct = _num(q.get('pct') if isinstance(q, dict) else None)
        if pct is None:
            continue
        out.append((short, pct))
    return out


def quotes_chart(quotes):
    """涨跌幅发散柱。只画有当次 pct 的标的。"""
    pts = _quote_pcts(quotes)
    title = '涨跌幅 diverging barh（零线居中）'
    if not pts:
        return _rows_html(title, [], '没有涨跌幅就不画柱。'), _plain(title, [], '')
    limit = max(1.0, max(abs(v) for _, v in pts))
    rows = [(name, diverging_bar(v, limit), f'{_fmt(v)}%') for name, v in pts]
    note = (f'满刻度 ±{limit:.2f}%。+ 在零线右侧，- 在左侧；缺涨跌幅的标的不补 0。')
    return _rows_html(title, rows, note), _plain(title, rows, note)


def forces_chart(scan):
    """力量分有序柱 + 做多合成分发散柱。刻度固定 0–100 / ±100。"""
    forces = (scan or {}).get('forces') or []
    title = '推动力量 ordered barh + 做多合成分'
    rows = []
    ranked = sorted(forces, key=lambda f: -(_num(f.get('score')) or 0))
    for f in ranked:
        score = _num(f.get('score'))
        if score is None:
            continue
        direction = f.get('direction') or 0
        fill = DIR_FILL.get(direction, LIGHT)
        name = str(f.get('name') or '').split('（')[0].split('(')[0]
        rows.append((name, pos_bar(score, 100, fill=fill),
                     f'{score:.0f} {DIR_WORD.get(direction, "中性")}'))
    verdict = (scan or {}).get('verdict') or {}
    long_score = _num(verdict.get('long_score'))
    if long_score is not None and (rows or verdict):
        rows.append(('做多合成分', diverging_bar(long_score, 100, half=10),
                     f'{_fmt(long_score, 1)} {verdict.get("stance") or ""}'.strip()))
    note = ('力量分柱按 0–100 固定刻度（#利好 =利空 .中性）；做多合成分零线居中，'
            '阈值 −10 防御 / +10 轻仓试多 / +25 可做多。')
    if not rows:
        return _rows_html(title, [], note), _plain(title, [], note)
    return _rows_html(title, rows, note), _plain(title, rows, note)


def community_chart(counts):
    """社区研判构成：一条堆叠柱。家数来自当次汇总，不另算。"""
    counts = counts or {}
    parts = [(glyph, int(counts.get(key) or 0)) for key, _name, glyph in COMMUNITY_PARTS]
    title = '社区研判 stacked bar（构成）'
    total = sum(c for _, c in parts)
    if total <= 0:
        return _rows_html(title, [], '没有社区研判计数，不编构成。'), _plain(title, [], '')
    bar, _ = stacked_bar(parts, width=24)
    legend = '  '.join(f'{glyph}{name}{counts.get(key) or 0}'
                       for key, name, glyph in COMMUNITY_PARTS)
    rows = [('构成', bar, f'共 {total} 家'), ('图例', legend, '')]
    note = '一条柱即 100%（matplotlib bar stacked）。格子按家数占比分配，柱端是家数不是涨跌幅。'
    return _rows_html(title, rows, note), _plain(title, rows, note)


def long_short_chart(verdict):
    """开头用一条固定刻度的字符轴交代多空合成分。

    这张图故意保持极简：零线居中、空头在左、多头在右，数值仍来自
    panorama 的当次 verdict。合成分缺失时不把缺失误画成中性 0。
    """
    title = '多空坐标 · 做多合成分（固定 ±100）'
    verdict = verdict or {}
    score = _num(verdict.get('long_score'))
    # panorama 会保留中间计算分，但 can_long=unknown 时结论并不成立；
    # 此时必须留空，不能把内部的 0.0 误读成「中性」。
    if score is None or verdict.get('can_long') == 'unknown':
        note = '当次没有可用做多合成分，不标方向、不把缺失数据画成中性 0。'
        return _rows_html(title, [], note), _plain(title, [], note)

    axis = diverging_bar(score, 100, half=12)
    stance = str(verdict.get('stance') or '当次结论未标注')
    rows = [
        ('空头 / 防守  ←', axis, f'{score:+.1f}'),
        ('阈值', '−25    −10      0      +10    +25', stance),
    ]
    note = ('▓ 左侧 = 空 / 防守，| = 中性轴，█ 右侧 = 多 / 进攻。'
            '固定刻度 ±100，不按当次最大值拉伸；−10 / +10 / +25 为决策阈值。')
    return _rows_html(title, rows, note), _plain(title, rows, note)


def pairs_chart(quotes):
    """每条配对策略的 z。只画两腿涨跌幅都在的组合。"""
    title = '配对 z diverging bar（|z|=1 为进场）'
    raw = quotes or {}
    if isinstance(raw, dict) and isinstance(raw.get('quotes'), dict):
        raw = raw['quotes']
    rows = []
    zs = []
    pending = []
    for s in quant_pair.STRATEGIES:
        rec = quant_pair.recommend('', raw, hint=s['id'])
        if rec.get('z') is None:
            continue
        pending.append(rec)
        zs.append(abs(rec['z']))
    if not pending:
        note = '两腿涨跌幅不齐时不计算 z，本图不编柱。'
        return _rows_html(title, [], note), _plain(title, [], note)
    limit = max(1.8, max(zs))
    for rec in sorted(pending, key=lambda r: -abs(r['z'])):
        rows.append((rec['strategy_name'].replace('配对', ''),
                     diverging_bar(rec['z'], limit),
                     f'z={_fmt(rec["z"])} {rec.get("stance") or ""}'.strip()))
    note = (f'满刻度 ±{limit:.2f}。|z|<1 观望，|z|≥1 进场，|z|≥1.8 标准仓。'
            'z 与正文里的 AI 量化同一公式，不另写口径。')
    return _rows_html(title, rows, note), _plain(title, rows, note)


def sentiment_chart(sd):
    """舆情温度柱、多空构成、标的净情感发散柱。不读取来源字段。"""
    title = '舆情因子 bar / stacked / diverging'
    sd = sd or {}
    m = sd.get('market') or {}
    rows = []
    temp = _num(m.get('sent_temp'))
    if temp is not None:
        rows.append(('舆情温度', pos_bar(temp, 100), f'{temp:.1f} / 100'))
    pos, neg = _num(m.get('pos_share')), _num(m.get('neg_share'))
    if pos is not None and neg is not None:
        rest = max(0.0, 100.0 - pos - neg)
        # 用百分数取整后再分配，避免把未标注的残差画成另一套口径
        parts = [(FULL, int(round(pos))), (DARK, int(round(neg))), (LIGHT, int(round(rest)))]
        bar, total = stacked_bar(parts, width=20)
        if total:
            rows.append(('多空构成', bar, f'正{pos:.0f} 负{neg:.0f}'))
    targets = ((sd.get('matches') or {}).get('targets') or [])[:6]
    signed = []
    for t in targets:
        v = _num(t.get('net_senti'))
        if v is None:
            continue
        signed.append((str(t.get('name') or '标的'), v))
    if signed:
        limit = max(0.5, max(abs(v) for _, v in signed))
        for name, v in signed:
            rows.append((name, diverging_bar(v, limit), _fmt(v)))
    series = [row for row in (sd.get('series') or []) if _num(row.get('value')) is not None]
    if len(series) >= 2:
        vals = [_num(row.get('value')) for row in series[-12:]]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        spark = ''.join(SPARK[min(3, int((v - lo) / span * 4))] for v in vals)
        last = series[-1]
        rows.append(('情绪序列', spark, str(last.get('date') or '')[:10]))
    note = '温度柱刻度 0–100，50 为中性；净情感零线居中，序列只画数值、不写来源。'
    return _rows_html(title, rows, note), _plain(title, rows, note)


def forecast_chart(data):
    """AI 预测：预期涨跌幅发散柱 + 置信度有序柱。刻度按 σ 固定，不按当日最大值拉伸。

    只画当次真的算出了预期的标的；预测缺席（行情未取到）时不编柱，
    也绝不把上一版预测搬过来充数。
    """
    forecasts = (data or {}).get('forecasts') or []
    title = 'AI 预测 diverging bar（预期涨跌幅）+ 置信度 barh'
    note_base = ('预期涨跌幅零线居中，满刻度 ±1.5σ（模型对单日预测的硬上限）；'
                 '置信度柱按 0–100% 固定刻度。本图与正文同一次预测，缺数据不补 0。')
    if not forecasts:
        return _rows_html(title, [], note_base), _plain(title, [], note_base)

    short = dict(QUOTE_ORDER)
    rows = []
    for f in forecasts:
        mu = _num(f.get('mu_pct'))
        sigma = _num(f.get('sigma')) or 1.0
        if mu is None:
            continue
        name = short.get(f.get('key')) or str(f.get('name') or f.get('key') or '')
        rows.append((name, diverging_bar(mu, 1.5 * sigma),
                     f"{_fmt(mu)}% {f.get('dir_word') or ''}".strip()))
    stance = (data or {}).get('stance') or {}
    conf = _num(stance.get('confidence'))
    if conf is not None:
        rows.append(('平均置信度', pos_bar(conf * 100, 100), f'{conf * 100:.0f}%'))
    z = _num(stance.get('z'))
    if z is not None:
        rows.append(('明日倾向', diverging_bar(z, 1.0),
                     f"{_fmt(z)}σ {str(stance.get('label') or '').split('（')[0]}".strip()))
    note = note_base + f" 目标日 {(data or {}).get('target_date') or '未获取'}。"
    return _rows_html(title, rows, note), _plain(title, rows, note)


def forecast_review_chart(review):
    """历史预测回看：方向命中率 / 区间覆盖率有序柱（0–100% 固定刻度）。

    只画**已结算**的样本；一条都没结算时不编柱 —— 不用当次行情给当次预测打分。
    """
    review = review or {}
    title = '预测回看 ordered barh（命中率 · 已结算样本）'
    settled = _num(review.get('settled')) or 0
    note = ('只统计「目标日已抓到实际行情」的预测；未结算与作废的不计分。'
            '刻度固定 0–100%，不按样本最大值拉伸。')
    if not settled:
        return _rows_html(title, [], note), _plain(title, [], note)
    rows = []
    hr = _num(review.get('hit_rate'))
    if hr is not None:
        rows.append(('方向命中率', pos_bar(hr * 100, 100),
                     f"{hr * 100:.0f}% ({int(review.get('hits') or 0)}/{int(settled)})"))
    br = _num(review.get('band_rate'))
    if br is not None:
        rows.append(('落在区间内', pos_bar(br * 100, 100, fill=DARK),
                     f"{br * 100:.0f}% ({int(review.get('in_band') or 0)}/{int(settled)})"))
    for v in (review.get('by_symbol') or [])[:6]:
        rate = _num(v.get('hit_rate'))
        if rate is None:
            continue
        rows.append((str(v.get('name') or v.get('key') or ''), pos_bar(rate * 100, 100, fill=MID),
                     f"{rate * 100:.0f}% (n={int(v.get('n') or 0)})"))
    tail = note + (f" 样本 {int(settled)} 条"
                   + ('' if review.get('enough_sample') else '（样本不足，只作参考）') + '。')
    return _rows_html(title, rows, tail), _plain(title, rows, tail)


def macro_counts_chart(macro):
    """各小节入库条数。文件不可用时不画，避免把「清空的旧快照」画成实测 0。"""
    title = '宏观快讯条数 barh'
    macro = macro or {}
    if not (macro.get('categories') or {}):
        return '', ''
    avail = macro.get('unavailable')
    if isinstance(avail, dict) and not avail.get('ok', True):
        return '', ''
    rows = []
    for key, blk in (macro.get('categories') or {}).items():
        label = str((blk or {}).get('label') or key).split('—')[0].split('（')[0].strip()
        n = len((blk or {}).get('items') or [])
        rows.append((label, n))
    if not rows:
        return '', ''
    scale = max(1, max(n for _, n in rows))
    drawn = [(name, pos_bar(n, scale), f'{n} 条') for name, n in rows]
    note = f'柱长按本图最多 {scale} 条缩放。0 条是窗口内没有，不是历史条数。'
    return _rows_html(title, drawn, note), _plain(title, drawn, note)


def _self_test():
    checks = []

    def ok(name, cond):
        checks.append((name, bool(cond)))
        print(('  ✅ ' if cond else '  ❌ ') + name)

    bar = diverging_bar(1.2, 1.6)
    mid = bar.index('|')
    ok('正值的 █ 在零线右侧', POS in bar[mid + 1:] and NEG not in bar[mid + 1:])
    neg = diverging_bar(-0.4, 1.6)
    mid = neg.index('|')
    ok('负值的 ▓ 在零线左侧', NEG in neg[:mid] and POS not in neg[:mid])
    zero = diverging_bar(0, 1.6)
    ok('零值不画柱', set(zero) <= set('| '))
    ok('正柱长度随数值增加', pos_bar(80, 100).count(FULL) > pos_bar(20, 100).count(FULL))
    bar, total = stacked_bar([(FULL, 6), (DARK, 3), (MID, 3), (LIGHT, 2)], 24)
    ok('堆叠柱格子数等于宽度', len(bar) == 24 and total == 14)
    ok('堆叠柱保留四个序列', {FULL, DARK, MID, LIGHT} <= set(bar) and ' ' not in bar)
    html_q, plain_q = quotes_chart({})
    ok('无行情不编柱', '不编柱' in html_q and '%' not in plain_q.split('不编柱')[-1])
    html_q, plain_q = quotes_chart({'HSI': {'pct': 1.2}, 'WTI': {'pct': -0.4}})
    ok('有行情才写出涨跌幅', '+1.20%' in plain_q and '-0.40%' in plain_q)
    ok('字符柱进了微信表格', '<table' in html_q and 'diverging barh' in html_q)
    html_c, plain_c = community_chart({'bull': 6, 'bear': 3, 'neutral': 3, 'mixed': 2})
    ok('社区构成写出家数', '共 14 家' in plain_c and '偏多6' in plain_c.replace(' ', ''))
    html_ls, plain_ls = long_short_chart({'long_score': -18.5, 'stance': '偏防御'})
    ok('开头多空轴保留零线与方向', '|' in plain_ls and '空头' in plain_ls and '多' in plain_ls and '-18.5' in plain_ls)
    html_ls, plain_ls = long_short_chart({})
    ok('开头缺合成分不编中性柱', '不把缺失数据画成中性 0' in plain_ls and FULL not in plain_ls)
    html_p, plain_p = pairs_chart({})
    ok('配对缺腿不编 z', '不编柱' in html_p and 'z=' not in plain_p)
    html_s, plain_s = sentiment_chart({})
    ok('舆情空图不写来源名', '不编柱' in html_s and '米筐' not in html_s and 'RQ_' not in html_s)
    html_f, plain_f = forecast_chart({})
    ok('无预测不编柱', '不编柱' in html_f and FULL not in plain_f and '|' not in plain_f)
    html_f, plain_f = forecast_chart({
        'target_date': '', 'stance': {'z': 0.4, 'confidence': 0.6, 'label': '弱偏多'},
        'forecasts': [{'key': 'HSI', 'name': '恒生指数', 'mu_pct': 0.5, 'sigma': 1.2,
                       'dir_word': '看涨'}]})
    ok('预测柱写出预期涨跌幅与倾向', '+0.50%' in plain_f and '明日倾向' in plain_f)
    html_r, plain_r = forecast_review_chart({'settled': 0})
    ok('没有已结算样本就不画命中率', '不编柱' in html_r and FULL not in plain_r)
    html_r, plain_r = forecast_review_chart({'settled': 4, 'hits': 3, 'hit_rate': 0.75,
                                             'in_band': 2, 'band_rate': 0.5,
                                             'enough_sample': False, 'by_symbol': []})
    ok('已结算样本才画命中率', '75%' in plain_r and '样本不足' in plain_r)
    failed = [n for n, c in checks if not c]
    if failed:
        raise SystemExit('自检失败: ' + '、'.join(failed))
    print(f'\n✅ char_charts 自检通过（{len(checks)} 项）')


if __name__ == '__main__':
    import sys
    if '--self-test' in sys.argv:
        _self_test()
    else:
        demo, _ = quotes_chart({'HSI': {'pct': 1.2}, 'HSTECH': {'pct': -0.8}, 'WTI': {'pct': 2.1}})
        print(demo)
