#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# psql on the workshop DB — non-interactive/script contexts (systemd, SSM,
# harness) where the interactive psql() shell function from profile.d isn't
# loaded. Interactive terminals just type `psql`. Same recipe either way:
# libpq params from env, password from Secrets Manager AT CALL TIME (never
# on disk, never exported).
set -euo pipefail
[ -f /etc/workshop.env ] && source /etc/workshop.env
# module-7 cutover override wins (go_live.sh writes it)
if [ -f /etc/workshop-runtime.env ]; then set -a; source /etc/workshop-runtime.env; set +a; fi
: "${WORKSHOP_DB_SECRET_ARN:?run on the workshop host (or set WORKSHOP_* yourself)}"
PGPASSWORD=$(aws secretsmanager get-secret-value --secret-id "$WORKSHOP_DB_SECRET_ARN" \
  --region "$WORKSHOP_REGION" --query SecretString --output text \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['password'])") \
PGHOST="${PGHOST:-$WORKSHOP_DB_HOST}" PGPORT="${PGPORT:-5432}" \
PGDATABASE="${PGDATABASE:-$WORKSHOP_DB_NAME}" PGUSER="${PGUSER:-$WORKSHOP_DB_USER}" \
exec psql "$@"
