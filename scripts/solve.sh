#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# solve.sh m4|m5|m6|m7 — install a module's finished tools_disputes.py.
#
# Two roles. For modules 5, 6 and 7 this IS the module's step (the page
# says "one command"). For module 4 it is the fallback behind
# `check.sh m4 fix` when the hand edit goes wrong.
#
# Solutions are PER-MODULE snapshots (<name>.solution.mN.py): everything up
# to and including your module is solved; later modules' exercises stay
# yours to write. solve.sh m4 will never hand you module 7's draft_and_route
# wiring early — the agent would auto-file cases you haven't studied yet.
#
# Your current file is saved beside it as <name>.py.bak first — nothing is
# ever lost. The output names each change in plain words; keep the changes()
# table below in step with the CARVE sites in src/investigator/
# tools_disputes.py. check.sh <module> stays in each page's Stuck? box.
set -euo pipefail
cd "$(dirname "$0")/.."

MOD="${1:-}"
case "$MOD" in
  m4|m5|m6|m7) FILES=(investigator/tools_disputes.py) ;;
  *) echo "usage: solve.sh m4|m5|m6|m7   — install that module's finished version" >&2
     exit 1 ;;
esac

# what the finished version of each module brings, one line per change
changes() {
  case "$1" in
    m4) echo "check_ce3_eligibility: the CE 3.0 date window is in place (prior purchases 120 to 365 days before the disputed one)"
        echo "tool belt: get_transaction_history and check_ce3_eligibility wired in" ;;
    m5) echo "tool belt: find_precedents wired in" ;;
    m6) echo "find_precedents: naive_knn replaced by the two-stage pipeline (hybrid search, then Cohere Rerank)"
        echo "tool belt: get_rule_clauses wired in" ;;
    m7) echo "tool belt: draft_and_route wired in" ;;
  esac
}

swapped=0
for f in "${FILES[@]}"; do
  base="$(basename "${f%.py}")"
  sol="$(dirname "$f")/solutions/${base}.solution.$MOD.py"
  # a file whose sites all belong to one module ships only that snapshot;
  # fall back to the full solution when the per-module one isn't there
  [ -f "$sol" ] || sol="$(dirname "$f")/solutions/${base}.solution.py"
  if [ ! -f "$sol" ]; then
    echo "solve $MOD: no finished version for $f in solutions/ — pre-carve workspace (the code is already complete)."
    continue
  fi
  if cmp -s "$sol" "$f"; then
    echo "solve $MOD: $f already matches the finished module ${MOD#m} version — nothing to change; already in place:"
    changes "$MOD" | sed 's/^/  = /'
    continue
  fi
  cp "$f" "$f.bak"
  cp "$sol" "$f"
  echo "solve $MOD: $f is now the finished module ${MOD#m} version (your file is saved as $f.bak)"
  changes "$MOD" | sed 's/^/  + /'
  swapped=1
done

if [ "${SOLVE_TAIL:-}" != quiet ]; then     # check.sh fix / catchup.sh run the check themselves
  case "$MOD" in
    m4) echo "next: scripts/check.sh m4" ;;
    *)  echo "done — carry on with the page (scripts/check.sh $MOD sits in the page's Stuck? box if something looks off)" ;;
  esac
fi
