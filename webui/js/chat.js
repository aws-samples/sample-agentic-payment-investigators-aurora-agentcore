// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Sample code, non-production. See README.md for the full disclaimer.
// chat.js — the shared conversation renderer. Both chat surfaces (ops copilot
// dock, case-scoped "Ask the investigator") use this one function, because both
// speak the same typed-chunk stream.
//
// THE SIGNATURE ELEMENT lives here: tool chips. Every tool_start appends a
// pulsing monospace capsule (⚙ find_precedents …); its tool_end snaps it to a
// ✓ settled entry. The thread reads like a ledger of what the agent DID —
// which is the workshop's whole argument made visible.

import { chat, escapeHtml } from "./api.js";

export function makeThread(threadEl, agent, getScope) {
  let sessionId = null;

  async function send(promptText) {
    const scoped = getScope ? getScope(promptText) : promptText;

    const u = document.createElement("div");
    u.className = "msg msg-user";
    u.textContent = promptText;
    threadEl.appendChild(u);

    const chipRow = document.createElement("div");
    chipRow.className = "toolchips";
    threadEl.appendChild(chipRow);

    const a = document.createElement("div");
    a.className = "msg msg-agent";
    const textNode = document.createTextNode("");
    const cursor = document.createElement("span");
    cursor.className = "cursor";
    cursor.textContent = "▋";
    a.append(textNode, cursor);
    threadEl.appendChild(a);
    threadEl.scrollTop = threadEl.scrollHeight;

    // smart autoscroll (§8.4): only force scroll if the user is at the bottom
    const nearBottom = () =>
      threadEl.scrollHeight - threadEl.scrollTop - threadEl.clientHeight < 40;
    const scrollIf = (want) => { if (want) threadEl.scrollTop = threadEl.scrollHeight; };

    const chips = new Map();   // tool_use_id -> {el, t0, timer}
    let toolSinceText = false;  // a tool call started or ended since the last text chunk
    sessionId = await chat({ prompt: scoped, agent, sessionId }, (c) => {
      const stick = nearBottom();
      if (c.type === "text") {
        // the model's remark before a tool call and its answer after it are
        // separate text blocks with nothing between them: add a paragraph
        // break, or they render joined ("…why it was filed.Why this case…")
        if (c.text && toolSinceText) {
          const t = textNode.textContent;   // break only if the text does not already end with one
          if (t && !t.slice(t.trimEnd().length).includes("\n")) textNode.textContent += "\n\n";
          toolSinceText = false;
        }
        textNode.textContent += c.text;
      } else if (c.type === "tool_start") {
        toolSinceText = true;
        const chip = document.createElement("span");
        chip.className = "toolchip running";
        chip.innerHTML = `<span class="chip-glyph">⚙</span> ${escapeHtml(c.tool)} <span class="t"></span>`;
        // live elapsed counter (§8.2): the ledger shows cost
        const t0 = performance.now();
        const tEl = chip.querySelector(".t");
        const timer = setInterval(() => {
          tEl.textContent = `· ${((performance.now() - t0) / 1000).toFixed(1)}s`;
        }, 100);
        chips.set(c.tool_use_id, { el: chip, t0, timer });
        chipRow.appendChild(chip);
      } else if (c.type === "tool_end") {
        toolSinceText = true;
        const entry = chips.get(c.tool_use_id);
        if (entry) {
          clearInterval(entry.timer);
          const secs = ((performance.now() - entry.t0) / 1000).toFixed(1);
          entry.el.className = "toolchip done snap";        // ✓ + one-shot flash (§8.3)
          entry.el.querySelector(".chip-glyph")?.remove();
          entry.el.querySelector(".t").textContent = `· ${secs}s`;
        }
      } else if (c.type === "artifact") {
        // typed artifact side-channel (AVOID#1) — labeled block, minimal JSON tint (§8.6)
        const pre = document.createElement("pre");
        pre.className = "complaint";
        pre.innerHTML = tintJson(JSON.stringify(c.artifact, null, 2));
        threadEl.appendChild(pre);
      } else if (c.type === "error") {
        textNode.textContent += `\n[agent error] ${c.error}`;
        a.style.color = "var(--red)";
      } else if (c.type === "done") {
        if (c.text && !textNode.textContent.trim()) textNode.textContent = c.text;
      }
      scrollIf(stick);
    });
    chips.forEach((e) => clearInterval(e.timer));   // safety: no timer survives the stream
    cursor.remove();
    // models speak markdown: render the FINISHED message (streaming stays
    // plain text + cursor). renderMarkdown escapes first — model output is
    // untrusted (cardholder prose flows through it) — then whitelists a
    // minimal subset and linkifies trace ids.
    a.innerHTML = renderMarkdown(textNode.textContent);
    a.classList.add("md");
    threadEl.scrollTop = threadEl.scrollHeight;
  }

  return { send };
}

// A trace id the model wrote WITHOUT the ledger's prefix: "trace 0ada0dc3-…",
// "Trace ID: 0ada0dc3-…", "trace_id=0ada0dc3-…", "**Trace ID:** `0ada0dc3-…`",
// "API trace 0ada0dc3-…". The ledger stores trace-<uuid> and api-trace-<uuid>;
// models reword that, so the link cannot depend on the hyphen. Between the word
// and the id only separators and the tags inline() produces may sit.
// Only hex and hyphens are captured for the href (model output is untrusted).
// Keep "-" OUT of the separator class: it would match inside an existing href.
const BARE_TRACE = /\b((?:api[- ])?trace)((?:[ _-]?ids?)?(?:\s+is)?[\s:=#]*(?:<\/(?:strong|em|code)>[\s:=#]*)*)((?:<(?:code|strong|em)>)*)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b/gi;

// minimal markdown for agent replies: escape-first, then bold/italic/code,
// #### headings, -/*/1. lists, trace-id links. No library (design guardrail).
export function renderMarkdown(raw) {
  const esc = raw.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const inline = (s) => s
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(/((?:api-)?trace-[0-9a-f-]{8,})/g, '<a class="id" href="#traces/$1">$1</a>')
    .replace(BARE_TRACE, (m, word, sep, wrap, uuid) => (sep || wrap)
      ? `${word}${sep}${wrap}<a class="id" href="#traces/${word.toLowerCase().replace(" ", "-")}-${uuid.toLowerCase()}">${uuid}</a>`
      : m);
  const out = [];
  let inList = false;
  for (const line of esc.split("\n")) {
    const li = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)/);
    const h = line.match(/^\s*#{1,4}\s+(.*)/);
    if (li) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline(li[1])}</li>`);
      continue;
    }
    if (inList) { out.push("</ul>"); inList = false; }
    if (h) out.push(`<p class="md-h">${inline(h[1])}</p>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (inList) out.push("</ul>");
  return out.join("");
}

// minimal JSON tint (§8.6): keys muted, strings green@80%, numbers blue
function tintJson(s) {
  const esc = s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  return esc
    .replace(/"([^"]+)":/g, '<span class="json-k">"$1"</span>:')
    .replace(/: "([^"]*)"/g, ': <span class="json-s">"$1"</span>')
    .replace(/: (-?\d+\.?\d*)/g, ': <span class="json-n">$1</span>');
}
