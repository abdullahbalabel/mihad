"""The memory, engine and dream cycle on a project that is not the trial repository."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mihad_memory import install, project  # noqa: E402
from mihad_memory.experience import cycle, dream, live, practice, skills  # noqa: E402
from mihad_memory.experience.engine import Engine  # noqa: E402
from mihad_memory.experience.episodes import ingest_run  # noqa: E402

CORE = '''def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def first_word(text):
    return text.split()[0]
'''

TESTS = '''from shop.core import clamp, first_word


def test_clamp():
    assert clamp(5, 0, 3) == 3
    assert clamp(-1, 0, 3) == 0


class TestWords:
    def test_first(self):
        assert first_word("a b") == "a"
'''


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=u@u", "-c", "user.name=u", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


UNITTEST_TESTS = '''import sys
import unittest
sys.path.insert(0, "src")
from shop.core import clamp, first_word


class ClampTests(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(clamp(5, 0, 3), 3)


class WordsTests(unittest.TestCase):
    def test_first(self):
        self.assertEqual(first_word("a b"), "a")
        self.assertEqual(first_word(""), "")
'''


def make_project(root, runner="pytest"):
    root = Path(root)
    (root / "src" / "shop").mkdir(parents=True)
    (root / "src" / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "shop" / "core.py").write_text(CORE, encoding="utf-8")
    (root / "tests").mkdir()
    if runner == "pytest":
        (root / "tests" / "test_core.py").write_text(TESTS, encoding="utf-8")
        (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\npythonpath = ['src']\n", encoding="utf-8")
    else:
        (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (root / "tests" / "test_core.py").write_text(UNITTEST_TESTS, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    project.clear_cache()
    return root


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_project(Path(self.tmp.name) / "shop")

    def tearDown(self):
        project.clear_cache()
        self.tmp.cleanup()

    def test_detects_src_layout_and_pytest(self):
        cfg = project.load(self.root)
        self.assertEqual(cfg["source_dirs"], ["src/shop"])
        self.assertEqual(cfg["test_runner"], "pytest")
        self.assertTrue(project.is_source("src/shop/core.py", cfg))
        self.assertFalse(project.is_source("tests/test_core.py", cfg))
        self.assertEqual([p.name for p in project.source_files(self.root, cfg)], ["__init__.py", "core.py"])

    def test_config_file_overrides_detection(self):
        (self.root / ".mihad").mkdir()
        (self.root / ".mihad" / "project.json").write_text(json.dumps({"name": "Shop", "dream": {"tasks_per_cycle": 1}}),
                                                           encoding="utf-8")
        project.clear_cache()
        cfg = project.load(self.root / "src")  # found by walking up
        self.assertEqual(cfg["name"], "Shop")
        self.assertEqual(cfg["dream"]["tasks_per_cycle"], 1)
        self.assertEqual(cfg["dream"]["model"], "anthropic/claude-haiku-4-5")  # defaults kept

    def test_pytest_tests_found_for_function(self):
        self.assertEqual(skills.test_classes_for(self.root, "clamp"), ["tests/test_core.py::test_clamp"])
        self.assertEqual(skills.test_classes_for(self.root, "first_word"), ["tests/test_core.py::TestWords"])

    def test_install_keeps_existing_omp_config_and_uninstall_reverts(self):
        (self.root / ".omp").mkdir()
        (self.root / ".omp" / "mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}),
                                                     encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "user's own omp config")
        install.install(self.root, log=lambda *_: None)
        mcp =json.loads((self.root / ".omp" / "mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(set(mcp["mcpServers"]), {"other", "mihad_memory"})
        settings = json.loads((self.root / ".omp" / "settings.json").read_text(encoding="utf-8"))
        self.assertTrue(settings["extensions"][0].endswith("mihad-experience.ts"))
        exclude = (self.root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn(".mihad/", exclude)
        self.assertIn(".omp/settings.json", exclude)
        self.assertNotIn(".omp/mcp.json", exclude)  # existed before: the user's file, not ours
        self.assertTrue(list((self.root / ".mihad" / "backup").glob("mcp.json.*")))
        # git sees only the change to the user's own tracked file, nothing new
        self.assertEqual(git(self.root, "status", "--porcelain"), "M .omp/mcp.json")
        install.install(self.root, log=lambda *_: None)  # idempotent
        settings = json.loads((self.root / ".omp" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(len(settings["extensions"]), 1)
        install.uninstall(self.root, log=lambda *_: None)
        mcp = json.loads((self.root / ".omp" / "mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(set(mcp["mcpServers"]), {"other"})


class LiveLearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_project(Path(self.tmp.name) / "shop")
        self.eng = Engine(self.root / ".mihad" / "experience")
        live.MIN_AGE_SECONDS = 0

    def tearDown(self):
        project.clear_cache()
        self.tmp.cleanup()

    def test_user_commit_becomes_the_correction(self):
        live.record(self.eng, "start", self.root, "s1.jsonl", "Make first_word handle empty text")
        core = self.root / "src" / "shop" / "core.py"
        core.write_text(CORE.replace("return text.split()[0]",
                                     "words = text.split()\n    return words[0] if words else ''"), encoding="utf-8")
        live.record(self.eng, "end", self.root, "s1.jsonl", tests_ok=True)
        self.assertEqual(live.ingest(self.eng), [])  # no commit by the user yet
        core.write_text(CORE.replace("return text.split()[0]",
                                     "words = text.split()\n    return words[0] if words else None"), encoding="utf-8")
        git(self.root, "commit", "-qam", "user's version")
        (ep,) = live.ingest(self.eng)
        self.assertEqual(ep["families"], ["first_word"])
        self.assertEqual(ep["corrected_files"], ["src/shop/core.py"])
        self.assertEqual(ep["kept_fraction"], 0.5)
        self.assertTrue(ep["hidden_pass"])
        self.assertEqual(live.ingest(self.eng), [])  # once only

    def test_snapshot_leaves_the_branch_and_stash_alone(self):
        (self.root / "src" / "shop" / "core.py").write_text(CORE + "\n# wip\n", encoding="utf-8")
        before = (git(self.root, "rev-parse", "HEAD"), git(self.root, "stash", "list"))
        sha = live.snapshot(self.root)
        self.assertTrue(sha)
        self.assertEqual((git(self.root, "rev-parse", "HEAD"), git(self.root, "stash", "list")), before)
        self.assertIn("# wip", git(self.root, "status", "--porcelain") + git(self.root, "diff"))


class PracticeCycleTests(unittest.TestCase):
    """A dream cycle on the pytest project, with a fake agent that repairs the mutation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_project(Path(self.tmp.name) / "shop", runner="unittest")
        install.install(self.root, log=lambda *_: None)
        self.cfg = project.load(self.root)
        self.eng = Engine(project.path_in(self.cfg, "experience_dir"))
        base = git(self.root, "rev-parse", "HEAD")
        fixed = CORE.replace("return text.split()[0]",
                             "words = text.split()\n    if not words:\n        return ''\n    return words[0]")
        (self.root / "src" / "shop" / "core.py").write_text(fixed, encoding="utf-8")
        git(self.root, "commit", "-qam", "fix first_word")
        self.fixed = git(self.root, "rev-parse", "HEAD")
        self.eng.add_episode({"source": "live", "task_id": "live-1", "subject": "first_word on empty text",
                              "body": "", "parent": base, "commit": self.fixed, "hidden_test_ids": None,
                              "model": None, "hidden_pass": True, "tokens": 1000, "turns": 3,
                              "families": ["first_word"], "agent_files": ["src/shop/core.py"],
                              "ref_files": ["src/shop/core.py"], "commands": [], "errors": [], "reads": {},
                              "n_steps": 3})
        chk_dir = self.eng.root / "checkers"
        chk_dir.mkdir(parents=True, exist_ok=True)
        (chk_dir / "live-1.py").write_text("import sys\nsys.path.insert(0, 'src')\nfrom shop.core import first_word\n"
                                           "assert first_word('') == ''\nassert first_word('a b') == 'a'\n",
                                           encoding="utf-8")
        self.eng.put("checkers", {"live-1": {"status": "adopted", "path": "checkers/live-1.py",
                                             "families": ["first_word"], "attempts": []}})

    def tearDown(self):
        project.clear_cache()
        self.tmp.cleanup()

    def test_make_tasks_and_practice_layout(self):
        spec = dream.make_tasks(self.eng, str(self.root), n=1, seed=1)
        self.assertEqual(len(spec["tasks"]), 1)
        task = spec["tasks"][0]
        self.assertEqual(task["families"], ["first_word"])

        def fake_agent(cmd, ws, out_dir, env, seconds, visible, busy, self_ending=False):
            # The "agent" repairs the file by restoring the user's fixed version; one arm is slower.
            subprocess.run(["git", "apply", "-R", "-"], input=task["base_patch"], text=True, cwd=ws, check=True)
            msg = {"type": "message", "message": {"role": "assistant", "content": [],
                                                  "usage": {"input": 100 if "ablate" not in env[
                                                      "MIHAD_EXPERIENCE_TAG"] else 300}}}
            (out_dir / "agent.jsonl").write_text(json.dumps(msg) + "\n", encoding="utf-8")

        orig = practice._run_agent
        practice._run_agent = fake_agent
        try:
            run_dir = self.eng.root / "dreams" / "c1"
            rows = practice.run(self.eng, run_dir, cfg=self.cfg)
        finally:
            practice._run_agent = orig
        self.assertEqual(sorted(r["arm"] for r in rows), ["engine", "engine_ablate"])
        self.assertTrue(all(r["hidden_pass"] and r["checker_pass"] for r in rows))
        self.assertEqual(len(ingest_run(self.eng, run_dir, repo=str(self.root))), 2)
        summary = dream.evaluate(self.eng, run_dir)
        self.assertEqual(len(summary["pairs"]), 1)

    def test_cycle_skips_when_locked(self):
        (self.eng.root / "dream.lock").write_text("1", encoding="utf-8")
        self.assertIsNone(cycle.run_cycle(self.eng, self.cfg, practice_tasks=0, log=lambda *_: None))

    def test_session_counter_starts_cycle_every_n(self):
        started = []
        orig = cycle.spawn
        cycle.spawn = lambda eng, cfg: started.append(1) or "log"
        try:
            cfg = dict(self.cfg, dream=dict(self.cfg["dream"], auto_after_sessions=2))
            self.assertEqual(cycle.session_closed(self.eng, cfg), "")
            self.assertIn("started", cycle.session_closed(self.eng, cfg))
        finally:
            cycle.spawn = orig
        self.assertEqual(started, [1])


if __name__ == "__main__":
    unittest.main()
