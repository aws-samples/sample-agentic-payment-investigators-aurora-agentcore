# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""API bridge — browser ↔ AgentCore runtime + RLS-scoped data reads (SPEC §8).

ONE small FastAPI app, two jobs:
  1. POST /api/chat        → relay to InvokeAgentRuntime with the CALLER'S bearer,
                             stream the typed chunks back as SSE (§8c KEEP protocol:
                             text/tool_start/tool_end/artifact/done/error — verbatim,
                             the runtime already emits exactly these).
  2. GET  /api/...reads    → thin SQL reads as workshop_app; identity comes from the
                             VERIFIED JWT's sub → SET app.analyst_id → RLS scopes
                             review_queue server-side. No client-asserted identity.

Token split (§8c KEEP): the browser sends its Cognito ACCESS token for BOTH jobs —
AgentCore's CUSTOM_JWT authorizer re-validates it on the relay path, and this app
verifies it (issuer/signature/expiry via the pool's JWKS) for the data path. JWTs
are verified AT THE EDGE only; nothing downstream re-parses them (IdentityContext
carries sub opaquely — same rule as the agent adapter).

Config (env): WORKSHOP_DB_* (+ WORKSHOP_DB_SECRET_ARN), RUNTIME_ARN,
COGNITO_POOL_ID, COGNITO_CLIENT_ID, AWS_REGION.
Local dev: `uvicorn app:app --reload` from api/ (see api/README.md).
"""
import json
import os
import pathlib
import sys
import urllib.parse
import uuid

import httpx
import jwt  # PyJWT
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

# direct module imports ON PURPOSE: investigator/__init__ pulls the agent
# stack (strands) the bridge never runs — bypassing it keeps the Lambda
# bundle ~1,600 files lighter (the WS scanner's cumulative cap)
from investigator.config import load_settings
from investigator.identity import IdentityContext
from investigator.db import open_connection, query

REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNTIME_ARN = os.environ.get("RUNTIME_ARN", "")
POOL_ID = os.environ.get("COGNITO_POOL_ID", "")
CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"

app = FastAPI(title="payment-ws api", docs_url=None, redoc_url=None)

# CORS: same-origin in the deployed shape (nginx fronts SPA+api together) — this
# only matters for local dev where the SPA (:8081) and api (:8080) split ports.
# Opt-in via env so the deployed config carries no cross-origin surface.
if os.environ.get("API_CORS_ORIGIN"):
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in os.environ["API_CORS_ORIGIN"].split(",")],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

_jwks_cache: dict = {}


async def _jwks() -> dict:
    if not _jwks_cache:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{ISSUER}/.well-known/jwks.json")
            r.raise_for_status()
            _jwks_cache.update(r.json())
    return _jwks_cache


def _signing_key(token: str, jwks: dict):
    """The pool key the token's header names (kid) — Cognito rotates keys, the
    JWKS carries every current one. Missing/unknown kid fails verification
    (same 401 as a bad signature); the cache is not refreshed on a miss."""
    kid = jwt.get_unverified_header(token).get("kid")
    for k in jwks.get("keys", ()):
        if kid and k.get("kid") == kid:
            return jwt.PyJWK(k, algorithm="RS256").key
    raise jwt.InvalidTokenError("no signing key matches token kid")


async def verified_claims(request: Request) -> dict:
    """Verify the ACCESS token at the edge (issuer/signature/expiry/client_id).
    Mirrors the CUSTOM_JWT authorizer so data reads and agent calls agree on
    identity. Cognito access tokens carry client_id (not aud) — §8c AVOID#4."""
    auth = request.headers.get("x-ws-auth") or request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "missing bearer")
    if not POOL_ID:
        raise HTTPException(503, "COGNITO_POOL_ID not configured")
    token = auth[7:]
    try:
        keys = await _jwks()
        claims = jwt.decode(token, _signing_key(token, keys), algorithms=["RS256"],
                            issuer=ISSUER,
                            options={"require": ["exp", "iat", "sub"],
                                     "verify_aud": False})
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"token verification failed: {exc}") from exc
    except httpx.HTTPError as exc:
        # JWKS unreachable is OUR outage, not the caller's bad token
        raise HTTPException(503, f"identity provider unreachable: {exc}") from exc
    if claims.get("client_id") not in (CLIENT_ID, None):
        raise HTTPException(401, "token client_id not allowed")
    if claims.get("token_use") not in ("access", None):
        raise HTTPException(401, "access token required (not an ID token)")
    if not claims.get("sub"):
        raise HTTPException(401, "token carries no sub")
    return claims


def db_ident(claims: dict) -> IdentityContext:
    return IdentityContext.local_dev(          # sub from VERIFIED claims (edge)
        analyst_id=claims["sub"],
        session_id=f"api-{uuid.uuid4()}",
        trace_id=f"api-trace-{uuid.uuid4()}")


# ---------------------------------------------------------------------------
# 1. The chat relay: browser bearer → InvokeAgentRuntime → SSE passthrough
# ---------------------------------------------------------------------------
@app.post("/api/chat")
async def chat(request: Request, claims: dict = Depends(verified_claims)):
    """Relay a prompt to the deployed runtime. Body: {prompt, agent, session_id?}.
    The RUNTIME re-validates the same bearer (CUSTOM_JWT) — this app adds no
    authority; it is a CORS/SSE bridge, deliberately."""
    if not RUNTIME_ARN:
        raise HTTPException(503, "RUNTIME_ARN not configured")
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    agent = body.get("agent", "copilot")
    if not prompt:
        raise HTTPException(400, "prompt required")
    if agent not in ("dispute", "recon", "copilot"):
        raise HTTPException(400, f"unknown agent {agent!r}")
    # session id: browser-supplied for multi-turn continuity, else fresh (≥33)
    session_id = body.get("session_id") or f"webui-{claims['sub'][:8]}-{uuid.uuid4()}"

    esc = urllib.parse.quote(RUNTIME_ARN, safe="")
    url = (f"https://bedrock-agentcore.{REGION}.amazonaws.com"
           f"/runtimes/{esc}/invocations?qualifier=DEFAULT")
    bearer = request.headers.get("x-ws-auth") or request.headers["Authorization"]

    async def relay():
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as c:
            async with c.stream(
                "POST", url,
                headers={"Authorization": bearer,
                         "Content-Type": "application/json",
                         "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id},
                json={"prompt": prompt, "agent": agent},
            ) as resp:
                if resp.status_code != 200:
                    detail = (await resp.aread()).decode()[:300]
                    yield f"data: {json.dumps({'type': 'error', 'error': f'runtime {resp.status_code}: {detail}'})}\n\n"
                    return
                # pass SSE through verbatim — the runtime already frames
                # data: {typed chunk}\n\n (proven by harness_deployed)
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        yield line + "\n\n"
        yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"

    return StreamingResponse(relay(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# 2. Data reads (workshop_app + RLS; sub from verified claims)
# ---------------------------------------------------------------------------
def _rows(claims: dict, sql: str, params=()) -> list[dict]:
    settings = load_settings()
    with open_connection(settings, db_ident(claims)) as conn:
        return query(conn, sql, params)


@app.get("/api/queues")
async def queues(claims: dict = Depends(verified_claims)):
    """Case queue: disputes + breaks with status chips (SPEC §8 surface 1)."""
    return {
        "disputes": _rows(claims, """
            SELECT case_id, network, reason_code, amount::float, status,
                   confidence::float, evidence_score, trace_id, opened_at::text
            FROM disputes ORDER BY opened_at DESC"""),
        "breaks": _rows(claims, """
            SELECT break_id, break_type, status, confidence::float,
                   proposed_action, trace_id, opened_at::text
            FROM breaks ORDER BY opened_at DESC"""),
    }


@app.get("/api/case/{case_id}")
async def case_detail(case_id: int, claims: dict = Depends(verified_claims)):
    """One dispute + its transaction + CE3.0-relevant history (case view)."""
    d = _rows(claims, """
        SELECT d.case_id, d.network, d.reason_code, d.amount::float, d.status,
               d.complaint_text, d.draft_text, d.confidence::float,
               d.evidence_score, d.trace_id, d.opened_at::text,
               t.cardholder_id, t.descriptor, t.settlement_batch
        FROM disputes d JOIN transactions t USING (txn_id)
        WHERE d.case_id = %s""", (case_id,))
    if not d:
        raise HTTPException(404, f"no case {case_id}")
    history = _rows(claims, """
        SELECT txn_id, amount::float, ts::text, disputed,
               host(ip_addr) AS ip_addr, device_id
        FROM transactions WHERE cardholder_id = %s ORDER BY ts""",
        (d[0]["cardholder_id"],))
    # the case's LEDGER ROWS ride along: the drawer folds these into the
    # ask-investigator scope, so "why did you file this?" is answered from
    # the record (system-minted actions/traces/timestamps only — never
    # model-generated text like drafts; that would reopen the injection door)
    record = _rows(claims, """
        SELECT action, trace_id, created_at::text
        FROM action_log WHERE (row_ref->>'case_id')::int = %s
        ORDER BY created_at""", (case_id,))
    return {"case": d[0], "cardholder_history": history, "record": record}


@app.get("/api/break/{break_id}")
async def break_detail(break_id: int, claims: dict = Depends(verified_claims)):
    b = _rows(claims, """
        SELECT b.break_id, b.break_type, b.status, b.confidence::float,
               b.proposed_action, b.trace_id, b.opened_at::text,
               le.amount::float AS ledger_amount, le.ref AS ledger_ref,
               sl.amount::float AS line_amount, sl.ref_raw, sl.narration
        FROM breaks b
        LEFT JOIN ledger_entries le ON le.entry_id = b.ledger_entry_id
        LEFT JOIN statement_lines sl ON sl.line_id = b.line_id
        WHERE b.break_id = %s""", (break_id,))
    if not b:
        raise HTTPException(404, f"no break {break_id}")
    record = _rows(claims, """
        SELECT action, trace_id, created_at::text
        FROM action_log WHERE (row_ref->>'break_id')::int = %s
        ORDER BY created_at""", (break_id,))
    return {"break": b[0], "record": record}


@app.get("/api/review")
async def review_queue(claims: dict = Depends(verified_claims)):
    """RLS does the scoping — this query returns only the CALLER'S items,
    because app.analyst_id was set from the verified sub. No WHERE needed."""
    return {"items": _rows(claims, """
        SELECT item_id, kind, case_ref, draft, confidence::float,
               routed_tau::float, status, created_at::text
        FROM review_queue ORDER BY created_at DESC""")}


@app.post("/api/review/{item_id}/{verdict}")
async def review_decide(item_id: int, verdict: str,
                        claims: dict = Depends(verified_claims)):
    """The HITL click (M3 payoff / M4 approval). RLS: the UPDATE can only hit
    the caller's own row (USING filters target rows)."""
    if verdict not in ("approve", "reject"):
        raise HTTPException(400, "verdict must be approve|reject")
    status = "APPROVED" if verdict == "approve" else "REJECTED"
    settings = load_settings()
    ident = db_ident(claims)
    with open_connection(settings, ident) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE review_queue SET status = %s
                WHERE item_id = %s AND status = 'PENDING'
                RETURNING kind, case_ref""", (status, item_id))
            row = cur.fetchone()
            if row is not None:
                # the HUMAN verdict is an action too — same ledger, same
                # idempotency discipline as the agents' writes (the gate is
                # part of the audit trail, and the tape shows it). The PENDING
                # guard above already makes this exactly-once; the key records it.
                cur.execute("""
                    INSERT INTO action_log (idempotency_key, trace_id, session_id,
                                            actor_sub, action, row_ref)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (idempotency_key) DO NOTHING""",
                    (f"review:{item_id}:{status}", ident.trace_id, ident.session_id,
                     claims["sub"], f"review_{verdict}",
                     json.dumps({"table": "review_queue", "item_id": item_id,
                                 "kind": row["kind"],
                                 ("case_id" if row["kind"] == "dispute" else "break_id"):
                                     row["case_ref"]})))
        conn.commit()
    if row is None:
        raise HTTPException(409, "item not PENDING or not yours (RLS)")
    return {"item_id": item_id, "status": status}


@app.get("/api/activity")
async def activity(since_id: int = 0, limit: int = 40,
                   claims: dict = Depends(verified_claims)):
    """The settlement tape feed: recent action_log rows, ledger-derived only.
    Every line IS a row an agent committed — reads leave no tape (M5's design,
    ambient). since_id makes polling incremental: the tape asks 'anything after
    the last action_id I've seen?' and usually gets [].

    Joined against disputes/breaks for the human-readable ref + status the
    strip shows; row_ref carries the case pointer (kind + id) the write stamped."""
    limit = max(1, min(limit, 100))
    return {"events": _rows(claims, """
        SELECT al.action_id, al.action, al.trace_id, al.actor_sub,
               al.created_at::text,
               al.row_ref->>'table'                    AS ref_table,
               al.row_ref->>'kind'                     AS ref_kind,
               COALESCE(al.row_ref->>'case_id', al.row_ref->>'break_id') AS ref_id,
               d.status  AS dispute_status,
               d.confidence::float AS dispute_confidence,
               b.status  AS break_status,
               b.confidence::float AS break_confidence
        FROM action_log al
        LEFT JOIN disputes d ON al.row_ref->>'case_id' IS NOT NULL
                            AND al.row_ref->>'table' IN ('disputes','review_queue')
                            AND (al.row_ref->>'case_id')::bigint = d.case_id
        LEFT JOIN breaks   b ON al.row_ref->>'break_id' IS NOT NULL
                            AND al.row_ref->>'table' IN ('breaks','review_queue')
                            AND (al.row_ref->>'break_id')::bigint = b.break_id
        WHERE al.action_id > %s
        ORDER BY al.action_id DESC
        LIMIT %s""", (since_id, limit))}


@app.get("/api/trace/{trace_id}")
async def trace(trace_id: str, claims: dict = Depends(verified_claims)):
    """Trace-to-row (M5): every action_log row for a trace + the state rows
    referenced by row_ref."""
    actions = _rows(claims, """
        SELECT action_id, action, actor_sub, session_id, row_ref, created_at::text
        FROM action_log WHERE trace_id = %s ORDER BY action_id""", (trace_id,))
    return {"trace_id": trace_id, "actions": actions}


@app.get("/api/inspector/{case_id}")
async def inspector(case_id: int, claims: dict = Depends(verified_claims)):
    """Corpus inspector: the case's precedent hits + corpus confidence spread
    (SPEC §8 surface 4 — optional visual; terminal path is compare-retrieval)."""
    hits = _rows(claims, """
        SELECT p.precedent_id, p.analyst_id, left(p.complaint_text, 90) AS complaint,
               (1 - (p.complaint_embedding <=> d.complaint_embedding))::float AS cosine
        FROM disputes x
        JOIN LATERAL (SELECT complaint_embedding FROM precedent_cases
                      WHERE tag = 'paraphrase_a') d ON true
        JOIN precedent_cases p ON p.reason_code = x.reason_code AND p.outcome = 'won'
        WHERE x.case_id = %s
        ORDER BY p.complaint_embedding <=> d.complaint_embedding LIMIT 5""",
        (case_id,))
    spread = _rows(claims, """
        SELECT width_bucket(confidence, 0, 1, 10) AS bucket, count(*)::int AS n
        FROM disputes WHERE confidence IS NOT NULL GROUP BY bucket ORDER BY bucket""")
    return {"case_id": case_id, "precedents": hits, "confidence_spread": spread}


@app.get("/api/health")
async def health():
    """Unauthenticated liveness for the CFN smoke (no data exposure)."""
    return JSONResponse({"ok": True, "runtime_configured": bool(RUNTIME_ARN)})
