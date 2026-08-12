"""Safe loader and deterministic registry for atomic manifest schema v3."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .atomic_models import (
    AdaptationPolicy,
    AtomicPatternCategory,
    AtomicPatternDefinition,
    AtomicPatternStatus,
    SerializedJSONValue,
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

_EXTERNAL_URL_RE = re.compile(
    r"(?:https?|ftp|wss?|data|blob|javascript):\s*(?://|[^\s\"'`)>]+)|//[^\s\"'`)>]+",
    re.IGNORECASE,
)
_CSS_IMPORT_RE = re.compile(r"(?:^|[^\w])@import\b", re.IGNORECASE)
_CSS_URL_RE = re.compile(r"\burl\s*\(", re.IGNORECASE)
_HTML_ACTIVE_ELEMENTS = frozenset(
    {"base", "embed", "iframe", "link", "meta", "object", "script", "style"}
)
_HTML_FORBIDDEN_ATTRIBUTES = frozenset({"srcdoc"})
_HTML_LOCAL_FRAGMENT_RE = re.compile(r"^#[^\s]*$")
_HTML_URL_ATTRIBUTES = frozenset(
    {
        "action",
        "background",
        "cite",
        "data",
        "formaction",
        "href",
        "icon",
        "longdesc",
        "manifest",
        "ping",
        "poster",
        "profile",
        "src",
        "srcset",
        "usemap",
        "xlink:href",
    }
)
_DANGEROUS_IDENTIFIERS = frozenset(
    {
        "fetch",
        "open",
        "location",
        "settimeout",
        "setinterval",
        "xmlhttprequest",
        "websocket",
        "eventsource",
        "eval",
        "import",
        "localstorage",
        "sessionstorage",
        "indexeddb",
        "caches",
        "sendbeacon",
        "cookie",
        "cookiestore",
        "storage",
    }
)
_DANGEROUS_MEMBER_STRINGS = _DANGEROUS_IDENTIFIERS | {
    "function",
    "open",
    "request",
    "get",
    "post",
    "put",
    "delete",
    "src",
    "href",
    "srcdoc",
    "constructor",
    "write",
    "writeln",
    "location",
    "assign",
    "replace",
    "setattribute",
    "insertadjacenthtml",
    "innerhtml",
    "outerhtml",
}


@dataclass(frozen=True, slots=True)
class _ImplementationToken:
    kind: str
    value: str


class _FragmentHTMLValidator(HTMLParser):
    """Validate fragment structure without interpreting visible text as code."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._inspect_tag(tag, attrs)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._inspect_tag(tag, attrs)

    @staticmethod
    def _inspect_tag(
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() in _HTML_ACTIVE_ELEMENTS:
            raise AtomicPatternRegistryError(
                "forbidden implementation capability: active HTML element"
            )
        for raw_name, raw_value in attrs:
            name = "".join(raw_name.split()).casefold()
            if name == "style" or name.startswith("on"):
                raise AtomicPatternRegistryError(
                    "forbidden implementation capability: inline code"
                )
            if name in _HTML_FORBIDDEN_ATTRIBUTES:
                raise AtomicPatternRegistryError(
                    "forbidden implementation capability: active HTML attribute"
                )
            if raw_value is None or name not in _HTML_URL_ATTRIBUTES:
                continue
            value = unescape(raw_value).strip()
            if value and not _HTML_LOCAL_FRAGMENT_RE.fullmatch(value):
                raise AtomicPatternRegistryError(
                    "forbidden implementation capability: external URL"
                )


def _validate_fragment_html(source: str) -> None:
    parser = _FragmentHTMLValidator()
    try:
        parser.feed(source)
        parser.close()
    except AtomicPatternRegistryError:
        raise
    except (TypeError, ValueError) as exc:
        raise AtomicPatternRegistryError("fragment HTML is invalid") from exc


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
            actual_hash = _implementation_sha256(
                html=definition.html,
                css=definition.css,
                javascript=definition.javascript,
            )
            if actual_hash != definition.implementation_sha256:
                raise AtomicPatternRegistryError(
                    "implementation hash mismatch for "
                    f"{definition.pattern_id}@{definition.version}"
                )
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

    def selector_catalog(
        self,
        *,
        effective_review_states: Mapping[tuple[str, int], str] | None = None,
    ) -> tuple[dict[str, SerializedJSONValue], ...]:
        """Return active metadata using optional effective review overrides."""

        return tuple(
            definition.selector_dict()
            for definition in self.definitions
            if definition.status is AtomicPatternStatus.ACTIVE
            and (
                (
                    effective_review_states.get(
                        (definition.pattern_id, definition.version),
                        definition.provenance.get("review_state"),
                    )
                    if effective_review_states is not None
                    else definition.provenance.get("review_state")
                )
                == "approved"
            )
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
    actual_hash = _implementation_sha256(
        html=html,
        css=css,
        javascript=javascript,
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
    html, css, javascript = assets
    _validate_fragment_html(html)
    _validate_external_urls(css, line_comments=False, html_comments=False)
    _validate_external_urls(javascript, line_comments=True, html_comments=False)
    _scan_javascript_capabilities(javascript)


def _validate_external_urls(
    source: str,
    *,
    line_comments: bool,
    html_comments: bool,
) -> None:
    uncommented = _strip_comments(
        source,
        line_comments=line_comments,
        html_comments=html_comments,
    )
    if not line_comments and not html_comments and _CSS_IMPORT_RE.search(uncommented):
        raise AtomicPatternRegistryError("forbidden implementation capability: CSS import")
    if not line_comments and not html_comments and _CSS_URL_RE.search(uncommented):
        raise AtomicPatternRegistryError(
            "forbidden implementation capability: CSS resource URL"
        )
    if _EXTERNAL_URL_RE.search(uncommented):
        raise AtomicPatternRegistryError("forbidden implementation capability: external URL")


def _scan_javascript_capabilities(source: str) -> None:
    tokens = _tokenize(source, line_comments=True)
    for index, token in enumerate(tokens):
        if token.kind == "identifier":
            normalized = token.value.casefold()
            previous = tokens[index - 1] if index else None
            if normalized in _DANGEROUS_IDENTIFIERS or token.value == "Function":
                raise AtomicPatternRegistryError(
                    f"forbidden implementation capability: {token.value}"
                )
            if (
                previous is not None
                and previous.value == "."
                and normalized in _DANGEROUS_MEMBER_STRINGS
            ):
                raise AtomicPatternRegistryError(
                    f"forbidden implementation capability: member {token.value}"
                )
            continue
        if token.kind != "string":
            continue
        member_value = _constant_computed_member_value(tokens, index)
        if member_value is not None:
            value, _ = member_value
        else:
            value = token.value
        if value.casefold() not in _DANGEROUS_MEMBER_STRINGS:
            continue
        previous = tokens[index - 1] if index else None
        if previous is not None and previous.value in {"[", "."}:
            raise AtomicPatternRegistryError(
                f"forbidden implementation capability: member {value}"
            )


def _constant_computed_member_value(
    tokens: tuple[_ImplementationToken, ...],
    index: int,
) -> tuple[str, int] | None:
    """Fold a string-only computed member such as ``["send" + "Beacon"]``."""

    if index == 0 or tokens[index - 1].value != "[":
        return None
    if tokens[index].kind != "string":
        return None
    parts = [tokens[index].value]
    cursor = index + 1
    while cursor < len(tokens):
        if tokens[cursor].value == "]":
            return "".join(parts), cursor
        if (
            tokens[cursor].value != "+"
            or cursor + 1 >= len(tokens)
            or tokens[cursor + 1].kind != "string"
        ):
            return None
        parts.append(tokens[cursor + 1].value)
        cursor += 2
    return None


def _tokenize(source: str, *, line_comments: bool) -> tuple[_ImplementationToken, ...]:
    tokens: list[_ImplementationToken] = []
    index = 0
    length = len(source)
    while index < length:
        character = source[index]
        if character.isspace():
            index += 1
            continue
        if line_comments and source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if character in {"'", '"', "`"}:
            quote = character
            start = index + 1
            index = start
            value: list[str] = []
            while index < length:
                current = source[index]
                if current == "\\" and index + 1 < length:
                    escaped = source[index + 1]
                    if escaped == "x" and index + 3 < length:
                        digits = source[index + 2 : index + 4]
                        if all(character in "0123456789abcdefABCDEF" for character in digits):
                            value.append(chr(int(digits, 16)))
                            index += 4
                            continue
                    if escaped == "u" and index + 5 < length:
                        digits = source[index + 2 : index + 6]
                        if all(character in "0123456789abcdefABCDEF" for character in digits):
                            value.append(chr(int(digits, 16)))
                            index += 6
                            continue
                    value.append(escaped)
                    index += 2
                    continue
                if current == quote:
                    index += 1
                    break
                value.append(current)
                index += 1
            tokens.append(_ImplementationToken("string", "".join(value)))
            continue
        if character.isalpha() or character in {"_", "$"}:
            start = index
            index += 1
            while index < length and (
                source[index].isalnum() or source[index] in {"_", "$"}
            ):
                index += 1
            tokens.append(_ImplementationToken("identifier", source[start:index]))
            continue
        if character.isdigit():
            start = index
            index += 1
            while index < length and (source[index].isalnum() or source[index] in {".", "_"}):
                index += 1
            tokens.append(_ImplementationToken("number", source[start:index]))
            continue
        tokens.append(_ImplementationToken("punctuator", character))
        index += 1
    return tuple(tokens)


def _strip_comments(
    source: str,
    *,
    line_comments: bool,
    html_comments: bool,
) -> str:
    output: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        if html_comments and source.startswith("<!--", index):
            end = source.find("-->", index + 4)
            index = length if end < 0 else end + 3
            output.append(" ")
            continue
        if line_comments and source.startswith("//", index):
            end = source.find("\n", index + 2)
            index = length if end < 0 else end
            output.append("\n")
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                output.append(" ")
                break
            comment = source[index : end + 2]
            output.append("\n" * comment.count("\n"))
            index = end + 2
            continue
        character = source[index]
        if character in {"'", '"', "`"}:
            quote = character
            start = index
            index += 1
            while index < length:
                current = source[index]
                if current == "\\" and index + 1 < length:
                    index += 2
                    continue
                index += 1
                if current == quote:
                    break
            output.append(source[start:index])
            continue
        output.append(character)
        index += 1
    return "".join(output)


def _implementation_hash_bytes(assets: tuple[tuple[str, bytes], ...]) -> str:
    digest = hashlib.sha256()
    for name, asset in assets:
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(2, "big"))
        digest.update(encoded_name)
        digest.update(len(asset).to_bytes(8, "big"))
        digest.update(asset)
    return digest.hexdigest()


def _implementation_sha256(*, html: str, css: str, javascript: str) -> str:
    return _implementation_hash_bytes(
        tuple(
            (name, asset.encode("utf-8"))
            for name, asset in (
                ("fragment.html", html),
                ("styles.css", css),
                ("behavior.js", javascript),
            )
        )
    )


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
