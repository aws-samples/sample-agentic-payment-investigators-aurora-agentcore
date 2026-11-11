# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""Ops copilot tools — the global chatbot.

5 READ-ONLY SQL tools over the shared schema + exactly ONE write:
enqueue_task — whose target queue is resolved SERVER-SIDE by the enqueue_task()
Postgres function (FK lookup; the model never picks a specialist), which REFUSES
tasks anchored to no existing entity. Zero agent-to-agent calls: the copilot
reads rows (the workers' knowledge IS their work product) and enqueues tasks;
it never invokes a worker and never receives a worker's answer (choreography,
not orchestration). Cross-domain judgment lives in RUBRIC VIEWS (at_risk), not
this prompt (the God-agent guard). One connection per tool call via the db
factory (see the tools_disputes docstring).
"""
import logging

import psycopg
from strands import tool

from .config import Settings
from .db import query
from .identity import IdentityContext

log = logging.getLogger("investigator.tools.copilot")

COPILOT_SYSTEM_PROMPT = """You are the operations copilot for {company}'s payment
investigation floor. You answer cross-domain questions by READING the shared
ledger — the investigators' work product (drafts, confidence scores, traces,
queues) — and the governed rubric views.

You can: report queue states, look up any dispute or break, read action_log
traces, summarize precedent stats, and list at-risk disputes (the at_risk view's
deterministic rubric — cite its risk_score, do not invent your own).

You can also DELEGATE: enqueue_task(case_ref, instruction) files a durable task
for the owning investigator. You never learn the outcome in this conversation —
give the user the task id and the case deep-link (/case/{{id}}). If a task has no
existing case to anchor to, the system refuses it — point the user at the case
views instead.

You never invoke another agent, never answer for a domain investigator, and
never assess risk yourself — read the views. Answers cite case ids and
trace_ids so every claim is checkable.

Your answers render in a narrow chat dock that shows plain text with light
markdown (bold, inline code) only — NEVER markdown tables, headers,
horizontal rules, or emoji. Answer in short sentences and write counts inline
("8 open, 17 filed, 3 accepted, 2 denied")."""


def build_copilot_tools(db, settings: Settings, identity: IdentityContext) -> list:
    """The per-request tool belt. `db` is a zero-arg factory yielding a
    context-managed, identity-scoped connection (db_factory in db.py)."""

    @tool
    def queue_status() -> dict:
        """Counts by status for both work queues (disputes, breaks) and the
        HITL review queue — 'what's on the floor right now?'."""
        with db() as conn:
            return {
                "disputes": query(conn,
                    "SELECT status, count(*)::int AS n FROM disputes GROUP BY status ORDER BY status"),
                "breaks": query(conn,
                    "SELECT status, count(*)::int AS n FROM breaks GROUP BY status ORDER BY status"),
                "review_queue": query(conn,
                    "SELECT status, count(*)::int AS n FROM review_queue GROUP BY status ORDER BY status"),
            }

    @tool
    def case_lookup(case_ref: int) -> dict:
        """Look up a case id in BOTH domains: the dispute and/or break with
        that id, PLUS any break tied to the dispute's settlement batch (the
        recon tie-back: a disputed txn withheld from a batch surfaces as a
        break on that batch's statement line — same join the recon agent
        uses; ids never match across domains, the LEDGER is the link)."""
        with db() as conn:
            return {
                "dispute": query(conn, """
                    SELECT case_id, reason_code, amount::float, status, confidence::float,
                           evidence_score, trace_id, opened_at::text
                    FROM disputes WHERE case_id = %s""", (case_ref,)),
                "break": query(conn, """
                    SELECT break_id, break_type, status, confidence::float,
                           proposed_action, trace_id, opened_at::text
                    FROM breaks WHERE break_id = %s""", (case_ref,)),
                "linked_breaks_via_settlement": query(conn, """
                    SELECT b.break_id, b.break_type, b.status, b.confidence::float,
                           b.proposed_action, b.trace_id, sl.ref_raw AS statement_ref
                    FROM disputes d
                    JOIN transactions t USING (txn_id)
                    JOIN statement_lines sl ON sl.ref_raw LIKE '%%' || t.settlement_batch || '%%'
                    JOIN breaks b ON b.line_id = sl.line_id
                    WHERE d.case_id = %s""", (case_ref,)),
            }

    @tool
    def trace_lookup(trace_id: str) -> dict:
        """Every action_log row for a trace: what was written, by whom, when —
        the trace-to-row answer for 'why did the agent do this?'."""
        with db() as conn:
            return {"trace_id": trace_id, "actions": query(conn, """
                SELECT action_id, action, actor_sub, session_id, row_ref, created_at::text
                FROM action_log WHERE trace_id = %s ORDER BY action_id""", (trace_id,))}

    @tool
    def precedent_stats() -> dict:
        """Win-rate by analyst over the precedent corpus (the institutional-
        memory numbers: who wins, at what rate, over how many cases)."""
        with db() as conn:
            return {"by_analyst": query(conn, """
                SELECT analyst_id, count(*)::int AS cases,
                       round(avg((outcome = 'won')::int)::numeric, 3)::float AS win_rate
                FROM precedent_cases GROUP BY analyst_id ORDER BY win_rate DESC""")}

    @tool
    def at_risk_disputes(limit: int = 10) -> dict:
        """The at_risk rubric view: open disputes ranked by deterministic risk
        score (deadline proximity × amount band × missing evidence). Read the
        view's judgment; do not re-derive it."""
        with db() as conn:
            return {"at_risk": query(conn, """
                SELECT case_id, reason_code, amount::float, status, days_to_deadline,
                       evidence_score, round(risk_score, 2)::float AS risk_score
                FROM at_risk ORDER BY risk_score DESC LIMIT %s""", (limit,))}

    @tool
    def enqueue_task(case_ref: int, instruction: str, evidence_ref: str = "") -> dict:
        """Delegate work to the investigator that OWNS this case. The database
        resolves which queue (dispute vs recon) from the case id itself and
        refuses ids that don't exist. Delegation is RECORDED, not executed:
        the task lands as a durable task_queue row; nothing in this chat
        carries it out, and no result comes back here."""
        with db() as conn:
            try:
                rows = query(conn, "SELECT enqueue_task(%s, %s, %s) AS task_id",
                             (case_ref, instruction, evidence_ref or None))
            except psycopg.errors.RaiseException as exc:
                conn.rollback()
                return {"refused": True,
                        "reason": str(exc).splitlines()[0],
                        "hint": "anchor the task to an existing dispute case_id or break_id"}
            return {"task_id": rows[0]["task_id"], "case_ref": case_ref,
                    "ui_link": "#queue", "note":
                    "delegation recorded as a durable task_queue row; this "
                    "chat files the request and stops — the case itself is "
                    "on the Queue screen"}

    return [queue_status, case_lookup, trace_lookup, precedent_stats,
            at_risk_disputes, enqueue_task]
