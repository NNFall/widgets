"""Safe loader and deterministic registry for atomic manifest schema v3."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .atomic_models import (
    AdaptationPolicy,
    AtomicPatternCategory,
    AtomicPatternDefinition,
    AtomicPatternStatus,
    JSONValue,
)


_EXPECTED_MANIFEST_FIELDS = frozenset(
    {
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
)
_EXPECTED_ASSETS = frozenset({"manifest.json", "fragment.html", "styles.css", "behavior.js"})
_REQUIRED_ASSETS = frozenset({"manifest.json", "fragment.html", "styles.css"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IMPLEMENTATION_ASSETS = ("fragment.html", "styles.css", "behavior.js")
_MAX_ASSET_BYTES = 64 * 1024
_MAX_CATALOG_BYTES = 512 * 1024

# Keep this list deliberately capability-oriented.  It is checked against the
# exact implementation bytes, not selector-facing prose.
_FORBIDDEN_MARKERS = (
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
    "document.cookie",
    "navigator.sendbeacon",
    "sendbeacon",
    "cookiestore",
    "navigator.storage",
    "caches.",
    "javascript:",
    "require(",
)
_FORBIDDEN_CALL_RE = re.compile(
    r"\b(?:eval|fetch|function|import|require)\s*\(|\bnew\s+function\b",
    re.IGNORECASE,
)
_FORBIDDEN_IMPORT_RE = re.compile(
    r"\b(?:import|export)\s*(?:\{|\*|[\"']|[A-Za-z_$][\w$]*\s+from\b)",
    re.IGNORECASE,
)
_FORBIDDEN_URL_RE = re.compile(
    r"(?:https?|ftp|wss?|data|blob):\s*(?://|[^\s\"'`)>]+)|//[^\s\"'`)>]+",
    re.IGNORECASE,
)
_FORBIDDEN_STORAGE_RE = re.compile(
    r"\bdocument\s*(?:\.\s*cookie|\[\s*['\"]cookie['\"]\s*\])"
    r"|\b(?:localstorage|sessionstorage|indexeddb|cookiestore)\b"
    r"|\bnavigator\s*(?:\.\s*storage|\[\s*['\"]storage['\"]\s*\])",
    re.IGNORECASE,
)


class AtomicPatternRegistryError(ValueError):
    """The on-disk schema v3 catalog is unsafe or inconsistent."""


@dataclass(frozen=True, slots=True)
class AtomicPatternRegistry:
    """Immutable exact-version index over schema v3 pattern definitions."""

    definitions: tuple[AtomicPatternDefinition, ...]
    _index: Mapping[tuple[str, int], AtomicPatternDefinition] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        definitions = tuple(self.definitions)
        index: dict[tuple[str, int], AtomicPatternDefinition] = {}
        for definition in definitions:
            if not isinstance(definition, AtomicPatternDefinition):
                raise AtomicPatternRegistryError("definitions contain an invalid value")
            key = (definition.pattern_id, definition.version)
            if key in index:
                raise AtomicPatternRegistryError(
                    f"duplicate pattern version: {definition.pattern_id}@{definition.version}"
                )
            index[key] = definition
        object.__setattr__(self, "definitions", definitions)
        object.__setattr__(self, "_index", MappingProxyType(index))

    @property
    def index(self) -> Mapping[tuple[str, int], AtomicPatternDefinition]:
        """Read-only exact ``(pattern_id, version)`` lookup index."""

        return self._index

    @classmethod
    def load(cls, root: Path) -> "AtomicPatternRegistry":
        try:
            requested_root = Path(root)
            if requested_root.is_symlink():
                raise AtomicPatternRegistryError("catalog symlinks are not allowed")
            resolved_root = requested_root.resolve(strict=True)
        except AtomicPatternRegistryError:
            raise
        except (OSError, RuntimeError) as exc:
            raise AtomicPatternRegistryError("catalog root is not accessible") from exc
        if not resolved_root.is_dir():
            raise AtomicPatternRegistryError("catalog root must be a directory")

        try:
            entries = tuple(sorted(resolved_root.iterdir(), key=lambda item: item.name))
        except OSError as exc:
            raise AtomicPatternRegistryError("catalog root cannot be listed") from exc

        definitions: list[AtomicPatternDefinition] = []
        total_bytes = 0
        for directory in entries:
            if directory.is_symlink():
                raise AtomicPatternRegistryError("catalog symlinks are not allowed")
            if not directory.is_dir():
                raise AtomicPatternRegistryError(
                    f"unexpected catalog entry: {directory.name}"
                )
            try:
                resolved_directory = directory.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise AtomicPatternRegistryError(
                    f"pattern path cannot be resolved: {directory.name}"
                ) from exc
            if not _is_within(resolved_directory, resolved_root):
                raise AtomicPatternRegistryError("pattern path escapes catalog root")

            try:
                asset_entries = tuple(directory.iterdir())
            except OSError as exc:
                raise AtomicPatternRegistryError(
                    f"pattern directory cannot be listed: {directory.name}"
                ) from exc
            names = {item.name for item in asset_entries}
            if any(item.is_symlink() for item in asset_entries):
                raise AtomicPatternRegistryError("pattern asset symlinks are not allowed")
            unexpected = names - _EXPECTED_ASSETS
            if unexpected:
                raise AtomicPatternRegistryError(
                    f"unexpected pattern asset: {sorted(unexpected)[0]}"
                )
            missing = _REQUIRED_ASSETS - names
            if missing:
                raise AtomicPatternRegistryError(
                    f"missing pattern asset: {sorted(missing)[0]}"
                )
            for item in asset_entries:
                if item.is_symlink():
                    raise AtomicPatternRegistryError("pattern asset symlinks are not allowed")
                if not item.is_file():
                    raise AtomicPatternRegistryError("pattern assets must be regular files")
                try:
                    resolved_item = item.resolve(strict=True)
                    size = item.stat().st_size
                except (OSError, RuntimeError) as exc:
                    raise AtomicPatternRegistryError(
                        f"pattern asset cannot be read: {item.name}"
                    ) from exc
                if resolved_item.parent != resolved_directory:
                    raise AtomicPatternRegistryError("pattern asset escapes its directory")
                if size > _MAX_ASSET_BYTES:
                    raise AtomicPatternRegistryError(
                        f"pattern asset is too large: {item.name}"
                    )
                total_bytes += size
                if total_bytes > _MAX_CATALOG_BYTES:
                    raise AtomicPatternRegistryError("pattern catalog is too large")

            manifest = _read_manifest(directory / "manifest.json")
            try:
                definitions.append(_load_atomic_definition(directory, manifest))
            except AtomicPatternRegistryError:
                raise
            except ValueError as exc:
                raise AtomicPatternRegistryError(str(exc)) from exc

        if not definitions:
            raise AtomicPatternRegistryError("pattern catalog is empty")
        return cls(tuple(definitions))

    def resolve(self, pattern_id: str, version: int) -> AtomicPatternDefinition:
        try:
            return self._index[(pattern_id, version)]
        except (KeyError, TypeError) as exc:
            raise AtomicPatternRegistryError(
                f"unknown pattern version: {pattern_id}@{version}"
            ) from exc

    def active_for(
        self,
        category: AtomicPatternCategory,
    ) -> tuple[AtomicPatternDefinition, ...]:
        if not isinstance(category, AtomicPatternCategory):
            raise AtomicPatternRegistryError("category is invalid")
        return tuple(
            definition
            for definition in self.definitions
            if definition.category is category
            and definition.status is AtomicPatternStatus.ACTIVE
        )

    def selector_catalog(self) -> tuple[dict[str, JSONValue], ...]:
        """Return deterministic metadata for active and approved v3 patterns."""

        return tuple(
            definition.selector_dict()
            for definition in self.definitions
            if definition.status is AtomicPatternStatus.ACTIVE
            and definition.provenance.get("review_state") == "approved"
        )


def load_builtin_atomic_registry() -> AtomicPatternRegistry:
    """Load the technical fixtures shipped with this package."""

    return AtomicPatternRegistry.load(Path(__file__).with_name("atomic_catalog"))


def compute_implementation_hash(directory: Path) -> str:
    """Compute the v3 hash over exact UTF-8 asset bytes in stable order.

    The manifest is deliberately not part of the digest.  ``pattern_id`` and
    ``version`` identify an immutable definition, while lifecycle/review
    metadata may change without rewriting the implementation identity.
    """

    path = Path(directory)
    assets: list[tuple[str, bytes]] = []
    for name in _IMPLEMENTATION_ASSETS:
        asset_path = path / name
        if name == "behavior.js" and not asset_path.exists():
            assets.append((name, b""))
            continue
        raw = _read_asset_bytes(asset_path, name)
        assets.append((name, raw))
    return _implementation_hash_bytes(tuple(assets))


# A descriptive alias is useful to callers that prefer the field name.
compute_implementation_sha256 = compute_implementation_hash


def _load_atomic_definition(
    directory: Path,
    manifest: Mapping[str, object],
) -> AtomicPatternDefinition:
    if set(manifest) != _EXPECTED_MANIFEST_FIELDS:
        raise AtomicPatternRegistryError("manifest fields do not match the schema v3 contract")
    if manifest.get("schema_version") != 3:
        raise AtomicPatternRegistryError("unsupported manifest schema version")

    raw_category = manifest.get("category")
    raw_status = manifest.get("status")
    raw_policy = manifest.get("adaptation_policy")
    try:
        category = AtomicPatternCategory(raw_category)
        status = AtomicPatternStatus(raw_status)
        adaptation_policy = AdaptationPolicy(raw_policy)
    except (TypeError, ValueError) as exc:
        raise AtomicPatternRegistryError("manifest category, status, or policy is invalid") from exc

    expected_hash = manifest.get("implementation_sha256")
    if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
        raise AtomicPatternRegistryError("implementation hash is invalid")

    html_bytes = _read_asset_bytes(directory / "fragment.html", "fragment.html")
    css_bytes = _read_asset_bytes(directory / "styles.css", "styles.css")
    behavior_path = directory / "behavior.js"
    js_bytes = (
        _read_asset_bytes(behavior_path, "behavior.js")
        if behavior_path.exists()
        else b""
    )
    html = _decode_asset(html_bytes, "fragment.html")
    css = _decode_asset(css_bytes, "styles.css")
    javascript = _decode_asset(js_bytes, "behavior.js")
    _validate_implementation((html, css, javascript))
    actual_hash = _implementation_hash_bytes(
        (
            ("fragment.html", html_bytes),
            ("styles.css", css_bytes),
            ("behavior.js", js_bytes),
        )
    )
    if actual_hash != expected_hash:
        pattern_id = manifest.get("pattern_id", "<unknown>")
        version = manifest.get("version", "<unknown>")
        raise AtomicPatternRegistryError(
            f"implementation hash mismatch for {pattern_id}@{version}"
        )

    try:
        return AtomicPatternDefinition(
            schema_version=3,
            pattern_id=manifest.get("pattern_id", ""),
            version=manifest.get("version", 0),
            category=category,
            status=status,
            title=manifest.get("title", ""),
            summary=manifest.get("summary", ""),
            ai_description=manifest.get("ai_description", ""),
            technical_contract=manifest.get("technical_contract", ""),
            adaptation_policy=adaptation_policy,
            incompatible_with=manifest.get("incompatible_with", ()),
            implementation_sha256=expected_hash,
            provenance=manifest.get("provenance", {}),
            html=html,
            css=css,
            javascript=javascript,
        )
    except (TypeError, ValueError) as exc:
        raise AtomicPatternRegistryError(str(exc)) from exc


def _read_manifest(path: Path) -> dict[str, object]:
    raw = _read_asset_bytes(path, "manifest.json")
    try:
        text = raw.decode("utf-8", errors="strict")
        manifest = json.loads(text, parse_constant=_reject_json_constant)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise AtomicPatternRegistryError("manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise AtomicPatternRegistryError("manifest must be an object")
    return manifest


def _read_asset_bytes(path: Path, name: str) -> bytes:
    try:
        return path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise AtomicPatternRegistryError(f"cannot read pattern asset: {name}") from exc


def _decode_asset(raw: bytes, name: str) -> str:
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AtomicPatternRegistryError(f"pattern asset is not UTF-8: {name}") from exc


def _validate_implementation(assets: tuple[str, str, str]) -> None:
    combined = "\n".join(assets).lower()
    marker = next((item for item in _FORBIDDEN_MARKERS if item in combined), None)
    if marker is None and _FORBIDDEN_CALL_RE.search(combined):
        marker = "forbidden call"
    if marker is None and _FORBIDDEN_IMPORT_RE.search(combined):
        marker = "module import"
    if marker is None and _FORBIDDEN_URL_RE.search(combined):
        marker = "external URL"
    if marker is None and _FORBIDDEN_STORAGE_RE.search(combined):
        marker = "storage capability"
    if marker is not None:
        raise AtomicPatternRegistryError(
            f"forbidden implementation capability: {marker}"
        )
    html = assets[0].lower()
    if "<script" in html or "style=" in html:
        raise AtomicPatternRegistryError("fragment HTML must not contain inline code")


def _implementation_hash_bytes(assets: tuple[tuple[str, bytes], ...]) -> str:
    digest = hashlib.sha256()
    for name, asset in assets:
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(2, "big"))
        digest.update(encoded_name)
        digest.update(len(asset).to_bytes(8, "big"))
        digest.update(asset)
    return digest.hexdigest()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = [
    "AtomicPatternRegistry",
    "AtomicPatternRegistryError",
    "compute_implementation_hash",
    "compute_implementation_sha256",
    "load_builtin_atomic_registry",
]
