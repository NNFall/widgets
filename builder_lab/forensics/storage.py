from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from PIL import Image, UnidentifiedImageError

from builder_lab.redaction import JsonValue, redact_private_data
from builder_lab.reference_storage import private_write_new

from .config import GenerationForensicsConfig
from .models import (
    ForensicBlob,
    ForensicEntry,
    ForensicManifest,
    ForensicWriteResult,
    require_aware,
    utc_now,
    validate_relative_path,
)


MAX_FORENSIC_ENTRY_BYTES = 1_000_000
MAX_FORENSIC_BLOB_BYTES = 25 * 1024 * 1024
MAX_FORENSIC_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_USAGE_SCAN_ENTRIES = 100_000
MAX_FORENSIC_JPEG_WIDTH = 8_192
MAX_FORENSIC_JPEG_HEIGHT = 8_192
MAX_FORENSIC_JPEG_PIXELS = 40_000_000
_MAX_MARKER_BYTES = 4096
_EVENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_FATAL_FORENSIC_IO_ERRNOS = frozenset(
    {errno.EIO, errno.EMFILE, errno.ENFILE, errno.ENOMEM}
)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _raise_walk_error(error: OSError) -> None:
    raise error


def _validate_jpeg_bytes(data: bytes) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format != "JPEG":
                    raise ValueError("not JPEG")
                width, height = image.size
                if (
                    width < 1
                    or height < 1
                    or width > MAX_FORENSIC_JPEG_WIDTH
                    or height > MAX_FORENSIC_JPEG_HEIGHT
                    or width * height > MAX_FORENSIC_JPEG_PIXELS
                ):
                    raise ValueError("JPEG dimensions exceed the safe limit")
                image.verify()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ) as error:
        raise ValueError("forensic blob is not a safe verified JPEG") from error


def _assert_no_symlink_components(path: Path) -> None:
    absolute = path.expanduser().absolute()
    candidates: list[Path] = []
    current = absolute
    while True:
        candidates.append(current)
        if current.parent == current:
            break
        current = current.parent
    for candidate in reversed(candidates):
        try:
            if candidate.is_symlink():
                raise ValueError("forensic storage path must not contain symlinks")
        except OSError as error:
            raise ValueError("forensic storage path cannot be inspected safely") from error


def _chmod_private_directory(path: Path) -> None:
    path.chmod(0o700)


def _assert_private_permissions(path: Path, *, directory: bool) -> None:
    if os.name == "nt":
        return
    stat_result = path.lstat()
    expected = 0o700 if directory else 0o600
    if stat.S_IMODE(stat_result.st_mode) != expected:
        raise ValueError("forensic path has unsafe permission mode")
    if hasattr(os, "geteuid") and stat_result.st_uid != os.geteuid():
        raise ValueError("forensic path has an unexpected owner")


def _assert_private_descriptor(descriptor: int, *, directory: bool) -> os.stat_result:
    stat_result = os.fstat(descriptor)
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(stat_result.st_mode):
        raise ValueError("forensic descriptor has an unexpected file type")
    if os.name != "nt":
        expected_mode = 0o700 if directory else 0o600
        if stat.S_IMODE(stat_result.st_mode) != expected_mode:
            raise ValueError("forensic descriptor has unsafe permission mode")
        if hasattr(os, "geteuid") and stat_result.st_uid != os.geteuid():
            raise ValueError("forensic descriptor has an unexpected owner")
    return stat_result


def _read_descriptor_bounded(descriptor: int, *, maximum: int) -> bytes:
    stat_result = _assert_private_descriptor(descriptor, directory=False)
    if stat_result.st_size > maximum:
        raise ValueError("forensic file exceeds its safe size limit")
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > maximum:
        raise ValueError("forensic file exceeds its safe size limit")
    return data


def _read_file_bounded(path: Path, *, maximum: int) -> bytes:
    if path.is_symlink():
        raise ValueError("forensic file must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if error.errno in _FATAL_FORENSIC_IO_ERRNOS:
            raise
        raise ValueError("forensic file cannot be opened safely") from error
    try:
        return _read_descriptor_bounded(descriptor, maximum=maximum)
    finally:
        os.close(descriptor)


def _decode_small_json(data: bytes) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("forensic marker or manifest is not valid UTF-8") from error
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("forensic marker or manifest is invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("forensic marker or manifest must be an object")
    return value


def _read_small_json(path: Path, *, maximum: int) -> dict[str, Any]:
    return _decode_small_json(_read_file_bounded(path, maximum=maximum))


class _ManifestCommitError(OSError):
    def __init__(self, message: str, *, replaced: bool) -> None:
        super().__init__(message)
        self.replaced = replaced


class _UsageScanLimitError(ValueError):
    """Raised when usage accounting would traverse an unbounded run tree."""


class _PosixRootAnchor:
    """Descriptor-anchored I/O that never follows path components below root."""

    def __init__(self, root: Path, *, require_private_root: bool = True) -> None:
        if os.name == "nt":  # pragma: no cover - construction is POSIX-only
            raise RuntimeError("POSIX root anchors are unavailable on Windows")
        absolute = root.expanduser().absolute()
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(absolute.anchor or "/", flags)
        try:
            for part in absolute.parts[1:]:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            if require_private_root:
                _assert_private_descriptor(descriptor, directory=True)
        except OSError as error:
            os.close(descriptor)
            if error.errno in _FATAL_FORENSIC_IO_ERRNOS:
                raise
            raise ValueError(
                "forensic directory cannot be opened safely without symlinks"
            ) from error
        except Exception:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def make_root_private(self) -> None:
        os.fchmod(self._descriptor, 0o700)
        os.fsync(self._descriptor)
        _assert_private_descriptor(self._descriptor, directory=True)

    def close(self) -> None:
        descriptor = getattr(self, "_descriptor", -1)
        if descriptor >= 0:
            os.close(descriptor)
            self._descriptor = -1

    def __del__(self) -> None:  # pragma: no cover - best-effort process cleanup
        try:
            self.close()
        except OSError:
            pass

    @staticmethod
    def _validate_parts(parts: tuple[str, ...]) -> None:
        if any(
            not part
            or part in {".", ".."}
            or "/" in part
            or "\\" in part
            or "\x00" in part
            for part in parts
        ):
            raise ValueError("forensic anchored path contains an unsafe component")

    def _open_directory(
        self, parts: tuple[str, ...], *, create: bool = False
    ) -> int:
        self._validate_parts(parts)
        descriptor = os.dup(self._descriptor)
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            for part in parts:
                if create:
                    created = False
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                        created = True
                    except FileExistsError:
                        pass
                    if created:
                        os.fsync(descriptor)
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
                try:
                    _assert_private_descriptor(next_descriptor, directory=True)
                except Exception:
                    os.close(next_descriptor)
                    raise
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor
        except OSError as error:
            os.close(descriptor)
            if error.errno in _FATAL_FORENSIC_IO_ERRNOS:
                raise
            raise ValueError(
                "forensic directory cannot be opened safely without symlinks"
            ) from error
        except Exception:
            os.close(descriptor)
            raise

    def ensure_directory(self, parts: tuple[str, ...]) -> None:
        descriptor = self._open_directory(parts, create=True)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def assert_directory(self, parts: tuple[str, ...]) -> None:
        descriptor = self._open_directory(parts)
        os.close(descriptor)

    def kind(self, parts: tuple[str, ...]) -> str | None:
        self._validate_parts(parts)
        if not parts:
            return "directory"
        parent = self._open_directory(parts[:-1])
        try:
            try:
                result = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if stat.S_ISLNK(result.st_mode):
                raise ValueError("forensic anchored path must not be a symlink")
            if stat.S_ISREG(result.st_mode):
                return "file"
            if stat.S_ISDIR(result.st_mode):
                return "directory"
            raise ValueError("forensic anchored path has an unsafe file type")
        finally:
            os.close(parent)

    def list_names(self, parts: tuple[str, ...]) -> tuple[str, ...]:
        descriptor = self._open_directory(parts)
        try:
            return tuple(os.listdir(descriptor))
        finally:
            os.close(descriptor)

    def is_empty(self, parts: tuple[str, ...]) -> bool:
        descriptor = self._open_directory(parts)
        try:
            with os.scandir(descriptor) as entries:
                return next(entries, None) is None
        finally:
            os.close(descriptor)

    def iter_names(self, parts: tuple[str, ...]) -> Iterator[str]:
        descriptor = self._open_directory(parts)
        try:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    yield entry.name
        finally:
            os.close(descriptor)

    def read(self, parts: tuple[str, ...], *, maximum: int) -> bytes:
        self._validate_parts(parts)
        if not parts:
            raise ValueError("forensic anchored file path is empty")
        parent = self._open_directory(parts[:-1])
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(parts[-1], flags, dir_fd=parent)
            try:
                return _read_descriptor_bounded(descriptor, maximum=maximum)
            finally:
                os.close(descriptor)
        except OSError as error:
            if error.errno in _FATAL_FORENSIC_IO_ERRNOS:
                raise
            raise ValueError("forensic file cannot be opened safely") from error
        finally:
            os.close(parent)

    def write_new(self, parts: tuple[str, ...], data: bytes) -> None:
        self._validate_parts(parts)
        parent = self._open_directory(parts[:-1])
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
        )
        created = False
        try:
            descriptor = os.open(parts[-1], flags, 0o600, dir_fd=parent)
            created = True
            try:
                view = memoryview(data)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short forensic write")
                    view = view[written:]
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(parent)
        except Exception:
            if created:
                try:
                    os.unlink(parts[-1], dir_fd=parent)
                    os.fsync(parent)
                except OSError:
                    pass
            raise
        finally:
            os.close(parent)

    def replace(self, parts: tuple[str, ...], data: bytes) -> None:
        self._validate_parts(parts)
        parent = self._open_directory(parts[:-1])
        temporary = f".{parts[-1]}.{os.urandom(12).hex()}.tmp"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
        )
        replaced = False
        temporary_exists = False
        try:
            try:
                target_stat = os.stat(
                    parts[-1], dir_fd=parent, follow_symlinks=False
                )
            except FileNotFoundError:
                target_stat = None
            if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
                raise ValueError("forensic manifest target is unsafe")
            descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
            temporary_exists = True
            try:
                view = memoryview(data)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short forensic manifest write")
                    view = view[written:]
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(
                temporary,
                parts[-1],
                src_dir_fd=parent,
                dst_dir_fd=parent,
            )
            temporary_exists = False
            replaced = True
            os.fsync(parent)
        except OSError as error:
            raise _ManifestCommitError(
                f"forensic manifest directory fsync or replace failed: {error}",
                replaced=replaced,
            ) from error
        finally:
            if temporary_exists:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
            os.close(parent)

    def unlink(self, parts: tuple[str, ...]) -> None:
        self._validate_parts(parts)
        parent = self._open_directory(parts[:-1])
        try:
            try:
                os.unlink(parts[-1], dir_fd=parent)
            except FileNotFoundError:
                return
            os.fsync(parent)
        finally:
            os.close(parent)

    def rename_directory(
        self,
        source: tuple[str, ...],
        destination: tuple[str, ...],
    ) -> None:
        """Atomically move one anchored directory and fsync both parents."""

        self._validate_parts(source)
        self._validate_parts(destination)
        if not source or not destination:
            raise ValueError("forensic rename paths must not be empty")
        source_descriptor = self._open_directory(source)
        os.close(source_descriptor)
        source_parent = self._open_directory(source[:-1])
        destination_parent = self._open_directory(destination[:-1])
        try:
            try:
                os.stat(
                    destination[-1],
                    dir_fd=destination_parent,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError("forensic quarantine destination exists")
            os.rename(
                source[-1],
                destination[-1],
                src_dir_fd=source_parent,
                dst_dir_fd=destination_parent,
            )
            os.fsync(source_parent)
            os.fsync(destination_parent)
        finally:
            os.close(destination_parent)
            os.close(source_parent)

    def remove_tree(self, parts: tuple[str, ...]) -> None:
        """Delete an anchored tree without following any symlink."""

        self._validate_parts(parts)
        if not parts:
            raise ValueError("refusing to remove the forensic root")
        pending: list[tuple[tuple[str, ...], bool]] = [(parts, False)]
        while pending:
            current, visited = pending.pop()
            if visited:
                parent = self._open_directory(current[:-1])
                try:
                    try:
                        result = os.stat(
                            current[-1],
                            dir_fd=parent,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        continue
                    if not stat.S_ISDIR(result.st_mode):
                        raise ValueError(
                            "forensic cleanup directory changed during deletion"
                        )
                    os.rmdir(current[-1], dir_fd=parent)
                    os.fsync(parent)
                finally:
                    os.close(parent)
                continue

            directory = self._open_directory(current)
            child_directories: list[tuple[str, ...]] = []
            try:
                with os.scandir(directory) as entries:
                    names = tuple(entry.name for entry in entries)
                for name in names:
                    self._validate_parts((name,))
                    result = os.stat(
                        name,
                        dir_fd=directory,
                        follow_symlinks=False,
                    )
                    if stat.S_ISDIR(result.st_mode):
                        child_directories.append((*current, name))
                        continue
                    os.unlink(name, dir_fd=directory)
                os.fsync(directory)
            finally:
                os.close(directory)
            pending.append((current, True))
            pending.extend((child, False) for child in child_directories)

    def tree_size(
        self, parts: tuple[str, ...], *, maximum_entries: int
    ) -> tuple[int, int]:
        pending: list[tuple[str, ...]] = [parts]
        subtotal = 0
        scanned = 0
        while pending:
            current_parts = pending.pop()
            directory_fd = self._open_directory(current_parts)
            try:
                with os.scandir(directory_fd) as entries:
                    for entry in entries:
                        scanned += 1
                        if scanned > maximum_entries:
                            raise _UsageScanLimitError(
                                "forensic usage scan limit exceeded"
                            )
                        result = os.stat(
                            entry.name,
                            dir_fd=directory_fd,
                            follow_symlinks=False,
                        )
                        if stat.S_ISLNK(result.st_mode):
                            raise ValueError(
                                "forensic usage tree contains a symlink"
                            )
                        if stat.S_ISREG(result.st_mode):
                            if stat.S_IMODE(result.st_mode) != 0o600:
                                raise ValueError(
                                    "forensic usage file has unsafe permission mode"
                                )
                            if (
                                hasattr(os, "geteuid")
                                and result.st_uid != os.geteuid()
                            ):
                                raise ValueError(
                                    "forensic usage file has an unexpected owner"
                                )
                            subtotal += result.st_size
                            continue
                        if not stat.S_ISDIR(result.st_mode):
                            raise ValueError(
                                "forensic usage tree has an unsafe file type"
                            )
                        if stat.S_IMODE(result.st_mode) != 0o700:
                            raise ValueError(
                                "forensic usage directory has unsafe permission mode"
                            )
                        if (
                            hasattr(os, "geteuid")
                            and result.st_uid != os.geteuid()
                        ):
                            raise ValueError(
                                "forensic usage directory has an unexpected owner"
                            )
                        pending.append((*current_parts, entry.name))
            finally:
                os.close(directory_fd)
        return subtotal, scanned


def _bootstrap_private_write_new(root: Path, path: Path, data: bytes) -> None:
    if os.name == "nt":
        private_write_new(path, data)
        return
    anchor = _PosixRootAnchor(root)
    try:
        anchor.write_new((path.name,), data)
    finally:
        anchor.close()


def _atomic_private_replace(path: Path, data: bytes) -> None:
    _assert_no_symlink_components(path.parent)
    if path.is_symlink():
        raise ValueError("forensic manifest must not be a symlink")
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        _assert_no_symlink_components(path.parent)
        if path.is_symlink():
            raise ValueError("forensic manifest must not be a symlink")
        os.replace(temporary, path)
        replaced = True
        path.chmod(0o600)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError as error:
        raise _ManifestCommitError(
            f"forensic manifest directory fsync or replace failed: {error}",
            replaced=replaced,
        ) from error
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


@dataclass(frozen=True, slots=True)
class _OpenState:
    available: bool
    reason: str | None


class GenerationForensicStorage:
    ROOT_MARKER_NAME = ".kaigo-generation-forensics-root.json"
    RUN_MARKER_NAME = ".kaigo-generation-run.json"
    ROOT_KIND = "kaigo-generation-forensics-root"
    RUN_KIND = "kaigo-generation-forensics-run"

    def __init__(
        self,
        config: GenerationForensicsConfig,
        *,
        writable: bool,
        state: _OpenState,
    ) -> None:
        self.config = config
        self.root = config.root.expanduser().absolute()
        self.writable = writable
        self.available = state.available
        self.unavailable_reason = state.reason
        self._lock = threading.RLock()
        self._anchor = (
            _PosixRootAnchor(self.root)
            if state.available and os.name != "nt"
            else None
        )
        if self._anchor is not None:
            marker_data = self._anchor.read(
                (self.ROOT_MARKER_NAME,), maximum=_MAX_MARKER_BYTES
            )
            self._validate_root_marker_value(_decode_small_json(marker_data))
            if self._anchor.kind(("runs",)) is not None:
                self._anchor.assert_directory(("runs",))

    @classmethod
    def open(
        cls,
        config: GenerationForensicsConfig,
        *,
        writable: bool,
    ) -> GenerationForensicStorage:
        if not config.enabled:
            return cls(
                config,
                writable=writable,
                state=_OpenState(available=False, reason="disabled"),
            )
        root = config.root.expanduser().absolute()
        _assert_no_symlink_components(root)
        marker = root / cls.ROOT_MARKER_NAME
        if not root.exists():
            if not writable:
                return cls(
                    config,
                    writable=False,
                    state=_OpenState(available=False, reason="missing"),
                )
            root.mkdir(parents=True, mode=0o700)
            _chmod_private_directory(root)
            _bootstrap_private_write_new(
                root,
                marker,
                _canonical_json_bytes(
                    {"kind": cls.ROOT_KIND, "schema_version": 1}
                ),
            )
        else:
            if root.is_symlink() or not root.is_dir():
                raise ValueError("forensic storage root must be a real directory")
            if not marker.exists():
                if not writable:
                    return cls(
                        config,
                        writable=False,
                        state=_OpenState(available=False, reason="unmarked"),
                    )
                if os.name != "nt":
                    bootstrap = _PosixRootAnchor(
                        root,
                        require_private_root=False,
                    )
                    try:
                        root_is_empty = bootstrap.is_empty(())
                        if root_is_empty:
                            bootstrap.make_root_private()
                            bootstrap.write_new(
                                (cls.ROOT_MARKER_NAME,),
                                _canonical_json_bytes(
                                    {"kind": cls.ROOT_KIND, "schema_version": 1}
                                ),
                            )
                    finally:
                        bootstrap.close()
                else:
                    foreign = next(root.iterdir(), None)
                    root_is_empty = foreign is None
                    if root_is_empty:
                        _chmod_private_directory(root)
                        private_write_new(
                            marker,
                            _canonical_json_bytes(
                                {"kind": cls.ROOT_KIND, "schema_version": 1}
                            ),
                        )
                if not root_is_empty:
                    raise ValueError(
                        "refusing to initialize foreign or unmarked non-empty root"
                    )
        if os.name == "nt":
            cls._validate_root_marker(marker)
        runs = root / "runs"
        if os.name != "nt":
            bootstrap = _PosixRootAnchor(root)
            try:
                marker_data = bootstrap.read(
                    (cls.ROOT_MARKER_NAME,), maximum=_MAX_MARKER_BYTES
                )
                cls._validate_root_marker_value(_decode_small_json(marker_data))
                runs_kind = bootstrap.kind(("runs",))
                if runs_kind is None:
                    if not writable:
                        return cls(
                            config,
                            writable=False,
                            state=_OpenState(available=True, reason=None),
                        )
                    bootstrap.ensure_directory(("runs",))
                elif runs_kind != "directory":
                    raise ValueError("forensic runs path must be a real directory")
                else:
                    bootstrap.assert_directory(("runs",))
            finally:
                bootstrap.close()
            return cls(
                config,
                writable=writable,
                state=_OpenState(available=True, reason=None),
            )
        _assert_no_symlink_components(runs)
        if not runs.exists():
            if not writable:
                return cls(
                    config,
                    writable=False,
                    state=_OpenState(available=True, reason=None),
                )
            runs.mkdir(mode=0o700)
        if runs.is_symlink() or not runs.is_dir():
            raise ValueError("forensic runs path must be a real directory")
        if writable:
            _chmod_private_directory(root)
            _chmod_private_directory(runs)
        _assert_private_permissions(root, directory=True)
        _assert_private_permissions(runs, directory=True)
        _assert_private_permissions(marker, directory=False)
        return cls(
            config,
            writable=writable,
            state=_OpenState(available=True, reason=None),
        )

    @classmethod
    def open_existing(
        cls,
        config: GenerationForensicsConfig,
        *,
        writable: bool,
    ) -> GenerationForensicStorage:
        """Open a pre-existing marked root without bootstrapping any path."""

        if not config.enabled:
            raise ValueError("forensic cleanup requires enabled storage")
        root = config.root.expanduser().absolute()
        _assert_no_symlink_components(root)
        if not root.exists():
            raise FileNotFoundError("forensic storage root is missing")
        if root.is_symlink() or not root.is_dir():
            raise ValueError("forensic storage root must be a real directory")
        marker = root / cls.ROOT_MARKER_NAME
        if not marker.exists() or marker.is_symlink():
            raise ValueError("forensic storage root marker is missing or unsafe")
        if os.name == "nt":
            cls._validate_root_marker(marker)
        runs = root / "runs"
        if runs.exists() or runs.is_symlink():
            if runs.is_symlink() or not runs.is_dir():
                raise ValueError("forensic runs path must be a real directory")
            _assert_private_permissions(runs, directory=True)
        _assert_private_permissions(root, directory=True)
        _assert_private_permissions(marker, directory=False)
        return cls(
            config,
            writable=writable,
            state=_OpenState(available=True, reason=None),
        )

    @classmethod
    def _validate_root_marker(cls, marker: Path) -> None:
        value = _read_small_json(marker, maximum=_MAX_MARKER_BYTES)
        cls._validate_root_marker_value(value)

    @classmethod
    def _validate_root_marker_value(cls, value: dict[str, Any]) -> None:
        if value != {"kind": cls.ROOT_KIND, "schema_version": 1}:
            raise ValueError("foreign or unsupported forensic root marker")

    def _relative_parts(self, path: Path) -> tuple[str, ...]:
        try:
            relative = path.absolute().relative_to(self.root)
        except ValueError as error:
            raise ValueError("forensic path escapes the configured root") from error
        parts = tuple(relative.parts)
        if self._anchor is not None:
            self._anchor._validate_parts(parts)
        return parts

    def _path_kind(self, path: Path) -> str | None:
        if self._anchor is not None:
            return self._anchor.kind(self._relative_parts(path))
        if path.is_symlink():
            raise ValueError("forensic path must not be a symlink")
        if not path.exists():
            return None
        if path.is_file():
            return "file"
        if path.is_dir():
            return "directory"
        raise ValueError("forensic path has an unsafe file type")

    def _read_private(self, path: Path, *, maximum: int) -> bytes:
        if self._anchor is not None:
            return self._anchor.read(self._relative_parts(path), maximum=maximum)
        return _read_file_bounded(path, maximum=maximum)

    def _write_new_private(self, path: Path, data: bytes) -> None:
        if self._anchor is not None:
            self._anchor.write_new(self._relative_parts(path), data)
            return
        private_write_new(path, data)

    def _replace_private(self, path: Path, data: bytes) -> None:
        if self._anchor is not None:
            self._anchor.replace(self._relative_parts(path), data)
            return
        _atomic_private_replace(path, data)

    def _ensure_private_directory(self, path: Path) -> None:
        if self._anchor is not None:
            self._anchor.ensure_directory(self._relative_parts(path))
            return
        _assert_no_symlink_components(path)
        if not path.exists():
            path.mkdir(mode=0o700)
        if path.is_symlink() or not path.is_dir():
            raise ValueError("forensic path must be a real directory")
        _chmod_private_directory(path)

    def _list_private_directory(self, path: Path) -> tuple[str, ...]:
        if self._anchor is not None:
            return self._anchor.list_names(self._relative_parts(path))
        return tuple(item.name for item in path.iterdir())

    def _unlink_private(self, path: Path) -> None:
        if self._anchor is not None:
            self._anchor.unlink(self._relative_parts(path))
            return
        path.unlink(missing_ok=True)

    def _require_write(self) -> None:
        if not self.available:
            raise RuntimeError("forensic storage is unavailable")
        if not self.writable:
            raise RuntimeError("forensic storage is read-only")

    def _run_dir(self, run_id: UUID) -> Path:
        if not isinstance(run_id, UUID):
            raise ValueError("run_id must be a UUID")
        return self.root / "runs" / run_id.hex[:2] / str(run_id)

    def _validate_run_marker(
        self,
        run_dir: Path,
        *,
        expected_run_id: UUID | None = None,
        expected_user_id: int | None = None,
        expected_project_id: UUID | None = None,
        require_canonical_location: bool = True,
    ) -> dict[str, Any]:
        if self._path_kind(run_dir) != "directory":
            raise ValueError("forensic run path must be a real directory")
        marker_path = run_dir / self.RUN_MARKER_NAME
        marker = _decode_small_json(
            self._read_private(marker_path, maximum=_MAX_MARKER_BYTES)
        )
        if self._anchor is None:
            _assert_private_permissions(run_dir, directory=True)
            _assert_private_permissions(marker_path, directory=False)
        expected = {
            "kind": self.RUN_KIND,
            "project_id": str(UUID(str(marker.get("project_id")))),
            "run_id": str(UUID(str(marker.get("run_id")))),
            "schema_version": 1,
            "user_id": marker.get("user_id"),
        }
        if (
            isinstance(expected["user_id"], bool)
            or not isinstance(expected["user_id"], int)
            or expected["user_id"] < 1
            or marker != expected
            or expected["run_id"] != run_dir.name
        ):
            raise ValueError("foreign or invalid forensic run marker")
        marker_run_id = UUID(run_dir.name)
        if (
            expected_run_id is not None
            and marker_run_id != expected_run_id
        ):
            raise ValueError("forensic run marker has an unexpected run ID")
        if (
            expected_user_id is not None
            and marker["user_id"] != expected_user_id
        ):
            raise ValueError("forensic run marker has an unexpected user ID")
        if (
            expected_project_id is not None
            and marker["project_id"] != str(expected_project_id)
        ):
            raise ValueError("forensic run marker has an unexpected project ID")
        if (
            require_canonical_location
            and run_dir.parent.name != marker_run_id.hex[:2]
        ):
            raise ValueError("forensic run marker is stored in the wrong shard")
        return marker

    def _cleanup_trash_dir(self) -> Path:
        return self.root / ".trash"

    def _cleanup_quarantine_dir(self, run_id: UUID) -> Path:
        return self._cleanup_trash_dir() / str(run_id)

    def _cleanup_tombstone_path(self, run_id: UUID) -> Path:
        return self._cleanup_trash_dir() / f"{run_id}.deleted.json"

    @staticmethod
    def _cleanup_tombstone_bytes(run_id: UUID) -> bytes:
        return _canonical_json_bytes(
            {
                "kind": "kaigo-generation-forensics-deletion",
                "run_id": str(run_id),
                "schema_version": 1,
            }
        )

    def _validate_cleanup_tombstone(self, run_id: UUID) -> None:
        path = self._cleanup_tombstone_path(run_id)
        data = self._read_private(path, maximum=_MAX_MARKER_BYTES)
        if data != self._cleanup_tombstone_bytes(run_id):
            raise ValueError("forensic cleanup tombstone is invalid")

    def cleanup_has_tombstone(self, run_id: UUID) -> bool:
        with self._lock:
            trash = self._cleanup_trash_dir()
            if self._path_kind(trash) is None:
                return False
            kind = self._path_kind(self._cleanup_tombstone_path(run_id))
            if kind is None:
                return False
            if kind != "file":
                raise ValueError("forensic cleanup tombstone is unsafe")
            self._validate_cleanup_tombstone(run_id)
            return True

    def cleanup_purge_run(
        self,
        *,
        run_id: UUID,
        user_id: int,
        project_id: UUID,
    ) -> str:
        """Quarantine and delete one run, retaining a commit tombstone."""

        self._require_write()
        if not isinstance(run_id, UUID) or not isinstance(project_id, UUID):
            raise ValueError("cleanup run and project IDs must be UUIDs")
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
            raise ValueError("cleanup user_id must be a positive integer")
        with self._lock:
            run_dir = self._run_dir(run_id)
            trash_dir = self._cleanup_trash_dir()
            trash_kind = self._path_kind(trash_dir)
            if trash_kind is not None and trash_kind != "directory":
                raise ValueError("forensic cleanup trash path is unsafe")
            quarantine = self._cleanup_quarantine_dir(run_id)
            tombstone = self._cleanup_tombstone_path(run_id)
            quarantine_kind = (
                None if trash_kind is None else self._path_kind(quarantine)
            )
            tombstone_kind = (
                None if trash_kind is None else self._path_kind(tombstone)
            )
            if tombstone_kind is not None:
                if tombstone_kind != "file":
                    raise ValueError("forensic cleanup tombstone is unsafe")
                self._validate_cleanup_tombstone(run_id)
            run_kind = self._path_kind(run_dir)
            if run_kind is not None and run_kind != "directory":
                raise ValueError("forensic run path is unsafe")
            if quarantine_kind is not None and quarantine_kind != "directory":
                raise ValueError("forensic quarantine path is unsafe")
            if run_kind is not None and quarantine_kind is not None:
                raise ValueError("forensic run exists in both active and trash paths")
            if run_kind is None and quarantine_kind is None:
                return "already_removed" if tombstone_kind == "file" else "missing"

            recovered = quarantine_kind == "directory"
            candidate = quarantine if recovered else run_dir
            self._validate_run_marker(
                candidate,
                expected_run_id=run_id,
                expected_user_id=user_id,
                expected_project_id=project_id,
                require_canonical_location=not recovered,
            )
            if not recovered:
                if trash_kind is None:
                    self._ensure_private_directory(trash_dir)
                if self._anchor is not None:
                    self._anchor.rename_directory(
                        self._relative_parts(run_dir),
                        self._relative_parts(quarantine),
                    )
                else:
                    os.replace(run_dir, quarantine)
                self._validate_run_marker(
                    quarantine,
                    expected_run_id=run_id,
                    expected_user_id=user_id,
                    expected_project_id=project_id,
                    require_canonical_location=False,
                )
            if tombstone_kind is None:
                self._write_new_private(
                    tombstone,
                    self._cleanup_tombstone_bytes(run_id),
                )
            if self._anchor is not None:
                self._anchor.remove_tree(self._relative_parts(quarantine))
            else:
                self._remove_tree_no_follow(quarantine)
            return "recovered" if recovered else "removed"

    def _remove_tree_no_follow(self, root: Path) -> None:
        if root.is_symlink() or not root.is_dir():
            raise ValueError("forensic cleanup path must be a real directory")
        pending: list[tuple[Path, bool]] = [(root, False)]
        while pending:
            current, visited = pending.pop()
            if current.is_symlink():
                raise ValueError("forensic cleanup path must not be a symlink")
            if visited:
                current.rmdir()
                continue
            children: list[Path] = []
            with os.scandir(current) as entries:
                for entry in entries:
                    candidate = current / entry.name
                    result = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(result.st_mode):
                        children.append(candidate)
                    else:
                        candidate.unlink()
            pending.append((current, True))
            pending.extend((child, False) for child in children)

    def cleanup_finalize_run(self, run_id: UUID) -> None:
        self._require_write()
        with self._lock:
            trash = self._cleanup_trash_dir()
            if self._path_kind(trash) is None:
                return
            tombstone = self._cleanup_tombstone_path(run_id)
            kind = self._path_kind(tombstone)
            if kind is None:
                return
            if kind != "file":
                raise ValueError("forensic cleanup tombstone is unsafe")
            self._validate_cleanup_tombstone(run_id)
            self._unlink_private(tombstone)

    def initialize_run(
        self,
        *,
        user_id: int,
        project_id: UUID,
        run_id: UUID,
        created_at: datetime | None = None,
    ) -> ForensicManifest:
        self._require_write()
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
            raise ValueError("user_id must be a positive integer")
        if not all(isinstance(value, UUID) for value in (project_id, run_id)):
            raise ValueError("project_id and run_id must be UUIDs")
        created_at = require_aware(created_at or utc_now(), name="created_at")
        with self._lock:
            run_dir = self._run_dir(run_id)
            shard = run_dir.parent
            self._ensure_private_directory(shard)
            if self._path_kind(shard) != "directory":
                raise ValueError("forensic run shard must be a real directory")
            self._ensure_private_directory(run_dir)
            if self._path_kind(run_dir) != "directory":
                raise ValueError("forensic run path must be a real directory")
            marker_path = run_dir / self.RUN_MARKER_NAME
            expected_marker = {
                "kind": self.RUN_KIND,
                "project_id": str(project_id),
                "run_id": str(run_id),
                "schema_version": 1,
                "user_id": user_id,
            }
            if self._path_kind(marker_path) is not None:
                marker = self._validate_run_marker(run_dir)
                if marker != expected_marker:
                    raise ValueError("foreign or conflicting forensic run marker")
            else:
                foreign = self._list_private_directory(run_dir)
                if foreign:
                    raise ValueError("refusing to initialize foreign non-empty run path")
                self._write_new_private(
                    marker_path, _canonical_json_bytes(expected_marker)
                )
            manifest_path = run_dir / "manifest.json"
            if self._path_kind(manifest_path) is not None:
                manifest = self.load_manifest(run_id)
                if (
                    manifest.user_id != user_id
                    or manifest.project_id != project_id
                ):
                    raise ValueError("forensic manifest identity conflicts with run")
                return manifest
            manifest = ForensicManifest(
                user_id=user_id,
                project_id=project_id,
                run_id=run_id,
                created_at=created_at,
            )
            self._write_manifest(run_dir, manifest)
            return manifest

    def write_event(
        self,
        *,
        run_id: UUID,
        sequence: int,
        event_type: str,
        payload: Mapping[str, object],
        created_at: datetime | None = None,
    ) -> ForensicWriteResult:
        if isinstance(sequence, bool) or not 1 <= sequence <= 99_999_999:
            raise ValueError("event sequence must be a positive integer")
        if _EVENT_TYPE.fullmatch(event_type) is None:
            raise ValueError("event type is unsafe for a forensic path")
        return self.write_json_entry(
            run_id=run_id,
            relative_path=f"events/{sequence:08d}-{event_type}.json",
            kind="event",
            payload=payload,
            created_at=created_at,
        )

    def event_payload_matches(
        self,
        *,
        run_id: UUID,
        sequence: int,
        event_type: str,
        payload: Mapping[str, object],
    ) -> bool:
        """Compare immutable event evidence while ignoring its write timestamp."""

        if isinstance(sequence, bool) or not 1 <= sequence <= 99_999_999:
            raise ValueError("event sequence must be a positive integer")
        if _EVENT_TYPE.fullmatch(event_type) is None:
            raise ValueError("event type is unsafe for a forensic path")
        relative_path = f"events/{sequence:08d}-{event_type}.json"
        expected_payload = redact_private_data(payload)
        with self._lock:
            manifest = self.load_manifest(run_id)
            entry = next(
                (
                    item
                    for item in manifest.entries
                    if item.kind == "event" and item.relative_path == relative_path
                ),
                None,
            )
            if entry is None:
                return False
            data = self._read_private(
                self._run_dir(run_id).joinpath(*relative_path.split("/")),
                maximum=MAX_FORENSIC_ENTRY_BYTES,
            )
            if len(data) != entry.byte_count or _digest(data) != entry.sha256:
                raise ValueError("forensic event checksum mismatch")
            try:
                value = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("forensic event is invalid JSON") from error
            if not isinstance(value, dict) or _canonical_json_bytes(value) != data:
                raise ValueError("forensic event is not canonical JSON")
            if set(value) != {"created_at", "kind", "payload", "schema_version"}:
                return False
            try:
                require_aware(
                    datetime.fromisoformat(str(value["created_at"])),
                    name="created_at",
                )
            except ValueError:
                return False
            return (
                value["kind"] == "event"
                and type(value["schema_version"]) is int
                and value["schema_version"] == 1
                and value["payload"] == expected_payload
            )

    def write_model_call(
        self,
        *,
        run_id: UUID,
        call_id: UUID,
        attempt: int,
        payload: Mapping[str, object],
        created_at: datetime | None = None,
    ) -> ForensicWriteResult:
        if not isinstance(call_id, UUID):
            raise ValueError("call_id must be a UUID")
        if isinstance(attempt, bool) or not 1 <= attempt <= 99:
            raise ValueError("model call attempt must be between 1 and 99")
        return self.write_json_entry(
            run_id=run_id,
            relative_path=f"model-calls/{call_id}-{attempt:02d}.json",
            kind="model-call",
            payload=payload,
            created_at=created_at,
        )

    def write_json_entry(
        self,
        *,
        run_id: UUID,
        relative_path: str,
        kind: str,
        payload: object,
        created_at: datetime | None = None,
    ) -> ForensicWriteResult:
        self._require_write()
        relative_path = validate_relative_path(relative_path)
        created_at = require_aware(created_at or utc_now(), name="created_at")
        sanitized: JsonValue = redact_private_data(payload)
        data = _canonical_json_bytes(
            {
                "created_at": created_at.isoformat(),
                "kind": kind,
                "payload": sanitized,
                "schema_version": 1,
            }
        )
        if len(data) > MAX_FORENSIC_ENTRY_BYTES:
            return ForensicWriteResult(
                written=False,
                degraded=True,
                failure_code="entry_too_large",
            )
        return self._write_entry_bytes(
            run_id=run_id,
            relative_path=relative_path,
            kind=kind,
            data=data,
            created_at=created_at,
        )

    def write_blob(
        self, *, run_id: UUID, blob: ForensicBlob
    ) -> ForensicWriteResult:
        self._require_write()
        if not isinstance(blob, ForensicBlob):
            raise ValueError("blob must be a ForensicBlob")
        if blob.byte_count > MAX_FORENSIC_BLOB_BYTES:
            return ForensicWriteResult(
                written=False,
                degraded=True,
                failure_code="blob_too_large",
            )
        try:
            _validate_jpeg_bytes(blob.data)
        except ValueError:
            return ForensicWriteResult(
                written=False,
                degraded=True,
                failure_code="invalid_jpeg",
            )
        return self._write_entry_bytes(
            run_id=run_id,
            relative_path=f"blobs/{blob.sha256}.jpg",
            kind="blob",
            data=blob.data,
            created_at=blob.created_at,
        )

    def _write_entry_bytes(
        self,
        *,
        run_id: UUID,
        relative_path: str,
        kind: str,
        data: bytes,
        created_at: datetime,
    ) -> ForensicWriteResult:
        with self._lock:
            run_dir = self._run_dir(run_id)
            self._validate_run_marker(run_dir)
            entry = ForensicEntry(
                relative_path=relative_path,
                kind=kind,
                byte_count=len(data),
                sha256=_digest(data),
                created_at=created_at,
            )
            manifest = self.load_manifest(run_id)
            existing = next(
                (
                    item
                    for item in manifest.entries
                    if item.relative_path == entry.relative_path
                ),
                None,
            )
            if (
                kind == "blob"
                and existing is not None
                and existing.kind == entry.kind
                and existing.byte_count == entry.byte_count
                and existing.sha256 == entry.sha256
            ):
                entry = existing
            manifest = manifest.with_entry(entry)
            manifest_data = self._manifest_bytes(manifest)
            target = run_dir.joinpath(*relative_path.split("/"))
            current = run_dir
            for part in relative_path.split("/")[:-1]:
                current = current / part
                self._ensure_private_directory(current)
                if self._path_kind(current) != "directory":
                    raise ValueError("forensic entry parent must be a real directory")
            written = True
            if self._path_kind(target) is not None:
                if self._read_private(target, maximum=len(data)) != data:
                    raise ValueError("refusing to overwrite immutable forensic entry")
                written = False
            else:
                self._write_new_private(target, data)
            try:
                manifest_sha256 = self._write_manifest_bytes(
                    run_dir,
                    manifest_data,
                )
            except _ManifestCommitError as error:
                if written and not error.replaced:
                    self._unlink_private(target)
                raise
            except Exception:
                if written:
                    self._unlink_private(target)
                raise
            return ForensicWriteResult(
                written=written,
                degraded=False,
                entry=entry,
                manifest=manifest,
                manifest_sha256=manifest_sha256,
            )

    def _write_manifest(self, run_dir: Path, manifest: ForensicManifest) -> str:
        return self._write_manifest_bytes(run_dir, self._manifest_bytes(manifest))

    @staticmethod
    def _manifest_bytes(manifest: ForensicManifest) -> bytes:
        data = _canonical_json_bytes(manifest.to_dict())
        if len(data) > MAX_FORENSIC_MANIFEST_BYTES:
            raise ValueError("forensic manifest exceeds the safe size limit")
        return data

    def _write_manifest_bytes(self, run_dir: Path, data: bytes) -> str:
        self._replace_private(run_dir / "manifest.json", data)
        return _digest(data)

    def _load_manifest_snapshot(
        self, run_id: UUID
    ) -> tuple[ForensicManifest, str]:
        if not self.available:
            raise RuntimeError("forensic storage is unavailable")
        run_dir = self._run_dir(run_id)
        marker = self._validate_run_marker(run_dir)
        manifest_path = run_dir / "manifest.json"
        manifest_data = self._read_private(
            manifest_path, maximum=MAX_FORENSIC_MANIFEST_BYTES
        )
        if self._anchor is None:
            _assert_private_permissions(manifest_path, directory=False)
        try:
            value = json.loads(manifest_data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("forensic manifest is invalid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("forensic manifest must be an object")
        if _canonical_json_bytes(value) != manifest_data:
            raise ValueError("forensic manifest is not canonical JSON")
        try:
            manifest = ForensicManifest.from_dict(value)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("forensic manifest kind or path is invalid") from error
        if (
            manifest.run_id != run_id
            or manifest.user_id != marker["user_id"]
            or str(manifest.project_id) != marker["project_id"]
        ):
            raise ValueError("forensic manifest identity does not match run marker")
        for entry in manifest.entries:
            candidate = run_dir.joinpath(*entry.relative_path.split("/"))
            if self._path_kind(candidate) != "file":
                raise ValueError("forensic manifest entry is missing or unsafe")
            maximum = (
                MAX_FORENSIC_BLOB_BYTES
                if entry.kind == "blob"
                else MAX_FORENSIC_ENTRY_BYTES
            )
            data = self._read_private(candidate, maximum=maximum)
            if self._anchor is None:
                _assert_private_permissions(candidate, directory=False)
            if len(data) != entry.byte_count or _digest(data) != entry.sha256:
                raise ValueError("forensic manifest entry checksum mismatch")
            if entry.kind == "blob":
                try:
                    _validate_jpeg_bytes(data)
                except ValueError as error:
                    raise ValueError("forensic manifest blob is not a valid JPEG") from error
        return manifest, _digest(manifest_data)

    def load_manifest(self, run_id: UUID) -> ForensicManifest:
        with self._lock:
            return self._load_manifest_snapshot(run_id)[0]

    def manifest_digest(self, run_id: UUID) -> str:
        with self._lock:
            return self._load_manifest_snapshot(run_id)[1]

    def usage(self) -> int:
        if not self.available:
            return 0
        runs = self.root / "runs"
        if self._anchor is not None:
            if self._anchor.kind(("runs",)) is None:
                return 0
            total = 0
            scanned = 0
            for shard_name in self._anchor.iter_names(("runs",)):
                scanned += 1
                if scanned > MAX_USAGE_SCAN_ENTRIES:
                    raise _UsageScanLimitError(
                        "forensic usage scan limit exceeded"
                    )
                if re.fullmatch(r"[0-9a-f]{2}", shard_name) is None:
                    continue
                shard_parts = ("runs", shard_name)
                try:
                    self._anchor.assert_directory(shard_parts)
                    for run_name in self._anchor.iter_names(shard_parts):
                        scanned += 1
                        if scanned > MAX_USAGE_SCAN_ENTRIES:
                            raise _UsageScanLimitError(
                                "forensic usage scan limit exceeded"
                            )
                        try:
                            run_id = UUID(run_name)
                        except ValueError:
                            continue
                        if run_id.hex[:2] != shard_name:
                            continue
                        run_dir = runs / shard_name / run_name
                        try:
                            self._validate_run_marker(run_dir)
                            subtotal, visited = self._anchor.tree_size(
                                ("runs", shard_name, run_name),
                                maximum_entries=MAX_USAGE_SCAN_ENTRIES - scanned,
                            )
                            scanned += visited
                            total += subtotal
                        except _UsageScanLimitError:
                            raise
                        except OSError:
                            raise
                        except ValueError:
                            continue
                except _UsageScanLimitError:
                    raise
                except OSError:
                    raise
                except ValueError:
                    continue
            return total
        if not runs.exists() or runs.is_symlink():
            return 0
        total = 0
        for shard in runs.iterdir():
            if (
                shard.is_symlink()
                or not shard.is_dir()
                or re.fullmatch(r"[0-9a-f]{2}", shard.name) is None
            ):
                continue
            for run_dir in shard.iterdir():
                if run_dir.is_symlink() or not run_dir.is_dir():
                    continue
                try:
                    self._validate_run_marker(run_dir)
                    scanned = 0
                    subtotal = 0
                    unsafe = False
                    for directory, directory_names, file_names in os.walk(
                        run_dir,
                        followlinks=False,
                        onerror=_raise_walk_error,
                    ):
                        base = Path(directory)
                        for name in tuple(directory_names) + tuple(file_names):
                            scanned += 1
                            if scanned > MAX_USAGE_SCAN_ENTRIES:
                                raise _UsageScanLimitError(
                                    "forensic usage scan limit exceeded"
                                )
                            candidate = base / name
                            if candidate.is_symlink():
                                unsafe = True
                                break
                            if candidate.is_file():
                                subtotal += candidate.stat().st_size
                        if unsafe:
                            break
                    if not unsafe:
                        total += subtotal
                except _UsageScanLimitError:
                    raise
                except OSError:
                    raise
                except ValueError:
                    continue
        return total
