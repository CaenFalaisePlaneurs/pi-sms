# Security Policy

## Supported Versions

This is a personal-use project run on a single Raspberry Pi. Only the latest commit on `main` is supported.

## Reporting a Vulnerability

If you find a security issue (e.g. in the modem session handling, Trello credential storage, or the network setup), please open a private report via GitHub Security Advisories rather than a public issue.

## Secrets

- Trello API key/token and the modem base URL live in `/etc/pi-sms/config.yaml`, which is never committed to git (see `.gitignore`) and should be kept at file mode `600`.
- The daemon never logs the Trello key/token or full SMS content; only lifecycle events (sender, success/failure) are logged.
