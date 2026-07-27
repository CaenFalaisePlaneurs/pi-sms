"""Tests for pi_sms.modem.hilink using a mocked HTTP transport."""

import asyncio

import httpx
import pytest

from pi_sms.modem.hilink import HilinkClient

_SES_TOK_RESPONSE = (
    "<response><SesInfo>SessionID=abc123</SesInfo><TokInfo>tok-xyz</TokInfo></response>"
)
_SMS_LIST_RESPONSE = """<response>
<Messages>
<Message><Index>1</Index><Phone>+33612345678</Phone><Content>Hi</Content><Date>d</Date><Smstat>0</Smstat></Message>
</Messages>
</response>"""


def _client_with_handler(handler: httpx.MockTransport) -> HilinkClient:
    async_client = httpx.AsyncClient(transport=handler)
    return HilinkClient("http://192.168.8.1", client=async_client)


@pytest.mark.asyncio
async def test_list_inbox_returns_parsed_messages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/webserver/SesTokInfo":
            return httpx.Response(200, text=_SES_TOK_RESPONSE)
        if request.url.path == "/api/sms/sms-list":
            assert request.headers["Cookie"] == "SessionID=abc123"
            assert request.headers["__RequestVerificationToken"] == "tok-xyz"
            return httpx.Response(200, text=_SMS_LIST_RESPONSE)
        raise AssertionError(f"Unexpected request to {request.url.path}")

    client = _client_with_handler(httpx.MockTransport(handler))

    messages = await client.list_inbox()

    assert len(messages) == 1
    assert messages[0].phone == "+33612345678"


@pytest.mark.asyncio
async def test_list_inbox_returns_empty_list_on_session_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="error")

    client = _client_with_handler(httpx.MockTransport(handler))

    messages = await client.list_inbox()

    assert messages == []


@pytest.mark.asyncio
async def test_delete_sms_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/webserver/SesTokInfo":
            return httpx.Response(200, text=_SES_TOK_RESPONSE)
        if request.url.path == "/api/sms/delete-sms":
            assert b"<Index>1</Index>" in request.content
            return httpx.Response(200, text="<response>OK</response>")
        raise AssertionError(f"Unexpected request to {request.url.path}")

    client = _client_with_handler(httpx.MockTransport(handler))

    result = await client.delete_sms("1")

    assert result.success is True


@pytest.mark.asyncio
async def test_delete_sms_failure_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/webserver/SesTokInfo":
            return httpx.Response(200, text=_SES_TOK_RESPONSE)
        return httpx.Response(200, text="<error><code>125002</code></error>")

    client = _client_with_handler(httpx.MockTransport(handler))

    result = await client.delete_sms("1")

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_delete_sms_no_session_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client_with_handler(httpx.MockTransport(handler))

    result = await client.delete_sms("1")

    assert result.success is False
    assert result.error == "Could not obtain HiLink session token"


@pytest.mark.asyncio
async def test_send_sms_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/webserver/SesTokInfo":
            return httpx.Response(200, text=_SES_TOK_RESPONSE)
        if request.url.path == "/api/sms/send-sms":
            assert b"<Phone>+33612345678</Phone>" in request.content
            assert b"<Content>Hello there</Content>" in request.content
            return httpx.Response(200, text="<response>OK</response>")
        raise AssertionError(f"Unexpected request to {request.url.path}")

    client = _client_with_handler(httpx.MockTransport(handler))

    result = await client.send_sms("+33612345678", "Hello there")

    assert result.success is True


@pytest.mark.asyncio
async def test_send_sms_failure_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/webserver/SesTokInfo":
            return httpx.Response(200, text=_SES_TOK_RESPONSE)
        return httpx.Response(200, text="<error><code>125002</code></error>")

    client = _client_with_handler(httpx.MockTransport(handler))

    result = await client.send_sms("+33612345678", "Hello there")

    assert result.success is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_concurrent_calls_are_serialized_by_internal_lock() -> None:
    """The inbox poll and reply poll run as independent scheduler jobs and can
    both reach the same HilinkClient at once; the internal lock must ensure
    only one request is ever in flight so session tokens can't interleave.
    """
    active_requests = 0
    max_concurrent_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_requests, max_concurrent_requests
        active_requests += 1
        max_concurrent_requests = max(max_concurrent_requests, active_requests)
        try:
            await asyncio.sleep(0.01)
            if request.url.path == "/api/webserver/SesTokInfo":
                return httpx.Response(200, text=_SES_TOK_RESPONSE)
            if request.url.path == "/api/sms/sms-list":
                return httpx.Response(200, text=_SMS_LIST_RESPONSE)
            if request.url.path == "/api/sms/send-sms":
                return httpx.Response(200, text="<response>OK</response>")
            raise AssertionError(f"Unexpected request to {request.url.path}")
        finally:
            active_requests -= 1

    client = _client_with_handler(httpx.MockTransport(handler))

    await asyncio.gather(
        client.list_inbox(),
        client.send_sms("+33612345678", "Hi"),
    )

    assert max_concurrent_requests == 1


@pytest.mark.asyncio
async def test_send_sms_no_session_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client_with_handler(httpx.MockTransport(handler))

    result = await client.send_sms("+33612345678", "Hello there")

    assert result.success is False
    assert result.error == "Could not obtain HiLink session token"
