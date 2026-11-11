#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# chat.sh [dispute|recon|copilot] [thread-id=NAME] — an agent chat against
# the DEPLOYED runtime.
# The local dev loop is `uv run -m investigator.chat` (module 2);
# THIS one talks to the same deployed copy the UI uses: one Cognito token,
# one persistent ≥33-char session id, one streamed POST per turn. Multi-turn
# memory is REAL here: AgentCore Memory (STM) restores the session's turns
# into each per-request agent (a facilitator tool since 2026-09-30; module 9 runs invoke.sh).
# thread-id=NAME: any name you choose (thread-id=my-first-shift). The same
# name maps to the same session id every time — run again with the same
# thread-id to RESUME that thread; memory restores per (actor, session).
# No thread-id → a fresh throwaway thread each run. A bare name or a full
# ≥33-char session id (copied from a previous banner) also works.
set -euo pipefail
[ -f /etc/workshop.env ] && source /etc/workshop.env
# module-7 cutover override wins (go_live.sh writes it)
if [ -f /etc/workshop-runtime.env ]; then set -a; source /etc/workshop-runtime.env; set +a; fi

AGENT="${1:-dispute}"
case "$AGENT" in dispute|recon|copilot) ;; *)
  echo "usage: chat.sh [dispute|recon|copilot]" >&2; exit 1;; esac

ANALYST_PW=$(aws secretsmanager get-secret-value --secret-id "$WORKSHOP_ANALYST_SECRET_ARN" \
  --region "$WORKSHOP_REGION" --query SecretString --output text \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['password'])")
TOKEN=$(aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH \
  --client-id "$WORKSHOP_COGNITO_CLIENT_ID" \
  --auth-parameters USERNAME=analyst_martha,PASSWORD="$ANALYST_PW" \
  --region "$WORKSHOP_REGION" \
  --query 'AuthenticationResult.AccessToken' --output text)

RUNTIME_ARN_ESCAPED=$(python3 -c "import urllib.parse,os; print(urllib.parse.quote(os.environ['WORKSHOP_RUNTIME_ARN'], safe=''))")
URL="https://bedrock-agentcore.$WORKSHOP_REGION.amazonaws.com/runtimes/$RUNTIME_ARN_ESCAPED/invocations?qualifier=DEFAULT"
THREAD=""
if [ -n "${2:-}" ]; then
  ARG="${2#thread-id=}"                   # explicit form: thread-id=NAME
  if [ ${#ARG} -ge 33 ]; then
    SESSION_ID="$ARG"                     # a full session id, used verbatim
  else
    # a thread id: derive the same ≥33-char session id from it every time
    # (the runtime's contract wants ≥33 chars; the pad makes any name legal)
    THREAD=$(printf '%s' "$ARG" | tr -c 'a-zA-Z0-9_-' '-')
    SESSION_ID="chat-${THREAD}-0123456789abcdefghijklmnop"
  fi
else
  SESSION_ID="chat-$(whoami)-$(date +%s)-0123456789abcdef" # fresh throwaway thread
fi

case "$AGENT" in
  dispute) HINT='investigate case 4127' ;;
  recon)   HINT='investigate break 1' ;;
  copilot) HINT="what's in my queue today?" ;;
esac
if [ -n "$THREAD" ]; then
  # the derivation, visible at launch: your thread id IS the session id
  echo "[$AGENT @ deployed runtime] thread-id=$THREAD → session=$SESSION_ID"
else
  echo "[$AGENT @ deployed runtime] session=$SESSION_ID"
fi
echo "  (try: \"$HINT\" · exit, empty line, or Ctrl+C ends the chat)"
trap 'echo; echo "[$AGENT @ deployed runtime] chat ended — you are back at the shell."; exit 0' INT
while IFS= read -r -p "> " PROMPT; do
  case "$(echo "$PROMPT" | tr "[:upper:]" "[:lower:]")" in ""|exit|quit|q) break;; esac
  BODY=$(python3 -c "import json,sys; print(json.dumps({'agent': sys.argv[1], 'prompt': sys.argv[2]}))" "$AGENT" "$PROMPT")
  # stream typed chunks; render text deltas inline, tool chips as ⚙ lines
  curl -sN -m 300 -X POST "$URL" \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $SESSION_ID" \
    -d "$BODY" | python3 -u -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line:
        continue
    try:
        c = json.loads(line)
    except json.JSONDecodeError:
        continue
    t = c.get("type")
    if t == "text":
        print(c["text"], end="", flush=True)
    elif t == "tool_start":
        # no escapes inside f-string braces (SyntaxError before py3.12;
        # the host python3 is 3.9)
        tool = c.get("tool", "?")
        print(f"\n  ⚙ {tool} …", flush=True)
    elif t == "error":
        err = c.get("error")
        print(f"\n[agent error] {err}", flush=True)
    elif t == "done":
        print(flush=True)
'
done
echo "[$AGENT @ deployed runtime] chat ended — you are back at the shell."
