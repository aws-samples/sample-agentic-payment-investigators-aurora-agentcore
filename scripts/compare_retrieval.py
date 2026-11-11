#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""compare_retrieval — retrieval strategies side by side (modules 5 and 6).

One query text, every retrieval strategy over the SAME engine:
  NAIVE      pure cosine kNN, no WHERE (module 5's exercise — sounds right,
             argues wrong: watch the reason_code column)
  WHERE      categorical key only (B-tree) — no ranking
  TRGM       pg_trgm similarity (lexical, typo-tolerant)
  FTS        tsvector websearch match + ts_rank (exact-token prose)
  HYBRID     WHERE keys + pgvector rank (module 6 steps 1-2; prints the
             EXPLAIN ANALYZE so overfiltering is visible)
  RERANK     --rerank: HYBRID overfetch → Bedrock Rerank on (complaint,
             candidate REBUTTAL) pairs (module 6 step 3 — ranks on the field
             the draft consumes; VECTOR vs RERANKED side by side)
  RRF        rank-domain fusion of FTS+VECTOR (1/(60+rank) — fuse RANKS,
             never raw scores; ts_rank has no IDF and no BM25 on Aurora)

Usage (each beat flag scopes its own output — no spoilers):
  uv run scripts/compare_retrieval.py --case 4127 --naive    # module 5: naive kNN only
  uv run scripts/compare_retrieval.py --case 4136 --filter   # step 1: naive vs WHERE-filtered
  uv run scripts/compare_retrieval.py --case 4136 --scan     # step 2: the 0-rows overfilter beat
  uv run scripts/compare_retrieval.py "novel text" --rerank  # step 3: + the rerank stage
  uv run scripts/compare_retrieval.py --case 4136            # full: all four signals + taxonomy
Env: WORKSHOP_DB_* + Bedrock creds (one Titan call embeds the query; --rerank
adds one Rerank call ≈ $0.002).
"""
import argparse
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))   # deployed: investigator/ beside scripts/

import psycopg
from psycopg.rows import dict_row

CONNINFO = dict(
    host=os.environ.get("WORKSHOP_DB_HOST", "localhost"),
    port=int(os.environ.get("WORKSHOP_DB_PORT", "5432")),
    dbname=os.environ.get("WORKSHOP_DB_NAME", "workshop"),
    user=os.environ.get("WORKSHOP_DB_USER", "workshop_app"),
    password=os.environ.get("WORKSHOP_DB_PASSWORD", ""),
)


def resolve_password():
    """Same recipe as everything else: the secret ARN resolves at call time."""
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default=None, help="novel query text")
    ap.add_argument("--case", type=int, default=None,
                    help="use this dispute's complaint text (and reason code)")
    ap.add_argument("--naive", action="store_true",
                    help="module 5: ONLY naive kNN (later techniques stay unspoiled)")
    ap.add_argument("--filter", action="store_true",
                    help="module 6 step 1: naive vs WHERE-filtered, side by side")
    ap.add_argument("--scan", action="store_true",
                    help="module 6 step 2: the overfiltering beat (hybrid, iterative_scan on vs off)")
    ap.add_argument("--rerank", action="store_true",
                    help="module 6 step 3: add the Bedrock Rerank second stage")
    args = ap.parse_args()

    resolve_password()
    try:
        conn = psycopg.connect(**CONNINFO, row_factory=dict_row, autocommit=True,
                               connect_timeout=5)
    except psycopg.OperationalError as exc:
        print(f"compare_retrieval: database unreachable ({type(exc).__name__})",
              file=sys.stderr)
        return 75

    # resolve the query + the categorical key the hybrid arm filters on
    reason_code = "13.3"
    if args.case is not None:
        row = conn.execute(
            "SELECT reason_code, complaint_text FROM disputes WHERE case_id = %s",
            (args.case,)).fetchone()
        if row is None:
            print(f"no dispute {args.case}", file=sys.stderr)
            return 1
        q, reason_code = row["complaint_text"], row["reason_code"]
    else:
        q = args.query or "Received an item that matches none of the photos displayed on the product page."
    print(f"query ({'case '+str(args.case) if args.case else 'text'}, "
          f"key reason_code={reason_code}): {q!r}\n")

    from investigator.config import load_settings
    from investigator.retrieval import embed_query, rerank_precedents
    settings = load_settings()
    emb = embed_query(settings, q)   # ONE Titan call; corpus vectors are pre-computed
    qv = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
    params = {"q": q, "qv": qv, "rc": reason_code}

    strategies = {
        "NAIVE kNN (no WHERE)": """
            SELECT precedent_id, reason_code, outcome,
                   left(complaint_text, 52) AS hit,
                   (1 - (complaint_embedding <=> %(qv)s::vector))::float AS score
            FROM precedent_cases
            ORDER BY complaint_embedding <=> %(qv)s::vector, precedent_id
            LIMIT 5""",
        "WHERE (key only)": """
            SELECT precedent_id, reason_code, outcome,
                   left(complaint_text, 52) AS hit, NULL::float AS score
            FROM precedent_cases
            WHERE reason_code = %(rc)s AND outcome = 'won'
            LIMIT 5""",
        "TRGM (lexical)": """
            SELECT precedent_id, reason_code, outcome,
                   left(complaint_text, 52) AS hit,
                   similarity(complaint_text, %(q)s)::float AS score
            FROM precedent_cases
            WHERE reason_code = %(rc)s AND outcome = 'won'
            ORDER BY complaint_text <-> %(q)s
            LIMIT 5""",
        "FTS (exact tokens)": """
            SELECT precedent_id, reason_code, outcome,
                   left(complaint_text, 52) AS hit,
                   ts_rank(to_tsvector('english', complaint_text),
                           websearch_to_tsquery('english', %(q)s))::float AS score
            FROM precedent_cases
            WHERE reason_code = %(rc)s AND outcome = 'won'
              AND to_tsvector('english', complaint_text)
                  @@ websearch_to_tsquery('english', %(q)s)
            ORDER BY score DESC
            LIMIT 5""",
        "HYBRID (WHERE + cosine + iterative_scan)": """
            SELECT precedent_id, reason_code, outcome,
                   left(complaint_text, 52) AS hit,
                   (1 - (complaint_embedding <=> %(qv)s::vector))::float AS score
            FROM precedent_cases
            WHERE reason_code = %(rc)s AND outcome = 'won'
            ORDER BY complaint_embedding <=> %(qv)s::vector, precedent_id
            LIMIT 5""",
    }

    # scope the printed strategies to the beat the attendee is on (no spoilers).
    # default (no beat flag) = full taxonomy, as before.
    beat = args.naive or args.filter or args.scan or args.rerank
    if args.naive:                 # module 5: similarity only
        keep = ("NAIVE",)
    elif args.filter:              # module 6 step 1: naive (wrong) vs WHERE (right code)
        keep = ("NAIVE", "WHERE")
    elif args.scan:                # module 6 step 2: rendered below (off THEN on), skip the loop
        keep = ()
    elif args.rerank:              # module 6 step 3: hybrid feeds the reranker
        keep = ("HYBRID",)
    else:
        keep = None
    strategies_all = dict(strategies)   # unscoped, for beats that render their own arms
    if keep is not None:
        strategies = {k: v for k, v in strategies.items()
                      if any(k.startswith(p) for p in keep)}

    conn.execute("SELECT set_config('hnsw.iterative_scan', %s, false)", (settings.hnsw_iterative_scan,))
    for name, sql in strategies.items():
        t0 = time.perf_counter()
        rows = conn.execute(sql, params).fetchall()
        ms = (time.perf_counter() - t0) * 1000
        print(f"── {name}  ({ms:.1f} ms, {len(rows)} rows)")
        for r in rows:
            score = f"{r['score']:.3f}" if r.get("score") is not None else "  —  "
            print(f"   {score}  {r['reason_code']}  {r['outcome']:4s}  {r['hit']}")
        if not rows:
            print("   (no hits — this strategy has nothing for this query class)")
        print()

    if args.naive or args.filter:
        conn.close()
        return 0

    HYBRID_SQL = strategies_all["HYBRID (WHERE + cosine + iterative_scan)"]

    def print_hybrid(label, rows, plan_lines=None):
        print(f"── {label}  ({len(rows)} rows)")
        for line in (plan_lines or []):
            print(f"   {line}")
        for r in rows:
            print(f"   {r['score']:.3f}  {r['reason_code']}  {r['outcome']:4s}  {r['hit']}")
        print()

    # the overfiltering beat. --scan tells it problem-first (OFF → the fix → ON);
    # default run tells it fix-first (the loop already printed ON) then the OFF
    # counterfactual. --rerank skips it (that beat assumes the fix is in).
    if args.scan or not args.rerank:
        # pin the STOCK ef_search for BOTH legs: the floor's sessions default
        # to 200 (PGOPTIONS — the recall floor; see the appendix), which
        # would feed the filter enough candidates to hide the trap. At 40,
        # iterative_scan is the single variable the comparison isolates.
        conn.execute("SET hnsw.ef_search = 40")
        conn.execute("SET hnsw.iterative_scan = off")
        plan = [l["QUERY PLAN"].strip() for l in
                conn.execute(psycopg.sql.Composed([psycopg.sql.SQL("EXPLAIN ANALYZE "), psycopg.sql.SQL(HYBRID_SQL)]), params).fetchall()
                if "Rows Removed by Filter" in l["QUERY PLAN"] or "Index Scan" in l["QUERY PLAN"]]
        rows_off = conn.execute(HYBRID_SQL, params).fetchall()
        print_hybrid("HYBRID with iterative_scan=off  — the overfiltering trap", rows_off, plan)
        conn.execute("SELECT set_config('hnsw.iterative_scan', %s, false)", (settings.hnsw_iterative_scan,))
        if args.scan:   # show the fix working, then end the beat
            rows_on = conn.execute(HYBRID_SQL, params).fetchall()
            print_hybrid("HYBRID with iterative_scan=on  — the fix", rows_on)
            conn.execute("RESET hnsw.ef_search")   # back to the floor's 200
            conn.close()
            return 0
        conn.execute("RESET hnsw.ef_search")       # back to the floor's 200

    if args.rerank:
        cands = conn.execute("""
            SELECT precedent_id, analyst_id, complaint_text, rebuttal_text,
                   (1 - (complaint_embedding <=> %(qv)s::vector))::float AS cosine_similarity
            FROM precedent_cases
            WHERE reason_code = %(rc)s AND outcome = 'won'
            ORDER BY complaint_embedding <=> %(qv)s::vector, precedent_id
            LIMIT %(k)s""", {**params, "k": settings.rerank_overfetch}).fetchall()
        # the seeded hard negative: near-identical COMPLAINT, wrong-fact REBUTTAL
        hn = conn.execute("SELECT precedent_id FROM precedent_cases "
                          "WHERE tag = 'hard_negative'").fetchone()
        hn_id = hn["precedent_id"] if hn else None
        t0 = time.perf_counter()
        ranked = rerank_precedents(settings, q, cands)
        ms = (time.perf_counter() - t0) * 1000
        print(f"── RERANK stage 2 ({ms:.0f} ms, {len(cands)} candidates → top {len(ranked)})")
        print("   VECTOR top-5 (complaint↔complaint)     RERANKED top-5 (complaint↔rebuttal)")
        rr_top = {r["precedent_id"] for r in ranked[:5]}
        for i in range(max(5, len(ranked))):
            if i < min(5, len(cands)):
                cid = cands[i]["precedent_id"]
                tag = "  ← hard negative" if cid == hn_id else ""
                left = f"{cands[i]['cosine_similarity']:.3f}  #{cid}{tag}"
            else:
                left = ""
            right = (f"{ranked[i]['rerank_score']:.3f}  #{ranked[i]['precedent_id']}"
                     if i < len(ranked) else "")
            print(f"   {left:38s} {right}")
        # spotlight the ONE explainable move, and SHOW the case so the demotion
        # is visible, not asserted: cosine judged its complaint, rerank its rebuttal.
        vec_rank = next((i + 1 for i, c in enumerate(cands[:5])
                         if c["precedent_id"] == hn_id), None)
        hn_row = next((c for c in cands if c["precedent_id"] == hn_id), None)
        if hn_id and vec_rank and hn_row and hn_id not in rr_top:
            def wrap(s, w=66):
                return s if len(s) <= w else s[:w - 1] + "…"
            print(f"\n   why #{hn_id} moved — cosine rank {vec_rank}, dropped from reranked top-5:")
            print(f"     its COMPLAINT: {wrap(hn_row['complaint_text'])}")
            print(f"                    (nearly your query — so cosine ranked it high)")
            print(f"     its REBUTTAL:  {wrap(hn_row['rebuttal_text'])}")
            print(f"                    (delivery evidence — answers a DIDN'T-ARRIVE case,")
            print(f"                     not a NOT-AS-DESCRIBED one, so the reranker dropped it)")
        print()

    if not beat:   # the four-signal taxonomy only on a full run (appendix beat)
        print("No global winner: exact tokens (try 'INV-77812') → FTS; paraphrase →")
        print("HYBRID(+RERANK); keys never needed ranking. Fuse RANKS (1/(60+rank)),")
        print("never raw scores — ts_rank has no IDF; no BM25 extension on Aurora.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
