"""Text parsing and status formatting for Trello-comment-driven SMS replies.

A team member replies to an SMS conversation by writing a comment containing
the configured trigger marker; everything before it is free-form attribution
or notes kept only in Trello, everything after it (with internal newlines
preserved) is sent verbatim as the SMS body. Trello only lets the original
author of a comment edit it, and replies may be written by any team member,
so a send attempt is recorded as a *new* comment (always postable by the
daemon's own token) that references the trigger comment's ID, instead of
editing the trigger comment itself.
"""

from dataclasses import dataclass
from datetime import datetime

from ..core.config import ReplyConfig
from ..trello.trello import TrelloComment


@dataclass
class ReplyParse:
    """A parsed reply comment: attribution/notes before the trigger, and the SMS body after it."""

    attribution: str
    body: str


def parse_reply(
    comment_text: str, trigger: str, case_insensitive: bool = True
) -> ReplyParse | None:
    """Parse a comment into attribution and SMS body around the first trigger occurrence.

    Args:
        comment_text: Full raw comment text
        trigger: Marker string (e.g. ">>RE:"); text after its first occurrence is the SMS body
        case_insensitive: Whether to match the trigger case-insensitively

    Returns:
        ReplyParse with the attribution and body, or None if the trigger is
        absent or the body is empty after trimming.
    """
    haystack = comment_text.lower() if case_insensitive else comment_text
    needle = trigger.lower() if case_insensitive else trigger

    index = haystack.find(needle)
    if index == -1:
        return None

    attribution = comment_text[:index].strip()
    body = comment_text[index + len(trigger) :].strip()
    if not body:
        return None
    return ReplyParse(attribution=attribution, body=body)


def format_retry_delay(seconds: int) -> str:
    """Format a duration in seconds as a compact compound string.

    Examples: 30 -> "30 s", 180 -> "3 min", 90 -> "1 min 30 s".
    """
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes and remaining_seconds:
        return f"{minutes} min {remaining_seconds} s"
    if minutes:
        return f"{minutes} min"
    return f"{remaining_seconds} s"


def _references_trigger(comment_text: str, marker: str, trigger_comment_id: str) -> bool:
    return marker in comment_text and trigger_comment_id in comment_text


def is_reply_already_sent(
    comments: list[TrelloComment], trigger_comment_id: str, config: ReplyConfig
) -> bool:
    """Return True if a sent confirmation for the given trigger comment already exists."""
    return any(
        _references_trigger(comment.text, config.sent_marker, trigger_comment_id)
        for comment in comments
    )


def has_failure_notice(
    comments: list[TrelloComment], trigger_comment_id: str, config: ReplyConfig
) -> bool:
    """Return True if a failure notice for the given trigger comment was already posted."""
    return any(
        _references_trigger(comment.text, config.failure_marker, trigger_comment_id)
        for comment in comments
    )


def build_sent_confirmation(trigger_comment_id: str, config: ReplyConfig, sent_at: datetime) -> str:
    """Build a new comment recording that the reply SMS for a trigger comment was sent.

    Args:
        trigger_comment_id: Trello action ID of the comment that requested the reply
        config: Reply configuration (templates)
        sent_at: Local, tz-aware timestamp of the successful send, rendered
            for humans reading the card (unlike the UTC timestamps used
            elsewhere in the app for machine-facing state)
    """
    tag = config.sent_tag_template.format(
        date=sent_at.strftime("%d/%m/%Y"), time=sent_at.strftime("%H:%M")
    )
    return f"{tag} (réf: {trigger_comment_id})"


def build_failure_notice(trigger_comment_id: str, config: ReplyConfig) -> str:
    """Build a new comment recording that the reply SMS for a trigger comment failed to send."""
    return f"{config.failure_tag_template} (réf: {trigger_comment_id})"
