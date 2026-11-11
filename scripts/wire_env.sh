#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# wire_env.sh — stamp the floor's config into the agentcore project (module 9).
# The agentcore CLI is schema-first: agentcore.json is the source of truth,
# and env vars / exec role have no add-agent flags — they live in the config.
# This writes exactly two things into runtimes[]:
#   envVars           — the same WORKSHOP_* set the incumbent runtime carries
#                       (DB endpoint, secret ARN, model ids, session memory id)
#   executionRoleArn  — the platform team's runtime role (workshop.env);
#                       the role is pre-created by the stack: attendees never
#                       mint IAM
# Idempotent: re-running overwrites the same two keys, nothing else.
set -euo pipefail
[ -f /etc/workshop.env ] && source /etc/workshop.env

PROJ="${1:-$HOME/environment/paymentagents}"
CFG="$PROJ/agentcore/agentcore.json"
[ -f "$CFG" ] || { echo "no agentcore project at $PROJ — run 'agentcore add agent' first" >&2; exit 1; }

MEMORY_ID=$(aws bedrock-agentcore-control get-agent-runtime \
  --agent-runtime-id "${WORKSHOP_RUNTIME_ARN##*/}" --region "$WORKSHOP_REGION" \
  --query 'environmentVariables.WORKSHOP_MEMORY_ID' --output text)

python3 - "$CFG" "$MEMORY_ID" <<'EOF'
import json, os, sys
cfg_path, memory_id = sys.argv[1], sys.argv[2]
d = json.load(open(cfg_path))
if not d.get("runtimes"):
    sys.exit("agentcore.json has no runtimes[] — run 'agentcore add agent' first")
rt = d["runtimes"][0]
rt["executionRoleArn"] = os.environ["WORKSHOP_EXEC_ROLE_ARN"]
pairs = {
    "WORKSHOP_DB_HOST": os.environ["WORKSHOP_DB_HOST"],
    "WORKSHOP_DB_NAME": os.environ["WORKSHOP_DB_NAME"],
    "WORKSHOP_DB_USER": os.environ["WORKSHOP_DB_USER"],
    "WORKSHOP_DB_SECRET_ARN": os.environ["WORKSHOP_DB_SECRET_ARN"],
    "WORKSHOP_MODEL_REGION": os.environ["WORKSHOP_REGION"],
    "WORKSHOP_MODEL_ID": os.environ["WORKSHOP_MODEL_ID"],
    "WORKSHOP_COPILOT_MODEL_ID": os.environ["WORKSHOP_COPILOT_MODEL_ID"],
    "WORKSHOP_MEMORY_ID": memory_id,
    # HNSW recall floor (see indexes_postload.sql header): libpq startup
    # option — the runtime's psycopg connections inherit it
    "PGOPTIONS": "-c hnsw.ef_search=200",
}
rt["envVars"] = [{"name": k, "value": v} for k, v in pairs.items()]
json.dump(d, open(cfg_path, "w"), indent=2)
print(f"{cfg_path}")
print(f"  executionRoleArn = {rt['executionRoleArn']}")
for kv in rt["envVars"]:
    print(f"  envVars[{kv['name']}]")
EOF
