#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# go_live.sh — point the floor at YOUR runtime (module 9 cutover).
# The attendee's `agentcore deploy` created a NEW runtime; until this runs,
# the UI/api bridge still talks to the provision-time incumbent. This reads
# the deployed ARN from the CLI project's own state, then hands it to the
# root helper (sudoers-scoped to exactly that command) which writes
# /etc/workshop-runtime.env for the terminals and repoints the PRODUCTION
# bridge Lambda for the floor (env update + wait; no service restarts —
# the app tier is serverless).
set -euo pipefail
[ -f /etc/workshop.env ] && source /etc/workshop.env

PROJ="${1:-$HOME/environment/paymentagents}"
STATE="$PROJ/agentcore/.cli/deployed-state.json"
[ -f "$STATE" ] || { echo "no deployed state at $STATE — run 'agentcore deploy' first" >&2; exit 1; }

ARN=$(python3 - "$STATE" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
# deployed-state: find the first runtime ARN in the state tree
def find(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and v.startswith("arn:aws:bedrock-agentcore:") and ":runtime/" in v:
                return v
            hit = find(v)
            if hit:
                return hit
    if isinstance(node, list):
        for v in node:
            hit = find(v)
            if hit:
                return hit
print(find(d) or "")
EOF
)
[ -n "$ARN" ] || { echo "no runtime ARN in $STATE" >&2; exit 1; }

OLD="${WORKSHOP_RUNTIME_ARN:-unset}"
sudo /usr/local/bin/workshop-go-live "$ARN"
printf '%-12s %s\n' 'was:' "$OLD"
printf '%-12s %s\n' 'now:' "$ARN"
printf '%-12s %s\n' 'bridge:' "$(aws lambda get-function-configuration --function-name "$BRIDGE_FN" --region "$WORKSHOP_REGION" --query 'Environment.Variables.RUNTIME_ARN' --output text 2>/dev/null || echo unknown)"
