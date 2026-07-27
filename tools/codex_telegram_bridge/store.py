from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Job:
    id: int
    update_id: int
    chat_id: int
    user_id: int
    thread_id: str
    prompt: str
    status: str
    response: str | None
    error: str | None
    delivery_cursor: int


class BridgeStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA secure_delete=ON")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    update_id INTEGER NOT NULL UNIQUE,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    thread_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'delivery', 'done', 'failed')),
                    response TEXT,
                    error TEXT,
                    delivery_cursor INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS jobs_status_id_idx ON jobs(status, id);
                CREATE TABLE IF NOT EXISTS bindings (
                    chat_id INTEGER PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS processed_updates (
                    update_id INTEGER PRIMARY KEY,
                    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _job(row: sqlite3.Row | None) -> Job | None:
        if row is None:
            return None
        return Job(
            id=row["id"],
            update_id=row["update_id"],
            chat_id=row["chat_id"],
            user_id=row["user_id"],
            thread_id=row["thread_id"],
            prompt=row["prompt"],
            status=row["status"],
            response=row["response"],
            error=row["error"],
            delivery_cursor=row["delivery_cursor"],
        )

    def enqueue(self, update_id: int, chat_id: int, user_id: int, thread_id: str, prompt: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO jobs(update_id, chat_id, user_id, thread_id, prompt, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (update_id, chat_id, user_id, thread_id, prompt),
            )
            return cursor.rowcount == 1

    def has_update(self, update_id: int) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM jobs WHERE update_id = ?
                UNION ALL
                SELECT 1 FROM processed_updates WHERE update_id = ?
                LIMIT 1
                """,
                (update_id, update_id),
            ).fetchone()
            return row is not None

    def mark_update_processed(self, update_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO processed_updates(update_id) VALUES (?)",
                (update_id,),
            )

    def claim_next(self) -> Job | None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM jobs WHERE status = 'pending' ORDER BY id LIMIT 1"
                ).fetchone()
                if row is None:
                    self._connection.commit()
                    return None
                self._connection.execute(
                    "UPDATE jobs SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["id"],),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
            refreshed = dict(row)
            refreshed["status"] = "running"
            return Job(**{key: refreshed[key] for key in Job.__dataclass_fields__})

    def next_delivery(self) -> Job | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM jobs WHERE status = 'delivery' ORDER BY id LIMIT 1"
            ).fetchone()
            return self._job(row)

    def mark_for_delivery(self, job_id: int, response: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE jobs SET status = 'delivery', response = ?, error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (response, job_id),
            )

    def mark_done(self, job_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE jobs SET status = 'done', prompt = '', response = NULL, error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )

    def mark_chunk_delivered(self, job_id: int, cursor: int) -> None:
        if cursor < 0:
            raise ValueError("delivery cursor cannot be negative")
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE jobs SET delivery_cursor = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'delivery' AND delivery_cursor < ?
                """,
                (cursor, job_id, cursor),
            )

    def mark_failed(self, job_id: int, error: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE jobs SET status = 'failed', prompt = '', response = NULL, error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (error[:2000], job_id),
            )

    def recover_interrupted(self) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE jobs SET status = 'pending', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'running'
                """
            )
            return cursor.rowcount

    def prune_history(self, retention_days: int) -> tuple[int, int]:
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        threshold = f"-{retention_days} days"
        with self._lock:
            with self._connection:
                jobs = self._connection.execute(
                    """
                    DELETE FROM jobs
                    WHERE status IN ('done', 'failed')
                      AND updated_at < datetime('now', ?)
                    """,
                    (threshold,),
                ).rowcount
                updates = self._connection.execute(
                    "DELETE FROM processed_updates WHERE processed_at < datetime('now', ?)",
                    (threshold,),
                ).rowcount
            if jobs or updates:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._connection.execute("VACUUM")
            return jobs, updates

    def pending_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('pending', 'running', 'delivery')"
            ).fetchone()
            return int(row["count"])

    def ensure_binding(self, chat_id: int, thread_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO bindings(chat_id, thread_id) VALUES (?, ?)",
                (chat_id, thread_id),
            )
            return cursor.rowcount == 1

    def set_binding(self, chat_id: int, thread_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO bindings(chat_id, thread_id) VALUES (?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, thread_id),
            )

    def get_binding(self, chat_id: int) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT thread_id FROM bindings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            return None if row is None else str(row["thread_id"])

    def set_last_update_id(self, update_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO state(key, value) VALUES ('last_update_id', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(update_id),),
            )

    def get_last_update_id(self) -> int | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM state WHERE key = 'last_update_id'"
            ).fetchone()
            return None if row is None else int(row["value"])

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "BridgeStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
