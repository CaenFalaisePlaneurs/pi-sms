"""Tests for pi_sms.reply.text and pi_sms.reply.reply."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from pi_sms.core.config import Config, ReplyConfig, TrelloConfig
from pi_sms.modem.hilink import HilinkResult
from pi_sms.reply.reply import poll_and_send_replies
from pi_sms.reply.text import (
    build_failure_text,
    build_sent_text,
    format_retry_delay,
    has_failure_tag,
    is_already_sent,
    parse_reply,
)
from pi_sms.trello.trello import TrelloCard, TrelloComment, TrelloResult

_TRIGGER = ">>RE:"


def _reply_config() -> ReplyConfig:
    return ReplyConfig()


def _config() -> Config:
    return Config(
        trello=TrelloConfig(key="k", token="t", list_id="l"),
        reply=_reply_config(),
    )


# --- parse_reply ---


def test_parse_reply_splits_attribution_and_body() -> None:
    parsed = parse_reply("Nico - je renvoie un sms\n>>RE: Toujours interessé?", _TRIGGER)

    assert parsed is not None
    assert parsed.attribution == "Nico - je renvoie un sms"
    assert parsed.body == "Toujours interessé?"


def test_parse_reply_preserves_internal_newlines_in_body() -> None:
    comment = "Nico\n>>RE: Ligne 1\nLigne 2\nMerci."

    parsed = parse_reply(comment, _TRIGGER)

    assert parsed is not None
    assert parsed.body == "Ligne 1\nLigne 2\nMerci."


def test_parse_reply_no_trigger_returns_none() -> None:
    assert parse_reply("Just a note, no reply here", _TRIGGER) is None


def test_parse_reply_empty_body_returns_none() -> None:
    assert parse_reply("Nico >>RE:   ", _TRIGGER) is None


def test_parse_reply_case_insensitive_by_default() -> None:
    parsed = parse_reply("Nico >>re: hello", _TRIGGER)

    assert parsed is not None
    assert parsed.body == "hello"


def test_parse_reply_case_sensitive_when_disabled() -> None:
    assert parse_reply("Nico >>re: hello", _TRIGGER, case_insensitive=False) is None


def test_parse_reply_without_attribution() -> None:
    parsed = parse_reply(">>RE: hello there", _TRIGGER)

    assert parsed is not None
    assert parsed.attribution == ""
    assert parsed.body == "hello there"


# --- format_retry_delay ---


def test_format_retry_delay_seconds_only() -> None:
    assert format_retry_delay(30) == "30 s"


def test_format_retry_delay_whole_minutes() -> None:
    assert format_retry_delay(180) == "3 min"


def test_format_retry_delay_compound() -> None:
    assert format_retry_delay(90) == "1 min 30 s"


# --- tag helpers ---


def test_is_already_sent_true_when_marker_present() -> None:
    config = _reply_config()
    text = "hello\n\n[Réponse envoyée le 17/07/2026 a 12:26]"

    assert is_already_sent(text, config) is True


def test_is_already_sent_false_without_marker() -> None:
    assert is_already_sent("hello", _reply_config()) is False


def test_has_failure_tag_true_when_marker_present() -> None:
    text = "hello\n\n[Echec d'envoi, nouvel essai dans 30 s]"

    assert has_failure_tag(text, _reply_config()) is True


def test_build_sent_text_appends_sent_tag() -> None:
    sent_at = datetime(2026, 7, 17, 12, 26)

    result = build_sent_text(">>RE: hello", _reply_config(), sent_at)

    assert result == ">>RE: hello\n\n[Réponse envoyée le 17/07/2026 a 12:26]"


def test_build_sent_text_removes_stale_failure_tag() -> None:
    original = ">>RE: hello\n\n[Echec d'envoi, nouvel essai dans 30 s]"
    sent_at = datetime(2026, 7, 17, 12, 26)

    result = build_sent_text(original, _reply_config(), sent_at)

    assert "[Echec d'envoi" not in result
    assert "[Réponse envoyée le 17/07/2026 a 12:26]" in result


def test_build_failure_text_appends_failure_tag() -> None:
    result = build_failure_text(">>RE: hello", _reply_config(), 30)

    assert result == ">>RE: hello\n\n[Echec d'envoi, nouvel essai dans 30 s]"


def test_build_failure_text_replaces_stale_failure_tag() -> None:
    original = ">>RE: hello\n\n[Echec d'envoi, nouvel essai dans 30 s]"

    result = build_failure_text(original, _reply_config(), 90)

    assert result.count("[Echec d'envoi") == 1
    assert "1 min 30 s" in result


# --- poll_and_send_replies ---


def _card(name: str = "SMS from +33612345678", card_id: str = "card-1") -> TrelloCard:
    return TrelloCard(id=card_id, name=name)


def _comment(text: str, comment_id: str = "action-1") -> TrelloComment:
    return TrelloComment(id=comment_id, text=text, date="2026-07-17T10:00:00.000Z")


@pytest.mark.asyncio
async def test_poll_and_send_replies_sends_sms_and_tags_comment_on_success() -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card()], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.update_comment", new=AsyncMock()) as mock_update_comment,
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_awaited_once_with("+33612345678", "Toujours interessé?")
    mock_update_comment.assert_awaited_once()
    _, args, kwargs = mock_update_comment.mock_calls[0]
    assert "[Réponse envoyée" in args[2]
    assert is_running_ref["value"] is False


@pytest.mark.asyncio
async def test_poll_and_send_replies_tags_failure_when_send_fails() -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=False, error="no signal")
    is_running_ref = {"value": False}

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card()], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.update_comment", new=AsyncMock()) as mock_update_comment,
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    mock_update_comment.assert_awaited_once()
    _, args, kwargs = mock_update_comment.mock_calls[0]
    assert "[Echec d'envoi" in args[2]


@pytest.mark.asyncio
async def test_poll_and_send_replies_refreshes_failure_tag_on_repeated_failure() -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=False, error="no signal")
    is_running_ref = {"value": False}
    stale_failure_comment = ">>RE: Toujours interessé?\n\n[Echec d'envoi, nouvel essai dans 30 s]"

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card()], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([_comment(stale_failure_comment)], None)),
        ),
        patch("pi_sms.reply.reply.update_comment", new=AsyncMock()) as mock_update_comment,
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    mock_update_comment.assert_awaited_once()
    _, args, kwargs = mock_update_comment.mock_calls[0]
    assert args[2].count("[Echec d'envoi") == 1


@pytest.mark.asyncio
async def test_poll_and_send_replies_retries_comment_update_after_transient_failure() -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    mock_update_comment = AsyncMock(
        side_effect=[
            TrelloResult(success=False, error="transient network error"),
            TrelloResult(success=True, action="updated"),
        ]
    )

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card()], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.update_comment", new=mock_update_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=AsyncMock()),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    assert mock_update_comment.await_count == 2


@pytest.mark.asyncio
async def test_poll_and_send_replies_uses_exponential_backoff_between_retries() -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    mock_update_comment = AsyncMock(
        return_value=TrelloResult(success=False, error="persistent network error")
    )
    mock_sleep = AsyncMock()

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card()], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.update_comment", new=mock_update_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=mock_sleep),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    sleep_delays = [call.args[0] for call in mock_sleep.await_args_list]
    assert sleep_delays == [2, 4, 8, 15]


@pytest.mark.asyncio
async def test_poll_and_send_replies_honors_retry_after_on_rate_limit() -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    mock_update_comment = AsyncMock(
        side_effect=[
            TrelloResult(
                success=False,
                error="rate limited",
                status_code=429,
                retry_after_seconds=12.0,
            ),
            TrelloResult(success=True, action="updated"),
        ]
    )
    mock_sleep = AsyncMock()

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card()], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.update_comment", new=mock_update_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=mock_sleep),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    mock_sleep.assert_awaited_once_with(12.0)


@pytest.mark.asyncio
async def test_poll_and_send_replies_gives_up_after_exhausting_update_retries() -> None:
    """If every tag-write attempt fails after a successful send, the comment stays
    untagged (a documented at-least-once trade-off: the next poll may resend).
    """
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    mock_update_comment = AsyncMock(
        return_value=TrelloResult(success=False, error="persistent network error")
    )

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card()], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.update_comment", new=mock_update_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=AsyncMock()),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    assert mock_update_comment.await_count == 5


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_already_sent_comment() -> None:
    modem = AsyncMock()
    is_running_ref = {"value": False}
    already_sent = ">>RE: hello\n\n[Réponse envoyée le 17/07/2026 a 12:26]"

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card()], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([_comment(already_sent)], None)),
        ),
        patch("pi_sms.reply.reply.update_comment", new=AsyncMock()) as mock_update_comment,
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()
    mock_update_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_comment_without_trigger() -> None:
    modem = AsyncMock()
    is_running_ref = {"value": False}

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card()], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([_comment("just an internal note")], None)),
        ),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_when_phone_not_replyable() -> None:
    modem = AsyncMock()
    is_running_ref = {"value": False}

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card(name="SMS from Free")], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([_comment(">>RE: hello")], None)),
        ),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_when_already_running() -> None:
    modem = AsyncMock()
    is_running_ref = {"value": True}

    with patch("pi_sms.reply.reply.list_open_cards", new=AsyncMock()) as mock_list_open_cards:
        await poll_and_send_replies(_config(), modem, is_running_ref)

    mock_list_open_cards.assert_not_awaited()
    assert is_running_ref["value"] is True


@pytest.mark.asyncio
async def test_poll_and_send_replies_leaves_state_when_card_lookup_fails() -> None:
    modem = AsyncMock()
    is_running_ref = {"value": False}

    with patch(
        "pi_sms.reply.reply.list_open_cards",
        new=AsyncMock(return_value=([], "boom")),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()
    assert is_running_ref["value"] is False


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_card_when_comments_lookup_fails_but_processes_others() -> (
    None
):
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    broken_card = _card(name="SMS from +33600000000", card_id="card-broken")
    healthy_card = _card(name="SMS from +33612345678", card_id="card-healthy")

    async def _list_card_comments(
        _config: Config, card_id: str, _client: httpx.AsyncClient
    ) -> tuple[list[TrelloComment], str | None]:
        if card_id == "card-broken":
            return [], "boom"
        return [_comment(">>RE: Toujours interessé?")], None

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([broken_card, healthy_card], None)),
        ),
        patch("pi_sms.reply.reply.list_card_comments", new=_list_card_comments),
        patch("pi_sms.reply.reply.update_comment", new=AsyncMock()),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_awaited_once_with("+33612345678", "Toujours interessé?")
    assert is_running_ref["value"] is False
