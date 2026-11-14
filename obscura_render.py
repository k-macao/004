"""Optional Obscura renderer for already-verified public HTML pages.

Never use this as an HTTP transport: CLI eval does not expose navigation status,
response headers, or redirect history. The caller must retain its original HTTP
metadata and must not use rendered output for authenticated/API requests.
"""
import json
import math
import os
import shutil
import subprocess
from urllib.parse import urlsplit

MAX_HTML = 4 * 1024 * 1024
EXPRESSION = 'JSON.stringify({url:location.href,html:document.documentElement.outerHTML})'


def executable():
    return shutil.which(os.environ.get('OBSCURA_BIN', 'obscura'))


def render(url, timeout=10, binary=None):
    """Return rendered HTML or raise; reject redirects and non-HTTP URLs."""
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('invalid page URL')
    binary = binary or executable()
    if not binary:
        raise FileNotFoundError('obscura not installed')
    seconds = max(1, math.ceil(float(timeout)))
    cmd = [binary, 'fetch', url, '--quiet', '--timeout', str(seconds), '--eval', EXPRESSION]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=seconds + 2, check=True)
    if len(proc.stdout) > MAX_HTML:
        raise ValueError('rendered output exceeds limit')
    data = json.loads(proc.stdout)
    if not isinstance(data, dict) or data.get('url') != url:
        raise ValueError('rendered page URL changed')
    html = data.get('html')
    if not isinstance(html, str) or not html.strip():
        raise ValueError('empty rendered page')
    return html
