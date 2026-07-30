from __future__ import annotations

import asyncio
import hashlib
import threading
from datetime import datetime, timedelta
from uuid import UUID
from weakref import WeakValueDictionary

from .models import require_aware


FORENSIC_RETENTION = timedelta(hours=120)
TERMINAL_RUN_STATES = frozenset({"completed", "failed", "cancelled"})
_PROCESS_RUN_LOCKS: WeakValueDictionary[UUID, asyncio.Lock] = WeakValueDictionary()
_PROCESS_RUN_LOCKS_GUARD = threading.Lock()


def canonical_storage_key(run_id: UUID) -> str:
    if not isinstance(run_id, UUID):
        raise ValueError("run_id must be a UUID")
    return f"runs/{run_id.hex[:2]}/{run_id}"


def retention_expires_at(finished_at: datetime) -> datetime:
    return require_aware(finished_at, name="finished_at") + FORENSIC_RETENTION


def is_ttl_cleanup_eligible(
    *,
    run_state: str,
    manifest_state: str,
    finished_at: datetime | None,
    expires_at: datetime | None,
    now: datetime,
) -> bool:
    now = require_aware(now, name="now")
    if (
        run_state not in TERMINAL_RUN_STATES
        or manifest_state != run_state
        or finished_at is None
        or expires_at is None
    ):
        return False
    expected = retention_expires_at(finished_at)
    stored = require_aware(expires_at, name="expires_at")
    return stored == expected and now >= expected


def _signed_advisory_key(namespace: bytes, run_id: UUID | None = None) -> int:
    material = namespace if run_id is None else namespace + b"\x00" + run_id.bytes
    digest = hashlib.sha256(material).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def run_advisory_key(run_id: UUID) -> int:
    if not isinstance(run_id, UUID):
        raise ValueError("run_id must be a UUID")
    return _signed_advisory_key(b"kaigo-forensics-v1", run_id)


def cleanup_advisory_key() -> int:
    return _signed_advisory_key(b"kaigo-forensics-cleanup-v1")


def process_run_lock(run_id: UUID) -> asyncio.Lock:
    if not isinstance(run_id, UUID):
        raise ValueError("run_id must be a UUID")
    with _PROCESS_RUN_LOCKS_GUARD:
        lock = _PROCESS_RUN_LOCKS.get(run_id)
        if lock is None:
            lock = asyncio.Lock()
            _PROCESS_RUN_LOCKS[run_id] = lock
        return lock
