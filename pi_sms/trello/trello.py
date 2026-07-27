"""Trello card creation and comment management for incoming SMS messages.

One card is kept per phone number: the first SMS creates a card, and later
SMS from the same number are appended as comments so the card reads as a
conversation thread.
"""

from dataclasses import dataclass

import httpx

from ..core.config import TrelloConfig
from ..modem.sms import SmsMessage

_TRELLO_API_BASE_URL = "https://api.trello.com/1"

# Trello does not reliably send a standard Retry-After header on 429s, but it
# does document a fixed rate-limit window (10s) and returns the window size
# on every response via x-rate-limit-*-interval-ms; fall back to that window
# when neither header is present.
_DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 10.0


@dataclass
class TrelloCard:
    """A minimal Trello card representation used for phone-number matching."""

    id: str
    name: str


@dataclass
class TrelloComment:
    """A comment (commentCard action) on a Trello card."""

    id: str
    text: str
    date: str


@dataclass
class TrelloResult:
    """Outcome of a Trello API operation."""

    success: bool
    card_id: str | None = None
    action: str | None = None  # "created" or "commented"
    error: str | None = None
    status_code: int | None = None
    retry_after_seconds: float | None = None  # populated when status_code == 429


async def record_sms(
    config: TrelloConfig,
    message: SmsMessage,
    client: httpx.AsyncClient | None = None,
) -> TrelloResult:
    """Record an SMS message in Trello, keeping one card per phone number.

    Looks up an open card already named for the sender's phone number in the
    configured list; if found, the SMS is appended as a comment, otherwise a
    new card is created. A lookup failure is treated as recoverable (the
    caller should leave the message on the modem for the next poll) so we
    never risk creating a duplicate card for an existing conversation.

    Args:
        config: Trello configuration (key, token, list_id, templates)
        message: SMS message to record
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        TrelloResult describing whether a card was created or commented on
    """
    if client is not None:
        return await _record_sms(config, message, client)
    async with httpx.AsyncClient() as new_client:
        return await _record_sms(config, message, new_client)


async def _record_sms(
    config: TrelloConfig, message: SmsMessage, client: httpx.AsyncClient
) -> TrelloResult:
    card_id, error = await find_card_id_for_phone(config, message.phone, client)
    if error is not None:
        return TrelloResult(success=False, error=error)

    if card_id is not None:
        return await add_comment(config, card_id, message, client)
    return await create_card(config, message, client)


async def create_card(
    config: TrelloConfig,
    message: SmsMessage,
    client: httpx.AsyncClient | None = None,
) -> TrelloResult:
    """Create a Trello card for an SMS message in the configured list.

    Args:
        config: Trello configuration (key, token, list_id, templates)
        message: SMS message to create a card for
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        TrelloResult with action="created" and the new card ID on success
    """
    name = config.card_name_template.format(
        phone=message.phone, date=message.date, content=message.content
    )
    desc = config.card_desc_template.format(
        phone=message.phone, date=message.date, content=message.content
    )
    params = {
        "key": config.key,
        "token": config.token,
        "idList": config.list_id,
        "name": name,
        "desc": desc,
    }

    if client is not None:
        return await _post_card(client, params)
    async with httpx.AsyncClient() as new_client:
        return await _post_card(new_client, params)


async def add_comment(
    config: TrelloConfig,
    card_id: str,
    message: SmsMessage,
    client: httpx.AsyncClient | None = None,
) -> TrelloResult:
    """Add an SMS message as a comment on an existing Trello card.

    Args:
        config: Trello configuration (key, token, templates)
        card_id: Trello card ID to comment on
        message: SMS message to render as a comment
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        TrelloResult with action="commented" on success
    """
    text = config.card_comment_template.format(
        phone=message.phone, date=message.date, content=message.content
    )
    params = {"key": config.key, "token": config.token, "text": text}

    if client is not None:
        return await _post_comment(client, card_id, params)
    async with httpx.AsyncClient() as new_client:
        return await _post_comment(new_client, card_id, params)


async def list_open_cards(
    config: TrelloConfig,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[TrelloCard], str | None]:
    """List open (non-archived) cards in the configured Trello list.

    Args:
        config: Trello configuration (key, token, list_id)
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        Tuple of (cards, error). On success, error is None. On failure,
        cards is empty and error describes the failure.
    """
    if client is not None:
        return await _list_open_cards(config, client)
    async with httpx.AsyncClient() as new_client:
        return await _list_open_cards(config, new_client)


async def _list_open_cards(
    config: TrelloConfig, client: httpx.AsyncClient
) -> tuple[list[TrelloCard], str | None]:
    try:
        response = await client.get(
            f"{_TRELLO_API_BASE_URL}/lists/{config.list_id}/cards",
            params={"key": config.key, "token": config.token, "fields": "name"},
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        return [], str(e)

    try:
        raw_cards = response.json()
    except ValueError:
        return [], "Invalid JSON response listing cards"

    cards = [TrelloCard(id=c["id"], name=c.get("name", "")) for c in raw_cards]
    return cards, None


async def find_card_id_for_phone(
    config: TrelloConfig,
    phone: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[str | None, str | None]:
    """Find the open card whose name contains the given phone number.

    Args:
        config: Trello configuration (key, token, list_id)
        phone: Sender phone number to match against card names
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        Tuple of (card_id, error). card_id is None if no card matches (or on
        failure); error is None on success (including a "no match" outcome).
    """
    cards, error = await list_open_cards(config, client)
    if error is not None:
        return None, error

    for card in cards:
        if phone in card.name:
            return card.id, None
    return None, None


async def list_card_comments(
    config: TrelloConfig,
    card_id: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[TrelloComment], str | None]:
    """List comments (commentCard actions) on a Trello card.

    Args:
        config: Trello configuration (key, token)
        card_id: Trello card ID to list comments for
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        Tuple of (comments, error). On success, error is None. On failure,
        comments is empty and error describes the failure.
    """
    if client is not None:
        return await _list_card_comments(config, card_id, client)
    async with httpx.AsyncClient() as new_client:
        return await _list_card_comments(config, card_id, new_client)


async def _list_card_comments(
    config: TrelloConfig, card_id: str, client: httpx.AsyncClient
) -> tuple[list[TrelloComment], str | None]:
    try:
        response = await client.get(
            f"{_TRELLO_API_BASE_URL}/cards/{card_id}/actions",
            params={
                "key": config.key,
                "token": config.token,
                "filter": "commentCard",
                # "data" (which holds data.text, the comment body) is only
                # returned when explicitly listed here; omitting it makes
                # Trello drop the field entirely, not just leave it empty.
                "fields": "id,type,date,data",
            },
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        return [], str(e)

    try:
        raw_actions = response.json()
    except ValueError:
        return [], "Invalid JSON response listing card comments"

    comments = [
        TrelloComment(
            id=action["id"],
            text=action.get("data", {}).get("text", ""),
            date=action.get("date", ""),
        )
        for action in raw_actions
    ]
    return comments, None


async def update_comment(
    config: TrelloConfig,
    comment_id: str,
    text: str,
    client: httpx.AsyncClient | None = None,
) -> TrelloResult:
    """Update the text of an existing comment (commentCard action).

    Only the author of a comment can edit it via the Trello API; since the
    daemon and every team member authenticate as the same Trello token, the
    daemon can always edit its own reply comments to mark them as sent.

    Args:
        config: Trello configuration (key, token)
        comment_id: Trello action ID of the comment to update
        text: New comment text
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        TrelloResult with action="updated" on success
    """
    params = {"key": config.key, "token": config.token, "text": text}
    if client is not None:
        return await _put_comment(client, comment_id, params)
    async with httpx.AsyncClient() as new_client:
        return await _put_comment(new_client, comment_id, params)


async def _put_comment(
    client: httpx.AsyncClient, comment_id: str, params: dict[str, str]
) -> TrelloResult:
    try:
        response = await client.put(
            f"{_TRELLO_API_BASE_URL}/actions/{comment_id}",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        retry_after = _rate_limit_backoff_seconds(e.response) if status_code == 429 else None
        return TrelloResult(
            success=False, error=str(e), status_code=status_code, retry_after_seconds=retry_after
        )
    except httpx.HTTPError as e:
        return TrelloResult(success=False, error=str(e))
    return TrelloResult(success=True, action="updated")


def _rate_limit_backoff_seconds(response: httpx.Response) -> float:
    """Determine how long to wait before retrying a 429 response.

    Trello does not reliably send a standard Retry-After header, so this
    prefers it when present, falls back to Trello's documented rate-limit
    window headers (interval in milliseconds), and otherwise uses the
    documented default window size.
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return float(retry_after)
        except ValueError:
            pass

    interval_headers = (
        "x-rate-limit-api-token-interval-ms",
        "x-rate-limit-api-key-interval-ms",
    )
    interval_ms_values = []
    for header in interval_headers:
        value = response.headers.get(header)
        if value is not None:
            try:
                interval_ms_values.append(float(value))
            except ValueError:
                continue

    if interval_ms_values:
        return max(interval_ms_values) / 1000

    return _DEFAULT_RATE_LIMIT_BACKOFF_SECONDS


async def _post_card(client: httpx.AsyncClient, params: dict[str, str]) -> TrelloResult:
    try:
        response = await client.post(f"{_TRELLO_API_BASE_URL}/cards", params=params, timeout=15)
        response.raise_for_status()
    except httpx.HTTPError as e:
        return TrelloResult(success=False, error=str(e))

    try:
        card_id = response.json().get("id")
    except ValueError:
        card_id = None
    return TrelloResult(success=True, card_id=card_id, action="created")


async def _post_comment(
    client: httpx.AsyncClient, card_id: str, params: dict[str, str]
) -> TrelloResult:
    try:
        response = await client.post(
            f"{_TRELLO_API_BASE_URL}/cards/{card_id}/actions/comments",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        return TrelloResult(success=False, error=str(e))
    return TrelloResult(success=True, card_id=card_id, action="commented")
