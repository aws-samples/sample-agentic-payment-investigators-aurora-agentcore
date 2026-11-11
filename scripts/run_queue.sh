#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
# run_queue.sh — the queue runner: N DELIBERATELY DUMB worker loops.
# The work queue lives in the database; the dispatcher is one atomic
# UPDATE … FOR UPDATE SKIP LOCKED … RETURNING: the row LEAVES the OPEN pool
# in the same statement that picks it. A bare SELECT FOR UPDATE is NOT a
# claim — the lock dies with the transaction (psql exits, row still OPEN,
# next worker re-claims it; verified with 2 workers: every break
# invoked twice). Claim = state change, not lock. No orchestrator — in
# production this loop is EventBridge/SQS/pg_cron; the pattern is the same.
#
# Usage: scripts/run_queue.sh              # 2 workers, default τ (0.70)
#        WORKERS=1 scripts/run_queue.sh    # serial
#        TAU=0.78 scripts/run_queue.sh     # module 11 τ rep
set -euo pipefail
[ -f /etc/workshop.env ] && source /etc/workshop.env
# module-9 cutover override wins (go_live.sh writes it)
if [ -f /etc/workshop-runtime.env ]; then set -a; source /etc/workshop-runtime.env; set +a; fi
cd "$(dirname "$0")/.."

TAU="${TAU:-}"
WORKERS="${WORKERS:-2}"
[ -n "$TAU" ] && echo "per-request τ override = $TAU (rides each invoke payload)"
export TAU   # invoke.sh folds it into the payload; the adapter clamps [0,1]

# crash recovery FIRST: a break stuck INVESTIGATING with no verdict is a
# claim whose invoke died mid-flight (network blip, throttle). Claims are
# state changes, so a dead worker's claim survives it — requeue anything
# claimed but never scored, or it is unclaimable forever and check.sh m11
# stays red with nothing an attendee can do about it.
REQUEUED=$(scripts/psql.sh -tAc \
  "UPDATE breaks SET status = 'OPEN'
   WHERE status = 'INVESTIGATING' AND confidence IS NULL
   RETURNING break_id" | grep -cE '^[0-9]+' || true)
[ "$REQUEUED" -gt 0 ] && echo "requeued $REQUEUED break(s) stuck INVESTIGATING (a prior claim died before its verdict)"

OPEN=$(scripts/psql.sh -tAc "SELECT count(*) FROM breaks WHERE status = 'OPEN'" | tr -d '[:space:]')
echo "queue: $OPEN open break(s) · $WORKERS worker(s) racing FOR UPDATE SKIP LOCKED"

worker() {
  local W="$1"
  while true; do
    # the CLAIM: atomically move one break OPEN → INVESTIGATING and return
    # its id. SKIP LOCKED skips rows a racing worker is claiming this very
    # moment; the status change makes the claim durable after the txn ends.
    # The agent invocation happens OUTSIDE any transaction (module-5
    # discipline: never hold a txn across model calls).
    local BREAK_ID T0 VERDICT
    # grep: keep only the returned id — psql appends its command tag
    # ("UPDATE 1") even under -tA, and 2 + UPDATE 1 concatenates to junk
    BREAK_ID=$(scripts/psql.sh -tAc \
      "UPDATE breaks SET status = 'INVESTIGATING'
       WHERE break_id = (SELECT break_id FROM breaks WHERE status = 'OPEN'
                         ORDER BY opened_at FOR UPDATE SKIP LOCKED LIMIT 1)
       RETURNING break_id" | grep -oE '^[0-9]+' | head -1)
    [ -z "$BREAK_ID" ] && break
    T0=$SECONDS
    printf 'worker %s  break %-3s claimed\n' "$W" "$BREAK_ID"
    scripts/invoke.sh "$BREAK_ID" recon > /dev/null 2>&1 || true
    # the row is the verdict: report what the DATABASE says happened
    VERDICT=$(scripts/psql.sh -tAc \
      "SELECT b.status || '  confidence=' || coalesce(b.confidence::text, '-')
       FROM breaks b WHERE b.break_id = $BREAK_ID" | tr -d '\n' | sed 's/^ *//')
    printf 'worker %s  break %-3s %-32s %ss\n' "$W" "$BREAK_ID" "$VERDICT" "$((SECONDS-T0))"
  done
}

for W in $(seq 1 "$WORKERS"); do worker "$W" & done
wait

scripts/psql.sh -c \
  "SELECT status, count(*) FROM breaks GROUP BY status ORDER BY status"
