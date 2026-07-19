import asyncio
import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from builder_lab.reference_crawler import (
    CaptureSettings,
    GuardedUrl,
    LinkCandidate,
    ReferenceCrawlLimits,
    UnsafeReferenceUrl,
    UrlGuard,
    browser_unavailable_reason,
    capture_reference_page,
    select_reference_pages,
)


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


class LazyFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/virtual"):
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
        else:
            body = b"""<!doctype html><meta charset='utf-8'>
        <style>
          body{margin:0;font-family:Test Sans;background:rgb(248,245,239)}
          .spacer{height:1200px}.reveal{opacity:0;transition:opacity .1s}
          .reveal.seen{opacity:1}.bottom{height:900px;background:rgb(18,18,18);color:white}
        </style>
        <nav><a href='/services'>Services</a></nav><h1>Warm editorial</h1>
        <div class='spacer'></div><section class='reveal' id='lazy'><h2>Revealed project</h2></section>
        <div class='bottom'>Bottom evidence</div>
        <script>
          setTimeout(() => document.body.dataset.warmed = 'yes', 800);
          addEventListener('scroll', () => {
            if (scrollY > 400 && document.body.dataset.warmed === 'yes')
              document.querySelector('#lazy').classList.add('seen');
          });
        </script>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class BrowserLifecycleTests(unittest.TestCase):
    def test_warmup_incremental_scroll_and_tiles_reveal_lazy_content(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
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

    def test_transform_virtual_scroller_uses_wheel_evidence_not_full_page_stitching(self):
        reason = browser_unavailable_reason()
        if reason:
            self.skipTest(f"Playwright Chromium unavailable: {reason}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), LazyFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
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


if __name__ == "__main__":
    unittest.main()
