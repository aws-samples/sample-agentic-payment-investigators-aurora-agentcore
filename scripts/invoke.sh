#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# invoke.sh <case_id> — run the DEPLOYED agent on a case (module 9's first
# production case; run_queue.sh loops this). Ten lines of substance: a
# Cognito token, then one streamed POST to the runtime's invocation endpoint
# — the sequence module 9 shows by hand in its "token and the POST" expand.
# Identity is the JWT — the runtime's CUSTOM_JWT authorizer validates it
# before any agent code runs.
#
# Usage: scripts/invoke.sh 4127            # investigate a case
#        scripts/invoke.sh 4127 recon      # ...with ReconInvestigator
#        scripts/invoke.sh --smoke         # smoke: trivial prompt, copilot
#        TAU=0.78 scripts/invoke.sh 3 recon  # per-request τ (module 11)
set -euo pipefail
[ -f /etc/workshop.env ] && source /etc/workshop.env
# module-9 cutover override wins (go_live.sh writes it)
if [ -f /etc/workshop-runtime.env ]; then set -a; source /etc/workshop-runtime.env; set +a; fi

AGENT="dispute"; PROMPT=""
if [ "${1:-}" = "--smoke" ]; then
  AGENT="copilot"; PROMPT="Reply with the single word ok."
else
  CASE="${1:?usage: invoke.sh <case_id> [dispute|recon]}"
  AGENT="${2:-dispute}"
  PROMPT="Investigate case $CASE."
  [ "$AGENT" = "recon" ] && PROMPT="Investigate break $CASE."
fi
# per-request τ override rides the payload (adapter clamps to [0,1])
BODY=$(python3 -c "
import json, os, sys
b = {'agent': sys.argv[1], 'prompt': sys.argv[2]}
if os.environ.get('TAU'):
    b['tau'] = float(os.environ['TAU'])
print(json.dumps(b))" "$AGENT" "$PROMPT")

# 1. a Cognito ACCESS token for analyst_martha (identity = the JWT, not a header you invent)
ANALYST_PW=$(aws secretsmanager get-secret-value --secret-id "$WORKSHOP_ANALYST_SECRET_ARN" \
  --region "$WORKSHOP_REGION" --query SecretString --output text \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['password'])")
TOKEN=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id "$WORKSHOP_COGNITO_CLIENT_ID" \
  --auth-parameters USERNAME=analyst_martha,PASSWORD="$ANALYST_PW" \
  --region "$WORKSHOP_REGION" \
  --query 'AuthenticationResult.AccessToken' --output text)

# 2. one POST to the runtime's invocation endpoint, streaming the response
#    (session id must be ≥33 chars — shorter 400s client-side)
RUNTIME_ARN_ESCAPED=$(python3 -c "import urllib.parse,os; print(urllib.parse.quote(os.environ['WORKSHOP_RUNTIME_ARN'], safe=''))")
SESSION_ID="terminal-$(whoami)-$(date +%s)-0123456789abcdef"
URL="https://bedrock-agentcore.$WORKSHOP_REGION.amazonaws.com/runtimes/$RUNTIME_ARN_ESCAPED/invocations?qualifier=DEFAULT"
post() {   # $1 = extra curl args (the TTY path appends the HTTP status as a final typed line)
  curl -sSN -m 300 -X POST "$URL" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $SESSION_ID" \
    -d "$BODY" ${1:-}
}
# On a terminal, render the typed chunks the way chat.sh does (text inline,
# tool chips as ⚙ lines) and end with the runtime-minted trace id. Piped
# output (run_queue.sh, harnesses) stays the raw stream.
render() {
  python3 -u -c '
import json, sys
trace, http, failed = None, None, False
for line in sys.stdin:
    line = line.strip()
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line:
        continue
    try:
        c = json.loads(line)
    except json.JSONDecodeError:
        print(line, file=sys.stderr, flush=True)          # not a typed chunk: show it verbatim
        continue
    t = c.get("type") if isinstance(c, dict) else None
    if t == "text":
        print(c.get("text", ""), end="", flush=True)
    elif t == "tool_start":
        print("\n  ⚙ " + str(c.get("tool", "?")) + " …", flush=True)
    elif t == "error":
        print("\n[agent error] " + str(c.get("error")), flush=True); failed = True
        trace = c.get("trace_id") or trace
    elif t == "done":
        trace = c.get("trace_id") or (c.get("data") or {}).get("trace_id")
    elif t == "__http__":
        http = int(c.get("code") or 0)
    elif t is None:
        print(json.dumps(c), file=sys.stderr, flush=True)  # untyped body = the service refusing the request ({"message": ...})
    # every other typed chunk (tool_end, artifact, ...) is protocol, not for the reader
print(flush=True)
if http and http >= 400:
    print("HTTP " + str(http) + " from the runtime — the message above says why "
          "(an expired token, a wrong runtime ARN, or a cold container still starting).", flush=True)
    failed = True
if trace:
    print("trace id: " + trace, flush=True)
else:
    print("trace id: (none — the stream ended without a done chunk; see the message above)", flush=True)
    failed = True
sys.exit(1 if failed else 0)
'
}
if [ -t 1 ]; then
  echo "[$AGENT @ deployed runtime] $PROMPT"
  post "-w \n{\"type\":\"__http__\",\"code\":\"%{http_code}\"}\n" | render
else
  post
  echo
fi
