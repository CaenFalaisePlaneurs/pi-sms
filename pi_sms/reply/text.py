"""Text parsing and tag formatting for Trello-comment-driven SMS replies.

A team member replies to an SMS conversation by writing a comment containing
the configured trigger marker; everything before it is free-form attribution
or notes kept only in Trello, everything after it (with internal newlines
preserved) is sent verbatim as the SMS body. After a send attempt, the
comment is annotated with a sent or failure tag so it is never resent.
"""

from dataclasses import dataclass
from datetime import datetime

from ..core.config import ReplyConfig


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


def is_already_sent(comment_text: str, config: ReplyConfig) -> bool:
    """Return True if the comment already carries the sent tag."""
    return config.sent_marker in comment_text


def has_failure_tag(comment_text: str, config: ReplyConfig) -> bool:
    """Return True if the comment already carries a pending-failure tag."""
    return config.failure_marker in comment_text


def build_sent_text(comment_text: str, config: ReplyConfig, sent_at: datetime) -> str:
    """Return the comment text updated with the sent tag, removing any failure tag.

    Args:
        comment_text: Current comment text (may carry a stale failure tag)
        config: Reply configuration (templates)
        sent_at: Local, tz-aware timestamp of the successful send, rendered
            for humans reading the card (unlike the UTC timestamps used
            elsewhere in the app for machine-facing state)
    """
    base = _strip_tag(comment_text, config.failure_marker)
    tag = config.sent_tag_template.format(
        date=sent_at.strftime("%d/%m/%Y"), time=sent_at.strftime("%H:%M")
    )
    return f"{base}\n\n{tag}"


def build_failure_text(comment_text: str, config: ReplyConfig, delay_seconds: int) -> str:
    """Return the comment text updated with a fresh failure tag, replacing any stale one."""
    base = _strip_tag(comment_text, config.failure_marker)
    tag = config.failure_tag_template.format(delay=format_retry_delay(delay_seconds))
    return f"{base}\n\n{tag}"


def _strip_tag(comment_text: str, marker: str) -> str:
    """Remove a trailing tag (and its leading whitespace) identified by marker, if present."""
    index = comment_text.find(marker)
    if index == -1:
        return comment_text
    return comment_text[:index].rstrip()
