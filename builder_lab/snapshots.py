from __future__ import annotations

import io
import json
import re
import tarfile
from pathlib import PurePosixPath
from typing import Any

from .models import WidgetArtifact


DECLARED_PATHS = frozenset(
    {"out/widget-artifact.json", "out/build-report.json"}
)


class SnapshotRejected(ValueError):
    """Raised when an agent snapshot crosses the Kaigo import boundary."""


def _safe_member_path(raw_name: str) -> str:
    normalized = raw_name.replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        raise SnapshotRejected("snapshot contains an absolute path")
    path = PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise SnapshotRejected("snapshot contains a traversal path")
    return path.as_posix()


def _decode_object(data: bytes, path: str) -> dict[str, Any]:
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotRejected(f"{path} must contain valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict):
        raise SnapshotRejected(f"{path} JSON must be an object")
    return decoded


def collect_declared_snapshot(
    archive_bytes: bytes,
    *,
    max_total_bytes: int = 10 * 1024 * 1024,
    max_file_bytes: int = 2 * 1024 * 1024,
    max_members: int = 2_000,
) -> tuple[WidgetArtifact, dict[str, Any]]:
    """Inspect a tar snapshot and import only the two declared output objects."""

    if min(max_total_bytes, max_file_bytes, max_members) < 1:
        raise ValueError("snapshot limits must be positive")
    collected: dict[str, bytes] = {}
    total_bytes = 0
    member_count = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise SnapshotRejected("snapshot is not a readable tar archive") from exc

    with archive:
        for member in archive:
            member_count += 1
            if member_count > max_members:
                raise SnapshotRejected("snapshot contains too many members")
            path = _safe_member_path(member.name)
            if member.isdir():
                continue
            if path in DECLARED_PATHS and not member.isfile():
                raise SnapshotRejected(
                    f"declared snapshot path must be a regular file: {path}"
                )
            if not member.isfile():
                # Remote environment snapshots may represent deletion of an
                # unrelated cache file as an overlay whiteout (a character
                # device). Nothing is extracted here; only the two declared
                # regular JSON files cross the import boundary.
                continue
            if member.size < 0:
                raise SnapshotRejected("snapshot member has an invalid size")
            total_bytes += member.size
            if total_bytes > max_total_bytes:
                raise SnapshotRejected("snapshot uncompressed bytes exceed the limit")
            if path not in DECLARED_PATHS:
                continue
            if path in collected:
                raise SnapshotRejected(f"duplicate declared snapshot path: {path}")
            if member.size > max_file_bytes:
                raise SnapshotRejected(f"declared snapshot file exceeds the limit: {path}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise SnapshotRejected(f"declared snapshot file cannot be read: {path}")
            data = extracted.read(max_file_bytes + 1)
            if len(data) != member.size or len(data) > max_file_bytes:
                raise SnapshotRejected(f"declared snapshot file exceeds the limit: {path}")
            collected[path] = data

    missing = DECLARED_PATHS - collected.keys()
    if missing:
        raise SnapshotRejected(
            "snapshot is missing declared files: " + ", ".join(sorted(missing))
        )
    artifact_payload = _decode_object(
        collected["out/widget-artifact.json"], "out/widget-artifact.json"
    )
    report = _decode_object(
        collected["out/build-report.json"], "out/build-report.json"
    )
    try:
        artifact = WidgetArtifact.from_dict(artifact_payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotRejected("widget artifact does not match the Kaigo contract") from exc
    if (
        report.get("validator") != "passed"
        or report.get("schema_version") != artifact.schema_version
        or report.get("artifact_revision") != artifact.revision
    ):
        raise SnapshotRejected("build report does not attest the imported artifact")
    return artifact, report
