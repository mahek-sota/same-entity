"""A small rule-based extractor for the fixture's report formats.

Supported document patterns
---------------------------
Labeled lines (label at the start of a line, case- and accent-insensitive):

    name     Organization: / Organisation: / Org: / Organización: / Entidad:
             A trailing "(XYZ)" in the value is recorded as a declared alias.
    city     Location: / City: / Ciudad: / Sede: / Ubicación:
             The value before the first comma is looked up in a small gazetteer.
    date     Date: / Fecha:  -- ISO YYYY-MM-DD only.
    alias    Also known as: / También conocida como: / Alias:

Inline patterns (anywhere in the text):

    registry Registry ID: / Registro: / Reg. No.:  followed by SR-#####
    domain   Web: / Website: / Sitio web:  followed by a *.example domain
    leader   Director(a) / Coordinator / Coordinador(a) / President(e/a):
             followed by two or three capitalized words
    founded  "founded|established|opened [in] YYYY" or
             "fundado/a|creado/a|establecido/a en YYYY"
    alias    "<extracted name> (<alias>)" elsewhere in the text

Prose fallback, only when no name label exists, at the start of a line:

    "<Name>, based in <City>, ..."   /   "[El|La] <Name>, con sede en <City>, ..."

Rules of the extractor
----------------------
* Every extracted value keeps its exact excerpt and character offsets into the
  source text, so ``text[start:end] == excerpt`` always holds.
* A field that is not found stays ``None`` (unknown). Nothing is inferred.
* Problems are written to ``failures``: no recognizable name, an unparseable
  date, a registry-like token with non-digit characters, a city outside the
  gazetteer. Identifiers and labels are never OCR-repaired.
* Lines that look like instructions to an automated system are recorded in
  ``flags`` as data. Neither matcher reads ``flags`` or the free text.
"""
from __future__ import annotations

import re
from datetime import date

from .normalize import canonical_city, fold, ocr_fix

NAME_LABELS = r"organization|organisation|org|organizaci[oó]n|entidad"
CITY_LABELS = r"location|city|ciudad|sede|ubicaci[oó]n"
DATE_LABELS = r"date|fecha"
ALIAS_LABELS = r"also known as|tambi[eé]n conocid[ao] como|alias"

RE_NAME = re.compile(rf"^[ \t]*(?:{NAME_LABELS})[ \t]*:[ \t]*(?P<v>[^\n]+?)[ \t]*$", re.I | re.M)
RE_CITY = re.compile(rf"^[ \t]*(?:{CITY_LABELS})[ \t]*:[ \t]*(?P<v>[^\n]+?)[ \t]*$", re.I | re.M)
RE_DATE = re.compile(rf"^[ \t]*(?:{DATE_LABELS})[ \t]*:[ \t]*(?P<v>[^\n]+?)[ \t]*$", re.I | re.M)
RE_ALIAS = re.compile(rf"^[ \t]*(?:{ALIAS_LABELS})[ \t]*:[ \t]*(?P<v>[^\n]+?)[ \t.]*$", re.I | re.M)
RE_REGISTRY = re.compile(r"\b(?:registry id|registro|reg\. no\.)[ \t]*:[ \t]*(?P<v>[^\s.,;]+)", re.I)
RE_REGISTRY_LIKE = re.compile(r"\bSR-[0-9A-Z]{5}\b")
RE_DOMAIN = re.compile(r"\b(?:web|website|sitio web)[ \t]*:[ \t]*(?P<v>[a-z0-9-]+(?:\.[a-z0-9-]+)*\.example)\b", re.I)
_CAP = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+"
RE_LEADER = re.compile(
    rf"\b(?i:director|directora|coordinator|coordinadora|coordinador|president|presidente|presidenta)"
    rf"[ \t]*:[ \t]*(?P<v>{_CAP}(?:[ \t]+{_CAP}){{1,2}})",
)
RE_FOUNDED = re.compile(
    r"\b(?:(?:founded|established|opened)(?:[ \t]+in)?|(?:fundad|cread|establecid)[oa][ \t]+en)"
    r"[ \t]+(?P<v>(?:19|20)\d{2})\b",
    re.I,
)
RE_PROSE = re.compile(
    r"^(?:(?:The|El|La|Los|Las)[ \t]+)?(?P<name>[A-ZÁÉÍÓÚÑ][^\n,]{1,80}?),[ \t]+"
    r"(?:based in|con sede en)[ \t]+(?P<city>[A-ZÁÉÍÓÚÑ][^\n,.]*)",
    re.M,
)
RE_INSTRUCTION = re.compile(
    r"\b(ignore|ignora|ignorar|approve|aprueba|aprobar|skip (?:human )?review|"
    r"instruction|instrucci[oó]n|matching system|para el sistema)\b",
    re.I,
)
RE_LABEL_LINE = re.compile(r"^[ \t]*(?P<label>[^\s:]{2,20})[ \t]*:", re.M)
RE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FIELDS = ["name", "city", "report_date", "founded_year", "registry_id", "domain", "leader"]


def _span(source_id: str, text: str, m: re.Match, group: str = "v", value=None, pattern: str = "") -> dict:
    s, e = m.span(group)
    return {
        "value": value if value is not None else m.group(group),
        "excerpt": text[s:e],
        "start": s,
        "end": e,
        "source_id": source_id,
        "pattern": pattern,
    }


def _sub(source_id: str, text: str, start: int, end: int, value, pattern: str) -> dict:
    return {"value": value, "excerpt": text[start:end], "start": start, "end": end,
            "source_id": source_id, "pattern": pattern}


def _line_at(text: str, pos: int) -> tuple[int, int]:
    s = text.rfind("\n", 0, pos) + 1
    e = text.find("\n", pos)
    return s, (len(text) if e == -1 else e)


def extract(source_id: str, lang: str, text: str) -> dict:
    rec: dict = {"source_id": source_id, "lang": lang, **{f: None for f in FIELDS},
                 "aliases": [], "flags": [], "failures": []}
    sid = source_id

    # --- name (label first, then prose fallback) ---------------------------
    m = RE_NAME.search(text)
    if m:
        vs, ve = m.span("v")
        value = m.group("v")
        pm = re.match(r"^(?P<n>.*?)\s*\((?P<a>[^)]+)\)\s*$", value)
        if pm and pm.group("n"):
            rec["name"] = _sub(sid, text, vs + pm.start("n"), vs + pm.end("n"), pm.group("n"), "name label")
            rec["aliases"].append(_sub(sid, text, vs + pm.start("a"), vs + pm.end("a"), pm.group("a"),
                                       "parenthetical after name"))
        else:
            v = value.rstrip(" ;")
            if v.endswith(".") and not re.search(r"\b[A-Z]\.$", v):  # keep "S.A." intact
                v = v[:-1]
            rec["name"] = _sub(sid, text, vs, vs + len(v), v, "name label")
    else:
        pm = RE_PROSE.search(text)
        if pm:
            rec["name"] = _span(sid, text, pm, "name", pattern="prose: '<Name>, based in / con sede en <City>'")
            city_raw = pm.group("city").strip()
            canon = canonical_city(city_raw)
            if canon:
                rec["city"] = _span(sid, text, pm, "city", value=canon, pattern="prose: based in / con sede en")
            else:
                rec["failures"].append({"field": "city", "reason": f"'{city_raw}' is not in the gazetteer",
                                        **_span(sid, text, pm, "city")})

    if rec["name"] is None:
        failure = {"field": "name",
                   "reason": "No recognized organization label and no '<Name>, based in <City>' sentence."}
        for lm in RE_LABEL_LINE.finditer(text):
            label = ocr_fix(fold(lm.group("label")))
            if re.fullmatch(r"org|organization|organisation|organizacion|entidad", label):
                ls, le = _line_at(text, lm.start("label"))
                failure["reason"] = (f"Line '{text[ls:le]}' looks like an OCR-damaged organization label; "
                                     "the extractor does not repair labels.")
                failure.update(_sub(sid, text, ls, le, None, "damaged label"))
                break
        rec["failures"].append(failure)

    # --- aliases -------------------------------------------------------------
    for am in RE_ALIAS.finditer(text):
        rec["aliases"].append(_span(sid, text, am, pattern="alias label"))
    if rec["name"]:
        for am in re.finditer(re.escape(rec["name"]["value"]) + r"\s*\((?P<v>[^)]+)\)", text):
            if all(a["start"] != am.start("v") for a in rec["aliases"]):
                rec["aliases"].append(_span(sid, text, am, pattern="parenthetical after name"))
        name_key = fold(rec["name"]["value"])
        rec["aliases"] = [a for a in rec["aliases"] if fold(a["value"]) != name_key]

    # --- city ------------------------------------------------------------------
    if rec["city"] is None:
        cm = RE_CITY.search(text)
        if cm:
            canon = canonical_city(cm.group("v"))
            if canon:
                rec["city"] = _span(sid, text, cm, value=canon, pattern="city label")
            else:
                rec["failures"].append({"field": "city",
                                        "reason": f"'{cm.group('v')}' is not in the gazetteer",
                                        **_span(sid, text, cm)})

    # --- report date -----------------------------------------------------------
    dm = RE_DATE.search(text)
    if dm:
        v = dm.group("v").strip()
        ok = False
        if RE_ISO.match(v):
            try:
                date.fromisoformat(v)
                ok = True
            except ValueError:
                pass
        if ok:
            rec["report_date"] = _span(sid, text, dm, pattern="date label")
        else:
            rec["failures"].append({"field": "report_date", "reason": f"Date value '{v}' is not a valid ISO date.",
                                    **_span(sid, text, dm)})

    # --- founded year ------------------------------------------------------------
    fm = RE_FOUNDED.search(text)
    if fm:
        rec["founded_year"] = _span(sid, text, fm, value=int(fm.group("v")), pattern="founded/fundado en YYYY")
        rec["founded_year"]["context"] = text[fm.start():fm.end()]

    # --- registry ID ---------------------------------------------------------------
    rm = RE_REGISTRY.search(text)
    if rm and re.fullmatch(r"SR-\d{5}", rm.group("v")):
        rec["registry_id"] = _span(sid, text, rm, pattern="registry label")
    for tm in RE_REGISTRY_LIKE.finditer(text):
        if not re.fullmatch(r"SR-\d{5}", tm.group(0)):
            rec["failures"].append({"field": "registry_id",
                                    "reason": f"Registry-like token '{tm.group(0)}' contains non-digits; "
                                              "identifiers are never OCR-corrected.",
                                    **_sub(sid, text, tm.start(), tm.end(), None, "registry-like token")})

    # --- domain / leader ----------------------------------------------------------
    wm = RE_DOMAIN.search(text)
    if wm:
        rec["domain"] = _span(sid, text, wm, value=wm.group("v").lower(), pattern="web label")
    lm = RE_LEADER.search(text)
    if lm:
        rec["leader"] = _span(sid, text, lm, pattern="leader label")

    # --- instruction-like text (recorded, never acted on) ------------------------
    seen = set()
    for im in RE_INSTRUCTION.finditer(text):
        ls, le = _line_at(text, im.start())
        if (ls, le) in seen:
            continue
        seen.add((ls, le))
        rec["flags"].append({"type": "instruction_like_text",
                             "note": "Treated as source data. Neither matcher reads free text or flags.",
                             **_sub(sid, text, ls, le, None, "instruction-like line")})
    return rec


def extract_all(reports: list[dict]) -> list[dict]:
    return [extract(r["source_id"], r["lang"], r["text"]) for r in reports]
