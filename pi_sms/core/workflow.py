"""Poll-and-process workflow orchestration.

Each poll: list the modem inbox, then for every message either handle it as
a detected MMS (auto-reply and delete), delete it outright (filtered), or
create a Trello card and delete it on success. A Trello or MMS-reply failure
leaves the message on the modem so the next poll retries it.
"""

from ..filter.filter import SmsFilter
from ..modem.hilink import HilinkClient
from ..modem.sms import SmsMessage, is_mms, is_replyable_sender
from ..trello.trello import record_sms
from .config import Config
from .debug import debug_print


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
        messages = await modem.list_inbox()
        if not messages:
            debug_print("Poll: no messages in inbox")
            return

        debug_print(f"Poll: {len(messages)} message(s) in inbox")
        for message in messages:
            if config.mms.enabled and is_mms(message):
                await _handle_mms(config, modem, message)
                continue

            if sms_filter.is_excluded(message):
                debug_print(f"Filtered SMS from {message.phone} (matched exclude pattern)")
                await modem.delete_sms(message.index)
                continue

            result = await record_sms(config.trello, message)
            if result.success:
                if result.action == "commented":
                    print(f"Added SMS from {message.phone} to existing card")
                else:
                    print(f"Created Trello card for SMS from {message.phone}")
                await modem.delete_sms(message.index)
            else:
                debug_print(
                    f"Failed to record Trello card for SMS from {message.phone}: {result.error}"
                )
                # Leave the message on the modem so the next poll retries it.
    finally:
        is_running_ref["value"] = False


async def _handle_mms(config: Config, modem: HilinkClient, message: SmsMessage) -> None:
    """Handle a detected MMS: reply asking for plain text, then delete it.

    A non-replyable sender (alphanumeric ID, short code) cannot receive a
    reply and carries no readable content, so the message is simply deleted.
    No Trello card is created for an MMS; a plain-text follow-up from the
    sender creates one through the normal flow.
    """
    if not is_replyable_sender(message.phone):
        debug_print(f"Discarded unreadable MMS from non-replyable sender {message.phone}")
        await modem.delete_sms(message.index)
        return

    result = await modem.send_sms(message.phone, config.mms.reply_text)
    if not result.success:
        debug_print(f"Failed to send MMS auto-reply to {message.phone}: {result.error}")
        # Leave the message on the modem so the next poll retries the reply.
        return

    print(f"Sent MMS auto-reply to {message.phone}")
    await modem.delete_sms(message.index)
