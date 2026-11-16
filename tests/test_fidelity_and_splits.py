"""Source excerpt fidelity, leakage guards, and reproducibility."""
import inspect
import json
import unittest

from same_entity import match, tune
from same_entity.data import OUTPUTS, load_organizations, load_pairs, load_reports, load_source_entities
from same_entity.evaluate import evaluate
from same_entity.extract import FIELDS, extract_all

from helpers import frozen

TEXT = {r["source_id"]: r["text"] for r in load_reports()}


def _check_ref(test, ref):
    test.assertIn(ref["source_id"], TEXT)
    test.assertEqual(TEXT[ref["source_id"]][ref["start"]:ref["end"]], ref["excerpt"])
    test.assertTrue(ref["excerpt"].strip())


class ExcerptFidelity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = evaluate()

    def test_every_extracted_value_points_to_its_exact_excerpt(self):
        n = 0
        for r in extract_all(load_reports()):
            for f in FIELDS:
                if r[f]:
                    _check_ref(self, r[f]); n += 1
            for item in r["aliases"] + r["flags"]:
                _check_ref(self, item); n += 1
            for fail in r["failures"]:
                if "start" in fail:
                    _check_ref(self, fail)
        self.assertGreater(n, 200)

    def test_every_evidence_item_in_saved_cases_is_quoted_from_source(self):
        cases = json.loads((OUTPUTS / "cases.json").read_text(encoding="utf-8"))
        n = 0
        for c in cases:
            for key in ("baseline", "evidence_aware", "evidence_conservative"):
                res = c[key]
                for item in res["evidence_for"] + res["evidence_against"] + res["evidence_neutral"]:
                    for side, sid in (("left", c["left"]), ("right", c["right"])):
                        ref = item[side]
                        if ref:
                            self.assertEqual(ref["source_id"], sid)
                            _check_ref(self, ref); n += 1
        self.assertGreater(n, 300)

    def test_report_text_is_preserved(self):
        saved = json.loads((OUTPUTS / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual({s["source_id"]: s["text"] for s in saved}, TEXT)


class Splits(unittest.TestCase):
    def test_no_organization_in_both_splits(self):
        pairs = load_pairs()
        ent = load_source_entities()
        by_split = {"dev": set(), "heldout": set()}
        for p in pairs:
            by_split[p["split"]] |= {ent[p["left"]], ent[p["right"]]}
        self.assertEqual(by_split["dev"] & by_split["heldout"], set())
        clusters = {}
        for o in load_organizations():
            clusters.setdefault(o["cluster"], set()).add(o["split"])
        self.assertTrue(all(len(s) == 1 for s in clusters.values()))

    def test_labels_agree_with_entity_ids_and_are_balanced(self):
        ent = load_source_entities()
        pairs = load_pairs()
        for p in pairs:
            expected = "match" if ent[p["left"]] == ent[p["right"]] else "non_match"
            self.assertEqual(p["label"], expected, p["pair_id"])
        self.assertTrue(60 <= len(pairs) <= 80)
        m = sum(p["label"] == "match" for p in pairs)
        self.assertLessEqual(abs(m - (len(pairs) - m)), 3)
        self.assertEqual(len({(p["left"], p["right"]) for p in pairs}), len(pairs))

    def test_matcher_inputs_carry_no_ground_truth(self):
        forbidden = {"entity_id", "label", "split", "cluster", "pair_id"}
        for r in extract_all(load_reports()):
            self.assertFalse(forbidden & set(r), r["source_id"])
        src = inspect.getsource(match)
        for word in ("entity_id", "\"label\"", "split"):
            self.assertNotIn(word, src)

    def test_tuning_reads_only_dev_and_is_reproducible(self):
        f = frozen()
        dev = [p["pair_id"] for p in load_pairs() if p["split"] == "dev"]
        self.assertEqual(f["dev_pair_ids"], dev)
        src = inspect.getsource(tune.compute_frozen)
        self.assertIn('p["split"] == "dev"', src)
        self.assertNotIn("heldout", src)
        again = tune.compute_frozen()  # re-run on dev without writing
        self.assertEqual(again, f)


if __name__ == "__main__":
    unittest.main()
