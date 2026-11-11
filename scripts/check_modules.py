#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""check_modules.py m4..m11 — the per-module assertions behind check.sh.

Every check names exactly which assertion disagrees (the content promises
that). Structured payloads only: tool returns, DB rows, catalog state,
typed SSE chunks, control-plane state — never substrings of model text.
Exit 75 = environment unreachable (SKIP), 1 = a check failed, 0 = green.

Module map (dispatch keys are the ATTENDEE-FACING module ids — the internal
fn names keep an earlier m2..m8 numbering):
  m4  evidence tools (get_transaction_history + check_ce3_eligibility)
  m5  naive_knn wired (embeddings module)
  m6  hybrid + iterative_scan + rerank pipeline
  m7  exactly-once write path (idempotency + transition guard + retry)
  m8  recon: tie-back + resolution_precedent_search split (keys vs free text)
  m9  deployed: runtime READY + cutover + live invoke + #4144 worked by it
  m10 observability: human review verdicts recorded in the audit log
  m11 optional drain: run_queue.sh emptied the breaks queue through YOUR
      runtime (exactly-once ledger rows, REVIEW holds) — requires m9

m9/m10/m11 run ON the WebHost (control-plane + deployed invoke + private Aurora
are all reachable there); off-host they degrade to loud SKIP lines, never a
silent pass. Only the module 9 check reads review_queue, RLS-scoped under the
attendee's JWT sub; the others avoid it: it is the one RLS-scoped table
(analyst_id = the JWT sub), and this checker connects as the local string
'analyst_martha', so review_queue would return zero rows — action_log and
breaks carry no RLS and hold the same facts.
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))  # deployed layout

FAILURES = []
SKIPPED = []   # mandatory checks that could NOT run (no token, no API access): not green


def _colour(code: str, text: str) -> str:
    """ANSI colour ONLY on a real terminal (the attendee's Code Editor shell);
    harness/SSM captures and pipes stay plain text so nothing that greps the
    output changes. NO_COLOR is honoured."""
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        return f"\033[{code}m{text}\033[0m"
    return text


def skip(name, detail=""):
    """A check that could not run at all. Printed loud, remembered, and main()
    reports UNVERIFIED (exit 75) — never green — until the cause is fixed. A
    2026-09-27 review showed check m9 printing green after four SKIPs."""
    print(f"  {_colour('33', 'SKIP')}  {name}" + (f": {detail}" if detail else ""))
    SKIPPED.append(name)


def check(name, ok, detail="", why_fail=""):
    """`detail` prints always (context); `why_fail` prints ONLY on failure —
    keep alarming diagnoses out of green lines."""
    suffix = detail if ok else (why_fail or detail)
    tag = _colour("32", "ok ") if ok else _colour("31", "FAIL")
    print(f"  {tag} {name}" + (f" — {suffix}" if suffix else ""))
    if not ok:
        FAILURES.append(name)


def invoke(label, fn, **kwargs):
    """Call one of the participant's own functions VISIBLY — same ⚙ glyph the
    agent transcripts use. The check exercises your code; it should look like it."""
    args = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
    print(f"  ⚙ {label}({args})")
    return fn(**kwargs)


def connect_or_skip():
    from investigator import IdentityContext, load_settings, new_session_id, new_trace_id
    from investigator.db import open_connection
    settings = load_settings()
    ident = IdentityContext.local_dev("analyst_martha", new_session_id(), new_trace_id())
    try:
        ctx = open_connection(settings, ident)
        conn = ctx.__enter__()
    except Exception as exc:
        print(f"  database unreachable ({type(exc).__name__}) — set WORKSHOP_DB_*")
        sys.exit(75)
    return settings, ident, ctx, conn


def tools_for(settings, ident):
    from investigator.db import db_factory
    from investigator.tools_disputes import build_dispute_tools
    tools = build_dispute_tools(db_factory(settings, ident), settings, ident)
    return {t.tool_name: t for t in tools}


def require_wired(t, names, site):
    """The belt list at the bottom of build_dispute_tools is the agent's
    hands; each module's closing exercise wires the new tool in. A name
    missing here means the tool exists but the agent can't reach it — and
    every later assertion would crash trying. One check line per tool, so
    the wiring edit gets the same ok/FAIL feedback the tool bodies get."""
    mod = site.split("-")[0]
    for n in names:
        check(f"{n}: wired into the agent's belt", n in t,
              why_fail=(f"written but not wired — open tools_disputes.py, find "
                        f"the YOUR CODE HERE ({site}) line at the bottom of "
                        f"build_dispute_tools, and add {n} to the belt list")
                       if mod == "m4" else
                       (f"not wired — run scripts/solve.sh {mod} (the module's "
                        f"step); it installs the finished belt"))
    return all(n in t for n in names)


def m2(settings, ident, conn):
    """Evidence tools: the module-4 exercises."""
    from investigator.db import query
    t = tools_for(settings, ident)
    if not require_wired(t, ["get_transaction_history", "check_ce3_eligibility"],
                         "m4-belt"):
        return   # nothing below can run against an unwired belt

    print("  exercising YOUR tools from tools_disputes.py, same objects the agent holds:")
    hist = invoke("get_transaction_history", t["get_transaction_history"],
                  cardholder_id="ch_jackson_m")
    check("get_transaction_history: returns the cardholder's transactions",
          isinstance(hist.get("transactions"), list) and hist.get("count", 0) >= 3,
          f"count={hist.get('count')}")
    ts = [r["ts"] for r in hist.get("transactions", [])]
    check("get_transaction_history: time-ordered", ts == sorted(ts))
    need = {"txn_id", "amount", "ts", "disputed", "ip_addr", "device_id", "shipping_addr", "email"}
    have = set(hist["transactions"][0]) if hist.get("transactions") else set()
    check("get_transaction_history: CE 3.0 fields present", need <= have,
          f"missing: {sorted(need - have)}" if not need <= have else "")

    # check_ce3_eligibility WRITES evidence_score (its contract); snapshot and
    # restore so the check leaves the floor exactly as found (the write-module rule:
    # checks verify, never mutate — the agent's own run makes the real write)
    before = query(conn, "SELECT evidence_score FROM disputes WHERE case_id = 4127")[0]["evidence_score"]
    ce3 = invoke("check_ce3_eligibility", t["check_ce3_eligibility"], case_id=4127)
    query(conn, "UPDATE disputes SET evidence_score = %s WHERE case_id = 4127", (before,))
    check("check_ce3_eligibility: 4127 eligible", ce3.get("ce3_eligible") is True)
    check("check_ce3_eligibility: ≥2 qualifying priors",
          len(ce3.get("qualifying_priors", [])) >= 2,
          f"got {len(ce3.get('qualifying_priors', []))}")
    check("check_ce3_eligibility: every prior has a core element (IP/device)",
          all(any(m in ("ip_match", "device_match") for m in p["matched_elements"])
              for p in ce3.get("qualifying_priors", [])))
    # the window, both edges. The corpus seeds a 45-day-old jackson purchase
    # (matching elements, INSIDE the 120-day floor) precisely so a missing or
    # floorless window is observable: a correct window excludes it.
    row = query(conn, """
        SELECT t.ts FROM disputes d JOIN transactions t USING (txn_id)
        WHERE d.case_id = 4127""")
    disputed_ts = row[0]["ts"]
    late = [p for p in ce3.get("qualifying_priors", []) if p["ts"] >= str(disputed_ts)]
    check("check_ce3_eligibility: window direction (no future txns qualify)",
          not late, f"{len(late)} prior(s) postdate the disputed txn")
    recent = query(conn, """
        SELECT count(*) AS n FROM transactions p,
               (SELECT t.* FROM disputes d JOIN transactions t USING (txn_id)
                WHERE d.case_id = 4127) disputed
        WHERE p.cardholder_id = disputed.cardholder_id
          AND p.txn_id <> disputed.txn_id AND NOT p.disputed
          AND p.ts > disputed.ts - interval '120 days'
          AND p.ts < disputed.ts""")[0]["n"]
    # data-driven floor check: the qualifying set must be smaller than the
    # "all undisputed priors" set — the 45-day trap row must NOT qualify
    check("check_ce3_eligibility: the 120-day floor holds (45-day-old purchase excluded)",
          recent >= 1 and len(ce3.get("qualifying_priors", [])) == 2,
          f"qualifying={len(ce3.get('qualifying_priors', []))}, "
          f"inside-floor candidates={recent}",
          why_fail="a purchase newer than 120 days is qualifying — the window's "
                   "FLOOR is missing (CE 3.0 rejects recent priors: a 45-day-old "
                   "purchase could be the fraudster's own)")


def m3(settings, ident, conn):
    """naive_knn: module 5's exercise — embeddings wired, anti-pattern intact."""
    from investigator.db import query
    from investigator.retrieval import naive_knn
    # module 5's closing edit: find_precedents joins the belt. naive_knn
    # below is belt-independent, so keep checking it either way.
    require_wired(tools_for(settings, ident), ["find_precedents"], "m5-belt")
    # Use the SAME complaint the content demonstrates on — case #4127's own text
    # (content §"Find one complaint's nearest neighbors" runs naive_knn on the
    # open case, and its screenshot shows the top-5 including LOST cases). An
    # ad-hoc string can be TOO unambiguous ("I didn't make this charge" alone
    # returns all-won 10.4), which hides the very anti-pattern the module teaches.
    # FLOOR CANARIES first — these catch drift the beat outputs can't:
    # (a) the recall floor is plumbed (PGOPTIONS -> hnsw.ef_search=200; a
    #     parallel-built HNSW graph at the default 40 MISSED an exact
    #     self-match on a live floor), and (b) the index finds the seeded
    #     anchor's own point at the content's pinned similarities — exact
    #     values, because the vectors ship in the seeds.
    ef = query(conn, "SHOW hnsw.ef_search")[0]["hnsw.ef_search"]
    check("vector recall floor active (hnsw.ef_search = 200)", ef == "200",
          f"ef_search={ef}",
          why_fail="open a NEW terminal (it sources /etc/workshop.env, which "
                   "exports PGOPTIONS) and re-run")
    ANCHOR_TOP3 = """
        WITH q AS (SELECT complaint_embedding FROM precedent_cases WHERE precedent_id = 60001)
        SELECT precedent_id,
               round((1 - (complaint_embedding <=> (SELECT complaint_embedding FROM q)))::numeric, 3) AS sim
        FROM precedent_cases
        ORDER BY complaint_embedding <=> (SELECT complaint_embedding FROM q)
        LIMIT 3"""
    TRIO = [(60001, 1.000), (60003, 0.817), (60002, 0.559)]
    print("  ⚙ SELECT … ORDER BY complaint_embedding <=> (anchor) LIMIT 3  (floor session, ef 200)")
    top3 = query(conn, ANCHOR_TOP3)
    got = [(r["precedent_id"], float(r["sim"])) for r in top3]
    check("self-recall: anchor top-3 at the pinned similarities",
          got == TRIO, f"got={got}",
          why_fail="the floor's top-3 for the anchor drifted — the seed "
                   "corpus or its embeddings changed (reseed)")
    # (c) REMOVED 2026-09-28: the two "demo pin / demo recall at ef_search = 40"
    #     checks asserted a module-5 EXPLAIN demo that the Tier 1 reading cuts
    #     deleted, and the recall half depended on per-floor HNSW build luck
    #     (a fresh floor can miss one of the trio at 40 while the floor's ef 200
    #     is exact). It went red on a WS test-event floor with a remedy
    #     (REINDEX) that workshop_app cannot run. The recall floor + exact
    #     self-recall checks above are the floor canaries that matter.
    complaint = query(conn, "SELECT complaint_text FROM disputes WHERE case_id = 4127")[0]["complaint_text"]
    print("  exercising YOUR naive_knn from retrieval.py (one Titan embed + one pgvector scan):")
    rows = invoke("naive_knn", lambda complaint_text: naive_knn(conn, settings, complaint_text),
                  complaint_text=complaint)
    check("naive_knn: returns top-k rows", len(rows) == settings.retrieval_top_k,
          f"got {len(rows)}")
    need = {"precedent_id", "reason_code", "outcome", "cosine_similarity"}
    check("naive_knn: structured fields present", rows and need <= set(rows[0]))
    sims = [float(r["cosine_similarity"]) for r in rows]
    check("naive_knn: ranked by similarity", sims == sorted(sims, reverse=True))
    # the TEACHING property (content §520/529): naive cosine can't tell WON from
    # LOST — "every 10.4 sounds like every other 10.4". The observable
    # anti-pattern is a top-k that mixes outcomes (or crosses reason codes),
    # so a human would argue from a losing precedent. #4127 surfaces lost 10.4s.
    outcomes = {r["outcome"] for r in rows}
    check("naive_knn: anti-pattern observable (mixes won/lost or crosses codes)",
          len(outcomes) > 1 or len({r["reason_code"] for r in rows}) > 1,
          f"outcomes={sorted(outcomes)}",
          why_fail="top-k is single-outcome single-code — corpus drift? the "
                   "'similar complaints are cheap' demo loses its bite")


def m4(settings, ident, conn):
    """Hybrid + iterative_scan + rerank: the production retrieval pipeline."""
    from investigator.retrieval import hybrid_precedent_search, rerank_precedents
    from investigator.db import query as q
    # module 6's closing edit: get_rule_clauses joins the belt (and
    # find_precedents' body becomes the two-stage pipeline, asserted below)
    t = tools_for(settings, ident)
    require_wired(t, ["get_rule_clauses"], "m6-belt")
    novel = "The product I got looks totally different from the listing photos"

    # the m6-swap site is a single INLINE line: the
    # naive_knn call carries the ==> marker in its own trailing comment, and
    # the exercise is one action — replace that line with the two-line
    # pipeline. rerank_score is minted only inside rerank_precedents, so its
    # presence in the TOOL's own output proves the pipeline is what ran.
    if "find_precedents" in t:
        fp = invoke("find_precedents (your swapped body)",
                    t["find_precedents"], case_id=4127)
        precs = fp.get("precedents") or []
        check("find_precedents: two-stage pipeline live (hits carry rerank_score)",
              bool(precs) and all("rerank_score" in p for p in precs),
              why_fail="the tool still returns naive results — the "
                       "(m6-swap)-tagged 'hits = naive_knn(...)' line in "
                       "tools_disputes.py must be replaced by the two-line "
                       "pipeline (and no naive_knn assignment may follow "
                       "it); scripts/solve.sh m6 is the module's step — run it")

    # the module's zero-rows demo case must exist and keep its property
    demo = q(conn, "SELECT reason_code, complaint_text FROM disputes WHERE case_id = 4136")
    check("demo case 4136 present (13.3, fraud-flavored complaint text)",
          bool(demo) and demo[0]["reason_code"] == "13.3",
          why_fail="reseed — module 4's overfilter demo depends on it")

    print("  exercising YOUR retrieval pipeline from retrieval.py:")
    hits = invoke("hybrid_precedent_search",
                  lambda reason_code, complaint_text: hybrid_precedent_search(
                      conn, settings, reason_code, complaint_text),
                  reason_code="13.3", complaint_text=novel)
    check("hybrid: fills top-k under a selective filter (iterative_scan on)",
          len(hits) == settings.retrieval_top_k, f"got {len(hits)}")
    def _filter_holds(rows):
        # the tool's SELECT does not return reason_code/outcome, so prove the
        # filter from the table itself: every returned id must be 13.3 AND won
        ids = [int(r["precedent_id"]) for r in rows]
        if not ids:
            return False, "no rows returned"
        got = {int(r["precedent_id"]): r for r in q(conn,
               "SELECT precedent_id, reason_code, outcome FROM precedent_cases "
               "WHERE precedent_id = ANY(%s)", (ids,))}
        bad = [i for i in ids if i not in got
               or got[i]["reason_code"] != "13.3" or got[i]["outcome"] != "won"]
        return (not bad), (f"off-filter ids: {bad}" if bad
                           else f"{len(ids)}/{len(ids)} returned rows are reason 13.3 and won")
    ok_filter, filter_detail = _filter_holds(hits)
    check("hybrid: keys are HARD-filtered (every hit is reason 13.3 AND outcome won, checked in precedent_cases)",
          ok_filter, filter_detail,
          why_fail="a returned precedent is off-filter — the WHERE must carry "
                   "reason_code = %s AND outcome = 'won' (module 6 §1), not a soft match")
    cands = invoke("hybrid_precedent_search",
                   lambda reason_code, complaint_text, limit: hybrid_precedent_search(
                       conn, settings, reason_code, complaint_text, limit=limit),
                   reason_code="13.3", complaint_text=novel,
                   limit=settings.rerank_overfetch)
    check("hybrid: overfetch honors the limit arg", len(cands) == settings.rerank_overfetch,
          f"got {len(cands)}")
    ok_filter, filter_detail = _filter_holds(cands)
    check("hybrid: the overfetch rows are hard-filtered too", ok_filter, filter_detail,
          why_fail="an overfetched precedent is off-filter — the same WHERE must apply at any limit")

    print(f"  ⚙ rerank_precedents(query={(novel[:40] + '…')!r}, candidates=<{len(cands)} rows>)")
    ranked = rerank_precedents(settings, novel, cands)
    check("rerank: returns top-k of the candidates",
          len(ranked) == settings.retrieval_top_k, f"got {len(ranked)}")
    check("rerank: every hit carries a rerank_score",
          all("rerank_score" in r for r in ranked))
    scores = [r["rerank_score"] for r in ranked]
    check("rerank: ranked by rerank_score", scores == sorted(scores, reverse=True))
    check("rerank: reorders vs cosine (stage 2 is doing work)",
          [r["precedent_id"] for r in ranked] !=
          [c["precedent_id"] for c in cands[:len(ranked)]],
          why_fail="rerank order identical to cosine order — check the sources field (rebuttal_text?)")


def m5(settings, ident, conn):
    """Exactly-once: the write-path clauses + retry, and the hero case's
    ledger evidence — YOUR AGENT filed #4127 via draft_and_route (belt beat);
    the double-fire demo ran on a filler. This check verifies — it never
    mutates the floor (a check that files a case would print a mystery line
    on the settlement tape)."""
    import inspect

    from investigator import writes
    from investigator.db import query, serializable_txn

    # module 7's closing edit: draft_and_route joins the belt — the agent
    # can now submit, not just draft
    require_wired(tools_for(settings, ident), ["draft_and_route"], "m7-belt")

    print("  reading YOUR write path (source inspection, then the ledger — never mutating):")
    print("  ⚙ inspect.getsource(file_representment)")
    src = inspect.getsource(writes.file_representment)
    check("file_representment: idempotency clause present",
          "ON CONFLICT (idempotency_key) DO NOTHING" in src and "RETURNING action_id" in src)
    check("file_representment: transition guard present",
          "dispute_transitions" in src and "EXISTS" in src)
    dbsrc = inspect.getsource(serializable_txn)
    check("serializable_txn: bounded backoff retry present",
          "SerializationFailure" in dbsrc and "sleep" in dbsrc)

    # the agent's filing left evidence (draft_and_route auto-filed at ≥ τ);
    # assert exactly-once from the LEDGER, read-only
    print("  ⚙ SELECT count(*) FROM action_log WHERE idempotency_key = 'dispute:4127:FILED'")
    n = query(conn, """
        SELECT count(*) AS n FROM action_log
        WHERE idempotency_key = 'dispute:4127:FILED'""")
    if n[0]["n"] == 0:
        check("exactly-once evidence: one ledger row for dispute:4127:FILED", False,
              "no row yet — in the agent chat (uv run -m investigator.chat --agent dispute) "
              "send module 7's chat prompt for case 4127; the agent files it through draft_and_route")
    else:
        check("exactly-once evidence: EXACTLY one ledger row for dispute:4127:FILED",
              n[0]["n"] == 1, f"rows={n[0]['n']}",
              why_fail=f"rows={n[0]['n']} — the idempotency clause is not deduping")
        hero = query(conn, "SELECT status FROM disputes WHERE case_id = 4127")
        check("hero case FILED exactly once (not double-transitioned)",
              hero[0]["status"] in ("FILED", "ACCEPTED"), hero[0]["status"])


def m6(settings, ident, conn):
    """Recon: the tie-back JOIN + the key-vs-free-text split (validates the SPLIT,
    not exact syntax — the content's promise)."""
    import inspect

    from investigator import retrieval
    from investigator.db import db_factory
    from investigator.tools_recon import build_recon_tools
    t = {x.tool_name: x for x in build_recon_tools(db_factory(settings, ident), settings, ident)}

    print("  exercising YOUR recon tools from tools_recon.py:")
    try:
        ctx = invoke("get_break_context", t["get_break_context"], break_id=1)
    except Exception as e:  # a crash IS the finding — teach it, don't traceback
        placeholder_left = "placeholders but" in str(e)
        check("get_break_context: runs without crashing", False, str(e)[:120],
              why_fail="the placeholder WHERE line is still in the query — your "
                       "WHERE must REPLACE it, not sit next to it; delete the "
                       "line saying 'write the tie-back predicate' and re-run"
              if placeholder_left else
              "get_break_context raised — re-check your tie-back edit against "
              "the module's Solution tab")
        return
    check("get_break_context: both sides of the disagreement present",
          "ledger_amount" in ctx and "line_amount" in ctx and "amount_gap" in ctx)
    # the tie-back predicate is a BUILD site: an unwritten/wrong WHERE returns
    # an empty list, so assert the JOIN actually finds the withheld dispute
    tb = ctx.get("dispute_tieback") or []
    check("get_break_context: tie-back finds the withheld dispute (#4127, $847)",
          any(r.get("case_id") == 4127 and abs(r.get("amount", 0) - 847.0) < 0.01
              for r in tb),
          f"tieback={[(r.get('case_id'), r.get('amount')) for r in tb]}",
          why_fail="break 1's $847 gap should tie back to case 4127 — write the "
                   "tie-back WHERE in get_break_context (batch match + disputed "
                   "+ amount explains the gap); hints in the module")

    print("  ⚙ inspect.getsource(resolution_precedent_search)")
    src = inspect.getsource(retrieval.resolution_precedent_search)
    check("resolution_precedent_search: break_type is a KEY (WHERE), not embedded",
          "break_type" in src and "WHERE" in src.upper())
    check("resolution_precedent_search: free text ranked by embedding",
          "note_embedding" in src and "<=>" in src)
    rows = invoke("resolution_precedent_search",
                  lambda break_type, note: retrieval.resolution_precedent_search(
                      conn, settings, break_type, note),
                  break_type="SHORT_PAY", note="buyer took the early payment discount")
    check("resolution_precedent_search: returns ranked rows",
          0 < len(rows) <= settings.retrieval_top_k and "cosine_similarity" in rows[0])


def _effective_runtime_arn() -> str:
    """The ARN the floor actually talks to: /etc/workshop-runtime.env (the
    cutover file go_live.sh writes) wins over /etc/workshop.env, exactly as
    every scripts/*.sh sources them. We read the files rather than trust the
    process env so the check reports the floor's real state, not this shell's."""
    arn = ""
    for path in ("/etc/workshop.env", "/etc/workshop-runtime.env"):  # later wins
        try:
            for line in pathlib.Path(path).read_text().splitlines():
                line = line.strip()
                if line.startswith("WORKSHOP_RUNTIME_ARN="):
                    arn = line.split("=", 1)[1].strip().strip('"')
        except OSError:
            continue
    return arn or os.environ.get("WORKSHOP_RUNTIME_ARN", "")


def _invoke_runtime(runtime_arn, region, bearer, payload, session_id):
    """One HTTPS invoke of the deployed runtime with a Cognito bearer, stdlib
    only. Returns (status, [typed chunks], raw[:500]). The CUSTOM_JWT runtime
    validates the bearer itself, so we skip SigV4 and POST directly — the same
    call invoke.sh makes. (Kept in-file: harness_deployed.py is an author-side
    gate that does not ship to the WebHost.)"""
    import urllib.error
    import urllib.parse
    import urllib.request
    esc = urllib.parse.quote(runtime_arn, safe="")
    url = (f"https://bedrock-agentcore.{region}.amazonaws.com"
           f"/runtimes/{esc}/invocations?qualifier=DEFAULT")
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {bearer}")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Amzn-Bedrock-AgentCore-Runtime-Session-Id", session_id)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:  # nosec B310 - fixed https AWS endpoint built above
            body, status = resp.read().decode(), resp.status
    except urllib.error.HTTPError as e:
        return e.code, [], e.read().decode()[:500]
    chunks = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                chunks.append(obj)
        except json.JSONDecodeError:
            continue
    return status, chunks, body[:500]


def m7(settings, ident, conn):
    """Deployed: the attendee's own runtime is READY, the floor is cut over to
    it, it answers a live authenticated invoke, and case #4144 — the invoke
    step — left ledger rows behind. Read-only: the invoke uses the copilot
    (read tools only), and every DB assertion counts, never writes. The queue
    drain lives in optional module 11 (function m9 below).

    review_queue is deliberately untouched — it is RLS-scoped to the JWT sub,
    so this local-identity connection can't see the deployed agent's rows;
    action_log (no RLS) carries the same evidence."""
    import subprocess
    import uuid

    from investigator.db import query

    arn = _effective_runtime_arn()
    region = settings.model_region

    # 1. cutover state — files on THIS host (the WebHost), not cloud calls
    cutover = pathlib.Path("/etc/workshop-runtime.env")
    check("cutover file written (go_live.sh ran)", cutover.is_file(),
          why_fail="no /etc/workshop-runtime.env — run scripts/go_live.sh after deploy")
    check("floor points at YOUR runtime (paymentagents), not the incumbent",
          "paymentagents" in arn, f"arn=…{arn[-40:]}" if arn else "no ARN resolved",
          why_fail="effective WORKSHOP_RUNTIME_ARN is still the incumbent — cutover didn't take")

    # the PRODUCTION bridge (Lambda) was repointed by the cutover — read the
    # deployed config back rather than trusting the local file (go_live.sh
    # waits for function-updated, so a mismatch here is a REAL failure)
    bridge = os.environ.get("BRIDGE_FN", "")
    if bridge:
        try:
            import boto3
            benv = (boto3.client("lambda", region_name=region)
                    .get_function_configuration(FunctionName=bridge)
                    .get("Environment", {}).get("Variables", {}))
            check("production bridge points at YOUR runtime",
                  benv.get("RUNTIME_ARN") == arn,
                  f"bridge arn=…{str(benv.get('RUNTIME_ARN'))[-40:]}",
                  why_fail="bridge env RUNTIME_ARN != cutover ARN — go_live.sh didn't take")
        except Exception as exc:                                    # noqa: BLE001
            skip("bridge check", f"{type(exc).__name__} "
                 "(need lambda:GetFunctionConfiguration on this host's role)")
    else:
        skip("bridge check", "BRIDGE_FN unset — cannot verify the production bridge (run on the WebHost)")

    # 2. the control plane agrees the runtime exists and is READY
    try:
        import boto3
        cp = boto3.client("bedrock-agentcore-control", region_name=region)
        runtimes = cp.list_agent_runtimes().get("agentRuntimes", [])
    except Exception as exc:                                        # noqa: BLE001
        skip("control-plane checks (runtime exists, READY, rollback target)",
             f"{type(exc).__name__} (need bedrock-agentcore-control on this host's role)")
        runtimes = None
    if runtimes is not None:
        mine = [r for r in runtimes
                if "paymentagents" in r.get("agentRuntimeName", "")]
        check("your runtime exists in the control plane", bool(mine),
              why_fail="no paymentagents_* runtime — did `agentcore deploy` finish?")
        check("your runtime is READY",
              any(r.get("status") == "READY" for r in mine),
              f"statuses={[r.get('status') for r in mine]}" if mine else "")
        check("incumbent still present as rollback target (2 runtimes)",
              len(runtimes) >= 2, f"{len(runtimes)} runtime(s)")

    # 3. a live authenticated invoke returns a typed stream ending in `done`.
    #    Same HTTPS+bearer path invoke.sh uses (a CUSTOM_JWT runtime wants the
    #    bearer in Authorization, so we call the data-plane endpoint directly,
    #    not boto3's SigV4 client). Every failure below prints a loud SKIP —
    #    never a silent pass (the module's whole promise is "it's really live").
    token = ""  # nosec B105 - empty placeholder, bearer is fetched below
    if not arn:
        skip("live invoke", "no runtime ARN resolved (see cutover check above)")
    else:
        try:
            import boto3
            secret = os.environ["WORKSHOP_ANALYST_SECRET_ARN"]
            pw = json.loads(boto3.client("secretsmanager", region_name=region)
                            .get_secret_value(SecretId=secret)["SecretString"])["password"]
            token = boto3.client("cognito-idp", region_name=region).initiate_auth(
                AuthFlow="USER_PASSWORD_AUTH",
                ClientId=os.environ["WORKSHOP_COGNITO_CLIENT_ID"],
                AuthParameters={"USERNAME": "analyst_martha", "PASSWORD": pw},
            )["AuthenticationResult"]["AccessToken"]
        except Exception as exc:                                   # noqa: BLE001
            skip("live invoke", f"could not mint a Cognito token ({type(exc).__name__}: {exc}) "
                 "— need WORKSHOP_ANALYST_SECRET_ARN + WORKSHOP_COGNITO_CLIENT_ID and "
                 "cognito/secrets access")
    if token:
        sid = f"check-m7-{uuid.uuid4()}"                            # ≥33 chars
        print("  ⚙ POST /runtimes/…/invocations  (copilot, read-only)")
        status, chunks, raw = _invoke_runtime(
            arn, region, token,
            {"agent": "copilot", "prompt": "Reply with the single word ok."}, sid)
        check("deployed runtime answers 200", status == 200, f"http={status} {raw[:80]}")
        check("typed stream ends in a `done` chunk with a trace_id",
              any(c.get("type") == "done" and c.get("trace_id") for c in chunks),
              f"kinds={sorted({c.get('type') for c in chunks})}",
              why_fail="no `done` chunk — the runtime errored or the stream was truncated")
        check("no error chunks in the stream",
              not any(c.get("type") == "error" for c in chunks),
              str([c for c in chunks if c.get("type") == "error"])[:150])

    # 4. #4144 — the invoke step (scripts/invoke.sh 4144) — must have
    #    ROUTED TO REVIEW through the deployed runtime, because module 10's one
    #    review card IS that routing. Three facts, weakest to strongest:
    #    (a) ledger rows exist (any dispute:4144:<STATUS> key — get_case alone
    #        writes INVESTIGATING, so this only proves the case was touched);
    #    (b) the DRAFTED→REVIEW transition is in the ledger and FILED is not
    #        (an auto-file would leave module 10 with an empty queue — the seed
    #        pins #4144's evidence score to 0 so the rubric stays below τ);
    #    (c) a review_queue row for 4144 is visible to the SAME login the
    #        attendee uses in the UI. review_queue is RLS-scoped to the JWT sub,
    #        so this one read scopes itself with set_config(...) inside a single
    #        transaction — nothing is written. ANY status passes: the card is
    #        PENDING at module 9 and APPROVED/REJECTED once module 10 decides
    #        it, and attendees re-run check m9 afterwards (2026-09-27: a
    #        status='PENDING' filter here turned every post-module-10 re-run
    #        red). (%% : query() runs through psycopg's parameterizer even
    #        with no params.)
    print("  ⚙ SELECT idempotency_key FROM action_log WHERE idempotency_key LIKE 'dispute:4144:%'")
    keys = {r["idempotency_key"] for r in query(conn, """
        SELECT idempotency_key FROM action_log
        WHERE idempotency_key LIKE 'dispute:4144:%%'""")}
    statuses = sorted(k.split(":")[-1] for k in keys)
    check("#4144 investigated through your runtime (ledger rows exist)", bool(keys),
          f"statuses={statuses}" if keys else "no ledger row for case 4144",
          why_fail="no ledger row for case 4144 — run the invoke step: "
                   "scripts/invoke.sh 4144")
    check("#4144 routed to review, not auto-filed (dispute:4144:REVIEW in the ledger)",
          "dispute:4144:REVIEW" in keys and "dispute:4144:FILED" not in keys,
          f"statuses={statuses}",
          why_fail="case 4144 did not route to review — module 10 needs its PENDING card. "
                   "If it FILED, the floor's seed is stale (evidence_score must be 0 for "
                   "4144; seeds/generate_corpus.py); if only INVESTIGATING, the agent did "
                   "not submit — rerun scripts/invoke.sh 4144 and wait for draft_and_route; "
                   "if it never calls draft_and_route the deployed belt lacks it: run "
                   "cd ~/environment/app/api && scripts/catchup.sh m9 (installs the finished "
                   "belt, deploys from the CLI project, cuts over), then "
                   "~/environment/app/api/scripts/invoke.sh 4144 again")
    sub = _jwt_sub(token) if token else ""
    if not sub:
        skip("#4144 review-card check", "no Cognito token (see live invoke above)")
    else:
        print("  ⚙ set_config('app.analyst_id', <your sub>, true); "
              "SELECT status FROM review_queue WHERE kind='dispute' AND case_ref=4144")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.analyst_id', %s, true)", (sub,))
                cur.execute("""SELECT status FROM review_queue
                               WHERE kind = 'dispute' AND case_ref = 4144
                               ORDER BY item_id""")
                rows = cur.fetchall()
            conn.commit()
            card_statuses = [str(r["status"] if isinstance(r, dict) else r[0]) for r in rows]
        except Exception:
            conn.rollback()
            raise
        check("#4144 has a review card for your login (RLS-scoped read)", bool(card_statuses),
              f"status={card_statuses} for sub …{sub[-6:]}; PENDING until module 10 decides it",
              why_fail="no review_queue row for 4144 under your JWT sub — the route did not "
                       "enqueue, or it ran under a different identity (the local terminal "
                       "chat uses the analyst NAME; the invoke step uses your sub)")


def _jwt_sub(token: str) -> str:
    """The `sub` claim of an access token we minted ourselves (no signature check
    needed — it is only used to scope one read-only RLS query)."""
    import base64
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("sub", "") or ""
    except Exception:                                                # noqa: BLE001
        return ""


def m8(settings, ident, conn):
    """Observability: the human review verdicts you clicked are recorded in the
    audit log, exactly once, attributed to a validated identity. The trace/τ
    viewers and the agent re-runs are read-only, so the ONLY new rows module 8
    leaves are the approve/reject entries in action_log — that's what we assert
    (and it's what distinguishes 'did m8' from 'did m7 only').

    Deliberately NOT asserted (would false-positive after m7 alone): review_queue
    rows exist, routed_tau populated, #4127 FILED, breaks drained — all set in
    m5–m7. And review_queue itself is RLS-scoped away from this connection."""
    from investigator.db import query

    print("  ⚙ SELECT … FROM action_log WHERE action IN ('review_approve','review_reject')")
    # %% : query() runs through psycopg's parameterizer, which scans for
    # placeholders even with no params — a bare % in a LIKE is a syntax error.
    verdicts = query(conn, """
        SELECT count(*) AS rows,
               count(DISTINCT idempotency_key) AS keys,
               count(*) FILTER (WHERE actor_sub IS NOT NULL) AS attributed,
               count(*) FILTER (WHERE trace_id LIKE 'api-trace-%%') AS api_traced
        FROM action_log WHERE action IN ('review_approve', 'review_reject')""")[0]
    check("a review verdict was recorded (you worked at least one card)",
          verdicts["rows"] >= 1, f"{verdicts['rows']} verdict row(s)",
          why_fail="no review_approve/reject in action_log — approve or reject a card "
                   "in the Review queue first (this is the module-8 write)")
    if verdicts["rows"] == 0:
        return
    check("each verdict is exactly-once (rows == distinct idempotency keys)",
          verdicts["rows"] == verdicts["keys"],
          f"rows={verdicts['rows']} keys={verdicts['keys']}",
          why_fail="a review verdict double-wrote — the ON CONFLICT guard isn't holding")
    check("every verdict is attributed to a validated identity (actor_sub)",
          verdicts["attributed"] == verdicts["rows"],
          f"{verdicts['attributed']}/{verdicts['rows']} carry actor_sub")
    check("verdicts were minted by the API relay (api-trace- id)",
          verdicts["api_traced"] == verdicts["rows"],
          f"{verdicts['api_traced']}/{verdicts['rows']} api-traced",
          why_fail="a verdict's trace_id isn't api-trace-* — did it come from review_decide?")

    # the key encodes the decision: review:{item_id}:{APPROVED|REJECTED}
    keyed = query(conn, r"""
        SELECT count(*) AS n FROM action_log
        WHERE action IN ('review_approve', 'review_reject')
          AND idempotency_key ~ '^review:[0-9]+:(APPROVED|REJECTED)$'""")[0]["n"]
    check("verdict keys encode item + decision (review:<id>:<VERDICT>)",
          keyed == verdicts["rows"], f"{keyed}/{verdicts['rows']} well-formed")

    # trace-to-row: the finale's own SQL — #4127's history joins by case, and
    # every agent write on it carries a trace_id (read-only; the walked query)
    print("  ⚙ SELECT … FROM action_log JOIN disputes … WHERE case_id = 4127")
    hero = query(conn, """
        SELECT count(*) AS rows,
               count(*) FILTER (WHERE al.trace_id IS NOT NULL) AS traced
        FROM action_log al
        WHERE al.row_ref->>'case_id' = '4127'""")[0]
    check("#4127's writes are all trace-stamped (trace-to-row joins cleanly)",
          hero["rows"] > 0 and hero["traced"] == hero["rows"],
          f"{hero['traced']}/{hero['rows']} rows carry a trace_id")


def m9(settings, ident, conn):
    """Optional module 11 — the queue drain. run_queue.sh emptied the breaks
    queue through YOUR deployed runtime, and every drained break is stamped
    with exactly-once ledger rows. Requires module 9: the floor must already
    be cut over to your runtime. Read-only: every assertion counts rows in
    breaks and action_log, never writes.

    review_queue is deliberately untouched — it is RLS-scoped to the JWT sub,
    so this local-identity connection can't see the deployed agent's rows; the
    breaks table and action_log (no RLS) carry the same drain evidence."""
    from investigator.db import query

    # the breaks queue run_queue.sh drained — from breaks + action_log (no RLS)
    print("  ⚙ SELECT status, count(*) FROM breaks GROUP BY status")
    rows = query(conn, "SELECT status, count(*) AS n FROM breaks GROUP BY status")
    by_status = {r["status"]: r["n"] for r in rows}
    check("breaks queue fully drained (0 OPEN)", by_status.get("OPEN", 0) == 0,
          f"{by_status.get('OPEN', 0)} still OPEN",
          why_fail="OPEN breaks remain — run scripts/run_queue.sh to drain them")
    # terminal = RESOLVED or REVIEW, and EVERY non-OPEN row must be one of the
    # two. INVESTIGATING is a claim whose invoke never returned a verdict — a
    # dead worker, not a drained break — so one such row fails this check.
    settled = by_status.get("RESOLVED", 0) + by_status.get("REVIEW", 0)
    stuck = {st: n for st, n in by_status.items() if st not in ("OPEN", "RESOLVED", "REVIEW")}
    check("every drained break reached a terminal state (RESOLVED/REVIEW)",
          settled >= 1 and not stuck, f"{by_status}",
          why_fail="a break is still INVESTIGATING (or nothing was drained) — "
                   "re-run scripts/run_queue.sh: it requeues dead claims first, "
                   "then processes them")
    stamped = query(conn, """
        SELECT count(*) AS n,
               count(*) FILTER (WHERE confidence IS NOT NULL
                                  AND trace_id IS NOT NULL) AS stamped
        FROM breaks WHERE status <> 'OPEN'""")[0]
    check("every drained break carries confidence + trace_id",
          stamped["n"] > 0 and stamped["stamped"] == stamped["n"],
          f"{stamped['stamped']}/{stamped['n']} stamped",
          why_fail="a non-OPEN break has null confidence/trace — its agent run "
                   "ended without a verdict. Re-run scripts/run_queue.sh: it "
                   "requeues dead claims (INVESTIGATING, no confidence) and "
                   "re-processes them")
    pbr = query(conn, """
        SELECT count(*) AS rows, count(DISTINCT idempotency_key) AS keys
        FROM action_log WHERE action = 'post_break_resolution'""")[0]
    check("recon writes are exactly-once (rows == distinct idempotency keys)",
          pbr["rows"] > 0 and pbr["rows"] == pbr["keys"],
          f"rows={pbr['rows']} keys={pbr['keys']}",
          why_fail="duplicate idempotency_key — the recon write path is not deduping")
    # review routings HOLD: a break an agent routed to a human must still be
    # REVIEW unless a human verdict advanced it (caught live 2026-08-12: one
    # invocation resolved a sibling break out from under its review routing)
    overruled = query(conn, """
        SELECT DISTINCT (al.row_ref->>'break_id')::int AS break_id
        FROM action_log al
        JOIN breaks b ON b.break_id = (al.row_ref->>'break_id')::int
        WHERE al.action = 'post_break_resolution'
          AND al.idempotency_key LIKE 'break:%%:REVIEW'
          AND b.status <> 'REVIEW'
          AND NOT EXISTS (SELECT 1 FROM action_log v
                          WHERE v.action IN ('review_approve', 'review_reject')
                            AND v.row_ref->>'break_id' = al.row_ref->>'break_id')""")
    check("review routings hold (no agent overruled a human-review break)",
          not overruled, f"overruled: {[r['break_id'] for r in overruled]}",
          why_fail="a break left REVIEW without a human verdict — the write "
                   "guard must refuse agent posts on reviewed breaks")


def main() -> int:
    mod = sys.argv[1] if len(sys.argv) > 1 else ""
    stages = {"m4": m2, "m5": m3, "m6": m4, "m7": m5, "m8": m6, "m9": m7, "m10": m8,
              "m11": m9}
    if mod not in stages:
        print(f"usage: check_modules.py {' '.join(stages)}", file=sys.stderr)
        return 2
    settings, ident, ctx, conn = connect_or_skip()
    try:
        stages[mod](settings, ident, conn)
    finally:
        ctx.__exit__(None, None, None)
    if FAILURES:
        print(_colour("1;31", f"check {mod}: {len(FAILURES)} FAILING — fix and re-run"))
        if mod == "m4":
            print(f"  see exactly where your edit differs from the finished version: check.sh {mod} diff")
        elif mod in ("m5", "m6", "m7"):
            print(f"  read the failed line above: for missing or wrong code, check.sh {mod} diff shows the difference "
                  f"and scripts/solve.sh {mod} installs the finished file; for a missing case action, run the step the line names")
        return 1
    if SKIPPED:
        print(_colour("1;33", f"check {mod}: {len(SKIPPED)} UNVERIFIED — not green. Could not run: "
                      + "; ".join(SKIPPED) + ". Fix the cause named on the SKIP line(s) and re-run"))
        return 75
    print(_colour("1;32", f"check {mod}: green"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
