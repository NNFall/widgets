import pytest

from builder_lab.strict_visual_models import (
    CORE_VISUAL_DIMENSIONS,
    STRICT_VISUAL_DIMENSIONS,
    StrictVisualAssessment,
    StrictVisualCritique,
    StrictVisualDimension,
    StrictVisualFinding,
    StrictVisualObservation,
    StrictVisualRevisionAction,
    StrictVisualSeverity,
    StrictVisualVerdict,
)
from builder_lab.visual_models import NormalizedRegion


def finding(
    finding_id: str = "conversation-flat",
    *,
    dimension: StrictVisualDimension = StrictVisualDimension.CONVERSATION_CLARITY,
) -> StrictVisualFinding:
    return StrictVisualFinding(
        finding_id=finding_id,
        dimension=dimension,
        screenshot_id="desktop.after_turn_2",
        evidence=(
            "In desktop.after_turn_2 the two message rows share the same pale "
            "rectangle from x=0.58 to x=0.79, so authorship is not scannable."
        ),
        region=NormalizedRegion(
            x=0.58,
            y=0.38,
            width=0.21,
            height=0.18,
            semantic_region="messages",
        ),
        confidence=0.94,
    )


def action(
    finding_id: str = "conversation-flat",
) -> StrictVisualRevisionAction:
    return StrictVisualRevisionAction(
        action_id="separate-authors",
        finding_ids=(finding_id,),
        artifact_fields=("css",),
        instruction=(
            "Give user and assistant message surfaces visibly different alignment "
            "and tone while preserving the selected direction."
        ),
    )


def assessments(
    *,
    default: int = 5,
    overrides: dict[StrictVisualDimension, int] | None = None,
) -> tuple[StrictVisualAssessment, ...]:
    overrides = overrides or {}
    values = []
    for dimension in STRICT_VISUAL_DIMENSIONS:
        score = overrides.get(dimension, default)
        linked = ("conversation-flat",) if score in {1, 2, 3} else ()
        values.append(
            StrictVisualAssessment(
                dimension=dimension,
                score=score,
                confidence=0.9,
                finding_ids=linked,
            )
        )
    return tuple(values)


def critique(
    values: tuple[StrictVisualAssessment, ...],
) -> StrictVisualCritique:
    linked = any(item.finding_ids for item in values)
    return StrictVisualCritique(
        observations=(
            StrictVisualObservation(
                screenshot_id="desktop.after_turn_2",
                observation=(
                    "The panel occupies the lower-right corner and shows two bounded "
                    "message rows above a one-line composer."
                ),
            ),
        ),
        assessments=values,
        findings=(finding(),) if linked else (),
        revision_actions=(action(),) if linked else (),
        summary="Evidence-bound strict visual inspection.",
    )


def test_rubric_exposes_exactly_ten_dimensions():
    assert tuple(item.value for item in STRICT_VISUAL_DIMENSIONS) == (
        "direction_fidelity",
        "page_subordination",
        "visual_hierarchy",
        "conversation_clarity",
        "typography_legibility",
        "spacing_alignment",
        "system_coherence",
        "responsive_composition",
        "craft_polish",
        "distinctiveness",
    )
    assert StrictVisualDimension.CONVERSATION_CLARITY in CORE_VISUAL_DIMENSIONS


def test_host_rejects_score_below_release_bar():
    values = assessments(default=3)
    result = critique(values)
    assert result.weighted_score == 3
    assert result.verdict is StrictVisualVerdict.REPAIR


def test_core_dimension_below_four_blocks_even_high_average():
    values = assessments(
        default=5,
        overrides={StrictVisualDimension.CONVERSATION_CLARITY: 3},
    )
    result = critique(values)
    assert result.weighted_score > 4
    assert result.verdict is StrictVisualVerdict.REPAIR


def test_any_observable_score_two_blocks_release():
    values = assessments(
        default=5,
        overrides={StrictVisualDimension.CRAFT_POLISH: 2},
    )
    assert critique(values).verdict is StrictVisualVerdict.REPAIR


def test_all_release_ready_scores_pass():
    assert critique(assessments(default=4)).verdict is StrictVisualVerdict.PASS


def test_zero_is_not_observable_and_does_not_invent_a_defect():
    item = StrictVisualAssessment(
        dimension=StrictVisualDimension.DISTINCTIVENESS,
        score=0,
        confidence=0,
        finding_ids=(),
    )
    assert item.not_observable
    assert item.severity is None


@pytest.mark.parametrize("score", [1, 2, 3])
def test_low_score_requires_a_concrete_linked_finding(score):
    with pytest.raises(ValueError, match="linked finding"):
        StrictVisualAssessment(
            dimension=StrictVisualDimension.CRAFT_POLISH,
            score=score,
            confidence=0.9,
            finding_ids=(),
        )


def test_observable_assessment_requires_high_confidence():
    with pytest.raises(ValueError, match="at least 0.80"):
        StrictVisualAssessment(
            dimension=StrictVisualDimension.CRAFT_POLISH,
            score=4,
            confidence=0.79,
        )


def test_severity_is_derived_from_score():
    assert (
        StrictVisualAssessment(
            dimension=StrictVisualDimension.CRAFT_POLISH,
            score=1,
            confidence=0.9,
            finding_ids=("f",),
        ).severity
        is StrictVisualSeverity.BLOCKER
    )
    assert (
        StrictVisualAssessment(
            dimension=StrictVisualDimension.CRAFT_POLISH,
            score=2,
            confidence=0.9,
            finding_ids=("f",),
        ).severity
        is StrictVisualSeverity.MAJOR
    )
    assert (
        StrictVisualAssessment(
            dimension=StrictVisualDimension.CRAFT_POLISH,
            score=3,
            confidence=0.9,
            finding_ids=("f",),
        ).severity
        is StrictVisualSeverity.MINOR
    )


def test_critique_requires_every_dimension_once_and_real_links():
    with pytest.raises(ValueError, match="exactly ten"):
        critique(assessments()[:-1])
    values = assessments(
        overrides={StrictVisualDimension.CONVERSATION_CLARITY: 3},
    )
    with pytest.raises(ValueError, match="unknown finding"):
        StrictVisualCritique(
            observations=(
                StrictVisualObservation(
                    screenshot_id="desktop.after_turn_2",
                    observation="Two visually identical message surfaces sit above composer.",
                ),
            ),
            assessments=values,
            findings=(),
            revision_actions=(),
            summary="Strict inspection.",
        )


def test_model_payload_must_not_supply_its_own_verdict():
    payload = critique(assessments(default=4)).to_model_dict()
    payload["verdict"] = "pass"
    with pytest.raises(ValueError, match="must not return verdict"):
        StrictVisualCritique.from_model_dict(payload)
