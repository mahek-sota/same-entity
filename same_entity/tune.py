"""Choose thresholds on the DEVELOPMENT split only and freeze them.

    python3 -m same_entity.tune

Writes ``config/frozen_rules.json``. This module filters pairs to
``split == "dev"`` before extracting or scoring anything, so held-out labels
are never read here.

Selection rule
--------------
* Objective: cost = 4 x false merges + 2 x false splits + 1 x review.
  False merges cost most because they silently combine two organizations'
  records; reviews cost an analyst's time but cannot be wrong.
* Ties: many threshold settings give the same dev cost. Among the tied
  settings, pick the one closest to their centroid (each parameter scaled by
  its grid range), so thresholds sit in the middle of the flat region rather
  than on the edge of a dev example.
* A second baseline is chosen to approximate the evidence-aware matcher's dev
  review rate (within ±0.05 if possible; lowest error cost 4·FM + 2·FS), so
  the two methods can be compared at a similar review rate.
"""
from __future__ import annotations

import hashlib
import json
from itertools import product

from .data import CONFIG, load_pairs, load_reports
from .extract import extract_all
from .match import BaselineConfig, EvidenceConfig, baseline, config_dict, evidence_aware
from .metrics import COST, score

GRID = [round(0.30 + 0.05 * i, 2) for i in range(15)]  # 0.30 .. 1.00
SUPPORT = [1, 2, 3, 4]


def _pick_center(cands: list[tuple]) -> tuple:
    """cands: list of (params_tuple, metrics). Pick the one nearest the centroid."""
    params = [c[0] for c in cands]
    k = len(params[0])
    lo = [min(p[i] for p in params) for i in range(k)]
    hi = [max(p[i] for p in params) for i in range(k)]
    span = [(h - l) or 1 for l, h in zip(lo, hi)]
    cen = [sum(p[i] for p in params) / len(params) for i in range(k)]
    return min(cands, key=lambda c: (sum(((c[0][i] - cen[i]) / span[i]) ** 2 for i in range(k)), c[0]))


def compute_frozen() -> dict:
    pairs = [p for p in load_pairs() if p["split"] == "dev"]
    needed = {p["left"] for p in pairs} | {p["right"] for p in pairs}
    recs = {r["source_id"]: r for r in extract_all([r for r in load_reports() if r["source_id"] in needed])}
    labels = [p["label"] for p in pairs]

    def run(fn, cfg):
        return score(labels, [fn(recs[p["left"]], recs[p["right"]], cfg)["decision"] for p in pairs])

    # Evidence-aware grid
    ev = []
    for hi, lo, sh, sm in product(GRID, GRID, SUPPORT, SUPPORT):
        if lo >= hi:
            continue
        cfg = EvidenceConfig(high_name=hi, low_name=lo, support_high=sh, support_mid=sm)
        ev.append(((hi, lo, sh, sm), run(evidence_aware, cfg)))
    best_cost = min(m["cost"] for _, m in ev)
    ev_tied = [c for c in ev if c[1]["cost"] == best_cost]
    (hi, lo, sh, sm), ev_m = _pick_center(ev_tied)
    ev_cfg = EvidenceConfig(high_name=hi, low_name=lo, support_high=sh, support_mid=sm)

    # Conservative operating point, declared before held-out scoring: a shared
    # city (1 point) can never be enough to link on its own.
    cons = [c for c in ev if c[0][2] >= 2]
    c_best = min(m["cost"] for _, m in cons)
    (chi, clo, csh, csm), cons_m = _pick_center([c for c in cons if c[1]["cost"] == c_best])
    cons_cfg = EvidenceConfig(high_name=chi, low_name=clo, support_high=csh, support_mid=csm)

    # Baseline grid
    bl = []
    for link_at, sep in product(GRID, GRID):
        if sep > link_at:
            continue
        cfg = BaselineConfig(link_at=link_at, separate_below=sep)
        bl.append(((link_at, sep), run(baseline, cfg)))
    b_best = min(m["cost"] for _, m in bl)
    (b_link, b_sep), b_m = _pick_center([c for c in bl if c[1]["cost"] == b_best])

    # Baseline at a review rate similar to the evidence-aware matcher's
    target = ev_m["review_rate"]["value"]
    near = [c for c in bl if abs(c[1]["review_rate"]["value"] - target) <= 0.05 + 1e-9]
    if not near:
        gap = min(abs(c[1]["review_rate"]["value"] - target) for c in bl)
        near = [c for c in bl if abs(abs(c[1]["review_rate"]["value"] - target) - gap) < 1e-9]
    err = lambda m: COST["false_merge"] * m["false_merges"]["n"] + COST["false_split"] * m["false_splits"]["n"]
    e_best = min(err(m) for _, m in near)
    (r_link, r_sep), r_m = _pick_center([c for c in near if err(c[1]) == e_best])

    dev_ids = [p["pair_id"] for p in pairs]
    frozen = {
        "frozen_on": "dev split only",
        "dev_pair_ids": dev_ids,
        "dev_pair_ids_sha256": hashlib.sha256(",".join(dev_ids).encode()).hexdigest(),
        "objective": "cost = 4*false_merges + 2*false_splits + 1*reviews (dev only)",
        "cost_weights": COST,
        "grid": {"similarity": GRID, "support_points": SUPPORT},
        "tie_break": "closest to the centroid of all minimum-cost settings (parameters scaled by range)",
        "evidence_aware": {"config": config_dict(ev_cfg), "dev_metrics": ev_m,
                           "tied_settings": len(ev_tied)},
        "evidence_conservative": {
            "config": config_dict(cons_cfg), "dev_metrics": cons_m,
            "selection": "lowest dev cost among evidence-aware settings with support_high >= 2",
        },
        "baseline": {"config": config_dict(BaselineConfig(b_link, b_sep)), "dev_metrics": b_m},
        "baseline_review_matched": {
            "config": config_dict(BaselineConfig(r_link, r_sep)), "dev_metrics": r_m,
            "target_review_rate": target,
            "selection": "baseline setting with dev review rate within ±0.05 of the evidence-aware rate "
                         "(or the closest available), lowest 4*FM + 2*FS",
        },
    }
    return frozen


def tune() -> dict:
    frozen = compute_frozen()
    CONFIG.mkdir(exist_ok=True)
    (CONFIG / "frozen_rules.json").write_text(json.dumps(frozen, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return frozen


if __name__ == "__main__":
    f = tune()
    for k in ("evidence_aware", "evidence_conservative", "baseline", "baseline_review_matched"):
        m = f[k]["dev_metrics"]
        print(f"{k:24s} {f[k]['config']}  FM {m['false_merges']['n']}/{m['false_merges']['d']}  "
              f"FS {m['false_splits']['n']}/{m['false_splits']['d']}  review {m['review_rate']['n']}/{m['review_rate']['d']}  "
              f"cost {m['cost']}")
