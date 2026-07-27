"""Tests for pi_sms.reply.text and pi_sms.reply.reply."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from pi_sms.core.config import Config, ReplyConfig, TrelloConfig
from pi_sms.modem.hilink import HilinkResult
from pi_sms.reply.reply import poll_and_send_replies
from pi_sms.reply.text import (
    build_failure_notice,
    build_sent_confirmation,
    format_retry_delay,
    has_failure_notice,
    is_reply_already_sent,
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


# --- status comment helpers ---


def _status_comment(text: str, comment_id: str = "status-1") -> TrelloComment:
    return TrelloComment(id=comment_id, text=text, date="2026-07-17T10:00:00.000Z")


def test_is_reply_already_sent_true_when_referencing_comment() -> None:
    config = _reply_config()
    comments = [_status_comment("[Réponse envoyée le 17/07/2026 a 12:26] (réf: trigger-1)")]

    assert is_reply_already_sent(comments, "trigger-1", config) is True


def test_is_reply_already_sent_false_for_different_comment_id() -> None:
    config = _reply_config()
    comments = [_status_comment("[Réponse envoyée le 17/07/2026 a 12:26] (réf: trigger-other)")]

    assert is_reply_already_sent(comments, "trigger-1", config) is False


def test_is_reply_already_sent_false_without_any_status_comment() -> None:
    assert is_reply_already_sent([], "trigger-1", _reply_config()) is False


def test_has_failure_notice_true_when_referencing_comment() -> None:
    config = _reply_config()
    comments = [_status_comment("[Echec d'envoi, nouvelle tentative en cours] (réf: trigger-1)")]

    assert has_failure_notice(comments, "trigger-1", config) is True


def test_has_failure_notice_false_for_different_comment_id() -> None:
    config = _reply_config()
    comments = [
        _status_comment("[Echec d'envoi, nouvelle tentative en cours] (réf: trigger-other)")
    ]

    assert has_failure_notice(comments, "trigger-1", config) is False


def test_build_sent_confirmation_references_trigger_comment() -> None:
    sent_at = datetime(2026, 7, 17, 12, 26)

    result = build_sent_confirmation("trigger-1", _reply_config(), sent_at)

    assert result == "[Réponse envoyée le 17/07/2026 a 12:26] (réf: trigger-1)"


def test_build_failure_notice_references_trigger_comment() -> None:
    result = build_failure_notice("trigger-1", _reply_config())

    assert result == "[Echec d'envoi, nouvelle tentative en cours] (réf: trigger-1)"


# --- poll_and_send_replies ---


def _card(name: str = "SMS from +33612345678", card_id: str = "card-1") -> TrelloCard:
    return TrelloCard(id=card_id, name=name)


def _comment(text: str, comment_id: str = "action-1") -> TrelloComment:
    return TrelloComment(id=comment_id, text=text, date="2026-07-17T10:00:00.000Z")


@pytest.mark.asyncio
async def test_poll_and_send_replies_sends_sms_and_posts_sent_confirmation() -> None:
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
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()) as mock_post_comment,
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_awaited_once_with("+33612345678", "Toujours interessé?")
    mock_post_comment.assert_awaited_once()
    _, args, kwargs = mock_post_comment.mock_calls[0]
    assert args[0] == _config().trello
    assert args[1] == "card-1"
    assert "[Réponse envoyée" in args[2]
    assert "action-1" in args[2]
    assert is_running_ref["value"] is False


@pytest.mark.asyncio
async def test_poll_and_send_replies_posts_failure_notice_when_send_fails() -> None:
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
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()) as mock_post_comment,
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    mock_post_comment.assert_awaited_once()
    _, args, kwargs = mock_post_comment.mock_calls[0]
    assert "[Echec d'envoi" in args[2]
    assert "action-1" in args[2]


@pytest.mark.asyncio
async def test_poll_and_send_replies_does_not_repost_failure_notice_but_keeps_retrying_send() -> (
    None
):
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=False, error="no signal")
    is_running_ref = {"value": False}
    trigger_comment = _comment(">>RE: Toujours interessé?")
    existing_failure_notice = _status_comment(
        "[Echec d'envoi, nouvelle tentative en cours] (réf: action-1)", comment_id="status-1"
    )

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card()], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([trigger_comment, existing_failure_notice], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()) as mock_post_comment,
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_awaited_once_with("+33612345678", "Toujours interessé?")
    mock_post_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_sends_and_confirms_after_a_prior_failure_notice() -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    trigger_comment = _comment(">>RE: Toujours interessé?")
    existing_failure_notice = _status_comment(
        "[Echec d'envoi, nouvelle tentative en cours] (réf: action-1)", comment_id="status-1"
    )

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card()], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([trigger_comment, existing_failure_notice], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()) as mock_post_comment,
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_awaited_once_with("+33612345678", "Toujours interessé?")
    mock_post_comment.assert_awaited_once()
    _, args, kwargs = mock_post_comment.mock_calls[0]
    assert "[Réponse envoyée" in args[2]


@pytest.mark.asyncio
async def test_poll_and_send_replies_retries_status_comment_post_after_transient_failure() -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    mock_post_comment = AsyncMock(
        side_effect=[
            TrelloResult(success=False, error="transient network error"),
            TrelloResult(success=True, action="commented"),
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
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=AsyncMock()),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    assert mock_post_comment.await_count == 2


@pytest.mark.asyncio
async def test_poll_and_send_replies_uses_exponential_backoff_between_retries() -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    mock_post_comment = AsyncMock(
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
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
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
    mock_post_comment = AsyncMock(
        side_effect=[
            TrelloResult(
                success=False,
                error="rate limited",
                status_code=429,
                retry_after_seconds=12.0,
            ),
            TrelloResult(success=True, action="commented"),
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
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=mock_sleep),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    mock_sleep.assert_awaited_once_with(12.0)


@pytest.mark.asyncio
async def test_poll_and_send_replies_stops_retrying_status_comment_on_permanent_error() -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    mock_post_comment = AsyncMock(
        return_value=TrelloResult(success=False, error="unauthorized", status_code=401)
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
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=mock_sleep),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    mock_post_comment.assert_awaited_once()
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_keeps_retrying_status_comment_on_server_error() -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    mock_post_comment = AsyncMock(
        return_value=TrelloResult(success=False, error="server error", status_code=500)
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
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=AsyncMock()),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    assert mock_post_comment.await_count == 5


@pytest.mark.asyncio
async def test_poll_and_send_replies_gives_up_after_exhausting_confirmation_retries() -> None:
    """If every confirmation-post attempt fails after a successful send, the trigger stays
    unconfirmed (a documented at-least-once trade-off: the next poll may resend).
    """
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    mock_post_comment = AsyncMock(
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
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=AsyncMock()),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    assert mock_post_comment.await_count == 5


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_already_sent_comment() -> None:
    modem = AsyncMock()
    is_running_ref = {"value": False}
    trigger_comment = _comment(">>RE: hello")
    sent_confirmation = _status_comment(
        "[Réponse envoyée le 17/07/2026 a 12:26] (réf: action-1)", comment_id="status-1"
    )

    with (
        patch(
            "pi_sms.reply.reply.list_open_cards",
            new=AsyncMock(return_value=([_card()], None)),
        ),
        patch(
            "pi_sms.reply.reply.list_card_comments",
            new=AsyncMock(return_value=([trigger_comment, sent_confirmation], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()) as mock_post_comment,
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()
    mock_post_comment.assert_not_awaited()


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
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()),
    ):
        await poll_and_send_replies(_config(), modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_awaited_once_with("+33612345678", "Toujours interessé?")
    assert is_running_ref["value"] is False
