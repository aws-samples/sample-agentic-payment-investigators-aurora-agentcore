# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""Routing — confidence ≥ τ → act; else → review queue (§4 step 5, LO5).

The route decision is data (a row in review_queue or a state transition), never
a message to another agent — HITL goes to a HUMAN (§4: no supervisor). τ comes
from Settings (given here; you pick your own and re-run on the floor).
"""
from dataclasses import dataclass

from .confidence import ConfidenceScore
from .identity import IdentityContext


@dataclass(frozen=True)
class RouteDecision:
    action: str                # "auto" | "review"
    tau: float
    confidence: float
    reason: str


def route(score: ConfidenceScore, tau: float) -> RouteDecision:
    if score.score >= tau:
        return RouteDecision("auto", tau, score.score,
                             f"confidence {score.score:.3f} ≥ τ {tau:.2f}")
    return RouteDecision("review", tau, score.score,
                         f"confidence {score.score:.3f} < τ {tau:.2f} — analyst review")


def enqueue_review(cur, kind: str, case_ref: int, draft: str,
                   score: ConfidenceScore, identity: IdentityContext,
                   tau: float = 0.7) -> int:
    """Insert into the RLS-scoped review queue (runs inside the caller's txn —
    the write path owns transaction boundaries, not the router).

    Idempotent per open item: a re-fired route (two separate agent invocations
    of draft_and_route — the doublefire, review-path edition) must not
    stack duplicate PENDING items for the same case. Enforced by the schema's
    review_queue_one_pending partial UNIQUE index — same by-construction shape
    as the action_log idempotency key, not caller discipline."""
    cur.execute("""
        INSERT INTO review_queue (kind, case_ref, draft, confidence, routed_tau, analyst_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (kind, case_ref) WHERE status = 'PENDING' DO NOTHING
        RETURNING item_id""",
        (kind, case_ref, draft, score.score, tau, identity.sub))
    row = cur.fetchone()
    if row is not None:
        return row["item_id"]
    cur.execute("""
        SELECT item_id FROM review_queue
        WHERE kind = %s AND case_ref = %s AND status = 'PENDING'""", (kind, case_ref))
    existing = cur.fetchone()
    if existing is None:
        # conflict row vanished between INSERT and SELECT (concurrent approve) —
        # surface a real error instead of TypeError on None (review finding #2)
        raise RuntimeError(f"review item for {kind} {case_ref} raced away mid-enqueue")
    return existing["item_id"]
