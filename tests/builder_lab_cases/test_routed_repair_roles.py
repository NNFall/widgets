from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.contracts import ModelRequest, ModelResponse, ModelUsage, ProviderCapabilities
from app.models.router import ModelPolicy, ModelRouter, ProviderTarget, SqlModelCallAudit
from app.saas.models import ModelCall
from builder_lab.engines.gemini_direct import GeminiDirectEngine
from builder_lab.models import (
    BuilderRequest,
    DirectionProposal,
    DirectionRole,
    EngineName,
    Stage,
)
from builder_lab.worker import OrchestratorStageHandler, RunClaim, StageInput
from tests.builder_lab_cases.test_validation import GOOD_HTML, artifact
from tests.builder_lab_cases.test_visual_review import finding
from tests.saas_cases.test_trial_service import _database


def _direction() -> DirectionProposal:
    return DirectionProposal(
        proposal_id="candidate-1",
        role=DirectionRole.INTERACTION_INVENTOR,
        title="Compact concierge",
        art_direction="A compact branded chat widget.",
        interaction_model="Open a small panel and keep the page visible.",
        safeguards=("Keep every chat control functional",),
    )


def _composition_payload() -> dict[str, object]:
    pattern_ids = {
        "launcher": "orb-pulse",
        "shell": "compact-chat",
        "messages": "paired-bubbles",
        "composer": "single-line-pill",
        "motion": "spring-reveal",
    }
    return {
        "schema_version": 1,
        "direction_id": "candidate-1",
        "selections": [
            {
                "slot": slot,
                "pattern_id": pattern_id,
                "version": 1,
                "parameters": {},
                "reason": f"Use the verified {slot} pattern.",
            }
            for slot, pattern_id in pattern_ids.items()
        ],
        "custom_escape": None,
        "summary": "Verified compact chat composition.",
    }


def _direct_context(direction: DirectionProposal) -> dict[str, object]:
    return {
        "selected_direction": direction.to_dict(),
        "composition_plan": _composition_payload(),
    }


def _claim(run_id, *, stage: str) -> RunClaim:
    return RunClaim(
        run_id=run_id,
        project_id=__import__("uuid").uuid4(),
        worker_id="role-worker",
        mode="express",
        next_stage=stage,
        last_completed_stage="art_direction",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )


def _router(factory, provider) -> ModelRouter:
    target = ProviderTarget("provider", "model", 1, 1)
    return ModelRouter(
        providers={"provider": provider},
        policies={
            ("widget_generator", "express"): ModelPolicy("builder-v1", (target,)),
            ("motion_designer", "express"): ModelPolicy("builder-v1", (target,)),
            ("repair", "express"): ModelPolicy("repair-v1", (target,)),
        },
        audit=SqlModelCallAudit(factory),
    )


def _handler(*, router, stage_input, visual_gate_factory=None):
    class Queue:
        async def stage_input(self, claim):
            return stage_input

    return OrchestratorStageHandler(
        queue=Queue(),
        engine_factories={},
        reference_analyzer=lambda _url: None,
        visual_gate_factory=visual_gate_factory,
        routed_engine_factory=lambda claim, role: GeminiDirectEngine(
            model_router=router,
            routing_role=role,
            routing_mode=claim.mode,
            run_id=claim.run_id,
        ),
    )


@pytest.mark.asyncio
async def test_deterministic_artifact_repair_uses_repair_role_and_exact_run_audit(
    tmp_path,
) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)

    class Provider:
        capabilities = ProviderCapabilities(structured_output=True)

        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.calls += 1
            candidate = artifact(revision=1, stage=Stage.FOUNDATION)
            if self.calls == 1:
                candidate = artifact(
                    revision=1,
                    stage=Stage.FOUNDATION,
                    body_html=GOOD_HTML + "<script>alert(1)</script>",
                )
            payload = candidate.to_dict()
            payload.pop("schema_version")
            return ModelResponse(
                text="{}",
                parsed=payload,
                usage=ModelUsage(input_tokens=10, output_tokens=4, thinking_tokens=1),
                request_id=f"artifact-{self.calls}",
            )

    router = _router(factory, Provider())
    direction = _direction()
    handler = _handler(
        router=router,
        stage_input=StageInput(
            request=BuilderRequest(engine=EngineName.DIRECT, brief="Repair invalid HTML"),
            context=_direct_context(direction),
        ),
    )
    try:
        result = await handler(_claim(run_ids[0], stage="foundation"))
        assert result.artifact is not None
        async with factory() as database:
            calls = (
                await database.execute(
                    select(ModelCall).where(ModelCall.run_id == run_ids[0]).order_by(ModelCall.created_at)
                )
            ).scalars().all()
        assert [(call.role, call.mode) for call in calls] == [
            ("widget_generator", "express"),
            ("repair", "express"),
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_visual_finding_artifact_generation_uses_repair_role(tmp_path) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)

    class Provider:
        capabilities = ProviderCapabilities(structured_output=True)

        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.calls += 1
            candidate = artifact(
                revision=5,
                stage=Stage.MOTION_POLISH,
                css=artifact().css + ("\n.repaired {}" if self.calls == 2 else ""),
            )
            payload = candidate.to_dict()
            payload.pop("schema_version")
            return ModelResponse(text="{}", parsed=payload, request_id=f"motion-{self.calls}")

    class RepairingGate:
        async def evaluate(self, **kwargs):
            repaired = await kwargs["engine"].generate(
                request=kwargs["request"],
                stage=Stage.MOTION_POLISH,
                revision=kwargs["candidate"].revision,
                previous_artifact=kwargs["candidate"],
                visual_findings=(finding("judge-1", "Composer is clipped."),),
                selected_direction=kwargs["selected_direction"],
            )
            return repaired.artifact

    router = _router(factory, Provider())
    direction = _direction()
    handler = _handler(
        router=router,
        stage_input=StageInput(
            request=BuilderRequest(engine=EngineName.DIRECT, brief="Repair visual issue"),
            previous_artifact=artifact(revision=4, stage=Stage.CONVERSATION),
            context=_direct_context(direction),
        ),
        visual_gate_factory=lambda _claim: RepairingGate(),
    )
    try:
        await handler(_claim(run_ids[0], stage="motion_polish"))
        async with factory() as database:
            calls = (
                await database.execute(
                    select(ModelCall).where(ModelCall.run_id == run_ids[0]).order_by(ModelCall.created_at)
                )
            ).scalars().all()
        assert [(call.role, call.mode) for call in calls] == [
            ("motion_designer", "express"),
            ("repair", "express"),
        ]
    finally:
        await engine.dispose()
