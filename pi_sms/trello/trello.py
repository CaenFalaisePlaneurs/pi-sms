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
    card_id: str = ""
    card_name: str = ""
    list_id: str = ""


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
    return await post_comment(config, card_id, text, client)


async def post_comment(
    config: TrelloConfig,
    card_id: str,
    text: str,
    client: httpx.AsyncClient | None = None,
) -> TrelloResult:
    """Add a new comment with arbitrary text to a Trello card.

    Posting a new comment is always permitted for the daemon's own token,
    unlike editing an existing one (which the Trello API restricts to the
    original author) - this is how reply status notices are recorded without
    needing to touch a comment written by a team member.

    Args:
        config: Trello configuration (key, token)
        card_id: Trello card ID to comment on
        text: Comment body
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        TrelloResult with action="commented" on success
    """
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


async def fetch_emoji_map(
    client: httpx.AsyncClient | None = None,
) -> tuple[dict[str, str], str | None]:
    """Fetch Trello's emoji list and flatten it into a shortcode-to-character map.

    Trello stores comment text as markdown source: typing or autocompleting an
    emoji inserts `:shortname:` syntax, and only Trello's own client renders
    that into the actual glyph. The REST API's raw comment text keeps the
    markdown form, so a reply comment containing an emoji arrives here as
    literal `:heart:` text; this map lets the reply path resolve it back into
    the real character before sending it as an SMS.

    This endpoint is public and does not require a key/token.

    Args:
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        Tuple of (shortcode -> native character map, error). On success,
        error is None. On failure, the map is empty and error describes the
        failure.
    """
    if client is not None:
        return await _fetch_emoji_map(client)
    async with httpx.AsyncClient() as new_client:
        return await _fetch_emoji_map(new_client)


async def _fetch_emoji_map(client: httpx.AsyncClient) -> tuple[dict[str, str], str | None]:
    try:
        response = await client.get(f"{_TRELLO_API_BASE_URL}/emoji", timeout=15)
        response.raise_for_status()
    except httpx.HTTPError as e:
        return {}, str(e)

    try:
        raw_emoji = response.json()
    except ValueError:
        return {}, "Invalid JSON response listing emoji"

    emoji_map: dict[str, str] = {}
    for entry in raw_emoji.get("trello", []):
        native = entry.get("native")
        if not native:
            continue
        for short_name in entry.get("shortNames", []):
            emoji_map[short_name.lower()] = native
    return emoji_map, None


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

    comments = [_comment_from_action(action) for action in raw_actions]
    return comments, None


_BOARD_COMMENTS_PAGE_LIMIT = 1000


async def get_list_board_id(
    config: TrelloConfig,
    client: httpx.AsyncClient | None = None,
) -> tuple[str | None, str | None]:
    """Resolve the board id that owns the configured Trello list.

    Args:
        config: Trello configuration (key, token, list_id)
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        Tuple of (board_id, error). On success, error is None. On failure,
        board_id is None and error describes the failure.
    """
    if client is not None:
        return await _get_list_board_id(config, client)
    async with httpx.AsyncClient() as new_client:
        return await _get_list_board_id(config, new_client)


async def _get_list_board_id(
    config: TrelloConfig, client: httpx.AsyncClient
) -> tuple[str | None, str | None]:
    try:
        response = await client.get(
            f"{_TRELLO_API_BASE_URL}/lists/{config.list_id}/board",
            params={"key": config.key, "token": config.token, "fields": "id"},
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        return None, str(e)

    try:
        payload = response.json()
    except ValueError:
        return None, "Invalid JSON response fetching list board"

    board_id = payload.get("id")
    if not board_id:
        return None, "Board id missing from list board response"
    return str(board_id), None


async def get_latest_board_comment_id(
    config: TrelloConfig,
    board_id: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[str | None, str | None]:
    """Return the newest commentCard action id on a board, if any exist.

    Used as the incremental cursor snapshot taken before a first-run full
    scan, so comments posted during bootstrap are picked up on the next poll.

    Args:
        config: Trello configuration (key, token)
        board_id: Trello board ID
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        Tuple of (action_id, error). action_id is None when the board has no
        comments (or on failure); error is None on success.
    """
    if client is not None:
        return await _get_latest_board_comment_id(config, board_id, client)
    async with httpx.AsyncClient() as new_client:
        return await _get_latest_board_comment_id(config, board_id, new_client)


async def _get_latest_board_comment_id(
    config: TrelloConfig, board_id: str, client: httpx.AsyncClient
) -> tuple[str | None, str | None]:
    comments, error = await _list_board_comments_page(
        config, board_id, client, since=None, before=None, limit=1
    )
    if error is not None:
        return None, error
    if not comments:
        return None, None
    return comments[0].id, None


async def list_board_comments_since(
    config: TrelloConfig,
    board_id: str,
    since_action_id: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[TrelloComment], str | None]:
    """List commentCard actions on a board newer than since_action_id.

    Pages with `before` when a response is full. Results are oldest-first so
    replies are sent in the order they were written.

    Args:
        config: Trello configuration (key, token)
        board_id: Trello board ID
        since_action_id: Exclusive cursor (Trello action id or ISO date)
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        Tuple of (comments, error). On success, error is None. On failure,
        comments is empty and error describes the failure.
    """
    if client is not None:
        return await _list_board_comments_since(config, board_id, since_action_id, client)
    async with httpx.AsyncClient() as new_client:
        return await _list_board_comments_since(config, board_id, since_action_id, new_client)


async def _list_board_comments_since(
    config: TrelloConfig,
    board_id: str,
    since_action_id: str,
    client: httpx.AsyncClient,
) -> tuple[list[TrelloComment], str | None]:
    newest_first: list[TrelloComment] = []
    before: str | None = None
    while True:
        page, error = await _list_board_comments_page(
            config,
            board_id,
            client,
            since=since_action_id,
            before=before,
            limit=_BOARD_COMMENTS_PAGE_LIMIT,
        )
        if error is not None:
            return [], error
        newest_first.extend(page)
        if len(page) < _BOARD_COMMENTS_PAGE_LIMIT:
            break
        before = page[-1].id

    newest_first.reverse()
    return newest_first, None


async def get_card_list_and_name(
    config: TrelloConfig,
    card_id: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[tuple[str, str] | None, str | None]:
    """Fetch a card's current list id and name.

    Used when a board commentCard action omits `data.list` so we can still
    ignore comments that are not on the configured SMS list.

    Args:
        config: Trello configuration (key, token)
        card_id: Trello card ID
        client: Optional pre-configured httpx.AsyncClient (for tests); when
            provided, it is reused and not closed by this function.

    Returns:
        Tuple of ((list_id, name), error). On success, error is None. On
        failure, the pair is None and error describes the failure.
    """
    if client is not None:
        return await _get_card_list_and_name(config, card_id, client)
    async with httpx.AsyncClient() as new_client:
        return await _get_card_list_and_name(config, card_id, new_client)


async def _get_card_list_and_name(
    config: TrelloConfig, card_id: str, client: httpx.AsyncClient
) -> tuple[tuple[str, str] | None, str | None]:
    try:
        response = await client.get(
            f"{_TRELLO_API_BASE_URL}/cards/{card_id}",
            params={"key": config.key, "token": config.token, "fields": "idList,name"},
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        return None, str(e)

    try:
        payload = response.json()
    except ValueError:
        return None, "Invalid JSON response fetching card"

    list_id = payload.get("idList")
    if not list_id:
        return None, "Card idList missing from response"
    return (str(list_id), str(payload.get("name", ""))), None


async def _list_board_comments_page(
    config: TrelloConfig,
    board_id: str,
    client: httpx.AsyncClient,
    *,
    since: str | None,
    before: str | None,
    limit: int,
) -> tuple[list[TrelloComment], str | None]:
    params: dict[str, str | int] = {
        "key": config.key,
        "token": config.token,
        "filter": "commentCard",
        "fields": "id,type,date,data",
        "limit": limit,
    }
    if since:
        params["since"] = since
    if before:
        params["before"] = before

    try:
        response = await client.get(
            f"{_TRELLO_API_BASE_URL}/boards/{board_id}/actions",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        return [], str(e)

    try:
        raw_actions = response.json()
    except ValueError:
        return [], "Invalid JSON response listing board comments"

    return [_comment_from_action(action) for action in raw_actions], None


def _comment_from_action(action: dict[str, object]) -> TrelloComment:
    data = action.get("data")
    data_dict = data if isinstance(data, dict) else {}
    card = data_dict.get("card")
    card_dict = card if isinstance(card, dict) else {}
    list_info = data_dict.get("list")
    list_dict = list_info if isinstance(list_info, dict) else {}
    return TrelloComment(
        id=str(action.get("id", "")),
        text=str(data_dict.get("text", "") or ""),
        date=str(action.get("date", "") or ""),
        card_id=str(card_dict.get("id", "") or ""),
        card_name=str(card_dict.get("name", "") or ""),
        list_id=str(list_dict.get("id", "") or ""),
    )


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
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        retry_after = _rate_limit_backoff_seconds(e.response) if status_code == 429 else None
        return TrelloResult(
            success=False, error=str(e), status_code=status_code, retry_after_seconds=retry_after
        )
    except httpx.HTTPError as e:
        return TrelloResult(success=False, error=str(e))
    return TrelloResult(success=True, card_id=card_id, action="commented")
