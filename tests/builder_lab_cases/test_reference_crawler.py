import asyncio
import base64
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

from builder_lab.reference_crawler import (
    CaptureSettings,
    CrawlAccumulator,
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
        with self.assertRaises(ValueError):
            ReferenceCrawlLimits(max_pages=6)
        with self.assertRaises(ValueError):
            ReferenceCrawlLimits(scroll_delay_ms=599)
        with self.assertRaises(ValueError):
            ReferenceCrawlLimits(scroll_delay_ms=1201)

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

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        self.hit_counts[path] = self.hit_counts.get(path, 0) + 1
        if path == "/cross-redirect":
            self.send_response(302)
            self.send_header("Location", self.external_target)
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
        elif path == "/lazy-once":
            body = b"""<!doctype html><meta charset='utf-8'><style>body{margin:0}.spacer{height:500px}img{display:block;width:100px;height:100px}.tail{height:900px}</style>
            <h1>Lazy image fixture</h1><div class='spacer'></div><img id='late' loading='lazy' alt='late'><div class='tail'></div>
            <script>addEventListener('wheel',()=>{if(!late.src)late.src='/lazy.png'},{passive:true})</script>"""
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
                offset=Math.min(1200,offset+Math.max(0,event.deltaY));
                scene.style.transform=`translateY(${-offset}px)`;
                if(offset>400) document.querySelector('.reveal').classList.add('seen');
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
              document.querySelector('.top-motion')?.remove();
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


class BrowserLifecycleTests(unittest.TestCase):
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
                        max_scroll_steps=2,
                        total_timeout_seconds=60,
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
        self.assertEqual(result.status, "succeeded", result.failure)
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
        self.assertEqual(captures[0], ("top", {"y": 0, "wheels": 0}))
        self.assertEqual(evidence.reset_strategy, "none")
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
