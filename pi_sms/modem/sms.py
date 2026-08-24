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
    sms_type: str = "1"
    # Every modem inbox index that makes up this message; a concatenated SMS
    # that we assembled from leftover parts lists all of them so the workflow
    # can delete each segment after a successful Trello post.
    indexes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.indexes:
            self.indexes = (self.index,)


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
        sms_type = _text(message_el, "SmsType")
        if index is None:
            continue
        messages.append(
            SmsMessage(
                index=index,
                phone=phone or "",
                content=content or "",
                date=date or "",
                smstat=smstat or "",
                sms_type=sms_type or "1",
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


def find_replyable_phone(text: str) -> str | None:
    """Return the first MSISDN-like token in text that we can SMS back.

    Used to recover the sender phone number embedded in a Trello card name
    (the reverse of `TrelloConfig.card_name_template`), so a reply feature
    can send an SMS without a separate phone-to-card index.

    Returns the *first* matching token, so this assumes `{phone}` comes
    before any other numeric placeholder (e.g. `{date}`) in
    `card_name_template`; the default `"SMS from {phone}"` satisfies this,
    but a reordered template with a numeric field ahead of `{phone}` could
    cause the wrong token to be picked up.
    """
    for candidate in re.findall(r"\+?\d{4,15}", text):
        candidate_str = str(candidate)
        if is_replyable_sender(candidate_str):
            return candidate_str
    return None


def _text(element: ElementTree.Element, tag: str) -> str | None:
    """Return the stripped text content of a child element, or None if absent."""
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip()
