import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from scripts.analyze_reference_site import (
    MAX_INLINE_BYTES,
    ReferenceAnalysisError,
    analyze_reference_site,
    serialize_reference,
    validate_screenshot_inputs,
    validate_source_url,
)


REQUIRED_LABELS = (
    "desktop.top",
    "desktop.middle",
    "desktop.bottom",
    "mobile.top",
    "mobile.middle",
    "mobile.bottom",
)


def jpeg(width: int = 64, height: int = 48, color=(240, 240, 240)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, "JPEG", quality=80)
    return output.getvalue()


def valid_analysis(labels: tuple[str, ...]) -> dict:
    return {
        "visual_summary": "Плоская монохромная редакционная система с крупным гротеском и тонкими линиями.",
        "public_facts": [
            {
                "statement": "На странице указано название RAW BUREAU.",
                "evidence": [labels[0]],
            }
        ],
        "visual_tokens": {
            "palette": [
                {
                    "token": "page-background",
                    "value": "#f5f5f5",
                    "evidence": [labels[0]],
                }
            ],
            "typography": [
                {
                    "token": "display-heading",
                    "value": "тяжёлый гротеск, верхний регистр",
                    "evidence": [labels[0]],
                }
            ],
            "geometry": [
                {
                    "token": "corners",
                    "value": "преимущественно острые",
                    "evidence": [labels[-1]],
                }
            ],
            "motion": [],
        },
    }


def evidence_with_manifest(root: Path, *, payloads: dict[str, bytes] | None = None):
    inputs = []
    manifest_screenshots = []
    payloads = payloads or {}
    for index, label in enumerate(REQUIRED_LABELS):
        viewport, position = label.split(".")
        path = root / f"{label}.jpg"
        data = payloads.get(label, jpeg(color=(240 - index, 240, 240)))
        path.write_bytes(data)
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
        inputs.append((label, path))
        manifest_screenshots.append(
            {
                "screenshot_id": f"home-{viewport}-{position}",
                "page_id": "home" if viewport == "desktop" else "home-mobile",
                "viewport": viewport,
                "position": position,
                "mime_type": "image/jpeg",
                "width": width,
                "height": height,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        )
    manifest = {
        "source_url": "https://rawbureau.ru/",
        "status": "succeeded",
        "coverage_status": "complete",
        "started_at": "2026-07-19T12:09:22.199524+00:00",
        "completed_at": "2026-07-19T12:10:23.127441+00:00",
        "pages": [
            {
                "page_id": "home",
                "coverage_status": "complete",
                "screenshots": manifest_screenshots[:3],
            },
            {
                "page_id": "home-mobile",
                "coverage_status": "complete",
                "screenshots": manifest_screenshots[3:],
            },
        ],
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return inputs, manifest_path


class FakeModels:
    def __init__(self, payload):
        self.payloads = payload if isinstance(payload, list) else [payload]
        self.calls: list[dict] = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        if isinstance(payload, BaseException):
            raise payload
        return SimpleNamespace(
            parsed=payload,
            text=json.dumps(payload, ensure_ascii=False),
            response_id="reference-analysis-123",
            usage_metadata=SimpleNamespace(
                prompt_token_count=321,
                candidates_token_count=54,
                thoughts_token_count=12,
                total_token_count=387,
            ),
        )


class FakeClient:
    def __init__(self, payload: dict | list[dict]):
        self.aio = SimpleNamespace(models=FakeModels(payload))


class ReferenceInputValidationTests(unittest.TestCase):
    def test_source_must_be_allowlisted_https_without_credentials_or_port(self):
        self.assertEqual(
            validate_source_url("https://rawbureau.ru/", allowed_hosts={"rawbureau.ru"}),
            "https://rawbureau.ru/",
        )
        for value in (
            "http://rawbureau.ru/",
            "https://user:pass@rawbureau.ru/",
            "https://rawbureau.ru:444/",
            "https://evil.example/",
            "https://rawbureau.ru.evil.example/",
        ):
            with self.subTest(value=value), self.assertRaises(ReferenceAnalysisError):
                validate_source_url(value, allowed_hosts={"rawbureau.ru"})

    def test_jpegs_must_be_real_bounded_regular_files_under_trusted_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "desktop-top.jpg"
            second = root / "mobile-bottom.jpg"
            first.write_bytes(jpeg(1440, 900))
            second.write_bytes(jpeg(390, 844))

            items = validate_screenshot_inputs(
                [("desktop.top", first), ("mobile.bottom", second)],
                evidence_root=root,
            )

            self.assertEqual([item.label for item in items], ["desktop.top", "mobile.bottom"])
            self.assertEqual((items[0].width, items[0].height), (1440, 900))
            self.assertEqual(items[0].mime_type, "image/jpeg")
            self.assertEqual(len(items[0].sha256), 64)

            outside = root.parent / "outside-reference.jpg"
            outside.write_bytes(jpeg())
            self.addCleanup(lambda: outside.unlink(missing_ok=True))
            with self.assertRaises(ReferenceAnalysisError):
                validate_screenshot_inputs([("outside", outside)], evidence_root=root)

            bogus = root / "bogus.jpg"
            bogus.write_bytes(b"not-a-jpeg")
            with self.assertRaises(ReferenceAnalysisError):
                validate_screenshot_inputs([("bogus", bogus)], evidence_root=root)

            large = root / "large.jpg"
            large.write_bytes(b"\xff\xd8\xff" + b"0" * 1_500_000 + b"\xff\xd9")
            with self.assertRaises(ReferenceAnalysisError):
                validate_screenshot_inputs([("large", large)], evidence_root=root)

            link = root / "linked.jpg"
            try:
                os.symlink(first, link)
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaises(ReferenceAnalysisError):
                    validate_screenshot_inputs([("linked", link)], evidence_root=root)

    def test_total_inline_cap_is_decimal_eight_million_bytes_inclusive(self):
        def sized_jpeg(size: int) -> bytes:
            source = jpeg()
            self.assertLess(len(source), size - 2)
            return source + b"0" * (size - len(source) - 2) + b"\xff\xd9"

        self.assertEqual(MAX_INLINE_BYTES, 8_000_000)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            at_limit = [1_400_000] * 5 + [1_000_000]
            inputs = []
            for index, size in enumerate(at_limit):
                path = root / f"at-limit-{index}.jpg"
                path.write_bytes(sized_jpeg(size))
                inputs.append((f"limit.{index}", path))
            self.assertEqual(
                sum(item.size_bytes for item in validate_screenshot_inputs(inputs, evidence_root=root)),
                8_000_000,
            )
            inputs[-1][1].write_bytes(sized_jpeg(1_000_001))
            with self.assertRaises(ReferenceAnalysisError) as caught:
                validate_screenshot_inputs(inputs, evidence_root=root)
            self.assertEqual(caught.exception.error_code, "visual_payload_too_large")


class ReferenceGeminiAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_transient_provider_unavailable_with_backoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, manifest_path = evidence_with_manifest(root)
            fake = FakeClient(
                [
                    RuntimeError("503 UNAVAILABLE high demand"),
                    valid_analysis(REQUIRED_LABELS),
                ]
            )

            with patch(
                "scripts.analyze_reference_site.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep:
                reference = await analyze_reference_site(
                    source_url="https://rawbureau.ru/",
                    allowed_hosts={"rawbureau.ru"},
                    screenshot_inputs=inputs,
                    evidence_root=root,
                    captured_at="2026-07-19T12:10:23.127441+00:00",
                    coverage_status="complete",
                    capture_manifest=manifest_path,
                    api_key=None,
                    client=fake,
                    model="gemini-3.6-flash",
                )

        self.assertEqual(reference["analysis"]["public_facts"][0]["evidence"], ["desktop.top"])
        self.assertEqual(len(fake.aio.models.calls), 2)
        sleep.assert_awaited_once_with(0.5)

    async def test_retries_once_with_explicit_local_budgets_after_semantic_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, manifest_path = evidence_with_manifest(root)
            invalid = valid_analysis(REQUIRED_LABELS)
            invalid["public_facts"] = []
            fake = FakeClient([invalid, valid_analysis(REQUIRED_LABELS)])

            reference = await analyze_reference_site(
                source_url="https://rawbureau.ru/",
                allowed_hosts={"rawbureau.ru"},
                screenshot_inputs=inputs,
                evidence_root=root,
                captured_at="2026-07-19T12:10:23.127441+00:00",
                coverage_status="complete",
                capture_manifest=manifest_path,
                api_key="test-key",
                model="gemini-2.5-flash",
                client=fake,
            )

            self.assertEqual(len(fake.aio.models.calls), 2)
            retry_prompt = fake.aio.models.calls[1]["contents"][0].text
            self.assertIn("public_facts: 1 to 24 items", retry_prompt)
            self.assertIn("each visual token category: 0 to 16 items", retry_prompt)
            self.assertEqual(reference["provenance"]["attempt_count"], 2)

    async def test_retries_semantic_failures_with_exact_validator_feedback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, manifest_path = evidence_with_manifest(root)
            invalid = valid_analysis(REQUIRED_LABELS)
            invalid["public_facts"] = []
            fake = FakeClient(
                [
                    invalid,
                    invalid,
                    invalid,
                    valid_analysis(REQUIRED_LABELS),
                ]
            )

            reference = await analyze_reference_site(
                source_url="https://rawbureau.ru/",
                allowed_hosts={"rawbureau.ru"},
                screenshot_inputs=inputs,
                evidence_root=root,
                captured_at="2026-07-19T12:10:23.127441+00:00",
                coverage_status="complete",
                capture_manifest=manifest_path,
                api_key="test-key",
                model="gemini-3.6-flash",
                client=fake,
            )

            self.assertEqual(len(fake.aio.models.calls), 4)
            final_retry_prompt = fake.aio.models.calls[-1]["contents"][0].text
            self.assertIn(
                "public_facts is outside its item budget",
                final_retry_prompt,
            )
            self.assertEqual(reference["provenance"]["attempt_count"], 4)

    async def test_normalizes_safe_duplicate_items_from_provider(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, manifest_path = evidence_with_manifest(root)
            payload = valid_analysis(REQUIRED_LABELS)
            payload["public_facts"][0]["evidence"] = [
                "desktop.top",
                "desktop.top",
            ]
            payload["public_facts"].append(dict(payload["public_facts"][0]))
            payload["visual_tokens"]["palette"].append(
                dict(payload["visual_tokens"]["palette"][0])
            )
            fake = FakeClient(payload)

            reference = await analyze_reference_site(
                source_url="https://rawbureau.ru/",
                allowed_hosts={"rawbureau.ru"},
                screenshot_inputs=inputs,
                evidence_root=root,
                captured_at="2026-07-19T12:10:23.127441+00:00",
                coverage_status="complete",
                capture_manifest=manifest_path,
                api_key="test-key",
                model="gemini-3.6-flash",
                client=fake,
            )

            analysis = reference["analysis"]
            self.assertEqual(len(fake.aio.models.calls), 1)
            self.assertEqual(len(analysis["public_facts"]), 1)
            self.assertEqual(
                analysis["public_facts"][0]["evidence"],
                ["desktop.top"],
            )
            self.assertEqual(len(analysis["visual_tokens"]["palette"]), 1)

    async def test_gemini_2_5_omits_unsupported_thinking_level(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, manifest_path = evidence_with_manifest(root)
            fake = FakeClient(valid_analysis(REQUIRED_LABELS))

            await analyze_reference_site(
                source_url="https://rawbureau.ru/",
                allowed_hosts={"rawbureau.ru"},
                screenshot_inputs=inputs,
                evidence_root=root,
                captured_at="2026-07-19T12:10:23.127441+00:00",
                coverage_status="complete",
                capture_manifest=manifest_path,
                api_key="test-key",
                model="gemini-2.5-flash",
                client=fake,
            )

            config = fake.aio.models.calls[0]["config"]
            self.assertIsNone(config.thinking_config.thinking_level)
            self.assertEqual(config.thinking_config.thinking_budget, 0)
            provider_schema = json.dumps(config.response_json_schema, sort_keys=True)
            for unsupported in (
                '"maxItems"',
                '"minItems"',
                '"maxLength"',
                '"minLength"',
                '"uniqueItems"',
            ):
                self.assertNotIn(unsupported, provider_schema)

    async def test_sends_label_then_real_inline_jpeg_for_every_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            labels = REQUIRED_LABELS
            inputs, manifest_path = evidence_with_manifest(root)
            fake = FakeClient(valid_analysis(labels))

            reference = await analyze_reference_site(
                source_url="https://rawbureau.ru/",
                allowed_hosts={"rawbureau.ru"},
                screenshot_inputs=inputs,
                evidence_root=root,
                captured_at="2026-07-19T12:10:23.127441+00:00",
                coverage_status="complete",
                capture_manifest=manifest_path,
                api_key="super-secret-key",
                thinking_level="high",
                client=fake,
            )

            call = fake.aio.models.calls[0]
            self.assertEqual(call["model"], "gemini-3.5-flash")
            self.assertEqual(call["config"].temperature, 0.1)
            self.assertEqual(call["config"].top_p, 1.0)
            self.assertEqual(call["config"].tools, [])
            self.assertIn("HIGH", str(call["config"].thinking_config.thinking_level).upper())
            self.assertEqual(call["config"].response_mime_type, "application/json")

            parts = call["contents"]
            image_indexes = [index for index, part in enumerate(parts) if getattr(part, "inline_data", None)]
            self.assertEqual(len(image_indexes), len(labels))
            for label, image_index in zip(labels, image_indexes):
                self.assertEqual(getattr(parts[image_index - 1], "text", "").strip(), f"EVIDENCE {label}")
                self.assertEqual(parts[image_index].inline_data.mime_type, "image/jpeg")
                self.assertTrue(parts[image_index].inline_data.data.startswith(b"\xff\xd8\xff"))

            serialized = serialize_reference(reference)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("super-secret-key", serialized)
            self.assertNotIn("inline_data", serialized)
            self.assertNotIn("file_uri", serialized)
            document = json.loads(serialized)
            self.assertEqual(document["source"]["url"], "https://rawbureau.ru/")
            self.assertEqual(document["capture"]["coverage_status"], "complete")
            self.assertEqual(document["capture"]["started_at"], "2026-07-19T12:09:22.199524+00:00")
            self.assertEqual(len(document["capture"]["manifest_sha256"]), 64)
            self.assertEqual([item["state"] for item in document["screenshots"]], list(labels))
            self.assertEqual(document["provenance"]["image_transport"], "inline JPEG bytes")
            self.assertEqual(document["provenance"]["model"], "gemini-3.5-flash")
            self.assertEqual(document["provenance"]["request_id"], "reference-analysis-123")
            self.assertEqual(
                document["provenance"]["usage"],
                {
                    "prompt_tokens": 321,
                    "output_tokens": 54,
                    "thinking_tokens": 12,
                    "total_tokens": 387,
                },
            )

    async def test_native_client_uses_configured_v1beta_base_url(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, manifest_path = evidence_with_manifest(root)
            fake = FakeClient(valid_analysis(REQUIRED_LABELS))
            with patch.dict(
                os.environ,
                {"GOOGLE_AI_NATIVE_BASE_URL": "https://proxy.test/protected/v1beta"},
            ), patch("scripts.analyze_reference_site.genai.Client", return_value=fake) as factory:
                await analyze_reference_site(
                    source_url="https://rawbureau.ru/",
                    allowed_hosts={"rawbureau.ru"},
                    screenshot_inputs=inputs,
                    evidence_root=root,
                    captured_at="2026-07-19T12:10:23.127441+00:00",
                    coverage_status="complete",
                    capture_manifest=manifest_path,
                    api_key="test-key",
                )
            options = factory.call_args.kwargs["http_options"]
            self.assertEqual(options.base_url, "https://proxy.test/protected")
            self.assertEqual(options.api_version, "v1beta")

    async def test_complete_analysis_requires_attested_six_state_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, manifest_path = evidence_with_manifest(root)
            with self.assertRaises(ReferenceAnalysisError) as caught:
                await analyze_reference_site(
                    source_url="https://rawbureau.ru/",
                    allowed_hosts={"rawbureau.ru"},
                    screenshot_inputs=inputs,
                    evidence_root=root,
                    captured_at="2026-07-19T12:10:23.127441+00:00",
                    coverage_status="complete",
                    capture_manifest=None,
                    api_key="test-key",
                    client=FakeClient(valid_analysis(REQUIRED_LABELS)),
                )
            self.assertEqual(caught.exception.error_code, "unattested_capture")

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["pages"][0]["screenshots"][0]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ReferenceAnalysisError) as caught:
                await analyze_reference_site(
                    source_url="https://rawbureau.ru/",
                    allowed_hosts={"rawbureau.ru"},
                    screenshot_inputs=inputs,
                    evidence_root=root,
                    captured_at="2026-07-19T12:10:23.127441+00:00",
                    coverage_status="complete",
                    capture_manifest=manifest_path,
                    api_key="test-key",
                    client=FakeClient(valid_analysis(REQUIRED_LABELS)),
                )
            self.assertEqual(caught.exception.error_code, "manifest_mismatch")

    async def test_partial_and_future_captures_are_rejected_before_gemini(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, manifest_path = evidence_with_manifest(root)
            fake = FakeClient(valid_analysis(REQUIRED_LABELS))

            with self.assertRaises(ReferenceAnalysisError) as caught:
                await analyze_reference_site(
                    source_url="https://rawbureau.ru/",
                    allowed_hosts={"rawbureau.ru"},
                    screenshot_inputs=inputs,
                    evidence_root=root,
                    captured_at="2099-01-01T00:00:00+00:00",
                    coverage_status="partial",
                    capture_manifest=None,
                    api_key="test-key",
                    client=fake,
                )
            self.assertEqual(caught.exception.error_code, "unattested_capture")

            with self.assertRaises(ReferenceAnalysisError) as caught:
                await analyze_reference_site(
                    source_url="https://rawbureau.ru/",
                    allowed_hosts={"rawbureau.ru"},
                    screenshot_inputs=inputs,
                    evidence_root=root,
                    captured_at="2026-07-19T12:10:23.127441+00:00",
                    coverage_status="partial",
                    capture_manifest=manifest_path,
                    api_key="test-key",
                    client=fake,
                )
            self.assertEqual(caught.exception.error_code, "incomplete_capture")

            future_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            future_manifest["started_at"] = "2098-12-31T23:59:00+00:00"
            future_manifest["completed_at"] = "2099-01-01T00:00:00+00:00"
            manifest_path.write_text(json.dumps(future_manifest), encoding="utf-8")
            with self.assertRaises(ReferenceAnalysisError) as caught:
                await analyze_reference_site(
                    source_url="https://rawbureau.ru/",
                    allowed_hosts={"rawbureau.ru"},
                    screenshot_inputs=inputs,
                    evidence_root=root,
                    captured_at="2099-01-01T00:00:00+00:00",
                    coverage_status="complete",
                    capture_manifest=manifest_path,
                    api_key="test-key",
                    client=fake,
                )
            self.assertEqual(caught.exception.error_code, "invalid_manifest")
            self.assertEqual(fake.aio.models.calls, [])

    async def test_rejects_semantic_output_that_cites_unknown_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, manifest_path = evidence_with_manifest(root)
            payload = valid_analysis(REQUIRED_LABELS)
            payload["public_facts"][0]["evidence"] = ["not-supplied"]

            with self.assertRaises(ReferenceAnalysisError) as caught:
                await analyze_reference_site(
                    source_url="https://rawbureau.ru/",
                    allowed_hosts={"rawbureau.ru"},
                    screenshot_inputs=inputs,
                    evidence_root=root,
                    captured_at="2026-07-19T12:10:23.127441+00:00",
                    coverage_status="complete",
                    capture_manifest=manifest_path,
                    api_key="test-key",
                    client=FakeClient(payload),
                )
            self.assertEqual(caught.exception.error_code, "invalid_semantic_output")


class RawBureauPacketTests(unittest.TestCase):
    def test_checked_in_reference_is_public_grounded_and_reproducible(self):
        packet = Path(__file__).parents[2] / "examples" / "raw-bureau"
        raw = (packet / "reference.json").read_text(encoding="utf-8")
        reference = json.loads(raw)

        self.assertEqual(reference["schema_version"], "kaigo.reference.v1")
        self.assertEqual(reference["source"]["url"], "https://rawbureau.ru/")
        self.assertEqual(reference["capture"]["coverage_status"], "complete")
        self.assertEqual(len(reference["screenshots"]), 6)
        self.assertEqual(
            {item["state"] for item in reference["screenshots"]},
            {
                "desktop.top",
                "desktop.middle",
                "desktop.bottom",
                "mobile.top",
                "mobile.middle",
                "mobile.bottom",
            },
        )
        self.assertTrue(all(len(item["sha256"]) == 64 for item in reference["screenshots"]))
        self.assertTrue(all(fact["evidence"] for fact in reference["analysis"]["public_facts"]))
        facts = "\n".join(item["statement"] for item in reference["analysis"]["public_facts"])
        self.assertIn("6 000 ₽/м²", facts)
        self.assertIn("exclusions", reference)
        self.assertNotIn("kaigo-reference-evidence", raw)
        self.assertNotIn("D:\\", raw)
        self.assertEqual(reference["provenance"]["model_analysis_status"], "pending_real_call")
        self.assertNotIn("model", reference["provenance"])
        semantic = reference["provenance"]["evidence_sources"][0]
        self.assertEqual(semantic["excerpt"], "от 6 000 ₽/м²")
        self.assertEqual(
            semantic["manifest_sha256"],
            "1c7dd7ffdcbb47c1e2e9f79a0e8d33ec82a957c658781beaf61254538a49951b",
        )

    def test_chat_prompt_and_generation_brief_pin_truth_and_runtime_contracts(self):
        packet = Path(__file__).parents[2] / "examples" / "raw-bureau"
        chat = (packet / "chat-system-prompt.txt").read_text(encoding="utf-8")
        brief = (packet / "generation-brief.txt").read_text(encoding="utf-8")

        for required in (
            "Ты — AI",
            "не человек",
            "не придумывай",
            "неизвестно",
            "RAW BUREAU",
            "контакт",
            "историю диалога",
            "6 000 ₽/м²",
            "могла измениться",
        ):
            self.assertIn(required.casefold(), chat.casefold())
        for required in (
            "плавающая проектная заметка",
            "216×46px",
            "372×304px",
            "min(536px, 68dvh)",
            "calc(100vw - 24px)",
            "320px",
            "70dvh",
            "data-region=\"root\"",
            "data-region=\"launcher\"",
            "data-region=\"panel\"",
            "data-region=\"header\"",
            "data-region=\"messages\"",
            "data-region=\"suggestions\"",
            "data-region=\"composer\"",
            "trusted runtime",
            "без пузырей",
            "без default scrollbar",
            "fake actions",
        ):
            self.assertIn(required.casefold(), brief.casefold())


if __name__ == "__main__":
    unittest.main()
