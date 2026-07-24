from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from scripts.analyze_reference_site import (
    ReferenceAnalysisError,
    analyze_reference_site,
)

from .models import TokenUsage
from .reference_crawler import (
    ReferenceCrawlLimits,
    UnsafeReferenceUrl,
    VisualReferenceCrawler,
)
from .reference_models import ReferenceCrawlResult, ScreenshotEvidence


REFERENCE_STATES = (
    "desktop.top",
    "desktop.middle",
    "desktop.bottom",
    "mobile.top",
    "mobile.middle",
    "mobile.bottom",
)
MAX_REFERENCE_CONTEXT_CHARS = 8_000


class ReferencePipelineError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        public_message: str,
        *,
        diagnostic: str | None = None,
    ) -> None:
        super().__init__(public_message)
        self.error_code = error_code
        self.public_message = public_message
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class ReferenceAnalysisResult:
    context: str
    summary: str
    usage: TokenUsage = TokenUsage()


ReferenceAnalyzer = Callable[..., Awaitable[dict[str, Any]]]


def _usage(payload: dict[str, Any]) -> TokenUsage:
    provenance = payload.get("provenance")
    raw = provenance.get("usage") if isinstance(provenance, dict) else None
    if not isinstance(raw, dict):
        return TokenUsage()
    try:
        return TokenUsage(
            prompt_tokens=max(0, int(raw.get("prompt_tokens", 0))),
            output_tokens=max(0, int(raw.get("output_tokens", 0))),
            thinking_tokens=max(0, int(raw.get("thinking_tokens", 0))),
        )
    except (TypeError, ValueError):
        return TokenUsage()


def _copy_facts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement", "")).strip()
        evidence = item.get("evidence")
        if not statement or not isinstance(evidence, list):
            continue
        labels = [str(label) for label in evidence if str(label).strip()]
        if labels:
            result.append({"statement": statement, "evidence": labels})
    return result


def _copy_tokens(value: Any) -> dict[str, list[dict[str, Any]]]:
    source = value if isinstance(value, dict) else {}
    result: dict[str, list[dict[str, Any]]] = {}
    for category in ("palette", "typography", "geometry", "motion"):
        items = source.get(category)
        copied = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                token = str(item.get("token", "")).strip()
                token_value = str(item.get("value", "")).strip()
                evidence = item.get("evidence")
                if not token or not token_value or not isinstance(evidence, list):
                    continue
                labels = [str(label) for label in evidence if str(label).strip()]
                if labels:
                    copied.append(
                        {
                            "token": token,
                            "value": token_value,
                            "evidence": labels,
                        }
                    )
        result[category] = copied
    return result


def compile_reference_context(payload: dict[str, Any]) -> ReferenceAnalysisResult:
    if not isinstance(payload, dict):
        raise ReferencePipelineError(
            "reference_analysis_invalid",
            "Не удалось собрать визуальный бриф сайта",
        )
    source = payload.get("source")
    analysis = payload.get("analysis")
    if not isinstance(source, dict) or not isinstance(analysis, dict):
        raise ReferencePipelineError(
            "reference_analysis_invalid",
            "Не удалось собрать визуальный бриф сайта",
        )
    source_url = str(source.get("url", "")).strip()
    summary = str(analysis.get("visual_summary", "")).strip()
    facts = _copy_facts(analysis.get("public_facts"))
    tokens = _copy_tokens(analysis.get("visual_tokens"))
    if not source_url or not summary or not facts:
        raise ReferencePipelineError(
            "reference_analysis_invalid",
            "Gemini не смог подтвердить факты и визуальный стиль сайта",
        )

    compiled = {
        "source_url": source_url,
        "visual_summary": summary,
        "public_facts": facts,
        "visual_tokens": tokens,
    }

    def serialize() -> str:
        return json.dumps(
            compiled,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    context = serialize()
    while len(context) > MAX_REFERENCE_CONTEXT_CHARS:
        largest_category = max(tokens, key=lambda item: len(tokens[item]))
        if tokens[largest_category]:
            tokens[largest_category].pop()
        elif len(facts) > 1:
            facts.pop()
        elif len(facts[0]["statement"]) > 240:
            facts[0]["statement"] = facts[0]["statement"][:240]
        elif len(compiled["visual_summary"]) > 320:
            compiled["visual_summary"] = compiled["visual_summary"][:320]
        else:
            for item in facts:
                item["evidence"] = item["evidence"][:1]
            for category_items in tokens.values():
                for item in category_items:
                    item["evidence"] = item["evidence"][:1]
            context = serialize()
            if len(context) > MAX_REFERENCE_CONTEXT_CHARS:
                raise ReferencePipelineError(
                    "reference_analysis_too_large",
                    "Визуальный бриф сайта получился слишком большим",
                )
            break
        context = serialize()

    return ReferenceAnalysisResult(
        context=context,
        summary=summary,
        usage=_usage(payload),
    )


def _six_homepage_states(
    result: ReferenceCrawlResult,
) -> dict[str, ScreenshotEvidence]:
    states: dict[str, ScreenshotEvidence] = {}
    for page in result.pages:
        if page.category != "home":
            continue
        for screenshot in page.screenshots:
            state = f"{screenshot.viewport}.{screenshot.position}"
            if state in REFERENCE_STATES and state not in states:
                states[state] = screenshot
    if tuple(state for state in REFERENCE_STATES if state in states) != REFERENCE_STATES:
        raise ReferencePipelineError(
            "reference_capture_incomplete",
            "Не удалось снять главную страницу во всех desktop/mobile состояниях",
        )
    if any(states[state].data is None for state in REFERENCE_STATES):
        raise ReferencePipelineError(
            "reference_capture_incomplete",
            "Снимки сайта недоступны для визуального анализа",
        )
    return states


class GeminiReferencePipeline:
    def __init__(
        self,
        *,
        crawler: Any,
        analyzer: ReferenceAnalyzer = analyze_reference_site,
        api_key: str | None,
        model: str,
        thinking_level: str,
        base_url: str,
        timeout_seconds: float = 120,
    ) -> None:
        self._crawler = crawler
        self._analyzer = analyzer
        self._api_key = api_key
        self._model = model
        self._thinking_level = thinking_level
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_config(cls, config: Any) -> "GeminiReferencePipeline":
        limits = ReferenceCrawlLimits(
            max_pages=1,
            max_depth=0,
            total_timeout_seconds=config.reference_timeout_seconds,
            page_timeout_seconds=config.reference_page_timeout_seconds,
            max_total_bytes=config.reference_max_total_bytes,
            max_page_bytes=config.reference_max_page_bytes,
            max_retries=config.reference_max_retries,
            max_scroll_steps=config.reference_max_scroll_steps,
            scroll_delay_ms=config.reference_scroll_delay_ms,
            warmup_ms=config.reference_warmup_ms,
            final_settle_ms=config.reference_final_settle_ms,
            max_scroll_height=config.reference_max_scroll_height,
            trace_ttl_seconds=config.reference_trace_ttl_seconds,
            respect_robots=config.reference_respect_robots,
        )
        return cls(
            crawler=VisualReferenceCrawler(limits=limits),
            api_key=config.gemini_api_key,
            model=config.reference_analyzer_model,
            thinking_level=config.reference_analyzer_thinking_level,
            base_url=config.gemini_base_url,
            timeout_seconds=min(180, config.reference_timeout_seconds),
        )

    async def analyze(self, source_url: str) -> ReferenceAnalysisResult:
        try:
            crawl = await self._crawler.crawl(source_url)
        except UnsafeReferenceUrl as exc:
            raise ReferencePipelineError(
                "reference_url_unsafe",
                "Можно анализировать только публичные сайты",
                diagnostic=str(exc)[:1_000],
            ) from exc
        except Exception as exc:
            raise ReferencePipelineError(
                "reference_capture_failed",
                "Не удалось открыть сайт для визуального анализа",
                diagnostic=f"{type(exc).__name__}: {str(exc)[:1_000]}",
            ) from exc
        if crawl.status != "succeeded":
            diagnostic = crawl.failure.message if crawl.failure else crawl.status
            raise ReferencePipelineError(
                "reference_capture_failed",
                "Не удалось полностью снять главную страницу сайта",
                diagnostic=diagnostic[:1_000],
            )

        states = _six_homepage_states(crawl)
        host = (urlsplit(source_url).hostname or "").lower().rstrip(".")
        with tempfile.TemporaryDirectory(prefix="kaigo-reference-") as temporary:
            root = Path(temporary)
            screenshots_dir = root / "screenshots"
            screenshots_dir.mkdir()
            screenshot_inputs = []
            for state in REFERENCE_STATES:
                path = screenshots_dir / f"{state.replace('.', '-')}.jpg"
                path.write_bytes(states[state].data or b"")
                screenshot_inputs.append((state, path))
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(crawl.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            try:
                analysis = await self._analyzer(
                    source_url=source_url,
                    allowed_hosts={host},
                    screenshot_inputs=tuple(screenshot_inputs),
                    evidence_root=root,
                    captured_at=crawl.completed_at.isoformat(),
                    coverage_status="complete",
                    capture_manifest=manifest,
                    api_key=self._api_key,
                    model=self._model,
                    thinking_level=self._thinking_level,
                    base_url=self._base_url,
                    timeout_seconds=self._timeout_seconds,
                )
            except ReferenceAnalysisError as exc:
                error_code = (
                    "missing_api_key"
                    if exc.error_code == "missing_api_key"
                    else "reference_analysis_failed"
                )
                raise ReferencePipelineError(
                    error_code,
                    exc.public_message,
                    diagnostic=str(
                        getattr(exc, "diagnostic", None) or str(exc)
                    )[:1_000],
                ) from exc
            except Exception as exc:
                raise ReferencePipelineError(
                    "reference_analysis_failed",
                    "Gemini не завершил визуальный анализ сайта",
                    diagnostic=f"{type(exc).__name__}: {str(exc)[:1_000]}",
                ) from exc
        return compile_reference_context(analysis)


__all__ = [
    "GeminiReferencePipeline",
    "MAX_REFERENCE_CONTEXT_CHARS",
    "REFERENCE_STATES",
    "ReferenceAnalysisResult",
    "ReferencePipelineError",
    "compile_reference_context",
]
