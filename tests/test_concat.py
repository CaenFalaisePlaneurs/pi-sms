"""Tests for pi_sms.modem.concat.assemble_inbox."""

from datetime import datetime

from pi_sms.modem.concat import assemble_inbox
from pi_sms.modem.sms import SmsMessage

_NOW = datetime(2026, 8, 24, 10, 0, 20)
_PHONE = "+33600000000"
_OTHER_PHONE = "+33611111111"


def _message(
    index: str,
    content: str,
    *,
    phone: str = _PHONE,
    date: str = "2026-08-24 10:00:00",
    sms_type: str = "1",
) -> SmsMessage:
    return SmsMessage(
        index=index,
        phone=phone,
        content=content,
        date=date,
        smstat="0",
        sms_type=sms_type,
    )


def test_assemble_inbox_passes_single_messages_through() -> None:
    first = _message("1", "Hello")
    second = _message("2", "World", phone=_OTHER_PHONE)

    assert assemble_inbox([first, second], now=_NOW) == [first, second]


def test_assemble_inbox_defers_young_multipart_parts() -> None:
    part = _message("40017", "Hello ", date="2026-08-24 10:00:15", sms_type="2")

    assert assemble_inbox([part], now=_NOW) == []


def test_assemble_inbox_passes_settled_single_multipart_through() -> None:
    merged = _message(
        "40017",
        "Hello from a long message",
        date="2026-08-24 10:00:00",
        sms_type="2",
    )

    ready = assemble_inbox([merged], now=_NOW)

    assert len(ready) == 1
    assert ready[0].content == "Hello from a long message"
    assert ready[0].indexes == ("40017",)


def test_assemble_inbox_joins_settled_parts_in_date_then_index_order() -> None:
    # Newest-first API order, and indexes not in part order, to match the
    # modem list that previously became out-of-order Trello comments.
    later = _message("40019", "two", date="2026-08-24 10:00:01", sms_type="2")
    earlier = _message("40018", "one", date="2026-08-24 10:00:00", sms_type="2")

    ready = assemble_inbox([later, earlier], now=_NOW)

    assert len(ready) == 1
    assert ready[0].content == "onetwo"
    assert ready[0].date == "2026-08-24 10:00:00"
    assert ready[0].index == "40018"
    assert ready[0].indexes == ("40018", "40019")


def test_assemble_inbox_same_date_parts_sort_by_numeric_index() -> None:
    part_two = _message("40019", "B", date="2026-08-24 10:00:00", sms_type="2")
    part_one = _message("40018", "A", date="2026-08-24 10:00:00", sms_type="2")

    ready = assemble_inbox([part_two, part_one], now=_NOW)

    assert ready[0].content == "AB"
    assert ready[0].indexes == ("40018", "40019")


def test_assemble_inbox_does_not_join_distinct_single_messages() -> None:
    first = _message("1", "Hello", date="2026-08-24 10:00:00")
    second = _message("2", "Again", date="2026-08-24 10:00:05")

    ready = assemble_inbox([first, second], now=_NOW)

    assert ready == [first, second]


def test_assemble_inbox_does_not_join_multipart_from_different_phones() -> None:
    from_a = _message("40017", "A", sms_type="2")
    from_b = _message("40018", "B", phone=_OTHER_PHONE, sms_type="2")

    ready = assemble_inbox([from_a, from_b], now=_NOW)

    assert [message.content for message in ready] == ["A", "B"]
    assert ready[0].phone == _PHONE
    assert ready[1].phone == _OTHER_PHONE


def test_assemble_inbox_passes_singles_while_deferring_young_multipart() -> None:
    single = _message("1", "Hello")
    young_part = _message("40017", "frag", date="2026-08-24 10:00:15", sms_type="2")

    ready = assemble_inbox([young_part, single], now=_NOW)

    assert ready == [single]


def test_assemble_inbox_treats_unparseable_dates_as_settled() -> None:
    part_one = _message("40017", "A", date="not-a-date", sms_type="2")
    part_two = _message("40018", "B", date="not-a-date", sms_type="2")

    ready = assemble_inbox([part_two, part_one], now=_NOW)

    assert len(ready) == 1
    assert ready[0].content == "AB"
    assert ready[0].indexes == ("40017", "40018")
