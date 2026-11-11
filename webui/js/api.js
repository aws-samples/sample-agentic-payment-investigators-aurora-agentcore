// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// Sample code, non-production. See README.md for the full disclaimer.
// api.js — fetch + SSE against the bridge (api/app.py). Typed chunks only —
// nothing in this UI ever parses model prose.

import { getToken } from "./auth.js";

const base = window.WS_CONFIG.apiBase || "";

/** HTML-escape any server-, URL- or model-sourced value before it lands in
 * innerHTML (text OR attribute context — quotes are covered). Shared by every
 * view module; null/undefined render as "". */
export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// CloudFront OAC (Lambda origin) requires the VIEWER to send the payload
// hash on POST/PUT; the empty-body hash is the SHA-256 of "".
async function bodySha256(text) {
  const data = new TextEncoder().encode(text || "");
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
}

export async function get(path) {
  const r = await fetch(base + path, {
    headers: { "x-ws-auth": `Bearer ${await getToken()}` /* CloudFront OAC owns Authorization */ },
  });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

export async function post(path, body) {
  const payload = body ? JSON.stringify(body) : "";
  const r = await fetch(base + path, {
    method: "POST",
    headers: {
      "x-ws-auth": `Bearer ${await getToken()}` /* CloudFront OAC owns Authorization */,
      "Content-Type": "application/json",
      "x-amz-content-sha256": await bodySha256(payload),
    },
    body: payload || undefined,
  });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

/** Stream /api/chat. Calls onChunk({type, ...}) per typed chunk. Returns the
 * session_id chunk's value (for multi-turn continuity) when the stream ends. */
export async function chat({ prompt, agent, sessionId }, onChunk) {
  const payload = JSON.stringify({ prompt, agent, session_id: sessionId || undefined });
  const r = await fetch(base + "/api/chat", {
    method: "POST",
    headers: {
      "x-ws-auth": `Bearer ${await getToken()}` /* CloudFront OAC owns Authorization */,
      "Content-Type": "application/json",
      "x-amz-content-sha256": await bodySha256(payload),
    },
    body: payload,
  });
  if (!r.ok || !r.body) {
    onChunk({ type: "error", error: `chat → ${r.status}` });
    return sessionId;
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "", session = sessionId;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, i); buf = buf.slice(i + 2);
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      try {
        const chunk = JSON.parse(line.slice(5).trim());
        if (chunk.type === "session") session = chunk.session_id;
        else onChunk(chunk);
      } catch { /* partial frame — ignored */ }
    }
  }
  return session;
}
