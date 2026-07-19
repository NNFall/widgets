from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

from .reference_models import (
    CrawlFailure,
    ReferenceCrawlResult,
    ReferencePageEvidence,
    ScreenshotEvidence,
    TraceEvidence,
)


KAIGO_RESEARCH_USER_AGENT = "KaigoVisualResearch/1.0 (+https://kaigo.space)"
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


class ReferenceCaptureError(RuntimeError):
    def __init__(self, message: str, *, trace: TraceEvidence | None = None) -> None:
        super().__init__(message)
        self.trace = trace


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
    total_timeout_seconds: int = 180
    page_timeout_seconds: int = 45
    max_total_bytes: int = 40 * 1024 * 1024
    max_page_bytes: int = 10 * 1024 * 1024
    max_retries: int = 1
    max_scroll_steps: int = 24
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


@dataclass(frozen=True)
class CaptureSettings:
    width: int
    height: int
    warmup_ms: int = 5000
    scroll_delay_ms: int = 750
    final_settle_ms: int = 1500
    max_scroll_steps: int = 24
    max_scroll_height: int = 50_000
    page_timeout_seconds: int = 45
    max_page_bytes: int = 10 * 1024 * 1024

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
            max_total_bytes=max(self.max_page_bytes, 40 * 1024 * 1024),
        )


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


async def _wait_for_fonts_and_visible_images(page: Any, timeout_ms: int) -> None:
    await page.wait_for_function(
        """async () => {
          if (document.fonts && document.fonts.ready) await document.fonts.ready;
          const images = [...document.images].slice(0, 500).filter((img) => {
            const r = img.getBoundingClientRect();
            const s = getComputedStyle(img);
            return r.width > 1 && r.height > 1 && s.display !== 'none' && s.visibility !== 'hidden';
          });
          return images.every((img) => img.complete);
        }""",
        timeout=timeout_ms,
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
  const visible = [];
  for (let y = 40; y < innerHeight; y += Math.max(80, Math.floor(innerHeight / 8))) {
    for (const el of document.elementsFromPoint(innerWidth / 2, y)) {
      const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
      if (text && text.length < 180 && !visible.includes(text)) visible.push(text);
      if (visible.length >= 20) break;
    }
  }
  return {
    kind: scroller === root ? 'document' : 'element',
    top: scroller === root ? (scrollY || root.scrollTop || 0) : scroller.scrollTop,
    height: scroller.scrollHeight,
    client: scroller === root ? innerHeight : scroller.clientHeight,
    transformSignature: transformNodes.map((el) => `${el.tagName}:${getComputedStyle(el).transform}`).join('|'),
    visibleSignature: visible.join('|'),
    visible
  };
}"""


async def _scroll_once(page: Any, state: Mapping[str, Any], step: int) -> None:
    viewport = await page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    await page.mouse.move(0.5 * viewport["width"], 0.5 * viewport["height"])
    await page.mouse.wheel(0, step)
    await page.wait_for_timeout(50)
    after = await page.evaluate(_SCROLL_STATE_SCRIPT)
    if int(after["top"]) <= int(state["top"]) and after["transformSignature"] == state["transformSignature"]:
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


async def _take_screenshot(
    page: Any,
    *,
    page_id: str,
    viewport: str,
    position: str,
    width: int,
    height: int,
) -> ScreenshotEvidence:
    data = await page.screenshot(type="jpeg", quality=82, full_page=False, animations="disabled")
    return ScreenshotEvidence(
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
  const moving = descriptors.filter((item) => item.transition !== 'all 0s ease 0s' || item.animation !== 'none').slice(0,30);
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


async def _capture_loaded_page(
    page: Any,
    *,
    requested_url: str,
    page_id: str,
    category: str,
    viewport: str,
    settings: CaptureSettings,
    guard: UrlGuard | None,
) -> ReferencePageEvidence:
    started = time.monotonic()
    console_failures: list[str] = []
    page_failures: list[str] = []
    request_failures: list[str] = []
    observed_texts: list[str] = []
    response_bytes = 0
    declared_bytes = 0
    budget_exceeded = asyncio.Event()
    transfer_tasks: set[asyncio.Task[Any]] = set()

    def on_console(message: Any) -> None:
        if message.type in {"error", "warning"} and len(console_failures) < 100:
            console_failures.append(f"{message.type}: {message.text}"[:1000])

    def on_page_error(error: Exception) -> None:
        if len(page_failures) < 100:
            page_failures.append(str(error)[:1000])

    def on_request_failed(request: Any) -> None:
        if len(request_failures) < 100:
            request_failures.append(f"{request.method} {request.url}: {request.failure}"[:1000])

    async def collect_size(request: Any) -> None:
        nonlocal response_bytes
        try:
            sizes = await request.sizes()
            response_bytes += max(0, int(sizes.get("responseBodySize", 0)))
        except Exception:
            return

    def on_request_finished(request: Any) -> None:
        task = asyncio.create_task(collect_size(request))
        transfer_tasks.add(task)
        task.add_done_callback(transfer_tasks.discard)

    def on_response(response: Any) -> None:
        nonlocal declared_bytes
        try:
            size = int(response.headers.get("content-length", "0"))
        except (TypeError, ValueError):
            size = 0
        declared_bytes += max(0, size)
        if declared_bytes > settings.max_page_bytes:
            budget_exceeded.set()

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_request_failed)
    page.on("requestfinished", on_request_finished)
    page.on("response", on_response)

    timeout_ms = settings.page_timeout_seconds * 1000
    await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    await page.wait_for_load_state("load", timeout=timeout_ms)
    if budget_exceeded.is_set():
        raise ReferenceCaptureError("page byte limit exceeded")
    if guard is not None:
        await asyncio.to_thread(guard.validate_redirect, page.url)
    await _wait_for_fonts_and_visible_images(page, timeout_ms)
    await page.wait_for_timeout(settings.warmup_ms)
    if budget_exceeded.is_set():
        raise ReferenceCaptureError("page byte limit exceeded")
    initial_hidden = await page.evaluate(
        """() => [...document.querySelectorAll('body *')].slice(0, 5000).filter((el) => {
          const s=getComputedStyle(el); const r=el.getBoundingClientRect();
          return /reveal|animate|hidden/i.test(el.className || '') && r.width>1 && r.height>1 && (s.opacity==='0' || s.visibility==='hidden');
        }).length"""
    )

    screenshots: dict[str, ScreenshotEvidence] = {}
    state = await page.evaluate(_SCROLL_STATE_SCRIPT)
    last_height = int(state["height"])
    stable_height_at_end = 0
    stable_visible = 0
    previous_visible = ""
    step_px = max(1, int(settings.height * 0.7))
    for step_index in range(settings.max_scroll_steps):
        observed_texts.extend(state.get("visible", ()))
        if state.get("visibleSignature") == previous_visible:
            stable_visible += 1
        else:
            stable_visible = 0
            previous_visible = str(state.get("visibleSignature", ""))
        progress = min(1.0, (int(state["top"]) + int(state["client"])) / max(1, int(state["height"])))
        if (
            progress >= 0.38
            and "middle" not in screenshots
            and (int(state["height"]) > int(state["client"]) or step_index > 0)
        ):
            screenshots["middle"] = await _take_screenshot(
                page, page_id=page_id, viewport=viewport, position="middle",
                width=settings.width, height=settings.height,
            )
        at_end = progress >= 0.985
        if at_end and int(state["height"]) == last_height:
            stable_height_at_end += 1
        else:
            stable_height_at_end = 0
        if at_end and stable_height_at_end >= 2:
            break
        if int(state["height"]) > settings.max_scroll_height:
            page_failures.append("scroll height cap reached")
            break
        await _scroll_once(page, state, step_px)
        await page.wait_for_timeout(settings.scroll_delay_ms)
        if budget_exceeded.is_set():
            raise ReferenceCaptureError("page byte limit exceeded")
        next_state = await page.evaluate(_SCROLL_STATE_SCRIPT)
        if (
            int(next_state["top"]) == int(state["top"])
            and next_state.get("transformSignature") == state.get("transformSignature")
            and stable_visible >= 2
        ):
            state = next_state
            break
        last_height = int(state["height"])
        state = next_state
    observed_texts.extend(state.get("visible", ()))
    screenshots["bottom"] = await _take_screenshot(
        page, page_id=page_id, viewport=viewport, position="bottom",
        width=settings.width, height=settings.height,
    )
    if "middle" not in screenshots:
        screenshots["middle"] = await _take_screenshot(
            page, page_id=page_id, viewport=viewport, position="middle",
            width=settings.width, height=settings.height,
        )

    sample = await page.evaluate(
        _SAMPLE_SCRIPT,
        {"initialHidden": int(initial_hidden), "observedTexts": observed_texts[:160]},
    )

    # A reload is deliberately used for stable top evidence. On virtual/smooth
    # scroll sites, scrollY and bounding boxes can report a logical top while a
    # transformed scene remains elsewhere. Full-page stitching is never used as
    # the sole oracle.
    await page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
    await page.wait_for_load_state("load", timeout=timeout_ms)
    if budget_exceeded.is_set():
        raise ReferenceCaptureError("page byte limit exceeded")
    if guard is not None:
        await asyncio.to_thread(guard.validate_redirect, page.url)
    await _wait_for_fonts_and_visible_images(page, timeout_ms)
    await page.wait_for_timeout(settings.final_settle_ms)
    screenshots["top"] = await _take_screenshot(
        page, page_id=page_id, viewport=viewport, position="top",
        width=settings.width, height=settings.height,
    )
    if transfer_tasks:
        await asyncio.gather(*tuple(transfer_tasks), return_exceptions=True)
    screenshot_bytes = sum(item.size_bytes for item in screenshots.values())
    transferred_bytes = response_bytes + screenshot_bytes
    if transferred_bytes > settings.max_page_bytes:
        raise ReferenceCaptureError("page byte limit exceeded")
    return ReferencePageEvidence(
        page_id=page_id,
        category=category,
        requested_url=requested_url,
        final_url=page.url,
        depth=0 if category == "home" else 1,
        screenshots=tuple(screenshots[position] for position in ("top", "middle", "bottom")),
        semantic_sample=sample["semantic"],
        style_sample=sample["style"],
        console_failures=tuple(console_failures),
        page_failures=tuple(page_failures),
        request_failures=tuple(request_failures),
        timings_ms={"total": round((time.monotonic() - started) * 1000, 1)},
        transferred_bytes=transferred_bytes,
    )


async def _route_with_guard(route: Any, request: Any, guard: UrlGuard | None) -> None:
    parsed = urlsplit(request.url)
    if parsed.scheme not in {"http", "https"}:
        await route.abort("blockedbyclient")
        return
    if request.method.upper() not in {"GET", "HEAD"}:
        await route.abort("blockedbyclient")
        return
    if request.resource_type in {"media", "eventsource"}:
        await route.abort("blockedbyclient")
        return
    if guard is not None:
        try:
            await asyncio.to_thread(guard.validate, request.url)
        except UnsafeReferenceUrl:
            await route.abort("blockedbyclient")
            return
    await route.continue_()


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


async def capture_reference_page(
    url: str,
    *,
    page_id: str,
    category: str,
    viewport: str,
    settings: CaptureSettings,
    guard: UrlGuard | None = None,
    trace_ttl_seconds: int = 3600,
) -> ReferencePageEvidence:
    """Capture one explicitly supplied page without discovery or LLM calls."""
    from playwright.async_api import async_playwright

    if guard is not None:
        await asyncio.to_thread(guard.validate, url)
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
        await context.clear_cookies()
        page = await context.new_page()
        await page.route(
            "**/*", lambda route, request: _route_with_guard(route, request, guard)
        )
        await page.route_web_socket(
            "**/*", lambda route: route.close(code=1008, reason="blocked")
        )
        await context.tracing.start(screenshots=True, snapshots=True, sources=False)
        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=settings.page_timeout_seconds * 1000,
            )
            evidence = await _capture_loaded_page(
                page,
                requested_url=url,
                page_id=page_id,
                category=category,
                viewport=viewport,
                settings=settings,
                guard=guard,
            )
        except Exception as exc:
            trace = await _trace_failure(context, trace_ttl_seconds)
            raise ReferenceCaptureError(str(exc), trace=trace) from exc
        else:
            await context.tracing.stop()
            return evidence
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
        width, height = ((1440, 900) if viewport == "desktop" else (390, 844))
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

    async def crawl(self, source_url: str) -> ReferenceCrawlResult:
        from crawlee import ConcurrencySettings, Request
        from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
        from crawlee.request_loaders import ThrottlingRequestManager
        from crawlee.storage_clients import MemoryStorageClient
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
        pages: list[ReferencePageEvidence] = []
        failure_trace: TraceEvidence | None = None
        total_bytes = 0
        selected_urls: set[str] = {home_url}

        storage_client = MemoryStorageClient()
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
                "viewport": {"width": 1440, "height": 900},
                "device_scale_factor": 1,
                "user_agent": KAIGO_RESEARCH_USER_AGENT,
                "locale": "ru-RU",
                "permissions": [],
                "accept_downloads": False,
                "service_workers": "block",
            },
            goto_options={"wait_until": "domcontentloaded"},
            navigation_timeout=timedelta(seconds=self.limits.page_timeout_seconds),
            request_handler_timeout=timedelta(seconds=self.limits.page_timeout_seconds * 3),
            max_request_retries=self.limits.max_retries,
            max_requests_per_crawl=self.limits.max_pages,
            max_crawl_depth=self.limits.max_depth,
            use_session_pool=False,
            retry_on_blocked=False,
            respect_robots_txt_file=self.limits.respect_robots,
            abort_on_error=True,
            concurrency_settings=ConcurrencySettings(
                min_concurrency=1, max_concurrency=1, desired_concurrency=1
            ),
        )

        @crawler.pre_navigation_hook
        async def guard_navigation(context: Any) -> None:
            await asyncio.to_thread(self.guard.validate, context.request.url)
            await context.page.route(
                "**/*",
                lambda route, request: _route_with_guard(route, request, self.guard),
            )
            await context.page.route_web_socket(
                "**/*",
                lambda route: route.close(code=1008, reason="blocked"),
            )

        @crawler.post_navigation_hook
        async def guard_redirects(context: Any) -> None:
            request = context.response.request
            chain: list[str] = []
            while request is not None:
                chain.append(request.url)
                request = request.redirected_from
            for redirected_url in reversed(chain):
                await asyncio.to_thread(self.guard.validate_redirect, redirected_url)

        @crawler.router.default_handler
        async def handler(context: PlaywrightCrawlingContext) -> None:
            nonlocal total_bytes, failure_trace
            user_data = context.request.user_data
            category = str(user_data.get("category", "other"))
            page_id = str(user_data.get("page_id", "home"))
            await context.page.context.tracing.start(
                screenshots=True, snapshots=True, sources=False
            )
            try:
                evidence = await _capture_loaded_page(
                    context.page,
                    requested_url=context.request.url,
                    page_id=page_id,
                    category=category,
                    viewport="desktop",
                    settings=self._settings("desktop"),
                    guard=self.guard,
                )
                total_bytes += evidence.transferred_bytes
                if total_bytes > self.limits.max_total_bytes:
                    raise ReferenceCaptureError("crawl byte limit exceeded")
                pages.append(evidence)
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
                    requests = []
                    for index, selected_page in enumerate(selected[1:], start=1):
                        if selected_page.url in selected_urls:
                            continue
                        selected_urls.add(selected_page.url)
                        requests.append(
                            Request.from_url(
                                selected_page.url,
                                user_data={
                                    "category": selected_page.category,
                                    "page_id": _page_id(selected_page.category, index),
                                },
                                max_retries=self.limits.max_retries,
                            )
                        )
                    if requests:
                        await context.add_requests(requests)
            except Exception as exc:
                failure_trace = await _trace_failure(
                    context.page.context, self.limits.trace_ttl_seconds
                )
                raise ReferenceCaptureError(str(exc), trace=failure_trace) from exc
            else:
                await context.page.context.tracing.stop()

        initial = Request.from_url(
            home_url,
            user_data={"category": "home", "page_id": "home"},
            max_retries=self.limits.max_retries,
        )
        try:
            await asyncio.wait_for(crawler.run([initial]), timeout=remaining_timeout())
            desktop_pages = tuple(pages)
            if not desktop_pages:
                raise ReferenceCaptureError(
                    "no pages captured; robots policy or navigation rejected the site"
                )
            content_page = next((page for page in desktop_pages if page.category != "home"), None)
            mobile_targets = [
                ("home", "home", desktop_pages[0].final_url)
            ]
            if content_page is not None:
                mobile_targets.append(
                    (content_page.page_id, content_page.category, content_page.final_url)
                )
            for page_id, category, target_url in mobile_targets:
                mobile = await asyncio.wait_for(
                    capture_reference_page(
                        target_url,
                        page_id=f"{page_id}-mobile",
                        category=category,
                        viewport="mobile",
                        settings=self._settings("mobile"),
                        guard=self.guard,
                        trace_ttl_seconds=self.limits.trace_ttl_seconds,
                    ),
                    timeout=remaining_timeout(),
                )
                total_bytes += mobile.transferred_bytes
                if total_bytes > self.limits.max_total_bytes:
                    raise ReferenceCaptureError("crawl byte limit exceeded")
                pages.append(mobile)
        except Exception as exc:
            if isinstance(exc, ReferenceCaptureError) and exc.trace is not None:
                failure_trace = exc.trace
            return ReferenceCrawlResult.failed(
                source_url=home_url,
                failure=CrawlFailure(code="crawl_failed", message=str(exc)[:1000]),
                trace=failure_trace,
                pages=tuple(pages),
                started_at=started_at,
            )
        return ReferenceCrawlResult.succeeded(
            source_url=home_url,
            pages=tuple(pages),
            started_at=started_at,
        )


def result_json(result: ReferenceCrawlResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
