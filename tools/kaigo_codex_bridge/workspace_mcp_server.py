from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, BinaryIO, Callable
from uuid import uuid4

from builder_lab.refinement_workspace import (
    FrozenWorkspaceReceipt,
    RefinementWorkspaceError,
    import_refinement_workspace,
)


MCP_TOOL_NAMES = (
    "list_workspace_inputs",
    "read_workspace_file",
    "write_editable_file",
    "validate_candidate",
)

_READABLE_LIMITS = {
    "CONTRACT.md": 64 * 1024,
    "request.md": 64 * 1024,
    "source-artifact.json": 512 * 1024,
    "source-persona.json": 128 * 1024,
    "manifest.json": 64 * 1024,
    "editable/widget.html": 64 * 1024,
    "editable/widget.css": 96 * 1024,
    "editable/widget.js": 256 * 1024,
    "editable/persona.json": 64 * 1024,
    "editable/result.json": 16 * 1024,
}
_EDITABLE_LIMITS = {
    "editable/widget.html": 64 * 1024,
    "editable/widget.css": 96 * 1024,
    "editable/widget.js": 256 * 1024,
    "editable/persona.json": 64 * 1024,
    "editable/result.json": 16 * 1024,
}
_MAX_RPC_LINE_BYTES = 1024 * 1024
_MAX_RPC_OUTPUT_BYTES = 2 * 1024 * 1024
_PROTOCOL_VERSION = "2025-06-18"
_SERVER_NAME = "kaigo-workspace-editor"
_SERVER_VERSION = "1.0.0"
_VALIDATION_DETAILS = {
    "changed_files_mismatch": (
        "Make changed_files exactly match the editable HTML, CSS, or persona "
        "files whose canonical content changed."
    ),
    "duplicate_json_key": "Remove duplicate JSON keys from the editable JSON file.",
    "empty_public_summary": "Add a non-empty Russian public_summary to result.json.",
    "file_changed_during_read": "Stop changing files and validate the candidate again.",
    "file_too_large": "Reduce the edited file to its documented byte limit.",
    "immutable_hash_mismatch": "Restore every immutable workspace input.",
    "immutable_javascript": "Restore editable/widget.js to its original content.",
    "immutable_manifest_mismatch": "Restore manifest.json to its original content.",
    "immutable_persona_field": (
        "Keep persona schema_version, employee_type, and safeguards unchanged."
    ),
    "invalid_artifact": "Fix the edited widget so it passes the Kaigo artifact contract.",
    "invalid_immutable_source": "Restore every immutable workspace input.",
    "invalid_json": "Write valid UTF-8 JSON in every editable JSON file.",
    "invalid_persona": "Fix editable/persona.json to match the persona schema.",
    "invalid_public_summary": (
        "Use a bounded Russian public_summary in editable/result.json."
    ),
    "invalid_receipt": "Restore every immutable workspace input.",
    "invalid_result": "Use the exact refinement-result.v1 schema in result.json.",
    "invalid_utf8": "Write valid UTF-8 text in every editable text file.",
    "missing_path": "Restore every required workspace file.",
    "no_changes": "Change at least one allowed HTML, CSS, or persona value.",
    "reserved_runtime_attribute": (
        "Remove trusted runtime attributes from editable/widget.html."
    ),
    "result_not_complete": (
        "Complete editable/result.json with the exact result schema before "
        "validating again."
    ),
    "unexpected_owner": "Restore regular workspace files owned by the editor user.",
    "unexpected_path": "Remove paths outside the documented workspace contract.",
    "unsafe_file_type": "Restore each workspace path as a regular file or directory.",
    "unsafe_link_count": "Replace linked files with single-link regular files.",
}


class WorkspaceMcpError(ValueError):
    """A model request crossed the fixed workspace-tool boundary."""


@dataclass(frozen=True, slots=True)
class _Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": self.annotations,
        }


_EMPTY_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}
_TOOLS = (
    _Tool(
        name="list_workspace_inputs",
        description=(
            "List the exact text inputs available for this Kaigo refinement. "
            "Screenshots are already attached to the turn."
        ),
        input_schema=_EMPTY_OBJECT_SCHEMA,
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    _Tool(
        name="read_workspace_file",
        description="Read one exact allowlisted UTF-8 workspace text file.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "enum": list(_READABLE_LIMITS),
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    _Tool(
        name="write_editable_file",
        description=(
            "Atomically replace one exact editable UTF-8 file within its fixed "
            "byte limit."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "enum": list(_EDITABLE_LIMITS),
                },
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
    _Tool(
        name="validate_candidate",
        description=(
            "Run the fixed trusted Kaigo importer and validator against the "
            "current editable candidate."
        ),
        input_schema=_EMPTY_OBJECT_SCHEMA,
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
)


class WorkspaceMcpService:
    """Exact file-tool surface exposed to the model over local STDIO MCP."""

    def __init__(self, root: Path) -> None:
        self.root = _trusted_workspace_root(root)
        self.receipt = _receipt_from_manifest(self.root)
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "list_workspace_inputs": self.list_workspace_inputs,
            "read_workspace_file": self.read_workspace_file,
            "write_editable_file": self.write_editable_file,
            "validate_candidate": self.validate_candidate,
        }

    def tool_specs(self) -> tuple[dict[str, Any], ...]:
        return tuple(tool.to_dict() for tool in _TOOLS)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(name, str) or name not in self._handlers:
            raise WorkspaceMcpError("unknown tool")
        if not isinstance(arguments, dict):
            raise WorkspaceMcpError("tool arguments must be an object")
        return self._handlers[name](arguments)

    def list_workspace_inputs(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _require_exact_arguments(arguments, frozenset())
        files = []
        for relative, limit in _READABLE_LIMITS.items():
            payload = _read_allowlisted(self.root, relative, limit=limit)
            files.append(
                {
                    "path": relative,
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "editable": relative in _EDITABLE_LIMITS,
                }
            )
        return {"files": files}

    def read_workspace_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _require_exact_arguments(arguments, frozenset({"path"}))
        relative = arguments.get("path")
        if not isinstance(relative, str) or relative not in _READABLE_LIMITS:
            raise WorkspaceMcpError("path is not allowlisted")
        payload = _read_allowlisted(
            self.root,
            relative,
            limit=_READABLE_LIMITS[relative],
        )
        try:
            content = payload.decode("utf-8")
        except UnicodeError as error:
            raise WorkspaceMcpError("allowlisted file is not UTF-8") from error
        return {
            "path": relative,
            "content": content,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    def write_editable_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _require_exact_arguments(arguments, frozenset({"path", "content"}))
        relative = arguments.get("path")
        content = arguments.get("content")
        if not isinstance(relative, str) or relative not in _EDITABLE_LIMITS:
            raise WorkspaceMcpError("path is not allowlisted for editing")
        if not isinstance(content, str):
            raise WorkspaceMcpError("content must be a UTF-8 string")
        payload = content.encode("utf-8")
        if len(payload) > _EDITABLE_LIMITS[relative]:
            raise WorkspaceMcpError("editable content exceeds its size limit")
        _atomic_write_allowlisted(self.root, relative, payload)
        return {
            "path": relative,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    def validate_candidate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _require_exact_arguments(arguments, frozenset())
        try:
            imported = import_refinement_workspace(
                self.root,
                receipt=self.receipt,
            )
        except RefinementWorkspaceError as error:
            code = error.error_code
            return {
                "valid": False,
                "error": {
                    "code": code if code in _VALIDATION_DETAILS else "candidate_invalid",
                    "detail": _VALIDATION_DETAILS.get(
                        code,
                        "Restore the workspace contract and validate the candidate again.",
                    ),
                },
            }
        except Exception as error:
            raise WorkspaceMcpError("trusted candidate validation failed") from error
        return {
            "valid": True,
            "changed_paths": list(imported.changed_paths),
            "public_summary": imported.public_summary,
        }


def _trusted_workspace_root(root: Path) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        raise ValueError("workspace root must be an absolute Path")
    for component in _existing_components(root):
        if _is_link_like(component):
            raise ValueError("workspace root contains a link-like component")
    try:
        info = root.lstat()
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise ValueError("workspace root is unavailable") from error
    if not stat.S_ISDIR(info.st_mode) or resolved != root:
        raise ValueError("workspace root must be a regular directory")
    editable = root / "editable"
    try:
        editable_info = editable.lstat()
    except OSError as error:
        raise ValueError("workspace editable directory is unavailable") from error
    if not stat.S_ISDIR(editable_info.st_mode) or _is_link_like(editable):
        raise ValueError("workspace editable directory must be regular")
    return resolved


def _receipt_from_manifest(root: Path) -> FrozenWorkspaceReceipt:
    manifest_bytes = _read_allowlisted(root, "manifest.json", limit=64 * 1024)
    try:
        manifest = json.loads(
            manifest_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        immutable = manifest["immutable_hashes"]
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("workspace manifest is invalid") from error
    if not isinstance(immutable, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in immutable.items()
    ):
        raise ValueError("workspace immutable hash manifest is invalid")
    return FrozenWorkspaceReceipt(
        root=root,
        manifest_bytes=manifest_bytes,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        immutable_hashes=tuple(sorted(immutable.items())),
    )


def _require_exact_arguments(
    arguments: dict[str, Any],
    expected: frozenset[str],
) -> None:
    if set(arguments) != expected:
        raise WorkspaceMcpError("tool arguments differ from the exact schema")


def _read_allowlisted(root: Path, relative: str, *, limit: int) -> bytes:
    if relative not in _READABLE_LIMITS:
        raise WorkspaceMcpError("path is not allowlisted")
    if os.name == "posix":
        return _read_allowlisted_posix(root, relative, limit=limit)
    return _read_allowlisted_portable(root, relative, limit=limit)


def _read_allowlisted_posix(root: Path, relative: str, *, limit: int) -> bytes:
    root_fd = _open_directory_nofollow(root)
    directory_fd: int | None = None
    try:
        parts = relative.split("/")
        if len(parts) == 2:
            directory_fd = os.open(
                parts[0],
                _directory_open_flags(),
                dir_fd=root_fd,
            )
            parent_fd = directory_fd
            name = parts[1]
        else:
            parent_fd = root_fd
            name = parts[0]
        file_fd = os.open(name, _readonly_open_flags(), dir_fd=parent_fd)
        try:
            return _read_bounded_descriptor(file_fd, limit=limit)
        finally:
            os.close(file_fd)
    except OSError as error:
        raise WorkspaceMcpError("allowlisted path must be a bounded regular file") from error
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(root_fd)


def _read_allowlisted_portable(root: Path, relative: str, *, limit: int) -> bytes:
    path = root.joinpath(*relative.split("/"))
    if _is_link_like(path) or _is_link_like(path.parent):
        raise WorkspaceMcpError("allowlisted path must be a bounded regular file")
    try:
        before = path.lstat()
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            payload = stream.read(limit + 1)
        after = path.lstat()
    except OSError as error:
        raise WorkspaceMcpError("allowlisted path must be a bounded regular file") from error
    identities = (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
        (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns),
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
    )
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or len(set(identities)) != 1
        or len(payload) > limit
        or len(payload) != opened.st_size
    ):
        raise WorkspaceMcpError("allowlisted path must be a bounded regular file")
    return payload


def _atomic_write_allowlisted(root: Path, relative: str, payload: bytes) -> None:
    if relative not in _EDITABLE_LIMITS:
        raise WorkspaceMcpError("path is not allowlisted for editing")
    if os.name == "posix":
        _atomic_write_allowlisted_posix(root, relative, payload)
        return
    _atomic_write_allowlisted_portable(root, relative, payload)


def _atomic_write_allowlisted_posix(
    root: Path,
    relative: str,
    payload: bytes,
) -> None:
    root_fd = _open_directory_nofollow(root)
    editable_fd: int | None = None
    temporary: str | None = None
    try:
        editable_fd = os.open("editable", _directory_open_flags(), dir_fd=root_fd)
        name = relative.removeprefix("editable/")
        before = os.stat(name, dir_fd=editable_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise WorkspaceMcpError("editable target must be a single-link regular file")
        temporary = f".kaigo-mcp-{uuid4().hex}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=editable_fd,
        )
        try:
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(
            temporary,
            name,
            src_dir_fd=editable_fd,
            dst_dir_fd=editable_fd,
        )
        temporary = None
        after = os.stat(name, dir_fd=editable_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or after.st_size != len(payload)
        ):
            raise WorkspaceMcpError("editable replacement was not a regular file")
        os.fsync(editable_fd)
    except WorkspaceMcpError:
        raise
    except OSError as error:
        raise WorkspaceMcpError("editable target must be a regular file") from error
    finally:
        if temporary is not None and editable_fd is not None:
            try:
                os.unlink(temporary, dir_fd=editable_fd)
            except OSError:
                pass
        if editable_fd is not None:
            os.close(editable_fd)
        os.close(root_fd)


def _atomic_write_allowlisted_portable(
    root: Path,
    relative: str,
    payload: bytes,
) -> None:
    path = root.joinpath(*relative.split("/"))
    if _is_link_like(path) or _is_link_like(path.parent):
        raise WorkspaceMcpError("editable target must be a regular file")
    try:
        before = path.lstat()
    except OSError as error:
        raise WorkspaceMcpError("editable target must be a regular file") from error
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise WorkspaceMcpError("editable target must be a single-link regular file")
    temporary = path.parent / f".kaigo-mcp-{uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        after = path.lstat()
        if (
            _is_link_like(path)
            or not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or after.st_size != len(payload)
        ):
            raise WorkspaceMcpError("editable replacement was not a regular file")
    except WorkspaceMcpError:
        raise
    except OSError as error:
        raise WorkspaceMcpError("editable target must be a regular file") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _open_directory_nofollow(path: Path) -> int:
    try:
        return os.open(path, _directory_open_flags())
    except OSError as error:
        raise WorkspaceMcpError("workspace directory is unavailable") from error


def _directory_open_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise WorkspaceMcpError("platform lacks no-follow directory support")
    return os.O_RDONLY | nofollow | directory | getattr(os, "O_CLOEXEC", 0)


def _readonly_open_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise WorkspaceMcpError("platform lacks no-follow file support")
    return (
        os.O_RDONLY
        | nofollow
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _read_bounded_descriptor(descriptor: int, *, limit: int) -> bytes:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_size > limit
    ):
        raise WorkspaceMcpError("allowlisted path must be a bounded regular file")
    payload = bytearray()
    while True:
        chunk = os.read(descriptor, min(64 * 1024, limit - len(payload) + 1))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > limit:
            raise WorkspaceMcpError("allowlisted file exceeds its size limit")
    if len(payload) != info.st_size:
        raise WorkspaceMcpError("allowlisted file changed while being read")
    return bytes(payload)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


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


def _is_link_like(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _dispatch_rpc(
    service: WorkspaceMcpService,
    request: dict[str, Any],
) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    if request.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return _rpc_error(request_id, -32600, "invalid request")
    if request_id is None:
        return None
    if method == "initialize":
        params = request.get("params")
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        protocol = requested if isinstance(requested, str) else _PROTOCOL_VERSION
        return _rpc_result(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
            },
        )
    if method == "ping":
        return _rpc_result(request_id, {})
    if method == "tools/list":
        return _rpc_result(request_id, {"tools": list(service.tool_specs())})
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict) or set(params) - {"name", "arguments", "_meta"}:
            return _rpc_result(
                request_id,
                _tool_result({"error": "invalid tool request"}, is_error=True),
            )
        try:
            payload = service.call_tool(
                params.get("name"),
                params.get("arguments", {}),
            )
        except WorkspaceMcpError as error:
            return _rpc_result(
                request_id,
                _tool_result({"error": str(error)}, is_error=True),
            )
        return _rpc_result(request_id, _tool_result(payload))
    return _rpc_error(request_id, -32601, "method not found")


def _rpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def serve_stdio(
    service: WorkspaceMcpService,
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
) -> None:
    source = stdin or sys.stdin.buffer
    destination = stdout or sys.stdout.buffer
    while True:
        line = source.readline(_MAX_RPC_LINE_BYTES + 1)
        if not line:
            return
        if len(line) > _MAX_RPC_LINE_BYTES or not line.endswith(b"\n"):
            response = _rpc_error(None, -32700, "bounded JSON line required")
        else:
            try:
                request = json.loads(
                    line.decode("utf-8"),
                    object_pairs_hook=_reject_duplicate_keys,
                )
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
                response = _dispatch_rpc(service, request)
            except (UnicodeError, json.JSONDecodeError, ValueError):
                response = _rpc_error(None, -32700, "parse error")
        if response is None:
            continue
        serialized = json.dumps(
            response,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(serialized) > _MAX_RPC_OUTPUT_BYTES:
            serialized = json.dumps(
                _rpc_error(response.get("id"), -32603, "bounded response exceeded"),
                separators=(",", ":"),
            ).encode("utf-8")
        destination.write(serialized + b"\n")
        destination.flush()


def main() -> int:
    try:
        service = WorkspaceMcpService(Path("/workspace"))
        serve_stdio(service)
        return 0
    except BaseException as error:
        print(f"workspace_mcp_error:{type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
