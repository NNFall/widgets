from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


EXPIRY_MARKER = ".kaigo-evidence-expiry.json"


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def validate_evidence_output_dir(
    output_dir: Path,
    *,
    workspace_root: Path,
    allow_workspace: bool,
) -> Path:
    raw = output_dir.expanduser().absolute()
    for candidate in (raw, *raw.parents):
        if candidate.exists() and candidate.is_symlink():
            raise ValueError("evidence output path must not contain symlinks")
    resolved = raw.resolve(strict=False)
    workspace = workspace_root.expanduser().resolve()
    if _is_within(resolved, workspace) and not allow_workspace:
        raise ValueError(
            "evidence output inside the Git workspace requires --allow-workspace"
        )
    return resolved


def _atomic_private_write(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite evidence file: {path.name}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_evidence_expiry_marker(run_dir: Path, expires_at: datetime) -> Path:
    if expires_at.tzinfo is None:
        raise ValueError("expires_at must be timezone aware")
    run_dir = run_dir.resolve(strict=True)
    payload = json.dumps(
        {
            "schema_version": 1,
            "kind": "kaigo-private-reference-evidence",
            "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    marker = run_dir / EXPIRY_MARKER
    _atomic_private_write(marker, payload)
    return marker


def cleanup_expired_evidence(
    root: Path, *, now: datetime | None = None
) -> tuple[Path, ...]:
    root = root.expanduser().resolve(strict=True)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    removed: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        marker = child / EXPIRY_MARKER
        try:
            if marker.is_symlink() or marker.stat().st_size > 4096:
                continue
            payload = json.loads(marker.read_text(encoding="ascii"))
            if payload.get("kind") != "kaigo-private-reference-evidence":
                continue
            expires_at = datetime.fromisoformat(str(payload["expires_at"]))
            if expires_at.tzinfo is None or expires_at > now:
                continue
            resolved = child.resolve(strict=True)
            if resolved.parent != root:
                continue
            shutil.rmtree(resolved)
            removed.append(resolved)
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return tuple(sorted(removed, key=str))


def private_write_new(path: Path, data: bytes) -> None:
    """Write a new raw evidence file without following or replacing links."""
    _atomic_private_write(path, data)
