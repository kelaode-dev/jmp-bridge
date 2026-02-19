#!/bin/bash
# Send an SMS via the JMP bridge
# Usage: send_sms.sh +1XXXXXXXXXX "message body"
OUTBOX="${HOME}/.openclaw/.jmp-outbox"
mkdir -p "$OUTBOX"
TO="$1"
BODY="$2"
TS=$(date +%s)
echo "{\"to\": \"${TO}\", \"body\": \"${BODY}\"}" > "${OUTBOX}/${TS}-outgoing.json"
echo "Queued SMS to ${TO}"
