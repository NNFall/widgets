from __future__ import annotations

import logging
from typing import Any

import aiosqlite

from .config import settings

logger = logging.getLogger(__name__)

DATABASE_URL = settings.DATABASE_URL


async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    column_names = {row["name"] for row in rows}
    if column not in column_names:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        await db.commit()


async def init_db() -> None:
    """Initialise the lightweight message store used for conversation history."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                widget_id INTEGER,
                widget_slug TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                first_seen DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()

        await _ensure_column(db, "messages", "widget_id", "INTEGER")
        await _ensure_column(db, "messages", "widget_slug", "TEXT")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_widget ON messages(widget_slug, timestamp)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, timestamp)")
        await db.commit()


async def add_message(
    user_id: int,
    role: str,
    content: str,
    *,
    widget_id: int | None = None,
    widget_slug: str | None = None,
) -> None:
    """Persist a single message for conversation history."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            "INSERT INTO messages (user_id, widget_id, widget_slug, role, content) VALUES (?, ?, ?, ?, ?)",
            (user_id, widget_id, widget_slug, role, content),
        )
        await db.commit()


async def get_history(
    user_id: int,
    limit: int = 10,
    *,
    widget_id: int | None = None,
    widget_slug: str | None = None,
) -> list[dict[str, Any]]:
    """Return the latest messages for the given user (optionally filtered by widget)."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT role, content FROM messages WHERE user_id = ?"
        params: list[Any] = [user_id]

        if widget_id is not None:
            query += " AND widget_id = ?"
            params.append(widget_id)
        elif widget_slug is not None:
            query += " AND widget_slug = ?"
            params.append(widget_slug)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [
            {"role": row["role"], "content": row["content"]}
            for row in reversed(rows)
        ]


async def get_recent_widget_messages(
    *,
    widget_id: int | None = None,
    widget_slug: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return the latest messages associated with a widget across all users."""
    if widget_id is None and widget_slug is None:
        raise ValueError("widget_id or widget_slug must be provided")

    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT user_id, role, content, timestamp FROM messages"
        conditions: list[str] = []
        params: list[Any] = []

        if widget_id is not None:
            conditions.append("widget_id = ?")
            params.append(widget_id)
        if widget_slug is not None:
            conditions.append("widget_slug = ?")
            params.append(widget_slug)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [
            {
                "user_id": row["user_id"],
                "role": row["role"],
                "content": row["content"],
                "timestamp": row["timestamp"],
            }
            for row in rows
        ]


async def add_or_get_user(user_id: int, username: str | None = None) -> dict[str, Any]:
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()
        if user is None:
            await db.execute(
                "INSERT INTO users (user_id, username) VALUES (?, ?)",
                (user_id, username),
            )
            await db.commit()
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = await cursor.fetchone()
        return dict(user)


async def set_user_active_status(user_id: int, is_active: bool) -> None:
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute(
            "UPDATE users SET is_active = ? WHERE user_id = ?",
            (is_active, user_id),
        )
        await db.commit()
        logger.info("User %s is_active=%s", user_id, is_active)


async def get_user_by_username(username: str) -> dict[str, Any] | None:
    clean_username = username.lstrip('@')
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE username = ?", (clean_username,))
        user = await cursor.fetchone()
        return dict(user) if user else None


async def delete_history_for_user(user_id: int) -> None:
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        await db.commit()
        logger.info("Cleared message history for user %s", user_id)


async def does_user_exist(user_id: int) -> bool:
    async with aiosqlite.connect(DATABASE_URL) as db:
        cursor = await db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        return await cursor.fetchone() is not None


async def get_recent_widget_conversations(
    *,
    widget_id: int | None = None,
    widget_slug: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return summary rows for the latest conversations within a widget."""
    if widget_id is None and widget_slug is None:
        raise ValueError("widget_id or widget_slug must be provided")

    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        conditions: list[str] = []
        params: list[Any] = []
        if widget_id is not None:
            conditions.append("widget_id = ?")
            params.append(widget_id)
        if widget_slug is not None:
            conditions.append("widget_slug = ?")
            params.append(widget_slug)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = (
            "SELECT user_id, MAX(timestamp) AS last_ts, COUNT(*) AS message_count "
            "FROM messages"
            + where_clause
            + " GROUP BY user_id ORDER BY last_ts DESC LIMIT ?"
        )
        summary_params = list(params) + [limit]
        cursor = await db.execute(query, summary_params)
        rows = await cursor.fetchall()

        conversations: list[dict[str, Any]] = []
        for row in rows:
            user_id = row["user_id"]
            last_ts = row["last_ts"]
            message_count = row["message_count"]

            detail_where = "user_id = ?"
            detail_params = [user_id]
            if widget_id is not None:
                detail_where += " AND widget_id = ?"
                detail_params.append(widget_id)
            if widget_slug is not None:
                detail_where += " AND widget_slug = ?"
                detail_params.append(widget_slug)

            detail_query = (
                "SELECT role, content FROM messages WHERE "
                + detail_where
                + " ORDER BY timestamp DESC LIMIT 1"
            )
            detail_cursor = await db.execute(detail_query, detail_params)
            detail_row = await detail_cursor.fetchone()
            last_role = detail_row["role"] if detail_row else "assistant"
            last_content = detail_row["content"] if detail_row else ""

            conversations.append(
                {
                    "user_id": user_id,
                    "last_timestamp": last_ts,
                    "message_count": message_count,
                    "last_role": last_role,
                    "last_message": last_content,
                }
            )

        return conversations


async def get_conversation_messages(
    user_id: int,
    *,
    widget_id: int | None = None,
    widget_slug: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch a single conversation (chronological order)."""
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT role, content, timestamp FROM messages WHERE user_id = ?"
        params: list[Any] = [user_id]

        if widget_id is not None:
            query += " AND widget_id = ?"
            params.append(widget_id)
        if widget_slug is not None:
            query += " AND widget_slug = ?"
            params.append(widget_slug)

        query += " ORDER BY timestamp ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "timestamp": row["timestamp"],
            }
            for row in rows
        ]
