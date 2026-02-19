# jmp-bridge

XMPP-to-file bridge for [JMP.chat](https://jmp.chat) SMS, designed for [OpenClaw](https://github.com/openclaw/openclaw) agents.

Gives your AI agent a real phone number it can send and receive SMS through — no apps, no GUI, no exposed ports. Just an XMPP client connection and a pair of directories.

## How It Works

```
Phone (SMS) → JMP.chat → XMPP → jmp_bridge.py → ~/.openclaw/.jmp-inbox/*.json
                                                    ↓
                                              OpenClaw reads it

OpenClaw writes → ~/.openclaw/.jmp-outbox/*.json → jmp_bridge.py → XMPP → JMP.chat → Phone (SMS)
```

The bridge maintains a persistent XMPP connection to your JMP account. Incoming SMS arrive as XMPP messages and get written to the inbox as JSON files. Outgoing SMS are dropped into the outbox as JSON files and picked up by the bridge every 2 seconds.

## Prerequisites

- Python 3.8+
- [slixmpp](https://codeberg.org/poezio/slixmpp): `pip3 install slixmpp`
- A JMP.chat account ($4.99/mo — [jmp.chat](https://jmp.chat))
- An XMPP account on any public server (we used [jabber.fr](https://jabber.fr))

## Setup

### 1. Get an XMPP Account

Register on any public XMPP server. We used jabber.fr:

```bash
# You can register via any XMPP client, or programmatically with slixmpp
# The bridge expects JID + password
```

### 2. Register with JMP.chat

Message `cheogram.com` from your XMPP client:

```
register jmp.chat
```

The bot walks you through:
1. Search for a number by area code (e.g., `512` for Austin, TX)
2. Pick from the results
3. Pay ($4.99/mo — Bitcoin, PayPal, or credit card)

### 3. Configure

Set environment variables:

```bash
export JMP_JID="you@jabber.fr"
export JMP_PASSWORD="your-xmpp-password"

# Optional (these are the defaults):
export JMP_INBOX="$HOME/.openclaw/.jmp-inbox"
export JMP_OUTBOX="$HOME/.openclaw/.jmp-outbox"
export JMP_LOG="$HOME/.openclaw/.jmp-bridge.log"
```

### 4. Run

```bash
python3 jmp_bridge.py
```

Or backgrounded:

```bash
JMP_PASSWORD="..." nohup python3 jmp_bridge.py &
```

Or as a systemd service (recommended):

```ini
[Unit]
Description=JMP XMPP-SMS Bridge
After=network.target

[Service]
Type=simple
User=your-user
Environment=JMP_JID=you@jabber.fr
Environment=JMP_PASSWORD=your-password
ExecStart=/usr/bin/python3 /path/to/jmp_bridge.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Usage

### Receiving SMS

Incoming messages appear as JSON in the inbox directory:

```json
{
  "from": "+15125551234",
  "body": "Hey, got your message!",
  "timestamp": 1771468248,
  "jid": "+15125551234@cheogram.com"
}
```

Filename format: `{timestamp}-{phone}.json`

### Sending SMS

Drop a JSON file in the outbox:

```json
{
  "to": "+15125551234",
  "body": "Hello from my AI agent!"
}
```

Or use the helper script:

```bash
./send_sms.sh +15125551234 "Hello from my AI agent!"
```

The bridge polls the outbox every 2 seconds and sends any `.json` files it finds. Successfully sent messages are deleted; failures get renamed to `.failed`.

## Architecture

- **No inbound ports** — the bridge is a pure XMPP client making outbound TLS connections
- **File-based IPC** — dead simple integration with any system that can read/write files
- **Auto-reconnect** — reconnects on disconnect with a 5-second backoff
- **Presence auto-accept** — automatically accepts subscription requests (needed for JMP/cheogram routing)

## Integration with OpenClaw

The bridge is designed to be consumed by OpenClaw agents. A few integration patterns:

1. **Heartbeat polling**: Check the inbox directory during heartbeat cycles
2. **Filesystem watcher**: Use inotifywait or similar to trigger on new inbox files
3. **OpenClaw hook** (planned): Direct webhook integration so new SMS trigger agent turns

## What JMP.chat Gives You

- Real US/Canadian phone number on carrier routes
- SMS send/receive via XMPP (no app needed)
- SIP for voice calls
- MMS support (images via XMPP file transfer)
- Number porting in/out
- $4.99/mo, payable with Bitcoin
- Privacy-focused (no KYC beyond payment)

## Lessons Learned

- **jabber.fr works well** as a free XMPP server — stable, supports in-band registration
- **cheogram.com** is JMP's bot — all account management goes through it
- **SMS routing format**: `+1XXXXXXXXXX@cheogram.com` — the phone number is the XMPP JID localpart
- **Presence matters**: You must send presence to `cheogram.com` and accept its subscription for SMS routing to work
- **The outbox pattern** is simple but effective — any process can queue an SMS by writing a JSON file
- **slixmpp** (async) is the right choice over sleekxmpp (deprecated) for Python 3.8+

## TODO

- [ ] systemd service template
- [ ] OpenClaw hook integration (new SMS → agent turn)
- [ ] MMS/image support
- [ ] Delivery receipts
- [ ] Signal registration guide (JMP numbers may work for Signal)
- [ ] Rate limiting / abuse prevention
- [ ] Skill packaging for OpenClaw

## License

MIT
