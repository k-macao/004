# -*- coding: utf-8 -*-
"""Regression checks for the published report's identity and real timestamps.

A failed Pages deployment leaves the previously published HTML untouched. A
wrong project URL can also send readers to a different, older report. Neither
case should be hidden by replacing the capture/build time with the visitor's
current clock. These tests run without network access or a GitHub credential.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import build_site as bs
from tools import wechat_push as wp

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_URL = 'https://k-macao.github.io/004/'
NOW = datetime(2026, 11, 5, 12, 30, 45, tzinfo=timezone.utc)
OLD_CAPTURE = '2026-11-02 10:07:00 UTC'


def old_market():
    return {
        'generated_at': OLD_CAPTURE,
        'fetch_date': '2026-11-02',
        'mode': 'live',
        'quotes': {
            'HSI': {
                'name': '恒生指数', 'unit': '点', 'decimals': 2,
                'last': 25000.0, 'prev_close': 24900.0,
                'chg': 100.0, 'pct': 0.4,
                'as_of': '2026-11-02', 'source': 'yahoo',
            },
        },
        'summary': {'ok': 1, 'total': 1, 'failed': []},
    }


class TestReportTimestamps(unittest.TestCase):
    def test_build_time_is_current_without_rewriting_source_dates(self):
        data = old_market()
        tokens = bs.build_tokens(data, NOW)
        self.assertEqual(tokens['{{TS_FULL}}'], '2026-11-05 12:30:45 UTC')
        self.assertEqual(tokens['{{FETCH_DATE}}'], '2026-11-02')
        self.assertEqual(tokens['{{HSI_ASOF}}'], '2026-11-02')
        self.assertIn(OLD_CAPTURE, tokens['{{FETCH_STATUS}}'])
        self.assertEqual(data, old_market(), 'rendering must not mutate capture metadata')

    def test_visible_header_and_footer_use_build_time_not_old_capture(self):
        template = (REPO_ROOT / 'report.html').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_path = root / 'market.json'
            data_path.write_text(json.dumps(old_market()), encoding='utf-8')
            source = root / 'template.html'
            source.write_text(template, encoding='utf-8')
            output = root / 'index.html'
            argv = ['build_site.py', '--template', str(source), '--out', str(output),
                    '--data', str(data_path), '--community', str(root / 'missing-community.json'),
                    '--sentiment', str(root / 'missing-sentiment.json'),
                    '--macro', str(root / 'missing-macro.json'), '--macro-no-fetch',
                    '--rss', str(root / 'missing-rss.json'), '--rss-no-fetch',
                    '--forecast-no-history']
            # Exercise the real CLI render path even on an Actions runner.
            with (mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}),
                  mock.patch('sys.argv', argv),
                  mock.patch.object(bs, 'datetime', wraps=datetime) as clock,
                  contextlib.redirect_stdout(io.StringIO()),
                  contextlib.redirect_stderr(io.StringIO())):
                clock.now.return_value = NOW
                bs.main()
            html = output.read_text(encoding='utf-8')
            self.assertEqual(json.loads(data_path.read_text(encoding='utf-8')), old_market())

        stamp = NOW.strftime('%Y-%m-%d %H:%M:%S UTC')
        header = re.search(r'<div class="masthead-date">([^<]+)</div>', html)
        self.assertIsNotNone(header)
        self.assertEqual(header.group(1), stamp)
        footer = re.search(r'<p id="pageGenTime">(.*?)</p>', html, re.S)
        self.assertIsNotNone(footer)
        self.assertIn(f'生成时间：{stamp}', footer.group(1))
        self.assertNotIn('{{TS_FULL}}', html)
        self.assertNotIn(OLD_CAPTURE, html, 'capture status text is hidden while build time is current')
        self.assertIn('<td>2026-11-02</td>', html, 'the exchange date is not relabelled as today')


class TestReportDestination(unittest.TestCase):
    def test_page_push_payload_and_readme_use_this_repository(self):
        self.assertEqual(wp.PAGES_URL, REPORT_URL)
        template = (REPO_ROOT / 'report.html').read_text(encoding='utf-8')
        embedded = re.search(
            r'<script id="wechat-parts" type="application/json">\s*(.*?)\s*</script>',
            template, re.S,
        )
        self.assertIsNotNone(embedded)
        self.assertEqual(json.loads(embedded.group(1))['pages_url'], REPORT_URL)
        readme = (REPO_ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn(REPORT_URL, readme)
        for text in (template, readme, (REPO_ROOT / 'tools/wechat_push.py').read_text(encoding='utf-8')):
            self.assertNotIn('https://k-macao.github.io/03/', text)


if __name__ == '__main__':
    unittest.main()
