from builder_lab.models import BuilderRequest, EngineName, Stage, WidgetArtifact
from builder_lab.prompts import build_stage_prompt
from builder_lab.visual_models import (
    NormalizedRegion,
    VisualCategory,
    VisualFinding,
    VisualSeverity,
)


def _request() -> BuilderRequest:
    return BuilderRequest(
        engine=EngineName.DIRECT,
        brief="Сохрани компактный брендовый чат.",
    )


def _artifact() -> WidgetArtifact:
    return WidgetArtifact(
        schema_version="1.0",
        revision=5,
        stage=Stage.MOTION_POLISH,
        art_direction="Editorial monochrome.",
        body_html='<section class="kaigo-widget"></section>',
        css=".kaigo-widget { color: #111; }",
        theme_tokens={"ink": "#111"},
        suggested_actions=("Уточнить задачу",),
        change_summary="Добавлена исходная композиция.",
        javascript="",
        layout_contract={"mobile_height": "70vh"},
    )


def _finding(
    finding_id: str,
    *artifact_fields: str,
) -> VisualFinding:
    return VisualFinding(
        finding_id=finding_id,
        severity=VisualSeverity.MAJOR,
        category=VisualCategory.RESPONSIVE_INTEGRITY,
        screenshot_id="mobile.open_initial",
        evidence="A visible defect is bounded to the cited region.",
        region=NormalizedRegion(
            x=0.1,
            y=0.1,
            width=0.5,
            height=0.5,
            semantic_region="panel",
        ),
        artifact_fields=artifact_fields,
        repair_instruction="Repair only the cited visible defect.",
        confidence=0.95,
    )


def test_visual_repair_prompt_lists_union_of_allowed_and_locked_fields():
    prompt = build_stage_prompt(
        request=_request(),
        stage=Stage.MOTION_POLISH,
        revision=5,
        previous_artifact=_artifact(),
        visual_findings=(
            _finding("contrast", "css", "theme_tokens"),
            _finding("mobile-fit", "css", "layout_contract"),
        ),
    )

    assert (
        'VISUAL_REPAIR_ALLOWED_FIELDS_JSON: ["change_summary","css","layout_contract","theme_tokens"]'
        in prompt
    )
    assert "VISUAL_REPAIR_LOCKED_FIELDS_JSON:" in prompt
    assert '"body_html"' in prompt
    assert "byte-identical" in prompt
    assert "без CSS-селекторов, HTML-тегов, имён полей и кодов цветов" in prompt


def test_visual_repair_always_allows_plain_language_change_summary():
    prompt = build_stage_prompt(
        request=_request(),
        stage=Stage.MOTION_POLISH,
        revision=5,
        previous_artifact=_artifact(),
        visual_findings=(
            _finding("summary-copy", "css"),
        ),
    )

    assert 'VISUAL_REPAIR_ALLOWED_FIELDS_JSON: ["change_summary","css"]' in prompt
    locked_line = next(
        line
        for line in prompt.splitlines()
        if line.startswith("VISUAL_REPAIR_LOCKED_FIELDS_JSON:")
    )
    assert '"change_summary"' not in locked_line


def test_stage_prompt_exposes_exact_html_element_and_attribute_allowlists():
    prompt = build_stage_prompt(
        request=_request(),
        stage=Stage.IDENTITY,
        revision=6,
        previous_artifact=_artifact(),
    )

    assert "HTML_ALLOWED_ELEMENTS_JSON:" in prompt
    assert '"button"' in prompt
    assert '"svg"' in prompt
    assert "HTML_ALLOWED_ATTRIBUTES_JSON:" in prompt
    assert '"aria-*"' in prompt
    assert '"data-region"' in prompt
    assert '"data-action"' in prompt
    assert "foreignObject, use, mask, filter, style, form" in prompt
    assert "xmlns, focusable, event attributes and arbitrary data-*" in prompt


def test_stage_prompt_exposes_numeric_mobile_safe_inset_contract():
    prompt = build_stage_prompt(
        request=_request(),
        stage=Stage.FOUNDATION,
        revision=2,
        previous_artifact=_artifact(),
    )

    assert "не меньше 8px с каждой стороны" in prompt
    assert "calc(100vw - 16px)" in prompt
    assert "390x844" in prompt
