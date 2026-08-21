"""Prepare and freeze a local, provider-neutral widget refinement benchmark.

This module deliberately has no project, route, database, or publication imports.
Every input is a bounded local file or an already validated in-memory model.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
import hashlib
import hmac
import html
import inspect
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import secrets
import shutil
import stat
import sys
import tempfile
from typing import Any, Protocol
from uuid import UUID, uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builder_lab.comparison import (
    ComparisonVariant,
    freeze_bundle,
    render_comparison_page,
    verify_bundle,
)
from builder_lab.models import AssistantPersona, WidgetArtifact
from builder_lab.preview import build_trusted_runtime_document
from builder_lab.refinement_workspace import (
    CANONICAL_SCREENSHOT_NAMES,
    FrozenWorkspaceReceipt,
    ImportedRefinement,
    RefinementWorkspace,
    RefinementWorkspaceError,
    create_refinement_workspace,
    import_refinement_workspace,
)
from builder_lab.validation import validate_artifact


_INPUT_JSON_LIMIT = 2 * 1024 * 1024
_REQUEST_LIMIT = 64 * 1024
_SCREENSHOT_LIMIT = 8 * 1024 * 1024
_PUBLIC_RECEIPT_LIMIT = 64 * 1024
_CONTROL_LIMIT = 512 * 1024
_EDITOR_TIMEOUT_SECONDS = 300
_GATE_TIMEOUT_SECONDS = 180
_RECEIPT_SCHEMA = "kaigo.refinement-benchmark-receipt.v1"
_CONTROL_SCHEMA = "kaigo.refinement-benchmark-control.v1"
_ATTESTATION_SCHEMA = "kaigo.external-editor-attestation.v1"
_SIGNED_ATTESTATION_SCHEMA = "kaigo.signed-external-attestation.v1"
_INVOCATION_SCHEMA = "kaigo.external-invocation-record.v1"
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ARM_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_SECRET_VALUE = re.compile(
    r"(?i)(?:\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}|"
    r"\bAIza[0-9A-Za-z_-]{20,}|"
    r"[?&](?:access_token|api_key|key|token)=[^\s&]{4,})"
)


@dataclass(frozen=True, slots=True)
class ArmSpec:
    name: str
    title: str
    provider: str
    model: str
    effort: str
    execution: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _ARM_NAME.fullmatch(self.name) is None:
            raise ValueError("benchmark arm name is invalid")
        for field_name in ("title", "provider", "model", "effort"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value.encode("utf-8")) > 200
                or "\x00" in value
            ):
                raise ValueError(f"benchmark arm {field_name} is invalid")
        if self.execution not in {"docker-codex", "external", "injected"}:
            raise ValueError("benchmark arm execution is invalid")
        codex_identity = self.provider == "openai-codex-cli" or self.model == "gpt-5.6-sol"
        if codex_identity and self.execution != "docker-codex":
            raise ValueError("Codex identity requires docker-codex execution")
        if self.execution == "docker-codex" and not codex_identity:
            raise ValueError("docker-codex execution requires a Codex identity")
        antigravity_identity = self.provider == "antigravity-cli" or self.model.startswith(
            "gemini-"
        )
        if antigravity_identity and self.execution != "external":
            raise ValueError("Antigravity identity requires external execution")


DEFAULT_ARM_SPECS = (
    ArmSpec(
        name="codex-sol",
        title="Codex Sol",
        provider="openai-codex-cli",
        model="gpt-5.6-sol",
        effort="max",
        execution="docker-codex",
    ),
    ArmSpec(
        name="antigravity-3-7-high",
        title="Antigravity 3.7 High",
        provider="antigravity-cli",
        model="gemini-3.7-flash-high",
        effort="high",
        execution="external",
    ),
    ArmSpec(
        name="antigravity-3-1-pro-high",
        title="Antigravity 3.1 Pro High",
        provider="antigravity-cli",
        model="gemini-3.1-pro-high",
        effort="high",
        execution="external",
    ),
)


@dataclass(frozen=True, slots=True)
class EditorMetadata:
    duration_ms: int | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        _optional_bounded_int(self.duration_ms, field_name="duration_ms", maximum=86_400_000)
        for field_name in ("input_tokens", "cached_input_tokens", "output_tokens"):
            _optional_bounded_int(
                getattr(self, field_name),
                field_name=field_name,
                maximum=100_000_000,
            )


@dataclass(frozen=True, slots=True)
class ExternalInvocationRecord:
    provider: str
    model: str
    effort: str
    invocation_id: str
    event_log: bytes
    tool_paths: tuple[str, ...]
    network_accesses: tuple[str, ...]
    fallback_used: bool
    duration_ms: int | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None

    def __post_init__(self) -> None:
        for field_name in ("provider", "model", "effort"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"external attestation {field_name} is invalid")
        try:
            parsed = UUID(self.invocation_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("external invocation_id is invalid") from exc
        if str(parsed) != self.invocation_id:
            raise ValueError("external invocation_id is not canonical")
        if (
            not isinstance(self.event_log, bytes)
            or not self.event_log
            or len(self.event_log) > _CONTROL_LIMIT
        ):
            raise ValueError("external invocation event_log is invalid")
        if (
            not isinstance(self.tool_paths, tuple)
            or len(self.tool_paths) > 512
            or not isinstance(self.network_accesses, tuple)
            or len(self.network_accesses) > 64
        ):
            raise ValueError("external invocation event scope is invalid")
        for value in (*self.tool_paths, *self.network_accesses):
            if (
                not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > 1024
                or "\x00" in value
            ):
                raise ValueError("external invocation event is invalid")
        EditorMetadata(
            duration_ms=self.duration_ms,
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
        )
        if type(self.fallback_used) is not bool:
            raise ValueError("external invocation fallback_used must be boolean")


@dataclass(frozen=True, slots=True)
class ExternalAttestation:
    arm_name: str
    execution: str
    provider: str
    model: str
    effort: str
    input_digest: str
    challenge: str
    workspace_receipt_sha256: str
    candidate_digest: str
    editable_hashes: tuple[tuple[str, str], ...]
    invocation_id: str
    invocation_sha256: str
    duration_ms: int | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    model_verified: bool
    workspace_scope_verified: bool
    fallback_used: bool

    def __post_init__(self) -> None:
        if _ARM_NAME.fullmatch(self.arm_name) is None:
            raise ValueError("external attestation arm_name is invalid")
        if self.execution not in {"docker-codex", "external"}:
            raise ValueError("external attestation execution is invalid")
        for field_name in ("provider", "model", "effort"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"external attestation {field_name} is invalid")
        for field_name in (
            "input_digest",
            "challenge",
            "workspace_receipt_sha256",
            "candidate_digest",
            "invocation_sha256",
        ):
            if _HEX_DIGEST.fullmatch(getattr(self, field_name)) is None:
                raise ValueError(f"external attestation {field_name} is invalid")
        if (
            not isinstance(self.editable_hashes, tuple)
            or tuple(path for path, _digest in self.editable_hashes) != (
                "editable/persona.json",
                "editable/result.json",
                "editable/widget.css",
                "editable/widget.html",
            )
            or any(_HEX_DIGEST.fullmatch(digest) is None for _, digest in self.editable_hashes)
        ):
            raise ValueError("external attestation editable_hashes are invalid")
        try:
            parsed = UUID(self.invocation_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("external attestation invocation_id is invalid") from exc
        if str(parsed) != self.invocation_id:
            raise ValueError("external attestation invocation_id is not canonical")
        EditorMetadata(
            duration_ms=self.duration_ms,
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
        )
        for field_name in ("model_verified", "workspace_scope_verified", "fallback_used"):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"external attestation {field_name} must be boolean")
        if not self.model_verified or not self.workspace_scope_verified or self.fallback_used:
            raise ValueError("external attestation does not prove the required boundary")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _ATTESTATION_SCHEMA,
            "arm_name": self.arm_name,
            "execution": self.execution,
            "provider": self.provider,
            "model": self.model,
            "effort": self.effort,
            "input_digest": self.input_digest,
            "challenge": self.challenge,
            "workspace_receipt_sha256": self.workspace_receipt_sha256,
            "candidate_digest": self.candidate_digest,
            "editable_hashes": [list(item) for item in self.editable_hashes],
            "invocation_id": self.invocation_id,
            "invocation_sha256": self.invocation_sha256,
            "duration_ms": self.duration_ms,
            "usage": {
                "input_tokens": self.input_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "output_tokens": self.output_tokens,
            },
            "model_verified": self.model_verified,
            "workspace_scope_verified": self.workspace_scope_verified,
            "fallback_used": self.fallback_used,
        }

    @classmethod
    def from_dict(cls, payload: object) -> ExternalAttestation:
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "arm_name",
            "execution",
            "provider",
            "model",
            "effort",
            "input_digest",
            "challenge",
            "workspace_receipt_sha256",
            "candidate_digest",
            "editable_hashes",
            "invocation_id",
            "invocation_sha256",
            "duration_ms",
            "usage",
            "model_verified",
            "workspace_scope_verified",
            "fallback_used",
        }:
            raise ValueError("external attestation payload is invalid")
        usage = payload["usage"]
        if not isinstance(usage, dict) or set(usage) != {
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
        }:
            raise ValueError("external attestation usage is invalid")
        if payload["schema_version"] != _ATTESTATION_SCHEMA:
            raise ValueError("external attestation schema is invalid")
        editable_hashes = payload["editable_hashes"]
        if not isinstance(editable_hashes, list) or any(
            not isinstance(item, list) or len(item) != 2
            for item in editable_hashes
        ):
            raise ValueError("external attestation editable_hashes are invalid")
        return cls(
            arm_name=payload["arm_name"],
            execution=payload["execution"],
            provider=payload["provider"],
            model=payload["model"],
            effort=payload["effort"],
            input_digest=payload["input_digest"],
            challenge=payload["challenge"],
            workspace_receipt_sha256=payload["workspace_receipt_sha256"],
            candidate_digest=payload["candidate_digest"],
            editable_hashes=tuple(tuple(item) for item in editable_hashes),
            invocation_id=payload["invocation_id"],
            invocation_sha256=payload["invocation_sha256"],
            duration_ms=payload["duration_ms"],
            input_tokens=usage["input_tokens"],
            cached_input_tokens=usage["cached_input_tokens"],
            output_tokens=usage["output_tokens"],
            model_verified=payload["model_verified"],
            workspace_scope_verified=payload["workspace_scope_verified"],
            fallback_used=payload["fallback_used"],
        )


@dataclass(frozen=True, slots=True)
class SignedExternalAttestation:
    attestation: ExternalAttestation
    payload_sha256: str
    signature_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.attestation, ExternalAttestation):
            raise TypeError("attestation must be ExternalAttestation")
        if _HEX_DIGEST.fullmatch(self.payload_sha256) is None:
            raise ValueError("attestation payload digest is invalid")
        if _HEX_DIGEST.fullmatch(self.signature_sha256) is None:
            raise ValueError("attestation signature is invalid")


@dataclass(frozen=True, slots=True)
class PreparedArm:
    spec: ArmSpec
    workspace: Path
    receipt: FrozenWorkspaceReceipt
    input_digest: str
    challenge: str

    @property
    def refinement_workspace(self) -> RefinementWorkspace:
        return RefinementWorkspace(
            root=self.workspace,
            editable=self.workspace / "editable",
            manifest_path=self.workspace / "manifest.json",
            receipt=self.receipt,
        )


@dataclass(frozen=True, slots=True)
class BenchmarkBundle:
    output: Path
    source_artifact: WidgetArtifact
    source_persona: AssistantPersona
    request: str
    screenshots: tuple[tuple[str, bytes], ...]
    source_digest: str
    arms: tuple[PreparedArm, ...]


@dataclass(frozen=True, slots=True)
class ArmResult:
    arm: PreparedArm
    imported: ImportedRefinement | None
    receipt: Mapping[str, Any]
    receipt_path: Path


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    bundle: BenchmarkBundle
    results: tuple[ArmResult, ...]
    evidence_root: Path
    comparison_index: Path | None


class BenchmarkEditor(Protocol):
    async def run(
        self,
        *,
        workspace: RefinementWorkspace,
        timeout_seconds: float,
    ) -> EditorMetadata | Any: ...


GateCallable = Callable[
    [WidgetArtifact, AssistantPersona],
    object | Awaitable[object],
]


class BenchmarkError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def prepare_benchmark_bundle(
    *,
    output: Path,
    source_artifact: WidgetArtifact,
    source_persona: AssistantPersona,
    request: str,
    screenshots: Sequence[tuple[str, bytes]],
    arm_specs: Sequence[ArmSpec] | None = None,
    trusted_private_base: Path | None = None,
) -> BenchmarkBundle:
    """Create fresh arm workspaces plus external receipts under a private root."""

    if not isinstance(source_artifact, WidgetArtifact):
        raise TypeError("source_artifact must be WidgetArtifact")
    if not isinstance(source_persona, AssistantPersona):
        raise TypeError("source_persona must be AssistantPersona")
    issues = validate_artifact(
        source_artifact,
        previous_revision=max(0, source_artifact.revision - 1),
    )
    if issues:
        raise BenchmarkError("invalid_source_artifact", "source artifact is invalid")
    request = _validated_request(request)
    screenshot_items = _validated_screenshots(screenshots)
    specs = tuple(DEFAULT_ARM_SPECS if arm_specs is None else arm_specs)
    if not specs or len(specs) > 8 or any(not isinstance(item, ArmSpec) for item in specs):
        raise ValueError("benchmark requires between one and eight arm specs")
    if len({item.name for item in specs}) != len(specs):
        raise ValueError("benchmark arm names must be unique")

    output_root = _prepare_private_output(
        output,
        trusted_private_base=trusted_private_base,
    )
    source_digest = _input_digest(
        source_artifact=source_artifact,
        source_persona=source_persona,
        request=request,
        screenshots=screenshot_items,
    )
    control = output_root / "control"
    arms_root = output_root / "arms"
    _make_private_directory(control)
    _make_private_directory(arms_root)
    _materialize_control_inputs(
        control=control,
        source_artifact=source_artifact,
        source_persona=source_persona,
        request=request,
        screenshots=screenshot_items,
    )

    arms: list[PreparedArm] = []
    receipts_root = control / "workspace-receipts"
    _make_private_directory(receipts_root)
    template_workspace: RefinementWorkspace | None = None
    for spec in specs:
        if template_workspace is None:
            workspace = create_refinement_workspace(
                root=arms_root / spec.name,
                trusted_private_base=trusted_private_base,
                source_artifact=source_artifact,
                source_persona=source_persona,
                change_request=request,
                screenshots=screenshot_items,
                candidate_revision=source_artifact.revision + 1,
            )
            template_workspace = workspace
        else:
            workspace = _clone_refinement_workspace(
                template=template_workspace,
                root=arms_root / spec.name,
            )
        prepared = PreparedArm(
            spec=spec,
            workspace=workspace.root,
            receipt=workspace.receipt,
            input_digest=source_digest,
            challenge=secrets.token_hex(32),
        )
        _write_control_receipt(receipts_root / f"{spec.name}.json", prepared)
        arms.append(prepared)

    _write_control_manifest(
        control / "bundle.json",
        source_digest=source_digest,
        specs=specs,
    )
    return BenchmarkBundle(
        output=output_root,
        source_artifact=source_artifact,
        source_persona=source_persona,
        request=request,
        screenshots=screenshot_items,
        source_digest=source_digest,
        arms=tuple(arms),
    )


def load_prepared_benchmark(
    output: Path,
    *,
    trusted_private_base: Path | None = None,
) -> BenchmarkBundle:
    """Reload persisted root-of-trust receipts without trusting arm workspaces."""

    output_root = _validate_existing_private_output(
        output,
        trusted_private_base=trusted_private_base,
    )
    control = output_root / "control"
    _validate_private_directory(control)
    manifest = _read_json(control / "bundle.json", limit=_CONTROL_LIMIT)
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "source_digest",
        "arms",
    }:
        raise BenchmarkError("invalid_control", "control manifest is invalid")
    if manifest["schema_version"] != _CONTROL_SCHEMA:
        raise BenchmarkError("invalid_control", "control schema is invalid")
    source_artifact = WidgetArtifact.from_dict(
        _read_json(control / "input" / "source-artifact.json", limit=_INPUT_JSON_LIMIT)
    )
    source_persona = AssistantPersona.from_dict(
        _read_json(control / "input" / "source-persona.json", limit=_INPUT_JSON_LIMIT)
    )
    request = _read_text(control / "input" / "request.md", limit=_REQUEST_LIMIT)
    screenshots = tuple(
        (
            name,
            _read_regular_bytes(
                control / "input" / "screenshots" / f"{name}.jpg",
                limit=_SCREENSHOT_LIMIT,
            ),
        )
        for name in CANONICAL_SCREENSHOT_NAMES
    )
    source_digest = _input_digest(
        source_artifact=source_artifact,
        source_persona=source_persona,
        request=request,
        screenshots=screenshots,
    )
    if manifest["source_digest"] != source_digest or _HEX_DIGEST.fullmatch(
        str(manifest["source_digest"])
    ) is None:
        raise BenchmarkError("input_digest_mismatch", "control inputs were modified")
    arm_payloads = manifest["arms"]
    if not isinstance(arm_payloads, list) or not 1 <= len(arm_payloads) <= 8:
        raise BenchmarkError("invalid_control", "control arms are invalid")
    specs = tuple(_arm_spec_from_dict(item) for item in arm_payloads)
    if len({item.name for item in specs}) != len(specs):
        raise BenchmarkError("invalid_control", "control arm names are duplicated")
    arms = tuple(
        _read_control_receipt(
            output_root=output_root,
            spec=spec,
            source_digest=source_digest,
            path=control / "workspace-receipts" / f"{spec.name}.json",
        )
        for spec in specs
    )
    return BenchmarkBundle(
        output=output_root,
        source_artifact=source_artifact,
        source_persona=source_persona,
        request=request,
        screenshots=screenshots,
        source_digest=source_digest,
        arms=arms,
    )


def _sign_external_attestation(
    attestation: ExternalAttestation,
    *,
    key: bytes,
) -> SignedExternalAttestation:
    payload = _canonical_json_bytes(attestation.to_dict())
    if len(key) != 32:
        raise BenchmarkError("invalid_control", "attestation key is invalid")
    return SignedExternalAttestation(
        attestation=attestation,
        payload_sha256=_sha256(payload),
        signature_sha256=hmac.new(key, payload, hashlib.sha256).hexdigest(),
    )


def _record_controller_attestation(
    bundle: BenchmarkBundle,
    arm: PreparedArm,
    invocation: ExternalInvocationRecord,
) -> tuple[SignedExternalAttestation, Path]:
    if arm.spec.execution != "docker-codex":
        raise BenchmarkError(
            "external_attestation_unsupported",
            "only the trusted Docker Codex controller may mint execution proof",
        )
    _validate_external_invocation(arm, invocation)
    imported = import_refinement_workspace(arm.workspace, receipt=arm.receipt)
    candidate_digest, editable_hashes = _candidate_binding(arm, imported)
    invocation_record = _invocation_record_payload(invocation)
    attestation = ExternalAttestation(
        arm_name=arm.spec.name,
        execution=arm.spec.execution,
        provider=invocation.provider,
        model=invocation.model,
        effort=invocation.effort,
        input_digest=arm.input_digest,
        challenge=arm.challenge,
        workspace_receipt_sha256=_workspace_receipt_digest(arm.receipt),
        candidate_digest=candidate_digest,
        editable_hashes=editable_hashes,
        invocation_id=invocation.invocation_id,
        invocation_sha256=_sha256(_canonical_json_bytes(invocation_record)),
        duration_ms=invocation.duration_ms,
        input_tokens=invocation.input_tokens,
        cached_input_tokens=invocation.cached_input_tokens,
        output_tokens=invocation.output_tokens,
        model_verified=True,
        workspace_scope_verified=True,
        fallback_used=False,
    )
    key = secrets.token_bytes(32)
    signed = _sign_external_attestation(attestation, key=key)
    root = bundle.output / "control" / "execution-proofs"
    _ensure_private_directory(root)
    proof_root = root / arm.spec.name
    if proof_root.exists() or proof_root.is_symlink():
        raise BenchmarkError("external_attestation_reused", "arm proof already exists")
    temporary = Path(tempfile.mkdtemp(prefix=f".{arm.spec.name}-", dir=root))
    try:
        if os.name == "posix":
            temporary.chmod(0o700)
        _write_new_bytes(temporary / "verification.key", key)
        _write_new_bytes(
            temporary / "invocation.json",
            _canonical_json_bytes(invocation_record) + b"\n",
        )
        _write_new_bytes(
            temporary / "attestation.json",
            _canonical_json_bytes(_signed_attestation_dict(signed)) + b"\n",
        )
        os.replace(temporary, proof_root)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return signed, proof_root / "attestation.json"


def load_execution_attestations(
    bundle: BenchmarkBundle,
) -> dict[str, SignedExternalAttestation]:
    root = bundle.output / "control" / "execution-proofs"
    if not root.exists():
        return {}
    _validate_private_directory(root)
    result: dict[str, SignedExternalAttestation] = {}
    for arm in bundle.arms:
        if arm.spec.execution != "docker-codex":
            continue
        proof_root = root / arm.spec.name
        if not proof_root.exists():
            continue
        _validate_private_directory(proof_root)
        path = proof_root / "attestation.json"
        if not path.exists():
            continue
        payload = _read_json(path, limit=_CONTROL_LIMIT)
        signed = _signed_attestation_from_dict(payload)
        _verify_attestation_signature(bundle, arm, signed)
        result[arm.spec.name] = signed
    return result


def load_external_attestations(
    bundle: BenchmarkBundle,
) -> dict[str, SignedExternalAttestation]:
    external_names = {
        arm.spec.name for arm in bundle.arms if arm.spec.execution == "external"
    }
    return {
        name: signed
        for name, signed in load_execution_attestations(bundle).items()
        if name in external_names
    }


def _verify_external_attestation(
    bundle: BenchmarkBundle,
    arm: PreparedArm,
    signed: SignedExternalAttestation,
) -> ExternalAttestation:
    if arm.spec.execution != "docker-codex":
        raise BenchmarkError(
            "external_attestation_unsupported",
            "external AntiGravity proof has no trusted verifier",
        )
    attestation = _verify_attestation_signature(bundle, arm, signed)
    try:
        imported = import_refinement_workspace(arm.workspace, receipt=arm.receipt)
        candidate_digest, editable_hashes = _candidate_binding(arm, imported)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        raise BenchmarkError(
            "external_attestation_mismatch",
            "attested candidate is no longer importable",
        ) from exc
    if (
        candidate_digest != attestation.candidate_digest
        or editable_hashes != attestation.editable_hashes
    ):
        raise BenchmarkError(
            "external_attestation_mismatch",
            "attested candidate changed after the external invocation",
        )
    return attestation


def _verify_attestation_signature(
    bundle: BenchmarkBundle,
    arm: PreparedArm,
    signed: SignedExternalAttestation,
) -> ExternalAttestation:
    """Authenticate persisted controller proof without trusting live candidate files."""

    if arm.spec.execution != "docker-codex":
        raise BenchmarkError(
            "external_attestation_unsupported",
            "external AntiGravity proof has no trusted verifier",
        )
    if not isinstance(signed, SignedExternalAttestation):
        raise BenchmarkError(
            "external_attestation_invalid",
            "external attestation is not signed",
        )
    attestation = signed.attestation
    if (
        attestation.arm_name != arm.spec.name
        or attestation.execution != arm.spec.execution
        or attestation.provider != arm.spec.provider
        or attestation.model != arm.spec.model
        or attestation.effort != arm.spec.effort
        or attestation.input_digest != arm.input_digest
        or attestation.input_digest != bundle.source_digest
        or attestation.challenge != arm.challenge
        or attestation.workspace_receipt_sha256
        != _workspace_receipt_digest(arm.receipt)
        or attestation.fallback_used
        or not attestation.model_verified
        or not attestation.workspace_scope_verified
    ):
        raise BenchmarkError(
            "external_attestation_mismatch",
            "external attestation does not match the prepared arm",
        )
    payload = _canonical_json_bytes(attestation.to_dict())
    if not hmac.compare_digest(signed.payload_sha256, _sha256(payload)):
        raise BenchmarkError(
            "external_attestation_invalid",
            "external attestation payload digest is invalid",
        )
    proof_root = bundle.output / "control" / "execution-proofs" / arm.spec.name
    invocation_record = _read_invocation_record(proof_root, arm)
    invocation_digest = _sha256(_canonical_json_bytes(invocation_record))
    usage = invocation_record["usage"]
    if (
        invocation_digest != attestation.invocation_sha256
        or invocation_record["invocation_id"] != attestation.invocation_id
        or invocation_record["provider"] != attestation.provider
        or invocation_record["model"] != attestation.model
        or invocation_record["effort"] != attestation.effort
        or invocation_record["duration_ms"] != attestation.duration_ms
        or usage["input_tokens"] != attestation.input_tokens
        or usage["cached_input_tokens"] != attestation.cached_input_tokens
        or usage["output_tokens"] != attestation.output_tokens
    ):
        raise BenchmarkError(
            "external_attestation_mismatch",
            "external attestation does not match its invocation record",
        )
    key = _read_regular_bytes(proof_root / "verification.key", limit=32)
    expected = hmac.new(key, payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signed.signature_sha256, expected):
        raise BenchmarkError(
            "external_attestation_invalid",
            "external attestation signature is invalid",
        )
    return attestation


def _validate_external_invocation(
    arm: PreparedArm,
    invocation: ExternalInvocationRecord,
) -> None:
    if not isinstance(invocation, ExternalInvocationRecord):
        raise TypeError("invocation must be ExternalInvocationRecord")
    if (
        invocation.provider != arm.spec.provider
        or invocation.model != arm.spec.model
        or invocation.effort != arm.spec.effort
        or invocation.fallback_used
        or invocation.network_accesses
    ):
        raise BenchmarkError(
            "external_attestation_mismatch",
            "captured external invocation does not match the prepared arm",
        )
    _validate_invocation_scope(
        tool_paths=invocation.tool_paths,
        network_accesses=invocation.network_accesses,
    )


def _validate_invocation_scope(
    *,
    tool_paths: Sequence[str],
    network_accesses: Sequence[str],
) -> None:
    if network_accesses:
        raise BenchmarkError(
            "external_attestation_mismatch",
            "captured external invocation used network access",
        )
    for raw_path in tool_paths:
        normalized = raw_path.replace("\\", "/")
        posix_path = PurePosixPath(normalized)
        windows_path = PureWindowsPath(raw_path)
        if (
            _URI_SCHEME.match(raw_path)
            or raw_path.startswith(("//", "\\\\"))
            or windows_path.drive
            or windows_path.root
            or posix_path.is_absolute()
            or not posix_path.parts
            or any(part in {"", ".", ".."} for part in posix_path.parts)
            or str(posix_path) != normalized
        ):
            raise BenchmarkError(
                "external_scope_violation",
                "captured tool event escaped the refinement workspace",
            )


def _invocation_record_payload(
    invocation: ExternalInvocationRecord,
) -> dict[str, Any]:
    return {
        "schema_version": _INVOCATION_SCHEMA,
        "provider": invocation.provider,
        "model": invocation.model,
        "effort": invocation.effort,
        "invocation_id": invocation.invocation_id,
        "event_log_sha256": _sha256(invocation.event_log),
        "tool_paths": list(invocation.tool_paths),
        "network_accesses": list(invocation.network_accesses),
        "fallback_used": invocation.fallback_used,
        "duration_ms": invocation.duration_ms,
        "usage": {
            "input_tokens": invocation.input_tokens,
            "cached_input_tokens": invocation.cached_input_tokens,
            "output_tokens": invocation.output_tokens,
        },
    }


def _read_invocation_record(
    proof_root: Path,
    arm: PreparedArm,
) -> dict[str, Any]:
    payload = _read_json(proof_root / "invocation.json", limit=_CONTROL_LIMIT)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "provider",
        "model",
        "effort",
        "invocation_id",
        "event_log_sha256",
        "tool_paths",
        "network_accesses",
        "fallback_used",
        "duration_ms",
        "usage",
    }:
        raise BenchmarkError("invalid_control", "invocation record is invalid")
    usage = payload["usage"]
    tool_paths = payload["tool_paths"]
    network_accesses = payload["network_accesses"]
    if (
        payload["schema_version"] != _INVOCATION_SCHEMA
        or payload["provider"] != arm.spec.provider
        or payload["model"] != arm.spec.model
        or payload["effort"] != arm.spec.effort
        or _HEX_DIGEST.fullmatch(str(payload["event_log_sha256"])) is None
        or not isinstance(tool_paths, list)
        or len(tool_paths) > 512
        or not all(isinstance(item, str) for item in tool_paths)
        or not isinstance(network_accesses, list)
        or len(network_accesses) > 64
        or not all(isinstance(item, str) for item in network_accesses)
        or type(payload["fallback_used"]) is not bool
        or payload["fallback_used"]
        or not isinstance(usage, dict)
        or set(usage)
        != {"input_tokens", "cached_input_tokens", "output_tokens"}
    ):
        raise BenchmarkError("invalid_control", "invocation record is inconsistent")
    try:
        parsed_id = UUID(payload["invocation_id"])
        metadata = EditorMetadata(
            duration_ms=payload["duration_ms"],
            input_tokens=usage["input_tokens"],
            cached_input_tokens=usage["cached_input_tokens"],
            output_tokens=usage["output_tokens"],
        )
    except (TypeError, ValueError) as exc:
        raise BenchmarkError("invalid_control", "invocation record is invalid") from exc
    if str(parsed_id) != payload["invocation_id"]:
        raise BenchmarkError("invalid_control", "invocation id is not canonical")
    del metadata
    _validate_invocation_scope(
        tool_paths=tool_paths,
        network_accesses=network_accesses,
    )
    return payload


def _signed_attestation_dict(
    signed: SignedExternalAttestation,
) -> dict[str, Any]:
    return {
        "schema_version": _SIGNED_ATTESTATION_SCHEMA,
        "payload": signed.attestation.to_dict(),
        "payload_sha256": signed.payload_sha256,
        "signature_sha256": signed.signature_sha256,
    }


def _signed_attestation_from_dict(payload: object) -> SignedExternalAttestation:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "payload",
        "payload_sha256",
        "signature_sha256",
    }:
        raise BenchmarkError("invalid_control", "signed attestation is invalid")
    if payload["schema_version"] != _SIGNED_ATTESTATION_SCHEMA:
        raise BenchmarkError("invalid_control", "signed attestation schema is invalid")
    try:
        return SignedExternalAttestation(
            attestation=ExternalAttestation.from_dict(payload["payload"]),
            payload_sha256=payload["payload_sha256"],
            signature_sha256=payload["signature_sha256"],
        )
    except (TypeError, ValueError) as exc:
        raise BenchmarkError("invalid_control", "signed attestation is invalid") from exc


async def finalize_benchmark_bundle(
    bundle: BenchmarkBundle,
    *,
    editors: Mapping[str, BenchmarkEditor] | None = None,
    external_attestations: Mapping[str, SignedExternalAttestation] | None = None,
    browser_gate: GateCallable | None = None,
    motion_gate: GateCallable | None = None,
    editor_timeout_seconds: float = _EDITOR_TIMEOUT_SECONDS,
    gate_timeout_seconds: float = _GATE_TIMEOUT_SECONDS,
    forbidden_fingerprints: Sequence[bytes] = (),
    allow_injected_gates: bool = False,
    freeze_comparison: bool = True,
) -> BenchmarkReport:
    """Import every arm fail-closed, run gates, and render explicit result cards."""

    if not isinstance(bundle, BenchmarkBundle):
        raise TypeError("bundle must be BenchmarkBundle")
    if (
        isinstance(editor_timeout_seconds, bool)
        or not isinstance(editor_timeout_seconds, (int, float))
        or not 0 < float(editor_timeout_seconds) <= 1800
    ):
        raise ValueError("editor_timeout_seconds is invalid")
    if (
        isinstance(gate_timeout_seconds, bool)
        or not isinstance(gate_timeout_seconds, (int, float))
        or not 0 < float(gate_timeout_seconds) <= 600
    ):
        raise ValueError("gate_timeout_seconds is invalid")
    if type(allow_injected_gates) is not bool:
        raise TypeError("allow_injected_gates must be boolean")
    if type(freeze_comparison) is not bool:
        raise TypeError("freeze_comparison must be boolean")
    custom_gates = browser_gate is not None or motion_gate is not None
    if custom_gates and (browser_gate is None or motion_gate is None):
        raise ValueError("browser_gate and motion_gate must be injected together")
    if custom_gates and any(
        arm.spec.execution != "injected" for arm in bundle.arms
    ) and not allow_injected_gates:
        raise BenchmarkError(
            "injected_gate_forbidden",
            "external and Docker arms require trusted internal gates",
        )
    gate_mode = "injected" if custom_gates else "trusted"
    fingerprints = _validated_fingerprints(forbidden_fingerprints)
    _assert_no_secret_material(
        _canonical_json_bytes(
            {
                "arms": [_arm_spec_dict(arm.spec) for arm in bundle.arms],
            }
        ),
        fingerprints=fingerprints,
    )
    editor_map = dict(editors or {})
    attestation_map = load_execution_attestations(bundle)
    attestation_map.update(external_attestations or {})
    unknown_editors = set(editor_map) - {arm.spec.name for arm in bundle.arms}
    if unknown_editors:
        raise ValueError("an editor was supplied for an unknown arm")
    unknown_attestations = set(attestation_map) - {
        arm.spec.name for arm in bundle.arms if arm.spec.execution != "injected"
    }
    if unknown_attestations:
        raise ValueError("an attestation was supplied for a non-external arm")
    browser = browser_gate or _trusted_browser_gate
    motion = motion_gate or _trusted_motion_gate
    results: list[ArmResult] = []
    for arm in bundle.arms:
        metadata = EditorMetadata()
        editor = editor_map.get(arm.spec.name)
        attestation: ExternalAttestation | None = None
        imported: ImportedRefinement | None = None
        gate_statuses = {
            "validation": "not_run",
            "browser": "not_run",
            "motion": "not_run",
        }
        gate_evidence: dict[str, str | None] = {
            "mode": gate_mode,
            "candidate_digest": None,
            "browser_sha256": None,
            "motion_sha256": None,
        }
        try:
            if arm.spec.execution == "external":
                raise BenchmarkError(
                    "external_attestation_unsupported",
                    "external AntiGravity proof has no trusted verifier",
                )
            if arm.spec.execution in {"docker-codex", "external"} and editor is None:
                signed = attestation_map.get(arm.spec.name)
                if signed is None:
                    raise BenchmarkError(
                        (
                            "docker_editor_required"
                            if arm.spec.execution == "docker-codex"
                            else "external_attestation_required"
                        ),
                        "arm requires a signed controller execution proof",
                    )
                attestation = _verify_external_attestation(bundle, arm, signed)
                metadata = EditorMetadata(
                    duration_ms=attestation.duration_ms,
                    input_tokens=attestation.input_tokens,
                    cached_input_tokens=attestation.cached_input_tokens,
                    output_tokens=attestation.output_tokens,
                )
            elif arm.spec.execution == "injected" and editor is None:
                raise BenchmarkError(
                    "editor_required",
                    "injected arm requires exactly one editor",
                )
            if editor is not None:
                if arm.spec.execution == "docker-codex":
                    _require_docker_workspace_editor(editor)
                run_editor = _bound_async_method(
                    editor,
                    "run",
                    error_code="invalid_async_editor",
                )
                try:
                    async with asyncio.timeout(float(editor_timeout_seconds)):
                        raw_metadata = await run_editor(
                            workspace=arm.refinement_workspace,
                            timeout_seconds=editor_timeout_seconds,
                        )
                except TimeoutError as exc:
                    raise BenchmarkError("editor_timeout", "editor timed out") from exc
                metadata = _normalize_editor_metadata(raw_metadata)
                if arm.spec.execution == "docker-codex":
                    invocation = ExternalInvocationRecord(
                        provider=arm.spec.provider,
                        model=arm.spec.model,
                        effort=arm.spec.effort,
                        invocation_id=str(uuid4()),
                        event_log=_canonical_json_bytes(
                            {
                                "controller": "DockerWorkspaceEditor",
                                "input_digest": arm.input_digest,
                                "metadata": asdict(metadata),
                            }
                        ),
                        tool_paths=(
                            "editable/persona.json",
                            "editable/result.json",
                            "editable/widget.css",
                            "editable/widget.html",
                        ),
                        network_accesses=(),
                        fallback_used=False,
                        duration_ms=metadata.duration_ms,
                        input_tokens=metadata.input_tokens,
                        cached_input_tokens=metadata.cached_input_tokens,
                        output_tokens=metadata.output_tokens,
                    )
                    signed, _path = _record_controller_attestation(
                        bundle,
                        arm,
                        invocation,
                    )
                    attestation = _verify_external_attestation(bundle, arm, signed)
            imported = import_refinement_workspace(
                arm.workspace,
                receipt=arm.receipt,
            )
            gate_evidence["candidate_digest"] = _candidate_digest(imported)
            _assert_candidate_safe(
                arm=arm,
                imported=imported,
                request=bundle.request,
                fingerprints=fingerprints,
            )
            validation_issues = validate_artifact(
                imported.artifact,
                previous_revision=bundle.source_artifact.revision,
            )
            if validation_issues:
                gate_statuses["validation"] = "failed"
                raise BenchmarkError("validation_failed", "candidate validation failed")
            gate_statuses["validation"] = "passed"
            try:
                async with asyncio.timeout(float(gate_timeout_seconds)):
                    browser_report = await _await_gate(
                        browser,
                        imported.artifact,
                        imported.persona,
                        gate_name="browser",
                    )
                gate_evidence["browser_sha256"] = _gate_evidence_digest(
                    browser_report,
                    candidate_digest=str(gate_evidence["candidate_digest"]),
                    gate_name="browser",
                )
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                    raise
                gate_statuses["browser"] = "failed"
                raise BenchmarkError("browser_gate_failed", "browser gate failed") from exc
            gate_statuses["browser"] = "passed"
            try:
                async with asyncio.timeout(float(gate_timeout_seconds)):
                    motion_report = await _await_gate(
                        motion,
                        imported.artifact,
                        imported.persona,
                        gate_name="motion",
                    )
                gate_evidence["motion_sha256"] = _gate_evidence_digest(
                    motion_report,
                    candidate_digest=str(gate_evidence["candidate_digest"]),
                    gate_name="motion",
                )
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                    raise
                gate_statuses["motion"] = "failed"
                raise BenchmarkError("motion_gate_failed", "motion gate failed") from exc
            gate_statuses["motion"] = "passed"
            receipt = _success_receipt(
                bundle=bundle,
                arm=arm,
                imported=imported,
                metadata=metadata,
                attestation=attestation,
                gate_evidence=gate_evidence,
            )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            receipt = _failure_receipt(
                bundle=bundle,
                arm=arm,
                metadata=metadata,
                error_code=_safe_error_code(exc),
                gate_statuses=gate_statuses,
                imported=imported,
                attestation=attestation,
                gate_evidence=gate_evidence,
            )
        results.append(
            ArmResult(
                arm=arm,
                imported=imported,
                receipt=receipt,
                receipt_path=Path(),
            )
        )

    evidence_root, frozen_results = _persist_finalized_evidence(
        bundle,
        tuple(results),
        fingerprints=fingerprints,
    )
    comparison_index = (
        _freeze_comparison(
            bundle,
            frozen_results,
            evidence_root=evidence_root,
            fingerprints=fingerprints,
        )
        if freeze_comparison
        else None
    )
    return BenchmarkReport(
        bundle=bundle,
        results=frozen_results,
        evidence_root=evidence_root,
        comparison_index=comparison_index,
    )


async def run_benchmark_editors(
    bundle: BenchmarkBundle,
    *,
    editors: Mapping[str, BenchmarkEditor],
    editor_timeout_seconds: float = _EDITOR_TIMEOUT_SECONDS,
) -> dict[str, str]:
    """Run and attest Docker editors only; do not import gates or freeze evidence."""

    if not isinstance(bundle, BenchmarkBundle):
        raise TypeError("bundle must be BenchmarkBundle")
    editor_map = dict(editors)
    expected = {
        arm.spec.name for arm in bundle.arms if arm.spec.execution == "docker-codex"
    }
    if set(editor_map) != expected:
        raise ValueError("run requires exactly the prepared Docker editors")
    statuses = {
        arm.spec.name: "external_pending"
        for arm in bundle.arms
        if arm.spec.execution == "external"
    }
    for arm in bundle.arms:
        if arm.spec.execution != "docker-codex":
            continue
        editor = editor_map[arm.spec.name]
        _require_docker_workspace_editor(editor)
        run_editor = _bound_async_method(
            editor,
            "run",
            error_code="invalid_async_editor",
        )
        try:
            async with asyncio.timeout(float(editor_timeout_seconds)):
                raw_metadata = await run_editor(
                    workspace=arm.refinement_workspace,
                    timeout_seconds=editor_timeout_seconds,
                )
        except TimeoutError as exc:
            raise BenchmarkError("editor_timeout", "editor timed out") from exc
        metadata = _normalize_editor_metadata(raw_metadata)
        invocation = ExternalInvocationRecord(
            provider=arm.spec.provider,
            model=arm.spec.model,
            effort=arm.spec.effort,
            invocation_id=str(uuid4()),
            event_log=_canonical_json_bytes(
                {
                    "controller": "DockerWorkspaceEditor",
                    "input_digest": arm.input_digest,
                    "metadata": asdict(metadata),
                }
            ),
            tool_paths=(
                "editable/persona.json",
                "editable/result.json",
                "editable/widget.css",
                "editable/widget.html",
            ),
            network_accesses=(),
            fallback_used=False,
            duration_ms=metadata.duration_ms,
            input_tokens=metadata.input_tokens,
            cached_input_tokens=metadata.cached_input_tokens,
            output_tokens=metadata.output_tokens,
        )
        _record_controller_attestation(bundle, arm, invocation)
        statuses[arm.spec.name] = "completed"
    return statuses


def load_docker_codex_editor(**kwargs: Any) -> BenchmarkEditor:
    """Lazily construct the only permitted Codex editor boundary.

    The Docker runner is intentionally imported only when an actual Codex arm is
    invoked, so external preparation and unit tests do not depend on its module.
    """

    try:
        from tools.kaigo_codex_bridge.docker_workspace_runner import (
            DockerWorkspaceEditor,
        )
    except (ImportError, AttributeError) as exc:
        raise BenchmarkError(
            "docker_editor_unavailable",
            "DockerWorkspaceEditor is unavailable",
        ) from exc
    return DockerWorkspaceEditor(**kwargs)


def _require_docker_workspace_editor(editor: object) -> None:
    try:
        from tools.kaigo_codex_bridge.docker_workspace_runner import (
            DockerWorkspaceEditor,
        )
    except (ImportError, AttributeError) as exc:
        raise BenchmarkError(
            "docker_editor_unavailable",
            "DockerWorkspaceEditor is unavailable",
        ) from exc
    if not isinstance(editor, DockerWorkspaceEditor):
        raise BenchmarkError(
            "unsafe_codex_editor",
            "Codex benchmark arms require DockerWorkspaceEditor",
        )


async def _trusted_browser_gate(
    artifact: WidgetArtifact,
    persona: AssistantPersona,
) -> object:
    from functools import partial

    from builder_lab.browser_audit import BrowserAudit

    return await BrowserAudit(
        document_builder=partial(
            build_trusted_runtime_document,
            assistant_label=persona.display_name,
        )
    ).audit(artifact)


async def _trusted_motion_gate(
    artifact: WidgetArtifact,
    persona: AssistantPersona,
) -> object:
    from builder_lab.refinement_motion import RefinementMotionAudit

    return await RefinementMotionAudit(
        assistant_label=persona.display_name,
    ).audit(artifact)


async def _await_gate(
    gate: GateCallable,
    artifact: WidgetArtifact,
    persona: AssistantPersona,
    *,
    gate_name: str,
) -> object:
    run_gate = _bound_async_callable(
        gate,
        error_code="invalid_async_gate",
    )
    result = await run_gate(artifact, persona)
    if gate_name == "browser":
        from builder_lab.browser_audit import BrowserAuditReport

        valid = isinstance(result, BrowserAuditReport)
    elif gate_name == "motion":
        from builder_lab.refinement_motion import RefinementMotionReport

        valid = isinstance(result, RefinementMotionReport)
    else:  # pragma: no cover - internal programming guard
        raise AssertionError("unknown benchmark gate")
    if not valid:
        raise BenchmarkError("invalid_gate_report", "gate did not return typed evidence")
    return result


def _bound_async_method(
    instance: object,
    name: str,
    *,
    error_code: str,
) -> Callable[..., Awaitable[object]]:
    descriptor = inspect.getattr_static(type(instance), name, None)
    if descriptor is None or not inspect.iscoroutinefunction(descriptor):
        raise BenchmarkError(error_code, "callable must be declared async")
    return descriptor.__get__(instance, type(instance))


def _bound_async_callable(
    value: object,
    *,
    error_code: str,
) -> Callable[..., Awaitable[object]]:
    if inspect.iscoroutinefunction(value):
        return value
    descriptor = inspect.getattr_static(type(value), "__call__", None)
    if descriptor is None or not inspect.iscoroutinefunction(descriptor):
        raise BenchmarkError(error_code, "callable must be declared async")
    return descriptor.__get__(value, type(value))


def _gate_evidence_digest(
    report: object,
    *,
    candidate_digest: str,
    gate_name: str,
) -> str:
    if gate_name == "browser":
        from builder_lab.browser_audit import BrowserAuditReport

        if not isinstance(report, BrowserAuditReport):
            raise BenchmarkError("invalid_gate_report", "browser evidence is invalid")
        evidence: object = {
            "screenshots": [item.evidence.to_dict() for item in report.screenshots],
            "layouts": [item.to_dict() for item in report.layouts],
        }
    elif gate_name == "motion":
        from builder_lab.refinement_motion import RefinementMotionReport

        if not isinstance(report, RefinementMotionReport):
            raise BenchmarkError("invalid_gate_report", "motion evidence is invalid")
        evidence = asdict(report)
    else:  # pragma: no cover - internal programming guard
        raise AssertionError("unknown benchmark gate")
    return _sha256(
        _canonical_json_bytes(
            {
                "candidate_digest": candidate_digest,
                "gate": gate_name,
                "evidence": evidence,
            }
        )
    )


def _success_receipt(
    *,
    bundle: BenchmarkBundle,
    arm: PreparedArm,
    imported: ImportedRefinement,
    metadata: EditorMetadata,
    attestation: ExternalAttestation | None,
    gate_evidence: Mapping[str, str | None],
) -> dict[str, Any]:
    receipt = _receipt_base(
        bundle=bundle,
        arm=arm,
        metadata=metadata,
        attestation=attestation,
    )
    receipt.update(
        {
            "status": "completed",
            "candidate_digest": _candidate_digest(imported),
            "changed_paths": list(imported.changed_paths),
            "validation": {"status": "passed"},
            "browser": {"status": "passed"},
            "motion": {"status": "passed"},
            "gate_evidence": dict(gate_evidence),
            "public_summary": imported.public_summary,
            "failure": None,
        }
    )
    _assert_public_payload_safe(receipt)
    return receipt


def _failure_receipt(
    *,
    bundle: BenchmarkBundle,
    arm: PreparedArm,
    metadata: EditorMetadata,
    error_code: str,
    gate_statuses: Mapping[str, str],
    imported: ImportedRefinement | None,
    attestation: ExternalAttestation | None,
    gate_evidence: Mapping[str, str | None],
) -> dict[str, Any]:
    receipt = _receipt_base(
        bundle=bundle,
        arm=arm,
        metadata=metadata,
        attestation=attestation,
    )
    public_message = _failure_public_message(error_code)
    candidate_is_reportable = imported is not None and error_code != "unsafe_public_output"
    receipt.update(
        {
            "status": "failed",
            "candidate_digest": (
                _candidate_digest(imported) if imported is not None else None
            ),
            "changed_paths": (
                list(imported.changed_paths) if imported is not None else []
            ),
            "validation": {"status": gate_statuses["validation"]},
            "browser": {"status": gate_statuses["browser"]},
            "motion": {"status": gate_statuses["motion"]},
            "gate_evidence": dict(gate_evidence),
            "public_summary": (
                imported.public_summary if candidate_is_reportable else public_message
            ),
            "failure": {
                "code": error_code,
                "public_message": public_message,
            },
        }
    )
    _assert_public_payload_safe(receipt)
    return receipt


def _receipt_base(
    *,
    bundle: BenchmarkBundle,
    arm: PreparedArm,
    metadata: EditorMetadata,
    attestation: ExternalAttestation | None,
) -> dict[str, Any]:
    return {
        "schema_version": _RECEIPT_SCHEMA,
        "arm": arm.spec.name,
        "provider": arm.spec.provider,
        "model": arm.spec.model,
        "effort": arm.spec.effort,
        "duration_ms": metadata.duration_ms,
        "usage": {
            "input_tokens": metadata.input_tokens,
            "cached_input_tokens": metadata.cached_input_tokens,
            "output_tokens": metadata.output_tokens,
        },
        "source_digest": bundle.source_digest,
        "attestation": (
            {
                "input_digest": attestation.input_digest,
                "invocation_id": attestation.invocation_id,
                "invocation_sha256": attestation.invocation_sha256,
                "model_verified": attestation.model_verified,
                "workspace_scope_verified": attestation.workspace_scope_verified,
                "fallback_used": attestation.fallback_used,
            }
            if attestation is not None
            else None
        ),
    }


def _safe_error_code(exc: BaseException) -> str:
    if isinstance(exc, RefinementWorkspaceError):
        code = exc.error_code
    elif isinstance(exc, BenchmarkError):
        code = exc.error_code
    else:
        code = "editor_or_gate_failed"
    if not isinstance(code, str) or re.fullmatch(r"[a-z0-9_]{1,64}", code) is None:
        return "benchmark_failed"
    return code


def _failure_public_message(error_code: str) -> str:
    if error_code in {"result_not_complete", "missing_output", "no_changes"}:
        return "Кандидат не завершил локальную подготовку."
    if error_code == "unsafe_public_output":
        return "Кандидат отклонён: публичный результат содержит закрытые данные."
    if error_code in {
        "docker_editor_required",
        "docker_editor_unavailable",
        "unsafe_codex_editor",
    }:
        return "Codex-кандидат не запущен в обязательном Docker-контуре."
    if error_code in {
        "external_attestation_required",
        "external_attestation_invalid",
        "external_attestation_mismatch",
        "external_attestation_unsupported",
        "external_editor_forbidden",
    }:
        return "Внешний кандидат не имеет подтверждённого локального запуска."
    if error_code == "editor_timeout":
        return "Редактор не завершил работу в отведённое время."
    if error_code == "validation_failed":
        return "Кандидат не прошёл локальную проверку артефакта."
    return "Кандидат не прошёл локальный контур проверки."


def _persist_finalized_evidence(
    bundle: BenchmarkBundle,
    results: tuple[ArmResult, ...],
    *,
    fingerprints: tuple[bytes, ...],
) -> tuple[Path, tuple[ArmResult, ...]]:
    state_digest = _sha256(
        _canonical_json_bytes(
            {
                "source_digest": bundle.source_digest,
                "receipts": [result.receipt for result in results],
            }
        )
    )
    evidence_parent = bundle.output / "evidence"
    _ensure_private_directory(evidence_parent)
    with tempfile.TemporaryDirectory(
        prefix="finalized-evidence-",
        dir=bundle.output / "control",
    ) as temporary:
        source = Path(temporary)
        receipts = source / "receipts"
        candidates = source / "candidates"
        baseline = source / "source"
        for directory in (receipts, candidates, baseline):
            _make_private_directory(directory)
        _write_new_bytes(
            baseline / "artifact.json",
            _canonical_json_bytes(bundle.source_artifact.to_dict()),
        )
        _write_new_bytes(
            baseline / "persona.json",
            _canonical_json_bytes(bundle.source_persona.to_dict()),
        )
        for result in results:
            _write_public_receipt(
                receipts / f"{result.arm.spec.name}.json",
                result.receipt,
                fingerprints=fingerprints,
            )
            if result.receipt["status"] != "completed":
                continue
            if result.imported is None:  # pragma: no cover - internal invariant
                raise RuntimeError("completed result is missing its candidate")
            candidate_root = candidates / result.arm.spec.name
            _make_private_directory(candidate_root)
            _write_new_bytes(
                candidate_root / "artifact.json",
                _canonical_json_bytes(result.imported.artifact.to_dict()),
            )
            _write_new_bytes(
                candidate_root / "persona.json",
                _canonical_json_bytes(result.imported.persona.to_dict()),
            )
        frozen = freeze_bundle(
            source,
            evidence_parent / state_digest,
            metadata={
                "kind": "refinement-finalized-evidence",
                "source_digest": bundle.source_digest,
                "state_digest": state_digest,
            },
        )
    if not verify_bundle(frozen.root):
        raise BenchmarkError("invalid_evidence", "finalized evidence is not frozen")
    pointer = {
        "schema_version": _CONTROL_SCHEMA,
        "state_digest": state_digest,
        "bundle_digest": frozen.digest,
        "evidence_relative": f"evidence/{state_digest}",
    }
    _write_canonical_atomic(
        bundle.output / "control" / "latest-finalized.json",
        pointer,
        limit=_CONTROL_LIMIT,
    )
    frozen_results = tuple(
        ArmResult(
            arm=result.arm,
            imported=result.imported,
            receipt=result.receipt,
            receipt_path=frozen.root / "receipts" / f"{result.arm.spec.name}.json",
        )
        for result in results
    )
    return frozen.root, frozen_results


def load_finalized_benchmark(
    bundle: BenchmarkBundle,
) -> tuple[Path, tuple[ArmResult, ...]]:
    pointer = _read_json(
        bundle.output / "control" / "latest-finalized.json",
        limit=_CONTROL_LIMIT,
    )
    if not isinstance(pointer, dict) or set(pointer) != {
        "schema_version",
        "state_digest",
        "bundle_digest",
        "evidence_relative",
    }:
        raise BenchmarkError("invalid_evidence", "finalized pointer is invalid")
    state_digest = str(pointer["state_digest"])
    bundle_digest = str(pointer["bundle_digest"])
    if (
        pointer["schema_version"] != _CONTROL_SCHEMA
        or _HEX_DIGEST.fullmatch(state_digest) is None
        or _HEX_DIGEST.fullmatch(bundle_digest) is None
        or pointer["evidence_relative"] != f"evidence/{state_digest}"
    ):
        raise BenchmarkError("invalid_evidence", "finalized pointer is inconsistent")
    evidence_root = _resolved_local_path(bundle.output / "evidence" / state_digest)
    if not evidence_root.is_relative_to(bundle.output) or not verify_bundle(evidence_root):
        raise BenchmarkError("invalid_evidence", "finalized evidence was modified")
    manifest = _read_json(evidence_root / "manifest.json", limit=_CONTROL_LIMIT)
    if not isinstance(manifest, dict) or manifest.get("bundle_digest") != bundle_digest:
        raise BenchmarkError("invalid_evidence", "finalized digest does not match")
    results: list[ArmResult] = []
    for arm in bundle.arms:
        receipt_path = evidence_root / "receipts" / f"{arm.spec.name}.json"
        receipt = _read_json(receipt_path, limit=_PUBLIC_RECEIPT_LIMIT)
        if (
            not isinstance(receipt, dict)
            or receipt.get("schema_version") != _RECEIPT_SCHEMA
            or receipt.get("arm") != arm.spec.name
            or receipt.get("source_digest") != bundle.source_digest
        ):
            raise BenchmarkError("invalid_evidence", "frozen receipt is invalid")
        _assert_public_payload_safe(receipt)
        imported: ImportedRefinement | None = None
        if receipt.get("status") == "completed":
            candidate_root = evidence_root / "candidates" / arm.spec.name
            artifact = WidgetArtifact.from_dict(
                _read_json(candidate_root / "artifact.json", limit=_INPUT_JSON_LIMIT)
            )
            persona = AssistantPersona.from_dict(
                _read_json(candidate_root / "persona.json", limit=_INPUT_JSON_LIMIT)
            )
            imported = ImportedRefinement(
                artifact=artifact,
                persona=persona,
                public_summary=str(receipt["public_summary"]),
                changed_paths=tuple(receipt["changed_paths"]),
            )
            if _candidate_digest(imported) != receipt.get("candidate_digest"):
                raise BenchmarkError("invalid_evidence", "frozen candidate is invalid")
        results.append(
            ArmResult(
                arm=arm,
                imported=imported,
                receipt=receipt,
                receipt_path=receipt_path,
            )
        )
    return evidence_root, tuple(results)


def freeze_finalized_benchmark(
    bundle: BenchmarkBundle,
    *,
    forbidden_fingerprints: Sequence[bytes] = (),
) -> Path:
    fingerprints = _validated_fingerprints(forbidden_fingerprints)
    evidence_root, results = load_finalized_benchmark(bundle)
    return _freeze_comparison(
        bundle,
        results,
        evidence_root=evidence_root,
        fingerprints=fingerprints,
    )


def _freeze_comparison(
    bundle: BenchmarkBundle,
    results: tuple[ArmResult, ...],
    *,
    evidence_root: Path,
    fingerprints: tuple[bytes, ...],
) -> Path:
    if not verify_bundle(evidence_root):
        raise BenchmarkError("invalid_evidence", "finalized evidence was modified")
    _assert_no_secret_material(
        _canonical_json_bytes(
            {
                "artifact": bundle.source_artifact.to_dict(),
                "persona": bundle.source_persona.to_dict(),
            }
        ),
        fingerprints=fingerprints,
    )
    variants: list[ComparisonVariant] = []
    with tempfile.TemporaryDirectory(
        prefix="comparison-source-",
        dir=bundle.output / "control",
    ) as temporary:
        source_root = Path(temporary)
        variants_root = source_root / "variants"
        receipts_root = source_root / "receipts"
        _make_private_directory(variants_root)
        _make_private_directory(receipts_root)
        baseline_source = variants_root / "baseline"
        _make_private_directory(baseline_source)
        baseline_document = build_trusted_runtime_document(
            bundle.source_artifact,
            assistant_label=bundle.source_persona.display_name,
        )
        _assert_no_secret_material(
            baseline_document.encode("utf-8"),
            fingerprints=fingerprints,
            scan_patterns=False,
        )
        _write_private_text(baseline_source / "index.html", baseline_document)
        variants.append(
            ComparisonVariant(
                slug="variants/baseline",
                title="RFN — исходная версия",
                model="current accepted baseline",
                thinking="n/a",
                status="baseline",
                summary="Исходная принятая версия до единого редактора.",
            )
        )

        for result in results:
            spec = result.arm.spec
            variant_source = variants_root / spec.name
            _make_private_directory(variant_source)
            receipt_bytes = _read_regular_bytes(
                evidence_root / "receipts" / f"{spec.name}.json",
                limit=_PUBLIC_RECEIPT_LIMIT,
            )
            _assert_no_secret_material(receipt_bytes, fingerprints=fingerprints)
            _write_new_bytes(receipts_root / f"{spec.name}.json", receipt_bytes)
            if result.receipt["status"] == "completed":
                candidate_root = evidence_root / "candidates" / spec.name
                artifact = WidgetArtifact.from_dict(
                    _read_json(
                        candidate_root / "artifact.json",
                        limit=_INPUT_JSON_LIMIT,
                    )
                )
                persona = AssistantPersona.from_dict(
                    _read_json(
                        candidate_root / "persona.json",
                        limit=_INPUT_JSON_LIMIT,
                    )
                )
                document = build_trusted_runtime_document(
                    artifact,
                    assistant_label=persona.display_name,
                )
                status = "completed"
                summary = str(result.receipt["public_summary"])
            else:
                code = str(result.receipt["failure"]["code"])
                summary = str(result.receipt["public_summary"])
                document = _failure_card(spec.title, code, summary)
                status = "failed"
                summary = f"{summary} [{code}]"
            _assert_no_secret_material(
                document.encode("utf-8"),
                fingerprints=fingerprints,
                scan_patterns=False,
            )
            _write_private_text(variant_source / "index.html", document)
            variants.append(
                ComparisonVariant(
                    slug=f"variants/{spec.name}",
                    title=spec.title,
                    model=spec.model,
                    thinking=spec.effort,
                    status=status,
                    summary=summary,
                )
            )
        comparison_document = render_comparison_page(tuple(variants))
        _assert_no_secret_material(
            comparison_document.encode("utf-8"),
            fingerprints=fingerprints,
            scan_patterns=False,
        )
        _write_private_text(
            source_root / "index.html",
            comparison_document,
        )
        evidence_manifest = _read_json(
            evidence_root / "manifest.json",
            limit=_CONTROL_LIMIT,
        )
        evidence_digest = str(evidence_manifest.get("bundle_digest", ""))
        if _HEX_DIGEST.fullmatch(evidence_digest) is None:
            raise BenchmarkError("invalid_evidence", "evidence digest is invalid")
        comparison_parent = bundle.output / "comparison"
        _ensure_private_directory(comparison_parent)
        frozen = freeze_bundle(
            source_root,
            comparison_parent / evidence_root.name,
            metadata={
                "kind": "refinement-comparison",
                "source_digest": bundle.source_digest,
                "evidence_bundle_digest": evidence_digest,
            },
        )
    if not verify_bundle(frozen.root):
        raise BenchmarkError("invalid_comparison", "comparison bundle is not frozen")
    return frozen.root / "index.html"


def _failure_card(title: str, code: str, message: str) -> str:
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>html,body{{height:100%;margin:0}}body{{display:grid;place-items:center;background:#181714;color:#f4efe7;font:16px/1.5 Arial,sans-serif}}main{{width:min(560px,calc(100% - 32px));padding:28px;border:1px solid #514b42;border-radius:22px;background:#24211d}}code{{color:#d8ff52}}</style>
</head><body><main><p>Локальный прогон не завершён.</p>
<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>
<p><code>{html.escape(code)}</code></p></main></body></html>"""


def _normalize_editor_metadata(value: object) -> EditorMetadata:
    if value is None:
        return EditorMetadata()
    if isinstance(value, EditorMetadata):
        return value
    return EditorMetadata(
        duration_ms=getattr(value, "duration_ms", None),
        input_tokens=getattr(value, "input_tokens", None),
        cached_input_tokens=getattr(value, "cached_input_tokens", None),
        output_tokens=getattr(value, "output_tokens", None),
    )


def _candidate_digest(imported: ImportedRefinement) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "artifact": imported.artifact.to_dict(),
                "persona": imported.persona.to_dict(),
            }
        )
    ).hexdigest()


def _candidate_binding(
    arm: PreparedArm,
    imported: ImportedRefinement,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    editable_hashes = tuple(
        (
            relative,
            _sha256(
                _read_regular_bytes(
                    arm.workspace / relative,
                    limit=_INPUT_JSON_LIMIT,
                )
            ),
        )
        for relative in (
            "editable/persona.json",
            "editable/result.json",
            "editable/widget.css",
            "editable/widget.html",
        )
    )
    return _candidate_digest(imported), editable_hashes


def _workspace_receipt_digest(receipt: FrozenWorkspaceReceipt) -> str:
    return _sha256(
        _canonical_json_bytes(
            {
                "manifest_sha256": receipt.manifest_sha256,
                "immutable_hashes": [list(item) for item in receipt.immutable_hashes],
            }
        )
    )


def _input_digest(
    *,
    source_artifact: WidgetArtifact,
    source_persona: AssistantPersona,
    request: str,
    screenshots: Sequence[tuple[str, bytes]],
) -> str:
    payload = {
        "artifact_sha256": _sha256(_canonical_json_bytes(source_artifact.to_dict())),
        "persona_sha256": _sha256(_canonical_json_bytes(source_persona.to_dict())),
        "request_sha256": _sha256(request.encode("utf-8")),
        "screenshots": [
            {"name": name, "sha256": _sha256(data)} for name, data in screenshots
        ],
    }
    return _sha256(_canonical_json_bytes(payload))


def _materialize_control_inputs(
    *,
    control: Path,
    source_artifact: WidgetArtifact,
    source_persona: AssistantPersona,
    request: str,
    screenshots: Sequence[tuple[str, bytes]],
) -> None:
    input_root = control / "input"
    shots_root = input_root / "screenshots"
    _make_private_directory(input_root)
    _make_private_directory(shots_root)
    _write_new_bytes(
        input_root / "source-artifact.json",
        _canonical_json_bytes(source_artifact.to_dict()),
    )
    _write_new_bytes(
        input_root / "source-persona.json",
        _canonical_json_bytes(source_persona.to_dict()),
    )
    _write_new_bytes(input_root / "request.md", request.encode("utf-8"))
    for name, payload in screenshots:
        _write_new_bytes(shots_root / f"{name}.jpg", payload)


def _clone_refinement_workspace(
    *,
    template: RefinementWorkspace,
    root: Path,
) -> RefinementWorkspace:
    """Create an independent byte-identical workspace with a root-bound receipt."""

    if root.exists() or root.is_symlink() or _is_link_like(root.parent):
        raise BenchmarkError("unsafe_output", "arm workspace destination is unsafe")
    shutil.copytree(template.root, root, copy_function=shutil.copy2)
    for path in sorted(root.rglob("*")):
        if _is_link_like(path):
            raise BenchmarkError("unsafe_output", "cloned workspace contains a link")
        if os.name == "posix":
            path.chmod(0o700 if path.is_dir() else 0o600)
    if os.name == "posix":
        root.chmod(0o700)
    receipt = FrozenWorkspaceReceipt(
        root=root,
        manifest_bytes=template.receipt.manifest_bytes,
        manifest_sha256=template.receipt.manifest_sha256,
        immutable_hashes=template.receipt.immutable_hashes,
    )
    return RefinementWorkspace(
        root=root,
        editable=root / "editable",
        manifest_path=root / "manifest.json",
        receipt=receipt,
    )


def _write_control_manifest(
    path: Path,
    *,
    source_digest: str,
    specs: Sequence[ArmSpec],
) -> None:
    payload = {
        "schema_version": _CONTROL_SCHEMA,
        "source_digest": source_digest,
        "arms": [_arm_spec_dict(spec) for spec in specs],
    }
    _write_new_bytes(path, _canonical_json_bytes(payload) + b"\n")


def _write_control_receipt(path: Path, arm: PreparedArm) -> None:
    payload = {
        "schema_version": _CONTROL_SCHEMA,
        "arm": arm.spec.name,
        "input_digest": arm.input_digest,
        "challenge": arm.challenge,
        "workspace_relative": f"arms/{arm.spec.name}",
        "manifest_bytes_b64": base64.b64encode(arm.receipt.manifest_bytes).decode("ascii"),
        "manifest_sha256": arm.receipt.manifest_sha256,
        "immutable_hashes": [list(item) for item in arm.receipt.immutable_hashes],
    }
    _write_new_bytes(path, _canonical_json_bytes(payload) + b"\n")


def _read_control_receipt(
    *,
    output_root: Path,
    spec: ArmSpec,
    source_digest: str,
    path: Path,
) -> PreparedArm:
    payload = _read_json(path, limit=_CONTROL_LIMIT)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "arm",
        "input_digest",
        "challenge",
        "workspace_relative",
        "manifest_bytes_b64",
        "manifest_sha256",
        "immutable_hashes",
    }:
        raise BenchmarkError("invalid_control", "workspace receipt is invalid")
    expected_relative = f"arms/{spec.name}"
    if (
        payload["schema_version"] != _CONTROL_SCHEMA
        or payload["arm"] != spec.name
        or payload["input_digest"] != source_digest
        or _HEX_DIGEST.fullmatch(str(payload["challenge"])) is None
        or payload["workspace_relative"] != expected_relative
    ):
        raise BenchmarkError("invalid_control", "workspace receipt is inconsistent")
    try:
        manifest_bytes = base64.b64decode(
            str(payload["manifest_bytes_b64"]),
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise BenchmarkError("invalid_control", "workspace manifest encoding is invalid") from exc
    if len(manifest_bytes) > _CONTROL_LIMIT:
        raise BenchmarkError("invalid_control", "workspace manifest is too large")
    immutable_raw = payload["immutable_hashes"]
    if not isinstance(immutable_raw, list):
        raise BenchmarkError("invalid_control", "workspace hashes are invalid")
    immutable_hashes: list[tuple[str, str]] = []
    for item in immutable_raw:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(value, str) for value in item)
            or _HEX_DIGEST.fullmatch(item[1]) is None
        ):
            raise BenchmarkError("invalid_control", "workspace hashes are invalid")
        immutable_hashes.append((item[0], item[1]))
    workspace = _resolved_local_path(output_root / expected_relative)
    if workspace == output_root or not workspace.is_relative_to(output_root):
        raise BenchmarkError("invalid_control", "workspace path escapes output")
    receipt = FrozenWorkspaceReceipt(
        root=workspace,
        manifest_bytes=manifest_bytes,
        manifest_sha256=str(payload["manifest_sha256"]),
        immutable_hashes=tuple(tuple(item) for item in immutable_hashes),
    )
    return PreparedArm(
        spec=spec,
        workspace=workspace,
        receipt=receipt,
        input_digest=source_digest,
        challenge=str(payload["challenge"]),
    )


def _arm_spec_dict(spec: ArmSpec) -> dict[str, str]:
    return {
        "name": spec.name,
        "title": spec.title,
        "provider": spec.provider,
        "model": spec.model,
        "effort": spec.effort,
        "execution": spec.execution,
    }


def _arm_spec_from_dict(payload: object) -> ArmSpec:
    if not isinstance(payload, dict) or set(payload) != {
        "name",
        "title",
        "provider",
        "model",
        "effort",
        "execution",
    }:
        raise BenchmarkError("invalid_control", "arm spec is invalid")
    try:
        return ArmSpec(**payload)
    except (TypeError, ValueError) as exc:
        raise BenchmarkError("invalid_control", "arm spec is invalid") from exc


def _validated_request(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("request must be text")
    if not value.strip() or "\x00" in value or len(value.encode("utf-8")) > _REQUEST_LIMIT:
        raise BenchmarkError("invalid_request", "request is empty or too large")
    return value


def _validated_screenshots(
    screenshots: Sequence[tuple[str, bytes]],
) -> tuple[tuple[str, bytes], ...]:
    items = tuple(screenshots)
    if tuple(item[0] for item in items if isinstance(item, tuple) and len(item) == 2) != (
        CANONICAL_SCREENSHOT_NAMES
    ) or len(items) != len(CANONICAL_SCREENSHOT_NAMES):
        raise BenchmarkError("invalid_screenshots", "screenshots are not canonical")
    for name, payload in items:
        if (
            not isinstance(name, str)
            or not isinstance(payload, bytes)
            or not payload.startswith(b"\xff\xd8\xff")
            or not payload.endswith(b"\xff\xd9")
            or len(payload) > _SCREENSHOT_LIMIT
        ):
            raise BenchmarkError("invalid_screenshots", "screenshot is invalid")
    return items


def _prepare_private_output(
    output: Path,
    *,
    trusted_private_base: Path | None,
) -> Path:
    root = _resolved_local_path(output)
    base = _resolved_local_path(trusted_private_base) if trusted_private_base else None
    _validate_below_base(root, base)
    if root.exists() or root.is_symlink():
        raise FileExistsError(f"benchmark output already exists: {root}")
    parent = root.parent
    _validate_private_parent(parent)
    _make_private_directory(root)
    return root


def _validate_existing_private_output(
    output: Path,
    *,
    trusted_private_base: Path | None,
) -> Path:
    root = _resolved_local_path(output)
    base = _resolved_local_path(trusted_private_base) if trusted_private_base else None
    _validate_below_base(root, base)
    _validate_private_directory(root)
    return root


def _validate_below_base(root: Path, base: Path | None) -> None:
    if base is not None and (root == base or not root.is_relative_to(base)):
        raise BenchmarkError("unsafe_output", "output must be below the trusted base")
    if os.name == "nt" and base is None:
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            raise BenchmarkError("unsafe_output", "LOCALAPPDATA is unavailable")
        windows_base = (Path(local_app_data) / "Temp").resolve(strict=False)
        if root == windows_base or not root.is_relative_to(windows_base):
            raise BenchmarkError("unsafe_output", "output must be under the private temp base")


def _resolved_local_path(value: Path | None) -> Path:
    if not isinstance(value, Path):
        raise TypeError("local paths must be pathlib.Path values")
    text = str(value)
    if (
        not text
        or "\x00" in text
        or _URI_SCHEME.match(text)
        or text.startswith("\\\\")
        or text.startswith("//")
    ):
        raise BenchmarkError("non_local_path", "only local filesystem paths are allowed")
    absolute = Path(os.path.abspath(value))
    _reject_link_like_components(absolute)
    return absolute


def _make_private_directory(path: Path) -> None:
    os.mkdir(path, mode=0o700)
    if os.name == "posix":
        os.chmod(path, 0o700)


def _ensure_private_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        _validate_private_directory(path)
        return
    _validate_private_parent(path.parent)
    _make_private_directory(path)


def _validate_private_parent(path: Path) -> None:
    _reject_link_like_components(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise BenchmarkError("unsafe_output", "output parent is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or _is_link_like(path):
        raise BenchmarkError("unsafe_output", "output parent must be a real directory")
    if os.name == "posix":
        mode = stat.S_IMODE(info.st_mode)
        sticky_safe = bool(mode & stat.S_ISVTX)
        writable_by_others = bool(mode & (stat.S_IWGRP | stat.S_IWOTH))
        current_uid = os.geteuid()
        if (writable_by_others and not sticky_safe) or (
            info.st_uid != current_uid and not sticky_safe
        ):
            raise BenchmarkError(
                "unsafe_output",
                "output parent is not private or sticky-safe",
            )


def _validate_private_directory(path: Path) -> None:
    _validate_private_parent(path)
    if os.name == "posix":
        info = path.lstat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & (
            stat.S_IRWXG | stat.S_IRWXO
        ):
            raise BenchmarkError("unsafe_output", "evidence directory is not private")


def _reject_link_like_components(path: Path) -> None:
    current = path
    existing: list[Path] = []
    while True:
        if current.exists() or current.is_symlink():
            existing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for component in reversed(existing):
        if _is_link_like(component):
            raise BenchmarkError("unsafe_output", "path contains a symlink or junction")


def _write_new_bytes(path: Path, payload: bytes) -> None:
    if os.name == "posix":
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.chmod(path, 0o600)
    else:
        with path.open("xb") as stream:
            stream.write(payload)


def _write_private_text(path: Path, value: str) -> None:
    _write_new_bytes(path, value.encode("utf-8"))


def _write_atomic_bytes(path: Path, payload: bytes) -> None:
    _validate_private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_canonical_atomic(
    path: Path,
    payload: Mapping[str, Any],
    *,
    limit: int,
) -> None:
    serialized = _canonical_json_bytes(payload) + b"\n"
    if len(serialized) > limit:
        raise BenchmarkError("receipt_too_large", "local receipt is too large")
    _write_atomic_bytes(path, serialized)


def _write_public_receipt(
    path: Path,
    payload: Mapping[str, Any],
    *,
    fingerprints: tuple[bytes, ...],
) -> None:
    _assert_public_payload_safe(payload)
    _assert_no_secret_material(
        _canonical_json_bytes(payload),
        fingerprints=fingerprints,
    )
    _write_canonical_atomic(path, payload, limit=_PUBLIC_RECEIPT_LIMIT)


def _read_json(path: Path, *, limit: int) -> Any:
    payload = _read_regular_bytes(path, limit=limit)
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError("invalid_json", "local JSON input is invalid") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkError("duplicate_json_key", "local JSON has duplicate keys")
        result[key] = value
    return result


def _read_text(path: Path, *, limit: int) -> str:
    try:
        return _read_regular_bytes(path, limit=limit).decode("utf-8")
    except UnicodeError as exc:
        raise BenchmarkError("invalid_utf8", "local input is not UTF-8") from exc


def _read_regular_bytes(path: Path, *, limit: int) -> bytes:
    _reject_link_like_components(path.parent)
    try:
        pre_open = path.lstat()
    except OSError as exc:
        raise BenchmarkError("missing_input", "local input is unavailable") from exc
    if (
        not stat.S_ISREG(pre_open.st_mode)
        or pre_open.st_nlink != 1
        or pre_open.st_size > limit
        or _is_link_like(path)
    ):
        raise BenchmarkError("unsafe_input", "local input is not a safe regular file")
    try:
        descriptor = _open_readonly_nofollow(path)
    except OSError as exc:
        raise BenchmarkError("missing_input", "local input is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > limit
        ):
            raise BenchmarkError(
                "unsafe_input",
                "local input is not a bounded regular file",
            )
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        path_info = path.lstat()
    except OSError as exc:
        raise BenchmarkError("input_changed_during_read", "local input disappeared") from exc
    identity_before = (
        pre_open.st_dev,
        pre_open.st_ino,
        pre_open.st_mode,
        pre_open.st_nlink,
        pre_open.st_size,
        pre_open.st_mtime_ns,
    )
    opened_identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
    )
    path_identity = (
        path_info.st_dev,
        path_info.st_ino,
        path_info.st_mode,
        path_info.st_nlink,
        path_info.st_size,
        path_info.st_mtime_ns,
    )
    if (
        len(payload) > limit
        or len(payload) != before.st_size
        or identity_before != opened_identity
        or opened_identity != identity_after
        or identity_after != path_identity
        or _is_link_like(path)
    ):
        raise BenchmarkError("input_changed_during_read", "local input changed during read")
    return payload


def _open_readonly_nofollow(path: Path) -> int:
    if os.name == "nt":
        return _open_windows_readonly_nofollow(path)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise BenchmarkError("unsafe_input", "platform has no no-follow file open")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    return os.open(path, flags)


def _open_windows_readonly_nofollow(path: Path) -> int:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class _FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("reparse_tag", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    generic_read = 0x80000000
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    file_flag_sequential_scan = 0x08000000
    file_attribute_reparse_point = 0x00000400
    file_attribute_tag_info = 9
    handle = kernel32.CreateFileW(
        str(path),
        generic_read,
        share_read_write_delete,
        None,
        open_existing,
        file_flag_open_reparse_point | file_flag_sequential_scan,
        None,
    )
    invalid_handle = wintypes.HANDLE(-1).value
    if handle in (None, invalid_handle):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, "CreateFileW failed", str(path))

    tag_info = _FileAttributeTagInfo()
    if not kernel32.GetFileInformationByHandleEx(
        handle,
        file_attribute_tag_info,
        ctypes.byref(tag_info),
        ctypes.sizeof(tag_info),
    ):
        error_code = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise OSError(error_code, "GetFileInformationByHandleEx failed", str(path))
    if tag_info.file_attributes & file_attribute_reparse_point:
        kernel32.CloseHandle(handle)
        raise BenchmarkError("unsafe_input", "local input is a reparse point")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        return msvcrt.open_osfhandle(int(handle), flags)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    junction = getattr(path, "is_junction", None)
    return bool(junction is not None and junction())


def _assert_safe_public_text(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > 2048
        or "\x00" in value
        or _SECRET_VALUE.search(value)
    ):
        raise BenchmarkError("unsafe_public_output", "public output is unsafe")


def _validated_fingerprints(values: Sequence[bytes]) -> tuple[bytes, ...]:
    result: list[bytes] = []
    for value in values:
        if not isinstance(value, bytes) or not 8 <= len(value) <= 4096:
            raise ValueError("forbidden fingerprints must be bounded byte strings")
        if value not in result:
            result.append(value)
    return tuple(result)


def _assert_candidate_safe(
    *,
    arm: PreparedArm,
    imported: ImportedRefinement,
    request: str,
    fingerprints: tuple[bytes, ...],
) -> None:
    _assert_safe_public_text(imported.public_summary)
    if request.strip() in imported.public_summary:
        raise BenchmarkError(
            "unsafe_public_output",
            "public summary repeats the private request",
        )
    payloads = [
        _read_regular_bytes(arm.workspace / relative, limit=_INPUT_JSON_LIMIT)
        for relative in (
            "editable/widget.html",
            "editable/widget.css",
            "editable/persona.json",
            "editable/result.json",
        )
    ]
    payloads.append(
        _canonical_json_bytes(
            {
                "artifact": imported.artifact.to_dict(),
                "persona": imported.persona.to_dict(),
            }
        )
    )
    for payload in payloads:
        _assert_no_secret_material(payload, fingerprints=fingerprints)


def _assert_no_secret_material(
    payload: bytes,
    *,
    fingerprints: tuple[bytes, ...],
    scan_patterns: bool = True,
) -> None:
    if any(fingerprint in payload for fingerprint in fingerprints):
        raise BenchmarkError("unsafe_public_output", "output contains a secret fingerprint")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise BenchmarkError("unsafe_public_output", "public output is not UTF-8") from exc
    if "\x00" in text or (scan_patterns and _SECRET_VALUE.search(text)):
        raise BenchmarkError("unsafe_public_output", "output contains secret-like material")


def _assert_public_payload_safe(value: object) -> None:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 4096 or "\x00" in value or _SECRET_VALUE.search(value):
            raise BenchmarkError("unsafe_public_output", "receipt contains unsafe text")
    elif isinstance(value, Mapping):
        forbidden = {"authorization", "access_token", "api_key", "raw_model_output", "final_text", "prompt"}
        for key, child in value.items():
            if not isinstance(key, str) or key.lower() in forbidden:
                raise BenchmarkError("unsafe_public_output", "receipt contains a forbidden field")
            _assert_public_payload_safe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_public_payload_safe(child)
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise BenchmarkError("unsafe_public_output", "receipt contains an unsupported value")


def _optional_bounded_int(value: int | None, *, field_name: str, maximum: int) -> None:
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= maximum
    ):
        raise ValueError(f"{field_name} is invalid")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _local_path_argument(value: str) -> Path:
    if (
        not value
        or "\x00" in value
        or _URI_SCHEME.match(value)
        or value.startswith("\\\\")
        or value.startswith("//")
    ):
        raise argparse.ArgumentTypeError("only local filesystem paths are allowed")
    return Path(value)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--source-artifact", required=True, type=_local_path_argument)
    command.add_argument("--source-persona", required=True, type=_local_path_argument)
    command.add_argument("--request", required=True, type=_local_path_argument)
    command.add_argument("--screenshots", required=True, type=_local_path_argument)
    command.add_argument("--output", required=True, type=_local_path_argument)
    command.add_argument(
        "--mode",
        choices=("prepare", "run", "finalize", "freeze"),
        default="prepare",
        help="Local benchmark phase; run is the only phase that invokes Docker Codex.",
    )
    command.add_argument(
        "--prepare-arm",
        choices=tuple(spec.name for spec in DEFAULT_ARM_SPECS),
        help="Prepare exactly one fixed local arm; no provider call is made.",
    )
    return command


def _load_cli_inputs(args: argparse.Namespace) -> tuple[
    WidgetArtifact,
    AssistantPersona,
    str,
    tuple[tuple[str, bytes], ...],
]:
    artifact = WidgetArtifact.from_dict(
        _read_json(
            _resolved_local_path(args.source_artifact),
            limit=_INPUT_JSON_LIMIT,
        )
    )
    persona = AssistantPersona.from_dict(
        _read_json(
            _resolved_local_path(args.source_persona),
            limit=_INPUT_JSON_LIMIT,
        )
    )
    request = _read_text(
        _resolved_local_path(args.request),
        limit=_REQUEST_LIMIT,
    )
    screenshot_root = _resolved_local_path(args.screenshots)
    if not screenshot_root.is_dir() or _is_link_like(screenshot_root):
        raise BenchmarkError("invalid_screenshots", "screenshot input must be a directory")
    screenshots = tuple(
        (
            name,
            _read_regular_bytes(
                screenshot_root / f"{name}.jpg",
                limit=_SCREENSHOT_LIMIT,
            ),
        )
        for name in CANONICAL_SCREENSHOT_NAMES
    )
    return artifact, persona, request, _validated_screenshots(screenshots)


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    artifact, persona, request, screenshots = _load_cli_inputs(args)
    if args.mode == "prepare":
        specs = (
            tuple(spec for spec in DEFAULT_ARM_SPECS if spec.name == args.prepare_arm)
            if args.prepare_arm
            else DEFAULT_ARM_SPECS
        )
        bundle = prepare_benchmark_bundle(
            output=args.output,
            source_artifact=artifact,
            source_persona=persona,
            request=request,
            screenshots=screenshots,
            arm_specs=specs,
        )
        response: dict[str, Any] = {
            "phase": "prepare",
            "output": str(bundle.output),
            "source_digest": bundle.source_digest,
            "prepared_arms": [arm.spec.name for arm in bundle.arms],
        }
    else:
        if args.prepare_arm is not None:
            raise SystemExit("--prepare-arm is valid only with --mode prepare")
        bundle = load_prepared_benchmark(args.output)
        supplied_digest = _input_digest(
            source_artifact=artifact,
            source_persona=persona,
            request=request,
            screenshots=screenshots,
        )
        if supplied_digest != bundle.source_digest:
            raise SystemExit("local inputs do not match the prepared benchmark")
        editors: dict[str, BenchmarkEditor] = {}
        if args.mode == "run":
            codex_arm = next(
                (
                    arm
                    for arm in bundle.arms
                    if arm.spec.execution == "docker-codex"
                ),
                None,
            )
            if codex_arm is not None:
                image_id = os.environ.get("KAIGO_CODEX_WORKSPACE_IMAGE_ID", "").strip()
                auth_file = os.environ.get("KAIGO_CODEX_AUTH_FILE", "").strip()
                if not image_id or not auth_file:
                    raise SystemExit(
                        "Docker Codex requires KAIGO_CODEX_WORKSPACE_IMAGE_ID and "
                        "KAIGO_CODEX_AUTH_FILE"
                    )
                editors[codex_arm.spec.name] = load_docker_codex_editor(
                    image_id=image_id,
                    auth_file=_local_path_argument(auth_file),
                    private_temp_base=bundle.output.parent,
                )
        if args.mode == "run":
            statuses = asyncio.run(
                run_benchmark_editors(
                    bundle,
                    editors=editors,
                )
            )
            response = {
                "phase": "run",
                "output": str(bundle.output),
                "source_digest": bundle.source_digest,
                "statuses": statuses,
            }
        elif args.mode == "finalize":
            report = asyncio.run(
                finalize_benchmark_bundle(
                    bundle,
                    freeze_comparison=False,
                )
            )
            response = {
                "phase": "finalize",
                "output": str(bundle.output),
                "source_digest": bundle.source_digest,
                "evidence": str(report.evidence_root),
                "statuses": {
                    result.arm.spec.name: result.receipt["status"]
                    for result in report.results
                },
            }
        else:
            comparison_index = freeze_finalized_benchmark(bundle)
            response = {
                "phase": "freeze",
                "output": str(bundle.output),
                "source_digest": bundle.source_digest,
                "comparison": str(comparison_index),
            }
    print(json.dumps(response, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ArmResult",
    "ArmSpec",
    "BenchmarkBundle",
    "BenchmarkEditor",
    "BenchmarkError",
    "BenchmarkReport",
    "DEFAULT_ARM_SPECS",
    "EditorMetadata",
    "ExternalAttestation",
    "ExternalInvocationRecord",
    "PreparedArm",
    "finalize_benchmark_bundle",
    "freeze_finalized_benchmark",
    "load_external_attestations",
    "load_execution_attestations",
    "load_finalized_benchmark",
    "load_docker_codex_editor",
    "load_prepared_benchmark",
    "main",
    "parser",
    "prepare_benchmark_bundle",
    "run_benchmark_editors",
    "SignedExternalAttestation",
]
