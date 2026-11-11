# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""seeder.py — the CFN Custom::Seeder Lambda handler (provision-time seed).

Wraps the validated provisioning sequence as a custom resource:
  1. schema.sql (as master)
  2. server-side S3 import of every table (gzipped TSVs; aws_s3 extension) —
     WITH RETRY: an s3Import role association reports ACTIVE ~1-2 min before
     the engine can actually use the credentials
  3. indexes_postload.sql (drop-first — reseed-safe)
  4. workshop_app LOGIN password from AppRoleSecret + GRANT USAGE
     (PG15+ public-schema grant; register_vector fails without it)
  5. review_queue seeded with the REAL analyst sub (post-identity; the RLS
     rows can't exist before Cognito does)

Ships in provisioner.zip beside load_seeds.py, workshop/sql/, and vendored
psycopg+pgvector wheels (webui/Makefile zip-assets builds it).
"""
import json
import time
import urllib.request

import boto3


def _cfn_respond(event, context, status, data=None, reason=""):
    body = json.dumps({
        "Status": status,
        "Reason": (reason or "")[:800] + f" (log: {context.log_stream_name})",
        "PhysicalResourceId": event.get("PhysicalResourceId",
                                        f"seeder-{event['LogicalResourceId']}"),
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": data or {},
    }).encode()
    req = urllib.request.Request(event["ResponseURL"], data=body, method="PUT",
                                 headers={"Content-Type": ""})
    urllib.request.urlopen(req, timeout=30)  # nosec B310 - CFN-supplied https ResponseURL


def _secret(arn, region):
    raw = boto3.client("secretsmanager", region_name=region).get_secret_value(
        SecretId=arn)["SecretString"]
    try:
        return json.loads(raw).get("password", raw)
    except (json.JSONDecodeError, AttributeError):
        return raw


def handler(event, context):
    try:
        if event["RequestType"] == "Delete":
            return _cfn_respond(event, context, "SUCCESS")
        p = event["ResourceProperties"]
        region = p["Region"]

        already = False  # idempotency probe result (set after connect)

        import psycopg   # vendored in the zip
        from psycopg import sql as pgsql

        # aws_s3.table_import_from_s3's region arg must match the BUCKET's
        # region, not the stack's. Derive it.
        loc = boto3.client("s3", region_name=region).get_bucket_location(
            Bucket=p["SeedBucket"]).get("LocationConstraint")
        bucket_region = loc or "us-east-1"   # LocationConstraint is None for us-east-1
        master_pw = _secret(p["MasterSecretArn"], region)
        conn = psycopg.connect(host=p["DbHost"], port=5432, dbname="workshop",
                               user="postgres", password=master_pw,
                               autocommit=True, connect_timeout=30)

        import load_seeds   # beside this file in the zip

        # IDEMPOTENCY: CFN retries the Create on timeout, and
        # template updates send Update events. schema.sql is NOT rerun-safe
        # (CREATE TABLE without IF NOT EXISTS) and a re-seed would wipe live
        # workshop data. "Seeded" means the seed_meta marker row exists — it
        # is written LAST, after tables, indexes, grants, and review_queue
        # all succeeded. A populated table alone proves nothing: a run that
        # died mid-way leaves data but no marker, and must be rebuilt.
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name = 'seed_meta'")
            if cur.fetchone()[0] > 0:
                cur.execute("SELECT count(*) FROM seed_meta "
                            "WHERE step = 'complete'")
                already = cur.fetchone()[0] > 0
        if already:
            conn.close()
            print("already seeded (seed_meta marker present) — idempotent success")
            return _cfn_respond(event, context, "SUCCESS",
                                {"Tables": "already-seeded", "ReviewItems": 0})
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = 'public'")
            partial = cur.fetchone()[0] > 0
        if partial:
            # objects exist but no marker = a failed partial seed: start clean
            print("partial seed detected — dropping schema for clean retry")
            conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")

        conn.execute(open("schema.sql").read())
        conn.execute("CREATE EXTENSION IF NOT EXISTS aws_s3 CASCADE")

        # tables via server-side S3 import, with the propagation retry
        counts, last_err = {}, None
        for attempt in range(8):                     # ≤ ~8 min inside the 15-min cap
            try:
                for t in load_seeds.TABLES:
                    if t not in counts:
                        counts[t] = load_seeds.s3_import_table(
                            conn, t, p["SeedBucket"], p["SeedPrefix"], bucket_region)
                break
            except psycopg.errors.InternalError_ as e:
                last_err = e
                if "credentials" not in str(e):
                    raise
                print(f"s3Import credentials propagating (attempt {attempt+1}) — 60s")
                time.sleep(60)
        else:
            raise RuntimeError(f"s3Import never became usable: {last_err}")

        for table, col in load_seeds.SEQUENCED.items():
            conn.execute(pgsql.Composed([pgsql.SQL("SELECT setval(pg_get_serial_sequence("), pgsql.Literal(table),
                                         pgsql.SQL(", "), pgsql.Literal(col), pgsql.SQL("), (SELECT coalesce(max("),
                                         pgsql.Identifier(col), pgsql.SQL("), 0) + 1 FROM "), pgsql.Identifier(table),
                                         pgsql.SQL("), false)")]))
        conn.execute("SET maintenance_work_mem = '2GB'")
        conn.execute(open("indexes_postload.sql").read())

        # app role credential + the PG15+ schema grant.
        # ALTER ROLE can't take a bind parameter for the password; psycopg.sql
        # Literal quotes it safely (a literal %L in the query string trips
        # psycopg's own placeholder parser).
        app_pw = _secret(p["AppSecretArn"], region)
        conn.execute(pgsql.Composed([pgsql.SQL("ALTER ROLE workshop_app LOGIN PASSWORD "),
                                     pgsql.Literal(app_pw)]))
        conn.execute("GRANT USAGE ON SCHEMA public TO workshop_app")

        # review_queue with the REAL analyst sub (post-identity step)
        n_review = load_seeds.seed_review_queue(conn, p["AnalystSub"])

        # completion marker — the LAST write, after every step above: its
        # existence is the only thing the idempotency probe trusts
        conn.execute("CREATE TABLE seed_meta (step text PRIMARY KEY, "
                     "seeded_at timestamptz NOT NULL DEFAULT now())")
        conn.execute("INSERT INTO seed_meta (step) VALUES ('complete')")

        conn.close()
        print(f"seeded: {counts}; review_queue: {n_review}")
        _cfn_respond(event, context, "SUCCESS",
                     {"Tables": json.dumps(counts), "ReviewItems": n_review})
    except Exception as e:
        print(f"SEEDER FAILED: {type(e).__name__}: {e}")
        _cfn_respond(event, context, "FAILED", reason=f"{type(e).__name__}: {e}")
