# Changelog

## [Unreleased]

### Added

- One-command installer (`scripts/install.sh`) that installs base tools, creates a virtual environment, installs the package from GitHub, and runs setup non-interactively
- One Trello card per phone number: the first SMS from a number creates a card, and later SMS from the same number are appended as comments, so a card reads as a conversation thread (configurable via the new `trello.card_comment_template`)

### Changed

- Modem and LAN NetworkManager connections are now bound to MAC addresses instead of interface names, avoiding a boot-time `eth0`/`eth1` naming race where the modem's DHCP server could hijack the Pi's default route and drop it off the LAN

### Fixed

- The one-command installer now force-reinstalls `pi-sms` (without reinstalling unchanged dependencies) when it detects an existing installation, since a plain `pip install git+URL` silently skips reinstalling an already-installed package name even when the underlying commit changed

## [0.1.0] - 2026-07-16

### Added

- Initial release: SMS-to-Trello daemon for Raspberry Pi with a Huawei E3372 HiLink modem. Polls the modem's SMS inbox, filters out known-noise messages (e.g. Free Mobile voicemail notifications), creates a Trello card for each remaining message, and deletes the message from the modem once handled.

[0.1.0]: https://github.com/nmassart/pi-sms/releases/tag/v0.1.0
