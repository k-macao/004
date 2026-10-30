#!/usr/bin/env python3
"""Offline integration tests for the multi-strategy community comment pipeline."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'tools')]

import build_site as bs
import community_comments as cc
import community_data as cd
import community_spider as cs
import scrapling_core as sc
import wechat_push as wp

NOW = datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc)
TS = int(NOW.timestamp())
PTT = next(c for c in cd.COMMUNITIES if c['key'] == 'PTT')
LIHKG = next(c for c in cd.COMMUNITIES if c['key'] == 'LIHKG')
REDDIT = next(c for c in cd.COMMUNITIES if c['key'] == 'RINVESTING')
PTT_THREAD = 'https://www.ptt.cc/bbs/Stock/M.12345.A.ABC.html'
PTT_OTHER = 'https://www.ptt.cc/bbs/Stock/M.12346.A.DEF.html'
LIHKG_THREAD = 'https://lihkg.com/api_v2/thread/123/page/1'

PTT_INDEX = '''<html><body><div class="r-ent">
  <div class="nrec"><span>爆</span></div>
  <div class="title"><a href="/bbs/Stock/M.12345.A.ABC.html">[討論] 港股與美股走勢</a></div></div>
<div class="r-ent"><div class="nrec">53</div>
  <div class="title"><a href="/bbs/Stock/M.12346.A.DEF.html">[討論] 股票投資心得</a></div></div>
<div class="r-ent"><div class="nrec">90</div>
  <div class="title"><a href="https://evil.example/bbs/Stock/M.12.A.AAA.html">港股外部連結</a></div></div>
<div class="r-ent"><div class="nrec">X1</div><div class="title"><a href="/bbs/Stock/M.999.A.AAA.html">股票</a></div></div>
</body></html>'''
PTT_DETAIL = '''<html><body><div id="main-content">
<div class="push"><span class="push-tag">推 </span><span class="push-userid">secret-user</span>
<span class="push-content">: 港股估值有支撐，南向資金仍在流入。</span>
<span class="push-ipdatetime"> 1.2.3.4 10/30 09:45</span></div>
<div class="push"><span class="push-tag">→ </span><span class="push-userid">someone</span>
<span class="push-content">: 成交量是否能持續還需要觀察。</span><span class="push-ipdatetime">10/30 09:46</span></div>
<div class="push"><span class="push-tag">推 </span><span class="push-content">: 推</span></div>
</div></body></html>'''
LIHKG_INDEX = {'success': 1, 'response': {'items': [
    {'thread_id': '123', 'title': '恒指與港股走勢', 'no_of_reply': '100'},
    {'thread_id': '../bad', 'title': '港股外部連結', 'no_of_reply': '999'},
    {'thread_id': '123', 'title': '重複', 'no_of_reply': '99'},
]}}
LIHKG_DETAIL = {'success': 1, 'response': {'item_data': [
    {'msg_num': '1', 'msg': '這是港股主帖而非回覆', 'like_count': '999', 'reply_time': TS},
    {'msg_num': '2', 'msg': '<b>港股資金</b>持續流入，估值仍有空間。', 'like_count': '35', 'reply_time': TS - 60},
    {'msg_num': '3', 'msg': '港股波動較大，應先控制倉位。', 'like_count': '12', 'reply_time': TS - 120},
    {'msg_num': '4', 'msg': '港股很久以前的旧回覆不应出现。', 'like_count': '9999', 'reply_time': TS - 9 * 86400},
    {'msg_num': '5', 'msg': '港股沒有時間的回覆不应出现。', 'like_count': '9999'},
    {'msg_num': '6', 'msg': '港股票數被隱藏的回复不应参与排名。', 'like_count': '999',
     'reply_time': TS, 'display_vote': False},
    {'msg': '港股沒有樓層的內容不应出现。', 'like_count': '888', 'reply_time': TS},
]}}


def response(url, content, status=200):
    raw = content if isinstance(content, bytes) else (
        json.dumps(content, ensure_ascii=False).encode() if isinstance(content, dict) else str(content).encode())
    return sc.Response(url=url, content=raw, status=status, reason='OK', headers={}, request_headers={})


def transport_for(pages, calls=None):
    def transport(method, url, headers, timeout, proxy=None, body=None):
        if calls is not None:
            calls.append((url, dict(headers)))
        val = pages.get(url, (404, 'not found'))
        status, data = val if isinstance(val, tuple) else (200, val)
        raw = data if isinstance(data, bytes) else (
            json.dumps(data, ensure_ascii=False).encode() if isinstance(data, dict) else str(data).encode())
        return status, 'OK', {'Content-Type': 'text/html; charset=utf-8'}, raw, url
    return transport


class TestPTTStrategy(unittest.TestCase):
    def test_listing_filters_offsite_and_preserves_push_semantics(self):
        threads = cc.ptt_thread_choices(response(PTT['url'], PTT_INDEX))
        self.assertEqual([x['url'] for x in threads], [PTT_THREAD, PTT_OTHER])
        self.assertTrue(threads[0]['lower_bound'])
        comments = cc.parse_ptt_replies(response(PTT_THREAD, PTT_DETAIL), threads[0])
        self.assertEqual(len(comments), 2)
        first = comments[0]
        self.assertEqual(first['kind'], 'thread_reply')
        self.assertEqual(first['metric']['type'], 'thread_net_pushes')
        self.assertIn('≥100', first['metric']['label'])
        self.assertIn('非回复点赞', first['metric']['label'])
        self.assertEqual(first['published_at'], '')
        self.assertIn('年份未知', first['published_label'])
        january = PTT_DETAIL.replace('10/30 09:45', '1/02 9:45')
        early = cc.parse_ptt_replies(response(PTT_THREAD, january), threads[0])[0]
        self.assertEqual(early['published_label'], '1/02 9:45（年份未知）')
        self.assertNotIn('1.2.3.4', json.dumps(comments, ensure_ascii=False))
        self.assertNotIn('secret-user', json.dumps(comments, ensure_ascii=False))


class TestLIHKGStrategy(unittest.TestCase):
    def test_chooses_finance_threads_and_only_recent_ranked_replies(self):
        choices = cc.lihkg_thread_choices(response(cc.LIHKG_LIST_URL, LIHKG_INDEX))
        self.assertEqual([t['id'] for t in choices], ['123'])
        comments = cc.parse_lihkg_replies(response(LIHKG_THREAD, LIHKG_DETAIL), choices[0], NOW)
        selected, matched = cc._finish_candidates(comments)
        self.assertEqual(matched, 2)
        self.assertEqual([c['metric']['value'] for c in selected], [35, 12])
        self.assertEqual(selected[0]['published_at'], '2026-10-30T02:59:00Z')
        self.assertEqual(selected[0]['url'], 'https://lihkg.com/thread/123/page/1')
        self.assertNotIn('主帖', str(selected))
        self.assertNotIn('<b>', selected[0]['text'])
        self.assertTrue(LIHKG['url'].endswith('/category/15'))

    def test_bad_json_or_platform_error_is_not_a_comment(self):
        for content in ('<html>login</html>', {'success': 0, 'response': {'items': []}},
                        {'error_code': 401, 'response': {'items': []}}):
            self.assertIsNone(cc.lihkg_thread_choices(response(cc.LIHKG_LIST_URL, content)))


class FakeSubmission:
    def __init__(self, title, comments):
        self.title = title
        self.comments = comments
        self.comment_sort = ''
        self.comment_limit = 0


class FakeReddit:
    def __init__(self, submissions):
        self.submissions = submissions
        self.calls = []

    def subreddit(self, name):
        self.calls.append(name)
        return SimpleNamespace(hot=lambda limit: iter(self.submissions))


def reddit_comment(text, score, timestamp=TS, permalink='/r/investing/comments/abc/title/def/'):
    return SimpleNamespace(body=text, score=score, created_utc=timestamp, permalink=permalink,
                           author='must-not-be-exported')


class TestPRAWStrategy(unittest.TestCase):
    def test_reddit_uses_praw_top_comments_and_actual_timestamps(self):
        submission = FakeSubmission('Stock market discussion', [
            reddit_comment('Market valuations may improve as rates fall.', 3),
            reddit_comment('Market earnings are stronger than expected.', 22,
                           permalink='/r/investing/comments/abc/title/ghi/'),
            reddit_comment('Market old comment should be excluded.', 999, TS - 9 * 86400,
                           '/r/investing/comments/abc/title/old/'),
            reddit_comment('[removed]', 500),
            SimpleNamespace(body='Market hidden scores should not be ranked.', score=999, score_hidden=True,
                            created_utc=TS, permalink='/r/investing/comments/abc/title/hidden/'),
            reddit_comment('Market offsite link should not be accepted.', 900, TS,
                           'https://evil.example/r/investing/comments/abc/title/def/'),
            SimpleNamespace(),  # MoreComments / malformed entry
        ])
        client = FakeReddit([submission])
        result = cc.fetch_reddit_comments([REDDIT], client=client, now=NOW)['RINVESTING']
        self.assertEqual(result['attempt']['status'], 'ok')
        self.assertEqual([c['metric']['value'] for c in result['comments']], [22, 3])
        self.assertEqual(submission.comment_sort, 'top')
        self.assertEqual(submission.comment_limit, cc.MAX_COMMENTS_PER_THREAD)
        self.assertEqual(client.calls, ['investing'])
        self.assertNotIn('must-not-be-exported', str(result))
        self.assertEqual(result['comments'][0]['published_at'], '2026-10-30T03:00:00Z')

    def test_missing_credentials_or_package_is_explicit_and_does_not_log_secrets(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = cc.fetch_reddit_comments([REDDIT], now=NOW)
        self.assertEqual(result['RINVESTING']['attempt']['status'], 'not_configured')
        self.assertEqual(result['RINVESTING']['comments'], [])
        wrong = dict(REDDIT, url='https://www.reddit.com/r/options/')
        self.assertEqual(cc.fetch_reddit_comments([wrong], client=FakeReddit([]), now=NOW), {})


class TestPostInterval(unittest.TestCase):
    def test_cooldown_is_after_completion_or_failure_not_just_after_start(self):
        class Clock:
            now = 0.0
            def monotonic(self):
                return self.now
            def sleep(self, seconds):
                self.now += seconds
        clock = Clock()
        limiter = cc.PostInterval(clock=clock.monotonic, sleep=clock.sleep)
        def slow():
            clock.now += 20
        def failed():
            clock.now += 2
            raise OSError('offline')
        limiter.call(slow)
        self.assertEqual(clock.now, 20)
        with self.assertRaises(OSError):
            limiter.call(failed)
        self.assertEqual(clock.now, 32)
        self.assertEqual(limiter.call(lambda: clock.now), 42)
        self.assertEqual(limiter.started, 3)

    def test_post_requests_are_globally_spaced_across_sources_and_retries(self):
        class Clock:
            now = 100.0
            def monotonic(self):
                return self.now
            def sleep(self, seconds):
                self.now += seconds

        clock = Clock()
        limiter = cc.PostInterval(clock=clock.monotonic, sleep=clock.sleep)
        starts = []
        pages = {PTT['url']: PTT_INDEX, PTT_THREAD: PTT_DETAIL, PTT_OTHER: PTT_DETAIL,
                 cc.LIHKG_LIST_URL: LIHKG_INDEX, LIHKG_THREAD: LIHKG_DETAIL}
        base_transport = transport_for(pages)
        attempts = {'ptt': 0}

        def transport(method, url, headers, timeout, proxy=None, body=None):
            if url in (PTT_THREAD, PTT_OTHER, LIHKG_THREAD):
                starts.append((url, clock.now))
                if url == PTT_THREAD and attempts['ptt'] == 0:
                    attempts['ptt'] += 1
                    raise OSError('temporary offline test failure')
            return base_transport(method, url, headers, timeout, proxy, body)

        class TimedSubmission(FakeSubmission):
            def __init__(self):
                self.title = 'Stock market discussion'
                self.comment_sort = ''
                self.comment_limit = 0
            @property
            def comments(self):
                starts.append(('reddit', clock.now))
                return [reddit_comment('Market earnings remain stronger than expected.', 8)]

        client = FakeReddit([TimedSubmission()])
        session = sc.FetcherSession(transport=transport, retries=2, retry_delay=0)
        with mock.patch.object(cc, 'reddit_client', return_value=(client, 'ready')):
            outcomes, stats = cc.collect_comments(
                [PTT, LIHKG, REDDIT], session=session, now=NOW, post_limiter=limiter,
                retries=2, autothrottle=False, max_blocked_retries=0, download_delay=0)
        self.assertEqual({s for s, _ in starts}, {PTT_THREAD, PTT_OTHER, LIHKG_THREAD, 'reddit'})
        self.assertEqual(len(starts), 5)  # three HTML/API posts, one retry, one Reddit post
        self.assertTrue(all(b[1] - a[1] >= 10 for a, b in zip(starts, starts[1:])), starts)
        self.assertEqual(stats['post_interval_seconds'], 10)
        self.assertEqual(stats['post_fetch_attempts'], len(starts))
        self.assertEqual(outcomes['RINVESTING']['attempt']['status'], 'ok')


class TestPipeline(unittest.TestCase):
    def test_two_strategies_flow_into_data_web_and_wechat(self):
        pages = {
            PTT['url']: PTT_INDEX, PTT_THREAD: PTT_DETAIL, PTT_OTHER: PTT_DETAIL,
            LIHKG['url']: '<html><body><h1>港股公開頁一般摘錄</h1></body></html>',
            cc.LIHKG_LIST_URL: LIHKG_INDEX, LIHKG_THREAD: LIHKG_DETAIL,
        }
        calls = []
        session = sc.FetcherSession(transport=transport_for(pages, calls), retries=1)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cd, 'COMMUNITIES', [PTT, LIHKG]):
            data = cd.generate_all_communities(
                market={}, now=NOW, session=session, storage_file=os.path.join(tmp, 'a.db'),
                verbose=False, autothrottle=False, max_blocked_retries=0, download_delay=0,
                post_limiter=cc.PostInterval(interval=0))
        ptt, lihkg = data['communities']
        self.assertEqual(data['summary']['ok'], 2)
        self.assertEqual(ptt['content_kind'], 'thread_reply')
        self.assertEqual(lihkg['content_kind'], 'comment')
        self.assertEqual(lihkg['strategy'], 'lihkg_api')
        self.assertEqual(lihkg['source_url'], 'https://lihkg.com/thread/123/page/1')
        self.assertEqual(lihkg['matched_comments'], 2)
        self.assertEqual(wp.CHANNEL_READ_RE.findall(lihkg['meta']), ['2026-10-30'])
        self.assertEqual(wp.CHANNEL_READ_RE.findall(ptt['meta']), ['2026-10-30'])
        self.assertEqual(data['fetch_engine']['comment_fetch']['comment_sources'], 2)
        self.assertEqual(data['fetch_engine']['response_status_count']['status_200'], len(calls))
        self.assertTrue(any(u == cc.LIHKG_LIST_URL and h.get('X-LI-DEVICE-TYPE') == 'browser'
                            for u, h in calls))
        web = bs.build_community_html(data['communities'])
        for phrase in ('采样评论', '热帖回复', '非回复点赞', '评论点赞 35',
                       '其他采样回复', '2026-10-30T02:59:00Z', '查看原帖/原页'):
            self.assertIn(phrase, web)
        wechat = wp.community_card(lihkg, level='standard')
        for phrase in ('采样评论', '评论点赞 35', '评论点赞 12', '原帖/原页'):
            self.assertIn(phrase, wechat)
        self.assertNotIn('secret-user', web + wechat)

    def test_praw_success_survives_blocked_base_page_and_reaches_renderers(self):
        submission = FakeSubmission('Stock market discussion', [
            reddit_comment('Market earnings are stronger than expected.', 22),
            reddit_comment('Market valuations remain uncertain this week.', 4,
                           permalink='/r/investing/comments/abc/title/ghi/'),
        ])
        client = FakeReddit([submission])
        session = sc.FetcherSession(transport=transport_for({REDDIT['url']: (403, 'denied')}), retries=1)
        with (tempfile.TemporaryDirectory() as tmp,
              mock.patch.object(cd, 'COMMUNITIES', [REDDIT]),
              mock.patch.object(cc, 'reddit_client', return_value=(client, 'ready'))):
            data = cd.generate_all_communities(market={}, now=NOW, session=session,
                storage_file=os.path.join(tmp, 'a.db'), verbose=False, autothrottle=False,
                max_blocked_retries=0, download_delay=0)
        record = data['communities'][0]
        self.assertEqual(record['content_kind'], 'comment')
        self.assertEqual(record['strategy'], 'reddit_praw')
        self.assertEqual(record['source'], 'live')
        self.assertEqual(data['summary']['comment_sources'], 1)
        self.assertEqual(data['summary']['page_excerpt_sources'], 0)
        self.assertIn('本条采样评论', record['verdict'])
        self.assertNotIn('公开页摘录', record['verdict'])
        self.assertIn('评论站内得分 22', record['meta'])
        self.assertIn('reddit.com/r/investing/comments/', bs.build_community_html([record]))
        self.assertIn('评论站内得分 4', wp.community_card(record, level='full'))
        self.assertEqual(data['fetch_engine']['comment_fetch']['attempts']['RINVESTING']['status'], 'ok')

    def test_comment_sentiment_does_not_include_the_parent_title(self):
        hit = {'content_kind': 'comment', 'strategy': 'reddit_praw',
               'comments': [{'metric': {'label': '评论站内得分 4'}}]}
        record = cd.build_live_record(REDDIT, 'The market is stable with no directional signal.',
                                      '2026-10-30', title='Stock market bullish rally gains', evidence=hit)
        expected = cd.analyze_live_snippet('The market is stable with no directional signal.',
                                           title='', ctype=REDDIT['ctype'])
        self.assertEqual(record['quant']['sentiment']['score'], expected['quant']['sentiment']['score'])
        self.assertIn('bullish rally', record['quote'])  # title remains context, not sentiment input

    def test_failure_preserves_page_excerpt_and_reports_attempt(self):
        pages = {LIHKG['url']: '<html><body><h1>港股公開頁一般摘錄</h1></body></html>',
                 cc.LIHKG_LIST_URL: (403, 'blocked')}
        session = sc.FetcherSession(transport=transport_for(pages), retries=1)
        with tempfile.TemporaryDirectory() as tmp:
            result, stats = cs.fetch_live_snippets([LIHKG], session=session, now=NOW,
                storage_file=os.path.join(tmp, 'a.db'), verbose=False, autothrottle=False,
                max_blocked_retries=0, download_delay=0)
        hit = result['LIHKG']
        self.assertTrue(hit['ok'])
        self.assertEqual(hit['content_kind'], 'page_excerpt')
        self.assertEqual(hit['comment_attempt']['status'], 'http_403')
        self.assertEqual(stats['comment_fetch']['comment_sources'], 0)
        rec = cd.build_live_record(LIHKG, hit['snippet'], '2026-10-30', evidence=hit)
        self.assertIn('未核验为评论', bs.build_community_html([rec]))
        self.assertFalse(rec['comments'])

    def test_robots_disallow_followups_and_do_not_discard_page_excerpt(self):
        calls = []
        pages = {LIHKG['url']: '<html><body><h1>港股公开页摘录可用</h1></body></html>',
                 'https://lihkg.com/robots.txt': 'User-agent: *\nDisallow: /api_v2/\n'}
        session = sc.FetcherSession(transport=transport_for(pages, calls), retries=1)
        with tempfile.TemporaryDirectory() as tmp:
            result, stats = cs.fetch_live_snippets([LIHKG], session=session, now=NOW,
                storage_file=os.path.join(tmp, 'a.db'), verbose=False, obey_robots=True,
                autothrottle=False, max_blocked_retries=0, download_delay=0)
        self.assertTrue(result['LIHKG']['ok'])
        self.assertEqual(result['LIHKG']['comment_attempt']['status'], 'robots_disallowed')
        self.assertGreaterEqual(stats['comment_fetch']['robots_disallowed_count'], 1)
        self.assertFalse(any(u == cc.LIHKG_LIST_URL for u, _ in calls))

    def test_truncated_comment_is_marked_in_data_and_rendering(self):
        title = '港股消息'
        long_text = '港股資金正在流入' + '样' * 310
        comment = reddit_comment(long_text, 7)
        result = cc.fetch_reddit_comments([REDDIT], client=FakeReddit([FakeSubmission(title, [comment])]), now=NOW)
        item = result['RINVESTING']['comments'][0]
        self.assertTrue(item['text_truncated'])
        self.assertEqual(len(item['text']), cs.MAX_SNIPPET)
        hit = {'content_kind': 'comment', 'comments': [item], 'url': item['url'], 'strategy': 'reddit_praw'}
        rec = cd.build_live_record(REDDIT, item['text'], '2026-10-30', title=title, evidence=hit)
        self.assertTrue(rec['quote'].endswith('…'))
        self.assertIn('…', bs.build_community_html([rec]))

    def test_adapter_failure_does_not_cancel_other_strategies(self):
        submission = FakeSubmission('Stock market discussion', [
            reddit_comment('Market earnings remain stronger than expected.', 8)])
        client = FakeReddit([submission])
        with (mock.patch.object(cc.CommentSpider, 'crawl', side_effect=RuntimeError('bad engine')),
              mock.patch.object(cc, 'reddit_client', return_value=(client, 'ready'))):
            outcomes, stats = cc.collect_comments([PTT, REDDIT], now=NOW,
                                                    autothrottle=False, download_delay=0)
        self.assertEqual(outcomes['PTT']['attempt']['status'], 'engine_error')
        self.assertEqual(outcomes['RINVESTING']['attempt']['status'], 'ok')
        self.assertEqual(stats['comment_sources'], 1)
        self.assertEqual(stats['error'], 'RuntimeError')

    def test_comment_requests_do_not_follow_offsite_redirects(self):
        calls = []
        def transport(method, url, headers, timeout, proxy=None, body=None):
            calls.append(url)
            return 302, 'Found', {'Location': 'https://evil.example/leak'}, b'', url
        session = sc.FetcherSession(transport=transport, retries=1)
        outcomes, _ = cc.collect_comments([PTT], session=session, now=NOW,
            autothrottle=False, max_blocked_retries=0, download_delay=0)
        self.assertEqual(outcomes['PTT']['attempt']['status'], 'http_302')
        self.assertEqual(calls, [PTT['url']])

    def test_demo_cli_and_verdict_do_not_claim_real_fetch(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'demo.json')
            with (mock.patch.object(sys, 'argv', ['community_data.py', '--demo', '--json', path,
                                                 '--market-data', os.path.join(tmp, 'missing.json')]),
                  contextlib.redirect_stdout(out)):
                cd.main()
            with open(path, encoding='utf-8') as f:
                demo = json.load(f)
        self.assertIn('演示记录 49/49', out.getvalue())
        self.assertNotIn('实抓成功', out.getvalue())
        self.assertIn('演示文本', demo['communities'][0]['verdict'])
        self.assertNotIn('实抓', demo['communities'][0]['verdict'])
        self.assertNotIn('抓取核验', demo['communities'][0]['meta'])
        self.assertEqual(wp.CHANNEL_READ_RE.findall(demo['communities'][0]['meta']), [])
        self.assertEqual(wp.CHANNEL_READ_RE.findall('演示样本 · 抓取核验 2026-10-30'), [])
        self.assertNotIn('核验日期', bs._community_fetch_status(demo))

    def test_limit_does_not_claim_unrequested_sources_were_checked(self):
        first = cd.COMMUNITIES[0]
        pages = {first['url']: '<html><body><h1>恒指與港股資金有關的討論</h1></body></html>'}
        session = sc.FetcherSession(transport=transport_for(pages), retries=1)
        with tempfile.TemporaryDirectory() as tmp:
            data = cd.generate_all_communities(market={}, now=NOW, limit=1, session=session,
                storage_file=os.path.join(tmp, 'a.db'), verbose=False, autothrottle=False,
                max_blocked_retries=0, download_delay=0)
        self.assertEqual(data['summary']['ok'], 1)
        self.assertIn('调试限制未请求此源', data['communities'][1]['meta'])
        self.assertNotIn('抓取核验', data['communities'][1]['meta'])
        self.assertEqual(data['communities'][1]['fetch_date'], '')

    def test_rendering_escapes_comments_and_rejects_unsafe_urls(self):
        comm = dict(LIHKG, available=True, verdict_class='neutral', verdict_label='中性',
                    quote='<script>alert(1)</script>', verdict='', quant=None,
                    content_kind='comment', source_url='javascript:alert(1)', matched_comments=2,
                    comments=[{'text': 'first', 'published_label': 'today', 'scope': 'sample'},
                              {'text': '<img src=x onerror=alert(1)>', 'post_title': '" onload="x',
                               'url': 'https://example.com/"bad', 'metric': {'label': '<bad>'}}])
        for rendered in (bs.build_community_html([comm]), wp.community_card(comm, level='full')):
            self.assertNotIn('<script>', rendered)
            self.assertNotIn('<img src=x', rendered)
            self.assertNotIn('href="javascript:', rendered)
            self.assertIn('&lt;img', rendered)
            self.assertIn('&quot;bad', rendered)


if __name__ == '__main__':
    unittest.main()
