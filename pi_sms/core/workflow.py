"""Poll-and-process workflow orchestration.

Each poll: list the modem inbox, then for every message either delete it
outright (filtered) or create a Trello card and delete it on success. A
Trello failure leaves the message on the modem so the next poll retries it.
"""

from ..filter.filter import SmsFilter
from ..modem.hilink import HilinkClient
from ..trello.trello import create_card
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
            if sms_filter.is_excluded(message):
                debug_print(f"Filtered SMS from {message.phone} (matched exclude pattern)")
                await modem.delete_sms(message.index)
                continue

            result = await create_card(config.trello, message)
            if result.success:
                print(f"Created Trello card for SMS from {message.phone}")
                await modem.delete_sms(message.index)
            else:
                debug_print(
                    f"Failed to create Trello card for SMS from {message.phone}: {result.error}"
                )
                # Leave the message on the modem so the next poll retries it.
    finally:
        is_running_ref["value"] = False
