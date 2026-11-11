// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Sample code, non-production. See README.md for the full disclaimer.
// views.js — the four rail views + the case drawer. Server data only; every
// number is a DB row the participant can also reach via psql (the UI is a
// payoff, never a dependency).

import { escapeHtml, get, post } from "./api.js";
import { makeThread, renderMarkdown } from "./chat.js";
import { renderConfidenceHist, renderInflowSpark, renderQueueDonut } from "./charts.js";

const $ = (sel, el = document) => el.querySelector(sel);
const usd = (n) => n == null ? "—" : escapeHtml(n.toLocaleString("en-US", { style: "currency", currency: "USD" }));
const chip = (s) => `<span class="chip ${escapeHtml(s)}">${escapeHtml(s)}</span>`;
const TAU = 0.7;
// confidence cell: number + 36px micro-bar; amber < τ, green ≥ τ
const confCell = (v) => v == null ? "—" :
  `<span class="cellconf"><span class="confbar ${v >= TAU ? "hi" : ""}" style="width:36px"><i style="width:${Math.round(v * 100)}%"></i></span>${v.toFixed(2)}</span>`;
// the same four buckets charts.js uses — for the donut click-filter
const STATE_OF = (s) => (
  ["FILED", "RESOLVED", "ACCEPTED", "APPROVED", "CLOSED"].includes(s) ? "settled" :
  ["REVIEW", "PENDING", "PROPOSED"].includes(s) ? "review" :
  ["DENIED", "REJECTED", "ESCALATED"].includes(s) ? "denied" : "working");

/* ── queue ──────────────────────────────────────────────────────────────── */
export async function renderQueue(main, openCase) {
  const { disputes, breaks } = await get("/api/queues");
  main.innerHTML = `
    <div class="floorband">
      <div class="floorcard">
        <p class="view-title">FLOOR COMPOSITION</p>
        <div id="donut" class="donut-wrap"></div>
        <div id="inflow"></div>
      </div>
      <div class="floorcard">
        <p class="view-title">CONFIDENCE vs τ — where processed cases landed</p>
        <div id="conf-hist"></div>
      </div>
    </div>
    <button id="filter-pill" class="filter-pill hidden"></button>
    <p class="view-title">DISPUTES — post-flag investigation queue</p>
    <table class="ledger"><thead><tr>
      <th>CASE</th><th>NETWORK</th><th>REASON</th><th style="text-align:right">AMOUNT</th>
      <th>STATUS</th><th style="text-align:right">CONF</th><th>OPENED</th>
    </tr></thead><tbody id="tb-disputes"></tbody></table>
    <p class="view-title" style="margin-top:26px">RECONCILIATION BREAKS — residual queue</p>
    <table class="ledger"><thead><tr>
      <th>BREAK</th><th>TYPE</th><th>STATUS</th><th style="text-align:right">CONF</th>
      <th>PROPOSED ACTION</th><th>OPENED</th>
    </tr></thead><tbody id="tb-breaks"></tbody></table>`;
  // one render pass, reusable under chart filters (click-to-filter)
  const renderRows = (filter) => {
    const match = (r) => !filter ? true :
      filter.kind === "state" ? STATE_OF(r.status) === filter.value :
      r.confidence != null && Math.min(9, Math.floor(r.confidence * 10)) === filter.value;
    const tbD = $("#tb-disputes", main);
    tbD.innerHTML = "";
    disputes.filter(match).forEach((d, i) => {
      const tr = document.createElement("tr");
      if (i < 12) { tr.classList.add("row-in"); tr.style.animationDelay = `${i * 25}ms`; }
      tr.dataset.ref = `case:${d.case_id}`;
      tr.innerHTML = `
        <td class="id">#${d.case_id}</td><td>${escapeHtml(d.network)}</td>
        <td class="mono">${escapeHtml(d.reason_code)}</td><td class="num">${usd(d.amount)}</td>
        <td>${chip(d.status)}</td>
        <td class="num">${confCell(d.confidence)}</td>
        <td class="mono">${escapeHtml(d.opened_at.slice(0, 10))}</td>`;
      wireRowOpen(tr, main, () => openCase("case", d.case_id));
      tbD.appendChild(tr);
    });
    const tbB = $("#tb-breaks", main);
    tbB.innerHTML = "";
    breaks.filter(match).forEach((b, i) => {
      const tr = document.createElement("tr");
      if (i < 12) { tr.classList.add("row-in"); tr.style.animationDelay = `${i * 25}ms`; }
      tr.dataset.ref = `break:${b.break_id}`;
      tr.innerHTML = `
        <td class="id">#${b.break_id}</td><td class="mono">${escapeHtml(b.break_type)}</td>
        <td>${chip(b.status)}</td>
        <td class="num">${confCell(b.confidence)}</td>
        <td>${escapeHtml(b.proposed_action) || "—"}</td>
        <td class="mono">${escapeHtml(b.opened_at.slice(0, 10))}</td>`;
      wireRowOpen(tr, main, () => openCase("break", b.break_id));
      tbB.appendChild(tr);
    });
  };
  renderRows(null);

  // chart click-to-filter: amber pill above the table; click clears
  let activeFilter = null;
  main.addEventListener("chart-filter", (e) => {
    const pill = $("#filter-pill", main);
    if (activeFilter && activeFilter.kind === e.detail.kind && activeFilter.value === e.detail.value) {
      activeFilter = null; pill.classList.add("hidden"); renderRows(null); return;
    }
    activeFilter = e.detail;
    pill.textContent = `filtered: ${e.detail.label} ×`;
    pill.classList.remove("hidden");
    renderRows(activeFilter);
  });
  $("#filter-pill", main).addEventListener("click", () => {
    activeFilter = null; $("#filter-pill", main).classList.add("hidden"); renderRows(null);
  });

  // the floor band: composition donut over BOTH queues + confidence-vs-τ
  renderQueueDonut(main.querySelector("#donut"), [...disputes, ...breaks]);
  renderConfidenceHist(
    main.querySelector("#conf-hist"),
    [...disputes, ...breaks].map((r) => r.confidence).filter((v) => v != null));
  renderInflowSpark(main.querySelector("#inflow"), [...disputes, ...breaks]);
}

// selected-row accent: one selected row at a time
function selectRow(main, tr) {
  main.querySelectorAll("tr.selected").forEach((r) => r.classList.remove("selected"));
  tr.classList.add("selected");
}

// row activation (keyboard nav): click OR focus+Enter opens the drawer
function wireRowOpen(tr, main, open) {
  tr.tabIndex = 0;
  const go = () => { selectRow(main, tr); open(); };
  tr.addEventListener("click", go);
  tr.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); go(); }
  });
}

// drawer leave: reverse-out over --t-fast, THEN hide. The hide
// keys on animationend filtered by name — a close cancelled by an immediate
// reopen fires animationcancel instead, so it can never hide the new panel.
function closePanel(panel) {
  if (panel.classList.contains("hidden") || panel.classList.contains("panel-out")) return;
  if (!panel.dataset.outWired) {
    panel.dataset.outWired = "1";
    panel.addEventListener("animationend", (e) => {
      if (e.animationName !== "panel-out") return;
      panel.classList.add("hidden");
      panel.classList.remove("panel-out");
    });
  }
  panel.classList.remove("panel-in");
  panel.classList.add("panel-out");
  // selected-row accent clears with the drawer
  document.querySelectorAll(".ledger tr.selected").forEach((r) => r.classList.remove("selected"));
}

/* ── case drawer + "Ask the investigator" (case-SCOPED chat) ────────────── */
export async function renderCasePanel(panel, kind, id) {
  const firstOpen = panel.classList.contains("hidden") || panel.classList.contains("panel-out");
  panel.classList.remove("hidden", "panel-out");   // reopen mid-close cancels the leave
  if (firstOpen) {            // slide, don't pop — re-renders stay put
    panel.classList.remove("panel-in"); void panel.offsetWidth;
    panel.classList.add("panel-in");
  }
  // skeleton shimmer shaped like the kv grid — no layout jump
  panel.innerHTML = `<div class="panel-head"><h2>loading…</h2>
    <button class="ghost" id="panel-close" aria-label="close">×</button></div>
    <div class="panel-body"><div class="skel"><i></i><i></i><i></i><i></i><i></i></div></div>`;
  $("#panel-close", panel).addEventListener("click", () => closePanel(panel));

  let head, body, scopeText, agent, panelTrace;
  // the ask rides WITH the record: the same action_log rows the trace viewer
  // shows, folded into the scope — provenance questions ("why did you file
  // this?") get answered from the ledger, not from a fresh invocation's
  // (empty) memory. System-minted fields only; drafts stay out of prompts.
  const recordBlock = (rec) => rec?.length
    ? ` Its ledger record (action_log, the system of record):\n${rec.map(
        (r) => `${r.created_at.slice(0, 19)} ${r.action} ${r.trace_id}`).join("\n")}\nAnswer from this record plus your tools. `
    : " ";
  if (kind === "case") {
    const { case: c, cardholder_history: hist, record } = await get(`/api/case/${id}`);
    head = `Case #${c.case_id} ${chip(c.status)}`;
    agent = "dispute";
    scopeText = `Regarding dispute case ${c.case_id}.${recordBlock(record)}`;
    panelTrace = c.trace_id;
    body = `
      <dl class="kv">
        <dt>network / reason</dt><dd class="mono">${escapeHtml(c.network)} · ${escapeHtml(c.reason_code)}</dd>
        <dt>amount</dt><dd class="mono">${usd(c.amount)}</dd>
        <dt>cardholder</dt><dd class="mono">${escapeHtml(c.cardholder_id)}</dd>
        <dt>descriptor</dt><dd>${escapeHtml(c.descriptor)}</dd>
        <dt>evidence score</dt><dd class="mono"><span class="pips">${[1,2,3].map(n =>
          `<i class="${n <= c.evidence_score ? "on" : ""}"></i>`).join("")}</span>${c.evidence_score} / 3</dd>
        <dt>confidence</dt><dd class="mono">${c.confidence != null ? c.confidence.toFixed(3) : "—"}</dd>
        <dt>trace</dt><dd>${c.trace_id ? `<a class="id" href="#traces/${escapeHtml(c.trace_id)}">${escapeHtml(c.trace_id.slice(0, 22))}…</a>` : "—"}</dd>
      </dl>
      <div class="complaint ${["REVIEW","PENDING"].includes(c.status) ? "state-review" : ""}">“${escapeHtml(c.complaint_text)}”</div>
      <p class="view-title">CARDHOLDER HISTORY (${hist.length} txns — CE 3.0 raw material)</p>
      ${c.draft_text ? `<p class="view-title">CURRENT DRAFT</p><div class="review-card"><div class="draft">${renderMarkdown(c.draft_text)}</div></div>` : ""}`;
  } else {
    const { break: b, record } = await get(`/api/break/${id}`);
    head = `Break #${b.break_id} ${chip(b.status)}`;
    agent = "recon";
    scopeText = `Regarding reconciliation break ${b.break_id}.${recordBlock(record)}`;
    panelTrace = b.trace_id;
    const gap = b.ledger_amount != null && b.line_amount != null
      ? (b.ledger_amount - b.line_amount) : null;
    body = `
      <dl class="kv">
        <dt>type</dt><dd class="mono">${escapeHtml(b.break_type)}</dd>
        <dt>ledger</dt><dd class="mono">${usd(b.ledger_amount)} · ${escapeHtml(b.ledger_ref) || "—"}</dd>
        <dt>statement</dt><dd class="mono">${usd(b.line_amount)} · ${escapeHtml(b.ref_raw) || "—"}</dd>
        <dt>gap</dt><dd class="mono">${gap != null ? usd(gap) : "—"}</dd>
        <dt>proposed</dt><dd>${escapeHtml(b.proposed_action) || "—"}</dd>
        <dt>trace</dt><dd>${b.trace_id ? `<a class="id" href="#traces/${escapeHtml(b.trace_id)}">${escapeHtml(b.trace_id.slice(0, 22))}…</a>` : "—"}</dd>
      </dl>
      ${b.narration ? `<div class="complaint">“${escapeHtml(b.narration)}”</div>` : ""}`;
  }

  // "Ask the investigator" gates on trace_id: it exists to EXPLAIN work an
  // investigator already did. On an untouched row the chat would instead
  // TRIGGER the investigation (get_case writes OPEN→INVESTIGATING; a "please
  // investigate" could run the whole flow) — the doors-open tour must not be
  // able to disturb module state (found live 2026-07-07: a tour question
  // flipped #4127 to INVESTIGATING).
  panel.innerHTML = `
    <div class="panel-head"><h2>${head}</h2>
      <button class="ghost" id="panel-close" aria-label="close">×</button></div>
    <div class="panel-body">${body}</div>
    <div class="dock-head" style="border-top:1px solid var(--rule)">
      <span><span class="chip-glyph agent">◈</span> Ask the investigator</span></div>
    ${panelTrace ? `
    <div class="thread" id="case-thread"></div>
    <form class="composer" id="case-form">
      <input type="text" placeholder="Why did you route this the way you did?"
             aria-label="ask the investigator" autocomplete="off">
      <button type="submit">Ask</button>
    </form>` : `
    <div class="empty" style="padding:14px 16px">No investigation to explain yet.
      This chat opens once an investigator has worked the case — its answers
      come from the trace it leaves behind.</div>`}
  `;
  $("#panel-close", panel).addEventListener("click", () => closePanel(panel));
  panel.querySelectorAll(".kv dd").forEach((dd, i) => { dd.style.animationDelay = `${i * 20}ms`; });

  if (panelTrace) {
    // case-SCOPED conversation: the clicked case IS the scope — no classifier
    const thread = makeThread($("#case-thread", panel), agent, (q) => scopeText + q);
    $("#case-form", panel).addEventListener("submit", (e) => {
      e.preventDefault();
      const input = $("input", e.target);
      if (input.value.trim()) { thread.send(input.value.trim()); input.value = ""; }
    });
  }
}

/* ── review (the M3/M4 payoff tab) ──────────────────────────────────────── */
export async function renderReview(main, refresh) {
  const { items } = await get("/api/review");
  if (!items.length) {
    main.innerHTML = `<p class="view-title">REVIEW QUEUE — your low-confidence drafts</p>
      <div class="empty"><span class="empty-glyph">◈</span><b>Nothing awaiting your judgment.</b>
      Drafts land here when the investigator's confidence falls below τ.
      <span class="hint">your agent routes its first low-confidence draft here in module 5</span></div>`;
    return;
  }
  main.innerHTML = `<p class="view-title">REVIEW QUEUE — routed to YOU because confidence &lt; τ
      <span class="kbd-hint"><b>a</b> approve · <b>r</b> reject</span></p>
    <div id="cards"></div>`;
  const cards = $("#cards", main);
  for (const it of items) {
    const el = document.createElement("div");
    el.className = "review-card card-in";
    el.style.animationDelay = `${Math.min(items.indexOf(it), 10) * 40}ms`;
    el.style.maxHeight = "400px";   // measured ceiling → the leave transition has a from-value
    const pct = Math.round(it.confidence * 100);
    el.innerHTML = `
      <div class="review-meta">
        <span class="id">${escapeHtml(it.kind)} #${it.case_ref}</span>
        ${chip(it.status)}
        <div class="confbar ${it.confidence >= (it.routed_tau ?? 0.7) ? "hi" : ""}"
             style="--tau-pos:${Math.round((it.routed_tau ?? 0.7) * 100)}%"
             title="routed at τ ${(it.routed_tau ?? 0.7).toFixed(2)}"><i style="width:0%" data-pct="${pct}"></i></div>
        <span class="mono">${it.confidence.toFixed(3)}</span>
        <span>${escapeHtml(it.created_at.slice(0, 16).replace("T", " "))}</span>
      </div>
      <div class="draft">${renderMarkdown(it.draft)}</div>
      ${it.status === "PENDING" ? `
      <div class="review-actions">
        <button class="btn-approve">Approve</button>
        <button class="btn-reject">Reject</button>
      </div>` : ""}`;
    if (it.status === "PENDING") {
      const decide = async (verdict, btn) => {
        // confirm IN PLACE — no view teardown, no blank flash. Sequence:
        // buttons lock → verdict lands on the card → card departs → strip
        // catches up. The click's outcome is visible where the click happened.
        const actions = $(".review-actions", el);
        actions.querySelectorAll("button").forEach((b) => (b.disabled = true));
        btn.textContent = verdict === "approve" ? "Approving…" : "Rejecting…";
        try {
          await post(`/api/review/${it.item_id}/${verdict}`);
        } catch (e) {
          btn.textContent = verdict === "approve" ? "Approve" : "Reject";
          actions.querySelectorAll("button").forEach((b) => (b.disabled = false));
          el.classList.add("card-error");
          actions.insertAdjacentHTML("beforeend",
            `<span class="card-note error">Didn't go through (${escapeHtml(e.message)}) — try again.</span>`);
          setTimeout(() => { $(".card-note", el)?.remove(); el.classList.remove("card-error"); }, 4000);
          return;
        }
        // verdict lands: chip + card border flip to the outcome color
        const ok = verdict === "approve";
        el.classList.add(ok ? "card-approved" : "card-rejected");
        $(".chip", el).outerHTML = chip(ok ? "APPROVED" : "REJECTED");
        // the click RECORDS an approval — review_queue flips to APPROVED and a
        // review_approve row lands in action_log. Nothing is filed or posted
        // downstream by this button, so the confirmation says exactly that for
        // both kinds (a dispute card claiming "filed" would overstate it)
        const filed = "approval recorded";
        actions.innerHTML = `<span class="card-note ${ok ? "good" : "bad"}">
          ${ok ? `✓ Approved — ${filed}` : "✕ Rejected — recorded"}</span>`;
        window.dispatchEvent(new CustomEvent("floor-changed"));   // strip updates live
        // depart after the confirmation has had its moment
        setTimeout(() => {
          el.classList.add("card-leave");
          el.addEventListener("transitionend", () => {
            el.remove();
            if (!cards.children.length) refresh();   // last card → the empty state
          }, { once: true });
        }, 1400);
      };
      $(".btn-approve", el).addEventListener("click", (e) => decide("approve", e.target));
      $(".btn-reject", el).addEventListener("click", (e) => decide("reject", e.target));
    }
    cards.appendChild(el);
  }
  // confbar fill-in, 200ms after cards land
  setTimeout(() => {
    cards.querySelectorAll(".confbar i[data-pct]").forEach((i) => { i.style.width = i.dataset.pct + "%"; });
  }, 200);
  // keyboard: a approve / r reject on the TOP pending card
  const onKey = (e) => {
    if (e.target.matches("input, textarea")) return;
    // bare keys only: Cmd+R (reload) carries key==="r" — without this guard
    // every page refresh REJECTED the top card.
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key !== "a" && e.key !== "r") return;
    const top = cards.querySelector(".review-card .btn-approve");
    if (!top) return;
    const card = top.closest(".review-card");
    (e.key === "a" ? card.querySelector(".btn-approve") : card.querySelector(".btn-reject"))?.click();
  };
  document.addEventListener("keydown", onKey);
  // view teardown removes the listener (router replaces main's content)
  new MutationObserver((_, obs) => {
    if (!document.contains(cards)) { document.removeEventListener("keydown", onKey); obs.disconnect(); }
  }).observe(main, { childList: true });
}

/* ── traces (M5: trace-to-row) ──────────────────────────────────────────── */
export async function renderTraces(main, traceId) {
  main.innerHTML = `
    <p class="view-title">TRACE → ROW — “why did the agent do this?”, answered from the ledger</p>
    <div class="trace-input">
      <input id="trace-q" placeholder="trace-…" value="${escapeHtml(traceId)}"
             aria-label="trace id">
      <button class="btn-approve" id="trace-go">Look up</button>
    </div>
    <div id="trace-out" class="trace-tree"></div>`;
  const out = $("#trace-out", main);
  async function lookup(id) {
    if (!id) return;
    const { actions } = await get(`/api/trace/${encodeURIComponent(id)}`);
    out.innerHTML = actions.length ? "" :
      `<div class="empty"><b>No ledger rows for that trace.</b>
       Every agent write stamps its trace_id into action_log — run a case in M3,
       copy its trace id from the terminal, and look it up here.</div>`;
    for (const a of actions) {
      const row = document.createElement("div");
      row.className = "trace-row";
      row.innerHTML = `
        <span class="tick">✓</span>
        <span><b>${escapeHtml(a.action)}</b> <span class="row-ref">as ${escapeHtml(a.actor_sub)}</span><br>
          <span class="row-ref">${a.row_ref ? escapeHtml(JSON.stringify(a.row_ref)) : ""}</span></span>
        <span class="when">${escapeHtml(a.created_at.slice(11, 19))}</span>`;
      out.appendChild(row);
    }
  }
  $("#trace-go", main).addEventListener("click", () => lookup($("#trace-q", main).value.trim()));
  $("#trace-q", main).addEventListener("keydown", (e) => {
    if (e.key === "Enter") lookup(e.target.value.trim());
  });
  if (traceId) lookup(traceId);
}

/* ── corpus inspector ───────────────────────────────────────────────────── */
export async function renderCorpus(main) {
  main.innerHTML = `
    <p class="view-title">CORPUS INSPECTOR — precedent hits & confidence spread
      <span style="float:right">terminal path: <span class="mono">make compare-retrieval</span></span></p>
    <div class="trace-input">
      <input id="corpus-case" placeholder="case id (e.g. 4127)" value="4127" aria-label="case id">
      <button class="btn-approve" id="corpus-go">Inspect</button>
    </div>
    <div class="corpus-grid">
      <div class="hitlist" id="hits"></div>
      <div>
        <p class="view-title">CONFIDENCE SPREAD (corpus-wide)</p>
        <div class="sparkbars" id="spark"></div>
        <p class="view-title" style="margin-top:4px">bars right of τ auto-file; left routes to review</p>
      </div>
    </div>`;
  async function inspect(caseId) {
    const { precedents, confidence_spread } = await get(`/api/inspector/${caseId}`);
    const hits = $("#hits", main);
    hits.innerHTML = `<p class="view-title">TOP PRECEDENTS — this case's bucket, ranked from the corpus anchor (not this case's text)</p>`;
    for (const p of precedents) {
      const el = document.createElement("div");
      el.className = "hit";
      el.innerHTML = `<span class="cos">${p.cosine.toFixed(3)}</span>
        <span><span class="id">#${p.precedent_id}</span>
        <span class="row-ref">${escapeHtml(p.analyst_id)}</span><br>${escapeHtml(p.complaint)}</span>`;
      hits.appendChild(el);
    }
    const spark = $("#spark", main);
    spark.innerHTML = "";
    const max = Math.max(1, ...confidence_spread.map((b) => b.n));
    for (let i = 1; i <= 10; i++) {
      const b = confidence_spread.find((x) => x.bucket === i);
      const bar = document.createElement("i");
      bar.style.height = `${((b?.n || 0) / max) * 100}%`;
      bar.style.animationDelay = `${(i - 1) * 30}ms`;   // staggered rise
      if (i > 7) bar.classList.add("tau-right");   // τ=0.7 default
      bar.title = `${(i - 1) / 10}–${i / 10}: ${b?.n || 0}`;
      spark.appendChild(bar);
    }
    // τ divider between bucket 7 and 8, matching the histogram's language
    const rule = document.createElement("span");
    rule.className = "tau-rule";
    rule.style.left = "70%";
    spark.appendChild(rule);
  }
  $("#corpus-go", main).addEventListener("click", () => inspect($("#corpus-case", main).value.trim()));
  inspect(4127);
}

