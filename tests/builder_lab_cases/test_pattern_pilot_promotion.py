from __future__ import annotations

import json

import pytest

from scripts.promote_atomic_pattern_pilot import (
    _load_decisions,
    _quality_counts,
    desired_pilot_states,
)
from builder_lab.patterns.atomic_registry import load_builtin_atomic_registry


def test_pilot_promotes_review_ready_catalog_but_preserves_owner_rejections() -> None:
    decisions = {
        ("background-aurora-dust", 1): "rejected",
        ("launcher-concierge-capsule", 1): "rejected",
        ("shell-layered-concierge", 1): "rejected",
    }

    states = desired_pilot_states(decisions=decisions)

    assert len(states) >= 84
    assert sum(state == "approved" for state in states.values()) == 25
    assert sum(state == "rejected" for state in states.values()) == 3
    assert sum(state == "ready_for_review" for state in states.values()) == len(states) - 28
    assert states[("assistant-message-cascade", 1)] == "approved"


def test_pilot_preserves_manifest_ready_state_without_an_explicit_decision() -> None:
    states = desired_pilot_states(decisions={})

    assert states[("assistant-message-card-bloom", 1)] == "ready_for_review"
    assert states[("widget-open-spring-unfold", 1)] == "approved"


def test_pilot_rejects_decisions_for_unknown_exact_versions() -> None:
    with pytest.raises(ValueError, match="unknown versions"):
        desired_pilot_states(decisions={("missing-pattern", 1): "approved"})


def test_pilot_rejects_explicit_approval_of_quality_ineligible_fixture() -> None:
    with pytest.raises(ValueError, match="quality-ineligible"):
        desired_pilot_states(
            decisions={("widget-open-technical", 1): "approved"}
        )


@pytest.mark.parametrize("version", [True, 1.0, "1", 0, -1])
def test_review_decision_version_requires_exact_positive_integer(tmp_path, version) -> None:
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "decisions": [{"pattern_id": "widget-open-spring-unfold", "version": version, "state": "approved"}],
    }), encoding="utf-8")

    with pytest.raises((ValueError, TypeError)):
        _load_decisions(path)


def test_pilot_counts_separate_selectable_versions_from_approved_fixtures() -> None:
    states = desired_pilot_states(decisions={})
    counts = _quality_counts(states, load_builtin_atomic_registry())

    assert counts["catalog_versions"] == len(states)
    assert counts["approved_versions"] == counts["approved"]
    assert counts["approved_fixtures"] >= 1
    assert counts["selectable_versions"] < counts["approved_versions"]


def test_review_decisions_non_mapping_payload_is_controlled_error(tmp_path) -> None:
    path = tmp_path / "decisions.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="contract"):
        _load_decisions(path)
