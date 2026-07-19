import hashlib
import unittest
from datetime import datetime, timedelta, timezone

from builder_lab.reference_models import (
    CrawlFailure,
    ReferenceCrawlResult,
    ReferencePageEvidence,
    ScreenshotEvidence,
    TraceEvidence,
)


class ReferenceModelsTests(unittest.TestCase):
    def screenshot(self, *, payload: bytes = b"jpeg-bytes") -> ScreenshotEvidence:
        return ScreenshotEvidence(
            screenshot_id="home-desktop-bottom",
            page_id="home",
            viewport="desktop",
            position="bottom",
            mime_type="image/jpeg",
            width=1440,
            height=900,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            data=payload,
        )

    def test_public_serialization_excludes_raw_screenshot_bytes(self):
        screenshot = self.screenshot()
        page = ReferencePageEvidence(
            page_id="home",
            category="home",
            requested_url="https://example.com/",
            final_url="https://example.com/",
            depth=0,
            screenshots=(screenshot,),
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

        public_screenshot = payload["pages"][0]["screenshots"][0]
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


if __name__ == "__main__":
    unittest.main()
