# Changelog

## [Unreleased]

### Added

- One-command installer (`scripts/install.sh`) that installs base tools, creates a virtual environment, installs the package from GitHub, and runs setup non-interactively
- One Trello card per phone number: the first SMS from a number creates a card, and later SMS from the same number are appended as comments, so a card reads as a conversation thread (configurable via the new `trello.card_comment_template`)
- `mms.ignore_sender_max_digits` (default 5) to override how many digits count as a short code that should not receive an MMS auto-reply or a Trello SMS reply

### Changed

- Modem and LAN NetworkManager connections are now bound to MAC addresses instead of interface names, avoiding a boot-time `eth0`/`eth1` naming race where the modem's DHCP server could hijack the Pi's default route and drop it off the LAN
- Reply polling reads new board comments since a persisted SQLite cursor instead of listing comments on every open card each cycle
- The MMS auto-reply text now also covers long texts that a phone sends as MMS, not only photos and attachments

### Fixed

- The one-command installer now force-reinstalls `pi-sms` (without reinstalling unchanged dependencies) when it detects an existing installation, since a plain `pip install git+URL` silently skips reinstalling an already-installed package name even when the underlying commit changed
- Trello reply comments containing an emoji (e.g. `:heart:`) no longer send the literal shortcode text as the SMS body; Trello stores comment text as markdown source, so typing or autocompleting an emoji leaves the `:shortcode:` syntax in the raw API text, and it is now resolved back into the actual character before sending
- Long SMS that arrive as several modem inbox parts are posted as a single Trello comment: young multipart segments are left on the modem so firmware can merge them, and leftover parts from the same sender are joined in date-then-index order
- An incoming MMS now triggers at most one auto-reply SMS: empty inbox rows from the same sender are grouped, and a successful send is recorded so a failed modem delete retries cleanup without sending again
- Senders of up to 5 digits (French short codes such as `2051`) are treated as non-replyable, so they no longer receive an MMS auto-reply SMS

## [0.1.0] - 2026-07-16

### Added

- Initial release: SMS-to-Trello daemon for Raspberry Pi with a Huawei E3372 HiLink modem. Polls the modem's SMS inbox, filters out known-noise messages (e.g. Free Mobile voicemail notifications), creates a Trello card for each remaining message, and deletes the message from the modem once handled.

[0.1.0]: https://github.com/nmassart/pi-sms/releases/tag/v0.1.0
