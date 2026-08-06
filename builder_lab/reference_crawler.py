from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zlib
from collections import Counter
from contextlib import AsyncExitStack
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

from crawlee.storage_clients import MemoryStorageClient
from PIL import Image, UnidentifiedImageError

from .reference_models import (
    CrawlFailure,
    ReferenceCrawlResult,
    ReferencePageEvidence,
    ScreenshotEvidence,
    TraceEvidence,
    public_url,
)


class _RunScopedMemoryStorageClient(MemoryStorageClient):
    """Prevent Crawlee's global storage cache from leaking queues across runs."""

    def __init__(self) -> None:
        super().__init__()
        self._kaigo_run_scope = uuid.uuid4().hex

    def get_storage_client_cache_key(self, configuration: Any) -> Any:
        return (
            super().get_storage_client_cache_key(configuration),
            self._kaigo_run_scope,
        )


KAIGO_RESEARCH_USER_AGENT = "KaigoVisualResearch/1.0 (+https://kaigo.space)"
KAIGO_RESEARCH_ROBOTS_AGENT = "KaigoVisualResearch"
_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.google.internal.",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
_METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.169.123"),
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
_CATEGORY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "services",
        (
            "service",
            "services",
            "price",
            "prices",
            "pricing",
            "услуг",
            "цен",
            "стоим",
            "тариф",
        ),
    ),
    (
        "portfolio",
        (
            "portfolio",
            "project",
            "projects",
            "catalog",
            "work",
            "case",
            "портфол",
            "проект",
            "каталог",
            "работ",
            "кейс",
        ),
    ),
    ("faq", ("faq", "question", "help", "вопрос", "ответ", "помощ")),
    (
        "contacts",
        ("contact", "contacts", "about", "office", "контакт", "о нас", "офис"),
    ),
)


class UnsafeReferenceUrl(ValueError):
    pass


class RobotsDenied(RuntimeError):
    pass


class ReferenceCaptureError(RuntimeError):
    def __init__(self, message: str, *, trace: TraceEvidence | None = None) -> None:
        super().__init__(message)
        self.trace = trace


class FontReadyTimeout(TimeoutError):
    def __init__(
        self,
        message: str,
        *,
        image_error: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.image_error = image_error


@dataclass(frozen=True)
class GuardedUrl:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


Resolver = Callable[[str], Iterable[str]]


def _system_resolver(host: str) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return tuple(dict.fromkeys(item[4][0] for item in records))
    return (str(literal),)


class UrlGuard:
    """Fail-closed SSRF guard for every top-level and subresource URL.

    DNS is re-resolved on every call, including redirects and browser requests.
    Playwright cannot pin a hostname to the checked address, so a DNS change in
    the very small interval between this check and Chromium's connection remains
    a residual TOCTOU limitation. Production isolation must therefore also deny
    private/metadata egress at the container or network boundary.
    """

    def __init__(self, resolver: Resolver | None = None) -> None:
        self._resolver = resolver or _system_resolver

    def validate(self, url: str) -> GuardedUrl:
        raw = url.strip()
        try:
            parsed = urlsplit(raw)
        except ValueError as exc:
            raise UnsafeReferenceUrl("reference URL is malformed") from exc
        if parsed.scheme.lower() not in {"http", "https"}:
            raise UnsafeReferenceUrl("only public http/https URLs are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeReferenceUrl("URL credentials are not allowed")
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            raise UnsafeReferenceUrl("URL host is required")
        if host in _METADATA_HOSTS or host.endswith(".metadata.google.internal"):
            raise UnsafeReferenceUrl("cloud metadata hosts are not allowed")
        try:
            port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        except ValueError as exc:
            raise UnsafeReferenceUrl("URL port is malformed") from exc
        if port not in {80, 443}:
            raise UnsafeReferenceUrl("only ports 80 and 443 are allowed")
        try:
            raw_addresses = tuple(self._resolver(host))
        except Exception as exc:
            raise UnsafeReferenceUrl("reference host could not be resolved") from exc
        if not raw_addresses:
            raise UnsafeReferenceUrl("reference host has no A or AAAA records")
        addresses: list[str] = []
        for raw_address in raw_addresses:
            try:
                address = ipaddress.ip_address(str(raw_address).split("%", 1)[0])
            except ValueError as exc:
                raise UnsafeReferenceUrl("DNS returned an invalid address") from exc
            if (
                not address.is_global
                or address.is_loopback
                or address.is_private
                or address.is_link_local
                or address.is_multicast
                or address.is_unspecified
                or address.is_reserved
                or address in _METADATA_IPS
            ):
                raise UnsafeReferenceUrl("all DNS answers must be public addresses")
            normalized = str(address)
            if normalized not in addresses:
                addresses.append(normalized)
        return GuardedUrl(url=raw, host=host, port=port, addresses=tuple(addresses))

    def validate_redirect(self, url: str) -> GuardedUrl:
        return self.validate(url)


def sanitize_url_for_log(url: str) -> str:
    return public_url(url)


def _sanitize_text(value: str) -> str:
    return re.sub(
        r"https?://[^\s\"'<>]+",
        lambda match: sanitize_url_for_log(match.group(0)),
        value,
    )[:1000]


def _exception_text(exc: BaseException) -> str:
    current = exc
    seen: set[int] = set()
    parts: list[str] = []
    while id(current) not in seen:
        seen.add(id(current))
        message = _sanitize_text(str(current))
        if message and message not in parts:
            parts.append(message)
        next_error = current.__cause__ or current.__context__
        if next_error is None:
            break
        current = next_error
    return " caused by ".join(parts)[:1000] or "unknown crawl failure"


def _origin_url(url: str) -> str:
    parsed = urlsplit(url)
    port = parsed.port
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    suffix = f":{port}" if port is not None and port != default_port else ""
    return urlunsplit(
        (parsed.scheme.lower(), f"{(parsed.hostname or '').lower()}{suffix}", "", "", "")
    )


class GuardedRobotsPolicy:
    """Per-origin robots policy loaded only through the Kaigo URL guard."""

    def __init__(
        self,
        *,
        guard: UrlGuard,
        transport: Any | None = None,
        respect_robots: bool = True,
        timeout_seconds: int = 15,
        max_bytes: int = 256 * 1024,
    ) -> None:
        self.guard = guard
        self.transport = transport
        self.respect_robots = respect_robots
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self._cache: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _load(self, origin: str) -> Any:
        import httpx
        from crawlee._utils.robots import RobotsTxtFile

        current = f"{origin}/robots.txt"
        async with httpx.AsyncClient(
            follow_redirects=False,
            headers={"User-Agent": KAIGO_RESEARCH_USER_AGENT},
            timeout=self.timeout_seconds,
            trust_env=False,
            transport=self.transport,
        ) as client:
            for _ in range(4):
                await asyncio.to_thread(self.guard.validate_redirect, current)
                try:
                    async with client.stream("GET", current) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise RobotsDenied("robots redirect has no location")
                            redirected = urljoin(current, location)
                            await asyncio.to_thread(
                                self.guard.validate_redirect, redirected
                            )
                            if _origin_url(redirected) != origin:
                                raise RobotsDenied(
                                    "cross-origin robots redirects are denied"
                                )
                            current = redirected
                            continue
                        if response.status_code in {404, 410}:
                            return True
                        if response.status_code != 200:
                            raise RobotsDenied(
                                f"robots request failed with HTTP {response.status_code}"
                            )
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self.max_bytes:
                                raise RobotsDenied("robots response exceeds byte cap")
                            chunks.append(chunk)
                        content = b"".join(chunks).decode(
                            response.encoding or "utf-8", errors="replace"
                        )
                        return await RobotsTxtFile.from_content(current, content)
                except RobotsDenied:
                    raise
                except httpx.HTTPError as exc:
                    raise RobotsDenied("robots request failed closed") from exc
        raise RobotsDenied("robots redirect limit exceeded")

    async def _policy(self, url: str) -> Any:
        await asyncio.to_thread(self.guard.validate, url)
        if not self.respect_robots:
            return True
        origin = _origin_url(url)
        if origin in self._cache:
            return self._cache[origin]
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._cache:
                self._cache[origin] = await self._load(origin)
        return self._cache[origin]

    async def is_allowed(self, url: str) -> bool:
        policy = await self._policy(url)
        if policy is True:
            return True
        return bool(policy.is_allowed(url, user_agent=KAIGO_RESEARCH_ROBOTS_AGENT))

    async def require_allowed(self, url: str) -> None:
        if not await self.is_allowed(url):
            raise RobotsDenied(f"robots policy denies {sanitize_url_for_log(url)}")


async def _canonicalize_document_url(
    url: str,
    *,
    guard: UrlGuard,
    robots: GuardedRobotsPolicy | None,
    timeout_seconds: int,
    max_redirects: int = 8,
) -> str:
    """Resolve only HEAD redirects; any later GET redirect is fail-closed."""
    import httpx

    current = _without_fragment(url)
    allowed_origin = _origin_key(current)
    seen: set[str] = set()
    async with httpx.AsyncClient(
        follow_redirects=False,
        headers={"User-Agent": KAIGO_RESEARCH_USER_AGENT},
        timeout=timeout_seconds,
        trust_env=False,
    ) as client:
        for _hop in range(max_redirects + 1):
            if current in seen:
                raise ReferenceCaptureError("document redirect loop blocked")
            seen.add(current)
            await asyncio.to_thread(guard.validate_redirect, current)
            if robots is not None:
                await robots.require_allowed(current)
            try:
                response = await client.head(current)
            except httpx.HTTPError as exc:
                raise ReferenceCaptureError(
                    "document canonicalization failed closed"
                ) from exc
            if not response.is_redirect:
                return current
            location = response.headers.get("location")
            if not location:
                raise ReferenceCaptureError(
                    "document redirect without location blocked"
                )
            redirected = _without_fragment(urljoin(current, location))
            await asyncio.to_thread(guard.validate_redirect, redirected)
            if _origin_key(redirected) != allowed_origin:
                raise ReferenceCaptureError("cross-origin document redirect blocked")
            current = redirected
    raise ReferenceCaptureError("document redirect hop limit exceeded")


@dataclass(frozen=True)
class LinkCandidate:
    url: str
    text: str = ""
    source: str = "navigation"


@dataclass(frozen=True)
class SelectedReferencePage:
    url: str
    category: str
    source: str


def _origin_key(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower().rstrip("."),
        parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
    )


def _without_fragment(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _candidate_category(candidate: LinkCandidate) -> str:
    haystack = f"{urlsplit(candidate.url).path} {candidate.text}".casefold()
    for category, patterns in _CATEGORY_PATTERNS:
        if any(pattern in haystack for pattern in patterns):
            return category
    return "other"


def select_reference_pages(
    home_url: str,
    candidates: Sequence[LinkCandidate],
    *,
    max_pages: int = 5,
) -> tuple[SelectedReferencePage, ...]:
    if not 1 <= max_pages <= 5:
        raise ValueError("max_pages must be between 1 and 5")
    normalized_home = _without_fragment(home_url)
    origin = _origin_key(normalized_home)
    unique: dict[str, LinkCandidate] = {}
    for candidate in candidates:
        try:
            absolute = _without_fragment(urljoin(normalized_home, candidate.url))
            parsed = urlsplit(absolute)
            same_origin = _origin_key(absolute) == origin
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not same_origin:
            continue
        if parsed.username is not None or parsed.password is not None:
            continue
        unique.setdefault(
            absolute,
            LinkCandidate(absolute, candidate.text.strip()[:300], candidate.source),
        )
    unique.pop(normalized_home, None)
    selected = [SelectedReferencePage(normalized_home, "home", "explicit")]
    used: set[str] = {normalized_home}
    for category, _patterns in _CATEGORY_PATTERNS:
        match = next(
            (
                item
                for item in unique.values()
                if item.url not in used and _candidate_category(item) == category
            ),
            None,
        )
        if match is not None and len(selected) < max_pages:
            selected.append(SelectedReferencePage(match.url, category, match.source))
            used.add(match.url)
    for item in unique.values():
        if len(selected) >= max_pages:
            break
        if item.url not in used:
            selected.append(SelectedReferencePage(item.url, "other", item.source))
            used.add(item.url)
    return tuple(selected)


@dataclass(frozen=True)
class ReferenceCrawlLimits:
    max_pages: int = 5
    max_depth: int = 1
    total_timeout_seconds: int = 600
    page_timeout_seconds: int = 45
    max_total_bytes: int = 100 * 1024 * 1024
    max_page_bytes: int = 25 * 1024 * 1024
    max_retries: int = 1
    max_scroll_steps: int = 40
    scroll_delay_ms: int = 750
    warmup_ms: int = 5000
    final_settle_ms: int = 1500
    max_scroll_height: int = 50_000
    trace_ttl_seconds: int = 3600
    concurrency_per_host: int = 1
    respect_robots: bool = True

    def __post_init__(self) -> None:
        ranges = (
            ("max_pages", self.max_pages, 1, 5),
            ("max_depth", self.max_depth, 0, 2),
            ("total_timeout_seconds", self.total_timeout_seconds, 15, 600),
            ("page_timeout_seconds", self.page_timeout_seconds, 5, 120),
            ("max_total_bytes", self.max_total_bytes, 1024 * 1024, 100 * 1024 * 1024),
            ("max_page_bytes", self.max_page_bytes, 256 * 1024, 25 * 1024 * 1024),
            ("max_retries", self.max_retries, 0, 2),
            ("max_scroll_steps", self.max_scroll_steps, 1, 60),
            ("scroll_delay_ms", self.scroll_delay_ms, 600, 1200),
            ("warmup_ms", self.warmup_ms, 1000, 15_000),
            ("final_settle_ms", self.final_settle_ms, 500, 5000),
            ("max_scroll_height", self.max_scroll_height, 5000, 100_000),
            ("trace_ttl_seconds", self.trace_ttl_seconds, 300, 86_400),
        )
        for name, value, minimum, maximum in ranges:
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
        if self.max_page_bytes > self.max_total_bytes:
            raise ValueError("max_page_bytes cannot exceed max_total_bytes")
        if self.concurrency_per_host != 1:
            raise ValueError("concurrency_per_host must remain 1")


def _request_handler_timeout(limits: ReferenceCrawlLimits) -> timedelta:
    return timedelta(seconds=limits.total_timeout_seconds)


@dataclass(frozen=True)
class CaptureSettings:
    width: int
    height: int
    warmup_ms: int = 5000
    scroll_delay_ms: int = 750
    final_settle_ms: int = 1500
    max_scroll_steps: int = 40
    max_scroll_height: int = 50_000
    page_timeout_seconds: int = 45
    max_page_bytes: int = 25 * 1024 * 1024

    def __post_init__(self) -> None:
        if not 320 <= self.width <= 2560 or not 480 <= self.height <= 1600:
            raise ValueError("capture viewport is outside the supported range")
        ReferenceCrawlLimits(
            max_scroll_steps=self.max_scroll_steps,
            scroll_delay_ms=self.scroll_delay_ms,
            warmup_ms=self.warmup_ms,
            final_settle_ms=self.final_settle_ms,
            max_scroll_height=self.max_scroll_height,
            page_timeout_seconds=self.page_timeout_seconds,
            max_page_bytes=self.max_page_bytes,
            max_total_bytes=max(self.max_page_bytes, 100 * 1024 * 1024),
        )


class CrawlByteBudget:
    """One monotonic byte ledger shared by retries, pages, and viewports."""

    def __init__(self, max_bytes: int) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = max_bytes
        self.used_bytes = 0
        self._charges: set[str] = set()

    def consume(self, amount: int, *, source: str, charge_id: str | None = None) -> None:
        if amount < 0:
            raise ValueError("byte charge must not be negative")
        if charge_id is not None and charge_id in self._charges:
            return
        if charge_id is not None:
            self._charges.add(charge_id)
        self.used_bytes += amount
        if self.used_bytes > self.max_bytes:
            raise ReferenceCaptureError(
                f"crawl byte limit exceeded while accounting {source}"
            )

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.max_bytes - self.used_bytes)


class CrawlAccumulator:
    """Idempotent crawl state committed only after child enqueue succeeds."""

    def __init__(self, *, max_total_bytes: int, selected_urls: set[str]) -> None:
        self.max_total_bytes = max_total_bytes
        self._selected_urls = set(selected_urls)
        self._pages: dict[tuple[str, str], ReferencePageEvidence] = {}
        self._total_bytes = 0

    @staticmethod
    def _key(evidence: ReferencePageEvidence) -> tuple[str, str]:
        viewport = (
            evidence.screenshots[0].viewport if evidence.screenshots else "not_captured"
        )
        return evidence.page_id, viewport

    @property
    def pages(self) -> tuple[ReferencePageEvidence, ...]:
        return tuple(self._pages.values())

    @property
    def selected_urls(self) -> frozenset[str]:
        return frozenset(self._selected_urls)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    async def commit(
        self,
        *,
        evidence: ReferencePageEvidence,
        child_urls: Sequence[str],
        enqueue: Callable[[], Any],
    ) -> None:
        key = self._key(evidence)
        if key in self._pages:
            return
        projected = self._total_bytes + evidence.transferred_bytes
        if projected > self.max_total_bytes:
            raise ReferenceCaptureError("crawl byte limit exceeded")
        result = enqueue()
        if hasattr(result, "__await__"):
            await result
        self._pages[key] = evidence
        self._total_bytes = projected
        self._selected_urls.update(child_urls)

    async def commit_partial(self, evidence: ReferencePageEvidence) -> None:
        async def no_enqueue() -> None:
            return None

        await self.commit(evidence=evidence, child_urls=(), enqueue=no_enqueue)


@dataclass
class CaptureTelemetry:
    max_page_bytes: int
    total_budget: CrawlByteBudget | None = None
    attempt_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    console_failures: list[str] = field(default_factory=list)
    page_failures: list[str] = field(default_factory=list)
    request_failures: list[str] = field(default_factory=list)
    policy_blocks: list[str] = field(default_factory=list)
    response_bytes: int = 0
    decoded_response_bytes: int = 0
    declared_oversize: asyncio.Event = field(default_factory=asyncio.Event)
    transfer_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    _policy_labels: set[str] = field(default_factory=set)
    _attached_context: bool = False
    _attached_pages: set[int] = field(default_factory=set)
    accounted: bool = False
    _charge_sequence: int = 0
    byte_limit_error: str | None = None
    native_transport: bool = False
    native_validated_origins: set[tuple[str, str, int]] = field(
        default_factory=set,
        repr=False,
    )
    native_validation_locks: dict[tuple[str, str, int], asyncio.Lock] = field(
        default_factory=dict,
        repr=False,
    )
    http_client: Any | None = field(default=None, repr=False)

    @staticmethod
    def _append_unique(target: list[str], value: str) -> None:
        if value not in target and len(target) < 100:
            target.append(value[:1000])

    def record_policy_block(self, reason: str, url: str) -> None:
        label = sanitize_url_for_log(url)
        self._policy_labels.add(label)
        self._append_unique(self.policy_blocks, f"{reason}: {label}")

    def record_network_bytes(self, amount: int) -> None:
        self.response_bytes += amount
        self._charge_sequence += 1
        if self.total_budget is not None:
            self.total_budget.consume(
                amount,
                source="network response",
                charge_id=f"{self.attempt_id}:network:{self._charge_sequence}",
            )
        self.raise_if_oversize()

    def record_screenshot_bytes(self, screenshot_id: str, amount: int) -> None:
        if self.total_budget is not None:
            self.total_budget.consume(
                amount,
                source="screenshot",
                charge_id=f"{self.attempt_id}:screenshot:{screenshot_id}",
            )

    @property
    def decoded_remaining_bytes(self) -> int:
        return max(0, self.max_page_bytes - self.decoded_response_bytes)

    def record_decoded_bytes(self, amount: int) -> None:
        self.decoded_response_bytes += amount
        if self.decoded_response_bytes > self.max_page_bytes:
            raise ReferenceCaptureError("decoded page byte limit exceeded")

    def attach_context(self, context: Any) -> None:
        if self._attached_context:
            return
        self._attached_context = True

        def on_response(response: Any) -> None:
            try:
                size = int(response.headers.get("content-length", "0"))
            except (TypeError, ValueError):
                size = 0
            if size > self.max_page_bytes:
                self.declared_oversize.set()
            if self.native_transport and size > 0:
                try:
                    self.record_network_bytes(size)
                except ReferenceCaptureError:
                    self.declared_oversize.set()

        def on_request_failed(request: Any) -> None:
            label = sanitize_url_for_log(request.url)
            if label in self._policy_labels:
                return
            failure = _sanitize_text(str(request.failure or "request failed"))
            self._append_unique(
                self.request_failures, f"{request.method} {label}: {failure}"
            )

        context.on("response", on_response)
        context.on("requestfailed", on_request_failed)

    def attach_page(self, page: Any) -> None:
        key = id(page)
        if key in self._attached_pages:
            return
        self._attached_pages.add(key)

        def on_console(message: Any) -> None:
            if message.type in {"error", "warning"}:
                self._append_unique(
                    self.console_failures,
                    _sanitize_text(f"{message.type}: {message.text}"),
                )

        def on_page_error(error: Exception) -> None:
            self._append_unique(self.page_failures, _sanitize_text(str(error)))

        page.on("console", on_console)
        page.on("pageerror", on_page_error)

    async def settle_transfers(self) -> None:
        if self.transfer_tasks:
            await asyncio.gather(*tuple(self.transfer_tasks), return_exceptions=True)

    def raise_if_oversize(self) -> None:
        if self.declared_oversize.is_set() or self.response_bytes > self.max_page_bytes:
            raise ReferenceCaptureError("page byte limit exceeded")

    async def close_http_client(self) -> None:
        client = self.http_client
        self.http_client = None
        if client is not None and not client.is_closed:
            await client.aclose()

    async def validate_native_destination(self, guard: UrlGuard, url: str) -> None:
        origin = _origin_key(url)
        if origin in self.native_validated_origins:
            return
        lock = self.native_validation_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin in self.native_validated_origins:
                return
            await asyncio.to_thread(guard.validate_redirect, url)
            self.native_validated_origins.add(origin)


@dataclass(frozen=True)
class _BufferedHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


_HOP_BY_HOP_REQUEST_HEADERS = frozenset(
    {
        "accept-encoding",
        "connection",
        "content-length",
        "cookie",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_SENSITIVE_CROSS_ORIGIN_HEADERS = frozenset(
    {"authorization", "cookie", "proxy-authorization", "referer"}
)
_TRACKING_HOSTS = frozenset(
    {
        "analytics.google.com",
        "connect.facebook.net",
        "googleads.g.doubleclick.net",
        "mc.yandex.com",
        "mc.yandex.ru",
        "px.ads.linkedin.com",
        "snap.licdn.com",
        "stats.g.doubleclick.net",
        "top-fwz1.mail.ru",
        "top.mail.ru",
        "www.google-analytics.com",
        "www.googletagmanager.com",
    }
)


def _host_matches(host: str, expected: str) -> bool:
    return host == expected or host.endswith(f".{expected}")


def _is_non_content_tracking_request(url: str, resource_type: str) -> bool:
    if resource_type == "document":
        return False
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    path = parsed.path.casefold()
    if any(_host_matches(host, expected) for expected in _TRACKING_HOSTS):
        return True
    if _host_matches(host, "googletagmanager.com"):
        return True
    if _host_matches(host, "google-analytics.com"):
        return True
    if host in {"vk.com", "www.vk.com"} and path.startswith("/rtrg"):
        return True
    if host in {"www.facebook.com", "facebook.com"} and path.startswith("/tr"):
        return True
    return False


def _new_streaming_client() -> Any:
    import httpx

    return httpx.AsyncClient(
        follow_redirects=False,
        timeout=45,
        trust_env=False,
    )


async def _bounded_stream_fetch(
    client: Any,
    *,
    method: str,
    url: str,
    request_headers: Mapping[str, str],
    telemetry: CaptureTelemetry,
) -> _BufferedHttpResponse:
    """Fetch one hop while bounding memory and wire bytes before buffering."""
    headers = {
        name: value
        for name, value in request_headers.items()
        if name.casefold() not in _HOP_BY_HOP_REQUEST_HEADERS
    }
    # Buffering compressed bytes and asking route.fulfill to decode them is not
    # reliable, while decoding an attacker-controlled compression bomb would
    # defeat the hard memory cap. Request an identity representation and fail
    # closed if the origin ignores it.
    headers["Accept-Encoding"] = "identity"
    async with client.stream(method, url, headers=headers) as response:
        response_headers = dict(response.headers.items())
        header_bytes = sum(
            len(str(name).encode("utf-8"))
            + len(str(value).encode("utf-8"))
            + 4
            for name, value in response.headers.multi_items()
        )
        telemetry.record_network_bytes(header_bytes)
        declared = int(response.headers.get("content-length", "0") or "0")
        page_remaining = max(0, telemetry.max_page_bytes - telemetry.response_bytes)
        total_remaining = (
            telemetry.total_budget.remaining_bytes
            if telemetry.total_budget is not None
            else page_remaining
        )
        if declared > min(page_remaining, total_remaining):
            telemetry.declared_oversize.set()
            raise ReferenceCaptureError("page byte limit exceeded")
        if method == "HEAD" or 300 <= response.status_code < 400:
            return _BufferedHttpResponse(
                status=response.status_code,
                headers=response_headers,
                body=b"",
            )
        content_encoding = response.headers.get("content-encoding", "identity")
        normalized_encoding = content_encoding.casefold().strip()
        if normalized_encoding in {"", "identity"}:
            decoder = None
        elif normalized_encoding == "gzip":
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif normalized_encoding == "deflate":
            decoder = zlib.decompressobj()
        else:
            raise ValueError("unsupported content encoding")
        body = bytearray()
        async for chunk in response.aiter_raw(chunk_size=64 * 1024):
            telemetry.record_network_bytes(len(chunk))
            if decoder is None:
                telemetry.record_decoded_bytes(len(chunk))
                body.extend(chunk)
                continue
            pending = chunk
            while pending:
                remaining = telemetry.decoded_remaining_bytes
                if remaining <= 0:
                    raise ReferenceCaptureError("decoded page byte limit exceeded")
                decoded = decoder.decompress(pending, remaining + 1)
                if len(decoded) > remaining:
                    raise ReferenceCaptureError("decoded page byte limit exceeded")
                telemetry.record_decoded_bytes(len(decoded))
                body.extend(decoded)
                pending = decoder.unconsumed_tail
            if decoder.unused_data:
                raise ValueError("concatenated encoded response is unsupported")
        if decoder is not None:
            remaining = telemetry.decoded_remaining_bytes
            decoded = decoder.flush(max(1, remaining + 1))
            if len(decoded) > remaining:
                raise ReferenceCaptureError("decoded page byte limit exceeded")
            telemetry.record_decoded_bytes(len(decoded))
            body.extend(decoded)
        return _BufferedHttpResponse(
            status=response.status_code,
            headers=response_headers,
            body=bytes(body),
        )


def _sync_browser_cookies(
    client: Any, url: str, request_headers: Mapping[str, str]
) -> None:
    raw_cookie = next(
        (
            value
            for name, value in request_headers.items()
            if name.casefold() == "cookie"
        ),
        "",
    )
    host = urlsplit(url).hostname
    if not raw_cookie or not host:
        return
    parsed = SimpleCookie()
    try:
        parsed.load(raw_cookie)
    except Exception:
        return
    for name, morsel in parsed.items():
        client.cookies.set(name, morsel.value, domain=host, path="/")


def _redirect_chain(request: Any) -> tuple[str, ...]:
    chain: list[str] = []
    previous = request.redirected_from
    while previous is not None and len(chain) <= 9:
        chain.append(_without_fragment(previous.url))
        previous = previous.redirected_from
    chain.reverse()
    return tuple(chain)


def _native_browser_transport_enabled() -> bool:
    return os.getenv("KAIGO_REFERENCE_NATIVE_TRANSPORT", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _viewport_capture_concurrency() -> int:
    configured = os.getenv("KAIGO_REFERENCE_VIEWPORT_CONCURRENCY", "2").strip()
    return 1 if configured == "1" else 2


async def _guarded_native_context_route(
    route: Any,
    request: Any,
    *,
    guard: UrlGuard | None,
    robots: GuardedRobotsPolicy | None,
    allowed_document_origin: tuple[str, str, int],
    primary_page: dict[str, Any],
    telemetry: CaptureTelemetry,
) -> None:
    """Validate browser traffic while leaving transfer/CORS to Chromium.

    This mode is opt-in because production must pair it with the immutable
    builder-network egress guard. It avoids copying every page resource through
    Python while retaining per-request URL, method, document-origin, popup,
    robots, redirect-loop, and analytics policy checks.
    """

    async def block(reason: str, url: str | None = None) -> None:
        telemetry.record_policy_block(reason, url or request.url)
        await route.abort("blockedbyclient")

    parsed = urlsplit(request.url)
    if parsed.scheme not in {"http", "https"}:
        await block("non-http scheme blocked")
        return
    if parsed.username is not None or parsed.password is not None:
        await block("URL credentials blocked")
        return
    if request.method.upper() not in {"GET", "HEAD"}:
        await block("non-read request blocked")
        return
    if _is_non_content_tracking_request(request.url, request.resource_type):
        await block("analytics blocked")
        return
    if request.resource_type in {"media", "eventsource"}:
        await block(f"{request.resource_type} blocked")
        return

    is_document = request.resource_type == "document"
    if is_document:
        try:
            request_origin = _origin_key(request.url)
        except ValueError:
            await block("malformed document URL blocked")
            return
        try:
            request_frame = request.frame
            request_page = request_frame.page
            is_main_frame = request_frame == request_page.main_frame
        except Exception:
            request_page = None
            is_main_frame = True
        expected_page = primary_page.get("page")
        if expected_page is not None and request_page is not expected_page:
            await block("popup document blocked")
            return
        if request_origin != allowed_document_origin:
            await block(
                "cross-origin document blocked"
                if is_main_frame
                else "cross-origin subframe document blocked"
            )
            return
        if robots is not None:
            try:
                await robots.require_allowed(request.url)
            except (RobotsDenied, UnsafeReferenceUrl):
                await block("robots policy blocked document")
                return

    current = _without_fragment(request.url)
    chain = _redirect_chain(request)
    if len(chain) >= 8:
        await block("resource redirect hop limit blocked", current)
        return
    if current in chain:
        await block("resource redirect loop blocked", current)
        return
    if guard is not None:
        try:
            await telemetry.validate_native_destination(guard, current)
        except UnsafeReferenceUrl:
            await block("unsafe destination blocked", current)
            return
    await route.continue_()


async def _guarded_context_route(
    route: Any,
    request: Any,
    *,
    guard: UrlGuard | None,
    robots: GuardedRobotsPolicy | None,
    allowed_document_origin: tuple[str, str, int],
    primary_page: dict[str, Any],
    telemetry: CaptureTelemetry,
) -> None:
    async def block(reason: str, url: str | None = None) -> None:
        telemetry.record_policy_block(reason, url or request.url)
        await route.abort("blockedbyclient")

    parsed = urlsplit(request.url)
    if parsed.scheme not in {"http", "https"}:
        await block("non-http scheme blocked")
        return
    if request.method.upper() not in {"GET", "HEAD"}:
        await block("non-read request blocked")
        return
    if _is_non_content_tracking_request(request.url, request.resource_type):
        await block("analytics blocked")
        return
    if request.resource_type in {"media", "eventsource"}:
        await block(f"{request.resource_type} blocked")
        return
    is_document = request.resource_type == "document"
    if is_document:
        try:
            request_origin = _origin_key(request.url)
        except ValueError:
            await block("malformed document URL blocked")
            return
        try:
            request_frame = request.frame
            request_page = request_frame.page
            is_main_frame = request_frame == request_page.main_frame
        except Exception:
            request_page = None
            is_main_frame = True
        expected_page = primary_page.get("page")
        if expected_page is not None and request_page is not expected_page:
            await block("popup document blocked")
            return
        if request_origin != allowed_document_origin:
            await block(
                "cross-origin document blocked"
                if is_main_frame
                else "cross-origin subframe document blocked"
            )
            return
        if robots is not None:
            try:
                await robots.require_allowed(request.url)
            except (RobotsDenied, UnsafeReferenceUrl):
                await block("robots policy blocked document")
                return
    current = _without_fragment(request.url)
    chain = _redirect_chain(request)
    if len(chain) >= 8:
        await block("resource redirect hop limit blocked", current)
        return
    if current in chain:
        await block("resource redirect loop blocked", current)
        return
    request_headers = await request.all_headers()
    _sync_browser_cookies(telemetry.http_client, current, request_headers)
    seen = set(chain)
    original_is_cross_origin = _origin_key(current) != allowed_document_origin
    async with AsyncExitStack() as isolated_clients:
        current_client = telemetry.http_client
        for _hop in range(max(0, 9 - len(chain))):
            if current in seen:
                await block("resource redirect loop blocked", current)
                return
            seen.add(current)
            if guard is not None:
                try:
                    await asyncio.to_thread(guard.validate_redirect, current)
                except UnsafeReferenceUrl:
                    await block("unsafe destination blocked", current)
                    return
            try:
                response = await _bounded_stream_fetch(
                    current_client,
                    method=request.method.upper(),
                    url=current,
                    request_headers=request_headers,
                    telemetry=telemetry,
                )
            except ReferenceCaptureError as exc:
                telemetry.byte_limit_error = _sanitize_text(str(exc))
                await block("response byte limit blocked", current)
                return
            except Exception:
                await block("resource fetch failed closed", current)
                return
            response_headers = {
                name: value
                for name, value in response.headers.items()
                if name.casefold()
                not in {
                    "connection",
                    "content-encoding",
                    "content-length",
                    "transfer-encoding",
                }
            }
            if not 300 <= response.status < 400:
                await route.fulfill(
                    status=response.status,
                    headers=response_headers,
                    body=response.body,
                )
                return
            location = response.headers.get("location")
            if not location:
                await block("resource redirect without location blocked", current)
                return
            next_url = _without_fragment(urljoin(current, location))
            if guard is not None:
                try:
                    await asyncio.to_thread(guard.validate_redirect, next_url)
                except UnsafeReferenceUrl:
                    await block("unsafe destination blocked", next_url)
                    return
            try:
                current_origin = _origin_key(current)
                next_origin = _origin_key(next_url)
            except ValueError:
                await block("malformed redirect blocked", next_url)
                return
            if is_document:
                if next_origin != allowed_document_origin:
                    await block("cross-origin document redirect blocked", next_url)
                else:
                    await block("unexpected document redirect blocked", next_url)
                return
            if next_origin != current_origin:
                source_origin = urlunsplit(
                    (urlsplit(current).scheme, urlsplit(current).netloc, "", "", "")
                )
                destination_origin = urlunsplit(
                    (
                        urlsplit(next_url).scheme,
                        urlsplit(next_url).netloc,
                        "",
                        "",
                        "",
                    )
                )
                if not original_is_cross_origin:
                    # A same-origin browser URL fulfilled with cross-origin bytes
                    # would become script-readable and bypass native CORS.
                    await block(
                        "cross-origin resource redirect blocked "
                        f"({source_origin} -> {destination_origin})",
                        next_url,
                    )
                    return
                # The browser URL was already cross-origin relative to the page,
                # so fulfilling final bytes cannot gain same-origin privilege.
                # A fresh client and stripped headers prevent credential leakage.
                request_headers = {
                    name: value
                    for name, value in request_headers.items()
                    if name.casefold() not in _SENSITIVE_CROSS_ORIGIN_HEADERS
                }
                current_client = await isolated_clients.enter_async_context(
                    _new_streaming_client()
                )
            current = next_url
        await block("resource redirect hop limit blocked", current)


async def _install_context_policy(
    context: Any,
    *,
    primary_page: dict[str, Any],
    guard: UrlGuard | None,
    robots: GuardedRobotsPolicy | None,
    allowed_document_origin: tuple[str, str, int],
    telemetry: CaptureTelemetry,
) -> None:
    native_transport = _native_browser_transport_enabled()
    telemetry.native_transport = native_transport
    telemetry.attach_context(context)
    telemetry.http_client = None if native_transport else _new_streaming_client()

    def close_transport(*_args: Any) -> None:
        try:
            asyncio.get_running_loop().create_task(telemetry.close_http_client())
        except RuntimeError:
            pass

    context.on("close", close_transport)
    route_handler = (
        _guarded_native_context_route
        if native_transport
        else _guarded_context_route
    )
    await context.route(
        "**/*",
        lambda route, request: route_handler(
            route,
            request,
            guard=guard,
            robots=robots,
            allowed_document_origin=allowed_document_origin,
            primary_page=primary_page,
            telemetry=telemetry,
        ),
    )
    await context.route_web_socket(
        "**/*",
        lambda websocket: (
            telemetry.record_policy_block("websocket blocked", websocket.url),
            websocket.close(code=1008, reason="blocked"),
        )[1],
    )

    async def close_popup(page: Any) -> None:
        if primary_page.get("page") is None:
            primary_page["page"] = page
            return
        if page is not primary_page.get("page"):
            telemetry.record_policy_block("popup page closed", page.url)
            await page.close()

    context.on("page", close_popup)


def browser_unavailable_reason() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return f"Python package is not installed ({exc})"
    try:
        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            if not executable.exists():
                return f"Chromium executable is missing at {executable}"
    except Exception as exc:  # pragma: no cover - platform-specific startup errors
        return str(exc)
    return None


async def _wait_for_fonts_and_viewport_images(page: Any, timeout_ms: int) -> None:
    async def wait_for_fonts() -> None:
        outcome = await page.evaluate(
            """(timeoutMs) => new Promise((resolve, reject) => {
              let finished = false;
              const timer = setTimeout(() => {
                finished = true;
                resolve('timeout');
              }, timeoutMs);
              const ready = document.fonts && document.fonts.ready
                ? document.fonts.ready
                : Promise.resolve();
              Promise.resolve(ready).then(() => {
                if (finished) return;
                finished = true;
                clearTimeout(timer);
                resolve('ready');
              }, (error) => {
                if (finished) return;
                finished = true;
                clearTimeout(timer);
                reject(error);
              });
            })""",
            timeout_ms,
        )
        if outcome == "timeout":
            raise FontReadyTimeout("document fonts ready timed out")

    async def wait_for_images() -> None:
        await page.wait_for_function(
            """() => {
              const images = [...document.images].slice(0, 500).filter((img) => {
                const r = img.getBoundingClientRect();
                const s = getComputedStyle(img);
                const intersectsViewport =
                  r.bottom >= -200 && r.top <= innerHeight + 200 &&
                  r.right >= -200 && r.left <= innerWidth + 200;
                return intersectsViewport &&
                  r.width > 1 && r.height > 1 &&
                  s.display !== 'none' && s.visibility !== 'hidden';
              });
              return images.every((img) => {
                const source =
                  img.currentSrc ||
                  img.getAttribute('src') ||
                  img.getAttribute('srcset');
                return !source ||
                  (img.naturalWidth > 0 && img.naturalHeight > 0) ||
                  img.complete;
              });
            }""",
            timeout=timeout_ms,
        )

    font_result, image_result = await asyncio.gather(
        wait_for_fonts(),
        wait_for_images(),
        return_exceptions=True,
    )
    for result in (font_result, image_result):
        if isinstance(result, BaseException) and not isinstance(result, Exception):
            raise result
    if isinstance(font_result, BaseException):
        if isinstance(font_result, FontReadyTimeout) and isinstance(
            image_result,
            BaseException,
        ):
            font_result.image_error = image_result
        raise font_result
    if isinstance(image_result, BaseException):
        raise image_result


async def _settle_viewport_assets_nonfatal(
    page: Any,
    *,
    timeout_ms: int,
    skipped_reasons: list[str],
    image_timeout_reason: str | None,
) -> None:
    def resolved_image_timeout_reason(error: BaseException) -> str | None:
        if image_timeout_reason is not None:
            return image_timeout_reason
        if isinstance(error, TimeoutError) or (
            type(error).__name__ == "TimeoutError"
            and type(error).__module__.startswith("playwright.")
        ):
            return "initial_viewport_image_settle_timeout"
        return None

    try:
        await _wait_for_fonts_and_viewport_images(page, timeout_ms)
    except FontReadyTimeout as exc:
        if "font_ready_timeout" not in skipped_reasons:
            skipped_reasons.append("font_ready_timeout")
        if exc.image_error is not None:
            timeout_reason = resolved_image_timeout_reason(exc.image_error)
            if timeout_reason is None:
                raise exc.image_error
            if timeout_reason not in skipped_reasons:
                skipped_reasons.append(timeout_reason)
    except Exception as exc:
        timeout_reason = resolved_image_timeout_reason(exc)
        if timeout_reason is None:
            raise
        if timeout_reason not in skipped_reasons:
            skipped_reasons.append(timeout_reason)


async def _wait_for_visual_quiet(
    page: Any,
    *,
    minimum_ms: int = 600,
    quiet_ms: int = 450,
    maximum_ms: int = 5000,
) -> Mapping[str, Any]:
    return await page.evaluate(
        """({minimumMs, quietMs, maximumMs}) => new Promise(resolve => {
          const started = performance.now();
          let lastMutation = started;
          let stableSamples = 0;
          let previous = '';
          const observer = new MutationObserver(() => { lastMutation = performance.now(); });
          observer.observe(document.documentElement, {
            attributes:true, childList:true, characterData:true, subtree:true
          });
          const viewportImages = () => [...document.images].slice(0,500).filter(img => {
            const rect = img.getBoundingClientRect();
            const style = getComputedStyle(img);
            return rect.bottom >= -200 && rect.top <= innerHeight + 200
              && rect.right >= -200 && rect.left <= innerWidth + 200
              && rect.width > 1 && rect.height > 1
              && style.display !== 'none' && style.visibility !== 'hidden';
          });
          const imageReady = (img) => {
            const source = img.currentSrc
              || img.getAttribute('src')
              || img.getAttribute('srcset');
            return !source
              || (img.naturalWidth > 0 && img.naturalHeight > 0)
              || img.complete;
          };
          const signature = () => {
            const root = document.scrollingElement || document.documentElement;
            const pendingImages = viewportImages().filter(img => !imageReady(img)).length;
            const sample = [...document.querySelectorAll('body *')].slice(0,1200)
              .map(node => {
                const rect = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                return [node.tagName, Math.round(rect.x), Math.round(rect.y),
                  Math.round(rect.width), Math.round(rect.height), style.display,
                  style.visibility, style.opacity].join(':');
              }).join('|');
            return [
              document.readyState,
              document.fonts?.status || 'unsupported',
              pendingImages,
              root.scrollWidth, root.scrollHeight,
              sample
            ].join('\\n');
          };
          const tick = () => {
            const now = performance.now();
            const current = signature();
            stableSamples = current === previous ? stableSamples + 1 : 0;
            previous = current;
            const elapsed = now - started;
            const quietFor = now - lastMutation;
            const ready = document.readyState === 'complete'
              && (!document.fonts || document.fonts.status === 'loaded')
              && viewportImages().every(imageReady);
            if (
              elapsed >= minimumMs
              && quietFor >= quietMs
              && stableSamples >= 3
              && ready
            ) {
              observer.disconnect();
              resolve({settled:true, elapsedMs:Math.round(elapsed),
                quietMs:Math.round(quietFor), stableLayoutSamples:stableSamples});
              return;
            }
            if (elapsed >= maximumMs) {
              observer.disconnect();
              resolve({settled:false, elapsedMs:Math.round(elapsed),
                quietMs:Math.round(quietFor), stableLayoutSamples:stableSamples});
              return;
            }
            setTimeout(tick, 100);
          };
          tick();
        })""",
        {
            "minimumMs": minimum_ms,
            "quietMs": quiet_ms,
            "maximumMs": maximum_ms,
        },
    )


_SCROLL_STATE_SCRIPT = r"""() => {
  const root = document.scrollingElement || document.documentElement;
  const nodes = [...document.querySelectorAll('body *')].slice(0, 5000);
  const candidates = nodes.filter((el) => {
    const s = getComputedStyle(el);
    return /(auto|scroll|overlay)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 80;
  }).map((el) => ({
    el,
    score: Math.max(0, el.clientWidth * el.clientHeight) * (el.scrollHeight - el.clientHeight)
  })).sort((a,b) => b.score-a.score);
  const scroller = candidates[0]?.el || root;
  if (!scroller.dataset.kaigoScrollId) scroller.dataset.kaigoScrollId = 'active';
  const transformNodes = nodes.filter((el) => {
    const t = getComputedStyle(el).transform;
    return t && t !== 'none';
  }).slice(0, 40);
  const rawRect = scroller === root
    ? {left: 0, top: 0, right: innerWidth, bottom: innerHeight}
    : scroller.getBoundingClientRect();
  const rect = {
    x: Math.max(0, rawRect.left),
    y: Math.max(0, rawRect.top),
    width: Math.max(1, Math.min(innerWidth, rawRect.right) - Math.max(0, rawRect.left)),
    height: Math.max(1, Math.min(innerHeight, rawRect.bottom) - Math.max(0, rawRect.top))
  };
  const visible = [];
  for (let y = 40; y < innerHeight; y += Math.max(80, Math.floor(innerHeight / 8))) {
    for (const el of document.elementsFromPoint(innerWidth / 2, y)) {
      const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
      if (text && text.length < 180 && !visible.includes(text)) visible.push(text);
      if (visible.length >= 20) break;
    }
  }
  const rootOverflow = `${getComputedStyle(document.documentElement).overflowY}|${getComputedStyle(document.body).overflowY}`;
  const potentialVirtual = /(hidden|clip)/.test(rootOverflow) && nodes.some((el) => {
    const s = getComputedStyle(el);
    return (s.willChange || '').includes('transform') || (s.transform && s.transform !== 'none');
  });
  return {
    kind: scroller === root ? 'document' : 'element',
    top: scroller === root ? (scrollY || root.scrollTop || 0) : scroller.scrollTop,
    height: scroller.scrollHeight,
    client: scroller === root ? innerHeight : scroller.clientHeight,
    rect,
    transformSignature: transformNodes.map((el) => `${el.tagName}:${getComputedStyle(el).transform}`).join('|'),
    visibleSignature: visible.join('|'),
    potentialVirtual,
    endProven: document.documentElement.dataset.kaigoScrollEnd === 'true' || document.body.dataset.kaigoScrollEnd === 'true',
    visible
  };
}"""


async def _scroll_once(page: Any, state: Mapping[str, Any], step: int) -> str:
    rect = state["rect"]
    target_x = float(rect["x"]) + float(rect["width"]) / 2
    target_y = float(rect["y"]) + float(rect["height"]) / 2
    await page.mouse.move(target_x, target_y)
    await page.mouse.wheel(0, step)
    await page.wait_for_timeout(50)
    after = await page.evaluate(_SCROLL_STATE_SCRIPT)
    if (
        int(after["top"]) <= int(state["top"])
        and after["transformSignature"] == state["transformSignature"]
        and after["visibleSignature"] == state["visibleSignature"]
    ):
        await page.evaluate(
            """(dy) => {
              const marked = document.querySelector('[data-kaigo-scroll-id="active"]');
              if (!marked || marked === document.documentElement || marked === document.body) {
                window.scrollBy({top: dy, left: 0, behavior: 'instant'});
              } else {
                marked.scrollBy({top: dy, left: 0, behavior: 'instant'});
              }
            }""",
            step,
        )
        await page.wait_for_timeout(50)
        fallback_after = await page.evaluate(_SCROLL_STATE_SCRIPT)
        if (
            int(fallback_after["top"]) > int(after["top"])
            or fallback_after["transformSignature"] != after["transformSignature"]
            or fallback_after["visibleSignature"] != after["visibleSignature"]
        ):
            return "script_fallback"
        return "stalled"
    return "wheel"


def _scroll_state_changed(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> bool:
    return (
        int(after["top"]) != int(before["top"])
        or after.get("transformSignature") != before.get("transformSignature")
        or after.get("visibleSignature") != before.get("visibleSignature")
    )


async def _settle_scrolled_viewport(
    page: Any,
    *,
    settings: CaptureSettings,
    skipped_reasons: list[str],
    image_timeout_reason: str,
) -> None:
    timeout_ms = settings.page_timeout_seconds * 1000

    visual_task = asyncio.create_task(
        _wait_for_visual_quiet(
            page,
            minimum_ms=settings.scroll_delay_ms,
            quiet_ms=450,
            maximum_ms=min(2500, timeout_ms),
        )
    )
    asset_task = asyncio.create_task(
        _settle_viewport_assets_nonfatal(
            page,
            timeout_ms=min(2500, timeout_ms),
            skipped_reasons=skipped_reasons,
            image_timeout_reason=image_timeout_reason,
        )
    )
    tasks = (visual_task, asset_task)
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _warm_reference_page(
    page: Any,
    *,
    settings: CaptureSettings,
) -> tuple[Mapping[str, Any], tuple[str, ...], int]:
    initial_state = await page.evaluate(_SCROLL_STATE_SCRIPT)
    state = initial_state
    skipped_reasons: list[str] = []
    step_px = max(1, int(settings.height * 0.7))
    stable_steps = 0
    native_end_stable_steps = 0
    progressed = False
    native_progressed = False
    virtual = bool(state.get("potentialVirtual"))
    exhausted = True
    steps = 0
    sweep_pause_ms = min(250, max(100, settings.scroll_delay_ms // 4))

    for _step_index in range(settings.max_scroll_steps):
        if int(state["height"]) > settings.max_scroll_height:
            skipped_reasons.append("warmup_scroll_height_cap_reached")
            exhausted = False
            break
        previous = state
        await _scroll_once(page, previous, step_px)
        steps += 1
        await page.wait_for_timeout(sweep_pause_ms)
        state = await page.evaluate(_SCROLL_STATE_SCRIPT)
        meaningful_change = _scroll_state_changed(previous, state)
        if meaningful_change:
            progressed = True
            stable_steps = 0
        else:
            stable_steps += 1

        top_changed = int(state["top"]) != int(previous["top"])
        transform_changed = (
            state.get("transformSignature")
            != previous.get("transformSignature")
        )
        visible_changed = (
            state.get("visibleSignature")
            != previous.get("visibleSignature")
        )
        if top_changed:
            native_progressed = True
            virtual = False
        elif not native_progressed and (transform_changed or visible_changed):
            virtual = True

        if virtual:
            if state.get("endProven"):
                exhausted = False
                break
        else:
            max_native_scroll = max(
                0,
                int(state["height"]) - int(state["client"]),
            )
            at_end = (
                max_native_scroll == 0
                or int(state["top"]) >= max_native_scroll - 2
            )
            if (
                at_end
                and int(state["top"]) == int(previous["top"])
                and int(state["height"]) == int(previous["height"])
            ):
                native_end_stable_steps += 1
            else:
                native_end_stable_steps = 0
            if (
                progressed
                and at_end
                and native_end_stable_steps >= 2
            ) or (not progressed and stable_steps >= 2):
                exhausted = False
                break

    if exhausted:
        skipped_reasons.append("warmup_scroll_step_cap_reached")
    if steps:
        await _settle_scrolled_viewport(
            page,
            settings=settings,
            skipped_reasons=skipped_reasons,
            image_timeout_reason="warmup_lazy_image_settle_timeout",
        )
    return initial_state, tuple(skipped_reasons), steps


def _scroll_state_is_restored(
    state: Mapping[str, Any],
    initial_state: Mapping[str, Any],
) -> bool:
    if abs(int(state["top"]) - int(initial_state["top"])) > 2:
        return False
    if initial_state.get("potentialVirtual"):
        return (
            state.get("transformSignature")
            == initial_state.get("transformSignature")
        )
    return True


async def _restore_reference_start(
    page: Any,
    *,
    initial_state: Mapping[str, Any],
    settings: CaptureSettings,
) -> tuple[tuple[str, ...], int]:
    state = await page.evaluate(_SCROLL_STATE_SCRIPT)
    skipped_reasons: list[str] = []
    if _scroll_state_is_restored(state, initial_state):
        return (), 0

    step_px = -max(1, int(settings.height * 0.7))
    stable_steps = 0
    steps = 0
    for _step_index in range(settings.max_scroll_steps):
        previous = state
        await _scroll_once(page, previous, step_px)
        steps += 1
        state = await page.evaluate(_SCROLL_STATE_SCRIPT)
        if _scroll_state_is_restored(state, initial_state):
            await _settle_scrolled_viewport(
                page,
                settings=settings,
                skipped_reasons=skipped_reasons,
                image_timeout_reason="reset_lazy_image_settle_timeout",
            )
            state = await page.evaluate(_SCROLL_STATE_SCRIPT)
            if _scroll_state_is_restored(state, initial_state):
                return tuple(skipped_reasons), steps
            break
        if _scroll_state_changed(previous, state):
            stable_steps = 0
        else:
            stable_steps += 1
        if stable_steps >= 2:
            break

    raise ReferenceCaptureError("reference scroll position could not be restored")


async def _take_screenshot(
    page: Any,
    *,
    page_id: str,
    viewport: str,
    position: str,
    width: int,
    height: int,
    telemetry: CaptureTelemetry | None = None,
) -> ScreenshotEvidence:
    data = await page.screenshot(type="jpeg", quality=82, full_page=False, animations="allow")
    try:
        with Image.open(BytesIO(data)) as image:
            actual_width, actual_height = image.size
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise ReferenceCaptureError("screenshot JPEG cannot be decoded") from exc
    if (actual_width, actual_height) != (width, height):
        raise ReferenceCaptureError(
            "screenshot dimensions do not match the requested viewport"
        )
    evidence = ScreenshotEvidence(
        screenshot_id=f"{page_id}-{viewport}-{position}",
        page_id=page_id,
        viewport=viewport,
        position=position,
        mime_type="image/jpeg",
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        data=data,
    )
    if telemetry is not None:
        telemetry.record_screenshot_bytes(evidence.screenshot_id, evidence.size_bytes)
    return evidence


_SAMPLE_SCRIPT = r"""({initialHidden, observedTexts}) => {
  const text = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 300);
  const unique = (values, limit) => [...new Set(values.filter(Boolean))].slice(0, limit);
  const nodes = [...document.querySelectorAll('body *')].slice(0, 2500);
  const descriptors = nodes.filter((el) => {
    const tag = el.tagName.toLowerCase();
    return ['h1','h2','h3','p','a','button','nav','header','footer','section','main','aside','input','textarea'].includes(tag);
  }).slice(0, 80).map((el) => {
    const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    return {tag: el.tagName.toLowerCase(), text: text(el).slice(0,120), fontFamily:s.fontFamily,
      fontSize:s.fontSize, fontWeight:s.fontWeight, lineHeight:s.lineHeight, color:s.color,
      backgroundColor:s.backgroundColor, border:s.border, borderRadius:s.borderRadius,
      boxShadow:s.boxShadow, padding:s.padding, margin:s.margin, position:s.position,
      transition:s.transition, animation:s.animationName === 'none' ? 'none' : `${s.animationName} ${s.animationDuration}`,
      width:Math.round(r.width), height:Math.round(r.height)};
  });
  const cssVars = {}; const rootStyle = getComputedStyle(document.documentElement);
  for (const name of [...rootStyle].filter((name) => name.startsWith('--')).slice(0,40)) cssVars[name] = rootStyle.getPropertyValue(name).trim().slice(0,200);
  const colors = [];
  for (const el of nodes.slice(0,500)) { const s=getComputedStyle(el); colors.push(s.color,s.backgroundColor,s.borderColor); }
  const colorCounts = {};
  for (const value of colors.filter((v) => v && v !== 'rgba(0, 0, 0, 0)')) colorCounts[value]=(colorCounts[value]||0)+1;
  const images = [...document.images].slice(0,30).map((img) => ({width:img.naturalWidth,height:img.naturalHeight,aspectRatio:img.naturalHeight ? +(img.naturalWidth/img.naturalHeight).toFixed(3) : null,alt:(img.alt||'').slice(0,160)}));
  const fixedSticky = nodes.filter((el) => ['fixed','sticky'].includes(getComputedStyle(el).position)).slice(0,20).map((el) => ({tag:el.tagName.toLowerCase(),text:text(el).slice(0,100),position:getComputedStyle(el).position}));
  const moving = nodes.filter((el) => {
    const s=getComputedStyle(el);
    return (s.transitionDuration && s.transitionDuration !== '0s') || (s.animationName && s.animationName !== 'none');
  }).slice(0,60).map((el) => {
    const s=getComputedStyle(el);
    return {tag:el.tagName.toLowerCase(),text:text(el).slice(0,120),transition:s.transition,
      animation:s.animationName === 'none' ? 'none' : `${s.animationName} ${s.animationDuration}`};
  });
  const visibleReveal = nodes.filter((el) => /reveal|animate|visible|shown|seen/i.test(el.className || '') && parseFloat(getComputedStyle(el).opacity || '1') > 0).length;
  return {
    semantic: {
      title: document.title.slice(0,300),
      lang: document.documentElement.lang || '',
      headings: unique([...document.querySelectorAll('h1,h2,h3')].map(text),30),
      body: unique([...document.querySelectorAll('main p, article p, body > p')].map(text),30),
      navigation: unique([...document.querySelectorAll('nav a, header a')].map(text),30),
      controls: unique([...document.querySelectorAll('button,a,input,textarea')].map(text),40),
      observed_viewport_texts: unique(observedTexts,80),
      initial_hidden_reveal_count: initialHidden,
      reveal_observed: initialHidden > 0 && visibleReveal > 0
    },
    style: {
      body: (() => {const s=getComputedStyle(document.body);return {fontFamily:s.fontFamily,fontSize:s.fontSize,lineHeight:s.lineHeight,color:s.color,backgroundColor:s.backgroundColor}})(),
      fonts: unique(descriptors.map((item) => item.fontFamily),20),
      dominantColors: Object.entries(colorCounts).sort((a,b)=>b[1]-a[1]).slice(0,16).map(([value,count])=>({value,count})),
      cssVariables: cssVars,
      elements: descriptors,
      imageAspectRatios: images,
      fixedSticky,
      motionInventory: moving
    }
  };
}"""


def _ordered_union(values: Iterable[Any], *, limit: int) -> list[Any]:
    result: list[Any] = []
    fingerprints: set[str] = set()
    for value in values:
        fingerprint = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _merge_page_samples(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    first = samples[0]
    semantic: dict[str, Any] = {
        "title": first["semantic"].get("title", ""),
        "lang": first["semantic"].get("lang", ""),
        "initial_hidden_reveal_count": first["semantic"].get(
            "initial_hidden_reveal_count", 0
        ),
        "reveal_observed": any(
            sample["semantic"].get("reveal_observed", False) for sample in samples
        ),
    }
    for field_name, limit in (
        ("headings", 30),
        ("body", 30),
        ("navigation", 30),
        ("controls", 40),
        ("observed_viewport_texts", 80),
    ):
        semantic[field_name] = _ordered_union(
            (
                item
                for sample in samples
                for item in sample["semantic"].get(field_name, ())
            ),
            limit=limit,
        )
    colors: Counter[str] = Counter()
    for sample in samples:
        for item in sample["style"].get("dominantColors", ()):
            colors[str(item.get("value", ""))] += int(item.get("count", 0))
    images_by_alt: dict[str, Any] = {}
    for page_sample in samples:
        for item in page_sample["style"].get("imageAspectRatios", ()):
            key = str(item.get("alt", ""))
            current = images_by_alt.get(key)
            if current is None or (
                current.get("aspectRatio") is None
                and item.get("aspectRatio") is not None
            ):
                images_by_alt[key] = item
    style = {
        "body": dict(first["style"].get("body", {})),
        "fonts": _ordered_union(
            (font for sample in samples for font in sample["style"].get("fonts", ())),
            limit=20,
        ),
        "dominantColors": [
            {"value": value, "count": count}
            for value, count in colors.most_common(16)
            if value
        ],
        "cssVariables": dict(first["style"].get("cssVariables", {})),
        "elements": _ordered_union(
            (
                item
                for sample in samples
                for item in sample["style"].get("elements", ())
            ),
            limit=80,
        ),
        "imageAspectRatios": list(images_by_alt.values())[:30],
        "fixedSticky": _ordered_union(
            (
                item
                for sample in samples
                for item in sample["style"].get("fixedSticky", ())
            ),
            limit=20,
        ),
        "motionInventory": _ordered_union(
            (
                item
                for sample in samples
                for item in sample["style"].get("motionInventory", ())
            ),
            limit=30,
        ),
    }
    return {"semantic": semantic, "style": style}


async def _capture_loaded_page(
    page: Any,
    *,
    requested_url: str,
    page_id: str,
    category: str,
    viewport: str,
    settings: CaptureSettings,
    guard: UrlGuard | None,
    telemetry: CaptureTelemetry,
    initial_skipped_reasons: Sequence[str] = (),
    initial_timings_ms: Mapping[str, int | float] | None = None,
) -> ReferencePageEvidence:
    started = time.monotonic()
    phase_started = started
    timings_ms: dict[str, int | float] = dict(initial_timings_ms or {})
    observed_texts: list[str] = []
    skipped_reasons = list(initial_skipped_reasons)
    samples: list[Mapping[str, Any]] = []

    timeout_ms = settings.page_timeout_seconds * 1000
    skipped_reasons.extend(
        await _wait_for_reference_load(page, timeout_ms=min(timeout_ms, 5_000))
    )
    if any(
        item.startswith("cross-origin document blocked:")
        for item in telemetry.policy_blocks
    ):
        raise ReferenceCaptureError("cross-origin document blocked")
    telemetry.raise_if_oversize()
    if guard is not None:
        await asyncio.to_thread(guard.validate_redirect, page.url)
    await _settle_viewport_assets_nonfatal(
        page,
        timeout_ms=min(timeout_ms, 5000),
        skipped_reasons=skipped_reasons,
        image_timeout_reason=None,
    )
    await page.wait_for_timeout(settings.warmup_ms)
    await _wait_for_visual_quiet(page)
    telemetry.raise_if_oversize()
    initial_hidden = await page.evaluate(
        """() => [...document.querySelectorAll('body *')].slice(0, 5000).filter((el) => {
          const s=getComputedStyle(el); const r=el.getBoundingClientRect();
          return /reveal|animate|hidden/i.test(el.className || '') && r.width>1 && r.height>1 && (s.opacity==='0' || s.visibility==='hidden');
        }).length"""
    )
    timings_ms["load_and_initial_settle"] = round(
        (time.monotonic() - phase_started) * 1000,
        1,
    )

    phase_started = time.monotonic()
    screenshots: dict[str, ScreenshotEvidence] = {}
    state = await page.evaluate(_SCROLL_STATE_SCRIPT)
    initial_scroll_top = int(state["top"])
    initial_client_height = max(1, int(state["client"]))
    observed_texts.extend(state.get("visible", ()))
    samples.append(
        await page.evaluate(
            _SAMPLE_SCRIPT,
            {"initialHidden": int(initial_hidden), "observedTexts": observed_texts},
        )
    )
    screenshots["top"] = await _take_screenshot(
        page,
        page_id=page_id,
        viewport=viewport,
        position="top",
        width=settings.width,
        height=settings.height,
        telemetry=telemetry,
    )

    step_px = max(1, int(settings.height * 0.7))
    modes: set[str] = set()
    fallback_used = False
    stable_steps = 0
    native_end_stable_steps = 0
    progressed = False
    virtual = bool(state.get("potentialVirtual"))
    native_progressed = False
    exhausted = True
    coverage_complete = False
    evidence_steps = 0
    sweep_pause_ms = min(250, max(100, settings.scroll_delay_ms // 4))
    for step_index in range(settings.max_scroll_steps):
        if int(state["height"]) > settings.max_scroll_height:
            skipped_reasons.append("scroll_height_cap_reached")
            exhausted = False
            break
        previous = state
        scroll_result = await _scroll_once(page, previous, step_px)
        evidence_steps += 1
        fallback_used = fallback_used or scroll_result == "script_fallback"
        await page.wait_for_timeout(sweep_pause_ms)
        state = await page.evaluate(_SCROLL_STATE_SCRIPT)
        top_changed = int(state["top"]) != int(previous["top"])
        transform_changed = (
            state.get("transformSignature") != previous.get("transformSignature")
        )
        visible_changed = (
            state.get("visibleSignature") != previous.get("visibleSignature")
        )
        meaningful_change = top_changed or transform_changed or visible_changed
        if meaningful_change:
            progressed = True
            stable_steps = 0
        else:
            stable_steps += 1
        if top_changed:
            native_progressed = True
            virtual = False
            modes.discard("virtual")
            if state.get("kind") == "element":
                modes.add("nested")
            else:
                modes.add("document")
        elif not native_progressed and (transform_changed or visible_changed):
            virtual = True
            modes.add("virtual")
        elif virtual:
            modes.add("virtual")
        observed_texts.extend(state.get("visible", ()))
        max_native_scroll = max(0, int(state["height"]) - int(state["client"]))
        native_progress = (
            min(1.0, int(state["top"]) / max(1, max_native_scroll))
            if max_native_scroll
            else 0.0
        )
        anchors: list[str] = []
        if viewport == "desktop" and "after_top" not in screenshots and (
            (virtual and meaningful_change)
            or (
                not virtual
                and int(state["top"])
                >= initial_scroll_top + initial_client_height - 2
            )
        ):
            anchors.append("after_top")
        if "middle" not in screenshots and (
            (virtual and meaningful_change)
            or (not virtual and native_progress >= 0.45)
        ):
            anchors.append("middle")
        if anchors:
            await _settle_scrolled_viewport(
                page,
                settings=settings,
                skipped_reasons=skipped_reasons,
                image_timeout_reason="lazy_image_settle_timeout",
            )
            telemetry.raise_if_oversize()
            samples.append(
                await page.evaluate(
                    _SAMPLE_SCRIPT,
                    {
                        "initialHidden": int(initial_hidden),
                        "observedTexts": observed_texts[:160],
                    },
                )
            )
            for anchor in anchors:
                screenshots[anchor] = await _take_screenshot(
                    page,
                    page_id=page_id,
                    viewport=viewport,
                    position=anchor,
                    width=settings.width,
                    height=settings.height,
                    telemetry=telemetry,
                )
        if virtual:
            if state.get("endProven"):
                exhausted = False
                coverage_complete = True
                break
        else:
            at_end = max_native_scroll == 0 or int(state["top"]) >= max_native_scroll - 2
            if (
                at_end
                and int(state["top"]) == int(previous["top"])
                and int(state["height"]) == int(previous["height"])
            ):
                native_end_stable_steps += 1
            else:
                native_end_stable_steps = 0
            if (
                progressed
                and at_end
                and native_end_stable_steps >= 2
            ) or (not progressed and stable_steps >= 2):
                exhausted = False
                coverage_complete = True
                break
    if exhausted:
        skipped_reasons.append("scroll_step_cap_reached")
    timings_ms["evidence_pass"] = round(
        (time.monotonic() - phase_started) * 1000,
        1,
    )
    timings_ms["evidence_steps"] = evidence_steps

    phase_started = time.monotonic()
    await _settle_scrolled_viewport(
        page,
        settings=settings,
        skipped_reasons=skipped_reasons,
        image_timeout_reason="final_lazy_image_settle_timeout",
    )
    telemetry.raise_if_oversize()
    observed_texts.extend(state.get("visible", ()))
    samples.append(
        await page.evaluate(
            _SAMPLE_SCRIPT,
            {
                "initialHidden": int(initial_hidden),
                "observedTexts": observed_texts[:160],
            },
        )
    )
    coverage_status = "complete" if coverage_complete else "partial"
    final_position = "bottom" if coverage_status == "complete" else "last_observed"
    if (
        viewport == "desktop"
        and "after_top" not in screenshots
        and coverage_status == "complete"
    ):
        screenshots["after_top"] = await _take_screenshot(
            page,
            page_id=page_id,
            viewport=viewport,
            position="after_top",
            width=settings.width,
            height=settings.height,
            telemetry=telemetry,
        )
    screenshots[final_position] = await _take_screenshot(
        page,
        page_id=page_id,
        viewport=viewport,
        position=final_position,
        width=settings.width,
        height=settings.height,
        telemetry=telemetry,
    )
    if "middle" not in screenshots and coverage_status == "complete":
        screenshots["middle"] = await _take_screenshot(
            page,
            page_id=page_id,
            viewport=viewport,
            position="middle",
            width=settings.width,
            height=settings.height,
            telemetry=telemetry,
        )
    sample = _merge_page_samples(samples)
    await telemetry.settle_transfers()
    telemetry.raise_if_oversize()
    screenshot_bytes = sum(item.size_bytes for item in screenshots.values())
    transferred_bytes = telemetry.response_bytes + screenshot_bytes
    if transferred_bytes > settings.max_page_bytes:
        raise ReferenceCaptureError("page byte limit exceeded")
    if not progressed:
        scroll_strategy = "static"
    elif len(modes) == 1:
        scroll_strategy = next(iter(modes))
    else:
        scroll_strategy = "mixed"
    if fallback_used:
        scroll_strategy = f"{scroll_strategy}+script_fallback"
    timings_ms["final_settle_and_screenshots"] = round(
        (time.monotonic() - phase_started) * 1000,
        1,
    )
    timings_ms["total"] = round((time.monotonic() - started) * 1000, 1)
    return ReferencePageEvidence(
        page_id=page_id,
        category=category,
        requested_url=requested_url,
        final_url=page.url,
        depth=0 if category == "home" else 1,
        screenshots=tuple(
            screenshots[position]
            for position in (
                ("top", "after_top", "middle", final_position)
                if viewport == "desktop"
                else ("top", "middle", final_position)
            )
            if position in screenshots
        ),
        semantic_sample=sample["semantic"],
        style_sample=sample["style"],
        console_failures=tuple(telemetry.console_failures),
        page_failures=tuple(telemetry.page_failures),
        request_failures=tuple(telemetry.request_failures),
        policy_blocks=tuple(telemetry.policy_blocks),
        timings_ms=timings_ms,
        transferred_bytes=transferred_bytes,
        scroll_strategy=scroll_strategy,
        reset_strategy="single-pass",
        skipped_reasons=tuple(skipped_reasons),
        coverage_status=coverage_status,
    )


async def _has_meaningfully_rendered_document(page: Any) -> bool:
    try:
        probe = await page.evaluate(
            """() => {
              const body = document.body;
              const root = document.scrollingElement || document.documentElement;
              return {
                href: location.href,
                bodyExists: Boolean(body),
                textLength: (body?.innerText || '').trim().length,
                elementCount: body?.querySelectorAll('*').length || 0,
                scrollHeight: root?.scrollHeight || 0,
              };
            }"""
        )
    except Exception:
        return False
    if not isinstance(probe, Mapping) or not probe.get("bodyExists"):
        return False
    try:
        href = str(probe.get("href", ""))
        text_length = int(probe.get("textLength", 0))
        element_count = int(probe.get("elementCount", 0))
        scroll_height = int(probe.get("scrollHeight", 0))
    except (TypeError, ValueError):
        return False
    if urlsplit(href).scheme not in {"http", "https"}:
        return False
    return scroll_height >= 200 and (
        text_length >= 40 or element_count >= 8
    )


async def _goto_reference_document(
    page: Any,
    url: str,
    *,
    timeout_ms: int,
) -> tuple[str, ...]:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    try:
        await page.goto(
            url,
            wait_until="commit",
            timeout=min(timeout_ms, 15_000),
        )
    except PlaywrightTimeoutError:
        if not await _has_meaningfully_rendered_document(page):
            raise
        return ("navigation_commit_timeout_rendered",)
    return ("domcontentloaded_deferred_after_commit",)


async def _wait_for_reference_load(
    page: Any,
    *,
    timeout_ms: int,
) -> tuple[str, ...]:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    try:
        ready_state = await page.evaluate("() => document.readyState")
    except Exception:
        ready_state = None
    if ready_state == "complete":
        return ()
    if ready_state == "interactive":
        # Navigation already waited for DOMContentLoaded. A separate `load`
        # gate can be held indefinitely by analytics, fonts, or other
        # background resources, while the document is ready for bounded asset
        # settling and the lazy-load sweep below.
        return ("load_deferred_after_domcontentloaded",)
    if await _has_meaningfully_rendered_document(page):
        return ("load_deferred_after_rendered_document",)

    try:
        await page.wait_for_function(
            """() => {
              const body = document.body;
              const root = document.scrollingElement || document.documentElement;
              if (!body || !root || root.scrollHeight < 200) return false;
              const textLength = (body.innerText || '').trim().length;
              const elementCount = body.querySelectorAll('*').length;
              return textLength >= 40 || elementCount >= 8;
            }""",
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError:
        if not await _has_meaningfully_rendered_document(page):
            raise
    return ("load_deferred_after_rendered_document",)


_FATAL_DOCUMENT_POLICY_PREFIXES = (
    "cross-origin document blocked:",
    "cross-origin document redirect blocked:",
    "malformed document URL blocked:",
    "popup document blocked:",
    "robots policy blocked document:",
    "unexpected document redirect blocked:",
)


def _capture_failure_message(
    exc: BaseException,
    policy_blocks: Sequence[str],
) -> str:
    policy_reason = next(
        (
            item.split(":", 1)[0]
            for item in policy_blocks
            if item.startswith(_FATAL_DOCUMENT_POLICY_PREFIXES)
        ),
        None,
    )
    return policy_reason or _exception_text(exc)


async def _trace_failure(context: Any, ttl_seconds: int) -> TraceEvidence | None:
    try:
        with tempfile.TemporaryDirectory(prefix="kaigo-reference-trace-") as temp_dir:
            path = Path(temp_dir) / "trace.zip"
            await context.tracing.stop(path=path)
            data = path.read_bytes()
    except Exception:
        return None
    if not data or len(data) > 100 * 1024 * 1024:
        return None
    digest = hashlib.sha256(data).hexdigest()
    return TraceEvidence(
        trace_id=f"failed-{digest[:16]}",
        sha256=digest,
        size_bytes=len(data),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        data=data,
    )


def _single_page_browser_pool() -> Any:
    from crawlee.browsers import BrowserPool, PlaywrightBrowserPlugin

    plugin = PlaywrightBrowserPlugin(
        browser_type="chromium",
        use_incognito_pages=True,
        max_open_pages_per_browser=2,
        fingerprint_generator=None,
        browser_launch_options={"headless": True},
        browser_new_context_options={
            "viewport": {"width": 1920, "height": 1080},
            "device_scale_factor": 1,
            "user_agent": KAIGO_RESEARCH_USER_AGENT,
            "locale": "ru-RU",
            "permissions": [],
            "accept_downloads": False,
            "service_workers": "block",
        },
    )
    return BrowserPool(plugins=[plugin])


async def _capture_reference_page_on_page(
    page: Any,
    url: str,
    *,
    requested_url: str | None = None,
    page_id: str,
    category: str,
    viewport: str,
    settings: CaptureSettings,
    guard: UrlGuard | None = None,
    robots_policy: GuardedRobotsPolicy | None = None,
    byte_budget: CrawlByteBudget | None = None,
    trace_ttl_seconds: int = 3600,
) -> ReferencePageEvidence:
    """Capture a canonical URL with an already-open isolated Playwright page."""
    requested_url = requested_url or url
    context = page.context
    await page.set_viewport_size({"width": settings.width, "height": settings.height})
    await context.clear_cookies()
    telemetry = CaptureTelemetry(
        max_page_bytes=settings.max_page_bytes,
        total_budget=byte_budget,
    )
    primary_page: dict[str, Any] = {"page": page}
    await _install_context_policy(
        context,
        primary_page=primary_page,
        guard=guard,
        robots=robots_policy,
        allowed_document_origin=_origin_key(url),
        telemetry=telemetry,
    )
    telemetry.attach_page(page)
    await context.tracing.start(screenshots=True, snapshots=True, sources=False)
    try:
        navigation_started = time.monotonic()
        navigation_warnings = await _goto_reference_document(
            page,
            url,
            timeout_ms=settings.page_timeout_seconds * 1000,
        )
        navigation_ms = round((time.monotonic() - navigation_started) * 1000, 1)
        evidence = await _capture_loaded_page(
            page,
            requested_url=requested_url,
            page_id=page_id,
            category=category,
            viewport=viewport,
            settings=settings,
            guard=guard,
            telemetry=telemetry,
            initial_skipped_reasons=navigation_warnings,
            initial_timings_ms={"navigation": navigation_ms},
        )
    except Exception as exc:
        trace = await _trace_failure(context, trace_ttl_seconds)
        message = (
            telemetry.byte_limit_error
            or _capture_failure_message(exc, telemetry.policy_blocks)
        )
        raise ReferenceCaptureError(message, trace=trace) from exc
    else:
        await context.tracing.stop()
        return evidence
    finally:
        await telemetry.close_http_client()


async def capture_reference_page(
    url: str,
    *,
    page_id: str,
    category: str,
    viewport: str,
    settings: CaptureSettings,
    guard: UrlGuard | None = None,
    robots_policy: GuardedRobotsPolicy | None = None,
    byte_budget: CrawlByteBudget | None = None,
    trace_ttl_seconds: int = 3600,
) -> ReferencePageEvidence:
    """Capture one explicitly supplied page without discovery or LLM calls."""
    from playwright.async_api import async_playwright

    requested_url = url
    if guard is not None:
        await asyncio.to_thread(guard.validate, url)
        robots_policy = robots_policy or GuardedRobotsPolicy(guard=guard)
    if robots_policy is not None:
        await robots_policy.require_allowed(url)
    if guard is not None:
        url = await _canonicalize_document_url(
            url,
            guard=guard,
            robots=robots_policy,
            timeout_seconds=min(15, settings.page_timeout_seconds),
        )
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": settings.width, "height": settings.height},
            device_scale_factor=1,
            user_agent=KAIGO_RESEARCH_USER_AGENT,
            locale="ru-RU",
            permissions=[],
            accept_downloads=False,
            service_workers="block",
        )
        page = await context.new_page()
        try:
            return await _capture_reference_page_on_page(
                page,
                url,
                requested_url=requested_url,
                page_id=page_id,
                category=category,
                viewport=viewport,
                settings=settings,
                guard=guard,
                robots_policy=robots_policy,
                byte_budget=byte_budget,
                trace_ttl_seconds=trace_ttl_seconds,
            )
        finally:
            await context.close()
            await browser.close()


async def _safe_get_text(
    url: str,
    *,
    guard: UrlGuard,
    max_bytes: int,
    timeout_seconds: int,
    max_redirects: int = 3,
) -> tuple[str, str] | None:
    import httpx

    current = url
    async with httpx.AsyncClient(
        follow_redirects=False,
        headers={"User-Agent": KAIGO_RESEARCH_USER_AGENT},
        timeout=timeout_seconds,
        trust_env=False,
    ) as client:
        for _ in range(max_redirects + 1):
            await asyncio.to_thread(guard.validate_redirect, current)
            async with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        return None
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        return None
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                return current, b"".join(chunks).decode(encoding, errors="replace")
    return None


async def _sitemap_candidates(
    home_url: str,
    *,
    guard: UrlGuard,
    timeout_seconds: int,
) -> tuple[LinkCandidate, ...]:
    parsed = urlsplit(home_url)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    sitemap_urls = [urljoin(origin, "/sitemap.xml")]
    try:
        robots = await _safe_get_text(
            urljoin(origin, "/robots.txt"),
            guard=guard,
            max_bytes=256 * 1024,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        robots = None
    if robots:
        for line in robots[1].splitlines():
            if line.casefold().startswith("sitemap:"):
                sitemap_urls.append(line.split(":", 1)[1].strip())
    candidates: list[LinkCandidate] = []
    seen_sitemaps: set[str] = set()
    for sitemap_url in sitemap_urls[:3]:
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)
        try:
            document = await _safe_get_text(
                sitemap_url,
                guard=guard,
                max_bytes=1024 * 1024,
                timeout_seconds=timeout_seconds,
            )
            if not document:
                continue
            lowered = document[1].casefold()
            if "<!doctype" in lowered or "<!entity" in lowered:
                continue
            root = ET.fromstring(document[1])
        except Exception:
            continue
        locations = [
            (element.text or "").strip()
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1].lower() == "loc" and element.text
        ]
        for location in locations[:200]:
            candidates.append(LinkCandidate(location, source="sitemap"))
    return tuple(candidates)


def _page_id(category: str, index: int) -> str:
    return category if category != "other" else f"page-{index + 1}"


class VisualReferenceCrawler:
    """A bounded Crawlee queue with deterministic Playwright evidence capture."""

    def __init__(
        self,
        *,
        limits: ReferenceCrawlLimits | None = None,
        guard: UrlGuard | None = None,
    ) -> None:
        self.limits = limits or ReferenceCrawlLimits()
        self.guard = guard or UrlGuard()

    def _settings(self, viewport: str) -> CaptureSettings:
        width, height = ((1920, 1080) if viewport == "desktop" else (390, 844))
        return CaptureSettings(
            width=width,
            height=height,
            warmup_ms=self.limits.warmup_ms,
            scroll_delay_ms=self.limits.scroll_delay_ms,
            final_settle_ms=self.limits.final_settle_ms,
            max_scroll_steps=self.limits.max_scroll_steps,
            max_scroll_height=self.limits.max_scroll_height,
            page_timeout_seconds=self.limits.page_timeout_seconds,
            max_page_bytes=self.limits.max_page_bytes,
        )

    async def _capture_single_page_viewports(
        self,
        home_url: str,
        *,
        robots_policy: GuardedRobotsPolicy,
        byte_budget: CrawlByteBudget,
        remaining_timeout: Callable[[], float],
    ) -> tuple[
        ReferencePageEvidence | Exception,
        ReferencePageEvidence | Exception,
    ]:
        async with _single_page_browser_pool() as browser_pool:
            async def capture(
                *,
                page_id: str,
                viewport: str,
            ) -> ReferencePageEvidence | Exception:
                last_error: Exception | None = None

                async def attempt() -> ReferencePageEvidence:
                    pool_page_id = (
                        page_id
                        if page_id.endswith(f"-{viewport}")
                        else f"{page_id}-{viewport}"
                    )
                    crawlee_page = await browser_pool.new_page(page_id=pool_page_id)
                    try:
                        return await _capture_reference_page_on_page(
                            crawlee_page.page,
                            home_url,
                            page_id=page_id,
                            category="home",
                            viewport=viewport,
                            settings=self._settings(viewport),
                            guard=self.guard,
                            robots_policy=robots_policy,
                            byte_budget=byte_budget,
                            trace_ttl_seconds=self.limits.trace_ttl_seconds,
                        )
                    finally:
                        await crawlee_page.page.close()

                for _attempt in range(self.limits.max_retries + 1):
                    try:
                        return await asyncio.wait_for(
                            attempt(),
                            timeout=remaining_timeout(),
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        last_error = exc
                assert last_error is not None
                return last_error

            if _viewport_capture_concurrency() == 1:
                desktop = await capture(page_id="home", viewport="desktop")
                mobile = await capture(page_id="home-mobile", viewport="mobile")
            else:
                desktop, mobile = await asyncio.gather(
                    capture(page_id="home", viewport="desktop"),
                    capture(page_id="home-mobile", viewport="mobile"),
                )
            return desktop, mobile

    async def crawl(self, source_url: str) -> ReferenceCrawlResult:
        from crawlee import ConcurrencySettings, Request
        from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
        from crawlee.request_loaders import ThrottlingRequestManager
        from crawlee.storages import RequestQueue

        started_at = datetime.now(timezone.utc)
        deadline = time.monotonic() + self.limits.total_timeout_seconds

        def remaining_timeout() -> float:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("crawl total timeout exceeded")
            return remaining

        guarded = await asyncio.wait_for(
            asyncio.to_thread(self.guard.validate, source_url),
            timeout=min(10, remaining_timeout()),
        )
        home_url = _without_fragment(guarded.url)
        robots = GuardedRobotsPolicy(
            guard=self.guard,
            respect_robots=self.limits.respect_robots,
            timeout_seconds=min(15, self.limits.page_timeout_seconds),
        )
        try:
            await asyncio.wait_for(
                robots.require_allowed(home_url), timeout=min(20, remaining_timeout())
            )
            home_url = await asyncio.wait_for(
                _canonicalize_document_url(
                    home_url,
                    guard=self.guard,
                    robots=robots,
                    timeout_seconds=min(15, self.limits.page_timeout_seconds),
                ),
                timeout=min(30, remaining_timeout()),
            )
        except Exception as exc:
            return ReferenceCrawlResult.failed(
                source_url=home_url,
                failure=CrawlFailure(
                    code="robots_denied", message=_sanitize_text(str(exc))
                ),
                started_at=started_at,
            )
        sitemap: tuple[LinkCandidate, ...] = ()
        if self.limits.max_pages > 1:
            try:
                sitemap = await asyncio.wait_for(
                    _sitemap_candidates(
                        home_url,
                        guard=self.guard,
                        timeout_seconds=min(15, self.limits.page_timeout_seconds),
                    ),
                    timeout=min(20, remaining_timeout()),
                )
            except (TimeoutError, OSError):
                sitemap = ()
        failure_trace: TraceEvidence | None = None
        byte_budget = CrawlByteBudget(self.limits.max_total_bytes)

        if self.limits.max_pages == 1:
            captures = await self._capture_single_page_viewports(
                home_url,
                robots_policy=robots,
                byte_budget=byte_budget,
                remaining_timeout=remaining_timeout,
            )
            pages = tuple(
                capture
                for capture in captures
                if isinstance(capture, ReferencePageEvidence)
            )
            failures = tuple(
                capture
                for capture in captures
                if isinstance(capture, Exception)
            )
            if failures:
                first_failure = failures[0]
                trace = getattr(first_failure, "trace", None)
                return ReferenceCrawlResult.failed(
                    source_url=home_url,
                    failure=CrawlFailure(
                        code="crawl_failed",
                        message=_exception_text(first_failure),
                    ),
                    trace=trace if isinstance(trace, TraceEvidence) else None,
                    pages=pages,
                    started_at=started_at,
                )
            if any(page.coverage_status != "complete" for page in pages):
                return ReferenceCrawlResult.partial(
                    source_url=home_url,
                    pages=pages,
                    started_at=started_at,
                )
            return ReferenceCrawlResult.succeeded(
                source_url=home_url,
                pages=pages,
                started_at=started_at,
            )

        accumulator = CrawlAccumulator(
            max_total_bytes=self.limits.max_total_bytes,
            selected_urls={home_url},
        )
        desktop_settings = self._settings("desktop")
        telemetry_by_page: dict[int, CaptureTelemetry] = {}
        attempt_by_request: dict[str, tuple[CaptureTelemetry, Any, int]] = {}
        trace_active: set[int] = set()
        document_origin = _origin_key(home_url)

        async def close_all_http_clients() -> None:
            await asyncio.gather(
                *(item.close_http_client() for item in telemetry_by_page.values()),
                return_exceptions=True,
            )

        async def stop_success_trace(context: Any) -> None:
            key = id(context.page)
            if key in trace_active:
                await context.page.context.tracing.stop()
                trace_active.discard(key)

        async def stop_failure_trace(request: Any) -> TraceEvidence | None:
            attempt = attempt_by_request.get(request.unique_key)
            if attempt is None:
                return None
            _telemetry, browser_context, key = attempt
            if key not in trace_active:
                return None
            trace_active.discard(key)
            return await _trace_failure(
                browser_context, self.limits.trace_ttl_seconds
            )

        storage_client = _RunScopedMemoryStorageClient()
        request_queue = await RequestQueue.open(
            alias=f"kaigo-reference-{uuid.uuid4().hex}",
            storage_client=storage_client,
        )

        async def open_memory_queue(**kwargs: Any) -> Any:
            return await RequestQueue.open(
                alias=kwargs.get("alias"), storage_client=storage_client
            )

        request_manager = ThrottlingRequestManager(
            request_queue,
            domains=[guarded.host],
            request_manager_opener=open_memory_queue,
        )
        crawler = PlaywrightCrawler(
            request_manager=request_manager,
            storage_client=storage_client,
            browser_type="chromium",
            headless=True,
            use_incognito_pages=True,
            fingerprint_generator=None,
            browser_new_context_options={
                "viewport": {
                    "width": desktop_settings.width,
                    "height": desktop_settings.height,
                },
                "device_scale_factor": 1,
                "user_agent": KAIGO_RESEARCH_USER_AGENT,
                "locale": "ru-RU",
                "permissions": [],
                "accept_downloads": False,
                "service_workers": "block",
            },
            goto_options={"wait_until": "domcontentloaded"},
            navigation_timeout=timedelta(seconds=self.limits.page_timeout_seconds),
            request_handler_timeout=_request_handler_timeout(self.limits),
            max_request_retries=self.limits.max_retries,
            max_requests_per_crawl=self.limits.max_pages,
            max_crawl_depth=self.limits.max_depth,
            use_session_pool=False,
            retry_on_blocked=False,
            # Crawlee's automatic fetch bypasses Kaigo's redirect-by-redirect
            # SSRF guard. Robots are therefore enforced by GuardedRobotsPolicy.
            respect_robots_txt_file=False,
            abort_on_error=False,
            concurrency_settings=ConcurrencySettings(
                min_concurrency=1, max_concurrency=1, desired_concurrency=1
            ),
        )

        @crawler.pre_navigation_hook
        async def guard_navigation(context: Any) -> None:
            await asyncio.to_thread(self.guard.validate, context.request.url)
            await robots.require_allowed(context.request.url)
            telemetry = CaptureTelemetry(
                max_page_bytes=self.limits.max_page_bytes,
                total_budget=byte_budget,
            )
            primary_page: dict[str, Any] = {"page": context.page}
            await _install_context_policy(
                context.page.context,
                primary_page=primary_page,
                guard=self.guard,
                robots=robots,
                allowed_document_origin=document_origin,
                telemetry=telemetry,
            )
            telemetry.attach_page(context.page)
            telemetry_by_page[id(context.page)] = telemetry
            attempt_by_request[context.request.unique_key] = (
                telemetry,
                context.page.context,
                id(context.page),
            )
            await context.page.context.tracing.start(
                screenshots=True, snapshots=True, sources=False
            )
            trace_active.add(id(context.page))

        @crawler.router.default_handler
        async def handler(context: PlaywrightCrawlingContext) -> None:
            user_data = context.request.user_data
            category = str(user_data.get("category", "other"))
            page_id = str(user_data.get("page_id", "home"))
            telemetry = telemetry_by_page[id(context.page)]
            evidence = await _capture_loaded_page(
                context.page,
                requested_url=context.request.url,
                page_id=page_id,
                category=category,
                viewport="desktop",
                settings=desktop_settings,
                guard=self.guard,
                telemetry=telemetry,
            )
            requests: list[Any] = []
            child_urls: list[str] = []
            skipped: list[str] = []
            if category == "home" and self.limits.max_pages > 1:
                nav_links = await context.page.locator("a[href]").evaluate_all(
                    """(links) => links.slice(0,500).map((a) => ({
                      url: a.href, text: (a.innerText || a.textContent || '').trim().slice(0,300)
                    }))"""
                )
                candidates = list(sitemap) + [
                    LinkCandidate(item["url"], item["text"], "navigation")
                    for item in nav_links
                ]
                selected = select_reference_pages(
                    evidence.final_url, candidates, max_pages=self.limits.max_pages
                )
                for index, selected_page in enumerate(selected[1:], start=1):
                    if selected_page.url in accumulator.selected_urls:
                        continue
                    try:
                        canonical_url = await _canonicalize_document_url(
                            selected_page.url,
                            guard=self.guard,
                            robots=robots,
                            timeout_seconds=min(
                                15, self.limits.page_timeout_seconds
                            ),
                        )
                    except (
                        ReferenceCaptureError,
                        RobotsDenied,
                        UnsafeReferenceUrl,
                    ):
                        skipped.append(
                            f"robots_denied: {sanitize_url_for_log(selected_page.url)}"
                        )
                        continue
                    if canonical_url in accumulator.selected_urls:
                        continue
                    child_urls.append(canonical_url)
                    requests.append(
                        Request.from_url(
                            canonical_url,
                            user_data={
                                "category": selected_page.category,
                                "page_id": _page_id(selected_page.category, index),
                            },
                            max_retries=self.limits.max_retries,
                        )
                    )
            if skipped:
                evidence = replace(
                    evidence,
                    skipped_reasons=evidence.skipped_reasons + tuple(skipped),
                )
            await accumulator.commit(
                evidence=evidence,
                child_urls=child_urls,
                enqueue=lambda: context.add_requests(requests) if requests else None,
            )
            await stop_success_trace(context)

        @crawler.error_handler
        async def on_retry(context: Any, _error: Exception) -> None:
            nonlocal failure_trace
            trace = await stop_failure_trace(context.request)
            if trace is not None:
                failure_trace = trace
            return None

        @crawler.failed_request_handler
        async def on_failed(
            context: Any, error: Exception
        ) -> None:
            nonlocal failure_trace
            attempt = attempt_by_request.get(context.request.unique_key)
            telemetry = attempt[0] if attempt is not None else None
            trace = await stop_failure_trace(context.request)
            if trace is not None:
                failure_trace = trace
            user_data = context.request.user_data
            category = str(user_data.get("category", "other"))
            page_id = str(user_data.get("page_id", "page-failed"))
            message = _sanitize_text(str(error))
            partial = ReferencePageEvidence(
                page_id=page_id,
                category=category,
                requested_url=context.request.url,
                final_url=context.request.url,
                depth=0 if category == "home" else 1,
                page_failures=(message,),
                request_failures=(
                    tuple(telemetry.request_failures) if telemetry is not None else ()
                ),
                policy_blocks=(
                    tuple(telemetry.policy_blocks) if telemetry is not None else ()
                ),
                transferred_bytes=(telemetry.response_bytes if telemetry else 0),
                scroll_strategy="not_captured",
                reset_strategy="not_captured",
                skipped_reasons=("capture_failed",),
                coverage_status="not_captured",
            )
            await accumulator.commit_partial(partial)

        initial = Request.from_url(
            home_url,
            user_data={"category": "home", "page_id": "home"},
            max_retries=self.limits.max_retries,
        )
        try:
            await asyncio.wait_for(crawler.run([initial]), timeout=remaining_timeout())
            desktop_pages = accumulator.pages
            desktop_home = next(
                (
                    page
                    for page in desktop_pages
                    if page.page_id == "home" and bool(page.screenshots)
                ),
                None,
            )
            if desktop_home is None:
                raise ReferenceCaptureError(
                    "no pages captured; robots policy or navigation rejected the site"
                )
            content_page = next(
                (
                    page
                    for page in desktop_pages
                    if page.category != "home" and bool(page.screenshots)
                ),
                None,
            )
            mobile_targets = [("home", "home", desktop_home.final_url)]
            if content_page is not None:
                mobile_targets.append(
                    (content_page.page_id, content_page.category, content_page.final_url)
                )
            for page_id, category, target_url in mobile_targets:
                mobile_id = f"{page_id}-mobile"
                try:
                    mobile = await asyncio.wait_for(
                        capture_reference_page(
                            target_url,
                            page_id=mobile_id,
                            category=category,
                            viewport="mobile",
                            settings=self._settings("mobile"),
                            guard=self.guard,
                            robots_policy=robots,
                            byte_budget=byte_budget,
                            trace_ttl_seconds=self.limits.trace_ttl_seconds,
                        ),
                        timeout=remaining_timeout(),
                    )
                except Exception as exc:
                    if page_id == "home":
                        raise
                    mobile = ReferencePageEvidence(
                        page_id=mobile_id,
                        category=category,
                        requested_url=target_url,
                        final_url=target_url,
                        depth=1,
                        page_failures=(_sanitize_text(str(exc)),),
                        scroll_strategy="not_captured",
                        reset_strategy="not_captured",
                        skipped_reasons=("mobile_capture_failed",),
                        coverage_status="not_captured",
                    )
                await accumulator.commit_partial(mobile)
        except Exception as exc:
            if isinstance(exc, ReferenceCaptureError) and exc.trace is not None:
                failure_trace = exc.trace
            await close_all_http_clients()
            return ReferenceCrawlResult.failed(
                source_url=home_url,
                failure=CrawlFailure(
                    code="crawl_failed", message=_exception_text(exc)
                ),
                trace=failure_trace,
                pages=accumulator.pages,
                started_at=started_at,
            )
        await close_all_http_clients()
        if any(
            page.coverage_status != "complete" for page in accumulator.pages
        ):
            return ReferenceCrawlResult.partial(
                source_url=home_url,
                pages=accumulator.pages,
                started_at=started_at,
            )
        return ReferenceCrawlResult.succeeded(
            source_url=home_url, pages=accumulator.pages, started_at=started_at
        )


def result_json(result: ReferenceCrawlResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
