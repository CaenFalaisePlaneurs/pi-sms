"""Tests for pi_sms.modem.sms XML parsing."""

from pi_sms.modem.sms import parse_sms_list

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
