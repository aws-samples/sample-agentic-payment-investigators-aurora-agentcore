// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Sample code, non-production. See README.md for the full disclaimer.
// tape.js — the settlement tape: a bottom-docked, ledger-derived activity
// strip. Every line IS an action_log row (writes only — reads leave no tape,
// which is module 5's exactly-once design made ambient). Collapsed: the
// latest event on one line. Expanded: the scrolling feed. Every line links
// to its trace (#traces/<id>) — trace-to-row, one click from anywhere.
//
// Polling is incremental (since_id): collapsed rides the strip's 15s cadence,
// expanded tightens to 3s. Nothing here writes; the tape cannot disturb state.

import { escapeHtml, get } from "./api.js";

const $ = (sel, el = document) => el.querySelector(sel);

// action -> the verb the tape prints (fallback: the raw action string)
const VERBS = {
  file_representment: "filed representment",
  post_break_resolution: "posted resolution",
  review_approve: "approved ✓",
  review_reject: "rejected ✕",
};
const verbFor = (a) =>
  VERBS[a.action] ||
  (a.action.startsWith("set_status:")
    ? `→ ${a.action.slice("set_status:".length)}`
    : a.action.replace(/_/g, " "));

// who did it: agents are named by the queue they work (the cast table's
// mapping); a review verdict is the HUMAN gate — the analyst, not an agent
const actorFor = (a) =>
  a.action.startsWith("review_") ? "analyst (you)" :
  a.ref_table === "breaks" ? "ReconInvestigator" : "DisputeInvestigator";

const stateClass = (a) => {
  if (a.action === "review_approve") return "good";
  if (a.action === "review_reject") return "bad";
  const s = a.dispute_status || a.break_status || "";
  if (["FILED", "RESOLVED", "ACCEPTED", "APPROVED", "CLOSED"].includes(s)) return "good";
  if (["REVIEW", "PENDING", "PROPOSED"].includes(s)) return "warn";
  if (["DENIED", "REJECTED", "ESCALATED"].includes(s)) return "bad";
  return "";
};

const refFor = (a) =>
  a.ref_table === "breaks" || a.ref_kind === "break"
    ? `break ${a.ref_id}` : `case ${a.ref_id}`;

const timeFor = (iso) => (iso || "").slice(11, 19);

function lineEl(a, isNew) {
  const li = document.createElement("div");
  li.className = `tape-line ${stateClass(a)}` + (isNew ? " tape-in" : "");
  li.innerHTML = `
    <span class="t-time">${escapeHtml(timeFor(a.created_at))}</span>
    <span class="t-agent">${actorFor(a)}</span>
    <span class="t-ref">${escapeHtml(refFor(a))}</span>
    <span class="t-verb">${escapeHtml(verbFor(a))}</span>
    ${a.trace_id ? `<a class="id t-trace" href="#traces/${escapeHtml(a.trace_id)}">${escapeHtml(a.trace_id.slice(0, 18))}…</a>` : ""}`;
  return li;
}

export function initTape() {
  const tape = $("#tape");
  const feed = $("#tape-feed");
  const latest = $("#tape-latest");
  const toggle = $("#tape-toggle");
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let sinceId = 0;
  let expanded = false;
  let timer = null;
  let count = 0;
  let inflight = false;  // one poll at a time: the interval, expand, and
  let rerun = false;     // floor-changed can collide; a collision re-runs once
  const seen = new Set(); // action_ids already rendered — no line prints twice

  async function poll() {
    if (inflight) { rerun = true; return; }
    inflight = true;
    try {
      let events;
      try {
        // limit=100 (the server's cap) so the burstiest beat — the module-9
        // queue drain — lands in one page and no event falls in a gap
        ({ events } = await get(`/api/activity?since_id=${sinceId}&limit=100`));
      } catch {
        return; // strip's runtime dot already signals API health; the tape stays quiet
      }
      const fresh = events.filter((e) => !seen.has(e.action_id));
      if (!fresh.length) return;
      for (const e of fresh) seen.add(e.action_id);
      sinceId = Math.max(sinceId, ...fresh.map((e) => e.action_id));
      count += fresh.length;

      // newest-first from the API; the feed renders newest on top
      for (const a of [...fresh].reverse()) {
        feed.prepend(lineEl(a, !REDUCED));
      }
      while (feed.children.length > 200) feed.lastChild.remove();

      // collapsed summary: the latest event, one line
      const top = fresh[0];
      latest.innerHTML = "";
      latest.appendChild(lineEl(top, false));
      $("#tape-count").textContent = count;
      $("#tape-count").classList.remove("hidden");
      $("#tape-empty")?.remove();

      if (!expanded && !REDUCED) {   // pulse the collapsed bar on change only
        tape.classList.remove("tape-blip"); void tape.offsetWidth;
        tape.classList.add("tape-blip");
      }
    } finally {
      inflight = false;
      if (rerun) { rerun = false; poll(); }
    }
  }

  function schedule() {
    clearInterval(timer);
    timer = setInterval(poll, expanded ? 3000 : 15000);
  }

  toggle.addEventListener("click", () => {
    expanded = !expanded;
    tape.classList.toggle("open", expanded);
    toggle.setAttribute("aria-expanded", String(expanded));
    schedule();
    if (expanded) poll();
  });

  poll();
  schedule();
  window.addEventListener("floor-changed", poll);   // approvals reflect instantly
}
