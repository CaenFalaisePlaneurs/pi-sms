"""Tests for pi_sms.modem.sms XML parsing."""

from pi_sms.modem.sms import SmsMessage, is_mms, is_replyable_sender, parse_sms_list

_TWO_MESSAGE_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<response>
<Count>2</Count>
<Messages>
<Message>
<Smstat>0</Smstat>
<Index>1</Index>
<Phone>+33612345678</Phone>
<Content>Hello there</Content>
<Date>2026-07-15 19:35:12</Date>
<Sca></Sca>
<SaveType>0</SaveType>
<Priority>0</Priority>
<SmsType>1</SmsType>
</Message>
<Message>
<Smstat>1</Smstat>
<Index>2</Index>
<Phone>666</Phone>
<Content>Messagerie "666" Free: vous avez 1 nouveau message</Content>
<Date>2026-07-15 20:00:00</Date>
</Message>
</Messages>
</response>
"""


def test_parse_sms_list_returns_all_messages() -> None:
    messages = parse_sms_list(_TWO_MESSAGE_RESPONSE)

    assert len(messages) == 2
    assert messages[0].index == "1"
    assert messages[0].phone == "+33612345678"
    assert messages[0].content == "Hello there"
    assert messages[0].date == "2026-07-15 19:35:12"
    assert messages[0].smstat == "0"
    assert messages[1].index == "2"
    assert messages[1].content.startswith('Messagerie "666" Free')


def test_parse_sms_list_empty_inbox() -> None:
    xml_text = "<response><Count>0</Count><Messages></Messages></response>"

    assert parse_sms_list(xml_text) == []


def test_parse_sms_list_malformed_xml_returns_empty_list() -> None:
    assert parse_sms_list("not xml at all <<<") == []


def test_parse_sms_list_skips_message_without_index() -> None:
    xml_text = "<response><Messages><Message><Phone>1</Phone></Message></Messages></response>"

    assert parse_sms_list(xml_text) == []


def _message(content: str, phone: str = "+33612345678") -> SmsMessage:
    return SmsMessage(index="1", phone=phone, content=content, date="d", smstat="0")


def test_is_mms_true_for_empty_content() -> None:
    assert is_mms(_message("")) is True


def test_is_mms_true_for_whitespace_only_content() -> None:
    assert is_mms(_message("   ")) is True


def test_is_mms_false_for_text_content() -> None:
    assert is_mms(_message("Hello there")) is False


def test_is_replyable_sender_true_for_e164_number() -> None:
    assert is_replyable_sender("+33612345678") is True


def test_is_replyable_sender_true_for_plain_digits() -> None:
    assert is_replyable_sender("0612345678") is True


def test_is_replyable_sender_false_for_alphanumeric_sender_id() -> None:
    assert is_replyable_sender("Free") is False


def test_is_replyable_sender_false_for_empty_phone() -> None:
    assert is_replyable_sender("") is False


def test_is_replyable_sender_false_for_short_code() -> None:
    assert is_replyable_sender("666") is False
