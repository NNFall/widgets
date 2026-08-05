from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from builder_lab.patterns.atomic_models import (
    AdaptationPolicy,
    AtomicPatternStatus,
)
from builder_lab.patterns.atomic_registry import (
    AtomicPatternRegistry,
    AtomicPatternRegistryError,
    load_builtin_atomic_registry,
)
from builder_lab.patterns.models import PatternCategory
from builder_lab.patterns.registry import PatternRegistry


ATOMIC_FIELDS = {
    "schema_version",
    "pattern_id",
    "version",
    "category",
    "status",
    "title",
    "summary",
    "ai_description",
    "technical_contract",
    "adaptation_policy",
    "incompatible_with",
    "implementation_sha256",
    "provenance",
}

EXPECTED_ATOMIC_CATEGORIES = {
    "launcher_shape",
    "launcher_idle",
    "launcher_attention",
    "shell_layout",
    "widget_open",
    "widget_close",
    "background_effect",
    "assistant_message_enter",
    "user_message_enter",
    "typing_indicator",
    "message_send",
    "composer_focus",
    "control_hover",
    "responsive_transition",
}


def implementation_hash(*assets: bytes) -> str:
    digest = hashlib.sha256()
    for name, asset in zip(
        ("fragment.html", "styles.css", "behavior.js"), assets, strict=True
    ):
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(2, "big"))
        digest.update(encoded_name)
        digest.update(len(asset).to_bytes(8, "big"))
        digest.update(asset)
    return digest.hexdigest()


def write_atomic_pattern(
    directory: Path,
    *,
    pattern_id: str = "widget-open-technical",
    version: int = 1,
    category: str = "widget_open",
    status: str = "active",
    ai_description: str | None = None,
    behavior: str | None = "",
    implementation_sha256: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    html = f'<div class="kaigo-atomic-{category}" data-state="closed">fixture</div>\n'
    css = f".kaigo-atomic-{category} {{ display: block; }}\n"
    js = "" if behavior is None else behavior
    description = ai_description or (
        "A neutral technical reference for this atomic interaction. "
        "It names the runtime state, keeps the DOM contract small, and leaves "
        "visual styling open for adaptation to the approved product direction."
    )
    manifest: dict[str, object] = {
        "schema_version": 3,
        "pattern_id": pattern_id,
        "version": version,
        "category": category,
        "status": status,
        "title": f"{category.replace('_', ' ').title()} technical fixture",
        "summary": "Neutral technical fixture for contract tests.",
        "ai_description": description,
        "technical_contract": "chat-runtime-atomic-v1",
        "adaptation_policy": "adaptive",
        "incompatible_with": [],
        "provenance": {"origin": "kaigo-owned", "review_state": "approved"},
    }
    (directory / "fragment.html").write_text(html, encoding="utf-8")
    (directory / "styles.css").write_text(css, encoding="utf-8")
    if behavior is not None:
        (directory / "behavior.js").write_text(js, encoding="utf-8")
    assets = tuple(
        (directory / name).read_bytes() if (directory / name).exists() else b""
        for name in ("fragment.html", "styles.css", "behavior.js")
    )
    manifest["implementation_sha256"] = implementation_sha256 or implementation_hash(
        *assets
    )
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return directory


def valid_atomic_catalog(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    write_atomic_pattern(root / "widget-open-technical-v1")
    return root


def rewrite_raw_asset_hash(directory: Path) -> None:
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assets = tuple(
        (directory / name).read_bytes() if (directory / name).exists() else b""
        for name in ("fragment.html", "styles.css", "behavior.js")
    )
    manifest["implementation_sha256"] = implementation_hash(*assets)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def test_v3_registry_exposes_full_ai_description_without_assets() -> None:
    registry = load_builtin_atomic_registry()
    definition = registry.resolve("widget-open-technical", 1)

    public = definition.selector_dict()

    assert public["ai_description"] == definition.ai_description
    assert "html" not in public
    assert "css" not in public
    assert "javascript" not in public
    assert public["technical_contract"] == definition.technical_contract
    assert public["adaptation_policy"] == definition.adaptation_policy.value
    assert public["provenance"] == dict(definition.provenance)


def test_v3_implementation_dict_returns_exact_assets() -> None:
    definition = load_builtin_atomic_registry().resolve("widget-open-technical", 1)

    assert definition.implementation_dict() == {
        "html": definition.html,
        "css": definition.css,
        "javascript": definition.javascript,
    }


def test_builtin_registry_contains_one_active_approved_fixture_per_category() -> None:
    registry = load_builtin_atomic_registry()

    assert {item.category.value for item in registry.definitions} == EXPECTED_ATOMIC_CATEGORIES
    assert len(registry.definitions) == len(EXPECTED_ATOMIC_CATEGORIES)
    assert all(item.status is AtomicPatternStatus.ACTIVE for item in registry.definitions)
    assert all(item.provenance["review_state"] == "approved" for item in registry.definitions)
    assert all(item.adaptation_policy in set(AdaptationPolicy) for item in registry.definitions)


def test_v3_registry_rejects_hash_drift(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    (root / "widget-open-technical-v1" / "styles.css").write_text(
        "changed", encoding="utf-8"
    )

    with pytest.raises(AtomicPatternRegistryError, match="implementation hash"):
        AtomicPatternRegistry.load(root)


@pytest.mark.parametrize(
    "description",
    ["short", "x" * 79, "x" * 4001, "valid text" + "\x00" + "x" * 90],
)
def test_v3_registry_rejects_invalid_ai_description(
    tmp_path: Path,
    description: str,
) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(
        root / "widget-open-technical-v1", ai_description=description
    )

    with pytest.raises(AtomicPatternRegistryError, match="ai_description"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_unknown_files(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    (root / "widget-open-technical-v1" / "unexpected.txt").write_text(
        "not allowed", encoding="utf-8"
    )

    with pytest.raises(AtomicPatternRegistryError, match="unexpected"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_symlink_assets(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    target = tmp_path / "outside.css"
    target.write_text(".outside { display: block; }", encoding="utf-8")
    link = pattern / "behavior.js"
    link.unlink()
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are unavailable in this environment: {exc}")

    with pytest.raises(AtomicPatternRegistryError, match="symlink"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_forbidden_markers(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    (root / "widget-open-technical-v1" / "behavior.js").write_text(
        "fetch('https://example.invalid')", encoding="utf-8"
    )

    with pytest.raises(AtomicPatternRegistryError, match="forbidden"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_network_beacon_capability(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "behavior.js").write_text(
        "navigator.sendBeacon('/telemetry')", encoding="utf-8"
    )
    rewrite_raw_asset_hash(pattern)

    with pytest.raises(AtomicPatternRegistryError, match="forbidden"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_document_cookie_storage(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "behavior.js").write_text("document.cookie", encoding="utf-8")
    rewrite_raw_asset_hash(pattern)

    with pytest.raises(AtomicPatternRegistryError, match="forbidden"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_function_constructor_eval_capability(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "behavior.js").write_text("Function('return 1')", encoding="utf-8")
    rewrite_raw_asset_hash(pattern)

    with pytest.raises(AtomicPatternRegistryError, match="forbidden"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_static_import_without_spacing(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "behavior.js").write_text(
        'import{load}from"module"', encoding="utf-8"
    )
    rewrite_raw_asset_hash(pattern)

    with pytest.raises(AtomicPatternRegistryError, match="forbidden"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_protocol_relative_url(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "styles.css").write_text(
        ".fixture { background-image: url(//cdn.invalid/pixel); }",
        encoding="utf-8",
    )
    rewrite_raw_asset_hash(pattern)

    with pytest.raises(AtomicPatternRegistryError, match="forbidden"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_comment_spaced_fetch_call(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "behavior.js").write_text("fetch/*c*/('external')", encoding="utf-8")
    rewrite_raw_asset_hash(pattern)

    with pytest.raises(AtomicPatternRegistryError, match="forbidden"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_comment_spaced_cookie_member(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "behavior.js").write_text(
        "document/*c*/.cookie", encoding="utf-8"
    )
    rewrite_raw_asset_hash(pattern)

    with pytest.raises(AtomicPatternRegistryError, match="forbidden"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_computed_fetch_member(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "behavior.js").write_text(
        'globalThis["fetch"]("external")', encoding="utf-8"
    )
    rewrite_raw_asset_hash(pattern)

    with pytest.raises(AtomicPatternRegistryError, match="forbidden"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_rejects_computed_cache_member(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "behavior.js").write_text(
        'globalThis["caches"]["open"]("key")', encoding="utf-8"
    )
    rewrite_raw_asset_hash(pattern)

    with pytest.raises(AtomicPatternRegistryError, match="forbidden"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_allows_safe_anonymous_callback(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "behavior.js").write_text("function() { return 1; }", encoding="utf-8")
    rewrite_raw_asset_hash(pattern)

    definition = AtomicPatternRegistry.load(root).resolve("widget-open-technical", 1)

    assert definition.javascript == "function() { return 1; }"


def test_v3_registry_ignores_local_state_line_comment(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "behavior.js").write_text(
        "//local-state\nconst value = 1;", encoding="utf-8"
    )
    rewrite_raw_asset_hash(pattern)

    assert AtomicPatternRegistry.load(root).resolve("widget-open-technical", 1)


def test_v3_registry_ignores_local_state_css_comment(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "styles.css").write_text(
        "/* //local-state */\n.fixture { color: black; }", encoding="utf-8"
    )
    rewrite_raw_asset_hash(pattern)

    assert AtomicPatternRegistry.load(root).resolve("widget-open-technical", 1)


def test_v3_registry_ignores_local_state_html_comment(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "fragment.html").write_text(
        "<!-- //local-state -->\n<div class=\"fixture\">ok</div>", encoding="utf-8"
    )
    rewrite_raw_asset_hash(pattern)

    assert AtomicPatternRegistry.load(root).resolve("widget-open-technical", 1)


def test_v3_registry_rejects_external_html_url(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    pattern = root / "widget-open-technical-v1"
    (pattern / "fragment.html").write_text(
        '<img src="//cdn.invalid/pixel" alt="external">', encoding="utf-8"
    )
    rewrite_raw_asset_hash(pattern)

    with pytest.raises(AtomicPatternRegistryError, match="forbidden"):
        AtomicPatternRegistry.load(root)


def test_v3_hash_uses_unambiguous_asset_framing(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for directory in (first, second):
        directory.mkdir()
    (first / "fragment.html").write_bytes(b"a")
    (first / "styles.css").write_bytes(b"bc")
    (first / "behavior.js").write_bytes(b"")
    (second / "fragment.html").write_bytes(b"ab")
    (second / "styles.css").write_bytes(b"c")
    (second / "behavior.js").write_bytes(b"")

    from builder_lab.patterns.atomic_registry import compute_implementation_hash

    assert compute_implementation_hash(first) != compute_implementation_hash(second)


def test_selector_catalog_requires_active_approved_definition(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(
        root / "approved", pattern_id="approved", status="active"
    )
    write_atomic_pattern(
        root / "ready", pattern_id="ready", status="active"
    )
    write_atomic_pattern(
        root / "rejected", pattern_id="rejected", status="active"
    )
    write_atomic_pattern(
        root / "deprecated", pattern_id="deprecated", status="deprecated"
    )
    write_atomic_pattern(root / "draft", pattern_id="draft", status="draft")
    for name, state in (("ready", "ready_for_review"), ("rejected", "rejected")):
        manifest_path = root / name / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["provenance"]["review_state"] = state
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    catalog = AtomicPatternRegistry.load(root).selector_catalog()

    assert [item["pattern_id"] for item in catalog] == ["approved"]


def test_selector_catalog_review_override_can_approve_or_reject(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(root / "manifest-ready", pattern_id="manifest-ready")
    write_atomic_pattern(root / "manifest-approved", pattern_id="manifest-approved")
    for name, state in (("manifest-ready", "ready_for_review"),):
        manifest_path = root / name / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["provenance"]["review_state"] = state
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    registry = AtomicPatternRegistry.load(root)
    catalog = registry.selector_catalog(
        effective_review_states={
            ("manifest-ready", 1): "approved",
            ("manifest-approved", 1): "rejected",
        }
    )

    assert [item["pattern_id"] for item in catalog] == ["manifest-ready"]


@pytest.mark.parametrize(
    "description",
    [
        "function render() { return '<div>' + value + '</div>'; } " * 3,
        "technical " * 60,
        '{"state": true, "items": [1, 2, 3], "nested": {"ok": false}} ' * 5,
    ],
)
def test_v3_registry_rejects_code_or_blob_shaped_ai_description(
    tmp_path: Path,
    description: str,
) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(root / "widget-open-technical-v1", ai_description=description)

    with pytest.raises(AtomicPatternRegistryError, match="ai_description"):
        AtomicPatternRegistry.load(root)


@pytest.mark.parametrize(
    "description",
    [
        "This neutral technical description explains the runtime state, stable DOM contract, and safe visual adaptation boundaries for the pattern.",
        "Это нейтральное техническое описание объясняет состояние runtime, контракт DOM и безопасные границы адаптации визуального эффекта паттерна.",
    ],
)
def test_v3_registry_accepts_english_and_russian_prose(
    tmp_path: Path,
    description: str,
) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(root / "widget-open-technical-v1", ai_description=description)

    definition = AtomicPatternRegistry.load(root).resolve("widget-open-technical", 1)

    assert definition.ai_description == description


@pytest.mark.parametrize(
    "description",
    [
        "Let the description explain how the function preserves the runtime contract while the interface adapts calmly across approved visual contexts.",
        "Описание объясняет состояние runtime и контракт оболочки; визуальная адаптация остаётся безопасной, ясной и согласованной с направлением продукта.",
    ],
)
def test_v3_registry_accepts_natural_prose_words_and_punctuation(
    tmp_path: Path,
    description: str,
) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(root / "widget-open-technical-v1", ai_description=description)

    definition = AtomicPatternRegistry.load(root).resolve("widget-open-technical", 1)

    assert definition.ai_description == description


@pytest.mark.parametrize(
    "blob",
    [
        "QWxhZGRpbjpvcGVuIHNlc2FtZQ" * 6,
        "deadbeefcafebabe" * 10,
    ],
)
def test_v3_registry_rejects_long_encoded_blob_tokens(
    tmp_path: Path,
    blob: str,
) -> None:
    root = tmp_path / "catalog"
    description = (
        "A neutral technical description explains runtime behavior and preserves "
        f"the contract token {blob} for a local preview reference."
    )
    write_atomic_pattern(root / "widget-open-technical-v1", ai_description=description)

    with pytest.raises(AtomicPatternRegistryError, match="ai_description"):
        AtomicPatternRegistry.load(root)


@pytest.mark.parametrize("mutation", ["remove", "extra"])
def test_v3_registry_rejects_real_manifest_field_mutations(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = valid_atomic_catalog(tmp_path)
    manifest_path = root / "widget-open-technical-v1" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "remove":
        del manifest["summary"]
    else:
        manifest["unexpected"] = "not part of schema v3"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(AtomicPatternRegistryError, match="manifest fields"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_allows_missing_optional_behavior(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(root / "widget-open-technical-v1", behavior=None)

    definition = AtomicPatternRegistry.load(root).resolve("widget-open-technical", 1)

    assert definition.javascript == ""


def test_v3_registry_rejects_duplicate_pattern_version(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(root / "a", pattern_id="same-pattern")
    write_atomic_pattern(root / "b", pattern_id="same-pattern")

    with pytest.raises(AtomicPatternRegistryError, match="duplicate"):
        AtomicPatternRegistry.load(root)


def test_v3_registry_order_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    write_atomic_pattern(root / "z-pattern-v1", pattern_id="z-pattern")
    write_atomic_pattern(root / "a-pattern-v1", pattern_id="a-pattern")

    first = AtomicPatternRegistry.load(root)
    second = AtomicPatternRegistry.load(root)

    assert first.selector_catalog() == second.selector_catalog()
    assert [item.pattern_id for item in first.definitions] == [
        "a-pattern",
        "z-pattern",
    ]


def test_v3_manifest_has_exact_top_level_field_set(tmp_path: Path) -> None:
    root = valid_atomic_catalog(tmp_path)
    manifest_path = root / "widget-open-technical-v1" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert set(manifest) == ATOMIC_FIELDS


def test_legacy_registry_remains_available_and_unchanged() -> None:
    registry = PatternRegistry.load(Path("builder_lab/patterns/catalog"))

    assert PatternCategory.LAUNCHER in set(item.category for item in registry.definitions)
    assert len(registry.definitions) == 30
