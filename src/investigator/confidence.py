# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""Confidence — a deterministic, explainable rubric (§4 step 5).

NOT model-asserted: the same move as the at_risk view (rubric in code/SQL, never
"ask the LLM how sure it is"). Components are returned alongside the score so
the calibration rep (inspect distribution, pick your own τ) has something to
inspect, and the review queue can show WHY a case routed to a human.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceScore:
    score: float               # 0.0–1.0
    components: dict           # name -> contribution, sums (clamped) to score

    def explain(self) -> str:
        parts = ", ".join(f"{k}={v:+.2f}" for k, v in self.components.items())
        return f"{self.score:.3f} ({parts})"


def dispute_confidence(evidence_score: int, precedents: list[dict],
                       clause_count: int) -> ConfidenceScore:
    """Rubric for a drafted representment:
    - evidence_score (0-3, from CE3.0/evidence tools) carries half the weight
    - precedent support: count + best cosine similarity of won-case matches
    - rule grounding: any mapped clause cited
    """
    components = {}
    components["evidence"] = 0.5 * (min(evidence_score, 3) / 3.0)
    best_sim = max((p.get("cosine_similarity") or 0.0 for p in precedents), default=0.0)
    components["precedent_count"] = 0.15 * min(len(precedents), 3) / 3.0
    components["precedent_similarity"] = 0.20 * max(0.0, min(float(best_sim), 1.0))
    components["rule_grounding"] = 0.15 if clause_count > 0 else 0.0
    score = round(min(sum(components.values()), 1.0), 3)
    return ConfidenceScore(score=score, components=components)


def break_confidence(precedents: list[dict], amount_gap: float,
                     has_counterparty_history: bool) -> ConfidenceScore:
    """Rubric for a proposed break resolution:
    - precedent support (count + similarity) dominates: recon judgment IS
      institutional memory here (§1b — the recon team never had an {{ANALYST}})
    - small absolute gaps are safer to auto-post than large ones
    - counterparty history (prior resolved breaks, same pattern) adds trust
    """
    components = {}
    best_sim = max((p.get("cosine_similarity") or 0.0 for p in precedents), default=0.0)
    components["precedent_count"] = 0.25 * min(len(precedents), 3) / 3.0
    components["precedent_similarity"] = 0.35 * max(0.0, min(float(best_sim), 1.0))
    components["amount_band"] = 0.25 if abs(amount_gap) < 100 else (0.10 if abs(amount_gap) < 1000 else 0.0)
    components["counterparty_history"] = 0.15 if has_counterparty_history else 0.0
    score = round(min(sum(components.values()), 1.0), 3)
    return ConfidenceScore(score=score, components=components)
