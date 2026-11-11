# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""Writes — idempotent, SERIALIZABLE state-machine commits.

The two-layer defense:
  1. deterministic idempotency key — derived in TOOL CODE from (case_id,
     target_status), NEVER model-generated (an LLM-minted key differs across
     invocations and ON CONFLICT would dedup nothing);
  2. status-transition guard — the state machine as data (dispute_transitions).

SERIALIZABLE wraps ONLY these commits; retrieval runs outside (the storm's
measured fix: flywheel storm 56% 40001 → 0%). Retry lives in db.serializable_txn.
"""
import json
import logging

from .db import serializable_txn
from .identity import IdentityContext

log = logging.getLogger("investigator.writes")


def idempotency_key(kind: str, case_ref: int, target_status: str) -> str:
    """Deterministic: same logical action → same key, across any number of
    agent invocations. THE mechanism (schema: action_log UNIQUE)."""
    return f"{kind}:{case_ref}:{target_status}"


def file_representment(conn, identity: IdentityContext, case_id: int,
                       draft: str) -> dict:
    """DRAFTED→FILED commit: evidence packet + action_log + status, exactly once.

    Returns a structured payload either way; a duplicate fire reports
    idempotent_noop=True (the doublefire assertion target).
    """
    key = idempotency_key("dispute", case_id, "FILED")

    def txn(cur):
        # layer 1: the idempotency ledger — a replayed action no-ops here
        cur.execute("""
            INSERT INTO action_log (idempotency_key, trace_id, session_id, actor_sub,
                                    action, row_ref)
            VALUES (%s, %s, %s, %s, 'file_representment', %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING action_id""",
            (key, identity.trace_id, identity.session_id, identity.sub,
             json.dumps({"table": "disputes", "case_id": case_id})))
        row = cur.fetchone()
        if row is None:
            # noop branch reports the ACTUAL current status, never the target —
            # the case may have advanced past FILED since the first fire
            # (review finding: "idempotent-noop return payload lies")
            cur.execute("SELECT status FROM disputes WHERE case_id = %s", (case_id,))
            current = cur.fetchone()
            return {"case_id": case_id,
                    "status": current["status"] if current else f"missing:{case_id}",
                    "idempotent_noop": True, "action_id": None,
                    "trace_id": identity.trace_id}

        # layer 2: the transition guard — the state machine as data
        cur.execute("""
            UPDATE disputes d
            SET status = 'FILED', draft_text = %s, trace_id = %s, updated_at = now()
            WHERE d.case_id = %s
              AND EXISTS (SELECT 1 FROM dispute_transitions t
                          WHERE t.from_status = d.status AND t.to_status = 'FILED')
            RETURNING d.case_id, d.status""",
            (draft, identity.trace_id, case_id))
        updated = cur.fetchone()
        if updated is None:
            raise ValueError(
                f"file_representment: dispute {case_id} is not in a status that "
                f"can transition to FILED (guard: dispute_transitions)")
        return {"case_id": case_id, "status": "FILED",
                "idempotent_noop": False, "action_id": row["action_id"],
                "trace_id": identity.trace_id}

    return serializable_txn(conn, txn)


def set_dispute_status(conn, identity: IdentityContext, case_id: int,
                       target_status: str, draft: str | None = None,
                       confidence: float | None = None) -> dict:
    """Generic guarded transition (OPEN→INVESTIGATING, DRAFTED→REVIEW, …) —
    same two layers, any legal edge. Used by the agent's intake/draft steps."""
    key = idempotency_key("dispute", case_id, target_status)

    def txn(cur):
        cur.execute("""
            INSERT INTO action_log (idempotency_key, trace_id, session_id, actor_sub,
                                    action, row_ref)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING action_id""",
            (key, identity.trace_id, identity.session_id, identity.sub,
             f"set_status:{target_status}",
             json.dumps({"table": "disputes", "case_id": case_id})))
        if cur.fetchone() is None:
            cur.execute("SELECT status FROM disputes WHERE case_id = %s", (case_id,))
            current = cur.fetchone()
            return {"case_id": case_id,
                    "status": current["status"] if current else f"missing:{case_id}",
                    "idempotent_noop": True, "trace_id": identity.trace_id}
        cur.execute("""
            UPDATE disputes d
            SET status = %s,
                draft_text = COALESCE(%s, d.draft_text),
                confidence = COALESCE(%s, d.confidence),
                trace_id = %s, updated_at = now()
            WHERE d.case_id = %s
              AND EXISTS (SELECT 1 FROM dispute_transitions t
                          WHERE t.from_status = d.status AND t.to_status = %s)
            RETURNING d.status""",
            (target_status, draft, confidence, identity.trace_id, case_id, target_status))
        if cur.fetchone() is None:
            raise ValueError(f"illegal transition for dispute {case_id} → {target_status}")
        return {"case_id": case_id, "status": target_status, "idempotent_noop": False,
                "trace_id": identity.trace_id}

    return serializable_txn(conn, txn)


def post_break_resolution(conn, identity: IdentityContext, break_id: int,
                          proposed_action: str, confidence: float,
                          target_status: str) -> dict:
    """The file_* write path reused — rename + one column map.
    target_status ∈ {'PROPOSED','REVIEW','RESOLVED'}. REVIEW is a one-way
    door for agents: a break already routed to a human is not writable
    here — only the review verdict path advances it."""
    key = idempotency_key("break", break_id, target_status)

    def txn(cur):
        cur.execute("""
            INSERT INTO action_log (idempotency_key, trace_id, session_id, actor_sub,
                                    action, row_ref)
            VALUES (%s, %s, %s, %s, 'post_break_resolution', %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING action_id""",
            (key, identity.trace_id, identity.session_id, identity.sub,
             json.dumps({"table": "breaks", "break_id": break_id})))
        if cur.fetchone() is None:
            cur.execute("SELECT status FROM breaks WHERE break_id = %s", (break_id,))
            current = cur.fetchone()
            return {"break_id": break_id,
                    "status": current["status"] if current else f"missing:{break_id}",
                    "idempotent_noop": True, "trace_id": identity.trace_id}
        cur.execute("""
            UPDATE breaks
            SET status = %s, proposed_action = %s, confidence = %s, trace_id = %s
            WHERE break_id = %s AND status IN ('OPEN','INVESTIGATING','PROPOSED')
            RETURNING break_id""",
            (target_status, proposed_action, confidence, identity.trace_id, break_id))
        if cur.fetchone() is None:
            raise ValueError(f"illegal transition for break {break_id} → {target_status}")
        return {"break_id": break_id, "status": target_status,
                "idempotent_noop": False, "trace_id": identity.trace_id}

    return serializable_txn(conn, txn)
