#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# setup_deploy.sh — deploy the investigator agents to AgentCore Runtime.
#
# Runs at PROVISION time (CFN custom resource / SSM step) and again for the
# participant's `make deploy` (M3 open). Idempotent: agentcore deploy is a CDK
# apply — re-running converges. The deploy bundle is src/ with EXACT pins
# (CodeZip resolves pyproject server-side — pins ARE what ships; never
# floor-pin here).
#
# Required env (CFN Outputs → env, or explicit export for a dev environment):
#   COGNITO_DISCOVERY_URL   e.g. https://cognito-idp.us-east-1.amazonaws.com/<pool>/.well-known/openid-configuration
#   COGNITO_CLIENT_ID       app client id (the JWT authorizer's allowed client)
#   WORKSHOP_DB_HOST        Aurora cluster endpoint
#   WORKSHOP_DB_SECRET_ARN  app-role secret (resolved at the edge by load_settings)
# Optional:
#   WORKSHOP_MODEL_REGION / WORKSHOP_MODEL_ID (default from config.py)
#   AGENT_NAME (default: investigator), PROJECT_DIR (default: infra/acproject)
#
# Pinned CLI: @aws/agentcore@0.25.0 — the version vendored onto the WebHost
# (infra/bootstrap_webhost.sh). STABLE 0.x track only. All flags used here
# verified present on 0.22.0 through 0.31.1. npm `latest` is 1.0.0 since
# 2026-10-22: a ground-up rewrite (`add agent` -> `add runtime`, no `-y`,
# agentcore.json schema v2) — do NOT install it for this project.
#
AGENTCORE_CLI_PIN="0.25.0"
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
AGENT_NAME="${AGENT_NAME:-investigator}"
PROJECT_DIR="${PROJECT_DIR:-$HERE/acproject}"

: "${COGNITO_DISCOVERY_URL:?set COGNITO_DISCOVERY_URL}"
: "${COGNITO_CLIENT_ID:?set COGNITO_CLIENT_ID}"
: "${WORKSHOP_DB_HOST:?set WORKSHOP_DB_HOST}"
: "${WORKSHOP_DB_SECRET_ARN:?set WORKSHOP_DB_SECRET_ARN}"

ACV=$(agentcore --version | head -1)
if [[ "$ACV" != "$AGENTCORE_CLI_PIN" ]]; then
  echo "ERROR: agentcore CLI $ACV != pinned $AGENTCORE_CLI_PIN" >&2
  echo "  install: npm install -g @aws/agentcore@$AGENTCORE_CLI_PIN" >&2
  echo "  (preview versions are forbidden on this project)" >&2
  exit 1
fi

# ⚠ Scaffold provenance: a project dir scaffolded by a DIFFERENT CLI version
# must be regenerated (delete the dir; step 1 re-creates) — an earlier
# preview scaffold carried an aws-cdk-lib schema-drift bug;
# never assume scaffolds are portable across CLI versions. The marker below is
# OURS (the CLI writes no version stamp of its own).
MARKER="$PROJECT_DIR/.scaffolded-by"
if [[ -d "$PROJECT_DIR" && -f "$MARKER" && "$(cat "$MARKER")" != "$AGENTCORE_CLI_PIN" ]]; then
  echo "ERROR: $PROJECT_DIR scaffolded by CLI $(cat "$MARKER"), pin is $AGENTCORE_CLI_PIN — delete the dir and re-run" >&2
  exit 1
fi
if [[ -d "$PROJECT_DIR" && ! -f "$MARKER" ]]; then
  echo "ERROR: $PROJECT_DIR has no scaffold marker (pre-pin scaffold?) — delete the dir and re-run" >&2
  exit 1
fi

# -- 1. project scaffold (once) ---------------------------------------------
if [[ ! -f "$PROJECT_DIR/agentcore/agentcore.json" ]]; then
  mkdir -p "$PROJECT_DIR"
  ( cd "$PROJECT_DIR" && agentcore create --project-name paymentws --no-agent --defaults )
  # create nests a dir named after the project; flatten INCLUDING dotfiles (.git)
  if [[ -d "$PROJECT_DIR/paymentws/agentcore" ]]; then
    ( shopt -s dotglob && mv "$PROJECT_DIR/paymentws/"* "$PROJECT_DIR/" && rmdir "$PROJECT_DIR/paymentws" )
  fi
  echo "$AGENTCORE_CLI_PIN" > "$MARKER"
fi
cd "$PROJECT_DIR"

# -- 1b. platform version pin (2026-11-03) --------------------------------------
# From 2026-11-12 a runtime created without platformVersion lands on AgentCore
# Runtime V2 (snapshot/restore). The workshop was certified on V1, so the
# attendee skeleton (vendor/agentcore-project-skel.tar.gz) carries this same
# escape hatch in agentcore/cdk/lib/cdk-stack.ts; mirror it here so authoring
# deploys match. CLI 0.25.0 / aws-cdk-lib 2.261 expose no typed prop.
# Idempotent: a file that already carries exactly the V1 pin (in code, not comments)
# is left alone; any OTHER PlatformVersion setting, or an unrecognised import or
# anchor, fails loudly instead of reporting success.
python3 - "$PROJECT_DIR/agentcore/cdk/lib/cdk-stack.ts" <<'PY'
import pathlib, re, sys
f = pathlib.Path(sys.argv[1]); s = f.read_text()
PIN = "(env.runtime.node.defaultChild as CfnResource).addPropertyOverride('PlatformVersion', 'V1');"
code = re.sub(r"/\*.*?\*/", "", s, flags=re.S)                                   # block comments do not count
code = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith("//"))   # nor // lines
imp = re.search(r"^import \{([^}]*)\} from 'aws-cdk-lib';", code, re.M)
has_cfn = bool(imp and re.search(r"\bCfnResource\b", imp.group(1)))
mentions = len(re.findall("PlatformVersion", code))
if code.count(PIN) == 1 and mentions == 1 and has_cfn:
    sys.exit(0)                                   # already pinned, exactly as expected
if mentions:
    sys.exit("cdk-stack.ts sets PlatformVersion in an unexpected form (not the V1 pin) — fix by hand")
anchor = "    this.application = new AgentCoreApplication(this, 'Application', appProps as any);\n"
imp_old = "import { CfnOutput, Stack, type StackProps } from 'aws-cdk-lib';\n"
imp_new = "import { CfnOutput, CfnResource, Stack, type StackProps } from 'aws-cdk-lib';\n"
if s.count(anchor) != 1:
    sys.exit("cdk-stack.ts anchors not found — vended template changed; pin by hand")
if s.count(imp_old) == 1:
    s = s.replace(imp_old, imp_new)
elif not has_cfn:
    sys.exit("cdk-stack.ts aws-cdk-lib import not recognised — vended template changed; pin by hand")
block = anchor + (
    "\n"
    "    // Pin the AgentCore Runtime platform version to V1 (2026-11-03): the\n"
    "    // service default becomes V2 on 2026-11-12; this project was certified on V1.\n"
    "    for (const env of this.application.environments.values()) {\n"
    "      " + PIN + "\n"
    "    }\n")
f.write_text(s.replace(anchor, block))
print("  platform version pin applied to agentcore/cdk/lib/cdk-stack.ts")
PY

# -- 2. agent registration (once; BYO CodeZip pointing at src/) ---------------
if ! python3 -c "
import json,sys
d=json.load(open('agentcore/agentcore.json'))
sys.exit(0 if any(r['name']=='$AGENT_NAME' for r in d.get('runtimes',[])) else 1)"; then
  # VPC mode when the DB is private (the workshop shape): runtime ENIs land in
  # the given subnets/SG and reach Aurora via the SG self-ref 5432 rule. Needs
  # bedrock-runtime + secretsmanager interface endpoints (or NAT) in the VPC.
  NETWORK_ARGS=()
  if [[ -n "${RUNTIME_SUBNETS:-}" ]]; then
    NETWORK_ARGS=(--network-mode VPC --subnets "$RUNTIME_SUBNETS" \
                  --security-groups "${RUNTIME_SECURITY_GROUPS:?set with RUNTIME_SUBNETS}")
  fi
  agentcore add agent \
    --name "$AGENT_NAME" \
    --type byo --build CodeZip --language Python --framework Strands \
    --model-provider Bedrock \
    --code-location "$REPO/src" \
    --entrypoint main.py \
    --authorizer-type CUSTOM_JWT \
    --discovery-url "$COGNITO_DISCOVERY_URL" \
    --allowed-clients "$COGNITO_CLIENT_ID" \
    --request-header-allowlist "Authorization" \
    "${NETWORK_ARGS[@]}" \
    --json
fi
# ⚠ VERIFIED on a deployed runtime: WITHOUT requestHeaderAllowlist=[Authorization],
# the runtime VALIDATES the JWT but does NOT forward the header to the entrypoint
# — adapter fails "no Authorization header on request context". The LOCAL dev
# server forwards the header unconditionally; the DEPLOYED data plane strips it
# unless allowlisted — exactly the deployed-only divergence harness_deployed
# exists to catch.

# -- 2b. deployment target (create nests none; deploy requires "default") -----
if python3 -c "
import json,sys
t=json.load(open('agentcore/aws-targets.json'))
sys.exit(0 if any(x.get('name')=='default' for x in t) else 1)" 2>/dev/null; then
  :
else
  ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
  REGION="${AWS_REGION:-us-east-1}"
  python3 - "$ACCOUNT" "$REGION" <<'EOF'
import json, sys
targets = [{"name": "default", "description": "workshop deploy target",
            "account": sys.argv[1], "region": sys.argv[2]}]
json.dump(targets, open("agentcore/aws-targets.json", "w"), indent=2)
print("aws-targets.json: default target ->", sys.argv[1], sys.argv[2])
EOF
fi

# -- 3. runtime env vars (idempotent rewrite each run) ------------------------
# The adapter reads config via WORKSHOP_* (config.py). Secret ARN, not password
# (the secret value never rides an env var or template).
python3 - "$AGENT_NAME" <<'EOF'
import json, os, sys
name = sys.argv[1]
p = "agentcore/agentcore.json"
d = json.load(open(p))
rt = next(r for r in d["runtimes"] if r["name"] == name)
env = {
    "WORKSHOP_DB_HOST": os.environ["WORKSHOP_DB_HOST"],
    "WORKSHOP_DB_PORT": os.environ.get("WORKSHOP_DB_PORT", "5432"),
    "WORKSHOP_DB_NAME": os.environ.get("WORKSHOP_DB_NAME", "workshop"),
    "WORKSHOP_DB_USER": os.environ.get("WORKSHOP_DB_USER", "workshop_app"),
    "WORKSHOP_DB_SECRET_ARN": os.environ["WORKSHOP_DB_SECRET_ARN"],
}
for opt in ("WORKSHOP_MODEL_REGION", "WORKSHOP_MODEL_ID"):
    if os.environ.get(opt):
        env[opt] = os.environ[opt]
rt["envVars"] = [{"name": k, "value": v} for k, v in sorted(env.items())]
json.dump(d, open(p, "w"), indent=2)
print(f"envVars set on runtime {name}: {sorted(env)}")
EOF

# -- 4. deploy (CDK; converges on re-run) -------------------------------------
agentcore deploy --yes --verbose

# -- 5. smoke: ping + one unauthenticated invoke MUST fail (authorizer live) --
agentcore fetch 2>&1 | head -20 || true
