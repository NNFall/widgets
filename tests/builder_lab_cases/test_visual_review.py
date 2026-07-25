import pytest

from builder_lab.models import TokenUsage
from builder_lab.visual_critic import VisualCriticResult, VisualCriticRole
from builder_lab.visual_models import (
    NormalizedRegion,
    VisualCategory,
    VisualCritique,
    VisualFinding,
    VisualSeverity,
    VisualVerdict,
)
from builder_lab.visual_review import VisualJudgeError, validate_visual_judgement
from builder_lab.visual_review import (
    RepairVerificationError,
    validate_repair_verification,
)


def finding(finding_id, evidence):
    return VisualFinding(
        finding_id=finding_id,
        severity=VisualSeverity.MAJOR,
        category=VisualCategory.RESPONSIVE_INTEGRITY,
        screenshot_id="mobile.after_turn_2",
        evidence=evidence,
        region=NormalizedRegion(
            x=0.68,
            y=0.79,
            width=0.26,
            height=0.12,
            semantic_region="composer",
        ),
        artifact_fields=("css",),
        repair_instruction="Keep the composer fully inside the panel.",
        confidence=0.93,
    )


def result(item):
    return VisualCriticResult(
        critique=VisualCritique(
            verdict=VisualVerdict.REPAIR,
            summary="Strict review completed.",
            findings=(item,),
        ),
        observations=(),
        pixel_proof=None,
        usage=TokenUsage(),
    )


def role_results():
    return {
        VisualCriticRole.CONVERSATION_UX: result(
            finding("ux-edge", "The send control crosses the panel edge.")
        ),
        VisualCriticRole.BRAND_MOTION: result(
            finding(
                "brand-overflow",
                "On mobile the composer action is visibly clipped on the right.",
            )
        ),
        VisualCriticRole.ADVERSARIAL_CUSTOMER: VisualCriticResult(
            critique=VisualCritique(
                verdict=VisualVerdict.PASS,
                summary="No additional blocking issue.",
            ),
            observations=(),
            pixel_proof=None,
            usage=TokenUsage(),
        ),
    }


def judgement_payload(sources):
    return {
        "verdict": "repair",
        "summary": "Two critics independently found the same mobile composer defect.",
        "findings": [
            {
                "finding_id": "judge-1",
                "severity": "major",
                "category": "responsive_integrity",
                "screenshot_id": "mobile.after_turn_2",
                "evidence": "The mobile composer send control is clipped by the panel edge.",
                "region": {
                    "x": 0.68,
                    "y": 0.79,
                    "width": 0.26,
                    "height": 0.12,
                    "semantic_region": "composer",
                },
                "artifact_fields": ["css"],
                "repair_instruction": "Keep the composer fully inside the mobile panel.",
                "confidence": 0.94,
                "sources": sources,
            }
        ],
    }


def test_judge_accepts_semantic_consensus_via_two_source_references():
    judged = validate_visual_judgement(
        judgement_payload(
            [
                {"role": "conversation_ux", "finding_id": "ux-edge"},
                {"role": "brand_motion", "finding_id": "brand-overflow"},
            ]
        ),
        role_results(),
    )

    assert judged.critique.verdict is VisualVerdict.REPAIR
    assert judged.supporting_roles["judge-1"] == (
        VisualCriticRole.CONVERSATION_UX,
        VisualCriticRole.BRAND_MOTION,
    )


def test_judge_rejects_even_a_blocker_when_only_one_role_supports_it():
    payload = judgement_payload(
        [{"role": "conversation_ux", "finding_id": "ux-edge"}]
    )
    payload["findings"][0]["severity"] = "blocker"

    with pytest.raises(VisualJudgeError) as caught:
        validate_visual_judgement(payload, role_results())
    assert caught.value.diagnostic == "two distinct critic roles are required"


def test_judge_rejects_fabricated_source_ids():
    with pytest.raises(VisualJudgeError) as caught:
        validate_visual_judgement(
            judgement_payload(
                [
                    {"role": "conversation_ux", "finding_id": "missing"},
                    {"role": "brand_motion", "finding_id": "brand-overflow"},
                ]
            ),
            role_results(),
        )
    assert "unknown critic finding" in caught.value.diagnostic


def test_repair_verifier_requires_one_explicit_check_per_judge_finding():
    original = finding("judge-1", "The mobile composer is clipped.")
    verified = validate_repair_verification(
        {
            "summary": "The requested mobile correction is present.",
            "checks": [
                {
                    "finding_id": "judge-1",
                    "status": "fixed",
                    "evidence": "The composer now stays within the mobile panel.",
                }
            ],
        },
        (original,),
    )

    assert verified.checks["judge-1"].status == "fixed"


def test_repair_verifier_rejects_missing_or_fabricated_finding_ids():
    original = finding("judge-1", "The mobile composer is clipped.")

    with pytest.raises(RepairVerificationError):
        validate_repair_verification(
            {
                "summary": "Incorrect result.",
                "checks": [
                    {
                        "finding_id": "invented",
                        "status": "fixed",
                        "evidence": "No source.",
                    }
                ],
            },
            (original,),
        )
