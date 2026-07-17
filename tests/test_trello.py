"""Tests for pi_sms.trello.trello using a mocked HTTP transport."""

import httpx
import pytest

from pi_sms.core.config import TrelloConfig
from pi_sms.modem.sms import SmsMessage
from pi_sms.trello.trello import (
    add_comment,
    create_card,
    find_card_id_for_phone,
    list_open_cards,
    record_sms,
)

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
        card_comment_template="{content}",
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
    assert result.action == "created"


@pytest.mark.asyncio
async def test_create_card_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid key")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await create_card(_config(), _MESSAGE, client=client)

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_add_comment_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/1/cards/card-abc/actions/comments"
        assert request.url.params["text"] == "Hello there"
        return httpx.Response(200, json={"id": "action-1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await add_comment(_config(), "card-abc", _MESSAGE, client=client)

    assert result.success is True
    assert result.card_id == "card-abc"
    assert result.action == "commented"


@pytest.mark.asyncio
async def test_add_comment_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="card not found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await add_comment(_config(), "card-abc", _MESSAGE, client=client)

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_list_open_cards_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/1/lists/list123/cards"
        return httpx.Response(
            200,
            json=[
                {"id": "card-1", "name": "SMS from +33612345678"},
                {"id": "card-2", "name": "SMS from +33699999999"},
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    cards, error = await list_open_cards(_config(), client=client)

    assert error is None
    assert [c.id for c in cards] == ["card-1", "card-2"]


@pytest.mark.asyncio
async def test_list_open_cards_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid key")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    cards, error = await list_open_cards(_config(), client=client)

    assert cards == []
    assert error is not None


@pytest.mark.asyncio
async def test_find_card_id_for_phone_match() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "card-1", "name": "SMS from +33612345678"},
                {"id": "card-2", "name": "SMS from +33699999999"},
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    card_id, error = await find_card_id_for_phone(_config(), "+33699999999", client=client)

    assert error is None
    assert card_id == "card-2"


@pytest.mark.asyncio
async def test_find_card_id_for_phone_no_match() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "card-1", "name": "SMS from +33612345678"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    card_id, error = await find_card_id_for_phone(_config(), "+33699999999", client=client)

    assert error is None
    assert card_id is None


@pytest.mark.asyncio
async def test_find_card_id_for_phone_lookup_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    card_id, error = await find_card_id_for_phone(_config(), "+33699999999", client=client)

    assert card_id is None
    assert error is not None


@pytest.mark.asyncio
async def test_record_sms_creates_card_when_no_existing_card() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])
        assert request.url.path == "/1/cards"
        return httpx.Response(200, json={"id": "card-new"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await record_sms(_config(), _MESSAGE, client=client)

    assert result.success is True
    assert result.action == "created"
    assert result.card_id == "card-new"


@pytest.mark.asyncio
async def test_record_sms_comments_on_existing_card() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, json=[{"id": "card-existing", "name": "SMS from +33612345678"}]
            )
        assert request.url.path == "/1/cards/card-existing/actions/comments"
        return httpx.Response(200, json={"id": "action-1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await record_sms(_config(), _MESSAGE, client=client)

    assert result.success is True
    assert result.action == "commented"
    assert result.card_id == "card-existing"


@pytest.mark.asyncio
async def test_record_sms_fails_when_lookup_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await record_sms(_config(), _MESSAGE, client=client)

    assert result.success is False
    assert result.action is None
