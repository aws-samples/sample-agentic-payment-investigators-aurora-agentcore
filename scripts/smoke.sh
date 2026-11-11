#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# smoke.sh — the doors-open re-check (module 1). Three moving parts, three
# checks, each against the REAL path an attendee uses later:
#   database   Aurora reachable + seeded (the module 1 psql path)
#   bedrock    Haiku + Titan + Rerank invocable (modules 2-4 model calls)
#   agentcore  one COLD-CAPABLE runtime invoke (the module 1 invoke path —
#              catches VPC cold-start failures that warm containers mask)
set -uo pipefail
[ -f /etc/workshop.env ] && source /etc/workshop.env
# module-9 cutover override wins (go_live.sh writes it)
if [ -f /etc/workshop-runtime.env ]; then set -a; source /etc/workshop-runtime.env; set +a; fi
cd "$(dirname "$0")/.."

PASS=0; FAIL=0
ok()   { echo "  ok  $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL $1 — $2"; FAIL=$((FAIL+1)); }

# 1. database: count the precedent corpus through the same env psql uses
N=$(scripts/psql.sh -tAc "SELECT count(*) FROM precedent_cases" 2>/dev/null || echo 0)
if [ "${N:-0}" -ge 60000 ]; then
  ok "database    Aurora reachable, schema seeded ($(printf "%'d" "$N") precedents)"
else
  fail "database" "expected ≥60,000 precedent rows, got '${N}' (check WORKSHOP_DB_* / seeder)"
fi

# 2. bedrock: one minimal call per model class the workshop uses.
# Per-call failure capture — a single lumped flag can't tell you WHICH model
# class (or local setup step) actually failed.
# mktemp, not a fixed path: /tmp files created by another user (e.g. a
# root SSM run) are not overwritable and hardcoded names collide.
BFAILED=""
aws bedrock-runtime converse --region "$WORKSHOP_REGION" --model-id "$WORKSHOP_MODEL_ID" \
  --messages '[{"role":"user","content":[{"text":"hi"}]}]' \
  --inference-config '{"maxTokens":1}' >/dev/null 2>&1 || BFAILED="$BFAILED converse(Haiku)"
EMBED_OUT=$(mktemp)
aws bedrock-runtime invoke-model --region "$WORKSHOP_REGION" \
  --model-id "${WORKSHOP_EMBED_MODEL_ID:-amazon.titan-embed-text-v2:0}" \
  --body "$(echo -n '{"inputText":"smoke","dimensions":1024,"normalize":true}' | base64)" \
  "$EMBED_OUT" >/dev/null 2>&1 || BFAILED="$BFAILED invoke-model(Titan)"
rm -f "$EMBED_OUT"
RERANK_ID="${WORKSHOP_RERANK_MODEL_ID:-cohere.rerank-v3-5}"
aws bedrock-agent-runtime rerank --region "$WORKSHOP_REGION" \
  --queries '[{"type":"TEXT","textQuery":{"text":"smoke"}}]' \
  --sources '[{"type":"INLINE","inlineDocumentSource":{"type":"TEXT","textDocument":{"text":"smoke doc"}}}]' \
  --reranking-configuration "{\"type\":\"BEDROCK_RERANKING_MODEL\",\"bedrockRerankingConfiguration\":{\"modelConfiguration\":{\"modelArn\":\"arn:aws:bedrock:${WORKSHOP_REGION}::foundation-model/${RERANK_ID%:0}:0\"},\"numberOfResults\":1}}" \
  >/dev/null 2>&1 || BFAILED="$BFAILED rerank(Cohere)"
if [ -z "$BFAILED" ]; then
  ok "bedrock     Haiku + Titan + Rerank invocable in this account"
else
  fail "bedrock" "failed:${BFAILED} (fresh account? Marketplace subscription can take ~60s — re-run)"
fi

# 3. agentcore: a real authed invoke against the deployed runtime. Generous
# curl budget because a COLD container must boot inside it — a healthy
# vendored bundle answers in seconds; an init-timeout (424) means the
# bundle regressed to server-side dependency resolution.
OUT=$(scripts/invoke.sh --smoke 2>/dev/null || true)
if echo "$OUT" | grep -q '"type": *"done"'; then
  ok "agentcore   deployed starter agent answered a round-trip invoke"
else
  fail "agentcore" "no 'done' chunk — $(echo "$OUT" | grep -o '"error":[^}]*' | head -c 120 || echo 'empty response')"
fi

echo "smoke: $PASS/3 green"
[ "$FAIL" = 0 ]
