from __future__ import annotations

from dataclasses import replace

import pytest

from builder_lab.modes import (
    EXPRESS_MODE_POLICY,
    ModePolicyError,
    get_mode_policy,
)
from builder_lab.models import BuilderRequest, EngineName, Stage
from builder_lab.orchestrator import stages_for_mode
from builder_lab.worker import (
    DIRECT_STAGE_SEQUENCE,
    PostgresWorkerQueue,
    STAGE_PUBLIC_NAMES,
)


def test_express_policy_is_a_complete_reviewable_build() -> None:
    policy = get_mode_policy("express")

    assert policy is EXPRESS_MODE_POLICY
    assert policy.stage_sequence == DIRECT_STAGE_SEQUENCE
    assert policy.stage_sequence[-2:] == ("conversation", "motion_polish")
    assert policy.requires_browser_chat_gate
    assert policy.requires_visual_gate
    assert policy.critic_roles
    assert policy.max_repairs >= 1
    assert policy.visual_repair_limit >= 1
    assert set(policy.model_roles) == set(policy.stage_sequence)


def test_durable_worker_and_orchestrator_use_same_express_sequence() -> None:
    expected = (
        Stage.ART_DIRECTION,
        Stage.FOUNDATION,
        Stage.IDENTITY,
        Stage.CONVERSATION,
        Stage.MOTION_POLISH,
    )

    assert PostgresWorkerQueue.stage_sequence("express") == (
        "reference_analysis",
        *(stage.value for stage in expected),
    )
    assert stages_for_mode("express") == expected


def test_express_policy_applies_budgets_without_mutating_original_request() -> None:
    original = BuilderRequest(
        engine=EngineName.DIRECT,
        brief="Собери полный пробный виджет",
        max_repairs=4,
        visual_repair_limit=10,
    )

    configured = EXPRESS_MODE_POLICY.apply_to_request(original)

    assert configured is not original
    assert configured.max_repairs == EXPRESS_MODE_POLICY.max_repairs
    assert configured.visual_repair_limit == EXPRESS_MODE_POLICY.visual_repair_limit
    assert original.max_repairs == 4
    assert original.visual_repair_limit == 10


def test_express_motion_stage_cannot_disable_visual_gate() -> None:
    with pytest.raises(ModePolicyError, match="visual gate"):
        replace(EXPRESS_MODE_POLICY, requires_visual_gate=False)


def test_express_conversation_stage_cannot_disable_browser_chat_gate() -> None:
    with pytest.raises(ModePolicyError, match="browser chat gate"):
        replace(EXPRESS_MODE_POLICY, requires_browser_chat_gate=False)


def test_express_public_stage_messages_are_russian() -> None:
    for stage in EXPRESS_MODE_POLICY.stage_sequence:
        message = STAGE_PUBLIC_NAMES[stage]
        assert message
        assert any("а" <= character.lower() <= "я" or character.lower() == "ё" for character in message)
