import json
import tempfile
import unittest
from pathlib import Path

from builder_lab.comparison import (
    ComparisonVariant,
    freeze_bundle,
    render_comparison_page,
    verify_bundle,
)


class ComparisonBundleTests(unittest.TestCase):
    def test_freeze_bundle_is_content_addressed_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "brief.txt").write_text("RAW BUREAU AI widget", encoding="utf-8")
            evidence = source / "evidence"
            evidence.mkdir()
            (evidence / "desktop.jpg").write_bytes(b"\xff\xd8evidence\xff\xd9")
            output = root / "frozen"

            bundle = freeze_bundle(
                source,
                output,
                metadata={
                    "source_url": "https://rawbureau.ru/",
                    "model_matrix": {"builder": "gemini-3.6-flash/high"},
                    "acceptance_profile": "kaigo-widget-experimental-v2",
                },
            )

            self.assertEqual(bundle.manifest["schema_version"], 1)
            self.assertEqual(
                bundle.manifest["files"]["brief.txt"]["byte_count"],
                len("RAW BUREAU AI widget".encode("utf-8")),
            )
            self.assertEqual(len(bundle.manifest["files"]["brief.txt"]["sha256"]), 64)
            self.assertTrue(verify_bundle(output))
            same = freeze_bundle(
                source,
                output,
                metadata={
                    "source_url": "https://rawbureau.ru/",
                    "model_matrix": {"builder": "gemini-3.6-flash/high"},
                    "acceptance_profile": "kaigo-widget-experimental-v2",
                },
            )
            self.assertEqual(same.digest, bundle.digest)

            (output / "brief.txt").write_text("tampered", encoding="utf-8")
            self.assertFalse(verify_bundle(output))

    def test_refuses_to_replace_a_different_existing_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "brief.txt").write_text("first", encoding="utf-8")
            (second / "brief.txt").write_text("second", encoding="utf-8")
            output = root / "frozen"
            freeze_bundle(first, output)

            with self.assertRaises(FileExistsError):
                freeze_bundle(second, output)

    def test_comparison_page_names_models_status_and_relative_routes(self):
        page = render_comparison_page(
            (
                ComparisonVariant(
                    slug="archive/raw-bureau-v1",
                    title="RAW BUREAU — baseline",
                    model="Gemini 2.5 Flash",
                    thinking="low",
                    status="archived",
                    summary="Исходная версия до смены моделей.",
                ),
                ComparisonVariant(
                    slug="direct-3-6",
                    title="Direct",
                    model="Gemini 3.6 Flash",
                    thinking="high",
                    status="completed",
                    summary="Пять последовательных ревизий.",
                ),
                ComparisonVariant(
                    slug="antigravity-3-6",
                    title="Antigravity",
                    model="antigravity-preview-05-2026",
                    thinking="agent",
                    status="completed",
                    summary="Агентская сборка.",
                ),
            )
        )

        self.assertIn("RAW BUREAU — baseline", page)
        self.assertIn("Gemini 3.6 Flash", page)
        self.assertIn("Antigravity", page)
        self.assertIn('href="direct-3-6/"', page)
        self.assertIn('href="antigravity-3-6/"', page)
        self.assertIn(
            'sandbox="allow-scripts"',
            page,
        )
        self.assertIn('referrerpolicy="no-referrer"', page)
        self.assertNotIn("allow-same-origin", page)
        self.assertNotIn("allow-forms", page)
        self.assertNotIn("<script", page.lower())
        json.dumps(page)

    def test_comparison_page_can_render_raw_final_pair_and_metrics(self):
        page = render_comparison_page(
            (
                ComparisonVariant(
                    slug="product-chat/final",
                    raw_slug="product-chat/raw",
                    title="A · Product Chat",
                    profile="product_chat",
                    model="gemini-3.6-flash",
                    thinking="high",
                    status="completed",
                    summary="Compact and familiar chat.",
                    critique_summary="Raw 3.7 → final 4.3.",
                    final_label="Rejected final",
                    elapsed_seconds=87.4,
                    total_tokens=12_345,
                    cost_usd=0.0456,
                ),
            )
        )

        self.assertIn('href="product-chat/raw/viewer.html"', page)
        self.assertIn('href="product-chat/final/viewer.html"', page)
        self.assertNotIn('href="product-chat/raw/"', page)
        self.assertNotIn('href="product-chat/final/"', page)
        self.assertIn('src="product-chat/raw/"', page)
        self.assertIn('src="product-chat/final/"', page)
        self.assertIn("product_chat", page)
        self.assertIn("12 345", page)
        self.assertIn("$0.0456", page)
        self.assertIn("Raw 3.7 → final 4.3.", page)
        self.assertIn("Rejected final", page)

    def test_trusted_live_wrapper_is_explicit_and_static_evidence_stays_opaque(self):
        page = render_comparison_page(
            (
                ComparisonVariant(
                    slug="product-chat/final",
                    raw_slug="product-chat/raw",
                    live_slug="/builder-comparison/direct-abc-v1/product-chat",
                    trusted_live=True,
                    title="A · Product Chat",
                    profile="product_chat",
                    model="gemini-3.6-flash",
                    thinking="high",
                    status="completed",
                    summary="Accepted strict final with a live chat wrapper.",
                    critique_summary="Raw 3.7 → final 4.3.",
                    elapsed_seconds=87.4,
                    total_tokens=12_345,
                    cost_usd=0.0456,
                ),
            )
        )

        self.assertIn(
            'href="/builder-comparison/direct-abc-v1/product-chat"', page
        )
        self.assertIn(
            'src="/builder-comparison/direct-abc-v1/product-chat"', page
        )
        self.assertIn('href="product-chat/final/viewer.html"', page)
        self.assertIn('href="product-chat/raw/viewer.html"', page)
        self.assertNotIn('href="product-chat/final/"', page)
        self.assertNotIn('href="product-chat/raw/"', page)
        self.assertEqual(
            page.count('sandbox="allow-scripts allow-same-origin"'), 1
        )
        self.assertIn('src="product-chat/raw/"', page)
        self.assertIn('title="A · Product Chat raw"', page)
        self.assertIn('sandbox="allow-scripts"></iframe>', page)
        self.assertNotIn(
            'src="product-chat/final/" title="A · Product Chat final"',
            page,
        )

    def test_live_wrapper_flag_is_all_or_none_same_origin_and_completed_only(self):
        common = dict(
            slug="product-chat/final",
            raw_slug="product-chat/raw",
            title="A",
            profile="product_chat",
            model="gemini-3.6-flash",
            thinking="high",
            status="completed",
            summary="Accepted.",
            critique_summary="Passed.",
            elapsed_seconds=1,
            total_tokens=1,
            cost_usd=0,
        )
        with self.assertRaises(ValueError):
            ComparisonVariant(
                **common,
                live_slug="/builder-comparison/direct-abc-v1/product-chat",
            )
        with self.assertRaises(ValueError):
            ComparisonVariant(**common, trusted_live=True)
        with self.assertRaises(ValueError):
            ComparisonVariant(
                **common,
                live_slug="https://attacker.example/widget",
                trusted_live=True,
            )
        with self.assertRaises(ValueError):
            ComparisonVariant(
                **{**common, "status": "failed"},
                live_slug="/builder-comparison/direct-abc-v1/product-chat",
                trusted_live=True,
            )

    def test_optional_experiment_fields_are_all_or_none_and_bounded(self):
        with self.assertRaises(ValueError):
            ComparisonVariant(
                slug="product-chat/final",
                raw_slug="product-chat/raw",
                title="A",
                model="gemini-3.6-flash",
                thinking="high",
                status="completed",
                summary="Missing profile and metrics.",
            )


if __name__ == "__main__":
    unittest.main()
