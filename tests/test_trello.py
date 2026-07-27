"""Tests for pi_sms.trello.trello using a mocked HTTP transport."""

import httpx
import pytest

from pi_sms.core.config import TrelloConfig
from pi_sms.modem.sms import SmsMessage
from pi_sms.trello.trello import (
    add_comment,
    create_card,
    find_card_id_for_phone,
    list_card_comments,
    list_open_cards,
    record_sms,
    update_comment,
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
async def test_list_card_comments_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/1/cards/card-abc/actions"
        assert request.url.params["filter"] == "commentCard"
        # "data" must be requested explicitly, or Trello omits data.text
        # (the comment body) from the response entirely.
        assert "data" in request.url.params["fields"].split(",")
        return httpx.Response(
            200,
            json=[
                {
                    "id": "action-1",
                    "data": {"text": ">>RE: hello"},
                    "date": "2026-07-17T10:00:00.000Z",
                },
                {
                    "id": "action-2",
                    "data": {"text": "just a note"},
                    "date": "2026-07-17T11:00:00.000Z",
                },
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    comments, error = await list_card_comments(_config(), "card-abc", client=client)

    assert error is None
    assert [c.id for c in comments] == ["action-1", "action-2"]
    assert comments[0].text == ">>RE: hello"


@pytest.mark.asyncio
async def test_list_card_comments_defaults_to_empty_text_when_data_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"id": "action-1", "date": "2026-07-17T10:00:00.000Z"}],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    comments, error = await list_card_comments(_config(), "card-abc", client=client)

    assert error is None
    assert comments[0].text == ""


@pytest.mark.asyncio
async def test_list_card_comments_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid key")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    comments, error = await list_card_comments(_config(), "card-abc", client=client)

    assert comments == []
    assert error is not None


@pytest.mark.asyncio
async def test_update_comment_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/1/actions/action-1"
        assert request.method == "PUT"
        assert request.url.params["text"] == "new text"
        return httpx.Response(200, json={"id": "action-1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await update_comment(_config(), "action-1", "new text", client=client)

    assert result.success is True
    assert result.action == "updated"


@pytest.mark.asyncio
async def test_update_comment_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="action not found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await update_comment(_config(), "action-1", "new text", client=client)

    assert result.success is False
    assert result.error is not None
    assert result.status_code == 404
    assert result.retry_after_seconds is None


@pytest.mark.asyncio
async def test_update_comment_429_prefers_retry_after_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            text="rate limited",
            headers={
                "Retry-After": "7",
                "x-rate-limit-api-token-interval-ms": "10000",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await update_comment(_config(), "action-1", "new text", client=client)

    assert result.success is False
    assert result.status_code == 429
    assert result.retry_after_seconds == 7.0


@pytest.mark.asyncio
async def test_update_comment_429_falls_back_to_rate_limit_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            text="rate limited",
            headers={"x-rate-limit-api-token-interval-ms": "10000"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await update_comment(_config(), "action-1", "new text", client=client)

    assert result.success is False
    assert result.status_code == 429
    assert result.retry_after_seconds == 10.0


@pytest.mark.asyncio
async def test_update_comment_429_uses_larger_of_key_and_token_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            text="rate limited",
            headers={
                "x-rate-limit-api-token-interval-ms": "10000",
                "x-rate-limit-api-key-interval-ms": "15000",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await update_comment(_config(), "action-1", "new text", client=client)

    assert result.retry_after_seconds == 15.0


@pytest.mark.asyncio
async def test_update_comment_429_defaults_when_no_headers_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await update_comment(_config(), "action-1", "new text", client=client)

    assert result.status_code == 429
    assert result.retry_after_seconds == 10.0


@pytest.mark.asyncio
async def test_update_comment_network_error_has_no_status_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await update_comment(_config(), "action-1", "new text", client=client)

    assert result.success is False
    assert result.status_code is None
    assert result.retry_after_seconds is None


@pytest.mark.asyncio
async def test_record_sms_fails_when_lookup_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await record_sms(_config(), _MESSAGE, client=client)

    assert result.success is False
    assert result.action is None
