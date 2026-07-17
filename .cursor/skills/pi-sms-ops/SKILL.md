---
name: pi-sms-ops
description: Operate and troubleshoot a deployed pi-sms Raspberry Pi over SSH - check modem SIM/signal, send a test SMS, view live daemon logs, manage the systemd service, and recover the Pi's network if it drops off the LAN. Use when the user asks to check the pi-sms modem, test SMS sending, view pi-sms logs, restart/manage the pi-sms service, or the Pi becomes unreachable over SSH/LAN.
---

# pi-sms Operations

Replace `<PI_HOST>` and `<PI_USER>` with the actual Pi's address and SSH user
before running commands. Never commit real credentials or IPs into this file
or into chat output that could be published.

## Connect

```bash
ssh <PI_USER>@<PI_HOST>
```

## Check modem SIM/signal status

The HiLink API requires a fresh session token before every state-changing or
authenticated call (`SesTokInfo` -> `Cookie` + `__RequestVerificationToken`,
single-use). Run on the Pi:

```bash
M=http://192.168.8.1
R=$(curl -s -m6 "$M/api/webserver/SesTokInfo")
SID=$(echo "$R" | sed -n 's:.*<SesInfo>\(.*\)</SesInfo>.*:\1:p')
TOK=$(echo "$R" | sed -n 's:.*<TokInfo>\(.*\)</TokInfo>.*:\1:p')
curl -s -H "Cookie: $SID" -H "__RequestVerificationToken: $TOK" \
  "$M/api/monitoring/status" | tr '>' '>\n' | grep -iE "SimStatus|SignalIcon|ServiceStatus"
```

Ready to send/receive when `SimStatus=1` and `SignalIcon` > 0. `SimStatus=255`
means no SIM detected (missing, misseated, or wrong orientation).

## Send a test SMS

Confirms the modem/SIM can send, independent of the daemon:

```bash
PHONE="+33612345678"
MSG="Hello from the Pi modem"

M=http://192.168.8.1
R=$(curl -s -m6 "$M/api/webserver/SesTokInfo")
SID=$(echo "$R" | sed -n 's:.*<SesInfo>\(.*\)</SesInfo>.*:\1:p')
TOK=$(echo "$R" | sed -n 's:.*<TokInfo>\(.*\)</TokInfo>.*:\1:p')
curl -s -m10 \
  -H "Cookie: $SID" \
  -H "__RequestVerificationToken: $TOK" \
  -H "Content-Type: text/xml" \
  -d "<?xml version='1.0' encoding='UTF-8'?><request><Index>-1</Index><Phones><Phone>${PHONE}</Phone></Phones><Sca></Sca><Content>${MSG}</Content><Length>${#MSG}</Length><Reserved>1</Reserved><Date>$(date '+%Y-%m-%d %H:%M:%S')</Date></request>" \
  "$M/api/sms/send-sms"; echo
```

A successful send returns `<response>OK</response>`.

## View live daemon logs

```bash
sudo journalctl -u pi-sms -f
```

Only card creations and comments are logged in normal mode
(`Created Trello card for SMS from {phone}` and
`Added SMS from {phone} to existing card`). For real-time flushing and full
visibility into every received/filtered message:

```bash
sudo systemctl edit pi-sms
```

Add under `[Service]`, then `sudo systemctl restart pi-sms`:

```
Environment=PYTHONUNBUFFERED=1
Environment=DEBUG_MODE=true
```

`DEBUG_MODE=true` also switches to the faster `debug.poll_interval_seconds`
from `config.yaml`.

## Manage the service

```bash
sudo systemctl status pi-sms
sudo systemctl restart pi-sms
sudo systemctl stop pi-sms
sudo systemctl start pi-sms
```

## Recover the Pi if it drops off the LAN

If SSH/LAN access is lost (typically after manually running
`nmcli device connect <modem-iface>` instead of relying on the `pi-sms-modem`
profile), the modem's DHCP server can hijack the default route. Recover from
the physical console:

```bash
sudo nmcli device disconnect eth1
sudo nmcli device connect eth0
ip -br addr show eth0
```

The MAC-based bindings applied by `pi_sms.setup.setup` (see
`pi_sms/setup/network.py`) prevent this at boot, but a manual
`nmcli device connect` on the modem interface can still trigger it.

## Reinstall / update

```bash
curl -fsSL https://raw.githubusercontent.com/CaenFalaisePlaneurs/pi-sms/main/scripts/install.sh | sh
```
