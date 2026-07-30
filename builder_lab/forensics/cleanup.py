from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .config import GenerationForensicsConfig
from .policy import canonical_storage_key
from .storage import MAX_USAGE_SCAN_ENTRIES, GenerationForensicStorage


@dataclass(frozen=True, slots=True)
class ForensicCleanupTarget:
    run_id: UUID
    user_id: int
    project_id: UUID
    storage_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, UUID) or not isinstance(self.project_id, UUID):
            raise ValueError("cleanup run and project IDs must be UUIDs")
        if (
            isinstance(self.user_id, bool)
            or not isinstance(self.user_id, int)
            or self.user_id < 1
        ):
            raise ValueError("cleanup user_id must be a positive integer")
        if self.storage_key != canonical_storage_key(self.run_id):
            raise ValueError("forensic cleanup requires a canonical storage key")


@dataclass(frozen=True, slots=True)
class FilesystemPurgeResult:
    removed: bool
    recovered: bool = False
    missing: bool = False


@dataclass(frozen=True, slots=True)
class ForensicStorageUsage:
    bytes_total: int
    foreign_skipped: int = 0


class GenerationForensicFilesystemCleanup:
    """Filesystem half of cleanup; SQL commit acknowledgement stays external."""

    def __init__(self, storage: GenerationForensicStorage) -> None:
        self.storage = storage

    @classmethod
    def open(
        cls,
        config: GenerationForensicsConfig,
    ) -> GenerationForensicFilesystemCleanup:
        return cls(
            GenerationForensicStorage.open_existing(config, writable=True)
        )

    def purge(self, target: ForensicCleanupTarget) -> FilesystemPurgeResult:
        if not isinstance(target, ForensicCleanupTarget):
            raise ValueError("cleanup target is invalid")
        outcome = self.storage.cleanup_purge_run(
            run_id=target.run_id,
            user_id=target.user_id,
            project_id=target.project_id,
        )
        if outcome == "missing":
            return FilesystemPurgeResult(
                removed=False,
                missing=True,
            )
        if outcome not in {"removed", "recovered", "already_removed"}:
            raise RuntimeError("forensic storage returned an unknown cleanup outcome")
        return FilesystemPurgeResult(
            removed=True,
            recovered=outcome != "removed",
        )

    def has_tombstone(self, run_id: UUID) -> bool:
        return self.storage.cleanup_has_tombstone(run_id)

    def finalize(self, run_id: UUID) -> None:
        self.storage.cleanup_finalize_run(run_id)

    def usage(self) -> ForensicStorageUsage:
        total, foreign = self._scan_root_usage(self.storage.root)
        return ForensicStorageUsage(bytes_total=total, foreign_skipped=foreign)

    @staticmethod
    def _known_path(parts: tuple[str, ...], *, directory: bool) -> bool:
        """Recognise only Kaigo-owned layout entries without trusting their names."""

        if len(parts) == 1:
            if parts[0] == GenerationForensicStorage.ROOT_MARKER_NAME:
                return not directory
            return directory and parts[0] in {"runs", ".trash"}
        if parts[0] == "runs":
            if len(parts) == 2:
                return directory and re.fullmatch(r"[0-9a-f]{2}", parts[1]) is not None
            if len(parts) == 3:
                if not directory:
                    return False
                try:
                    run_id = UUID(parts[2])
                except ValueError:
                    return False
                return run_id.hex[:2] == parts[1]
            return True
        if parts[0] == ".trash":
            if len(parts) == 2:
                if directory:
                    try:
                        UUID(parts[1])
                    except ValueError:
                        return False
                    return True
                suffix = ".deleted.json"
                if not parts[1].endswith(suffix):
                    return False
                try:
                    UUID(parts[1][:-len(suffix)])
                except ValueError:
                    return False
                return True
            return True
        return False

    @classmethod
    def _scan_root_usage(cls, root: Path) -> tuple[int, int]:
        """Count resident bytes, never following links, and flag foreign roots."""

        total = 0
        foreign = 0
        scanned = 0
        pending: list[tuple[Path, tuple[str, ...], bool]] = [(root, (), False)]
        while pending:
            directory_path, parent_parts, foreign_parent = pending.pop()
            with os.scandir(directory_path) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > MAX_USAGE_SCAN_ENTRIES:
                        raise RuntimeError("forensic usage scan limit exceeded")
                    parts = (*parent_parts, entry.name)
                    result = entry.stat(follow_symlinks=False)
                    is_directory = stat.S_ISDIR(result.st_mode)
                    known = cls._known_path(parts, directory=is_directory)
                    is_foreign = foreign_parent or not known
                    if stat.S_ISLNK(result.st_mode):
                        if not foreign_parent:
                            foreign += 1
                        continue
                    if stat.S_ISREG(result.st_mode):
                        total += result.st_size
                        if is_foreign and not foreign_parent:
                            foreign += 1
                        continue
                    if is_directory:
                        if is_foreign and not foreign_parent:
                            foreign += 1
                        pending.append((Path(entry.path), parts, is_foreign))
                        continue
                    if not foreign_parent:
                        foreign += 1
        return total, foreign
