"""SMS content filtering based on configured exclude patterns."""

import re

from ..modem.sms import SmsMessage


class SmsFilter:
    """Compiles exclude patterns once and matches SMS content against them."""

    def __init__(self, exclude_patterns: list[str]) -> None:
        self._compiled_patterns = [re.compile(pattern) for pattern in exclude_patterns]

    def is_excluded(self, message: SmsMessage) -> bool:
        """Return True if the message content matches any exclude pattern."""
        return any(pattern.search(message.content) for pattern in self._compiled_patterns)
