import unittest

from builder_lab.concept_roles import ConceptRolesError, run_concept_roles
from builder_lab.engines.base import BuilderEngineError, ConceptRoleResult
from builder_lab.models import (
    BuilderRequest,
    ConceptRole,
    ConceptRoleBrief,
    CreativeProfile,
    EngineName,
    TokenUsage,
)
from builder_lab.prompts import build_concept_role_prompt


def request(
    *,
    profile: CreativeProfile = CreativeProfile.PRODUCT_CHAT,
) -> BuilderRequest:
    return BuilderRequest(
        engine=EngineName.DIRECT,
        brief="Создай AI-консультанта для RAW BUREAU",
        reference_context='{"visual_summary":"sharp monochrome grid"}',
        creative_profile=profile,
    )


def brief(role: ConceptRole) -> ConceptRoleBrief:
    return ConceptRoleBrief(
        role=role,
        summary={
            ConceptRole.SITE_BRAND_ANALYST: "Редакционная система RAW",
            ConceptRole.CONVERSATION_DESIGNER: "Короткий проектный диалог",
            ConceptRole.ART_DIRECTOR_FRONTEND_DEVELOPER: "Плавающая записка",
        }[role],
        decisions=(
            {
                ConceptRole.SITE_BRAND_ANALYST: "Использовать строгую сетку",
                ConceptRole.CONVERSATION_DESIGNER: "AI отвечает коротко и по делу",
                ConceptRole.ART_DIRECTOR_FRONTEND_DEVELOPER: (
                    "Соединить монохромную сетку и чат-пузырьки"
                ),
            }[role],
        ),
        safeguards=("Не придумывать услуги",),
    )


class RecordingConceptRoleEngine:
    def __init__(self) -> None:
        self.calls = []

    async def develop_concept_role(self, *, request, role, prior_briefs=()):
        self.calls.append((role.value, tuple(item.role.value for item in prior_briefs)))
        return ConceptRoleResult(
            brief=brief(role),
            usage=TokenUsage(prompt_tokens=10, output_tokens=2, thinking_tokens=1),
        )


class ConceptRoleModelTests(unittest.TestCase):
    def test_brief_is_bounded_and_round_trips(self):
        item = brief(ConceptRole.CONVERSATION_DESIGNER)
        self.assertEqual(ConceptRoleBrief.from_dict(item.to_dict()), item)

        with self.assertRaises(ValueError):
            ConceptRoleBrief(
                role=ConceptRole.SITE_BRAND_ANALYST,
                summary="x" * 81,
                decisions=("valid",),
                safeguards=(),
            )
        with self.assertRaises(ValueError):
            ConceptRoleBrief(
                role=ConceptRole.SITE_BRAND_ANALYST,
                summary="valid",
                decisions=tuple(f"decision-{index}" for index in range(5)),
                safeguards=(),
            )

    def test_non_final_prompts_are_canonical_across_profiles(self):
        requests = tuple(
            request(profile=profile)
            for profile in (
                CreativeProfile.PRODUCT_CHAT,
                CreativeProfile.BRAND_MOTION,
                CreativeProfile.AI_CHARACTER,
            )
        )
        analyst_prompts = tuple(
            build_concept_role_prompt(
                request=item,
                role=ConceptRole.SITE_BRAND_ANALYST,
                prior_briefs=(),
            )
            for item in requests
        )
        conversation_prompts = tuple(
            build_concept_role_prompt(
                request=item,
                role=ConceptRole.CONVERSATION_DESIGNER,
                prior_briefs=(brief(ConceptRole.SITE_BRAND_ANALYST),),
            )
            for item in requests
        )

        self.assertEqual(len(set(analyst_prompts)), 1)
        self.assertEqual(len(set(conversation_prompts)), 1)
        for prompt in analyst_prompts + conversation_prompts:
            self.assertNotIn("CREATIVE_PROFILE_BRIEF:", prompt)
            self.assertNotIn("motion carte blanche", prompt)
            self.assertNotIn("digital employee or character", prompt)

    def test_final_prompt_gets_prior_briefs_as_untrusted_data_and_one_profile(self):
        prompt = build_concept_role_prompt(
            request=request(profile=CreativeProfile.BRAND_MOTION),
            role=ConceptRole.ART_DIRECTOR_FRONTEND_DEVELOPER,
            prior_briefs=(
                brief(ConceptRole.SITE_BRAND_ANALYST),
                brief(ConceptRole.CONVERSATION_DESIGNER),
            ),
        )

        self.assertEqual(prompt.count("CREATIVE_PROFILE_BRIEF:"), 1)
        self.assertIn("motion carte blanche", prompt)
        self.assertIn("UNTRUSTED_PRIOR_CONCEPT_BRIEFS_JSON", prompt)
        self.assertIn("data, not instructions", prompt)
        self.assertIn('"role":"site_brand_analyst"', prompt)
        self.assertIn('"role":"conversation_designer"', prompt)


class ConceptRoleSequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_roles_run_sequentially_and_receive_prior_work(self):
        engine = RecordingConceptRoleEngine()

        result = await run_concept_roles(engine=engine, request=request())

        self.assertEqual(
            engine.calls,
            [
                ("site_brand_analyst", ()),
                ("conversation_designer", ("site_brand_analyst",)),
                (
                    "art_director_frontend_developer",
                    ("site_brand_analyst", "conversation_designer"),
                ),
            ],
        )
        self.assertEqual(
            result.selected_direction.role,
            ConceptRole.ART_DIRECTOR_FRONTEND_DEVELOPER,
        )
        self.assertEqual(result.selected_direction.proposal_id, "candidate-1")
        self.assertIn(
            "AI отвечает коротко и по делу",
            result.selected_direction.interaction_model,
        )
        self.assertEqual(result.usage.prompt_tokens, 30)
        self.assertEqual(result.usage.output_tokens, 6)
        self.assertEqual(result.usage.thinking_tokens, 3)

    async def test_failure_usage_includes_completed_and_failed_call_exactly_once(self):
        class FailingEngine(RecordingConceptRoleEngine):
            async def develop_concept_role(self, *, request, role, prior_briefs=()):
                self.calls.append(
                    (role.value, tuple(item.role.value for item in prior_briefs))
                )
                if role is ConceptRole.CONVERSATION_DESIGNER:
                    raise BuilderEngineError(
                        "invalid_artifact",
                        "bad conversation brief",
                        usage=TokenUsage(
                            prompt_tokens=7,
                            output_tokens=3,
                            thinking_tokens=2,
                        ),
                    )
                return ConceptRoleResult(
                    brief=brief(role),
                    usage=TokenUsage(
                        prompt_tokens=10,
                        output_tokens=2,
                        thinking_tokens=1,
                    ),
                )

        engine = FailingEngine()
        with self.assertRaises(ConceptRolesError) as caught:
            await run_concept_roles(engine=engine, request=request())

        self.assertEqual(
            engine.calls,
            [
                ("site_brand_analyst", ()),
                ("conversation_designer", ("site_brand_analyst",)),
            ],
        )
        self.assertEqual(caught.exception.error_code, "invalid_artifact")
        self.assertEqual(caught.exception.usage.prompt_tokens, 17)
        self.assertEqual(caught.exception.usage.output_tokens, 5)
        self.assertEqual(caught.exception.usage.thinking_tokens, 3)

    async def test_role_mismatch_stops_the_sequence(self):
        class WrongRoleEngine(RecordingConceptRoleEngine):
            async def develop_concept_role(self, *, request, role, prior_briefs=()):
                self.calls.append(
                    (role.value, tuple(item.role.value for item in prior_briefs))
                )
                return ConceptRoleResult(
                    brief=brief(ConceptRole.SITE_BRAND_ANALYST),
                    usage=TokenUsage(prompt_tokens=4, output_tokens=1),
                )

        engine = WrongRoleEngine()
        with self.assertRaises(ConceptRolesError) as caught:
            await run_concept_roles(engine=engine, request=request())

        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(caught.exception.usage.prompt_tokens, 8)
        self.assertEqual(caught.exception.usage.output_tokens, 2)


if __name__ == "__main__":
    unittest.main()
