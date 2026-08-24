"""Tests for pi_sms.reply.text and pi_sms.reply.reply."""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from pi_sms.core.config import Config, ReplyConfig, TrelloConfig
from pi_sms.modem.hilink import HilinkResult
from pi_sms.reply.reply import poll_and_send_replies
from pi_sms.reply.store import (
    STATUS_PENDING,
    STATUS_SENT,
    STATUS_SENT_UNCONFIRMED,
    get_reply,
    insert_reply,
    open_store,
    set_board_id,
    set_last_action_id,
)
from pi_sms.reply.text import (
    build_failure_notice,
    build_sent_confirmation,
    contains_emoji_shortcode,
    format_retry_delay,
    has_failure_notice,
    is_reply_already_sent,
    parse_reply,
    resolve_emoji_shortcodes,
)
from pi_sms.trello.trello import TrelloCard, TrelloComment, TrelloResult

_TRIGGER = ">>RE:"


def _reply_config() -> ReplyConfig:
    return ReplyConfig()


def _config(tmp_path: Path) -> Config:
    return Config(
        trello=TrelloConfig(key="k", token="t", list_id="l"),
        reply=ReplyConfig(sqlite_path=str(tmp_path / "reply.sqlite")),
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


# --- contains_emoji_shortcode / resolve_emoji_shortcodes ---


def test_contains_emoji_shortcode_true_when_present() -> None:
    assert contains_emoji_shortcode("reponse avec emotion :+1: :heart:") is True


def test_contains_emoji_shortcode_false_when_absent() -> None:
    assert contains_emoji_shortcode("just a normal reply") is False


def test_resolve_emoji_shortcodes_replaces_known_shortcodes() -> None:
    emoji_map = {"+1": "\U0001f44d", "heart": "\u2764\ufe0f"}

    resolved = resolve_emoji_shortcodes("reponse avec emotion :+1: :heart:", emoji_map)

    assert resolved == "reponse avec emotion \U0001f44d \u2764\ufe0f"


def test_resolve_emoji_shortcodes_leaves_unknown_shortcode_untouched() -> None:
    resolved = resolve_emoji_shortcodes("see :unknown-thing:", {"heart": "\u2764\ufe0f"})

    assert resolved == "see :unknown-thing:"


def test_resolve_emoji_shortcodes_is_noop_without_any_shortcode() -> None:
    assert resolve_emoji_shortcodes("just text", {"heart": "\u2764\ufe0f"}) == "just text"


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


def _board_comment(
    text: str,
    comment_id: str = "action-1",
    card_id: str = "card-1",
    card_name: str = "SMS from +33612345678",
    list_id: str = "l",
) -> TrelloComment:
    return TrelloComment(
        id=comment_id,
        text=text,
        date="2026-07-17T10:00:00.000Z",
        card_id=card_id,
        card_name=card_name,
        list_id=list_id,
    )


def _seed_cursor(sqlite_path: str, last_action_id: str = "cursor-0") -> None:
    conn = open_store(sqlite_path)
    set_board_id(conn, "board-1")
    set_last_action_id(conn, last_action_id)
    conn.close()


@contextmanager
def _bootstrap_patches(
    *,
    cards: list[TrelloCard] | tuple[list[TrelloCard], str | None],
    comments: object,
    post_comment: object | None = None,
    latest_id: str | None = "latest-1",
) -> Iterator[None]:
    if isinstance(cards, tuple):
        list_cards = AsyncMock(return_value=cards)
    else:
        list_cards = AsyncMock(return_value=(cards, None))
    patches = [
        patch(
            "pi_sms.reply.reply.get_list_board_id",
            new=AsyncMock(return_value=("board-1", None)),
        ),
        patch(
            "pi_sms.reply.reply.get_latest_board_comment_id",
            new=AsyncMock(return_value=(latest_id, None)),
        ),
        patch("pi_sms.reply.reply.list_open_cards", new=list_cards),
        patch("pi_sms.reply.reply.list_card_comments", new=comments),
    ]
    if post_comment is not None:
        patches.append(patch("pi_sms.reply.reply.post_comment", new=post_comment))
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        yield


@pytest.mark.asyncio
async def test_poll_and_send_replies_sends_sms_and_posts_sent_confirmation(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([_board_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()) as mock_post_comment,
        patch("pi_sms.reply.reply.list_open_cards", new=AsyncMock()) as mock_list_cards,
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_awaited_once_with("+33612345678", "Toujours interessé?")
    mock_post_comment.assert_awaited_once()
    mock_list_cards.assert_not_awaited()
    _, args, _kwargs = mock_post_comment.mock_calls[0]
    assert args[1] == "card-1"
    assert "[Réponse envoyée" in args[2]
    assert "action-1" in args[2]
    assert is_running_ref["value"] is False


@pytest.mark.asyncio
async def test_poll_and_send_replies_bootstraps_full_scan_then_goes_incremental(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)

    with _bootstrap_patches(
        cards=[_card()],
        comments=AsyncMock(return_value=([_comment(">>RE: first")], None)),
        post_comment=AsyncMock(),
    ):
        with patch("pi_sms.reply.reply.list_board_comments_since", new=AsyncMock()) as mock_since:
            await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_awaited_once_with("+33612345678", "first")
    mock_since.assert_not_awaited()

    is_running_ref["value"] = False
    modem.send_sms.reset_mock()
    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(
                return_value=([_board_comment(">>RE: second", comment_id="action-2")], None)
            ),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()),
        patch("pi_sms.reply.reply.list_open_cards", new=AsyncMock()) as mock_cards_again,
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_awaited_once_with("+33612345678", "second")
    mock_cards_again.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_posts_failure_notice_when_send_fails(tmp_path: Path) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=False, error="no signal")
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([_board_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()) as mock_post_comment,
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    mock_post_comment.assert_awaited_once()
    _, args, _kwargs = mock_post_comment.mock_calls[0]
    assert "[Echec d'envoi" in args[2]
    assert "action-1" in args[2]


@pytest.mark.asyncio
async def test_poll_and_send_replies_does_not_repost_failure_notice_but_keeps_retrying_send(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=False, error="no signal")
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)
    conn = open_store(config.reply.sqlite_path)
    insert_reply(
        conn,
        trigger_comment_id="action-1",
        card_id="card-1",
        card_name="SMS from +33612345678",
        body="Toujours interessé?",
        status=STATUS_PENDING,
        failure_notice_posted=True,
    )
    conn.close()

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()) as mock_post_comment,
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_awaited_once_with("+33612345678", "Toujours interessé?")
    mock_post_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_sends_and_confirms_after_a_prior_failure_notice(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)
    conn = open_store(config.reply.sqlite_path)
    insert_reply(
        conn,
        trigger_comment_id="action-1",
        card_id="card-1",
        card_name="SMS from +33612345678",
        body="Toujours interessé?",
        status=STATUS_PENDING,
        failure_notice_posted=True,
    )
    conn.close()

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()) as mock_post_comment,
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_awaited_once_with("+33612345678", "Toujours interessé?")
    mock_post_comment.assert_awaited_once()
    _, args, _kwargs = mock_post_comment.mock_calls[0]
    assert "[Réponse envoyée" in args[2]


@pytest.mark.asyncio
async def test_poll_and_send_replies_retries_confirmation_only_when_sent_unconfirmed(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)
    conn = open_store(config.reply.sqlite_path)
    insert_reply(
        conn,
        trigger_comment_id="action-1",
        card_id="card-1",
        card_name="SMS from +33612345678",
        body="Toujours interessé?",
        status=STATUS_SENT_UNCONFIRMED,
    )
    conn.close()

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()) as mock_post_comment,
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()
    mock_post_comment.assert_awaited_once()
    conn = open_store(config.reply.sqlite_path)
    record = get_reply(conn, "action-1")
    conn.close()
    assert record is not None
    assert record.status == STATUS_SENT


@pytest.mark.asyncio
async def test_poll_and_send_replies_retries_status_comment_post_after_transient_failure(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)
    mock_post_comment = AsyncMock(
        side_effect=[
            TrelloResult(success=False, error="transient network error"),
            TrelloResult(success=True, action="commented"),
        ]
    )

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([_board_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=AsyncMock()),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    assert mock_post_comment.await_count == 2


@pytest.mark.asyncio
async def test_poll_and_send_replies_uses_exponential_backoff_between_retries(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)
    mock_post_comment = AsyncMock(
        return_value=TrelloResult(success=False, error="persistent network error")
    )
    mock_sleep = AsyncMock()

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([_board_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=mock_sleep),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    sleep_delays = [call.args[0] for call in mock_sleep.await_args_list]
    assert sleep_delays == [2, 4, 8, 15]


@pytest.mark.asyncio
async def test_poll_and_send_replies_honors_retry_after_on_rate_limit(tmp_path: Path) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)
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
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([_board_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=mock_sleep),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    mock_sleep.assert_awaited_once_with(12.0)


@pytest.mark.asyncio
async def test_poll_and_send_replies_stops_retrying_status_comment_on_permanent_error(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)
    mock_post_comment = AsyncMock(
        return_value=TrelloResult(success=False, error="unauthorized", status_code=401)
    )
    mock_sleep = AsyncMock()

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([_board_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=mock_sleep),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    mock_post_comment.assert_awaited_once()
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_keeps_retrying_status_comment_on_server_error(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)
    mock_post_comment = AsyncMock(
        return_value=TrelloResult(success=False, error="server error", status_code=500)
    )

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([_board_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=AsyncMock()),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    assert mock_post_comment.await_count == 5


@pytest.mark.asyncio
async def test_poll_and_send_replies_does_not_resend_after_exhausting_confirmation_retries(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)
    mock_post_comment = AsyncMock(
        return_value=TrelloResult(success=False, error="persistent network error")
    )

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([_board_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
        patch("pi_sms.reply.reply.asyncio.sleep", new=AsyncMock()),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    assert mock_post_comment.await_count == 5
    assert modem.send_sms.await_count == 1

    is_running_ref["value"] = False
    modem.send_sms.reset_mock()
    mock_post_comment.reset_mock()
    mock_post_comment.return_value = TrelloResult(success=True, action="commented")
    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=mock_post_comment),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()
    mock_post_comment.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_and_send_replies_resolves_emoji_shortcodes_before_sending(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)
    emoji_map = {"+1": "\U0001f44d", "heart": "\u2764\ufe0f"}

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(
                return_value=([_board_comment(">>RE: reponse avec emotion :+1: :heart:")], None)
            ),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()),
        patch(
            "pi_sms.reply.reply.fetch_emoji_map", new=AsyncMock(return_value=(emoji_map, None))
        ) as mock_fetch_emoji_map,
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    mock_fetch_emoji_map.assert_awaited_once()
    modem.send_sms.assert_awaited_once_with(
        "+33612345678", "reponse avec emotion \U0001f44d \u2764\ufe0f"
    )


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_emoji_map_fetch_without_shortcode(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([_board_comment(">>RE: Toujours interessé?")], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()),
        patch("pi_sms.reply.reply.fetch_emoji_map", new=AsyncMock()) as mock_fetch_emoji_map,
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    mock_fetch_emoji_map.assert_not_awaited()
    modem.send_sms.assert_awaited_once_with("+33612345678", "Toujours interessé?")


@pytest.mark.asyncio
async def test_poll_and_send_replies_reuses_cached_emoji_map_across_calls(tmp_path: Path) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)
    emoji_cache_ref: dict[str, dict[str, str]] = {}
    emoji_map = {"heart": "\u2764\ufe0f"}

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(
                side_effect=[
                    ([_board_comment(">>RE: salut :heart:", comment_id="a1")], None),
                    ([_board_comment(">>RE: encore :heart:", comment_id="a2")], None),
                ]
            ),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()),
        patch(
            "pi_sms.reply.reply.fetch_emoji_map", new=AsyncMock(return_value=(emoji_map, None))
        ) as mock_fetch_emoji_map,
    ):
        await poll_and_send_replies(
            config,
            modem,
            is_running_ref,
            client=httpx.AsyncClient(),
            emoji_cache_ref=emoji_cache_ref,
        )
        is_running_ref["value"] = False
        await poll_and_send_replies(
            config,
            modem,
            is_running_ref,
            client=httpx.AsyncClient(),
            emoji_cache_ref=emoji_cache_ref,
        )

    mock_fetch_emoji_map.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_already_known_trigger_id(tmp_path: Path) -> None:
    modem = AsyncMock()
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)
    conn = open_store(config.reply.sqlite_path)
    insert_reply(
        conn,
        trigger_comment_id="action-1",
        card_id="card-1",
        card_name="SMS from +33612345678",
        body="hello",
        status=STATUS_SENT,
    )
    conn.close()

    with (
        patch(
            "pi_sms.reply.reply.list_board_comments_since",
            new=AsyncMock(return_value=([_board_comment(">>RE: hello")], None)),
        ),
        patch("pi_sms.reply.reply.post_comment", new=AsyncMock()) as mock_post_comment,
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()
    mock_post_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_already_sent_comment_during_bootstrap(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    trigger_comment = _comment(">>RE: hello")
    sent_confirmation = _status_comment(
        "[Réponse envoyée le 17/07/2026 a 12:26] (réf: action-1)", comment_id="status-1"
    )

    with (
        _bootstrap_patches(
            cards=[_card()],
            comments=AsyncMock(return_value=([trigger_comment, sent_confirmation], None)),
            post_comment=AsyncMock(),
        ),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_comment_without_trigger(tmp_path: Path) -> None:
    modem = AsyncMock()
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)

    with patch(
        "pi_sms.reply.reply.list_board_comments_since",
        new=AsyncMock(return_value=([_board_comment("just an internal note")], None)),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_ignores_comments_on_other_lists(tmp_path: Path) -> None:
    modem = AsyncMock()
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)

    with patch(
        "pi_sms.reply.reply.list_board_comments_since",
        new=AsyncMock(
            return_value=(
                [_board_comment(">>RE: hello", list_id="some-other-list")],
                None,
            )
        ),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_when_phone_not_replyable(tmp_path: Path) -> None:
    modem = AsyncMock()
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    _seed_cursor(config.reply.sqlite_path)

    with patch(
        "pi_sms.reply.reply.list_board_comments_since",
        new=AsyncMock(
            return_value=([_board_comment(">>RE: hello", card_name="SMS from Free")], None)
        ),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_when_already_running(tmp_path: Path) -> None:
    modem = AsyncMock()
    is_running_ref = {"value": True}

    with patch("pi_sms.reply.reply.get_list_board_id", new=AsyncMock()) as mock_board:
        await poll_and_send_replies(_config(tmp_path), modem, is_running_ref)

    mock_board.assert_not_awaited()
    assert is_running_ref["value"] is True


@pytest.mark.asyncio
async def test_poll_and_send_replies_leaves_state_when_card_lookup_fails(tmp_path: Path) -> None:
    modem = AsyncMock()
    is_running_ref = {"value": False}
    config = _config(tmp_path)

    with _bootstrap_patches(
        cards=([], "boom"),
        comments=AsyncMock(),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_not_awaited()
    assert is_running_ref["value"] is False


@pytest.mark.asyncio
async def test_poll_and_send_replies_skips_card_when_comments_lookup_fails_but_processes_others(
    tmp_path: Path,
) -> None:
    modem = AsyncMock()
    modem.send_sms.return_value = HilinkResult(success=True)
    is_running_ref = {"value": False}
    config = _config(tmp_path)
    broken_card = _card(name="SMS from +33600000000", card_id="card-broken")
    healthy_card = _card(name="SMS from +33612345678", card_id="card-healthy")

    async def _list_card_comments(
        _config: Config, card_id: str, _client: httpx.AsyncClient
    ) -> tuple[list[TrelloComment], str | None]:
        if card_id == "card-broken":
            return [], "boom"
        return [_comment(">>RE: Toujours interessé?")], None

    with _bootstrap_patches(
        cards=[broken_card, healthy_card],
        comments=_list_card_comments,
        post_comment=AsyncMock(),
    ):
        await poll_and_send_replies(config, modem, is_running_ref, client=httpx.AsyncClient())

    modem.send_sms.assert_awaited_once_with("+33612345678", "Toujours interessé?")
    assert is_running_ref["value"] is False
