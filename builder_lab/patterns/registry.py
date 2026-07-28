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


_EXPECTED_FILES = frozenset(
    {"manifest.json", "fragment.html", "styles.css", "behavior.js"}
)
_MANIFEST_FIELDS = frozenset(
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
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
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


class PatternRegistryError(ValueError):
    """The on-disk pattern catalog is not safe or internally consistent."""


class PatternStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


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

    def public_dict(self) -> dict[str, JSONValue]:
        return {
            "pattern_id": self.pattern_id,
            "version": self.version,
            "category": self.category.value,
            "status": self.status.value,
            "description": self.description,
            "parameter_schema": _plain_json(self.parameter_schema),
            "incompatible_with": list(self.incompatible_with),
            "implementation_sha256": self.implementation_sha256,
        }


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
            names = {item.name for item in directory.iterdir()}
            unexpected = names - _EXPECTED_FILES
            missing = _EXPECTED_FILES - names
            if unexpected:
                raise PatternRegistryError(
                    f"unexpected pattern asset: {sorted(unexpected)[0]}"
                )
            if missing:
                raise PatternRegistryError(f"missing pattern asset: {sorted(missing)[0]}")
            for item in directory.iterdir():
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
            definitions.append(_load_definition(directory))
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

    def public_catalog(self) -> tuple[dict[str, JSONValue], ...]:
        return tuple(definition.public_dict() for definition in self.definitions)


def load_builtin_registry() -> PatternRegistry:
    return PatternRegistry.load(Path(__file__).with_name("catalog"))


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise PatternRegistryError(f"cannot read pattern asset: {path.name}") from exc


def _load_definition(directory: Path) -> PatternDefinition:
    try:
        manifest = json.loads(_read_utf8(directory / "manifest.json"))
    except json.JSONDecodeError as exc:
        raise PatternRegistryError("manifest is not valid JSON") from exc
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_FIELDS:
        raise PatternRegistryError("manifest fields do not match the contract")
    if manifest.get("schema_version") != 1:
        raise PatternRegistryError("unsupported manifest schema version")
    pattern_id = _identifier(manifest.get("pattern_id"), "pattern_id")
    version = manifest.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PatternRegistryError("manifest version is invalid")
    try:
        category = PatternCategory(manifest.get("category"))
        status = PatternStatus(manifest.get("status"))
    except (TypeError, ValueError) as exc:
        raise PatternRegistryError("manifest category or status is invalid") from exc
    description = manifest.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 500:
        raise PatternRegistryError("manifest description is invalid")
    parameter_schema = manifest.get("parameter_schema")
    if not isinstance(parameter_schema, dict):
        raise PatternRegistryError("parameter schema must be an object")
    try:
        parameter_schema_bytes = json.dumps(
            parameter_schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PatternRegistryError("parameter schema must be plain JSON") from exc
    if len(parameter_schema_bytes) > 16 * 1024:
        raise PatternRegistryError("parameter schema is too large")
    incompatible = manifest.get("incompatible_with")
    if not isinstance(incompatible, list) or len(incompatible) > 32:
        raise PatternRegistryError("incompatible_with must be a bounded list")
    incompatible_with = tuple(
        _identifier(item, "incompatible pattern") for item in incompatible
    )
    if len(set(incompatible_with)) != len(incompatible_with):
        raise PatternRegistryError("incompatible_with contains duplicates")
    expected_hash = manifest.get("implementation_sha256")
    if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
        raise PatternRegistryError("implementation hash is invalid")

    html = _read_utf8(directory / "fragment.html")
    css = _read_utf8(directory / "styles.css")
    javascript = _read_utf8(directory / "behavior.js")
    _validate_implementation(html, css, javascript)
    actual_hash = _implementation_hash(manifest, html, css, javascript)
    if actual_hash != expected_hash:
        raise PatternRegistryError(
            f"implementation hash mismatch for {pattern_id}@{version}"
        )
    return PatternDefinition(
        pattern_id=pattern_id,
        version=version,
        category=category,
        status=status,
        description=description.strip(),
        parameter_schema=MappingProxyType(parameter_schema),
        incompatible_with=incompatible_with,
        implementation_sha256=actual_hash,
        html=html,
        css=css,
        javascript=javascript,
    )


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise PatternRegistryError(f"{field} is invalid")
    if len(value) > 80:
        raise PatternRegistryError(f"{field} is too long")
    return value


def _implementation_hash(
    manifest: Mapping[str, object],
    html: str,
    css: str,
    javascript: str,
) -> str:
    payload = {
        key: value
        for key, value in manifest.items()
        if key != "implementation_sha256"
    }
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    for asset in (html, css, javascript):
        digest.update(b"\0")
        digest.update(asset.encode("utf-8"))
    return digest.hexdigest()


def _validate_implementation(html: str, css: str, javascript: str) -> None:
    combined = "\n".join((html, css, javascript)).lower()
    marker = next(
        (item for item in _FORBIDDEN_IMPLEMENTATION_MARKERS if item in combined),
        None,
    )
    if marker is not None:
        raise PatternRegistryError(f"forbidden implementation capability: {marker}")
    if "<script" in html.lower() or "style=" in html.lower():
        raise PatternRegistryError("fragment HTML must not contain inline code")


def _plain_json(value: object) -> JSONValue:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_plain_json(item) for item in value)
    return value  # type: ignore[return-value]
