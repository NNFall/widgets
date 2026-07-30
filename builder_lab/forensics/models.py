from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Mapping
from uuid import UUID


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EVENT_ENTRY_PATH = re.compile(
    r"^events/[0-9]{8}-[a-z0-9][a-z0-9_.-]{0,127}\.json$"
)
_MODEL_CALL_ENTRY_PATH = re.compile(
    r"^model-calls/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}-[0-9]{2}\.json$"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_aware(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone aware")
    return value.astimezone(timezone.utc)


def validate_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("forensic path must be a non-empty relative path")
    if "\x00" in value or "\\" in value:
        raise ValueError("forensic path must use a safe relative POSIX path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not posix.parts
        or value != posix.as_posix()
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise ValueError("forensic path must be relative and must not contain parents")
    for part in posix.parts:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", part) is None:
            raise ValueError("forensic path contains an unsafe component")
    return posix.as_posix()


def validate_sha256(value: str) -> str:
    normalized = value.strip().casefold()
    if _SHA256.fullmatch(normalized) is None:
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
    return normalized


@dataclass(frozen=True, slots=True)
class ForensicBlob:
    data: bytes = field(repr=False)
    mime_type: str
    byte_count: int
    sha256: str
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if type(self.data) is not bytes:
            raise ValueError("forensic blob data must be bytes")
        if self.mime_type != "image/jpeg":
            raise ValueError("forensic blobs only support image/jpeg")
        if isinstance(self.byte_count, bool) or self.byte_count != len(self.data):
            raise ValueError("forensic blob byte_count does not match data")
        digest = validate_sha256(self.sha256)
        if digest != hashlib.sha256(self.data).hexdigest():
            raise ValueError("forensic blob sha256 does not match data")
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(
            self, "created_at", require_aware(self.created_at, name="created_at")
        )


@dataclass(frozen=True, slots=True)
class ForensicEntry:
    relative_path: str
    kind: str
    byte_count: int
    sha256: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "relative_path", validate_relative_path(self.relative_path)
        )
        if not self.kind or len(self.kind) > 64:
            raise ValueError("forensic entry kind is invalid")
        if isinstance(self.byte_count, bool) or self.byte_count < 0:
            raise ValueError("forensic entry byte_count must not be negative")
        object.__setattr__(self, "sha256", validate_sha256(self.sha256))
        object.__setattr__(
            self, "created_at", require_aware(self.created_at, name="created_at")
        )
        if self.kind == "event":
            valid_mapping = _EVENT_ENTRY_PATH.fullmatch(self.relative_path) is not None
        elif self.kind == "model-call":
            valid_mapping = (
                _MODEL_CALL_ENTRY_PATH.fullmatch(self.relative_path) is not None
            )
        elif self.kind == "blob":
            valid_mapping = self.relative_path == f"blobs/{self.sha256}.jpg"
        else:
            valid_mapping = False
        if not valid_mapping:
            raise ValueError("forensic entry kind does not match its relative path")

    def to_dict(self) -> dict[str, object]:
        return {
            "byte_count": self.byte_count,
            "created_at": self.created_at.isoformat(),
            "kind": self.kind,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ForensicEntry:
        byte_count = value["byte_count"]
        if type(byte_count) is not int:
            raise ValueError("forensic entry byte_count must be an integer")
        return cls(
            relative_path=str(value["relative_path"]),
            kind=str(value["kind"]),
            byte_count=byte_count,
            sha256=str(value["sha256"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
        )


@dataclass(frozen=True, slots=True)
class ForensicManifest:
    user_id: UUID
    project_id: UUID
    run_id: UUID
    created_at: datetime
    entries: tuple[ForensicEntry, ...] = ()
    kind: str = "kaigo-generation-forensics-manifest"
    schema_version: int = 1

    def __post_init__(self) -> None:
        for name in ("user_id", "project_id", "run_id"):
            if not isinstance(getattr(self, name), UUID):
                raise ValueError(f"{name} must be a UUID")
        if self.kind != "kaigo-generation-forensics-manifest":
            raise ValueError("foreign forensic manifest kind")
        if self.schema_version != 1:
            raise ValueError("unsupported forensic manifest schema")
        object.__setattr__(
            self, "created_at", require_aware(self.created_at, name="created_at")
        )
        paths = [entry.relative_path for entry in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("forensic manifest contains duplicate paths")

    def with_entry(self, entry: ForensicEntry) -> ForensicManifest:
        by_path = {item.relative_path: item for item in self.entries}
        existing = by_path.get(entry.relative_path)
        if existing is not None and existing != entry:
            raise ValueError("forensic entry path is already bound to other bytes")
        by_path[entry.relative_path] = entry
        return ForensicManifest(
            user_id=self.user_id,
            project_id=self.project_id,
            run_id=self.run_id,
            created_at=self.created_at,
            entries=tuple(by_path[path] for path in sorted(by_path)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "created_at": self.created_at.isoformat(),
            "entries": [entry.to_dict() for entry in self.entries],
            "kind": self.kind,
            "project_id": str(self.project_id),
            "run_id": str(self.run_id),
            "schema_version": self.schema_version,
            "user_id": str(self.user_id),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ForensicManifest:
        entries = value.get("entries")
        if not isinstance(entries, list):
            raise ValueError("forensic manifest entries must be a list")
        schema_version = value["schema_version"]
        if type(schema_version) is not int:
            raise ValueError("forensic manifest schema_version must be an integer")
        return cls(
            user_id=UUID(str(value["user_id"])),
            project_id=UUID(str(value["project_id"])),
            run_id=UUID(str(value["run_id"])),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            entries=tuple(ForensicEntry.from_dict(item) for item in entries),
            kind=str(value["kind"]),
            schema_version=schema_version,
        )


@dataclass(frozen=True, slots=True)
class ForensicWriteResult:
    written: bool
    degraded: bool
    entry: ForensicEntry | None = None
    manifest: ForensicManifest | None = None
    manifest_sha256: str | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if self.manifest_sha256 is not None:
            object.__setattr__(
                self, "manifest_sha256", validate_sha256(self.manifest_sha256)
            )
        if self.degraded and self.written:
            raise ValueError("degraded forensic result cannot be written")
        if self.degraded and not self.failure_code:
            raise ValueError("degraded forensic result requires a failure code")
        if self.failure_code and len(self.failure_code) > 64:
            raise ValueError("forensic failure code is too long")
