#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""
embed_corpus.py — BUILD-TIME embedding of the seed corpus (SPEC §6, §8b.6).

Reads seeds/output/*.tsv (from generate_corpus.py), fills the vector columns via
Amazon Titan Text Embeddings V2 (1024-dim), writes *_embedded.tsv — the COPY-ready
artifacts that ship in the WS assets bucket. NEVER runs at provision time (§6:
~100k+ Bedrock calls × N accounts would be slow, quota-fragile, and race the
Bedrock warm-up).

Also runs the §10.6 MEASUREMENT GATE (--verify): Titan must separate the seeded
paraphrase pair in cosine where pg_trgm scores it near-zero, and the M2-stretch
hard negative must sit close enough in complaint-space to crack a naive top-5.
If the gate fails, the pre-named fallback is cohere.embed-english-v3 (SPEC §10.6)
— a decision made by measurement, not preference.

Usage:
  python3 embed_corpus.py               # embed all files (resumable; caches by text hash)
  python3 embed_corpus.py --verify      # run the §10.6 gate on existing embeddings
  python3 embed_corpus.py --dry-run     # count texts + estimate cost, no AWS calls

Cost estimate: ~61k texts × ~40 tokens avg ≈ 2.5M tokens × $0.02/1M ≈ $0.05–2.00.
Runtime: batched serially with retry/backoff; ~30–60 min for the full corpus.
Requires: AWS_PROFILE with bedrock:InvokeModel on amazon.titan-embed-text-v2:0.
"""
import argparse, csv, hashlib, json, math, os, pathlib, sys, time

OUT = pathlib.Path(__file__).parent / "output"
# One small json per text-hash (resumability). Lives OUTSIDE the repo on purpose: the repo sits
# in a cloud-synced folder (WorkDocs Drive), and ~100k tiny files in a hidden dir there put the
# sync daemon into a permanent retry loop (fileproviderd at 100% CPU, 2026-09-14..21).
CACHE = pathlib.Path(os.environ.get("DAT438_EMBED_CACHE",
                     pathlib.Path.home() / ".cache" / "dat438" / "embed_cache"))
MODEL_ID = "amazon.titan-embed-text-v2:0"
DIM = 1024

# (file, [(text_col, vector_col), ...])
TARGETS = [
    ("precedent_cases", [("complaint_text", "complaint_embedding"),
                         ("rebuttal_text", "rebuttal_embedding")]),
    ("rule_clauses",     [("clause_text", "clause_embedding")]),
    ("break_resolutions", [("root_cause_note", "note_embedding")]),
]

csv.field_size_limit(10_000_000)

def rows_of(name, suffix=""):
    with open(OUT / f"{name}{suffix}.tsv") as f:
        r = list(csv.DictReader(f, delimiter="\t"))
    return r

def write_rows(name, rows, suffix="_embedded"):
    path = OUT / f"{name}{suffix}.tsv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter="\t",
                           quoting=csv.QUOTE_NONE, escapechar="\\")
        w.writeheader(); w.writerows(rows)
    print(f"  wrote {path.name} ({len(rows):,} rows)")

def text_key(text):
    return hashlib.sha256(text.encode()).hexdigest()[:32]

# ---------------------------------------------------------------------------
# Bedrock
# ---------------------------------------------------------------------------
def get_client():
    import boto3, os
    return boto3.client("bedrock-runtime",
                        region_name=os.environ.get("AWS_REGION", "us-east-1"))

def embed_one(client, text, retries=6):
    """One Titan V2 call; exponential backoff on throttles (§9: RPM-throttled)."""
    body = json.dumps({"inputText": text[:8000], "dimensions": DIM, "normalize": True})
    for attempt in range(retries):
        try:
            resp = client.invoke_model(modelId=MODEL_ID, body=body)
            return json.loads(resp["body"].read())["embedding"]
        except Exception as e:
            if attempt == retries - 1: raise
            wait = min(2 ** attempt, 30)
            if "Throttling" in str(e) or "TooManyRequests" in str(e):
                time.sleep(wait)
            else:
                time.sleep(1)
    raise RuntimeError("unreachable")

def embed_cached(client, text):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{text_key(text)}.json"
    if p.exists():
        return json.loads(p.read_text())
    v = embed_one(client, text)
    p.write_text(json.dumps(v))
    return v

def to_pgvector(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

# ---------------------------------------------------------------------------
# Main embed pass
# ---------------------------------------------------------------------------
def run_embed(dry_run=False):
    total_texts, done = 0, 0
    plan = []
    for name, cols in TARGETS:
        rows = rows_of(name)
        uniq = {r[tc] for r in rows for tc, _ in cols if r.get(tc)}
        plan.append((name, cols, rows, len(uniq)))
        total_texts += len(uniq)
    print(f"unique texts to embed: {total_texts:,} across {len(plan)} files")
    if dry_run:
        est_tokens = total_texts * 40
        print(f"est. tokens ~{est_tokens/1e6:.1f}M → est. cost ~${est_tokens/1e6*0.02:.2f} (Titan V2 $0.02/1M)")
        return

    # PARALLEL embed of the unique-text set (workers share the disk cache;
    # each text embeds exactly once), then a fast cache-only fill pass per row.
    # 8 workers ≈ 8× throughput; Titan RPM throttles are absorbed by the
    # existing exponential backoff in embed_one. (Serial measured 416 texts/min
    # → 103k texts ≈ 3.5 h; parallel ≈ 25–30 min.)
    import concurrent.futures as cf
    client = get_client()
    t0 = time.time()
    all_unique = sorted({r[tc] for _, cols, rows, _ in plan
                         for r in rows for tc, _ in cols if r.get(tc)})
    pending = [t for t in all_unique if not (CACHE / f"{text_key(t)}.json").exists()]
    print(f"{len(pending):,} of {len(all_unique):,} texts not yet cached — "
          f"embedding with 8 workers …")
    done_ct = 0
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        for _ in pool.map(lambda t: embed_cached(client, t), pending):
            done_ct += 1
            if done_ct % 5000 == 0:
                rate = done_ct / (time.time() - t0)
                print(f"  {done_ct:,} texts  ({rate*60:.0f}/min, "
                      f"~{(len(pending)-done_ct)/max(rate,1)/60:.0f} min left)")
    print(f"  embed pass done in {(time.time()-t0)/60:.1f} min; filling rows from cache …")
    for name, cols, rows, _ in plan:
        for r in rows:
            for text_col, vec_col in cols:
                txt = r.get(text_col, "")
                r[vec_col] = to_pgvector(embed_cached(client, txt)) if txt else ""
        write_rows(name, rows)
    print(f"done in {(time.time()-t0)/60:.1f} min. Next: --verify (the §10.6 gate).")

# ---------------------------------------------------------------------------
# §10.6 verification gate (measurement decides the embedding model)
# ---------------------------------------------------------------------------
def cosine(a, b):
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(x*x for x in b))
    return dot / (na * nb)

def parse_vec(s):
    return [float(x) for x in s.strip("[]").split(",")]

def trigram_similarity(a, b):
    """pg_trgm-equivalent trigram similarity (padded, lowercased — matches PG)."""
    def grams(t):
        t = "".join(c if c.isalnum() else " " for c in t.lower())
        out = set()
        for w in t.split():
            w = "  " + w + " "
            out |= {w[i:i+3] for i in range(len(w) - 2)}
        return out
    A, B = grams(a), grams(b)
    return len(A & B) / max(len(A | B), 1)

def run_verify():
    prec = rows_of("precedent_cases", "_embedded")
    by_tag = {r["tag"]: r for r in prec if r["tag"]}
    failures = []

    def check(name, ok, detail):
        print(f"  {'PASS' if ok else 'FAIL'}  {name} — {detail}")
        if not ok: failures.append(name)

    print("== §10.6 gate: paraphrase separation (embeddings vs pg_trgm) ==")
    a, b = by_tag["paraphrase_a"], by_tag["paraphrase_b"]
    cos_ab = cosine(parse_vec(a["complaint_embedding"]), parse_vec(b["complaint_embedding"]))
    trg_ab = trigram_similarity(a["complaint_text"], b["complaint_text"])
    # criterion: semantically-same pair must be CLOSE in cosine while trigram ~0
    check("paraphrase pair: cosine similarity ≥ 0.5", cos_ab >= 0.5, f"cos={cos_ab:.3f}")
    check("paraphrase pair: trigram similarity ≤ 0.25 (trgm can't see it)", trg_ab <= 0.25, f"trgm={trg_ab:.3f}")
    check("separation margin (cos − trgm) ≥ 0.3", cos_ab - trg_ab >= 0.3, f"margin={cos_ab-trg_ab:.3f}")

    print("== §10.8 pre-check: hard negative enters the naive top-5 ==")
    hn = by_tag["hard_negative"]
    qv = parse_vec(a["complaint_embedding"])   # query ≈ paraphrase_a's complaint
    bucket = [r for r in prec if r["reason_code"] == "13.3" and r["outcome"] == "won"
              and r["complaint_embedding"]]
    sims = sorted(((cosine(qv, parse_vec(r["complaint_embedding"])), r["tag"] or r["precedent_id"])
                   for r in bucket), reverse=True)
    top5_tags = [t for _, t in sims[:5]]
    check("hard_negative in naive complaint-cosine top-5 (so the rerank demo has something to demote)",
          "hard_negative" in top5_tags, f"top5={top5_tags}")

    print("== recon: EPD pair separates semantically ==")
    res = rows_of("break_resolutions", "_embedded")
    epd = [r for r in res if r["tag"] == "epd_pair"]
    cos_epd = cosine(parse_vec(epd[0]["note_embedding"]), parse_vec(epd[1]["note_embedding"]))
    trg_epd = trigram_similarity(epd[0]["root_cause_note"], epd[1]["root_cause_note"])
    check("EPD pair: cosine ≥ 0.4 while trigram ≤ 0.2", cos_epd >= 0.4 and trg_epd <= 0.2,
          f"cos={cos_epd:.3f} trgm={trg_epd:.3f}")

    print()
    if failures:
        print(f"§10.6 GATE FAILED ({len(failures)}) — the pre-named fallback path:")
        print("  1) re-author the failing seeded pair(s) with stronger separation, re-embed, re-verify;")
        print("  2) if Titan V2 cannot separate ANY authored pair, switch to cohere.embed-english-v3")
        print("     (SPEC §10.6: Marketplace IAM + warm-up + 512-token audit priced in).")
        sys.exit(1)
    print("§10.6 GATE PASSED — Titan V2 confirmed; embedded corpus is freeze-ready.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.verify: run_verify()
    else: run_embed(dry_run=args.dry_run)
