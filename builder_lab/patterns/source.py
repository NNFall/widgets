from __future__ import annotations

import hashlib
from collections import Counter
from html.parser import HTMLParser
import json
import math
from pathlib import Path
from string import Template
from dataclasses import dataclass
from typing import Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from ..models import Stage, WidgetArtifact
from .models import CompositionPlan, JSONValue, PatternCategory, PatternSelection
from .quality import validate_source_css, validate_source_fragment
from .registry import (
    PatternDefinition,
    PatternIntegrationMode,
    PatternRegistry,
    PatternRegistryError,
    PatternStatus,
)


_RUNTIME_CONTRACT_ID = "chat-v1"
_RUNTIME_CONTRACT_VERSION = 1
_COMPILER_REVISION = 1
_BASE_CSS_PATH = Path(__file__).with_name("runtime_source") / "base.css"
_CANONICAL_REGIONS = (
    "root",
    "launcher",
    "panel",
    "header",
    "messages",
    "suggestions",
    "composer",
)
_CANONICAL_ACTIONS = ("open", "close", "send")
_BODY_TEMPLATE = Template(
    """<section class="kaigo-widget $root_classes" data-region="root" data-state="closed" aria-label="Консультант">
  <button class="kaigo-widget__launcher" type="button" data-region="launcher" data-action="open" aria-label="Открыть консультанта" aria-expanded="false">
    $launcher_fragment
  </button>
  <section class="kaigo-widget__panel" data-region="panel" role="dialog" aria-label="Диалог с консультантом" aria-hidden="true">
    <div class="kaigo-widget__shell-decoration" aria-hidden="true">$shell_fragment</div>
    <header class="kaigo-widget__header" data-region="header">
      <div><strong>Консультант</strong><span>На связи</span></div>
      <button type="button" data-action="close" aria-label="Закрыть консультанта">×</button>
    </header>
    <main class="kaigo-widget__messages" data-region="messages" role="log" aria-live="polite">
      <article class="kaigo-widget__message kaigo-widget__message--assistant">
        <span class="kaigo-widget__message-decoration" aria-hidden="true">$messages_fragment</span>
        <span class="kaigo-widget__message-label">Консультант</span>
        <p>Здравствуйте! Я изучил ваш сайт. Чем помочь?</p>
      </article>
    </main>
    <div class="kaigo-widget__suggestions" data-region="suggestions" aria-label="Быстрые вопросы"></div>
    <div class="kaigo-widget__composer" data-region="composer" role="group" aria-label="Сообщение консультанту">
      <span class="kaigo-widget__composer-decoration" aria-hidden="true">$composer_fragment</span>
      <label class="kaigo-widget__sr-only" for="kaigo-message">Сообщение</label>
      <textarea id="kaigo-message" rows="1" placeholder="Напишите вопрос…" aria-label="Введите сообщение"></textarea>
      <button type="button" data-action="send" aria-label="Отправить сообщение">↑</button>
    </div>
  </section>
  <div class="kaigo-widget__motion-decoration" aria-hidden="true">$motion_fragment</div>
</section>
"""
)


class PatternCompilationError(ValueError):
    """A composition cannot be compiled into the fixed trusted runtime seed."""


@dataclass(frozen=True, slots=True)
class PatternSourceProvenance:
    runtime_contract_id: str
    runtime_contract_version: int
    compiler_bundle_id: str
    compiler_revision: int
    base_css_sha256: str
    pattern_versions: tuple[str, ...]
    implementation_hashes: tuple[str, ...]
    anatomy_sha256: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class PatternBrowserAuditContract:
    runtime_contract: str
    expected_pattern_root_classes: tuple[str, ...]
    compiler_bundle_id: str
    anatomy_sha256: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class CompiledPatternSource:
    body_html: str
    css: str
    javascript: str
    root_classes: tuple[str, ...]
    compiler_bundle_snapshot: str
    provenance: PatternSourceProvenance

    def __post_init__(self) -> None:
        if self.javascript != "":
            raise PatternCompilationError("runtime source JavaScript must remain empty")

    @property
    def source_sha256(self) -> str:
        return self.provenance.source_sha256

    def as_seed(self, *, revision: int, art_direction: str) -> WidgetArtifact:
        return WidgetArtifact(
            schema_version="1.0",
            revision=revision,
            stage=Stage.ART_DIRECTION,
            art_direction=art_direction,
            body_html=self.body_html,
            css=self.css,
            javascript="",
            suggested_actions=(),
            change_summary="Kaigo собрал безопасную основу виджета.",
            layout_contract={
                "runtime_contract": "chat-v1@1",
                "pattern_source_sha256": self.source_sha256,
                "compiler_bundle_id": self.provenance.compiler_bundle_id,
            },
        )

    def browser_audit_contract(self) -> PatternBrowserAuditContract:
        """Return inert expectations for a future trusted-runtime BrowserAudit.

        This does not wire the source compiler into BrowserAudit or production.
        It only makes the exact future boundary independently testable.
        """

        return PatternBrowserAuditContract(
            runtime_contract="chat-v1@1",
            expected_pattern_root_classes=self.root_classes,
            compiler_bundle_id=self.provenance.compiler_bundle_id,
            anatomy_sha256=self.provenance.anatomy_sha256,
            source_sha256=self.source_sha256,
        )


class _AnatomyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.region_counts: dict[str, int] = {}
        self.action_counts: dict[str, int] = {}
        self.suggestion_count = 0
        self.ids: list[str] = []
        self.focusable: list[tuple[str, str | None, str | None]] = []
        self.root_classes: set[str] = set()
        self.root_state_count = 0
        self.runtime_attributes: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        attributes: dict[str, str] = {}
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            if name in attributes:
                self.errors.append(f"duplicate attribute: {name}")
                continue
            attributes[name] = raw_value or ""
        region = attributes.get("data-region")
        if region:
            self.region_counts[region] = self.region_counts.get(region, 0) + 1
            if region == "root":
                self.root_classes.update(attributes.get("class", "").split())
                if attributes.get("data-state") == "closed":
                    self.root_state_count += 1
        action = attributes.get("data-action")
        if action:
            self.action_counts[action] = self.action_counts.get(action, 0) + 1
        if "data-suggestion" in attributes:
            self.suggestion_count += 1
        if "id" in attributes:
            self.ids.append(attributes["id"])
        self.runtime_attributes.extend(
            name for name in attributes if name.startswith("data-kaigo-runtime-")
        )
        if tag in {"button", "input", "select", "textarea", "a", "details", "summary"}:
            self.focusable.append((tag, region, action))
        if tag not in {"input"}:
            self.stack.append(tag)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1] == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "input":
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append("compiled anatomy markup is not balanced")
            return
        self.stack.pop()

    def close(self) -> None:
        super().close()
        if self.stack:
            self.errors.append("compiled anatomy markup is not balanced")


def canonical_anatomy_fingerprint(
    body_html: str,
    *,
    expected_root_classes: tuple[str, ...],
) -> str:
    parser = _AnatomyParser()
    try:
        parser.feed(body_html)
        parser.close()
    except Exception as exc:  # pragma: no cover - defensive parser boundary
        raise PatternCompilationError("compiled anatomy could not be parsed") from exc
    if parser.errors:
        raise PatternCompilationError(parser.errors[0])
    for region in _CANONICAL_REGIONS:
        if parser.region_counts.get(region, 0) != 1:
            raise PatternCompilationError(
                f"compiled anatomy requires exactly one {region} region"
            )
    unexpected_regions = set(parser.region_counts) - set(_CANONICAL_REGIONS)
    if unexpected_regions:
        raise PatternCompilationError("compiled anatomy contains a private region")
    for action in _CANONICAL_ACTIONS:
        if parser.action_counts.get(action, 0) != 1:
            raise PatternCompilationError(
                f"compiled anatomy requires exactly one {action} action"
            )
    if set(parser.action_counts) != set(_CANONICAL_ACTIONS):
        raise PatternCompilationError("compiled anatomy contains a private action")
    if parser.suggestion_count > 2:
        raise PatternCompilationError(
            "compiled anatomy requires zero to two suggestions"
        )
    if parser.ids != ["kaigo-message"]:
        raise PatternCompilationError("compiled anatomy contains a non-canonical id")
    if parser.root_state_count != 1:
        raise PatternCompilationError("compiled anatomy root state is invalid")
    if parser.runtime_attributes:
        raise PatternCompilationError("compiled seed cannot predeclare runtime attributes")
    if "kaigo-widget" not in parser.root_classes or not set(
        expected_root_classes
    ).issubset(parser.root_classes):
        raise PatternCompilationError("compiled anatomy is missing source root classes")
    expected_focusable = Counter(
        [
            ("button", "launcher", "open"),
            ("button", None, "close"),
            ("button", None, "send"),
            ("textarea", None, None),
        ]
        + [("button", None, None)] * parser.suggestion_count
    )
    if Counter(parser.focusable) != expected_focusable:
        raise PatternCompilationError(
            "compiled anatomy contains non-canonical interactive controls"
        )
    normalized_focusable = sorted(
        ((tag, region or "", action or "") for tag, region, action in parser.focusable)
    )
    record = {
        "regions": {name: parser.region_counts[name] for name in _CANONICAL_REGIONS},
        "actions": {name: parser.action_counts[name] for name in _CANONICAL_ACTIONS},
        "suggestions": parser.suggestion_count,
        "ids": parser.ids,
        "root_classes": sorted(parser.root_classes),
        "focusable": normalized_focusable,
    }
    return _sha256(_canonical_json(record))


def compile_runtime_source(
    plan: CompositionPlan,
    registry: PatternRegistry,
) -> CompiledPatternSource:
    if plan.custom_escape is not None:
        raise PatternCompilationError(
            "custom_escape is not available for runtime source compositions"
        )
    resolved: list[tuple[PatternSelection, PatternDefinition]] = []
    for selection in plan.selections:
        try:
            definition = registry.resolve(selection.pattern_id, selection.version)
        except PatternRegistryError as exc:
            raise PatternCompilationError(str(exc)) from exc
        if definition.status is not PatternStatus.ACTIVE:
            raise PatternCompilationError(
                f"pattern is not active: {definition.pattern_id}@{definition.version}"
            )
        if definition.category is not selection.slot:
            raise PatternCompilationError(
                f"pattern category does not match slot: {definition.pattern_id}"
            )
        if (
            definition.integration_mode is not PatternIntegrationMode.RUNTIME_SOURCE
            or definition.source_contract is None
        ):
            raise PatternCompilationError(
                f"pattern is not runtime_source: {definition.pattern_id}@{definition.version}"
            )
        validate_source_fragment(definition, definition.html)
        validate_source_css(definition, definition.css)
        _validate_parameters(selection, definition)
        resolved.append((selection, definition))

    selected_ids = {definition.pattern_id for _, definition in resolved}
    for _, definition in resolved:
        conflicts = selected_ids.intersection(definition.incompatible_with)
        if conflicts:
            raise PatternCompilationError(
                f"incompatible patterns: {definition.pattern_id} and {sorted(conflicts)[0]}"
            )

    by_category = {definition.category: definition for _, definition in resolved}
    if set(by_category) != set(PatternCategory):
        raise PatternCompilationError("runtime source composition is incomplete")
    root_classes = tuple(
        by_category[category].source_contract.root_class  # type: ignore[union-attr]
        for category in PatternCategory
    )
    body_html = _BODY_TEMPLATE.substitute(
        root_classes=" ".join(root_classes),
        launcher_fragment=by_category[PatternCategory.LAUNCHER].html.strip(),
        shell_fragment=by_category[PatternCategory.SHELL].html.strip(),
        messages_fragment=by_category[PatternCategory.MESSAGES].html.strip(),
        composer_fragment=by_category[PatternCategory.COMPOSER].html.strip(),
        motion_fragment=by_category[PatternCategory.MOTION].html.strip(),
    )
    anatomy_sha256 = canonical_anatomy_fingerprint(
        body_html,
        expected_root_classes=root_classes,
    )

    base_css = _BASE_CSS_PATH.read_text(encoding="utf-8", errors="strict")
    base_css_sha256 = _sha256(base_css)
    compiler_bundle_snapshot = _canonical_json(
        {
            "base_css": base_css,
            "base_css_sha256": base_css_sha256,
            "body_template": _BODY_TEMPLATE.template,
            "compiler_revision": _COMPILER_REVISION,
            "runtime_contract_id": _RUNTIME_CONTRACT_ID,
            "runtime_contract_version": _RUNTIME_CONTRACT_VERSION,
        }
    )
    compiler_bundle_id = _sha256(compiler_bundle_snapshot)
    parameter_css = [
        rendered
        for selection, definition in resolved
        if (rendered := _render_parameter_css(definition, selection.parameters))
    ]
    css_parts = [base_css.rstrip(), *parameter_css]
    css_parts.extend(definition.css.rstrip() for _, definition in resolved)
    css = "\n\n".join(part for part in css_parts if part) + "\n"
    pattern_versions = tuple(
        f"{definition.pattern_id}@{definition.version}"
        for _, definition in resolved
    )
    implementation_hashes = tuple(
        definition.implementation_sha256 for _, definition in resolved
    )
    source_payload = {
        "anatomy_sha256": anatomy_sha256,
        "body_html": body_html,
        "compiler_bundle_snapshot": compiler_bundle_snapshot,
        "css": css,
        "implementation_hashes": implementation_hashes,
        "javascript": "",
        "pattern_versions": pattern_versions,
        "root_classes": root_classes,
    }
    source_sha256 = _sha256(_canonical_json(source_payload))
    return CompiledPatternSource(
        body_html=body_html,
        css=css,
        javascript="",
        root_classes=root_classes,
        compiler_bundle_snapshot=compiler_bundle_snapshot,
        provenance=PatternSourceProvenance(
            runtime_contract_id=_RUNTIME_CONTRACT_ID,
            runtime_contract_version=_RUNTIME_CONTRACT_VERSION,
            compiler_bundle_id=compiler_bundle_id,
            compiler_revision=_COMPILER_REVISION,
            base_css_sha256=base_css_sha256,
            pattern_versions=pattern_versions,
            implementation_hashes=implementation_hashes,
            anatomy_sha256=anatomy_sha256,
            source_sha256=source_sha256,
        ),
    )


def _validate_parameters(
    selection: PatternSelection,
    definition: PatternDefinition,
) -> None:
    schema = _plain_json(definition.parameter_schema)
    parameters = _plain_json(selection.parameters)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(parameters)
    except (SchemaError, ValidationError) as exc:
        raise PatternCompilationError(
            f"parameters are invalid for {definition.pattern_id}: {exc.message}"
        ) from exc
    contract = definition.source_contract
    if contract is None:  # pragma: no cover - caller invariant
        raise PatternCompilationError("runtime source contract is missing")
    unbound = set(selection.parameters) - set(contract.parameter_bindings)
    if unbound:
        raise PatternCompilationError(
            f"parameter has no typed CSS binding: {sorted(unbound)[0]}"
        )


def _render_parameter_css(
    definition: PatternDefinition,
    parameters: Mapping[str, JSONValue],
) -> str:
    contract = definition.source_contract
    if contract is None:  # pragma: no cover - caller invariant
        raise PatternCompilationError("runtime source contract is missing")
    declarations: list[str] = []
    for name, value in sorted(parameters.items()):
        binding = contract.parameter_bindings.get(name)
        if binding is None:
            raise PatternCompilationError(f"parameter has no typed CSS binding: {name}")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise PatternCompilationError(f"parameter {name} is not numeric")
        declarations.append(f"{binding.css_variable}:{value:g}{binding.unit}")
    if not declarations:
        return ""
    return (
        f".kaigo-widget.{contract.root_class}"
        + "{"
        + ";".join(declarations)
        + ";}"
    )


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "CompiledPatternSource",
    "PatternBrowserAuditContract",
    "PatternCompilationError",
    "PatternSourceProvenance",
    "canonical_anatomy_fingerprint",
    "compile_runtime_source",
]
