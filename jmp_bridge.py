#!/usr/bin/env python3
"""
JMP.chat XMPP-to-file bridge for OpenClaw.
Maintains a persistent XMPP connection, logs incoming SMS to files,
and fires OpenClaw webhooks on new messages.
"""

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

import slixmpp

# Config
JID = os.environ.get('JMP_JID', '')
PASSWORD = os.environ.get('JMP_PASSWORD', '')
INBOX_DIR = Path(os.environ.get('JMP_INBOX', os.path.expanduser('~/.openclaw/.jmp-inbox')))
OUTBOX_DIR = Path(os.environ.get('JMP_OUTBOX', os.path.expanduser('~/.openclaw/.jmp-outbox')))
LOG_FILE = Path(os.environ.get('JMP_LOG', os.path.expanduser('~/.openclaw/.jmp-bridge.log')))

# OpenClaw hook config
HOOK_URL = os.environ.get('JMP_HOOK_URL', '')  # e.g. http://127.0.0.1:18789/hooks/sms
HOOK_TOKEN = os.environ.get('JMP_HOOK_TOKEN', '')

# Ensure dirs exist
INBOX_DIR.mkdir(parents=True, exist_ok=True)
OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

def log(msg):
    ts = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(line + '\n')


def fire_hook(phone, body, timestamp):
    """POST incoming SMS to OpenClaw hook endpoint."""
    if not HOOK_URL:
        return
    payload = json.dumps({
        'from': phone,
        'body': body,
        'timestamp': timestamp,
    }).encode()
    headers = {
        'Content-Type': 'application/json',
    }
    if HOOK_TOKEN:
        headers['Authorization'] = f'Bearer {HOOK_TOKEN}'
    try:
        req = Request(HOOK_URL, data=payload, headers=headers, method='POST')
        resp = urlopen(req, timeout=10)
        log(f"[HOOK] Fired for {phone} -> {resp.status}")
    except URLError as e:
        log(f"[HOOK ERROR] {e}")
    except Exception as e:
        log(f"[HOOK ERROR] {e}")


class JMPBridge(slixmpp.ClientXMPP):
    def __init__(self):
        super().__init__(JID, PASSWORD)
        self.add_event_handler("session_start", self.on_session)
        self.add_event_handler("message", self.on_message)
        self.add_event_handler("presence_subscribe", self.on_subscribe)
        self.add_event_handler("disconnected", self.on_disconnect)
        self.running = True

    async def on_session(self, event):
        await self.get_roster()
        self.send_presence()
        self.send_presence(pto='cheogram.com', ptype='subscribed')
        log(f"Connected as {self.boundjid.full}")

        # Start outbox watcher
        asyncio.ensure_future(self.watch_outbox())

    def on_subscribe(self, presence):
        self.send_presence(pto=str(presence['from']), ptype='subscribed')
        log(f"Accepted subscription from {presence['from']}")

    def on_message(self, msg):
        if not msg['body']:
            return

        frm = str(msg['from']).split('/')[0]
        body = msg['body']

        # Skip server welcome messages
        if frm == 'jabber.fr':
            return

        # Skip cheogram bot admin messages (not SMS)
        if frm == 'cheogram.com':
            log(f"[ADMIN] cheogram.com: {body[:100]}")
            return

        # This is an incoming SMS!
        phone = frm.replace('@cheogram.com', '')
        ts = int(time.time())

        msg_data = {
            'from': phone,
            'body': body,
            'timestamp': ts,
            'jid': frm,
        }

        # Write to inbox
        filename = f"{ts}-{phone.replace('+', '')}.json"
        inbox_path = INBOX_DIR / filename
        with open(inbox_path, 'w') as f:
            json.dump(msg_data, f)

        log(f"[SMS IN] {phone}: {body[:100]}")

        # Fire OpenClaw hook (non-blocking)
        asyncio.get_event_loop().run_in_executor(None, fire_hook, phone, body, ts)

    def on_disconnect(self, event):
        if self.running:
            log("Disconnected, reconnecting in 5s...")
            asyncio.ensure_future(self.reconnect_delayed())

    async def reconnect_delayed(self):
        await asyncio.sleep(5)
        if self.running:
            self.connect()

    async def watch_outbox(self):
        """Watch outbox directory for messages to send."""
        while self.running:
            try:
                for f in sorted(OUTBOX_DIR.iterdir()):
                    if f.suffix == '.json':
                        try:
                            with open(f) as fh:
                                data = json.load(fh)
                            to_phone = data['to']
                            body = data['body']
                            jid = f"{to_phone}@cheogram.com"
                            self.send_message(mto=jid, mbody=body, mtype='chat')
                            log(f"[SMS OUT] {to_phone}: {body[:100]}")
                            f.unlink()
                        except Exception as e:
                            log(f"[ERROR] Failed to send {f.name}: {e}")
                            f.rename(f.with_suffix('.failed'))
            except Exception as e:
                pass
            await asyncio.sleep(2)

    def stop(self):
        self.running = False
        self.disconnect()


async def main():
    if not PASSWORD:
        print("ERROR: JMP_PASSWORD not set", file=sys.stderr)
        sys.exit(1)

    log("Starting JMP bridge...")
    if HOOK_URL:
        log(f"Hook configured: {HOOK_URL}")
    else:
        log("No hook URL configured (file-only mode)")

    bot = JMPBridge()

    # Handle signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, bot.stop)

    bot.connect()

    # Run forever
    while bot.running:
        await asyncio.sleep(1)

    log("JMP bridge stopped.")


if __name__ == '__main__':
    asyncio.run(main())
