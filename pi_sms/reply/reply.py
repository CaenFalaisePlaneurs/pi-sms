"""Trello-comment-driven SMS reply orchestration.

Each poll retries unfinished SQLite rows, then either bootstraps once (full
scan of open cards, no cursor yet) or fetches new board `commentCard` actions
since the persisted cursor. Trigger comments are sent via the modem and
status is recorded as a new Trello comment (Trello only lets the original
author edit a comment, and a reply may be written by any team member).
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime

import httpx

from ..core.config import Config
from ..core.debug import debug_print
from ..modem.hilink import HilinkClient
from ..modem.sms import find_replyable_phone
from ..trello.trello import (
    TrelloComment,
    TrelloResult,
    fetch_emoji_map,
    get_card_list_and_name,
    get_latest_board_comment_id,
    get_list_board_id,
    list_board_comments_since,
    list_card_comments,
    list_open_cards,
    post_comment,
)
from .store import (
    STATUS_PENDING,
    STATUS_SENT,
    STATUS_SENT_UNCONFIRMED,
    ReplyRecord,
    get_board_id,
    get_last_action_id,
    get_reply,
    insert_reply,
    list_retryable,
    mark_failure_notice_posted,
    open_store,
    set_board_id,
    set_last_action_id,
    update_reply_status,
)
from .text import (
    build_failure_notice,
    build_sent_confirmation,
    contains_emoji_shortcode,
    has_failure_notice,
    is_reply_already_sent,
    parse_reply,
    resolve_emoji_shortcodes,
)

# Posting a new status comment is always permitted for the daemon's own
# token, so failures here should be rare and transient; retrying a few times
# with backoff narrows the window where an SMS sends successfully but the
# confirmation never gets recorded. Non-transient failures (see _is_retryable)
# give up after a single attempt instead of spending the full backoff
# schedule, so one bad reply can't stretch a poll cycle past
# `poll_interval_seconds` and overlap with the next one.
_POST_COMMENT_ATTEMPTS = 5
_BASE_RETRY_DELAY_SECONDS = 2
_MAX_RETRY_DELAY_SECONDS = 15


async def poll_and_send_replies(
    config: Config,
    modem: HilinkClient,
    is_running_ref: dict[str, bool],
    client: httpx.AsyncClient | None = None,
    emoji_cache_ref: dict[str, dict[str, str]] | None = None,
) -> None:
    """Poll Trello comments for reply triggers and send matching SMS.

    Args:
        config: Configuration object
        modem: HiLink modem client
        is_running_ref: Dictionary with 'value' key to prevent concurrent polls
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.
        emoji_cache_ref: Dictionary caching the Trello shortcode-to-emoji map
            under the "map" key across polls; when not provided, a fresh
            (per-poll) cache is used, so the map is refetched every call.
    """
    if is_running_ref.get("value", False):
        debug_print("Reply poll skipped: already running (previous poll still in progress)")
        return

    if emoji_cache_ref is None:
        emoji_cache_ref = {}

    is_running_ref["value"] = True
    try:
        if client is not None:
            await _poll_and_send_replies(config, modem, client, emoji_cache_ref)
        else:
            async with httpx.AsyncClient() as new_client:
                await _poll_and_send_replies(config, modem, new_client, emoji_cache_ref)
    finally:
        is_running_ref["value"] = False


async def _poll_and_send_replies(
    config: Config,
    modem: HilinkClient,
    client: httpx.AsyncClient,
    emoji_cache_ref: dict[str, dict[str, str]],
) -> None:
    conn = open_store(config.reply.sqlite_path)
    try:
        await _retry_unfinished(config, modem, client, conn, emoji_cache_ref)
        await _ingest_new_comments(config, modem, client, conn, emoji_cache_ref)
    finally:
        conn.close()


async def _retry_unfinished(
    config: Config,
    modem: HilinkClient,
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    emoji_cache_ref: dict[str, dict[str, str]],
) -> None:
    for record in list_retryable(conn):
        await _attempt_send(config, modem, client, conn, record, emoji_cache_ref)


async def _ingest_new_comments(
    config: Config,
    modem: HilinkClient,
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    emoji_cache_ref: dict[str, dict[str, str]],
) -> None:
    board_id = await _resolve_board_id(config, client, conn)
    if board_id is None:
        return

    if get_last_action_id(conn) is None:
        await _bootstrap(config, modem, client, conn, board_id, emoji_cache_ref)
        return

    await _incremental(config, modem, client, conn, board_id, emoji_cache_ref)


async def _resolve_board_id(
    config: Config, client: httpx.AsyncClient, conn: sqlite3.Connection
) -> str | None:
    cached = get_board_id(conn)
    if cached:
        return cached

    board_id, error = await get_list_board_id(config.trello, client)
    if error is not None or board_id is None:
        debug_print(f"Reply poll: failed to resolve Trello board id: {error}")
        return None
    set_board_id(conn, board_id)
    return board_id


async def _bootstrap(
    config: Config,
    modem: HilinkClient,
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    board_id: str,
    emoji_cache_ref: dict[str, dict[str, str]],
) -> None:
    cursor, cursor_error = await get_latest_board_comment_id(config.trello, board_id, client)
    if cursor_error is not None:
        debug_print(f"Reply poll: failed to snapshot board comment cursor: {cursor_error}")
        return

    cards, error = await list_open_cards(config.trello, client)
    if error is not None:
        debug_print(f"Reply poll: failed to list Trello cards: {error}")
        return

    scan_failed = False
    for card in cards:
        comments, comments_error = await list_card_comments(config.trello, card.id, client)
        if comments_error is not None:
            debug_print(f"Reply poll: failed to list comments on card {card.id}: {comments_error}")
            scan_failed = True
            continue

        for comment in comments:
            await _ingest_trigger_from_card_comments(
                config,
                modem,
                client,
                conn,
                card.id,
                card.name,
                comment,
                comments,
                emoji_cache_ref,
            )

    # Leave the cursor unset so the next poll retries bootstrap for cards whose
    # comment listing failed; already-recorded triggers are skipped via PK.
    if not scan_failed:
        set_last_action_id(conn, cursor if cursor is not None else _utc_now())


async def _incremental(
    config: Config,
    modem: HilinkClient,
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    board_id: str,
    emoji_cache_ref: dict[str, dict[str, str]],
) -> None:
    since = get_last_action_id(conn)
    if since is None:
        return

    comments, error = await list_board_comments_since(config.trello, board_id, since, client)
    if error is not None:
        debug_print(f"Reply poll: failed to list new board comments: {error}")
        return

    newest_id = since
    for comment in comments:
        newest_id = comment.id
        await _ingest_board_comment(config, modem, client, conn, comment, emoji_cache_ref)

    if newest_id != since:
        set_last_action_id(conn, newest_id)


async def _ingest_trigger_from_card_comments(
    config: Config,
    modem: HilinkClient,
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    card_id: str,
    card_name: str,
    comment: TrelloComment,
    comments: list[TrelloComment],
    emoji_cache_ref: dict[str, dict[str, str]],
) -> None:
    parsed = parse_reply(comment.text, config.reply.trigger, config.reply.case_insensitive)
    if parsed is None:
        return

    if get_reply(conn, comment.id) is not None:
        return

    if is_reply_already_sent(comments, comment.id, config.reply):
        insert_reply(
            conn,
            trigger_comment_id=comment.id,
            card_id=card_id,
            card_name=card_name,
            body=parsed.body,
            status=STATUS_SENT,
        )
        return

    inserted = insert_reply(
        conn,
        trigger_comment_id=comment.id,
        card_id=card_id,
        card_name=card_name,
        body=parsed.body,
        status=STATUS_PENDING,
        failure_notice_posted=has_failure_notice(comments, comment.id, config.reply),
    )
    if not inserted:
        return

    record = get_reply(conn, comment.id)
    if record is None:
        return
    await _attempt_send(config, modem, client, conn, record, emoji_cache_ref)


async def _ingest_board_comment(
    config: Config,
    modem: HilinkClient,
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    comment: TrelloComment,
    emoji_cache_ref: dict[str, dict[str, str]],
) -> None:
    parsed = parse_reply(comment.text, config.reply.trigger, config.reply.case_insensitive)
    if parsed is None:
        return

    if get_reply(conn, comment.id) is not None:
        return

    card_id, card_name, on_list = await _resolve_sms_list_card(config, client, comment)
    if not on_list or card_id is None or card_name is None:
        return

    inserted = insert_reply(
        conn,
        trigger_comment_id=comment.id,
        card_id=card_id,
        card_name=card_name,
        body=parsed.body,
        status=STATUS_PENDING,
    )
    if not inserted:
        return

    record = get_reply(conn, comment.id)
    if record is None:
        return
    await _attempt_send(config, modem, client, conn, record, emoji_cache_ref)


async def _resolve_sms_list_card(
    config: Config, client: httpx.AsyncClient, comment: TrelloComment
) -> tuple[str | None, str | None, bool]:
    """Return (card_id, card_name, on_sms_list) for a board comment."""
    list_id = comment.list_id
    card_id = comment.card_id
    card_name = comment.card_name

    if list_id:
        return card_id or None, card_name or None, list_id == config.trello.list_id

    if not card_id:
        return None, None, False

    location, error = await get_card_list_and_name(config.trello, card_id, client)
    if error is not None or location is None:
        debug_print(f"Reply poll: failed to resolve list for card {card_id}: {error}")
        return None, None, False

    fetched_list_id, fetched_name = location
    return card_id, fetched_name or card_name, fetched_list_id == config.trello.list_id


async def _attempt_send(
    config: Config,
    modem: HilinkClient,
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    record: ReplyRecord,
    emoji_cache_ref: dict[str, dict[str, str]],
) -> None:
    if record.status == STATUS_SENT:
        return

    phone = find_replyable_phone(record.card_name)
    if phone is None:
        debug_print(f"Reply poll: no replyable phone found in card name '{record.card_name}'")
        return

    if record.status == STATUS_SENT_UNCONFIRMED:
        await _post_sent_confirmation(config, client, conn, record, phone)
        return

    body = await _resolve_body(record.body, client, emoji_cache_ref)
    result = await modem.send_sms(phone, body)
    if result.success:
        update_reply_status(conn, record.trigger_comment_id, STATUS_SENT_UNCONFIRMED)
        await _post_sent_confirmation(
            config,
            client,
            conn,
            ReplyRecord(
                trigger_comment_id=record.trigger_comment_id,
                card_id=record.card_id,
                card_name=record.card_name,
                body=record.body,
                status=STATUS_SENT_UNCONFIRMED,
                failure_notice_posted=record.failure_notice_posted,
                created_at=record.created_at,
                updated_at=record.updated_at,
            ),
            phone,
        )
        return

    debug_print(f"Failed to send SMS reply to {phone}: {result.error}")
    if record.failure_notice_posted:
        return
    notice = build_failure_notice(record.trigger_comment_id, config.reply)
    post_result = await _post_status_comment_with_retries(config, record.card_id, notice, client)
    if post_result.success:
        mark_failure_notice_posted(conn, record.trigger_comment_id)


async def _post_sent_confirmation(
    config: Config,
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    record: ReplyRecord,
    phone: str,
) -> None:
    confirmation = build_sent_confirmation(
        record.trigger_comment_id, config.reply, datetime.now().astimezone()
    )
    post_result = await _post_status_comment_with_retries(
        config, record.card_id, confirmation, client
    )
    if post_result.success:
        update_reply_status(conn, record.trigger_comment_id, STATUS_SENT)
        print(f"Sent SMS reply to {phone}")
        return
    print(
        f"Sent SMS reply to {phone} but failed to record sent confirmation after retries "
        f"(will retry confirmation next poll, without resending): {post_result.error}"
    )


async def _resolve_body(
    body: str, client: httpx.AsyncClient, emoji_cache_ref: dict[str, dict[str, str]]
) -> str:
    """Resolve Trello emoji shortcodes (e.g. `:heart:`) in a reply body into real characters."""
    if not contains_emoji_shortcode(body):
        return body

    emoji_map = emoji_cache_ref.get("map")
    if not emoji_map:
        emoji_map, error = await fetch_emoji_map(client)
        if error is not None:
            debug_print(f"Reply poll: failed to fetch Trello emoji map: {error}")
        if emoji_map:
            emoji_cache_ref["map"] = emoji_map

    return resolve_emoji_shortcodes(body, emoji_map or {})


async def _post_status_comment_with_retries(
    config: Config,
    card_id: str,
    text: str,
    client: httpx.AsyncClient,
) -> TrelloResult:
    """Post a status comment, retrying with backoff to reduce the odds of a transient failure.

    A 429 response's indicated cooldown (from `TrelloResult.retry_after_seconds`)
    is honored instead of the regular exponential backoff, since Trello tells
    us exactly how long to wait.
    """
    result = await post_comment(config.trello, card_id, text, client)
    attempt = 1
    while not result.success and _is_retryable(result) and attempt < _POST_COMMENT_ATTEMPTS:
        await asyncio.sleep(_retry_delay_seconds(result, attempt))
        result = await post_comment(config.trello, card_id, text, client)
        attempt += 1
    return result


def _is_retryable(result: TrelloResult) -> bool:
    """Return True if retrying result's failure could plausibly succeed.

    Network errors (no status code), 429s, and 5xx responses may be
    transient; other 4xx errors (bad request, auth, not found) will fail
    identically on every retry, so burning through the backoff schedule
    only delays giving up and risks the poll cycle running long enough to
    overlap with the next scheduled one.
    """
    if result.status_code is None:
        return True
    return result.status_code == 429 or result.status_code >= 500


def _retry_delay_seconds(result: TrelloResult, attempt: int) -> float:
    """Return how long to wait before the next retry, given the last failed attempt."""
    if result.retry_after_seconds is not None:
        return result.retry_after_seconds
    return float(min(_BASE_RETRY_DELAY_SECONDS * 2 ** (attempt - 1), _MAX_RETRY_DELAY_SECONDS))


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
