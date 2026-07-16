"""Tests for pi_sms.trello.trello using a mocked HTTP transport."""

import httpx
import pytest

from pi_sms.core.config import TrelloConfig
from pi_sms.modem.sms import SmsMessage
from pi_sms.trello.trello import create_card

_MESSAGE = SmsMessage(
    index="1", phone="+33612345678", content="Hello there", date="2026-07-15 19:35:12", smstat="0"
)


def _config() -> TrelloConfig:
    return TrelloConfig(
        key="key",
        token="token",
        list_id="list123",
        card_name_template="SMS from {phone}",
        card_desc_template="{content}",
    )


@pytest.mark.asyncio
async def test_create_card_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/1/cards"
        assert request.url.params["idList"] == "list123"
        assert request.url.params["name"] == "SMS from +33612345678"
        assert request.url.params["desc"] == "Hello there"
        return httpx.Response(200, json={"id": "card-abc"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await create_card(_config(), _MESSAGE, client=client)

    assert result.success is True
    assert result.card_id == "card-abc"


@pytest.mark.asyncio
async def test_create_card_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid key")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await create_card(_config(), _MESSAGE, client=client)

    assert result.success is False
    assert result.error is not None
