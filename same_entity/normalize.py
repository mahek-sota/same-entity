"""Name, city, and person normalization.

Everything here is a small, inspectable heuristic:

* ``fold``          lowercase, strip accents, collapse punctuation.
* ``name_tokens``   fold + drop trailing legal forms (S.A., S.L., A.C., ...),
                    drop stopwords, repair letter/digit OCR confusions inside
                    words (``Verd3`` -> ``verde``), map Spanish and English
                    words through a ~70-entry bilingual lexicon so that
                    "Fundación Agua Clara" and "Clear Water Foundation" share
                    tokens, and strip a plural "s" from unknown words.
* ``compare_names`` returns a 0-1 *heuristic* similarity and the method used.
                    It is not a probability.

Known gaps (by design, documented in the report): words that are not in the
lexicon do not translate ("Growers" vs "Productores"), acronyms only match
the initials of the other name in that name's own language, and the
gazetteer only knows the cities used in the fixture.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

STOPWORDS = {
    "de", "del", "la", "el", "los", "las", "y", "e", "para", "por", "en",
    "the", "of", "for", "and", "a", "an",
}

# Spanish and English surface forms -> shared concept token.
LEXICON = {
    # organization types
    "fundacion": "foundation", "foundation": "foundation",
    "asociacion": "association", "association": "association",
    "cooperativa": "cooperative", "cooperative": "cooperative",
    "instituto": "institute", "institute": "institute",
    "centro": "center", "center": "center", "centre": "center",
    "red": "network", "network": "network",
    "colectivo": "collective", "collective": "collective",
    "comite": "committee", "committee": "committee",
    "alianza": "alliance", "alliance": "alliance",
    "ediciones": "publishing", "editorial": "publishing", "publishing": "publishing",
    # descriptive words
    "agua": "water", "aguas": "water", "water": "water",
    "clara": "clear", "claras": "clear", "claro": "clear", "clear": "clear",
    "verde": "green", "green": "green", "luz": "light", "light": "light",
    "sur": "south", "southern": "south", "south": "south",
    "norte": "north", "northern": "north", "north": "north",
    "salud": "health", "health": "health",
    "comunitaria": "community", "comunitario": "community", "community": "community",
    "andino": "andean", "andina": "andean", "andean": "andean",
    "mujeres": "women", "women": "women",
    "rurales": "rural", "rural": "rural",
    "unidas": "united", "unidos": "united", "united": "united",
    "semillas": "seed", "semilla": "seed", "seed": "seed",
    "puentes": "bridge", "puente": "bridge", "bridge": "bridge",
    "migrantes": "migrant", "migrant": "migrant",
    "esperanza": "hope", "hope": "hope",
    "nueva": "new", "nuevo": "new", "new": "new",
    "tierra": "land", "land": "land", "viva": "living", "vivo": "living", "living": "living",
    "pescadores": "fisher", "fisher": "fisher",
    "bahia": "bay", "bay": "bay", "azul": "blue", "blue": "blue",
    "productores": "producer", "producer": "producer",
    "cafe": "coffee", "coffee": "coffee",
    "organico": "organic", "organica": "organic", "organic": "organic",
    "derechos": "right", "right": "right",
    "humanos": "human", "human": "human",
    "rio": "river", "river": "river",
    "energia": "energy", "energy": "energy",
}

LEGAL_SUFFIX = re.compile(
    r"[\s,]+(S\.\s?A\.\s?S\.?|S\.\s?A\.\s?C\.?|S\.\s?A\.?|S\.\s?L\.?|A\.\s?C\.?|Inc\.?|LLC|Ltd\.?)\s*$",
    re.IGNORECASE,
)

OCR_MAP = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t"})

ACRONYM_SCORE = 0.9  # fixed heuristic value when an acronym spells the other name's initials


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def strip_legal(raw: str) -> str:
    return LEGAL_SUFFIX.sub("", raw.strip())


def ocr_fix(token: str) -> str:
    if re.search(r"[a-z]", token) and re.search(r"\d", token):
        return token.translate(OCR_MAP)
    return token


def canon(token: str) -> str:
    if token in LEXICON:
        return LEXICON[token]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        stem = token[:-1]
        return LEXICON.get(stem, stem)
    return token


def name_tokens(raw: str) -> list[str]:
    s = re.sub(r"['’]s\b", "", strip_legal(raw))
    out = []
    for t in fold(s).split():
        t = ocr_fix(t)
        if t in STOPWORDS:
            continue
        out.append(canon(t))
    return out


def initials(raw: str) -> str:
    return "".join(t[0] for t in fold(strip_legal(raw)).split() if t not in STOPWORDS)


def is_acronym(raw: str) -> bool:
    return bool(re.fullmatch(r"[A-ZÁÉÍÓÚÑ]{2,6}", raw.strip()))


def compare_names(a: str | None, b: str | None) -> tuple[float | None, str]:
    """Heuristic 0-1 similarity between two raw names, and how it was computed."""
    if not a or not b:
        return None, "missing_name"
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return None, "missing_name"
    if set(ta) == set(tb):
        return 1.0, "same_normalized_tokens"
    for x, y in ((a, b), (b, a)):
        if is_acronym(x) and not is_acronym(y) and fold(x) == initials(y):
            return ACRONYM_SCORE, "acronym_matches_initials"
    sa, sb = set(ta), set(tb)
    jaccard = len(sa & sb) / len(sa | sb)
    seq = SequenceMatcher(None, " ".join(sorted(sa)), " ".join(sorted(sb))).ratio()
    return round((jaccard + seq) / 2, 3), "token_overlap"


# --- cities -----------------------------------------------------------------
CITIES = {
    "Lima": ["lima"], "Cusco": ["cusco", "cuzco"], "Arequipa": ["arequipa"],
    "Quito": ["quito"], "Bogotá": ["bogota"], "Piura": ["piura"],
    "Chiclayo": ["chiclayo"],
    "Mexico City": ["mexico city", "ciudad de mexico", "cdmx"],
    "Monterrey": ["monterrey"], "Medellín": ["medellin"],
    "Seville": ["seville", "sevilla"], "Valencia": ["valencia"],
    "Manta": ["manta"], "Oaxaca": ["oaxaca"], "Tapachula": ["tapachula"],
    "Guadalajara": ["guadalajara"],
}
_CITY_LOOKUP = {v: k for k, vs in CITIES.items() for v in vs}


def canonical_city(raw: str) -> str | None:
    return _CITY_LOOKUP.get(fold(raw.split(",")[0]))


def person_key(raw: str) -> str:
    return fold(raw)
