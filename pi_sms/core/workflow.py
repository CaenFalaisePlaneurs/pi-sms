"""Poll-and-process workflow orchestration.

Each poll: list the modem inbox, assemble leftover concatenated-SMS parts,
then for every ready message either handle it as a detected MMS (auto-reply
and delete), delete it outright (filtered), or create a Trello card and
delete it on success. A Trello or MMS-reply failure leaves the message on
the modem so the next poll retries it. Young multipart parts are omitted
from processing so the firmware can finish merging them.
"""

from __future__ import annotations

import sqlite3

from ..filter.filter import SmsFilter
from ..modem.concat import assemble_inbox
from ..modem.hilink import HilinkClient
from ..modem.sms import SmsMessage, is_mms, is_replyable_sender
from ..reply.store import (
    clear_mms_auto_reply,
    has_mms_auto_reply,
    mark_mms_auto_reply,
    open_store,
)
from ..trello.trello import record_sms
from .config import Config
from .debug import debug_print

_MMS_DELETE_ATTEMPTS = 3


async def poll_and_process(
    config: Config,
    modem: HilinkClient,
    sms_filter: SmsFilter,
    is_running_ref: dict[str, bool],
) -> None:
    """Poll the modem inbox and process each message.

    Args:
        config: Configuration object
        modem: HiLink modem client
        sms_filter: Compiled SMS exclude-pattern filter
        is_running_ref: Dictionary with 'value' key to prevent concurrent polls
    """
    if is_running_ref.get("value", False):
        debug_print("Poll skipped: already running (previous poll still in progress)")
        return

    is_running_ref["value"] = True
    try:
        inbox = await modem.list_inbox()
        if not inbox:
            debug_print("Poll: no messages in inbox")
            return

        messages = assemble_inbox(inbox)
        debug_print(f"Poll: {len(inbox)} inbox message(s), {len(messages)} ready after assembly")

        mms_by_phone: dict[str, list[SmsMessage]] = {}
        others: list[SmsMessage] = []
        for message in messages:
            if config.mms.enabled and is_mms(message):
                mms_by_phone.setdefault(message.phone, []).append(message)
            else:
                others.append(message)

        if mms_by_phone:
            conn = open_store(config.reply.sqlite_path)
            try:
                for group in mms_by_phone.values():
                    await _handle_mms_group(config, modem, group, conn)
            finally:
                conn.close()

        for message in others:
            if sms_filter.is_excluded(message):
                debug_print(f"Filtered SMS from {message.phone} (matched exclude pattern)")
                await _delete_indexes(modem, message.indexes)
                continue

            result = await record_sms(config.trello, message)
            if result.success:
                if result.action == "commented":
                    print(f"Added SMS from {message.phone} to existing card")
                else:
                    print(f"Created Trello card for SMS from {message.phone}")
                await _delete_indexes(modem, message.indexes)
            else:
                debug_print(
                    f"Failed to record Trello card for SMS from {message.phone}: {result.error}"
                )
                # Leave the message on the modem so the next poll retries it.
    finally:
        is_running_ref["value"] = False


async def _handle_mms_group(
    config: Config,
    modem: HilinkClient,
    messages: list[SmsMessage],
    conn: sqlite3.Connection,
) -> None:
    """Send at most one MMS auto-reply for this sender's empty inbox rows.

    Multiple empty rows in one poll (or leftovers after a failed delete) are
    treated as the same MMS event. A successful send is recorded per inbox
    index so a later poll retries delete without sending another SMS.
    """
    phone = messages[0].phone
    already_replied, pending = _partition_mms_messages(conn, messages)
    await _delete_mms_indexes(modem, conn, already_replied)

    if not pending:
        return

    pending_indexes = _message_indexes(pending)
    if not is_replyable_sender(phone, config.mms.ignore_sender_max_digits):
        debug_print(f"Discarded unreadable MMS from non-replyable sender {phone}")
        await _delete_indexes(modem, pending_indexes)
        return

    result = await modem.send_sms(phone, config.mms.reply_text)
    if not result.success:
        debug_print(f"Failed to send MMS auto-reply to {phone}: {result.error}")
        # Leave the message on the modem so the next poll retries the reply.
        return

    for index in pending_indexes:
        mark_mms_auto_reply(conn, index, phone)
    print(f"Sent MMS auto-reply to {phone}")
    await _delete_mms_indexes(modem, conn, pending)


def _partition_mms_messages(
    conn: sqlite3.Connection, messages: list[SmsMessage]
) -> tuple[list[SmsMessage], list[SmsMessage]]:
    already_replied: list[SmsMessage] = []
    pending: list[SmsMessage] = []
    for message in messages:
        if all(has_mms_auto_reply(conn, index, message.phone) for index in message.indexes):
            already_replied.append(message)
        else:
            pending.append(message)
    return already_replied, pending


def _message_indexes(messages: list[SmsMessage]) -> tuple[str, ...]:
    return tuple(index for message in messages for index in message.indexes)


async def _delete_mms_indexes(
    modem: HilinkClient, conn: sqlite3.Connection, messages: list[SmsMessage]
) -> None:
    """Delete MMS inbox rows and drop auto-reply markers for rows that actually left."""
    for index in _message_indexes(messages):
        if await _delete_index_with_retries(modem, index):
            clear_mms_auto_reply(conn, index)


async def _delete_index_with_retries(modem: HilinkClient, index: str) -> bool:
    for _ in range(_MMS_DELETE_ATTEMPTS):
        result = await modem.delete_sms(index)
        if result.success:
            return True
        debug_print(f"Failed to delete MMS inbox index {index}: {result.error}")
    return False


async def _delete_indexes(modem: HilinkClient, indexes: tuple[str, ...]) -> None:
    """Delete every modem inbox index that belongs to a (possibly assembled) SMS."""
    for index in indexes:
        await modem.delete_sms(index)
