#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small, bounded community-comment adapters used by community_spider.

The PTT and LIHKG adapters use the same scheduler, per-host throttling and
optional robots policy as the existing scraper. Reddit uses the optional PRAW
SDK and its own API rate limiting. No login wall or CAPTCHA is bypassed.
See docs/community-comment-strategies.md for sources, limitations and licences.
"""
from __future__ import annotations

import email.utils
import itertools
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urljoin, urlparse

import scrapling_core as sc
import community_spider as common
import community_data as cd

MAX_THREADS = 3
MAX_COMMENTS_PER_THREAD = 20
MAX_DISPLAY_COMMENTS = 3
COMMENT_MAX_AGE_DAYS = 7
POST_INTERVAL_SECONDS = 10.0
LIHKG_LIST_URL = 'https://lihkg.com/api_v2/thread/category?cat_id=15&page=1&count=20&type=now'
LIHKG_HEADERS = {'Accept': 'application/json', 'X-LI-DEVICE-TYPE': 'browser'}
BOGLEHEADS_BOARD = 'https://www.bogleheads.org/forum/viewforum.php?f=10'
DISCUSS_HOSTS = ('www.discuss.com.hk', 'discuss.com.hk', 'finance.discuss.com.hk')
REDDIT_SUBREDDITS = {
    'REDDIT': 'ChinaStocks', 'RINVESTING': 'investing',
    'ROPTIONS': 'options', 'RCRYPTO': 'CryptoCurrency',
}


class PostInterval:
    """Serialize post-detail fetches with a cooldown after each attempt.

    The same instance is shared by PTT, LIHKG and PRAW during a collection.
    Holding the lock prevents overlapping post fetches; each new attempt waits
    at least the configured interval after the previous one finished, including
    on exceptions and transport-level retries.
    """

    def __init__(self, interval=POST_INTERVAL_SECONDS, clock=None, sleep=None):
        self.interval = max(0.0, float(interval))
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()
        self._last_finished = None
        self.started = 0

    def call(self, func, *args, **kwargs):
        with self._lock:
            if self._last_finished is not None:
                remaining = self.interval - (self._clock() - self._last_finished)
                if remaining > 0:
                    self._sleep(remaining)
            self.started += 1
            try:
                return func(*args, **kwargs)
            finally:
                self._last_finished = self._clock()


def _host(url):
    try:
        return (urlparse(str(url or '')).hostname or '').lower()
    except ValueError:
        return ''


def _is_ptt(comm):
    url = comm.get('url', '')
    return (comm.get('key') == 'PTT' and _host(url) in ('www.ptt.cc', 'ptt.cc')
            and urlparse(url).path == '/bbs/Stock/index.html')


def _is_lihkg(comm):
    url = comm.get('url', '')
    return (comm.get('key') == 'LIHKG' and _host(url) in ('lihkg.com', 'www.lihkg.com')
            and urlparse(url).path.rstrip('/') == '/category/15')


def _is_bogleheads(comm):
    url = comm.get('url', '')
    return (comm.get('key') == 'BOGLEHEADS' and _host(url) in ('www.bogleheads.org', 'bogleheads.org')
            and urlparse(url).path == '/forum/index.php')


def _is_discuss(comm):
    url = comm.get('url', '')
    parsed = urlparse(url)
    return (comm.get('key') == 'DISCUSS' and _host(url) in DISCUSS_HOSTS
            and parsed.path == '/forumdisplay.php' and parse_qs(parsed.query).get('fid') == ['115'])


def _is_reddit(comm):
    name = REDDIT_SUBREDDITS.get(comm.get('key'))
    url = comm.get('url', '')
    return bool(name and _host(url) in ('www.reddit.com', 'reddit.com', 'old.reddit.com')
                and urlparse(url).path.rstrip('/').lower() == f'/r/{name}'.lower())


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r'-?\d+', value.strip()):
        return int(value.strip())
    return None


def _now_utc(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _recent_timestamp(value, now, max_age_days):
    """Return the actual UTC timestamp only if it is in the sample window."""
    try:
        if isinstance(value, bool):
            return ''
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return ''
    if dt < now - timedelta(days=max_age_days) or dt > now + timedelta(minutes=5):
        return ''
    return dt.isoformat(timespec='seconds').replace('+00:00', 'Z')


def _clean_message(value, is_html=False):
    text = str(value or '')
    if is_html:
        text = sc.Selector(content=text).get_all_text(separator=' ', strip=True)
    return common.strip_noise(text)


def _useful_comment(text, title):
    """Topic may be in the parent title; the comment itself must be substantive."""
    text = common.strip_noise(text)
    if len(text) < 6 or text.lower() in ('[deleted]', '[removed]'):
        return False
    if common.NOISE_PATTERNS.match(text) or any(x in text.lower() for x in common.NOISE_SUBSTRINGS):
        return False
    return common.count_topic_hits(f'{title} {text}') > 0


def _first_text(node, selector):
    found = node.css(selector)
    return common.strip_noise(found.first.get_all_text(separator=' ', strip=True)) if found else ''


def _ptt_score(value):
    """PTT's index displays net pushes, not an individual comment's likes."""
    value = str(value or '').strip()
    if value == '爆':
        return 100, '≥100', True
    number = _number(value)
    if number is None or number <= 0:
        return None
    return number, str(number), False


def ptt_thread_choices(response):
    """Select at most three same-board links with a visible positive push count."""
    threads = []
    for index, row in enumerate(response.css('.r-ent')):
        link = row.css('.title a')
        score = _ptt_score(_first_text(row, '.nrec'))
        if not link or not score:
            continue
        title = common.strip_noise(link.first.get_all_text(separator=' ', strip=True))[:120]
        url = urljoin(response.url, link.first.attrib.get('href') or '')
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or _host(url) not in ('www.ptt.cc', 'ptt.cc')
                or not re.fullmatch(r'/bbs/Stock/M\.[A-Za-z0-9.]+\.html', parsed.path)
                or parsed.query or parsed.fragment or not title):
            continue
        threads.append({'url': url, 'title': title, 'rank': score[0],
                        'display': score[1], 'lower_bound': score[2], 'index': index})
    threads.sort(key=lambda x: (-x['rank'], x['index']))
    # A duplicate listing must not schedule the same thread twice.
    unique = {}
    for thread in threads:
        unique.setdefault(thread['url'], thread)
    return list(unique.values())[:MAX_THREADS]


def parse_ptt_replies(response, thread):
    comments = []
    for index, push in enumerate(itertools.islice(response.css('.push'), MAX_COMMENTS_PER_THREAD)):
        tag = _first_text(push, '.push-tag')
        if tag not in ('推', '噓', '→'):
            continue
        text = _first_text(push, '.push-content').lstrip(':： ').strip()
        if not _useful_comment(text[:common.MAX_SNIPPET], thread['title']):
            continue
        # push-ipdatetime can contain an IP. Keep only the month/day and time;
        # no year/timezone is provided, so do not invent an ISO timestamp.
        time_text = _first_text(push, '.push-ipdatetime')
        match = re.search(r'\b(\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2})\b', time_text)
        comments.append({
            'text': text[:common.MAX_SNIPPET], 'text_truncated': len(text) > common.MAX_SNIPPET,
            'post_title': thread['title'], 'url': thread['url'], 'published_at': '', 'http_status': response.status,
            'response_bytes': len(response.body or b''),
            'published_label': (match.group(1) + '（年份未知）') if match else '发布时间未知',
            'kind': 'thread_reply', 'strategy': 'ptt_html',
            'metric': {'type': 'thread_net_pushes', 'value': thread['rank'],
                       'lower_bound': thread['lower_bound'],
                       'label': f"所属帖推噓净数 {thread['display']}（非回复点赞）"},
            'scope': 'Stock 列表本页最多 3 个高推帖，各取前 20 条回复；不代表全站排行',
            '_rank': thread['rank'], '_order': index,
        })
    return comments


def _json_response(response):
    try:
        data = json.loads(response.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    if not isinstance(data, dict):
        return None
    # Different versions return either success or error_code. Do not treat an
    # error payload that happens to have HTTP 200 as a successful response.
    if data.get('error_code') not in (None, 0, '0', '') or data.get('success') in (False, 0, '0'):
        return None
    return data.get('response') if isinstance(data.get('response'), dict) else None


def lihkg_thread_choices(response):
    data = _json_response(response)
    if data is None or not isinstance(data.get('items'), list):
        return None
    threads = []
    for index, item in enumerate(data['items'][:20]):
        if not isinstance(item, dict):
            continue
        thread_id = str(item.get('thread_id') or '')
        title = _clean_message(item.get('title'), is_html=True)[:120]
        replies = _number(item.get('no_of_reply'))
        if not re.fullmatch(r'\d+', thread_id) or not title or replies is None or replies <= 0:
            continue
        threads.append({'id': thread_id, 'title': title, 'replies': replies, 'index': index})
    threads.sort(key=lambda x: (-x['replies'], x['index']))
    unique = {}
    for thread in threads:
        unique.setdefault(thread['id'], thread)
    return list(unique.values())[:MAX_THREADS]


def parse_lihkg_replies(response, thread, now=None, max_age_days=COMMENT_MAX_AGE_DAYS):
    data = _json_response(response)
    if data is None or not isinstance(data.get('item_data'), list):
        return None
    now = _now_utc(now)
    comments = []
    for index, item in enumerate(data['item_data'][:MAX_COMMENTS_PER_THREAD]):
        if not isinstance(item, dict):
            continue
        # msg_num=1 is the original post, not a reply. Unknown numbers cannot
        # be safely labelled as replies either.
        number = _number(item.get('msg_num'))
        likes = _number(item.get('like_count'))
        published = _recent_timestamp(item.get('reply_time'), now, max_age_days)
        text = _clean_message(item.get('msg'), is_html=True)
        if (number is None or number <= 1 or likes is None or likes < 0 or not published
                or item.get('display_vote') is False
                or not _useful_comment(text[:common.MAX_SNIPPET], thread['title'])):
            continue
        comments.append({
            'text': text[:common.MAX_SNIPPET], 'text_truncated': len(text) > common.MAX_SNIPPET,
            'post_title': thread['title'], 'url': f"https://lihkg.com/thread/{thread['id']}/page/1",
            'published_at': published, 'published_label': published, 'http_status': response.status,
            'response_bytes': len(response.body or b''),
            'kind': 'comment', 'strategy': 'lihkg_api',
            'metric': {'type': 'comment_likes', 'value': likes, 'label': f'评论点赞 {likes}'},
            'scope': '财经台列表本页最多 3 个高回复帖的第 1 页、每帖前 20 楼；按样本内点赞排序',
            '_rank': likes, '_order': index,
        })
    return comments


def _thread_url(href, base, hosts, path, id_field, allowed_forum=None):
    """Allow only a same-site thread ID; discard tracking and pagination URLs."""
    try:
        url = urljoin(base, str(href or ''))
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        ident = query.get(id_field, [])
        if (parsed.scheme != 'https' or _host(url) not in hosts or parsed.path != path
                or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)
                or len(ident) != 1 or not re.fullmatch(r'\d+', ident[0])):
            return None
        if allowed_forum and query.get('f', [allowed_forum]) != [allowed_forum]:
            return None
        return parsed.hostname, ident[0]
    except ValueError:
        return None


def _reply_count(node, selector):
    # Accept an explicit numeric counter only; a missing/abbreviated number is
    # not a reason to invent hotness or treat a read-only announcement as a post.
    text = _first_text(node, selector).replace(',', '')
    match = re.fullmatch(r'(?:(?:Replies?|回复|回覆)\s*)?(\d+)(?:\s*(?:Replies?|回复|回覆))?',
                         text, flags=re.I)
    return int(match.group(1)) if match else None


def bogleheads_thread_choices(response):
    """phpBB board: up to three same-board threads by visible reply count."""
    threads = []
    for index, row in enumerate(itertools.islice(response.css('li.row'), 50)):
        link = row.css('a.topictitle')
        replies = _reply_count(row, '.posts')
        if not link or replies is None or replies <= 0:
            continue
        title = common.strip_noise(link.first.get_all_text(separator=' ', strip=True))[:120]
        parsed = _thread_url(link.first.attrib.get('href'), response.url,
                             ('www.bogleheads.org', 'bogleheads.org'), '/forum/viewtopic.php', 't', '10')
        if not parsed or not title:
            continue
        _, ident = parsed
        # If the board exposes pagination, read its latest visible page rather
        # than the opening page of a possibly years-old long-running topic.
        last_start = 0
        for page_link in row.css('a[href*="viewtopic.php"]'):
            try:
                page_url = urljoin(response.url, page_link.attrib.get('href') or '')
            except ValueError:
                continue
            page_id = _thread_url(page_url, response.url, ('www.bogleheads.org', 'bogleheads.org'),
                                  '/forum/viewtopic.php', 't', '10')
            if not page_id or page_id[1] != ident:
                continue
            starts = parse_qs(urlparse(page_url).query).get('start', [])
            if len(starts) == 1 and re.fullmatch(r'\d{1,7}', starts[0]):
                last_start = max(last_start, int(starts[0]))
        suffix = f'&start={last_start}' if last_start else ''
        threads.append({'id': ident, 'title': title, 'replies': replies, 'index': index,
                        'later_page': bool(last_start),
                        'url': f'https://www.bogleheads.org/forum/viewtopic.php?f=10&t={ident}{suffix}'})
    threads.sort(key=lambda t: (-t['replies'], t['index']))
    unique = {}
    for thread in threads:
        unique.setdefault(thread['id'], thread)
    return list(unique.values())[:MAX_THREADS]


def discuss_thread_choices(response):
    """Discuz board: up to three thread IDs with explicit visible reply counts."""
    threads = []
    for index, row in enumerate(itertools.islice(response.css('tbody[id^="normalthread_"]'), 50)):
        replies = _reply_count(row, 'td.nums strong')
        if replies is None or replies <= 0:
            continue
        for link in row.css('th.subject a'):
            title = common.strip_noise(link.get_all_text(separator=' ', strip=True))[:120]
            parsed = _thread_url(link.attrib.get('href'), response.url, DISCUSS_HOSTS, '/viewthread.php', 'tid')
            if not parsed or not title:
                continue
            host, ident = parsed
            if row.attrib.get('id') != f'normalthread_{ident}':
                continue
            threads.append({'id': ident, 'title': title, 'replies': replies, 'index': index,
                            'url': f'https://{host}/viewthread.php?tid={ident}&page=1'})
            break
    threads.sort(key=lambda t: (-t['replies'], t['index']))
    unique = {}
    for thread in threads:
        unique.setdefault(thread['id'], thread)
    return list(unique.values())[:MAX_THREADS]


def _own_post_text(selector):
    """Exclude embedded quotes and hidden/script blocks from the author's text."""
    pieces = []
    def walk(node):
        classes = set((node.attrib.get('class') or '').lower().split())
        style = (node.attrib.get('style') or '').lower().replace(' ', '')
        if (node.tag.lower() in ('blockquote', 'script', 'style', 'noscript', 'svg', 'iframe')
                or classes.intersection(('quote', 'quotecontent', 'hidecontent', 'locked'))
                or 'display:none' in style or 'visibility:hidden' in style
                or 'hidden' in node.attrib or node.attrib.get('aria-hidden') == 'true'):
            return
        if node.text:
            pieces.append(node.text)
        for child in node.children:
            walk(child)
            if child.tail:
                pieces.append(child.tail)
    walk(selector._root)
    return common.strip_noise(' '.join(pieces))


def parse_forum_replies(response, thread, strategy):
    """Parse bounded phpBB/Discuz replies; never claim per-reply popularity."""
    if strategy == 'phpbb_html':
        posts, id_pattern = response.css('div.post'), r'p(\d+)'
        content_selector = '.content'
    elif strategy == 'discuz_html':
        posts, id_pattern = response.css('div[id^="postmessage_"], td[id^="postmessage_"]'), r'postmessage_(\d+)'
        content_selector = None
    else:
        return []
    comments = []
    # Skip the first visible post conservatively: on page 1 it is the opening
    # message; on a later page it can be a reply, which is merely omitted.
    for index, post in enumerate(itertools.islice(posts, 1, MAX_COMMENTS_PER_THREAD + 1)):
        match = re.fullmatch(id_pattern, str(post.attrib.get('id') or ''))
        body = post.css(content_selector).first if content_selector else post
        if not match or body is None:
            continue
        text = _own_post_text(body)
        if not _useful_comment(text[:common.MAX_SNIPPET], thread['title']):
            continue
        post_id = match.group(1)
        fragment = f'p{post_id}' if strategy == 'phpbb_html' else f'pid{post_id}'
        comments.append({
            'text': text[:common.MAX_SNIPPET], 'text_truncated': len(text) > common.MAX_SNIPPET,
            'post_title': thread['title'], 'url': f"{thread['url']}#{fragment}",
            'published_at': '', 'published_label': '发布时间未知',
            'http_status': response.status, 'response_bytes': len(response.body or b''),
            'kind': 'thread_reply', 'strategy': strategy,
            'metric': {'type': 'thread_reply_count', 'value': thread['replies'],
                       'label': f"所属帖回复数 {thread['replies']}（非回复点赞）"},
            'scope': ('当前版块列表前 50 条中最多 3 个高回复帖，各取可见末页前 20 条回复；不代表全站排行'
                      if thread.get('later_page') else
                      '当前版块列表前 50 条中最多 3 个高回复帖，各取第一页前 20 条回复；不代表全站排行'),
            '_rank': thread['replies'], '_order': index,
        })
    return comments


def _finish_candidates(items):
    """Deterministic within-source sorting and deduplication; never mix metrics across sites."""
    ordered = sorted(items, key=lambda x: (-x['_rank'], x['url'], x['_order'], x['text']))
    seen, selected = set(), []
    for item in ordered:
        fingerprint = (item['url'], re.sub(r'\s+', ' ', item['text']).casefold())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        selected.append({k: v for k, v in item.items() if not k.startswith('_')})
    return selected[:MAX_DISPLAY_COMMENTS], len(seen)


class CommentSpider(sc.Spider):
    """Two-stage public-page/API strategies, reusing the project's crawler engine."""

    name = 'community_comments'
    concurrent_requests = 2
    concurrent_requests_per_domain = 1
    download_delay = 0.15
    request_timeout = 10.0
    request_retries = 2
    max_blocked_retries = 2
    autothrottle_enabled = True
    autothrottle_start_delay = 0.5
    autothrottle_max_delay = 8.0
    robots_txt_obey = False

    def __init__(self, communities, session=None, now=None, max_age_days=COMMENT_MAX_AGE_DAYS,
                 post_limiter=None):
        self.communities = list(communities)
        self.now = _now_utc(now)
        self.max_age_days = max_age_days
        self.candidates = {}
        self.attempts = {}
        self.post_limiter = post_limiter or PostInterval()
        session = session or sc.FetcherSession(timeout=self.request_timeout, retries=self.request_retries)
        self._detail_base_transport = session.config.get('transport') or sc._default_transport
        super().__init__(session=session)

    def _detail_transport(self, *args, **kwargs):
        return self.post_limiter.call(self._detail_base_transport, *args, **kwargs)

    def start_requests(self):
        for comm in self.communities:
            key, url = comm.get('key'), comm.get('url', '')
            if _is_ptt(comm):
                self.attempts[key] = {'strategy': 'ptt_html', 'status': 'no_comments'}
                yield sc.Request(url, callback=self.parse_ptt_index, meta={'key': key, 'stage': 'index'},
                                 follow_redirects=False)
            elif _is_lihkg(comm):
                self.attempts[key] = {'strategy': 'lihkg_api', 'status': 'no_comments'}
                yield sc.Request(LIHKG_LIST_URL, callback=self.parse_lihkg_index,
                                 meta={'key': key, 'stage': 'index'}, headers=LIHKG_HEADERS,
                                 follow_redirects=False)
            elif _is_bogleheads(comm):
                self.attempts[key] = {'strategy': 'phpbb_html', 'status': 'no_comments'}
                yield sc.Request(BOGLEHEADS_BOARD, callback=self.parse_forum_index,
                                 meta={'key': key, 'stage': 'index'}, follow_redirects=False)
            elif _is_discuss(comm):
                self.attempts[key] = {'strategy': 'discuz_html', 'status': 'no_comments'}
                yield sc.Request(url, callback=self.parse_forum_index,
                                 meta={'key': key, 'stage': 'index'}, follow_redirects=False)

    def parse(self, response):
        return []

    def parse_ptt_index(self, response):
        if not response.ok or _host(response.url) not in ('www.ptt.cc', 'ptt.cc'):
            self.attempts['PTT']['status'] = f'http_{response.status}'
            return
        for thread in ptt_thread_choices(response):
            yield sc.Request(thread['url'], callback=self.parse_ptt_thread,
                             meta={'key': 'PTT', 'stage': 'thread', 'thread': thread},
                             transport=self._detail_transport, follow_redirects=False)

    def parse_ptt_thread(self, response):
        if response.ok and _host(response.url) in ('www.ptt.cc', 'ptt.cc'):
            thread = response.request.meta['thread']
            self.candidates.setdefault('PTT', []).extend(parse_ptt_replies(response, thread))
        else:
            self.attempts['PTT']['status'] = f'http_{response.status}'
        return []

    def parse_lihkg_index(self, response):
        if not response.ok or _host(response.url) != 'lihkg.com':
            self.attempts['LIHKG']['status'] = f'http_{response.status}'
            return
        threads = lihkg_thread_choices(response)
        if threads is None:
            self.attempts['LIHKG']['status'] = 'invalid_response'
            return
        for thread in threads:
            url = f"https://lihkg.com/api_v2/thread/{thread['id']}/page/1"
            yield sc.Request(url, callback=self.parse_lihkg_thread,
                             meta={'key': 'LIHKG', 'stage': 'thread', 'thread': thread},
                             headers=LIHKG_HEADERS, transport=self._detail_transport, follow_redirects=False)

    def parse_lihkg_thread(self, response):
        if not response.ok or _host(response.url) != 'lihkg.com':
            self.attempts['LIHKG']['status'] = f'http_{response.status}'
            return []
        thread = response.request.meta['thread']
        comments = parse_lihkg_replies(response, thread, self.now, self.max_age_days)
        if comments is None:
            self.attempts['LIHKG']['status'] = 'invalid_response'
        else:
            self.candidates.setdefault('LIHKG', []).extend(comments)
        return []

    def parse_forum_index(self, response):
        key = response.request.meta['key']
        hosts = ('www.bogleheads.org', 'bogleheads.org') if key == 'BOGLEHEADS' else DISCUSS_HOSTS
        if not response.ok or _host(response.url) not in hosts:
            self.attempts[key]['status'] = f'http_{response.status}'
            return
        choices = bogleheads_thread_choices(response) if key == 'BOGLEHEADS' else discuss_thread_choices(response)
        for thread in choices:
            yield sc.Request(thread['url'], callback=self.parse_forum_thread,
                             meta={'key': key, 'stage': 'thread', 'thread': thread},
                             transport=self._detail_transport, follow_redirects=False)

    def parse_forum_thread(self, response):
        key = response.request.meta['key']
        hosts = ('www.bogleheads.org', 'bogleheads.org') if key == 'BOGLEHEADS' else DISCUSS_HOSTS
        if not response.ok or _host(response.url) not in hosts:
            self.attempts[key]['status'] = f'http_{response.status}'
            return []
        strategy = self.attempts[key]['strategy']
        self.candidates.setdefault(key, []).extend(
            parse_forum_replies(response, response.request.meta['thread'], strategy))
        return []

    def on_error(self, request, error):
        key = (request.meta or {}).get('key')
        if key in self.attempts:
            status = getattr(error, 'status', 0) or 0
            self.attempts[key]['status'] = ('robots_disallowed' if str(error) == 'robots_disallowed' else
                                            f'http_{status}' if status else 'request_failed')

    def results(self):
        results = {}
        for key, attempt in self.attempts.items():
            comments, matched = _finish_candidates(self.candidates.get(key, []))
            if comments:
                attempt['status'] = 'ok' if attempt['status'] == 'no_comments' else 'partial'
            attempt['matched_comments'] = matched
            results[key] = {'comments': comments, 'attempt': dict(attempt)}
        return results


def _reddit_comment_url(permalink, subreddit):
    """Accept only a relative permalink for the expected subreddit and comment."""
    if not isinstance(permalink, str):
        return ''
    match = re.fullmatch(r'/r/([A-Za-z0-9_]+)/comments/[A-Za-z0-9]+/[^/?#]*/[A-Za-z0-9]+/?', permalink)
    if not match or match.group(1).lower() != subreddit.lower():
        return ''
    return 'https://www.reddit.com' + permalink


def reddit_client(timeout):
    """Load PRAW only with an explicitly configured, read-only API application."""
    client_id = os.environ.get('REDDIT_CLIENT_ID', '').strip()
    client_secret = os.environ.get('REDDIT_CLIENT_SECRET', '').strip()
    user_agent = os.environ.get('REDDIT_USER_AGENT', '').strip()
    if not (client_id and client_secret and user_agent):
        return None, 'not_configured'
    try:
        import praw  # optional dependency
    except ImportError:
        return None, 'praw_not_installed'
    try:
        client = praw.Reddit(client_id=client_id, client_secret=client_secret,
                             user_agent=user_agent, requestor_kwargs={'timeout': timeout},
                             check_for_updates=False)
        client.read_only = True
        return client, 'ready'
    except Exception:  # do not log credentials or SDK exceptions
        return None, 'client_error'


def fetch_reddit_comments(communities, timeout=10, now=None, max_age_days=COMMENT_MAX_AGE_DAYS,
                          client=None, post_limiter=None):
    post_limiter = post_limiter or PostInterval()
    targets = [c for c in communities if _is_reddit(c)]
    if not targets:
        return {}
    if client is None:
        client, status = reddit_client(timeout)
    else:
        status = 'ready'
    results = {}
    now = _now_utc(now)
    for comm in targets:
        key, subreddit = comm['key'], REDDIT_SUBREDDITS[comm['key']]
        attempt = {'strategy': 'reddit_praw', 'status': status, 'matched_comments': 0}
        candidates = []
        if client is not None:
            attempt['status'] = 'no_comments'
            had_error = False
            try:
                submissions = client.subreddit(subreddit).hot(limit=MAX_THREADS)
                for submission in itertools.islice(submissions, MAX_THREADS):
                    try:
                        title = common.strip_noise(getattr(submission, 'title', ''))[:120]
                        submission.comment_sort = 'top'
                        submission.comment_limit = MAX_COMMENTS_PER_THREAD
                        # Accessing .comments is lazy in PRAW. Fetch each post
                        # inside the shared limiter; never call replace_more.
                        comments_for_post = post_limiter.call(
                            lambda: list(itertools.islice(submission.comments, MAX_COMMENTS_PER_THREAD)))
                        for index, comment in enumerate(comments_for_post):
                            text = _clean_message(getattr(comment, 'body', ''))
                            score = _number(getattr(comment, 'score', None))
                            published = _recent_timestamp(getattr(comment, 'created_utc', None), now, max_age_days)
                            url = _reddit_comment_url(getattr(comment, 'permalink', ''), subreddit)
                            if (score is None or getattr(comment, 'score_hidden', False) or not published
                                    or not url or not _useful_comment(text[:common.MAX_SNIPPET], title)):
                                continue
                            candidates.append({
                                'text': text[:common.MAX_SNIPPET], 'text_truncated': len(text) > common.MAX_SNIPPET,
                                'post_title': title, 'url': url,
                                'published_at': published, 'published_label': published,
                                'kind': 'comment', 'strategy': 'reddit_praw',
                                'metric': {'type': 'comment_score', 'value': score,
                                           'label': f'评论站内得分 {score}'},
                                'scope': 'subreddit 热帖前 3 个，每帖取前 20 条顶层评论；按样本内得分排序',
                                '_rank': score, '_order': index,
                            })
                    except Exception:
                        had_error = True
            except Exception:
                had_error = True
            comments, matched = _finish_candidates(candidates)
            attempt['matched_comments'] = matched
            attempt['status'] = ('partial' if had_error else 'ok') if comments else (
                'request_failed' if had_error else 'no_comments')
        else:
            comments = []
        results[key] = {'comments': comments, 'attempt': attempt}
    return results


def collect_comments(communities, session=None, timeout=10, retries=2, concurrency=6,
                     obey_robots=False, autothrottle=True, max_blocked_retries=2,
                     download_delay=0.15, now=None, post_limiter=None):
    """Collect bounded comments. Returns (per-source outcomes, crawler stats)."""
    post_limiter = post_limiter or PostInterval()
    sources = [c for c in communities if _is_ptt(c) or _is_lihkg(c)
               or _is_bogleheads(c) or _is_discuss(c)]
    outcomes, stats = {}, {'requests_count': 0, 'robots_disallowed_count': 0, 'blocked_requests_count': 0}
    if sources:
        spider = CommentSpider(sources, session=session, now=now, post_limiter=post_limiter)
        spider.concurrent_requests = min(max(1, concurrency), len(sources))
        spider.request_timeout = timeout
        spider.request_retries = retries
        spider.max_blocked_retries = max_blocked_retries
        spider.download_delay = download_delay
        spider.autothrottle_enabled = autothrottle
        spider.robots_txt_obey = obey_robots
        try:
            crawl = spider.crawl()
            outcomes = spider.results()
            stats = crawl.stats.to_dict()
        except Exception as exc:  # isolate an engine failure from the PRAW strategy
            outcomes = spider.results()
            for comm in sources:
                key = comm['key']
                strategies = {'PTT': 'ptt_html', 'LIHKG': 'lihkg_api',
                              'BOGLEHEADS': 'phpbb_html', 'DISCUSS': 'discuz_html'}
                outcome = outcomes.setdefault(key, {'comments': [], 'attempt': {
                    'strategy': strategies[key], 'matched_comments': 0}})
                outcome['attempt']['status'] = 'partial' if outcome['comments'] else 'engine_error'
            stats['error'] = type(exc).__name__
    try:
        outcomes.update(fetch_reddit_comments(communities, timeout=timeout, now=now,
                                               post_limiter=post_limiter))
    except Exception as exc:  # do not discard already-collected PTT/LIHKG results
        stats['praw_error'] = type(exc).__name__
        for comm in communities:
            if _is_reddit(comm):
                outcomes[comm['key']] = {'comments': [], 'attempt': {
                    'strategy': 'reddit_praw', 'status': 'client_error', 'matched_comments': 0}}
    stats['attempted_sources'] = len(outcomes)
    stats['comment_sources'] = sum(bool(item['comments']) for item in outcomes.values())
    stats['matched_comments'] = sum(item['attempt']['matched_comments'] for item in outcomes.values())
    stats['attempts'] = {key: item['attempt'] for key, item in outcomes.items()}
    # PRAW manages its own transport; its internal HTTP calls are not in requests_count.
    stats['requests_count_excludes_praw'] = True
    stats['post_interval_seconds'] = post_limiter.interval
    stats['post_fetch_attempts'] = post_limiter.started
    return outcomes, stats


# ---------------------------------------------------------------------------
# RSS 优先策略：对每个源先尝试公开 RSS 订阅，不可用再回退到网站/APP 公开信息流。
# RSS 条目与 HTML 摘录一样，必须命中财经主题词并落在时间窗口内，绝不编造内容。
# ---------------------------------------------------------------------------

def _rss_feed_url(comm):
    """取该源的 RSS 地址：优先条目自带 rss 字段，再查全局 RSS_FEEDS 映射，无则空字符串。"""
    if not isinstance(comm, dict):
        return ''
    return (comm.get('rss') or cd.RSS_FEEDS.get(comm.get('key'), '') or '').strip()


def _parse_rss_date(value, now, max_age_days):
    """把 RSS pubDate / Atom published 解析为 UTC ISO 字符串；超窗或不可解析返回空。"""
    if not value:
        return ''
    try:
        dt = email.utils.parsedate_to_datetime(str(value))
    except (TypeError, ValueError, OverflowError, OSError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except (TypeError, ValueError, OverflowError, OSError):
            return ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    if dt < now - timedelta(days=max_age_days) or dt > now + timedelta(minutes=5):
        return ''
    return dt.isoformat(timespec='seconds').replace('+00:00', 'Z')


def _parse_rss_items(xml_bytes, now, max_age_days=COMMENT_MAX_AGE_DAYS):
    """解析 RSS 2.0 / Atom，返回最多 MAX_THREADS 条命中财经主题词的条目。"""
    if not xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except (ET.ParseError, ValueError, TypeError):
        return None

    def _local(el):
        return el.tag.split('}')[-1] if '}' in el.tag else el.tag

    def _find(el, tag):
        """按本地名查找子元素，兼容带/不带默认命名空间的 RSS 与 Atom。"""
        for child in el:
            if _local(child) == tag:
                return child
        return None

    def _findall(el, tag):
        return [child for child in el if _local(child) == tag]

    channel = _find(root, 'channel')
    items = _findall(channel or root, 'item')
    is_atom = False
    if not items:
        items = _findall(root, 'entry')
        is_atom = True
    if not items:
        return []

    entries = []
    for item in items:
        def _text(tag):
            el = _find(item, tag)
            if el is None or not el.text:
                return ''
            return el.text.strip()

        if is_atom:
            title = _text('title')
            link_el = _find(item, 'link')
            link = link_el.attrib.get('href', '') if link_el is not None else ''
            summary_el = _find(item, 'summary')
            summary = common.strip_noise(summary_el.text) if (summary_el is not None and summary_el.text) else ''
            published = _text('published') or _text('updated')
        else:
            title = _text('title')
            link = _text('link')
            summary = common.strip_noise(_text('description'))
            published = (_text('pubDate') or _text('dc:date') or _text('lastBuildDate') or '')
        if not title or not link:
            continue
        text = common.strip_noise(f'{title} {summary}')
        if common.count_topic_hits(text) <= 0:
            continue
        published_at = _parse_rss_date(published, now, max_age_days)
        if not published_at:
            continue
        entries.append({
            'title': title[:120],
            'url': link,
            'text': text[:common.MAX_SNIPPET],
            'published_at': published_at,
            'published_label': published_at or '发布时间未知',
        })
    entries.sort(key=lambda x: x['published_at'] or '', reverse=True)
    return entries[:MAX_THREADS]


def fetch_rss_feed(comm, session, timeout=10, now=None, max_age_days=COMMENT_MAX_AGE_DAYS,
                   post_limiter=None):
    """按 RSS 优先策略抓取单个源。返回与 collect_comments 一致的 {comments, attempt} 结构。

    无 RSS 地址 → status 'no_rss'（调用方应回退到网页/APP 公开信息流）；
    网络/解析失败 → 显式失败状态，不编造条目；
    成功 → 返回命中财经主题词的 RSS 条目，strategy='rss_feed'。
    """
    key = comm.get('key')
    rss_url = _rss_feed_url(comm)
    attempt = {'strategy': 'rss_feed', 'matched_comments': 0}
    if not rss_url:
        attempt['status'] = 'no_rss'
        return {'comments': [], 'attempt': dict(attempt)}

    now = _now_utc(now)
    post_limiter = post_limiter or PostInterval()
    transport = session.config.get('transport') or sc._default_transport

    def _do_fetch():
        return transport('GET', rss_url, {}, timeout)

    try:
        status, reason, headers, body, final_url = post_limiter.call(_do_fetch)
    except Exception as exc:  # 网络错误不阻断整体，回退到公开信息流
        attempt['status'] = 'request_failed'
        return {'comments': [], 'attempt': dict(attempt)}

    if not body:
        attempt['status'] = 'empty_response'
        return {'comments': [], 'attempt': dict(attempt)}

    entries = _parse_rss_items(body, now, max_age_days)
    if entries is None:
        attempt['status'] = 'invalid_response'
        return {'comments': [], 'attempt': dict(attempt)}

    comments = []
    for index, entry in enumerate(entries):
        comments.append({
            'text': entry['text'],
            'text_truncated': len(entry['text']) >= common.MAX_SNIPPET,
            'post_title': entry['title'],
            'url': entry['url'],
            'published_at': entry['published_at'],
            'published_label': entry['published_label'],
            'http_status': status,
            'response_bytes': len(body or b''),
            'kind': 'thread_reply',
            'strategy': 'rss_feed',
            'metric': {'type': 'rss_item', 'value': 0,
                       'label': 'RSS 订阅条目（非回复点赞）'},
            'scope': 'RSS 订阅源最新条目，命中财经主题词方入库，不代表全站排行',
            '_rank': 0, '_order': index,
        })
    comments, matched = _finish_candidates(comments)
    attempt['matched_comments'] = matched
    attempt['status'] = 'ok' if comments else 'no_comments'
    return {'comments': comments, 'attempt': dict(attempt)}
