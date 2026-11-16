# Same Entity?

When two reports name an organization differently, do they provide enough
evidence to link them, keep them separate, or send them for human review?

A small, reproducible entity-resolution prototype on **synthetic** English and
Spanish reports. It compares a name-only baseline with an evidence-aware
matcher, traces every decision back to exact source excerpts, and publishes the
results as a static three-tab site in `report/`.

> Every report, organization, person, registry ID (`SR-#####`), and `.example`
> domain in this repository is fictional. Cities are real places used only as
> locations.

## Requirements

- Python 3.10+ (developed with 3.11). The pipeline uses only the standard library.
- `pytest` for the test runner (optional: the tests also run with `unittest`).

## Commands

Run from the repository root.

```bash
# 1. Choose thresholds on the DEVELOPMENT split only and freeze them
python3 -m same_entity.tune          # -> config/frozen_rules.json

# 2. Score dev + held-out pairs with the frozen rules
python3 -m same_entity.evaluate      # -> outputs/{sources,extractions,cases,metrics}.json, outputs/cases.csv

# 3. Generate the site from the outputs
python3 -m same_entity.build_site    # -> report/index.html, report/downloads/

# 4. Tests
python3 -m pytest -q tests
# or, without pytest:
python3 -m unittest discover -s tests -t tests

# 5. Serve the report locally, then open http://127.0.0.1:8000/#about
python3 -m http.server 8000 --directory report --bind 127.0.0.1
```

`make all` runs steps 1–4, and `make serve` runs step 5. Every step is
deterministic: rerunning produces byte-identical outputs.

`report/index.html` is self-contained (inline CSS and JS, evaluation data
embedded, no external requests), so you can also open it directly from disk.

## What's inside

```
data/
  reports.txt            53 synthetic reports (### <id> | <lang> blocks, text preserved exactly)
  organizations.csv      22 fictional organizations -> cluster -> split (ground truth)
  source_entities.csv    report -> fictional organization (ground truth)
  pairs.csv              73 hand-labeled pairs (37 match / 36 non-match) + challenge tag + note
same_entity/
  extract.py             documented regex extractor (patterns listed in the module docstring)
  normalize.py           name folding, bilingual lexicon, acronym rule, city gazetteer
  match.py               name-only baseline and evidence-aware matcher (inspectable rules)
  metrics.py             false merges, false splits, review rate, precision (with n/d)
  tune.py                dev-only grid search -> config/frozen_rules.json
  evaluate.py            frozen rules -> outputs/
  build_site.py          outputs/ -> report/index.html
  templates/report.html  page template (About text, styles, demo JS)
config/frozen_rules.json thresholds frozen on dev, with dev metrics and dev pair IDs
outputs/                 per-source extractions, per-case decisions + evidence, metrics
report/                  the deployable static site
tests/                   focused tests (see below)
```

### Leakage guards

- The split is by **fictional organization cluster**: each organization and its
  look-alikes are entirely in dev (C01–C06, 40 pairs) or entirely in held-out
  (C07–C11, 33 pairs). A test enforces that no organization appears in both.
- Matchers receive extraction records only. Entity IDs, labels, and splits are
  joined after decisions are made, and a test checks that extraction records
  carry no ground-truth keys.
- `tune.compute_frozen` filters to `split == "dev"` before extracting anything.
  `evaluate.py` only reads the frozen file. A test re-runs tuning and checks
  that it reproduces `config/frozen_rules.json`.
- Caveat: one author wrote both the rules and the fixture. The holdout protects
  threshold choices, not the fixture from its author's expectations.

### Tests (`tests/`)

| Test | What it checks |
|---|---|
| `ClosestNameFalseMatch` | Agua Clara vs Aguas Claras (same city): the baseline links, the evidence-aware matcher separates on the founding-year conflict and quotes `2009` / `2016`. Also pins the documented city-only false merge (P05) and the conservative variant routing it to review. |
| `MissingEvidence` | Missing fields route to review, not to Keep separate. Unknown fields stay `None`. A failed extraction routes to review. |
| `EmbeddedInstructions` | Instruction-like lines are flagged. Removing them changes no decision. The organization named in the instruction is not extracted. "Approve the link" text does not produce a Link. |
| `ExcerptFidelity` | `text[start:end] == excerpt` for every extracted value, alias, flag, failure, and every evidence item saved in `outputs/cases.json`. |
| `Splits` | No organization crosses splits, labels agree with entity IDs, balance and size checks, no ground truth in matcher inputs, and tuning is reproducible and dev-only. |

## Results (from `outputs/metrics.json`)

Held-out split (33 pairs: 16 true matches, 17 true non-matches):

| Method | False merges | False splits | Sent to review | Precision of auto links |
|---|---|---|---|---|
| Name-only baseline | 0 / 17 | 3 / 16 | 2 / 33 | 12 / 12 |
| Name-only baseline (review-matched) | 0 / 17 | 3 / 16 | 2 / 33 | 12 / 12 |
| Evidence-aware matcher | 0 / 17 | 3 / 16 | 4 / 33 | 11 / 11 |
| Evidence-aware, conservative | 0 / 17 | 3 / 16 | 10 / 33 | 5 / 5 |

Development split (40 pairs, used to choose thresholds): baseline 2/19 false
merges, 4/21 false splits, 5/40 reviews. Evidence-aware 1/19, 2/21, 4/40.

**On the held-out set, the evidence-aware matcher did not beat the baseline.**
Both methods made the same errors, and it sent two more pairs to review. The
held-out look-alike names scored below the baseline's strict 0.85 cut, so names
alone were enough there. A relocated organization (P49) is a genuine failure
of the evidence-aware rules. The report tab discusses this in detail. These
results describe this fixture only.

## Deploying to Vercel (not done; run only when you choose to)

The site is static: no build step, no framework, no environment variables.

**Dashboard:** import the repository, set **Root Directory** to `report`,
**Framework Preset** to *Other*, and leave the Build Command and Output
Directory empty. Deploy.

**CLI:**

```bash
npm i -g vercel
cd report
vercel            # preview deployment; answer the prompts (framework: Other)
vercel --prod     # production
```

Direct links `/#about`, `/#demo`, and `/#report` work because routing is
hash-based on a single page.

## 60-second demo walkthrough

1. **0–10 s · About.** Open `/#about`. One question, synthetic data, and a clear
   line between professional work and prototype results.
2. **10–30 s · Demo, case 1.** Open `/#demo`. Fundación Agua Clara vs Fundación
   Aguas Claras, both in Lima. Pick *Link*, then *Reveal decisions*. The
   baseline links (a false merge). The evidence-aware matcher keeps them
   separate because the highlighted founding years, `2009` and `2016`, conflict.
   The ground truth is revealed separately.
3. **30–40 s · Case 2.** "Red Semillas" vs Cooperativa Semillas del Sur. The
   baseline splits them. The shared registry ID `SR-22017` lets the
   evidence-aware matcher link them.
4. **40–50 s · Case 3.** A sparse English note. The evidence-aware matcher
   says *Needs review*, and the missing fields are listed as unknown, not as
   evidence against a match.
5. **50–60 s · Report.** Open `/#report`. The table shows the honest result:
   on held-out data, the evidence-aware matcher did not beat the baseline.
   Scroll to *Representative mistakes* and *Downloads*.
