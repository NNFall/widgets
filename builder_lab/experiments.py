from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from .browser_audit import BrowserAuditReport
from .comparison import ComparisonVariant, render_comparison_page
from .contracts import resolve_widget_contract
from .models import BuilderRequest, CreativeProfile, TokenUsage, WidgetArtifact
from .preview import build_preview_document
from .strict_visual_models import StrictVisualCritique


ABC_PROFILES = (
    CreativeProfile.PRODUCT_CHAT,
    CreativeProfile.BRAND_MOTION,
    CreativeProfile.AI_CHARACTER,
)
PUBLIC_SLUGS = {
    CreativeProfile.PRODUCT_CHAT: "product-chat",
    CreativeProfile.BRAND_MOTION: "brand-motion",
    CreativeProfile.AI_CHARACTER: "ai-character",
}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_STATUS_VALUES = frozenset({"completed", "failed"})
_MAX_MANIFEST_BYTES = 12 * 1024 * 1024
_MAX_EVENTS = 48
_T = TypeVar("_T")


def _text(value: Any, field_name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    result = value.strip()
    if not result or len(result) > limit or "\x00" in result:
        raise ValueError(f"{field_name} is invalid")
    return result


def _finite(
    value: Any,
    field_name: str,
    *,
    minimum: float = 0,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{field_name} is out of bounds")
    return result


def _digest(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase 64hex digest")
    return value


def _snapshot_artifact(artifact: WidgetArtifact) -> WidgetArtifact:
    return WidgetArtifact.from_dict(artifact.to_dict())


@dataclass(frozen=True)
class ExperimentPricingSnapshot:
    currency: str
    prompt_per_million: float
    output_per_million: float
    thinking_per_million: float
    captured_at: str
    source: str

    def __post_init__(self) -> None:
        if self.currency != "USD":
            raise ValueError("pricing currency must be USD")
        for name in (
            "prompt_per_million",
            "output_per_million",
            "thinking_per_million",
        ):
            object.__setattr__(
                self,
                name,
                _finite(getattr(self, name), name, maximum=100),
            )
        captured_at = _text(self.captured_at, "captured_at", 64)
        try:
            parsed = datetime.fromisoformat(captured_at)
        except ValueError as exc:
            raise ValueError("captured_at must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("captured_at must be timezone aware")
        source = _text(self.source, "pricing source", 500)
        if not source.startswith("https://"):
            raise ValueError("pricing source must be HTTPS")
        object.__setattr__(self, "captured_at", captured_at)
        object.__setattr__(self, "source", source)

    def cost(self, usage: TokenUsage) -> float:
        if not isinstance(usage, TokenUsage):
            raise TypeError("usage must be TokenUsage")
        return (
            usage.prompt_tokens * self.prompt_per_million
            + usage.output_tokens * self.output_per_million
            + usage.thinking_tokens * self.thinking_per_million
        ) / 1_000_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "prompt_per_million": self.prompt_per_million,
            "output_per_million": self.output_per_million,
            "thinking_per_million": self.thinking_per_million,
            "captured_at": self.captured_at,
            "source": self.source,
        }


@dataclass(frozen=True)
class ExperimentRoleEvent:
    role: str
    status: str
    summary: str
    usage: TokenUsage = field(default_factory=TokenUsage)

    def __post_init__(self) -> None:
        role = _text(self.role, "role", 96)
        if not _IDENTIFIER.fullmatch(role):
            raise ValueError("role is invalid")
        status = _text(self.status, "role status", 32)
        if status not in {"started", "completed", "failed"}:
            raise ValueError("role status is invalid")
        summary = _text(self.summary, "role summary", 1000)
        if not isinstance(self.usage, TokenUsage):
            raise ValueError("role usage must be TokenUsage")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "summary", summary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "status": self.status,
            "summary": self.summary,
            "usage": self.usage.to_dict(),
        }


@dataclass(frozen=True)
class ExperimentEvidence:
    artifact: WidgetArtifact
    audit: BrowserAuditReport
    critique: StrictVisualCritique

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, WidgetArtifact):
            raise ValueError("evidence artifact must be WidgetArtifact")
        if not isinstance(self.audit, BrowserAuditReport):
            raise ValueError("evidence audit must be BrowserAuditReport")
        if not isinstance(self.critique, StrictVisualCritique):
            raise ValueError("evidence critique must be StrictVisualCritique")
        object.__setattr__(self, "artifact", _snapshot_artifact(self.artifact))

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact.to_dict(),
            "audit": {
                "total_bytes": self.audit.total_bytes,
                "screenshots": [
                    screenshot.evidence.to_dict()
                    for screenshot in self.audit.screenshots
                ],
                "layouts": [layout.to_dict() for layout in self.audit.layouts],
            },
            "critique": self.critique.to_dict(),
        }


@dataclass(frozen=True)
class ExperimentVariant:
    profile: CreativeProfile
    public_slug: str
    run_id: str
    model: str
    thinking: str
    status: str
    raw: ExperimentEvidence | None
    final: ExperimentEvidence | None
    usage: TokenUsage
    elapsed_seconds: float
    pricing: ExperimentPricingSnapshot
    cost_usd: float
    role_events: tuple[ExperimentRoleEvent, ...] = ()
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.profile not in ABC_PROFILES:
            raise ValueError("variant profile must be an A/B/C profile")
        if self.public_slug != PUBLIC_SLUGS[self.profile]:
            raise ValueError("variant public slug is not stable for its profile")
        run_id = _text(self.run_id, "run_id", 128)
        model = _text(self.model, "model", 120)
        thinking = _text(self.thinking, "thinking", 32)
        status = _text(self.status, "status", 32)
        if status not in _STATUS_VALUES:
            raise ValueError("variant status is invalid")
        if not isinstance(self.usage, TokenUsage):
            raise ValueError("variant usage must be TokenUsage")
        elapsed = _finite(
            self.elapsed_seconds,
            "elapsed_seconds",
            maximum=7 * 24 * 60 * 60,
        )
        if not isinstance(self.pricing, ExperimentPricingSnapshot):
            raise ValueError("variant pricing must be a pricing snapshot")
        expected_cost = self.pricing.cost(self.usage)
        cost = _finite(self.cost_usd, "cost_usd", maximum=100_000)
        if not math.isclose(cost, expected_cost, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("cost_usd does not match the pricing snapshot")
        events = tuple(self.role_events)
        if len(events) > _MAX_EVENTS or any(
            not isinstance(item, ExperimentRoleEvent) for item in events
        ):
            raise ValueError("role events are invalid")
        if status == "completed":
            if (
                not isinstance(self.raw, ExperimentEvidence)
                or not isinstance(self.final, ExperimentEvidence)
            ):
                raise ValueError("completed variant requires raw and final evidence")
            if self.raw.artifact == self.final.artifact:
                raise ValueError("completed variant raw and final artifacts must differ")
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("completed variant cannot contain an error")
        else:
            if self.final is not None:
                raise ValueError("failed variant cannot fabricate final evidence")
            error_code = _text(self.error_code, "error_code", 96)
            if not _IDENTIFIER.fullmatch(error_code):
                raise ValueError("error_code is invalid")
            object.__setattr__(
                self,
                "error_message",
                _text(self.error_message, "error_message", 2000),
            )
            object.__setattr__(self, "error_code", error_code)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "thinking", thinking)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "elapsed_seconds", elapsed)
        object.__setattr__(self, "cost_usd", cost)
        object.__setattr__(self, "role_events", events)

    @classmethod
    def create(
        cls,
        *,
        profile: CreativeProfile,
        public_slug: str,
        run_id: str,
        model: str,
        thinking: str,
        status: str,
        raw: ExperimentEvidence | None,
        final: ExperimentEvidence | None,
        usage: TokenUsage,
        elapsed_seconds: float,
        pricing: ExperimentPricingSnapshot,
        role_events: Sequence[ExperimentRoleEvent] = (),
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> "ExperimentVariant":
        return cls(
            profile=profile,
            public_slug=public_slug,
            run_id=run_id,
            model=model,
            thinking=thinking,
            status=status,
            raw=raw,
            final=final,
            usage=usage,
            elapsed_seconds=elapsed_seconds,
            pricing=pricing,
            cost_usd=pricing.cost(usage),
            role_events=tuple(role_events),
            error_code=error_code,
            error_message=error_message,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "public_slug": self.public_slug,
            "run_id": self.run_id,
            "model": self.model,
            "thinking": self.thinking,
            "status": self.status,
            "raw": self.raw.to_dict() if self.raw else None,
            "final": self.final.to_dict() if self.final else None,
            "usage": self.usage.to_dict(),
            "elapsed_seconds": self.elapsed_seconds,
            "pricing": self.pricing.to_dict(),
            "cost_usd": self.cost_usd,
            "role_events": [item.to_dict() for item in self.role_events],
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class AbcExperimentManifest:
    schema_version: int
    generated_at: str
    source_digest: str
    common_input_digest: str
    contract_id: str
    variants: tuple[ExperimentVariant, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported experiment manifest schema")
        try:
            generated = datetime.fromisoformat(self.generated_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("generated_at must be ISO-8601") from exc
        if generated.tzinfo is None:
            raise ValueError("generated_at must be timezone aware")
        _digest(self.source_digest, "source_digest")
        _digest(self.common_input_digest, "common_input_digest")
        resolve_widget_contract(self.contract_id)
        variants = tuple(self.variants)
        profiles = tuple(item.profile for item in variants)
        if profiles != ABC_PROFILES:
            raise ValueError("manifest requires exactly A/B/C profiles once")
        if len({item.public_slug for item in variants}) != 3:
            raise ValueError("manifest public slugs must be unique")
        if len({item.run_id for item in variants}) != 3:
            raise ValueError("manifest run ids must be unique")
        if len({(item.model, item.thinking) for item in variants}) != 1:
            raise ValueError("manifest variants must use the same model and thinking")
        object.__setattr__(self, "variants", variants)

    @classmethod
    def create(
        cls,
        *,
        source_digest: str,
        common_input_digest: str,
        contract_id: str,
        variants: Sequence[ExperimentVariant],
        generated_at: str | None = None,
    ) -> "AbcExperimentManifest":
        by_profile = {item.profile: item for item in variants}
        if len(by_profile) != len(variants) or set(by_profile) != set(ABC_PROFILES):
            raise ValueError("manifest requires exactly A/B/C profiles once")
        return cls(
            schema_version=1,
            generated_at=generated_at
            or datetime.now(timezone.utc).isoformat(),
            source_digest=source_digest,
            common_input_digest=common_input_digest,
            contract_id=contract_id,
            variants=tuple(by_profile[profile] for profile in ABC_PROFILES),
        )

    @property
    def total_cost_usd(self) -> float:
        return sum(item.cost_usd for item in self.variants)

    def variant(self, profile: CreativeProfile) -> ExperimentVariant:
        return next(item for item in self.variants if item.profile is profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "source_digest": self.source_digest,
            "common_input_digest": self.common_input_digest,
            "contract_id": self.contract_id,
            "total_cost_usd": self.total_cost_usd,
            "variants": [item.to_dict() for item in self.variants],
        }


def canonical_common_input_digest(
    *,
    source_digest: str,
    request: BuilderRequest,
    model: str,
    thinking: str,
) -> str:
    _digest(source_digest, "source_digest")
    if not isinstance(request, BuilderRequest):
        raise TypeError("request must be BuilderRequest")
    request_payload = request.to_dict()
    request_payload.pop("creative_profile")
    payload = {
        "source_digest": source_digest,
        "request": request_payload,
        "model": _text(model, "model", 120),
        "thinking": _text(thinking, "thinking", 32),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class VariantExecutionError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        usage: TokenUsage | None = None,
        elapsed_seconds: float = 0,
        raw: ExperimentEvidence | None = None,
        role_events: Sequence[ExperimentRoleEvent] = (),
    ) -> None:
        super().__init__(message)
        self.error_code = _text(error_code, "error_code", 96)
        self.public_message = _text(message, "error message", 2000)
        self.usage = usage or TokenUsage()
        self.elapsed_seconds = _finite(
            elapsed_seconds,
            "elapsed_seconds",
            maximum=7 * 24 * 60 * 60,
        )
        self.raw = raw
        self.role_events = tuple(role_events)


@dataclass(frozen=True)
class ExperimentVariantContext:
    source_digest: str
    common_input_digest: str
    request: BuilderRequest
    run_id: str
    model: str
    thinking: str
    pricing: ExperimentPricingSnapshot
    audit_semaphore: asyncio.Semaphore

    async def run_browser_audit(
        self,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        async with self.audit_semaphore:
            return await operation()


async def run_abc_experiment(
    *,
    source_digest: str,
    base_request: BuilderRequest,
    model: str,
    thinking: str,
    pricing: ExperimentPricingSnapshot,
    execute_variant: Callable[
        [ExperimentVariantContext], Awaitable[ExperimentVariant]
    ],
    run_id_factory: Callable[[CreativeProfile], str] | None = None,
) -> AbcExperimentManifest:
    _digest(source_digest, "source_digest")
    if not isinstance(base_request, BuilderRequest):
        raise TypeError("base_request must be BuilderRequest")
    if base_request.visual_repair_limit != 1:
        raise ValueError("A/B/C experiment requires exactly one visual revision limit")
    model = _text(model, "model", 120)
    thinking = _text(thinking, "thinking", 32)
    if not isinstance(pricing, ExperimentPricingSnapshot):
        raise TypeError("pricing must be ExperimentPricingSnapshot")
    common_digest = canonical_common_input_digest(
        source_digest=source_digest,
        request=base_request,
        model=model,
        thinking=thinking,
    )
    audit_semaphore = asyncio.Semaphore(1)
    run_id_factory = run_id_factory or (
        lambda profile: f"abc-{profile.value}-{secrets.token_hex(12)}"
    )
    contexts = tuple(
        ExperimentVariantContext(
            source_digest=source_digest,
            common_input_digest=common_digest,
            request=replace(base_request, creative_profile=profile),
            run_id=_text(run_id_factory(profile), "run_id", 128),
            model=model,
            thinking=thinking,
            pricing=pricing,
            audit_semaphore=audit_semaphore,
        )
        for profile in ABC_PROFILES
    )
    if len({item.run_id for item in contexts}) != len(contexts):
        raise ValueError("run_id_factory must produce independent run ids")

    async def run_one(context: ExperimentVariantContext) -> ExperimentVariant:
        try:
            result = await execute_variant(context)
        except asyncio.CancelledError:
            raise
        except VariantExecutionError as exc:
            return ExperimentVariant.create(
                profile=context.request.creative_profile,
                public_slug=PUBLIC_SLUGS[context.request.creative_profile],
                run_id=context.run_id,
                model=context.model,
                thinking=context.thinking,
                status="failed",
                raw=exc.raw,
                final=None,
                usage=exc.usage,
                elapsed_seconds=exc.elapsed_seconds,
                pricing=context.pricing,
                role_events=exc.role_events,
                error_code=exc.error_code,
                error_message=exc.public_message,
            )
        except Exception as exc:
            return ExperimentVariant.create(
                profile=context.request.creative_profile,
                public_slug=PUBLIC_SLUGS[context.request.creative_profile],
                run_id=context.run_id,
                model=context.model,
                thinking=context.thinking,
                status="failed",
                raw=None,
                final=None,
                usage=TokenUsage(),
                elapsed_seconds=0,
                pricing=context.pricing,
                error_code="internal_error",
                error_message=f"{type(exc).__name__}: {str(exc)[:1000]}",
            )
        expected = (
            context.request.creative_profile,
            PUBLIC_SLUGS[context.request.creative_profile],
            context.run_id,
            context.model,
            context.thinking,
        )
        actual = (
            result.profile,
            result.public_slug,
            result.run_id,
            result.model,
            result.thinking,
        )
        if actual != expected:
            raise ValueError("variant result does not match its immutable run context")
        return result

    variants = await asyncio.gather(*(run_one(context) for context in contexts))
    return AbcExperimentManifest.create(
        source_digest=source_digest,
        common_input_digest=common_digest,
        contract_id=base_request.contract_id,
        variants=variants,
    )


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _write_evidence(root: Path, evidence: ExperimentEvidence) -> None:
    _write(root / "artifact.json", _json_bytes(evidence.artifact.to_dict()))
    _write(root / "critique.json", _json_bytes(evidence.critique.to_dict()))
    _write(
        root / "audit.json",
        _json_bytes(evidence.to_dict()["audit"]),
    )
    _write(
        root / "index.html",
        build_preview_document(evidence.artifact).encode("utf-8"),
    )
    for screenshot in evidence.audit.screenshots:
        _write(
            root
            / "screenshots"
            / f"{screenshot.evidence.screenshot_id}.jpg",
            screenshot.data,
        )


def _failure_page(variant: ExperimentVariant) -> str:
    from html import escape

    return f"""<!doctype html><meta charset="utf-8"><title>Variant failed</title>
<main><h1>{escape(variant.profile.value)}</h1><p>Status: failed</p>
<p>{escape(variant.error_code or "")}: {escape(variant.error_message or "")}</p></main>"""


def write_experiment_package(
    output_dir: str | Path,
    manifest: AbcExperimentManifest,
) -> Path:
    if not isinstance(manifest, AbcExperimentManifest):
        raise TypeError("manifest must be AbcExperimentManifest")
    output = Path(output_dir).resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite experiment package: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}-", dir=str(output.parent))
    )
    try:
        manifest_bytes = _json_bytes(manifest.to_dict())
        if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
            raise ValueError("experiment manifest exceeds the package limit")
        _write(temporary / "manifest.json", manifest_bytes)
        cards: list[ComparisonVariant] = []
        for variant in manifest.variants:
            variant_root = temporary / variant.public_slug
            _write(
                variant_root / "role-events.json",
                _json_bytes(
                    {
                        "events": [
                            item.to_dict() for item in variant.role_events
                        ]
                    }
                ),
            )
            if variant.raw is not None:
                _write_evidence(variant_root / "raw", variant.raw)
            if variant.final is not None:
                _write_evidence(variant_root / "final", variant.final)
                critique_summary = (
                    f"Raw {variant.raw.critique.weighted_score:.2f} → "
                    f"final {variant.final.critique.weighted_score:.2f}."
                    if variant.raw is not None
                    else variant.final.critique.summary
                )
                cards.append(
                    ComparisonVariant(
                        slug=f"{variant.public_slug}/final",
                        raw_slug=f"{variant.public_slug}/raw",
                        title=variant.profile.value.replace("_", " ").title(),
                        profile=variant.profile.value,
                        model=variant.model,
                        thinking=variant.thinking,
                        status=variant.status,
                        summary=variant.final.artifact.change_summary
                        or variant.final.critique.summary,
                        critique_summary=critique_summary,
                        elapsed_seconds=variant.elapsed_seconds,
                        total_tokens=variant.usage.total_tokens,
                        cost_usd=variant.cost_usd,
                    )
                )
            else:
                _write(
                    variant_root / "index.html",
                    _failure_page(variant).encode("utf-8"),
                )
                cards.append(
                    ComparisonVariant(
                        slug=variant.public_slug,
                        title=variant.profile.value.replace("_", " ").title(),
                        model=variant.model,
                        thinking=variant.thinking,
                        status=variant.status,
                        summary=f"{variant.error_code}: {variant.error_message}",
                    )
                )
            _write(
                variant_root / "report.json",
                _json_bytes(
                    {
                        "profile": variant.profile.value,
                        "status": variant.status,
                        "model": variant.model,
                        "thinking": variant.thinking,
                        "usage": variant.usage.to_dict(),
                        "elapsed_seconds": variant.elapsed_seconds,
                        "pricing": variant.pricing.to_dict(),
                        "cost_usd": variant.cost_usd,
                        "error_code": variant.error_code,
                        "error_message": variant.error_message,
                    }
                ),
            )
        _write(
            temporary / "index.html",
            render_comparison_page(tuple(cards)).encode("utf-8"),
        )
        os.replace(temporary, output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output


__all__ = [
    "ABC_PROFILES",
    "PUBLIC_SLUGS",
    "AbcExperimentManifest",
    "ExperimentEvidence",
    "ExperimentPricingSnapshot",
    "ExperimentRoleEvent",
    "ExperimentVariant",
    "ExperimentVariantContext",
    "VariantExecutionError",
    "canonical_common_input_digest",
    "run_abc_experiment",
    "write_experiment_package",
]
