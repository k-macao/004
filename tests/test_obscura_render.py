import json
import subprocess
import unittest
from unittest.mock import patch

import obscura_render
import community_spider as spider
import scrapling_core as sc


class ObscuraTests(unittest.TestCase):
    def test_render_command_and_redirect_rejection(self):
        payload = {'url': 'https://example.org/', 'html': '<html><body>港股上涨</body></html>'}
        with patch('obscura_render.subprocess.run') as run:
            run.return_value.stdout = json.dumps(payload).encode()
            self.assertIn('港股', obscura_render.render(payload['url'], 4, '/bin/obscura'))
            self.assertIn('--eval', run.call_args.args[0])
            payload['url'] = 'https://other.example/'
            run.return_value.stdout = json.dumps(payload).encode()
            with self.assertRaises(ValueError):
                obscura_render.render('https://example.org/', 4, '/bin/obscura')

    def test_timeout_and_invalid_url(self):
        with self.assertRaises(ValueError):
            obscura_render.render('file:///etc/passwd', binary='/bin/obscura')
        with patch('obscura_render.subprocess.run', side_effect=subprocess.TimeoutExpired('obscura', 1)):
            with self.assertRaises(subprocess.TimeoutExpired):
                obscura_render.render('https://example.org/', binary='/bin/obscura')

    def test_spider_uses_render_or_static_fallback(self):
        page = sc.Response(url='https://example.org/', content='<html><body><p>港股下跌市场投资者关注利率变化和经济前景走势。</p></body></html>', status=200, reason='OK')
        page.request = sc.Request(page.url, meta={'key': 'TEST'})
        s = spider.CommunitySpider([], adaptive=False, obscura_binary='/bin/obscura')
        with patch('obscura_render.render', return_value='<html><body><p>港股上涨市场投资者关注利率变化和经济前景走势。</p></body></html>'):
            result = list(s.parse(page))[0]
        self.assertEqual(result['strategy'], 'obscura_page')
        self.assertEqual(result['http_status_source'], 'static_preflight')
        self.assertIn('上涨', result['snippet'])
        with patch('obscura_render.render', side_effect=ValueError):
            result = list(s.parse(page))[0]
        self.assertEqual(result['strategy'], 'scrapling_page')
        self.assertIn('下跌', result['snippet'])

    def test_non_success_not_rendered(self):
        page = sc.Response(url='https://example.org/', content='blocked', status=403, reason='Forbidden')
        page.request = sc.Request(page.url, meta={'key': 'TEST'})
        s = spider.CommunitySpider([], adaptive=False, obscura_binary='/bin/obscura')
        with patch('obscura_render.render') as render:
            list(s.parse(page))
            render.assert_not_called()


if __name__ == '__main__':
    unittest.main()
