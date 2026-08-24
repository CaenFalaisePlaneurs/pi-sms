"""Tests for pi_sms.trello.trello using a mocked HTTP transport."""

import httpx
import pytest

from pi_sms.core.config import TrelloConfig
from pi_sms.modem.sms import SmsMessage
from pi_sms.trello.trello import (
    add_comment,
    create_card,
    fetch_emoji_map,
    find_card_id_for_phone,
    get_card_list_and_name,
    get_latest_board_comment_id,
    get_list_board_id,
    list_board_comments_since,
    list_card_comments,
    list_open_cards,
    post_comment,
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
async def test_post_comment_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/1/cards/card-abc/actions/comments"
        assert request.url.params["text"] == "new text"
        return httpx.Response(200, json={"id": "action-1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await post_comment(_config(), "card-abc", "new text", client=client)

    assert result.success is True
    assert result.action == "commented"


@pytest.mark.asyncio
async def test_post_comment_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="card not found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await post_comment(_config(), "card-abc", "new text", client=client)

    assert result.success is False
    assert result.error is not None
    assert result.status_code == 404
    assert result.retry_after_seconds is None


@pytest.mark.asyncio
async def test_post_comment_429_prefers_retry_after_header() -> None:
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

    result = await post_comment(_config(), "card-abc", "new text", client=client)

    assert result.success is False
    assert result.status_code == 429
    assert result.retry_after_seconds == 7.0


@pytest.mark.asyncio
async def test_post_comment_429_falls_back_to_rate_limit_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            text="rate limited",
            headers={"x-rate-limit-api-token-interval-ms": "10000"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await post_comment(_config(), "card-abc", "new text", client=client)

    assert result.success is False
    assert result.status_code == 429
    assert result.retry_after_seconds == 10.0


@pytest.mark.asyncio
async def test_post_comment_429_uses_larger_of_key_and_token_headers() -> None:
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

    result = await post_comment(_config(), "card-abc", "new text", client=client)

    assert result.retry_after_seconds == 15.0


@pytest.mark.asyncio
async def test_post_comment_429_defaults_when_no_headers_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await post_comment(_config(), "card-abc", "new text", client=client)

    assert result.status_code == 429
    assert result.retry_after_seconds == 10.0


@pytest.mark.asyncio
async def test_post_comment_network_error_has_no_status_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await post_comment(_config(), "card-abc", "new text", client=client)

    assert result.success is False
    assert result.status_code is None
    assert result.retry_after_seconds is None


@pytest.mark.asyncio
async def test_fetch_emoji_map_flattens_short_names_to_native_character() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/1/emoji"
        return httpx.Response(
            200,
            json={
                "trello": [
                    {
                        "native": "\U0001f44d",
                        "shortName": "thumbsup",
                        "shortNames": ["thumbsup", "+1", "yes"],
                    },
                    {
                        "native": "\u2764\ufe0f",
                        "shortName": "heart",
                        "shortNames": ["heart"],
                    },
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    emoji_map, error = await fetch_emoji_map(client=client)

    assert error is None
    assert emoji_map["+1"] == "\U0001f44d"
    assert emoji_map["yes"] == "\U0001f44d"
    assert emoji_map["heart"] == "\u2764\ufe0f"


@pytest.mark.asyncio
async def test_fetch_emoji_map_returns_empty_map_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    emoji_map, error = await fetch_emoji_map(client=client)

    assert emoji_map == {}
    assert error is not None


@pytest.mark.asyncio
async def test_record_sms_fails_when_lookup_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await record_sms(_config(), _MESSAGE, client=client)

    assert result.success is False
    assert result.action is None


@pytest.mark.asyncio
async def test_get_list_board_id_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/1/lists/list123/board"
        assert request.url.params["fields"] == "id"
        return httpx.Response(200, json={"id": "board-abc"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    board_id, error = await get_list_board_id(_config(), client=client)

    assert error is None
    assert board_id == "board-abc"


@pytest.mark.asyncio
async def test_get_latest_board_comment_id_returns_newest() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/1/boards/board-abc/actions"
        assert request.url.params["filter"] == "commentCard"
        assert request.url.params["limit"] == "1"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "act-newest",
                    "data": {
                        "text": "hello",
                        "card": {"id": "c1", "name": "n"},
                        "list": {"id": "l"},
                    },
                    "date": "2026-07-17T11:00:00.000Z",
                }
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    action_id, error = await get_latest_board_comment_id(_config(), "board-abc", client=client)

    assert error is None
    assert action_id == "act-newest"


@pytest.mark.asyncio
async def test_list_board_comments_since_paginates_and_returns_oldest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pi_sms.trello.trello as trello_module

    monkeypatch.setattr(trello_module, "_BOARD_COMMENTS_PAGE_LIMIT", 2)
    seen_before: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/1/boards/board-abc/actions"
        assert request.url.params["since"] == "cursor-0"
        assert request.url.params["filter"] == "commentCard"
        before = request.url.params.get("before")
        seen_before.append(before)
        if before is None:
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "act-3",
                        "data": {
                            "text": "third",
                            "card": {"id": "c1", "name": "SMS from +33612345678"},
                            "list": {"id": "list123"},
                        },
                        "date": "2026-07-17T13:00:00.000Z",
                    },
                    {
                        "id": "act-2",
                        "data": {
                            "text": "second",
                            "card": {"id": "c1", "name": "SMS from +33612345678"},
                            "list": {"id": "list123"},
                        },
                        "date": "2026-07-17T12:00:00.000Z",
                    },
                ],
            )
        assert before == "act-2"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "act-1",
                    "data": {
                        "text": "first",
                        "card": {"id": "c1", "name": "SMS from +33612345678"},
                        "list": {"id": "list123"},
                    },
                    "date": "2026-07-17T11:00:00.000Z",
                }
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    comments, error = await list_board_comments_since(
        _config(), "board-abc", "cursor-0", client=client
    )

    assert error is None
    assert [c.id for c in comments] == ["act-1", "act-2", "act-3"]
    assert comments[0].text == "first"
    assert comments[0].card_id == "c1"
    assert comments[0].list_id == "list123"
    assert seen_before == [None, "act-2"]


@pytest.mark.asyncio
async def test_get_card_list_and_name_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/1/cards/card-abc"
        assert request.url.params["fields"] == "idList,name"
        return httpx.Response(200, json={"idList": "list123", "name": "SMS from +33612345678"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    location, error = await get_card_list_and_name(_config(), "card-abc", client=client)

    assert error is None
    assert location == ("list123", "SMS from +33612345678")
