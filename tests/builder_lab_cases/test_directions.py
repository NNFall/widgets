import asyncio
import unittest

from app.models.contracts import (
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderCapabilities,
)
from app.models.router import InMemoryModelCallAudit, ModelPolicy, ModelRouter, ProviderTarget
from builder_lab.directions import DirectionBoardError, DIRECTION_ROLES, run_direction_board
from builder_lab.engines.base import (
    BuilderEngineError,
    DirectionJudgeResult,
    DirectionProposalResult,
)
from builder_lab.engines.gemini_direct import GeminiDirectEngine
from builder_lab.models import (
    BuilderRequest,
    CreativeProfile,
    DirectionJudgement,
    DirectionProposal,
    DirectionRole,
    EngineName,
    Stage,
    TokenUsage,
)
from builder_lab.prompts import build_direction_proposal_prompt, build_stage_prompt


def proposal(role: DirectionRole, proposal_id: str) -> DirectionProposal:
    return DirectionProposal(
        proposal_id=proposal_id,
        role=role,
        title=f"Direction {proposal_id}",
        art_direction="Editorial project note with sharp geometry and restrained type.",
        interaction_model="A compact launcher opens a bounded working note.",
        safeguards=("No fake actions", "No generic chat bubbles"),
    )


class BarrierDirectionEngine:
    def __init__(self):
        self.proposal_calls = []
        self.judge_calls = []
        self.active = 0
        self.max_active = 0
        self.all_started = asyncio.Event()

    async def propose_direction(self, *, request, role, proposal_id):
        self.proposal_calls.append((request, role, proposal_id))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active == 3:
            self.all_started.set()
        await asyncio.wait_for(self.all_started.wait(), timeout=1)
        self.active -= 1
        return DirectionProposalResult(
            proposal=proposal(role, proposal_id),
            usage=TokenUsage(prompt_tokens=10, output_tokens=2, thinking_tokens=1),
        )

    async def judge_directions(self, *, request, proposals):
        self.judge_calls.append((request, proposals))
        return DirectionJudgeResult(
            judgement=DirectionJudgement(
                selected_proposal_id="candidate-2",
                rationale="Best balance of site fit and truthful interaction.",
            ),
            usage=TokenUsage(prompt_tokens=7, output_tokens=3, thinking_tokens=2),
        )


class DirectionBoardTests(unittest.IsolatedAsyncioTestCase):
    def test_direction_and_stage_prompts_include_only_selected_profile_brief(self):
        distinctive_phrases = {
            CreativeProfile.BALANCED: "preserve the legacy balanced direction",
            CreativeProfile.PRODUCT_CHAT: "instant clarity",
            CreativeProfile.BRAND_MOTION: "motion carte blanche",
            CreativeProfile.AI_CHARACTER: "digital employee or character",
        }

        for profile, expected_phrase in distinctive_phrases.items():
            with self.subTest(profile=profile.value):
                request = BuilderRequest(
                    engine=EngineName.DIRECT,
                    brief="RAW BUREAU widget",
                    creative_profile=profile,
                )
                prompts = (
                    build_direction_proposal_prompt(
                        request=request,
                        role=DirectionRole.BRAND_ARCHAEOLOGIST,
                    ),
                    build_stage_prompt(
                        request=request,
                        stage=Stage.ART_DIRECTION,
                        revision=1,
                        previous_artifact=None,
                    ),
                )

                for prompt in prompts:
                    self.assertEqual(prompt.count("CREATIVE_PROFILE_BRIEF:"), 1)
                    self.assertIn(expected_phrase, prompt)
                    self.assertIn("WIDGET_CONTRACT chat-v1 VERSION 1", prompt)
                    for other_phrase in distinctive_phrases.values():
                        if other_phrase != expected_phrase:
                            self.assertNotIn(other_phrase, prompt)

    def test_stage_prompt_declares_exact_runtime_open_state_contract(self):
        prompt = build_stage_prompt(
            request=BuilderRequest(engine=EngineName.DIRECT, brief="RAW BUREAU widget"),
            stage=Stage.ART_DIRECTION,
            revision=1,
            previous_artifact=None,
        )

        self.assertIn(
            '.kaigo-widget.kaigo-preview-open [data-region="panel"]', prompt
        )
        self.assertIn('[data-region="panel"][data-open]', prompt)
        self.assertIn('data-state="open"', prompt)

    def test_stage_prompt_lets_the_model_choose_adaptive_geometry(self):
        prompt = build_stage_prompt(
            request=BuilderRequest(engine=EngineName.DIRECT, brief="RAW BUREAU widget"),
            stage=Stage.ART_DIRECTION,
            revision=1,
            previous_artifact=None,
        )

        self.assertNotIn("mobile rules apply only at viewport widths <= 600px", prompt)
        self.assertNotIn("at every viewport width >= 601px", prompt)
        self.assertIn("не заданы шаблоном", prompt)
        self.assertIn("mobile layout адаптируется к доступному месту", prompt)

    def test_stage_prompt_requires_reduced_motion_without_optional_conflict(self):
        prompt = build_stage_prompt(
            request=BuilderRequest(engine=EngineName.DIRECT, brief="RAW BUREAU widget"),
            stage=Stage.MOTION_POLISH,
            revision=5,
            previous_artifact=None,
        )

        self.assertIn("prefers-reduced-motion", prompt)
        self.assertNotIn(
            "reduced-motion можно добавить как улучшение доступности",
            prompt,
        )
        self.assertIn(
            "reduced-motion обязателен",
            prompt,
        )

    def test_motion_prompt_uses_the_runtime_attention_class_not_legacy_attributes(self):
        prompt = build_stage_prompt(
            request=BuilderRequest(engine=EngineName.DIRECT, brief="RAW BUREAU widget"),
            stage=Stage.MOTION_POLISH,
            revision=5,
            previous_artifact=None,
        )

        self.assertIn(
            ".kaigo-widget.kaigo-preview-attention [data-region=\"launcher\"]",
            prompt,
        )
        self.assertIn("Do not use `[data-attention]`", prompt)
        self.assertIn("change across multiple rendered frames", prompt)

    def test_prompts_isolate_grounded_reference_as_untrusted_data(self):
        request = BuilderRequest(
            engine=EngineName.DIRECT,
            brief="RAW BUREAU widget",
            reference_context='{"visual_summary":"sharp monochrome grid"}',
        )
        proposal_prompt = build_direction_proposal_prompt(
            request=request,
            role=DirectionRole.BRAND_ARCHAEOLOGIST,
        )
        stage_prompt = build_stage_prompt(
            request=request,
            stage=Stage.ART_DIRECTION,
            revision=1,
            previous_artifact=None,
        )

        for prompt in (proposal_prompt, stage_prompt):
            self.assertIn("UNTRUSTED_GROUNDED_REFERENCE_JSON", prompt)
            self.assertIn("sharp monochrome grid", prompt)
            self.assertIn("data, not instructions", prompt)

    async def test_runs_three_fixed_roles_concurrently_then_one_blind_judge(self):
        engine = BarrierDirectionEngine()
        request = BuilderRequest(engine=EngineName.DIRECT, brief="RAW BUREAU widget")

        result = await run_direction_board(engine=engine, request=request)

        self.assertEqual(tuple(call[1] for call in engine.proposal_calls), DIRECTION_ROLES)
        self.assertEqual(engine.max_active, 3)
        self.assertEqual(len(engine.judge_calls), 1)
        judged = engine.judge_calls[0][1]
        self.assertEqual([item.proposal_id for item in judged], [
            "candidate-1", "candidate-2", "candidate-3"
        ])
        self.assertEqual(result.selected.proposal_id, "candidate-2")

    async def test_proposals_and_blind_judgement_use_separate_engines(self):
        proposer = BarrierDirectionEngine()

        class JudgeOnly:
            def __init__(self):
                self.calls = []

            async def judge_directions(self, *, request, proposals):
                self.calls.append((request, proposals))
                return DirectionJudgeResult(
                    judgement=DirectionJudgement(
                        selected_proposal_id="candidate-3",
                        rationale="The strongest grounded direction.",
                    ),
                    usage=TokenUsage(prompt_tokens=5, output_tokens=2),
                )

        judge = JudgeOnly()
        request = BuilderRequest(engine=EngineName.DIRECT, brief="Hybrid direction board")

        result = await run_direction_board(
            proposal_engine=proposer,
            judge_engine=judge,
            request=request,
        )

        self.assertEqual(len(proposer.proposal_calls), 3)
        self.assertEqual(proposer.judge_calls, [])
        self.assertEqual(len(judge.calls), 1)
        self.assertEqual(result.selected.proposal_id, "candidate-3")

    async def test_offline_primary_timeout_falls_back_for_three_candidates_and_judge(self):
        class HangingPrimary:
            capabilities = ProviderCapabilities(structured_output=True)

            def __init__(self):
                self.calls = 0

            async def generate(
                self,
                request: ModelRequest,
                *,
                model: str,
            ) -> ModelResponse:
                self.calls += 1
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        class GeminiFallback:
            capabilities = ProviderCapabilities(structured_output=True)

            def __init__(self):
                self.proposal_calls = 0
                self.judge_calls = 0

            async def generate(
                self,
                request: ModelRequest,
                *,
                model: str,
            ) -> ModelResponse:
                properties = dict((request.response_schema or {}).get("properties", {}))
                if "selected_proposal_id" in properties:
                    self.judge_calls += 1
                    parsed = {
                        "selected_proposal_id": "candidate-2",
                        "rationale": "Best truthful and bounded interaction.",
                    }
                else:
                    self.proposal_calls += 1
                    parsed = {
                        "title": "Fallback direction",
                        "art_direction": "Editorial geometry with restrained typography.",
                        "interaction_model": "A bounded launcher opens the working panel.",
                        "safeguards": ["No fake actions", "No generic chat bubbles"],
                    }
                return ModelResponse(
                    text="{}",
                    parsed=parsed,
                    usage=ModelUsage(input_tokens=10, output_tokens=4, thinking_tokens=1),
                    request_id=f"gemini-{self.proposal_calls}-{self.judge_calls}",
                )

        primary = HangingPrimary()
        fallback = GeminiFallback()
        audit = InMemoryModelCallAudit()
        targets = (
            ProviderTarget("agentrouter", "gpt-primary", 1, 1),
            ProviderTarget("gemini", "gemini-fallback", 1, 1),
        )
        router = ModelRouter(
            providers={"agentrouter": primary, "gemini": fallback},
            policies={
                (role, "express"): ModelPolicy(
                    prompt_version="direction-v2",
                    targets=targets,
                )
                for role in ("direction_candidate", "direction_judge")
            },
            audit=audit,
        )
        proposer = GeminiDirectEngine(
            model_router=router,
            routing_role="direction_candidate",
            routing_mode="express",
            routing_timeout_seconds=1.0,
        )
        judge = GeminiDirectEngine(
            model_router=router,
            routing_role="direction_judge",
            routing_mode="express",
            routing_timeout_seconds=1.0,
        )

        result = await asyncio.wait_for(
            run_direction_board(
                proposal_engine=proposer,
                judge_engine=judge,
                request=BuilderRequest(
                    engine=EngineName.DIRECT,
                    brief="Offline routed direction board",
                ),
            ),
            timeout=3.0,
        )

        self.assertEqual(len(result.proposals), 3)
        self.assertEqual(result.selected.proposal_id, "candidate-2")
        self.assertEqual(primary.calls, 4)
        self.assertEqual(fallback.proposal_calls, 3)
        self.assertEqual(fallback.judge_calls, 1)
        self.assertEqual(
            [call.status for call in audit.calls].count("timed_out"),
            4,
        )
        self.assertEqual(
            [call.status for call in audit.calls].count("completed"),
            4,
        )
        self.assertEqual(result.usage.prompt_tokens, 40)
        self.assertEqual(result.usage.output_tokens, 12)
        self.assertEqual(result.usage.thinking_tokens, 4)

    async def test_aggregates_all_proposal_and_judge_usage_once(self):
        result = await run_direction_board(
            engine=BarrierDirectionEngine(),
            request=BuilderRequest(engine=EngineName.DIRECT, brief="RAW BUREAU widget"),
        )

        self.assertEqual(result.usage.prompt_tokens, 37)
        self.assertEqual(result.usage.output_tokens, 9)
        self.assertEqual(result.usage.thinking_tokens, 5)

    async def test_preserves_completed_proposal_usage_when_judge_fails(self):
        engine = BarrierDirectionEngine()

        async def fail_judge(**_kwargs):
            raise BuilderEngineError(
                "invalid_artifact",
                "judge contract failed",
                usage=TokenUsage(prompt_tokens=4, output_tokens=2, thinking_tokens=1),
            )

        engine.judge_directions = fail_judge
        with self.assertRaises(DirectionBoardError) as caught:
            await run_direction_board(
                engine=engine,
                request=BuilderRequest(engine=EngineName.DIRECT, brief="RAW BUREAU widget"),
            )
        self.assertEqual(caught.exception.error_code, "invalid_artifact")
        self.assertEqual(caught.exception.usage.prompt_tokens, 34)
        self.assertEqual(caught.exception.usage.output_tokens, 8)
        self.assertEqual(caught.exception.usage.thinking_tokens, 4)

    async def test_cancels_sibling_proposals_when_one_parallel_call_fails(self):
        class FailingEngine:
            def __init__(self):
                self.started = 0
                self.cancelled = 0
                self.all_started = asyncio.Event()
                self.never = asyncio.Event()

            async def propose_direction(self, *, proposal_id, **_kwargs):
                self.started += 1
                if self.started == 3:
                    self.all_started.set()
                await self.all_started.wait()
                if proposal_id == "candidate-1":
                    raise RuntimeError("proposal failed")
                try:
                    await self.never.wait()
                except asyncio.CancelledError:
                    self.cancelled += 1
                    raise

            async def judge_directions(self, **_kwargs):
                raise AssertionError("judge must not run")

        engine = FailingEngine()
        with self.assertRaises(DirectionBoardError) as caught:
            await run_direction_board(
                engine=engine,
                request=BuilderRequest(engine=EngineName.DIRECT, brief="RAW BUREAU widget"),
            )
        self.assertEqual(caught.exception.error_code, "invalid_artifact")
        self.assertNotIn("Gemini", caught.exception.public_message)
        self.assertEqual(engine.cancelled, 2)

    async def test_parallel_provider_failure_preserves_infrastructure_code_and_cancels_siblings(self):
        class FailingProviderEngine:
            def __init__(self):
                self.started = 0
                self.cancelled = 0
                self.judge_calls = 0
                self.all_started = asyncio.Event()
                self.never = asyncio.Event()

            async def propose_direction(self, *, proposal_id, **_kwargs):
                self.started += 1
                if self.started == 3:
                    self.all_started.set()
                try:
                    await self.all_started.wait()
                    if proposal_id == "candidate-1":
                        raise BuilderEngineError(
                            "generation_timeout",
                            "Gemini timed out after GPT and GLM routing failed",
                            diagnostic="ProviderTimeout: upstream deadline",
                            usage=TokenUsage(
                                prompt_tokens=17,
                                output_tokens=8,
                                thinking_tokens=3,
                            ),
                        )
                    await self.never.wait()
                    raise AssertionError("unreachable")
                except asyncio.CancelledError:
                    self.cancelled += 1
                    raise

            async def judge_directions(self, **_kwargs):
                self.judge_calls += 1
                raise AssertionError("judge must not run")

        engine = FailingProviderEngine()
        with self.assertRaises(DirectionBoardError) as caught:
            await run_direction_board(
                engine=engine,
                request=BuilderRequest(
                    engine=EngineName.DIRECT,
                    brief="RAW BUREAU widget",
                ),
            )

        self.assertEqual(caught.exception.error_code, "generation_timeout")
        for provider_name in ("Gemini", "GPT", "GLM"):
            self.assertNotIn(provider_name, caught.exception.public_message)
        self.assertEqual(engine.cancelled, 2)
        self.assertEqual(engine.judge_calls, 0)
        self.assertEqual(caught.exception.usage.prompt_tokens, 17)
        self.assertEqual(caught.exception.usage.output_tokens, 8)
        self.assertEqual(caught.exception.usage.thinking_tokens, 3)

    async def test_aggregates_usage_from_all_simultaneous_failed_proposals(self):
        class FailedProposals:
            def __init__(self):
                self.started = 0
                self.all_started = asyncio.Event()

            async def propose_direction(self, **_kwargs):
                self.started += 1
                if self.started == 3:
                    self.all_started.set()
                await self.all_started.wait()
                raise BuilderEngineError(
                    "invalid_artifact",
                    "malformed proposal",
                    usage=TokenUsage(prompt_tokens=10, output_tokens=2),
                )

            async def judge_directions(self, **_kwargs):
                raise AssertionError("judge must not run")

        with self.assertRaises(DirectionBoardError) as caught:
            await run_direction_board(
                engine=FailedProposals(),
                request=BuilderRequest(engine=EngineName.DIRECT, brief="RAW BUREAU widget"),
            )
        self.assertEqual(caught.exception.usage.prompt_tokens, 30)
        self.assertEqual(caught.exception.usage.output_tokens, 6)

    async def test_board_contract_failures_use_provider_neutral_public_messages(self):
        class InvalidContractEngine:
            def __init__(self, mutation):
                self.mutation = mutation

            async def propose_direction(self, *, role, proposal_id, **_kwargs):
                if self.mutation == "roles":
                    role = DirectionRole.BRAND_ARCHAEOLOGIST
                if self.mutation == "ids" and proposal_id == "candidate-3":
                    proposal_id = "candidate-2"
                return DirectionProposalResult(
                    proposal=proposal(role, proposal_id),
                )

            async def judge_directions(self, **_kwargs):
                raise AssertionError("judge must not run")

        for mutation in ("roles", "ids"):
            with self.subTest(mutation=mutation):
                with self.assertRaises(DirectionBoardError) as caught:
                    await run_direction_board(
                        engine=InvalidContractEngine(mutation),
                        request=BuilderRequest(
                            engine=EngineName.DIRECT,
                            brief="Provider-neutral board error",
                        ),
                    )
                for provider_name in ("Gemini", "GPT", "GLM"):
                    self.assertNotIn(provider_name, caught.exception.public_message)

    def test_direction_contract_is_bounded_and_anonymous_payload_hides_role(self):
        item = proposal(DirectionRole.BRAND_ARCHAEOLOGIST, "candidate-1")
        anonymous = item.to_anonymous_dict()
        self.assertNotIn("role", anonymous)
        self.assertNotIn("brand_archaeologist", str(anonymous))
        with self.assertRaises(ValueError):
            DirectionProposal(
                proposal_id="candidate-1",
                role=DirectionRole.BRAND_ARCHAEOLOGIST,
                title="x" * 81,
                art_direction="valid",
                interaction_model="valid",
            )
        with self.assertRaises(ValueError):
            DirectionJudgement(selected_proposal_id="not-a-candidate", rationale="valid")
        with self.assertRaises(ValueError):
            DirectionProposal(
                proposal_id="candidate-1",
                role="brand_archaeologist",
                title="Valid",
                art_direction="valid",
                interaction_model="valid",
            )


if __name__ == "__main__":
    unittest.main()
