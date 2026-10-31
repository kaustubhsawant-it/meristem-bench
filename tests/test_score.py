import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import score  # noqa: E402


def row(arm, ttype, served, subtype="removed", **kw):
    base = {
        "arm": arm, "type": ttype, "subtype": subtype, "served_target": served, "served_any": served,
        "n_served": int(served), "tokens": 10, "ms": 1.0, "error": None, "query_style": "query",
        "represented": True, "root_manifest": True, "caught": False,
    }
    base.update(kw)
    return base


class ScoreTest(unittest.TestCase):
    def test_wilson_matches_known_values(self):
        lo, hi = score.wilson(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=3)
        self.assertAlmostEqual(hi, 0.7634, places=3)
        lo, hi = score.wilson(0, 20)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.1611, places=3)
        self.assertTrue(math.isnan(score.wilson(0, 0)[0]))

    def test_primary_population_drops_unrepresented_and_nested_manifests(self):
        rows = [row("meristem", "falsified", False), row("meristem", "falsified", True, represented=False), row("meristem", "falsified", True, root_manifest=False)]
        s = score.summarize(rows, "primary", "query")
        self.assertEqual(s["arms"]["meristem"]["stale_serve"]["n"], 1)
        s_all = score.summarize(rows, "all", "query")
        self.assertEqual(s_all["arms"]["meristem"]["stale_serve"]["n"], 3)

    def test_errors_are_counted_and_excluded(self):
        rows = [row("meristem", "live", True), row("meristem", "live", True, error="boom")]
        s = score.summarize(rows, "primary", "query")
        self.assertEqual((s["errors"], s["arms"]["meristem"]["live_recall"]["n"]), (1, 1))

    def arms(self, me_stale, base_stale, me_live=100, base_live=100, n=100):
        def mk(name, stale, live):
            rows = [row(name, "falsified", i < stale) for i in range(n)]
            rows += [row(name, "live", i < live) for i in range(n)]
            return rows
        rows = mk("meristem", me_stale, me_live)
        for b in score.BASELINES:
            rows += mk(b, base_stale, base_live)
        return score.summarize(rows, "primary", "query")["arms"]

    def test_verdict_claim(self):
        v = score.verdict(self.arms(me_stale=5, base_stale=60))
        self.assertEqual(v["verdict"], "claim")

    def test_verdict_reposition_when_gap_small_or_recall_lost(self):
        self.assertEqual(score.verdict(self.arms(me_stale=40, base_stale=50))["verdict"], "reposition")
        self.assertEqual(score.verdict(self.arms(me_stale=5, base_stale=60, me_live=80))["verdict"], "reposition")

    def test_verdict_pivot_when_no_gap(self):
        self.assertEqual(score.verdict(self.arms(me_stale=50, base_stale=50))["verdict"], "pivot")
        self.assertEqual(score.verdict(self.arms(me_stale=70, base_stale=50))["verdict"], "pivot")

    def test_verdict_insufficient_with_few_falsified_tasks(self):
        self.assertEqual(score.verdict(self.arms(me_stale=1, base_stale=5, n=10))["verdict"], "insufficient")


if __name__ == "__main__":
    unittest.main()
