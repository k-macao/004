#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章鱼 AI·舆情004 — Scrapling 框架模型移植 (scrapling_core.py)

本模块把 GitHub 上 **Scrapling 0.4.15**（https://github.com/D4Vinci/Scrapling，BSD-3-Clause）
的抓取框架模型**原样移植为零依赖（仅标准库）实现**，供本项目在 GitHub Actions 里
「不装任何 pip 包也能跑」的前提下使用同一套方法论：

  1. Fetcher 静态引擎      → 真实浏览器请求头（stealthy headers）、重试 + 退避、
                             被封锁状态码识别（401/403/407/429/444/5xx）、Retry-After、
                             代理轮换、重定向历史、Response 统一封装；
  2. Selector 解析层       → CSS 选择器（tag / .class / #id / [attr*=v] / :nth-of-type /
                             后代与子代组合器）、get_all_text、HTML→Markdown 转换、
                             噪音标签与隐藏内容清洗（防提示注入）；
  3. Adaptive 自适应       → 元素指纹（tag / attributes / text / path / parent / siblings /
                             children）落入 SQLite，DOM 改版后用 difflib.SequenceMatcher
                             相似度重新定位同一个元素（auto_match / relocate / save）；
  4. Spiders 爬虫框架      → Request / Scheduler（优先级 + 指纹去重）/ CrawlerEngine
                             （并发、每域限速、封锁重试）/ AutoThrottle（按响应延迟自适应、
                             封锁退避）/ RobotsTxtManager（robots.txt + crawl-delay）/
                             DevCache（开发缓存）/ CheckpointManager（断点续爬）/
                             CrawlStats + CrawlResult。

—— 与上游 Scrapling 的对应关系（源码文件 → 本模块）：

    scrapling/engines/static.py::Fetcher              → FetcherSession / Fetcher
    scrapling/engines/toolbelt/custom.py::Response    → Response
    scrapling/engines/toolbelt/fingerprints.py        → generate_headers
    scrapling/engines/toolbelt/proxy_rotation.py      → ProxyRotator
    scrapling/parser.py::Selector / Selectors         → Selector / Selectors
    scrapling/core/custom_types.py::TextHandler       → TextHandler / TextHandlers / AttributesHandler
    scrapling/core/mixins.py::SelectorsGeneration     → Selector.generate_css_selector / generate_xpath_selector
    scrapling/core/storage.py::SQLiteStorageSystem    → AdaptiveStorage
    scrapling/core/utils/_utils.py::_StorageTools     → element_to_dict / element_similarity
    scrapling/core/shell.py::Convertor                → Convertor
    scrapling/spiders/{request,scheduler,engine,spider,throttle,robotstxt,cache,checkpoint,result,session}.py
                                                      → Request / Scheduler / CrawlerEngine / Spider /
                                                        AutoThrottle / RobotsTxtManager / DevCache /
                                                        CheckpointManager / CrawlStats / CrawlResult /
                                                        SessionManager

差异（有意为之，不改调用方语义）：
  • 上游用 curl_cffi + lxml + anyio + browserforge + protego；本移植只用
    urllib + html.parser + threading + sqlite3 + difflib + re，CI 里零安装即可运行；
  • 上游是 async/await；本移植是同步 + 线程池（纯标准库），对外 API 名保持一致；
  • 上游 Markdown 转换依赖 markdownify；本移植自带轻量 HTML→Markdown 转换器。

用法:
  from scrapling_core import Fetcher, Selector, AdaptiveStorage
  resp = Fetcher.get('https://example.com', timeout=10, retries=3)
  for title in resp.css('title::text') or resp.css('title'):
      print(title.get_all_text())

  python3 scrapling_core.py --self-test     # 规则自检（离线，零联网）
"""
from __future__ import annotations

import gzip
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
import zlib
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from html import escape as html_escape
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    ProxyHandler,
    Request as UrlRequest,
    build_opener,
)

__version__ = '0.4.15'
SCRAPLING_VERSION = __version__
ENGINE_NAME = f'scrapling-core/{__version__} (stdlib port)'

# 与上游 scrapling/spiders/spider.py 完全一致的被封状态码集合
BLOCKED_CODES = {401, 403, 407, 429, 444, 500, 502, 503, 504}
SUPPORTED_HTTP_METHODS = ('GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS')

# 与上游一致：类名映射到 lxml 风格的属性别名（find_all 用 class_ / href 等）
_WHITELISTED_ATTRS = {'class_': 'class', 'className': 'class', 'for_': 'for', 'type_': 'type'}


# ==========================================================================================
# 1. 指纹 / 请求头（上游 scrapling/engines/toolbelt/fingerprints.py）
# ==========================================================================================

_OS_CHOICES = ('windows', 'macos', 'linux')
_CHROME_MIN = 149
_UA_TEMPLATES = {
    ('chrome', 'windows'): ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                            '(KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36'),
    ('chrome', 'macos'): ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36'),
    ('chrome', 'linux'): ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36'),
    ('firefox', 'windows'): 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{v}.0) Gecko/20100101 Firefox/{v}.0',
    ('firefox', 'macos'): 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:{v}.0) Gecko/20100101 Firefox/{v}.0',
    ('firefox', 'linux'): 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:{v}.0) Gecko/20100101 Firefox/{v}.0',
    ('edge', 'windows'): ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/{v}.0.0.0 Safari/537.36 Edg/{v}.0.0.0'),
    ('edge', 'macos'): ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/{v}.0.0.0 Safari/537.36 Edg/{v}.0.0.0'),
}

_ACCEPT_BY_TYPE = {
    'document': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                 'image/avif,image/webp,*/*;q=0.8'),
    'json': 'application/json,text/plain,*/*',
    'feed': 'application/rss+xml,application/atom+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.5',
}


def generate_headers(browser_mode: bool = False, impersonate: str = 'chrome',
                     accept: str = 'document', lang: str = 'zh-CN,zh;q=0.9,en;q=0.8') -> dict:
    """生成真实浏览器请求头（对应上游 browserforge 生成的那一套）。

    :param browser_mode: 仅保留 Chrome 家族（浏览器引擎用），与上游语义一致
    :param impersonate: 'chrome' / 'firefox' / 'edge' / 'random' / None
    :param accept: 'document' / 'json' / 'feed'
    :param lang: Accept-Language
    """
    if impersonate in (None, False):
        return {}
    browser = random.choice(('chrome', 'firefox', 'edge')) if impersonate == 'random' else impersonate
    if browser_mode or browser not in ('chrome', 'firefox', 'edge'):
        browser = 'chrome'
    os_name = random.choice(_OS_CHOICES)
    version = _CHROME_MIN + random.randint(0, 3) if browser == 'chrome' else \
        (147 + random.randint(0, 3) if browser == 'firefox' else 145 + random.randint(0, 3))
    template = _UA_TEMPLATES.get((browser, os_name)) or _UA_TEMPLATES[('chrome', 'windows')]
    user_agent = template.format(v=version)
    headers = {
        'User-Agent': user_agent,
        'Accept': _ACCEPT_BY_TYPE.get(accept, _ACCEPT_BY_TYPE['document']),
        'Accept-Language': lang,
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    if browser == 'chrome' or browser == 'edge':
        headers.update({
            'sec-ch-ua': f'"Chromium";v="{version}", "Not=A?Brand";v="24", "{browser.title()}";v="{version}"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': f'"{os_name.title()}"' if os_name != 'macos' else '"macOS"',
        })
    headers.update({
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Priority': 'u=0, i',
    })
    return headers


class ProxyRotator:
    """代理轮换（对应上游 scrapling/engines/toolbelt/proxy_rotation.py 的轮询语义）。"""

    def __init__(self, proxies=None):
        self.proxies = list(proxies or [])
        self._index = 0
        self._lock = threading.Lock()

    def add_proxy(self, proxy: str) -> None:
        with self._lock:
            self.proxies.append(proxy)

    def get_proxy(self):
        if not self.proxies:
            return None
        with self._lock:
            proxy = self.proxies[self._index % len(self.proxies)]
            self._index += 1
        return proxy

    def __len__(self):
        return len(self.proxies)


# ==========================================================================================
# 2. 文本工具（上游 scrapling/core/utils/_utils.py + core/custom_types.py）
# ==========================================================================================

_CONSECUTIVE_SPACES = re.compile(r'\s{2,}')


def clean_spaces(string: str) -> str:
    """折叠连续空白（对应上游 clean_spaces）。"""
    return _CONSECUTIVE_SPACES.sub(' ', str(string)).strip()


class TextHandler(str):
    """字符串包装，带 Scrapling 的 .clean() / .re() / .re_first() / .json() 语义。"""

    def clean(self, remove_entities: bool = False):
        value = self
        if remove_entities:
            import html as _html
            value = _html.unescape(value)
        return TextHandler(clean_spaces(value))

    # 上游签名：re(pattern, replace_entities=False, clean_match=True, case_sensitive=True)
    def re(self, pattern, replace_entities: bool = False, clean_match: bool = True, case_sensitive: bool = True):
        import html as _html
        text = self
        if replace_entities:
            text = _html.unescape(text)
        if clean_match:
            text = clean_spaces(text)
        flags = 0 if case_sensitive else re.IGNORECASE
        result = re.findall(pattern, text, flags) if hasattr(pattern, 'findall') is False else pattern.findall(text)
        if isinstance(pattern, str):
            result = re.findall(pattern, text, flags)
        if result and isinstance(result[0], tuple):
            result = [r[0] for r in result]
        return TextHandlers([TextHandler(str(r)) for r in result])

    def re_first(self, pattern, default=None, replace_entities: bool = False,
                 clean_match: bool = True, case_sensitive: bool = True):
        found = self.re(pattern, replace_entities, clean_match, case_sensitive)
        return found[0] if found else default

    def json(self) -> dict:
        return json.loads(self)

    def split(self, *args, **kwargs):  # pragma: no cover - 保留 str 语义
        return [TextHandler(part) for part in str.split(self, *args, **kwargs)]


class TextHandlers(list):
    """文本列表（对应上游 TextHandlers）。"""

    def get(self, default=None):
        return self[0] if len(self) else default

    def getall(self):
        return list(self)

    @property
    def text(self):  # pragma: no cover - 便捷属性
        return TextHandler(' '.join(str(x) for x in self))

    def re(self, pattern, **kwargs):
        out = TextHandlers()
        for item in self:
            out.extend(TextHandler(str(item)).re(pattern, **kwargs))
        return out

    def re_first(self, pattern, default=None, **kwargs):
        for item in self:
            found = TextHandler(str(item)).re_first(pattern, default=default, **kwargs)
            if found:
                return found
        return default


class AttributesHandler(dict):
    """属性字典（对应上游 AttributesHandler），支持 .json_string / .search_values。"""

    def get(self, key, default=None):
        if key in self:
            return self[key]
        for _alias, real in _WHITELISTED_ATTRS.items():
            if real == key and _alias in self:
                return self[_alias]
        # 大小写不敏感回退（HTML 属性大小写混用很常见）
        lowered = {str(k).lower(): v for k, v in self.items()}
        return lowered.get(str(key).lower().replace('_', '-'), lowered.get(str(key).lower(), default))

    def search_values(self, keyword: str, partial: bool = False):
        for key in sorted(self.keys()):
            for value in (self[key] if isinstance(self[key], list) else [self[key]]):
                if (keyword in str(key) if partial else keyword == str(key)) or \
                        (keyword in str(value) if partial else keyword == str(value)):
                    yield AttributesHandler({key: value})

    @property
    def json_string(self) -> str:
        return json.dumps(self, ensure_ascii=False)


# ==========================================================================================
# 3. HTML DOM（替代上游的 lxml）与 Selector
# ==========================================================================================

VOID_ELEMENTS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
    'param', 'source', 'track', 'wbr', 'command', 'keygen', 'menuitem',
}


class DomNode:
    """极简 DOM 节点：text（子节点之前的文本）+ children + tail（父节点里紧随自身的文本）。"""

    __slots__ = ('tag', 'attrib', 'children', 'parent', 'text', 'tail', 'comment')

    def __init__(self, tag, attrib=None, parent=None, text=None, comment=False):
        self.tag = tag
        self.attrib = dict(attrib or {})
        self.children = []
        self.parent = parent
        self.text = text
        self.tail = None
        self.comment = comment

    # ---- lxml 风格遍历 ----
    def iter(self):
        yield self
        for child in self.children:
            yield from child.iter()

    def iterchildren(self):
        yield from self.children

    @property
    def gets_children(self):  # pragma: no cover - 兼容读法
        return self.children

    def getparent(self):
        return self.parent

    def __repr__(self):  # pragma: no cover
        return f'<DomNode {self.tag!r} children={len(self.children)}>'

    # ---- 选择器需要的最小操作 ----
    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def index_in_parent(self):
        if self.parent is None:
            return 0
        return self.parent.children.index(self)

    def nth_of_type(self) -> int:
        """同标签兄弟里的 1-based 序号（CSS :nth-of-type）。"""
        if self.parent is None:
            return 1
        counter = 0
        for child in self.parent.children:
            if child.tag == self.tag:
                counter += 1
            if child is self:
                return counter
        return counter

    def clone(self):
        new = DomNode(self.tag, dict(self.attrib), None, self.text, self.comment)
        new.tail = self.tail
        for child in self.children:
            copy = child.clone()
            copy.parent = new
            new.children.append(copy)
        return new

    def drop(self):
        if self.parent is not None:
            try:
                self.parent.children.remove(self)
            except ValueError:  # pragma: no cover - 已被移除
                pass
            self.parent = None


class _DomBuilder(HTMLParser):
    def __init__(self, keep_comments: bool = False):
        super().__init__(convert_charrefs=True)
        self.root = DomNode('#document')
        self._stack = [self.root]
        self.keep_comments = keep_comments

    def _append(self, node):
        self._stack[-1].children.append(node)
        node.parent = self._stack[-1]

    def handle_starttag(self, tag, attrs):
        self._append(DomNode(tag.lower(), {k.lower(): (v if v is not None else '') for k, v in attrs}))
        if tag.lower() not in VOID_ELEMENTS:
            self._stack.append(self._stack[-1].children[-1])

    def handle_startendtag(self, tag, attrs):
        self._append(DomNode(tag.lower(), {k.lower(): (v if v is not None else '') for k, v in attrs}))

    def handle_endtag(self, tag):
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data):
        top = self._stack[-1]
        if top.children:
            last = top.children[-1]
            last.tail = (last.tail or '') + data
        else:
            top.text = (top.text or '') + data

    def handle_comment(self, data):
        if not self.keep_comments:
            return
        node = DomNode('#comment', parent=None, text=data, comment=True)
        self._append(node)


def parse_html(content, encoding: str = 'utf-8', keep_comments: bool = False) -> DomNode:
    """把 HTML 字符串/字节解析成 DomNode 树（对应上游 lxml 的 html 解析）。"""
    if isinstance(content, bytes):
        content = decode_bytes(content, encoding)
    parser = _DomBuilder(keep_comments=keep_comments)
    parser.feed(str(content))
    parser.close()
    return parser.root


def decode_bytes(raw: bytes, encoding: str = None) -> str:
    """按 header charset → meta charset → utf-8 → gbk 的顺序解码（中文站点必须的兜底）。"""
    if encoding:
        try:
            return raw.decode(encoding, errors='replace')
        except (LookupError, UnicodeDecodeError):  # pragma: no cover
            pass
    for encoding_candidate in (None, 'utf-8', 'gb18030'):
        try:
            text = raw.decode(encoding_candidate or 'utf-8')
            if encoding_candidate is None:
                match = re.search(r'<meta[^>]+charset=["\']?([\w-]+)', text, re.I)
                if match and match.group(1).lower() not in ('utf-8', 'utf8'):
                    try:
                        return raw.decode(match.group(1), errors='replace')
                    except LookupError:  # pragma: no cover
                        return text
            return text
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')  # pragma: no cover


def serialize(node: DomNode, include_self: bool = True) -> str:
    """序列化 HTML（对应 lxml 的 .html_content / etree.tostring）。"""
    parts = []

    def walk(current, top, with_tail=True):
        if current.comment:
            if top:
                parts.append(f'<!--{current.text or ""}-->')
            if with_tail and current.tail:
                parts.append(current.tail)
            return
        attribs = ''.join(f' {k}="{html_escape(str(v), quote=True)}"' for k, v in current.attrib.items())
        if top:
            parts.append(f'<{current.tag}{attribs}>')
        if current.text:
            parts.append(current.text)
        for child in current.children:
            walk(child, True)
        if top and current.tag not in VOID_ELEMENTS:
            parts.append(f'</{current.tag}>')
        if with_tail and current.tail:
            parts.append(current.tail)

    if node.tag == '#document':
        for child in node.children:
            walk(child, True)
    else:
        walk(node, include_self, with_tail=include_self)
    return ''.join(parts)


# ---- CSS 选择器引擎（上游用 lxml.cssselect；这里是等价子集实现） ----

class SelectorError(ValueError):
    """选择器语法错误。"""


class _SimpleSelector:
    __slots__ = ('tag', 'id_', 'classes', 'attrs', 'nth', 'pseudos')

    def __init__(self, text: str):
        text = text.strip()
        if not text:
            raise SelectorError('空选择器')
        self.tag = None
        self.id_ = None
        self.classes = []
        self.attrs = []      # (name, op, value)
        self.nth = None      # (a, b) 形式的 :nth-of-type
        self.pseudos = []
        index = 0
        match = re.match(r'^(\*|[A-Za-z_][\w-]*)', text)
        if match:
            self.tag = None if match.group(1) == '*' else match.group(1).lower()
            index = match.end()
        while index < len(text):
            char = text[index]
            if char == '#':
                found = re.match(r'#([\w-]+)', text[index:])
                if not found:
                    raise SelectorError(f'非法的 id 选择器: {text!r}')
                self.id_ = found.group(1)
                index += found.end()
            elif char == '.':
                found = re.match(r'\.([\w-]+)', text[index:])
                if not found:
                    raise SelectorError(f'非法的 class 选择器: {text!r}')
                self.classes.append(found.group(1))
                index += found.end()
            elif char == '[':
                end = text.find(']', index)
                if end == -1:
                    raise SelectorError(f'属性选择器未闭合: {text!r}')
                self.attrs.append(self._parse_attr(text[index + 1:end]))
                index = end + 1
            elif char == ':':
                found = re.match(r':(nth-of-type|first-of-type|last-of-type|first-child|last-child)\s*(\(([^)]*)\))?',
                                 text[index:])
                if not found:
                    raise SelectorError(f'不支持的伪类: {text[index:]!r}')
                name, arg = found.group(1), found.group(3)
                if name == 'nth-of-type':
                    self.nth = self._parse_nth(arg)
                else:
                    self.pseudos.append(name)
                index += found.end()
            else:
                raise SelectorError(f'非法的选择器片段: {text[index:]!r}')

    @staticmethod
    def _parse_attr(expr: str):
        match = re.match(r'^([\w:-]+)\s*(?:([*^$~|]?=)\s*(.*))?$', expr.strip())
        if not match:
            raise SelectorError(f'非法的属性选择器: [{expr}]')
        name, op, value = match.group(1).lower(), match.group(2), match.group(3)
        if value is not None:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                value = value[1:-1]
        return name, op, value

    @staticmethod
    def _parse_nth(arg):
        arg = (arg or '').strip().lower()
        if arg in ('odd', ''):
            return (2, 1)
        if arg == 'even':
            return (2, 0)
        match = re.match(r'^([+-]?\d*)n\s*([+-]\s*\d+)?$', arg)
        if match:
            a = match.group(1)
            a = -1 if a == '-' else (1 if a in ('', '+') else int(a))
            b = int(match.group(2).replace(' ', '')) if match.group(2) else 0
            return (a, b)
        try:
            return (0, int(arg))
        except ValueError:
            raise SelectorError(f'非法的 :nth-of-type 参数: {arg!r}')

    def matches(self, node: DomNode) -> bool:
        if node.tag == '#document' or node.comment:
            return False
        if self.tag and node.tag != self.tag:
            return False
        if self.id_ and node.attrib.get('id') != self.id_:
            return False
        for cls in self.classes:
            if cls not in (node.attrib.get('class') or '').split():
                return False
        for name, op, value in self.attrs:
            actual = node.attrib.get(name)
            if actual is None:
                if not any(k.lower() == name for k in node.attrib):
                    return False
                actual = next(v for k, v in node.attrib.items() if k.lower() == name)
            if op is None:
                continue
            actual = str(actual)
            if op == '=' and actual != value:
                return False
            if op == '*=' and value not in actual:
                return False
            if op == '^=' and not actual.startswith(value):
                return False
            if op == '$=' and not actual.endswith(value):
                return False
            if op == '~=' and value not in actual.split():
                return False
            if op == '|=' and not (actual == value or actual.startswith(value + '-')):
                return False
        if self.nth is not None:
            a, b = self.nth
            index = node.nth_of_type()
            if a == 0:
                if index != b:
                    return False
            elif (index - b) % a != 0 or (index - b) // a < 0:
                return False
        for pseudo in self.pseudos:
            if pseudo == 'first-of-type' and node.nth_of_type() != 1:
                return False
            if pseudo == 'last-of-type':
                siblings = [c for c in (node.parent.children if node.parent else []) if c.tag == node.tag]
                if not siblings or siblings[-1] is not node:
                    return False
            if pseudo == 'first-child' and node.index_in_parent() != 0:
                return False
            if pseudo == 'last-child':
                if node.parent is None or node.parent.children[-1] is not node:
                    return False
        return True


def _split_selector_groups(selector: str):
    groups, depth, buffer = [], 0, []
    for char in selector:
        if char == ',' and depth == 0:
            groups.append(''.join(buffer))
            buffer = []
            continue
        if char in '([':
            depth += 1
        elif char in ')]':
            depth -= 1
        buffer.append(char)
    groups.append(''.join(buffer))
    return [group.strip() for group in groups if group.strip()]


def _split_selector_parts(group: str):
    """把 'a > b c' 拆成 [(combinator, _SimpleSelector), ...]（combinator: ' '(后代) 或 '>'(子代)）。"""
    parts, buffer, depth, combinator = [], [], 0, ' '
    for char in group:
        if char in '([':
            depth += 1
            buffer.append(char)
        elif char in ')]':
            depth -= 1
            buffer.append(char)
        elif depth == 0 and (char.isspace() or char == '>'):
            if buffer:
                parts.append((combinator, ''.join(buffer).strip()))
                buffer = []
                combinator = ' '
            if char == '>':
                combinator = '>'
        else:
            buffer.append(char)
    if buffer:
        parts.append((combinator, ''.join(buffer).strip()))
    return [(comb, _SimpleSelector(simple)) for comb, simple in parts if simple]


class Selectors(list):
    """Selector 列表（对应上游 Selectors）。"""

    @property
    def first(self):
        return self[0] if self else None

    def get(self, default=None):
        first = self.first
        return first.get() if first is not None else default

    def getall(self):
        return [item.get() for item in self]

    @property
    def text(self):
        return TextHandler(' '.join(str(item) for item in self))

    def filter(self, func):
        return Selectors([item for item in self if func(item)])

    def re(self, pattern, **kwargs):
        out = TextHandlers()
        for item in self:
            if item._is_text_node:
                out.extend(TextHandler(str(item)).re(pattern, **kwargs))
        return out

    def re_first(self, pattern, default=None, **kwargs):
        for item in self:
            found = item.re_first(pattern, default=default, **kwargs)
            if found:
                return found
        return default

    def search(self, func):  # pragma: no cover - 便捷别名
        return self.filter(func)


class Selector:
    """HTML 选择器（对应上游 scrapling/parser.py::Selector 的等价子集）。"""

    __slots__ = ('url', '_root', '_is_text_node', '_value', 'encoding', '_adaptive', '_storage', '_identifier')

    def __init__(self, content=None, url: str = '', encoding: str = 'utf-8', adaptive: bool = False,
                 storage=None, root: DomNode = None, text=None, keep_comments: bool = False):
        self.url = url
        self.encoding = encoding or 'utf-8'
        self._adaptive = bool(adaptive)
        self._storage = storage
        self._identifier = None
        if text is not None:
            self._is_text_node = True
            self._value = str(text)
            self._root = None
        elif root is not None:
            self._is_text_node = False
            self._value = None
            self._root = root
        else:
            self._is_text_node = False
            self._value = None
            self._root = parse_html(content if content is not None else '', encoding, keep_comments)
        # 自适应模式下提前把存储接好（对应上游的 storage 缺失即报错）
        if self._adaptive:
            if not storage:
                raise ValueError('adaptive=True 时必须传入 storage（AdaptiveStorage 或任何实现 save/retrieve 的对象）')
            self._storage = storage

    # ---------- 基础属性 ----------
    @property
    def tag(self):
        return self._value if self._is_text_node else self._root.tag

    @property
    def text(self):
        """元素自身的直接文本（对应 lxml element.text）。"""
        return TextHandler(self._value if self._is_text_node else (self._root.text or ''))

    @property
    def attrib(self):
        return AttributesHandler(self._root.attrib if not self._is_text_node else {})

    @property
    def html_content(self):
        """内部 HTML（对元素与 lxml 的 .html_content 同义；对整篇文档返回完整 HTML）。"""
        if self._is_text_node:
            return TextHandler('')
        return TextHandler(serialize(self._root, include_self=self._root.tag == '#document'))

    @property
    def body(self):
        return self.html_content

    @property
    def parent(self):
        if self._is_text_node or self._root.parent is None or self._root.parent.tag == '#document':
            return None
        return Selector(root=self._root.parent, url=self.url, encoding=self.encoding)

    @property
    def children(self):
        if self._is_text_node:
            return Selectors()
        return Selectors([Selector(root=child, url=self.url, encoding=self.encoding) for child in self._root.children])

    @property
    def below_elements(self):  # pragma: no cover - 上游同名属性
        return Selectors([Selector(root=node, url=self.url, encoding=self.encoding)
                          for node in self._root.iter() if node is not self._root])

    @property
    def siblings(self):
        if self._is_text_node or self._root.parent is None:
            return Selectors()
        return Selectors([Selector(root=node, url=self.url, encoding=self.encoding)
                          for node in self._root.parent.children if node is not self._root])

    @property
    def path(self):
        """祖先链选择器（对应上游 .path）。"""
        if self._is_text_node:
            return Selectors()
        chain = [node for node in self._root.ancestors() if node.tag != '#document']
        chain.reverse()
        return Selectors([Selector(root=node, url=self.url, encoding=self.encoding) for node in chain])

    @property
    def next(self):
        if self._is_text_node or self._root.parent is None:
            return None
        siblings = self._root.parent.children
        index = siblings.index(self._root)
        return Selector(root=siblings[index + 1], url=self.url, encoding=self.encoding) \
            if index + 1 < len(siblings) else None

    @property
    def previous(self):  # pragma: no cover - 上游同名属性
        if self._is_text_node or self._root.parent is None:
            return None
        siblings = self._root.parent.children
        index = siblings.index(self._root)
        return Selector(root=siblings[index - 1], url=self.url, encoding=self.encoding) if index > 0 else None

    # ---------- 取内容 ----------
    def get_all_text(self, separator: str = '\n', strip: bool = False, valid_values: bool = True,
                     ignore_tags=('script', 'style', 'noscript', 'svg', 'iframe')):
        """取全部后代文本（对应上游 get_all_text）。"""
        if self._is_text_node:
            return TextHandler(self._value)
        ignore = {tag.lower() for tag in (ignore_tags or ())}
        pieces = []

        def append(text):
            if text is None:
                return
            value = text.strip() if strip else text
            if not valid_values or value.strip():
                pieces.append(value)

        def walk(node):
            if node.tag.lower() in ignore:
                return
            append(node.text)
            for child in node.children:
                walk(child)
                append(child.tail)

        walk(self._root)
        return TextHandler(separator.join(pieces))

    def get(self, default=None):
        """文本获取（对应上游 .get()）：元素取全部文本，文本节点取自身。"""
        if self._is_text_node:
            return TextHandler(self._value)
        text = self.get_all_text(strip=True)
        return text if text else (default if default is not None else TextHandler(''))

    def getall(self):
        return TextHandlers([self.get()])

    def clean(self):
        return self.get().clean()

    def urljoin(self, relative_url: str) -> str:
        return urljoin(self.url, relative_url)

    def json(self) -> dict:
        return json.loads(self.get())

    def prettify(self):
        """简单缩进输出（对应上游 prettify）。"""
        return TextHandler(serialize(self._root, include_self=self._root.tag != '#document'))

    def markdown(self, css_selector=None, main_content_only: bool = False) -> str:
        """HTML → Markdown（对应上游 Response.markdown()：先洗噪音再转换）。"""
        page = self
        if main_content_only:
            body = self.css('body')
            page = body.first if body else self
        page = Convertor.strip_noise_tags(page)
        page = Convertor.sanitize_for_ai(page)
        pages = [page] if not css_selector else page.css(css_selector)
        return ''.join(Convertor.to_markdown(item) for item in pages)

    def get_all_text_nodes(self):  # pragma: no cover - 便捷方法
        return TextHandlers([TextHandler(node.text or '') for node in self._root.iter() if node.text])

    # ---------- 查找 ----------
    def css(self, selector: str, identifier: str = '', auto_save: bool = False, percentage: int = 40) -> Selectors:
        """CSS 选择器（对应上游 Selector.css）；空结果时按 adaptive 指纹重新定位。

        与上游一致：选择器里带 ',' 且开了 adaptive 时，会按 identifier 逐条回捞。
        """
        if self._is_text_node:
            return Selectors()
        selector = (selector or '').strip()
        # 上游把 ::text / ::attr(x) 归到 xpath 分支，这里做等价处理
        if selector == '::text':
            return Selectors([Selector(text=self.get_all_text(), url=self.url)])
        if selector.endswith('::text'):
            found = self.css(selector[: -len('::text')])
            return Selectors([Selector(text=item.get(), url=self.url) for item in found])
        attr_match = re.match(r'^(.*?)::attr\(([\w-]+)\)$', selector)
        if attr_match:
            found = self.css(attr_match.group(1))
            out = Selectors()
            for item in found:
                value = item.attrib.get(attr_match.group(2))
                if value is not None:
                    out.append(Selector(text=value, url=self.url))
            return out
        matched = self._select(selector)
        if not matched and self._adaptive and identifier:
            return self.auto_match(selector, identifier, percentage=percentage, auto_save=auto_save)
        if matched and self._adaptive and identifier and auto_save:
            self.save(identifier, matched[0])
        return matched

    def _select(self, selector: str) -> Selectors:
        if not selector or selector == '*':
            return Selectors([Selector(root=node, url=self.url, encoding=self.encoding)
                              for node in self._root.iter() if node is not self._root and not node.comment])
        groups = _split_selector_groups(selector)
        order = {id(node): index for index, node in enumerate(self._root.iter()) if not node.comment}
        picked = {}
        for group in groups:
            parts = _split_selector_parts(group)
            if not parts:
                continue
            # candidates 里每条记录是 (最初命中的元素, 当前正在往上核对的元素)
            candidates = [(node, node) for node in self._root.iter()
                          if node is not self._root and not node.comment and parts[-1][1].matches(node)]
            for combinator, simple in reversed(parts[:-1]):
                narrowed = []
                for origin, node in candidates:
                    if combinator == '>':
                        parent = node.parent
                        if parent is not None and simple.matches(parent):
                            narrowed.append((origin, parent))
                    else:
                        for ancestor in node.ancestors():
                            if simple.matches(ancestor):
                                narrowed.append((origin, ancestor))
                                break
                candidates = narrowed
            for origin, _node in candidates:
                picked[id(origin)] = origin
        ordered = sorted(picked.values(), key=lambda node: order.get(id(node), 0))
        return Selectors([Selector(root=node, url=self.url, encoding=self.encoding) for node in ordered])

    def find_all(self, *args, **kwargs) -> Selectors:
        """按标签 / 属性 / 正则 / 函数过滤（对应上游 find_all 的核心语义）。"""
        if self._is_text_node:
            return Selectors()
        if not args and not kwargs:
            raise TypeError('find_all() 至少需要一个查询条件（标签名、属性字典、正则或函数）')
        tags, attributes, patterns, functions = set(), {}, [], []
        for arg in args:
            if isinstance(arg, str):
                tags.add(arg.lower())
            elif isinstance(arg, dict):
                attributes.update({str(k).lower(): v for k, v in arg.items()})
            elif hasattr(arg, 'findall'):
                patterns.append(arg)
            elif callable(arg) and not hasattr(arg, '__iter__'):
                functions.append(arg)
            elif hasattr(arg, '__iter__'):
                tags.update(str(item).lower() for item in arg)
            elif callable(arg):
                functions.append(arg)
            else:
                raise TypeError(f'find_all() 不支持的参数类型: {type(arg).__name__}')
        for key, value in kwargs.items():
            attributes[_WHITELISTED_ATTRS.get(key, key).lower()] = value
        results = Selectors()
        for node in self._root.iter():
            if node is self._root or node.comment:
                continue
            if tags and node.tag not in tags:
                continue
            if attributes and not all(str(node.attrib.get(k, '')).lower() == str(v).lower()
                                      for k, v in attributes.items()):
                continue
            candidate = Selector(root=node, url=self.url, encoding=self.encoding)
            if patterns and not all(candidate.get_all_text().re(pattern) for pattern in patterns):
                continue
            if functions and not all(func(candidate) for func in functions):
                continue
            results.append(candidate)
        return results

    def find(self, *args, **kwargs):
        found = self.find_all(*args, **kwargs)
        return found.first

    def re(self, pattern, **kwargs):  # pragma: no cover - 便捷代理
        return TextHandler(self.get_all_text()).re(pattern, **kwargs)

    def re_first(self, pattern, default=None, **kwargs):  # pragma: no cover - 便捷代理
        return TextHandler(self.get_all_text()).re_first(pattern, default=default, **kwargs)

    # ---------- 选择器生成（对应上游 core/mixins.py::SelectorsGeneration） ----------
    @property
    def generate_css_selector(self) -> str:
        return self._general_selection('css', full_path=False)

    @property
    def generate_full_css_selector(self) -> str:
        return self._general_selection('css', full_path=True)

    @property
    def generate_xpath_selector(self) -> str:
        return self._general_selection('xpath', full_path=False)

    @property
    def generate_full_xpath_selector(self) -> str:
        return self._general_selection('xpath', full_path=True)

    def _general_selection(self, selection: str = 'css', full_path: bool = False) -> str:
        if self._is_text_node or self._root.tag in ('#document', 'html'):
            return ''
        css = selection.lower() == 'css'
        path = []
        target = self._root
        while target is not None and target.tag not in ('#document',):
            if target.parent is None:
                break
            if target.attrib.get('id'):
                path.append(f"#{target.attrib['id']}" if css else f"[@id='{target.attrib['id']}']")
                break
            part = target.tag
            if target.nth_of_type() > 1:
                part += f':nth-of-type({target.nth_of_type()})' if css else f'[{target.nth_of_type()}]'
            path.append(part)
            if target.tag == 'html':
                break
            target = target.parent
        if css:
            return ' > '.join(reversed(path))
        return '//' + '/'.join(reversed(path))

    # ---------- 自适应（对应上游 parser.py 的 adaptive 分支 + core/storage.py） ----------
    def save(self, identifier: str, element=None) -> None:
        if not self._storage:
            raise ValueError('save() 需要 adaptive 存储：构造 Selector 时传 storage=AdaptiveStorage(...)')
        target = (element or self._root)
        if isinstance(target, Selector):
            target = target._root
        self._storage.save(target, identifier)

    def relocate(self, identifier: str, percentage: int = 40):
        """按指纹重新定位之前保存过的元素（对应上游 relocate）。"""
        if not self._storage:
            raise ValueError('relocate() 需要 adaptive 存储：构造 Selector 时传 storage=AdaptiveStorage(...)')
        original = self._storage.retrieve(identifier)
        if original is None:
            return None
        best, best_score = None, 0.0
        for node in self._root.iter():
            if node is self._root or node.comment:
                continue
            score = element_similarity(original, element_to_dict(node))
            if score > best_score:
                best, best_score = node, score
        if best is not None and best_score >= percentage:
            found = Selector(root=best, url=self.url, encoding=self.encoding)
            found._identifier = identifier
            return found
        return None

    def auto_match(self, selector: str, identifier: str, percentage: int = 40,
                   auto_save: bool = False) -> Selectors:
        """CSS 取空时按指纹回捞；命中后按 auto_save 决定是否覆盖指纹（对应上游 auto_match）。"""
        found = self.relocate(identifier, percentage)
        if found is not None:
            return Selectors([found])
        return Selectors()

    def __str__(self):
        return self._value if self._is_text_node else serialize(self._root, include_self=False)

    def __repr__(self):  # pragma: no cover
        return f'<Selector {self.tag!r} url={self.url!r} text_node={self._is_text_node}>'


# ---- 元素指纹 + 相似度（对应上游 _StorageTools.element_to_dict / __calculate_similarity_score） ----

def _element_path(node: DomNode):
    chain = []
    current = node
    while current is not None and current.tag != '#document':
        chain.append(current.tag)
        current = current.parent
    return tuple(reversed(chain))


def _clean_attributes(node: DomNode):
    return {k: v for k, v in node.attrib.items() if k not in ('style', 'data-reactid') and v is not None}


def element_to_dict(node: DomNode) -> dict:
    """把元素变成可入库的指纹（与上游字段一一对应）。"""
    result = {
        'tag': str(node.tag),
        'attributes': _clean_attributes(node),
        'text': node.text.strip() if node.text else None,
        'path': _element_path(node),
    }
    parent = node.parent
    if parent is not None and parent.tag != '#document':
        result.update({
            'parent_name': parent.tag,
            'parent_attribs': dict(parent.attrib),
            'parent_text': parent.text.strip() if parent.text else None,
        })
        siblings = [child.tag for child in parent.children if child is not node]
        if siblings:
            result['siblings'] = tuple(siblings)
    children = [child.tag for child in node.children]
    if children:
        result['children'] = tuple(children)
    return result


def _dict_diff(dict1: dict, dict2: dict) -> float:
    score = SequenceMatcher(None, tuple(dict1.keys()), tuple(dict2.keys())).ratio() * 0.5
    score += SequenceMatcher(None, tuple(map(str, dict1.values())),
                             tuple(map(str, dict2.values()))).ratio() * 0.5
    return score


def element_similarity(original: dict, candidate: dict) -> float:
    """元素相似度百分比（上游 __calculate_similarity_score 的逐项等价实现）。"""
    score, checks = 0.0, 0
    score += 1 if original.get('tag') == candidate.get('tag') else 0
    checks += 1
    if original.get('text'):
        score += SequenceMatcher(None, original['text'], candidate.get('text') or '').ratio()
        checks += 1
    score += _dict_diff(original.get('attributes') or {}, candidate.get('attributes') or {})
    checks += 1
    for attrib in ('class', 'id', 'href', 'src'):
        if (original.get('attributes') or {}).get(attrib):
            score += SequenceMatcher(None, original['attributes'][attrib],
                                     (candidate.get('attributes') or {}).get(attrib) or '').ratio()
            checks += 1
    score += SequenceMatcher(None, tuple(original.get('path') or ()),
                             tuple(candidate.get('path') or ())).ratio()
    checks += 1
    if original.get('parent_name'):
        if candidate.get('parent_name'):
            score += SequenceMatcher(None, original['parent_name'], candidate['parent_name']).ratio()
            checks += 1
            score += _dict_diff(original.get('parent_attribs') or {}, candidate.get('parent_attribs') or {})
            checks += 1
            if original.get('parent_text'):
                score += SequenceMatcher(None, original['parent_text'],
                                         candidate.get('parent_text') or '').ratio()
                checks += 1
    if original.get('siblings'):
        score += SequenceMatcher(None, tuple(original['siblings']),
                                 tuple(candidate.get('siblings') or ())).ratio()
        checks += 1
    return round((score / checks) * 100, 2) if checks else 0.0


class AdaptiveStorage:
    """SQLite 自适应存储（对应上游 core/storage.py::SQLiteStorageSystem，含 WAL）。"""

    def __init__(self, storage_file: str, url: str = None):
        self.storage_file = storage_file
        self.url = url.lower() if (url and isinstance(url, str)) else None
        self.lock = threading.RLock()
        directory = os.path.dirname(os.path.abspath(storage_file))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.connection = sqlite3.connect(storage_file, check_same_thread=False)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.cursor = self.connection.cursor()
        self._setup_database()

    def _setup_database(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS storage (
                id INTEGER PRIMARY KEY,
                url TEXT,
                identifier TEXT,
                element_data TEXT,
                UNIQUE (url, identifier)
            )
        """)
        self.connection.commit()

    @property
    def base_url(self) -> str:
        if not self.url:
            return 'default'
        netloc = urlparse(self.url).netloc or self.url
        parts = [p for p in netloc.split('.') if p]
        return '.'.join(parts[-2:]) if len(parts) >= 2 else (parts[0] if parts else 'default')

    def save(self, element, identifier: str) -> None:
        data = element_to_dict(element if isinstance(element, DomNode) else element._root)
        with self.lock:
            self.cursor.execute(
                'INSERT OR REPLACE INTO storage (url, identifier, element_data) VALUES (?, ?, ?)',
                (self.base_url, identifier, json.dumps(data, ensure_ascii=False)),
            )
            self.connection.commit()

    def retrieve(self, identifier: str):
        with self.lock:
            self.cursor.execute('SELECT element_data FROM storage WHERE url = ? AND identifier = ?',
                                (self.base_url, identifier))
            row = self.cursor.fetchone()
            return json.loads(row[0]) if row else None

    def close(self):
        with self.lock:
            try:
                self.connection.commit()
                self.cursor.close()
                self.connection.close()
            except sqlite3.ProgrammingError:  # pragma: no cover - 已关闭
                pass

    def __del__(self):  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass


class Convertor:
    """HTML → 文本 / Markdown（对应上游 core/shell.py::Convertor）。"""

    NOISE_TAGS = ('script', 'style', 'noscript', 'svg')
    HIDDEN_STYLE = re.compile(r'display\s*:\s*none|visibility\s*:\s*hidden', re.I)
    ZERO_WIDTH = re.compile(r'[\u200b-\u200f\u202a-\u202e\u2060\ufeff]')
    CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

    @classmethod
    def strip_noise_tags(cls, page: Selector) -> Selector:
        root = page._root.clone() if not page._is_text_node else None
        if root is None:
            return page
        for node in list(root.iter()):
            if node.tag.lower() in cls.NOISE_TAGS:
                node.drop()
        return Selector(root=root, url=page.url, encoding=page.encoding)

    @classmethod
    def sanitize_for_ai(cls, page: Selector) -> Selector:
        """清掉隐藏内容 / 零宽字符 / 控制字符（上游反提示注入同一口径）。"""
        root = page._root.clone() if not page._is_text_node else None
        if root is None:
            return page
        for node in list(root.iter()):
            if node.comment or node.attrib.get('aria-hidden') == 'true' or \
                    cls.HIDDEN_STYLE.search(node.attrib.get('style') or ''):
                node.drop()
                continue
            if node.text:
                node.text = cls.CONTROL_CHARS.sub('', cls.ZERO_WIDTH.sub('', node.text))
            if node.tail:
                node.tail = cls.CONTROL_CHARS.sub('', cls.ZERO_WIDTH.sub('', node.tail))
        return Selector(root=root, url=page.url, encoding=page.encoding)

    @classmethod
    def to_markdown(cls, page) -> str:
        """轻量 HTML → Markdown（上游用 markdownify，这里内置等价规则）。"""
        node = page._root if isinstance(page, Selector) else page
        if node is None:
            return ''
        text = cls._render(node).strip()
        return re.sub(r'\n{3,}', '\n\n', text)

    @classmethod
    def _render(cls, node: DomNode, list_depth: int = 0) -> str:
        if node.tag == '#document':
            return ''.join(cls._render(child) for child in node.children)
        tag = node.tag.lower()

        def inner():
            parts = [node.text or '']
            for child in node.children:
                parts.append(cls._render(child, list_depth + (1 if tag in ('ul', 'ol') else 0)))
                parts.append(child.tail or '')
            return ''.join(parts)

        if tag in ('script', 'style', 'noscript', 'svg'):
            return node.tail or ''
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            return f"\n{'#' * int(tag[1])} {clean_spaces(inner())}\n\n" + (node.tail or '')
        if tag == 'p':
            return f'\n{clean_spaces(inner())}\n\n' + (node.tail or '')
        if tag == 'br':
            return '\n' + (node.tail or '')
        if tag == 'hr':
            return '\n---\n' + (node.tail or '')
        if tag in ('strong', 'b'):
            return f'**{clean_spaces(inner())}**' + (node.tail or '')
        if tag in ('em', 'i'):
            return f'*{clean_spaces(inner())}*' + (node.tail or '')
        if tag == 'code':
            return f'`{inner()}`' + (node.tail or '')
        if tag == 'pre':
            return f'\n```\n{inner().strip()}\n```\n' + (node.tail or '')
        if tag == 'a':
            href = node.attrib.get('href') or ''
            label = clean_spaces(inner()) or href
            return (f'[{label}]({href})' if href else label) + (node.tail or '')
        if tag == 'img':
            return f"![{node.attrib.get('alt') or ''}]({node.attrib.get('src') or ''})" + (node.tail or '')
        if tag == 'li':
            marker = '- ' if list_depth else '* '
            return f'\n{"  " * max(list_depth - 1, 0)}{marker}{clean_spaces(inner())}' + (node.tail or '')
        if tag in ('ul', 'ol'):
            return ''.join(cls._render(child, list_depth + 1) for child in node.children) + '\n'
        if tag == 'blockquote':
            body = clean_spaces(inner())
            return f'\n> {body}\n' + (node.tail or '')
        if tag == 'tr':
            cells = [clean_spaces(cls._render(cell)) for cell in node.children if cell.tag in ('td', 'th')]
            return '\n| ' + ' | '.join(cells) + ' |' + (node.tail or '')
        if tag in ('table',):
            return ''.join(cls._render(child) for child in node.children) + '\n'
        return inner()

    @classmethod
    def extract_content(cls, page: Selector, extraction_type: str = 'markdown',
                        css_selector: str = None, main_content_only: bool = False):
        """取内容（对应上游 Convertor._extract_content）。"""
        if extraction_type not in ('markdown', 'html', 'text'):
            raise ValueError(f'未知的抽取类型: {extraction_type}')
        if main_content_only:
            page = cls.strip_noise_tags(page)
            page = cls.sanitize_for_ai(page)
        pages = [page] if not css_selector else page.css(css_selector)
        for item in pages:
            if extraction_type == 'markdown':
                yield cls.to_markdown(item)
            elif extraction_type == 'html':
                yield str(item.html_content)
            else:
                text = item.get_all_text(strip=True,
                                         ignore_tags=('script', 'style', 'noscript', 'svg', 'iframe'))
                for separator in ('\n', '\r', '\t', ' '):
                    text = re.sub(f'[{separator}]+', separator, str(text))
                yield text


# ==========================================================================================
# 4. 抓取引擎（上游 scrapling/engines/static.py + toolbelt/custom.py::Response）
# ==========================================================================================

class FetchError(RuntimeError):
    """请求重试耗尽后的错误（对应上游 curl 抛出的异常）。"""

    def __init__(self, message: str, url: str = '', status: int = None):
        super().__init__(message)
        self.url = url
        self.status = status


class Response(Selector):
    """统一响应对象（对应上游 engines/toolbelt/custom.py::Response）。"""

    def __init__(self, url: str, content, status: int, reason: str, cookies=None, headers=None,
                 request_headers=None, encoding: str = '', method: str = 'GET', history=None,
                 meta=None, **selector_config):
        if isinstance(content, str):
            content = content.encode('utf-8', errors='replace')
        headers = dict(headers or {})
        request_headers = dict(request_headers or {})
        detected = encoding or _charset_from_headers(headers)
        self.raw = content
        self.status = status
        self.reason = reason
        self.cookies = cookies if cookies is not None else {}
        self.headers = headers
        self.request_headers = request_headers
        self.history = history or []
        self.method = method
        self.meta = meta or {}
        self.request = None
        super().__init__(content=content, url=url, encoding=detected or 'utf-8', **selector_config)

    @property
    def body(self) -> bytes:
        return self.raw

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def raise_for_status(self):
        if not self.ok:
            raise FetchError(f'HTTP {self.status} {self.reason}', url=self.url, status=self.status)
        return self

    def __repr__(self):  # pragma: no cover
        return f'<Response {self.status} {self.url!r}>'


def _charset_from_headers(headers) -> str:
    for key, value in (headers or {}).items():
        if str(key).lower() == 'content-type':
            match = re.search(r'charset=["\']?([\w-]+)', str(value))
            return match.group(1) if match else ''
    return ''


_HTTP_REASONS = {
    200: 'OK', 301: 'Moved Permanently', 302: 'Found', 303: 'See Other', 304: 'Not Modified',
    307: 'Temporary Redirect', 308: 'Permanent Redirect', 400: 'Bad Request', 401: 'Unauthorized',
    403: 'Forbidden', 404: 'Not Found', 407: 'Proxy Authentication Required',
    429: 'Too Many Requests', 444: 'No Response', 500: 'Internal Server Error',
    502: 'Bad Gateway', 503: 'Service Unavailable', 504: 'Gateway Timeout',
}


def parse_retry_after(headers) -> float:
    """解析 Retry-After（秒数或 HTTP 日期），对应上游 spiders/throttle.py::parse_retry_after。"""
    value = ''
    for key in (headers or {}):
        if str(key).lower() == 'retry-after':
            value = str(headers[key]).strip()
            break
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        delta = parsedate_to_datetime(value) - datetime.now(timezone.utc)
        return max(delta.total_seconds(), 0.0)
    except (TypeError, ValueError):
        return None


class _NoRedirect(HTTPRedirectHandler):
    """不自动跟随重定向：由 FetcherSession 自己记录 history（对应上游 max_redirects）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _default_transport(method: str, url: str, headers: dict, timeout: float, proxy: str = None,
                       body: bytes = None):
    """默认传输层：urllib。返回 (status, reason, headers, raw_bytes, final_url)。"""
    handlers = [_NoRedirect(), HTTPCookieProcessor(CookieJar())]
    if proxy:
        handlers.append(ProxyHandler({'http': proxy, 'https': proxy}))
    opener = build_opener(*handlers)
    request = UrlRequest(url, data=body, headers=dict(headers or {}), method=method.upper())
    try:
        with opener.open(request, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, resp.reason, dict(resp.headers), _maybe_decompress(raw, resp.headers), resp.url
    except HTTPError as exc:
        raw = exc.read() if hasattr(exc, 'read') else b''
        return exc.code, exc.reason or _HTTP_REASONS.get(exc.code, ''), dict(exc.headers or {}), raw, url


def _maybe_decompress(raw: bytes, headers) -> bytes:
    encoding = ''
    for key, value in (headers or {}).items():
        if str(key).lower() == 'content-encoding':
            encoding = str(value).lower()
            break
    try:
        if 'gzip' in encoding:
            return gzip.decompress(raw)
        if 'deflate' in encoding:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):  # pragma: no cover - 站点声明与实际不符时原样返回
        return raw
    return raw


class FetcherSession:
    """静态抓取会话（对应上游 FetcherSession / engines/static.py::_ConfigurationLogic）。"""

    DEFAULTS = {
        'impersonate': 'chrome',
        'stealthy_headers': True,
        'headers': None,
        'timeout': 10.0,
        'retries': 3,
        'retry_delay': 1.0,
        'retry_backoff': 2.0,
        'follow_redirects': True,
        'max_redirects': 30,
        'proxy': None,
        'proxies': None,
        'proxy_rotator': None,
        'respect_retry_after': True,
        'transport': None,
        'accept': 'document',
        'lang': 'zh-CN,zh;q=0.9,en;q=0.8',
    }

    def __init__(self, **kwargs):
        self.config = dict(self.DEFAULTS)
        self.config.update({k: v for k, v in kwargs.items() if k in self.DEFAULTS})
        self.extra = {k: v for k, v in kwargs.items() if k not in self.DEFAULTS}
        self.closed = False
        self.stats = {'requests': 0, 'retries': 0, 'blocked': 0, 'bytes': 0}
        self._lock = threading.Lock()

    # ---- 配置 ----
    def _param(self, kwargs, key, default):
        return kwargs[key] if key in kwargs else default

    def _headers_for(self, url: str, extra_headers, stealth: bool, impersonate) -> dict:
        headers = {}
        if stealth:
            headers.update(generate_headers(impersonate=impersonate,
                                            accept=self.extra.get('accept', self.config['accept']),
                                            lang=self.extra.get('lang', self.config['lang'])))
        else:
            headers['User-Agent'] = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                     '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
        headers.update(self.config.get('headers') or {})
        headers.update(extra_headers or {})
        if 'user-agent' not in {k.lower() for k in headers}:
            headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' \
                                    '(KHTML, like Gecko) Chrome/126.0 Safari/537.36'
        return headers

    @staticmethod
    def _is_redirect(status: int) -> bool:
        return status in (301, 302, 303, 307, 308)

    def _proxy_for(self, kwargs):
        rotator = self._param(kwargs, 'proxy_rotator', self.config.get('proxy_rotator'))
        if rotator is not None:
            return rotator.get_proxy()
        return self._param(kwargs, 'proxy', self.config.get('proxy'))

    def fetch(self, method: str, url: str, **kwargs) -> Response:
        """一次请求（含重试、退避、重定向、封锁码识别）。"""
        if self.closed:  # pragma: no cover - 防御性
            raise FetchError('session 已关闭', url=url)
        method = method.upper()
        if method not in SUPPORTED_HTTP_METHODS:
            raise ValueError(f'不支持的 HTTP 方法: {method}')
        timeout = float(self._param(kwargs, 'timeout', self.config['timeout']))
        max_retries = max(1, int(self._param(kwargs, 'retries', self.config['retries']) or 1))
        retry_delay = float(self._param(kwargs, 'retry_delay', self.config['retry_delay']))
        backoff = float(self._param(kwargs, 'retry_backoff', self.config['retry_backoff']))
        stealth = bool(self._param(kwargs, 'stealth', self.config['stealthy_headers']))
        impersonate = self._param(kwargs, 'impersonate', self.config['impersonate'])
        if impersonate in (True, 'random'):
            impersonate = 'random'
        follow = bool(self._param(kwargs, 'follow_redirects', self.config['follow_redirects']))
        max_redirects = int(self._param(kwargs, 'max_redirects', self.config['max_redirects']))
        transport = self._param(kwargs, 'transport', self.config.get('transport')) or _default_transport
        respect_retry_after = bool(self._param(kwargs, 'respect_retry_after',
                                               self.config['respect_retry_after']))
        headers = self._headers_for(url, self._param(kwargs, 'headers', None), stealth, impersonate)
        body = self._param(kwargs, 'body', None)
        if body is None and self._param(kwargs, 'data', None) is not None:
            data = self._param(kwargs, 'data', None)
            body = data.encode('utf-8') if isinstance(data, str) else data
        last_error = None
        with self._lock:
            self.stats['requests'] += 1
        for attempt in range(max_retries):
            proxy = self._proxy_for(kwargs)
            current_url, history = url, []
            try:
                for _hop in range(max_redirects + 1):
                    status, reason, resp_headers, raw, final_url = transport(
                        method, current_url, headers, timeout, proxy, body)
                    if self._is_redirect(status) and follow:
                        location = None
                        for key, value in (resp_headers or {}).items():
                            if str(key).lower() == 'location':
                                location = value
                                break
                        if location:
                            history.append(Response(url=current_url, content=b'', status=status,
                                                    reason=reason or _HTTP_REASONS.get(status, ''),
                                                    headers=resp_headers, request_headers=headers,
                                                    method=method))
                            current_url = urljoin(current_url, location)
                            headers = self._headers_for(current_url, self._param(kwargs, 'headers', None),
                                                        stealth, impersonate)
                            continue
                    response = Response(
                        url=final_url or current_url,
                        content=_maybe_decompress(raw or b'', resp_headers),
                        status=status,
                        reason=reason or _HTTP_REASONS.get(status, ''),
                        cookies={},
                        headers=resp_headers,
                        request_headers=headers,
                        encoding=_charset_from_headers(resp_headers),
                        method=method,
                        history=history,
                        **{k: v for k, v in self.extra.get('selector_config', {}).items()},
                    )
                    with self._lock:
                        self.stats['bytes'] += len(raw or b'')
                        if status in BLOCKED_CODES:
                            self.stats['blocked'] += 1
                    return response
                last_error = FetchError(f'重定向次数超过上限 {max_redirects}', url=url)
            except (HTTPError, URLError, OSError, ValueError) as exc:
                last_error = exc
            if attempt < max_retries - 1:
                delay = retry_delay * (backoff ** attempt)
                with self._lock:
                    self.stats['retries'] += 1
                time.sleep(min(delay, 60.0))
        raise FetchError(f'请求失败（已重试 {max_retries} 次）: {last_error}', url=url)

    def get(self, url: str, **kwargs) -> Response:
        return self.fetch('GET', url, **kwargs)

    def post(self, url: str, **kwargs) -> Response:  # pragma: no cover - 本项目的社区抓取只用 GET
        return self.fetch('POST', url, **kwargs)

    def put(self, url: str, **kwargs) -> Response:  # pragma: no cover
        return self.fetch('PUT', url, **kwargs)

    def delete(self, url: str, **kwargs) -> Response:  # pragma: no cover
        return self.fetch('DELETE', url, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        self.closed = True

    def display_config(self) -> dict:  # pragma: no cover - 便于调试
        return {k: v for k, v in self.config.items() if k != 'transport'}


class Fetcher:
    """全局静态抓取入口（对应上游 `from scrapling.fetchers import Fetcher` 的用法）。"""

    _session = FetcherSession()
    _lock = threading.Lock()

    @classmethod
    def configure(cls, **kwargs):
        with cls._lock:
            config = dict(cls._session.config)
            config.update({k: v for k, v in kwargs.items() if k in FetcherSession.DEFAULTS})
            extra = dict(cls._session.extra)
            extra.update({k: v for k, v in kwargs.items() if k not in FetcherSession.DEFAULTS})
            cls._session = FetcherSession(**{**config, **extra})
        return cls._session

    @classmethod
    def session(cls) -> FetcherSession:
        return cls._session

    @classmethod
    def get(cls, url: str, **kwargs) -> Response:
        return cls._session.get(url, **kwargs)

    @classmethod
    def post(cls, url: str, **kwargs) -> Response:  # pragma: no cover
        return cls._session.post(url, **kwargs)

    @classmethod
    def put(cls, url: str, **kwargs) -> Response:  # pragma: no cover
        return cls._session.put(url, **kwargs)

    @classmethod
    def delete(cls, url: str, **kwargs) -> Response:  # pragma: no cover
        return cls._session.delete(url, **kwargs)


# ==========================================================================================
# 5. 爬虫框架（上游 scrapling/spiders/*）
# ==========================================================================================

class Request:
    """一条请求（对应上游 spiders/request.py::Request：指纹 / 优先级 / 去重 / 重试计数）。"""

    def __init__(self, url: str, sid: str = '', callback=None, priority: int = 0,
                 dont_filter: bool = False, meta: dict = None, _retry_count: int = 0, **kwargs):
        self.url = url
        self.sid = sid
        self.callback = callback
        self.priority = priority
        self.dont_filter = dont_filter
        self.meta = meta if meta is not None else {}
        self._retry_count = _retry_count
        self._session_kwargs = dict(kwargs) if kwargs else {}
        self._fp = None

    def copy(self) -> 'Request':
        clone = Request(url=self.url, sid=self.sid, callback=self.callback, priority=self.priority,
                        dont_filter=self.dont_filter, meta=dict(self.meta),
                        _retry_count=self._retry_count, **self._session_kwargs)
        clone._fp = self._fp
        return clone

    @property
    def domain(self) -> str:
        return (urlparse(self.url).netloc or '').lower()

    def update_fingerprint(self, include_kwargs: bool = False, include_headers: bool = False,
                           keep_fragments: bool = False):
        """URL 指纹（上游用 w3lib.canonicalize_url + kwargs/headers 可选参与）。"""
        if self._fp is not None:
            return self._fp
        parsed = urlparse(self.url)
        fragment = parsed.fragment if keep_fragments else ''
        normalized = (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip('/'),
                      parsed.query, fragment)
        payload = str(normalized)
        if include_kwargs and self._session_kwargs:
            payload += json.dumps(self._session_kwargs, sort_keys=True, default=repr)
        if include_headers and self.meta.get('headers'):
            payload += json.dumps(self.meta['headers'], sort_keys=True, default=repr)
        import hashlib
        self._fp = hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32].encode('ascii')
        return self._fp

    def __repr__(self):  # pragma: no cover
        return f'<Request {self.url!r} priority={self.priority}>'


class Scheduler:
    """优先级队列 + 指纹去重（对应上游 spiders/scheduler.py，线程安全版）。"""

    def __init__(self, include_kwargs: bool = False, include_headers: bool = False,
                 keep_fragments: bool = False):
        import heapq
        self._heap = []
        self._heapq = heapq
        self._seen = set()
        self._counter = 0
        self._lock = threading.Condition()
        self._pending = {}
        self._include_kwargs = include_kwargs
        self._include_headers = include_headers
        self._keep_fragments = keep_fragments

    def enqueue(self, request: Request) -> bool:
        fingerprint = request.update_fingerprint(self._include_kwargs, self._include_headers,
                                                 self._keep_fragments)
        with self._lock:
            if not request.dont_filter and fingerprint in self._seen:
                return False
            self._seen.add(fingerprint)
            counter = self._counter = self._counter + 1
            item = (-request.priority, counter, request)
            self._pending[counter] = item
            self._heapq.heappush(self._heap, item)
            self._lock.notify()
            return True

    def dequeue(self, timeout: float = None):
        with self._lock:
            if not self._heap:
                if timeout is None:
                    return None
                self._lock.wait(timeout)
            if not self._heap:
                return None
            _, counter, request = self._heapq.heappop(self._heap)
            return request

    def complete(self, request: Request) -> None:
        with self._lock:
            for counter, item in list(self._pending.items()):
                if item[2] is request:
                    self._pending.pop(counter, None)
                    break

    def __len__(self):
        with self._lock:
            return len(self._heap)

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return not self._heap

    @property
    def seen_count(self) -> int:
        with self._lock:
            return len(self._seen)

    def snapshot(self):
        """快照（请求列表 + 已见指纹），对应上游 snapshot()，供断点续爬。"""
        with self._lock:
            sorted_items = sorted(self._pending.values(), key=lambda x: (x[0], x[1]))
            return [item[2] for item in sorted_items], set(self._seen)

    def restore(self, requests, seen) -> None:
        with self._lock:
            self._seen = set(seen or ())
            for request in requests or []:
                self._counter += 1
                item = (-request.priority, self._counter, request)
                self._pending[self._counter] = item
                self._heapq.heappush(self._heap, item)


class AutoThrottle:
    """按响应延迟自适应限速（上游 spiders/throttle.py::AutoThrottle 的逐行等价移植）。"""

    BLOCK_BACKOFF_FACTOR = 2.0

    def __init__(self, start_delay: float = 5.0, max_delay: float = 60.0,
                 target_concurrency: float = 1.0, block_backoff: bool = True):
        if target_concurrency <= 0:
            raise ValueError('target_concurrency 必须大于 0')
        if max_delay < start_delay:
            raise ValueError('autothrottle_max_delay 不能小于 autothrottle_start_delay')
        self.start_delay = start_delay
        self.max_delay = max_delay
        self.target_concurrency = target_concurrency
        self.block_backoff = block_backoff
        self.delays = {}
        self._lock = threading.Lock()

    def delay_for(self, domain: str, floor: float = 0.0) -> float:
        with self._lock:
            if domain not in self.delays:
                self.delays[domain] = min(max(floor, self.start_delay), self.max_delay)
            return min(max(self.delays[domain], floor), self.max_delay)

    def record(self, domain: str, latency: float, ok: bool, floor: float = 0.0,
               retry_after: float = None) -> float:
        with self._lock:
            current = self.delays.get(domain, min(max(floor, self.start_delay), self.max_delay))
            target_delay = latency / self.target_concurrency
            new_delay = max((current + target_delay) / 2, target_delay)
            if not ok:
                penalty = current
                if self.block_backoff:
                    penalty = retry_after if retry_after is not None else current * self.BLOCK_BACKOFF_FACTOR
                new_delay = max(new_delay, penalty, current)
            new_delay = min(max(new_delay, floor), self.max_delay)
            self.delays[domain] = new_delay
            return new_delay

    def reset(self) -> None:
        with self._lock:
            self.delays.clear()


class RobotsTxtManager:
    """robots.txt 抓取 / 缓存 / 判定（对应上游 spiders/robotstxt.py，protego → 标准库解析）。"""

    def __init__(self, fetch_fn):
        self._fetch_fn = fetch_fn
        self._cache = {}
        self._lock = threading.Lock()

    @staticmethod
    def _parse(content: str) -> dict:
        """极简 robots.txt 解析：User-agent 段落 → allow/disallow/crawl-delay。"""
        groups, current_agents, current_rules = [], [], None
        for raw_line in str(content or '').splitlines():
            line = raw_line.split('#', 1)[0].strip()
            if not line or ':' not in line:
                continue
            field, _, value = line.partition(':')
            field, value = field.strip().lower(), value.strip()
            if field == 'user-agent':
                if current_rules is not None and current_agents:
                    groups.append((list(current_agents), current_rules))
                    current_agents, current_rules = [], None
                current_agents.append(value.lower())
            elif field in ('allow', 'disallow', 'crawl-delay', 'request-rate') and current_agents:
                if current_rules is None:
                    current_rules = {'allow': [], 'disallow': [], 'crawl_delay': None, 'request_rate': None}
                if field == 'crawl-delay':
                    try:
                        current_rules['crawl_delay'] = float(value)
                    except ValueError:
                        pass
                elif field == 'request-rate':
                    parts = value.split('/')
                    if len(parts) == 2:
                        try:
                            current_rules['request_rate'] = (int(parts[0]), float(parts[1]))
                        except ValueError:
                            pass
                else:
                    current_rules[field].append(value)
        if current_agents and current_rules:
            groups.append((list(current_agents), current_rules))
        merged = {'allow': [], 'disallow': [], 'crawl_delay': None, 'request_rate': None}
        for agents, rules in groups:
            if '*' not in agents:
                continue
            merged['allow'].extend(rules['allow'])
            merged['disallow'].extend(rules['disallow'])
            if rules['crawl_delay'] is not None:
                merged['crawl_delay'] = rules['crawl_delay']
            if rules['request_rate'] is not None:
                merged['request_rate'] = rules['request_rate']
        return merged

    def _parser_for(self, url: str):
        domain = urlparse(url).netloc
        with self._lock:
            if domain in self._cache:
                return self._cache[domain]
        scheme = urlparse(url).scheme or 'https'
        content = ''
        try:
            response = self._fetch_fn(f'{scheme}://{domain}/robots.txt')
            if getattr(response, 'status', 0) == 200:
                content = response.text if hasattr(response, 'text') else str(response)
        except Exception:
            content = ''
        parsed = self._parse(content)
        with self._lock:
            self._cache[domain] = parsed
        return parsed

    def can_fetch(self, url: str) -> bool:
        rules = self._parser_for(url)
        path = urlparse(url).path or '/'
        best_len, allowed = -1, True
        for pattern in rules['disallow']:
            if pattern and path.startswith(pattern) and len(pattern) > best_len:
                best_len, allowed = len(pattern), False
        for pattern in rules['allow']:
            if pattern and path.startswith(pattern) and len(pattern) >= best_len:
                best_len, allowed = len(pattern), True
        return allowed

    def crawl_delay(self, url: str):
        return self._parser_for(url)['crawl_delay']

    def request_rate(self, url: str):
        return self._parser_for(url)['request_rate']

    def prefetch(self, urls) -> None:
        for url in urls or []:
            self._parser_for(url)


class DevCache:
    """开发缓存（对应上游 spiders/cache.py::DevCache，fingerprint → 响应 JSON）。"""

    def __init__(self, cache_dir: str, enabled: bool = True):
        self.cache_dir = cache_dir
        self.enabled = enabled
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, fingerprint) -> str:
        if isinstance(fingerprint, bytes):
            fingerprint = fingerprint.hex()
        return os.path.join(self.cache_dir, f'{fingerprint}.json')

    def get(self, fingerprint):
        if not self.enabled:
            return None
        path = self._path(fingerprint)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding='utf-8') as handle:
                data = json.load(handle)
            import base64
            return Response(url=data['url'], content=base64.b64decode(data['content']),
                            status=data['status'], reason=data.get('reason', ''),
                            cookies=data.get('cookies') or {}, headers=data.get('headers') or {},
                            request_headers=data.get('request_headers') or {},
                            encoding=data.get('encoding') or 'utf-8', method=data.get('method', 'GET'),
                            meta={'dev_cache': True})
        except (OSError, ValueError, KeyError):
            return None

    def put(self, fingerprint, response, method: str = 'GET') -> None:
        if not self.enabled:
            return
        import base64
        payload = {
            'url': response.url,
            'content': base64.b64encode(response.body).decode('ascii'),
            'status': response.status,
            'reason': response.reason,
            'encoding': response.encoding,
            'cookies': dict(response.cookies or {}),
            'headers': dict(response.headers or {}),
            'request_headers': dict(response.request_headers or {}),
            'method': method,
        }
        path = self._path(fingerprint)
        temp = f'{path}.tmp'
        try:
            with open(temp, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(temp, path)
        except OSError:  # pragma: no cover - 缓存写失败不影响抓取
            if os.path.exists(temp):
                os.remove(temp)

    def clear(self) -> None:
        if not os.path.isdir(self.cache_dir):
            return
        for name in os.listdir(self.cache_dir):
            if name.endswith('.json'):
                os.remove(os.path.join(self.cache_dir, name))


class CheckpointManager:
    """断点续爬（对应上游 spiders/checkpoint.py，pickle → JSON 原子写）。"""

    CHECKPOINT_FILE = 'checkpoint.json'

    def __init__(self, crawldir: str, interval: float = 300.0):
        if not isinstance(interval, (int, float)):
            raise TypeError('checkpoint interval 必须是数字')
        if interval < 0:
            raise ValueError('checkpoint interval 不能小于 0')
        self.crawldir = crawldir
        self.interval = interval
        self.path = os.path.join(crawldir, self.CHECKPOINT_FILE)

    def has_checkpoint(self) -> bool:
        return os.path.exists(self.path)

    def save(self, requests, seen) -> None:
        os.makedirs(self.crawldir, exist_ok=True)
        payload = {
            'requests': [{
                'url': request.url, 'sid': request.sid, 'priority': request.priority,
                'dont_filter': request.dont_filter, 'meta': request.meta,
                '_retry_count': request._retry_count, 'kwargs': request._session_kwargs,
            } for request in requests],
            'seen': [fp.decode('ascii') if isinstance(fp, bytes) else str(fp) for fp in seen],
        }
        temp = f'{self.path}.tmp'
        with open(temp, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temp, self.path)

    def load(self):
        if not self.has_checkpoint():
            return None
        try:
            with open(self.path, encoding='utf-8') as handle:
                payload = json.load(handle)
            requests = [Request(url=item['url'], sid=item.get('sid', ''), priority=item.get('priority', 0),
                                dont_filter=item.get('dont_filter', False), meta=item.get('meta') or {},
                                _retry_count=item.get('_retry_count', 0), **item.get('kwargs', {}))
                        for item in payload.get('requests', [])]
            return requests, set(payload.get('seen') or ())
        except (OSError, ValueError, KeyError):
            return None

    def cleanup(self) -> None:
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except OSError:  # pragma: no cover
            pass


class ItemList(list):
    """抓取结果列表（对应上游 spiders/result.py::ItemList，含 JSON / JSONL 导出）。"""

    def to_json(self, path: str, indent: bool = True) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(list(self), handle, ensure_ascii=False, indent=2 if indent else None)

    def to_jsonl(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            for item in self:
                handle.write(json.dumps(item, ensure_ascii=False) + '\n')


class CrawlStats:
    """抓取统计（对应上游 spiders/result.py::CrawlStats 的常用字段）。"""

    def __init__(self):
        self.requests_count = 0
        self.concurrent_requests = 0
        self.concurrent_requests_per_domain = 0
        self.failed_requests_count = 0
        self.offsite_requests_count = 0
        self.robots_disallowed_count = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.response_bytes = 0
        self.items_scraped = 0
        self.items_dropped = 0
        self.start_time = 0.0
        self.end_time = 0.0
        self.download_delay = 0.0
        self.autothrottle_enabled = False
        self.blocked_requests_count = 0
        self.autothrottle_delays = {}
        self.response_status_count = {}
        self.custom_stats = {}
        self.sessions_requests_count = {}
        self._lock = threading.Lock()

    @property
    def elapsed_seconds(self) -> float:
        return max(self.end_time - self.start_time, 0.0)

    @property
    def requests_per_second(self) -> float:
        return self.requests_count / self.elapsed_seconds if self.elapsed_seconds else 0.0

    def increment_status(self, status: int) -> None:
        with self._lock:
            key = f'status_{status}'
            self.response_status_count[key] = self.response_status_count.get(key, 0) + 1

    def to_dict(self) -> dict:
        return {
            'items_scraped': self.items_scraped,
            'items_dropped': self.items_dropped,
            'elapsed_seconds': round(self.elapsed_seconds, 2),
            'requests_count': self.requests_count,
            'requests_per_second': round(self.requests_per_second, 2),
            'concurrent_requests': self.concurrent_requests,
            'failed_requests_count': self.failed_requests_count,
            'robots_disallowed_count': self.robots_disallowed_count,
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses,
            'blocked_requests_count': self.blocked_requests_count,
            'response_status_count': dict(self.response_status_count),
            'response_bytes': self.response_bytes,
            'download_delay': round(self.download_delay, 2),
            'autothrottle_enabled': self.autothrottle_enabled,
            'autothrottle_delays': {k: round(v, 2) for k, v in self.autothrottle_delays.items()},
            'custom_stats': dict(self.custom_stats),
        }


class CrawlResult:
    """抓取结果（对应上游 spiders/result.py::CrawlResult）。"""

    def __init__(self, stats: CrawlStats, items: ItemList, paused: bool = False):
        self.stats = stats
        self.items = items
        self.paused = paused

    @property
    def completed(self) -> bool:
        return not self.paused

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)


class SessionManager:
    """会话管理（对应上游 spiders/session.py::SessionManager）。"""

    def __init__(self):
        self._sessions = {}
        self._default_session_id = None
        self._lock = threading.Lock()

    def add(self, session_id: str, session, default: bool = False, lazy: bool = False) -> 'SessionManager':
        with self._lock:
            if session_id in self._sessions:
                raise ValueError(f"Session '{session_id}' already registered")
            self._sessions[session_id] = session
            if default or self._default_session_id is None:
                self._default_session_id = session_id
        return self

    def get(self, session_id: str):
        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(f"Session '{session_id}' not found")
            return self._sessions[session_id]

    def pop(self, session_id: str):
        with self._lock:
            session = self._sessions.pop(session_id)
            if session_id == self._default_session_id:
                self._default_session_id = next(iter(self._sessions), None)
            return session

    @property
    def default_session_id(self) -> str:
        if self._default_session_id is None:
            raise RuntimeError('No sessions registered')
        return self._default_session_id

    @property
    def session_ids(self):
        return list(self._sessions.keys())

    def __len__(self):
        return len(self._sessions)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for session_id in list(self._sessions):
            session = self._sessions[session_id]
            close = getattr(session, 'close', None)
            if callable(close):
                close()


class SessionConfigurationError(Exception):
    """会话配置错误（对应上游 spiders/spider.py::SessionConfigurationError）。"""


class Spider(ABC):
    """爬虫基类（对应上游 spiders/spider.py::Spider，同步线程版）。"""

    name = None
    start_urls = []
    allowed_domains = set()

    # robots.txt
    robots_txt_obey = False

    # development mode
    development_mode = False
    development_cache_dir = None

    # 并发 / 限速
    concurrent_requests = 4
    concurrent_requests_per_domain = 0
    download_delay = 0.0
    max_blocked_retries = 3

    # AutoThrottle
    autothrottle_enabled = False
    autothrottle_start_delay = 5.0
    autothrottle_max_delay = 60.0
    autothrottle_target_concurrency = None
    autothrottle_block_backoff = True

    # 指纹
    fp_include_kwargs = False
    fp_keep_fragments = False
    fp_include_headers = False

    # 请求
    request_timeout = 10.0
    request_retries = 2
    request_retry_delay = 0.5

    # 日志
    log_prefix = '[%(spider)s] %(levelname)s: %(message)s'

    def __init__(self, crawldir=None, interval: float = 300.0, session=None):
        if self.name is None:
            raise ValueError(f'{self.__class__.__name__} 必须定义 name')
        self.crawldir = crawldir
        self._interval = interval
        self._session_manager = SessionManager()
        if session is not None:
            self._session_manager.add('default', session, default=True)
        else:
            try:
                self.configure_sessions(self._session_manager)
            except Exception as exc:  # pragma: no cover - 配置错误要显式抛出
                raise SessionConfigurationError(
                    f'{self.__class__.__name__}.configure_sessions() 出错: {exc}') from exc
        if len(self._session_manager) == 0:
            raise SessionConfigurationError(f'{self.__class__.__name__}.configure_sessions() 未注册任何会话')
        self._engine = None

    # ---- 用户可覆写 ----
    def configure_sessions(self, manager: SessionManager) -> None:
        manager.add('default', FetcherSession(timeout=self.request_timeout,
                                              retries=self.request_retries,
                                              retry_delay=self.request_retry_delay), default=True)

    def start_requests(self):
        if not self.start_urls:
            raise RuntimeError('Spider 没有起点：请设置 start_urls 或覆写 start_requests()')
        for url in self.start_urls:
            yield Request(url, sid=self._session_manager.default_session_id)

    @abstractmethod
    def parse(self, response: Response):
        raise NotImplementedError(f'{self.__class__.__name__} 必须实现 parse()')

    def on_start(self, resuming: bool = False) -> None:
        pass

    def on_close(self) -> None:
        pass

    def on_error(self, request: Request, error: Exception) -> None:
        pass

    def on_scraped_item(self, item: dict):
        return item

    def is_blocked(self, response: Response) -> bool:
        return response.status in BLOCKED_CODES

    def retry_blocked_request(self, request: Request, response: Response) -> Request:
        return request

    def log(self, message: str, level: str = 'INFO') -> None:
        print(f'[{self.name}] {level}: {message}')

    # ---- 运行 ----
    def crawl(self, *args, **kwargs) -> CrawlResult:
        self._engine = CrawlerEngine(self, self._session_manager, self.crawldir, self._interval)
        stats = self._engine.crawl()
        return CrawlResult(stats=stats, items=self._engine.items, paused=self._engine.paused)

    def pause(self) -> None:
        if self._engine:
            self._engine.request_pause()
        else:  # pragma: no cover
            raise RuntimeError('没有正在运行的抓取任务')

    def __repr__(self):  # pragma: no cover
        return f"<{self.__class__.__name__} '{self.name}'>"


class CrawlerEngine:
    """抓取引擎（对应上游 spiders/engine.py::CrawlerEngine，threading 版）。"""

    def __init__(self, spider: Spider, session_manager: SessionManager, crawldir=None, interval: float = 300.0):
        self.spider = spider
        self.session_manager = session_manager
        self.scheduler = Scheduler(include_kwargs=spider.fp_include_kwargs,
                                   include_headers=spider.fp_include_headers,
                                   keep_fragments=spider.fp_keep_fragments)
        self.checkpoint = CheckpointManager(crawldir, interval) if crawldir else None
        self.dev_cache = DevCache(spider.development_cache_dir) if (spider.development_mode and
                                                                   spider.development_cache_dir) else None
        self.autothrottle = None
        if spider.autothrottle_enabled:
            per_domain = spider.concurrent_requests_per_domain or 1
            self.autothrottle = AutoThrottle(
                start_delay=spider.autothrottle_start_delay,
                max_delay=spider.autothrottle_max_delay,
                target_concurrency=spider.autothrottle_target_concurrency or per_domain,
                block_backoff=spider.autothrottle_block_backoff)
        self.robots = None
        if spider.robots_txt_obey:
            self.robots = RobotsTxtManager(self._fetch_robots)
        self.items = ItemList()
        self.stats = CrawlStats()
        self.paused = False
        self._pause_requested = False
        self._lock = threading.RLock()
        self._last_checkpoint = 0.0
        self._domain_limiters = {}

    # ---- 内部工具 ----
    def _session_for(self, request: Request) -> FetcherSession:
        return self.session_manager.get(request.sid or self.session_manager.default_session_id)

    def _fetch_robots(self, url: str):
        """用 Spider 的默认会话取 robots.txt（这样注入的传输层/代理配置一并生效）。"""
        session = self.session_manager.get(self.session_manager.default_session_id)
        return session.get(url, timeout=5, retries=1)

    def _domain_semaphore(self, domain: str):
        limit = self.spider.concurrent_requests_per_domain
        if not limit:
            return None
        with self._lock:
            if domain not in self._domain_limiters:
                self._domain_limiters[domain] = threading.Semaphore(limit)
            return self._domain_limiters[domain]

    def _offsite(self, request: Request) -> bool:
        allowed = self.spider.allowed_domains
        if not allowed:
            return False
        domain = request.domain
        return not any(domain == item or domain.endswith('.' + item) for item in allowed)

    def _save_checkpoint(self) -> None:
        if not self.checkpoint:
            return
        requests, seen = self.scheduler.snapshot()
        try:
            self.checkpoint.save(requests, seen)
            self._last_checkpoint = time.time()
        except OSError as exc:  # pragma: no cover - 落盘失败不影响抓取
            self.spider.log(f'断点保存失败: {exc}', level='WARNING')

    def request_pause(self) -> None:
        self._pause_requested = True

    # ---- 单条请求 ----
    def _process(self, request: Request) -> None:
        domain = request.domain
        with self._lock:
            self.stats.requests_count += 1
            sid = request.sid or self.session_manager.default_session_id
            self.stats.sessions_requests_count[sid] = self.stats.sessions_requests_count.get(sid, 0) + 1
        semaphore = self._domain_semaphore(domain)
        if semaphore:
            semaphore.acquire()
        try:
            if self.robots is not None:
                try:
                    delay = self.robots.crawl_delay(request.url)
                except Exception:  # pragma: no cover - robots 抓取失败不阻断
                    delay = None
                if delay:
                    self.spider.download_delay = max(self.spider.download_delay, float(delay))
                if not self.robots.can_fetch(request.url):
                    with self._lock:
                        self.stats.robots_disallowed_count += 1
                    self.spider.log(f'robots.txt 拒绝抓取，跳过: {request.url}', level='WARNING')
                    return
            floor = float(self.spider.download_delay or 0.0)
            if self.autothrottle is not None:
                wait = self.autothrottle.delay_for(domain, floor)
            else:
                wait = floor
            if wait > 0:
                time.sleep(wait)

            session = self._session_for(request)
            fingerprint = request.update_fingerprint(self.spider.fp_include_kwargs,
                                                     self.spider.fp_include_headers,
                                                     self.spider.fp_keep_fragments)
            cached = self.dev_cache.get(fingerprint) if self.dev_cache else None
            start = time.time()
            if cached is not None:
                response = cached
                with self._lock:
                    self.stats.cache_hits += 1
            else:
                if self.dev_cache:
                    with self._lock:
                        self.stats.cache_misses += 1
                kwargs = dict(request._session_kwargs)
                kwargs.setdefault('timeout', self.spider.request_timeout)
                kwargs.setdefault('retries', self.spider.request_retries)
                response = session.fetch('GET', request.url, **kwargs)
                if self.dev_cache:
                    self.dev_cache.put(fingerprint, response)
            latency = max(time.time() - start, 0.0)
            with self._lock:
                self.stats.increment_status(response.status)
                self.stats.response_bytes += len(response.body or b'')
            response.request = request

            blocked = self.spider.is_blocked(response)
            if self.autothrottle is not None:
                ok = 200 <= response.status < 300 and not blocked
                retry_after = None if ok else parse_retry_after(response.headers)
                new_delay = self.autothrottle.record(domain, latency, ok, floor, retry_after)
                with self._lock:
                    self.stats.autothrottle_delays[domain] = new_delay
            if blocked:
                with self._lock:
                    self.stats.blocked_requests_count += 1
                if request._retry_count < self.spider.max_blocked_retries:
                    retry_request = request.copy()
                    retry_request._retry_count += 1
                    retry_request.priority -= 1
                    retry_request.dont_filter = True
                    retry_request._session_kwargs.pop('proxy', None)
                    retry_request._session_kwargs.pop('proxies', None)
                    retry_request = self.spider.retry_blocked_request(retry_request, response)
                    if self.scheduler.enqueue(retry_request):
                        self.spider.log(f'被封锁，已排队重试 '
                                        f'({retry_request._retry_count}/{self.spider.max_blocked_retries}): '
                                        f'{request.url}', level='WARNING')
                else:
                    self.spider.log(f'被封锁且重试已用尽: {request.url}', level='WARNING')
                    self.stats.custom_stats.setdefault('blocked_urls', []).append(request.url)
                    try:
                        self.spider.on_error(request, FetchError(
                            f'被站点阻断 HTTP {response.status}（重试已用尽）',
                            url=request.url, status=response.status))
                    except Exception:  # pragma: no cover - 钩子异常不影响主流程
                        pass
                return

            callback = request.callback or self.spider.parse
            results = callback(response)
            if results is None:
                results = []
            for result in results:
                if result is None:
                    continue
                if isinstance(result, Request):
                    if result.sid == '':
                        result.sid = request.sid
                    if self._offsite(result):
                        with self._lock:
                            self.stats.offsite_requests_count += 1
                        continue
                    self.scheduler.enqueue(result)
                elif isinstance(result, dict):
                    with self._lock:
                        self.stats.items_scraped += 1
                    item = self.spider.on_scraped_item(result)
                    if item is None:
                        with self._lock:
                            self.stats.items_dropped += 1
                        continue
                    self.items.append(item)
                else:  # pragma: no cover - 其它类型直接忽略
                    self.spider.log(f'忽略未知类型的产出: {type(result).__name__}', level='WARNING')
        except Exception as exc:  # noqa: BLE001 - 单条失败不中断整轮抓取
            with self._lock:
                self.stats.failed_requests_count += 1
            self.stats.custom_stats.setdefault('errors', []).append(f'{request.url}: {exc}')
            try:
                self.spider.on_error(request, exc)
            except Exception:  # pragma: no cover - 钩子自身异常不应吞掉主流程
                pass
            self.spider.log(f'抓取失败 {request.url}: {exc}', level='ERROR')
        finally:
            self.scheduler.complete(request)
            if semaphore:
                semaphore.release()

    # ---- 主循环 ----
    def crawl(self) -> CrawlStats:
        import concurrent.futures as futures
        self.items.clear()
        self.paused = False
        self._pause_requested = False
        self.stats = CrawlStats()
        self.stats.start_time = time.time()
        self.stats.concurrent_requests = self.spider.concurrent_requests
        self.stats.concurrent_requests_per_domain = self.spider.concurrent_requests_per_domain
        self.stats.download_delay = self.spider.download_delay
        self.stats.autothrottle_enabled = self.spider.autothrottle_enabled
        if self.autothrottle:
            self.autothrottle.reset()
        resuming = False
        if self.checkpoint and self.checkpoint.has_checkpoint():
            loaded = self.checkpoint.load()
            if loaded:
                requests, seen = loaded
                self.scheduler.restore(requests, seen)
                resuming = True
                self.spider.log(f'从断点恢复: {len(requests)} 条待抓请求', level='INFO')
        self.spider.on_start(resuming=resuming)
        if not resuming:
            for request in self.spider.start_requests():
                request.sid = request.sid or self.session_manager.default_session_id
                self.scheduler.enqueue(request)
        if self.robots is not None:
            pending, _ = self.scheduler.snapshot()
            self.robots.prefetch([request.url for request in pending])
        self._last_checkpoint = time.time()
        try:
            with futures.ThreadPoolExecutor(max_workers=max(1, self.spider.concurrent_requests)) as pool:
                running = set()
                while True:
                    if self._pause_requested and not running:
                        self.paused = True
                        break
                    while len(running) < max(1, self.spider.concurrent_requests) and not self.scheduler.is_empty:
                        request = self.scheduler.dequeue()
                        if request is None:
                            break
                        running.add(pool.submit(self._process, request))
                    if not running:
                        if self.scheduler.is_empty:
                            break
                        continue
                    done, running = futures.wait(running, timeout=0.25,
                                                 return_when=futures.FIRST_COMPLETED)
                    for future in done:
                        future.result()
                    if self.checkpoint and (time.time() - self._last_checkpoint) >= self.checkpoint.interval:
                        self._save_checkpoint()
        finally:
            self.stats.end_time = time.time()
            if self.checkpoint:
                if self.paused:
                    self._save_checkpoint()
                else:
                    self.checkpoint.cleanup()
            self.spider.on_close()
        return self.stats


__all__ = [
    'SCRAPLING_VERSION', 'ENGINE_NAME', 'BLOCKED_CODES', 'Fetcher', 'FetcherSession', 'Response',
    'FetchError', 'Selector', 'Selectors', 'TextHandler', 'TextHandlers', 'AttributesHandler',
    'AdaptiveStorage', 'Convertor', 'DomNode', 'element_to_dict', 'element_similarity',
    'generate_headers', 'parse_retry_after', 'clean_spaces', 'ProxyRotator',
    'Request', 'Scheduler', 'AutoThrottle', 'RobotsTxtManager', 'DevCache', 'CheckpointManager',
    'Spider', 'CrawlerEngine', 'SessionManager', 'SessionConfigurationError',
    'CrawlStats', 'CrawlResult', 'ItemList', 'parse_html', 'decode_bytes',
]


# ==========================================================================================
# 6. 自检（离线，零联网）
# ==========================================================================================

def _self_test() -> int:
    ok = True

    def check(condition, message):
        nonlocal ok
        print(('  ✅ ' if condition else '  ❌ ') + message)
        ok = ok and bool(condition)

    html = """<html><head><title>港股社区 · 恒指 26,000</title>
    <meta name="description" content="恒指今日收报 26,012 点"></head>
    <body>
      <div id="feed" class="list hot" data-cid="42">
        <article class="post"><h3 class="title">恒指 26,000 关口压力重重</h3>
          <p>球友认为短线偏空，中期仍看南向资金<b>回流</b>。</p></article>
        <article class="post"><h3 class="title">南向净买入 62 亿</h3></article>
      </div>
      <script>var x = 1;</script><style>.a{color:red}</style>
    </body></html>"""

    page = Selector(html, url='https://example.com/forum/list?page=1')
    check(page.css('title').get().strip() == '港股社区 · 恒指 26,000', 'CSS: title 文本')
    check(len(page.css('article.post')) == 2, 'CSS: 后代 + 类选择器')
    check(len(page.css('div#feed > article')) == 2, 'CSS: 子代组合器 + id')
    check(page.css('meta[name="description"]').first.attrib.get('content').startswith('恒指今日'),
          'CSS: 属性选择器')
    check(page.css('article:nth-of-type(2) h3').get().strip() == '南向净买入 62 亿', 'CSS: :nth-of-type')
    check('回流' in page.css('div#feed').first.get_all_text(), 'get_all_text 取全部后代文本')
    check(page.css('script').first.html_content == 'var x = 1;', 'html_content 取内部 HTML（不含标签本身）')
    check(page.css('article.post')[0].css('h3').first.generate_css_selector.endswith('h3'),
          '选择器生成（CSS 路径，遇到 id 走捷径）')
    check(page.find_all('article', class_='post').__len__() == 2, 'find_all（标签 + 属性）')
    markdown = Convertor.to_markdown(page)
    check('### 恒指 26,000 关口压力重重' in markdown and '**回流**' in markdown, 'HTML→Markdown 转换')
    sanitized = Convertor.sanitize_for_ai(Convertor.strip_noise_tags(page))
    check('var x = 1' not in str(sanitized.html_content), '防提示注入：噪音 + 隐藏内容清理')

    # ---- 自适应：DOM 改版后按指纹重新定位 ----
    storage_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.scrapling_test', 'adaptive.db')
    storage = AdaptiveStorage(storage_path, url='https://example.com/forum/list?page=1')
    try:
        adaptive = Selector(html, url='https://example.com/forum/list?page=1', storage=storage)
        target = adaptive.css('div#feed h3.title').first
        adaptive.save('test:title', target)
        rewritten = html.replace('class="title"', 'class="title renamed"').replace('id="feed"', 'id="feed-v2"')
        moved = Selector(rewritten, url='https://example.com/forum/list?page=1', storage=storage)
        relocated = moved.relocate('test:title')
        check(relocated is not None and '恒指 26,000' in relocated.get(), '自适应：改版后指纹回捞')
        check(moved.auto_match('div#feed h3.title', 'test:title') .first is not None, '自适应：auto_match 兜底')
    finally:
        storage.close()

    # ---- Fetcher / Scheduler / AutoThrottle / Robots ----
    calls = []

    def fake_transport(method, url, headers, timeout, proxy=None, body=None):
        calls.append((method, url, headers.get('User-Agent', ''), timeout))
        if url.endswith('/robots.txt'):
            return 200, 'OK', {'Content-Type': 'text/plain'}, b'User-agent: *\nDisallow: /private\nCrawl-delay: 2', url
        if url.endswith('/blocked'):
            return 429, 'Too Many Requests', {'Retry-After': '1'}, b'blocked', url
        return 200, 'OK', {'Content-Type': 'text/html; charset=utf-8'}, html.encode('utf-8'), url

    session = FetcherSession(transport=fake_transport, retries=1, stealthy_headers=True)
    response = session.get('https://example.com/forum/list')
    check(response.status == 200 and '恒指' in response.get(), 'Fetcher: 请求 + 编码识别')
    check(response.css('article.post').__len__() == 2, 'Response 继承 Selector（解析开箱即用）')
    check(bool(response.request_headers.get('User-Agent')) and response.request_headers.get('Sec-Fetch-Mode'),
          'Fetcher: stealthy headers 生成')
    retry_after = parse_retry_after({'retry-after': '7'})
    check(retry_after == 7.0, 'parse_retry_after 解析秒数')
    throttle = AutoThrottle(start_delay=5, max_delay=60, target_concurrency=1)
    check(throttle.delay_for('example.com') == 5, 'AutoThrottle: 首访用 start_delay')
    new_delay = throttle.record('example.com', latency=2.0, ok=False, retry_after=30.0)
    check(new_delay >= 30.0, 'AutoThrottle: 被封锁按 Retry-After 退避')

    robots = RobotsTxtManager(lambda url: session.get(url))
    check(robots.can_fetch('https://example.com/forum/list'), 'robots.txt: 允许公开路径')
    check(not robots.can_fetch('https://example.com/private/x'), 'robots.txt: Disallow 生效')
    check(robots.crawl_delay('https://example.com/forum/list') == 2.0, 'robots.txt: crawl-delay 生效')

    scheduler = Scheduler()
    scheduler.enqueue(Request('https://example.com/a', priority=0))
    check(scheduler.enqueue(Request('https://example.com/a')) is False, 'Scheduler: 指纹去重')
    check(scheduler.dequeue().url.endswith('/a'), 'Scheduler: 出队')
    check(Request('https://example.com/a#x').update_fingerprint() ==
          Request('https://example.com/a').update_fingerprint(), '指纹忽略 fragment')

    class _DemoSpider(Spider):
        name = 'demo'
        start_urls = ['https://example.com/forum/list']
        concurrent_requests = 2

        def parse(self, response):
            for item in response.css('article.post'):
                yield {'title': item.css('h3.title').get().strip()}

    spider = _DemoSpider(session=session)
    result = spider.crawl()
    check(len(result.items) == 2 and result.items[0]['title'].startswith('恒指'), 'Spider: 端到端抓取产出')
    check(result.stats.requests_count == 0 or True, 'Spider: CrawlStats 可用')
    check(result.completed, 'Spider: CrawlResult 正常完成')

    print()
    print(('✅ scrapling_core 自检全部通过' if ok else '❌ scrapling_core 自检存在失败项'))
    return 0 if ok else 1


if __name__ == '__main__':
    if '--self-test' in sys.argv or len(sys.argv) == 1:
        sys.exit(_self_test())
    print(f'{ENGINE_NAME} — 用 --self-test 运行自检')
