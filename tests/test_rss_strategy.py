#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline tests for the RSS-first collection strategy.

RSS 优先：对有公开 RSS 地址的 49 源先尝试订阅源，不可用再回退到
网站/APP 公开信息流。零联网，全部用模拟报文验证。
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT]

import community_comments as cc
import community_data as cd
import community_spider as cs
import scrapling_core as sc
from tests.test_community_comments import NOW, PTT, response, transport_for

RSS2 = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>港股社区订阅</title>
  <item>
    <title>恒指估值有支撑，南向资金持续流入</title>
    <link>https://example.com/thread/1</link>
    <pubDate>Fri, 30 Oct 2026 03:00:00 +0000</pubDate>
    <description>港股估值有支撑，南向资金持续流入，估值修复带动偏多讨论。</description>
  </item>
  <item>
    <title>今天天气不错，适合出去走走</title>
    <link>https://example.com/thread/2</link>
    <pubDate>Fri, 30 Oct 2026 03:05:00 +0000</pubDate>
    <description>无关生活闲聊，不含任何财经关键词。</description>
  </item>
  <item>
    <title>恒科波动加大，期权交投活跃</title>
    <link>https://example.com/thread/3</link>
    <pubDate>Fri, 30 Oct 2026 02:50:00 +0000</pubDate>
    <description>恒科波动加大，期权交投活跃，做多做空分歧明显。</description>
  </item>
</channel>
</rss>'''

ATOM = '''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Reddit r/ChinaStocks</title>
  <entry>
    <title>恒指估值有支撑（Atom）</title>
    <link href="https://www.reddit.com/r/ChinaStocks/comments/abc/"/>
    <published>2026-10-30T03:00:00Z</published>
    <summary>南向资金持续流入，估值修复。</summary>
  </entry>
</feed>'''

EXPIRED_RSS = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <item>
    <title>旧恒指消息</title>
    <link>https://example.com/old</link>
    <pubDate>Fri, 21 Oct 2026 03:00:00 +0000</pubDate>
    <description>九天前的旧消息，应被时间窗口剔除。</description>
  </item>
</channel>
</rss>'''


class TestRssParsing(unittest.TestCase):
    def test_rss2_parses_finance_items_and_excludes_non_finance(self):
        entries = cc._parse_rss_items(RSS2.encode('utf-8'), NOW)
        self.assertIsNotNone(entries)
        self.assertEqual([e['title'] for e in entries],
                         ['恒指估值有支撑，南向资金持续流入',
                          '恒科波动加大，期权交投活跃'])
        self.assertEqual(entries[0]['url'], 'https://example.com/thread/1')
        self.assertEqual(entries[0]['published_at'], '2026-10-30T03:00:00Z')
        self.assertIn('南向资金', entries[0]['text'])

    def test_atom_parses_entries(self):
        entries = cc._parse_rss_items(ATOM.encode('utf-8'), NOW)
        self.assertIsNotNone(entries)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['title'], '恒指估值有支撑（Atom）')
        self.assertEqual(entries[0]['url'], 'https://www.reddit.com/r/ChinaStocks/comments/abc/')
        self.assertEqual(entries[0]['published_at'], '2026-10-30T03:00:00Z')

    def test_invalid_xml_returns_none(self):
        self.assertIsNone(cc._parse_rss_items(b'<rss><broken', NOW))

    def test_empty_body_returns_empty_list(self):
        self.assertEqual(cc._parse_rss_items(b'', NOW), [])

    def test_expired_items_are_excluded(self):
        entries = cc._parse_rss_items(EXPIRED_RSS.encode('utf-8'), NOW)
        self.assertEqual(entries, [])

    def test_max_items_capped(self):
        many = ''.join(
            f'<item><title>恒指第 {i} 条</title><link>https://example.com/{i}</link>'
            f'<pubDate>Fri, 30 Oct 2026 03:{i:02d}:00 +0000</pubDate>'
            f'<description>恒指讨论 {i}</description></item>'
            for i in range(15))
        xml = f'<?xml version="1.0"?><rss version="2.0"><channel>{many}</channel></rss>'
        entries = cc._parse_rss_items(xml.encode('utf-8'), NOW)
        self.assertEqual(len(entries), cc.MAX_THREADS)


class TestRssFeedFetch(unittest.TestCase):
    def _session(self, pages):
        return sc.FetcherSession(transport=transport_for(pages), retries=1)

    def test_no_rss_url_returns_no_rss(self):
        comm = dict(PTT, rss='')
        result = cc.fetch_rss_feed(comm, self._session({}), now=NOW)
        self.assertEqual(result['attempt']['status'], 'no_rss')
        self.assertEqual(result['comments'], [])

    def test_community_rss_field_takes_precedence(self):
        comm = dict(PTT, rss='https://example.com/ptt.rss')
        pages = {'https://example.com/ptt.rss': RSS2}
        result = cc.fetch_rss_feed(comm, self._session(pages), now=NOW)
        self.assertEqual(result['attempt']['status'], 'ok')
        self.assertEqual(len(result['comments']), 2)
        self.assertEqual(result['comments'][0]['strategy'], 'rss_feed')
        self.assertIn('非回复点赞', result['comments'][0]['metric']['label'])

    def test_rss_global_mapping_used_when_community_field_empty(self):
        comm = dict(cd.COMMUNITIES[cd.COMMUNITIES.index(next(c for c in cd.COMMUNITIES if c['key'] == 'REDDIT'))],
                    rss='')
        pages = {'https://www.reddit.com/r/ChinaStocks/.rss': RSS2}
        result = cc.fetch_rss_feed(comm, self._session(pages), now=NOW)
        self.assertEqual(result['attempt']['status'], 'ok')

    def test_network_error_is_explicit_and_does_not_fabricate(self):
        def boom(method, url, headers, timeout, proxy=None, body=None):
            raise OSError('DNS failure')
        comm = dict(PTT, rss='https://example.com/ptt.rss')
        session = sc.FetcherSession(transport=boom, retries=1)
        result = cc.fetch_rss_feed(comm, session, now=NOW)
        self.assertEqual(result['attempt']['status'], 'request_failed')
        self.assertEqual(result['comments'], [])

    def test_invalid_xml_is_explicit(self):
        comm = dict(PTT, rss='https://example.com/ptt.rss')
        pages = {'https://example.com/ptt.rss': '<rss><broken'}
        result = cc.fetch_rss_feed(comm, self._session(pages), now=NOW)
        self.assertEqual(result['attempt']['status'], 'invalid_response')
        self.assertEqual(result['comments'], [])

    def test_rss_shares_global_ten_second_post_interval(self):
        class Clock:
            now = 0.0
            def monotonic(self):
                return self.now
            def sleep(self, seconds):
                self.now += seconds

        clock = Clock()
        limiter = cc.PostInterval(clock=clock.monotonic, sleep=clock.sleep)
        starts = []

        def transport(method, url, headers, timeout, proxy=None, body=None):
            if url.endswith('.rss'):
                starts.append(clock.now)
            return 200, 'OK', {}, RSS2.encode('utf-8'), url

        session = sc.FetcherSession(transport=transport, retries=1)
        comms = [dict(c, rss=f'https://example.com/{c["key"]}.rss') for c in
                 [next(c for c in cd.COMMUNITIES if c['key'] == 'REDDIT'),
                  next(c for c in cd.COMMUNITIES if c['key'] == 'BOGLEHEADS')]]
        for comm in comms:
            cc.fetch_rss_feed(comm, session, now=NOW, post_limiter=limiter)
        self.assertEqual(len(starts), 2)
        self.assertTrue(starts[1] - starts[0] >= 10, starts)


class TestRssInSpiderPipeline(unittest.TestCase):
    def test_rss_success_skips_html_fetch(self):
        """RSS 成功时，该源不应再触发网页 HTML 抓取。"""
        comm = dict(PTT, rss='https://example.com/ptt.rss')
        calls = []

        def transport(method, url, headers, timeout, proxy=None, body=None):
            calls.append(url)
            if url.endswith('.rss'):
                return 200, 'OK', {}, RSS2.encode('utf-8'), url
            return 200, 'OK', {}, '<html><h1>恒指公开页</h1></html>'.encode('utf-8'), url

        session = sc.FetcherSession(transport=transport, retries=1)
        with tempfile.TemporaryDirectory() as tmp:
            results, stats = cs.fetch_live_snippets(
                [comm], session=session, now=NOW,
                storage_file=os.path.join(tmp, 'a.db'), verbose=False,
                autothrottle=False, max_blocked_retries=0, download_delay=0,
                post_limiter=cc.PostInterval(interval=0))
        hit = results['PTT']
        self.assertTrue(hit['ok'])
        self.assertEqual(hit['strategy'], 'rss_feed')
        self.assertEqual(hit['content_kind'], 'thread_reply')
        self.assertEqual(calls, ['https://example.com/ptt.rss'])
        self.assertIn('南向资金', hit['snippet'])

    def test_rss_failure_falls_back_to_html(self):
        """RSS 不可用时，回退到网页公开信息流。"""
        comm = dict(PTT, rss='https://example.com/ptt.rss')
        html_page = '<html><head><title>恒指 26,000</title></head><body>\n<article>\n<h3>恒指估值有支撑</h3>\n<p>南向资金净买入 62 亿，港股科技轮动。</p>\n</article></body></html>'
        calls = []

        def transport(method, url, headers, timeout, proxy=None, body=None):
            calls.append(url)
            if url.endswith('.rss'):
                return 404, 'Not Found', {}, b'', url
            return 200, 'OK', {}, html_page.encode('utf-8'), url

        session = sc.FetcherSession(transport=transport, retries=1)
        with tempfile.TemporaryDirectory() as tmp:
            results, stats = cs.fetch_live_snippets(
                [comm], session=session, now=NOW,
                storage_file=os.path.join(tmp, 'a.db'), verbose=False,
                autothrottle=False, max_blocked_retries=0, download_delay=0,
                post_limiter=cc.PostInterval(interval=0))
        hit = results['PTT']
        self.assertTrue(hit['ok'])
        self.assertEqual(hit['strategy'], 'scrapling_page')
        self.assertIn('https://example.com/ptt.rss', calls)
        self.assertIn(PTT['url'], calls)


if __name__ == '__main__':
    unittest.main()
