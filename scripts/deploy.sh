#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# deploy.sh — ship YOUR agent code to the AgentCore runtime (module 7 opener;
# all agents ride the one bundle). Every output line after the bundle is a
# control-plane call and the fact it returned — each label is a runnable
# `aws bedrock-agentcore-control` verb.
#   1. bundle the investigator package + a DEPENDENCY-VENDORED site tree
#      (deps resolve at BUILD time, into the zip — a source-only bundle makes
#      the runtime resolve pyproject server-side at container init and blows
#      the 30s VPC cold-start deadline — every cold container then returns HTTP 424)
#   2. upload the bundle where the runtime's CodeConfiguration points
#   3. UpdateAgentRuntime with the SAME config (new version = new containers)
#   4. wait READY, then read back what production runs now (endpoint, versions)
set -euo pipefail
[ -f /etc/workshop.env ] && source /etc/workshop.env
# module-7 cutover override wins (go_live.sh writes it)
if [ -f /etc/workshop-runtime.env ]; then set -a; source /etc/workshop-runtime.env; set +a; fi
cd "$(dirname "$0")/.."

RUNTIME_ID=$(python3 -c "import os; print(os.environ['WORKSHOP_RUNTIME_ARN'].rsplit('/',1)[1])")
echo "deploy → runtime $RUNTIME_ID"

# 1. vendored bundle: package code + resolved deps in one tree
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cp -R investigator main.py pyproject.toml "$WORK/" 2>/dev/null || {
  # deployed workspace layout: the package lives beside this script's parent
  cp -R ../src/investigator ../src/main.py ../src/pyproject.toml "$WORK/" ; }
echo "  resolving pinned dependencies into the bundle …"
uv pip install --quiet --target "$WORK" "$WORK" 2>/dev/null || \
  uv pip install --quiet --target "$WORK" .
( cd "$WORK" && zip -qr /tmp/bundle-deploy.zip . )
SIZE=$(du -m /tmp/bundle-deploy.zip | cut -f1)
echo "  bundle: ${SIZE} MB (vendored)"

# 2. upload to the bucket+key the runtime already points at
# plain assignment (NOT a herestring around $(…)) so set -e stops the script
# right here if the control-plane call fails, instead of cascading
CODE=$(aws bedrock-agentcore-control get-agent-runtime \
  --agent-runtime-id "$RUNTIME_ID" --region "$WORKSHOP_REGION" \
  --query '[agentRuntimeArtifact.codeConfiguration.code.s3.bucket, agentRuntimeArtifact.codeConfiguration.code.s3.prefix, agentRuntimeVersion, status]' \
  --output text)
read -r BUCKET KEY CURVER CURST <<< "$CODE"
printf '  %-28s version %-3s %s\n' get-agent-runtime "$CURVER" "$CURST"
aws s3 cp /tmp/bundle-deploy.zip "s3://$BUCKET/$KEY" --only-show-errors
printf '  %-28s s3://%s/%s\n' 's3 cp' "$BUCKET" "$KEY"

# 3. re-apply the runtime's own current config (update = converge; the only
#    change is the new bundle bytes behind the same S3 key)
CFG=$(aws bedrock-agentcore-control get-agent-runtime \
  --agent-runtime-id "$RUNTIME_ID" --region "$WORKSHOP_REGION" \
  --query '{artifact: agentRuntimeArtifact, roleArn: roleArn, net: networkConfiguration, auth: authorizerConfiguration, env: environmentVariables, headers: requestHeaderConfiguration}' \
  --output json)
T0=$SECONDS
python3 - "$RUNTIME_ID" "$WORKSHOP_REGION" <<EOF
import json, subprocess, sys
cfg = json.loads('''$CFG''')
# the provision-time runtime is stage-clamped (AGENT_STAGE=evidence — the
# module-2 "same code, two runtimes" contract). YOUR module-7 deploy ships
# the full workspace code, so the clamp comes off here: deploy what you tested.
env = cfg.get("env") or {}
delta = "env -AGENT_STAGE" if env.pop("AGENT_STAGE", None) is not None else "env unchanged"
cfg["env"] = env
args = ["aws", "bedrock-agentcore-control", "update-agent-runtime",
        "--agent-runtime-id", sys.argv[1], "--region", sys.argv[2],
        "--agent-runtime-artifact", json.dumps(cfg["artifact"]),
        "--role-arn", cfg["roleArn"],
        "--network-configuration", json.dumps(cfg["net"]),
        "--authorizer-configuration", json.dumps(cfg["auth"]),
        "--request-header-configuration", json.dumps(cfg["headers"]),
        "--environment-variables", json.dumps(cfg["env"]),
        "--query", "agentRuntimeVersion", "--output", "text"]
v = subprocess.check_output(args, text=True).strip()
print(f"  {'update-agent-runtime':<28} version {v:<3} {delta}")
EOF

# 4. wait READY (updates converge in ~30-60s), then read back live state
ST=""
NEWVER=""
for i in $(seq 1 30); do
  POLL=$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" --region "$WORKSHOP_REGION" \
    --query '[agentRuntimeVersion, status]' --output text)
  read -r NEWVER ST <<< "$POLL"
  [ "$ST" = "READY" ] && break
  sleep 10
done
if [ "$ST" != "READY" ]; then
  echo "  runtime did not reach READY in 5 min (status: $ST)" >&2
  exit 1
fi
printf '  %-28s version %-3s READY   %ss\n' get-agent-runtime "$NEWVER" "$((SECONDS-T0))"

LIVE=$(aws bedrock-agentcore-control list-agent-runtime-endpoints \
  --agent-runtime-id "$RUNTIME_ID" --region "$WORKSHOP_REGION" \
  --query 'runtimeEndpoints[?name==`DEFAULT`] | [0].liveVersion' --output text)
printf '  %-28s DEFAULT     live version %s\n' list-agent-runtime-endpoints "$LIVE"

VERS=$(aws bedrock-agentcore-control list-agent-runtime-versions \
  --agent-runtime-id "$RUNTIME_ID" --region "$WORKSHOP_REGION" \
  --query 'agentRuntimes[].[agentRuntimeVersion, status]' --output text)
SUMMARY=$(echo "$VERS" | python3 -c "
import sys
rows = [l.split('\t') for l in sys.stdin.read().strip().splitlines() if l.strip()]
vers = [r[0] for r in rows]; sts = {r[1] for r in rows}
tail = 'all ' + sts.pop() if len(sts) == 1 else '  '.join(f'{v}:{s}' for v, s in rows)
print(' '.join(vers) + '   (' + tail + ')')")
printf '  %-28s %s\n' list-agent-runtime-versions "$SUMMARY"
