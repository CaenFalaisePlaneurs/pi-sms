"""Trello card creation for incoming SMS messages."""

from dataclasses import dataclass

import httpx

from ..core.config import TrelloConfig
from ..modem.sms import SmsMessage

_TRELLO_API_BASE_URL = "https://api.trello.com/1"


@dataclass
class TrelloResult:
    """Outcome of a Trello API operation."""

    success: bool
    card_id: str | None = None
    error: str | None = None


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
        TrelloResult with the created card ID on success
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
    return TrelloResult(success=True, card_id=card_id)
