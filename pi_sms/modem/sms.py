"""SMS message model and HiLink XML parsing helpers."""

from dataclasses import dataclass
from xml.etree import ElementTree


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


def _text(element: ElementTree.Element, tag: str) -> str | None:
    """Return the stripped text content of a child element, or None if absent."""
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip()
