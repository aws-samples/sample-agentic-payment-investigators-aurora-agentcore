# webui build/publish targets.
# The SPA is buildless (ES modules, no bundler) — "build" = copy + config stamp.

assets := ../.assets-out

serve:            ## local dev: SPA on :8081, api proxied (run api separately on :8080)
	@echo "serving webui on http://localhost:8081 (api expected on :8080)"
	@python3 -m http.server 8081 --directory .

zip-assets:       ## package ALL deployable assets (webui, api, agent bundle, provisioner, seeds)
	@mkdir -p $(assets)/api $(assets)/agent $(assets)/seeds
	@# zip -qr APPENDS into an existing archive — a layout rename leaves the
	@# old paths as ghosts (pre-rename files ride along beside the new ones).
	@# Start every build from empty zips.
	@rm -f $(assets)/webui.zip $(assets)/api/app.zip $(assets)/provisioner.zip
	@zip -qr $(assets)/webui.zip index.html styles.css config.js js
	@cd ../api && zip -qr ../webui/$(assets)/api/app.zip app.py pyproject.toml README.md -x "*__pycache__*"
	@# The workspace zip and the agent bundle below both carry the complete
	@# investigator package. The workshop build hollows the exercise sites
	@# in the workspace copy. This public tree skips that step, so both
	@# copies are the same code.
	@rm -rf /tmp/app-stage && mkdir -p /tmp/app-stage
	@cd ../src && cp -R investigator main.py /tmp/app-stage/
	@find /tmp/app-stage -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@cd /tmp/app-stage && zip -qr "$(abspath $(assets))/api/app.zip" investigator main.py
	@# participant scripts land INSIDE api/ (content runs `scripts/…` from app/api;
	@# authoring-side scripts — harness*, audit, package, preview — never ship)
	@cd .. && zip -q "$(abspath $(assets))/api/app.zip" \
	  scripts/smoke.sh scripts/invoke.sh scripts/chat.sh \
	  scripts/psql.sh scripts/check.sh scripts/check_modules.py \
	  scripts/catchup.sh scripts/get_token.sh scripts/go_live.sh scripts/wire_env.sh \
	  scripts/compare_retrieval.py scripts/doublefire.py scripts/storm.py \
	  scripts/run_queue.sh scripts/solve.sh
	@# agent CodeZip: DEPENDENCY-VENDORED runtime bundle (entrypoint at zip
	@# ROOT — CFN EntryPoint: main.py). Vendored because AgentCore gives a
	@# container 30s to init: a source-only bundle makes the runtime resolve
	@# pyproject server-side and EVERY COLD CONTAINER 424s (warm containers
	@# mask it). aarch64 = the runtime's arch.
	@rm -rf /tmp/agent-bundle && mkdir -p /tmp/agent-bundle
	@cd ../src && cp -R main.py pyproject.toml investigator /tmp/agent-bundle/
	@cd ../src && uv pip install -q --target /tmp/agent-bundle \
	  --python-platform aarch64-manylinux_2_28 --python-version 3.14 .
	@find /tmp/agent-bundle -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@# SCAN DIET (the workshop build pipeline caps scanned archives at 5,000 files
	@# cumulative; a fully-vendored bundle exceeds it): drop unused botocore service models —
	@# the runtime creates only bedrock*/secretsmanager/sts clients.
	@find /tmp/agent-bundle/botocore/data -mindepth 1 -maxdepth 1 -type d \
	  ! -name 'bedrock*' ! -name 'secretsmanager' ! -name 'sts' -exec rm -rf {} +
	@echo "  bundle file count: $$(find /tmp/agent-bundle -type f | wc -l | tr -d ' ') (scanner cap 5000 cumulative)"
	@# import-smoke the tree structure (cross-arch .so can't import here —
	@# assert the load-bearing packages LANDED instead)
	@python3 -c "import pathlib; r = pathlib.Path('/tmp/agent-bundle'); \
	  missing = [p for p in ('strands','bedrock_agentcore','psycopg','pgvector','pydantic','boto3','investigator') \
	             if not (r/p).is_dir()]; \
	  assert not missing, f'vendored bundle missing: {missing}'; \
	  assert (r/'main.py').is_file(), 'entrypoint missing'; \
	  print(f'  bundle tree OK ({sum(f.stat().st_size for f in r.rglob(chr(42)) if f.is_file())//(1<<20)} MB vendored)')"
	@rm -f $(assets)/agent/bundle.zip
	@cd /tmp/agent-bundle && zip -qr "$(abspath $(assets))/agent/bundle.zip" .
	@# provisioner: seeder handler + seed mechanics + SQL + vendored psycopg (Lambda arm64/py312)
	@rm -rf /tmp/provisioner && mkdir -p /tmp/provisioner
	@cp ../infra/seeder.py ../infra/runtime_manager.py ../seeds/load_seeds.py /tmp/provisioner/
	@cp ../workshop/sql/schema.sql ../workshop/sql/indexes_postload.sql /tmp/provisioner/
	@uv pip install -q --target /tmp/provisioner --python-platform x86_64-manylinux_2_28 \
	  --python-version 3.14 "psycopg[binary]==3.3.6" "boto3==1.43.118"
	@# boto3 vendored for runtime_manager.py: the Lambda runtime's bundled
	@# botocore may lag the bedrock-agentcore-control service model
	@find /tmp/provisioner/botocore/data -mindepth 1 -maxdepth 1 -type d \
	  ! -name 'bedrock*' ! -name 'secretsmanager' ! -name 'sts' ! -name 's3*' -exec rm -rf {} + 2>/dev/null || true
	@find /tmp/provisioner -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "  provisioner file count: $$(find /tmp/provisioner -type f | wc -l | tr -d ' ')"
	@cd /tmp/provisioner && zip -qr "$(abspath $(assets))/provisioner.zip" .
	@cp ../seeds/output/*.tsv.gz $(assets)/seeds/ 2>/dev/null || echo "  (no .tsv.gz yet — gzip the embedded TSVs first)"
	@# BRIDGE LAMBDA (app tier production-from-provision): the SAME api surface
	@# as app.zip, dependency-vendored for the Lambda python3.14 runtime.
	@# manylinux wheels are LOAD-BEARING — mac-built binaries (pydantic-core,
	@# psycopg) die at import with Runtime.ExitError. Runs behind the vendored
	@# Lambda Web Adapter layer (run.sh bootstrap; LWA needs python3, not python).
	@# strands-agents-tools stays out (zero imports — the scan-diet finding).
	@rm -rf /tmp/bridge-lambda && mkdir -p /tmp/bridge-lambda
	@cd ../src && uv pip install -q --target /tmp/bridge-lambda \
	  --python-platform x86_64-manylinux_2_28 --python-version 3.14 \
	  "fastapi==0.141.1" "starlette==1.10.0" "uvicorn==0.54.0" "httpx==0.28.1" "PyJWT[crypto]==2.15.1" \
	  "boto3==1.43.118" "cryptography==50.0.2" \
	  "pydantic==2.14.1" "pydantic-settings==2.16.1" "psycopg[binary]==3.3.6" "pgvector==0.5.1"
	@cp ../api/app.py /tmp/bridge-lambda/
	@cp -R /tmp/app-stage/investigator /tmp/bridge-lambda/investigator
	@# STUB __init__: importing ANY submodule executes the package __init__,
	@# which pulls .agents -> strands (dieted out of this bundle). The bridge
	@# imports only config/identity/db directly (see api/app.py import note).
	@# Found live: bed27 Runtime.ExitError, ModuleNotFoundError strands.
	@printf '%s\n' "\"\"\"investigator (bridge bundle): agent imports stripped - config/identity/db only.\"\"\"" > /tmp/bridge-lambda/investigator/__init__.py
	@printf '#!/bin/bash\nexec python3 -m uvicorn app:app --host 0.0.0.0 --port 8080\n' > /tmp/bridge-lambda/run.sh
	@chmod +x /tmp/bridge-lambda/run.sh
	@find /tmp/bridge-lambda -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@find /tmp/bridge-lambda/botocore/data -mindepth 1 -maxdepth 1 -type d \
	  ! -name 'bedrock*' ! -name 'secretsmanager' ! -name 'sts' -exec rm -rf {} +
	@echo "  bridge-lambda file count: $$(find /tmp/bridge-lambda -type f | wc -l | tr -d ' ') (scanner cap shared)"
	@rm -f $(assets)/api/bridge-lambda.zip
	@cd /tmp/bridge-lambda && zip -qr "$(abspath $(assets))/api/bridge-lambda.zip" .
	@# STANDALONE deploy-time fetches (NOT inside any zip): the WebHost UserData
	@# curls bootstrap_webhost.sh, and the CdkBootstrap nested stack fetches
	@# vendor/cdk-bootstrap-template.yaml — both from ${AssetsBucket}/${AssetsPrefix}.
	@# They MUST live in $(assets) so `aws s3 sync $(assets) s3://ws-bucket/<UUID> --delete`
	@# carries them (without this the --delete strips them and provisioning 404s).
	@cp ../infra/bootstrap_webhost.sh $(assets)/bootstrap_webhost.sh
	@mkdir -p $(assets)/vendor && cp ../infra/vendor/cdk-bootstrap-template.yaml $(assets)/vendor/
	@# NOTE: vendor/lwa-layer.zip (Lambda Web Adapter) is a BUCKET-RESIDENT
	@# vendored asset like the agentcore-cli/runtime-env tarballs — uploaded
	@# once, never rebuilt here, and deliberately NOT in $(assets) (the WS
	@# sync stays targeted-cp for vendor tarballs; see infra/publish_public.sh).
	@echo "assets -> $(assets)/ (webui.zip, api/app.zip + bridge-lambda.zip, agent/bundle.zip, provisioner.zip, seeds/, bootstrap_webhost.sh, vendor/)"

