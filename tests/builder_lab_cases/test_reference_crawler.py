import asyncio
import base64
import gzip
from io import BytesIO
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit

import builder_lab.reference_crawler as reference_crawler_module
from PIL import Image

from builder_lab.reference_crawler import (
    CaptureSettings,
    CrawlAccumulator,
    CrawlByteBudget,
    GuardedUrl,
    LinkCandidate,
    ReferenceCrawlLimits,
    UnsafeReferenceUrl,
    UrlGuard,
    VisualReferenceCrawler,
    browser_unavailable_reason,
    capture_reference_page,
    select_reference_pages,
)
from builder_lab.reference_models import ReferencePageEvidence


PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"
ROOT = Path(__file__).resolve().parents[2]


class StaticResolver:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, host):
        self.calls.append(host)
        value = self.answers[host]
        if isinstance(value, list) and value and isinstance(value[0], (list, tuple)):
            return value.pop(0)
        return value


class UrlGuardTests(unittest.TestCase):
    def test_accepts_public_http_https_and_resolves_every_address(self):
        resolver = StaticResolver({"example.com": [PUBLIC_V4, PUBLIC_V6]})
        guarded = UrlGuard(resolver=resolver).validate("https://example.com/a?b=1")
        self.assertEqual(
            guarded,
            GuardedUrl(
                url="https://example.com/a?b=1",
                host="example.com",
                port=443,
                addresses=(PUBLIC_V4, PUBLIC_V6),
            ),
        )

    def test_rejects_scheme_credentials_and_non_web_port(self):
        guard = UrlGuard(resolver=lambda _host: [PUBLIC_V4])
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/file",
            "https://user:secret@example.com/",
            "https://example.com:8443/",
        ):
            with self.subTest(url=url), self.assertRaises(UnsafeReferenceUrl):
                guard.validate(url)

    def test_rejects_private_special_and_known_metadata_destinations(self):
        unsafe = (
            "127.0.0.1",
            "10.0.0.1",
            "169.254.1.1",
            "224.0.0.1",
            "0.0.0.0",
            "192.0.2.1",
            "169.254.169.254",
            "100.100.100.200",
            "::1",
            "fe80::1",
            "ff02::1",
            "fd00:ec2::254",
        )
        for address in unsafe:
            guard = UrlGuard(resolver=lambda _host, address=address: [address])
            with self.subTest(address=address), self.assertRaises(UnsafeReferenceUrl):
                guard.validate("https://example.com/")
        with self.assertRaises(UnsafeReferenceUrl):
            UrlGuard(resolver=lambda _host: [PUBLIC_V4]).validate(
                "http://metadata.google.internal/computeMetadata/v1/"
            )

    def test_revalidates_redirect_and_detects_dns_rebinding(self):
        resolver = StaticResolver(
            {"example.com": [[PUBLIC_V4], ["127.0.0.1"]]}
        )
        guard = UrlGuard(resolver=resolver)
        guard.validate("https://example.com/")
        with self.assertRaisesRegex(UnsafeReferenceUrl, "public"):
            guard.validate_redirect("https://example.com/after")
        self.assertEqual(resolver.calls, ["example.com", "example.com"])

    def test_rejects_mixed_public_private_dns_answer(self):
        guard = UrlGuard(resolver=lambda _host: [PUBLIC_V4, "10.0.0.1"])
        with self.assertRaises(UnsafeReferenceUrl):
            guard.validate("https://example.com/")


class SelectionTests(unittest.TestCase):
    def test_selects_home_and_prioritized_same_origin_categories(self):
        candidates = [
            LinkCandidate("https://example.com/random", "News"),
            LinkCandidate("https://example.com/contacts/", "Контакты"),
            LinkCandidate("https://example.com/projects/", "Портфолио"),
            LinkCandidate("https://example.com/services/", "Услуги и цены"),
            LinkCandidate("https://example.com/faq/", "FAQ"),
            LinkCandidate("https://evil.example/contacts", "Contacts"),
            LinkCandidate("https://example.com/projects/#top", "Duplicate"),
            LinkCandidate("https://example.com:bad/", "Malformed"),
        ]

        selected = select_reference_pages(
            "https://example.com/", candidates, max_pages=5
        )

        self.assertEqual([item.category for item in selected], [
            "home", "services", "portfolio", "faq", "contacts"
        ])
        self.assertEqual(len({item.url for item in selected}), 5)

    def test_limits_are_bounded_and_robots_default_on(self):
        limits = ReferenceCrawlLimits()
        self.assertTrue(limits.respect_robots)
        self.assertEqual(limits.max_pages, 5)
        self.assertEqual(limits.concurrency_per_host, 1)
        self.assertEqual(limits.max_scroll_steps, 40)
        self.assertEqual(limits.total_timeout_seconds, 600)
        self.assertEqual(limits.max_page_bytes, 25 * 1024 * 1024)
        self.assertEqual(limits.max_total_bytes, 100 * 1024 * 1024)
        with self.assertRaises(ValueError):
            ReferenceCrawlLimits(max_pages=6)
        with self.assertRaises(ValueError):
            ReferenceCrawlLimits(scroll_delay_ms=599)
        with self.assertRaises(ValueError):
            ReferenceCrawlLimits(scroll_delay_ms=1201)

    def test_two_pass_request_handler_uses_whole_crawl_timeout(self):
        limits = ReferenceCrawlLimits(page_timeout_seconds=45)

        timeout = reference_crawler_module._request_handler_timeout(limits)

        self.assertEqual(timeout.total_seconds(), 600)

    def test_attempts_mobile_and_screenshots_share_one_monotonic_budget(self):
        budget = CrawlByteBudget(100)
        budget.consume(
            55, source="failed-desktop-network", charge_id="attempt-1:response"
        )
        budget.consume(
            55, source="duplicate-error-handler", charge_id="attempt-1:response"
        )
        budget.consume(30, source="mobile-network", charge_id="mobile:response")
        with self.assertRaisesRegex(Exception, "crawl byte limit"):
            budget.consume(
                20, source="mobile-screenshot", charge_id="mobile:screenshot"
            )
        self.assertEqual(budget.used_bytes, 105)

    def test_retry_accumulator_commits_only_after_enqueue_succeeds(self):
        accumulator = CrawlAccumulator(
            max_total_bytes=1024 * 1024,
            selected_urls={"https://example.com/"},
        )
        evidence = ReferencePageEvidence(
            page_id="home",
            category="home",
            requested_url="https://example.com/",
            final_url="https://example.com/",
            depth=0,
            transferred_bytes=100,
        )
        attempts = 0

        async def enqueue():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("transient enqueue failure")

        with self.assertRaisesRegex(RuntimeError, "transient"):
            asyncio.run(
                accumulator.commit(
                    evidence=evidence,
                    child_urls=("https://example.com/services",),
                    enqueue=enqueue,
                )
            )
        self.assertEqual(accumulator.pages, ())
        self.assertEqual(accumulator.total_bytes, 0)
        self.assertNotIn("https://example.com/services", accumulator.selected_urls)

        asyncio.run(
            accumulator.commit(
                evidence=evidence,
                child_urls=("https://example.com/services",),
                enqueue=enqueue,
            )
        )
        asyncio.run(
            accumulator.commit(
                evidence=evidence,
                child_urls=("https://example.com/services",),
                enqueue=enqueue,
            )
        )
        self.assertEqual(len(accumulator.pages), 1)
        self.assertEqual(accumulator.total_bytes, 100)
        self.assertEqual(attempts, 2)


class RunScopedStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_domain_queue_is_fresh_for_each_crawl(self):
        storage_type = getattr(
            reference_crawler_module, "_RunScopedMemoryStorageClient", None
        )
        self.assertIsNotNone(
            storage_type,
            "reference crawls need run-scoped Crawlee storage isolation",
        )

        from crawlee import Request
        from crawlee.storages import RequestQueue

        first = await RequestQueue.open(
            alias="throttled-qlean.ru",
            storage_client=storage_type(),
        )
        second = await RequestQueue.open(
            alias="throttled-qlean.ru",
            storage_client=storage_type(),
        )
        try:
            self.assertIsNot(first, second)
            await first.add_request(Request.from_url("https://qlean.ru/"))
            pending = await first.fetch_next_request()
            self.assertIsNotNone(pending)
            await first.mark_request_as_handled(pending)

            repeated = await second.add_request(Request.from_url("https://qlean.ru/"))
            self.assertIsNotNone(repeated)
            self.assertFalse(repeated.was_already_present)
            self.assertFalse(repeated.was_already_handled)
        finally:
            await first.drop()
            await second.drop()


class ReferenceCliTests(unittest.TestCase):
    def test_cli_fails_closed_before_browser_for_unsafe_url(self):
        with TemporaryDirectory() as temp_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "capture_reference_site.py"),
                    "http://127.0.0.1/",
                    "--output-dir",
                    temp_dir,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["failure"]["code"], "unsafe_url")

    def test_cli_stdout_is_utf8_json_even_when_python_io_is_cp1251(self):
        with TemporaryDirectory() as temp_dir:
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "cp1251:strict"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "capture_reference_site.py"),
                    "file:///reference-U0001f4a5",
                    "--output-dir",
                    temp_dir,
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=False,
                timeout=30,
                check=False,
            )
        self.assertEqual(completed.returncode, 2, completed.stderr.decode(errors="replace"))
        payload = json.loads(completed.stdout.decode("utf-8"))
        self.assertEqual(payload["status"], "failed")


class LazyFixtureHandler(BaseHTTPRequestHandler):
    hit_counts = {}
    external_target = ""
    fail_services = False

    def do_HEAD(self):
        path = self.path.split("?", 1)[0]
        self.hit_counts[path] = self.hit_counts.get(path, 0) + 1
        redirects = {
            "/doc-start": "/doc-second",
            "/doc-second": self.external_target,
            "/loop-a": "/loop-b",
            "/loop-b": "/loop-a",
        }
        if path in redirects:
            self.send_response(302)
            self.send_header("Location", redirects[path])
        else:
            self.send_response(200)
            self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        self.hit_counts[path] = self.hit_counts.get(path, 0) + 1
        if path == "/cross-redirect":
            self.send_response(302)
            self.send_header("Location", self.external_target)
            self.end_headers()
            return
        redirects = {
            "/doc-start": "/doc-second",
            "/doc-second": self.external_target,
            "/loop-a": "/loop-b",
            "/loop-b": "/loop-a",
            "/asset-safe-start": "/asset-safe-second",
            "/asset-safe-second": "/lazy.png",
            "/asset-private-start": "/asset-private-second",
            "/asset-private-second": self.external_target,
        }
        if path in redirects:
            self.send_response(302)
            self.send_header("Location", redirects[path])
            self.end_headers()
            return
        if path == "/services" and self.fail_services:
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            content_type = "text/plain"
        elif path == "/lazy.png":
            time.sleep(0.9)
            body = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
            content_type = "image/png"
        elif path == "/large-first":
            if self.hit_counts[path] == 1:
                body = b"<html><h1>Large initial</h1>" + (b"x" * 270_000) + b"</html>"
            else:
                body = b"<html><h1>Small retry</h1></html>"
            content_type = "text/html; charset=utf-8"
        elif path == "/popup":
            body = (
                "<!doctype html><h1>Popup host</h1><script>window.open("
                + json.dumps(self.external_target)
                + ", '_blank')</script>"
            ).encode()
            content_type = "text/html; charset=utf-8"
        elif path == "/cross-origin-frame":
            body = (
                "<!doctype html><h1>Safe host with external support frame</h1>"
                "<iframe src="
                + json.dumps(self.external_target)
                + "></iframe>"
            ).encode()
            content_type = "text/html; charset=utf-8"
        elif path == "/redirect-assets":
            body = b"""<!doctype html><meta charset='utf-8'>
            <style>img{width:100px;height:100px;display:block}</style>
            <h1>Redirected assets</h1>
            <img src='/asset-safe-start' alt='redirect-safe'>
            <img src='/asset-private-start' alt='redirect-private'>"""
            content_type = "text/html; charset=utf-8"
        elif path == "/nested":
            body = b"""<!doctype html><meta charset='utf-8'>
            <style>
              html,body{margin:0;height:100%;overflow:hidden}
              #panel{position:fixed;left:980px;top:80px;width:360px;height:700px;overflow:auto;background:#eee}
              .spacer{height:650px}.reveal{height:300px;opacity:0}.reveal.seen{opacity:1}.tail{height:900px}
            </style>
            <h1>Off center shell</h1><aside id='panel'><div class='spacer'></div>
              <section class='reveal'><h2>Nested wheel fired</h2></section><div class='tail'></div></aside>
            <script>
              document.querySelector('#panel').addEventListener('wheel',()=>{
                document.querySelector('.reveal').classList.add('seen');
                document.body.dataset.nestedWheel='yes';
              },{passive:true});
            </script>"""
            content_type = "text/html; charset=utf-8"
        elif path == "/lazy-prewarm":
            body = b"""<!doctype html><meta charset='utf-8'>
            <style>
              body{margin:0}.spacer{height:7200px}
              img{display:block;width:120px;height:120px}.tail{height:900px}
            </style>
            <h1>Lazy prewarm fixture</h1><div class='spacer'></div>
            <img id='late' loading='lazy' src='/lazy.png' alt='late'>
            <div class='tail'></div>
            <script>
              addEventListener('wheel',()=>{
                document.body.dataset.wheels=String(
                  1 + +(document.body.dataset.wheels || 0)
                );
              },{passive:true});
            </script>"""
            content_type = "text/html; charset=utf-8"
        elif path == "/lazy-once":
            body = b"""<!doctype html><meta charset='utf-8'><style>body{margin:0}.spacer{height:500px}img{display:block;width:100px;height:100px}.tail{height:900px}</style>
            <h1>Lazy image fixture</h1><div class='spacer'></div><img id='late' loading='lazy' alt='late'><div class='tail'></div>
            <script>addEventListener('wheel',()=>{if(!late.src)late.src='/lazy.png'},{passive:true})</script>"""
            content_type = "text/html; charset=utf-8"
        elif path == "/virtual-delayed":
            body = b"""<!doctype html><meta charset='utf-8'>
            <style>
              html,body{margin:0;height:100%;overflow:hidden;background:white}
              #scene{will-change:transform}.tail{height:900px;background:#ddd}
            </style>
            <main id='scene'><h1>Delayed virtual layout</h1>
              <section id='real' hidden><h2>Real virtual end</h2></section>
              <div class='tail'>Tail</div></main>
            <script>
              let wheels=0, offset=0;
              const scene=document.querySelector('#scene');
              addEventListener('wheel',(event)=>{
                wheels += 1;
                offset=Math.max(0,Math.min(500,offset+event.deltaY));
                if(wheels >= 4 && offset >= 500) {
                  real.hidden=false;
                  scene.style.transform='translateY(-500px)';
                } else if(offset === 0) {
                  real.hidden=true;
                  scene.style.transform='none';
                }
              },{passive:true});
            </script>"""
            content_type = "text/html; charset=utf-8"
        elif path == "/native-overflow-transform":
            body = b"""<!doctype html><meta charset='utf-8'>
            <style>
              html{margin:0;overflow-y:auto}
              body{margin:0;overflow-y:hidden;background:white}
              #animated{transform:translateZ(0)}
              .spacer{height:1500px}
              .tail{height:900px;background:#ddd}
            </style>
            <main><h1>Native document scroll</h1><div id='animated'>Animated brand</div>
              <div class='spacer'></div><section class='tail'><h2>Native bottom</h2></section>
            </main>"""
            content_type = "text/html; charset=utf-8"
        elif path == "/native-animated-bottom":
            body = b"""<!doctype html><meta charset='utf-8'>
            <style>
              html,body{margin:0;background:white}
              .spacer{height:1500px}
              .tail{height:900px;background:#ddd}
              #ticker{display:inline-block;animation:shift .2s infinite alternate}
              @keyframes shift{from{transform:translateX(0)}to{transform:translateX(20px)}}
            </style>
            <main><h1>Native animated document</h1><div class='spacer'></div>
              <section class='tail'><h2>Animated native bottom</h2><span id='ticker'>Live</span></section>
            </main>"""
            content_type = "text/html; charset=utf-8"
        elif path.startswith("/virtual"):
            body = b"""<!doctype html><meta charset='utf-8'>
            <style>
              html,body{margin:0;height:100%;overflow:hidden;background:white}
              #scene{will-change:transform}.spacer{height:650px}
              .reveal{height:300px;opacity:0;background:black;color:white}
              .reveal.seen{opacity:1}.tail{height:900px;background:#ddd}
            </style>
            <main id='scene'><h1>Virtual layout</h1><div class='spacer'></div>
              <section class='reveal'><h2>Virtual revealed project</h2></section>
              <div class='tail'>Tail</div></main>
            <script>
              let offset=0; const scene=document.querySelector('#scene');
              addEventListener('wheel',(event)=>{
                offset=Math.max(0,Math.min(1200,offset+event.deltaY));
                scene.style.transform=offset === 0
                  ? 'none'
                  : `translateY(${-offset}px)`;
                if(offset>400) document.querySelector('.reveal').classList.add('seen');
                if(offset>=1200) {
                  document.body.dataset.kaigoScrollEnd='true';
                } else {
                  delete document.body.dataset.kaigoScrollEnd;
                }
              },{passive:true});
            </script>"""
            content_type = "text/html; charset=utf-8"
        else:
            body = b"""<!doctype html><meta charset='utf-8'>
        <style>
          body{margin:0;font-family:Test Sans;background:rgb(248,245,239)}
          .spacer{height:1200px}.reveal{opacity:0;transition:opacity .1s}
          .reveal.seen{opacity:1}.bottom{height:900px;background:rgb(18,18,18);color:white}
          .top-motion,.bottom-motion{animation:pulse .2s 1}@keyframes pulse{from{opacity:.8}to{opacity:1}}
        </style>
        <nav><a href='/services'>Services</a></nav><h1>Warm editorial</h1>
        <div class='top-motion'>Top motion</div>
        <div class='spacer'></div><section class='reveal' id='lazy'><h2>Revealed project</h2></section>
        <div class='bottom'>Bottom evidence</div>
        <script>
          setTimeout(() => document.body.dataset.warmed = 'yes', 800);
          addEventListener('wheel', () => document.body.dataset.wheels = String(1 + +(document.body.dataset.wheels || 0)), {passive:true});
          addEventListener('scroll', () => {
            if (scrollY > 400 && document.body.dataset.warmed === 'yes') {
              document.querySelector('#lazy').classList.add('seen');
              if (!document.querySelector('.bottom-motion')) document.querySelector('.bottom').insertAdjacentHTML('beforebegin','<div class="bottom-motion">Bottom motion</div>');
            }
          });
        </script>"""
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class HitOnlyHandler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        body = b"should never be reached"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format, *_args):
        pass


class ChunkedBudgetHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    chunk_size = 16 * 1024
    total_size = 8 * 1024 * 1024
    bytes_sent = 0
    disconnected = False

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        type(self).bytes_sent = 0
        type(self).disconnected = False
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        if self.path == "/gzip-bomb":
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        payload = (
            gzip.compress(b"x" * (2 * 1024 * 1024), compresslevel=9)
            if self.path == "/gzip-bomb"
            else b"x" * self.total_size
        )
        try:
            for offset in range(0, len(payload), self.chunk_size):
                chunk = payload[offset : offset + self.chunk_size]
                self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii"))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                type(self).bytes_sent += len(chunk)
                time.sleep(0.004)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            type(self).disconnected = True

    def log_message(self, _format, *_args):
        pass


class CrossOriginTargetHandler(BaseHTTPRequestHandler):
    received_cookie = None
    received_authorization = None
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        type(self).received_cookie = self.headers.get("Cookie")
        type(self).received_authorization = self.headers.get("Authorization")
        body = b"cross-origin-secret"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class CredentialRedirectSourceHandler(BaseHTTPRequestHandler):
    target_url = ""

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if self.path == "/cors-start":
            self.send_response(302)
            self.send_header("Location", self.target_url)
            self.end_headers()
            return
        body = b"""<!doctype html><meta charset='utf-8'>
        <h1>Credential redirect fixture</h1><h2 id='cors'>CORS PENDING</h2>
        <script>
          fetch('/cors-start', {headers: {Authorization: 'Bearer source-secret'}})
            .then(response => response.text())
            .then(() => { cors.textContent = 'CORS READABLE'; })
            .catch(() => { cors.textContent = 'CORS BLOCKED'; });
        </script>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", "source_secret=must-not-leak; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class CrossOriginCdnRedirectHandler(BaseHTTPRequestHandler):
    target_url = ""

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", self.target_url)
        self.send_header("Set-Cookie", "cdn_secret=must-not-leak; Path=/")
        self.end_headers()

    def log_message(self, _format, *_args):
        pass


class CrossOriginCdnTargetHandler(BaseHTTPRequestHandler):
    hits = 0
    received_cookie = None
    received_authorization = None
    received_referer = None

    def do_GET(self):
        type(self).hits += 1
        type(self).received_cookie = self.headers.get("Cookie")
        type(self).received_authorization = self.headers.get("Authorization")
        type(self).received_referer = self.headers.get("Referer")
        body = b"document.querySelector('#cdn').textContent = 'CDN LOADED';"
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class CrossOriginAssetPageHandler(BaseHTTPRequestHandler):
    asset_url = ""

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        body = (
            "<!doctype html><meta charset='utf-8'>"
            "<h1>Cross origin CDN fixture</h1><h2 id='cdn'>CDN PENDING</h2>"
            f"<script src={json.dumps(self.asset_url)}></script>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class PermissiveLocalGuard:
    def validate(self, url):
        parsed = urlsplit(url)
        return GuardedUrl(
            url=url,
            host=parsed.hostname or "127.0.0.1",
            port=parsed.port or 80,
            addresses=(PUBLIC_V4,),
        )

    validate_redirect = validate


class SelectiveLocalGuard(PermissiveLocalGuard):
    def __init__(self, blocked_port):
        self.blocked_port = blocked_port

    def validate(self, url):
        if urlsplit(url).port == self.blocked_port:
            raise UnsafeReferenceUrl("all DNS answers must be public addresses")
        return super().validate(url)

    validate_redirect = validate


class BrowserLifecycleTests(unittest.TestCase):
    def test_subframe_policy_block_does_not_mask_the_capture_failure(self):
        failure = reference_crawler_module._capture_failure_message(
            reference_crawler_module.ReferenceCaptureError(
                "reference scroll position could not be restored"
            ),
            (
                "cross-origin subframe document blocked: https://example.org/frame",
            ),
        )

        self.assertEqual(failure, "reference scroll position could not be restored")

    def test_main_document_policy_block_remains_the_capture_failure(self):
        failure = reference_crawler_module._capture_failure_message(
            RuntimeError("navigation aborted"),
            ("cross-origin document blocked: https://example.org/",),
        )

        self.assertEqual(failure, "cross-origin document blocked")

    def test_domcontentloaded_timeout_keeps_a_meaningfully_rendered_document(self):
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        class RenderedPage:
            url = "https://mindbox.ru/"

            async def goto(self, *_args, **_kwargs):
                raise PlaywrightTimeoutError("DOMContentLoaded timed out")

            async def evaluate(self, _script):
                return {
                    "href": self.url,
                    "bodyExists": True,
                    "textLength": 9_488,
                    "elementCount": 1_561,
                    "scrollHeight": 9_117,
                }

        warnings = asyncio.run(
            reference_crawler_module._goto_reference_document(
                RenderedPage(),
                "https://mindbox.ru/",
                timeout_ms=45_000,
            )
        )

        self.assertEqual(warnings, ("domcontentloaded_timeout_rendered",))

    def test_domcontentloaded_timeout_still_rejects_a_blank_document(self):
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        class BlankPage:
            url = "about:blank"

            async def goto(self, *_args, **_kwargs):
                raise PlaywrightTimeoutError("DOMContentLoaded timed out")

            async def evaluate(self, _script):
                return {
                    "href": self.url,
                    "bodyExists": True,
                    "textLength": 0,
                    "elementCount": 0,
                    "scrollHeight": 0,
                }

        with self.assertRaisesRegex(PlaywrightTimeoutError, "DOMContentLoaded"):
            asyncio.run(
                reference_crawler_module._goto_reference_document(
                    BlankPage(),
                    "https://example.com/",
                    timeout_ms=45_000,
                )
            )

    def test_load_timeout_after_navigation_is_nonfatal_for_rendered_document(self):
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        class RenderedPage:
            url = "https://mindbox.ru/"

            async def wait_for_load_state(self, *_args, **_kwargs):
                raise PlaywrightTimeoutError("load timed out")

            async def evaluate(self, _script):
                return {
                    "href": self.url,
                    "bodyExists": True,
                    "textLength": 9_488,
                    "elementCount": 1_561,
                    "scrollHeight": 9_117,
                }

        warnings = asyncio.run(
            reference_crawler_module._wait_for_reference_load(
                RenderedPage(), timeout_ms=5_000
            )
        )

        self.assertEqual(warnings, ("load_timeout_rendered",))

    def test_default_desktop_capture_uses_wide_full_context_viewport(self):
        settings = VisualReferenceCrawler()._settings("desktop")
        self.assertEqual((settings.width, settings.height), (1920, 1080))

    def test_screenshot_manifest_rejects_dimension_drift(self):
        output = BytesIO()
        Image.new("RGB", (11, 7), "white").save(output, format="JPEG")

        class FakePage:
            async def screenshot(self, **_kwargs):
                return output.getvalue()

        with self.assertRaisesRegex(
            reference_crawler_module.ReferenceCaptureError,
            "dimensions",
        ):
            asyncio.run(
                reference_crawler_module._take_screenshot(
                    FakePage(),
                    page_id="home",
                    viewport="desktop",
                    position="top",
                    width=1920,
                    height=1080,
                )
            )

    def test_reverse_reset_settles_once_only_after_top_is_restored(self):
        initial_state = {
            "top": 0,
            "height": 2000,
            "client": 900,
            "rect": {"x": 0, "y": 0, "width": 1440, "height": 900},
            "transformSignature": "",
            "visibleSignature": "top",
            "potentialVirtual": False,
        }
        states = [
            {**initial_state, "top": 1200, "visibleSignature": "bottom"},
            {**initial_state, "top": 600, "visibleSignature": "middle"},
            initial_state,
            initial_state,
        ]
        events = []

        class FakePage:
            async def evaluate(self, _script):
                return states.pop(0)

        async def record_scroll(_page, _state, step):
            events.append(("wheel", step))
            return "wheel"

        async def record_settle(_page, **_kwargs):
            events.append(("settle", None))

        settings = CaptureSettings(
            width=1440,
            height=900,
            warmup_ms=1000,
            scroll_delay_ms=600,
            final_settle_ms=500,
            max_scroll_steps=4,
        )
        with (
            patch.object(reference_crawler_module, "_scroll_once", record_scroll),
            patch.object(
                reference_crawler_module,
                "_settle_scrolled_viewport",
                record_settle,
            ),
        ):
            asyncio.run(
                reference_crawler_module._restore_reference_start(
                    FakePage(),
                    initial_state=initial_state,
                    settings=settings,
                )
            )

        self.assertEqual(
            events,
            [("wheel", -630), ("wheel", -630), ("settle", None)],
        )

    def test_forward_settle_runs_quiet_and_images_together_without_fixed_delay(self):
        visual_active = False
        image_saw_visual = False
        visual_kwargs = {}
        fixed_delays = []

        class FakePage:
            async def wait_for_timeout(self, delay_ms):
                fixed_delays.append(delay_ms)

        async def record_visual(_page, **kwargs):
            nonlocal visual_active
            visual_kwargs.update(kwargs)
            visual_active = True
            await asyncio.sleep(0)
            visual_active = False

        async def record_images(_page, _timeout_ms):
            nonlocal image_saw_visual
            image_saw_visual = visual_active

        settings = CaptureSettings(
            width=1440,
            height=900,
            warmup_ms=1000,
            scroll_delay_ms=700,
            final_settle_ms=500,
            max_scroll_steps=4,
        )
        with (
            patch.object(
                reference_crawler_module,
                "_wait_for_visual_quiet",
                record_visual,
            ),
            patch.object(
                reference_crawler_module,
                "_wait_for_fonts_and_viewport_images",
                record_images,
            ),
        ):
            asyncio.run(
                reference_crawler_module._settle_scrolled_viewport(
                    FakePage(),
                    settings=settings,
                    skipped_reasons=[],
                    image_timeout_reason="lazy_image_settle_timeout",
                )
            )

        self.assertEqual(fixed_delays, [])
        self.assertTrue(image_saw_visual)
        self.assertEqual(visual_kwargs["minimum_ms"], settings.scroll_delay_ms)

    def test_visual_settle_failure_cancels_and_awaits_asset_sibling(self):
        asset_started = asyncio.Event()
        asset_cancelled = asyncio.Event()
        asset_finished = asyncio.Event()
        never_finished = asyncio.Event()

        async def fail_visual(_page, **_kwargs):
            await asset_started.wait()
            raise RuntimeError("visual quiet failed")

        async def wait_for_assets(_page, **_kwargs):
            asset_started.set()
            try:
                await never_finished.wait()
            except asyncio.CancelledError:
                asset_cancelled.set()
                raise
            finally:
                asset_finished.set()

        settings = CaptureSettings(
            width=1440,
            height=900,
            warmup_ms=1000,
            scroll_delay_ms=600,
            final_settle_ms=500,
            max_scroll_steps=4,
        )

        async def exercise_failure():
            with self.assertRaisesRegex(RuntimeError, "visual quiet failed"):
                await reference_crawler_module._settle_scrolled_viewport(
                    object(),
                    settings=settings,
                    skipped_reasons=[],
                    image_timeout_reason="lazy_image_settle_timeout",
                )
            await asyncio.sleep(0)
            self.assertTrue(asset_cancelled.is_set())
            self.assertTrue(asset_finished.is_set())

        with (
            patch.object(
                reference_crawler_module,
                "_wait_for_visual_quiet",
                fail_visual,
            ),
            patch.object(
                reference_crawler_module,
                "_settle_viewport_assets_nonfatal",
                wait_for_assets,
            ),
        ):
            asyncio.run(exercise_failure())

    def test_font_ready_wait_is_locally_bounded_by_supplied_timeout(self):
        evaluate_call = {}

        class FakePage:
            async def evaluate(self, script, *args):
                evaluate_call.update(script=script, args=args)
                return "ready"

            async def wait_for_function(self, _script, *, timeout):
                self.image_timeout = timeout

        page = FakePage()
        asyncio.run(
            reference_crawler_module._wait_for_fonts_and_viewport_images(
                page,
                1234,
            )
        )

        self.assertIn("setTimeout", evaluate_call["script"])
        self.assertIn("clearTimeout", evaluate_call["script"])
        self.assertEqual(evaluate_call["args"], (1234,))
        self.assertEqual(page.image_timeout, 1234)

    def test_font_ready_timeout_waits_for_images_then_raises_explicit_signal(self):
        image_wait_completed = False

        class FakePage:
            async def evaluate(self, _script, *_args):
                return "timeout"

            async def wait_for_function(self, _script, *, timeout):
                nonlocal image_wait_completed
                self.image_timeout = timeout
                await asyncio.sleep(0)
                image_wait_completed = True

        page = FakePage()
        with self.assertRaisesRegex(
            reference_crawler_module.FontReadyTimeout,
            "document fonts ready timed out",
        ):
            asyncio.run(
                reference_crawler_module._wait_for_fonts_and_viewport_images(
                    page,
                    1234,
                )
            )

        self.assertTrue(image_wait_completed)
        self.assertEqual(page.image_timeout, 1234)

    def test_font_timeout_is_nonfatal_and_deduped_for_initial_and_scroll_settles(self):
        reasons = []

        async def font_timeout(_page, _timeout_ms):
            raise reference_crawler_module.FontReadyTimeout(
                "document fonts ready timed out"
            )

        async def exercise_initial_and_scroll_settles():
            await reference_crawler_module._settle_viewport_assets_nonfatal(
                object(),
                timeout_ms=5000,
                skipped_reasons=reasons,
                image_timeout_reason=None,
            )
            await reference_crawler_module._settle_viewport_assets_nonfatal(
                object(),
                timeout_ms=2500,
                skipped_reasons=reasons,
                image_timeout_reason="lazy_image_settle_timeout",
            )

        with patch.object(
            reference_crawler_module,
            "_wait_for_fonts_and_viewport_images",
            font_timeout,
        ):
            asyncio.run(exercise_initial_and_scroll_settles())

        self.assertEqual(reasons, ["font_ready_timeout"])

    def test_initial_font_timeout_does_not_mask_concurrent_image_failure(self):
        reasons = []

        class FakePage:
            async def evaluate(self, _script, *_args):
                return "timeout"

            async def wait_for_function(self, _script, *, timeout):
                raise TimeoutError(f"image wait timed out after {timeout}ms")

        with self.assertRaisesRegex(TimeoutError, "image wait timed out"):
            asyncio.run(
                reference_crawler_module._settle_viewport_assets_nonfatal(
                    FakePage(),
                    timeout_ms=1234,
                    skipped_reasons=reasons,
                    image_timeout_reason=None,
                )
            )

        self.assertEqual(reasons, ["font_ready_timeout"])

    def test_scrolled_font_and_image_timeouts_record_both_reasons(self):
        reasons = []

        class FakePage:
            async def evaluate(self, _script, *_args):
                return "timeout"

            async def wait_for_function(self, _script, *, timeout):
                raise TimeoutError(f"image wait timed out after {timeout}ms")

        asyncio.run(
            reference_crawler_module._settle_viewport_assets_nonfatal(
                FakePage(),
                timeout_ms=1234,
                skipped_reasons=reasons,
                image_timeout_reason="lazy_image_settle_timeout",
            )
        )

        self.assertEqual(
            reasons,
            ["font_ready_timeout", "lazy_image_settle_timeout"],
        )

    def test_font_timeout_does_not_swallow_concurrent_cancellation(self):
        reasons = []

        class FakePage:
            async def evaluate(self, _script, *_args):
                return "timeout"

            async def wait_for_function(self, _script, *, timeout):
                raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(
                reference_crawler_module._settle_viewport_assets_nonfatal(
                    FakePage(),
                    timeout_ms=1234,
                    skipped_reasons=reasons,
                    image_timeout_reason="lazy_image_settle_timeout",
                )
            )

        self.assertEqual(reasons, [])

    def test_single_page_crawl_skips_sitemap_discovery(self):
        class StopAfterDiscovery(Exception):
            pass

        sitemap_called = False

        async def allow_robots(_self, _url):
            return None

        async def canonicalize(url, **_kwargs):
            return url

        async def record_sitemap(*_args, **_kwargs):
            nonlocal sitemap_called
            sitemap_called = True
            return ()

        async def stop_after_discovery(*_args, **_kwargs):
            raise StopAfterDiscovery

        crawler = VisualReferenceCrawler(
            guard=PermissiveLocalGuard(),
            limits=ReferenceCrawlLimits(max_pages=1),
        )
        with (
            patch.object(
                reference_crawler_module.GuardedRobotsPolicy,
                "require_allowed",
                allow_robots,
            ),
            patch.object(
                reference_crawler_module,
                "_canonicalize_document_url",
                canonicalize,
            ),
            patch.object(
                reference_crawler_module,
                "_sitemap_candidates",
                record_sitemap,
            ),
            patch.object(
                crawler,
                "_capture_single_page_viewports",
                stop_after_discovery,
            ),
            self.assertRaises(StopAfterDiscovery),
        ):
            asyncio.run(crawler.crawl("http://127.0.0.1/"))

        self.assertFalse(sitemap_called)

    def test_single_page_viewports_start_concurrently(self):
        crawler = VisualReferenceCrawler(
            guard=PermissiveLocalGuard(),
            limits=ReferenceCrawlLimits(max_pages=1, max_retries=0),
        )
        started: set[str] = set()
        both_started = asyncio.Event()
        release = asyncio.Event()

        async def capture(_url, *, page_id, category, viewport, **_kwargs):
            started.add(viewport)
            if started == {"desktop", "mobile"}:
                both_started.set()
            await release.wait()
            return ReferencePageEvidence(
                page_id=page_id,
                category=category,
                requested_url="https://example.com/",
                final_url="https://example.com/",
                depth=0,
            )

        async def run():
            with patch.object(
                reference_crawler_module,
                "capture_reference_page",
                capture,
            ):
                task = asyncio.create_task(
                    crawler._capture_single_page_viewports(
                        "https://example.com/",
                        robots_policy=object(),
                        byte_budget=CrawlByteBudget(1024),
                        remaining_timeout=lambda: 5.0,
                    )
                )
                await asyncio.wait_for(both_started.wait(), timeout=1)
                self.assertFalse(task.done())
                release.set()
                return await task

        desktop, mobile = asyncio.run(run())

        self.assertEqual(started, {"desktop", "mobile"})
        self.assertEqual(desktop.page_id, "home")
        self.assertEqual(mobile.page_id, "home-mobile")

    def test_chunked_response_is_aborted_near_byte_cap_without_full_buffering(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        ChunkedBudgetHandler.bytes_sent = 0
        ChunkedBudgetHandler.disconnected = False
        server = ThreadingHTTPServer(("127.0.0.1", 0), ChunkedBudgetHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cap = 256 * 1024
        budget = CrawlByteBudget(cap)
        try:
            with self.assertRaisesRegex(Exception, "byte limit"):
                asyncio.run(
                    capture_reference_page(
                        f"http://127.0.0.1:{server.server_port}/chunked",
                        page_id="chunked",
                        category="home",
                        viewport="desktop",
                        settings=CaptureSettings(
                            width=1440,
                            height=900,
                            warmup_ms=1000,
                            scroll_delay_ms=600,
                            final_settle_ms=500,
                            max_scroll_steps=2,
                            max_page_bytes=cap,
                        ),
                        guard=PermissiveLocalGuard(),
                        byte_budget=budget,
                    )
                )
            deadline = time.time() + 2
            while not ChunkedBudgetHandler.disconnected and time.time() < deadline:
                time.sleep(0.02)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertLessEqual(budget.used_bytes, cap + 64 * 1024)
        self.assertLess(ChunkedBudgetHandler.bytes_sent, 1024 * 1024)
        self.assertTrue(ChunkedBudgetHandler.disconnected)

    def test_compressed_response_cannot_expand_past_decoded_memory_cap(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), ChunkedBudgetHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cap = 256 * 1024
        budget = CrawlByteBudget(cap)
        try:
            with self.assertRaisesRegex(Exception, "decoded page byte limit"):
                asyncio.run(
                    capture_reference_page(
                        f"http://127.0.0.1:{server.server_port}/gzip-bomb",
                        page_id="gzip-bomb",
                        category="home",
                        viewport="desktop",
                        settings=CaptureSettings(
                            width=1440,
                            height=900,
                            warmup_ms=1000,
                            scroll_delay_ms=600,
                            final_settle_ms=500,
                            max_scroll_steps=2,
                            max_page_bytes=cap,
                        ),
                        guard=PermissiveLocalGuard(),
                        byte_budget=budget,
                    )
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertLess(budget.used_bytes, 64 * 1024)

    def test_cross_origin_redirect_preserves_browser_credential_and_cors_semantics(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        CrossOriginTargetHandler.hits = 0
        CrossOriginTargetHandler.received_cookie = None
        CrossOriginTargetHandler.received_authorization = None
        target = ThreadingHTTPServer(("127.0.0.1", 0), CrossOriginTargetHandler)
        source = ThreadingHTTPServer(("127.0.0.1", 0), CredentialRedirectSourceHandler)
        CredentialRedirectSourceHandler.target_url = (
            f"http://localhost:{target.server_port}/secret"
        )
        threads = [
            threading.Thread(target=target.serve_forever, daemon=True),
            threading.Thread(target=source.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            evidence = asyncio.run(
                capture_reference_page(
                    f"http://127.0.0.1:{source.server_port}/",
                    page_id="credentials",
                    category="home",
                    viewport="desktop",
                    settings=CaptureSettings(
                        width=1440,
                        height=900,
                        warmup_ms=1500,
                        scroll_delay_ms=600,
                        final_settle_ms=500,
                        max_scroll_steps=2,
                    ),
                    guard=PermissiveLocalGuard(),
                )
            )
        finally:
            source.shutdown()
            source.server_close()
            target.shutdown()
            target.server_close()
            for thread in threads:
                thread.join(timeout=2)
        self.assertEqual(CrossOriginTargetHandler.hits, 0)
        self.assertIsNone(CrossOriginTargetHandler.received_cookie)
        self.assertIsNone(CrossOriginTargetHandler.received_authorization)
        headings = tuple(evidence.semantic_sample.get("headings", ()))
        self.assertIn("CORS BLOCKED", headings)
        self.assertNotIn("CORS READABLE", headings)
        redirect_blocks = [
            item
            for item in evidence.policy_blocks
            if "cross-origin resource redirect blocked" in item
        ]
        self.assertEqual(len(redirect_blocks), 1)
        self.assertIn(f"127.0.0.1:{source.server_port}", redirect_blocks[0])
        self.assertIn(f"localhost:{target.server_port}", redirect_blocks[0])
        self.assertNotIn("source-secret", redirect_blocks[0])

    def test_already_cross_origin_asset_may_redirect_without_credentials(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        final = ThreadingHTTPServer(("127.0.0.1", 0), CrossOriginCdnTargetHandler)
        redirect = ThreadingHTTPServer(
            ("127.0.0.1", 0), CrossOriginCdnRedirectHandler
        )
        source = ThreadingHTTPServer(("127.0.0.1", 0), CrossOriginAssetPageHandler)
        CrossOriginCdnRedirectHandler.target_url = (
            f"http://localhost:{final.server_port}/asset.js"
        )
        CrossOriginAssetPageHandler.asset_url = (
            f"http://localhost:{redirect.server_port}/asset-start.js"
        )
        CrossOriginCdnTargetHandler.hits = 0
        CrossOriginCdnTargetHandler.received_cookie = None
        CrossOriginCdnTargetHandler.received_authorization = None
        CrossOriginCdnTargetHandler.received_referer = None
        servers = (source, redirect, final)
        threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in servers
        ]
        for thread in threads:
            thread.start()
        try:
            evidence = asyncio.run(
                capture_reference_page(
                    f"http://127.0.0.1:{source.server_port}/",
                    page_id="cdn",
                    category="home",
                    viewport="desktop",
                    settings=CaptureSettings(
                        width=1440,
                        height=900,
                        warmup_ms=1200,
                        scroll_delay_ms=600,
                        final_settle_ms=500,
                        max_scroll_steps=2,
                    ),
                    guard=PermissiveLocalGuard(),
                )
            )
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)
        self.assertEqual(CrossOriginCdnTargetHandler.hits, 1)
        self.assertIsNone(CrossOriginCdnTargetHandler.received_cookie)
        self.assertIsNone(CrossOriginCdnTargetHandler.received_authorization)
        self.assertIsNone(CrossOriginCdnTargetHandler.received_referer)
        self.assertIn("CDN LOADED", evidence.semantic_sample["headings"])
        self.assertFalse(
            any(
                "cross-origin resource redirect blocked" in item
                for item in evidence.policy_blocks
            )
        )

    def test_required_home_incomplete_coverage_marks_top_level_partial(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = asyncio.run(
                VisualReferenceCrawler(
                    guard=PermissiveLocalGuard(),
                    limits=ReferenceCrawlLimits(
                        max_pages=1,
                        max_retries=0,
                        warmup_ms=1000,
                        scroll_delay_ms=600,
                        final_settle_ms=500,
                        max_scroll_steps=1,
                        total_timeout_seconds=60,
                        page_timeout_seconds=20,
                        max_page_bytes=25 * 1024 * 1024,
                        max_total_bytes=50 * 1024 * 1024,
                    ),
                ).crawl(f"http://127.0.0.1:{server.server_port}/")
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(result.status, "partial")
        home = next(page for page in result.pages if page.page_id == "home")
        self.assertEqual(home.coverage_status, "partial")
        self.assertTrue(any(page.page_id == "home-mobile" for page in result.pages))

    def test_non_home_failure_keeps_partial_evidence_and_mobile_home(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        LazyFixtureHandler.fail_services = True
        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = asyncio.run(
                VisualReferenceCrawler(
                    guard=PermissiveLocalGuard(),
                    limits=ReferenceCrawlLimits(
                        max_pages=2,
                        max_retries=0,
                        warmup_ms=1000,
                        scroll_delay_ms=600,
                        final_settle_ms=500,
                        max_scroll_steps=8,
                        total_timeout_seconds=120,
                        page_timeout_seconds=20,
                        max_page_bytes=10 * 1024 * 1024,
                        max_total_bytes=20 * 1024 * 1024,
                    ),
                ).crawl(f"http://127.0.0.1:{server.server_port}/")
            )
        finally:
            LazyFixtureHandler.fail_services = False
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(result.status, "partial", result.failure)
        self.assertTrue(any(page.page_id == "home-mobile" for page in result.pages))
        failed = next(page for page in result.pages if page.category == "services")
        self.assertFalse(failed.screenshots)
        self.assertTrue(failed.skipped_reasons)

    def test_context_route_blocks_popup_first_request_and_cross_origin_redirect(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        HitOnlyHandler.hits = 0
        external = ThreadingHTTPServer(("127.0.0.1", 0), HitOnlyHandler)
        primary = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        external_thread = threading.Thread(target=external.serve_forever, daemon=True)
        primary_thread = threading.Thread(target=primary.serve_forever, daemon=True)
        external_thread.start()
        primary_thread.start()
        LazyFixtureHandler.external_target = (
            f"http://127.0.0.1:{external.server_port}/blocked?token=secret"
        )
        settings = CaptureSettings(
            width=1440,
            height=900,
            warmup_ms=1000,
            scroll_delay_ms=600,
            final_settle_ms=500,
            max_scroll_steps=2,
        )
        try:
            evidence = asyncio.run(
                capture_reference_page(
                    f"http://127.0.0.1:{primary.server_port}/popup",
                    page_id="popup",
                    category="home",
                    viewport="desktop",
                    settings=settings,
                    guard=PermissiveLocalGuard(),
                )
            )
            self.assertEqual(evidence.category, "home")
            with self.assertRaisesRegex(Exception, "cross-origin"):
                asyncio.run(
                    capture_reference_page(
                        f"http://127.0.0.1:{primary.server_port}/cross-redirect",
                        page_id="redirect",
                        category="home",
                        viewport="desktop",
                        settings=settings,
                        guard=PermissiveLocalGuard(),
                    )
                )
        finally:
            primary.shutdown()
            external.shutdown()
            primary.server_close()
            external.server_close()
            primary_thread.join(timeout=2)
            external_thread.join(timeout=2)
        self.assertEqual(HitOnlyHandler.hits, 0)

    def test_blocked_cross_origin_subframe_does_not_fail_primary_document(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        HitOnlyHandler.hits = 0
        external = ThreadingHTTPServer(("127.0.0.1", 0), HitOnlyHandler)
        primary = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        external_thread = threading.Thread(target=external.serve_forever, daemon=True)
        primary_thread = threading.Thread(target=primary.serve_forever, daemon=True)
        external_thread.start()
        primary_thread.start()
        LazyFixtureHandler.external_target = (
            f"http://127.0.0.1:{external.server_port}/blocked-frame?token=secret"
        )
        try:
            evidence = asyncio.run(
                capture_reference_page(
                    f"http://127.0.0.1:{primary.server_port}/cross-origin-frame",
                    page_id="cross-origin-frame",
                    category="home",
                    viewport="desktop",
                    settings=CaptureSettings(
                        width=1440,
                        height=900,
                        warmup_ms=1000,
                        scroll_delay_ms=600,
                        final_settle_ms=500,
                        max_scroll_steps=2,
                    ),
                    guard=PermissiveLocalGuard(),
                )
            )
        finally:
            primary.shutdown()
            external.shutdown()
            primary.server_close()
            external.server_close()
            primary_thread.join(timeout=2)
            external_thread.join(timeout=2)

        self.assertEqual(evidence.coverage_status, "complete")
        self.assertTrue(
            any(
                item.startswith("cross-origin subframe document blocked:")
                for item in evidence.policy_blocks
            )
        )
        self.assertEqual(HitOnlyHandler.hits, 0)

    def test_two_hop_document_redirect_is_rejected_before_external_target(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        HitOnlyHandler.hits = 0
        external = ThreadingHTTPServer(("127.0.0.1", 0), HitOnlyHandler)
        primary = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        threads = [
            threading.Thread(target=external.serve_forever, daemon=True),
            threading.Thread(target=primary.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        LazyFixtureHandler.external_target = (
            f"http://127.0.0.1:{external.server_port}/never?token=secret"
        )
        try:
            with self.assertRaisesRegex(Exception, "cross-origin"):
                asyncio.run(
                    capture_reference_page(
                        f"http://127.0.0.1:{primary.server_port}/doc-start",
                        page_id="redirect-chain",
                        category="home",
                        viewport="desktop",
                        settings=CaptureSettings(
                            width=1440,
                            height=900,
                            warmup_ms=1000,
                            scroll_delay_ms=600,
                            final_settle_ms=500,
                            max_scroll_steps=2,
                        ),
                        guard=PermissiveLocalGuard(),
                    )
                )
        finally:
            primary.shutdown()
            external.shutdown()
            primary.server_close()
            external.server_close()
            for thread in threads:
                thread.join(timeout=2)
        self.assertEqual(HitOnlyHandler.hits, 0)

    def test_two_hop_subresources_load_when_safe_and_block_external_target(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        HitOnlyHandler.hits = 0
        external = ThreadingHTTPServer(("127.0.0.1", 0), HitOnlyHandler)
        primary = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        threads = [
            threading.Thread(target=external.serve_forever, daemon=True),
            threading.Thread(target=primary.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        LazyFixtureHandler.external_target = (
            f"http://127.0.0.1:{external.server_port}/never-image"
        )
        try:
            evidence = asyncio.run(
                capture_reference_page(
                    f"http://127.0.0.1:{primary.server_port}/redirect-assets",
                    page_id="redirect-assets",
                    category="home",
                    viewport="desktop",
                    settings=CaptureSettings(
                        width=1440,
                        height=900,
                        warmup_ms=1000,
                        scroll_delay_ms=600,
                        final_settle_ms=500,
                        max_scroll_steps=2,
                    ),
                    guard=SelectiveLocalGuard(external.server_port),
                )
            )
        finally:
            primary.shutdown()
            external.shutdown()
            primary.server_close()
            external.server_close()
            for thread in threads:
                thread.join(timeout=2)
        safe = next(
            image
            for image in evidence.style_sample["imageAspectRatios"]
            if image["alt"] == "redirect-safe"
        )
        self.assertEqual(safe["width"], 1)
        self.assertTrue(
            any("unsafe destination" in item for item in evidence.policy_blocks)
            or any("cross-origin" in item for item in evidence.policy_blocks)
        )
        self.assertEqual(HitOnlyHandler.hits, 0)

    def test_document_redirect_loop_fails_closed_at_hop_limit(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaisesRegex(Exception, "redirect"):
                asyncio.run(
                    capture_reference_page(
                        f"http://127.0.0.1:{server.server_port}/loop-a",
                        page_id="redirect-loop",
                        category="home",
                        viewport="desktop",
                        settings=CaptureSettings(
                            width=1440,
                            height=900,
                            warmup_ms=1000,
                            scroll_delay_ms=600,
                            final_settle_ms=500,
                            max_scroll_steps=2,
                        ),
                        guard=PermissiveLocalGuard(),
                    )
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_scroll_step_cap_marks_partial_and_never_calls_last_tile_bottom(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            evidence = asyncio.run(
                capture_reference_page(
                    f"http://127.0.0.1:{server.server_port}/",
                    page_id="partial",
                    category="home",
                    viewport="desktop",
                    settings=CaptureSettings(
                        width=1440,
                        height=900,
                        warmup_ms=1000,
                        scroll_delay_ms=600,
                        final_settle_ms=500,
                        max_scroll_steps=1,
                    ),
                    guard=None,
                )
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(evidence.coverage_status, "partial")
        self.assertIn("scroll_step_cap_reached", evidence.skipped_reasons)
        positions = {shot.position for shot in evidence.screenshots}
        self.assertIn("last_observed", positions)
        self.assertNotIn("bottom", positions)

    def test_two_pass_warmup_loads_offscreen_lazy_image_before_top_capture(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        first_top = {}
        original_screenshot = reference_crawler_module._take_screenshot

        async def recording_screenshot(page, **kwargs):
            if kwargs["position"] == "top" and not first_top:
                first_top.update(
                    await page.evaluate(
                        """() => ({
                          y: scrollY,
                          wheels: +(document.body.dataset.wheels || 0),
                          complete: late.complete,
                          naturalWidth: late.naturalWidth
                        })"""
                    )
                )
            return await original_screenshot(page, **kwargs)

        try:
            with patch.object(
                reference_crawler_module, "_take_screenshot", recording_screenshot
            ):
                evidence = asyncio.run(
                    capture_reference_page(
                        f"http://127.0.0.1:{server.server_port}/lazy-prewarm",
                        page_id="lazy-prewarm",
                        category="home",
                        viewport="desktop",
                        settings=CaptureSettings(
                            width=1440,
                            height=900,
                            warmup_ms=1000,
                            scroll_delay_ms=600,
                            final_settle_ms=500,
                            max_scroll_steps=20,
                            page_timeout_seconds=5,
                        ),
                        guard=None,
                    )
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(first_top["y"], 0)
        self.assertGreater(first_top["wheels"], 0)
        self.assertTrue(first_top["complete"])
        self.assertGreater(first_top["naturalWidth"], 0)
        self.assertEqual(evidence.reset_strategy, "wheel-prewarm-return-top")
        self.assertEqual(
            set(evidence.timings_ms),
            {
                "load_and_initial_settle",
                "warm_pass",
                "reset_pass",
                "evidence_pass",
                "final_settle_and_screenshots",
                "total",
                "warm_steps",
                "reset_steps",
                "evidence_steps",
            },
        )
        self.assertGreater(evidence.timings_ms["warm_steps"], 0)
        self.assertGreater(evidence.timings_ms["reset_steps"], 0)
        self.assertGreater(evidence.timings_ms["evidence_steps"], 0)

    def test_warmup_incremental_scroll_and_tiles_reveal_lazy_content(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        captures = []
        original_screenshot = reference_crawler_module._take_screenshot

        async def recording_screenshot(page, **kwargs):
            captures.append(
                (
                    kwargs["position"],
                    await page.evaluate("() => ({y: scrollY, wheels: +(document.body.dataset.wheels || 0)})"),
                )
            )
            return await original_screenshot(page, **kwargs)

        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            with patch.object(
                reference_crawler_module, "_take_screenshot", recording_screenshot
            ):
                evidence = asyncio.run(
                    capture_reference_page(
                        url,
                        page_id="fixture",
                        category="home",
                        viewport="desktop",
                        settings=CaptureSettings(
                            width=1440,
                            height=900,
                            warmup_ms=1000,
                            scroll_delay_ms=600,
                            final_settle_ms=500,
                            max_scroll_steps=8,
                        ),
                        guard=None,
                    )
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertIn("Revealed project", evidence.semantic_sample["headings"])
        self.assertTrue(evidence.semantic_sample["reveal_observed"])
        positions = {shot.position for shot in evidence.screenshots}
        self.assertTrue({"top", "middle", "bottom"}.issubset(positions))
        self.assertTrue(all(shot.data for shot in evidence.screenshots))
        self.assertEqual(captures[0][0], "top")
        self.assertEqual(captures[0][1]["y"], 0)
        self.assertGreater(captures[0][1]["wheels"], 0)
        self.assertEqual(evidence.reset_strategy, "wheel-prewarm-return-top")
        motion_text = " ".join(
            item.get("text", "") for item in evidence.style_sample["motionInventory"]
        )
        self.assertIn("Top motion", motion_text)
        self.assertIn("Bottom motion", motion_text)

    def test_transform_virtual_scroller_uses_wheel_evidence_not_full_page_stitching(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        transforms = {}
        original_screenshot = reference_crawler_module._take_screenshot

        async def recording_screenshot(page, **kwargs):
            transforms[kwargs["position"]] = await page.locator("#scene").evaluate(
                "(el) => new DOMMatrixReadOnly(getComputedStyle(el).transform).m42"
            )
            return await original_screenshot(page, **kwargs)

        try:
            with patch.object(
                reference_crawler_module, "_take_screenshot", recording_screenshot
            ):
                evidence = asyncio.run(
                    capture_reference_page(
                        f"http://127.0.0.1:{server.server_port}/virtual",
                        page_id="virtual",
                        category="home",
                        viewport="desktop",
                        settings=CaptureSettings(
                            width=1440,
                            height=900,
                            warmup_ms=1000,
                            scroll_delay_ms=600,
                            final_settle_ms=500,
                            max_scroll_steps=6,
                        ),
                        guard=None,
                    )
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertTrue(evidence.semantic_sample["reveal_observed"])
        shots = {shot.position: shot for shot in evidence.screenshots}
        self.assertNotEqual(shots["bottom"].sha256, shots["top"].sha256)
        self.assertEqual(transforms["top"], 0)
        self.assertGreater(abs(transforms["middle"]), 0)
        self.assertLess(abs(transforms["middle"]), abs(transforms["bottom"]))
        self.assertEqual(abs(transforms["bottom"]), 1200)
        self.assertEqual(evidence.scroll_strategy, "virtual")

    def test_native_document_scroll_is_not_misclassified_by_overflow_and_transform(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            evidence = asyncio.run(
                capture_reference_page(
                    f"http://127.0.0.1:{server.server_port}/native-overflow-transform",
                    page_id="native-overflow-transform",
                    category="home",
                    viewport="desktop",
                    settings=CaptureSettings(
                        width=1440,
                        height=900,
                        warmup_ms=1000,
                        scroll_delay_ms=600,
                        final_settle_ms=500,
                        max_scroll_steps=6,
                    ),
                    guard=None,
                )
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(evidence.coverage_status, "complete")
        self.assertEqual(evidence.scroll_strategy, "document")
        self.assertIn("bottom", {shot.position for shot in evidence.screenshots})

    def test_native_document_end_uses_stable_geometry_despite_infinite_animation(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            evidence = asyncio.run(
                capture_reference_page(
                    f"http://127.0.0.1:{server.server_port}/native-animated-bottom",
                    page_id="native-animated-bottom",
                    category="home",
                    viewport="desktop",
                    settings=CaptureSettings(
                        width=1440,
                        height=900,
                        warmup_ms=1000,
                        scroll_delay_ms=600,
                        final_settle_ms=500,
                        max_scroll_steps=6,
                    ),
                    guard=None,
                )
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(evidence.coverage_status, "complete")
        self.assertEqual(evidence.scroll_strategy, "document")
        self.assertIn("bottom", {shot.position for shot in evidence.screenshots})

    def test_virtual_scroller_without_proven_end_never_synthesizes_bottom(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            evidence = asyncio.run(
                capture_reference_page(
                    f"http://127.0.0.1:{server.server_port}/virtual-delayed",
                    page_id="virtual-delayed",
                    category="home",
                    viewport="desktop",
                    settings=CaptureSettings(
                        width=1440,
                        height=900,
                        warmup_ms=1000,
                        scroll_delay_ms=600,
                        final_settle_ms=500,
                        max_scroll_steps=6,
                    ),
                    guard=PermissiveLocalGuard(),
                )
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(evidence.coverage_status, "partial")
        self.assertIn("last_observed", {shot.position for shot in evidence.screenshots})
        self.assertNotIn("bottom", {shot.position for shot in evidence.screenshots})
        self.assertIn("Real virtual end", evidence.semantic_sample["headings"])

    def test_off_center_nested_scroller_receives_real_wheel_event(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            evidence = asyncio.run(
                capture_reference_page(
                    f"http://127.0.0.1:{server.server_port}/nested",
                    page_id="nested",
                    category="home",
                    viewport="desktop",
                    settings=CaptureSettings(
                        width=1440,
                        height=900,
                        warmup_ms=1000,
                        scroll_delay_ms=600,
                        final_settle_ms=500,
                        max_scroll_steps=6,
                    ),
                    guard=None,
                )
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertTrue(evidence.semantic_sample["reveal_observed"])
        self.assertIn("Nested wheel fired", evidence.semantic_sample["headings"])
        self.assertEqual(evidence.scroll_strategy, "nested")
        self.assertNotIn("script_fallback", evidence.scroll_strategy)

    def test_post_wheel_settle_waits_for_new_visible_lazy_image(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        LazyFixtureHandler.hit_counts.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            evidence = asyncio.run(
                capture_reference_page(
                    f"http://127.0.0.1:{server.server_port}/lazy-once",
                    page_id="lazy-image",
                    category="home",
                    viewport="desktop",
                    settings=CaptureSettings(
                        width=1440,
                        height=900,
                        warmup_ms=1000,
                        scroll_delay_ms=600,
                        final_settle_ms=500,
                        max_scroll_steps=1,
                    ),
                    guard=None,
                )
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        image = next(
            item
            for item in evidence.style_sample["imageAspectRatios"]
            if item["alt"] == "late"
        )
        self.assertEqual(image["width"], 1)
        self.assertIsNotNone(image["aspectRatio"])

    def test_initial_response_is_included_in_page_byte_limit(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")
        LazyFixtureHandler.hit_counts.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaisesRegex(Exception, "byte limit"):
                asyncio.run(
                    capture_reference_page(
                        f"http://127.0.0.1:{server.server_port}/large-first",
                        page_id="large",
                        category="home",
                        viewport="desktop",
                        settings=CaptureSettings(
                            width=1440,
                            height=900,
                            warmup_ms=1000,
                            scroll_delay_ms=600,
                            final_settle_ms=500,
                            max_scroll_steps=1,
                            max_page_bytes=256 * 1024,
                        ),
                        guard=None,
                    )
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
