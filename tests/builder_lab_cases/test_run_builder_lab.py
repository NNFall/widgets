from types import SimpleNamespace
from unittest.mock import patch

from builder_lab.visual_critic import VisualCriticRole
from builder_lab.visual_committee import VisualCriticCommittee
from scripts.run_builder_lab import make_visual_critic_factory


def test_production_visual_factory_builds_three_independent_role_critics():
    config = SimpleNamespace(
        gemini_api_key="test-key",
        visual_critic_model="gemini-3.6-flash",
        visual_critic_thinking_level="high",
        gemini_base_url="https://example.test",
        visual_critic_timeout_seconds=180,
    )
    created = []

    def fake_critic(**kwargs):
        critic = SimpleNamespace(**kwargs)
        created.append(critic)
        return critic

    with patch("scripts.run_builder_lab.GeminiVisualCritic", side_effect=fake_critic):
        committee = make_visual_critic_factory(config)()

    assert isinstance(committee, VisualCriticCommittee)
    assert len(created) == 3
    assert {critic.role for critic in created} == set(VisualCriticRole)
    assert all(critic.model == "gemini-3.6-flash" for critic in created)
    assert all(critic.thinking_level == "high" for critic in created)
    assert all(critic.timeout_seconds == 180 for critic in created)
    assert len({id(critic) for critic in created}) == 3
