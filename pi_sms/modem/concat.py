"""Assemble concatenated SMS parts from a HiLink inbox snapshot.

The firmware can merge multipart segments (`SmsType=2`) on its own, but only
if they are left on the modem for a few seconds. Processing (and deleting) a
part during that window races the merge and leaves fragments as separate
Trello comments, often in API newest-first order.

Young multipart groups are therefore omitted from the returned list so the
next poll can see either a firmware-merged message or aged leftovers. Settled
leftovers from the same sender are joined in date-then-index order.
"""

from datetime import datetime, timedelta

from .sms import SmsMessage

_MULTIPART_SMS_TYPE = "2"
_MODEM_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_DEFAULT_SETTLE_SECONDS = 10.0


def assemble_inbox(
    messages: list[SmsMessage],
    *,
    now: datetime | None = None,
    settle_seconds: float = _DEFAULT_SETTLE_SECONDS,
) -> list[SmsMessage]:
    """Return inbox messages ready to post, assembling leftover multipart SMS.

    Args:
        messages: Raw inbox snapshot from the modem
        now: Clock used to decide whether multipart parts are still arriving;
            defaults to naive local `datetime.now()` to match modem `Date`
            strings, which have no timezone
        settle_seconds: Age under which a sender's newest multipart part is
            treated as still arriving

    Returns:
        Singles unchanged, plus settled multipart groups joined into one
        message each. Young multipart groups are omitted so they stay on the
        modem for the firmware (or the next poll) to finish assembling.
    """
    clock = now if now is not None else datetime.now()
    grouped: dict[str, list[SmsMessage]] = {}
    phone_order: list[str] = []
    for message in messages:
        if message.phone not in grouped:
            grouped[message.phone] = []
            phone_order.append(message.phone)
        grouped[message.phone].append(message)

    ready: list[SmsMessage] = []
    for phone in phone_order:
        group = grouped[phone]
        singles = [message for message in group if message.sms_type != _MULTIPART_SMS_TYPE]
        parts = [message for message in group if message.sms_type == _MULTIPART_SMS_TYPE]
        ready.extend(singles)
        if not parts:
            continue
        if _is_still_arriving(parts, clock, settle_seconds):
            continue
        ready.append(_join_parts(parts))
    return ready


def _is_still_arriving(parts: list[SmsMessage], now: datetime, settle_seconds: float) -> bool:
    """Return True if the newest parseable part is younger than the settle window.

    Unparseable dates are ignored so a malformed timestamp cannot keep a
    group in the inbox forever; if every date fails to parse, the group is
    treated as settled.
    """
    newest = _newest_parsed_date(parts)
    if newest is None:
        return False
    return newest + timedelta(seconds=settle_seconds) > now


def _newest_parsed_date(parts: list[SmsMessage]) -> datetime | None:
    parsed = [parsed_date for message in parts if (parsed_date := _parse_modem_date(message.date))]
    if not parsed:
        return None
    return max(parsed)


def _parse_modem_date(date: str) -> datetime | None:
    try:
        return datetime.strptime(date, _MODEM_DATE_FORMAT)
    except ValueError:
        return None


def _join_parts(parts: list[SmsMessage]) -> SmsMessage:
    ordered = sorted(parts, key=_part_sort_key)
    first = ordered[0]
    return SmsMessage(
        index=first.index,
        phone=first.phone,
        content="".join(part.content for part in ordered),
        date=first.date,
        smstat=first.smstat,
        sms_type=first.sms_type,
        indexes=tuple(index for part in ordered for index in part.indexes),
    )


def _part_sort_key(message: SmsMessage) -> tuple[str, int, int | str]:
    # Numeric indexes sort as integers so "9" precedes "10"; non-numeric
    # indexes are kept but ranked after numeric ones so the two kinds are
    # never compared directly.
    if message.index.isdigit():
        return (message.date, 0, int(message.index))
    return (message.date, 1, message.index)
