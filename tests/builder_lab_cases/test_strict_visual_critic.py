from types import SimpleNamespace

import pytest

from builder_lab.strict_visual_critic import (
    GeminiStrictVisualCritic,
    STRICT_VISUAL_CRITIC_SCHEMA,
    StrictVisualCriticError,
    build_strict_visual_critic_prompt,
)
from tests.builder_lab_cases.test_visual_critic import report


class FakeModels:
    def __init__(self, payload):
        self.payload = payload

    async def generate_content(self, **_kwargs):
        return SimpleNamespace(
            parsed=self.payload,
            response_id="strict-fake",
            usage_metadata=SimpleNamespace(
                prompt_token_count=10,
                candidates_token_count=4,
                thoughts_token_count=2,
            ),
        )


class FakeClient:
    def __init__(self, payload):
        self.aio = SimpleNamespace(models=FakeModels(payload))


def passing_payload():
    screenshot_ids = [item.evidence.screenshot_id for item in report().screenshots]
    return {
        "observations": [
            {
                "screenshot_id": screenshot_id,
                "observation": (
                    f"{screenshot_id} shows a bounded chat control with visible "
                    "placement and state-specific composition."
                ),
            }
            for screenshot_id in screenshot_ids
        ],
        "assessments": [
            {
                "dimension": dimension,
                "score": 5,
                "confidence": 0.94,
                "finding_ids": [],
            }
            for dimension in STRICT_VISUAL_CRITIC_SCHEMA["properties"][
                "assessments"
            ]["items"]["properties"]["dimension"]["enum"]
        ],
        "findings": [],
        "revision_actions": [],
        "summary": "Six screenshot states were inspected independently.",
    }


def test_russian_prompt_is_adversarial_evidence_bound_and_has_two_inspection_passes():
    prompt = build_strict_visual_critic_prompt(locale="ru", phase="raw")
    lowered = prompt.casefold()
    assert "не пишешь дружеский feedback" in lowered
    assert "не выдумывай дефект" in lowered
    assert "двухсекунд" in lowered
    assert "микродетал" in lowered
    assert "не возвращай verdict" in lowered
    assert "выглядит чисто" in lowered
    assert "можно улучшить иерархию" in lowered
    assert "добавить воздуха" in lowered
    assert "raw" in lowered


def test_english_prompt_has_the_same_adversarial_guards():
    prompt = build_strict_visual_critic_prompt(locale="en", phase="final").casefold()
    assert "not friendly feedback" in prompt
    assert "do not invent a defect" in prompt
    assert "two-second" in prompt
    assert "micro-detail" in prompt
    assert "do not return verdict" in prompt
    assert "final" in prompt


def test_provider_schema_has_no_model_owned_verdict_and_exact_dimensions():
    assert "verdict" not in STRICT_VISUAL_CRITIC_SCHEMA["properties"]
    dimensions = STRICT_VISUAL_CRITIC_SCHEMA["properties"]["assessments"]["items"][
        "properties"
    ]["dimension"]["enum"]
    assert dimensions == [
        "direction_fidelity",
        "page_subordination",
        "visual_hierarchy",
        "conversation_clarity",
        "typography_legibility",
        "spacing_alignment",
        "system_coherence",
        "responsive_composition",
        "craft_polish",
        "distinctiveness",
    ]
    assert (
        STRICT_VISUAL_CRITIC_SCHEMA["properties"]["revision_actions"]["maxItems"]
        == 3
    )


@pytest.mark.asyncio
async def test_critic_rejects_hallucinated_finding_screenshot_id():
    audit = report()
    payload = passing_payload()
    for assessment in payload["assessments"]:
        if assessment["dimension"] == "conversation_clarity":
            assessment["score"] = 3
            assessment["finding_ids"] = ["chat-flat"]
    payload["findings"] = [
        {
            "finding_id": "chat-flat",
            "dimension": "conversation_clarity",
            "screenshot_id": "desktop.imaginary_state",
            "evidence": "Both authors occupy one identical surface in the imaginary frame.",
            "region": {
                "x": 0.6,
                "y": 0.4,
                "width": 0.2,
                "height": 0.2,
                "semantic_region": "messages",
            },
            "confidence": 0.94,
        }
    ]
    payload["revision_actions"] = [
        {
            "action_id": "separate-chat",
            "finding_ids": ["chat-flat"],
            "artifact_fields": ["css"],
            "instruction": "Separate authors without changing the direction.",
        }
    ]
    critic = GeminiStrictVisualCritic(client=FakeClient(payload))

    with pytest.raises(StrictVisualCriticError) as caught:
        await critic.critique(
            audit=audit,
            brief="Compact editorial chat.",
            art_direction="Monochrome editorial assistant.",
        )

    assert caught.value.error_code == "visual_evidence_unproven"
    assert "desktop.imaginary_state" in caught.value.diagnostic


@pytest.mark.asyncio
async def test_critic_requires_every_desktop_and_mobile_screenshot_state():
    audit = report()
    payload = passing_payload()
    payload["observations"] = [
        item
        for item in payload["observations"]
        if item["screenshot_id"].startswith("desktop.")
    ]
    critic = GeminiStrictVisualCritic(client=FakeClient(payload))

    with pytest.raises(StrictVisualCriticError) as caught:
        await critic.critique(
            audit=audit,
            brief="Compact editorial chat.",
            art_direction="Monochrome editorial assistant.",
        )

    assert caught.value.error_code == "visual_evidence_unproven"
    assert "mobile.closed" in caught.value.diagnostic
