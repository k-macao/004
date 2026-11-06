#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「全内容隐藏链接」回归防线 —— 零联网。

锁死四件事：
  1. **网页社区卡片不渲染链接**：`build_site.community_evidence_html` /
     `build_community_html` 的产物里没有 `<a href>`，也不出现「查看原帖/原页」「原帖」字样；
  2. **微信社区卡片同口径**：`wechat_push.community_evidence_wechat` / `community_card`
     与网页一致，不渲染链接、不出现上述字样（两端同源，不允许只改一端）；
  3. **网页 05 节 RSS 条目只留纯文本标题**：`rss_feeds.render_web` 不再把条目标题
     包进 `<a href>`，条目 URL 不得以任何形式（原文或转义）进入内容层；
  4. **隐藏链接不牺牲可核查性与可用性**：出处 URL 仍完整留在数据层
     （`source_url` / `comments[].url` / `items_list[].url`），且 `report.html` 模板的
     页内导航锚点与字体 `<link>` 完好保留 —— 隐藏的是内容外链，不是导航。

不安全的 URL（`javascript:` 等）此前靠「转义后进 href」兜底；现在内容层根本不产生
href，因此这条防线同时断言：不安全 URL 连转义形态都不会出现在产物里。
"""
import contextlib
import io
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'tools')]

import build_site as bs
import community_data as cd
import rss_feeds as rf
import wechat_push as wp

# 一条「什么都有」的记录：有出处 URL、有采样评论、有每条回复自己的 URL。
# 隐藏链接的契约是——这些 URL 进得来（数据层），但渲染不出去（内容层）。
COMMENT_URL = 'https://lihkg.com/thread/123/page/2'
SOURCE_URL = 'https://lihkg.com/thread/123/page/1'
RECORD = {
    'id': '08', 'key': 'LIHKG', 'name': 'LIHKG 连登财经台', 'icon': '🔥',
    'ctype': '粤语本地论坛', 'url': 'https://lihkg.com',
    'available': True, 'verdict_class': 'neutral', 'verdict_label': '中性',
    'quote': '港股估值仍偏谨慎，市场情绪未见转向。',
    'verdict': '中性（词库净值 +0.00）', 'quant': None,
    'content_kind': 'comment', 'source_url': SOURCE_URL,
    'matched_comments': 2, 'fetch_date': '2026-11-06', 'meta': '实抓采样评论',
    'comments': [
        {'text': '第一条采样评论', 'published_label': '2026-11-06T01:00:00Z',
         'scope': '有限样本', 'url': SOURCE_URL, 'metric': {'label': '评论点赞 35'}},
        {'text': '第二条采样回复', 'post_title': '港股走势',
         'published_label': '2026-11-06T02:00:00Z',
         'url': COMMENT_URL, 'metric': {'label': '评论点赞 12'}},
    ],
}
# 链接被隐藏后，这些字样/域名都不该再出现在内容层。
FORBIDDEN_TEXT = ('查看原帖/原页', '原帖/原页', '原帖')
FORBIDDEN_HOST = ('lihkg.com',)

ANCHOR_RE = re.compile(r'<a\s[^>]*href', re.IGNORECASE)
HREF_RE = re.compile(r'href\s*=', re.IGNORECASE)


def _mock_rss():
    """离线回放产物（读 tests/fixtures，不联网）。"""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        data = rf.build(mock=True, quiet=True)
    return data


class TestCommunityContentHasNoLinks(unittest.TestCase):
    """网页与微信两个输出面：内容层零链接、零链接字样。"""

    def surfaces(self):
        return {
            '网页 community_evidence_html': bs.community_evidence_html(RECORD),
            '网页 build_community_html': bs.build_community_html([RECORD]),
            '微信 community_evidence_wechat(full)': wp.community_evidence_wechat(RECORD, level='full'),
            '微信 community_evidence_wechat(compact)': wp.community_evidence_wechat(RECORD, level='compact'),
            '微信 community_card(full)': wp.community_card(RECORD, level='full'),
            '微信 community_card(standard)': wp.community_card(RECORD, level='standard'),
        }

    def test_no_anchor_and_no_href_in_any_community_surface(self):
        for name, html in self.surfaces().items():
            self.assertTrue(html, f'{name} 不应因为去掉链接而变成空产物')
            self.assertEqual([], ANCHOR_RE.findall(html), f'{name} 不得渲染 <a href>')
            self.assertNotIn('href', html, f'{name} 不得出现任何 href')

    def test_link_wording_is_gone_from_every_community_surface(self):
        for name, html in self.surfaces().items():
            for phrase in FORBIDDEN_TEXT:
                self.assertNotIn(phrase, html, f'{name} 不得出现「{phrase}」字样')

    def test_source_urls_never_leak_into_community_content(self):
        """出处 URL 进了数据层，但一个字符都不能漏到内容层。"""
        for name, html in self.surfaces().items():
            for host in FORBIDDEN_HOST:
                self.assertNotIn(host, html, f'{name} 泄漏了来源域名 {host}')
            self.assertNotIn(SOURCE_URL, html)
            self.assertNotIn(COMMENT_URL, html)

    def test_evidence_content_itself_survives_hiding_links(self):
        """隐藏链接只删链接，不删证据：采样文本、热度口径、时间仍要显示。

        注意既有口径（与本次改动无关，别误当回归去「修」）：`comments[0]` 只贡献
        「时间 · 采样范围 · 匹配数」那行 meta，主摘录走记录自身的 `quote` 字段；
        只有 `comments[1:]` 才逐条渲染正文/标题/热度。因此首条的 text 与 metric
        本来就不会出现在产物里。
        """
        web = bs.build_community_html([RECORD])
        wechat = wp.community_card(RECORD, level='full')
        for html in (web, wechat):
            # 主摘录（quote）
            self.assertIn('港股估值仍偏谨慎', html)
            # 首条贡献的时间信息保留，但采样/互动状态说明已移除
            self.assertIn('2026-11-06T01:00:00Z', html)
            self.assertNotIn('有限样本', html)
            # 第二条起逐条渲染的正文、标题与时间
            for phrase in ('第二条采样回复', '港股走势',
                           '2026-11-06T02:00:00Z'):
                self.assertIn(phrase, html)

    def test_provenance_stays_in_the_data_layer(self):
        """可核查性不受影响：数据层照旧带出处 URL，且仍经 HTTP(S) 白名单校验。"""
        self.assertEqual(SOURCE_URL, cd.safe_source_url(RECORD['source_url']))
        self.assertEqual(COMMENT_URL, cd.safe_source_url(RECORD['comments'][1]['url']))
        # 非 http(s) 的危险 URL 仍被数据层拒绝（隐藏链接不等于放松校验）
        self.assertEqual('', cd.safe_source_url('javascript:alert(1)'))
        self.assertEqual('', cd.safe_source_url('https://user:pass@lihkg.com/t/1'))

    def test_unsafe_url_has_no_escaped_form_in_content_either(self):
        """原先靠「转义后进 href」兜底；现在内容层无 href，转义形态也不该出现。"""
        evil = dict(RECORD, source_url='javascript:alert(1)',
                    comments=[dict(RECORD['comments'][0], url='https://example.com/"bad')])
        for name, html in (('网页', bs.build_community_html([evil])),
                           ('微信', wp.community_card(evil, level='full'))):
            self.assertNotIn('href', html, f'{name} 不得出现 href')
            self.assertNotIn('javascript:', html, f'{name} 不得出现 javascript:')
            self.assertNotIn('example.com', html, f'{name} 不得出现评论 URL 域名')
            self.assertNotIn('&quot;bad', html, f'{name} 不得出现 URL 的转义残片')

    def test_record_without_comments_renders_no_link_only_block(self):
        """无采样评论时，过去会只剩一个「查看原帖/原页」链接块；现在应为空。"""
        bare = dict(RECORD, comments=[], matched_comments=0)
        self.assertEqual('', bs.community_evidence_html(bare))
        self.assertEqual('', wp.community_evidence_wechat(bare, level='full'))


class TestRssSectionHasNoLinks(unittest.TestCase):
    """网页 05 节：条目标题只作纯文本，URL 不进内容层。"""

    def setUp(self):
        self.data = _mock_rss()
        self.html = rf.render_web(self.data)

    def test_rendered_rss_section_contains_no_anchor(self):
        self.assertTrue(self.html)
        self.assertEqual([], ANCHOR_RE.findall(self.html), '05 节不得渲染 <a href>')
        self.assertNotIn('href', self.html, '05 节不得出现任何 href')
        self.assertNotIn('_blank', self.html)
        self.assertNotIn('noopener', self.html)

    def test_item_urls_do_not_leak_as_plain_text_either(self):
        """条目标题保留，但条目 URL 连纯文本形式都不出现。"""
        urls = [it.get('url') or ''
                for group in self.data['groups'].values()
                for src in group.get('sources', [])
                for it in (src.get('items_list') or [])]
        urls = [u for u in urls if u]
        self.assertTrue(urls, '离线回放产物应带条目 URL，否则本断言没有意义')
        self.assertEqual(0, len(re.findall(r'https?://[^\s<"\'&]{4,}', self.html)),
                         '05 节内容层不得出现任何 http(s) URL')
        for url in urls[:20]:
            self.assertNotIn(url, self.html)

    def test_titles_and_dates_still_render(self):
        """隐藏链接不删信息：标题、日期、摘要照常显示。"""
        titles = [it.get('title') or ''
                  for group in self.data['groups'].values()
                  for src in group.get('sources', [])
                  for it in (src.get('items_list') or [])]
        titles = [t for t in titles if t]
        self.assertTrue(titles)
        for title in titles[:10]:
            self.assertIn(title, self.html, f'条目标题应仍以纯文本渲染：{title}')

    def test_data_layer_keeps_item_urls(self):
        """可核查性不受影响：URL 仍在 rssfeeds 数据层。"""
        kept = [it.get('url')
                for group in self.data['groups'].values()
                for src in group.get('sources', [])
                for it in (src.get('items_list') or [])]
        self.assertTrue(any(kept), '数据层应保留条目 URL 供离线核查')


class TestNavigationAndAssetsArePreserved(unittest.TestCase):
    """隐藏的是内容外链，不是导航：模板的锚点与字体资源必须原样保留。"""

    def setUp(self):
        with open(os.path.join(ROOT, 'report.html'), encoding='utf-8') as handle:
            self.tpl = handle.read()

    def test_in_page_nav_anchors_are_kept(self):
        for anchor in ('href="#vix"', 'href="#battlefield"', 'href="#market"',
                       'href="#intel"', 'href="#forecast"', 'href="#rss"',
                       'href="#verdict"'):
            self.assertIn(anchor, self.tpl, f'页内导航锚点必须保留：{anchor}')

    def test_font_asset_links_are_kept(self):
        self.assertEqual(3, len(re.findall(r'<link[^>]*fonts\.', self.tpl)),
                         '三个字体资源 <link> 必须保留')

    def test_template_has_no_content_anchor_left(self):
        """模板里剩下的 <a href> 只应是页内导航（#…），不该有外链。"""
        hrefs = re.findall(r'<a\s[^>]*href="([^"]*)"', self.tpl, re.IGNORECASE)
        self.assertTrue(hrefs, '模板应保留导航锚点')
        external = [h for h in hrefs if not h.startswith('#')]
        self.assertEqual([], external, f'模板不应残留内容外链：{external}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
