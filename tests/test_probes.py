"""Executable review checks on a small project: each must report only what it really observes."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mihad_memory import project  # noqa: E402
from mihad_memory.experience import probes  # noqa: E402

CORE = '''def clamp(x, lo, hi):
    return x


def count_items(iterable):
    total = 0
    for _ in iterable:
        total += 1
    return total
'''
TESTS = '''import sys
import unittest
sys.path.insert(0, "src")
from shop.core import clamp, count_items


class ClampTests(unittest.TestCase):
    def test_inside(self):
        self.assertEqual(clamp(2, 0, 3), 2)


class CountItemsTests(unittest.TestCase):
    def test_count(self):
        self.assertEqual(count_items([1, 2, 3]), 3)
'''


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=u@u", "-c", "user.name=u", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "shop"
        (self.root / "src" / "shop").mkdir(parents=True)
        (self.root / "src" / "shop" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "src" / "shop" / "core.py").write_text(CORE, encoding="utf-8")
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

    def edit(self, old, new, path="src/shop/core.py"):
        p = self.root / path
        p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    def test_untested_branch_survives_mutation(self):
        self.edit("    return x\n", "    if x < lo:\n        return lo\n    return x\n")
        found = probes.mutation_adequacy(self.root, self.cfg)
        # The condition is exercised (negating it breaks a test), but the new `return lo` is not:
        # deleting it changes nothing the tests can see.
        self.assertTrue(any(f["kind"] == "survived" and f["line"] == 3 for f in found), found)
        self.assertFalse(any(f["kind"] == "survived" and f["line"] == 2 for f in found), found)
        self.assertEqual((self.root / "src" / "shop" / "core.py").read_text(encoding="utf-8").count("if x < lo"), 1)

    def test_tested_branch_is_quiet(self):
        self.edit("    return x\n", "    if x < lo:\n        return lo\n    return x\n")
        self.edit("        self.assertEqual(clamp(2, 0, 3), 2)\n",
                  "        self.assertEqual(clamp(2, 0, 3), 2)\n        self.assertEqual(clamp(-1, 0, 3), 0)\n"
                  "        self.assertEqual(clamp(0, 0, 3), 0)\n", "tests/test_core.py")
        found = probes.mutation_adequacy(self.root, self.cfg)
        self.assertEqual([f for f in found if f["kind"] == "survived"], [], found)

    def test_iterator_probe_catches_len_on_iterable(self):
        self.edit("    total = 0\n    for _ in iterable:\n        total += 1\n    return total\n",
                  "    return len(iterable)\n")
        found = probes.iterator_probe(self.root, self.cfg)
        self.assertEqual([f["function"] for f in found], ["count_items"], found)

    def test_iterator_probe_quiet_for_correct_code(self):
        self.edit("        total += 1\n", "        total = total + 1\n")
        self.assertEqual(probes.iterator_probe(self.root, self.cfg), [])

    def test_format_regression_is_reported(self):
        if subprocess.run([sys.executable, "-m", "ruff", "--version"], capture_output=True).returncode != 0:
            self.skipTest("ruff not installed")
        self.edit("    return x\n", "    return   x\n")
        kinds = [f["kind"] for f in probes.format_and_lint(self.root, self.cfg)]
        self.assertIn("format", kinds)

    def test_preference_for_tests_is_checked(self):
        from mihad_memory.service import Memory
        m = Memory(str(self.root), policy="verified")
        log = self.root / ".mihad" / "user_messages.txt"
        log.parent.mkdir(exist_ok=True)
        log.write_text("Always add a regression test for every bug you fix.\n", encoding="utf-8")
        m.user_log = str(log)
        m.capture_preferences()
        m.store.close()
        self.edit("    return x\n", "    return max(lo, min(x, hi))\n")
        found = probes.preference_checks(self.root, self.cfg)
        self.assertEqual(found[0]["detail"], "the change adds no test")
        # A test that passes on the old code too does not test the fix.
        self.edit("        self.assertEqual(clamp(2, 0, 3), 2)\n",
                  "        self.assertEqual(clamp(2, 0, 3), 2)\n        self.assertEqual(clamp(1, 0, 3), 1)\n",
                  "tests/test_core.py")
        self.assertIn("also pass on the old code", probes.preference_checks(self.root, self.cfg)[0]["detail"])
        self.edit("        self.assertEqual(clamp(1, 0, 3), 1)\n", "        self.assertEqual(clamp(9, 0, 3), 3)\n",
                  "tests/test_core.py")
        self.assertEqual(probes.preference_checks(self.root, self.cfg), [])

    def test_run_all_describes_findings(self):
        self.edit("    total = 0\n    for _ in iterable:\n        total += 1\n    return total\n",
                  "    return len(iterable)\n")
        texts = [probes.describe(f) for f in probes.run_all(self.root, self.cfg)]
        self.assertTrue(any("one-shot iterator" in t for t in texts), texts)
        json.dumps(texts)


if __name__ == "__main__":
    unittest.main()
