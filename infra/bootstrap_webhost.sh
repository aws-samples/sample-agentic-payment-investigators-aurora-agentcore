#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# bootstrap_webhost.sh — the WebHost bootstrap BODY (UserData is a stub that
# exports the stack's values and runs this; EC2 caps UserData at 16KB and the
# module-7 CLI additions blew past it). All ${Var} references
# are plain bash vars exported by the stub. Runs under the stub's set -e and
# cfn-signal EXIT trap.
# postgresql15 = the psql CLIENT (module 2's terminal beat; v15
# client against the PG18 server is fine for workshop usage).
# Amazon repo — within the zero-EXTERNAL-fetches rule.
# postgresql15 = psql client (module 2); jq = the intro's model
# tastes format JSON with it. Amazon repo — zero-external rule holds.
# retry: at first boot AL2023's own dnf jobs (makecache / security updates)
# can hold the lock and clean our cached RPMs mid-transaction
# ("Waiting for process…" then Errno 2 on a cached .rpm). Three
# attempts with a fresh metadata pull between them.
for i in 1 2 3; do
  dnf install -y nginx unzip aws-cfn-bootstrap postgresql15 jq nodejs22 git && break
  echo "dnf attempt $i failed; clearing cache and retrying in 20s"
  dnf clean packages || true
  sleep 20
  [ "$i" = 3 ] && exit 1
done
# ── provenance: the image and toolchain this bootstrap ran on, first thing in
# the log. Upstream drift (a new AL2023 image, a nodejs minor) is then visible
# in CloudWatch /workshop/bootstrap on the day it happens, not after an outage
# (one provisioning failure on 2026-10-01 was two third-party drifts).
IMDS_TOK=$(curl -s -m 3 -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)
echo "--- provenance: ami=$(curl -s -m 3 -H "X-aws-ec2-metadata-token: $IMDS_TOK" http://169.254.169.254/latest/meta-data/ami-id || echo unknown) kernel=$(uname -r) node=$(node --version 2>/dev/null || echo none) npm=$(npm --version 2>/dev/null || echo none) rpms=[$(rpm -q nodejs22 postgresql15 nginx aws-cfn-bootstrap 2>/dev/null | tr '\n' ' ')] ---"
# ── AgentCore CLI (module 7): VENDORED tarball — npm registry never
# touched at provision (zero-external-fetches rule). Pinned at
# author time; version bumps ride the monthly dependency cadence.
aws s3 cp s3://${AssetsBucket}/${AssetsPrefix}vendor/aws-agentcore-cli-0.25.0.tar.gz /tmp/ac-cli.tar.gz
mkdir -p /usr/lib/node_modules
tar xzf /tmp/ac-cli.tar.gz -C /usr/lib/node_modules && rm -f /tmp/ac-cli.tar.gz
ln -sf /usr/lib/node_modules/@aws/agentcore/dist/cli/index.mjs /usr/bin/agentcore
chmod +x /usr/lib/node_modules/@aws/agentcore/dist/cli/index.mjs
# project skeleton (pre-created `agentcore create` output, incl the
# cdk node_modules) + a warmed uv cache: `agentcore add agent` and
# `agentcore deploy` then run fully offline (UV_OFFLINE honors it;
# verified 2026-07-29: synth rc=0 with proxies poisoned)
# user FIRST: `useradd -m` skips /etc/skel (.bashrc/.bash_profile) when the
# home dir already exists — symptom: bare bash-5.2$ prompts + no auto-cd
id -u workshop >/dev/null 2>&1 || useradd -m workshop
mkdir -p /home/workshop/environment
# No ~/.agent-stage seed: the attendee workspace ships CARVED (app.zip),
# so the belt list itself IS module 4's starting state — one tool, organic
# blind run, no dial. The dial (AGENT_STAGE env / ~/.agent-stage file) is
# facilitator-only now, for catchup/demo on a solved workspace. The
# deployed runtime is clamped separately (CFN AGENT_STAGE: evidence).
aws s3 cp s3://${AssetsBucket}/${AssetsPrefix}vendor/agentcore-project-skel.tar.gz /tmp/ac-skel.tar.gz
tar xzf /tmp/ac-skel.tar.gz -C /home/workshop/environment && rm -f /tmp/ac-skel.tar.gz
aws s3 cp s3://${AssetsBucket}/${AssetsPrefix}vendor/uv-cache-agent.tar.gz /tmp/uvwarm.tar.gz
mkdir -p /home/workshop/.cache
tar xzf /tmp/uvwarm.tar.gz -C /tmp && rm -rf /home/workshop/.cache/uv && mv /tmp/uvwarm /home/workshop/.cache/uv
rm -f /tmp/uvwarm.tar.gz
# ZERO external fetches: the runtime
# env (uv + CPython 3.14 + fully-resolved .venv) ships as ONE
# pre-built tarball in the assets bucket — no astral.sh, no PyPI,
# no python CDN at provision time. Rebuild/upgrade path:
# infra/build_runtime_bundle.sh (bump pins → rebuild → update-stack).
aws s3 cp s3://${AssetsBucket}/${AssetsPrefix}runtime-env.tar.gz /tmp/runtime-env.tar.gz
tar xzf /tmp/runtime-env.tar.gz -C /opt
# participants run `uv run …` (the content assumes it) — the
# vendored binary IS the uv on PATH; no installer at provision time
ln -sf /opt/bundle/uv /usr/local/bin/uv
# app.zip = the ATTENDEE WORKSPACE (~/environment/app/api) — stays on the
# box. The floor UI + api serving moved to S3+CloudFront / Lambda (the
# app tier is production from provision); nothing is served from here
# except the Code Editor.
aws s3 cp s3://${AssetsBucket}/${AssetsPrefix}api/app.zip /tmp/app.zip
mkdir -p /opt/workshop/api
unzip -o /tmp/app.zip -d /opt/workshop/api
# graft the pre-built venv onto the shipped code (same pyproject).
# rm first: a RE-RUN (iterate_bootstrap.sh) would otherwise nest
# .venv inside the existing one. NOTE: CFN UserData updates do NOT
# re-run on healthy instances (cloud-init runs once per instance) —
# bootstrap changes are only exercised by CREATE or failed-state
# replacement; use iterate_bootstrap.sh to apply to a live box.
rm -rf /opt/workshop/api/.venv
mv /opt/bundle/api/.venv /opt/workshop/api/.venv
# the venv's interpreter symlinks point into /opt/bundle/python — keep it
# analyst password: the code-editor connection token (config.js is stamped
# into the SITE BUCKET by Custom::SiteDeploy now — no per-box web config)
# WORKSHOP ONLY: one generated credential opens both the UI and the editor so
# the attendee has a single value to carry. A real deployment issues the
# editor its own token and keeps it out of unit files and process arguments
# (see the unit below).
ANALYST_PW=$(aws secretsmanager get-secret-value --secret-id ${AnalystSecretArn} \
  --region ${CFN_REGION} --query SecretString --output text | python3 -c "import json,sys; print(json.load(sys.stdin)['password'])")
# NO api service: the bridge runs as a Lambda behind CloudFront (app tier
# is production from provision). This box hosts the workbench only.
# nginx: the Code Editor proxy ONLY (WebSocket-safe). OWN the whole
# nginx.conf — AL2023's stock default server lives IN nginx.conf (not
# conf.d) and its [::]:80 listener swallows localhost requests.
# The floor UI + api are CloudFront -> S3/Lambda now; a stray hit on
# this box's root gets a plain note instead of a broken app.
cat > /etc/nginx/nginx.conf <<NGINX
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log notice;
pid /run/nginx.pid;
events { worker_connections 1024; }
http {
  include /etc/nginx/mime.types;
  default_type application/octet-stream;
  sendfile on;
  keepalive_timeout 65;
  map \$http_upgrade \$connection_upgrade {
    default upgrade;
    "" close;
  }
  server {
    listen 80 default_server;
    listen [::]:80 default_server;
    location = / {
      default_type text/plain;
      return 200 'payment-ws workbench host. The floor UI is served by CloudFront; this box hosts the Code Editor (/code/) and your terminals.\n';
    }
    location /code/ {
      proxy_pass http://127.0.0.1:8443/;
      proxy_set_header Host \$host;
      proxy_set_header Upgrade \$http_upgrade;
      proxy_set_header Connection \$connection_upgrade;
      proxy_set_header Accept-Encoding gzip;
      proxy_read_timeout 86400s;
      proxy_buffering off;
    }
  }
}
NGINX
systemctl enable --now nginx
systemctl reload nginx   # config written above must win even if nginx pre-started (iterate path)
# doors-open smoke moved INTO the stack: Custom::BridgeSmoke proves the
# production bridge (health + authed RLS read with a real Cognito token)
# because this box no longer hosts the api. The workbench smoke (editor
# through nginx) runs at the END of this script — the editor starts last.
# fresh-account Bedrock warmup (non-fatal): first model call in a
# new account can 400 for ~60s (Marketplace subscription
# race). Trigger it NOW so participants never see it.
for M in "${ModelId}" "${CopilotModelId}"; do
  aws bedrock-runtime converse --region ${CFN_REGION} --model-id "$M" \
    --messages '[{"role":"user","content":[{"text":"hi"}]}]' \
    --inference-config '{"maxTokens":1}' >/dev/null 2>&1 || true
done
# rerank warmup (module 4 CORE): cohere.rerank-v3-5 is a THIRD-PARTY
# model — first invoke triggers the Marketplace auto-subscribe, which
# can AccessDenied for up to ~15 min on a fresh account. Fire it at
# provision so no participant ever hits the race. Non-fatal.
aws bedrock-agent-runtime rerank --region ${CFN_REGION} \
  --queries '[{"type":"TEXT","textQuery":{"text":"warmup"}}]' \
  --sources '[{"type":"INLINE","inlineDocumentSource":{"type":"TEXT","textDocument":{"text":"warmup document"}}}]' \
  --reranking-configuration "{\"type\":\"BEDROCK_RERANKING_MODEL\",\"bedrockRerankingConfiguration\":{\"modelConfiguration\":{\"modelArn\":\"arn:aws:bedrock:${CFN_REGION}::foundation-model/cohere.rerank-v3-5:0\"},\"numberOfResults\":1}}" \
  >/dev/null 2>&1 || true
# ── workshop env seeding (QoL): every value a participant
# or lab step could need lives in /etc/workshop.env — CFN Outputs
# stay MINIMAL (urls only). Terminals auto-source it.
cat > /etc/workshop.env <<WSENV
export WORKSHOP_REGION=${CFN_REGION}
# the agentcore CLI (and every boto3 default) reads AWS_REGION, NOT
# WORKSHOP_REGION — without these it falls back to us-east-1 and the
# module-7 deploy targets the wrong region in any non-us-east-1 event
export AWS_REGION=${CFN_REGION}
export AWS_DEFAULT_REGION=${CFN_REGION}
# settings.model_region (investigator config) defaults to us-east-1 and is
# passed to boto3 EXPLICITLY (region_name=), so the AWS_REGION default chain
# never applies — without this, login-shell runs (check.sh m9/m10/m11, dev-loop
# chat) hit the wrong region's control plane in any non-us-east-1 event.
# The systemd unit exports it separately for the api bridge.
export WORKSHOP_MODEL_REGION=${CFN_REGION}
export WORKSHOP_DB_HOST=${DbHost}
export WORKSHOP_DB_NAME=workshop
export WORKSHOP_DB_USER=workshop_app
export WORKSHOP_DB_SECRET_ARN=${AppSecretArn}
export WORKSHOP_ANALYST_SECRET_ARN=${AnalystSecretArn}
export WORKSHOP_RUNTIME_ARN=${RuntimeArn}
# the PRODUCTION bridge Lambda: go-live repoints it, check m9 reads it back
export BRIDGE_FN=${BridgeFn}
# the productionize module (agentcore CLI): the values `agentcore add agent` needs
export WORKSHOP_SUBNETS=${PrivateSubnetA},${PrivateSubnetB}
export WORKSHOP_SECURITY_GROUP=${WorkshopSG}
export WORKSHOP_EXEC_ROLE_ARN=${RuntimeExecRoleArn}
export WORKSHOP_DISCOVERY_URL=https://cognito-idp.${CFN_REGION}.amazonaws.com/${PoolId}/.well-known/openid-configuration
export UV_OFFLINE=1
export WORKSHOP_COGNITO_POOL_ID=${PoolId}
export WORKSHOP_COGNITO_CLIENT_ID=${UserPoolClient}
export WORKSHOP_MODEL_ID=${ModelId}
export WORKSHOP_COPILOT_MODEL_ID=${CopilotModelId}
export WORKSHOP_EMBED_MODEL_ID=${EmbedModelId}
export WORKSHOP_RERANK_MODEL_ID=${RerankModelId}
# uv runs against the vendored, exact-pinned venv — never re-resolve
# or touch PyPI mid-event; python is in-bundle too
export UV_NO_SYNC=1
export UV_PYTHON_INSTALL_DIR=/opt/bundle/python
# libpq conventions: bare `psql` (and pg_dump etc.) target the
# workshop DB with no flags. NOT the password — that stays in
# Secrets Manager, resolved per invocation by the psql() function
export PGHOST=${DbHost}
export PGPORT=5432
export PGDATABASE=workshop
export PGUSER=workshop_app
# HNSW recall floor: default ef_search=40 can miss exact matches on an
# unlucky parallel-built graph (measured live); 200 = effectively exact
# at this corpus size. libpq startup option — psql beats AND the dev-loop
# agent (psycopg is libpq) inherit it from the environment.
export PGOPTIONS='-c hnsw.ef_search=200'
WSENV
chmod 644 /etc/workshop.env
cat > /etc/profile.d/workshop.sh <<'WSPROF'
[ -f /etc/workshop.env ] && source /etc/workshop.env
# module-7 cutover override (go-live writes it): KEY=VALUE format,
# shared verbatim with the systemd unit's EnvironmentFile
if [ -f /etc/workshop-runtime.env ]; then set -a; source /etc/workshop-runtime.env; set +a; fi
# bare `psql` just works: PGHOST/PGUSER/PGDATABASE come from env
# above; the password is fetched from Secrets Manager AT CALL TIME
# and injected via env for the single child process — never
# exported to the shell, never written to disk (no .pgpass)
psql() {
  PGPASSWORD=$(aws secretsmanager get-secret-value \
    --secret-id "$WORKSHOP_DB_SECRET_ARN" --region "$WORKSHOP_REGION" \
    --query SecretString --output text \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['password'])") \
  command psql "$@"
}
# every participant terminal starts in the project (content commands
# assume it: `uv run …`, `scripts/…`); interactive workshop shells only
if [ -n "$PS1" ] && [ "$(id -un)" = "workshop" ] && [ -d "$HOME/environment/app/api" ]; then
  cd "$HOME/environment/app/api"
fi
WSPROF
# ── module-7 go-live helper: the ONLY root surface the attendee
# touches. Validates the ARN shape, writes the override env file
# atomically, restarts the api bridge. scripts/go_live.sh calls it
# via a sudoers rule scoped to exactly this command.
cat > /usr/local/bin/workshop-go-live <<'GOLIVE'
#!/bin/bash
set -euo pipefail
ARN="${1:?usage: workshop-go-live <agent-runtime-arn>}"
[[ "$ARN" =~ ^arn:aws:bedrock-agentcore:[a-z0-9-]+:[0-9]{12}:runtime/[A-Za-z0-9_-]+$ ]]               || { echo "not an agent-runtime ARN: $ARN" >&2; exit 1; }
source /etc/workshop.env
# terminals: the override env file (profile.d sources it on every shell)
TMP=$(mktemp)
echo "RUNTIME_ARN=$ARN" > "$TMP"
echo "WORKSHOP_RUNTIME_ARN=$ARN" >> "$TMP"
install -m 644 "$TMP" /etc/workshop-runtime.env && rm -f "$TMP"
# the floor: repoint the PRODUCTION bridge. update-function-configuration
# REPLACES Environment wholesale — read-modify-write, then wait for the
# config to land so the very next invoke sees the new runtime.
ENV_JSON=$(aws lambda get-function-configuration --function-name "$BRIDGE_FN" \
  --region "$WORKSHOP_REGION" --query 'Environment.Variables' --output json \
  | python3 -c "import json,sys; v=json.load(sys.stdin); v['RUNTIME_ARN']='$ARN'; print(json.dumps({'Variables':v}))")
aws lambda update-function-configuration --function-name "$BRIDGE_FN" \
  --region "$WORKSHOP_REGION" --environment "$ENV_JSON" >/dev/null
aws lambda wait function-updated --function-name "$BRIDGE_FN" --region "$WORKSHOP_REGION"
GOLIVE
chmod 755 /usr/local/bin/workshop-go-live
echo 'workshop ALL=(root) NOPASSWD: /usr/local/bin/workshop-go-live *'               > /etc/sudoers.d/workshop-go-live
chmod 440 /etc/sudoers.d/workshop-go-live
# ── AWS Code Editor (VENDORED tarball; ?tkn= URL login) ────
# ?tkn= URL sign-in is native to code-editor-server
# (CodeEditorURL ...?tkn=<token>); coder's code-server
# lacks it — hence AWS Code Editor, mirrored into our assets.
id -u workshop >/dev/null 2>&1 || useradd -m workshop
CE_DIR=/home/workshop/.local/lib/code-editor
mkdir -p $CE_DIR /home/workshop/.local/bin /home/workshop/environment
aws s3 cp s3://${AssetsBucket}/${AssetsPrefix}code-editor-server.tar.gz /tmp/ce.tar.gz
tar -xzf /tmp/ce.tar.gz -C $CE_DIR
ln -sf $CE_DIR/dist/bin/code-editor-server /home/workshop/.local/bin/code-editor-server
cp /etc/workshop.env /home/workshop/environment/.env || true
ln -sfn /opt/workshop /home/workshop/environment/app
# editor defaults (QoL): Dark Modern + no workspace-trust dialog —
# first open lands directly in a themed, trusted workspace
mkdir -p /home/workshop/.code-editor-server/data/User /home/workshop/.code-editor-server/data/Machine
cat > /home/workshop/.code-editor-server/data/User/settings.json <<VSSET
{
  "workbench.colorTheme": "Default Dark Modern",
  "workbench.startupEditor": "none",
  "workbench.editor.enablePreview": false,
  "workbench.panel.defaultLocation": "bottom",
  "terminal.integrated.hideOnStartup": "never",
  "workbench.panel.opensMaximized": "never",
  "task.allowAutomaticTasks": "on",
  "terminal.integrated.fontSize": 16,
  "terminal.integrated.lineHeight": 1.25,
  "editor.fontSize": 14,
  "security.workspace.trust.enabled": false,
  "security.workspace.trust.startupPrompt": "never",
  "security.workspace.trust.banner": "never",
  "telemetry.telemetryLevel": "off",
  "update.mode": "none",
  "terminal.integrated.defaultProfile.linux": "bash"
}
VSSET
cp /home/workshop/.code-editor-server/data/User/settings.json /home/workshop/.code-editor-server/data/Machine/settings.json
# auto-open a terminal on folder open (workshop is terminal-first)
mkdir -p /home/workshop/environment/.vscode
cat > /home/workshop/environment/.vscode/tasks.json <<VSTASK
{
  "version": "2.0.0",
  "tasks": [{
    "label": "terminal",
    "type": "shell",
    "command": "bash",
    "presentation": { "reveal": "always", "focus": true, "panel": "dedicated" },
    "runOptions": { "runOn": "folderOpen" },
    "problemMatcher": []
  }]
}
VSTASK
chown -R workshop:workshop /home/workshop
# /opt/workshop is the real tree behind the ~/environment/app symlink — the
# home-dir chown does NOT cross the link. Without this, solve.sh's .bak
# copy and any attendee edit die with Permission denied (app.zip unzips
# as root; workspace files must belong to workshop)
chown -R workshop:workshop /opt/workshop
# WORKSHOP ONLY: the connection token is passed as a process argument, so it
# is readable in this unit file and in `ps` on the box. The box is the
# attendee's own lab workbench, reached only through the token-gated editor.
# A real deployment reads the token from a mode-600 file or a secret at start.
cat > /usr/lib/systemd/system/code-editor@.service <<CEUNIT
[Unit]
Description=aws code editor
After=network.target
[Service]
Type=exec
ExecStart=/home/workshop/.local/bin/code-editor-server --accept-server-license-terms --host 127.0.0.1 --port 8443 --connection-token $ANALYST_PW --default-workspace /home/workshop/environment --default-folder /home/workshop/environment
Restart=always
User=%i
[Install]
WantedBy=default.target
CEUNIT
systemctl enable --now code-editor@workshop
# workbench smoke: the editor answers through nginx (any HTTP status is
# life; token auth means bare /code/ may 200 or 403 depending on version)
echo "--- smoke: code editor through nginx ---"
EOK=0
for i in $(seq 1 18); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/code/ 2>/dev/null || echo 000)
  case "$CODE" in 2??|3??|401|403) EOK=1; break;; esac
  sleep 5
done
[ "$EOK" = "1" ] || { echo "CODE EDITOR NEVER ANSWERED (90s): last http=$CODE"; systemctl status code-editor@workshop --no-pager 2>/dev/null | head -20; exit 1; }
echo "--- editor answering (http $CODE) after ~$((i*5))s ---"
echo "BOOTSTRAP OK"
