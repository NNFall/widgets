from __future__ import annotations

import json
from uuid import uuid4

import pytest

from tools.kaigo_codex_bridge.state import BridgeEvent, BridgeStateStore


def test_state_store_reopens_persistent_active_thread(tmp_path) -> None:
    run_id = str(uuid4())
    thread_id = str(uuid4())
    store = BridgeStateStore(tmp_path)

    stored = store.bind_thread(
        run_id=run_id,
        conversation_key="direction:candidate-a",
        thread_id=thread_id,
        model="gpt-5.6-luna",
    )

    reopened = BridgeStateStore(tmp_path)
    assert stored.thread_id == thread_id
    assert reopened.get_active_thread(
        run_id=run_id,
        conversation_key="direction:candidate-a",
    ) == stored


def test_state_store_rejects_conflicting_thread_binding(tmp_path) -> None:
    run_id = str(uuid4())
    store = BridgeStateStore(tmp_path)
    store.bind_thread(
        run_id=run_id,
        conversation_key="build",
        thread_id=str(uuid4()),
        model="gpt-5.6-luna",
    )

    with pytest.raises(ValueError, match="already bound"):
        store.bind_thread(
            run_id=run_id,
            conversation_key="build",
            thread_id=str(uuid4()),
            model="gpt-5.6-luna",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "not-a-uuid"),
        ("conversation_key", "../../escape"),
        ("conversation_key", "x" * 129),
        ("thread_id", "not-a-uuid"),
        ("model", ""),
    ],
)
def test_state_store_rejects_unsafe_identifiers(tmp_path, field, value) -> None:
    values = {
        "run_id": str(uuid4()),
        "conversation_key": "repair:verify",
        "thread_id": str(uuid4()),
        "model": "gpt-5.6-luna",
    }
    values[field] = value

    with pytest.raises(ValueError):
        BridgeStateStore(tmp_path).bind_thread(**values)


def test_state_store_lists_and_archives_run_threads(tmp_path) -> None:
    run_id = str(uuid4())
    store = BridgeStateStore(tmp_path)
    first = store.bind_thread(
        run_id=run_id,
        conversation_key="build",
        thread_id=str(uuid4()),
        model="gpt-5.6-luna",
    )
    second = store.bind_thread(
        run_id=run_id,
        conversation_key="critic:customer",
        thread_id=str(uuid4()),
        model="gpt-5.6-luna",
    )

    assert store.list_active_threads(run_id) == (first, second)
    store.mark_archived(
        run_id=run_id,
        conversation_key=first.conversation_key,
    )

    assert store.get_active_thread(
        run_id=run_id,
        conversation_key=first.conversation_key,
    ) is None
    assert store.list_active_threads(run_id) == (second,)


def test_safe_event_log_contains_only_allowlisted_operational_fields(tmp_path) -> None:
    store = BridgeStateStore(tmp_path)
    run_id = str(uuid4())
    thread_id = str(uuid4())
    private_marker = "private prompt and generated code"

    store.append_event(
        BridgeEvent(
            event="turn.completed",
            run_id=run_id,
            conversation_key="build",
            thread_id=thread_id,
            model="gpt-5.6-luna",
            input_tokens=120,
            output_tokens=45,
            cached_input_tokens=20,
            duration_ms=1500,
        )
    )

    line = store.event_log_path.read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert set(payload) == {
        "timestamp",
        "event",
        "run_id",
        "conversation_key",
        "thread_id",
        "model",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "duration_ms",
        "error_code",
    }
    assert private_marker not in line
    assert payload["error_code"] is None


def test_event_log_rejects_unknown_event_name_and_error_text(tmp_path) -> None:
    store = BridgeStateStore(tmp_path)
    common = {
        "run_id": str(uuid4()),
        "conversation_key": "build",
        "thread_id": str(uuid4()),
        "model": "gpt-5.6-luna",
    }

    with pytest.raises(ValueError, match="event"):
        store.append_event(BridgeEvent(event="reasoning.saved", **common))
    with pytest.raises(ValueError, match="error_code"):
        store.append_event(
            BridgeEvent(
                event="turn.failed",
                error_code="private failure text is forbidden",
                **common,
            )
        )

