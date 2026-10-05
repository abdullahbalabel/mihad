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
        self.fake(["1. Should it raise or return None?\n2. Does it cover the inverse too?"])
        block = question.goal_block("Fix the bug.")  # no workspace: nothing settled by the project
        self.assertIn("decisions open", block)
        self.assertIn("inverse", block)
        self.assertIn("ask_user", block)

    def test_detail_nudge_once(self):
        st = {"step": 5, "self_check_fail": {"step": 5}}
        self.assertIn("ask the user", question.detail_block(st))
        self.assertEqual(question.detail_block(st), "")

    def test_writing_a_test_triggers_the_assumption_check_once(self):
        from mihad_memory.experience import failures

        class Eng:
            def active_lessons(self): return []
            def fired(self, *a): pass
            def log(self, *a): pass
        os.environ["MIHAD_QUESTION"] = "1"
        Path(f"{self.state}.task.txt").write_text("Raise on mutation during iteration.", encoding="utf-8")
        seen = []
        old = question.assumption_check
        question.assumption_check = lambda ws, task, state, model=None: seen.append(task) or "[question] asked"
        try:
            ev = {"toolName": "write", "input": {"path": "tests/test_x.py"}, "isError": False, "text": "ok"}
            self.assertIn("[question] asked", failures.detect(Eng(), ev, self.state, self.tmp.name))
            self.assertEqual(failures.detect(Eng(), ev, self.state, self.tmp.name), "")  # once per session
            ev2 = {"toolName": "write", "input": {"path": "bidict/_base.py"}, "isError": False, "text": "ok"}
            self.assertEqual(failures.detect(Eng(), ev2, self.state, self.tmp.name), "")
        finally:
            question.assumption_check = old
        self.assertEqual(len(seen), 1)

    def test_start_block_is_delivered_once(self):
        self.fake(["1. Should it raise or return None?"])
        task = Path(self.tmp.name) / "task.txt"
        task.write_text("Fix the bug.", encoding="utf-8")
        question.start(self.tmp.name, self.state, str(task))
        self.assertIn("decisions open", question.pending_start(self.state))
        self.assertEqual(question.pending_start(self.state), "")

    def test_off_without_oracle_or_flag(self):
        os.environ["MIHAD_EXPERIENCE_DIR"] = self.tmp.name
        os.environ.pop("MIHAD_QUESTION", None)
        self.assertFalse(question.enabled(self.tmp.name))
        self.fake([])  # no model call may happen
        a = question.ask(self.tmp.name, "Anything?", self.state)
        self.assertEqual(a["source"], "none")


if __name__ == "__main__":
    unittest.main()
