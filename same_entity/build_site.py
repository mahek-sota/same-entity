"""Build ``report/index.html`` from the evaluation outputs.

    python3 -m same_entity.build_site

Reads only files in ``outputs/`` and ``config/``. Every number on the page is
computed from those files; the prose that interprets the results is written
for this fixture and is labeled as such.
"""
from __future__ import annotations

import collections
import json
import shutil
from html import escape

from .data import CONFIG, OUTPUTS, REPORT, ROOT
from .normalize import CITIES

TEMPLATE = ROOT / "same_entity" / "templates" / "report.html"

CHALLENGES = {
    "translated_name": "Translated name",
    "alias": "Alias",
    "abbreviation": "Abbreviation / acronym",
    "abbreviation_collision": "Acronym collision",
    "similar_name_shared_city": "Similar name, shared city",
    "similar_name_different_city": "Similar name, different city",
    "similar_name_missing_fields": "Similar name, missing fields",
    "missing_fields": "Missing fields",
    "conflicting_evidence": "Conflicting evidence",
    "low_quality_text": "Low-quality text",
    "embedded_instruction": "Embedded instruction",
    "shared_leader": "Shared leader",
    "easy_non_match": "Easy non-match",
}

FEATURED = [
    {"pair_id": "P04", "short": "Near-identical names",
     "title": "Fundación Agua Clara or Fundación Aguas Claras?",
     "prompt": "Both reports describe a foundation in Lima, and the names differ by two letters. Make your call before you look at the details."},
    {"pair_id": "P10", "short": "Alias, same organization",
     "title": "A cooperative and a nickname",
     "prompt": "One report uses a formal name, and the other uses a shorter alias. Is this the same organization?"},
    {"pair_id": "P17", "short": "Too thin to decide",
     "title": "A full Spanish name and a sparse English note",
     "prompt": "The names translate to each other, but the English note leaves out almost everything else."},
]

# Hand-written explanations of specific mistakes, shown only when the case is an error.
WHY = {
    "P04": "Agua Clara and Aguas Claras normalize to the same tokens (foundation, water, clear), so name-only similarity is 1.00. The founding years (2009 vs 2016) are what separate them.",
    "P05": "Both names normalize to “foundation water clear” and both reports say Lima. The cost-tuned rules let one shared city (1 point) confirm a link when names agree strongly. The conservative variant sends this pair to review.",
    "P03": "“FAC” only matches the Spanish name's initials. Comparing it with the English name “Clear Water Foundation” gives a very low score.",
    "P10": "The alias “Red Semillas” shares one word with the full name. Only the evidence-aware matcher sees the shared registry ID.",
    "P11": "“Southern Seeds Cooperative” and the alias “Red Semillas” share one concept token. Only the city agrees, and only one report has the registry ID.",
    "P22": "The acronym IASC comes from the Spanish name. The English name's initials are AICH, and the acronym rule does not translate.",
    "P49": "The foundation relocated. The reports agree on the name and disagree on the city, with no other details to corroborate. Rule E5 keeps them separate. The baseline ignores the city and linked correctly.",
    "P61": "“Growers” is not in the lexicon, so it never lines up with “Productores”, and name similarity drops to 0.67.",
    "P63": "Same limitation as IASC: the Spanish acronym APCO does not spell the English name's initials (OCGA).",
    "P69": "“Rio Claro Collective” omits “Human Rights”. The report has no city, identifier, or year to corroborate.",
}


def _pct(fr):
    return "–" if fr["value"] is None else f"{100 * fr['value']:.0f}%"


def _nd(fr):
    return f"{fr['n']} / {fr['d']} <span class=\"muted\">({_pct(fr)})</span>"


def _decision_class(d):
    return {"Link": "s-link", "Keep separate": "s-sep", "Needs review": "s-rev"}[d]


def comparison_table(metrics, split, caption):
    s = metrics["splits"][split]
    base_reviews = s["baseline"]["review_rate"]["n"]
    rows = []
    for key, label in metrics["methods"].items():
        m = s[key]
        delta = m["review_rate"]["n"] - base_reviews
        primary = key in ("baseline", "evidence_aware")
        rows.append(
            f"<tr class=\"{'primary' if primary else ''}\"><td>{escape(label)}</td>"
            f"<td class=num>{_nd(m['false_merges'])}</td><td class=num>{_nd(m['false_splits'])}</td>"
            f"<td class=num>{_nd(m['review_rate'])}</td>"
            f"<td class=num>{'±0' if delta == 0 else f'{delta:+d}'} pairs</td>"
            f"<td class=num>{_nd(m['precision_auto_links'])}</td></tr>")
    return (f"<div class=tablewrap><table><caption class=\"sr\">{escape(caption)}</caption><thead><tr><th>Method</th>"
            "<th>False merges<br><span class=muted>/ true non-matches</span></th>"
            "<th>False splits<br><span class=muted>/ true matches</span></th>"
            "<th>Sent to review<br><span class=muted>/ all pairs</span></th>"
            "<th>Review Δ<br><span class=muted>vs baseline</span></th>"
            "<th>Precision of auto links<br><span class=muted>correct / all links</span></th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>")


def headline(metrics):
    h = metrics["splits"]["heldout"]
    b, e = h["baseline"], h["evidence_aware"]
    be = b["false_merges"]["n"] + b["false_splits"]["n"]
    ee = e["false_merges"]["n"] + e["false_splits"]["n"]
    br, er = b["review_rate"]["n"], e["review_rate"]["n"]
    if ee < be and er <= br:
        verdict = "made fewer automatic errors than the name-only baseline without sending more pairs to review"
    elif ee < be:
        verdict = f"made fewer automatic errors than the baseline but sent {er - br} more pair(s) to review"
    elif ee == be and er > br:
        verdict = (f"<strong>did not beat the name-only baseline</strong>: both made the same number of automatic errors, "
                   f"and the evidence-aware matcher sent {er - br} more pair(s) to review")
    elif ee == be:
        verdict = "made the same number of automatic errors as the baseline"
    else:
        verdict = "<strong>made more automatic errors than the name-only baseline</strong>"
    d = metrics["splits"]["dev"]
    return (f"<p class=callout>On the held-out set ({h['baseline']['pairs']} pairs), the evidence-aware matcher {verdict} "
            f"({e['false_merges']['n']} false merges and {e['false_splits']['n']} false splits vs "
            f"{b['false_merges']['n']} and {b['false_splits']['n']}; review {er}/{e['pairs']} vs {br}/{b['pairs']}). "
            f"On the development set, which was used to choose the thresholds, it made "
            f"{d['evidence_aware']['false_merges']['n']} false merge(s) and {d['evidence_aware']['false_splits']['n']} false split(s) "
            f"vs the baseline's {d['baseline']['false_merges']['n']} and {d['baseline']['false_splits']['n']}. "
            "These numbers describe this 73-pair synthetic fixture only.</p>")


def disagreements(cases, ex):
    rows = []
    for c in cases:
        if c["split"] != "heldout" or c["baseline"]["decision"] == c["evidence_aware"]["decision"]:
            continue
        rows.append(
            f"<tr><td class=num>{c['pair_id']}</td><td>{escape(ex[c['left']]['name']['value'] if ex[c['left']]['name'] else '—')} / "
            f"{escape(ex[c['right']]['name']['value'] if ex[c['right']]['name'] else '—')}</td>"
            f"<td>{'Same org' if c['label'] == 'match' else 'Different orgs'}</td>"
            f"<td><span class=\"stamp {_decision_class(c['baseline']['decision'])}\" style=\"font-size:11px;padding:4px 6px\">{c['baseline']['decision']}</span><br><span class=small>{c['baseline']['outcome']}</span></td>"
            f"<td><span class=\"stamp {_decision_class(c['evidence_aware']['decision'])}\" style=\"font-size:11px;padding:4px 6px\">{c['evidence_aware']['decision']}</span><br><span class=small>{c['evidence_aware']['outcome']}</span></td>"
            f"<td class=small><code>{c['evidence_aware']['rule']}</code></td></tr>")
    return ("<div class=tablewrap><table><thead><tr><th>Pair</th><th>Names</th><th>Ground truth</th><th>Baseline</th>"
            f"<th>Evidence-aware</th><th>Rule</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>")


def mistakes(cases, ex, src):
    blocks = []
    for key, label in (("evidence_aware", "Evidence-aware matcher"), ("baseline", "Name-only baseline")):
        errs = [c for c in cases if c[key]["outcome"] in ("false merge", "false split")]
        items = []
        for c in errs:
            r = c[key]
            quotes = []
            for item in r["evidence_for"] + r["evidence_against"]:
                for side in ("left", "right"):
                    ref = item[side]
                    if ref and item["signal"] != "name_similarity":
                        quotes.append(f"{ref['source_id']}: “{ref['excerpt']}”")
            names = f"{ex[c['left']]['name']['value'] if ex[c['left']]['name'] else '(none)'} ↔ {ex[c['right']]['name']['value'] if ex[c['right']]['name'] else '(none)'}"
            items.append(
                f"<div class=mistake><div class=meta>{c['pair_id']} · {'held-out' if c['split'] == 'heldout' else 'dev'} · "
                f"{escape(CHALLENGES.get(c['challenge'], c['challenge']))} · <strong>{r['outcome']}</strong> · rule <code>{r['rule']}</code></div>"
                f"<div><strong>{escape(names)}</strong></div>"
                + (f"<blockquote>{escape(' · '.join(dict.fromkeys(quotes)))}</blockquote>" if quotes else "")
                + f"<p class=small style=\"margin:4px 0 0\">{escape(WHY.get(c['pair_id'], r['rule_text']))}</p></div>")
        blocks.append(f"<h4>{label}: {len(errs)} automatic errors across both splits</h4><div class=mistakes>{''.join(items)}</div>")
    return "".join(blocks)


def tradeoff_table(metrics):
    rows = []
    for split, name in (("dev", "Development"), ("heldout", "Held-out")):
        for key in ("evidence_aware", "evidence_conservative"):
            m = metrics["splits"][split][key]
            rows.append(f"<tr><td>{name}</td><td>{escape(metrics['methods'][key])}</td><td class=num>{_nd(m['false_merges'])}</td>"
                        f"<td class=num>{_nd(m['false_splits'])}</td><td class=num>{_nd(m['review_rate'])}</td>"
                        f"<td class=num>{_nd(m['matches_auto_linked'])}</td></tr>")
    return ("<div class=tablewrap><table><thead><tr><th>Split</th><th>Operating point</th><th>False merges</th><th>False splits</th>"
            f"<th>Sent to review</th><th>True matches auto-linked</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>")


def build():
    sources = json.loads((OUTPUTS / "sources.json").read_text(encoding="utf-8"))
    extractions = json.loads((OUTPUTS / "extractions.json").read_text(encoding="utf-8"))
    cases = json.loads((OUTPUTS / "cases.json").read_text(encoding="utf-8"))
    metrics = json.loads((OUTPUTS / "metrics.json").read_text(encoding="utf-8"))
    frozen = json.loads((CONFIG / "frozen_rules.json").read_text(encoding="utf-8"))
    cases_csv = (OUTPUTS / "cases.csv").read_text(encoding="utf-8")
    orgs = (ROOT / "data" / "organizations.csv").read_text(encoding="utf-8").strip().split("\n")[1:]

    ex = {x["source_id"]: x for x in extractions}
    src = {s["source_id"]: s for s in sources}
    langs = collections.Counter(s["lang"] for s in sources)
    split_counts = {sp: collections.Counter(c["label"] for c in cases if c["split"] == sp) for sp in ("dev", "heldout")}
    chal_counts = collections.Counter(c["challenge"] for c in cases)
    field_counts = {f: sum(1 for x in extractions if x[f]) for f in
                    ("name", "city", "report_date", "founded_year", "registry_id", "domain", "leader")}
    failures = [(x["source_id"], f) for x in extractions for f in x["failures"]]
    flags = [(x["source_id"], f) for x in extractions for f in x["flags"]]
    ev, cons = frozen["evidence_aware"]["config"], frozen["evidence_conservative"]["config"]
    bl, blr = frozen["baseline"]["config"], frozen["baseline_review_matched"]["config"]
    n_orgs = len(orgs)
    n_clusters = len({o.split(",")[1] for o in orgs})

    rm_note = ("The review-matched baseline came out identical to the cost-tuned baseline: no other baseline setting had a "
               f"development review rate within ±5 points of the evidence-aware matcher's {frozen['baseline_review_matched']['target_review_rate']:.0%} "
               "with fewer errors." if bl == blr else
               "The review-matched baseline uses thresholds chosen on development data to match the evidence-aware review rate.")

    report_html = f"""
<h2>Detailed report</h2>
<nav class=toc aria-label="Report sections"><a href="#r-results">Results</a><a href="#r-saw">What I saw</a><a href="#r-data">Dataset</a><a href="#r-holdout">Holdout</a><a href="#r-extract">Extraction</a><a href="#r-match">Matching</a><a href="#r-thresh">Thresholds</a><a href="#r-mistakes">Mistakes</a><a href="#r-review">Review trade-off</a><a href="#r-limits">Limitations</a><a href="#r-download">Downloads</a></nav>

<section id=r-results>
<h3>Results on the held-out set</h3>
{comparison_table(metrics, "heldout", "Held-out results")}
{headline(metrics)}
<p class=small><strong>Reading the table.</strong> A <em>false merge</em> is an automatic Link between different organizations (denominator: true non-match pairs). A <em>false split</em> is an automatic Keep separate between reports of the same organization (denominator: true match pairs). <em>Needs review</em> is a system decision, not a label, so a reviewed pair is never counted as an error. It is counted as manual work instead. {rm_note}</p>
<h4>Development set (used to choose thresholds)</h4>
{comparison_table(metrics, "dev", "Development results")}
</section>

<section id=r-saw>
<h3>What I saw</h3>
<p>The two methods disagreed on {sum(1 for c in cases if c['split'] == 'heldout' and c['baseline']['decision'] != c['evidence_aware']['decision'])} held-out pairs:</p>
{disagreements(cases, ex)}
<ul>
<li><strong>The held-out traps were easier for names alone than the development traps.</strong> In development, Agua Clara and Aguas Claras normalize to identical tokens, so only other evidence can separate them. The held-out near-duplicates (Centro Esperanza vs Centro Nueva Esperanza, the Bahía Azul cooperative vs association, the Río Claro collective vs committee) score 0.58–0.76. That is below the baseline's frozen {bl['separate_below']:.2f} cut, so the baseline kept them apart without needing any evidence.</li>
<li><strong>The frozen baseline is very strict.</strong> It links at ≥ {bl['link_at']:.2f} and separates below {bl['separate_below']:.2f}, which in practice means “link only identical normalized names.” Strict thresholds explain why it made no held-out false merges, and also why it split translated names whose vocabulary is outside the lexicon.</li>
<li><strong>Where evidence helped:</strong> a declared acronym plus a shared city linked APCO correctly (P62). A city conflict separated the two organizations that both call themselves APCO (P65). A shared founding year and city moved “Growers Alliance” from a false split to review (P61).</li>
<li><strong>Where evidence hurt:</strong> a relocated foundation reports a different city in each report. The evidence-aware matcher read that as a conflict and split it (P49, a genuine failure), while the baseline linked it correctly. The two Bahía Azul fishing organizations share a president and a city, which earned 3 support points and sent them to review rather than separating them (P56, P57).</li>
</ul>
</section>

<section id=r-data>
<h3>Dataset</h3>
<p>{len(sources)} short synthetic reports ({langs['es']} Spanish, {langs['en']} English) about {n_orgs} fictional organizations in {n_clusters} clusters of confusable names. Every organization, person, registry ID (<code>SR-#####</code>), and <code>.example</code> domain is invented. I labeled {len(cases)} pairs by hand: {sum(1 for c in cases if c['label'] == 'match')} true matches and {sum(1 for c in cases if c['label'] == 'non_match')} true non-matches. Labels are generated from a source → fictional-entity table, and a test checks that every pair label agrees with it.</p>
<div class=tablewrap><table><thead><tr><th>Challenge</th><th>Pairs</th></tr></thead><tbody>
{''.join(f'<tr><td>{escape(CHALLENGES[k])}</td><td class=num>{v}</td></tr>' for k, v in sorted(chal_counts.items(), key=lambda kv: -kv[1]))}
</tbody></table></div>
</section>

<section id=r-holdout>
<h3>Holdout strategy</h3>
<ul>
<li><strong>Split by fictional organization cluster.</strong> Each cluster holds an organization and its look-alikes. Clusters C01–C06 are development ({split_counts['dev']['match']} matches, {split_counts['dev']['non_match']} non-matches) and C07–C11 are held-out ({split_counts['heldout']['match']} matches, {split_counts['heldout']['non_match']} non-matches). No organization, alias, or report appears in both splits, and a test enforces this.</li>
<li><strong>Matchers never see ground truth.</strong> They receive extraction records only. Entity IDs, labels, and splits live in separate files and are joined after decisions are made.</li>
<li><strong>Frozen before scoring.</strong> <code>same_entity.tune</code> filters to development pairs before extracting anything, then writes <code>config/frozen_rules.json</code>. Held-out scoring reads that file and changes nothing. Frozen-rules SHA-256: <code>{metrics['frozen_rules_sha256'][:16]}…</code></li>
<li><strong>Caveat.</strong> I wrote the rules and both splits. The holdout protects the threshold choices, not the fixture from its author's expectations. After seeing development results I added one pre-declared operating point (the conservative variant), and it was frozen before any held-out pair was scored.</li>
</ul>
</section>

<section id=r-extract>
<h3>Extraction rules</h3>
<p>A small regex extractor (<code>same_entity/extract.py</code>) supports these document patterns. Every value keeps its exact excerpt and character offsets, and a test checks that <code>text[start:end] == excerpt</code> for every value and every piece of evidence.</p>
<div class=tablewrap><table><thead><tr><th>Field</th><th>Patterns (English / Spanish)</th><th>Found in</th></tr></thead><tbody>
<tr><td>Name</td><td><code>Organization:</code> <code>Org:</code> / <code>Organización:</code> <code>Entidad:</code> at line start. Fallback sentence: <code>&lt;Name&gt;, based in &lt;City&gt;</code> / <code>&lt;Name&gt;, con sede en &lt;City&gt;</code></td><td class=num>{field_counts['name']}/{len(sources)}</td></tr>
<tr><td>Aliases</td><td>Trailing <code>(ABC)</code> after the name. <code>Also known as:</code> / <code>También conocida como:</code></td><td class=num>{sum(len(x['aliases']) for x in extractions)} aliases</td></tr>
<tr><td>City</td><td><code>Location:</code> <code>City:</code> / <code>Ciudad:</code> <code>Sede:</code>, looked up in a {len(CITIES)}-city gazetteer (Sevilla → Seville, Ciudad de México → Mexico City)</td><td class=num>{field_counts['city']}/{len(sources)}</td></tr>
<tr><td>Report date</td><td><code>Date:</code> / <code>Fecha:</code>, ISO dates only. Recorded but not used for matching, because it describes the report, not the organization.</td><td class=num>{field_counts['report_date']}/{len(sources)}</td></tr>
<tr><td>Founded year</td><td><code>founded|established|opened [in] YYYY</code> / <code>fundado|creado|establecido en YYYY</code></td><td class=num>{field_counts['founded_year']}/{len(sources)}</td></tr>
<tr><td>Registry ID</td><td><code>Registry ID:</code> / <code>Registro:</code> followed by <code>SR-#####</code></td><td class=num>{field_counts['registry_id']}/{len(sources)}</td></tr>
<tr><td>Web domain</td><td><code>Web:</code> <code>Website:</code> followed by a <code>.example</code> domain</td><td class=num>{field_counts['domain']}/{len(sources)}</td></tr>
<tr><td>Leader</td><td><code>Director(a):</code> <code>Coordinator:</code> <code>Coordinador(a):</code> <code>President(e):</code> followed by two or three capitalized words</td><td class=num>{field_counts['leader']}/{len(sources)}</td></tr>
</tbody></table></div>
<p><strong>Missing stays unknown.</strong> A field not found is <code>null</code>. Nothing is guessed. <strong>Recorded failures:</strong> {'; '.join(f'{sid} {escape(f["field"])}: {escape(f["reason"])}' for sid, f in failures)}.</p>
<p><strong>Embedded instructions.</strong> Lines that look like instructions to an automated system are stored as flags: {'; '.join(f'{sid}: “{escape(f["excerpt"])}”' for sid, f in flags)}. Neither matcher reads free text or flags. Tests confirm that removing these lines leaves every decision unchanged, and that the organization named in the instruction is not extracted as an alias.</p>
</section>

<section id=r-match>
<h3>Matching logic</h3>
<h4>Name similarity (both methods)</h4>
<p>Names are lowercased and stripped of accents, punctuation, legal forms (S.A., S.L., A.C., S.A.S.), and stopwords (de, del, the, of, …). Words go through a ~70-entry Spanish/English lexicon (fundación → foundation, agua → water, …), and letter/digit OCR confusions inside words are repaired. Similarity is the mean of token Jaccard and a character ratio over sorted tokens. Identical token sets score 1.00, and an all-caps acronym that spells the other name's initials scores 0.90. <strong>This score is a heuristic, not a probability.</strong></p>
<h4>Name-only baseline</h4>
<p>Compares the primary extracted names only. Link if similarity ≥ {bl['link_at']:.2f}, Keep separate if &lt; {bl['separate_below']:.2f}, otherwise Needs review. If a name could not be extracted, it returns Needs review.</p>
<h4>Evidence-aware matcher</h4>
<p>Uses the best similarity across names and declared aliases, plus <em>support points</em> for agreeing details: shared domain +3, founding year +2, leader +2, city +1, declared alias +1. Different leaders are recorded but never counted against a match, because leadership changes. A domain, founding year, or city that differs is a <em>conflict</em>. The first rule that applies wins:</p>
<div class=tablewrap><table><thead><tr><th>Rule</th><th>Condition</th><th>Decision</th></tr></thead><tbody>
<tr><td><code>E1</code></td><td>Both have registry IDs and they differ</td><td>Keep separate</td></tr>
<tr><td><code>E2</code></td><td>Same registry ID and name similarity ≥ {ev['name_floor']:.2f} (E2b: below it)</td><td>Link (E2b: Needs review)</td></tr>
<tr><td><code>E3</code></td><td>No usable name on one side</td><td>Needs review</td></tr>
<tr><td><code>E4</code></td><td>Any conflict, and support ≥ {ev['mixed_support']}</td><td>Needs review</td></tr>
<tr><td><code>E5</code></td><td>Any conflict, and support &lt; {ev['mixed_support']}</td><td>Keep separate</td></tr>
<tr><td><code>E6/E7</code></td><td>Similarity ≥ high (<code>{ev['high_name']:.2f}</code>): support ≥ <code>{ev['support_high']}</code> links, otherwise review</td><td>Link / Needs review</td></tr>
<tr><td><code>E8/E9</code></td><td>low ≤ similarity &lt; high: support ≥ <code>{ev['support_mid']}</code> links, otherwise review</td><td>Link / Needs review</td></tr>
<tr><td><code>E10/E11</code></td><td>Similarity &lt; low (<code>{ev['low_name']:.2f}</code>): support ≥ <code>{ev['support_mid']}</code> goes to review, otherwise separate</td><td>Needs review / Keep separate</td></tr>
</tbody></table></div>
<p>Missing evidence never adds a conflict. With identical names and nothing else, rule E7 sends a pair to review rather than separating it.</p>
</section>

<section id=r-thresh>
<h3>Threshold choices</h3>
<p>I grid-searched similarity thresholds from 0.30 to 1.00 in steps of 0.05, and support requirements from 1 to 4, on development pairs only. The objective was <code>4 × false merges + 2 × false splits + 1 × reviews</code>. False merges cost most because they silently combine two organizations' records. A review costs time but cannot be wrong. Ties ({frozen['evidence_aware']['tied_settings']} evidence-aware settings shared the best cost) were broken by choosing the setting nearest the centroid of the tied region, so thresholds don't sit on the edge of a single development example.</p>
<div class=tablewrap><table><thead><tr><th>Method</th><th>Frozen settings</th></tr></thead><tbody>
<tr><td>Name-only baseline</td><td class=num>link ≥ {bl['link_at']:.2f}, separate &lt; {bl['separate_below']:.2f}</td></tr>
<tr><td>Name-only baseline (review-matched)</td><td class=num>link ≥ {blr['link_at']:.2f}, separate &lt; {blr['separate_below']:.2f}</td></tr>
<tr><td>Evidence-aware</td><td class=num>high {ev['high_name']:.2f}, low {ev['low_name']:.2f}, support_high {ev['support_high']}, support_mid {ev['support_mid']}</td></tr>
<tr><td>Evidence-aware, conservative</td><td class=num>high {cons['high_name']:.2f}, low {cons['low_name']:.2f}, support_high {cons['support_high']}, support_mid {cons['support_mid']}</td></tr>
</tbody></table></div>
<p>The cost-tuned evidence-aware setting chose <code>support_high = {ev['support_high']}</code>{', so a shared city alone can confirm two strongly similar names' if ev['support_high'] == 1 else ''}. Development data had several translated-name matches backed only by a city, and one misleading shared-city non-match (P05). Under the declared costs, linking them was cheaper than reviewing them. I kept that result rather than changing the costs after the fact, and froze the conservative variant, which requires at least 2 points, as a second operating point.</p>
</section>

<section id=r-mistakes>
<h3>Representative mistakes</h3>
<p>Every automatic error made by the two main methods, with the excerpts each decision relied on:</p>
{mistakes(cases, ex, src)}
</section>

<section id=r-review>
<h3>Why more review can mean fewer false merges, and more work</h3>
<p>The conservative variant refuses to link on a shared city alone. That removes the only development false merge (P05). The cost is many more pairs sent to review, most of which are true matches that a person must now confirm:</p>
{tradeoff_table(metrics)}
<p>Neither operating point is free. Raising the evidence bar turns would-be errors into reviews, and it also turns correct automatic links into reviews. Which point is better depends on what a false merge costs relative to a reviewer's time, and that is a decision for the people using the system, not one the fixture can settle.</p>
</section>

<section id=r-limits>
<h3>Limitations</h3>
<ul>
<li>{len(cases)} pairs from {len(sources)} reports is a fixture, not a benchmark. One case changes a rate by several points, so the comparison above is evidence about this fixture only.</li>
<li>The extractor only handles the formats listed above. Real documents would need more patterns, or a learned extractor whose output keeps the same excerpt-level traceability.</li>
<li>Translation relies on a small lexicon. Unknown words (“Growers”) and acronyms across languages (IASC vs “Andean Institute for Community Health”) fail.</li>
<li>A city conflict is treated as evidence of different organizations, which is wrong for relocations and multi-office organizations (P49).</li>
<li>A shared leader counts as support, which pushed two different organizations with the same president to review (P56, P57).</li>
<li>The gazetteer cannot tell apart cities with the same name (for example, Valencia in Spain and in Venezuela).</li>
</ul>
</section>

<section id=r-download>
<h3>Downloads and reproduction</h3>
<div class=dl><a id=dl-csv href="downloads/cases.csv" download="same-entity-cases.csv">Per-case results (CSV)</a><a id=dl-json href="downloads/cases.json" download="same-entity-cases.json">Per-case results (JSON)</a><a id=dl-metrics href="downloads/metrics.json" download="same-entity-metrics.json">Metrics (JSON)</a></div>
<p class=small style="margin-top:12px">Regenerate everything from the repository:</p>
<pre class=cmd>python3 -m same_entity.tune        # dev split only -&gt; config/frozen_rules.json
python3 -m same_entity.evaluate    # frozen rules -&gt; outputs/
python3 -m same_entity.build_site  # outputs/ -&gt; report/index.html
python3 -m pytest -q tests</pre>
</section>
"""

    data = {
        "sources": {s["source_id"]: {"lang": s["lang"], "text": s["text"]} for s in sources},
        "extractions": ex,
        "cases": cases,
        "cases_csv": cases_csv,
        "metrics": metrics,
        "featured": FEATURED,
        "challenge_labels": CHALLENGES,
    }
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
    html = (TEMPLATE.read_text(encoding="utf-8")
            .replace("{{REPORT_HTML}}", report_html)
            .replace("{{GENERATED_NOTE}}", f"from <code>outputs/</code> (frozen rules {metrics['frozen_rules_sha256'][:12]})")
            .replace("{{DATA_JSON}}", data_json))
    REPORT.mkdir(exist_ok=True)
    (REPORT / "index.html").write_text(html, encoding="utf-8")
    dl = REPORT / "downloads"
    dl.mkdir(exist_ok=True)
    for name in ("cases.csv", "cases.json", "metrics.json"):
        shutil.copyfile(OUTPUTS / name, dl / name)
    return REPORT / "index.html"


if __name__ == "__main__":
    p = build()
    print(f"wrote {p} ({p.stat().st_size // 1024} KB)")
