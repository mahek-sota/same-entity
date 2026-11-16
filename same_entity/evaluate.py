"""Score every pair with the FROZEN rules and write reproducible outputs.

    python3 -m same_entity.evaluate

Reads ``config/frozen_rules.json`` (produced by ``same_entity.tune`` from the
dev split). Nothing in this module changes thresholds. Labels are joined to
the matcher outputs only after each decision has been made.

Outputs (all deterministic):
    outputs/sources.json      report text exactly as parsed
    outputs/extractions.json  per-source extraction records with excerpts
    outputs/cases.json        per-pair decisions, evidence, labels
    outputs/cases.csv         flat per-pair table
    outputs/metrics.json      metrics per method and split, with n/d
"""
from __future__ import annotations

import csv
import hashlib
import json

from .data import CONFIG, OUTPUTS, load_pairs, load_reports
from .extract import extract_all
from .match import BaselineConfig, EvidenceConfig, baseline, evidence_aware
from .metrics import outcome, score

METHODS = {
    "baseline": ("Name-only baseline", baseline, BaselineConfig),
    "baseline_review_matched": ("Name-only baseline (review-matched)", baseline, BaselineConfig),
    "evidence_aware": ("Evidence-aware matcher", evidence_aware, EvidenceConfig),
    "evidence_conservative": ("Evidence-aware, conservative", evidence_aware, EvidenceConfig),
}


def load_frozen() -> dict:
    return json.loads((CONFIG / "frozen_rules.json").read_text(encoding="utf-8"))


def run_methods(a: dict, b: dict, frozen: dict) -> dict:
    out = {}
    for key, (_, fn, cls) in METHODS.items():
        out[key] = fn(a, b, cls(**frozen[key]["config"]))
    return out


def evaluate() -> dict:
    frozen = load_frozen()
    reports = load_reports()
    extractions = extract_all(reports)
    recs = {r["source_id"]: r for r in extractions}
    pairs = load_pairs()

    cases = []
    for p in pairs:
        decided = run_methods(recs[p["left"]], recs[p["right"]], frozen)  # no labels involved
        for key, res in decided.items():
            res["outcome"] = outcome(p["label"], res["decision"])
        cases.append({
            "pair_id": p["pair_id"], "split": p["split"], "left": p["left"], "right": p["right"],
            "challenge": p["challenge"], "label": p["label"], "label_note": p["note"],
            **decided,
        })

    metrics = {"methods": {k: v[0] for k, v in METHODS.items()}, "splits": {}}
    for split in ("dev", "heldout", "all"):
        sel = [c for c in cases if split == "all" or c["split"] == split]
        metrics["splits"][split] = {
            k: score([c["label"] for c in sel], [c[k]["decision"] for c in sel]) for k in METHODS
        }
    frozen_text = (CONFIG / "frozen_rules.json").read_bytes()
    metrics["frozen_rules_sha256"] = hashlib.sha256(frozen_text).hexdigest()
    metrics["configs"] = {k: frozen[k]["config"] for k in METHODS}
    metrics["dev_metrics_at_freeze_match"] = all(
        frozen[k]["dev_metrics"] == metrics["splits"]["dev"][k] for k in METHODS)

    OUTPUTS.mkdir(exist_ok=True)
    dump = lambda name, obj: (OUTPUTS / name).write_text(
        json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    dump("sources.json", reports)
    dump("extractions.json", extractions)
    dump("cases.json", cases)
    dump("metrics.json", metrics)
    write_csv(cases, OUTPUTS / "cases.csv")
    return {"cases": cases, "metrics": metrics, "extractions": extractions, "sources": reports, "frozen": frozen}


CSV_COLUMNS = ["pair_id", "split", "left", "right", "challenge", "label"]


def csv_rows(cases: list[dict]) -> list[dict]:
    rows = []
    for c in cases:
        row = {k: c[k] for k in CSV_COLUMNS}
        for key in METHODS:
            r = c[key]
            row[f"{key}_decision"] = r["decision"]
            row[f"{key}_outcome"] = r["outcome"]
            row[f"{key}_rule"] = r["rule"]
            row[f"{key}_name_similarity"] = "" if r["name_similarity"] is None else r["name_similarity"]
        e = c["evidence_aware"]
        row["evidence_support_points"] = e["support_points"]
        row["evidence_conflicts"] = ";".join(e["conflicts"])
        row["evidence_missing_fields"] = ";".join(m["field"] for m in e["missing"])
        row["evidence_for"] = " | ".join(i["detail"] for i in e["evidence_for"])
        row["evidence_against"] = " | ".join(i["detail"] for i in e["evidence_against"])
        rows.append(row)
    return rows


def write_csv(cases, path):
    rows = csv_rows(cases)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    res = evaluate()
    m = res["metrics"]
    print("frozen dev metrics reproduced:", m["dev_metrics_at_freeze_match"])
    for split in ("dev", "heldout"):
        print(f"\n== {split} ==")
        for k, label in m["methods"].items():
            s = m["splits"][split][k]
            f = lambda x: f"{s[x]['n']}/{s[x]['d']}"
            print(f"{label:38s} FM {f('false_merges'):6s} FS {f('false_splits'):6s} "
                  f"review {f('review_rate'):6s} precision(auto links) {f('precision_auto_links')}")
