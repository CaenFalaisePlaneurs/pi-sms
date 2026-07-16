"""Tests for pi_sms.filter.filter."""

from pi_sms.filter.filter import SmsFilter
from pi_sms.modem.sms import SmsMessage


def _message(content: str) -> SmsMessage:
    return SmsMessage(index="1", phone="+33600000000", content=content, date="", smstat="0")


def test_excludes_matching_free_voicemail_notification() -> None:
    sms_filter = SmsFilter(exclude_patterns=['^Messagerie "666" Free:'])

    message = _message('Messagerie "666" Free: vous avez un nouveau message vocal')

    assert sms_filter.is_excluded(message) is True


def test_keeps_message_not_matching_any_pattern() -> None:
    sms_filter = SmsFilter(exclude_patterns=['^Messagerie "666" Free:'])

    message = _message("Hello, this is a normal SMS")

    assert sms_filter.is_excluded(message) is False


def test_no_patterns_never_excludes() -> None:
    sms_filter = SmsFilter(exclude_patterns=[])

    assert sms_filter.is_excluded(_message("anything")) is False


def test_multiple_patterns_any_match_excludes() -> None:
    sms_filter = SmsFilter(exclude_patterns=["^FOO", "^BAR"])

    assert sms_filter.is_excluded(_message("BAR baz")) is True
    assert sms_filter.is_excluded(_message("nope")) is False
