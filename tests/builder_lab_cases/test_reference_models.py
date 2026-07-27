import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from builder_lab.reference_models import (
    CrawlFailure,
    ReferenceCrawlResult,
    ReferencePageEvidence,
    ScreenshotEvidence,
    TraceEvidence,
)


class ReferenceModelsTests(unittest.TestCase):
    def screenshot(
        self,
        *,
        payload: bytes = b"jpeg-bytes",
        screenshot_id: str = "home-desktop-bottom",
        page_id: str = "home",
        viewport: str = "desktop",
        position: str = "bottom",
    ) -> ScreenshotEvidence:
        return ScreenshotEvidence(
            screenshot_id=screenshot_id,
            page_id=page_id,
            viewport=viewport,
            position=position,
            mime_type="image/jpeg",
            width=1440,
            height=900,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            data=payload,
        )

    def test_public_serialization_excludes_raw_screenshot_bytes(self):
        top = self.screenshot(
            payload=b"top-bytes",
            screenshot_id="home-desktop-top",
            position="top",
        )
        screenshot = self.screenshot()
        page = ReferencePageEvidence(
            page_id="home",
            category="home",
            requested_url="https://example.com/",
            final_url="https://example.com/",
            depth=0,
            screenshots=(top, screenshot),
            semantic_sample={"headings": ["Example"]},
            style_sample={"fonts": ["Inter"]},
            scroll_strategy="document",
            reset_strategy="not-required-top-first",
            coverage_status="complete",
        )
        result = ReferenceCrawlResult.succeeded(
            source_url="https://example.com/",
            pages=(page,),
            started_at=datetime.now(timezone.utc),
        )

        payload = result.to_dict()

        public_screenshot = next(
            item
            for item in payload["pages"][0]["screenshots"]
            if item["position"] == "bottom"
        )
        self.assertNotIn("data", public_screenshot)
        self.assertEqual(public_screenshot["sha256"], screenshot.sha256)
        self.assertEqual(result.screenshot_bytes()[screenshot.screenshot_id], b"jpeg-bytes")
        self.assertEqual(payload["pages"][0]["scroll_strategy"], "document")
        self.assertEqual(
            payload["pages"][0]["reset_strategy"], "not-required-top-first"
        )
        self.assertEqual(payload["pages"][0]["coverage_status"], "complete")
        self.assertEqual(payload["pages"][0]["skipped_reasons"], [])

    def test_screenshot_validates_hash_dimensions_mime_and_size(self):
        with self.assertRaisesRegex(ValueError, "sha256"):
            self.screenshot(payload=b"different").__class__(
                **{
                    **self.screenshot(payload=b"different").to_dict(),
                    "sha256": "0" * 64,
                    "data": b"different",
                }
            )
        with self.assertRaises(ValueError):
            ScreenshotEvidence(
                screenshot_id="bad",
                page_id="home",
                viewport="desktop",
                position="top",
                mime_type="image/gif",
                width=0,
                height=900,
                sha256="0" * 64,
                size_bytes=1,
                data=b"x",
            )

    def test_failed_result_may_publish_expiring_trace_metadata_but_not_trace_bytes(self):
        now = datetime.now(timezone.utc)
        trace_bytes = b"trace-bytes"
        trace = TraceEvidence(
            trace_id="trace-failed-1",
            sha256=hashlib.sha256(trace_bytes).hexdigest(),
            size_bytes=len(trace_bytes),
            expires_at=now + timedelta(hours=1),
            data=trace_bytes,
        )
        result = ReferenceCrawlResult.failed(
            source_url="https://example.com/",
            failure=CrawlFailure(code="navigation_failed", message="timed out"),
            trace=trace,
            started_at=now,
        )

        payload = result.to_dict()

        self.assertEqual(payload["status"], "failed")
        self.assertNotIn("data", payload["trace"])
        self.assertIn("expires_at", payload["trace"])

    def test_success_result_rejects_trace_and_failed_result_requires_failure(self):
        now = datetime.now(timezone.utc)
        trace = TraceEvidence(
            trace_id="trace",
            sha256="a" * 64,
            size_bytes=1,
            expires_at=now + timedelta(minutes=5),
        )
        with self.assertRaises(ValueError):
            ReferenceCrawlResult(
                source_url="https://example.com/",
                status="succeeded",
                started_at=now,
                completed_at=now,
                trace=trace,
            )

    def test_page_samples_are_json_safe_and_bounded(self):
        kwargs = {
            "page_id": "home",
            "category": "home",
            "requested_url": "https://example.com/",
            "final_url": "https://example.com/",
            "depth": 0,
        }
        with self.assertRaisesRegex(ValueError, "JSON-safe"):
            ReferencePageEvidence(**kwargs, semantic_sample={"raw": b"not-public"})
        with self.assertRaisesRegex(ValueError, "byte limit"):
            ReferencePageEvidence(**kwargs, style_sample={"css": "x" * (513 * 1024)})

    def test_nested_evidence_is_deeply_frozen_and_serialization_is_detached(self):
        headings = ["Original"]
        source = {"headings": headings, "nested": {"value": [1, 2]}}
        page = ReferencePageEvidence(
            page_id="home",
            category="home",
            requested_url="https://example.com/?token=secret#fragment",
            final_url="https://example.com/final?key=secret",
            depth=0,
            semantic_sample=source,
            style_sample={"fonts": ["Inter"]},
            timings_ms={"total": 10},
            scroll_strategy="nested",
            reset_strategy="not-required-top-first",
            coverage_status="not_captured",
            skipped_reasons=("lazy_image_timeout",),
        )

        headings.append("Mutated source")
        source["nested"]["value"].append(3)
        self.assertEqual(tuple(page.semantic_sample["headings"]), ("Original",))
        with self.assertRaises(TypeError):
            page.semantic_sample["other"] = "x"
        with self.assertRaises(AttributeError):
            page.semantic_sample["headings"].append("x")

        public = page.to_dict()
        public["semantic_sample"]["headings"].append("Serialized mutation")
        self.assertEqual(tuple(page.semantic_sample["headings"]), ("Original",))
        self.assertEqual(public["requested_url"], "https://example.com/")
        self.assertEqual(public["final_url"], "https://example.com/final")

    def test_deeply_frozen_samples_survive_dataclass_replace(self):
        page = ReferencePageEvidence(
            page_id="home",
            category="home",
            requested_url="https://example.com/",
            final_url="https://example.com/",
            depth=0,
            semantic_sample={"headings": ["Example"]},
            style_sample={
                "components": [
                    {
                        "name": "hero",
                        "colors": ["#ffffff", "#111111"],
                    }
                ]
            },
            coverage_status="not_captured",
        )

        revised = replace(page, skipped_reasons=("robots_denied: /contacts",))

        self.assertEqual(revised.skipped_reasons, ("robots_denied: /contacts",))
        self.assertEqual(
            revised.to_dict()["style_sample"]["components"][0]["name"],
            "hero",
        )

    def test_page_coverage_requires_coherent_position_contract(self):
        kwargs = {
            "page_id": "home",
            "category": "home",
            "requested_url": "https://example.com/",
            "final_url": "https://example.com/",
            "depth": 0,
            "scroll_strategy": "document",
            "reset_strategy": "not-required-top-first",
        }
        top = self.screenshot(
            payload=b"top",
            screenshot_id="home-desktop-top",
            position="top",
        )
        bottom = self.screenshot(payload=b"bottom")
        last = self.screenshot(
            payload=b"last",
            screenshot_id="home-desktop-last",
            position="last_observed",
        )
        mobile_bottom = self.screenshot(
            payload=b"mobile",
            screenshot_id="home-mobile-bottom",
            viewport="mobile",
        )

        for changes in (
            {"coverage_status": "complete", "screenshots": (bottom,)},
            {
                "coverage_status": "complete",
                "screenshots": (top, mobile_bottom),
            },
            {
                "coverage_status": "partial",
                "screenshots": (last,),
                "skipped_reasons": ("step cap",),
            },
            {"coverage_status": "not_captured", "screenshots": (top,)},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ReferencePageEvidence(**kwargs, **changes)

        partial = ReferencePageEvidence(
            **kwargs,
            coverage_status="partial",
            screenshots=(top, last),
            skipped_reasons=("step cap",),
        )
        self.assertEqual(partial.coverage_status, "partial")

    def test_result_status_requires_nonempty_coherent_coverage(self):
        now = datetime.now(timezone.utc)
        top = self.screenshot(
            payload=b"top",
            screenshot_id="home-desktop-top",
            position="top",
        )
        bottom = self.screenshot(payload=b"bottom")
        last = self.screenshot(
            payload=b"last",
            screenshot_id="home-desktop-last",
            position="last_observed",
        )
        complete = ReferencePageEvidence(
            page_id="home",
            category="home",
            requested_url="https://example.com/",
            final_url="https://example.com/",
            depth=0,
            screenshots=(top, bottom),
            coverage_status="complete",
            scroll_strategy="document",
            reset_strategy="not-required-top-first",
        )
        partial = ReferencePageEvidence(
            page_id="home",
            category="home",
            requested_url="https://example.com/",
            final_url="https://example.com/",
            depth=0,
            screenshots=(top, last),
            coverage_status="partial",
            scroll_strategy="document",
            reset_strategy="not-required-top-first",
            skipped_reasons=("step cap",),
        )

        with self.assertRaises(ValueError):
            ReferenceCrawlResult.succeeded(
                source_url="https://example.com/", pages=(), started_at=now
            )
        with self.assertRaises(ValueError):
            ReferenceCrawlResult.succeeded(
                source_url="https://example.com/", pages=(partial,), started_at=now
            )
        with self.assertRaises(ValueError):
            ReferenceCrawlResult.partial(
                source_url="https://example.com/", pages=(complete,), started_at=now
            )
        with self.assertRaises(ValueError):
            ReferenceCrawlResult.partial(
                source_url="https://example.com/", pages=(), started_at=now
            )

    def test_result_rejects_same_page_and_viewport_with_different_shot_counts(self):
        now = datetime.now(timezone.utc)
        top = self.screenshot(
            payload=b"top",
            screenshot_id="home-desktop-top",
            position="top",
        )
        middle = self.screenshot(
            payload=b"middle",
            screenshot_id="home-desktop-middle",
            position="middle",
        )
        bottom = self.screenshot(payload=b"bottom")
        base = {
            "page_id": "home",
            "category": "home",
            "requested_url": "https://example.com/",
            "final_url": "https://example.com/",
            "depth": 0,
            "coverage_status": "complete",
            "scroll_strategy": "document",
            "reset_strategy": "not-required-top-first",
        }
        two_shots = ReferencePageEvidence(**base, screenshots=(top, bottom))
        three_shots = ReferencePageEvidence(
            **base, screenshots=(top, middle, bottom)
        )

        with self.assertRaisesRegex(ValueError, "duplicate page evidence"):
            ReferenceCrawlResult.succeeded(
                source_url="https://example.com/",
                pages=(two_shots, three_shots),
                started_at=now,
            )

    def test_result_rejects_globally_duplicate_screenshot_ids(self):
        now = datetime.now(timezone.utc)

        def page(page_id: str, url: str) -> ReferencePageEvidence:
            top = self.screenshot(
                payload=f"{page_id}-top".encode(),
                screenshot_id="shared-screenshot-id",
                page_id=page_id,
                position="top",
            )
            bottom = self.screenshot(
                payload=f"{page_id}-bottom".encode(),
                screenshot_id=f"{page_id}-desktop-bottom",
                page_id=page_id,
            )
            return ReferencePageEvidence(
                page_id=page_id,
                category="general",
                requested_url=url,
                final_url=url,
                depth=0,
                screenshots=(top, bottom),
                coverage_status="complete",
                scroll_strategy="document",
                reset_strategy="not-required-top-first",
            )

        with self.assertRaisesRegex(ValueError, "duplicate screenshot_id"):
            ReferenceCrawlResult.succeeded(
                source_url="https://example.com/",
                pages=(
                    page("home", "https://example.com/"),
                    page("about", "https://example.com/about"),
                ),
                started_at=now,
            )


if __name__ == "__main__":
    unittest.main()
