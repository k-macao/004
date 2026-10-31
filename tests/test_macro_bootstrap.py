#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建期宏观快讯自动补抓（macro_data.ensure）回归测试
====================================================

线上事实（2026-09-17 核查）：`.github/workflows/m.yml` 里没有 `macro_data.py` 这一步
（改动以 docs/macro-ci-workflow.patch 交付，需 workflows 权限手工应用，至今未应用），
所以每次构建都没有 macro_data.json，网页 02 节与微信 02 栏稳定显示
「今日未获取 · 未读到 macro_data.json（no_file）」。

本文件锁死「不改 workflow 也能拿到快讯」这条通路的边界：
  • 产物缺失 / 写坏 / 旧快照 → 就地补抓一次并落盘；
  • 当次已抓过但窗口内 0 条（no_items）→ 不重复烧网络；
  • 开关默认只在 CI 里开，本地/单测默认不联网，MACRO_AUTO_FETCH 可显式覆盖；
  • 补抓失败绝不抛异常、绝不阻断构建。
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, 'tools')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_site as bs          # noqa: E402
import macro_data as md          # noqa: E402
import wechat_push as wp         # noqa: E402

NOW = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)


def _stale_payload():
    """一份 40 天前构建的快照 —— availability() 判为 stale_snapshot。"""
    old = (NOW - timedelta(days=40)).strftime('%Y-%m-%d %H:%M:%S UTC')
    return {
        'generated_at': old, 'fetch_date': old[:10], 'mode': 'live',
        'categories': {'fed': {'label': '美联储利率路径与离岸流动性',
                               'items': [{'title': '旧快照里的旧闻 ABCXYZ',
                                          'published_date': old[:10]}]}},
        'summary': {'ok': 8, 'total': 8, 'kept_items': 1},
        'window': {'max_age_days': 7, 'since': old[:10]},
    }


def _empty_payload():
    """当次抓过但窗口内 0 条 —— availability() 判为 no_items。"""
    return {
        'generated_at': NOW.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'fetch_date': NOW.strftime('%Y-%m-%d'), 'mode': 'live',
        'categories': {c['key']: {'label': c['label'], 'items': []} for c in md.CATEGORIES},
        'summary': {'ok': 0, 'total': len(md.SOURCES), 'kept_items': 0},
        'window': {'max_age_days': 7, 'since': (NOW - timedelta(days=7)).strftime('%Y-%m-%d')},
    }


class _BuildSpy:
    """替换 md.build：记录调用次数，返回 mock 产物（离线，不联网）。"""

    def __init__(self, raises=False):
        self.calls = 0
        self.raises = raises
        self._orig = md.build

    def __enter__(self):
        def _fake(**kwargs):
            self.calls += 1
            if self.raises:
                raise RuntimeError('模拟抓取崩溃')
            return self._orig(mock=True, quiet=True, **{
                k: v for k, v in kwargs.items() if k not in ('mock', 'quiet')})
        md.build = _fake
        return self

    def __exit__(self, *exc):
        md.build = self._orig
        return False


class _MockBuild:
    """替换 md.build 为离线 mock 回放（正确转发全部 kwargs），记录调用次数。"""

    def __init__(self):
        self.calls = 0
        self._orig = None

    def __enter__(self):
        self._orig = md.build

        def _fake(**kw):
            self.calls += 1
            return self._orig(mock=True, quiet=True, **{
                k: v for k, v in kw.items() if k not in ('mock', 'quiet')})
        md.build = _fake
        return self

    def __exit__(self, *exc):
        md.build = self._orig
        return False


class _NoFetchBuild:
    """一旦有人试图抓取就失败 —— 用来断言「这条路径绝不联网」。"""

    def __init__(self):
        self._orig = None

    def __enter__(self):
        self._orig = md.build

        def _boom(**kw):
            raise AssertionError('渲染路径不得触发宏观快讯抓取')
        md.build = _boom
        return self

    def __exit__(self, *exc):
        md.build = self._orig
        return False


class _EnvGuard:
    """临时改写环境变量并在退出时**精确还原**（含"原本未设置"这一状态）。

    saved 必须存在 guard 自己身上：早先存在 test 实例上时，用例里再嵌套一次
    _env() 就会把外层的快照覆盖掉，清理时还原错值 —— SENTIMENT_DATA 会残留成
    一个已删除的临时路径，把后续 test_sentiment（依赖仓库根目录真实产物）带挂。
    """

    def __init__(self, env):
        self.env = env
        self._saved = None

    def __enter__(self):
        self._saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        return self

    def __exit__(self, *exc):
        self.restore()
        return False

    def restore(self):
        for k, v in (self._saved or {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._saved = {}


class TestAutoFetchSwitch(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ('MACRO_AUTO_FETCH', 'GITHUB_ACTIONS')}
        os.environ.pop('MACRO_AUTO_FETCH', None)
        os.environ.pop('GITHUB_ACTIONS', None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_explicit_argument_wins(self):
        self.assertTrue(md.auto_fetch_enabled(True))
        self.assertFalse(md.auto_fetch_enabled(False))

    def test_default_is_off_outside_ci(self):
        self.assertFalse(md.auto_fetch_enabled(None), '本地/单测默认不得联网')

    def test_ci_enables_by_default(self):
        os.environ['GITHUB_ACTIONS'] = 'true'
        self.assertTrue(md.auto_fetch_enabled(None))

    def test_env_flag_overrides_both(self):
        os.environ['GITHUB_ACTIONS'] = 'true'
        os.environ['MACRO_AUTO_FETCH'] = '0'
        self.assertFalse(md.auto_fetch_enabled(None))
        os.environ.pop('GITHUB_ACTIONS', None)
        os.environ['MACRO_AUTO_FETCH'] = '1'
        self.assertTrue(md.auto_fetch_enabled(None))


class TestEnsure(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, 'macro_data.json')

    def _write(self, payload):
        with open(self.path, 'w', encoding='utf-8') as f:
            if isinstance(payload, str):
                f.write(payload)
            else:
                json.dump(payload, f, ensure_ascii=False)

    def test_fresh_snapshot_is_not_refetched(self):
        self._write(md.build(mock=True, quiet=True))
        with _BuildSpy() as spy:
            res = md.ensure(self.path, now=NOW, allow_fetch=True)
        self.assertEqual(res['action'], 'fresh')
        self.assertEqual(spy.calls, 0)
        self.assertGreater(res['kept'], 0)

    def test_missing_file_is_fetched_and_persisted(self):
        with _BuildSpy() as spy:
            res = md.ensure(self.path, now=datetime.now(timezone.utc), allow_fetch=True)
        self.assertEqual(res['action'], 'fetched')
        self.assertEqual(spy.calls, 1)
        self.assertTrue(os.path.exists(self.path), '补抓产物必须落盘，微信端才能复用同一份')
        self.assertGreater(res['kept'], 0)
        # 落盘内容确实是当次可用产物（第二次调用直接判 fresh，不再抓）
        with _BuildSpy() as spy2:
            res2 = md.ensure(self.path, now=datetime.now(timezone.utc), allow_fetch=True)
        self.assertEqual(res2['action'], 'fresh')
        self.assertEqual(spy2.calls, 0)

    def test_stale_snapshot_is_refetched(self):
        self._write(_stale_payload())
        with _BuildSpy() as spy:
            res = md.ensure(self.path, now=NOW, allow_fetch=True)
        self.assertEqual(res['action'], 'fetched')
        self.assertEqual(spy.calls, 1)
        self.assertNotIn('ABCXYZ', json.dumps(res['data'], ensure_ascii=False),
                         '旧快照里的旧闻不得留在产物里')

    def test_bad_type_is_refetched(self):
        self._write('[1, 2, 3]')          # 半截/写坏的产物可能解析成 list
        self.assertEqual(md.availability(md.load_json(self.path))['reason'], 'bad_type')
        with _BuildSpy() as spy:
            res = md.ensure(self.path, now=datetime.now(timezone.utc), allow_fetch=True)
        self.assertEqual(res['action'], 'fetched')
        self.assertEqual(spy.calls, 1)

    def test_no_items_does_not_refetch(self):
        """当次已经抓过一轮且 0 条：再抓一遍只会拖长构建，必须跳过。"""
        self._write(_empty_payload())
        with _BuildSpy() as spy:
            res = md.ensure(self.path, now=NOW, allow_fetch=True)
        self.assertEqual(res['action'], 'no_refetch')
        self.assertEqual(res['reason'], 'no_items')
        self.assertEqual(spy.calls, 0)

    def test_fetch_disabled_leaves_file_untouched(self):
        with _BuildSpy() as spy:
            res = md.ensure(self.path, now=NOW, allow_fetch=False)
        self.assertEqual(res['action'], 'disabled')
        self.assertEqual(res['reason'], 'no_file')
        self.assertEqual(spy.calls, 0)
        self.assertFalse(os.path.exists(self.path))

    def test_fetch_crash_never_propagates(self):
        with _BuildSpy(raises=True) as spy:
            res = md.ensure(self.path, now=NOW, allow_fetch=True)
        self.assertEqual(res['action'], 'error')
        self.assertEqual(spy.calls, 1)
        self.assertIn('模拟抓取崩溃', res.get('detail', ''))
        self.assertFalse(os.path.exists(self.path), '崩溃时不得留下半截产物')

    def test_deadline_skips_remaining_sources(self):
        """墙钟预算用尽时，剩余源记 skipped_deadline 而不是把构建拖死。"""
        data = md.build(mock=True, quiet=True, deadline=time_past())
        self.assertEqual(data['summary']['ok'], 0)
        self.assertTrue(all((m.get('error') or '').startswith('skipped_deadline')
                            for m in data['sources']))


def time_past():
    import time as _t
    return _t.time() - 1


class TestWechatAndWebShareBootstrappedData(unittest.TestCase):
    """补抓出来的产物必须同时喂到微信 02 栏与网页 02 节（同一事实源）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, 'macro_data.json')
        # 把行情/社区/舆情指向不存在的文件，让渲染路径确定；guard 在 cleanup 里精确还原 ——
        # 否则残留的 SENTIMENT_DATA 会污染后续 test_sentiment（它依赖仓库根目录的真实产物）。
        missing = os.path.join(self._tmp.name, 'missing.json')
        guard = _EnvGuard({'MARKET_DATA': missing, 'COMMUNITY_DATA': missing,
                           'SENTIMENT_DATA': missing})
        guard.__enter__()
        self.addCleanup(guard.restore)

    def _env(self, env):
        return _EnvGuard(env)

    def test_cli_entry_bootstraps_when_missing(self):
        """补抓只发生在 CLI 入口 bootstrap_macro_data()，且确实把产物落盘。"""
        with self._env({'MACRO_DATA': self.path, 'MACRO_AUTO_FETCH': '1'}):
            with _MockBuild() as spy:
                res = wp.bootstrap_macro_data()
            data = wp.load_macro_data()
        self.assertEqual(res['action'], 'fetched')
        self.assertEqual(spy.calls, 1)
        self.assertGreater(res['kept'], 0, '补抓后微信 02 栏应拿到当次快讯')
        self.assertTrue(os.path.exists(self.path))
        kept = sum(len((b or {}).get('items') or [])
                   for b in (data.get('categories') or {}).values())
        self.assertGreater(kept, 0, '入口补抓后，纯读取的 load_macro_data 应能读到同一份产物')

    def test_render_path_never_fetches_even_in_ci(self):
        """回归防线：渲染路径（单测直接调用的库函数）在任何环境下都不得联网。

        2026-09-17 事故：补抓曾藏在 load_macro_data() 里，而它被
        build_single_wechat_html() 调用。CI 里 GITHUB_ACTIONS=true 默认开补抓，
        于是「四路数据全缺 → 必须降级」这类断言在 CI 抓到真快讯后集体挂掉，
        deploy job 的「🧪 舆情层自检」步骤直接红。本地无外网，复现不出来。
        """
        with self._env({'MACRO_DATA': self.path, 'MACRO_AUTO_FETCH': '1',
                        'GITHUB_ACTIONS': 'true'}):
            with _NoFetchBuild():
                self.assertEqual(wp.load_macro_data(), {}, '缺产物时应返回 {}，而不是去抓')
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    html, _t, _tf = wp.build_single_wechat_html()
        self.assertFalse(os.path.exists(self.path), '渲染过程不得凭空造出产物')
        plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))
        self.assertIn('宏观快讯 — 今日未获取', plain)


class TestBuildSiteEndToEnd(unittest.TestCase):
    """真正跑一遍 build_site.py：02 节必须由补抓到的快讯渲染，而不是「今日未获取」。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.macro = os.path.join(self._tmp.name, 'macro_data.json')
        self.rss = os.path.join(self._tmp.name, 'rssfeeds_data.json')
        self.out = os.path.join(self._tmp.name, 'out.html')
        self.missing = os.path.join(self._tmp.name, 'missing.json')

    def _run(self, *extra):
        # Only macro bootstrap is under test; RSS must stay offline even in CI.
        result = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, 'build_site.py'),
             '--template', os.path.join(REPO_ROOT, 'report.html'),
             '--out', self.out, '--data', self.missing, '--community', self.missing,
             '--sentiment', self.missing, '--macro', self.macro,
             '--rss', self.rss, '--rss-no-fetch', *extra],
            capture_output=True, text=True, timeout=180,
            env=dict(os.environ, GITHUB_ACTIONS='true', MACRO_AUTO_FETCH='0', RSSFEEDS_AUTO_FETCH='1'),
            cwd=self._tmp.name)
        self.assertFalse(os.path.exists(self.rss), '宏观快讯测试不得触发 RSS 补抓')
        return result

    def test_bootstrap_renders_real_items_into_section_02(self):
        p = self._run('--macro-mock', '--macro-fetch')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.exists(self.macro), '构建期补抓应把产物落到 --macro 指定路径')
        with open(self.out, encoding='utf-8') as f:
            page = f.read()
        block = page.split(bs.MACROLIST_MARK, 1)[1].split(bs.MACROLIST_CLOSE, 1)[0]
        self.assertNotIn('今日未获取', block)
        self.assertIn('时效窗口', block)
        self.assertIn('每次构建现抓', block)
        self.assertRegex(block, r'\[\d{1,2} 月 \d{1,2} 日\]', '每条快讯应自带发布日期')

    def test_without_bootstrap_the_page_still_degrades_explicitly(self):
        p = self._run()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(os.path.exists(self.macro), '关掉补抓时不得凭空造产物')
        with open(self.out, encoding='utf-8') as f:
            page = f.read()
        block = page.split(bs.MACROLIST_MARK, 1)[1].split(bs.MACROLIST_CLOSE, 1)[0]
        self.assertIn('今日未获取', block)
        self.assertIn('no_file', block)

    def test_check_mode_never_fetches(self):
        p = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, 'build_site.py'),
             '--check', '--template', os.path.join(REPO_ROOT, 'report.html'),
             '--data', self.missing, '--community', self.missing,
             '--sentiment', self.missing, '--macro', self.macro],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, GITHUB_ACTIONS='true', MACRO_AUTO_FETCH=''),
            cwd=self._tmp.name)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(os.path.exists(self.macro), '--check 是纯校验，绝不联网补抓')


if __name__ == '__main__':
    unittest.main()
