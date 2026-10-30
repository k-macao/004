#!/usr/bin/env python3
"""Offline phpBB and Discuz strategies; no third-party network access."""
import os
import tempfile
import unittest
from unittest import mock

import community_comments as cc
import community_data as cd
import community_spider as spider
import scrapling_core as sc
from tests.test_community_comments import NOW, PTT, PTT_INDEX, PTT_THREAD, PTT_OTHER, PTT_DETAIL, response, transport_for

BOGLEHEADS = next(c for c in cd.COMMUNITIES if c['key'] == 'BOGLEHEADS')
DISCUSS = next(c for c in cd.COMMUNITIES if c['key'] == 'DISCUSS')
BOGLE_THREAD = 'https://www.bogleheads.org/forum/viewtopic.php?f=10&t=123'
DISCUSS_THREAD = 'https://www.discuss.com.hk/viewthread.php?tid=678&page=1'

BOGLE_INDEX = '''<html><ul class="topiclist topics">
<li class="row"><a class="topictitle" href="./viewtopic.php?f=10&amp;t=123">Stock market risk</a>
<dd class="posts"><dfn>Replies</dfn> 42</dd></li>
<li class="row"><a class="topictitle" href="https://evil.example/forum/viewtopic.php?f=10&amp;t=456">stock</a>
<dd class="posts">999</dd></li>
<li class="row"><a class="topictitle" href="./viewtopic.php?f=9&amp;t=234">stock</a><dd class="posts">888</dd></li>
<li class="row"><a class="topictitle" href="./viewtopic.php?f=10&amp;t=123">duplicate</a><dd class="posts">41</dd></li>
<li class="row"><a class="topictitle" href="./viewtopic.php?f=10&amp;t=345">stock</a><dd class="posts">unknown</dd></li>
</ul></html>'''
BOGLE_DETAIL = '''<html>
<div class="post" id="p100"><div class="content">Original stock article, not a reply.</div></div>
<div class="post" id="p101"><div class="content"><blockquote>Other user bullish stock quote.</blockquote>
Market valuations may improve as rates fall.<div class="quote">quoted text not the author</div></div></div>
<div class="post" id="p102"><div class="content">Investing has risk and requires patience.</div></div>
<div class="post" id="p103"><div class="content"><blockquote>Stock only quoted material.</blockquote></div></div>
</html>'''
DISCUSS_INDEX = '''<html><table>
<tbody id="normalthread_678"><tr><th class="subject"><a href="viewthread.php?tid=678&amp;extra=page%3D1">港股投資交流</a></th>
<td class="nums"><strong>35</strong>/<em>2000</em></td></tr></tbody>
<tbody id="normalthread_999"><tr><th class="subject"><a href="https://evil.example/viewthread.php?tid=999">港股</a></th>
<td class="nums"><strong>999</strong>/<em>2000</em></td></tr></tbody>
<tbody id="normalthread_777"><tr><th class="subject"><a href="viewthread.php?tid=778">港股</a></th>
<td class="nums"><strong>800</strong>/<em>900</em></td></tr></tbody>
</table></html>'''
DISCUSS_DETAIL = '''<html>
<div id="postmessage_100">港股主帖不是回覆。</div>
<div id="postmessage_101"><div class="quote">股票别人说的内容</div>港股資金仍流入，值得觀察。</div>
<div id="postmessage_102">股票成交量不足，應注意風險。</div>
</html>'''


class TestPHPBB(unittest.TestCase):
    def test_chooses_bounded_same_forum_threads_and_actual_replies(self):
        choices = cc.bogleheads_thread_choices(response(cc.BOGLEHEADS_BOARD, BOGLE_INDEX))
        self.assertEqual([x['url'] for x in choices], [BOGLE_THREAD])
        replies = cc.parse_forum_replies(response(BOGLE_THREAD, BOGLE_DETAIL), choices[0], 'phpbb_html')
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]['url'], BOGLE_THREAD + '#p101')
        self.assertEqual(replies[0]['metric']['type'], 'thread_reply_count')
        self.assertIn('非回复点赞', replies[0]['metric']['label'])
        self.assertEqual(replies[0]['published_at'], '')
        self.assertNotIn('Other user', str(replies))
        self.assertNotIn('quoted text', str(replies))
        self.assertNotIn('Original stock article', str(replies))
        self.assertEqual(cc.parse_forum_replies(response(BOGLE_THREAD, BOGLE_DETAIL), choices[0], 'unknown'), [])

    def test_phpbb_count_with_suffix_is_understood(self):
        listing = BOGLE_INDEX.replace('<dfn>Replies</dfn> 42', '42 <dfn>Replies</dfn>')
        choices = cc.bogleheads_thread_choices(response(cc.BOGLEHEADS_BOARD, listing))
        self.assertEqual(choices[0]['replies'], 42)

    def test_last_listed_page_is_used_without_cross_thread_or_host_links(self):
        pages = ('<a href="./viewtopic.php?t=123&amp;start=50">2</a>'
                 '<a href="./viewtopic.php?t=123&amp;start=100">3</a>'
                 '<a href="./viewtopic.php?t=999&amp;start=500">other thread</a>'
                 '<a href="https://evil.example/forum/viewtopic.php?t=123&amp;start=999">offsite</a>')
        listing = BOGLE_INDEX.replace('<dd class="posts"><dfn>Replies</dfn> 42</dd>',
                                      '<dd class="posts"><dfn>Replies</dfn> 42</dd>' + pages, 1)
        choices = cc.bogleheads_thread_choices(response(cc.BOGLEHEADS_BOARD, listing))
        self.assertEqual(choices[0]['url'], BOGLE_THREAD + '&start=100')
        self.assertTrue(choices[0]['later_page'])
        replies = cc.parse_forum_replies(response(choices[0]['url'], BOGLE_DETAIL), choices[0], 'phpbb_html')
        self.assertIn('可见末页', replies[0]['scope'])
        self.assertEqual(replies[0]['url'], BOGLE_THREAD + '&start=100#p101')


class TestDiscuz(unittest.TestCase):
    def test_chooses_valid_thread_and_excludes_first_post_and_quotes(self):
        choices = cc.discuss_thread_choices(response(DISCUSS['url'], DISCUSS_INDEX))
        self.assertEqual([x['url'] for x in choices], [DISCUSS_THREAD])
        replies = cc.parse_forum_replies(response(DISCUSS_THREAD, DISCUSS_DETAIL), choices[0], 'discuz_html')
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]['url'], DISCUSS_THREAD + '#pid101')
        self.assertEqual(replies[0]['strategy'], 'discuz_html')
        self.assertEqual(replies[0]['metric']['value'], 35)
        self.assertNotIn('别人说', str(replies))
        self.assertNotIn('主帖', str(replies))
        self.assertEqual(replies[0]['published_label'], '发布时间未知')


class TestForumPipeline(unittest.TestCase):
    def test_both_new_strategies_share_global_ten_second_post_limiter(self):
        class Clock:
            now = 0.0
            def monotonic(self):
                return self.now
            def sleep(self, seconds):
                self.now += seconds
        clock = Clock()
        limiter = cc.PostInterval(clock=clock.monotonic, sleep=clock.sleep)
        pages = {cc.BOGLEHEADS_BOARD: BOGLE_INDEX, BOGLE_THREAD: BOGLE_DETAIL,
                 DISCUSS['url']: DISCUSS_INDEX, DISCUSS_THREAD: DISCUSS_DETAIL,
                 PTT['url']: PTT_INDEX, PTT_THREAD: PTT_DETAIL, PTT_OTHER: PTT_DETAIL}
        base = transport_for(pages)
        starts = []
        def transport(method, url, headers, timeout, proxy=None, body=None):
            if url in (BOGLE_THREAD, DISCUSS_THREAD, PTT_THREAD, PTT_OTHER):
                starts.append((url, clock.now))
            return base(method, url, headers, timeout, proxy, body)
        session = sc.FetcherSession(transport=transport, retries=1)
        outcomes, stats = cc.collect_comments([BOGLEHEADS, DISCUSS, PTT], session=session, now=NOW,
                         post_limiter=limiter, autothrottle=False, max_blocked_retries=0, download_delay=0)
        self.assertEqual(len(starts), 4)
        self.assertTrue(all(b[1] - a[1] >= 10 for a, b in zip(starts, starts[1:])), starts)
        self.assertEqual(stats['post_fetch_attempts'], 4)
        self.assertEqual(outcomes['BOGLEHEADS']['attempt']['status'], 'ok')
        self.assertEqual(outcomes['DISCUSS']['attempt']['status'], 'ok')

    def test_pipeline_renders_new_replies_not_generic_page_content(self):
        pages = {BOGLEHEADS['url']: '<html><h1>Stock markets</h1></html>',
                 DISCUSS['url']: DISCUSS_INDEX, cc.BOGLEHEADS_BOARD: BOGLE_INDEX,
                 BOGLE_THREAD: BOGLE_DETAIL, DISCUSS_THREAD: DISCUSS_DETAIL}
        session = sc.FetcherSession(transport=transport_for(pages), retries=1)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cd, 'COMMUNITIES', [BOGLEHEADS, DISCUSS]):
            result = cd.generate_all_communities(market={}, now=NOW, session=session,
                storage_file=os.path.join(tmp, 'adaptive.db'), verbose=False, autothrottle=False,
                max_blocked_retries=0, download_delay=0, post_limiter=cc.PostInterval(interval=0))
        first, second = result['communities']
        self.assertEqual(first['strategy'], 'phpbb_html')
        self.assertEqual(second['strategy'], 'discuz_html')
        self.assertEqual(first['content_kind'], 'thread_reply')
        self.assertEqual(result['summary']['comment_sources'], 2)
        self.assertIn('非回复点赞', first['meta'])
        self.assertIn('资金', second['analysis_input']['snippet'] if '资金' in second['analysis_input']['snippet']
                      else second['analysis_input']['snippet'].replace('資金', '资金'))

    def test_index_denial_is_explicit_and_no_detail_is_fabricated(self):
        session = sc.FetcherSession(transport=transport_for({cc.BOGLEHEADS_BOARD: (403, 'denied')}), retries=1)
        outcomes, _ = cc.collect_comments([BOGLEHEADS], session=session, now=NOW,
                          autothrottle=False, max_blocked_retries=0, download_delay=0)
        self.assertEqual(outcomes['BOGLEHEADS']['comments'], [])
        self.assertEqual(outcomes['BOGLEHEADS']['attempt']['status'], 'http_403')


if __name__ == '__main__':
    unittest.main()
