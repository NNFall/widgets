from __future__ import annotations

import json
from collections.abc import Iterator, Mapping

import pytest

from builder_lab.generation_events import (
    EVENT_REGISTRY,
    GenerationEventType,
    project_public_generation_event,
)


EXPECTED_GENERATION_EVENT_TYPES = {
    "artifact.committed",
    "artifact.draft_staged",
    "artifact.seeded",
    "artifact.validated",
    "direction.failed",
    "direction.judged",
    "provider.dispatch_armed",
    "reference.completed",
    "reference.failed",
    "reference.started",
    "refinement.started",
    "repair.completed",
    "repair.started",
    "run.cancel_requested",
    "run.cancelled",
    "run.completed",
    "run.created",
    "run.failed",
    "run.terminal_marked",
    "screenshot.captured",
    "stage.completed",
    "stage.failed",
    "stage.interrupted",
    "stage.result_staged",
    "stage.retry_scheduled",
    "stage.started",
    "visual_audit.blocked",
    "visual_audit.completed",
    "visual_audit.passed",
    "visual_audit.started",
    "visual_critic.completed",
    "visual_judge.completed",
    "visual_repair.completed",
    "visual_repair.started",
    "visual_repair.verifier_completed",
    "visual_repair.verifier_started",
}


def _serialized(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def test_event_registry_is_total_and_contains_no_funnel_events() -> None:
    assert {item.value for item in GenerationEventType} == EXPECTED_GENERATION_EVENT_TYPES
    assert set(EVENT_REGISTRY) == set(GenerationEventType)
    assert not (
        {"run_queued", "free_result", "first_artifact"}
        & EXPECTED_GENERATION_EVENT_TYPES
    )


def test_event_registry_marks_only_stage_result_compatible_events() -> None:
    compatible = {
        event_type.value
        for event_type, spec in EVENT_REGISTRY.items()
        if spec.stage_result_allowed
    }

    assert compatible == {"reference.completed", "repair.completed"}


def test_unknown_legacy_event_projects_to_fail_closed_sentinel() -> None:
    projected = project_public_generation_event(
        event_type="legacy.secret_event",
        public_message="token=secret-value-123",
        payload={"status": "running", "diagnostic": "secret-value-123"},
    )

    assert projected.event_type == "generation.unknown"
    assert projected.message is None
    assert projected.payload == {}


def test_public_projection_uses_per_event_fields_and_safe_value_shapes() -> None:
    projected = project_public_generation_event(
        event_type="stage.completed",
        public_message="Готово; token=private-message-value",
        payload={
            "status": "completed",
            "stage": "foundation",
            "next_stage": "identity",
            "usage": {
                "prompt_tokens": 12,
                "output_tokens": -1,
                "thinking_tokens": True,
                "provider": "private-provider",
            },
            "output_refs": [
                "model-call:foundation-1",
                "https://private.example/result",
                r"C:\private\result.json",
                "/tmp/private-result",
                "../relative-result",
                "folder/result.json",
                "bad\x00ref",
                "person@example.com",
                "sk-proj-abcdefghijklmnopqrstuvxyz",
                "mailto:person@example.com",
                "C:private-result.json",
                42,
            ],
            "revision": 99,
            "attempt": 4,
            "request": {"authorization": "private-request-value"},
            "result": {"raw": "private-result-value"},
            "artifact": {"html": "private-artifact-value"},
            "diagnostic": "private-diagnostic-value",
            "worker_id": "private-worker-value",
            "attempt_id": "private-attempt-value",
            "provider": "private-provider-value",
            "model": "private-model-value",
            "path": "/tmp/private-path",
            "unknown": "private-unknown-value",
        },
    )

    assert projected.event_type == "stage.completed"
    assert projected.message == "Готово; token=[REDACTED]"
    assert projected.payload == {
        "status": "completed",
        "stage": "foundation",
        "next_stage": "identity",
        "usage": {"prompt_tokens": 12},
        "output_refs": ["model-call:foundation-1"],
    }
    serialized = _serialized(projected.payload)
    if "private-" in serialized:
        pytest.fail("private event material leaked into the public projection")


def test_public_projection_bounds_and_redacts_issue_and_change_arrays() -> None:
    projected = project_public_generation_event(
        event_type="visual_critic.completed",
        public_message="Проверка завершена",
        payload={
            "status": "completed",
            "stage": "motion_polish",
            "revision": 3,
            "issues": [
                {
                    "code": "contrast",
                    "field": "composer",
                    "message": "Напишите person@example.com",
                    "diagnostic": "private-diagnostic-value",
                    "request": {"token": "private-request-value"},
                },
                "token=private-issue-value",
            ],
            "changes": [
                "Исправлен token=private-change-value",
                {"summary": "must-not-be-public"},
            ],
        },
    )

    assert projected.payload == {
        "status": "completed",
        "stage": "motion_polish",
        "revision": 3,
        "issues": [
            {
                "code": "contrast",
                "field": "composer",
                "message": "Напишите [REDACTED]",
            },
            "token=[REDACTED]",
        ],
        "changes": ["Исправлен token=[REDACTED]"],
    }


def test_public_projection_rejects_invalid_timestamps_and_non_mapping_payloads() -> None:
    invalid_timestamp = project_public_generation_event(
        event_type="stage.retry_scheduled",
        public_message=None,
        payload={
            "status": "queued",
            "not_before": "tomorrow /tmp/private",
            "attempt": 2,
        },
    )
    invalid_payload = project_public_generation_event(
        event_type="run.created",
        public_message=object(),
        payload=[("status", "queued")],
    )

    assert invalid_timestamp.payload == {"status": "queued", "attempt": 2}
    assert invalid_payload.message is None
    assert invalid_payload.payload == {}


def test_public_projection_bounds_usage_counter_count_and_integer_size() -> None:
    projected = project_public_generation_event(
        event_type="run.completed",
        public_message="Готово",
        payload={
            "status": "completed",
            "revision": 10**5_000,
            "usage": {
                **{f"counter_{index}": index for index in range(100)},
                "huge_counter": 10**5_000,
                "negative_counter": -1,
            },
        },
    )

    assert "revision" not in projected.payload
    assert "huge_counter" not in projected.payload["usage"]
    assert "negative_counter" not in projected.payload["usage"]
    assert len(projected.payload["usage"]) <= 16
    json.dumps(projected.payload, ensure_ascii=False)


def test_public_projection_bounds_scanned_payload_and_usage_entries() -> None:
    class ProbeMapping(Mapping[str, object]):
        def __init__(self, *, usage: bool) -> None:
            self.scanned = 0
            self.usage = usage

        def __getitem__(self, key: str) -> object:
            raise KeyError(key)

        def __iter__(self) -> Iterator[str]:
            return iter(())

        def __len__(self) -> int:
            return 100_000

        def items(self):
            for index in range(100_000):
                self.scanned += 1
                if self.scanned > 128:
                    raise RuntimeError("private unbounded iterator detail")
                key = f"INVALID_{index}" if self.usage else f"private_{index}"
                yield key, index

    payload = ProbeMapping(usage=False)
    usage = ProbeMapping(usage=True)

    projected_payload = project_public_generation_event(
        event_type="run.created",
        public_message=None,
        payload=payload,
    )
    projected_usage = project_public_generation_event(
        event_type="run.completed",
        public_message=None,
        payload={"status": "completed", "usage": usage},
    )

    assert projected_payload.payload == {}
    assert payload.scanned <= 65
    assert projected_usage.payload == {"status": "completed", "usage": {}}
    assert usage.scanned <= 65


def test_public_projection_fails_closed_for_malformed_payload_iteration() -> None:
    class BrokenPayload(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise RuntimeError("private payload detail")

        def __iter__(self) -> Iterator[str]:
            raise RuntimeError("private iterator detail")

        def __len__(self) -> int:
            return 1

    projected = project_public_generation_event(
        event_type="run.created",
        public_message="Запуск создан",
        payload=BrokenPayload(),
    )

    assert projected.payload == {}


def test_public_projection_keeps_unencodable_public_text_json_safe() -> None:
    projected = project_public_generation_event(
        event_type="run.created",
        public_message="\ud800",
        payload={"status": "\ud800"},
    )

    assert projected.message == "[TRUNCATED]"
    assert projected.payload == {"status": "[TRUNCATED]"}
    json.dumps(
        {"message": projected.message, "payload": projected.payload},
        ensure_ascii=False,
    ).encode("utf-8")


def test_public_projection_redacts_vendor_prefixed_inline_credentials() -> None:
    projected = project_public_generation_event(
        event_type="run.created",
        public_message="YOOKASSA_SECRET_KEY=private-yookassa-value",
        payload={"status": "OPENAI_API_KEY=private-openai-value"},
    )

    assert projected.message == "YOOKASSA_SECRET_KEY=[REDACTED]"
    assert projected.payload == {"status": "OPENAI_API_KEY=[REDACTED]"}


def test_public_projection_coerces_hostile_string_subclasses_safely() -> None:
    class HostileString(str):
        def __getitem__(self, _key):
            raise RuntimeError("private projection exception detail")

        def __str__(self) -> str:
            raise RuntimeError("private projection conversion detail")

    projected = project_public_generation_event(
        event_type="run.created",
        public_message=HostileString("token=private-message-value"),
        payload={"status": HostileString("token=private-status-value")},
    )

    assert projected.message == "token=[REDACTED]"
    assert projected.payload == {"status": "token=[REDACTED]"}
