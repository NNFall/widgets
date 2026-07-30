from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from PIL import Image

import builder_lab.forensics.storage as forensic_storage_module
from builder_lab.forensics.config import GenerationForensicsConfig
from builder_lab.forensics.models import ForensicBlob, ForensicEntry, ForensicManifest
from builder_lab.forensics.storage import GenerationForensicStorage


def _config(root: Path, *, enabled: bool = True) -> GenerationForensicsConfig:
    return GenerationForensicsConfig(
        enabled=enabled,
        root=root,
        ttl_hours=120,
        max_bytes=10 * 1024 * 1024 * 1024,
        admin_emails=("operator@example.com",),
    )


def _ids() -> tuple[int, UUID, UUID]:
    return 37, uuid4(), uuid4()


def _jpeg_bytes(color: str = "red") -> bytes:
    stream = BytesIO()
    Image.new("RGB", (4, 3), color).save(stream, format="JPEG")
    return stream.getvalue()


def _jpeg_with_declared_dimensions(width: int, height: int) -> bytes:
    data = bytearray(_jpeg_bytes())
    for marker in (b"\xff\xc0", b"\xff\xc2"):
        offset = data.find(marker)
        if offset >= 0:
            data[offset + 5 : offset + 7] = height.to_bytes(2, "big")
            data[offset + 7 : offset + 9] = width.to_bytes(2, "big")
            return bytes(data)
    raise AssertionError("test JPEG does not contain a SOF marker")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _assert_private_mode(path: Path, expected: int) -> None:
    # Windows ACLs are not represented by POSIX chmod bits in stat(). The
    # production target is Linux, where the exact contract is observable.
    if os.name != "nt":
        assert _mode(path) & 0o777 == expected


def _tree_size(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in path.rglob("*")
        if candidate.is_file() and not candidate.is_symlink()
    )


def test_forensics_config_defaults_are_disabled_and_do_not_create_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "must-not-exist"
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_ROOT", str(root))

    config = GenerationForensicsConfig.from_env(environment="development")

    assert config.enabled is False
    assert config.ttl_hours == 120
    assert config.max_bytes == 10 * 1024 * 1024 * 1024
    assert config.admin_emails == ()
    assert not root.exists()
    assert "must-not-exist" not in repr(config)


def test_shared_forensics_config_import_has_no_optional_worker_dependencies() -> None:
    workspace = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "from builder_lab.forensics.config import GenerationForensicsConfig",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("ttl_hours", [72, 96, 120])
def test_forensics_config_accepts_development_ttl_range(
    ttl_hours: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_TTL_HOURS", str(ttl_hours))
    assert (
        GenerationForensicsConfig.from_env(environment="test").ttl_hours
        == ttl_hours
    )


@pytest.mark.parametrize("ttl_hours", [0, 71, 121])
def test_forensics_config_rejects_ttl_outside_safe_range(
    ttl_hours: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_TTL_HOURS", str(ttl_hours))
    with pytest.raises(ValueError, match="72.*120"):
        GenerationForensicsConfig.from_env(environment="development")


def test_enabled_production_config_is_exact_and_normalizes_admin_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_ENABLED", "true")
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_ROOT", str(tmp_path / "private"))
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_TTL_HOURS", "120")
    monkeypatch.setenv(
        "KAIGO_GENERATION_FORENSICS_ADMIN_EMAILS",
        " Admin@Example.com,ops@example.com, admin@example.com ",
    )

    config = GenerationForensicsConfig.from_env(environment="production")

    assert config.enabled is True
    assert config.root == tmp_path / "private"
    assert config.ttl_hours == 120
    assert config.admin_emails == ("admin@example.com", "ops@example.com")
    rendered = repr(config)
    assert "admin@example.com" not in rendered
    assert str(config.root) not in rendered


@pytest.mark.parametrize(
    ("root", "ttl", "admins", "message"),
    [
        ("relative/root", "120", "ops@example.com", "absolute"),
        (None, "119", "ops@example.com", "120"),
        (None, "120", "   ", "allowlist"),
    ],
)
def test_enabled_production_config_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root: str | None,
    ttl: str,
    admins: str,
    message: str,
) -> None:
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_ENABLED", "true")
    monkeypatch.setenv(
        "KAIGO_GENERATION_FORENSICS_ROOT",
        root if root is not None else str(tmp_path / "private"),
    )
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_TTL_HOURS", ttl)
    monkeypatch.setenv("KAIGO_GENERATION_FORENSICS_ADMIN_EMAILS", admins)
    with pytest.raises(ValueError, match=message):
        GenerationForensicsConfig.from_env(environment="production")


def test_disabled_and_read_only_open_never_materialize_storage(tmp_path: Path) -> None:
    disabled_root = tmp_path / "disabled"
    disabled = GenerationForensicStorage.open(
        _config(disabled_root, enabled=False), writable=True
    )
    assert disabled.available is False
    assert disabled.unavailable_reason == "disabled"
    assert not disabled_root.exists()

    missing_root = tmp_path / "missing"
    readonly = GenerationForensicStorage.open(
        _config(missing_root), writable=False
    )
    assert readonly.available is False
    assert readonly.unavailable_reason == "missing"
    assert not missing_root.exists()

    unmarked_root = tmp_path / "unmarked"
    unmarked_root.mkdir()
    (unmarked_root / "foreign.txt").write_text("preserve", encoding="utf-8")
    readonly_unmarked = GenerationForensicStorage.open(
        _config(unmarked_root), writable=False
    )
    assert readonly_unmarked.available is False
    assert readonly_unmarked.unavailable_reason == "unmarked"
    assert (unmarked_root / "foreign.txt").read_text("utf-8") == "preserve"


def test_writable_open_initializes_marked_private_root_and_rejects_foreign_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "forensics"
    storage = GenerationForensicStorage.open(_config(root), writable=True)

    assert storage.available is True
    assert json.loads((root / storage.ROOT_MARKER_NAME).read_text("utf-8")) == {
        "kind": "kaigo-generation-forensics-root",
        "schema_version": 1,
    }
    _assert_private_mode(root, 0o700)
    _assert_private_mode(root / "runs", 0o700)
    _assert_private_mode(root / storage.ROOT_MARKER_NAME, 0o600)

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "do-not-touch.txt").write_text("foreign", encoding="utf-8")
    with pytest.raises(ValueError, match="unmarked|foreign"):
        GenerationForensicStorage.open(_config(foreign), writable=True)
    assert (foreign / "do-not-touch.txt").read_text("utf-8") == "foreign"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_rejecting_foreign_root_does_not_change_its_permissions(
    tmp_path: Path,
) -> None:
    foreign = tmp_path / "foreign-permissions"
    foreign.mkdir(mode=0o755)
    (foreign / "do-not-touch.txt").write_text("foreign", encoding="utf-8")
    original_mode = _mode(foreign)

    with pytest.raises(ValueError, match="unmarked|foreign"):
        GenerationForensicStorage.open(_config(foreign), writable=True)

    assert _mode(foreign) == original_mode


def test_rejecting_foreign_root_does_not_attempt_permission_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign = tmp_path / "foreign-no-mutation"
    foreign.mkdir()
    (foreign / "do-not-touch.txt").write_text("foreign", encoding="utf-8")
    chmod_calls: list[Path] = []

    def record_chmod(path: Path) -> None:
        chmod_calls.append(path)

    monkeypatch.setattr(
        forensic_storage_module,
        "_chmod_private_directory",
        record_chmod,
    )

    with pytest.raises(ValueError, match="unmarked|foreign"):
        GenerationForensicStorage.open(_config(foreign), writable=True)

    assert chmod_calls == []


def test_run_layout_entries_manifest_digest_and_restart_proof(tmp_path: Path) -> None:
    root = tmp_path / "forensics"
    user_id, project_id, run_id = _ids()
    storage = GenerationForensicStorage.open(_config(root), writable=True)

    initial = storage.initialize_run(
        user_id=user_id,
        project_id=project_id,
        run_id=run_id,
        created_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )
    result = storage.write_event(
        run_id=run_id,
        sequence=1,
        event_type="run.created",
        payload={
            "message": "started",
            "authorization": "Bearer never-store-this-token",
            "email": "private@example.com",
        },
        created_at=datetime(2026, 7, 30, 1, tzinfo=timezone.utc),
    )

    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)
    marker = json.loads(
        (run_dir / storage.RUN_MARKER_NAME).read_text(encoding="utf-8")
    )
    assert marker == {
        "kind": "kaigo-generation-forensics-run",
        "project_id": str(project_id),
        "run_id": str(run_id),
        "schema_version": 1,
        "user_id": user_id,
    }
    assert initial.run_id == run_id
    assert result.written is True
    assert result.degraded is False
    assert result.entry is not None
    assert result.entry.relative_path == "events/00000001-run.created.json"
    assert not Path(result.entry.relative_path).is_absolute()

    event_path = run_dir / result.entry.relative_path
    event_bytes = event_path.read_bytes()
    assert result.entry.byte_count == len(event_bytes)
    assert result.entry.sha256 == hashlib.sha256(event_bytes).hexdigest()
    assert b"never-store-this-token" not in event_bytes
    assert b"private@example.com" not in event_bytes
    _assert_private_mode(event_path, 0o600)
    _assert_private_mode(event_path.parent, 0o700)

    manifest_path = run_dir / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    assert manifest_bytes == json.dumps(
        json.loads(manifest_bytes),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert result.manifest_sha256 == hashlib.sha256(manifest_bytes).hexdigest()
    assert not list(run_dir.rglob("*.tmp"))

    restarted = GenerationForensicStorage.open(_config(root), writable=False)
    reloaded = restarted.load_manifest(run_id)
    assert reloaded == result.manifest
    assert restarted.manifest_digest(run_id) == result.manifest_sha256


def test_model_call_layout_is_canonical_and_restart_verified(tmp_path: Path) -> None:
    root = tmp_path / "forensics"
    user_id, project_id, run_id = _ids()
    call_id = uuid4()
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)

    result = storage.write_model_call(
        run_id=run_id,
        call_id=call_id,
        attempt=1,
        payload={"provider": "test", "response": {"safe": True}},
    )

    assert result.entry is not None
    assert result.entry.relative_path == f"model-calls/{call_id}-01.json"
    restarted = GenerationForensicStorage.open(_config(root), writable=False)
    assert restarted.load_manifest(run_id) == result.manifest


def test_restart_rejects_foreign_run_marker_and_tampered_entry(tmp_path: Path) -> None:
    root = tmp_path / "forensics"
    user_id, project_id, run_id = _ids()
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    result = storage.write_event(
        run_id=run_id,
        sequence=1,
        event_type="run.created",
        payload={"safe": True},
    )
    assert result.entry is not None
    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)
    event_path = run_dir / result.entry.relative_path

    event_path.write_bytes(b"tampered")
    restarted = GenerationForensicStorage.open(_config(root), writable=False)
    with pytest.raises(ValueError, match="checksum"):
        restarted.load_manifest(run_id)

    event_path.write_bytes(
        json.dumps({"safe": True}, separators=(",", ":")).encode("utf-8")
    )
    marker_path = run_dir / storage.RUN_MARKER_NAME
    marker = json.loads(marker_path.read_text("utf-8"))
    marker["kind"] = "foreign-run"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(ValueError, match="foreign|invalid"):
        restarted.load_manifest(run_id)


def test_restart_rejects_manifest_entry_above_type_specific_read_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from builder_lab.forensics import storage as storage_module

    root = tmp_path / "forensics"
    user_id, project_id, run_id = _ids()
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    result = storage.write_event(
        run_id=run_id,
        sequence=1,
        event_type="run.created",
        payload={"safe": True},
    )
    assert result.entry is not None
    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)
    entry_path = run_dir / result.entry.relative_path
    oversized = b"x" * 64
    entry_path.write_bytes(oversized)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["entries"][0]["byte_count"] = len(oversized)
    manifest["entries"][0]["sha256"] = hashlib.sha256(oversized).hexdigest()
    manifest_path.write_bytes(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    monkeypatch.setattr(storage_module, "MAX_FORENSIC_ENTRY_BYTES", 32)

    restarted = GenerationForensicStorage.open(_config(root), writable=False)
    with pytest.raises(ValueError, match="size|limit"):
        restarted.load_manifest(run_id)


def test_restart_rejects_kind_path_limit_bypass(tmp_path: Path) -> None:
    root = tmp_path / "forensics"
    user_id, project_id, run_id = _ids()
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    result = storage.write_event(
        run_id=run_id,
        sequence=1,
        event_type="run.created",
        payload={"safe": True},
    )
    assert result.entry is not None
    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)
    entry_path = run_dir / result.entry.relative_path
    oversized = b"x" * (1_000_000 + 1)
    entry_path.write_bytes(oversized)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["entries"][0].update(
        {
            "kind": "blob",
            "byte_count": len(oversized),
            "sha256": hashlib.sha256(oversized).hexdigest(),
        }
    )
    manifest_path.write_bytes(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )

    restarted = GenerationForensicStorage.open(_config(root), writable=False)
    with pytest.raises(ValueError, match="kind|path|limit"):
        restarted.load_manifest(run_id)


def test_restart_revalidates_blob_as_jpeg(tmp_path: Path) -> None:
    root = tmp_path / "forensics"
    user_id, project_id, run_id = _ids()
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    original = _jpeg_bytes()
    result = storage.write_blob(
        run_id=run_id,
        blob=ForensicBlob(
            data=original,
            mime_type="image/jpeg",
            byte_count=len(original),
            sha256=hashlib.sha256(original).hexdigest(),
        ),
    )
    assert result.entry is not None
    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)
    old_path = run_dir / result.entry.relative_path
    fake = b"not-a-jpeg"
    digest = hashlib.sha256(fake).hexdigest()
    new_path = run_dir / "blobs" / f"{digest}.jpg"
    old_path.rename(new_path)
    new_path.write_bytes(fake)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["entries"][0].update(
        {
            "relative_path": f"blobs/{digest}.jpg",
            "byte_count": len(fake),
            "sha256": digest,
        }
    )
    manifest_path.write_bytes(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )

    restarted = GenerationForensicStorage.open(_config(root), writable=False)
    with pytest.raises(ValueError, match="JPEG|jpeg"):
        restarted.load_manifest(run_id)


def test_broken_manifest_does_not_leave_new_orphan_entry(tmp_path: Path) -> None:
    root = tmp_path / "forensics"
    user_id, project_id, run_id = _ids()
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)
    (run_dir / "manifest.json").write_text("{broken", encoding="utf-8")
    entry_path = run_dir / "events" / "00000001-run.created.json"

    with pytest.raises(ValueError, match="manifest"):
        storage.write_event(
            run_id=run_id,
            sequence=1,
            event_type="run.created",
            payload={"safe": True},
        )

    assert not entry_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="directory fsync is POSIX-only")
def test_manifest_parent_fsync_failure_is_not_reported_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "forensics"
    user_id, project_id, run_id = _ids()
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)
    events_dir = run_dir / "events"
    events_dir.mkdir(mode=0o700)
    events_dir.chmod(0o700)
    run_identity = (run_dir.stat().st_dev, run_dir.stat().st_ino)
    original_fsync = os.fsync

    def fail_run_directory_fsync(descriptor: int) -> None:
        result = os.fstat(descriptor)
        if stat.S_ISDIR(result.st_mode) and (result.st_dev, result.st_ino) == run_identity:
            raise OSError("simulated directory fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_run_directory_fsync)
    with pytest.raises(OSError, match="fsync|directory"):
        storage.write_event(
            run_id=run_id,
            sequence=1,
            event_type="run.created",
            payload={"safe": True},
        )


def test_manifest_digest_hashes_the_same_snapshot_that_was_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "forensics"
    user_id, project_id, run_id = _ids()
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    manifest_path = root / "runs" / run_id.hex[:2] / str(run_id) / "manifest.json"
    verified = manifest_path.read_bytes()
    changed_value = json.loads(verified)
    changed_value["created_at"] = "2026-07-30T12:00:00+00:00"
    changed = json.dumps(
        changed_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    original_read = storage._read_private
    reads = 0

    def swap_after_first_manifest_read(path: Path, *, maximum: int) -> bytes:
        nonlocal reads
        data = original_read(path, maximum=maximum)
        if Path(path) == manifest_path:
            reads += 1
            if reads == 1:
                manifest_path.write_bytes(changed)
        return data

    monkeypatch.setattr(storage, "_read_private", swap_after_first_manifest_read)

    assert storage.manifest_digest(run_id) == hashlib.sha256(verified).hexdigest()
    assert reads == 1


@pytest.mark.parametrize(
    "relative_path",
    [
        "/absolute.json",
        "../escape.json",
        "events/../../escape.json",
        "C:/drive.json",
        ".",
        "events/./normalized.json",
        "events//normalized.json",
    ],
)
def test_entry_paths_reject_absolute_parent_and_drive_prefix(
    tmp_path: Path,
    relative_path: str,
) -> None:
    storage = GenerationForensicStorage.open(_config(tmp_path / "root"), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)

    with pytest.raises(ValueError, match="relative|path"):
        storage.write_json_entry(
            run_id=run_id,
            relative_path=relative_path,
            kind="event",
            payload={"safe": True},
        )


def test_symlinks_and_foreign_markers_are_rejected_before_access(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = tmp_path / "target"
    target.mkdir()
    try:
        root.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(ValueError, match="symlink"):
        GenerationForensicStorage.open(_config(root), writable=True)

    root.unlink()
    root.mkdir()
    (root / GenerationForensicStorage.ROOT_MARKER_NAME).write_text(
        json.dumps({"kind": "another-service", "schema_version": 1}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="marker"):
        GenerationForensicStorage.open(_config(root), writable=False)


def test_run_entry_symlink_is_rejected_without_touching_target(tmp_path: Path) -> None:
    root = tmp_path / "root"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)
    events = run_dir / "events"
    events.mkdir(mode=0o700)
    target = tmp_path / "target.json"
    target.write_text("foreign", encoding="utf-8")
    link = events / "00000001-run.created.json"
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(ValueError, match="symlink"):
        storage.write_event(
            run_id=run_id,
            sequence=1,
            event_type="run.created",
            payload={"safe": True},
        )
    assert target.read_text(encoding="utf-8") == "foreign"


@pytest.mark.skipif(os.name == "nt", reason="POSIX dirfd/openat contract")
def test_posix_anchor_rejects_replaced_intermediate_run_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    shard = root / "runs" / run_id.hex[:2]
    original = root / "runs" / f"{run_id.hex[:2]}-original"
    shard.rename(original)
    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    shard.symlink_to(external, target_is_directory=True)

    with pytest.raises((OSError, ValueError), match="symlink|safely|unsafe"):
        storage.load_manifest(run_id)

    assert (original / str(run_id) / "manifest.json").is_file()
    assert tuple(external.iterdir()) == ()


@pytest.mark.skipif(
    os.name == "nt" or not Path("/proc/self/fd").is_dir(),
    reason="Linux descriptor accounting contract",
)
def test_posix_anchor_closes_child_descriptor_when_validation_fails(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    events = root / "runs" / run_id.hex[:2] / str(run_id) / "events"
    events.mkdir(mode=0o700)
    events.chmod(0o755)
    assert storage._anchor is not None
    parts = ("runs", run_id.hex[:2], str(run_id), "events")
    before = len(tuple(Path("/proc/self/fd").iterdir()))

    for _ in range(50):
        with pytest.raises(ValueError, match="permission|mode"):
            storage._anchor.assert_directory(parts)

    after = len(tuple(Path("/proc/self/fd").iterdir()))
    assert after <= before + 1


def test_usage_propagates_marker_read_resource_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    original_open = os.open

    def fail_run_marker_open(path, flags, *args, **kwargs):
        if (
            isinstance(path, (str, os.PathLike))
            and Path(path).name == storage.RUN_MARKER_NAME
        ):
            raise OSError(errno.EMFILE, "simulated descriptor exhaustion")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", fail_run_marker_open)

    with pytest.raises(OSError) as error:
        storage.usage()
    assert error.value.errno == errno.EMFILE


def test_verified_jpeg_blob_is_immutable_and_metadata_is_checked(tmp_path: Path) -> None:
    root = tmp_path / "root"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    data = _jpeg_bytes()
    digest = hashlib.sha256(data).hexdigest()
    blob = ForensicBlob(
        data=data,
        mime_type="image/jpeg",
        byte_count=len(data),
        sha256=digest,
    )

    first = storage.write_blob(run_id=run_id, blob=blob)
    second = storage.write_blob(run_id=run_id, blob=blob)

    assert first.written is True
    assert second.written is False
    assert second.degraded is False
    assert first.entry is not None
    assert first.entry.relative_path == f"blobs/{digest}.jpg"
    blob_path = root / "runs" / run_id.hex[:2] / str(run_id) / first.entry.relative_path
    assert blob_path.read_bytes() == data
    _assert_private_mode(blob_path, 0o600)

    with pytest.raises(ValueError, match="byte_count"):
        ForensicBlob(
            data=data,
            mime_type="image/jpeg",
            byte_count=len(data) + 1,
            sha256=digest,
        )
    with pytest.raises(ValueError, match="sha256"):
        ForensicBlob(
            data=data,
            mime_type="image/jpeg",
            byte_count=len(data),
            sha256="0" * 64,
        )
    with pytest.raises(ValueError, match="image/jpeg"):
        ForensicBlob(
            data=data,
            mime_type="image/png",
            byte_count=len(data),
            sha256=digest,
        )

    fake = b"\xff\xd8not-a-real-jpeg\xff\xd9"
    invalid = ForensicBlob(
        data=fake,
        mime_type="image/jpeg",
        byte_count=len(fake),
        sha256=hashlib.sha256(fake).hexdigest(),
    )
    failed = storage.write_blob(run_id=run_id, blob=invalid)
    assert failed.degraded is True
    assert failed.failure_code == "invalid_jpeg"
    assert not (blob_path.parent / f"{invalid.sha256}.jpg").exists()


def test_identical_content_addressed_blob_is_idempotent_after_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    user_id, project_id, run_id = _ids()
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    data = _jpeg_bytes()
    digest = hashlib.sha256(data).hexdigest()
    first = storage.write_blob(
        run_id=run_id,
        blob=ForensicBlob(
            data=data,
            mime_type="image/jpeg",
            byte_count=len(data),
            sha256=digest,
            created_at=datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc),
        ),
    )
    assert first.entry is not None

    restarted = GenerationForensicStorage.open(_config(root), writable=True)
    second = restarted.write_blob(
        run_id=run_id,
        blob=ForensicBlob(
            data=data,
            mime_type="image/jpeg",
            byte_count=len(data),
            sha256=digest,
            created_at=datetime(2026, 7, 30, 10, 1, tzinfo=timezone.utc),
        ),
    )

    assert second.written is False
    assert second.entry == first.entry
    assert second.manifest is not None
    assert second.manifest.entries == (first.entry,)


def test_jpeg_with_bomb_sized_declared_dimensions_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    data = _jpeg_with_declared_dimensions(10_000, 10_000)
    result = storage.write_blob(
        run_id=run_id,
        blob=ForensicBlob(
            data=data,
            mime_type="image/jpeg",
            byte_count=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        ),
    )

    assert result.degraded is True
    assert result.failure_code == "invalid_jpeg"


def test_oversized_entry_and_blob_fail_bounded_without_partial_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from builder_lab.forensics import storage as storage_module

    root = tmp_path / "root"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)

    monkeypatch.setattr(storage_module, "MAX_FORENSIC_ENTRY_BYTES", 24)
    entry_result = storage.write_json_entry(
        run_id=run_id,
        relative_path="events/00000001-run.created.json",
        kind="event",
        payload={"value": "x" * 100},
    )
    assert entry_result.degraded is True
    assert entry_result.failure_code == "entry_too_large"

    data = _jpeg_bytes()
    monkeypatch.setattr(storage_module, "MAX_FORENSIC_BLOB_BYTES", len(data) - 1)
    blob_result = storage.write_blob(
        run_id=run_id,
        blob=ForensicBlob(
            data=data,
            mime_type="image/jpeg",
            byte_count=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        ),
    )
    assert blob_result.degraded is True
    assert blob_result.failure_code == "blob_too_large"
    assert not list(run_dir.rglob("*.tmp"))
    assert not (run_dir / "events" / "00000001-run.created.json").exists()
    assert not (run_dir / "blobs" / f"{hashlib.sha256(data).hexdigest()}.jpg").exists()


def test_usage_counts_bytes_only_in_valid_marked_run_directories(tmp_path: Path) -> None:
    root = tmp_path / "root"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    storage.write_event(
        run_id=run_id,
        sequence=1,
        event_type="run.created",
        payload={"safe": True},
    )
    valid_run = root / "runs" / run_id.hex[:2] / str(run_id)

    foreign = root / "runs" / "ff" / str(uuid4())
    foreign.mkdir(parents=True)
    (foreign / "large.bin").write_bytes(b"x" * 50_000)
    malformed = root / "runs" / "ee" / str(uuid4())
    malformed.mkdir(parents=True)
    (malformed / storage.RUN_MARKER_NAME).write_text(
        json.dumps({"kind": "foreign"}), encoding="utf-8"
    )
    (malformed / "large.bin").write_bytes(b"x" * 70_000)

    assert storage.usage() == _tree_size(valid_run)


def test_usage_stream_is_bounded_instead_of_materializing_unlimited_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from builder_lab.forensics import storage as storage_module

    root = tmp_path / "root"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)
    for index in range(5):
        orphan = run_dir / f"orphan-{index}.bin"
        orphan.write_bytes(b"x")
        orphan.chmod(0o600)
    monkeypatch.setattr(storage_module, "MAX_USAGE_SCAN_ENTRIES", 4)

    with pytest.raises(ValueError, match="scan limit"):
        storage.usage()


@pytest.mark.skipif(
    os.name == "nt" or not Path("/proc/self/fd").is_dir(),
    reason="Linux deep directory and RLIMIT_NOFILE contract",
)
def test_usage_walk_uses_an_explicit_stack_for_deep_valid_tree(tmp_path: Path) -> None:
    import resource

    root = tmp_path / "root"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)
    expected = sum(
        path.stat().st_size
        for path in (
            run_dir / storage.RUN_MARKER_NAME,
            run_dir / "manifest.json",
        )
    )
    current = run_dir
    created_directories: list[Path] = []
    for _ in range(1_050):
        current = current / "d"
        current.mkdir(mode=0o700)
        current.chmod(0o700)
        created_directories.append(current)

    previous_soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    open_descriptors = len(tuple(Path("/proc/self/fd").iterdir()))
    constrained_soft = max(open_descriptors + 16, 64)
    if previous_soft != resource.RLIM_INFINITY:
        constrained_soft = min(constrained_soft, previous_soft)
    if constrained_soft <= open_descriptors + 8:
        pytest.skip("RLIMIT_NOFILE has insufficient headroom for the regression")
    if hard != resource.RLIM_INFINITY and constrained_soft > hard:
        pytest.skip("RLIMIT_NOFILE hard limit is too small for the regression")
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (constrained_soft, hard))
        try:
            assert storage.usage() == expected
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, (previous_soft, hard))
    finally:
        for directory in reversed(created_directories):
            directory.rmdir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership/mode contract")
def test_readonly_restart_rejects_weakened_private_permissions(tmp_path: Path) -> None:
    root = tmp_path / "root"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)
    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)
    (run_dir / "manifest.json").chmod(0o644)

    restarted = GenerationForensicStorage.open(_config(root), writable=False)
    with pytest.raises(ValueError, match="permission|mode"):
        restarted.load_manifest(run_id)


def test_value_objects_require_timezone_aware_timestamps(tmp_path: Path) -> None:
    storage = GenerationForensicStorage.open(_config(tmp_path / "root"), writable=True)
    user_id, project_id, run_id = _ids()
    with pytest.raises(ValueError, match="timezone"):
        storage.initialize_run(
            user_id=user_id,
            project_id=project_id,
            run_id=run_id,
            created_at=datetime(2026, 7, 30),
        )


def test_manifest_value_objects_reject_coerced_boolean_and_string_numbers() -> None:
    entry = {
        "relative_path": "events/00000001-run.created.json",
        "kind": "event",
        "byte_count": True,
        "sha256": "0" * 64,
        "created_at": "2026-07-30T00:00:00+00:00",
    }
    with pytest.raises(ValueError, match="byte_count"):
        ForensicEntry.from_dict(entry)

    manifest = {
        "user_id": 999,
        "project_id": str(uuid4()),
        "run_id": str(uuid4()),
        "created_at": "2026-07-30T00:00:00+00:00",
        "entries": [],
        "kind": "kaigo-generation-forensics-manifest",
        "schema_version": "1",
    }
    with pytest.raises(ValueError, match="schema_version"):
        ForensicManifest.from_dict(manifest)

    manifest["schema_version"] = 1
    for invalid_user_id in (True, "999", 0, -1):
        manifest["user_id"] = invalid_user_id
        with pytest.raises(ValueError, match="user_id"):
            ForensicManifest.from_dict(manifest)


def test_restart_rejects_boolean_user_id_in_marker_only_run(tmp_path: Path) -> None:
    root = tmp_path / "root"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(
        user_id=user_id,
        project_id=project_id,
        run_id=run_id,
    )
    run_dir = root / "runs" / run_id.hex[:2] / str(run_id)
    marker_path = run_dir / GenerationForensicStorage.RUN_MARKER_NAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["user_id"] = True
    marker_path.write_text(
        json.dumps(
            marker,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (run_dir / "manifest.json").unlink()

    with pytest.raises(ValueError, match="marker"):
        storage.initialize_run(
            user_id=1,
            project_id=project_id,
            run_id=run_id,
        )


def test_event_sequence_is_bounded_to_the_eight_digit_path_contract(
    tmp_path: Path,
) -> None:
    storage = GenerationForensicStorage.open(_config(tmp_path / "root"), writable=True)
    user_id, project_id, run_id = _ids()
    storage.initialize_run(user_id=user_id, project_id=project_id, run_id=run_id)

    with pytest.raises(ValueError, match="sequence"):
        storage.write_event(
            run_id=run_id,
            sequence=100_000_000,
            event_type="run.created",
            payload={},
        )
