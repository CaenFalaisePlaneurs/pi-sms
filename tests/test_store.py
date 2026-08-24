"""Tests for pi_sms.reply.store."""

from pathlib import Path

from pi_sms.reply.store import (
    STATUS_PENDING,
    STATUS_SENT,
    STATUS_SENT_UNCONFIRMED,
    get_board_id,
    get_last_action_id,
    get_meta,
    get_reply,
    insert_reply,
    list_retryable,
    mark_failure_notice_posted,
    open_store,
    set_board_id,
    set_last_action_id,
    update_reply_status,
)


def test_open_store_creates_schema_and_restricts_permissions(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "reply.sqlite"

    conn = open_store(str(path))
    try:
        assert get_meta(conn, "schema_version") == "1"
        assert path.exists()
        assert (path.stat().st_mode & 0o777) == 0o600
    finally:
        conn.close()


def test_board_id_and_cursor_roundtrip(tmp_path: Path) -> None:
    conn = open_store(str(tmp_path / "reply.sqlite"))
    try:
        assert get_board_id(conn) is None
        assert get_last_action_id(conn) is None

        set_board_id(conn, "board-1")
        set_last_action_id(conn, "act-9")

        assert get_board_id(conn) == "board-1"
        assert get_last_action_id(conn) == "act-9"
    finally:
        conn.close()


def test_insert_reply_is_idempotent(tmp_path: Path) -> None:
    conn = open_store(str(tmp_path / "reply.sqlite"))
    try:
        first = insert_reply(
            conn,
            trigger_comment_id="trig-1",
            card_id="card-1",
            card_name="SMS from +33612345678",
            body="hello",
            status=STATUS_PENDING,
        )
        second = insert_reply(
            conn,
            trigger_comment_id="trig-1",
            card_id="card-other",
            card_name="other",
            body="ignored",
            status=STATUS_SENT,
        )

        assert first is True
        assert second is False
        record = get_reply(conn, "trig-1")
        assert record is not None
        assert record.status == STATUS_PENDING
        assert record.body == "hello"
        assert record.card_id == "card-1"
    finally:
        conn.close()


def test_list_retryable_includes_pending_and_unconfirmed_only(tmp_path: Path) -> None:
    conn = open_store(str(tmp_path / "reply.sqlite"))
    try:
        insert_reply(
            conn,
            trigger_comment_id="pending-1",
            card_id="c1",
            card_name="n1",
            body="a",
            status=STATUS_PENDING,
        )
        insert_reply(
            conn,
            trigger_comment_id="unconfirmed-1",
            card_id="c2",
            card_name="n2",
            body="b",
            status=STATUS_SENT_UNCONFIRMED,
        )
        insert_reply(
            conn,
            trigger_comment_id="sent-1",
            card_id="c3",
            card_name="n3",
            body="c",
            status=STATUS_SENT,
        )

        retryable_ids = [row.trigger_comment_id for row in list_retryable(conn)]
        assert retryable_ids == ["pending-1", "unconfirmed-1"]
    finally:
        conn.close()


def test_status_and_failure_notice_transitions(tmp_path: Path) -> None:
    conn = open_store(str(tmp_path / "reply.sqlite"))
    try:
        insert_reply(
            conn,
            trigger_comment_id="trig-1",
            card_id="c1",
            card_name="n1",
            body="hello",
            status=STATUS_PENDING,
        )
        update_reply_status(conn, "trig-1", STATUS_SENT_UNCONFIRMED)
        mark_failure_notice_posted(conn, "trig-1")

        record = get_reply(conn, "trig-1")
        assert record is not None
        assert record.status == STATUS_SENT_UNCONFIRMED
        assert record.failure_notice_posted is True

        update_reply_status(conn, "trig-1", STATUS_SENT)
        record = get_reply(conn, "trig-1")
        assert record is not None
        assert record.status == STATUS_SENT
        assert all(row.trigger_comment_id != "trig-1" for row in list_retryable(conn))
    finally:
        conn.close()
