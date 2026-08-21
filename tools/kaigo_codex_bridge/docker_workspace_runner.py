from __future__ import annotations

import asyncio
import base64
from builtins import BaseExceptionGroup
import csv
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Any, Iterable
from uuid import uuid4

import builder_lab.refinement_workspace as workspace_contract
from builder_lab.refinement_workspace import RefinementWorkspace
from tools.kaigo_codex_bridge.workspace_runner import WorkspaceEditorReceipt


_IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MIN_SECRET_BYTES = 8
_SECRET_FRAGMENT_BYTES = 16
_MAX_AUTH_BYTES = 2 * 1024 * 1024
_MAX_DOCKER_STDOUT = 256 * 1024
_MAX_DOCKER_STDERR = 128 * 1024
_CLEANUP_TIMEOUT_SECONDS = 10.0
_AUTH_EXPIRY_SAFETY_SECONDS = 3600.0
_CONTAINER_RESULT_KEYS = frozenset(
    {"schema_version", "editor", "invocation", "editable_files"}
)
_EDITOR_RESULT_KEYS = frozenset(
    {
        "model",
        "duration_ms",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "thread_id",
        "final_text",
    }
)
_INVOCATION_RESULT_KEYS = frozenset(
    {
        "model",
        "requested_reasoning_effort",
        "effective_reasoning_effort",
        "permission_profile",
        "mcp_tools",
        "shell_tool",
        "unified_exec",
        "screenshots_attached",
        "command_sha256",
    }
)
_PROOF_KEYS = frozenset(
    {
        "schema_version",
        "image_id",
        "source_manifest_sha256",
        "container_result_sha256",
        "editor",
        "invocation",
        "workspace_receipt",
        "candidate_files",
        "candidate_aggregate_sha256",
    }
)
_WORKSPACE_RECEIPT_KEYS = frozenset(
    {"manifest_bytes_b64", "manifest_sha256", "immutable_hashes"}
)
_EDITABLE_NAMES = (
    "widget.html",
    "widget.css",
    "widget.js",
    "persona.json",
    "result.json",
)
_EDITABLE_LIMITS = {
    "widget.html": 64 * 1024,
    "widget.css": 96 * 1024,
    "widget.js": 256 * 1024,
    "persona.json": 64 * 1024,
    "result.json": 16 * 1024,
}
_MODEL = "gpt-5.6-sol"
_REASONING_EFFORT = "max"
_PERMISSION_PROFILE = "kaigo_mcp_read_only"
_MCP_TOOL_NAMES = (
    "list_workspace_inputs",
    "read_workspace_file",
    "write_editable_file",
    "validate_candidate",
)
_DELIVERY_SCHEMA = "kaigo.codex-docker-delivery.v1"
_PROOF_LIMIT = 256 * 1024


class DockerWorkspaceError(RuntimeError):
    """Base error for the local disposable Docker editor."""


class DockerWorkspaceSecretLeak(DockerWorkspaceError):
    """A credential fingerprint appeared in untrusted editor output."""


class DockerWorkspaceTimeout(DockerWorkspaceError):
    """The local Docker benchmark exceeded its absolute deadline."""


class DockerWorkspaceInvalidOutput(DockerWorkspaceError):
    """The container result or copied editable tree failed validation."""


class DockerWorkspaceUnavailable(DockerWorkspaceError):
    """Docker or the disposable editor container was unavailable."""


@dataclass(frozen=True, slots=True)
class DockerCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class DockerWorkspaceRunResult:
    image_id: str
    model: str
    requested_reasoning_effort: str
    effective_reasoning_effort: str
    duration_ms: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    thread_id: str
    final_text: str
    workspace: RefinementWorkspace
    delivery_root: Path
    proof_path: Path
    proof_sha256: str
    candidate_aggregate_sha256: str


class _CommandRunner:
    async def run(
        self,
        command: tuple[str, ...],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        max_stderr_bytes: int,
    ) -> DockerCommandResult: ...


_ContainerNameFactory = Callable[[], str]


def build_container_create_command(
    *,
    docker_executable: str,
    container_name: str,
    image_id: str,
    workspace: Path,
    auth_copy: Path,
) -> tuple[str, ...]:
    """Build the fixed local benchmark container boundary."""

    docker_path = _trusted_absolute_executable(docker_executable)
    if not re.fullmatch(r"kaigo-refine-[a-z0-9][a-z0-9-]{0,47}", container_name):
        raise ValueError("container_name is invalid")
    if not isinstance(image_id, str) or not _IMAGE_ID_RE.fullmatch(image_id):
        raise ValueError("image_id must be an immutable sha256 image ID")
    workspace_root = _trusted_bind_source(workspace, kind="directory")
    auth_path = _trusted_bind_source(auth_copy, kind="file")
    if "," in str(workspace_root) or "," in str(auth_path):
        raise ValueError("bind source paths must not contain commas")

    return (
        str(docker_path),
        "create",
        "--name",
        container_name,
        "--read-only",
        "--user",
        "10001:10001",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--memory",
        "2g",
        "--memory-swap",
        "2g",
        "--cpus",
        "2",
        "--ulimit",
        "nofile=1024:1024",
        "--network",
        "bridge",
        "--log-driver",
        "none",
        "--stop-timeout",
        "5",
        "--mount",
        f"type=bind,source={workspace_root},target=/seed/workspace,readonly",
        "--mount",
        (
            f"type=bind,source={auth_path},"
            "target=/codex-home/auth.json,readonly"
        ),
        "--tmpfs",
        (
            "/workspace:rw,noexec,nosuid,nodev,size=64m,"
            "uid=10001,gid=10001,mode=0700"
        ),
        "--tmpfs",
        (
            "/codex-home:rw,noexec,nosuid,nodev,size=16m,"
            "uid=10001,gid=10001,mode=0700"
        ),
        "--tmpfs",
        (
            "/tmp:rw,noexec,nosuid,nodev,size=64m,"
            "uid=10001,gid=10001,mode=0700"
        ),
        image_id,
    )


def _trusted_bind_source(path: Path, *, kind: str) -> Path:
    if not isinstance(path, Path):
        raise TypeError("bind sources must be pathlib.Path values")
    absolute = path.absolute()
    if "," in str(absolute):
        raise ValueError("bind source paths must not contain commas")
    for component in _existing_components(absolute):
        if _path_component_is_link_like(component):
            raise ValueError("bind source path contains a symlink or reparse point")
    try:
        info = absolute.lstat()
    except OSError as error:
        raise ValueError("bind source must be an existing regular path") from error
    if kind == "directory":
        valid = stat.S_ISDIR(info.st_mode)
    elif kind == "file":
        valid = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    else:  # pragma: no cover - internal programming guard
        raise AssertionError("unknown bind kind")
    if not valid:
        raise ValueError("bind source must be an existing regular path")
    return absolute.resolve(strict=True)


def _trusted_absolute_executable(value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("executable path must be a non-empty string")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("executable path must be absolute")
    absolute = path.absolute()
    for component in _existing_components(absolute):
        if _path_component_is_link_like(component):
            raise ValueError("executable path contains a symlink or reparse point")
    try:
        info = absolute.lstat()
        resolved = absolute.resolve(strict=True)
    except OSError as error:
        raise ValueError("executable path must be a trusted regular file") from error
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("executable path must be a trusted regular file")
    return resolved


def _resolve_trusted_executable(value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("executable path must be a non-empty string")
    candidate = Path(value)
    if not candidate.is_absolute():
        resolved = shutil.which(value)
        if resolved is None:
            raise ValueError("executable could not be resolved to an absolute path")
        candidate = Path(resolved)
    return _trusted_absolute_executable(str(candidate))


def _existing_components(path: Path) -> tuple[Path, ...]:
    components: list[Path] = []
    current = path
    while True:
        if current.exists() or current.is_symlink():
            components.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return tuple(reversed(components))


def _path_component_is_link_like(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def secret_fingerprints(auth_payload: bytes) -> frozenset[bytes]:
    """Derive non-reportable fingerprints for every JSON string credential."""

    try:
        parsed = json.loads(auth_payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Codex auth copy must contain UTF-8 JSON") from error
    values = tuple(_json_string_values(parsed))
    if not values:
        raise ValueError("Codex auth JSON does not contain credential strings")
    fingerprints: set[bytes] = set()
    for value in values:
        raw = value.encode("utf-8")
        representations = {
            raw,
            json.dumps(value, ensure_ascii=True)[1:-1].encode("utf-8"),
            json.dumps(value, ensure_ascii=False)[1:-1].encode("utf-8"),
            base64.b64encode(raw),
            base64.urlsafe_b64encode(raw),
            base64.b64encode(raw).rstrip(b"="),
            base64.urlsafe_b64encode(raw).rstrip(b"="),
        }
        for representation in representations:
            if len(representation) >= _MIN_SECRET_BYTES:
                fingerprints.add(representation)
            if len(representation) >= _SECRET_FRAGMENT_BYTES:
                fingerprints.update(
                    representation[index : index + _SECRET_FRAGMENT_BYTES]
                    for index in range(
                        len(representation) - _SECRET_FRAGMENT_BYTES + 1
                    )
                )
    if not fingerprints:
        raise ValueError("Codex auth JSON does not contain bounded credentials")
    return frozenset(fingerprints)


def assert_no_secret_fingerprints(
    payloads: Iterable[bytes],
    fingerprints: frozenset[bytes],
) -> None:
    for payload in payloads:
        if not isinstance(payload, bytes):
            raise TypeError("fingerprint scan payloads must be bytes")
        if any(fingerprint in payload for fingerprint in fingerprints):
            raise DockerWorkspaceSecretLeak(
                "editor output contained a secret fingerprint"
            )


def _json_string_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _json_string_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_string_values(child)


def _require_fresh_auth_for_deadline(
    auth_payload: bytes,
    *,
    timeout_seconds: float,
    current_time: float | None = None,
    safety_margin_seconds: float = _AUTH_EXPIRY_SAFETY_SECONDS,
) -> None:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or isinstance(safety_margin_seconds, bool)
        or not isinstance(safety_margin_seconds, (int, float))
        or not math.isfinite(safety_margin_seconds)
        or safety_margin_seconds < 0
    ):
        raise ValueError("auth deadline values are invalid")
    now = time.time() if current_time is None else current_time
    if (
        isinstance(now, bool)
        or not isinstance(now, (int, float))
        or not math.isfinite(now)
        or now < 0
    ):
        raise ValueError("current_time is invalid")
    try:
        auth = json.loads(
            auth_payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        tokens = auth["tokens"]
        access_token = tokens["access_token"]
        account_id = tokens["account_id"]
        if (
            auth.get("auth_mode") != "chatgpt"
            or not isinstance(access_token, str)
            or not isinstance(account_id, str)
            or not account_id
            or len(access_token.encode("utf-8")) > 64 * 1024
        ):
            raise ValueError("invalid auth shape")
        segments = access_token.split(".")
        if len(segments) != 3 or any(not segment for segment in segments):
            raise ValueError("access token is not a JWT")
        encoded_claims = segments[1].encode("ascii")
        claims_payload = base64.b64decode(
            encoded_claims + b"=" * (-len(encoded_claims) % 4),
            altchars=b"-_",
            validate=True,
        )
        claims = json.loads(
            claims_payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        nested = claims["https://api.openai.com/auth"]
        token_account = nested["chatgpt_account_id"]
        expires_at = claims["exp"]
        if (
            not isinstance(claims, dict)
            or not isinstance(nested, dict)
            or not isinstance(token_account, str)
            or token_account != account_id
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or not math.isfinite(expires_at)
        ):
            raise ValueError("invalid JWT claims")
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise DockerWorkspaceUnavailable(
            "Codex auth metadata is invalid or incomplete"
        ) from error
    if expires_at <= float(now) + float(timeout_seconds) + float(safety_margin_seconds):
        raise DockerWorkspaceUnavailable(
            "Codex auth does not satisfy the required expiry margin"
        )


class DockerWorkspaceEditor:
    """Local trusted-input benchmark runner; production use is disabled."""

    production_enabled = False

    def __init__(
        self,
        *,
        image_id: str,
        auth_file: Path,
        private_temp_base: Path,
        docker_executable: str = "docker",
        command_runner: _CommandRunner | None = None,
        container_name_factory: _ContainerNameFactory | None = None,
        mode: str = "local_benchmark",
    ) -> None:
        if mode != "local_benchmark":
            raise ValueError("Docker workspace editing is production-disabled")
        if not _IMAGE_ID_RE.fullmatch(image_id):
            raise ValueError("image_id must be an immutable sha256 image ID")
        self.image_id = image_id
        self.auth_file = _trusted_bind_source(auth_file, kind="file")
        self.private_temp_base = _trusted_bind_source(
            private_temp_base,
            kind="directory",
        )
        if "," in str(self.private_temp_base):
            raise ValueError("private temp base must not contain commas")
        self.docker_executable = str(_resolve_trusted_executable(docker_executable))
        self._runner = command_runner or AsyncDockerCommandRunner()
        self._container_name_factory = (
            container_name_factory or _random_container_name
        )

    async def run(
        self,
        *,
        workspace: RefinementWorkspace,
        timeout_seconds: float,
    ) -> DockerWorkspaceRunResult:
        if not isinstance(workspace, RefinementWorkspace):
            raise TypeError("workspace must be a RefinementWorkspace")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        root = _validate_source_workspace(workspace)
        auth_payload = _read_regular_bytes(
            self.auth_file,
            limit=_MAX_AUTH_BYTES,
            label="Codex auth source",
        )
        _require_fresh_auth_for_deadline(
            auth_payload,
            timeout_seconds=float(timeout_seconds),
        )
        fingerprints = secret_fingerprints(auth_payload)
        container_name = self._container_name_factory()
        _validate_container_name(container_name)
        run_root: Path | None = None
        delivery_staging: Path | None = None
        delivery_final: Path | None = None
        create_attempted = False
        primary_error: BaseException | None = None
        container_result: _ParsedContainerResult | None = None
        proof_sha256: str | None = None
        try:
            run_root = _create_private_run_root(self.private_temp_base)
            auth_copy = run_root / "auth.json"
            output_root = run_root / "output"
            output_root.mkdir(mode=0o700)
            _write_private_file(auth_copy, auth_payload)
            _restrict_private_acl(run_root)
            _restrict_private_acl(auth_copy)
            async with asyncio.timeout(timeout_seconds):
                await self._require_exact_image()
                create_attempted = True
                await self._require_success(
                    build_container_create_command(
                        docker_executable=self.docker_executable,
                        container_name=container_name,
                        image_id=self.image_id,
                        workspace=root,
                        auth_copy=auth_copy,
                    ),
                    failure_message="Docker container could not be created",
                )
                await self._require_success(
                    (self.docker_executable, "start", container_name),
                    failure_message="Docker container could not be started",
                )
                execution = await self._run_command(
                    _container_exec_command(
                        docker_executable=self.docker_executable,
                        container_name=container_name,
                        timeout_seconds=timeout_seconds,
                    )
                )
                assert_no_secret_fingerprints(
                    (execution.stdout, execution.stderr),
                    fingerprints,
                )
                if execution.returncode != 0:
                    raise DockerWorkspaceUnavailable(
                        "Docker workspace editor exited unsuccessfully"
                    )
                container_result = _parse_container_result(execution.stdout)
                await self._require_success(
                    (
                        self.docker_executable,
                        "cp",
                        f"{container_name}:/workspace/editable/.",
                        str(output_root),
                    ),
                    failure_message="Docker editable output could not be copied",
                )
                payloads = _validate_copied_editable(
                    output_root,
                    records=container_result.records,
                    fingerprints=fingerprints,
                )
                delivery_staging, delivery_final, proof_sha256 = (
                    _build_delivery_staging(
                        workspace=workspace,
                        payloads=payloads,
                        container_result=container_result,
                        raw_container_result=execution.stdout,
                        image_id=self.image_id,
                        private_temp_base=self.private_temp_base,
                    )
                )
                _validate_candidate_delivery(
                    workspace=workspace,
                    payloads=payloads,
                    delivery_staging=delivery_staging,
                )
        except TimeoutError as error:
            primary_error = DockerWorkspaceTimeout(
                "Docker workspace editor timed out"
            )
            primary_error.__cause__ = error
        except BaseException as error:
            primary_error = error

        cleanup_errors: list[BaseException] = []
        if create_attempted:
            try:
                await _await_cleanup_shielded(
                    self._cleanup_container(container_name)
                )
            except BaseException as error:
                cleanup_errors.append(error)
        if run_root is not None:
            try:
                _remove_private_run_root(run_root)
            except BaseException as error:
                cleanup_errors.append(error)

        if primary_error is not None or cleanup_errors:
            if delivery_staging is not None:
                try:
                    _remove_private_run_root(delivery_staging)
                except BaseException as error:
                    cleanup_errors.append(error)
            _raise_primary_or_cleanup(primary_error, cleanup_errors)
        if (
            container_result is None
            or delivery_staging is None
            or delivery_final is None
            or proof_sha256 is None
        ):
            raise DockerWorkspaceInvalidOutput(
                "Docker editor did not produce a verified delivery"
            )
        _publish_delivery(delivery_staging, delivery_final)
        try:
            result = load_verified_docker_delivery(
                delivery_final,
                expected_proof_sha256=proof_sha256,
            )
        except BaseException:
            _remove_private_run_root(delivery_final)
            raise
        if result.image_id != self.image_id:
            _remove_private_run_root(delivery_final)
            raise DockerWorkspaceInvalidOutput("Delivery image proof is invalid")
        return result

    async def _cleanup_container(self, container_name: str) -> DockerCommandResult:
        removed = await self._runner.run(
            (
                self.docker_executable,
                "rm",
                "-f",
                container_name,
            ),
            timeout_seconds=_CLEANUP_TIMEOUT_SECONDS,
            max_stdout_bytes=16 * 1024,
            max_stderr_bytes=16 * 1024,
        )
        if removed.returncode == 0:
            return removed
        inspected = await self._runner.run(
            (
                self.docker_executable,
                "inspect",
                "--type",
                "container",
                "--format={{.Id}}",
                container_name,
            ),
            timeout_seconds=_CLEANUP_TIMEOUT_SECONDS,
            max_stdout_bytes=16 * 1024,
            max_stderr_bytes=16 * 1024,
        )
        if _inspect_proves_exact_absence(inspected, container_name):
            return inspected
        raise DockerWorkspaceUnavailable(
            "Disposable Docker container cleanup failed"
        )

    async def _require_exact_image(self) -> None:
        result = await self._run_command(
            (
                self.docker_executable,
                "image",
                "inspect",
                "--format={{.Id}}",
                self.image_id,
            )
        )
        if result.returncode != 0 or result.stdout.strip() != self.image_id.encode():
            raise DockerWorkspaceUnavailable(
                "Pinned Docker image ID is unavailable or mismatched"
            )

    async def _require_success(
        self,
        command: tuple[str, ...],
        *,
        failure_message: str,
    ) -> DockerCommandResult:
        result = await self._run_command(command)
        if result.returncode != 0:
            raise DockerWorkspaceUnavailable(failure_message)
        return result

    async def _run_command(self, command: tuple[str, ...]) -> DockerCommandResult:
        try:
            return await self._runner.run(
                command,
                timeout_seconds=3600,
                max_stdout_bytes=_MAX_DOCKER_STDOUT,
                max_stderr_bytes=_MAX_DOCKER_STDERR,
            )
        except TimeoutError as error:
            raise DockerWorkspaceTimeout(
                "Docker command timed out"
            ) from error
        except OSError as error:
            raise DockerWorkspaceUnavailable(
                "Docker command could not be started"
            ) from error


@dataclass(frozen=True, slots=True)
class _ParsedContainerResult:
    receipt: WorkspaceEditorReceipt
    invocation: dict[str, Any]
    records: tuple[tuple[str, int, str], ...]


def _parse_container_result(payload: bytes) -> _ParsedContainerResult:
    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise DockerWorkspaceInvalidOutput(
            "Container result is not valid UTF-8 JSON"
        ) from error
    if not isinstance(parsed, dict) or set(parsed) != _CONTAINER_RESULT_KEYS:
        raise DockerWorkspaceInvalidOutput(
            "Container result has unexpected keys"
        )
    if parsed.get("schema_version") != "kaigo.codex-container-result.v1":
        raise DockerWorkspaceInvalidOutput("Container result schema is invalid")
    editor = parsed.get("editor")
    if not isinstance(editor, dict) or set(editor) != _EDITOR_RESULT_KEYS:
        raise DockerWorkspaceInvalidOutput("Container editor receipt is invalid")
    if editor.get("model") != _MODEL:
        raise DockerWorkspaceInvalidOutput("Container editor model is invalid")
    counts = (
        editor.get("duration_ms"),
        editor.get("input_tokens"),
        editor.get("cached_input_tokens"),
        editor.get("output_tokens"),
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
        raise DockerWorkspaceInvalidOutput("Container usage receipt is invalid")
    thread_id = editor.get("thread_id")
    final_text = editor.get("final_text")
    if (
        not isinstance(thread_id, str)
        or not thread_id.strip()
        or not isinstance(final_text, str)
        or not final_text.strip()
        or len(final_text.encode("utf-8")) > 16 * 1024
    ):
        raise DockerWorkspaceInvalidOutput("Container completion receipt is invalid")
    invocation = _validate_invocation(parsed.get("invocation"))
    raw_records = parsed.get("editable_files")
    if not isinstance(raw_records, list) or len(raw_records) != len(_EDITABLE_NAMES):
        raise DockerWorkspaceInvalidOutput("Container editable manifest is invalid")
    records: list[tuple[str, int, str]] = []
    for index, item in enumerate(raw_records):
        expected_path = f"editable/{_EDITABLE_NAMES[index]}"
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "size", "sha256"}
            or item.get("path") != expected_path
            or isinstance(item.get("size"), bool)
            or not isinstance(item.get("size"), int)
            or item["size"] < 0
            or item["size"] > _EDITABLE_LIMITS[_EDITABLE_NAMES[index]]
            or not isinstance(item.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise DockerWorkspaceInvalidOutput(
                "Container editable manifest is invalid"
            )
        records.append((expected_path, item["size"], item["sha256"]))
    return _ParsedContainerResult(
        receipt=WorkspaceEditorReceipt(
            model=editor["model"],
            duration_ms=editor["duration_ms"],
            input_tokens=editor["input_tokens"],
            cached_input_tokens=editor["cached_input_tokens"],
            output_tokens=editor["output_tokens"],
            thread_id=thread_id,
            final_text=final_text,
        ),
        invocation=invocation,
        records=tuple(records),
    )


def _validate_invocation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _INVOCATION_RESULT_KEYS:
        raise DockerWorkspaceInvalidOutput("Container invocation proof is invalid")
    expected = {
        "model": _MODEL,
        "requested_reasoning_effort": _REASONING_EFFORT,
        "effective_reasoning_effort": _REASONING_EFFORT,
        "permission_profile": _PERMISSION_PROFILE,
        "mcp_tools": list(_MCP_TOOL_NAMES),
        "shell_tool": False,
        "unified_exec": False,
        "screenshots_attached": 6,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise DockerWorkspaceInvalidOutput("Container invocation proof is invalid")
    command_digest = value.get("command_sha256")
    if not isinstance(command_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", command_digest
    ):
        raise DockerWorkspaceInvalidOutput("Container invocation proof is invalid")
    return {
        **expected,
        "command_sha256": command_digest,
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DockerWorkspaceInvalidOutput(
                "Container result contains a duplicate JSON key"
            )
        result[key] = value
    return result


def _validate_source_workspace(workspace: RefinementWorkspace) -> Path:
    root = _trusted_bind_source(workspace.root, kind="directory")
    if root != workspace.receipt.root or workspace.editable != root / "editable":
        raise ValueError("workspace receipt does not match the mounted root")
    manifest = workspace_contract._verify_receipt(root, workspace.receipt)
    workspace_contract._scan_workspace(root)
    disk_manifest = workspace_contract._read_regular_bytes(
        root / "manifest.json",
        relative="manifest.json",
        limit=64 * 1024,
    )
    if disk_manifest != workspace.receipt.manifest_bytes:
        raise ValueError("workspace manifest changed before container launch")
    workspace_contract._verify_immutable_hashes(
        root,
        manifest,
        workspace.receipt,
    )
    return root


def _validate_copied_editable(
    root: Path,
    *,
    records: tuple[tuple[str, int, str], ...],
    fingerprints: frozenset[bytes],
) -> dict[str, bytes]:
    if not root.is_dir() or root.is_symlink():
        raise DockerWorkspaceInvalidOutput("Copied editable root is invalid")
    names = tuple(sorted(path.name for path in root.iterdir()))
    if names != tuple(sorted(_EDITABLE_NAMES)):
        raise DockerWorkspaceInvalidOutput(
            "Copied editable paths differ from the exact allowlist"
        )
    record_map = {Path(path).name: (size, digest) for path, size, digest in records}
    payloads: dict[str, bytes] = {}
    for name in _EDITABLE_NAMES:
        try:
            payload = _read_regular_bytes(
                root / name,
                limit=_EDITABLE_LIMITS[name],
                label=f"copied editable/{name}",
            )
        except ValueError as error:
            raise DockerWorkspaceInvalidOutput(
                "Copied editable file is unsafe or oversized"
            ) from error
        size, digest = record_map[name]
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
            raise DockerWorkspaceInvalidOutput(
                "Copied editable file differs from the container manifest"
            )
        payloads[name] = payload
    assert_no_secret_fingerprints(payloads.values(), fingerprints)
    return payloads


def _build_delivery_staging(
    *,
    workspace: RefinementWorkspace,
    payloads: dict[str, bytes],
    container_result: _ParsedContainerResult,
    raw_container_result: bytes,
    image_id: str,
    private_temp_base: Path,
) -> tuple[Path, Path, str]:
    if tuple(payloads) != _EDITABLE_NAMES:
        raise DockerWorkspaceInvalidOutput("Editable delivery set differs from allowlist")
    identifier = uuid4().hex
    staging = private_temp_base / f".kaigo-codex-delivery-{identifier}.staging"
    final = private_temp_base / f"kaigo-codex-delivery-{identifier}"
    try:
        staging.mkdir(mode=0o700)
        _restrict_private_acl(staging)
        candidate = staging / "workspace"
        shutil.copytree(
            workspace.root,
            candidate,
            symlinks=False,
            copy_function=shutil.copyfile,
        )
        _make_tree_private(candidate)
        for name, payload in payloads.items():
            target = candidate / "editable" / name
            target.unlink()
            _write_private_file(target, payload)
        candidate_receipt = replace(
            workspace.receipt,
            root=candidate.resolve(strict=True),
        )
        workspace_contract.import_refinement_workspace(
            candidate,
            receipt=candidate_receipt,
        )
        records = _candidate_records(candidate / "editable")
        if records != _records_to_dicts(container_result.records):
            raise DockerWorkspaceInvalidOutput(
                "Candidate files differ from the container manifest"
            )
        aggregate = _candidate_aggregate(records)
        editor = _editor_to_dict(container_result.receipt)
        proof = {
            "schema_version": _DELIVERY_SCHEMA,
            "image_id": image_id,
            "source_manifest_sha256": workspace.receipt.manifest_sha256,
            "container_result_sha256": hashlib.sha256(
                raw_container_result
            ).hexdigest(),
            "editor": editor,
            "invocation": container_result.invocation,
            "workspace_receipt": {
                "manifest_bytes_b64": base64.b64encode(
                    workspace.receipt.manifest_bytes
                ).decode("ascii"),
                "manifest_sha256": workspace.receipt.manifest_sha256,
                "immutable_hashes": [
                    list(item) for item in workspace.receipt.immutable_hashes
                ],
            },
            "candidate_files": records,
            "candidate_aggregate_sha256": aggregate,
        }
        proof_bytes = _canonical_json_bytes(proof) + b"\n"
        if len(proof_bytes) > _PROOF_LIMIT:
            raise DockerWorkspaceInvalidOutput("Invocation proof exceeds its limit")
        _write_private_file(staging / "invocation.json", proof_bytes)
        return staging, final, hashlib.sha256(proof_bytes).hexdigest()
    except BaseException as primary:
        if os.path.lexists(staging):
            try:
                _remove_private_run_root(staging)
            except BaseException as cleanup:
                primary.add_note("Delivery staging cleanup also failed.")
                raise primary from BaseExceptionGroup(
                    "delivery staging cleanup failures",
                    [cleanup],
                )
        raise


def _validate_candidate_delivery(
    *,
    workspace: RefinementWorkspace,
    payloads: dict[str, bytes],
    delivery_staging: Path,
) -> None:
    candidate = delivery_staging / "workspace"
    receipt = replace(
        workspace.receipt,
        root=candidate.resolve(strict=True),
    )
    for name, expected in payloads.items():
        actual = _read_regular_bytes(
            candidate / "editable" / name,
            limit=_EDITABLE_LIMITS[name],
            label=f"candidate editable/{name}",
        )
        if actual != expected:
            raise DockerWorkspaceInvalidOutput("Candidate editable digest mismatch")
    try:
        workspace_contract.import_refinement_workspace(candidate, receipt=receipt)
    except Exception as error:
        raise DockerWorkspaceInvalidOutput(
            "Candidate failed the trusted workspace importer"
        ) from error


def _candidate_records(editable: Path) -> list[dict[str, Any]]:
    actual_names = tuple(sorted(path.name for path in editable.iterdir()))
    if actual_names != tuple(sorted(_EDITABLE_NAMES)):
        raise DockerWorkspaceInvalidOutput("Candidate editable paths differ from allowlist")
    records: list[dict[str, Any]] = []
    for name in _EDITABLE_NAMES:
        payload = _read_regular_bytes(
            editable / name,
            limit=_EDITABLE_LIMITS[name],
            label=f"candidate editable/{name}",
        )
        records.append(
            {
                "path": f"editable/{name}",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return records


def _records_to_dicts(
    records: tuple[tuple[str, int, str], ...],
) -> list[dict[str, Any]]:
    return [
        {"path": path, "size": size, "sha256": digest}
        for path, size, digest in records
    ]


def _candidate_aggregate(records: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical_json_bytes(records)).hexdigest()


def _editor_to_dict(receipt: WorkspaceEditorReceipt) -> dict[str, Any]:
    return {
        "model": receipt.model,
        "duration_ms": receipt.duration_ms,
        "input_tokens": receipt.input_tokens,
        "cached_input_tokens": receipt.cached_input_tokens,
        "output_tokens": receipt.output_tokens,
        "thread_id": receipt.thread_id,
        "final_text": receipt.final_text,
    }


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _make_tree_private(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        path.chmod(0o700 if path.is_dir() else 0o600)


def _publish_delivery(staging: Path, final: Path) -> None:
    if staging.parent != final.parent or final.exists() or os.path.lexists(final):
        raise DockerWorkspaceInvalidOutput("Delivery publication target is invalid")
    try:
        os.replace(staging, final)
        if os.path.lexists(staging):
            raise DockerWorkspaceInvalidOutput("Delivery staging remained after publication")
        _trusted_bind_source(final, kind="directory")
    except BaseException:
        for candidate in (staging, final):
            if os.path.lexists(candidate):
                _remove_private_run_root(candidate)
        raise


def _raise_primary_or_cleanup(
    primary: BaseException | None,
    cleanup_errors: list[BaseException],
) -> None:
    if primary is not None:
        if cleanup_errors:
            primary.add_note(
                f"Disposable cleanup produced {len(cleanup_errors)} retained error(s)."
            )
            raise primary from BaseExceptionGroup(
                "disposable editor cleanup failures",
                cleanup_errors,
            )
        raise primary
    if cleanup_errors:
        error = DockerWorkspaceUnavailable("Disposable editor cleanup failed")
        raise error from BaseExceptionGroup(
            "disposable editor cleanup failures",
            cleanup_errors,
        )
    raise AssertionError("cleanup error dispatcher called without an error")


def load_verified_docker_delivery(
    delivery_root: Path,
    *,
    expected_proof_sha256: str,
) -> DockerWorkspaceRunResult:
    if not isinstance(expected_proof_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_proof_sha256
    ):
        raise ValueError("expected proof digest is invalid")
    try:
        root = _trusted_bind_source(delivery_root, kind="directory")
        names = tuple(sorted(path.name for path in root.iterdir()))
        if names != ("invocation.json", "workspace"):
            raise DockerWorkspaceInvalidOutput("Delivery paths differ from allowlist")
        proof_path = root / "invocation.json"
        proof_bytes = _read_regular_bytes(
            proof_path,
            limit=_PROOF_LIMIT,
            label="delivery invocation proof",
        )
        proof_sha256 = hashlib.sha256(proof_bytes).hexdigest()
        if proof_sha256 != expected_proof_sha256:
            raise DockerWorkspaceInvalidOutput("Invocation proof digest mismatch")
        proof = json.loads(
            proof_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        if (
            not isinstance(proof, dict)
            or set(proof) != _PROOF_KEYS
            or proof.get("schema_version") != _DELIVERY_SCHEMA
            or _canonical_json_bytes(proof) + b"\n" != proof_bytes
        ):
            raise DockerWorkspaceInvalidOutput("Invocation proof is invalid")
        image_id = proof.get("image_id")
        if not isinstance(image_id, str) or not _IMAGE_ID_RE.fullmatch(image_id):
            raise DockerWorkspaceInvalidOutput("Invocation image proof is invalid")
        for key in ("source_manifest_sha256", "container_result_sha256"):
            if not isinstance(proof.get(key), str) or not re.fullmatch(
                r"[0-9a-f]{64}", proof[key]
            ):
                raise DockerWorkspaceInvalidOutput("Invocation digest proof is invalid")
        editor = _parse_editor_proof(proof.get("editor"))
        invocation = _validate_invocation(proof.get("invocation"))
        receipt = _parse_workspace_receipt(
            root / "workspace",
            proof.get("workspace_receipt"),
        )
        if (
            receipt.manifest_sha256 != proof["source_manifest_sha256"]
            or receipt.manifest_sha256
            != hashlib.sha256(receipt.manifest_bytes).hexdigest()
        ):
            raise DockerWorkspaceInvalidOutput("Workspace receipt digest mismatch")
        records = _parse_candidate_file_proof(proof.get("candidate_files"))
        actual_records = _candidate_records(root / "workspace" / "editable")
        if actual_records != records:
            raise DockerWorkspaceInvalidOutput("Candidate file digest mismatch")
        aggregate = _candidate_aggregate(records)
        if aggregate != proof.get("candidate_aggregate_sha256"):
            raise DockerWorkspaceInvalidOutput("Candidate aggregate digest mismatch")
        workspace = RefinementWorkspace(
            root=(root / "workspace").resolve(strict=True),
            editable=(root / "workspace" / "editable").resolve(strict=True),
            manifest_path=(root / "workspace" / "manifest.json").resolve(strict=True),
            receipt=receipt,
        )
        workspace_contract.import_refinement_workspace(
            workspace.root,
            receipt=workspace.receipt,
        )
        return DockerWorkspaceRunResult(
            image_id=image_id,
            model=editor.model,
            requested_reasoning_effort=invocation["requested_reasoning_effort"],
            effective_reasoning_effort=invocation["effective_reasoning_effort"],
            duration_ms=editor.duration_ms,
            input_tokens=editor.input_tokens,
            cached_input_tokens=editor.cached_input_tokens,
            output_tokens=editor.output_tokens,
            thread_id=editor.thread_id,
            final_text=editor.final_text,
            workspace=workspace,
            delivery_root=root,
            proof_path=proof_path,
            proof_sha256=proof_sha256,
            candidate_aggregate_sha256=aggregate,
        )
    except DockerWorkspaceInvalidOutput:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise DockerWorkspaceInvalidOutput("Docker delivery proof is invalid") from error


def _parse_editor_proof(value: Any) -> WorkspaceEditorReceipt:
    if not isinstance(value, dict) or set(value) != _EDITOR_RESULT_KEYS:
        raise DockerWorkspaceInvalidOutput("Delivery editor proof is invalid")
    payload = {
        "schema_version": "kaigo.codex-container-result.v1",
        "editor": value,
        "invocation": {
            "model": _MODEL,
            "requested_reasoning_effort": _REASONING_EFFORT,
            "effective_reasoning_effort": _REASONING_EFFORT,
            "permission_profile": _PERMISSION_PROFILE,
            "mcp_tools": list(_MCP_TOOL_NAMES),
            "shell_tool": False,
            "unified_exec": False,
            "screenshots_attached": 6,
            "command_sha256": "0" * 64,
        },
        "editable_files": [
            {"path": f"editable/{name}", "size": 0, "sha256": "0" * 64}
            for name in _EDITABLE_NAMES
        ],
    }
    parsed = _parse_container_result(_canonical_json_bytes(payload))
    return parsed.receipt


def _parse_workspace_receipt(root: Path, value: Any) -> Any:
    if not isinstance(value, dict) or set(value) != _WORKSPACE_RECEIPT_KEYS:
        raise DockerWorkspaceInvalidOutput("Workspace receipt proof is invalid")
    encoded = value.get("manifest_bytes_b64")
    digest = value.get("manifest_sha256")
    raw_hashes = value.get("immutable_hashes")
    if (
        not isinstance(encoded, str)
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not isinstance(raw_hashes, list)
    ):
        raise DockerWorkspaceInvalidOutput("Workspace receipt proof is invalid")
    try:
        manifest_bytes = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeError, ValueError) as error:
        raise DockerWorkspaceInvalidOutput("Workspace receipt proof is invalid") from error
    immutable_hashes: list[tuple[str, str]] = []
    for item in raw_hashes:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
            or not re.fullmatch(r"[0-9a-f]{64}", item[1])
        ):
            raise DockerWorkspaceInvalidOutput("Workspace receipt proof is invalid")
        immutable_hashes.append((item[0], item[1]))
    if immutable_hashes != sorted(set(immutable_hashes)):
        raise DockerWorkspaceInvalidOutput("Workspace receipt proof is invalid")
    return workspace_contract.FrozenWorkspaceReceipt(
        root=root.resolve(strict=True),
        manifest_bytes=manifest_bytes,
        manifest_sha256=digest,
        immutable_hashes=tuple(immutable_hashes),
    )


def _parse_candidate_file_proof(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(_EDITABLE_NAMES):
        raise DockerWorkspaceInvalidOutput("Candidate file proof is invalid")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        name = _EDITABLE_NAMES[index]
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "size", "sha256"}
            or item.get("path") != f"editable/{name}"
            or isinstance(item.get("size"), bool)
            or not isinstance(item.get("size"), int)
            or not 0 <= item["size"] <= _EDITABLE_LIMITS[name]
            or not isinstance(item.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise DockerWorkspaceInvalidOutput("Candidate file proof is invalid")
        records.append(dict(item))
    return records


def _container_exec_command(
    *,
    docker_executable: str,
    container_name: str,
    timeout_seconds: float,
) -> tuple[str, ...]:
    return (
        docker_executable,
        "exec",
        "-i",
        "--user",
        "10001:10001",
        "-e",
        "CODEX_HOME=/codex-home",
        "-e",
        "HOME=/codex-home",
        "-e",
        "TMPDIR=/tmp",
        "-e",
        "TEMP=/tmp",
        "-e",
        "TMP=/tmp",
        container_name,
        "python3",
        "/opt/kaigo/tools/kaigo_codex_bridge/container_workspace_entrypoint.py",
        "--timeout-seconds",
        str(float(timeout_seconds)),
    )


def _validate_container_name(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"kaigo-refine-[a-z0-9][a-z0-9-]{0,47}",
        value,
    ):
        raise ValueError("container name factory returned an invalid name")


def _random_container_name() -> str:
    return f"kaigo-refine-{uuid4().hex[:24]}"


def _create_private_run_root(base: Path) -> Path:
    path = Path(tempfile.mkdtemp(prefix="kaigo-codex-docker-", dir=base))
    try:
        if _path_component_is_link_like(path):
            raise ValueError("private run directory is a reparse point")
        path.chmod(0o700)
        _restrict_private_acl(path)
        return path
    except BaseException as error:
        try:
            _remove_private_run_root(path)
        except BaseException as cleanup_error:
            raise DockerWorkspaceUnavailable(
                "Private auth staging cleanup could not be verified"
            ) from cleanup_error
        raise error


def _remove_private_run_root(path: Path) -> None:
    last_error: OSError | None = None
    for _attempt in range(3):
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return
        except OSError as error:
            last_error = error
        if not os.path.lexists(path):
            return
    raise DockerWorkspaceUnavailable(
        "Private auth staging cleanup could not be verified"
    ) from last_error


def _inspect_proves_exact_absence(
    result: DockerCommandResult,
    container_name: str,
) -> bool:
    if result.returncode == 0 or result.stdout.strip():
        return False
    try:
        message = result.stderr.decode("utf-8", errors="strict").strip()
    except UnicodeError:
        return False
    return message in {
        f"Error: No such object: {container_name}",
        f"Error response from daemon: No such container: {container_name}",
    }


def _restrict_private_acl(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    sid = _current_windows_sid()
    permission = f"*{sid}:(OI)(CI)F" if path.is_dir() else f"*{sid}:F"
    completed = subprocess.run(
        (
            str(_trusted_windows_system_executable("icacls.exe")),
            str(path),
            "/inheritance:r",
            "/grant:r",
            permission,
            "/q",
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise DockerWorkspaceUnavailable(
            "Could not restrict the private Windows ACL"
        )


def _current_windows_sid() -> str:
    completed = subprocess.run(
        (
            str(_trusted_windows_system_executable("whoami.exe")),
            "/user",
            "/fo",
            "csv",
            "/nh",
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        shell=False,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    if completed.returncode != 0:
        raise DockerWorkspaceUnavailable("Could not determine the current SID")
    try:
        row = next(csv.reader(io.StringIO(completed.stdout)))
    except (StopIteration, csv.Error) as error:
        raise DockerWorkspaceUnavailable(
            "Could not parse the current SID"
        ) from error
    if len(row) != 2 or not re.fullmatch(r"S-\d(?:-\d+)+", row[1]):
        raise DockerWorkspaceUnavailable("Current SID output was invalid")
    return row[1]


def _trusted_windows_system_executable(name: str) -> Path:
    if name not in {"icacls.exe", "whoami.exe"}:
        raise DockerWorkspaceUnavailable("Unapproved Windows support tool")
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise DockerWorkspaceUnavailable("trusted Windows tool root is unavailable")
    try:
        return _trusted_absolute_executable(
            str(Path(system_root) / "System32" / name)
        )
    except (TypeError, ValueError) as error:
        raise DockerWorkspaceUnavailable(
            "trusted Windows tool path is invalid"
        ) from error


def _read_regular_bytes(path: Path, *, limit: int, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise ValueError(f"{label} is unavailable") from error
    if (
        not stat.S_ISREG(before.st_mode)
        or _path_component_is_link_like(path)
        or before.st_nlink != 1
        or before.st_size > limit
    ):
        raise ValueError(f"{label} must be a bounded regular file")
    try:
        payload = path.read_bytes()
        after = path.lstat()
    except OSError as error:
        raise ValueError(f"{label} could not be read") from error
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(payload) != after.st_size:
        raise ValueError(f"{label} changed while being read")
    return payload


def _write_private_file(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    path.chmod(0o600)


async def _await_cleanup_shielded(cleanup: Awaitable[DockerCommandResult]) -> None:
    task = asyncio.create_task(
        asyncio.wait_for(cleanup, timeout=_CLEANUP_TIMEOUT_SECONDS)
    )
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    task.result()
    if interrupted:
        raise asyncio.CancelledError


class AsyncDockerCommandRunner:
    """Bounded no-shell Docker CLI transport used by the host runner."""

    async def run(
        self,
        command: tuple[str, ...],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        max_stderr_bytes: int,
    ) -> DockerCommandResult:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_docker_child_environment(),
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            raise DockerWorkspaceUnavailable("Docker CLI pipes were not created")
        tasks = (
            asyncio.create_task(
                _read_stream_bounded(process.stdout, max_stdout_bytes)
            ),
            asyncio.create_task(
                _read_stream_bounded(process.stderr, max_stderr_bytes)
            ),
            asyncio.create_task(process.wait()),
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                await asyncio.gather(*tasks)
            return DockerCommandResult(
                returncode=tasks[2].result(),
                stdout=tasks[0].result(),
                stderr=tasks[1].result(),
            )
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            process.kill()
            await asyncio.gather(*tasks, return_exceptions=True)
            await process.wait()
            raise


async def _read_stream_bounded(
    stream: asyncio.StreamReader,
    limit: int,
) -> bytes:
    payload = bytearray()
    while True:
        chunk = await stream.read(min(64 * 1024, limit - len(payload) + 1))
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)
        if len(payload) > limit:
            raise DockerWorkspaceInvalidOutput("Docker CLI output exceeded its limit")


def _docker_child_environment() -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH") or os.defpath,
        "PYTHONUTF8": "1",
    }
    for name in (
        "APPDATA",
        "COMSPEC",
        "DOCKER_CERT_PATH",
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "HOME",
        "LOCALAPPDATA",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment
