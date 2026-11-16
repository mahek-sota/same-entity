"""Metrics with explicit numerators and denominators."""
from __future__ import annotations

from .match import LINK, REVIEW, SEPARATE

# Costs used only to choose thresholds on the development split.
COST = {"false_merge": 4, "false_split": 2, "review": 1}


def outcome(label: str, decision: str) -> str:
    if decision == REVIEW:
        return "review"
    if decision == LINK:
        return "correct link" if label == "match" else "false merge"
    return "correct separate" if label == "non_match" else "false split"


def _frac(n: int, d: int) -> dict:
    return {"n": n, "d": d, "value": (round(n / d, 3) if d else None)}


def score(labels: list[str], decisions: list[str]) -> dict:
    assert len(labels) == len(decisions)
    matches = sum(l == "match" for l in labels)
    non = len(labels) - matches
    fm = sum(l == "non_match" and d == LINK for l, d in zip(labels, decisions))
    fs = sum(l == "match" and d == SEPARATE for l, d in zip(labels, decisions))
    links = sum(d == LINK for d in decisions)
    correct_links = links - fm
    seps = sum(d == SEPARATE for d in decisions)
    reviews = sum(d == REVIEW for d in decisions)
    auto = links + seps
    return {
        "pairs": len(labels),
        "true_matches": matches,
        "true_non_matches": non,
        "false_merges": _frac(fm, non),
        "false_splits": _frac(fs, matches),
        "review_rate": _frac(reviews, len(labels)),
        "precision_auto_links": _frac(correct_links, links),
        "auto_decision_accuracy": _frac(auto - fm - fs, auto),
        "matches_auto_linked": _frac(correct_links, matches),
        "decision_counts": {LINK: links, SEPARATE: seps, REVIEW: reviews},
        "confusion": {
            lab: {dec: sum(l == lab and d == dec for l, d in zip(labels, decisions))
                  for dec in (LINK, REVIEW, SEPARATE)}
            for lab in ("match", "non_match")
        },
        "cost": COST["false_merge"] * fm + COST["false_split"] * fs + COST["review"] * reviews,
    }
