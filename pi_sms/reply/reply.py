"""Trello-comment-driven SMS reply orchestration.

Each poll: list open cards in the configured Trello list, then for every
comment containing the reply trigger that has not already been marked as
sent, extract the phone number from the card name and the SMS body from the
comment, send it via the modem, and annotate the comment with a sent or
failure tag so it is never resent and the team gets visible confirmation.
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
    list_card_comments,
    list_open_cards,
    update_comment,
)
from .text import build_failure_text, build_sent_text, is_already_sent, parse_reply

# A successful SMS send followed by a failed Trello update would otherwise
# leave the comment untagged, causing the same SMS to be resent to the
# customer on the next poll; retrying the tag write a few times narrows that
# window without requiring persistent local state.
_UPDATE_COMMENT_ATTEMPTS = 5
_BASE_RETRY_DELAY_SECONDS = 2
_MAX_RETRY_DELAY_SECONDS = 15


async def poll_and_send_replies(
    config: Config,
    modem: HilinkClient,
    is_running_ref: dict[str, bool],
    client: httpx.AsyncClient | None = None,
) -> None:
    """Poll Trello comments for reply triggers and send matching SMS.

    Args:
        config: Configuration object
        modem: HiLink modem client
        is_running_ref: Dictionary with 'value' key to prevent concurrent polls
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.
    """
    if is_running_ref.get("value", False):
        debug_print("Reply poll skipped: already running (previous poll still in progress)")
        return

    is_running_ref["value"] = True
    try:
        if client is not None:
            await _poll_and_send_replies(config, modem, client)
        else:
            async with httpx.AsyncClient() as new_client:
                await _poll_and_send_replies(config, modem, new_client)
    finally:
        is_running_ref["value"] = False


async def _poll_and_send_replies(
    config: Config, modem: HilinkClient, client: httpx.AsyncClient
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
            await _process_comment(config, modem, client, card.name, comment)


async def _process_comment(
    config: Config,
    modem: HilinkClient,
    client: httpx.AsyncClient,
    card_name: str,
    comment: TrelloComment,
) -> None:
    if is_already_sent(comment.text, config.reply):
        return

    parsed = parse_reply(comment.text, config.reply.trigger, config.reply.case_insensitive)
    if parsed is None:
        return

    phone = find_replyable_phone(card_name)
    if phone is None:
        debug_print(f"Reply poll: no replyable phone found in card name '{card_name}'")
        return

    result = await modem.send_sms(phone, parsed.body)
    if result.success:
        sent_text = build_sent_text(comment.text, config.reply, datetime.now().astimezone())
        update_result = await _update_comment_with_retries(config, comment.id, sent_text, client)
        if update_result.success:
            print(f"Sent SMS reply to {phone}")
        else:
            print(
                f"Sent SMS reply to {phone} but failed to tag comment as sent after retries "
                f"(next poll may resend): {update_result.error}"
            )
        return

    debug_print(f"Failed to send SMS reply to {phone}: {result.error}")
    failure_text = build_failure_text(
        comment.text, config.reply, config.reply.poll_interval_seconds
    )
    await _update_comment_with_retries(config, comment.id, failure_text, client)


async def _update_comment_with_retries(
    config: Config,
    comment_id: str,
    text: str,
    client: httpx.AsyncClient,
) -> TrelloResult:
    """Update a comment, retrying with backoff to reduce the odds of a transient failure.

    A 429 response's indicated cooldown (from `TrelloResult.retry_after_seconds`)
    is honored instead of the regular exponential backoff, since Trello tells
    us exactly how long to wait.
    """
    result = await update_comment(config.trello, comment_id, text, client)
    attempt = 1
    while not result.success and attempt < _UPDATE_COMMENT_ATTEMPTS:
        await asyncio.sleep(_retry_delay_seconds(result, attempt))
        result = await update_comment(config.trello, comment_id, text, client)
        attempt += 1
    return result


def _retry_delay_seconds(result: TrelloResult, attempt: int) -> float:
    """Return how long to wait before the next retry, given the last failed attempt."""
    if result.retry_after_seconds is not None:
        return result.retry_after_seconds
    return float(min(_BASE_RETRY_DELAY_SECONDS * 2 ** (attempt - 1), _MAX_RETRY_DELAY_SECONDS))
