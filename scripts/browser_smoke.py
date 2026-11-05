#!/usr/bin/env python3
"""Optional actual browser check on a synthetic loopback-only page. No captures."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlparse

HTML = b'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Synthetic check</title>
<body><main><h1>Equipment reservation</h1><p>This is a synthetic runtime check, not an assessment app.</p>
<form id="f" novalidate><label for="name">Item name</label><input id="name" autocomplete="off">
<button type="submit">Reserve item</button></form><p role="alert" id="error" hidden></p>
<p role="status" id="status">No reservation yet</p><ul aria-label="Reservations" id="items"></ul>
<button id="reset" type="button">Clear reservations</button></main>
<script>
f.onsubmit = e => {e.preventDefault();const value=document.querySelector('#name').value.trim();
const er=document.querySelector('#error');if(!value){er.hidden=false;er.textContent='Enter an item name';return;}
er.hidden=true;const li=document.createElement('li');li.textContent=value;
document.querySelector('#items').append(li);document.querySelector('#status').textContent='Reservation created';};
document.querySelector('#reset').onclick=()=>{document.querySelector('#items').replaceChildren();
document.querySelector('#status').textContent='No reservation yet';};
</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(HTML)))
        self.end_headers()
        self.wfile.write(HTML)
    def log_message(self, *args):
        pass

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--executable', help='Existing allowed Chromium executable; no auto-download')
    a = p.parse_args()
    if a.executable and not Path(a.executable).is_file():
        print(json.dumps({'status': 'not-run', 'reason': 'Executable does not exist'}))
        return 2
    try:
        from playwright.sync_api import sync_playwright, expect
    except ImportError:
        print(json.dumps({'status': 'not-run', 'reason': 'Python Playwright is not installed. Use an existing allowed browser tool or obtain permission before installing.'}))
        return 2
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/'
    try:
        with sync_playwright() as pw:
            options = {'headless': True, 'chromium_sandbox': True, 'timeout': 15000}
            if a.executable:
                options['executable_path'] = a.executable
            browser = pw.chromium.launch(**options)
            try:
                context = browser.new_context(accept_downloads=False)
                def local_only(route):
                    target = urlparse(route.request.url)
                    if target.hostname == '127.0.0.1' and target.port == server.server_port:
                        route.continue_()
                    else:
                        route.abort()
                context.route('**/*', local_only)
                page = context.new_page()
                page.set_default_timeout(5000)
                page.goto(url, wait_until='domcontentloaded')
                expect(page.get_by_role('heading', name='Equipment reservation')).to_be_visible()
                page.get_by_role('button', name='Reserve item', exact=True).click()
                expect(page.get_by_role('alert')).to_have_text('Enter an item name')
                page.get_by_label('Item name').fill('Synthetic projector')
                page.get_by_role('button', name='Reserve item', exact=True).click()
                expect(page.get_by_role('status')).to_have_text('Reservation created')
                expect(page.get_by_role('listitem')).to_have_text('Synthetic projector')
                page.get_by_role('button', name='Clear reservations').click()
                expect(page.get_by_role('listitem')).to_have_count(0)
                expect(page.get_by_role('status')).to_have_text('No reservation yet')
                context.close()
            finally:
                browser.close()
        print(json.dumps({'status': 'pass', 'checks': ['page opened', 'empty input rejected', 'input and click', 'result observed', 'reset observed'],
                          'screenshots': False, 'video': False, 'trace': False, 'external_page_requests': False,
                          'limits': 'Synthetic browser operation only; not Codex recognition, product visual quality, human usability, or exam permission.'}, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'not-verified', 'reason': ('Chromium OS sandbox is unavailable in this environment' if 'sandbox' in str(exc).lower() else str(exc).splitlines()[0][:300]),
                          'action': 'Do not disable sandbox or change system permissions. Use an already permitted tool or report the limitation.'}, ensure_ascii=False, indent=2))
        return 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
if __name__ == '__main__':
    raise SystemExit(main())
