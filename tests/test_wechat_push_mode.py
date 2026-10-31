"""PushPlus 默认一对一及显式空群组覆盖的离线回归测试。"""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import wechat_push as wp


class WechatPushModeTests(unittest.TestCase):
    def test_page_defaults_to_one_to_one(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(wp.find_topic(str(ROOT / 'report.html')), '')
        html = (ROOT / 'report.html').read_text(encoding='utf-8')
        embedded = re.search(r'<script[^>]*id="wechat-parts"[^>]*>(.*?)</script>', html, re.S)
        self.assertEqual(json.loads(embedded.group(1))['mode'], 'one-to-one')
        self.assertNotIn('oai.1', html)
        self.assertIn("if (PUSHPLUS_TOPIC) body.append('topic', PUSHPLUS_TOPIC);", html)

    def test_explicit_empty_topic_overrides_environment(self):
        with patch.dict(os.environ, {'PUSHPLUS_TOPIC': 'old-group'}):
            self.assertEqual(wp.find_topic(str(ROOT / 'report.html'), ''), '')
            self.assertEqual(wp.find_topic(str(ROOT / 'report.html'), 'new-group'), 'new-group')
            self.assertEqual(wp.find_topic(str(ROOT / 'report.html')), 'old-group')

    def test_empty_environment_overrides_page_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'report.html'
            source.write_text("const PUSHPLUS_TOPIC = 'old-group';", encoding='utf-8')
            with patch.dict(os.environ, {'PUSHPLUS_TOPIC': ''}):
                self.assertEqual(wp.find_topic(str(source)), '')
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(wp.find_topic(str(source)), 'old-group')

    def test_one_to_one_request_omits_topic(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"code":200}'
        with patch.object(wp.urllib.request, 'urlopen', return_value=response) as send:
            self.assertEqual(wp.push_to_wechat('title', 'content', 'test-token')['code'], 200)
        body = json.loads(send.call_args.args[0].data)
        self.assertNotIn('topic', body)
        self.assertEqual(body['token'], 'test-token')

    def test_export_mode_matches_resolved_topic(self):
        for extra, env, expected in [
            ([], {}, 'one-to-one'),
            (['--topic', ''], {'PUSHPLUS_TOPIC': 'old-group'}, 'one-to-one'),
            (['--topic', 'group'], {}, 'one-to-many'),
        ]:
            with self.subTest(expected=expected, extra=extra), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / 'wechat.json'
                argv = ['wechat_push.py', '--emit', str(output), '--source', str(ROOT / 'report.html'),
                        '--rss-no-fetch'] + extra
                # wp.main() 会顺手做 05 栏补抓；CI 有真实出口时这会联网并把
                # rssfeeds_data.json 写进仓库根。本用例只关心推送群组模式。
                with patch.dict(os.environ, dict(env, RSSFEEDS_AUTO_FETCH='0'), clear=True), \
                        patch.object(wp, 'bootstrap_rss_feeds'), \
                        patch.object(sys, 'argv', argv), \
                        patch.object(wp, 'bootstrap_macro_data'), \
                        patch.object(wp, 'build_articles', return_value=([('title', 'content')], 'now', 'now')), \
                        contextlib.redirect_stdout(io.StringIO()):
                    wp.main()
                self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['mode'], expected)


if __name__ == '__main__':
    unittest.main()
