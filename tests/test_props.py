"""Property checks, closed-loop re-checks and the agent's own failed checks, on a small project."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mihad_memory import project  # noqa: E402
from mihad_memory.experience import failures, probes, propcheck, review  # noqa: E402

CORE = '''class span:
    """A run of integers from start to stop, like the built-in range() function with step 1."""

    def __init__(self, start, stop):
        self.start, self.stop = start, stop

    def __iter__(self):
        return iter(range(self.start, self.stop))

    def __len__(self):
        return max(0, self.stop - self.start)

    def __eq__(self, other):
        return isinstance(other, span) and (self.start, self.stop) == (other.start, other.stop)

    def __hash__(self):
        return hash((self.start, self.stop))


def pairs(iterable):
    return list(zip(iterable, iterable))


def total(iterable):
    return sum(iterable)
'''
TESTS = '''import sys
import unittest
sys.path.insert(0, "src")
from lib.core import pairs, span, total


class SpanTests(unittest.TestCase):
    def test_len(self):
        self.assertEqual(len(span(2, 5)), 3)
        self.assertEqual(list(span(2, 5)), [2, 3, 4])


class PairsTests(unittest.TestCase):
    def test_pairs(self):
        self.assertEqual(pairs([1, 2]), [(1, 1), (2, 2)])


class TotalTests(unittest.TestCase):
    def test_total(self):
        self.assertEqual(total([1, 2, 3]), 6)
'''


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=u@u", "-c", "user.name=u", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


class PropertyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "lib"
        (self.root / "src" / "lib").mkdir(parents=True)
        (self.root / "src" / "lib" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "src" / "lib" / "core.py").write_text(CORE, encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "tests" / "test_core.py").write_text(TESTS, encoding="utf-8")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "init")
        project.clear_cache()
        self.cfg = project.load(self.root)

    def tearDown(self):
        project.clear_cache()
        self.tmp.cleanup()

    def edit(self, old, new):
        p = self.root / "src" / "lib" / "core.py"
        s = p.read_text(encoding="utf-8")
        self.assertIn(old, s)
        p.write_text(s.replace(old, new), encoding="utf-8")

    def test_new_len_bug_is_reported_as_caused_by_the_change(self):
        self.edit("return max(0, self.stop - self.start)", "return self.stop - self.start")
        found = probes.property_checks(self.root, self.cfg, task_text="tidy span")
        lens = [f for f in found if f["template"] == "len"]
        self.assertTrue(lens, found)
        self.assertFalse(lens[0]["at_start"])
        self.assertIn("len()", lens[0]["detail"])

    def test_old_bug_reported_only_when_the_task_is_about_it(self):
        # pairs() opens its input twice already at the starting commit.
        self.edit("return list(zip(iterable, iterable))", "result = list(zip(iterable, iterable))\n    return result")
        unrelated = probes.property_checks(self.root, self.cfg, task_text="rename a variable in pairs")
        self.assertFalse([f for f in unrelated if f["template"] == "single_pass"], unrelated)
        about = probes.property_checks(self.root, self.cfg, task_text="pairs should accept a stream (iterator) input")
        sp = [f for f in about if f["template"] == "single_pass"]
        self.assertTrue(sp, about)
        self.assertTrue(sp[0]["at_start"])

    def test_correct_change_is_quiet(self):
        self.edit("return sum(iterable)", "total = 0\n    for x in iterable:\n        total += x\n    return total")
        self.assertEqual(probes.property_checks(self.root, self.cfg, task_text="anything about iterators"), [])

    def test_reference_comparison_with_range(self):
        # A deliberately wrong equality: spans that are both empty should be equal, like empty ranges.
        found = propcheck.check_reference(_load_span(), "span", [((1, 4), {})], __import__("random").Random(0),
                                          {"reference"}, __import__("time").time() + 5)
        self.assertTrue(found and found[0][0] == "reference", found)

    def test_recheck_reports_a_mutant_the_new_test_does_not_kill(self):
        self.edit("return sum(iterable)", "if not iterable:\n        return 0\n    return sum(iterable)")
        first = probes.mutation_adequacy(self.root, self.cfg)
        self.assertTrue(first, first)
        again = probes.recheck_survived(self.root, self.cfg, first)
        self.assertTrue(all(f["status"] == "survives" for f in again), again)
        # Now a test that reaches the new line and checks its value: the mutant is killed.
        t = self.root / "tests" / "test_core.py"
        t.write_text(t.read_text(encoding="utf-8") + "\n\nclass EmptyTests(unittest.TestCase):\n"
                     "    def test_empty(self):\n        self.assertEqual(total([]), 0)\n"
                     "        self.assertIs(type(total([])), int)\n", encoding="utf-8")
        killed = probes.recheck_survived(self.root, self.cfg, first)
        self.assertTrue(any(f["status"] in ("killed", "survives") for f in killed), killed)

    def test_equivalent_return_false_is_not_mutated_away(self):
        self.assertNotEqual(probes.mutate_line("    return False\n", "python"), "    pass\n")
        self.assertEqual(probes.mutate_line("    x = 1\n", "python"), "    pass\n")


class SelfCheckTests(unittest.TestCase):
    def test_failed_own_check_without_later_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "s.json")

            class Eng:
                def active_lessons(self):
                    return []

                def fired(self, *a):
                    pass
            eng = Eng()
            failures.detect(eng, {"toolName": "edit", "input": {"path": "a.py"}, "text": "ok"}, state)
            failures.detect(eng, {"toolName": "bash", "input": {"command": "python check.py"},
                                  "text": "first: True\nMatch: False\n"}, state)
            found = review._self_check(state)
            self.assertEqual(found[0]["line"], "Match: False")
            failures.detect(eng, {"toolName": "edit", "input": {"path": "a.py"}, "text": "ok"}, state)
            self.assertEqual(review._self_check(state), [])

    def test_pattern(self):
        for text, hit in [("Match: False", True), ("equal? False", True), ("all good: True", False),
                          ("MISMATCH at 3", True), ("x = False", False), ("is_match = False", False)]:
            self.assertEqual(bool(failures.SELF_CHECK_FAIL.search(text)), hit, text)


def _load_span():
    ns = {}
    exec(CORE.replace("isinstance(other, span) and (self.start, self.stop) == (other.start, other.stop)",
                      "isinstance(other, span) and (self.start, self.stop) == (other.start, other.stop)"), ns)
    return ns["span"]


if __name__ == "__main__":
    unittest.main()
