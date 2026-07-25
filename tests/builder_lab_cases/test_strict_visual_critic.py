import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from builder_lab.models import TokenUsage
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


class SequencedModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        payload, usage = outcome
        return SimpleNamespace(
            parsed=payload,
            response_id=f"strict-fake-{len(self.calls)}",
            usage_metadata=SimpleNamespace(
                prompt_token_count=usage.prompt_tokens,
                candidates_token_count=usage.output_tokens,
                thoughts_token_count=usage.thinking_tokens,
            ),
        )


class SequencedClient:
    def __init__(self, outcomes):
        self.models = SequencedModels(outcomes)
        self.aio = SimpleNamespace(models=self.models)


class SecretValidationPayload(dict):
    def __contains__(self, _key):
        raise ValueError(
            "Authorization: Bearer top-secret-token "
            "api_key=AIzaSyDefinitelySecret1234567890 "
            "https://operator:gateway-password@example.test/path "
            "client_secret=quoted-secret-value; safe score linkage mismatch"
        )


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
    assert "64–78%" in lowered
    assert "видимый контекст страницы" in lowered
    assert "не требуй fullscreen или bottom sheet" in lowered
    assert "raw" in lowered


def test_english_prompt_has_the_same_adversarial_guards():
    prompt = build_strict_visual_critic_prompt(locale="en", phase="final").casefold()
    assert "not friendly feedback" in prompt
    assert "do not invent a defect" in prompt
    assert "two-second" in prompt
    assert "micro-detail" in prompt
    assert "do not return verdict" in prompt
    assert "64–78%" in prompt
    assert "host page remains visible" in prompt
    assert "never require fullscreen or a bottom sheet" in prompt
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


def _payload_with_cross_dimension_finding():
    payload = deepcopy(passing_payload())
    assessment = next(
        item
        for item in payload["assessments"]
        if item["dimension"] == "typography_legibility"
    )
    assessment["score"] = 3
    assessment["finding_ids"] = ["finding-cross-dimension"]
    payload["findings"] = [
        {
            "finding_id": "finding-cross-dimension",
            "dimension": "conversation_clarity",
            "screenshot_id": "desktop.open",
            "evidence": "The message author treatment is visually ambiguous.",
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
            "action_id": "fix-chat-authorship",
            "finding_ids": ["finding-cross-dimension"],
            "artifact_fields": ["css"],
            "instruction": "Clarify chat authorship without redesigning the widget.",
        }
    ]
    return payload


@pytest.mark.asyncio
async def test_critic_retries_one_semantically_invalid_response_and_sums_usage():
    first_usage = TokenUsage(prompt_tokens=10, output_tokens=4, thinking_tokens=2)
    second_usage = TokenUsage(prompt_tokens=12, output_tokens=5, thinking_tokens=3)
    client = SequencedClient(
        [
            (_payload_with_cross_dimension_finding(), first_usage),
            (passing_payload(), second_usage),
        ]
    )
    critic = GeminiStrictVisualCritic(client=client)

    result = await critic.critique(
        audit=report(),
        brief="Compact editorial chat.",
        art_direction="Monochrome editorial assistant.",
    )

    assert result.usage == first_usage + second_usage
    assert len(client.models.calls) == 2
    assert client.models.calls[0]["config"].max_output_tokens is None
    correction = client.models.calls[1]["contents"][-1].text
    assert (
        "ValueError: score 1..3 must link a finding from the same dimension"
        in correction
    )
    assert "Every finding must use the same dimension" in correction
    assert "Every finding must be linked to a score 1..3 assessment" in correction


@pytest.mark.asyncio
async def test_critic_retries_one_audit_binding_failure():
    incomplete = passing_payload()
    incomplete["observations"] = [
        item
        for item in incomplete["observations"]
        if item["screenshot_id"] != "mobile.closed"
    ]
    client = SequencedClient(
        [
            (incomplete, TokenUsage(prompt_tokens=10)),
            (passing_payload(), TokenUsage(prompt_tokens=12)),
        ]
    )
    critic = GeminiStrictVisualCritic(client=client)

    result = await critic.critique(
        audit=report(),
        brief="Compact editorial chat.",
        art_direction="Monochrome editorial assistant.",
    )

    assert result.usage == TokenUsage(prompt_tokens=22)
    assert len(client.models.calls) == 2
    correction = client.models.calls[1]["contents"][-1].text
    assert "missing=mobile.closed" in correction


@pytest.mark.asyncio
async def test_critic_stops_after_two_semantic_failures_and_sums_usage():
    first_usage = TokenUsage(prompt_tokens=10, output_tokens=4, thinking_tokens=2)
    second_usage = TokenUsage(prompt_tokens=11, output_tokens=6, thinking_tokens=3)
    client = SequencedClient(
        [
            (_payload_with_cross_dimension_finding(), first_usage),
            (_payload_with_cross_dimension_finding(), second_usage),
        ]
    )
    critic = GeminiStrictVisualCritic(client=client)

    with pytest.raises(StrictVisualCriticError) as caught:
        await critic.critique(
            audit=report(),
            brief="Compact editorial chat.",
            art_direction="Monochrome editorial assistant.",
        )

    assert caught.value.error_code == "strict_visual_response_invalid"
    assert caught.value.usage == first_usage + second_usage
    assert len(client.models.calls) == 2


@pytest.mark.asyncio
async def test_critic_does_not_retry_provider_timeout():
    class SlowModels:
        def __init__(self):
            self.calls = 0

        async def generate_content(self, **_kwargs):
            self.calls += 1
            await asyncio.sleep(1)

    models = SlowModels()
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    critic = GeminiStrictVisualCritic(client=client, timeout_seconds=0.001)

    with pytest.raises(StrictVisualCriticError) as caught:
        await critic.critique(
            audit=report(),
            brief="Compact editorial chat.",
            art_direction="Monochrome editorial assistant.",
        )

    assert caught.value.error_code == "strict_visual_critic_timeout"
    assert caught.value.usage == TokenUsage()
    assert models.calls == 1


@pytest.mark.asyncio
async def test_critic_preserves_first_attempt_usage_on_retry_cancellation():
    first_usage = TokenUsage(prompt_tokens=10, output_tokens=4, thinking_tokens=2)
    client = SequencedClient(
        [
            (_payload_with_cross_dimension_finding(), first_usage),
            asyncio.CancelledError(),
        ]
    )
    critic = GeminiStrictVisualCritic(client=client)

    with pytest.raises(asyncio.CancelledError) as caught:
        await critic.critique(
            audit=report(),
            brief="Compact editorial chat.",
            art_direction="Monochrome editorial assistant.",
        )

    assert caught.value.usage == first_usage
    assert len(client.models.calls) == 2


@pytest.mark.asyncio
async def test_critic_redacts_secrets_from_retry_validation_correction():
    client = SequencedClient(
        [
            (SecretValidationPayload(), TokenUsage(prompt_tokens=10)),
            (passing_payload(), TokenUsage(prompt_tokens=12)),
        ]
    )
    critic = GeminiStrictVisualCritic(client=client)

    await critic.critique(
        audit=report(),
        brief="Compact editorial chat.",
        art_direction="Monochrome editorial assistant.",
    )

    correction = client.models.calls[1]["contents"][-1].text
    assert "top-secret-token" not in correction
    assert "DefinitelySecret" not in correction
    assert "gateway-password" not in correction
    assert "quoted-secret-value" not in correction
    assert "[REDACTED]" in correction
    assert "safe score linkage mismatch" in correction


@pytest.mark.asyncio
async def test_non_retry_provider_error_preserves_direct_cause():
    provider_error = RuntimeError("provider exploded")
    client = SequencedClient([provider_error])
    critic = GeminiStrictVisualCritic(client=client)

    with pytest.raises(StrictVisualCriticError) as caught:
        await critic.critique(
            audit=report(),
            brief="Compact editorial chat.",
            art_direction="Monochrome editorial assistant.",
        )

    assert caught.value.error_code == "strict_visual_critic_unavailable"
    assert caught.value.__cause__ is provider_error
    assert len(client.models.calls) == 1
