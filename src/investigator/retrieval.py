# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""Retrieval — the arc as code: naive kNN → hybrid WHERE+rank → iterative_scan.

Every function here runs OUTSIDE any SERIALIZABLE transaction (the storm fix:
retrieval in the write txn is what storms 40001s — measured: 56% → 0% after the split).
Query embedding happens at request time via Titan (the CORPUS was embedded at
build time — these are single-text calls, not bulk).

Exercise seams: each step of that arc is its own small function.
"""
import json
import logging

import boto3

from .config import Settings
from .db import query

log = logging.getLogger("investigator.retrieval")


def embed_query(settings: Settings, text: str) -> list[float]:
    """One Titan V2 call for the query text (normalized, matches corpus)."""
    client = boto3.client("bedrock-runtime", region_name=settings.model_region)
    body = json.dumps({"inputText": text[:8000],
                       "dimensions": settings.embed_dim, "normalize": True})
    resp = client.invoke_model(modelId=settings.embed_model_id, body=body)
    return json.loads(resp["body"].read())["embedding"]


def _vec(embedding: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"


# -- the anti-pattern, kept runnable so the demo is honest --------------------
def naive_knn(conn, settings: Settings, complaint_text: str) -> list[dict]:
    """Pure cosine kNN, no WHERE — rebuttals ride along UNFILTERED (winners,
    losers, wrong codes: whatever cosine drags in). Module 6 swaps it out."""
    emb = embed_query(settings, complaint_text)
    return query(conn, """
        SELECT precedent_id, reason_code, outcome, rebuttal_text,
               left(complaint_text, 120) AS complaint_text,
               1 - (complaint_embedding <=> %s::vector) AS cosine_similarity
        FROM precedent_cases
        ORDER BY complaint_embedding <=> %s::vector
        LIMIT %s""", (_vec(emb), _vec(emb), settings.retrieval_top_k))


# -- the hybrid query with the overfiltering fix ------------------------------
def hybrid_precedent_search(conn, settings: Settings, reason_code: str,
                            complaint_text: str, limit: int | None = None) -> list[dict]:
    """Hard WHERE on categorical keys + pgvector rank on free text (pattern step 3).

    At defaults (ef_search=40, iterative_scan=off) the 3%-selective predicate
    returns ZERO rows (measured on this workshop's dataset); iterative_scan is the fix.
    SET LOCAL keeps both knobs scoped to this transaction only.

    `limit` defaults to retrieval_top_k; module 6's two-stage pipeline passes
    rerank_overfetch (25) so the reranker has a shortlist to work with —
    relaxed_order is safe there because the reranker's total order subsumes it.
    """
    emb = embed_query(settings, complaint_text)
    with conn.cursor() as cur:
        # set_config with is_local=true == SET LOCAL, but parameterizable
        cur.execute("SELECT set_config('hnsw.ef_search', %s, true)",
                    (str(settings.hnsw_ef_search),))
        cur.execute("SELECT set_config('hnsw.iterative_scan', %s, true)",
                    (settings.hnsw_iterative_scan,))
        cur.execute("""
            SELECT precedent_id, analyst_id, complaint_text, rebuttal_text,
                   1 - (complaint_embedding <=> %s::vector) AS cosine_similarity
            FROM precedent_cases
            WHERE reason_code = %s AND outcome = 'won'
            ORDER BY complaint_embedding <=> %s::vector
            LIMIT %s""",
            (_vec(emb), reason_code, _vec(emb), limit or settings.retrieval_top_k))
        rows = cur.fetchall()
    conn.commit()
    return rows


# -- deterministic grounding join FIRST, text rank second ---------------------
def get_rule_grounding(conn, network: str, reason_code: str) -> list[dict]:
    """rule_mappings → rule_clauses: the categorical join IS the retrieval for
    rulebook grounding; embeddings play no part where a key exists (the rule)."""
    return query(conn, """
        SELECT rc.citation, rc.clause_text
        FROM rule_mappings rm
        JOIN rule_clauses rc USING (clause_id)
        WHERE rm.network = %s AND rm.reason_code = %s
        ORDER BY rc.clause_id""", (network, reason_code))


# -- rerank stage 2: score the field the draft CONSUMES -----------------------
def rerank_precedents(settings: Settings, complaint_text: str,
                      candidates: list[dict]) -> list[dict]:
    """Stage 2: cross-encoder scoring of (complaint, candidate REBUTTAL) pairs.
    Ranks on the field the draft consumes, which the bi-encoder cannot do."""
    if not candidates:
        return []
    client = boto3.client("bedrock-agent-runtime", region_name=settings.model_region)
    resp = client.rerank(
        queries=[{"type": "TEXT", "textQuery": {"text": complaint_text[:9000]}}],
        sources=[{"type": "INLINE",
                  "inlineDocumentSource": {"type": "TEXT",
                                           "textDocument": {"text": c["rebuttal_text"]}}}
                 for c in candidates],
        rerankingConfiguration={
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {"modelArn": settings.rerank_model_arn},
                "numberOfResults": min(settings.retrieval_top_k, len(candidates))}})
    ranked = []
    for r in resp["results"]:
        hit = dict(candidates[r["index"]])
        hit["rerank_score"] = round(r["relevanceScore"], 3)
        ranked.append(hit)
    return ranked


# -- the recon transfer instantiation over break_resolutions ------------------
def resolution_precedent_search(conn, settings: Settings, break_type: str,
                                note_text: str) -> list[dict]:
    """Same hybrid shape, recon domain: break_type is a KEY (WHERE — soft-matching
    it recreates the dispute anti-pattern); root-cause text is ranked by cosine."""
    emb = embed_query(settings, note_text)
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('hnsw.iterative_scan', %s, true)",
                    (settings.hnsw_iterative_scan,))
        cur.execute("""
            SELECT resolution_id, root_cause_note, resolution_action,
                   1 - (note_embedding <=> %s::vector) AS cosine_similarity
            FROM break_resolutions
            WHERE break_type = %s
            ORDER BY note_embedding <=> %s::vector
            LIMIT %s""",
            (_vec(emb), break_type, _vec(emb), settings.retrieval_top_k))
        rows = cur.fetchall()
    conn.commit()
    return rows


def few_shot_block(precedents: list[dict]) -> str:
    """Winning rebuttals → the drafting prompt's few-shot slot.

    Every shot is fenced in <untrusted-evidence> tags: precedent text is
    promoted CARDHOLDER prose (the flywheel writes complaints back into the
    corpus), which makes it a prompt-injection surface the moment it re-enters
    a prompt. The fence marks it as evidence to imitate in FORM, never
    instructions to follow — the module-5 injection rule, in code."""
    if not precedents:
        return "(no precedent found — draft from rule clauses alone and say so)"
    shots = []
    for p in precedents:
        # label honestly: the hybrid path only fetches winners (WHERE
        # outcome='won'), but module 5's naive lookup drags in whatever
        # cosine found — a lost rebuttal labeled "winning" would poison
        # the draft twice over
        outcome = p.get("outcome", "won")
        label = "WINNING REBUTTAL" if outcome == "won" \
            else f"REBUTTAL (outcome: {outcome.upper()} — do not imitate)"
        shots.append(
            "<untrusted-evidence>\n"
            f"COMPLAINT: {p['complaint_text']}\n"
            f"{label}: {p['rebuttal_text']}\n"
            "</untrusted-evidence>")
    return "\n---\n".join(shots)
