from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit


_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SCREENSHOT_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
_TOKEN_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]{24,}$")


def _validated_id(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} is invalid")
    return normalized


def _validated_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ValueError("sha256 must contain 64 lowercase hex characters")
    return normalized


def public_url(value: str) -> str:
    """Return a stable URL label without credentials, query, fragment, or tokens."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return "[redacted-url]"
    path_segments = []
    for segment in (parsed.path or "/").split("/"):
        path_segments.append("[redacted]" if _TOKEN_SEGMENT.fullmatch(segment) else segment)
    path = "/".join(path_segments) or "/"
    return urlunsplit((parsed.scheme.lower(), f"{host.lower()}{port}", path, "", ""))


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _bounded_json_mapping(
    value: Mapping[str, Any], field_name: str, max_bytes: int
) -> Mapping[str, Any]:
    result = _thaw_json(value)
    if len(result) > 32:
        raise ValueError(f"{field_name} contains too many fields")
    try:
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain JSON-safe values") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{field_name} exceeds its byte limit")
    return _freeze_json(result)


@dataclass(frozen=True)
class ScreenshotEvidence:
    """A screenshot and its public, content-addressed metadata.

    ``data`` intentionally never appears in :meth:`to_dict`. Callers may retain it
    in memory or explicitly persist it in a private evidence directory.
    """

    screenshot_id: str
    page_id: str
    viewport: str
    position: str
    mime_type: str
    width: int
    height: int
    sha256: str
    size_bytes: int
    data: bytes | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "screenshot_id", _validated_id(self.screenshot_id, "screenshot_id"))
        object.__setattr__(self, "page_id", _validated_id(self.page_id, "page_id"))
        if self.viewport not in {"desktop", "mobile"}:
            raise ValueError("viewport must be desktop or mobile")
        if self.position not in {
            "top",
            "after_top",
            "middle",
            "bottom",
            "last_observed",
        }:
            raise ValueError(
                "position must be top, after_top, middle, bottom, or last_observed"
            )
        if self.mime_type not in _SCREENSHOT_MIMES:
            raise ValueError("unsupported screenshot mime type")
        if self.width < 1 or self.height < 1:
            raise ValueError("screenshot dimensions must be positive")
        if self.width > 4096 or self.height > 4096:
            raise ValueError("screenshot dimensions are unreasonably large")
        if self.size_bytes < 1 or self.size_bytes > 20 * 1024 * 1024:
            raise ValueError("screenshot size is outside the allowed range")
        object.__setattr__(self, "sha256", _validated_sha256(self.sha256))
        if self.data is not None:
            if len(self.data) != self.size_bytes:
                raise ValueError("screenshot size_bytes does not match data")
            if hashlib.sha256(self.data).hexdigest() != self.sha256:
                raise ValueError("screenshot sha256 does not match data")

    def to_dict(self) -> dict[str, Any]:
        return {
            "screenshot_id": self.screenshot_id,
            "page_id": self.page_id,
            "viewport": self.viewport,
            "position": self.position,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class TraceEvidence:
    """Metadata for a failed-run Playwright trace with an explicit expiry."""

    trace_id: str
    sha256: str
    size_bytes: int
    expires_at: datetime
    data: bytes | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_id", _validated_id(self.trace_id, "trace_id"))
        object.__setattr__(self, "sha256", _validated_sha256(self.sha256))
        if self.size_bytes < 1 or self.size_bytes > 100 * 1024 * 1024:
            raise ValueError("trace size is outside the allowed range")
        if self.expires_at.tzinfo is None:
            raise ValueError("trace expires_at must be timezone aware")
        if self.data is not None:
            if len(self.data) != self.size_bytes:
                raise ValueError("trace size_bytes does not match data")
            if hashlib.sha256(self.data).hexdigest() != self.sha256:
                raise ValueError("trace sha256 does not match data")

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True)
class CrawlFailure:
    code: str
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _validated_id(self.code, "failure code"))
        message = self.message.strip()
        if not message or len(message) > 1000:
            raise ValueError("failure message is invalid")
        object.__setattr__(self, "message", message)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class ReferencePageEvidence:
    page_id: str
    category: str
    requested_url: str
    final_url: str
    depth: int
    screenshots: tuple[ScreenshotEvidence, ...] = ()
    semantic_sample: Mapping[str, Any] = field(default_factory=dict)
    style_sample: Mapping[str, Any] = field(default_factory=dict)
    console_failures: tuple[str, ...] = ()
    page_failures: tuple[str, ...] = ()
    request_failures: tuple[str, ...] = ()
    policy_blocks: tuple[str, ...] = ()
    timings_ms: Mapping[str, int | float] = field(default_factory=dict)
    transferred_bytes: int = 0
    scroll_strategy: str = "not_captured"
    reset_strategy: str = "none"
    skipped_reasons: tuple[str, ...] = ()
    coverage_status: str = "not_captured"

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_id", _validated_id(self.page_id, "page_id"))
        object.__setattr__(self, "category", _validated_id(self.category, "category"))
        if not self.requested_url or not self.final_url:
            raise ValueError("page URLs must not be empty")
        if self.depth < 0 or self.depth > 8:
            raise ValueError("page depth is invalid")
        if len(self.screenshots) > 8:
            raise ValueError("too many screenshots for one page")
        if any(item.page_id != self.page_id for item in self.screenshots):
            raise ValueError("screenshot page_id does not match page")
        screenshot_ids = [item.screenshot_id for item in self.screenshots]
        if len(set(screenshot_ids)) != len(screenshot_ids):
            raise ValueError("duplicate screenshot_id")
        for failures in (
            self.console_failures,
            self.page_failures,
            self.request_failures,
            self.policy_blocks,
            self.skipped_reasons,
        ):
            if len(failures) > 100:
                raise ValueError("too many recorded failures")
            if any(len(message) > 1000 for message in failures):
                raise ValueError("recorded failure is too long")
        if self.transferred_bytes < 0:
            raise ValueError("transferred_bytes must not be negative")
        allowed_scroll = {
            "not_captured",
            "static",
            "document",
            "nested",
            "virtual",
            "mixed",
            "document+script_fallback",
            "nested+script_fallback",
            "virtual+script_fallback",
            "mixed+script_fallback",
        }
        if self.scroll_strategy not in allowed_scroll:
            raise ValueError("scroll_strategy is invalid")
        if self.reset_strategy not in {
            "none",
            "not_captured",
            "not-required-top-first",
            "wheel-prewarm-return-top",
            "single-pass",
        }:
            raise ValueError("reset_strategy is invalid")
        if self.coverage_status not in {"complete", "partial", "not_captured"}:
            raise ValueError("coverage_status is invalid")
        positions = [item.position for item in self.screenshots]
        if len(positions) != len(set(positions)):
            raise ValueError("duplicate screenshot position")
        screenshot_shapes = {
            (item.viewport, item.width, item.height) for item in self.screenshots
        }
        if len(screenshot_shapes) > 1:
            raise ValueError("page screenshots must share one viewport and dimensions")
        position_set = set(positions)
        if self.coverage_status == "complete":
            if not {"top", "bottom"}.issubset(position_set):
                raise ValueError("complete coverage requires top and bottom screenshots")
            if "last_observed" in position_set:
                raise ValueError("complete coverage forbids last_observed")
        if self.coverage_status == "partial":
            if not {"top", "last_observed"}.issubset(position_set) or "bottom" in position_set:
                raise ValueError(
                    "partial coverage requires top and last_observed and forbids bottom"
                )
            if not self.skipped_reasons:
                raise ValueError("partial coverage requires a skipped reason")
        if self.coverage_status == "not_captured" and self.screenshots:
            raise ValueError("not_captured coverage forbids screenshots")
        if len(self.timings_ms) > 32 or any(
            not isinstance(value, (int, float)) or value < 0
            for value in self.timings_ms.values()
        ):
            raise ValueError("timings_ms is invalid")
        object.__setattr__(self, "screenshots", tuple(self.screenshots))
        object.__setattr__(
            self,
            "semantic_sample",
            _bounded_json_mapping(self.semantic_sample, "semantic_sample", 128 * 1024),
        )
        object.__setattr__(
            self,
            "style_sample",
            _bounded_json_mapping(self.style_sample, "style_sample", 512 * 1024),
        )
        object.__setattr__(self, "console_failures", tuple(self.console_failures))
        object.__setattr__(self, "page_failures", tuple(self.page_failures))
        object.__setattr__(self, "request_failures", tuple(self.request_failures))
        object.__setattr__(self, "policy_blocks", tuple(self.policy_blocks))
        object.__setattr__(self, "skipped_reasons", tuple(self.skipped_reasons))
        object.__setattr__(self, "timings_ms", _freeze_json(dict(self.timings_ms)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "category": self.category,
            "requested_url": public_url(self.requested_url),
            "final_url": public_url(self.final_url),
            "depth": self.depth,
            "screenshots": [item.to_dict() for item in self.screenshots],
            "semantic_sample": _thaw_json(self.semantic_sample),
            "style_sample": _thaw_json(self.style_sample),
            "console_failures": list(self.console_failures),
            "page_failures": list(self.page_failures),
            "request_failures": list(self.request_failures),
            "policy_blocks": list(self.policy_blocks),
            "timings_ms": _thaw_json(self.timings_ms),
            "transferred_bytes": self.transferred_bytes,
            "scroll_strategy": self.scroll_strategy,
            "reset_strategy": self.reset_strategy,
            "skipped_reasons": list(self.skipped_reasons),
            "coverage_status": self.coverage_status,
        }


@dataclass(frozen=True)
class ReferenceCrawlResult:
    source_url: str
    status: str
    started_at: datetime
    completed_at: datetime
    pages: tuple[ReferencePageEvidence, ...] = ()
    failure: CrawlFailure | None = None
    trace: TraceEvidence | None = None
    user_agent: str = "KaigoVisualResearch/1.0 (+https://kaigo.space)"

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "partial", "failed"}:
            raise ValueError("unsupported crawl status")
        if self.started_at.tzinfo is None or self.completed_at.tzinfo is None:
            raise ValueError("crawl timestamps must be timezone aware")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if self.status in {"succeeded", "partial"} and (
            self.failure is not None or self.trace is not None
        ):
            raise ValueError("non-failed crawls cannot contain failure trace metadata")
        if self.status == "failed" and self.failure is None:
            raise ValueError("failed crawls require a failure")
        if self.status == "succeeded" and (
            not self.pages
            or any(page.coverage_status != "complete" for page in self.pages)
        ):
            raise ValueError(
                "succeeded crawls require nonempty complete page evidence"
            )
        if self.status == "partial" and (
            not self.pages
            or all(page.coverage_status == "complete" for page in self.pages)
        ):
            raise ValueError(
                "partial crawls require nonempty incomplete page evidence"
            )
        if len(self.pages) > 7:  # five desktop plus two mobile captures
            raise ValueError("crawl result exceeds the page evidence cap")
        page_keys = [
            (
                item.page_id,
                item.screenshots[0].viewport if item.screenshots else None,
            )
            for item in self.pages
        ]
        if len(page_keys) != len(set(page_keys)):
            raise ValueError("duplicate page evidence")
        screenshot_ids = [
            screenshot.screenshot_id
            for page in self.pages
            for screenshot in page.screenshots
        ]
        if len(screenshot_ids) != len(set(screenshot_ids)):
            raise ValueError("duplicate screenshot_id across crawl result")
        object.__setattr__(self, "pages", tuple(self.pages))

    @classmethod
    def succeeded(
        cls,
        *,
        source_url: str,
        pages: tuple[ReferencePageEvidence, ...],
        started_at: datetime,
        completed_at: datetime | None = None,
    ) -> "ReferenceCrawlResult":
        return cls(
            source_url=source_url,
            status="succeeded",
            started_at=started_at,
            completed_at=completed_at or datetime.now(timezone.utc),
            pages=pages,
        )

    @classmethod
    def failed(
        cls,
        *,
        source_url: str,
        failure: CrawlFailure,
        started_at: datetime,
        completed_at: datetime | None = None,
        pages: tuple[ReferencePageEvidence, ...] = (),
        trace: TraceEvidence | None = None,
    ) -> "ReferenceCrawlResult":
        return cls(
            source_url=source_url,
            status="failed",
            started_at=started_at,
            completed_at=completed_at or datetime.now(timezone.utc),
            pages=pages,
            failure=failure,
            trace=trace,
        )

    @classmethod
    def partial(
        cls,
        *,
        source_url: str,
        pages: tuple[ReferencePageEvidence, ...],
        started_at: datetime,
        completed_at: datetime | None = None,
    ) -> "ReferenceCrawlResult":
        return cls(
            source_url=source_url,
            status="partial",
            started_at=started_at,
            completed_at=completed_at or datetime.now(timezone.utc),
            pages=pages,
        )

    def screenshot_bytes(self) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for page in self.pages:
            for screenshot in page.screenshots:
                if screenshot.data is not None:
                    result[screenshot.screenshot_id] = screenshot.data
        return result

    def to_dict(self) -> dict[str, Any]:
        if self.pages and all(
            page.coverage_status == "complete" for page in self.pages
        ):
            coverage_status = "complete"
        elif self.pages:
            coverage_status = "partial"
        else:
            coverage_status = "not_captured"
        return {
            "source_url": public_url(self.source_url),
            "status": self.status,
            "coverage_status": coverage_status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "user_agent": self.user_agent,
            "pages": [page.to_dict() for page in self.pages],
            "failure": self.failure.to_dict() if self.failure else None,
            "trace": self.trace.to_dict() if self.trace else None,
        }
