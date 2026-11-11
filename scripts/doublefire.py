#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""doublefire — the exactly-once demo.

Fires file_representment TWICE as two SEPARATE invocations (fresh identity/
trace each — a real replay, not a same-object rerun) against a case in DRAFTED
status, then proves exactly-once from STRUCTURED state:
  - exactly ONE action_log row for the (case, FILED) idempotency key
  - second call reported idempotent_noop=True with the ACTUAL current status
  - the case row is FILED (not double-transitioned)

By default the demo AUTO-PICKS a queue-filler case and never touches the
story cases — #4127 is filed by YOUR AGENT in this module's belt beat, and
its ledger row stays the agent's alone. Pass --case only to point the demo
at a specific unfiled dispute. Runs the LOCAL write path (the same writes.py
the deployed agent uses); re-runs pick the next unfiled filler.

Env: WORKSHOP_DB_* (workshop_app works — the write path is RLS-safe).
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))   # deployed: investigator/ beside scripts/

from investigator import IdentityContext, load_settings, new_session_id, new_trace_id
from investigator.db import open_connection, query
from investigator.writes import file_representment, idempotency_key, set_dispute_status

FAILURES = []
def check(name, ok, detail=""):
    """Silent when true — asserts print ONLY on failure (the field report
    above already shows the evidence; green noise adds nothing)."""
    if not ok:
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))
        FAILURES.append(name)


def fire(settings, case_id: int, label: str) -> dict:
    """One SEPARATE invocation: its own identity, session, trace, connection —
    the shape of a genuinely re-fired agent run."""
    ident = IdentityContext.local_dev("analyst_martha", new_session_id(), new_trace_id())
    with open_connection(settings, ident) as conn:
        result = file_representment(conn, ident, case_id,
                                    f"double-fire demo draft ({label})")
        noop = result["idempotent_noop"]
        print(f"  attempt {label}  trace={ident.trace_id[:24]}…")
        print(f"    INSERT action_log  ON CONFLICT DO NOTHING  "
              f"→ {'0 rows (key exists)' if noop else '1 row  action_id=' + str(result['action_id'])}")
        print(f"    UPDATE disputes    WHERE allowed transition "
              f"→ {'skipped' if noop else '1 row  status=FILED'}")
        print(f"    result: status={result['status']}  noop={noop}")
        return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", type=int, default=None,
                    help="target a specific unfiled dispute (default: auto-pick a filler)")
    args = ap.parse_args()
    settings = load_settings()
    case_id = args.case

    # arrange: the case must be DRAFTED for the first FILED transition to be legal
    ident = IdentityContext.local_dev("analyst_martha", new_session_id(), new_trace_id())
    try:
        probe = open_connection(settings, ident)
        probe.__enter__(); probe.__exit__(None, None, None)
    except Exception as exc:
        print(f"doublefire: database unreachable ({type(exc).__name__}).\n"
              f"  set WORKSHOP_DB_* (source /etc/workshop.env) and re-run", file=sys.stderr)
        return 75  # EX_TEMPFAIL — harness skip convention
    with open_connection(settings, ident) as conn:
        # default: auto-pick a queue filler. The story cases are UNTOUCHABLE:
        # 4127 is the hero — YOUR AGENT files it (this module's belt beat) and
        # its ledger row stays the agent's; 4133 must route to review; 4136 is
        # m4's overfilter demo; 4144 is production's first case (m9). A prior
        # run burns its filler's key, so re-runs pick the next unfiled one.
        if case_id is None:
            fresh = query(conn, """
                SELECT d.case_id FROM disputes d
                WHERE d.status IN ('OPEN','INVESTIGATING','DRAFTED')
                  AND d.case_id NOT IN (4127, 4133, 4136, 4144)
                  AND NOT EXISTS (SELECT 1 FROM action_log
                                  WHERE idempotency_key = 'dispute:'||d.case_id||':FILED')
                ORDER BY d.case_id LIMIT 1""")
            if not fresh:
                print("no unfiled filler case available — reseed")
                return 75
            case_id = fresh[0]["case_id"]
            print(f"  demo case: #{case_id} (auto-picked filler — story cases excluded)")
        else:
            burned = query(conn, "SELECT 1 FROM action_log WHERE idempotency_key = %s",
                           (idempotency_key("dispute", case_id, "FILED"),))
            if burned:
                print(f"case {case_id} already filed — the demo needs an unfiled case; "
                      f"run without --case to auto-pick a filler")
                return 75
        cur_status = query(conn, "SELECT status FROM disputes WHERE case_id=%s",
                           (case_id,))
        if not cur_status:
            print(f"no dispute {case_id}"); return 1
        status = cur_status[0]["status"]
        if status == "OPEN":
            set_dispute_status(conn, ident, case_id, "INVESTIGATING")
            set_dispute_status(conn, ident, case_id, "DRAFTED", draft="demo draft")
        elif status == "INVESTIGATING":
            set_dispute_status(conn, ident, case_id, "DRAFTED", draft="demo draft")
        elif status != "DRAFTED":
            print(f"  note: case {case_id} is {status} — the double-fire still "
                  f"demonstrates the noop, but the first fire won't transition")

    print(f"doublefire: file_representment({case_id}) fired twice, independently")
    r1 = fire(settings, case_id, "A")
    r2 = fire(settings, case_id, "B")

    key = idempotency_key("dispute", case_id, "FILED")
    ident = IdentityContext.local_dev("analyst_martha", new_session_id(), new_trace_id())
    with open_connection(settings, ident) as conn:
        logs = query(conn, "SELECT action_id, trace_id FROM action_log "
                           "WHERE idempotency_key = %s", (key,))
        row = query(conn, "SELECT status FROM disputes WHERE case_id=%s", (case_id,))

    print()
    check("exactly ONE action_log row for the idempotency key", len(logs) == 1,
          f"rows={len(logs)} key={key}")
    check("first fire performed the write", r1["idempotent_noop"] is False)
    check("second fire no-opped", r2["idempotent_noop"] is True)
    check("noop reported the ACTUAL current status", r2["status"] == row[0]["status"],
          f"noop said {r2['status']}, DB says {row[0]['status']}")
    check("case is FILED exactly once", row[0]["status"] == "FILED")

    if FAILURES:
        print(f"\nDOUBLEFIRE: FAILED ({len(FAILURES)})"); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
