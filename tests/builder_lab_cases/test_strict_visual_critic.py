from builder_lab.strict_visual_critic import (
    STRICT_VISUAL_CRITIC_SCHEMA,
    build_strict_visual_critic_prompt,
)


def test_russian_prompt_is_adversarial_evidence_bound_and_has_two_inspection_passes():
    prompt = build_strict_visual_critic_prompt(locale="ru", phase="raw")
    lowered = prompt.casefold()
    assert "не пишешь дружеский feedback" in lowered
    assert "не выдумывай дефект" in lowered
    assert "двухсекунд" in lowered
    assert "микродетал" in lowered
    assert "не возвращай verdict" in lowered
    assert "выглядит чисто" in lowered
    assert "можно улучшить иерархию" in lowered
    assert "добавить воздуха" in lowered
    assert "raw" in lowered


def test_english_prompt_has_the_same_adversarial_guards():
    prompt = build_strict_visual_critic_prompt(locale="en", phase="final").casefold()
    assert "not friendly feedback" in prompt
    assert "do not invent a defect" in prompt
    assert "two-second" in prompt
    assert "micro-detail" in prompt
    assert "do not return verdict" in prompt
    assert "final" in prompt


def test_provider_schema_has_no_model_owned_verdict_and_exact_dimensions():
    assert "verdict" not in STRICT_VISUAL_CRITIC_SCHEMA["properties"]
    dimensions = STRICT_VISUAL_CRITIC_SCHEMA["properties"]["assessments"]["items"][
        "properties"
    ]["dimension"]["enum"]
    assert dimensions == [
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
    ]
    assert (
        STRICT_VISUAL_CRITIC_SCHEMA["properties"]["revision_actions"]["maxItems"]
        == 3
    )
