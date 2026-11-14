#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐社区、逐条内容判定：是不是正文？有没有信号？

对每个社区的每一条入库内容（卡片引文 + 追加样本）各给两个独立判定：

① 内容判定 `content` —— 这段文字是什么
   - `body`     正文：有成句表述的作者原话 / 帖子回复 / 页面段落
   - `headline` 标题：新闻检索载体的条目、与标题重复、或没有成句的短标题
   - `shell`    页面外壳：导航、登录/下载提示、版权声明、站点简介、菜单词堆叠
   - `fragment` 过短片段：字数不足以表达一个完整意思

② 信号判定 `signal` —— 对行情判断有没有用
   - `signal` 有信号：有明确对象（指数/个股/板块/宏观变量）**且**有方向词，并有数字、
                     核心标的或事件佐证
   - `weak`   弱信号：谈到了市场对象，但缺方向，或只有方向没有佐证
   - `none`   无信号：外壳/过短片段，或没有市场对象

`usable = 正文 且 有信号`。标题可以带信号（新闻事实），但不算社区正文，所以不算 usable。

全部是确定性规则（复用 community_data 的多空词库、主题词与事件词），不联网、不调用模型；
每条判定都带 `reasons`，可逐条复核。它是对**已入库文本**的质量判读，不验证文本真伪，
也不代表该社区的整体立场。

    python3 community_judge.py --json community_data.json          # 逐条判定表
    python3 community_judge.py --json community_data.json --md out.md
    python3 community_judge.py --text "恒指放量上涨 2%，南向资金净流入 80 亿"
"""
import argparse
import json
import re
import sys

CONTENT_LABELS = {'body': '正文', 'headline': '标题', 'shell': '页面外壳', 'fragment': '过短片段'}
SIGNAL_LABELS = {'signal': '有信号', 'weak': '弱信号', 'none': '无信号'}
DIRECTION_LABELS = {'bull': '偏多', 'bear': '偏空', 'mixed': '多空分歧', 'neutral': '无方向'}

_CJK = re.compile(r'[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]')
_WORD = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’-]*")
_SENTENCE_END = re.compile(r'[。！？!?；;]|\.(\s|$)')
_CLAUSE = re.compile(r'[，,、：:]')
_SEPARATOR = re.compile(r'\s*[|｜·•›»>/]\s*|\s{2,}|\s[-–—]\s')
# 带单位的数字、涨跌幅、点位 —— 「有数字佐证」的依据
_FIGURE = re.compile(
    r'[+\-−]?\d[\d,]*(?:\.\d+)?\s*(?:%|％|个?点|點|bp|bps|基点|亿|億|万|萬|兆|美元|港元|港币|元|倍|'
    r'k|m|bn|billion|million|trillion|percent|points?)(?![a-z])', re.I)
_TICKER = re.compile(r'\$[A-Z]{1,5}\b|\b\d{4,6}\.(?:HK|SZ|SH|SS|TW)\b|\b[A-Z]{2,5}(?=\s*(?:股|涨|跌|calls?\b|puts?\b))')

# 页面外壳用语：出现就说明这段文字来自站点框架而不是讨论内容
SHELL_TERMS = (
    '登录', '登入', '注册', '註冊', '下载app', '下载客户端', '下載app', '扫码', '掃碼', '客户端下载',
    '版权所有', '版權所有', '备案', 'icp', '免责声明', '免責聲明', '隐私政策', '隱私政策', '用户协议',
    '服务条款', '关于我们', '關於我們', '联系我们', '聯絡我們', '意见反馈', '返回顶部', '网站地图',
    '加载中', '載入中', '查看更多', '点击查看', '點擊查看', '立即开户', '立即體驗', '热门推荐',
    'copyright', 'all rights reserved', 'privacy policy', 'terms of service', 'terms of use',
    'cookie', 'sign in', 'sign up', 'log in', 'subscribe now', 'download the app', 'get the app',
    'skip to content', 'skip to main', 'javascript', 'loading...', 'read more', 'view all',
    'ログイン', '会員登録', '利用規約', '로그인', '이용약관', 'anmelden', 'datenschutz', 'impressum',
)
# 站点自我介绍式口号（简介而非讨论）
SLOGAN_TERMS = ('为您提供', '為您提供', '一站式', '专业的', '領先的', '领先的', '官方网站', '官方網站',
                '投资者社区', '交流平台', '尽在', '盡在', 'the leading', 'the best place',
                'join the conversation', 'your source for', 'welcome to')


def _weight(text):
    """跨语种的「信息量」近似：一个 CJK 字算 1，一个拉丁词算 2。"""
    return len(_CJK.findall(text)) + 2 * len(_WORD.findall(text))


def _has_sentence(text):
    """是否成句：有句末标点，或有分句且够长，或英文达到一句话的词数。"""
    if _SENTENCE_END.search(text):
        return True
    cjk, words = len(_CJK.findall(text)), len(_WORD.findall(text))
    if _CLAUSE.search(text) and cjk + 2 * words >= 20:
        return True
    return words >= 9 and cjk < words


def _menu_like(text):
    """菜单词堆叠：被分隔符切成很多短块，且没有任何成句。"""
    parts = [p for p in _SEPARATOR.split(text) if p and p.strip()]
    if len(parts) < 5:
        return False
    short = sum(1 for p in parts if _weight(p) <= 6)
    return short / len(parts) >= 0.7 and not _SENTENCE_END.search(text)


def _is_carrier(strategy):
    strategy = str(strategy or '')
    return strategy.endswith('_carrier') or strategy.startswith('gnews')


def judge_content(text, title='', kind='page_excerpt', strategy=''):
    """① 内容判定，返回 (content, reasons)。"""
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    title = re.sub(r'\s+', ' ', str(title or '')).strip()
    lowered = text.lower()
    weight = _weight(text)
    sentence = _has_sentence(text)
    shell_hits = [t for t in SHELL_TERMS if t in lowered]
    slogan_hits = [t for t in SLOGAN_TERMS if t in lowered]

    if weight < (6 if kind in ('comment', 'thread_reply') else 10):
        return 'fragment', [f'信息量过少（约 {weight} 字）']
    if _menu_like(text):
        return 'shell', ['多个短词被分隔符隔开，像导航/菜单']
    if len(shell_hits) >= 2 or (shell_hits and not sentence):
        return 'shell', ['含页面框架用语「' + '、'.join(shell_hits[:3]) + '」']
    if slogan_hits and kind not in ('comment', 'thread_reply'):
        return 'shell', ['站点简介式用语「' + '、'.join(slogan_hits[:2]) + '」']
    if _is_carrier(strategy):
        return 'headline', ['来自新闻检索载体，是新闻条目而非社区原话']
    if title and (text == title or (text in title and len(text) >= len(title) * 0.6)):
        return 'headline', ['内容与标题重复，没有标题之外的正文']
    if not sentence:
        if kind in ('comment', 'thread_reply') and weight >= 6:
            return 'body', ['评论/回复，虽无句末标点但有完整意思']
        return 'headline', ['没有成句表述，像标题或条目名']
    if kind in ('comment', 'thread_reply'):
        return 'body', ['成句的评论/回复原话']
    if kind == 'demo':
        return 'body', ['成句文本（演示样本）']
    if weight < 24:
        return 'fragment', [f'页面摘录过短（约 {weight} 字）']
    return 'body', ['成句的页面段落']


def judge_signal(text, content, ctype=''):
    """② 信号判定，返回 dict(signal, direction, strength, reasons)。"""
    import community_data as cd
    import community_spider as spider
    analysis = cd.analyze_live_snippet(text, ctype=ctype)
    quant = analysis['quant']
    pos = quant['sentiment']['pos_hits']
    neg = quant['sentiment']['neg_hits']
    core = quant['relevance']['core_hits']
    topics = spider.count_topic_hits(text)
    event_terms = quant['event']['matched_terms']
    figures = _FIGURE.findall(text)
    tickers = _TICKER.findall(text)
    direction = analysis['verdict_class']
    has_direction = bool(pos or neg)
    has_subject = bool(core or topics or tickers)

    reasons = []
    if content in ('shell', 'fragment'):
        return {'signal': 'none', 'direction': 'neutral', 'strength': 0,
                'reasons': ['不是可用内容，不读信号']}
    if has_subject:
        reasons.append(f'市场对象：核心标的 {core} · 主题词 {topics}'
                       + (f' · 代码 {len(tickers)}' if tickers else ''))
    else:
        reasons.append('没有提到任何市场对象')
    if pos:
        reasons.append('正向词「' + '、'.join(pos[:3]) + '」')
    if neg:
        reasons.append('负向词「' + '、'.join(neg[:3]) + '」')
    if not has_direction:
        reasons.append('没有多空方向词')
    if figures:
        reasons.append(f'含 {len(figures)} 处带单位的数字')
    if event_terms:
        reasons.append(f'事件：{quant["event"]["label"]}')

    strength = 0
    if has_subject:
        strength = ((35 if has_direction else 0) + min(24, 8 * (len(pos) + len(neg)))
                    + (15 if figures else 0) + (14 if core else 0) + (12 if event_terms else 0))
        if not has_direction:
            strength = min(strength, 35)
    strength = max(0, min(100, strength))

    if has_subject and has_direction and strength >= 55:
        level = 'signal'
    elif has_subject and (has_direction or figures or event_terms):
        level = 'weak'
    else:
        level = 'none'
        strength = min(strength, 15)
    return {'signal': level, 'direction': direction if has_direction else 'neutral',
            'strength': strength, 'reasons': reasons}


def judge_item(text, title='', kind='page_excerpt', strategy='', ctype=''):
    """对一条内容给出完整判定。纯函数：同样输入永远同样输出。"""
    content, content_reasons = judge_content(text, title=title, kind=kind, strategy=strategy)
    sig = judge_signal(str(text or ''), content, ctype=ctype)
    return {
        'content': content,
        'content_label': CONTENT_LABELS[content],
        'is_body': content == 'body',
        'signal': sig['signal'],
        'signal_label': SIGNAL_LABELS[sig['signal']],
        'direction': sig['direction'],
        'direction_label': DIRECTION_LABELS.get(sig['direction'], '无方向'),
        'strength': sig['strength'],
        'usable': content == 'body' and sig['signal'] == 'signal',
        'reasons': content_reasons + sig['reasons'],
    }


def tag(judge):
    """渲染用短标签，如「正文 · 有信号 · 偏多 · 强度 72」；无判定返回 ''。"""
    if not isinstance(judge, dict) or judge.get('content') not in CONTENT_LABELS:
        return ''
    parts = [CONTENT_LABELS[judge['content']], SIGNAL_LABELS.get(judge.get('signal'), '无信号')]
    if judge.get('signal') in ('signal', 'weak'):
        if judge.get('direction') in ('bull', 'bear', 'mixed'):
            parts.append(DIRECTION_LABELS[judge['direction']])
        parts.append(f'强度 {int(judge.get("strength") or 0)}')
    return ' · '.join(parts)


def _record_items(record):
    """一个社区记录里要判定的条目：[(holder, text, title, kind, strategy)]。"""
    kind = record.get('content_kind') or 'page_excerpt'
    strategy = record.get('strategy') or ''
    comments = [c for c in (record.get('comments') or []) if isinstance(c, dict)]
    inputs = record.get('analysis_input') if isinstance(record.get('analysis_input'), dict) else {}
    main_text = str(inputs.get('snippet') or '').strip()
    if not main_text and comments:
        main_text = str(comments[0].get('text') or '')
    if not main_text:
        main_text = str(record.get('quote') or '')
    main_title = (comments[0].get('post_title') if comments else '') or inputs.get('title') or ''
    items = [(record, main_text, main_title, kind, strategy)]
    for item in comments[1:]:
        items.append((item, str(item.get('text') or ''), item.get('post_title') or '',
                      item.get('kind') or kind, item.get('strategy') or strategy))
    return items


def judge_record(record):
    """就地给一个社区记录及其追加样本写 `judge`；未获取的记录清掉旧判定。"""
    if not isinstance(record, dict):
        return []
    if not record.get('available'):
        record.pop('judge', None)
        return []
    judged = []
    for holder, text, title, kind, strategy in _record_items(record):
        holder['judge'] = judge_item(text, title=title, kind=kind, strategy=strategy,
                                     ctype=record.get('ctype', ''))
        judged.append(holder['judge'])
    comments = [c for c in (record.get('comments') or []) if isinstance(c, dict)]
    if comments:
        comments[0]['judge'] = dict(record['judge'])   # 首条即卡片引文，同一判定
    return judged


def judge_dataset(data):
    """给整份 community_data 逐源逐条写判定，并写 `judge_summary`。返回汇总。"""
    summary = {
        'sources_total': 0, 'sources_available': 0, 'items': 0,
        'content': {key: 0 for key in CONTENT_LABELS},
        'signal': {key: 0 for key in SIGNAL_LABELS},
        'usable_items': 0, 'usable_sources': 0, 'body_sources': 0, 'no_body_sources': [],
    }
    if not isinstance(data, dict) or not isinstance(data.get('communities'), list):
        return summary
    for record in data['communities']:
        if not isinstance(record, dict):
            continue
        summary['sources_total'] += 1
        judged = judge_record(record)
        if not judged:
            continue
        summary['sources_available'] += 1
        summary['items'] += len(judged)
        for item in judged:
            summary['content'][item['content']] += 1
            summary['signal'][item['signal']] += 1
            summary['usable_items'] += bool(item['usable'])
        if any(item['usable'] for item in judged):
            summary['usable_sources'] += 1
        if any(item['is_body'] for item in judged):
            summary['body_sources'] += 1
        else:
            summary['no_body_sources'].append(record.get('key') or record.get('name') or '')
    data['judge_summary'] = summary
    return summary


def rows(data):
    """逐条判定的平铺表（供 CLI / Markdown 导出）。"""
    out = []
    for record in (data or {}).get('communities') or []:
        if not isinstance(record, dict):
            continue
        if not record.get('available'):
            out.append({'id': record.get('id', ''), 'name': record.get('name', ''), 'n': 0,
                        'text': '', 'judge': None})
            continue
        for index, (holder, text, _title, _kind, _strategy) in enumerate(_record_items(record), 1):
            out.append({'id': record.get('id', ''), 'name': record.get('name', ''), 'n': index,
                        'text': text, 'judge': holder.get('judge')})
    return out


def render_text(data):
    summary = data.get('judge_summary') or judge_dataset(data)
    lines = []
    for row in rows(data):
        judge = row['judge']
        if judge is None:
            lines.append(f'{row["id"]:>2} {row["name"]} — 未获取，无内容可判')
            continue
        mark = '✅' if judge['usable'] else ('◐' if judge['signal'] != 'none' else '✗')
        lines.append(f'{row["id"]:>2} {row["name"]} #{row["n"]} {mark} {tag(judge)}')
        lines.append(f'     「{row["text"][:70]}{"…" if len(row["text"]) > 70 else ""}」')
        lines.append('     依据：' + '；'.join(judge['reasons']))
    c, s = summary['content'], summary['signal']
    lines.append('')
    lines.append(f'汇总：{summary["sources_available"]}/{summary["sources_total"]} 源有内容，共 '
                 f'{summary["items"]} 条 —— 正文 {c["body"]} / 标题 {c["headline"]} / 页面外壳 '
                 f'{c["shell"]} / 过短 {c["fragment"]}；有信号 {s["signal"]} / 弱信号 {s["weak"]} / '
                 f'无信号 {s["none"]}；正文且有信号 {summary["usable_items"]} 条，覆盖 '
                 f'{summary["usable_sources"]} 源')
    if summary['no_body_sources']:
        lines.append('没有任何正文的源：' + '、'.join(summary['no_body_sources']))
    return '\n'.join(lines)


def render_md(data):
    summary = data.get('judge_summary') or judge_dataset(data)

    def cell(value):
        return str(value).replace('|', '｜').replace('\n', ' ')

    lines = ['# 社区内容逐条判定', '',
             f'数据批次：{cell(data.get("generated_at") or "未知")} · 模式 {cell(data.get("mode") or "未知")}', '',
             '| 源 | 条 | 内容 | 信号 | 方向 | 强度 | 可用 | 原文（截断） | 依据 |',
             '|---|---|---|---|---|---|---|---|---|']
    for row in rows(data):
        judge = row['judge']
        name = cell(f'{row["id"]} {row["name"]}')
        if judge is None:
            lines.append(f'| {name} | — | 未获取 | — | — | — | — | — | — |')
            continue
        lines.append(
            f'| {name} | {row["n"]} | {judge["content_label"]} | {judge["signal_label"]} | '
            f'{judge["direction_label"]} | {judge["strength"]} | {"是" if judge["usable"] else "否"} | '
            f'{cell(row["text"][:60])} | {cell("；".join(judge["reasons"]))} |')
    c, s = summary['content'], summary['signal']
    lines += ['', '## 汇总', '',
              f'- 有内容的源：{summary["sources_available"]} / {summary["sources_total"]}，共 {summary["items"]} 条',
              f'- 内容：正文 {c["body"]} · 标题 {c["headline"]} · 页面外壳 {c["shell"]} · 过短片段 {c["fragment"]}',
              f'- 信号：有信号 {s["signal"]} · 弱信号 {s["weak"]} · 无信号 {s["none"]}',
              f'- 正文且有信号：{summary["usable_items"]} 条，覆盖 {summary["usable_sources"]} 源',
              f'- 没有任何正文的源：{"、".join(summary["no_body_sources"]) or "无"}', '']
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description='逐社区逐条内容判定：是否正文、是否有信号')
    ap.add_argument('--json', default='community_data.json', help='community_data.json 路径')
    ap.add_argument('--md', help='另存 Markdown 判定表')
    ap.add_argument('--write', action='store_true', help='把判定写回 JSON 文件')
    ap.add_argument('--text', help='只判定这一段文字')
    ap.add_argument('--kind', default='page_excerpt', choices=('comment', 'thread_reply', 'page_excerpt'))
    args = ap.parse_args(argv)
    if args.text:
        print(json.dumps(judge_item(args.text, kind=args.kind), ensure_ascii=False, indent=2))
        return 0
    try:
        with open(args.json, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f'错误: 读不到 {args.json}（{exc}）；先运行 python3 community_snapshot.py', file=sys.stderr)
        return 1
    judge_dataset(data)
    print(render_text(data))
    if args.md:
        with open(args.md, 'w', encoding='utf-8') as handle:
            handle.write(render_md(data))
    if args.write:
        with open(args.json, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
