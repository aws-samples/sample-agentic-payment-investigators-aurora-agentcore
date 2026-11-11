#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""
generate_corpus.py — authors the workshop's seed data (statistical corpus + story seeds).

Outputs TSV files to seeds/output/ (COPY-ready, embeddings columns EMPTY — filled
by embed_corpus.py at BUILD time; output/ is .gitignored, assets-bucket only).

Deterministic: seeded RNG, fixed story rows. Run acceptance.py after.

Story anchors (names are placeholders, swap via NAMES dict):
  - case #4127: $847 Visa 10.4, CE3.0-qualifying priors (same device+IP, 120-365d)
  - {{ANALYST}}'s precedent corpus: 68% win rate vs team 41% (derivable)
  - #4127's settlement batch -> non-clean recon break (M4 tie-back)
  - M3 low-confidence sibling case
  - "EPD taken - buyer paid early per terms" <-> "net-10 discount taken" recon paraphrase pair
  - M2-stretch hard negative; M2 paraphrase pairs; FTS lexical-wins pair
"""
import csv, random, datetime as dt, pathlib, itertools

RNG = random.Random(4127)
OUT = pathlib.Path(__file__).parent / "output"
OUT.mkdir(exist_ok=True)

NAMES = {  # placeholders pending naming constraints
    "ANALYST": "analyst_martha",          # {{ANALYST}} — RLS identity
    "COMPANY": "AnyCompany",         # {{COMPANY}}
}
TEAM = [NAMES["ANALYST"], "analyst_john", "analyst_mary", "analyst_saanvi"]

TODAY = dt.date(2026, 12, 1)            # workshop-relative "today" (re:Invent)
def days_ago(n): return TODAY - dt.timedelta(days=n)
def ts_days_ago(n, h=10): return f"{days_ago(n)}T{h:02d}:00:00Z"

# ---------------------------------------------------------------------------
# Reason-code universe + complaint/rebuttal text banks
# (representative, paraphrase-rich; the statistical corpus samples these with
#  perturbation so embeddings aren't degenerate duplicates)
# ---------------------------------------------------------------------------
CODES = {  # code -> (share, complaint templates, rebuttal templates)
    "10.4": (0.25, [
        "I didn't make this charge. I don't recognize this transaction at all.",
        "This purchase was not made by me — my card must have been compromised.",
        "Unauthorized transaction. I never bought anything from this merchant.",
        "Someone used my card for this online order. Not me.",
    ], [
        "Cardholder's device and IP match {n} prior undisputed purchases within 365 days; CE3.0 evidence attached (device fingerprint, IP, delivery to on-file address).",
        "Order placed from the cardholder's established device with delivery to their verified address; two qualifying prior transactions establish first-party pattern under CE3.0.",
    ]),
    "13.1": (0.24, [
        "My order never arrived. Tracking has shown no movement for weeks.",
        "I paid for this item a month ago and still have not received anything.",
        "Package never delivered. The merchant stopped responding to my emails.",
        "The product I purchased simply never showed up at my address.",
    ], [
        "Carrier confirmation shows delivery to the cardholder's address on {date} with signature; delivery photo attached.",
        "Shipment delivered and signed for prior to dispute date; tracking log and proof-of-delivery attached.",
    ]),
    "13.3": (0.06, [
        "The item looked nothing like the listing photos. Completely different product.",
        "What arrived was a cheap knockoff, not the brand advertised on the site.",
        "Product arrived damaged and does not function as described.",
        "The item is materially different from what was described at purchase.",
    ], [
        "Listing photographs, specification sheet, and QC record match the shipped item; cardholder retained the merchandise beyond the return window.",
        "Independent product verification confirms shipped item matches description; return policy was offered and declined.",
    ]),
    "13.2": (0.15, [
        "I cancelled this subscription last month but was charged again.",
        "This is a recurring charge I already told the merchant to stop.",
        "Charged after cancellation. I have the cancellation confirmation email.",
    ], [
        "Cancellation request post-dates the billing cycle cutoff per terms accepted at signup; terms and timestamped acceptance attached.",
        "No cancellation on record before the billing date; subscription terms and usage log attached.",
    ]),
    "13.6": (0.10, [
        "I returned the item weeks ago but never received my refund credit.",
        "Merchant accepted my return but the credit never appeared on my card.",
    ], [
        "Credit issued on {date}, ARN attached; posting delay is issuer-side.",
        "Refund processed within policy window; acquirer reference attached.",
    ]),
    "11.1": (0.10, [
        "The charge was declined at the register but appears on my statement.",
        "Merchant ran my card twice; one auth should have been reversed.",
    ], [
        "Single settlement matches single authorization; auth log attached showing the decline was a separate attempt never captured.",
    ]),
    "12.1": (0.10, [
        "I was charged in the wrong currency — the price shown was in USD.",
        "The amount billed differs from the receipt I signed.",
    ], [
        "Signed receipt matches billed amount; currency disclosed at checkout per DCC opt-in record.",
    ]),
}
assert abs(sum(v[0] for v in CODES.values()) - 1.0) < 1e-9

N_PRECEDENTS = 60_000  # validated scale: the ~3% predicate selectivity reproduces

def perturb(text, i):
    """Cheap deterministic textual variation so sampled rows aren't byte-duplicates.
    ⚠ SUFFIX TAILS ONLY — do NOT use for embedded columns: Titan embeds
    tail-variants nearly identically (measured: cos 0.82–0.88 vs base;
    60k rows collapsed to 6.6k distinct vectors and the HNSW graph
    degenerated). Embedded prose must go through the
    slot-fill synthesizers below instead."""
    tails = ["", " Order #{}.".format(1000 + i % 9000), " This was for a gift.",
             " I have screenshots.", " Please resolve quickly.", " See attached email thread.",
             " This is my second attempt to resolve this.", " Amount was significant for me."]
    return text + tails[i % len(tails)]


# ---------------------------------------------------------------------------
# Slot-fill synthesis for EMBEDDED prose (complaints/rebuttals/notes): every
# row gets a compositionally unique surface text so its embedding is a real,
# distinct point (measured: slot-fill pairwise cos 0.09–0.28 vs tails' 0.82+).
# ---------------------------------------------------------------------------
PRODUCTS = [
    "walnut bookshelf", "ceramic dinnerware set", "leather weekend bag", "stand mixer",
    "noise-cancelling headphones", "cast iron dutch oven", "linen duvet cover",
    "espresso machine", "trail running shoes", "mechanical keyboard", "wool area rug",
    "air purifier", "hiking backpack", "desk lamp", "robot vacuum", "food processor",
    "down winter parka", "acoustic guitar", "gaming monitor", "electric kettle",
    "yoga mat", "office chair", "table saw", "sewing machine", "telescope",
    "record player", "smart doorbell", "pressure washer", "paddle board", "e-reader",
]
DEFECTS = [
    "arrived in a completely different color than pictured",
    "was a smaller model than the listing described",
    "turned out to be an obvious counterfeit of the brand",
    "came with parts missing and no assembly hardware",
    "looked used, with scratches and someone else's return label inside",
    "was a cheaper knockoff with none of the advertised features",
    "did not match a single photo from the product gallery",
    "was the wrong item entirely — not even the same category",
    "stopped working within an hour of unboxing",
    "had a cracked housing straight out of the box",
]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
OPENERS = ["I ordered", "We purchased", "My spouse bought", "I bought",
           "Our office ordered", "I picked out"]
CLOSERS = [
    "The seller has stopped answering my messages.",
    "I want this charge reversed.", "Support kept me on hold twice and hung up.",
    "I've already returned it and heard nothing.", "This is not what I paid for.",
    "No response from the merchant in ten days.", "I expect a full refund.",
]


def synth_complaint(code, i, rng=None):
    """A unique complaint per row: independent RNG draws per slot (i-derived
    indices CYCLE — period lcm(pools)≈420/code, measured 2,944 distinct/60k;
    RNG draws give ~4M combinations). Deterministic via the seeded module rng."""
    rng = rng or RNG
    p = rng.choice(PRODUCTS)
    m = rng.choice(MONTHS)
    d = rng.randint(1, 28)
    o = rng.choice(OPENERS)
    c = rng.choice(CLOSERS)
    if code == "10.4":
        who = rng.choice(["I never made", "Nobody in my household made",
                          "I did not authorize", "There is no way I made"])
        return (f"{who} this charge for a {p} on {m} {d}. "
                f"My card has not left my possession. {c}")
    if code == "13.1":
        return (f"{o} a {p} on {m} {d} and it has never arrived. "
                f"Tracking has shown no movement since {m} {d + 2 if d < 27 else d}. {c}")
    if code == "13.3":
        return f"{o} a {p} on {m} {d} and it {rng.choice(DEFECTS)}. {c}"
    if code == "13.2":
        return (f"I cancelled the {p} subscription plan back in {m} "
                f"but was billed again on the {d}th. {c}")
    if code == "13.6":
        return (f"I returned the {p} on {m} {d} with the seller's own label "
                f"and the refund credit never posted. {c}")
    if code == "11.1":
        return (f"The register declined my card for the {p} on {m} {d}, "
                f"yet the charge still appears on my statement. {c}")
    return (f"The amount billed for the {p} on {m} {d} does not match "
            f"the receipt I signed. {c}")


def synth_rebuttal(code, i, rng=None):
    """A unique rebuttal per row — same RNG-draw discipline (these embed too)."""
    rng = rng or RNG
    p = rng.choice(PRODUCTS)
    m = rng.choice(MONTHS)
    d = rng.randint(1, 28)
    arn = f"74{chr(65 + rng.randint(0, 25))}{rng.randint(1000, 9999)}"
    n = rng.randint(2, 5)
    if code == "10.4":
        return (f"Order for the {p} was placed from the cardholder's enrolled device; "
                f"IP and device fingerprint match {n} prior undisputed purchases, the "
                f"earliest on {m} {d}. CE 3.0 evidence packet attached.")
    if code == "13.1":
        return (f"Carrier scan shows the {p} delivered on {m} {d} with signature; "
                f"geolocation places the driver at the verified address. POD attached.")
    if code == "13.3":
        return (f"Fulfillment photographed the {p} at pack-out on {m} {d}; the images "
                f"match the listing gallery and the QC checklist. Return was offered "
                f"and declined on {m} {min(d + 3, 28)}.")
    if code == "13.2":
        return (f"No cancellation for the {p} plan exists before the {m} {d} billing "
                f"date; the terms accepted at signup and the usage log are attached.")
    if code == "13.6":
        return (f"Credit for the returned {p} was issued {m} {d} under ARN {arn}; "
                f"posting delay is issuer-side. Timeline attached.")
    if code == "11.1":
        return (f"A single settlement matches the sole approved authorization for the "
                f"{p}; the declined attempt on {m} {d} was never captured.")
    return (f"The signed receipt for the {p} matches the billed amount; currency "
            f"disclosure and DCC opt-in from {m} {d} attached.")

# ---------------------------------------------------------------------------
# 1. precedent_cases (the M2 corpus + story anchors)
# ---------------------------------------------------------------------------
def gen_precedents():
    rows, pid = [], 1
    # story math: {{ANALYST}} wins 68%, team 41% (acceptance-checked)
    def outcome_for(analyst):
        p = 0.68 if analyst == NAMES["ANALYST"] else 0.41
        return "won" if RNG.random() < p else "lost"

    code_choices = list(CODES.items())
    for i in range(N_PRECEDENTS):
        r = RNG.random(); acc = 0
        for code, (share, complaints, rebuttals) in code_choices:
            acc += share
            if r < acc: break
        analyst = RNG.choice(TEAM)
        rows.append(dict(
            precedent_id=pid, network="visa" if RNG.random() < 0.7 else "mastercard",
            reason_code=code, outcome=outcome_for(analyst), analyst_id=analyst,
            complaint_text=synth_complaint(code, i),
            rebuttal_text=synth_rebuttal(code, i),
            winning_evidence_summary="", tag=""))
        pid += 1

    # --- authored anchors (fixed ids at the end; tags drive acceptance + demos) ---
    A = NAMES["ANALYST"]
    anchors = [
        # M2 paraphrase pair (same meaning, ~zero token overlap) — 13.3 won
        dict(reason_code="13.3", outcome="won", analyst_id=A, tag="paraphrase_a",
             complaint_text="The item looked nothing like the listing photos.",
             rebuttal_text="Listing photography and QC records match the shipped unit; retention beyond return window documented."),
        dict(reason_code="13.3", outcome="won", analyst_id=A, tag="paraphrase_b",
             # texts measured against live Titan V2 (gate: cos>=0.5, trgm<=0.25, margin>=0.3);
             # re-run embed_corpus.py --verify after ANY wording change here
             complaint_text="Received an item that matches none of the photos displayed on the product page.",
             rebuttal_text="Product photographed at fulfillment matches the listing imagery; spec comparison attached; return offered and declined."),
        # M2-stretch hard negative: lexically CLOSE complaint, WRONG-fact-pattern rebuttal
        dict(reason_code="13.3", outcome="won", analyst_id=A, tag="hard_negative",
             complaint_text="The item looked nothing like the pictures in the listing.",
             rebuttal_text="Carrier tracking shows delivery with signature on the scheduled date."),  # a 13.1-style rebuttal — wrong answer for a 13.3 fight
        # FTS lexical-wins pair: exact rare token INV-77812
        dict(reason_code="13.6", outcome="won", analyst_id=A, tag="fts_lexical_win",
             complaint_text="Refund for invoice INV-77812 never posted to my card.",
             rebuttal_text="Credit for INV-77812 issued with ARN 74J9-002; posting timeline attached."),
    ]
    for a in anchors:
        rows.append(dict(precedent_id=pid, network="visa",
                         winning_evidence_summary="", **a)); pid += 1
    return rows

# ---------------------------------------------------------------------------
# 2. transactions + disputes (doors-open queue + #4127 + sibling)
# ---------------------------------------------------------------------------
def gen_transactions_and_disputes():
    txns, disputes, tid = [], [], 1
    def add_txn(**kw):
        nonlocal tid
        row = dict(txn_id=tid, currency="USD", settlement_batch="", disputed=False,
                   shipping_addr="", email="")
        row.update(kw)                      # explicit args override defaults
        txns.append(row); tid += 1
        return row["txn_id"]

    # --- case #4127: the hero ---
    ch, dev, ip = "ch_jackson_m", "dev-a9f3e2", "203.0.113.44"
    em, addr = "m.jackson@example.com", "123 Any Street, Anytown, USA"
    # two CE3.0-qualifying priors: same device+IP (core x2), 120-365d old, undisputed
    add_txn(cardholder_id=ch, merchant_id="m_anycompany", amount=212.50, ts=ts_days_ago(310),
            descriptor="ANYCOMPANY ONLINE", ip_addr=ip, device_id=dev, shipping_addr=addr, email=em)
    add_txn(cardholder_id=ch, merchant_id="m_anycompany", amount=94.99, ts=ts_days_ago(151),
            descriptor="ANYCOMPANY ONLINE", ip_addr=ip, device_id=dev, shipping_addr=addr, email=em)
    hero_txn = add_txn(cardholder_id=ch, merchant_id="m_anycompany", amount=847.00, ts=ts_days_ago(9),
            descriptor="ANYCOMPANY ONLINE", ip_addr=ip, device_id=dev, shipping_addr=addr, email=em,
            settlement_batch="SB-2026-1122-04", disputed=True)
    # the M2 WINDOW TRAP: one more undisputed
    # jackson purchase, 45 days old — matching elements but INSIDE CE 3.0's
    # 120-day floor, so a correct window EXCLUDES it (2 qualifying priors).
    # A missing or sign-flipped window picks it up (3) and check.sh m2 catches
    # it. Teaching: the floor exists because a purchase that recent could be
    # the fraudster's own spree. Fixed values, NO RNG (byte-stability above);
    # placed AFTER the hero so txn_id 3 (the recon tie-back) is unchanged.
    add_txn(cardholder_id=ch, merchant_id="m_anycompany", amount=63.75, ts=ts_days_ago(45),
            descriptor="ANYCOMPANY ONLINE", ip_addr=ip, device_id=dev, shipping_addr=addr, email=em)
    # opened_at=1: the hero HEADLINES the queue (the UI sorts opened_at DESC;
    # the tour and module 2 both say "top of your queue" — end-to-end testing
    # caught 4133 outranking it)
    disputes.append(dict(case_id=4127, txn_id=hero_txn, network="visa", reason_code="10.4",
        amount=847.00, complaint_text="I didn't make this charge. I don't recognize this transaction.",
        status="OPEN", evidence_score=0, confidence="", draft_text="", trace_id="",
        opened_at=ts_days_ago(1)))

    # --- M3 low-confidence sibling: thin-evidence 13.1 ---
    st = add_txn(cardholder_id="ch_wolf_n", merchant_id="m_anycompany", amount=129.00,
                 ts=ts_days_ago(21), descriptor="ANYCOMPANY ONLINE",
                 ip_addr="198.51.100.7", device_id="dev-77c001", email="n.wolf@example.com")
    disputes.append(dict(case_id=4133, txn_id=st, network="visa", reason_code="13.1",
        amount=129.00, complaint_text="My order never arrived and tracking shows nothing.",
        status="OPEN", evidence_score=0, confidence="", draft_text="", trace_id="",
        opened_at=ts_days_ago(2)))

    # --- M4 overfilter demo case: a 13.3 whose PROSE reads like fraud ---
    # Codes are issuer-assigned; complaint text often doesn't read like its
    # code. This case's fraud-flavored text parks its embedding deep in 10.4
    # territory, so hybrid's WHERE (13.3 AND won, ~2.9% selectivity) kills the
    # whole ef_search=40 neighborhood: 0 rows without iterative_scan
    # (measured: off=0, relaxed_order=5). NO RNG draws here —
    # precedent byte-stability is load-bearing.
    ot = add_txn(cardholder_id="ch_ramirez_d", merchant_id="m_anycompany", amount=379.00,
                 ts=ts_days_ago(16), descriptor="ANYCOMPANY ONLINE",
                 ip_addr="198.51.100.61", device_id="dev-2f9a11", email="d.ramirez@example.com")
    disputes.append(dict(case_id=4136, txn_id=ot, network="visa", reason_code="13.3",
        amount=379.00,
        complaint_text="I don't recognize this charge at all. I never bought this item.",
        status="OPEN", evidence_score=0, confidence="", draft_text="", trace_id="",
        opened_at=ts_days_ago(3)))

    # --- doors-open queue spread (copilot's first answer; varied ages/amounts) ---
    for i, (code, amt, age) in enumerate([("13.2", 45.00, 4), ("10.4", 1210.00, 12),
                                          ("13.6", 88.20, 6), ("11.1", 310.75, 15),
                                          ("13.3", 264.99, 3)]):
        t = add_txn(cardholder_id=f"ch_queue_{i}", merchant_id="m_anycompany", amount=amt,
                    ts=ts_days_ago(age + 2), descriptor="ANYCOMPANY ONLINE",
                    ip_addr=f"192.0.2.{10+i}", device_id=f"dev-q{i}", email=f"q{i}@example.com")
        disputes.append(dict(case_id=4140 + i, txn_id=t, network="visa", reason_code=code,
            amount=amt, complaint_text=perturb(RNG.choice(CODES[code][1]), i),
            status="OPEN",
            # #4144 is module 9's production-test case and module 10's one review
            # card: its stored evidence score must stay 0 so the rubric lands
            # below τ on every run (2026-09-26). The RNG draw is still
            # consumed so every later row stays byte-identical.
            evidence_score=(lambda d: 0 if 4140 + i == 4144 else d)(RNG.randint(0, 3)),
            confidence="", draft_text="",
            trace_id="", opened_at=ts_days_ago(age)))

    # --- worked history (doors-open color: the floor has a PAST — the UI
    # shows seeded state from minute zero). SEPARATE RNG stream so adding these
    # never perturbs the main RNG sequence (precedent texts are bound to
    # already-embedded vectors — byte-stability is load-bearing).
    HRNG = random.Random(9200)
    hist_codes = ["10.4", "13.1", "13.3", "13.2", "13.6", "11.1", "12.1"]
    # realistic draft text per reason code (the review tab shows these
    # cards — filler text read as a mockup). Derived
    # from (code, case_id) with ZERO HRNG draws: hist regeneration stays
    # deterministic and the conf/status sequence is untouched.
    HIST_DRAFTS = {
        "10.4": "Representment under CE 3.0: cardholder completed {n} prior undisputed purchases from the same device and IP within the qualifying window. Evidence packet lists matched data elements per Visa condition 10.4.",
        "13.1": "Carrier confirmation attached: package scanned delivered to the address on file {n} days before the dispute was raised. Tracking history and signed POD included.",
        "13.3": "Product listing, spec sheet, and pre-shipment QC photos attached; the item shipped matches the published description. No return was initiated within the stated policy window.",
        "13.2": "Subscription terms and cancellation log attached: no cancellation request precedes the billing date. The charge covers the active period per the agreement accepted at signup.",
        "13.6": "Refund was issued to the original payment method {n} business days before the dispute date; ARN attached. The credit and the disputed charge are the same amount.",
        "11.1": "Authorization log attached: approval code obtained at point of sale and settled within the presentment window. No decline precedes the capture.",
        "12.1": "Settlement records attached: the billed amount matches the authorized amount and currency. No duplicate presentment exists for this transaction id.",
    }
    for i in range(22):
        code = HRNG.choice(hist_codes)
        amt = round(HRNG.uniform(30, 1500), 2)
        age = HRNG.randint(20, 90)
        t = add_txn(cardholder_id=f"ch_hist_{i}", merchant_id="m_anycompany", amount=amt,
                    ts=ts_days_ago(age + 3), descriptor="ANYCOMPANY ONLINE",
                    ip_addr=f"198.51.100.{100 + i}", device_id=f"dev-h{i}",
                    email=f"h{i}@example.com")
        # spread that makes the τ story visible: some clear it, some don't.
        # ALL history is TERMINAL (doors-open tour safety): sub-τ
        # cases were reviewed by a human IN THE PAST and decided — seeded as
        # APPROVED→FILED / REJECTED→DENIED review history (seed_review_queue
        # derives the verdict from the status). Zero PENDING at doors-open ⇒
        # zero clickable verdicts ⇒ the floor tour can't break later modules;
        # the badge lights 0→1 when the participant's OWN agent routes first.
        # (Status mapping consumes no HRNG draws — byte-stability preserved.)
        conf = round(HRNG.uniform(0.45, 0.98), 3)
        if conf >= 0.7:
            status = "FILED" if i % 3 else "ACCEPTED"
        else:
            status = "DENIED" if (i < 2 or i % 5 == 0) else "FILED"
        disputes.append(dict(case_id=9200 + i, txn_id=t, network="visa",
            reason_code=code, amount=amt,
            complaint_text=synth_complaint(code, 9000 + i, rng=HRNG),
            status=status, evidence_score=HRNG.randint(1, 3), confidence=conf,
            draft_text=HIST_DRAFTS[code].format(n=2 + (i % 3)), trace_id="",
            opened_at=ts_days_ago(age)))
    return txns, disputes

# ---------------------------------------------------------------------------
# 3. Recon: ledger + statement + breaks + resolutions (M4)
# ---------------------------------------------------------------------------
def gen_recon():
    ledger, lines, breaks, resolutions = [], [], [], []
    # matched bulk (the pre-built matcher clears ~90%): 200 clean pairs
    for i in range(1, 201):
        amt = round(RNG.uniform(20, 4000), 2)
        d = days_ago(RNG.randint(1, 6))
        ledger.append(dict(entry_id=i, account="acq_settlement", amount=amt,
                           value_date=d, ref=f"STL-{7000+i}", memo="daily settlement"))
        lines.append(dict(line_id=i, bank_account="operating_01", amount=amt,
                          booking_date=d, ref_raw=f"STL-{7000+i}",
                          narration=f"SETTLEMENT STL-{7000+i} ANYCOMPANY"))
    nid, bid = 201, 1
    def brk(btype, note_amt_ledger, note_amt_line, narr, ref_raw=""):
        nonlocal nid, bid
        d = days_ago(RNG.randint(0, 3))
        le = li = ""
        if note_amt_ledger is not None:
            ledger.append(dict(entry_id=nid, account="acq_settlement", amount=note_amt_ledger,
                               value_date=d, ref=f"STL-{7000+nid}", memo="")); le = nid
        if note_amt_line is not None:
            lines.append(dict(line_id=nid, bank_account="operating_01", amount=note_amt_line,
                              booking_date=d, ref_raw=ref_raw, narration=narr)); li = nid
        breaks.append(dict(break_id=bid, break_type=btype, ledger_entry_id=le, line_id=li,
                           status="OPEN", confidence="", proposed_action="", trace_id="",
                           opened_at=ts_days_ago(0))); nid += 1; bid += 1

    # residual breaks queue (M4): incl. the #4127 tie-back — batch SB-2026-1122-04
    # settles $847 short on the statement because the disputed txn was withheld.
    brk("SHORT_PAY", 14210.88, 13363.88,
        "ACQ SETTLEMENT SB-2026-1122-04 ANYCOMPANY NET", ref_raw="SB-2026-1122-04")  # break_id=1 = tie-back
    brk("SHORT_PAY", 2350.00, 2303.00, "PAYMENT ANYCOMPANY WHOLESALE NET TERMS", ref_raw="")
    brk("REF_MISMATCH", 512.40, 512.40, "TRANSFER REF INVOICE 9313", ref_raw="Invoice 9313")
    brk("TIMING", 1830.00, None, "", ref_raw="")
    brk("FX_ROUNDING", 402.11, 402.09, "FX SETTLEMENT EUR BATCH 2211", ref_raw="FX-2211")
    brk("DUP", None, 96.50, "CARD SETTLEMENT DUPLICATE RUN 2", ref_raw="STL-7042")

    # break_resolutions: M4 precedent corpus with vocabulary drift
    base = [
        # the seeded semantic win — texts measured against live Titan V2 (gate: cos>=0.4,
        # trgm<=0.2); bare "EPD taken" scored cos 0.085 and failed; re-verify after edits
        ("SHORT_PAY", "EPD taken - buyer paid early per terms", "post_fee_adjustment"),
        ("SHORT_PAY", "buyer took the negotiated 2% net-10 discount for settling the invoice early", "post_fee_adjustment"),
        ("SHORT_PAY", "counterparty deducted early payment discount per contract", "post_fee_adjustment"),
        ("SHORT_PAY", "settlement net of withheld disputed transaction", "link_dispute_and_age"),
        ("REF_MISMATCH", "bank truncated our reference; matched on amount+date", "relink_reference"),
        ("REF_MISMATCH", "invoice number reformatted by counterparty portal", "relink_reference"),
        ("TIMING", "wire posted next banking day; self-resolved", "age_1_day"),
        ("FX_ROUNDING", "rate rounding within tolerance band", "write_off_minor"),
        ("DUP", "processor re-ran settlement file; reversal requested", "request_reversal"),
    ]
    rid = 1
    for btype, note, action in base:
        tag = "epd_pair" if "EPD" in note or "net-10" in note else ""
        resolutions.append(dict(resolution_id=rid, break_type=btype, root_cause_note=note,
                                resolution_action=action, tag=tag)); rid += 1
    # statistical fill for retrieval texture — note_embedding is EMBEDDED, so
    # every note must be a compositionally distinct sentence (slot-fill, not
    # tails — see perturb()'s degeneracy warning above)
    COUNTERPARTIES = ["Northwind Retail", "Alpina Foods", "Cobalt Media", "Juniper Health",
                      "Praxis Manufacturing", "Delta Freight", "Harborline Logistics",
                      "Summit Office Supply", "Verity Insurance", "Kestrel Apparel"]
    SP_MODES = ["early-payment discount taken per their contract",
                "negotiated prompt-pay reduction applied at remittance",
                "volume rebate netted against the invoice total",
                "withheld the disputed line item pending our credit memo",
                "applied their damage allowance before paying"]
    RM_MODES = ["Bank truncated the reference to {} digits",
                "Counterparty portal reformatted the invoice number",
                "Remittance carried the PO number instead of our invoice id",
                "Reference arrived with the year prefix stripped",
                "Their ERP swapped invoice and account numbers in the memo field"]
    NOTE_SYNTH = {
        "SHORT_PAY": lambda i: (
            f"{RNG.choice(COUNTERPARTIES)} paid {RNG.randint(1, 5)}% short on the "
            f"{RNG.choice(MONTHS)} invoice — {RNG.choice(SP_MODES)}."),
        "REF_MISMATCH": lambda i: (
            f"{RNG.choice(RM_MODES).format(RNG.randint(4, 8))}; matched "
            f"{RNG.choice(COUNTERPARTIES)} on amount and value date instead."),
        "TIMING": lambda i: (
            f"{RNG.choice(COUNTERPARTIES)} wire posted {RNG.randint(1, 3)} banking "
            f"day(s) late in {RNG.choice(MONTHS)}; self-resolved on re-run."),
        "FX_ROUNDING": lambda i: (
            f"{RNG.choice(['EUR', 'GBP', 'JPY', 'CAD', 'MXN', 'AUD'])} conversion "
            f"rounding of {RNG.randint(1, 12)} cents on the "
            f"{RNG.choice(COUNTERPARTIES)} settlement; within tolerance."),
        "DUP": lambda i: (
            f"Processor re-ran the {RNG.choice(MONTHS)} settlement file for "
            f"{RNG.choice(COUNTERPARTIES)}; reversal requested for the duplicate batch."),
    }
    for i in range(600):
        btype, note, action = RNG.choice(base)
        synth = NOTE_SYNTH.get(btype)
        resolutions.append(dict(resolution_id=rid, break_type=btype,
            root_cause_note=synth(i) if synth else perturb(note, i),
            resolution_action=action, tag="")); rid += 1
    return ledger, lines, breaks, resolutions

# ---------------------------------------------------------------------------
# 4. rule_clauses + rule_mappings (M2 grounding; citations are illustrative)
# ---------------------------------------------------------------------------
def gen_rules():
    clauses = [
        ("visa", "CE3.0 §A", "For reason code 10.4 disputes, compelling evidence comprises two or more prior undisputed transactions between 120 and 365 days old sharing two qualifying data elements, at least one being IP address or device identifier."),
        ("visa", "CE3.0 §B", "Qualifying data elements are: customer IP address, device identifier or fingerprint, shipping address, and customer account or login identifier."),
        ("visa", "VCR 13.1-EV", "For merchandise-not-received disputes, proof of delivery to the cardholder's verified address with carrier confirmation constitutes primary evidence."),
        ("visa", "VCR 13.3-EV", "For not-as-described disputes, evidence must address the specific defect claim: listing imagery, specifications, quality-control records, and the return remedy offered."),
        ("visa", "VCR 13.2-EV", "For cancelled-recurring disputes, the merchant must evidence the cancellation policy terms, acceptance timestamp, and absence of a valid cancellation before the billing date."),
        ("visa", "VCR 13.6-EV", "For credit-not-processed disputes, the acquirer reference number and credit issuance date establish merchant compliance."),
        ("visa", "VCR 11.1-EV", "For authorization disputes, the settlement must be matched to a single approved authorization code."),
        ("visa", "VCR 12.1-EV", "For processing-error disputes, the signed receipt or DCC opt-in record must match the billed amount and currency."),
    ]
    rows = [dict(clause_id=i + 1, network=n, citation=c, clause_text=t)
            for i, (n, c, t) in enumerate(clauses)]
    mappings = [("visa", "10.4", 1), ("visa", "10.4", 2), ("visa", "13.1", 3),
                ("visa", "13.3", 4), ("visa", "13.2", 5), ("visa", "13.6", 6),
                ("visa", "11.1", 7), ("visa", "12.1", 8)]
    maps = [dict(network=n, reason_code=r, clause_id=c) for n, r, c in mappings]
    return rows, maps

# ---------------------------------------------------------------------------
def write_tsv(name, rows):
    if not rows: return
    path = OUT / f"{name}.tsv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter="\t",
                           quoting=csv.QUOTE_NONE, escapechar="\\")
        w.writeheader(); w.writerows(rows)
    print(f"  {path.name:28s} {len(rows):>7,} rows")

if __name__ == "__main__":
    print("generating seed corpus (deterministic, seed=4127)…")
    write_tsv("precedent_cases", gen_precedents())
    txns, disputes = gen_transactions_and_disputes()
    write_tsv("transactions", txns)
    write_tsv("disputes", disputes)
    ledger, lines, breaks, resolutions = gen_recon()
    write_tsv("ledger_entries", ledger)
    write_tsv("statement_lines", lines)
    write_tsv("breaks", breaks)
    write_tsv("break_resolutions", resolutions)
    clauses, maps = gen_rules()
    write_tsv("rule_clauses", clauses)
    write_tsv("rule_mappings", maps)
    print("done. next: embed_corpus.py (build-time), then acceptance.py")
