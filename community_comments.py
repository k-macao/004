#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small, bounded community-comment adapters used by community_spider.

The PTT and LIHKG adapters use the same scheduler, per-host throttling and
optional robots policy as the existing scraper. Reddit uses the optional PRAW
SDK and its own API rate limiting. No login wall or CAPTCHA is bypassed.
See docs/community-comment-strategies.md for sources, limitations and licences.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import scrapling_core as sc
import community_spider as common

MAX_THREADS = 3
MAX_COMMENTS_PER_THREAD = 20
MAX_DISPLAY_COMMENTS = 3
COMMENT_MAX_AGE_DAYS = 7
POST_INTERVAL_SECONDS = 10.0
LIHKG_LIST_URL = 'https://lihkg.com/api_v2/thread/category?cat_id=15&page=1&count=20&type=now'
LIHKG_HEADERS = {'Accept': 'application/json', 'X-LI-DEVICE-TYPE': 'browser'}
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
    sources = [c for c in communities if _is_ptt(c) or _is_lihkg(c)]
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
                outcome = outcomes.setdefault(key, {'comments': [], 'attempt': {
                    'strategy': 'ptt_html' if key == 'PTT' else 'lihkg_api', 'matched_comments': 0}})
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
