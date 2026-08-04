from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID


_CONVERSATION_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EVENT_NAMES = frozenset(
    {
        "turn.started",
        "turn.completed",
        "turn.failed",
        "thread.archived",
        "thread.archive_failed",
    }
)


@dataclass(frozen=True, slots=True)
class BridgeThread:
    run_id: str
    conversation_key: str
    thread_id: str
    model: str
    lifecycle: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class BridgeEvent:
    event: str
    run_id: str
    conversation_key: str
    thread_id: str | None
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    duration_ms: int = 0
    error_code: str | None = None
    timestamp: str | None = None

    def __post_init__(self) -> None:
        if self.event not in _EVENT_NAMES:
            raise ValueError("event is not allowlisted")
        _validate_uuid("run_id", self.run_id)
        _validate_conversation_key(self.conversation_key)
        if self.thread_id is not None:
            _validate_uuid("thread_id", self.thread_id)
        _validate_model(self.model)
        for name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "duration_ms",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.error_code is not None and _ERROR_CODE.fullmatch(
            self.error_code
        ) is None:
            raise ValueError("error_code must be a safe machine-readable label")

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp or _utc_now()
        return payload


class BridgeStateStore:
    """Small durable mapping and operational log owned by Kaigo, not Codex."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "state.sqlite3"
        self.event_log_path = self.root / "events.jsonl"
        self._event_lock = threading.Lock()
        self._initialize()

    def bind_thread(
        self,
        *,
        run_id: str,
        conversation_key: str,
        thread_id: str,
        model: str,
    ) -> BridgeThread:
        _validate_uuid("run_id", run_id)
        _validate_conversation_key(conversation_key)
        _validate_uuid("thread_id", thread_id)
        _validate_model(model)
        now = _utc_now()
        with self._connect() as database:
            try:
                database.execute(
                    """
                    INSERT INTO bridge_threads (
                        run_id, conversation_key, thread_id, model,
                        lifecycle, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (run_id, conversation_key, thread_id, model, now, now),
                )
            except sqlite3.IntegrityError as error:
                existing = self._fetch_thread(
                    database,
                    run_id=run_id,
                    conversation_key=conversation_key,
                    active_only=False,
                )
                if (
                    existing is not None
                    and existing.lifecycle == "active"
                    and existing.thread_id == thread_id
                    and existing.model == model
                ):
                    return existing
                raise ValueError("conversation is already bound to another thread") from error
        stored = self.get_active_thread(
            run_id=run_id,
            conversation_key=conversation_key,
        )
        assert stored is not None
        return stored

    def get_active_thread(
        self,
        *,
        run_id: str,
        conversation_key: str,
    ) -> BridgeThread | None:
        _validate_uuid("run_id", run_id)
        _validate_conversation_key(conversation_key)
        with self._connect() as database:
            return self._fetch_thread(
                database,
                run_id=run_id,
                conversation_key=conversation_key,
                active_only=True,
            )

    def list_active_threads(self, run_id: str) -> tuple[BridgeThread, ...]:
        _validate_uuid("run_id", run_id)
        with self._connect() as database:
            rows = database.execute(
                """
                SELECT run_id, conversation_key, thread_id, model,
                       lifecycle, created_at, updated_at
                FROM bridge_threads
                WHERE run_id = ? AND lifecycle = 'active'
                ORDER BY created_at, conversation_key
                """,
                (run_id,),
            ).fetchall()
        return tuple(_thread_from_row(row) for row in rows)

    def mark_archived(self, *, run_id: str, conversation_key: str) -> None:
        _validate_uuid("run_id", run_id)
        _validate_conversation_key(conversation_key)
        with self._connect() as database:
            cursor = database.execute(
                """
                UPDATE bridge_threads
                SET lifecycle = 'archived', updated_at = ?
                WHERE run_id = ? AND conversation_key = ?
                  AND lifecycle = 'active'
                """,
                (_utc_now(), run_id, conversation_key),
            )
        if cursor.rowcount != 1:
            raise KeyError("active bridge thread was not found")

    def append_event(self, event: BridgeEvent) -> None:
        line = json.dumps(
            event.to_payload(),
            ensure_ascii=True,
            separators=(",", ":"),
        ) + "\n"
        with self._event_lock:
            descriptor = os.open(
                self.event_log_path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(descriptor, line.encode("utf-8"))
            finally:
                os.close(descriptor)

    def _initialize(self) -> None:
        with self._connect() as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS bridge_threads (
                    run_id TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    lifecycle TEXT NOT NULL CHECK (
                        lifecycle IN ('active', 'archived')
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, conversation_key),
                    UNIQUE (thread_id)
                )
                """
            )
        try:
            os.chmod(self.database_path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=5)

    @staticmethod
    def _fetch_thread(
        database: sqlite3.Connection,
        *,
        run_id: str,
        conversation_key: str,
        active_only: bool,
    ) -> BridgeThread | None:
        lifecycle_clause = " AND lifecycle = 'active'" if active_only else ""
        row = database.execute(
            f"""
            SELECT run_id, conversation_key, thread_id, model,
                   lifecycle, created_at, updated_at
            FROM bridge_threads
            WHERE run_id = ? AND conversation_key = ?{lifecycle_clause}
            """,
            (run_id, conversation_key),
        ).fetchone()
        return _thread_from_row(row) if row is not None else None


def _thread_from_row(row: tuple[object, ...]) -> BridgeThread:
    return BridgeThread(
        run_id=str(row[0]),
        conversation_key=str(row[1]),
        thread_id=str(row[2]),
        model=str(row[3]),
        lifecycle=str(row[4]),
        created_at=str(row[5]),
        updated_at=str(row[6]),
    )


def _validate_uuid(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"{name} must be a UUID string") from error
    if str(parsed) != value.lower():
        raise ValueError(f"{name} must use canonical UUID form")


def _validate_conversation_key(value: str) -> None:
    if not isinstance(value, str) or _CONVERSATION_KEY.fullmatch(value) is None:
        raise ValueError("conversation_key is invalid")


def _validate_model(value: str) -> None:
    if not isinstance(value, str) or _MODEL_NAME.fullmatch(value) is None:
        raise ValueError("model is invalid")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
