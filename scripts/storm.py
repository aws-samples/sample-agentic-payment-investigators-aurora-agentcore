#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""storm — the SERIALIZABLE collision demo.

Runs N concurrent FLYWHEEL clients. Each transaction, in ONE SERIALIZABLE txn:
  1. the hybrid precedent HNSW scan (the query participants just wrote), and
  2. a write-back INSERT of a "resolved case" into precedent_cases
     (pre-computed vector from the seeded corpus — never a Bedrock call).
That is the institutional-memory flywheel storming itself. The vector scan takes
a RELATION-level SIReadLock (pgvector ampredlocks=false), so concurrent flywheel
txns conflict wholesale: measured 56% ERROR 40001 at 4 clients/30 s on APG 18.3.

--fixed runs the corrected shape participants build: retrieval OUTSIDE the txn,
SERIALIZABLE only around the state-machine write (measured 0%).

Usage:
  python3 scripts/storm.py               # naive flywheel (expect ~50%+ 40001)
  python3 scripts/storm.py --fixed       # corrected shape (expect ~0%)
  python3 scripts/storm.py --locks       # one-query pg_locks beat (run while a
                                            # storm is active in another terminal)
Env: WORKSHOP_DB_* (same as every harness stage).
"""
import argparse
import concurrent.futures as cf
import threading
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))   # deployed: investigator/ beside scripts/

import psycopg
from psycopg.rows import dict_row

DURATION_S = int(os.environ.get("STORM_SECONDS", "30"))
CLIENTS = int(os.environ.get("STORM_CLIENTS", "4"))

CONNINFO = dict(
    host=os.environ.get("WORKSHOP_DB_HOST", "localhost"),
    port=int(os.environ.get("WORKSHOP_DB_PORT", "5432")),
    dbname=os.environ.get("WORKSHOP_DB_NAME", "workshop"),
    user=os.environ.get("WORKSHOP_DB_USER", "workshop_app"),
    password=os.environ.get("WORKSHOP_DB_PASSWORD", ""),
)


def resolve_password():
    """Same recipe as everything else: the secret ARN resolves at call time
    (the password never rides an env var or disk on the workshop host)."""
    if CONNINFO["password"] or not os.environ.get("WORKSHOP_DB_SECRET_ARN"):
        return
    import json

    import boto3
    arn = os.environ["WORKSHOP_DB_SECRET_ARN"]
    region = arn.split(":")[3] if arn.startswith("arn:") else None
    raw = boto3.client("secretsmanager", region_name=region).get_secret_value(
        SecretId=arn)["SecretString"]
    try:
        CONNINFO["password"] = json.loads(raw).get("password", raw)
    except (ValueError, AttributeError):
        CONNINFO["password"] = raw

# the M2 hybrid query — same shape participants wrote (query vector: a seeded
# row's embedding, cast through text so no client-side vector adapter needed)
HYBRID_SCAN = """
    SELECT precedent_id FROM precedent_cases
    WHERE reason_code = '13.3' AND outcome = 'won'
    ORDER BY complaint_embedding <=> (
        SELECT complaint_embedding FROM precedent_cases WHERE tag = 'paraphrase_a')
    LIMIT 5"""

# write-back: a "just-resolved case" enters the corpus with LITERAL pre-computed
# values (fetched ONCE per client, outside any storm txn). This mirrors the real
# agent write-back (pre-computed vectors shipped with assets) — and it matters
# for the demo: an INSERT..SELECT would READ the stormed table inside the fixed
# arm's SERIALIZABLE txn and collide anyway (measured 68.8% before this fix —
# the "fix" must leave NO read of the hot table inside the write txn).
WRITE_BACK = """
    INSERT INTO precedent_cases
        (network, reason_code, outcome, analyst_id, complaint_text, rebuttal_text,
         tag, complaint_embedding, rebuttal_embedding)
    VALUES (%(network)s, %(reason_code)s, %(outcome)s, %(analyst)s,
            'storm write-back ' || clock_timestamp(), %(rebuttal)s,
            'storm', %(cemb)s::vector, %(remb)s::vector)"""

TEMPLATE_ROW = """
    SELECT network, reason_code, outcome, rebuttal_text,
           complaint_embedding::text AS cemb, rebuttal_embedding::text AS remb
    FROM precedent_cases WHERE tag = 'paraphrase_a'"""


LIVE = {"ok": 0, "fail": 0}
LIVE_LOCK = threading.Lock()


def client(n: int, fixed: bool, deadline: float) -> tuple[int, int]:
    """One storm client. Returns (committed, serialization_failures)."""
    ok = fail = 0
    with psycopg.connect(**CONNINFO, row_factory=dict_row, autocommit=False) as conn:
        # the write-back payload: fetched ONCE, plain read, before any storming
        with conn.cursor() as cur:
            cur.execute(TEMPLATE_ROW)
            t = cur.fetchone()
        conn.commit()
        payload = {"network": t["network"], "reason_code": t["reason_code"],
                   "outcome": t["outcome"], "rebuttal": t["rebuttal_text"],
                   "cemb": t["cemb"], "remb": t["remb"],
                   "analyst": f"storm_client_{n}"}
        # demo pin: at the floor's ef_search=200 the planner prices the HNSW
        # walk above brute force for this scan and quietly seq-scans — no
        # index SIReadLocks, and the ampredlocks=false beat vanishes from the
        # pg_locks sample. Pin the stock 40 so the scan takes the index path.
        with conn.cursor() as cur:
            cur.execute("SET hnsw.ef_search = 40")
        conn.commit()
        while time.time() < deadline:
            try:
                with conn.cursor() as cur:
                    if fixed:
                        # THE FIX: retrieval outside the write txn (plain read),
                        # SERIALIZABLE only around the write — which touches the
                        # hot table ONLY via the INSERT (no reads → no SIReadLock)
                        cur.execute(HYBRID_SCAN)
                        cur.fetchall()
                        conn.commit()
                        cur.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                        cur.execute(WRITE_BACK, payload)
                    else:
                        # NAIVE: scan + write-back in one SERIALIZABLE txn
                        cur.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                        cur.execute(HYBRID_SCAN)
                        cur.fetchall()
                        cur.execute(WRITE_BACK, payload)
                conn.commit()
                ok += 1
                with LIVE_LOCK:
                    LIVE["ok"] += 1
            except psycopg.errors.SerializationFailure:
                conn.rollback()
                fail += 1
                with LIVE_LOCK:
                    LIVE["fail"] += 1
            except psycopg.Error:
                conn.rollback()
                raise
    return ok, fail


def cleanup():
    """Truly delete the demo rows, via the one scoped delete the schema
    allows (purge_storm_rows, SECURITY DEFINER): retagging left the demo
    vectors resident in the HNSW graph, and 12k+ dead-weight neighbors
    starve iterative scans at default limits. On a pre-function floor,
    degrade to the old retag so the storm never hard-fails on cleanup."""
    with psycopg.connect(**CONNINFO, autocommit=True) as conn:
        try:
            return conn.execute("SELECT purge_storm_rows()").fetchone()[0]
        except psycopg.errors.UndefinedFunction:
            return conn.execute("""
                UPDATE precedent_cases
                SET tag = 'storm_done', reason_code = 'XX.storm', outcome = 'lost'
                WHERE tag = 'storm'""").rowcount


LOCK_QUERY = """
    SELECT locktype, relation::regclass AS relation, page, count(*) AS n
    FROM pg_locks WHERE mode = 'SIReadLock'
    GROUP BY locktype, relation, page
    ORDER BY relation::text, locktype"""


def sample_locks() -> list:
    """One pg_locks snapshot on its own connection (safe mid-storm)."""
    with psycopg.connect(**CONNINFO, row_factory=dict_row, autocommit=True) as conn:
        return conn.execute(LOCK_QUERY).fetchall()


def print_locks(rows, header=True):
    if header:
        print(f"  {'locktype':10s} {'relation':28s} {'page':>6s} {'count':>6s}")
    for r in rows:
        print(f"  {r['locktype']:10s} {str(r['relation']):28s} "
              f"{str(r['page'] if r['page'] is not None else '-'):>6s} {r['n']:>6d}")


def show_locks():
    """The one-query beat: relation-level SIReadLock on the HNSW-scanned table
    vs page-level on B-trees — run while a storm is active."""
    rows = sample_locks()
    if not rows:
        print("no SIReadLocks held — run this during a storm")
        return
    print_locks(rows)



def main() -> int:
    resolve_password()
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixed", action="store_true",
                    help="run the corrected shape (retrieval outside the txn)")
    ap.add_argument("--locks", action="store_true",
                    help="show the SIReadLock granularity (run during a storm)")
    args = ap.parse_args()

    try:
        with psycopg.connect(**CONNINFO, connect_timeout=5):
            pass
    except psycopg.OperationalError as exc:
        print(f"storm: database unreachable ({exc})", file=sys.stderr)
        return 75  # EX_TEMPFAIL — same skip convention as the harness stages

    if args.locks:
        show_locks()
        return 0

    shape = "FIXED (retrieval outside txn)" if args.fixed else "NAIVE (flywheel in one txn)"
    print(f"storm: {CLIENTS} clients × {DURATION_S}s — {shape}")
    LIVE["ok"] = LIVE["fail"] = 0
    deadline = time.time() + DURATION_S
    with cf.ThreadPoolExecutor(max_workers=CLIENTS) as pool:
        futures = [pool.submit(client, n, args.fixed, deadline)
                   for n in range(CLIENTS)]
        locks_midrun = None
        while any(not f.done() for f in futures):
            time.sleep(1)
            with LIVE_LOCK:
                ok_now, fail_now = LIVE["ok"], LIVE["fail"]
            total_now = ok_now + fail_now
            rate_now = (100.0 * fail_now / total_now) if total_now else 0.0
            left = max(0, int(deadline - time.time()))
            print(f"\r  {left:>2d}s left   committed: {ok_now:>5d}   "
                  f"aborted 40001: {fail_now:>5d}   rate: {rate_now:5.1f}%",
                  end="", flush=True)
            if locks_midrun is None and left <= DURATION_S // 2:
                try:
                    locks_midrun = sample_locks()   # snapshot at peak traffic
                except psycopg.Error:
                    locks_midrun = []
        print("\r" + " " * 78 + "\r", end="")
        results = [f.result() for f in futures]
    ok = sum(r[0] for r in results)
    fail = sum(r[1] for r in results)
    total = ok + fail
    rate = (100.0 * fail / total) if total else 0.0
    print(f"\n  committed:      {ok:>6d}")
    print(f"  aborted 40001:  {fail:>6d}")
    print(f"  failure rate:   {rate:.1f}%")
    if locks_midrun:
        print(f"\n  SIReadLocks held mid-run (pg_locks, sampled at ~{DURATION_S // 2}s):")
        print_locks(locks_midrun)
    elif locks_midrun is not None:
        print(f"\n  SIReadLocks held mid-run: none")
    print(f"\n  cleanup: purging {ok} storm write-backs …", end="", flush=True)
    n = cleanup()
    print(f" {n} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
