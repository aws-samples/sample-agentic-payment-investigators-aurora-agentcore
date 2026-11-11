#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# check.sh m4..m11 — per-module exercise validation. Thin wrapper: the
# assertions live in check_modules.py (structured payloads only — DB rows,
# JSON fields, tool returns, typed chunks, control-plane state; never
# substrings of model prose). m9/m10/m11 assert the deployed floor and are meant
# to run ON the WebHost; off-host they SKIP loudly, never a silent pass.
set -euo pipefail
[ -f /etc/workshop.env ] && source /etc/workshop.env
# module-9 cutover override wins (go_live.sh writes it)
if [ -f /etc/workshop-runtime.env ]; then set -a; source /etc/workshop-runtime.env; set +a; fi
cd "$(dirname "$0")/.."
MOD="${1:?usage: check.sh m4|m5|m6|m7|m8|m9|m10|m11 [diff|fix]}"
# Attendee-facing verb (see the Troubleshoot appendix):
#   check.sh mN fix  — swap in the module's solution (solve.sh), then run
# the check on the result: one command from red to verified, for attendees
# who can't recover by hand. The hand-edit exercise is m4; m5-m7's steps
# are scripted (solve.sh mN is the step itself), fix/diff still apply.
if [ "${2:-}" = "fix" ]; then
  case "$MOD" in
    m4|m5|m6|m7) SOLVE_TAIL=quiet scripts/solve.sh "$MOD"; echo ;;
    *) echo "fix: $MOD has no file exercise to swap — read the check's own why-fail lines for recovery." ;;
  esac
fi
#   check.sh mN diff — show exactly where YOUR file differs from the
# module's finished version (the per-module snapshots ship in
# investigator/solutions/). Read-only: fix it yourself and keep your work.
if [ "${2:-}" = "diff" ]; then
  case "$MOD" in
    m4|m5|m6|m7) FILES="tools_disputes" ;;
    *) echo "diff: $MOD has no file exercise — run check.sh $MOD and read its why-fail lines."; exit 0 ;;
  esac
  CLEAN=1
  for f in $FILES; do
    SNAP="investigator/solutions/${f}.solution.${MOD}.py"
    CUR="investigator/${f}.py"
    # --color=auto: red = finished version, green = your file as it is — only
    # when stdout is a terminal (pipes/captures stay plain text)
    if diff -u --color=auto --label "expected ($MOD complete)" --label "yours" "$SNAP" "$CUR"; then
      echo "ok  ${f}.py matches the $MOD finished version"
    else
      CLEAN=0
    fi
  done
  if [ "$CLEAN" != "1" ]; then
    case "$MOD" in
      m5|m6|m7) echo "
'-' lines are the finished version, the lines the script will add.
'+' lines are your file as it is now. Install the finished version: scripts/solve.sh $MOD" ;;
      *) echo "
'-' lines are the finished version. '+' lines are your file as it is now.
Fix the difference in the editor, or install the finished version: scripts/check.sh $MOD fix" ;;
    esac
  fi
  exit 0
fi
# resolve the app-role password the same way everything else does
if [ -z "${WORKSHOP_DB_PASSWORD:-}" ] && [ -n "${WORKSHOP_DB_SECRET_ARN:-}" ]; then
  WORKSHOP_DB_PASSWORD=$(aws secretsmanager get-secret-value \
    --secret-id "$WORKSHOP_DB_SECRET_ARN" --region "$WORKSHOP_REGION" \
    --query SecretString --output text \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['password'])")
  export WORKSHOP_DB_PASSWORD
fi
if command -v uv >/dev/null && [ -f pyproject.toml ]; then
  exec uv run scripts/check_modules.py "$MOD"
fi
exec python3 scripts/check_modules.py "$MOD"
