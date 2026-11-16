"""Loading the fixture files.

Ground truth (entity IDs, labels, splits) lives in separate files from the
report text. The matcher only ever receives extraction records; see
``evaluate.py`` for where labels are joined back in for scoring.
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUTS = ROOT / "outputs"
CONFIG = ROOT / "config"
REPORT = ROOT / "report"


def load_reports(path: Path = DATA / "reports.txt") -> list[dict]:
    """Parse ``### <id> | <lang>`` blocks. Text is preserved exactly."""
    reports: list[dict] = []
    current = None
    lines: list[str] = []

    def flush():
        if current is not None:
            while lines and not lines[-1].strip():
                lines.pop()
            reports.append({**current, "text": "\n".join(lines)})

    for raw in path.read_text(encoding="utf-8").split("\n"):
        if raw.startswith("### "):
            flush()
            sid, lang = [p.strip() for p in raw[4:].split("|")]
            current, lines = {"source_id": sid, "lang": lang}, []
        elif current is not None:
            lines.append(raw)
    flush()
    return reports


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_organizations() -> list[dict]:
    return _read_csv(DATA / "organizations.csv")


def load_source_entities() -> dict[str, str]:
    return {r["source_id"]: r["entity_id"] for r in _read_csv(DATA / "source_entities.csv")}


def load_pairs() -> list[dict]:
    """Labeled pairs with the split derived from the organizations' cluster."""
    orgs = {o["entity_id"]: o for o in load_organizations()}
    ent = load_source_entities()
    pairs = _read_csv(DATA / "pairs.csv")
    for p in pairs:
        splits = {orgs[ent[p["left"]]]["split"], orgs[ent[p["right"]]]["split"]}
        if len(splits) != 1:
            raise ValueError(f"{p['pair_id']} spans splits {splits}")
        p["split"] = splits.pop()
    return pairs
