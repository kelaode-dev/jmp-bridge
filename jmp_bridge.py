#!/usr/bin/env python3
"""
JMP.chat XMPP-to-file bridge for OpenClaw.
Maintains a persistent XMPP connection, logs incoming SMS to files,
and optionally fires OpenClaw webhooks on new messages.
"""

import asyncio
import json
import os
import signal
import stat
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import slixmpp

# Config
JID = os.environ.get("JMP_JID", "kelaode@jabber.fr")
PASSWORD = os.environ.get("JMP_PASSWORD", "")
INBOX_DIR = Path(os.environ.get("JMP_INBOX", os.path.expanduser("~/.openclaw/.jmp-inbox")))
OUTBOX_DIR = Path(os.environ.get("JMP_OUTBOX", os.path.expanduser("~/.openclaw/.jmp-outbox")))
LOG_FILE = Path(os.environ.get("JMP_LOG", os.path.expanduser("~/.openclaw/.jmp-bridge.log")))

# OpenClaw hook config
HOOK_URL = os.environ.get("JMP_HOOK_URL", "")  # e.g. http://127.0.0.1:18789/hooks/sms
HOOK_TOKEN = os.environ.get("JMP_HOOK_TOKEN", "")

# Security controls
ALLOWED_SENDERS = {s.strip() for s in os.environ.get("JMP_ALLOWED_SENDERS", "").split(",") if s.strip()}
REQUIRE_PREFIX = os.environ.get("JMP_REQUIRE_COMMAND_PREFIX", "").strip()
MAX_SMS_LEN = int(os.environ.get("JMP_MAX_SMS_LENGTH", "1000"))

INBOUND_LIMIT_PER_MIN = int(os.environ.get("JMP_INBOUND_PER_MIN", "30"))
OUTBOUND_LIMIT_PER_MIN = int(os.environ.get("JMP_OUTBOUND_PER_MIN", "30"))
OUTBOUND_LIMIT_PER_DAY = int(os.environ.get("JMP_OUTBOUND_PER_DAY", "300"))

# Ensure dirs exist with strict permissions
INBOX_DIR.mkdir(parents=True, exist_ok=True)
OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
os.chmod(INBOX_DIR, 0o700)
os.chmod(OUTBOX_DIR, 0o700)


class RateLimiter:
    def __init__(self):
        self.events_minute = defaultdict(deque)
        self.events_day = deque()

    @staticmethod
    def _trim_window(q, window_seconds, now):
        while q and now - q[0] > window_seconds:
            q.popleft()

    def allow_inbound(self, sender):
        now = time.time()
        q = self.events_minute[sender]
        self._trim_window(q, 60, now)
        if len(q) >= INBOUND_LIMIT_PER_MIN:
            return False
        q.append(now)
        return True

    def allow_outbound(self, sender_key="global"):
        now = time.time()

        per_min_q = self.events_minute[f"out::{sender_key}"]
        self._trim_window(per_min_q, 60, now)
        if len(per_min_q) >= OUTBOUND_LIMIT_PER_MIN:
            return False

        self._trim_window(self.events_day, 86400, now)
        if len(self.events_day) >= OUTBOUND_LIMIT_PER_DAY:
            return False

        per_min_q.append(now)
        self.events_day.append(now)
        return True


RL = RateLimiter()


def _safe_line(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    return f"[{ts}] {msg}"


def log(msg):
    line = _safe_line(msg)
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    os.chmod(LOG_FILE, stat.S_IRUSR | stat.S_IWUSR)


def redact_phone(phone):
    if len(phone) < 4:
        return "***"
    return f"***{phone[-4:]}"


def fire_hook(phone, body, timestamp):
    """POST incoming SMS to OpenClaw hook endpoint."""
    if not HOOK_URL:
        return
    payload = json.dumps({"from": phone, "body": body, "timestamp": timestamp}).encode()
    headers = {"Content-Type": "application/json"}
    if HOOK_TOKEN:
        headers["Authorization"] = f"Bearer {HOOK_TOKEN}"

    try:
        req = Request(HOOK_URL, data=payload, headers=headers, method="POST")
        resp = urlopen(req, timeout=10)
        log(f"[HOOK] Fired for {redact_phone(phone)} -> {resp.status}")
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
        self.send_presence(pto="cheogram.com", ptype="subscribed")
        log(f"Connected as {self.boundjid.full}")
        asyncio.ensure_future(self.watch_outbox())

    def on_subscribe(self, presence):
        self.send_presence(pto=str(presence["from"]), ptype="subscribed")
        log(f"Accepted subscription from {presence['from']}")

    def on_message(self, msg):
        if not msg["body"]:
            return

        frm = str(msg["from"]).split("/")[0]
        body = msg["body"]

        if frm == "jabber.fr":
            return

        if frm == "cheogram.com":
            log(f"[ADMIN] cheogram.com: {body[:100]}")
            return

        phone = frm.replace("@cheogram.com", "")
        if ALLOWED_SENDERS and phone not in ALLOWED_SENDERS:
            log(f"[DROP] Untrusted sender {redact_phone(phone)}")
            return

        if not RL.allow_inbound(phone):
            log(f"[DROP] Inbound rate limit for {redact_phone(phone)}")
            return

        if REQUIRE_PREFIX and not body.startswith(REQUIRE_PREFIX):
            log(f"[DROP] Missing required prefix from {redact_phone(phone)}")
            return

        body = body[:MAX_SMS_LEN]
        ts = int(time.time())
        msg_data = {"from": phone, "body": body, "timestamp": ts, "jid": frm}

        filename = f"{ts}-{phone.replace('+', '')}.json"
        inbox_path = INBOX_DIR / filename
        with open(inbox_path, "w") as f:
            json.dump(msg_data, f)
        os.chmod(inbox_path, stat.S_IRUSR | stat.S_IWUSR)

        log(f"[SMS IN] {redact_phone(phone)}: {body[:80]}")
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
        while self.running:
            try:
                for f in sorted(OUTBOX_DIR.iterdir()):
                    if f.suffix != ".json":
                        continue
                    try:
                        with open(f) as fh:
                            data = json.load(fh)
                        to_phone = str(data["to"])
                        body = str(data["body"])[:MAX_SMS_LEN]

                        if not RL.allow_outbound():
                            log(f"[DEFER] Outbound rate limit hit; keeping {f.name}")
                            continue

                        jid = f"{to_phone}@cheogram.com"
                        self.send_message(mto=jid, mbody=body, mtype="chat")
                        log(f"[SMS OUT] {redact_phone(to_phone)}: {body[:80]}")
                        f.unlink()
                    except Exception as e:
                        log(f"[ERROR] Failed to send {f.name}: {e}")
                        try:
                            f.rename(f.with_suffix(".failed"))
                        except Exception as rename_err:
                            log(f"[ERROR] Failed to mark {f.name} as failed: {rename_err}")
            except Exception as e:
                log(f"[ERROR] Outbox scan failed: {e}")

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

    if ALLOWED_SENDERS:
        log(f"Sender allowlist enabled ({len(ALLOWED_SENDERS)} entries)")
    else:
        log("Sender allowlist disabled (accepting all senders)")

    bot = JMPBridge()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, bot.stop)

    bot.connect()

    while bot.running:
        await asyncio.sleep(1)

    log("JMP bridge stopped.")


if __name__ == "__main__":
    asyncio.run(main())
