import json
from functools import lru_cache

from same_entity.data import CONFIG, load_reports
from same_entity.extract import extract
from same_entity.match import BaselineConfig, EvidenceConfig


@lru_cache(maxsize=None)
def frozen():
    return json.loads((CONFIG / "frozen_rules.json").read_text(encoding="utf-8"))


def ev_cfg(key="evidence_aware"):
    return EvidenceConfig(**frozen()[key]["config"])


def bl_cfg(key="baseline"):
    return BaselineConfig(**frozen()[key]["config"])


@lru_cache(maxsize=None)
def texts():
    return {r["source_id"]: r for r in load_reports()}


def rec(sid, text=None):
    r = texts()[sid]
    return extract(sid, r["lang"], r["text"] if text is None else text)


def blank(sid, lang, text):
    return extract(sid, lang, text)
