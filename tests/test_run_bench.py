import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_tasks  # noqa: E402
import run_bench  # noqa: E402
from test_gen_tasks import SyntheticRepo  # noqa: E402

from meristem import embeddings  # noqa: E402


def pyproject(*deps: str) -> str:
    items = ",\n".join("    " + json.dumps(d) for d in deps)
    return f'[project]\nname = "demo"\nversion = "1"\ndependencies = [\n{items}\n]\n'


class RunBenchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = SyntheticRepo(self._tmp.name)
        self.repo.commit({"pyproject.toml": pyproject("httpx>=0.27", "rich>=13", "typer>=0.12", "pydantic>=2")})
        self.repo.commit({"pyproject.toml": pyproject("httpx>=0.27", "typer>=0.12", "pydantic>=3", "attrs>=23")})
        self.embedder = run_bench.CachedEmbedder(embeddings.HashEmbedder())
        tasks, _, _ = gen_tasks.generate(self._tmp.name, repo_name="demo", abstain=3, seed=1)
        self.tasks = tasks
        rows = run_bench.run(tasks, self._tmp.name, Path(self._tmp.name) / "out", list(run_bench.ARMS), {a: -2.0 for a in run_bench.FLOORED}, self.embedder, styles=("query_typed",), progress=False)
        self.rows = rows

    def row(self, name, subtype, arm):
        ids = [t["id"] for t in self.tasks if t["dep"]["name"] == name and t["subtype"] == subtype]
        self.assertEqual(len(ids), 1, (name, subtype))
        found = [r for r in self.rows if r["task_id"] == ids[0] and r["arm"] == arm]
        self.assertEqual(len(found), 1)
        return found[0]

    def test_no_arm_errors(self):
        self.assertEqual([r["error"] for r in self.rows if r["error"]], [])

    def test_removed_dependency_is_caught_only_with_liveness(self):
        full = self.row("rich", "removed", "meristem")
        self.assertFalse(full["served_target"])
        self.assertTrue(full["caught"])
        self.assertTrue(self.row("rich", "removed", "meristem_no_liveness")["served_target"])
        self.assertTrue(self.row("rich", "removed", "grep")["served_target"])
        self.assertFalse(self.row("rich", "removed", "oracle")["served_target"])

    def test_repinned_dependency_is_caught(self):
        self.assertFalse(self.row("pydantic", "repinned", "meristem")["served_target"])
        self.assertTrue(self.row("pydantic", "repinned", "meristem_no_liveness")["served_target"])

    def test_unchanged_dependency_is_still_served(self):
        for arm in ("meristem", "meristem_no_liveness", "grep", "oracle", "whole_file"):
            self.assertTrue(self.row("httpx", "unchanged", arm)["served_target"], arm)

    def test_whole_file_never_abstains_and_oracle_always_does(self):
        for r in self.rows:
            if r["type"] == "abstain" and r["arm"] == "whole_file":
                self.assertTrue(r["served_any"])
            if r["type"] == "abstain" and r["arm"] in ("oracle", "grep"):
                self.assertFalse(r["served_any"])

    def test_every_task_is_represented_by_the_ingester(self):
        self.assertTrue(all(r["represented"] for r in self.rows))


if __name__ == "__main__":
    unittest.main()
