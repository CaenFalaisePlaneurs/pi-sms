"""Trello-comment-driven SMS reply orchestration.

Each poll: list open cards in the configured Trello list, then for every
comment containing the reply trigger that has no matching sent confirmation
yet, extract the phone number from the card name and the SMS body from the
comment, send it via the modem, and post a new status comment (sent or
failure) referencing the trigger comment's ID. Status is recorded as a new
comment rather than an edit because Trello only lets the original author of
a comment edit it, and a reply may be written by any team member.
"""

import asyncio
from datetime import datetime

import httpx

from ..core.config import Config
from ..core.debug import debug_print
from ..modem.hilink import HilinkClient
from ..modem.sms import find_replyable_phone
from ..trello.trello import (
    TrelloComment,
    TrelloResult,
    fetch_emoji_map,
    list_card_comments,
    list_open_cards,
    post_comment,
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
# with backoff narrows the (much smaller than before) window where an SMS
# sends successfully but the confirmation never gets recorded. Non-transient
# failures (see _is_retryable) give up after a single attempt instead of
# spending the full backoff schedule, so one bad reply can't stretch a poll
# cycle past `poll_interval_seconds` and overlap with the next one.
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
    cards, error = await list_open_cards(config.trello, client)
    if error is not None:
        debug_print(f"Reply poll: failed to list Trello cards: {error}")
        return

    for card in cards:
        comments, comments_error = await list_card_comments(config.trello, card.id, client)
        if comments_error is not None:
            debug_print(f"Reply poll: failed to list comments on card {card.id}: {comments_error}")
            continue

        for comment in comments:
            await _process_comment(
                config, modem, client, card.name, card.id, comment, comments, emoji_cache_ref
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


async def _process_comment(
    config: Config,
    modem: HilinkClient,
    client: httpx.AsyncClient,
    card_name: str,
    card_id: str,
    comment: TrelloComment,
    comments: list[TrelloComment],
    emoji_cache_ref: dict[str, dict[str, str]],
) -> None:
    parsed = parse_reply(comment.text, config.reply.trigger, config.reply.case_insensitive)
    if parsed is None:
        return

    if is_reply_already_sent(comments, comment.id, config.reply):
        return

    phone = find_replyable_phone(card_name)
    if phone is None:
        debug_print(f"Reply poll: no replyable phone found in card name '{card_name}'")
        return

    body = await _resolve_body(parsed.body, client, emoji_cache_ref)
    result = await modem.send_sms(phone, body)
    if result.success:
        confirmation = build_sent_confirmation(
            comment.id, config.reply, datetime.now().astimezone()
        )
        post_result = await _post_status_comment_with_retries(config, card_id, confirmation, client)
        if post_result.success:
            print(f"Sent SMS reply to {phone}")
        else:
            print(
                f"Sent SMS reply to {phone} but failed to record sent confirmation after retries "
                f"(next poll may resend): {post_result.error}"
            )
        return

    debug_print(f"Failed to send SMS reply to {phone}: {result.error}")
    if has_failure_notice(comments, comment.id, config.reply):
        return
    notice = build_failure_notice(comment.id, config.reply)
    await _post_status_comment_with_retries(config, card_id, notice, client)


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
