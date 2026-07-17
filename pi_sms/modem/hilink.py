"""Async client for the Huawei E3372 HiLink modem SMS API.

The E3372 HiLink web API uses a CSRF-style flow: every state-changing request
(listing or deleting SMS) requires a fresh session token pair fetched
immediately beforehand from `/api/webserver/SesTokInfo`. The token is
single-use, so it must not be cached across requests.
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from xml.sax.saxutils import escape

import httpx

from .sms import SmsMessage, parse_sms_list

_SES_INFO_PATTERN = re.compile(r"<SesInfo>(.*?)</SesInfo>")
_TOK_INFO_PATTERN = re.compile(r"<TokInfo>(.*?)</TokInfo>")

_LIST_INBOX_BODY = (
    "<?xml version='1.0' encoding='UTF-8'?>"
    "<request><PageIndex>1</PageIndex><ReadCount>50</ReadCount>"
    "<BoxType>1</BoxType><SortType>0</SortType><Ascending>0</Ascending>"
    "<UnreadPreferred>0</UnreadPreferred></request>"
)


@dataclass
class HilinkResult:
    """Outcome of a HiLink API write operation."""

    success: bool
    error: str | None = None


@dataclass
class _HilinkSession:
    """A fetched HiLink session token pair (single-use for the next POST request)."""

    session_id: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Cookie": self.session_id,
            "__RequestVerificationToken": self.token,
            "Content-Type": "text/xml",
        }


class HilinkClient:
    """Async client for the E3372 HiLink SMS API."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 10,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a HiLink client.

        Args:
            base_url: Modem web API base URL (e.g. "http://192.168.8.1")
            timeout_seconds: Per-request timeout
            client: Optional pre-configured httpx.AsyncClient (for tests); when
                provided, it is reused and not closed by this client.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._injected_client = client

    @asynccontextmanager
    async def _http_client(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._injected_client is not None:
            yield self._injected_client
            return
        async with httpx.AsyncClient() as client:
            yield client

    async def _fetch_session(self, client: httpx.AsyncClient) -> _HilinkSession | None:
        try:
            response = await client.get(
                f"{self._base_url}/api/webserver/SesTokInfo", timeout=self._timeout_seconds
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        ses_match = _SES_INFO_PATTERN.search(response.text)
        tok_match = _TOK_INFO_PATTERN.search(response.text)
        if not ses_match or not tok_match:
            return None
        return _HilinkSession(session_id=ses_match.group(1), token=tok_match.group(1))

    async def list_inbox(self) -> list[SmsMessage]:
        """Fetch all messages currently in the modem's SMS inbox.

        Returns an empty list on any connectivity or session error so the
        polling loop can simply retry on the next scheduled run.
        """
        async with self._http_client() as client:
            session = await self._fetch_session(client)
            if session is None:
                return []
            try:
                response = await client.post(
                    f"{self._base_url}/api/sms/sms-list",
                    headers=session.headers,
                    content=_LIST_INBOX_BODY,
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPError:
                return []
            return parse_sms_list(response.text)

    async def delete_sms(self, index: str) -> HilinkResult:
        """Delete a message from the modem inbox by its Index."""
        async with self._http_client() as client:
            session = await self._fetch_session(client)
            if session is None:
                return HilinkResult(success=False, error="Could not obtain HiLink session token")

            body = (
                "<?xml version='1.0' encoding='UTF-8'?>"
                f"<request><Index>{index}</Index></request>"
            )
            try:
                response = await client.post(
                    f"{self._base_url}/api/sms/delete-sms",
                    headers=session.headers,
                    content=body,
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPError as e:
                return HilinkResult(success=False, error=str(e))

            if "<response>OK</response>" in response.text:
                return HilinkResult(success=True)
            return HilinkResult(success=False, error=response.text.strip())

    async def send_sms(self, phone: str, content: str) -> HilinkResult:
        """Send an SMS to a phone number via the modem."""
        async with self._http_client() as client:
            session = await self._fetch_session(client)
            if session is None:
                return HilinkResult(success=False, error="Could not obtain HiLink session token")

            escaped_content = escape(content)
            date = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
            body = (
                "<?xml version='1.0' encoding='UTF-8'?>"
                "<request><Index>-1</Index>"
                f"<Phones><Phone>{escape(phone)}</Phone></Phones>"
                f"<Sca></Sca><Content>{escaped_content}</Content>"
                f"<Length>{len(content)}</Length><Reserved>1</Reserved>"
                f"<Date>{date}</Date></request>"
            )
            try:
                response = await client.post(
                    f"{self._base_url}/api/sms/send-sms",
                    headers=session.headers,
                    content=body,
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPError as e:
                return HilinkResult(success=False, error=str(e))

            if "<response>OK</response>" in response.text:
                return HilinkResult(success=True)
            return HilinkResult(success=False, error=response.text.strip())
