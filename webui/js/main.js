// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Sample code, non-production. See README.md for the full disclaimer.
// main.js — router + status strip + copilot dock. No framework: four views,
// one drawer, one dock — a build this size carries no bundler weight
// (plain ES modules).

import { escapeHtml, get } from "./api.js";
import { signIn, signOut, storedCreds, whoami } from "./auth.js";
import { makeThread } from "./chat.js";
import { initTape } from "./tape.js";
import { renderCasePanel, renderCorpus, renderQueue, renderReview, renderTraces } from "./views.js";

const $ = (sel) => document.querySelector(sel);
const main = $("#main");
const panel = $("#case-panel");

/* ── router (hash-based; #view or #traces/<id>) ─────────────────────────── */
const views = new Map([
  ["queue",  () => renderQueue(main, (kind, id) => renderCasePanel(panel, kind, id))],
  ["review", () => renderReview(main, () => route())],
  ["traces", (arg) => renderTraces(main, arg)],
  ["corpus", () => renderCorpus(main)],
]);

async function route() {
  const [name, arg] = (location.hash.slice(1) || "queue").split("/");
  const view = views.has(name) ? name : "queue";
  document.querySelectorAll(".rail-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  main.innerHTML = `<div class="empty">loading…</div>`;
  try {
    await views.get(view)(arg);
  } catch (e) {
    main.innerHTML = `<div class="empty"><b>Couldn't load this view.</b>${escapeHtml(e.message)}<br>
      Check that the api service is up (make smoke covers it).</div>`;
  }
}
window.addEventListener("hashchange", route);
document.querySelectorAll(".rail-tab").forEach((b) =>
  b.addEventListener("click", () => { location.hash = b.dataset.view; }));

/* ── status strip: the floor totals (poll; cheap counts) ────────────────── */
// count-up tick (§3.1): animate old→new over --t-slow; only on CHANGE.
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function tickNumber(el, to, flashDir) {
  const from = parseInt(el.textContent, 10);
  if (Number.isNaN(from) || from === to || REDUCED) { el.textContent = to; return; }
  const t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / 600);
    el.textContent = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
  if (flashDir) {
    const cls = flashDir > 0 ? "strip-flash-up" : "strip-flash-down";
    el.classList.remove("strip-flash-up", "strip-flash-down");
    void el.offsetWidth;   // restart the animation
    el.classList.add(cls);
    setTimeout(() => el.classList.remove(cls), 1000);
  }
}

async function strip() {
  try {
    const { disputes, breaks } = await get("/api/queues");
    const { items } = await get("/api/review");
    const open = (rows, terminal) => rows.filter((r) => !terminal.includes(r.status)).length;
    tickNumber($("#st-disputes"), open(disputes, ["ACCEPTED", "DENIED", "CLOSED"]));
    tickNumber($("#st-breaks"), open(breaks, ["RESOLVED", "ESCALATED"]));
    const pending = items.filter((i) => i.status === "PENDING").length;
    const prev = parseInt($("#st-review").textContent, 10) || 0;
    tickNumber($("#st-review"), pending, Math.sign(pending - prev));
    const badge = $("#review-badge");
    if (pending > prev) {   // badge pop only on increase (§3.2)
      badge.classList.remove("badge-pop"); void badge.offsetWidth;
      badge.classList.add("badge-pop");
    }
    badge.textContent = pending;
    badge.classList.toggle("hidden", pending === 0);
    $("#runtime-dot").className = "dot dot-live";
  } catch {
    $("#runtime-dot").className = "dot dot-err";
  }
}

/* ── copilot dock (the GLOBAL surface — §4 mandate) ─────────────────────── */
const dock = $("#copilot-dock");
const toggle = $("#copilot-toggle");
// leave = reverse-out over --t-fast, THEN hide (§1 leave rule). animationend is
// filtered by name; a reopen mid-close cancels the animation and never hides.
dock.addEventListener("animationend", (e) => {
  if (e.animationName !== "dock-out") return;
  dock.classList.add("hidden");
  dock.classList.remove("dock-out");
});
function openDock() {
  dock.classList.remove("hidden", "dock-out");
  toggle.setAttribute("aria-expanded", "true");
  $("#copilot-input").focus();
}
function closeDock() {
  if (dock.classList.contains("hidden") || dock.classList.contains("dock-out")) return;
  dock.classList.add("dock-out");
  toggle.setAttribute("aria-expanded", "false");
}
toggle.addEventListener("click", () => {
  (dock.classList.contains("hidden") || dock.classList.contains("dock-out"))
    ? openDock() : closeDock();
});
$("#copilot-close").addEventListener("click", closeDock);
const copilotThread = makeThread($("#copilot-thread"), "copilot");
$("#copilot-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#copilot-input");
  if (input.value.trim()) { copilotThread.send(input.value.trim()); input.value = ""; }
});

/* ── boot: login gate first, floor second ───────────────────────────────── */
async function bootFloor() {
  const gate = $("#login-gate");
  if (!gate.classList.contains("hidden")) {
    gate.style.opacity = "0";                    // fade (CSS transition), then hide
    setTimeout(() => { gate.classList.add("hidden"); gate.style.opacity = ""; }, 260);
  }
  document.body.classList.add("booted");         // masthead + strip slide in
  const { username } = await whoami();
  $("#analyst-name").textContent = username;
  await route();
  strip();
  setInterval(strip, 15000);
  window.addEventListener("floor-changed", strip);
  initTape();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#login-err");
  err.textContent = "";
  const btn = e.target.querySelector("button");
  btn.disabled = true; btn.textContent = "Signing in"; btn.classList.add("pending");
  try {
    await signIn($("#login-user").value.trim(), $("#login-pass").value);
    await bootFloor();
  } catch (ex) {
    err.textContent = `Sign-in failed: ${ex.message}`;
    btn.disabled = false; btn.textContent = "Sign in to the floor"; btn.classList.remove("pending");
  }
});

$("#analyst-name").addEventListener("dblclick", () => signOut());  // facilitator escape hatch

(async () => {
  if (storedCreds()) {
    try { await bootFloor(); return; } catch { /* stale creds → gate */ }
  }
  $("#login-gate").classList.remove("hidden");
  // pre-fill from the URL FRAGMENT (never sent to the server / never logged):
  // the stack's WebUIUrl output carries #user=…&pw=… so attendees land with
  // credentials filled in — they still perform the sign-in (authentic ritual)
  const frag = new URLSearchParams(location.hash.slice(1));
  if (frag.get("user")) $("#login-user").value = frag.get("user");
  if (frag.get("pw")) $("#login-pass").value = frag.get("pw");
  history.replaceState(null, "", location.pathname);   // scrub from the address bar
  (frag.get("pw") ? $("#login-form button") : $("#login-user")).focus();
})();
