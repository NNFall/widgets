from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .models import JSONValue, PatternCategory


_LEGACY_EXPECTED_FILES = frozenset(
    {"manifest.json", "fragment.html", "styles.css", "behavior.js"}
)
_SOURCE_EXPECTED_FILES = frozenset(
    {"manifest.json", "fragment.html", "styles.css"}
)
_COMMON_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "pattern_id",
        "version",
        "category",
        "status",
        "description",
        "parameter_schema",
        "incompatible_with",
        "implementation_sha256",
    }
)
_SOURCE_MANIFEST_FIELDS = _COMMON_MANIFEST_FIELDS | {
    "source_contract",
    "provenance",
}
_SOURCE_CONTRACT_FIELDS = frozenset(
    {
        "runtime_contract_id",
        "runtime_contract_version",
        "fragment_role",
        "root_class",
        "parameter_bindings",
    }
)
_CSS_BINDING_FIELDS = frozenset({"css_variable", "unit"})
_PROVENANCE_FIELDS = frozenset(
    {"origin", "review_state", "source_revision", "supersedes"}
)
_SUPERSEDES_FIELDS = frozenset({"pattern_id", "version"})
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
_SOURCE_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_ROOT_CLASS_RE = re.compile(r"^kaigo-pattern-[a-z][a-z0-9-]*$")
_CSS_VARIABLE_RE = re.compile(r"^--kaigo-pattern-[a-z][a-z0-9-]*$")
_PARAMETER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_ASSET_BYTES = 64 * 1024
_MAX_CATALOG_BYTES = 512 * 1024
_FORBIDDEN_IMPLEMENTATION_MARKERS = (
    "http://",
    "https://",
    "@import",
    "fetch(",
    "xmlhttprequest",
    "websocket",
    "eventsource",
    "localstorage",
    "sessionstorage",
    "indexeddb",
    "import(",
    "eval(",
    "new function",
)
_FRAGMENT_ROLES = {
    PatternCategory.LAUNCHER: "launcher-content",
    PatternCategory.SHELL: "shell-decoration",
    PatternCategory.MESSAGES: "message-decoration",
    PatternCategory.COMPOSER: "composer-decoration",
    PatternCategory.MOTION: "motion-none",
}


class PatternRegistryError(ValueError):
    """The on-disk pattern catalog is not safe or internally consistent."""


class PatternStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class PatternIntegrationMode(str, Enum):
    LEGACY_REFERENCE = "legacy_reference"
    RUNTIME_SOURCE = "runtime_source"


@dataclass(frozen=True, slots=True)
class CssParameterBinding:
    css_variable: str
    unit: str

    def public_dict(self) -> dict[str, JSONValue]:
        return {"css_variable": self.css_variable, "unit": self.unit}


@dataclass(frozen=True, slots=True)
class PatternSourceContract:
    runtime_contract_id: str
    runtime_contract_version: int
    fragment_role: str
    root_class: str
    parameter_bindings: Mapping[str, CssParameterBinding]

    def public_dict(self) -> dict[str, JSONValue]:
        return {
            "runtime_contract_id": self.runtime_contract_id,
            "runtime_contract_version": self.runtime_contract_version,
            "fragment_role": self.fragment_role,
            "root_class": self.root_class,
            "parameter_bindings": {
                name: binding.public_dict()
                for name, binding in sorted(self.parameter_bindings.items())
            },
        }


@dataclass(frozen=True, slots=True)
class PatternDefinition:
    pattern_id: str
    version: int
    category: PatternCategory
    status: PatternStatus
    description: str
    parameter_schema: Mapping[str, JSONValue]
    incompatible_with: tuple[str, ...]
    implementation_sha256: str
    html: str
    css: str
    javascript: str
    integration_mode: PatternIntegrationMode
    source_contract: PatternSourceContract | None
    provenance: Mapping[str, JSONValue]

    def public_dict(self) -> dict[str, JSONValue]:
        public: dict[str, JSONValue] = {
            "pattern_id": self.pattern_id,
            "version": self.version,
            "category": self.category.value,
            "status": self.status.value,
            "description": self.description,
            "parameter_schema": _plain_json(self.parameter_schema),
            "incompatible_with": list(self.incompatible_with),
            "implementation_sha256": self.implementation_sha256,
        }
        if self.integration_mode is PatternIntegrationMode.RUNTIME_SOURCE:
            if self.source_contract is None:  # pragma: no cover - loader invariant
                raise PatternRegistryError("runtime source contract is missing")
            public.update(
                {
                    "integration_mode": self.integration_mode.value,
                    "source_contract": self.source_contract.public_dict(),
                    "provenance": _plain_json(self.provenance),
                }
            )
        return public


@dataclass(frozen=True, slots=True)
class PatternRegistry:
    definitions: tuple[PatternDefinition, ...]
    _index: Mapping[tuple[str, int], PatternDefinition] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        index: dict[tuple[str, int], PatternDefinition] = {}
        for definition in self.definitions:
            key = (definition.pattern_id, definition.version)
            if key in index:
                raise PatternRegistryError(
                    f"duplicate pattern version: {definition.pattern_id}@{definition.version}"
                )
            index[key] = definition
        object.__setattr__(self, "_index", MappingProxyType(index))

    @classmethod
    def load(cls, root: Path) -> "PatternRegistry":
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise PatternRegistryError("catalog root must be a directory")
        definitions: list[PatternDefinition] = []
        total_bytes = 0
        for directory in sorted(root.iterdir(), key=lambda item: item.name):
            if directory.is_symlink():
                raise PatternRegistryError("catalog symlinks are not allowed")
            if not directory.is_dir():
                raise PatternRegistryError(f"unexpected catalog entry: {directory.name}")
            resolved = directory.resolve(strict=True)
            if root not in resolved.parents:
                raise PatternRegistryError("pattern path escapes catalog root")

            entries = tuple(directory.iterdir())
            names = {item.name for item in entries}
            if "manifest.json" not in names:
                raise PatternRegistryError("missing pattern asset: manifest.json")
            for item in entries:
                if item.is_symlink() or not item.is_file():
                    raise PatternRegistryError("pattern assets must be regular files")
                if item.resolve(strict=True).parent != resolved:
                    raise PatternRegistryError("pattern asset escapes its directory")
                size = item.stat().st_size
                if size > _MAX_ASSET_BYTES:
                    raise PatternRegistryError(f"pattern asset is too large: {item.name}")
                total_bytes += size
                if total_bytes > _MAX_CATALOG_BYTES:
                    raise PatternRegistryError("pattern catalog is too large")

            manifest = _read_manifest(directory / "manifest.json")
            schema_version = manifest.get("schema_version")
            if isinstance(schema_version, bool) or schema_version not in {1, 2}:
                raise PatternRegistryError("unsupported manifest schema version")
            expected_files = (
                _LEGACY_EXPECTED_FILES
                if schema_version == 1
                else _SOURCE_EXPECTED_FILES
            )
            unexpected = names - expected_files
            missing = expected_files - names
            if unexpected:
                raise PatternRegistryError(
                    f"unexpected pattern asset: {sorted(unexpected)[0]}"
                )
            if missing:
                raise PatternRegistryError(
                    f"missing pattern asset: {sorted(missing)[0]}"
                )
            definitions.append(_load_definition(directory, manifest))
        if not definitions:
            raise PatternRegistryError("pattern catalog is empty")
        return cls(tuple(definitions))

    def resolve(self, pattern_id: str, version: int) -> PatternDefinition:
        try:
            return self._index[(pattern_id, version)]
        except KeyError as exc:
            raise PatternRegistryError(
                f"unknown pattern version: {pattern_id}@{version}"
            ) from exc

    def active_for(self, category: PatternCategory) -> tuple[PatternDefinition, ...]:
        return tuple(
            definition
            for definition in self.definitions
            if definition.category is category
            and definition.status is PatternStatus.ACTIVE
        )

    def selectable_for(
        self,
        category: PatternCategory,
    ) -> tuple[PatternDefinition, ...]:
        return tuple(
            definition
            for definition in self.definitions
            if definition.category is category
            and definition.status is PatternStatus.ACTIVE
            and definition.integration_mode is PatternIntegrationMode.RUNTIME_SOURCE
        )

    def planner_catalog(self) -> tuple[dict[str, JSONValue], ...]:
        return tuple(
            definition.public_dict()
            for category in PatternCategory
            for definition in self.selectable_for(category)
        )

    def public_catalog(self) -> tuple[dict[str, JSONValue], ...]:
        """Keep the live planner on the historical fail-closed catalog.

        The source catalog is intentionally exposed only through
        :meth:`planner_catalog` until the separate production phase gate is met.
        """

        return tuple(
            definition.public_dict()
            for definition in self.definitions
            if definition.integration_mode is PatternIntegrationMode.LEGACY_REFERENCE
        )


def source_expected_files() -> set[str]:
    return set(_SOURCE_EXPECTED_FILES)


def load_builtin_registry() -> PatternRegistry:
    return PatternRegistry.load(Path(__file__).with_name("catalog"))


def compute_implementation_hash(directory: Path) -> str:
    manifest = _read_manifest(directory / "manifest.json")
    schema_version = manifest.get("schema_version")
    if schema_version == 1:
        assets = (
            _read_utf8(directory / "fragment.html"),
            _read_utf8(directory / "styles.css"),
            _read_utf8(directory / "behavior.js"),
        )
    elif schema_version == 2:
        assets = (
            _read_utf8(directory / "fragment.html"),
            _read_utf8(directory / "styles.css"),
        )
    else:
        raise PatternRegistryError("unsupported manifest schema version")
    return _implementation_hash(manifest, schema_version, assets)


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise PatternRegistryError(f"cannot read pattern asset: {path.name}") from exc


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        manifest = json.loads(_read_utf8(path))
    except json.JSONDecodeError as exc:
        raise PatternRegistryError("manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise PatternRegistryError("manifest must be an object")
    return manifest


def _load_definition(
    directory: Path,
    manifest: dict[str, object],
) -> PatternDefinition:
    schema_version = manifest.get("schema_version")
    fields = _COMMON_MANIFEST_FIELDS if schema_version == 1 else _SOURCE_MANIFEST_FIELDS
    if set(manifest) != fields:
        raise PatternRegistryError("manifest fields do not match the contract")

    integration_mode = (
        PatternIntegrationMode.LEGACY_REFERENCE
        if schema_version == 1
        else PatternIntegrationMode.RUNTIME_SOURCE
    )
    pattern_id = _identifier(manifest.get("pattern_id"), "pattern_id")
    if (
        integration_mode is PatternIntegrationMode.RUNTIME_SOURCE
        and not _SOURCE_IDENTIFIER_RE.fullmatch(pattern_id)
    ):
        raise PatternRegistryError("runtime source pattern_id is invalid")
    version = manifest.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PatternRegistryError("manifest version is invalid")
    try:
        category = PatternCategory(manifest.get("category"))
        status = PatternStatus(manifest.get("status"))
    except (TypeError, ValueError) as exc:
        raise PatternRegistryError("manifest category or status is invalid") from exc
    description = manifest.get("description")
    if (
        not isinstance(description, str)
        or not description.strip()
        or len(description) > 500
    ):
        raise PatternRegistryError("manifest description is invalid")
    parameter_schema = _parameter_schema(manifest.get("parameter_schema"))
    incompatible_with = _incompatible_with(manifest.get("incompatible_with"))
    expected_hash = manifest.get("implementation_sha256")
    if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
        raise PatternRegistryError("implementation hash is invalid")

    source_contract: PatternSourceContract | None = None
    provenance: Mapping[str, JSONValue] = MappingProxyType({})
    if integration_mode is PatternIntegrationMode.RUNTIME_SOURCE:
        source_contract = _source_contract(
            manifest.get("source_contract"),
            category=category,
            parameter_schema=parameter_schema,
        )
        provenance = _source_provenance(manifest.get("provenance"))

    html = _read_utf8(directory / "fragment.html")
    css = _read_utf8(directory / "styles.css")
    javascript = (
        _read_utf8(directory / "behavior.js")
        if integration_mode is PatternIntegrationMode.LEGACY_REFERENCE
        else ""
    )
    assets = (html, css, javascript) if schema_version == 1 else (html, css)
    actual_hash = _implementation_hash(manifest, int(schema_version), assets)
    if actual_hash != expected_hash:
        raise PatternRegistryError(
            f"implementation hash mismatch for {pattern_id}@{version}"
        )

    definition = PatternDefinition(
        pattern_id=pattern_id,
        version=version,
        category=category,
        status=status,
        description=description.strip(),
        parameter_schema=parameter_schema,
        incompatible_with=incompatible_with,
        implementation_sha256=actual_hash,
        html=html,
        css=css,
        javascript=javascript,
        integration_mode=integration_mode,
        source_contract=source_contract,
        provenance=provenance,
    )
    if integration_mode is PatternIntegrationMode.LEGACY_REFERENCE:
        _validate_legacy_implementation(html, css, javascript)
    else:
        from .quality import validate_source_css, validate_source_fragment

        try:
            validate_source_fragment(definition, html)
            validate_source_css(definition, css)
        except ValueError as exc:
            raise PatternRegistryError(str(exc)) from exc
    return definition


def _parameter_schema(value: object) -> Mapping[str, JSONValue]:
    if not isinstance(value, dict):
        raise PatternRegistryError("parameter schema must be an object")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PatternRegistryError("parameter schema must be plain JSON") from exc
    if len(encoded) > 16 * 1024:
        raise PatternRegistryError("parameter schema is too large")
    frozen = _freeze_json(value)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        raise PatternRegistryError("parameter schema must be an object")
    return frozen


def _incompatible_with(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 32:
        raise PatternRegistryError("incompatible_with must be a bounded list")
    incompatible = tuple(_identifier(item, "incompatible pattern") for item in value)
    if len(set(incompatible)) != len(incompatible):
        raise PatternRegistryError("incompatible_with contains duplicates")
    return incompatible


def _source_contract(
    value: object,
    *,
    category: PatternCategory,
    parameter_schema: Mapping[str, JSONValue],
) -> PatternSourceContract:
    if not isinstance(value, dict) or set(value) != _SOURCE_CONTRACT_FIELDS:
        raise PatternRegistryError("source contract fields do not match the contract")
    if value.get("runtime_contract_id") != "chat-v1":
        raise PatternRegistryError("source runtime contract id is invalid")
    if value.get("runtime_contract_version") != 1:
        raise PatternRegistryError("source runtime contract version is invalid")
    expected_role = _FRAGMENT_ROLES[category]
    if value.get("fragment_role") != expected_role:
        raise PatternRegistryError("source fragment role does not match category")
    root_class = value.get("root_class")
    if not isinstance(root_class, str) or not _ROOT_CLASS_RE.fullmatch(root_class):
        raise PatternRegistryError("source root class is invalid")
    raw_bindings = value.get("parameter_bindings")
    if not isinstance(raw_bindings, dict) or len(raw_bindings) > 32:
        raise PatternRegistryError("source parameter bindings are invalid")
    properties = parameter_schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise PatternRegistryError("parameter schema properties must be an object")
    bindings: dict[str, CssParameterBinding] = {}
    seen_variables: set[str] = set()
    for name, raw_binding in raw_bindings.items():
        if (
            not isinstance(name, str)
            or not _PARAMETER_NAME_RE.fullmatch(name)
            or name not in properties
        ):
            raise PatternRegistryError("source parameter binding key is invalid")
        if not isinstance(raw_binding, dict) or set(raw_binding) != _CSS_BINDING_FIELDS:
            raise PatternRegistryError("source CSS binding fields are invalid")
        css_variable = raw_binding.get("css_variable")
        if (
            not isinstance(css_variable, str)
            or not _CSS_VARIABLE_RE.fullmatch(css_variable)
        ):
            raise PatternRegistryError("source css variable is invalid")
        if css_variable in seen_variables:
            raise PatternRegistryError("source css variable is duplicated")
        unit = raw_binding.get("unit")
        if unit not in {"", "px", "ms"}:
            raise PatternRegistryError("source CSS binding unit is invalid")
        seen_variables.add(css_variable)
        bindings[name] = CssParameterBinding(css_variable=css_variable, unit=unit)
    return PatternSourceContract(
        runtime_contract_id="chat-v1",
        runtime_contract_version=1,
        fragment_role=expected_role,
        root_class=root_class,
        parameter_bindings=MappingProxyType(bindings),
    )


def _source_provenance(value: object) -> Mapping[str, JSONValue]:
    if not isinstance(value, dict) or set(value) != _PROVENANCE_FIELDS:
        raise PatternRegistryError("source provenance fields do not match the contract")
    if value.get("origin") != "kaigo-owned":
        raise PatternRegistryError("source provenance origin is invalid")
    if value.get("review_state") != "verified":
        raise PatternRegistryError("source review state is invalid")
    source_revision = value.get("source_revision")
    if (
        isinstance(source_revision, bool)
        or not isinstance(source_revision, int)
        or source_revision < 1
    ):
        raise PatternRegistryError("source revision is invalid")
    supersedes = value.get("supersedes")
    normalized_supersedes: JSONValue
    if supersedes is None:
        normalized_supersedes = None
    else:
        if not isinstance(supersedes, dict) or set(supersedes) != _SUPERSEDES_FIELDS:
            raise PatternRegistryError("source supersedes reference is invalid")
        superseded_version = supersedes.get("version")
        if (
            isinstance(superseded_version, bool)
            or not isinstance(superseded_version, int)
            or superseded_version < 1
        ):
            raise PatternRegistryError("source supersedes version is invalid")
        normalized_supersedes = MappingProxyType(
            {
                "pattern_id": _identifier(
                    supersedes.get("pattern_id"),
                    "supersedes pattern_id",
                ),
                "version": superseded_version,
            }
        )
    return MappingProxyType(
        {
            "origin": "kaigo-owned",
            "review_state": "verified",
            "source_revision": source_revision,
            "supersedes": normalized_supersedes,
        }
    )


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise PatternRegistryError(f"{field} is invalid")
    if len(value) > 80:
        raise PatternRegistryError(f"{field} is too long")
    return value


def _implementation_hash(
    manifest: Mapping[str, object],
    schema_version: int,
    assets: tuple[str, ...],
) -> str:
    excluded = {"implementation_sha256"}
    if schema_version == 2:
        # Source status is operational metadata. Deprecating a verified version
        # must not rewrite its immutable implementation identity.
        excluded.add("status")
    payload = {key: value for key, value in manifest.items() if key not in excluded}
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    for asset in assets:
        digest.update(b"\0")
        digest.update(asset.encode("utf-8"))
    return digest.hexdigest()


def _validate_legacy_implementation(html: str, css: str, javascript: str) -> None:
    combined = "\n".join((html, css, javascript)).lower()
    marker = next(
        (item for item in _FORBIDDEN_IMPLEMENTATION_MARKERS if item in combined),
        None,
    )
    if marker is not None:
        raise PatternRegistryError(f"forbidden implementation capability: {marker}")
    if "<script" in html.lower() or "style=" in html.lower():
        raise PatternRegistryError("fragment HTML must not contain inline code")


def _freeze_json(value: object) -> JSONValue:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value  # type: ignore[return-value]


def _plain_json(value: object) -> JSONValue:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_plain_json(item) for item in value)
    return value  # type: ignore[return-value]
