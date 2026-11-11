#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""
acceptance.py — seeding acceptance criteria as executable checks.

Runs against seeds/output/*.tsv (pre-embedding checks) — fast, no DB, no AWS.
Embedding-dependent checks (paraphrase separation, rerank delta) run
in embed_corpus.py's verify step after vectors exist.

Exit 0 = seed data frozen-ready; nonzero = a criterion failed (named).
"""
import csv, gzip, sys, pathlib, datetime as dt
from collections import Counter

OUT = pathlib.Path(__file__).parent / "output"
TODAY = dt.date(2026, 12, 1)
FAILURES = []

def load(name):
    # seeds ship gzipped (repo hygiene: no big files in git) — read either form
    for p in (OUT / f"{name}.tsv", OUT / f"{name}.tsv.gz"):
        if p.exists():
            opener = gzip.open if p.suffix == ".gz" else open
            with opener(p, "rt", encoding="utf-8") as f:
                return list(csv.DictReader(f, delimiter="\t"))
    sys.exit(f"missing seed file: {name}.tsv(.gz) under {OUT}")

def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not ok: FAILURES.append(name)

print("== acceptance: precedent corpus (M2) ==")
prec = load("precedent_cases")
n = len(prec)
check("corpus ≥ 50k rows", n >= 50_000, f"{n:,}")

match = sum(1 for r in prec if r["reason_code"] == "13.3" and r["outcome"] == "won")
sel = match / n
check("M2 predicate selectivity in 2–5% band", 0.02 <= sel <= 0.05,
      f"13.3+won = {match:,}/{n:,} = {sel:.4f}")

tags = {r["tag"] for r in prec}
for t in ("paraphrase_a", "paraphrase_b", "hard_negative", "fts_lexical_win"):
    check(f"anchor present: {t}", t in tags)

# hard negative must be 13.3+won so it ENTERS the M2 candidate set (else no demo)
hn = [r for r in prec if r["tag"] == "hard_negative"]
check("hard_negative is in the M2 bucket (13.3+won)",
      all(r["reason_code"] == "13.3" and r["outcome"] == "won" for r in hn))

print("== acceptance: story math ==")
by_analyst = Counter((r["analyst_id"], r["outcome"]) for r in prec)
def winrate(a):
    w, l = by_analyst[(a, "won")], by_analyst[(a, "lost")]
    return w / (w + l)
ANALYST = "analyst_martha"
wr_a = winrate(ANALYST)
team = [a for a, _ in {(r["analyst_id"], 1) for r in prec} if a != ANALYST]
team_w = sum(by_analyst[(a, "won")] for a in team)
team_t = sum(by_analyst[(a, "won")] + by_analyst[(a, "lost")] for a in team)
wr_t = team_w / team_t
check("{{ANALYST}} win rate ≈ 68% (±2)", abs(wr_a - 0.68) < 0.02, f"{wr_a:.3f}")
check("team win rate ≈ 41% (±2)", abs(wr_t - 0.41) < 0.02, f"{wr_t:.3f}")

print("== acceptance: case #4127 (CE 3.0) ==")
txns = load("transactions")
disp = load("disputes")
hero = [d for d in disp if d["case_id"] == "4127"]
check("case #4127 exists, 10.4, $847", bool(hero) and hero[0]["reason_code"] == "10.4"
      and float(hero[0]["amount"]) == 847.00)
if hero:
    hero_txn = [t for t in txns if t["txn_id"] == hero[0]["txn_id"]][0]
    ch = hero_txn["cardholder_id"]
    priors = []
    for t in txns:
        if t["cardholder_id"] != ch or t["txn_id"] == hero_txn["txn_id"]: continue
        if t["disputed"] == "True": continue
        age = (TODAY - dt.date.fromisoformat(t["ts"][:10])).days
        if not (120 <= age <= 365): continue
        core = (t["ip_addr"] == hero_txn["ip_addr"]) + (t["device_id"] == hero_txn["device_id"])
        secondary = (t["shipping_addr"] == hero_txn["shipping_addr"]) + (t["email"] == hero_txn["email"])
        if core + secondary >= 2 and core >= 1:
            priors.append(t)
    check("≥2 CE3.0-qualifying priors (120–365d, ≥2 elements, ≥1 core)", len(priors) >= 2,
          f"found {len(priors)}")
    check("hero txn carries settlement_batch", bool(hero_txn["settlement_batch"]))

print("== acceptance: M3 sibling + doors-open queue ==")
check("low-confidence sibling case 4133 exists (13.1, thin evidence)",
      any(d["case_id"] == "4133" and d["reason_code"] == "13.1" for d in disp))
open_d = [d for d in disp if d["status"] == "OPEN"]
check("doors-open queue ≥ 6 OPEN disputes, varied codes", len(open_d) >= 6
      and len({d["reason_code"] for d in open_d}) >= 4, f"{len(open_d)} open")
ages = {d["opened_at"][:10] for d in open_d}
check("queue ages varied (≥3 distinct open dates)", len(ages) >= 3)

print("== acceptance: recon (M4) ==")
brks = load("breaks")
res = load("break_resolutions")
lines = load("statement_lines")
tie = [b for b in brks if b["break_id"] == "1"]
check("tie-back break #1 exists (SHORT_PAY on SB-2026-1122-04)",
      bool(tie) and tie[0]["break_type"] == "SHORT_PAY")
if tie and hero:
    line = [l for l in lines if l["line_id"] == tie[0]["line_id"]][0]
    check("tie-back references #4127's settlement batch",
          "SB-2026-1122-04" in (line["ref_raw"] + line["narration"]))
    led = load("ledger_entries")
    lg = [e for e in led if e["entry_id"] == tie[0]["ledger_entry_id"]][0]
    gap = round(float(lg["amount"]) - float(line["amount"]), 2)
    check("tie-back gap equals #4127 amount ($847)", gap == 847.00, f"gap={gap}")
epd = [r for r in res if r["tag"] == "epd_pair"]
check("EPD paraphrase pair present (≥2 rows, zero token overlap phrasing)", len(epd) >= 2)
check("breaks queue has ≥5 OPEN breaks across ≥4 types",
      len([b for b in brks if b["status"] == "OPEN"]) >= 5
      and len({b["break_type"] for b in brks}) >= 4)

print("== acceptance: rules grounding (M2.4) ==")
maps = load("rule_mappings")
codes_mapped = {m["reason_code"] for m in maps}
disp_codes = {d["reason_code"] for d in disp}
check("every seeded dispute reason_code has ≥1 rule mapping",
      disp_codes <= codes_mapped, f"unmapped: {disp_codes - codes_mapped or '{}'}")

print()
if FAILURES:
    print(f"ACCEPTANCE FAILED ({len(FAILURES)}): " + ", ".join(FAILURES)); sys.exit(1)
print("ACCEPTANCE PASSED — seed corpus is freeze-ready (pre-embedding checks)")
