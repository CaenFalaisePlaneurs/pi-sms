"""Tests for pi_sms.core.workflow.poll_and_process."""

from unittest.mock import AsyncMock, patch

import pytest

from pi_sms.core.config import Config, TrelloConfig
from pi_sms.core.workflow import poll_and_process
from pi_sms.filter.filter import SmsFilter
from pi_sms.modem.hilink import HilinkResult
from pi_sms.modem.sms import SmsMessage
from pi_sms.trello.trello import TrelloResult


def _config() -> Config:
    return Config(
        trello=TrelloConfig(key="k", token="t", list_id="l"),
    )


def _message(index: str, content: str = "Hello") -> SmsMessage:
    return SmsMessage(index=index, phone="+33600000000", content=content, date="d", smstat="0")


@pytest.mark.asyncio
async def test_poll_and_process_creates_card_and_deletes_on_success() -> None:
    modem = AsyncMock()
    modem.list_inbox.return_value = [_message("1")]
    modem.delete_sms.return_value = HilinkResult(success=True)
    sms_filter = SmsFilter(exclude_patterns=[])
    is_running_ref = {"value": False}

    with patch(
        "pi_sms.core.workflow.create_card",
        new=AsyncMock(return_value=TrelloResult(success=True, card_id="c1")),
    ) as mock_create_card:
        await poll_and_process(_config(), modem, sms_filter, is_running_ref)

    mock_create_card.assert_awaited_once()
    modem.delete_sms.assert_awaited_once_with("1")
    assert is_running_ref["value"] is False


@pytest.mark.asyncio
async def test_poll_and_process_deletes_filtered_message_without_card() -> None:
    modem = AsyncMock()
    modem.list_inbox.return_value = [_message("2", content='Messagerie "666" Free: msg')]
    sms_filter = SmsFilter(exclude_patterns=['^Messagerie "666" Free:'])
    is_running_ref = {"value": False}

    with patch("pi_sms.core.workflow.create_card", new=AsyncMock()) as mock_create_card:
        await poll_and_process(_config(), modem, sms_filter, is_running_ref)

    mock_create_card.assert_not_awaited()
    modem.delete_sms.assert_awaited_once_with("2")


@pytest.mark.asyncio
async def test_poll_and_process_leaves_message_on_trello_failure() -> None:
    modem = AsyncMock()
    modem.list_inbox.return_value = [_message("3")]
    sms_filter = SmsFilter(exclude_patterns=[])
    is_running_ref = {"value": False}

    with patch(
        "pi_sms.core.workflow.create_card",
        new=AsyncMock(return_value=TrelloResult(success=False, error="boom")),
    ):
        await poll_and_process(_config(), modem, sms_filter, is_running_ref)

    modem.delete_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_process_skips_when_already_running() -> None:
    modem = AsyncMock()
    sms_filter = SmsFilter(exclude_patterns=[])
    is_running_ref = {"value": True}

    await poll_and_process(_config(), modem, sms_filter, is_running_ref)

    modem.list_inbox.assert_not_awaited()
    assert is_running_ref["value"] is True


@pytest.mark.asyncio
async def test_poll_and_process_handles_empty_inbox() -> None:
    modem = AsyncMock()
    modem.list_inbox.return_value = []
    sms_filter = SmsFilter(exclude_patterns=[])
    is_running_ref = {"value": False}

    await poll_and_process(_config(), modem, sms_filter, is_running_ref)

    modem.delete_sms.assert_not_awaited()
    assert is_running_ref["value"] is False
