import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import gen_tasks  # noqa: E402

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


class SyntheticRepo:
    def __init__(self, root: str):
        self.root = root
        self._git("init", "-q", "-b", "main")

    def _git(self, *args: str) -> str:
        res = subprocess.run(["git", "-C", self.root, *args], capture_output=True, text=True, env=GIT_ENV, check=True)
        return res.stdout.strip()

    def commit(self, files: dict, message: str = "c") -> str:
        for rel, content in files.items():
            path = Path(self.root, rel)
            if content is None:
                if path.exists():
                    path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self._git("add", "-A")
        self._git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", message)
        return self._git("rev-parse", "HEAD")


def pkg(**deps) -> str:
    return json.dumps({"name": "demo", "dependencies": deps}, indent=2) + "\n"


class GenTasksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = SyntheticRepo(self._tmp.name)

    def by(self, tasks, ttype, dep):
        return [t for t in tasks if t["type"] == ttype and t["dep"]["name"] == dep]

    def test_falsified_and_live_from_package_json(self):
        c1 = self.repo.commit({"package.json": pkg(**{"left-pad": "1", "lodash": "4"})})
        c2 = self.repo.commit({"package.json": pkg(**{"left-pad": "1", "lodash": "4", "express": "4"})})
        c3 = self.repo.commit({"package.json": pkg(**{"lodash": "4", "express": "4"})})
        tasks, dropped, _ = gen_tasks.generate(self._tmp.name, repo_name="demo")
        self.assertEqual(dropped, 0)
        fals = self.by(tasks, "falsified", "left-pad")
        self.assertEqual(len(fals), 1)
        self.assertEqual((fals[0]["commit_a"], fals[0]["commit_b"]), (c1, c3))
        self.assertEqual(fals[0]["gold"], "do_not_serve")
        live = self.by(tasks, "live", "express")
        self.assertEqual(len(live), 1)
        self.assertEqual((live[0]["commit_a"], live[0]["commit_b"]), (c2, c3))
        self.assertEqual(live[0]["gold"], "serve")
        self.assertEqual(len(self.by(tasks, "live", "lodash")), 1)
        for t in tasks:
            self.assertEqual(t["stratum"], "checkable")
            self.assertEqual(t["repo"], "demo")
            self.assertEqual(t["check"]["kind"], "declared")

    def test_requirements_and_pyproject(self):
        r1 = self.repo.commit({"requirements.txt": "requests>=2\nFlask_Login==0.6  # auth\n-r other.txt\n"})
        r2 = self.repo.commit({"requirements.txt": "requests>=2\n"})
        p1 = self.repo.commit({"pyproject.toml": '[project]\nname="x"\ndependencies=["httpx>=0.2", "rich"]\n'})
        p2 = self.repo.commit({"pyproject.toml": '[project]\nname="x"\ndependencies=["httpx>=0.2"]\n'})
        tasks, _, _ = gen_tasks.generate(self._tmp.name)
        fl = self.by(tasks, "falsified", "flask-login")
        self.assertEqual([(t["commit_a"], t["commit_b"]) for t in fl], [(r1, r2)])
        rich = self.by(tasks, "falsified", "rich")
        self.assertEqual([(t["commit_a"], t["commit_b"]) for t in rich], [(p1, p2)])
        self.assertEqual(len(self.by(tasks, "live", "httpx")), 1)
        self.assertEqual(len(self.by(tasks, "live", "requests")), 1)

    def test_output_is_deterministic_and_sorted(self):
        self.repo.commit({"package.json": pkg(**{f"dep{i}": "1" for i in range(12)})})
        self.repo.commit({"package.json": pkg(**{f"dep{i}": "1" for i in range(6)})})
        self.repo.commit({"package.json": pkg(**{**{f"dep{i}": "1" for i in range(6)}, "extra": "1"})})
        first, _, _ = gen_tasks.generate(self._tmp.name, seed=7, max_per_type=3)
        second, _, _ = gen_tasks.generate(self._tmp.name, seed=7, max_per_type=3)
        self.assertEqual(first, second)
        self.assertEqual(sum(t["type"] == "falsified" for t in first), 3)
        self.assertEqual(first, sorted(first, key=lambda t: (t["type"], t["id"])))
        other, _, _ = gen_tasks.generate(self._tmp.name, seed=8, max_per_type=3)
        self.assertEqual(len(other), len(first))

    def test_excluded_paths_are_ignored(self):
        self.repo.commit({"tests/fixtures/package.json": pkg(a="1"), "package.json": pkg(b="1")})
        self.repo.commit({"tests/fixtures/package.json": pkg(), "package.json": pkg(b="1", c="1")})
        tasks, _, _ = gen_tasks.generate(self._tmp.name)
        self.assertTrue(all(t["check"]["path"] == "package.json" for t in tasks))

    def test_invalid_json_commit_does_not_reset_state(self):
        c1 = self.repo.commit({"package.json": pkg(a="1", b="1")})
        self.repo.commit({"package.json": "<<<<<<< conflict\n"})
        c3 = self.repo.commit({"package.json": pkg(b="1")})
        tasks, _, _ = gen_tasks.generate(self._tmp.name)
        fals = self.by(tasks, "falsified", "a")
        self.assertEqual([(t["commit_a"], t["commit_b"]) for t in fals], [(c1, c3)])
        self.assertEqual(len(self.by(tasks, "live", "b")), 1)

    def test_deleted_manifest_creates_no_falsified_task(self):
        self.repo.commit({"package.json": pkg(a="1")})
        self.repo.commit({"package.json": None})
        tasks, _, _ = gen_tasks.generate(self._tmp.name)
        self.assertEqual(tasks, [])

    def test_name_in_another_section_is_not_a_declaration(self):
        self.repo.commit({"package.json": '{"dependencies": {"left-pad": "1"}}\n'})
        self.repo.commit({"package.json": '{"scripts": {"left-pad": "echo"}, "dependencies": {}}\n'})
        tasks, dropped, verified = gen_tasks.generate(self._tmp.name)
        self.assertEqual(len(tasks), 1)
        self.assertEqual((tasks[0]["type"], tasks[0]["subtype"], dropped), ("falsified", "removed", 0))

    def test_changed_pin_is_falsified_and_unchanged_pin_is_live(self):
        c1 = self.repo.commit({"package.json": pkg(a="1.0.0", b="2.0.0")})
        c2 = self.repo.commit({"package.json": pkg(a="1.0.0", b="3.0.0")})
        c3 = self.repo.commit({"package.json": pkg(a="1.0.0", b="3.0.0", c="1")})
        tasks, _, _ = gen_tasks.generate(self._tmp.name)
        repinned = self.by(tasks, "falsified", "b")
        self.assertEqual([(t["subtype"], t["commit_a"], t["commit_b"]) for t in repinned], [("repinned", c1, c2)])
        self.assertEqual(repinned[0]["dep"]["spec_at_a"], "2.0.0")
        live_a = self.by(tasks, "live", "a")
        self.assertEqual([(t["commit_a"], t["commit_b"]) for t in live_a], [(c1, c3)])
        live_b = self.by(tasks, "live", "b")
        self.assertEqual([(t["commit_a"], t["commit_b"]) for t in live_b], [(c2, c3)])

    def test_root_manifest_flag(self):
        self.repo.commit({"package.json": pkg(a="1"), "web/package.json": pkg(w="1")})
        self.repo.commit({"package.json": pkg(), "web/package.json": pkg()})
        tasks, _, _ = gen_tasks.generate(self._tmp.name)
        flags = {t["dep"]["name"]: t["root_manifest"] for t in tasks}
        self.assertEqual(flags, {"a": True, "w": False})

    def test_queries_name_the_package_and_not_the_path(self):
        self.repo.commit({"package.json": pkg(a="1")})
        self.repo.commit({"package.json": pkg()})
        task = gen_tasks.generate(self._tmp.name)[0][0]
        self.assertIn("a", task["query"])
        self.assertIn("`a`", task["query_typed"])
        self.assertNotIn("package.json", task["query"] + task["query_typed"])

    def test_abstain_tasks_ask_about_packages_never_declared(self):
        self.repo.commit({"package.json": pkg(lodash="4", react="18")})
        self.repo.commit({"package.json": pkg(lodash="4", react="18", zod="3")})
        tasks, _, _ = gen_tasks.generate(self._tmp.name, abstain=10, seed=3)
        abstain = [t for t in tasks if t["type"] == "abstain"]
        self.assertEqual(len(abstain), 10)
        names = {t["dep"]["name"] for t in abstain}
        self.assertTrue(names.isdisjoint({"lodash", "react", "zod"}))
        for t in abstain:
            self.assertEqual((t["gold"], t["commit_a"], t["commit_b"]), ("abstain", t["commit_a"], t["commit_a"]))
            self.assertTrue(t["root_manifest"])
        again, _, _ = gen_tasks.generate(self._tmp.name, abstain=10, seed=3)
        self.assertEqual(tasks, again)

    def test_cli_writes_jsonl(self):
        self.repo.commit({"package.json": pkg(a="1")})
        self.repo.commit({"package.json": pkg()})
        out = Path(self._tmp.name).parent / "tasks-test.jsonl"
        self.addCleanup(lambda: out.unlink(missing_ok=True))
        self.assertEqual(gen_tasks.main([self._tmp.name, "--out", str(out), "--repo-name", "demo"]), 0)
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "falsified")

    def test_not_a_repo_exits(self):
        with tempfile.TemporaryDirectory() as plain:
            with self.assertRaises(SystemExit):
                gen_tasks.generate(plain)


if __name__ == "__main__":
    unittest.main()
