import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mihad_memory.experience import (advisor, checkers, competence, corrections, dream, edges, failures,  # noqa: E402
                                     report, review, skills)
from mihad_memory.experience.brief import brief  # noqa: E402
from mihad_memory.experience.common import (mentioned_symbols, normalize_command, normalize_error,  # noqa: E402
                                            parse_diff, symbols_in_ranges)
from mihad_memory.experience.engine import Engine  # noqa: E402

MORE_V1 = '''__all__ = ["alpha", "beta"]


def alpha(x):
    """Double x."""
    return x * 2


def beta(x, y):
    if x < y:
        return x
    return y
'''

MORE_V2 = MORE_V1.replace("return x * 2", "if x is None:\n        raise ValueError('x')\n    return x * 2")

STUB = '''def alpha(x: int) -> int: ...
def beta(x: int, y: int) -> int: ...
'''

TESTS = '''import unittest
from more_itertools.more import alpha, beta


class AlphaTests(unittest.TestCase):
    def test_double(self):
        self.assertEqual(alpha(2), 4)


class BetaTests(unittest.TestCase):
    def test_min(self):
        self.assertEqual(beta(1, 2), 1)
'''


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def make_repo(root):
    root = Path(root)
    (root / "more_itertools").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "more_itertools" / "__init__.py").write_text("", encoding="utf-8")
    (root / "more_itertools" / "more.py").write_text(MORE_V1, encoding="utf-8")
    (root / "more_itertools" / "more.pyi").write_text(STUB, encoding="utf-8")
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_more.py").write_text(TESTS, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "v1")
    return git(root, "rev-parse", "HEAD").strip()


def episode(tid, ref_files, agent_files, families=(), errors=(), passed=True, tokens=1000, model="m"):
    return {"source": "run/arm", "task_id": tid, "subject": f"task {tid}", "body": "", "parent": "p", "commit": "c",
            "hidden_test_ids": [], "model": model, "hidden_pass": passed, "tokens": tokens, "turns": 3,
            "families": list(families), "agent_files": list(agent_files), "ref_files": list(ref_files),
            "commands": [], "errors": list(errors), "reads": {}, "n_steps": 10}


class CommonTests(unittest.TestCase):
    def test_diff_attributes_only_changed_lines(self):
        diff = ("diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -8,6 +8,7 @@ def beta(x, y):\n"
                " def beta(x, y):\n     if x < y:\n         return x\n+    # changed\n     return y\n")
        files = parse_diff(diff)
        self.assertEqual(files["m.py"]["new_ranges"], [(11, 1)])
        self.assertEqual(symbols_in_ranges(MORE_V1, files["m.py"]["new_ranges"]), {"beta"})
        # Hunk context reaches into alpha, but no changed line is there.
        self.assertNotIn("alpha", symbols_in_ranges(MORE_V1, files["m.py"]["new_ranges"]))

    def test_normalizers(self):
        self.assertEqual(normalize_error("Path 'a/b.py#3F2A' not found"), "Path '<s>' not found")
        self.assertEqual(normalize_command('cd "D:/x y" && python -m unittest tests.test_more.ATests -v 2>&1 | tail'),
                         "python -m unittest {test_target} -v")

    def test_short_names_need_code_form(self):
        known = {"first", "numeric_range"}
        self.assertEqual(mentioned_symbols("fix the first item of numeric_range", known), {"numeric_range"})
        self.assertEqual(mentioned_symbols("fix `first` for empty input", known), {"first"})


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.eng = Engine(Path(self.tmp.name) / "eng")

    def tearDown(self):
        self.tmp.cleanup()

    def test_co_change_needs_two_independent_tasks(self):
        self.eng.add_episode(episode("t1", ["more_itertools/more.py", "more_itertools/more.pyi"],
                                     ["more_itertools/more.py"]))
        self.assertEqual(corrections.mine(self.eng), [])
        # A second session of the same task is not independent evidence.
        self.eng.add_episode(episode("t1", ["more_itertools/more.py", "more_itertools/more.pyi"], []))
        self.assertEqual(corrections.mine(self.eng), [])
        self.eng.add_episode(episode("t2", ["more_itertools/more.py", "more_itertools/more.pyi"],
                                     ["more_itertools/more.py"]))
        rules = {(l["trigger"]["files"][0], l["partner"]): l for l in corrections.mine(self.eng)}
        rule = rules[("more_itertools/more.py", "more_itertools/more.pyi")]
        self.assertEqual(rule["support"], ["t1", "t2"])
        self.assertEqual(rule["missed"], ["t1", "t2"])

    def test_failure_lesson_needs_two_tasks_and_a_resolution(self):
        err = {"tool": "read", "signature": "Path '<s>' not found", "message": "Path 'a.py#AB12' not found",
               "args": {"path": "a.py#AB12:1-5"}, "resolution": {"path": "a.py:1-5"}}
        self.eng.add_episode(episode("t1", [], [], errors=[err]))
        self.assertEqual(failures.mine(self.eng), [])
        self.eng.add_episode(episode("t2", [], [], errors=[dict(err, resolution=None)]))
        (lesson,) = failures.mine(self.eng)
        self.assertIn("a.py:1-5", lesson["text"])
        state = Path(self.tmp.name) / "state.json"
        ev = {"toolName": "read", "input": {"path": "b.py#99FF:3-4"}, "isError": True, "text": "Path 'b.py#99FF' not found"}
        self.assertIn("a.py:1-5", failures.detect(self.eng, ev, state))
        self.assertEqual(failures.detect(self.eng, ev, state), "")  # once per session

    def test_detect_repeated_command_and_reads(self):
        state = Path(self.tmp.name) / "s.json"
        bad = {"toolName": "bash", "input": {"command": "python x.py"}, "isError": True, "text": "Traceback\nKeyError: 1"}
        self.assertEqual(failures.detect(self.eng, bad, state), "")
        self.assertIn("failed 2 times", failures.detect(self.eng, bad, state))
        read = {"toolName": "read", "input": {"path": "m.py:1-9"}, "isError": False, "text": "..."}
        outs = [failures.detect(self.eng, read, state) for _ in range(4)]
        self.assertEqual(outs[:3], ["", "", ""])
        self.assertIn("read m.py 4 times", outs[3])

    def test_tests_after_last_edit(self):
        state = Path(self.tmp.name) / "s.json"
        failures.detect(self.eng, {"toolName": "bash", "input": {"command": "python -m unittest"}, "isError": False}, state)
        failures.detect(self.eng, {"toolName": "edit", "input": {}, "isError": False}, state)
        self.assertFalse(failures.tests_after_last_edit(state))
        failures.detect(self.eng, {"toolName": "bash", "input": {"command": "python -m unittest"}, "isError": False}, state)
        self.assertTrue(failures.tests_after_last_edit(state))

    def test_competence_levels(self):
        for i, tok in enumerate([100, 200, 900]):
            self.eng.add_episode(episode(f"a{i}", [], [], families=["alpha"], tokens=tok))
        self.eng.add_episode(episode("b0", [], [], families=["beta"], tokens=5000))
        self.eng.add_episode(episode("c0", [], [], families=["gamma"], passed=False))
        comp = competence.mine(self.eng)
        self.assertEqual(comp["families"]["alpha"]["level"], "mastered")
        self.assertEqual(comp["families"]["beta"]["level"], "competent")
        self.assertEqual(comp["families"]["gamma"]["level"], "novice")

    def test_ablation_excludes_all_lessons(self):
        self.eng.save_lessons([{"id": "x", "kind": "failure", "status": "adopted"},
                               {"id": "y", "kind": "failure", "status": "candidate"}])
        self.assertEqual([l["id"] for l in self.eng.active_lessons()], ["x"])
        os.environ["MIHAD_EXPERIENCE_EXCLUDE"] = "*"
        try:
            self.assertEqual(self.eng.active_lessons(), [])
        finally:
            del os.environ["MIHAD_EXPERIENCE_EXCLUDE"]

    def test_dream_evaluate_promotes_and_retires(self):
        run = Path(self.tmp.name) / "dr"
        run.mkdir()
        rows = []
        for tid, (w_tok, wo_tok) in {"d1": (500, 1000), "d2": (600, 1000)}.items():
            rows.append({"task_id": tid, "arm": "engine", "hidden_pass": True, "usage": {"input": w_tok}})
            rows.append({"task_id": tid, "arm": "engine_ablate", "hidden_pass": True, "usage": {"input": wo_tok}})
        (run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        self.eng.save_lessons([{"id": "good", "kind": "failure", "status": "adopted"},
                               {"id": "quiet", "kind": "failure", "status": "adopted"}])
        for tid in ("d1", "d2"):
            os.environ["MIHAD_EXPERIENCE_TAG"] = f"dr/engine/{tid}"
            self.eng.fired("brief", ["good"])
        del os.environ["MIHAD_EXPERIENCE_TAG"]
        dream.evaluate(self.eng, run)
        status = {l["id"]: l["status"] for l in self.eng.lessons()}
        self.assertEqual(status, {"good": "promoted", "quiet": "adopted"})


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        self.base = make_repo(self.ws)
        self.eng = Engine(Path(self.tmp.name) / "eng")

    def tearDown(self):
        self.tmp.cleanup()

    def test_review_flags_missing_all_and_stub(self):
        (self.ws / "more_itertools" / "more.py").write_text(MORE_V1 + "\n\ndef gamma(x):\n    return x\n",
                                                           encoding="utf-8")
        issues = review.consistency_issues(self.ws)
        self.assertTrue(any("gamma" in i and "__all__" in i for i in issues))
        self.assertTrue(any("gamma" in i and "stub" in i for i in issues))

    def test_review_flags_signature_change_without_stub(self):
        (self.ws / "more_itertools" / "more.py").write_text(MORE_V1.replace("def beta(x, y)", "def beta(x, y, z=0)"),
                                                           encoding="utf-8")
        self.assertTrue(any("signature of beta" in i for i in review.consistency_issues(self.ws)))
        (self.ws / "more_itertools" / "more.pyi").write_text(STUB.replace("y: int)", "y: int, z: int = 0)"),
                                                            encoding="utf-8")
        self.assertEqual(review.consistency_issues(self.ws), [])

    def test_review_is_silent_without_changes(self):
        self.assertEqual(review.review(self.eng, self.ws)["text"], "")

    def test_checker_gate_and_regression(self):
        (self.ws / "more_itertools" / "more.py").write_text(MORE_V2, encoding="utf-8")
        git(self.ws, "commit", "-qam", "v2")
        fixed = git(self.ws, "rev-parse", "HEAD").strip()
        good = Path(self.tmp.name) / "good.py"
        good.write_text("from more_itertools.more import alpha\ntry:\n    alpha(None)\nexcept ValueError:\n"
                        "    print('CHECK OK')\nelse:\n    raise SystemExit(1)\n", encoding="utf-8")
        weak = Path(self.tmp.name) / "weak.py"
        weak.write_text("from more_itertools.more import alpha\nassert alpha(2) == 4\n", encoding="utf-8")
        self.assertTrue(checkers.validate(good, self.ws, self.base, fixed)["adopted"])
        self.assertFalse(checkers.validate(weak, self.ws, self.base, fixed)["adopted"])  # passes before the fix too
        (self.eng.root / "checkers").mkdir()
        (self.eng.root / "checkers" / "t1.py").write_text(good.read_text(), encoding="utf-8")
        self.eng.put("checkers", {"t1": {"status": "adopted", "path": "checkers/t1.py", "families": ["alpha"]}})
        self.assertEqual(checkers.regressions(self.eng, self.ws)[0], [])
        (self.ws / "more_itertools" / "more.py").write_text(MORE_V1, encoding="utf-8")  # the agent undoes the fix
        regs, _ = checkers.regressions(self.eng, self.ws)
        self.assertEqual([r["task_id"] for r in regs], ["t1"])

    def test_skill_finds_and_runs_test_classes(self):
        self.assertEqual(skills.test_classes_for(self.ws, "beta"), ["tests.test_more.BetaTests"])
        code, out = skills.skill_test_symbol(self.ws, "beta")
        self.assertEqual(code, 0, out)
        (self.ws / "more_itertools" / "more.py").write_text(MORE_V1.replace("return x\n", "return y\n", 1),
                                                           encoding="utf-8")
        self.assertEqual(skills.changed_symbols(self.ws), ["beta"])
        code, out = skills.skill_check_change(self.ws)
        self.assertEqual(code, 1, out)

    def test_mutants_skip_docstring_and_def_line(self):
        cands = dream._candidates(MORE_V1, "beta", __import__("random").Random(0))
        lines = {i for i, _, _ in cands}
        self.assertEqual(lines, {9})  # only "if x < y:" (0-based index)
        # alpha: only "return x * 2" (index 5), never the def line (3) or the docstring (4)
        self.assertEqual({i for i, _, _ in dream._candidates(MORE_V1, "alpha", __import__("random").Random(0))}, {5})

    def test_brief_is_gated_by_named_functions(self):
        self.eng.add_episode(dict(episode("t1", ["more_itertools/more.py"], [], families=["alpha"]),
                                  subject="Fix alpha for None"))
        text = brief(self.eng, self.ws, "Make beta handle ties", "m")
        self.assertNotIn("Fix alpha", text)
        self.assertIn("Fix alpha", brief(self.eng, self.ws, "alpha(None) should raise", "m"))


class EdgeAdvisorReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        make_repo(self.ws)
        self.eng = Engine(Path(self.tmp.name) / "eng")
        (self.eng.root / "checkers").mkdir()
        reg = {}
        for tid, code in {"t1": "x = list(f(iter([1, 2])))  # slow path", "t2": "assert g(iter('ab'))",
                          "t3": "assert h(n=-1)"}.items():
            (self.eng.root / "checkers" / f"{tid}.py").write_text(code, encoding="utf-8")
            reg[tid] = {"status": "adopted", "path": f"checkers/{tid}.py", "families": ["alpha"]}
        self.eng.put("checkers", reg)

    def tearDown(self):
        self.tmp.cleanup()
        for k in ("MIHAD_EXPERIENCE_ADVISOR",):
            os.environ.pop(k, None)

    def test_edge_kinds_need_two_fixes(self):
        kinds = {l["trigger"]["edge"] for l in edges.mine(self.eng)}
        self.assertIn("single_use_iterator", kinds)
        self.assertNotIn("negative_or_zero_size", kinds)  # one fix only

    def test_review_edge_checklist_once_per_session(self):
        edges.mine(self.eng)
        state = Path(self.tmp.name) / "st.json"
        (self.ws / "more_itertools" / "more.py").write_text(
            MORE_V1.replace("def beta(x, y):", "def beta(x, y, iterable=()):"), encoding="utf-8")
        first = review.review(self.eng, self.ws, state_path=state, edges=True)["issues"]
        self.assertTrue(any("one-shot iterator" in i for i in first))
        second = review.review(self.eng, self.ws, state_path=state, edges=True)["issues"]
        self.assertFalse(any("one-shot iterator" in i for i in second))
        self.assertFalse(any("one-shot" in i for i in review.review(self.eng, self.ws)["issues"]))  # flag off

    def test_advisor_due_only_when_enabled_and_stuck(self):
        st = {"step": 5, "test_fails": 2, "streak": 0, "last_edit": 3}
        self.assertIsNone(advisor.due(st))
        os.environ["MIHAD_EXPERIENCE_ADVISOR"] = "1"
        self.assertEqual(advisor.due(st), "tests failed repeatedly")
        self.assertIsNone(advisor.due(dict(st, advised=1)))  # at most once per session
        self.assertIsNone(advisor.due({"step": 5, "test_fails": 1, "streak": 0, "last_edit": 3}))

    def test_detect_tracks_failing_test_runs(self):
        state = Path(self.tmp.name) / "s.json"
        ev = {"toolName": "bash", "input": {"command": "python -m unittest tests.test_more"}, "isError": True,
              "text": "FAILED (failures=1)"}
        failures.detect(self.eng, ev, state)
        failures.detect(self.eng, ev, state)
        st = json.loads(state.read_text(encoding="utf-8"))
        self.assertEqual(st["test_fails"], 2)
        self.assertEqual(len(st["fails"]), 2)

    def test_report_renders(self):
        edges.mine(self.eng)
        text = report.render(self.eng)
        self.assertIn("Verifier scripts: 3 adopted", text)
        self.assertIn("edge case", text)


if __name__ == "__main__":
    unittest.main()
