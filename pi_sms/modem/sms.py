"""SMS message model and HiLink XML parsing helpers."""

import re
from dataclasses import dataclass
from xml.etree import ElementTree

_REPLYABLE_PHONE_PATTERN = re.compile(r"^\+?[0-9]{4,15}$")


@dataclass
class SmsMessage:
    """A single SMS message read from the modem inbox."""

    index: str
    phone: str
    content: str
    date: str
    smstat: str


def parse_sms_list(xml_text: str) -> list[SmsMessage]:
    """Parse the HiLink `/api/sms/sms-list` XML response into SmsMessage objects.

    Args:
        xml_text: Raw XML response body

    Returns:
        List of parsed messages (empty list if the response has no messages or is malformed)
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    messages: list[SmsMessage] = []
    for message_el in root.findall(".//Message"):
        index = _text(message_el, "Index")
        phone = _text(message_el, "Phone")
        content = _text(message_el, "Content")
        date = _text(message_el, "Date")
        smstat = _text(message_el, "Smstat")
        if index is None:
            continue
        messages.append(
            SmsMessage(
                index=index,
                phone=phone or "",
                content=content or "",
                date=date or "",
                smstat=smstat or "",
            )
        )
    return messages


def is_mms(message: SmsMessage) -> bool:
    """Return True if a message looks like an MMS/WAP-push notification.

    The E3372 HiLink modem cannot retrieve MMS content: an incoming MMS
    surfaces in the inbox as a message with an empty Content but a valid
    sender Phone, so an empty content is the only available signal.
    """
    return message.content.strip() == ""


def is_replyable_sender(phone: str) -> bool:
    """Return True if a phone number looks like a real MSISDN we can SMS back.

    Alphanumeric sender IDs (e.g. "Free") and short codes are send-only or not
    associated with a real subscriber, so replying to them is pointless.
    """
    return bool(_REPLYABLE_PHONE_PATTERN.match(phone.strip()))


def _text(element: ElementTree.Element, tag: str) -> str | None:
    """Return the stripped text content of a child element, or None if absent."""
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip()
