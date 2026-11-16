"""Two matchers that see only extraction records (never labels or entity IDs).

Both return one of LINK, SEPARATE, REVIEW, plus the rule that fired, the
evidence for and against with excerpts, and the fields that were missing.
Scores here are heuristic points and similarities. They are not probabilities.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from .normalize import compare_names, fold, person_key

LINK, SEPARATE, REVIEW = "Link", "Keep separate", "Needs review"
DECISIONS = (LINK, SEPARATE, REVIEW)

# Support points for agreeing evidence. Fixed by design, not tuned.
WEIGHTS = {"domain": 3, "founded_year": 2, "leader": 2, "city": 1, "declared_alias": 1}
COMPARED_FIELDS = ["registry_id", "domain", "founded_year", "leader", "city"]


@dataclass(frozen=True)
class BaselineConfig:
    link_at: float      # name similarity >= link_at  -> Link
    separate_below: float  # name similarity < separate_below -> Keep separate


@dataclass(frozen=True)
class EvidenceConfig:
    high_name: float      # "names agree strongly" threshold
    low_name: float       # below this, names disagree
    support_high: int     # points needed to Link when names agree strongly
    support_mid: int      # points needed to Link with mid similarity (and to rescue low similarity into review)
    name_floor: float = 0.35  # minimum name similarity for a registry-ID match to auto-link
    mixed_support: int = 2    # points that turn "conflict" into "mixed evidence -> review"


def _ref(field: dict | None) -> dict | None:
    if not field:
        return None
    return {k: field[k] for k in ("source_id", "value", "excerpt", "start", "end")}


def _name_item(a: dict, b: dict, score, method, a_field, b_field, direction) -> dict:
    return {
        "signal": "name_similarity",
        "field": "name",
        "direction": direction,
        "weight": None,
        "detail": (f"Name similarity {score:.2f} ({method.replace('_', ' ')}); heuristic, not a probability."
                   if score is not None else "A name is missing on at least one side."),
        "left": _ref(a_field),
        "right": _ref(b_field),
    }


def _result(method, decision, rule, rule_text, sim, items, missing, extra=None) -> dict:
    out = {
        "method": method,
        "decision": decision,
        "rule": rule,
        "rule_text": rule_text,
        "name_similarity": sim,
        "evidence_for": [i for i in items if i["direction"] == "for"],
        "evidence_against": [i for i in items if i["direction"] == "against"],
        "evidence_neutral": [i for i in items if i["direction"] == "neutral"],
        "missing": missing,
    }
    if extra:
        out.update(extra)
    return out


# --- baseline ------------------------------------------------------------------

def baseline(a: dict, b: dict, cfg: BaselineConfig) -> dict:
    """Compare the primary extracted names only."""
    sim, method = compare_names(a["name"] and a["name"]["value"], b["name"] and b["name"]["value"])
    missing = [{"field": "name", "left_present": bool(a["name"]), "right_present": bool(b["name"])}] \
        if sim is None else []
    if sim is None:
        item = _name_item(a, b, None, method, a["name"], b["name"], "neutral")
        return _result("baseline", REVIEW, "B0_missing_name",
                       "A name could not be extracted on one side, so there is nothing to compare.",
                       None, [item], missing)
    if sim >= cfg.link_at:
        d, rule, txt, dirn = LINK, "B1_similar_names", f"Name similarity {sim:.2f} ≥ {cfg.link_at:.2f}.", "for"
    elif sim < cfg.separate_below:
        d, rule, txt, dirn = SEPARATE, "B2_different_names", f"Name similarity {sim:.2f} < {cfg.separate_below:.2f}.", "against"
    else:
        d, rule, txt, dirn = REVIEW, "B3_in_between", (f"Name similarity {sim:.2f} is between "
                                                       f"{cfg.separate_below:.2f} and {cfg.link_at:.2f}."), "neutral"
    item = _name_item(a, b, sim, method, a["name"], b["name"], dirn)
    return _result("baseline", d, rule, txt, sim, [item], missing)


# --- evidence-aware ------------------------------------------------------------

def _variants(rec: dict) -> list[tuple[dict, bool]]:
    out = [(rec["name"], False)] if rec["name"] else []
    return out + [(a, True) for a in rec["aliases"]]


def _best_name(a: dict, b: dict):
    best = (None, "missing_name", None, None)
    for fa, _ in _variants(a):
        for fb, _ in _variants(b):
            sim, method = compare_names(fa["value"], fb["value"])
            if sim is not None and (best[0] is None or sim > best[0]):
                best = (sim, method, fa, fb)
    return best


def _declared_alias_match(a: dict, b: dict):
    """An alias explicitly declared in one report equals a name/alias in the other."""
    for x, y, flip in ((a, b, False), (b, a, True)):
        for alias in x["aliases"]:
            for fy, _ in _variants(y):
                if fold(alias["value"]) == fold(fy["value"]):
                    return (fy, alias) if flip else (alias, fy)
    return None


def _field_value(field: str, rec: dict):
    f = rec[field]
    if not f:
        return None
    return person_key(f["value"]) if field == "leader" else f["value"]


def evidence_aware(a: dict, b: dict, cfg: EvidenceConfig) -> dict:
    items: list[dict] = []
    missing: list[dict] = []
    support = 0
    conflicts: list[str] = []

    sim, method, fa, fb = _best_name(a, b)
    if sim is None:
        missing.append({"field": "name", "left_present": bool(a["name"]), "right_present": bool(b["name"])})

    for field in COMPARED_FIELDS:
        va, vb = _field_value(field, a), _field_value(field, b)
        if va is None or vb is None:
            missing.append({"field": field, "left_present": va is not None, "right_present": vb is not None})
            continue
        same = va == vb
        if field == "leader" and not same:
            items.append({"signal": "leader_differs", "field": field, "direction": "neutral", "weight": 0,
                          "detail": "Different leaders are not counted against a match; leadership changes.",
                          "left": _ref(a[field]), "right": _ref(b[field])})
            continue
        if same:
            w = WEIGHTS.get(field)
            if w:
                support += w
            items.append({"signal": f"{field}_match", "field": field, "direction": "for", "weight": w,
                          "detail": f"Both reports give {field.replace('_', ' ')} '{a[field]['value']}'.",
                          "left": _ref(a[field]), "right": _ref(b[field])})
        else:
            if field != "registry_id":
                conflicts.append(field)
            items.append({"signal": f"{field}_conflict", "field": field, "direction": "against", "weight": None,
                          "detail": (f"{field.replace('_', ' ').capitalize()} differs: "
                                     f"'{a[field]['value']}' vs '{b[field]['value']}'."),
                          "left": _ref(a[field]), "right": _ref(b[field])})

    am = _declared_alias_match(a, b)
    if am:
        support += WEIGHTS["declared_alias"]
        items.append({"signal": "declared_alias_match", "field": "alias", "direction": "for",
                      "weight": WEIGHTS["declared_alias"],
                      "detail": f"A declared alias matches exactly: '{am[0]['value']}' = '{am[1]['value']}'.",
                      "left": _ref(am[0]), "right": _ref(am[1])})

    def done(decision, rule, text, name_dir):
        name = _name_item(a, b, sim, method, fa, fb, name_dir)
        return _result("evidence_aware", decision, rule, text, sim, [name] + items, missing,
                       {"support_points": support, "conflicts": conflicts})

    hi, lo = cfg.high_name, cfg.low_name
    name_dir = "neutral" if sim is None else ("for" if sim >= hi else "against" if sim < lo else "neutral")
    ra, rb = _field_value("registry_id", a), _field_value("registry_id", b)

    if ra and rb and ra != rb:
        return done(SEPARATE, "E1_registry_conflict",
                    "Both reports carry registry IDs and they differ. Identifier conflicts override names.", name_dir)
    if ra and rb and ra == rb:
        if sim is None or sim >= cfg.name_floor:
            return done(LINK, "E2_registry_match", "Both reports carry the same registry ID.", name_dir)
        return done(REVIEW, "E2b_registry_match_names_disagree",
                    f"Same registry ID but name similarity {sim:.2f} < {cfg.name_floor:.2f}; possible rename or error.",
                    name_dir)
    if sim is None:
        return done(REVIEW, "E3_missing_name", "No usable name on one side and no shared identifier.", name_dir)
    if conflicts and support >= cfg.mixed_support:
        return done(REVIEW, "E4_mixed_evidence",
                    f"Conflicting {', '.join(conflicts)} but {support} support point(s) agree (≥ {cfg.mixed_support}).",
                    name_dir)
    if conflicts:
        return done(SEPARATE, "E5_conflict_without_support",
                    f"Conflicting {', '.join(conflicts)} and only {support} support point(s).", name_dir)
    if sim >= hi:
        if support >= cfg.support_high:
            return done(LINK, "E6_names_agree_and_corroborated",
                        f"Name similarity {sim:.2f} ≥ {hi:.2f}, {support} support point(s) ≥ {cfg.support_high}, no conflicts.",
                        name_dir)
        return done(REVIEW, "E7_names_agree_thin_evidence",
                    f"Name similarity {sim:.2f} ≥ {hi:.2f} but only {support} support point(s) "
                    f"(< {cfg.support_high}). Missing evidence is not treated as a mismatch.", name_dir)
    if sim >= lo:
        if support >= cfg.support_mid:
            return done(LINK, "E8_partial_names_well_corroborated",
                        f"Name similarity {sim:.2f} in [{lo:.2f}, {hi:.2f}), {support} support point(s) ≥ {cfg.support_mid}.",
                        name_dir)
        return done(REVIEW, "E9_partial_names_thin_evidence",
                    f"Name similarity {sim:.2f} in [{lo:.2f}, {hi:.2f}) with {support} support point(s) "
                    f"(< {cfg.support_mid}).", name_dir)
    if support >= cfg.support_mid:
        return done(REVIEW, "E10_names_differ_but_corroborated",
                    f"Name similarity {sim:.2f} < {lo:.2f} but {support} support point(s); possible rename.", name_dir)
    return done(SEPARATE, "E11_names_differ",
                f"Name similarity {sim:.2f} < {lo:.2f} and only {support} support point(s).", name_dir)


def config_dict(cfg) -> dict:
    return asdict(cfg)
