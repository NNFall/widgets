from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

from .models import BuilderRequest


class ModePolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BuildModePolicy:
    name: str
    stage_sequence: tuple[str, ...]
    max_repairs: int
    visual_repair_limit: int
    critic_roles: tuple[str, ...]
    model_roles: Mapping[str, str]
    requires_browser_chat_gate: bool = True
    requires_visual_gate: bool = True

    def __post_init__(self) -> None:
        name = self.name.strip().lower()
        stages = tuple(stage.strip().lower() for stage in self.stage_sequence)
        critics = tuple(role.strip() for role in self.critic_roles)
        roles = dict(self.model_roles)
        if not name or not stages or any(not stage for stage in stages):
            raise ModePolicyError("mode policy name and stages are required")
        if set(roles) != set(stages):
            raise ModePolicyError("mode policy must define one model role per stage")
        if not 0 <= self.max_repairs <= 4:
            raise ModePolicyError("mode repair budget is invalid")
        if not 0 <= self.visual_repair_limit <= 10:
            raise ModePolicyError("mode visual repair budget is invalid")
        if name == "express":
            if "conversation" not in stages or not self.requires_browser_chat_gate:
                raise ModePolicyError("express mode requires the browser chat gate")
            if stages[-1] != "motion_polish" or not self.requires_visual_gate:
                raise ModePolicyError("express mode requires the final visual gate")
            if not critics:
                raise ModePolicyError("express mode requires visual critics")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "stage_sequence", stages)
        object.__setattr__(self, "critic_roles", critics)
        object.__setattr__(self, "model_roles", MappingProxyType(roles))

    def apply_to_request(self, request: BuilderRequest) -> BuilderRequest:
        return replace(
            request,
            max_repairs=self.max_repairs,
            visual_repair_limit=self.visual_repair_limit,
        )

    def model_role_for(self, stage: str) -> str:
        try:
            return self.model_roles[stage]
        except KeyError as exc:
            raise ModePolicyError(f"stage {stage!r} is not part of {self.name}") from exc


_DIRECT_STAGES = (
    "reference_analysis",
    "art_direction",
    "foundation",
    "identity",
    "conversation",
    "motion_polish",
)
_DIRECT_ROLES = {
    "reference_analysis": "reference_analyst",
    "art_direction": "art_director",
    "foundation": "widget_generator",
    "identity": "brand_designer",
    "conversation": "conversation_designer",
    "motion_polish": "motion_designer",
}

DIRECT_MODE_POLICY = BuildModePolicy(
    name="direct",
    stage_sequence=_DIRECT_STAGES,
    max_repairs=3,
    visual_repair_limit=8,
    critic_roles=("visual_critic", "visual_judge"),
    model_roles=_DIRECT_ROLES,
)
EXPRESS_MODE_POLICY = BuildModePolicy(
    name="express",
    stage_sequence=_DIRECT_STAGES,
    max_repairs=2,
    visual_repair_limit=4,
    critic_roles=("visual_critic_primary", "visual_critic_independent", "visual_judge"),
    model_roles=_DIRECT_ROLES,
)
ANTIGRAVITY_MODE_POLICY = BuildModePolicy(
    name="antigravity",
    stage_sequence=("reference_analysis", "agent_build"),
    max_repairs=3,
    visual_repair_limit=0,
    critic_roles=(),
    model_roles={
        "reference_analysis": "reference_analyst",
        "agent_build": "agent_builder",
    },
    requires_browser_chat_gate=False,
    requires_visual_gate=False,
)

_POLICIES = {
    "direct": DIRECT_MODE_POLICY,
    "standard": DIRECT_MODE_POLICY,
    "express": EXPRESS_MODE_POLICY,
    "antigravity": ANTIGRAVITY_MODE_POLICY,
}


def get_mode_policy(mode: str) -> BuildModePolicy:
    normalized = mode.strip().lower()
    try:
        return _POLICIES[normalized]
    except KeyError as exc:
        raise ModePolicyError(f"unsupported builder mode: {mode!r}") from exc


__all__ = [
    "ANTIGRAVITY_MODE_POLICY",
    "BuildModePolicy",
    "DIRECT_MODE_POLICY",
    "EXPRESS_MODE_POLICY",
    "ModePolicyError",
    "get_mode_policy",
]
