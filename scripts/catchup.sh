#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# catchup.sh m4..m11 — fast-forward to a module's COMPLETED state so a stuck
# attendee can start the NEXT module. Facilitators would rather you catch up
# than copy-paste in a hurry.
#
# m4..m7 are CODE modules: `git switch mN-done` on the workspace repo (stash-
# safe). The mN-done branches are cut when the exercises are carved:
# main = carved TODOs, mN-done = that module complete, solution =
# everything. Until the carve, all branches are identical — this path is wired
# and inert.
#
# m9 (deploy), m10 (UI review clicks) and m11 (optional queue drain) write no
# participant CODE — there is no mN-done branch. Catching up means getting the
# FLOOR to the module's end state: m9 = deploy + cut over; m10 = have at least
# one worked review card; m11 = the breaks queue drained (the fast-forward IS
# the exercise). These paths drive the same scripts/UI the module does, then
# point you at check.sh.
set -euo pipefail
[ -f /etc/workshop.env ] && source /etc/workshop.env
# module-9 cutover override wins (go_live.sh writes it)
if [ -f /etc/workshop-runtime.env ]; then set -a; source /etc/workshop-runtime.env; set +a; fi
cd "$(dirname "$0")/.."

MOD="${1:?usage: catchup.sh m4|m5|m6|m7|m8|m9|m10|m11}"
case "$MOD" in m4|m5|m6|m7|m8|m9|m10|m11) ;; *)
  echo "usage: catchup.sh m4|m5|m6|m7|m8|m9|m10|m11" >&2; exit 1;; esac

# "catchup.sh mN diff|fix" is a common slip — those verbs belong to check.sh;
# hand over instead of silently ignoring the word (observed live 2026-09-10)
case "${2:-}" in
  diff|fix) echo "catchup: '$2' is a check.sh verb — running scripts/check.sh $MOD $2 instead"; exec scripts/check.sh "$MOD" "$2" ;;
  "") ;;
  *) echo "catchup: unknown option '$2' (usage: catchup.sh mN; for diff/fix use scripts/check.sh mN diff|fix)" >&2; exit 1 ;;
esac

# --- m9: deploy the runtime and cut the floor over to it (no code branch) ----
if [ "$MOD" = "m9" ]; then
  PROJ="$HOME/environment/paymentagents"
  echo "catchup m9: bringing YOUR runtime up and cutting the floor over to it."
  if [ ! -f "$PROJ/agentcore/agentcore.json" ]; then
    echo "  no agentcore project at $PROJ — walk module 9 §1 (agentcore add agent) first;"
    echo "  the project skeleton is what 'agentcore deploy' needs. Nothing to fast-forward."
    exit 0
  fi
  APP="$HOME/environment/app/api"
  SOLVE_TAIL=quiet "$APP/scripts/solve.sh" m7         # module 7's belt must be in the bundle (idempotent)
  "$APP/scripts/wire_env.sh"                       # stamp exec role + envVars (idempotent)
  ( cd "$PROJ" && agentcore deploy -y )            # create/update runtime (idempotent)
  "$APP/scripts/go_live.sh"                        # cut over: /etc/workshop-runtime.env + api restart
  echo "catchup: done — runtime deployed and live. Now run scripts/invoke.sh 4144 (module 9's case step), then scripts/check.sh m9 if you want the proof."
  exit 0
fi

# --- m8: ReconInvestigator ships COMPLETE in this build (no file exercise) -----
if [ "$MOD" = "m8" ]; then
  echo "catchup m8: ReconInvestigator ships complete in this build — nothing to"
  echo "  fast-forward. scripts/check.sh m8 (in the page's Stuck? box) confirms it if you want the proof."
  exit 0
fi

# --- m10: the module IS the UI click; there is nothing to fast-forward ---------
if [ "$MOD" = "m10" ]; then
  echo "catchup m10: nothing to fast-forward — module 10 is the finale, and its"
  echo "  one write is a human review verdict you make in the UI."
  echo "  Open the Payment Operations UI → Review queue → approve or reject any"
  echo "  one card. That click flows through the API to the audit log (RLS keeps"
  echo "  it yours). Then confirm it landed:"
  echo "    scripts/check.sh m10"
  exit 0
fi

# --- m11 (optional): the module IS the queue drain — fast-forwarding runs it ---
if [ "$MOD" = "m11" ]; then
  echo "catchup m11: module 11 is optional, and its whole exercise is draining the"
  echo "  breaks queue through YOUR deployed runtime — so the fast-forward IS the drain."
  if [ ! -f /etc/workshop-runtime.env ]; then
    echo "  no cutover yet (/etc/workshop-runtime.env missing) — module 11 needs module 9's"
    echo "  runtime live first: scripts/catchup.sh m9, then re-run scripts/catchup.sh m11." >&2
    exit 1
  fi
  echo "  running scripts/run_queue.sh (2 workers racing FOR UPDATE SKIP LOCKED)…"
  scripts/run_queue.sh
  echo "catchup: done — queue drained. Validate: scripts/check.sh m11"
  exit 0
fi

BRANCH="${MOD}-done"

# The shipped workspace unzips from app.zip with no git repo, so the mN-done
# checkpoint branches exist only once they are cut. Without a branch,
# fast-forward by swapping in the module's finished files — the same swap
# as `check.sh mN fix`; your file is kept beside it as .py.bak.
if ! command -v git >/dev/null 2>&1 || ! git rev-parse --git-dir >/dev/null 2>&1 \
   || { ! git rev-parse --verify --quiet "refs/heads/$BRANCH" >/dev/null \
        && ! git rev-parse --verify --quiet "refs/remotes/origin/$BRANCH" >/dev/null; }; then
  echo "catchup: no $BRANCH checkpoint branch here — swapping in the finished files instead."
  exec scripts/solve.sh "$MOD"
fi

# stash any half-typed work so nothing is lost, then switch
if ! git diff --quiet || ! git diff --cached --quiet; then
  git stash push -m "catchup-$MOD $(date +%H:%M:%S)" >/dev/null
  echo "catchup: your in-progress edits are stashed (git stash list)"
fi
git switch "$BRANCH" 2>/dev/null || git switch -c "$BRANCH" "origin/$BRANCH"
echo "catchup: workspace → $BRANCH"

# modules 2-6 are dev-loop only; module 9's deploy is the attendee's own
# `agentcore deploy` (idempotent; rerun it in the project) — nothing here

echo "catchup: done — you're at $MOD complete. Validate: scripts/check.sh $MOD"
