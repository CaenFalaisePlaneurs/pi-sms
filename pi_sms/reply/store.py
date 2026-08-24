"""SQLite persistence for Trello-comment-driven SMS replies.

Stores the board comment cursor and per-trigger send state so reply polls
can process new comments incrementally instead of listing every card.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = "1"

STATUS_PENDING = "pending"
STATUS_SENT_UNCONFIRMED = "sent_unconfirmed"
STATUS_SENT = "sent"

META_SCHEMA_VERSION = "schema_version"
META_BOARD_ID = "board_id"
META_LAST_ACTION_ID = "last_action_id"


@dataclass(frozen=True)
class ReplyRecord:
    """Persisted send state for one Trello trigger comment."""

    trigger_comment_id: str
    card_id: str
    card_name: str
    body: str
    status: str
    failure_notice_posted: bool
    created_at: str
    updated_at: str


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def open_store(path: str) -> sqlite3.Connection:
    """Open (or create) the reply SQLite file and apply schema migrations.

    Creates parent directories as needed and restricts the file to owner
    read/write so the DB cannot be read by other accounts on the Pi.
    """
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    os.chmod(db_path, 0o600)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS replies (
            trigger_comment_id TEXT PRIMARY KEY,
            card_id TEXT NOT NULL,
            card_name TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL,
            failure_notice_posted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)
    conn.commit()
    if get_meta(conn, META_SCHEMA_VERSION) is None:
        set_meta(conn, META_SCHEMA_VERSION, SCHEMA_VERSION)


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Return a meta value, or None if the key has not been stored."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return str(row["value"])


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Insert or replace a meta key."""
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def get_board_id(conn: sqlite3.Connection) -> str | None:
    """Return the cached Trello board id, if bootstrap has resolved it."""
    return get_meta(conn, META_BOARD_ID)


def set_board_id(conn: sqlite3.Connection, board_id: str) -> None:
    """Cache the Trello board id that owns the configured SMS list."""
    set_meta(conn, META_BOARD_ID, board_id)


def get_last_action_id(conn: sqlite3.Connection) -> str | None:
    """Return the Trello action cursor, if incremental polling has started."""
    return get_meta(conn, META_LAST_ACTION_ID)


def set_last_action_id(conn: sqlite3.Connection, action_id: str) -> None:
    """Advance the Trello action cursor past the last processed comment."""
    set_meta(conn, META_LAST_ACTION_ID, action_id)


def get_reply(conn: sqlite3.Connection, trigger_comment_id: str) -> ReplyRecord | None:
    """Return the stored record for a trigger comment, if any."""
    row = conn.execute(
        """
        SELECT trigger_comment_id, card_id, card_name, body, status,
               failure_notice_posted, created_at, updated_at
        FROM replies
        WHERE trigger_comment_id = ?
        """,
        (trigger_comment_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def insert_reply(
    conn: sqlite3.Connection,
    *,
    trigger_comment_id: str,
    card_id: str,
    card_name: str,
    body: str,
    status: str,
    failure_notice_posted: bool = False,
) -> bool:
    """Insert a reply row. Returns False if the trigger id was already stored."""
    now = _utc_now()
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO replies (
            trigger_comment_id, card_id, card_name, body, status,
            failure_notice_posted, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trigger_comment_id,
            card_id,
            card_name,
            body,
            status,
            1 if failure_notice_posted else 0,
            now,
            now,
        ),
    )
    conn.commit()
    return cursor.rowcount == 1


def list_retryable(conn: sqlite3.Connection) -> list[ReplyRecord]:
    """Return replies that still need an SMS send or a Trello confirmation post."""
    rows = conn.execute(
        """
        SELECT trigger_comment_id, card_id, card_name, body, status,
               failure_notice_posted, created_at, updated_at
        FROM replies
        WHERE status IN (?, ?)
        ORDER BY created_at ASC
        """,
        (STATUS_PENDING, STATUS_SENT_UNCONFIRMED),
    ).fetchall()
    return [_row_to_record(row) for row in rows]


def update_reply_status(conn: sqlite3.Connection, trigger_comment_id: str, status: str) -> None:
    """Set a reply's status and bump updated_at."""
    conn.execute(
        "UPDATE replies SET status = ?, updated_at = ? WHERE trigger_comment_id = ?",
        (status, _utc_now(), trigger_comment_id),
    )
    conn.commit()


def mark_failure_notice_posted(conn: sqlite3.Connection, trigger_comment_id: str) -> None:
    """Record that the one-time Trello failure notice has been posted."""
    conn.execute(
        """
        UPDATE replies
        SET failure_notice_posted = 1, updated_at = ?
        WHERE trigger_comment_id = ?
        """,
        (_utc_now(), trigger_comment_id),
    )
    conn.commit()


def _row_to_record(row: sqlite3.Row) -> ReplyRecord:
    return ReplyRecord(
        trigger_comment_id=str(row["trigger_comment_id"]),
        card_id=str(row["card_id"]),
        card_name=str(row["card_name"]),
        body=str(row["body"]),
        status=str(row["status"]),
        failure_notice_posted=bool(row["failure_notice_posted"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
