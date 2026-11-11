#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""load_seeds.py — the provision-time seeder.

    schema.sql  →  COPY seeds/output/*.tsv  →  indexes_postload.sql

COPY uses each TSV's header as the explicit column list, so generator column
order never has to match table order. Embedded variants (*_embedded.tsv) are
preferred automatically when present. Identity sequences are resynced after
load (authored rows carry explicit ids). Idempotent-ish: --reset drops and
reloads data tables.

Env: WORKSHOP_DB_HOST/PORT/NAME/USER/PASSWORD (same variables the agents use).
Local dev:  python3 seeds/load_seeds.py --schema --reset
Provision:  runs inside the seeding step against the cluster endpoint.
"""
import argparse
import gzip
import os
import pathlib
import sys
import time

import psycopg
from psycopg import sql as pgsql

HERE = pathlib.Path(__file__).parent
OUT = HERE / "output"
SQL = HERE.parent / "workshop" / "sql"

# load order respects FKs (transactions before disputes, ledger/lines before breaks)
TABLES = ["transactions", "disputes", "ledger_entries", "statement_lines",
          "breaks", "break_resolutions", "precedent_cases",
          "rule_clauses", "rule_mappings"]

# identity/PK column per table that needs its sequence resynced after explicit ids
SEQUENCED = {"transactions": "txn_id", "ledger_entries": "entry_id",
             "statement_lines": "line_id", "breaks": "break_id",
             "break_resolutions": "resolution_id", "precedent_cases": "precedent_id",
             "rule_clauses": "clause_id"}


def tsv_for(table: str) -> pathlib.Path:
    # embedded variant preferred; either may ship gzipped — the repo keeps
    # only .tsv.gz (the raw precedent TSV is 1.1 GB; git carries no big files)
    for name in (f"{table}_embedded.tsv", f"{table}_embedded.tsv.gz",
                 f"{table}.tsv", f"{table}.tsv.gz"):
        if (OUT / name).exists():
            return OUT / name
    raise FileNotFoundError(f"no seed TSV for {table} under {OUT}")


def connect():
    return psycopg.connect(
        host=os.environ.get("WORKSHOP_DB_HOST", "localhost"),
        port=int(os.environ.get("WORKSHOP_DB_PORT", "5432")),
        dbname=os.environ.get("WORKSHOP_DB_NAME", "workshop"),
        user=os.environ.get("WORKSHOP_DB_USER", "postgres"),
        password=os.environ.get("WORKSHOP_DB_PASSWORD", ""),
        autocommit=True)


# COPY options shared by both paths. csv format + tab delimiter + never-occurring
# quote char: our TSVs are written QUOTE_NONE, so a real quote char would corrupt
# fields that merely CONTAIN a double-quote. NULL '' maps empty fields to NULL.
COPY_OPTS = "FORMAT csv, DELIMITER E'\\t', QUOTE E'\\b', NULL ''"


def copy_table(conn, table: str) -> int:
    path = tsv_for(table)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        header = f.readline().rstrip("\n")
        cols = ", ".join(header.split("\t"))
        with conn.cursor() as cur:
            with cur.copy(f"COPY {table} ({cols}) FROM STDIN WITH ({COPY_OPTS})") as cp:
                while chunk := f.read(1 << 20):
                    cp.write(chunk)
            cur.execute(pgsql.Composed([pgsql.SQL("SELECT count(*) FROM "), pgsql.Identifier(table)]))
            return cur.fetchone()[0]


def s3_import_table(conn, table: str, bucket: str, prefix: str, region: str) -> int:
    """Provision-time path: Aurora PULLS the TSV from S3 server-side via the
    aws_s3 extension — the asset never transits the seeder host (kills the
    1.1 GB client-side transfer long-pole).

    Requires (CFN): aws_s3 extension + an IAM role associated to the cluster
    with the s3Import feature and read access on the assets bucket. The column
    list comes from the TSV's header via a ranged GET (~8 KB, not the file);
    HEADER true makes the server skip that row during import.

    Key resolution probes S3 (gzipped embedded variant first) — the provision
    host has NO local seeds/output, so tsv_for()'s local check is wrong here.
    Assets ship GZIPPED (.tsv.gz + Content-Encoding: gzip metadata): the WS
    assets quota is 1 GB/file, 3 GB total — the raw precedent TSV (1.1 GB)
    trips it, the gzip (387 MB) fits; table_import_from_s3 gunzips server-side
    when the object carries the Content-Encoding: gzip metadata (AWS docs).
    """
    import boto3
    import botocore.exceptions
    import zlib
    s3 = boto3.client("s3", region_name=region)
    key = None
    for candidate in (f"{table}_embedded.tsv.gz", f"{table}.tsv.gz",
                      f"{table}_embedded.tsv", f"{table}.tsv"):
        try:
            s3.head_object(Bucket=bucket, Key=f"{prefix}{candidate}")
            key = f"{prefix}{candidate}"
            break
        except botocore.exceptions.ClientError:
            continue
    if key is None:
        raise FileNotFoundError(f"no seed object for {table} under s3://{bucket}/{prefix}")
    head = s3.get_object(Bucket=bucket, Key=key, Range="bytes=0-65535")["Body"].read()
    if key.endswith(".gz"):
        # ranged GETs return raw compressed bytes; inflate just enough for the header
        head = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(head, 1 << 20)
    header = head.decode("utf-8").split("\n", 1)[0].rstrip("\r")
    cols = ", ".join(header.split("\t"))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT aws_s3.table_import_from_s3(%s, %s, %s, aws_commons.create_s3_uri(%s, %s, %s))",
            (table, cols, f"({COPY_OPTS}, HEADER true)", bucket, key, region))
        cur.execute(pgsql.Composed([pgsql.SQL("SELECT count(*) FROM "), pgsql.Identifier(table)]))
        return cur.fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", action="store_true", help="apply schema.sql first")
    ap.add_argument("--reset", action="store_true", help="TRUNCATE data tables first")
    ap.add_argument("--skip-indexes", action="store_true",
                    help="skip indexes_postload.sql (HNSW builds)")
    ap.add_argument("--from-s3", metavar="BUCKET/PREFIX", default=None,
                    help="server-side S3 import (aws_s3 extension) instead of "
                         "streaming local TSVs — the provision-time path")
    ap.add_argument("--s3-region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = ap.parse_args()

    bucket = prefix = None
    if args.from_s3:
        bucket, _, prefix = args.from_s3.partition("/")
        if prefix and not prefix.endswith("/"):
            prefix += "/"

    conn = connect()
    if args.schema:
        print("applying schema.sql …")
        conn.execute((SQL / "schema.sql").read_text())
    if args.from_s3:
        conn.execute("CREATE EXTENSION IF NOT EXISTS aws_s3 CASCADE")
    if args.reset:
        print("truncating data tables …")
        conn.execute(pgsql.Composed([pgsql.SQL("TRUNCATE "), pgsql.SQL(", ").join(map(pgsql.Identifier, reversed(TABLES))),
                                     pgsql.SQL(", action_log, review_queue, task_queue, matches CASCADE")]))

    for table in TABLES:
        t0 = time.time()
        if args.from_s3:
            n = s3_import_table(conn, table, bucket, prefix, args.s3_region)
            src = "s3"
        else:
            n = copy_table(conn, table)
            src = tsv_for(table).name
        print(f"  {table:24s} {n:>7,} rows  ({src}, {time.time()-t0:.1f}s)")

    for table, col in SEQUENCED.items():
        conn.execute(pgsql.Composed([pgsql.SQL("SELECT setval(pg_get_serial_sequence("), pgsql.Literal(table),
                                     pgsql.SQL(", "), pgsql.Literal(col), pgsql.SQL("), (SELECT coalesce(max("),
                                     pgsql.Identifier(col), pgsql.SQL("), 0) + 1 FROM "), pgsql.Identifier(table),
                                     pgsql.SQL("), false)")]))
    print("  sequences resynced")

    if not args.skip_indexes:
        print("building post-load indexes (HNSW ×4 + trgm; ~2 min at 60k) …")
        t0 = time.time()
        conn.execute((SQL / "indexes_postload.sql").read_text())
        print(f"  indexes built in {time.time()-t0:.0f}s")

    conn.close()
    print("seed load complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


def seed_review_queue(conn, analyst_sub: str) -> int:
    """PROVISION-TIME step (not generate-time): review_queue rows are
    RLS-scoped to the real analyst sub, which only exists once the Cognito
    user does. CFN calls this after the identity fixture.

    Seeds DECIDED history only (doors-open tour safety): sub-τ
    worked cases enter as APPROVED/REJECTED, the verdict derived from the
    dispute's terminal status, so the review tab shows the floor's past
    without a single clickable verdict. Zero PENDING at doors-open means an
    early clicker cannot consume module 5's payoff; the first live badge is
    the participant's own agent routing case 4133."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO review_queue (kind, case_ref, draft, confidence,
                                      analyst_id, status)
            SELECT 'dispute', case_id, draft_text, confidence, %s,
                   CASE WHEN status = 'DENIED' THEN 'REJECTED'
                        ELSE 'APPROVED' END
            FROM disputes
            WHERE confidence IS NOT NULL AND confidence < 0.7
              AND status IN ('FILED', 'ACCEPTED', 'DENIED')
            ON CONFLICT DO NOTHING""", (analyst_sub,))
        n = cur.rowcount
    conn.commit()
    return n
