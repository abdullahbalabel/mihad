"""The conscience's rule: like a teacher, step in after two mistakes since the last time, and tell the
advisor whether it was the same mistake again or different ones."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mihad_memory.experience import conscience as watch, failures  # noqa: E402


class Eng:
    def active_lessons(self):
        return []

    def fired(self, *a):
        pass

    def log(self, *a):
        pass


def bash(cmd, text, error):
    return {"toolName": "bash", "input": {"command": cmd}, "isError": error, "text": text}


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = str(Path(self.tmp.name) / "s.json")

    def tearDown(self):
        self.tmp.cleanup()

    def st(self):
        return json.loads(Path(self.state).read_text(encoding="utf-8"))

    def test_one_mistake_is_not_enough(self):
        failures.detect(Eng(), bash("python -m pytest", "AssertionError: 1 != 2", True), self.state)
        self.assertIsNone(watch.should_consult(self.st(), {}, 0))

    def test_same_mistake_twice(self):
        for _ in range(2):
            failures.detect(Eng(), bash("python -m pytest", "AssertionError: 1 != 2", True), self.state)
        reason = watch.should_consult(self.st(), {}, 0)
        self.assertIn("same mistake", reason)

    def test_different_mistakes(self):
        failures.detect(Eng(), bash("python -m pytest", "AssertionError: 1 != 2", True), self.state)
        failures.detect(Eng(), bash("python x.py", "NameError: name 'y' is not defined", True), self.state)
        reason = watch.should_consult(self.st(), {}, 0)
        self.assertIn("different mistakes", reason)

    def test_own_failed_check_counts(self):
        failures.detect(Eng(), bash("python check.py", "Match: False", False), self.state)
        failures.detect(Eng(), bash("python -m pytest", "AssertionError", True), self.state)
        self.assertIsNotNone(watch.should_consult(self.st(), {}, 0))

    def test_not_again_for_the_same_mistakes(self):
        for _ in range(2):
            failures.detect(Eng(), bash("python -m pytest", "AssertionError: 1 != 2", True), self.state)
        st = self.st()
        self.assertIsNotNone(watch.should_consult(st, {}, 0))
        self.assertIsNone(watch.should_consult(st, {}, 1))  # nothing new since that consultation

    def test_notes_are_delivered_once(self):
        Path(f"{self.state}.notes.jsonl").write_text(json.dumps({"reason": "r", "note": "Fix line 3."}) + "\n" +
                                                     json.dumps({"reason": "r", "note": ""}) + "\n", encoding="utf-8")
        self.assertIn("Fix line 3.", watch.pending_notes(self.state))
        self.assertEqual(watch.pending_notes(self.state), "")

    def test_past_resolution_of_the_same_error(self):
        failures.detect(Eng(), bash("ruff format x.py", "No module named ruff", True), self.state)
        mistakes = self.st()["mistakes"]
        sig = mistakes[0]["sig"]
        episodes = [{"errors": [
            {"tool": "bash", "signature": sig, "message": "No module named ruff", "args": {"command": "ruff x"},
             "resolution": {"command": "python -m black x.py"}},
            {"tool": "edit", "signature": sig, "args": {}, "resolution": {"path": "y"}},  # another tool: not it
            {"tool": "bash", "signature": "something else", "args": {}, "resolution": {"command": "z"}}]}]
        past = watch.past_resolutions(mistakes, episodes)
        self.assertEqual(len(past), 1)
        self.assertIn("python -m black", past[0])

    def test_memory_block_holds_preferences(self):
        prefs = Path(self.tmp.name) / "prefs.txt"
        prefs.write_text("- Always add a changelog entry.\n", encoding="utf-8")
        old = dict(os.environ)
        os.environ["MIHAD_CONSCIENCE_PREFS"] = str(prefs)
        os.environ["MIHAD_CONSCIENCE_PAST"] = str(Path(self.tmp.name) / "none.jsonl")
        try:
            text, held = watch.memory_context(self.tmp.name, [{"sig": "x", "tool": "bash", "step": 1}])
        finally:
            os.environ.clear(); os.environ.update(old)
        self.assertIn("Always add a changelog entry.", text)
        self.assertEqual(held, {"prefs": 1, "past": 0})


if __name__ == "__main__":
    unittest.main()
