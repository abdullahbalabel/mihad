"""The timely question: answers come from memory, the project, then the user; at most two per session."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mihad_memory.experience import question  # noqa: E402


class QuestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = str(Path(self.tmp.name) / "s.json")
        self.old_ask = question.ask_text
        self.old_env = dict(os.environ)
        self.calls = []

    def tearDown(self):
        question.ask_text = self.old_ask
        os.environ.clear(); os.environ.update(self.old_env)
        self.tmp.cleanup()

    def fake(self, replies):
        def ask_text(model, prompt, timeout=240):
            self.calls.append(prompt)
            return replies.pop(0)
        question.ask_text = ask_text

    def test_identifiers(self):
        ids = question._identifiers("Should `move_to_end` on OrderedBidict.inverse raise KeyError or ValueError?")
        self.assertIn("move_to_end", ids)
        self.assertIn("OrderedBidict.inverse", ids)

    def test_user_then_memory_then_cap(self):
        full = Path(self.tmp.name) / "full.txt"
        full.write_text("The removal must raise KeyError.", encoding="utf-8")
        os.environ["MIHAD_ORACLE_FILE"], os.environ["MIHAD_ORACLE_MODEL"] = str(full), "m"
        self.fake(["KeyError.", "ValueError.", "whatever"])
        a = question.ask(self.tmp.name, "Which exception?", self.state)
        self.assertEqual((a["answer"], a["source"]), ("KeyError.", "user"))
        again = question.ask(self.tmp.name, "which exception?", self.state)  # same question: from memory
        self.assertEqual(again["source"], "memory")
        b = question.ask(self.tmp.name, "What order?", self.state)
        self.assertEqual(b["source"], "user")
        c = question.ask(self.tmp.name, "A third one?", self.state)
        self.assertEqual(c["source"], "cap")
        self.assertEqual(len(question.past_answers(self.state)), 2)

    def test_goal_doubt_same_is_silent(self):
        self.fake(["SAME"])
        self.assertEqual(question.goal_block("Fix the bug."), "")
        self.fake(["A: raise\nB: return None"])
        self.assertIn("two ways", question.goal_block("Fix the bug."))

    def test_detail_nudge_once(self):
        st = {"step": 5, "self_check_fail": {"step": 5}}
        self.assertIn("ask the user", question.detail_block(st))
        self.assertEqual(question.detail_block(st), "")

    def test_off_without_oracle_or_flag(self):
        os.environ["MIHAD_EXPERIENCE_DIR"] = self.tmp.name
        os.environ.pop("MIHAD_QUESTION", None)
        self.assertFalse(question.enabled(self.tmp.name))
        self.fake([])  # no model call may happen
        a = question.ask(self.tmp.name, "Anything?", self.state)
        self.assertEqual(a["source"], "none")


if __name__ == "__main__":
    unittest.main()
