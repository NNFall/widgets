from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.models.lineage import ModelInvocationContext
from builder_lab.visual_critic import VisualCriticRole
from builder_lab.visual_committee import VisualCriticCommittee
from builder_lab.modes import EXPRESS_MODE_POLICY
from scripts.run_builder_lab import make_visual_critic_factory


def test_production_visual_factory_builds_three_independent_role_critics():
    config = SimpleNamespace(
        gemini_api_key="test-key",
        visual_critic_model="gemini-3.6-flash",
        visual_critic_thinking_level="high",
        gemini_base_url="https://example.test",
        visual_critic_timeout_seconds=180,
        agentrouter_timeout_seconds=120,
    )
    created = []
    judges = []

    def fake_critic(**kwargs):
        critic = SimpleNamespace(**kwargs)
        created.append(critic)
        return critic

    def fake_judge(**kwargs):
        judge = SimpleNamespace(**kwargs)
        judges.append(judge)
        return judge

    with (
        patch("scripts.run_builder_lab.GeminiVisualCritic", side_effect=fake_critic),
        patch("scripts.run_builder_lab.GeminiVisualJudge", side_effect=fake_judge),
    ):
        committee = make_visual_critic_factory(config)()

    assert isinstance(committee, VisualCriticCommittee)
    assert len(created) == 3
    assert {critic.role for critic in created} == set(VisualCriticRole)
    assert all(critic.model == "gemini-3.6-flash" for critic in created)
    assert all(critic.thinking_level == "high" for critic in created)
    assert all(critic.timeout_seconds == 180 for critic in created)
    assert all(critic.routing_timeout_seconds == 120 for critic in created)
    assert len({id(critic) for critic in created}) == 3
    assert len(judges) == 1
    assert judges[0].model == "gemini-3.6-flash"
    assert judges[0].thinking_level == "high"
    assert judges[0].routing_timeout_seconds == 120


def test_express_visual_factory_routes_every_critic_and_judge_through_router():
    config = SimpleNamespace(
        gemini_api_key="test-key",
        visual_critic_model="gemini-3.6-flash",
        visual_critic_thinking_level="high",
        gemini_base_url="https://example.test",
        visual_critic_timeout_seconds=180,
        agentrouter_timeout_seconds=120,
    )
    router = SimpleNamespace(generate=object())
    run_id = object()
    invocation_context = ModelInvocationContext(
        stage_attempt_id=uuid4(),
        stage="foundation",
        operation="visual_critic",
    )
    created = []
    judges = []

    def fake_critic(**kwargs):
        critic = SimpleNamespace(**kwargs)
        created.append(critic)
        return critic

    def fake_judge(**kwargs):
        judge = SimpleNamespace(**kwargs)
        judges.append(judge)
        return judge

    with (
        patch("scripts.run_builder_lab.GeminiVisualCritic", side_effect=fake_critic),
        patch("scripts.run_builder_lab.GeminiVisualJudge", side_effect=fake_judge),
    ):
        make_visual_critic_factory(
            config,
            policy=EXPRESS_MODE_POLICY,
            model_router=router,
            run_id=run_id,
            invocation_context=invocation_context,
        )()

    assert {item.role.value for item in created} == set(
        EXPRESS_MODE_POLICY.critic_roles
    )
    assert all(item.model_router is router for item in created)
    assert all(item.routing_mode == "express" for item in created)
    assert all(item.routing_timeout_seconds == 120 for item in created)
    assert all(item.run_id is run_id for item in created)
    assert all(item.invocation_context is invocation_context for item in created)
    assert len(judges) == 1
    assert judges[0].model_router is router
    assert judges[0].routing_role == "visual_judge"
    assert judges[0].routing_mode == "express"
    assert judges[0].routing_timeout_seconds == 120
    assert judges[0].invocation_context is invocation_context
