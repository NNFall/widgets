import asyncio
import hashlib
import io
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from builder_lab.browser_audit import BrowserAuditReport, CapturedScreenshot
from builder_lab.visual_critic import (
    GeminiVisualCritic,
    VISUAL_CRITIC_SCHEMA,
    VISUAL_PROBE_SCHEMA,
    VisualCriticError,
    _pixel_facts,
    _rasterize_proof,
    _visual_specificity_signature,
)
from builder_lab.visual_models import (
    LayoutEvidence,
    LayoutState,
    RegionEvidence,
    ScreenshotEvidence,
    ScreenshotState,
)


def jpeg(width, height, index):
    output = io.BytesIO()
    Image.new("RGB", (width, height), (235 - index, 235, 235)).save(output, "JPEG", quality=80)
    return output.getvalue()


def report():
    screenshots = []
    layouts = []
    for index, state in enumerate(ScreenshotState):
        width, height = ((1440, 900) if state.value.startswith("desktop") else (390, 844))
        data = jpeg(width, height, index)
        screenshots.append(
            CapturedScreenshot(
                evidence=ScreenshotEvidence(
                    screenshot_id=state.value,
                    state=state,
                    sha256=hashlib.sha256(data).hexdigest(),
                    mime_type="image/jpeg",
                    byte_count=len(data),
                    width=width,
                    height=height,
                ),
                data=data,
            )
        )
    screenshot_by_state = {item.evidence.state.value: item.evidence.screenshot_id for item in screenshots}
    for state in LayoutState:
        width, height = ((1440, 900) if state.value.startswith("desktop") else (390, 844))
        layouts.append(
            LayoutEvidence(
                evidence_id="layout-" + state.value,
                state=state,
                screenshot_id=(None if state.value.endswith("after_turn_1") else screenshot_by_state[state.value]),
                viewport_width=width,
                viewport_height=height,
                regions=(
                    RegionEvidence(
                        region="panel",
                        x=20,
                        y=20,
                        width=372 if width == 1440 else 366,
                        height=304 if "open_initial" in state.value else 300,
                        client_width=372 if width == 1440 else 366,
                        scroll_width=372 if width == 1440 else 366,
                        client_height=300,
                        scroll_height=300,
                        visible=not state.value.endswith("closed"),
                    ),
                ),
                horizontal_overflow_px=0,
                panel_inside_viewport=True,
            )
        )
    return BrowserAuditReport(screenshots=tuple(screenshots), layouts=tuple(layouts))


def response_payload(code="A7B9K2", state="mobile.open_initial", audit=None):
    audit = audit or report()
    facts = {
        shot.evidence.screenshot_id: _pixel_facts(shot.data)
        for shot in audit.screenshots
    }
    details = {
        ScreenshotState.DESKTOP_CLOSED: "launcher button sits at the bottom-right edge with a pale border",
        ScreenshotState.DESKTOP_OPEN_INITIAL: "open panel header and composer input use compact vertical spacing",
        ScreenshotState.DESKTOP_AFTER_TURN_2: "transcript messages remain above the composer input with a clear divider",
        ScreenshotState.MOBILE_CLOSED: "launcher control stays above the bottom edge with narrow right spacing",
        ScreenshotState.MOBILE_OPEN_INITIAL: "open panel width keeps side margins while header and input stay aligned",
        ScreenshotState.MOBILE_AFTER_TURN_2: "message transcript scroll area ends above the bottom composer button",
    }
    return {
        "verdict": "pass",
        "summary": " ".join(
            f"{item.value}: {details[item]}."
            for index, item in enumerate(ScreenshotState)
        ),
        "observations": [
            {
                "screenshot_id": state.value,
                "observation": f"{state.value}: {details[state]}.",
                "pixel_facts": facts[state.value],
            }
            for index, state in enumerate(ScreenshotState)
        ],
        "findings": [],
    }


PROOF_CODES = {
    state: f"K{index:05d}" for index, state in enumerate(ScreenshotState)
}


def probe_payload():
    return {
        "proofs": [
            {
                "code": PROOF_CODES[state],
                "screenshot_id": state.value,
                "visible_marker": f"PROOF {PROOF_CODES[state]} STATE {state.value}",
            }
            for state in ScreenshotState
        ],
    }


class FakeModels:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            parsed=self.payload,
            text=json.dumps(self.payload),
            response_id="fake-response-123",
            usage_metadata=SimpleNamespace(
                prompt_token_count=101,
                candidates_token_count=22,
                thoughts_token_count=7,
            ),
        )


class FakeClient:
    def __init__(self, payload):
        self.aio = SimpleNamespace(models=FakeModels(payload))


class GeminiVisualCriticTests(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_2_5_omits_unsupported_thinking_level(self):
        critique_client = FakeClient(response_payload())
        critic = GeminiVisualCritic(model="gemini-2.5-flash", client=critique_client)
        await critic.critique(
            audit=report(),
            brief="Compact editorial assistant.",
            art_direction="Warm monochrome floating note.",
        )
        critique_config = critique_client.aio.models.calls[0]["config"]
        self.assertIsNone(critique_config.thinking_config.thinking_level)
        self.assertEqual(critique_config.thinking_config.thinking_budget, 0)
        provider_schema = json.dumps(
            critique_config.response_json_schema, sort_keys=True
        )
        for unsupported in ('"maxItems"', '"minItems"', '"uniqueItems"'):
            self.assertNotIn(unsupported, provider_schema)

        probe_client = FakeClient(probe_payload())
        critic = GeminiVisualCritic(
            model="gemini-2.5-flash",
            client=probe_client,
            proof_code_factory=lambda state: PROOF_CODES[state],
        )
        await critic.probe_visual_evidence(audit=report())
        probe_config = probe_client.aio.models.calls[0]["config"]
        self.assertIsNone(probe_config.thinking_config.thinking_level)
        self.assertEqual(probe_config.thinking_config.thinking_budget, 0)
        self.assertNotIn(
            '"maxItems"',
            json.dumps(probe_config.response_json_schema, sort_keys=True),
        )

    async def test_report_rejects_noncanonical_id_wrong_viewport_or_payload_budget(self):
        valid = report()
        first = valid.screenshots[0]
        changed_evidence = replace(first.evidence, screenshot_id="renamed-state")
        changed = replace(first, evidence=changed_evidence)
        with self.assertRaises(ValueError):
            BrowserAuditReport(
                screenshots=(changed,) + valid.screenshots[1:], layouts=valid.layouts
            )

        tiny_data = jpeg(1, 1, 0)
        tiny = CapturedScreenshot(
            evidence=ScreenshotEvidence(
                screenshot_id=ScreenshotState.DESKTOP_CLOSED.value,
                state=ScreenshotState.DESKTOP_CLOSED,
                sha256=hashlib.sha256(tiny_data).hexdigest(),
                mime_type="image/jpeg",
                byte_count=len(tiny_data),
                width=1,
                height=1,
            ),
            data=tiny_data,
        )
        with self.assertRaises(ValueError):
            BrowserAuditReport(
                screenshots=(tiny,) + valid.screenshots[1:], layouts=valid.layouts
            )

        def padded(shot, size):
            data = shot.data[:-2] + (b"\0" * (size - len(shot.data))) + shot.data[-2:]
            evidence = replace(
                shot.evidence,
                sha256=hashlib.sha256(data).hexdigest(),
                byte_count=len(data),
            )
            return CapturedScreenshot(evidence=evidence, data=data)

        at_limit = tuple(padded(shot, 1_333_333 + (1 if index < 2 else 0)) for index, shot in enumerate(valid.screenshots))
        self.assertEqual(sum(len(item.data) for item in at_limit), 8_000_000)
        BrowserAuditReport(screenshots=at_limit, layouts=valid.layouts)
        over_limit = (padded(valid.screenshots[0], 1_333_335),) + at_limit[1:]
        with self.assertRaises(ValueError):
            BrowserAuditReport(screenshots=over_limit, layouts=valid.layouts)

    async def test_production_critique_sends_exact_six_unmodified_interleaved_jpegs(self):
        fake = FakeClient(response_payload())
        critic = GeminiVisualCritic(
            api_key="test-key",
            client=fake,
        )

        result = await critic.critique(
            audit=report(),
            brief="Компактный RAW AI-консультант.",
            art_direction="Floating project note with strict editorial hierarchy.",
        )

        self.assertIsNone(result.pixel_proof)
        self.assertEqual(len(result.observations), 6)
        self.assertEqual(result.usage.total_tokens, 130)
        call = fake.aio.models.calls[0]
        self.assertEqual(call["model"], "gemini-3.5-flash")
        config = call["config"]
        self.assertEqual(config.temperature, 0.1)
        self.assertEqual(config.top_p, 1.0)
        self.assertEqual(config.tools, [])
        self.assertIn("LOW", str(config.thinking_config.thinking_level).upper())
        self.assertIn("untrusted", str(config.system_instruction).lower())
        contents = call["contents"]
        image_parts = [part for part in contents if getattr(part, "inline_data", None)]
        self.assertEqual(len(image_parts), 6)
        self.assertEqual(
            [index for index, part in enumerate(contents) if getattr(part, "inline_data", None)],
            [2, 4, 6, 8, 10, 12],
        )
        self.assertTrue(all(part.inline_data.mime_type == "image/jpeg" for part in image_parts))
        text = "\n".join(str(getattr(part, "text", "") or "") for part in contents)
        self.assertIn("desktop.closed", text)
        self.assertIn("mobile.after_turn_2", text)
        self.assertEqual(
            [part.inline_data.data for part in image_parts],
            [shot.data for shot in report().screenshots],
        )

    async def test_explicit_pixel_probe_is_separate_and_fake_inspects_derived_image(self):
        fake = FakeClient(probe_payload())
        critic = GeminiVisualCritic(
            client=fake,
            proof_code_factory=lambda state: PROOF_CODES[state],
        )
        audit = report()
        proof = await critic.probe_visual_evidence(audit=audit)
        self.assertEqual(len(proof.proofs), 6)
        self.assertEqual(proof.usage.total_tokens, 130)
        self.assertEqual(proof.model, "gemini-3.5-flash")
        self.assertEqual(proof.response_id, "fake-response-123")
        call = fake.aio.models.calls[0]
        images = [part.inline_data.data for part in call["contents"] if getattr(part, "inline_data", None)]
        self.assertEqual(len(images), 6)
        for image, state, item in zip(images, ScreenshotState, proof.proofs):
            self.assertNotEqual(image, audit.screenshot(state).data)
            self.assertEqual(hashlib.sha256(image).hexdigest(), item.transmitted_sha256)
            self.assertEqual(item.source_sha256, audit.screenshot(state).evidence.sha256)
        text = "\n".join(str(getattr(part, "text", "") or "") for part in call["contents"])
        for state, code in PROOF_CODES.items():
            self.assertNotIn(code, text)
            self.assertNotIn(state.value, text)

    async def test_explicit_pixel_probe_enforces_aggregate_derived_payload_budget(self):
        fake = FakeClient(probe_payload())
        critic = GeminiVisualCritic(
            client=fake,
            proof_code_factory=lambda state: PROOF_CODES[state],
        )
        with patch(
            "builder_lab.visual_critic._rasterize_proof",
            return_value=b"x" * 1_400_000,
        ):
            with self.assertRaises(VisualCriticError) as caught:
                await critic.probe_visual_evidence(audit=report())
        self.assertEqual(caught.exception.error_code, "visual_payload_too_large")
        self.assertEqual(fake.aio.models.calls, [])

    async def test_explicit_pixel_probe_normalizes_visible_marker_whitespace(self):
        payload = probe_payload()
        for item in payload["proofs"]:
            item["visible_marker"] = item["visible_marker"].replace(" STATE ", "\nSTATE   ")
        critic = GeminiVisualCritic(
            client=FakeClient(payload),
            proof_code_factory=lambda state: PROOF_CODES[state],
        )
        proof = await critic.probe_visual_evidence(audit=report())
        self.assertEqual(len(proof.proofs), 6)

    async def test_mobile_proof_marker_keeps_a_clear_right_edge(self):
        audit = report()
        for state in ScreenshotState:
            if not state.value.startswith("mobile"):
                continue
            derived = _rasterize_proof(
                audit.screenshot(state).data, PROOF_CODES[state], state
            )
            with Image.open(io.BytesIO(derived)) as image:
                edge = [
                    sum(image.convert("RGB").getpixel((image.width - 1, y)))
                    for y in range(0, min(180, image.height))
                ]
            self.assertGreater(min(edge), 500, state.value)

    async def test_rejects_wrong_or_unproven_image_semantics(self):
        wrong_code = json.loads(json.dumps(probe_payload()))
        wrong_code["proofs"][0]["code"] = "WRONG1"
        wrong_state = json.loads(json.dumps(probe_payload()))
        wrong_state["proofs"][0]["screenshot_id"] = "mobile.closed"
        wrong_marker = json.loads(json.dumps(probe_payload()))
        wrong_marker["proofs"][0]["visible_marker"] = "not the rasterized marker"
        missing = {"proofs": probe_payload()["proofs"][:-1]}
        cases = (wrong_code, wrong_state, wrong_marker, missing)
        for payload in cases:
            with self.subTest(payload=payload):
                critic = GeminiVisualCritic(
                    client=FakeClient(payload),
                    proof_code_factory=lambda state: PROOF_CODES[state],
                )
                with self.assertRaises(VisualCriticError) as caught:
                    await critic.probe_visual_evidence(audit=report())
                self.assertEqual(caught.exception.error_code, "visual_evidence_unproven")

        misleading_field_root = response_payload()
        misleading_details = {
            ScreenshotState.DESKTOP_CLOSED: "launcher button sits at the bottom-right edge with a pale border",
            ScreenshotState.DESKTOP_OPEN_INITIAL: "open panel полностью uses compact vertical spacing",
            ScreenshotState.DESKTOP_AFTER_TURN_2: "transcript messages remain above the composer input with a clear divider",
            ScreenshotState.MOBILE_CLOSED: "launcher control stays above the bottom edge with narrow right spacing",
            ScreenshotState.MOBILE_OPEN_INITIAL: "open panel полностью keeps side margins and stays aligned",
            ScreenshotState.MOBILE_AFTER_TURN_2: "message transcript scroll area ends above the bottom composer button",
        }
        misleading_field_root["summary"] = " ".join(
            f"{state.value}: {misleading_details[state]}." for state in ScreenshotState
        )
        misleading_field_root["observations"] = [
            {
                "screenshot_id": state.value,
                "observation": f"{state.value}: {misleading_details[state]}.",
                "pixel_facts": _pixel_facts(report().screenshot(state).data),
            }
            for state in ScreenshotState
        ]
        false_root_words = {
            ScreenshotState.DESKTOP_CLOSED: "launcher button control подходит generic visual formula",
            ScreenshotState.DESKTOP_OPEN_INITIAL: "panel header composer input правильно generic visual formula",
            ScreenshotState.DESKTOP_AFTER_TURN_2: "message transcript composer input button сервис generic visual formula",
            ScreenshotState.MOBILE_CLOSED: "launcher button control надежный generic visual formula",
            ScreenshotState.MOBILE_OPEN_INITIAL: "panel header composer input малиновый generic visual formula",
            ScreenshotState.MOBILE_AFTER_TURN_2: "message transcript composer input button левитирует generic visual formula",
        }
        false_root_payload = response_payload()
        false_root_payload["summary"] = " ".join(
            f"{state.value}: {false_root_words[state]}." for state in ScreenshotState
        )
        false_root_payload["observations"] = [
            {
                "screenshot_id": state.value,
                "observation": f"{state.value}: {false_root_words[state]}.",
                "pixel_facts": _pixel_facts(report().screenshot(state).data),
            }
            for state in ScreenshotState
        ]

        semantic_cases = (
            {**response_payload(), "observations": response_payload()["observations"][:-1]},
            {
                **response_payload(),
                "observations": [
                    {"screenshot_id": state.value, "observation": "Same generic observation without a visual marker."}
                    for state in ScreenshotState
                ],
            },
            {**response_payload(), "summary": "Generic summary without screenshot IDs."},
            {
                **response_payload(),
                "summary": " ".join(state.value for state in ScreenshotState),
            },
            {
                **response_payload(),
                "summary": " ".join(
                    f"{state.value}: button panel color spacing marker-{index}."
                    for index, state in enumerate(ScreenshotState)
                ),
                "observations": [
                    {
                        "screenshot_id": state.value,
                        "observation": (
                            f"{state.value}: button panel color spacing marker-{index} "
                            "shows the visible state."
                        ),
                        "pixel_facts": _pixel_facts(report().screenshot(state).data),
                    }
                    for index, state in enumerate(ScreenshotState)
                ],
            },
            {
                **response_payload(),
                "observations": [
                    {
                        **item,
                        "pixel_facts": {
                            "luminance_band": "dark",
                            "dark_pixel_band": "much",
                            "edge_density_band": "high",
                            "dominant_hue": "blue",
                        },
                    }
                    for item in response_payload()["observations"]
                ],
            },
            {
                **response_payload(),
                "summary": " ".join(
                    f"{state.value}: "
                    + (
                        "launcher button control marker"
                        if state.value.endswith("closed")
                        else "panel header composer input marker"
                        if state.value.endswith("open_initial")
                        else "message transcript composer input button marker"
                    )
                    + f" index-{index}."
                    for index, state in enumerate(ScreenshotState)
                ),
                "observations": [
                    {
                        "screenshot_id": state.value,
                        "observation": (
                            f"{state.value}: "
                            + (
                                "launcher button control marker"
                                if state.value.endswith("closed")
                                else "panel header composer input marker"
                                if state.value.endswith("open_initial")
                                else "message transcript composer input button marker"
                            )
                            + f" visible state index-{index}."
                        ),
                        "pixel_facts": _pixel_facts(report().screenshot(state).data),
                    }
                    for index, state in enumerate(ScreenshotState)
                ],
            },
            {
                **response_payload(),
                "summary": " ".join(
                    f"{state.value}: "
                    + (
                        "launcher button control width"
                        if state.value.endswith("closed")
                        else "panel header composer input width"
                        if state.value.endswith("open_initial")
                        else "message transcript composer input button width"
                    )
                    + f" is {101 + index}{('px', 'rem', 'em', 'vh', 'vw', 'dvh')[index]} "
                    + f"in visible state index-{index}."
                    for index, state in enumerate(ScreenshotState)
                ),
                "observations": [
                    {
                        "screenshot_id": state.value,
                        "observation": (
                            f"{state.value}: "
                            + (
                                "launcher button control width"
                                if state.value.endswith("closed")
                                else "panel header composer input width"
                                if state.value.endswith("open_initial")
                                else "message transcript composer input button width"
                            )
                            + f" is {101 + index}{('px', 'rem', 'em', 'vh', 'vw', 'dvh')[index]} "
                            + f"in visible state index-{index}."
                        ),
                        "pixel_facts": _pixel_facts(report().screenshot(state).data),
                    }
                    for index, state in enumerate(ScreenshotState)
                ],
            },
            {
                **response_payload(),
                "summary": " ".join(
                    f"{state.value}: "
                    + (
                        "launcher button control visible"
                        if state.value.endswith("closed")
                        else "panel header composer input visible"
                        if state.value.endswith("open_initial")
                        else "message transcript composer input button visible"
                    )
                    + f" state index-{index}."
                    for index, state in enumerate(ScreenshotState)
                ),
            },
            misleading_field_root,
            false_root_payload,
        )
        for payload in semantic_cases:
            with self.subTest(payload=payload):
                critic = GeminiVisualCritic(api_key="test-key", client=FakeClient(payload))
                with self.assertRaises(VisualCriticError) as caught:
                    await critic.critique(audit=report(), brief="Brief", art_direction="Direction")
                self.assertEqual(caught.exception.error_code, "visual_evidence_unproven")

    async def test_meaningful_russian_visual_markers_are_accepted(self):
        payload = response_payload()
        russian_details = {
            ScreenshotState.DESKTOP_CLOSED: "кнопка запуска справа снизу имеет светлую границу",
            ScreenshotState.DESKTOP_OPEN_INITIAL: "панель сверху показывает заголовок и поле ввода",
            ScreenshotState.DESKTOP_AFTER_TURN_2: "сообщения диалога стоят над нижним полем ввода у светлой границы",
            ScreenshotState.MOBILE_CLOSED: "кнопка запуска сохраняет нижний и правый отступ",
            ScreenshotState.MOBILE_OPEN_INITIAL: "панель держит боковые отступы и ровный заголовок",
            ScreenshotState.MOBILE_AFTER_TURN_2: "сообщения заканчиваются над нижней кнопкой отправки справа",
        }
        payload["summary"] = " ".join(
            f"{state.value}: {russian_details[state]}." for state in ScreenshotState
        )
        payload["observations"] = [
            {
                "screenshot_id": state.value,
                "observation": f"{state.value}: {russian_details[state]}.",
                "pixel_facts": _pixel_facts(report().screenshot(state).data),
            }
            for index, state in enumerate(ScreenshotState)
        ]
        result = await GeminiVisualCritic(client=FakeClient(payload)).critique(
            audit=report(), brief="Brief", art_direction="Direction"
        )
        self.assertEqual(len(result.observations), 6)

    async def test_one_coarse_pixel_estimate_may_differ_per_original_image(self):
        payload = response_payload()
        for item in payload["observations"]:
            actual = item["pixel_facts"]["dominant_hue"]
            item["pixel_facts"]["dominant_hue"] = (
                "blue" if actual != "blue" else "orange"
            )

        result = await GeminiVisualCritic(client=FakeClient(payload)).critique(
            audit=report(), brief="Brief", art_direction="Direction"
        )

        self.assertEqual(result.critique.verdict.value, "pass")

    async def test_realistic_coarse_estimates_are_scored_across_all_six_images(self):
        payload = response_payload()

        def alternate(key, value):
            choices = {
                "luminance_band": ("dark", "mid", "light"),
                "dark_pixel_band": ("none", "some", "much"),
                "edge_density_band": ("low", "medium", "high"),
                "dominant_hue": (
                    "neutral", "red", "orange", "yellow", "green", "cyan", "blue", "purple"
                ),
            }
            return next(candidate for candidate in choices[key] if candidate != value)

        for item in payload["observations"]:
            facts = item["pixel_facts"]
            for key in ("dark_pixel_band", "dominant_hue"):
                facts[key] = alternate(key, facts[key])
        last = payload["observations"][-1]["pixel_facts"]
        last["edge_density_band"] = alternate(
            "edge_density_band", last["edge_density_band"]
        )

        result = await GeminiVisualCritic(client=FakeClient(payload)).critique(
            audit=report(), brief="Brief", art_direction="Direction"
        )

        self.assertEqual(result.critique.verdict.value, "pass")

    async def test_one_lucky_coarse_match_per_image_is_not_enough(self):
        payload = response_payload()
        alternatives = {
            "dark_pixel_band": ("none", "some", "much"),
            "edge_density_band": ("low", "medium", "high"),
            "dominant_hue": (
                "neutral", "red", "orange", "yellow", "green", "cyan", "blue", "purple"
            ),
        }
        for item in payload["observations"]:
            facts = item["pixel_facts"]
            for key, choices in alternatives.items():
                facts[key] = next(candidate for candidate in choices if candidate != facts[key])

        with self.assertRaises(VisualCriticError) as caught:
            await GeminiVisualCritic(client=FakeClient(payload)).critique(
                audit=report(), brief="Brief", art_direction="Direction"
            )

        self.assertEqual(caught.exception.error_code, "visual_evidence_unproven")
        self.assertIn("total matched 6/24", caught.exception.diagnostic or "")

    async def test_zero_coarse_matches_for_one_image_are_rejected(self):
        payload = response_payload()
        first = payload["observations"][0]["pixel_facts"]
        first["dominant_hue"] = "blue" if first["dominant_hue"] != "blue" else "orange"
        first["luminance_band"] = (
            "dark" if first["luminance_band"] != "dark" else "light"
        )
        first["dark_pixel_band"] = (
            "much" if first["dark_pixel_band"] != "much" else "none"
        )
        first["edge_density_band"] = (
            "high" if first["edge_density_band"] != "high" else "low"
        )

        with self.assertRaises(VisualCriticError) as caught:
            await GeminiVisualCritic(client=FakeClient(payload)).critique(
                audit=report(), brief="Brief", art_direction="Direction"
        )

        self.assertEqual(caught.exception.error_code, "visual_evidence_unproven")
        self.assertIn("matched 0/4", caught.exception.diagnostic or "")

    async def test_structured_screenshot_id_need_not_be_repeated_in_observation_text(self):
        payload = response_payload()
        for item in payload["observations"]:
            item["observation"] = item["observation"].split(": ", 1)[1]

        result = await GeminiVisualCritic(client=FakeClient(payload)).critique(
            audit=report(), brief="Brief", art_direction="Direction"
        )

        self.assertEqual(
            {item.screenshot_id for item in result.observations},
            {state.value for state in ScreenshotState},
        )

    async def test_russian_marker_prefix_is_not_treated_as_a_real_control(self):
        payload = response_payload()
        bad_detail = "панелевоз header composer input uses compact vertical spacing"
        payload["summary"] = payload["summary"].replace(
            "open panel header and composer input use compact vertical spacing",
            bad_detail,
        )
        payload["observations"][1]["observation"] = (
            f"desktop.open_initial: {bad_detail}."
        )

        with self.assertRaises(VisualCriticError) as caught:
            await GeminiVisualCritic(client=FakeClient(payload)).critique(
                audit=report(), brief="Brief", art_direction="Direction"
            )
        self.assertEqual(caught.exception.error_code, "visual_evidence_unproven")

    async def test_missing_state_control_diagnostic_names_the_screenshot_and_group(self):
        payload = response_payload()
        bad_detail = "orange border stays near the bottom-right edge with narrow spacing"
        payload["summary"] = payload["summary"].replace(
            "launcher button sits at the bottom-right edge with a pale border",
            bad_detail,
        )
        payload["observations"][0]["observation"] = bad_detail

        with self.assertRaises(VisualCriticError) as caught:
            await GeminiVisualCritic(client=FakeClient(payload)).critique(
                audit=report(), brief="Brief", art_direction="Direction"
            )

        diagnostic = caught.exception.diagnostic or ""
        self.assertIn("desktop.closed", diagnostic)
        self.assertIn("missing one of", diagnostic)
        for marker in ("launcher", "button", "control"):
            self.assertIn(marker, diagnostic)
        self.assertIn(bad_detail, diagnostic)

    async def test_common_russian_visual_inflections_are_accepted(self):
        payload = response_payload()
        detail = (
            "кнопка запуска выглядит контрастной и оранжевой на светлом фоне"
        )
        payload["summary"] = payload["summary"].replace(
            "launcher button sits at the bottom-right edge with a pale border",
            detail,
        )
        payload["observations"][0]["observation"] = f"desktop.closed: {detail}."

        result = await GeminiVisualCritic(client=FakeClient(payload)).critique(
            audit=report(), brief="Brief", art_direction="Direction"
        )
        self.assertEqual(result.critique.verdict.value, "pass")

    def test_every_approved_russian_adjective_accepts_common_case_forms(self):
        forms = {
            "left": "левой", "right": "правой", "top": "верхней",
            "bottom": "нижней", "center": "центральной", "aligned": "ровной",
            "wide": "широкой", "narrow": "узкой", "compact": "компактной",
            "large": "крупной", "small": "маленькой", "vertical": "вертикальной",
            "horizontal": "горизонтальной", "contrast": "контрастной",
            "pale": "бледной", "dark": "тёмной", "light": "светлой",
            "bright": "яркой", "muted": "приглушённой", "red": "красной",
            "orange": "оранжевой", "yellow": "жёлтой", "green": "зелёной",
            "cyan": "бирюзовой", "blue": "синей", "purple": "фиолетовой",
            "black": "чёрной", "white": "белой", "gray": "серой",
        }
        for expected, word in forms.items():
            with self.subTest(word=word):
                self.assertIn(
                    expected,
                    _visual_specificity_signature(
                        f"desktop.closed: кнопка выглядит {word}",
                        "desktop.closed",
                    ),
                )

    def test_provider_json_schemas_use_only_supported_gemini_keywords(self):
        supported = {
            "$id", "$defs", "$ref", "$anchor", "type", "format", "title",
            "description", "enum", "items", "prefixItems", "minItems", "maxItems",
            "minimum", "maximum", "anyOf", "oneOf", "properties",
            "additionalProperties", "required", "propertyOrdering",
        }

        def check(schema):
            for key, value in schema.items():
                self.assertIn(key, supported)
                if key in {"properties", "$defs"}:
                    for child in value.values():
                        check(child)
                elif key in {"items"} and isinstance(value, dict):
                    check(value)
                elif key in {"prefixItems", "anyOf", "oneOf"}:
                    for child in value:
                        check(child)

        check(VISUAL_CRITIC_SCHEMA)
        check(VISUAL_PROBE_SCHEMA)

    def test_pixel_facts_uses_pillow_11_compatible_pixel_access(self):
        with patch.object(
            Image.Image,
            "get_flattened_data",
            new=None,
        ):
            facts = _pixel_facts(jpeg(390, 844, 1))
        self.assertEqual(set(facts), {
            "luminance_band", "dark_pixel_band", "edge_density_band", "dominant_hue"
        })

    async def test_owned_client_closes_async_and_sync_transports(self):
        owned = SimpleNamespace(
            aio=SimpleNamespace(aclose=AsyncMock()),
            close=Mock(),
        )
        with patch("builder_lab.visual_critic.genai.Client", return_value=owned):
            critic = GeminiVisualCritic(api_key="owned-key")
        await critic.aclose()
        owned.aio.aclose.assert_awaited_once()
        owned.close.assert_called_once()

    async def test_owned_client_preserves_async_cancellation_if_sync_close_also_fails(self):
        owned = SimpleNamespace(
            aio=SimpleNamespace(aclose=AsyncMock(side_effect=asyncio.CancelledError)),
            close=Mock(side_effect=RuntimeError("secondary sync close failure")),
        )
        with patch("builder_lab.visual_critic.genai.Client", return_value=owned):
            critic = GeminiVisualCritic(api_key="owned-key")
        with self.assertRaises(asyncio.CancelledError):
            await critic.aclose()
        owned.close.assert_called_once()

    async def test_caller_cancellation_during_sync_close_overrides_prior_aio_error(self):
        sync_started = asyncio.Event()

        async def slow_sync_close():
            sync_started.set()
            await asyncio.Event().wait()

        owned = SimpleNamespace(
            aio=SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("aio close failed"))),
            close=Mock(side_effect=slow_sync_close),
        )
        with patch("builder_lab.visual_critic.genai.Client", return_value=owned):
            critic = GeminiVisualCritic(api_key="owned-key")
        task = asyncio.create_task(critic.aclose())
        await sync_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_caller_cancellation_does_not_wait_forever_for_secondary_sync_close(self):
        aio_started = asyncio.Event()

        async def slow_aio_close():
            aio_started.set()
            await asyncio.Event().wait()

        async def stuck_sync_close():
            await asyncio.Event().wait()

        owned = SimpleNamespace(
            aio=SimpleNamespace(aclose=slow_aio_close),
            close=Mock(side_effect=stuck_sync_close),
        )
        with patch("builder_lab.visual_critic.genai.Client", return_value=owned):
            critic = GeminiVisualCritic(api_key="owned-key")
        task = asyncio.create_task(critic.aclose())
        await aio_started.wait()
        task.cancel()
        done, _pending = await asyncio.wait({task}, timeout=0.25)
        if task not in done:
            task.cancel()
        self.assertIn(task, done)
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_owned_primary_async_close_has_an_internal_deadline(self):
        async def stuck_aio_close():
            await asyncio.Event().wait()

        owned = SimpleNamespace(
            aio=SimpleNamespace(aclose=stuck_aio_close),
            close=Mock(),
        )
        with patch("builder_lab.visual_critic.genai.Client", return_value=owned):
            critic = GeminiVisualCritic(api_key="owned-key")
        task = asyncio.create_task(critic.aclose())
        done, _pending = await asyncio.wait({task}, timeout=1.25)
        if task not in done:
            task.cancel()
        self.assertIn(task, done)
        with self.assertRaises(asyncio.TimeoutError):
            await task
        owned.close.assert_called_once()

    async def test_timed_out_secondary_close_drains_a_coroutine_that_ignores_one_cancel(self):
        close_task = None

        async def cancellation_resistant_close():
            nonlocal close_task
            close_task = asyncio.current_task()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.Event().wait()

        with self.assertRaises(asyncio.TimeoutError):
            await GeminiVisualCritic._await_secondary_close(
                cancellation_resistant_close(), timeout_seconds=0.01
            )
        self.assertIsNotNone(close_task)
        self.assertTrue(close_task.done())

    async def test_timed_out_secondary_close_owns_a_repeatedly_resistant_task(self):
        close_task = None
        stop_close = asyncio.Event()
        asyncio.get_running_loop().call_later(0.5, stop_close.set)

        async def repeatedly_cancellation_resistant_close():
            nonlocal close_task
            close_task = asyncio.current_task()
            while not stop_close.is_set():
                try:
                    await stop_close.wait()
                except asyncio.CancelledError:
                    pass

        with self.assertRaises(asyncio.TimeoutError):
            await GeminiVisualCritic._await_secondary_close(
                repeatedly_cancellation_resistant_close(), timeout_seconds=0.01
            )
        self.assertIsNotNone(close_task)
        self.assertTrue(close_task.done())

    async def test_rejects_findings_for_unknown_screenshots_and_payload_budget(self):
        payload = response_payload()
        payload["findings"] = [
            {
                "finding_id": "f1",
                "severity": "minor",
                "category": "site_fit",
                "screenshot_id": "unknown.state",
                "evidence": "A mismatch is visible.",
                "region": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2, "semantic_region": "panel"},
                "artifact_fields": ["css"],
                "repair_instruction": "Align the visual grammar.",
                "confidence": 0.9,
            }
        ]
        critic = GeminiVisualCritic(
            api_key="test-key",
            client=FakeClient(payload),
        )
        with self.assertRaises(VisualCriticError) as caught:
            await critic.critique(audit=report(), brief="Brief", art_direction="Direction")
        self.assertEqual(caught.exception.error_code, "invalid_visual_critique")

        payload = response_payload()
        payload["findings"] = [
            {
                "finding_id": "f1",
                "severity": "minor",
                "category": "site_fit",
                "screenshot_id": "desktop.closed",
                "evidence": "A mismatch is visible.",
                "region": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2, "semantic_region": "panel"},
                "artifact_fields": ["javascript"],
                "repair_instruction": "Align the visual grammar.",
                "confidence": 0.9,
            }
        ]
        critic = GeminiVisualCritic(
            api_key="test-key",
            client=FakeClient(payload),
        )
        with self.assertRaises(VisualCriticError) as caught:
            await critic.critique(audit=report(), brief="Brief", art_direction="Direction")
        self.assertEqual(caught.exception.error_code, "invalid_visual_critique")


if __name__ == "__main__":
    unittest.main()
