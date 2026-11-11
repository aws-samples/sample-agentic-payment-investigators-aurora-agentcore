// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Sample code, non-production. See README.md for the full disclaimer.
// charts.js — native SVG charts, first-class UI components (NOT agent artifacts:
// a string-sentinel pipeline that parses charts out of agent prose is the anti-pattern here).
// Data arrives as structured API rows; rendering is hand-authored SVG in the
// settlement-terminal language; animation is CSS-driven and respects
// prefers-reduced-motion. No libraries.

const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// state → color: the SAME three-state language the chips speak (never new hues)
const STATE_COLOR = {
  settled: "var(--green)",   // FILED / RESOLVED / ACCEPTED / APPROVED
  review:  "var(--amber)",   // REVIEW / PENDING / PROPOSED
  working: "var(--blue)",    // OPEN / INVESTIGATING / DRAFTED
  denied:  "var(--red)",     // DENIED / REJECTED / ESCALATED
};
const STATE_OF = (s) => (
  ["FILED", "RESOLVED", "ACCEPTED", "APPROVED", "CLOSED"].includes(s) ? "settled" :
  ["REVIEW", "PENDING", "PROPOSED"].includes(s) ? "review" :
  ["DENIED", "REJECTED", "ESCALATED"].includes(s) ? "denied" : "working");
const STATE_LABEL = { settled: "settled", review: "awaiting review",
                      working: "in flight", denied: "denied" };
const ORDER = ["working", "review", "settled", "denied"];   // fixed order, never cycled

/** Queue-composition donut: part-to-whole of case states across both queues.
 *  Animated sweep on (re)render; live total in the center; per-arc hover. */
export function renderQueueDonut(el, rows) {
  const counts = { settled: 0, review: 0, working: 0, denied: 0 };
  for (const r of rows) counts[STATE_OF(r.status)]++;
  const total = rows.length || 1;

  const R = 52, STROKE = 14, C = 2 * Math.PI * R;
  let acc = 0;
  const arcs = ORDER.filter((k) => counts[k] > 0).map((k) => {
    const frac = counts[k] / total;
    const a = { k, n: counts[k], frac, offset: acc };
    acc += frac;
    return a;
  });

  el.innerHTML = `
    <svg viewBox="0 0 140 140" role="img"
         aria-label="queue composition: ${arcs.map((a) => `${a.n} ${STATE_LABEL[a.k]}`).join(", ")}">
      <g transform="rotate(-90 70 70)">
        ${arcs.map((a) => `
        <circle class="donut-arc" data-k="${a.k}" cx="70" cy="70" r="${R}"
          fill="none" stroke="${STATE_COLOR[a.k]}" stroke-width="${STROKE}"
          stroke-dasharray="0 ${C}"
          data-dash="${Math.max(a.frac * C - 2, 0.5)} ${C}"
          stroke-dashoffset="${-a.offset * C}"/>`).join("")}
      </g>
      <text x="70" y="66" class="donut-n" id="donut-total">0</text>
      <text x="70" y="82" class="donut-cap" id="donut-cap">cases</text>
    </svg>
    <ul class="donut-legend">
      ${arcs.map((a) => `<li><i style="background:${STATE_COLOR[a.k]}"></i>
        <span>${STATE_LABEL[a.k]}</span><b class="mono">${a.n}</b></li>`).join("")}
    </ul>`;

  // sweep-in: dasharray animates from 0 to the arc's share (CSS transition)
  requestAnimationFrame(() => {
    el.querySelectorAll(".donut-arc").forEach((c) => {
      if (REDUCED) { c.setAttribute("stroke-dasharray", c.dataset.dash); return; }
      c.style.transition = "stroke-dasharray .9s var(--ease-out)";
      requestAnimationFrame(() => c.setAttribute("stroke-dasharray", c.dataset.dash));
    });
  });

  // center count-up alongside the sweep
  const totalEl = el.querySelector("#donut-total");
  if (REDUCED) totalEl.textContent = rows.length;
  else {
    const t0 = performance.now();
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / 600);
      totalEl.textContent = Math.round(rows.length * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  // hover: legend sync + CENTER-SWAP tooltip (no floating div)
  const capEl = el.querySelector("#donut-cap");
  el.querySelectorAll(".donut-arc").forEach((c, idx) => {
    const a = arcs[idx];
    c.addEventListener("mouseenter", () => {
      el.querySelector(`.donut-legend li:nth-child(${idx + 1})`)?.classList.add("hot");
      totalEl.textContent = a.n;
      totalEl.style.fill = STATE_COLOR[a.k];
      capEl.textContent = STATE_LABEL[a.k];
    });
    c.addEventListener("mouseleave", () => {
      el.querySelectorAll(".donut-legend li").forEach((li) => li.classList.remove("hot"));
      totalEl.textContent = rows.length;
      totalEl.style.fill = "";
      capEl.textContent = "cases";
    });
    // click-to-filter: tell the view; it owns the pill + table filter
    c.addEventListener("click", () =>
      el.dispatchEvent(new CustomEvent("chart-filter", {
        bubbles: true, detail: { kind: "state", value: a.k, label: STATE_LABEL[a.k] } })));
  });
}

/** 30-day inflow sparkline: cases opened per day, derived from the queue
 *  rows' opened_at — no new API. */
export function renderInflowSpark(el, rows) {
  const days = 30, today = new Date();
  const counts = Array(days).fill(0);
  for (const r of rows) {
    const d = Math.floor((today - new Date(r.opened_at)) / 86400000);
    if (d >= 0 && d < days) counts[days - 1 - d]++;
  }
  const W = 300, H = 36, PAD = 2;
  const max = Math.max(1, ...counts);
  const x = (i) => PAD + (i * (W - PAD * 2)) / (days - 1);
  const y = (n) => H - PAD - (n / max) * (H - PAD * 2);
  const line = counts.map((n, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(n).toFixed(1)}`).join(" ");
  const area = `${line} L${x(days - 1).toFixed(1)},${H - PAD} L${x(0).toFixed(1)},${H - PAD} Z`;
  el.innerHTML = `
    <p class="view-title spark-title">INFLOW — 30D</p>
    <svg id="inflow-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
         aria-label="cases opened per day, last 30 days">
      <path d="${area}" fill="#4ade8014"/>
      <path d="${line}" fill="none" stroke="var(--text-md)" stroke-width="1.5" id="spark-line"/>
      <circle cx="${x(days - 1)}" cy="${y(counts[days - 1])}" r="2.5" fill="var(--green)"/>
    </svg>`;
  const path = el.querySelector("#spark-line");
  if (!REDUCED && path.getTotalLength) {
    const L = path.getTotalLength();
    path.style.strokeDasharray = L;
    path.style.strokeDashoffset = L;
    path.style.transition = "stroke-dashoffset .6s var(--ease-out)";
    requestAnimationFrame(() => requestAnimationFrame(() => { path.style.strokeDashoffset = "0"; }));
  }
}

/** Confidence-vs-τ: the floor's processed cases against a threshold the
 *  ATTENDEE drives. The slider IS τ — drag it and every case re-routes in
 *  real time: the line follows, bars flip amber/green, the caption prices
 *  the choice. Pure model over on-screen history; nothing executes.
 *  (An earlier iteration stacked a static default line + a run-τ line +
 *  a what-if readout — three concepts at once; ONE moving line the
 *  attendee steers is the whole calibration lesson.) */
export function renderConfidenceHist(el, values, tau = 0.7) {
  const BUCKETS = 10;
  const confs = values.filter((v) => v != null);
  const counts = Array(BUCKETS).fill(0);
  for (const v of confs) counts[Math.min(BUCKETS - 1, Math.floor(v * BUCKETS))]++;
  const max = Math.max(1, ...counts);
  const W = 300, H = 110, PAD = 6, BW = (W - PAD * 2) / BUCKETS;
  const tauX = (v) => PAD + v * (W - PAD * 2);

  el.innerHTML = `
    <svg viewBox="0 0 ${W} ${H + 18}" role="img"
         aria-label="confidence distribution of ${confs.length} processed cases; drag the slider to move tau">
      ${counts.map((n, i) => {
        const h = (n / max) * (H - 14);
        const x = PAD + i * BW;
        const auto = (i + 0.5) / BUCKETS >= tau;
        return `<rect class="hist-bar" x="${x + 1}" width="${BW - 2}"
          y="${H}" height="0" rx="2"
          data-y="${H - h}" data-h="${h}"
          fill="${auto ? "var(--green)" : "var(--amber)"}" opacity=".85">
          <title>${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}: ${n} case${n === 1 ? "" : "s"}</title>
        </rect>`;
      }).join("")}
      <line id="tau-line" x1="${tauX(tau)}" y1="4" x2="${tauX(tau)}" y2="${H}" stroke="var(--text-hi)"
            stroke-dasharray="3 3" stroke-width="1"/>
      <text id="tau-label" x="${tauX(tau)}" y="${H + 14}" class="hist-tau">τ ${tau.toFixed(2)}</text>
      <text x="${PAD}" y="${H + 14}" class="hist-end">0</text>
      <text x="${W - PAD}" y="${H + 14}" class="hist-end" text-anchor="end">1</text>
    </svg>
    <div class="tau-controls">
      <input id="tau-slider" class="whatif-slider" type="range" min="0.05" max="0.95" step="0.01"
             value="${tau}" aria-label="tau — cases re-route live as you drag">
      <input id="tau-box" class="tau-box" type="number" min="0.05" max="0.95" step="0.01"
             value="${tau.toFixed(2)}" aria-label="tau — type an exact threshold">
    </div>
    <p class="hist-caption"><i class="sw" style="background:var(--amber)"></i> routes to review
      <i class="sw" style="background:var(--green)"></i> auto-files</p>`;

  const bars = [...el.querySelectorAll(".hist-bar")];
  const line = el.querySelector("#tau-line");
  const label = el.querySelector("#tau-label");
  const cap = el.querySelector(".hist-caption");
  let capState = cap.innerHTML;

  // the slider IS τ: line, colors, and pricing all follow it. The number box
  // is the same control for exact values (dragging to precisely 0.78 is
  // fiddly); both drive applyTau and stay in sync.
  const slider = el.querySelector("#tau-slider");
  const box = el.querySelector("#tau-box");
  const applyTau = (v) => {
    v = Math.min(0.95, Math.max(0.05, v));
    bars.forEach((r, i) => r.setAttribute(
      "fill", (i + 0.5) / BUCKETS >= v ? "var(--green)" : "var(--amber)"));
    const x = tauX(v);
    line.setAttribute("x1", x); line.setAttribute("x2", x);
    label.setAttribute("x", Math.min(W - 26, Math.max(26, x)));
    label.textContent = `τ ${v.toFixed(2)}`;
    const below = confs.filter((c) => c < v).length;
    // conditional voice: the slider MODELS a τ — the review badge counts
    // real pending rows and rightly doesn't move
    capState = `<b>at τ ${v.toFixed(2)}</b>: ${below} of these ${confs.length} would have routed to review · ${confs.length - below} would have auto-filed`;
    cap.innerHTML = capState;
  };
  slider.addEventListener("input", () => {
    box.value = (+slider.value).toFixed(2);
    applyTau(+slider.value);
  });
  box.addEventListener("input", () => {
    const v = parseFloat(box.value);
    if (Number.isNaN(v)) return;
    slider.value = v;
    applyTau(v);
  });

  requestAnimationFrame(() => {
    bars.forEach((r, i) => {
      const apply = () => { r.setAttribute("y", r.dataset.y); r.setAttribute("height", r.dataset.h); };
      if (REDUCED) { apply(); return; }
      r.style.transition = `y .6s ${i * 35}ms var(--ease-out), height .6s ${i * 35}ms var(--ease-out)`;
      requestAnimationFrame(apply);
    });
    if (!REDUCED) {
      const len = H - 4;
      line.style.strokeDasharray = `${len} ${len}`;   // temp: solid for draw-in
      line.style.strokeDashoffset = len;
      line.style.transition = "stroke-dashoffset .6s .5s var(--ease-out)";
      requestAnimationFrame(() => { line.style.strokeDashoffset = "0"; });
      line.addEventListener("transitionend", () => {
        line.style.strokeDasharray = "3 3";           // settle to dashed
        line.style.transition = "";
      }, { once: true });
    }
  });

  // hover: dim siblings + bucket readout; leave restores the τ pricing
  bars.forEach((r, i) => {
    r.addEventListener("mouseenter", () => {
      bars.forEach((s) => s !== r && s.classList.add("hist-dim"));
      cap.textContent = `${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}: ${counts[i]} case${counts[i] === 1 ? "" : "s"}`;
    });
    r.addEventListener("mouseleave", () => {
      bars.forEach((s) => s.classList.remove("hist-dim"));
      cap.innerHTML = capState;
    });
    // click-to-filter (original contract): bar click scopes the tables below
    r.addEventListener("click", () =>
      el.dispatchEvent(new CustomEvent("chart-filter", {
        bubbles: true, detail: { kind: "conf", value: i, label: `conf ${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}` } })));
  });
}
