# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""Database access — one connection per request scope, identity applied at open.

Every connection runs `SET app.analyst_id` from the IdentityContext before any
query, so RLS and enqueue_task() see the validated sub — identity is enforced in
the session, not asserted per-statement. SERIALIZABLE isolation is scoped per
statement PURPOSE: plain reads run READ COMMITTED; state-machine commits
opt in via `serializable_txn` with bounded 40001 retry (the storm's measured fix).
"""
import logging
import random
import sys
import time
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from .config import Settings
from .identity import IdentityContext

# stdout logging ONLY (a FileHandler at import = dead AgentCore runtime)
logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("botocore").setLevel(logging.WARNING)   # keep AWS SDK chatter out of transcripts
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("strands.telemetry").setLevel(logging.WARNING)  # MetricsClient chatter
log = logging.getLogger("investigator.db")

SERIALIZATION_FAILURE = "40001"


@contextmanager
def open_connection(settings: Settings, identity: IdentityContext):
    """Request-scoped connection with identity + vector adapter applied."""
    conn = psycopg.connect(
        host=settings.db_host, port=settings.db_port, dbname=settings.db_name,
        user=settings.db_user, password=settings.db_password,
        row_factory=dict_row, autocommit=False)
    try:
        register_vector(conn)
        with conn.cursor() as cur:
            # set_config (not SET): SET is a utility statement and cannot take
            # bind parameters. is_local=false → session-level, every txn sees it.
            cur.execute("SELECT set_config('app.analyst_id', %s, false)", (identity.sub,))
        conn.commit()
        yield conn
    finally:
        conn.close()


def db_factory(settings: Settings, identity: IdentityContext):
    """A bound open_connection — tools open ONE connection PER TOOL CALL.

    Strands' default ConcurrentToolExecutor runs sync tools in threads
    (parallel tool calls are real); a connection SHARED across tools would
    interleave transactions — thread B's commit() would commit thread A's
    half-done SERIALIZABLE txn. Per-call connections make that impossible by
    construction — no locks, no SequentialToolExecutor.
    """
    def factory():
        return open_connection(settings, identity)
    return factory


def query(conn, sql: str, params=()) -> list[dict]:
    """Read path: plain READ COMMITTED, commit to release the snapshot.
    Rolls back on error — a failed statement must not leave the connection
    in aborted-txn state that poisons every later call on it."""
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        raise


def serializable_txn(conn, fn, max_tries: int = 5):
    """Run fn(cursor) in a SERIALIZABLE transaction with bounded 40001 retry.

    The storm lesson (module 4) made executable: the 56% → 0% fix. SERIALIZABLE wraps
    ONLY the write-side state transition; retrieval stays outside the txn. On
    40001 the whole fn re-runs — fn must be side-effect-free outside the cursor.
    """
    for attempt in range(max_tries):
        try:
            with conn.cursor() as cur:
                # psycopg (autocommit=False) already opened the txn implicitly at
                # first execute — an explicit BEGIN here would be a warning no-op
                # and the isolation level would silently NOT apply. SET TRANSACTION
                # as the txn's first statement is the correct form.
                cur.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                result = fn(cur)
            conn.commit()
            return result
        except psycopg.errors.SerializationFailure:
            conn.rollback()
            if attempt == max_tries - 1:
                raise
            backoff = min(0.05 * (2 ** attempt), 0.5) * (1 + random.random())
            log.info("40001 serialization failure, retry %d/%d in %.0fms",
                     attempt + 1, max_tries, backoff * 1000)
            time.sleep(backoff)
        except Exception:
            conn.rollback()
            raise
