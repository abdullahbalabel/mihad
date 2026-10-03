import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mihad_memory.service import Memory, MemoryError  # noqa: E402

PY = sys.executable


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_project(tmp):
    """A tiny git project with one module and one pre-existing test file."""
    p = Path(tmp) / "proj"
    (p / "tests").mkdir(parents=True)
    (p / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (p / "tests" / "test_calc.py").write_text(
        "import unittest\nfrom calc import add\n\n"
        "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
        encoding="utf-8")
    (p / "README.md").write_text("Config lives in settings.toml\n", encoding="utf-8")
    git(p, "init", "-q")
    git(p, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    git(p, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return p


CMD = f'"{PY}" -m unittest tests/test_calc.py'.replace("\\", "/")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = make_project(self.tmp.name)
        self.db = Path(self.tmp.name) / "mem" / "memory.db"

    def tearDown(self):
        for m in getattr(self, "_mems", []):
            m.store.close()
        self.tmp.cleanup()

    def mem(self, policy="verified", **kw):
        m = Memory(self.proj, db_path=self.db, policy=policy,
                   allowed_commands=(f'"{PY}" -m unittest'.replace("\\", "/"),), **kw)
        self._mems = getattr(self, "_mems", []) + [m]
        return m

    def skill(self, m, files=("tests/test_calc.py",), cmd=CMD, **kw):
        return m.propose("skill", "add numbers", "use calc.add for sums", "math",
                         {"command": cmd, "test_files": list(files)}, **kw)


class GateTests(Base):
    def test_skill_adopted_when_baseline_tests_pass(self):
        r = self.skill(self.mem())
        self.assertEqual(r["status"], "adopted", r)

    def test_skill_rejected_when_tests_fail(self):
        (self.proj / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        r = self.skill(self.mem())
        self.assertEqual(r["status"], "rejected", r)

    def test_agent_authored_test_is_not_independent(self):
        m = self.mem()
        (self.proj / "tests" / "test_new.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n", encoding="utf-8")
        cmd = f'"{PY}" -m unittest tests/test_new.py'.replace("\\", "/")
        r = self.skill(m, files=("tests/test_new.py",), cmd=cmd)
        self.assertEqual(r["status"], "provisional", r)

    def test_modified_baseline_test_is_not_independent(self):
        m = self.mem()
        path = self.proj / "tests" / "test_calc.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# weakened\n", encoding="utf-8")
        self.assertEqual(self.skill(m)["status"], "provisional")

    def test_command_must_be_allowed_and_run_listed_files(self):
        m = self.mem()
        with self.assertRaises(MemoryError):
            self.skill(m, cmd="echo ok")
        with self.assertRaises(MemoryError):
            self.skill(m, cmd=f'"{PY}" -m unittest tests/other.py'.replace("\\", "/"))

    def test_lesson_stays_provisional_until_human_approval(self):
        m = self.mem()
        r = m.propose("lesson", "be careful", "always run tests")
        self.assertEqual(r["status"], "provisional")
        m.approve(r["id"])
        self.assertEqual(m.store.get(r["id"])["status"], "adopted")

    def test_store_all_adopts_immediately_with_shadow_decision(self):
        (self.proj / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        m = self.mem("store_all")
        r = self.skill(m)
        rec = m.store.get(r["id"])
        self.assertEqual(rec["status"], "adopted")
        self.assertEqual(rec["shadow_decision"], "rejected")

    def test_off_policy_stores_nothing(self):
        m = self.mem("off")
        self.assertFalse(m.propose("lesson", "x", "y")["stored"])
        self.assertEqual(m.recall("x")["items"], [])
        self.assertEqual(m.store.records(), [])


class FactAndLineageTests(Base):
    def test_fact_verified_by_quote_and_suspended_when_source_changes(self):
        m = self.mem()
        r = m.propose("fact", "config location", "configuration is in settings.toml", "config",
                      {"file": "README.md", "quote": "settings.toml"})
        self.assertEqual(r["status"], "adopted")
        self.assertEqual(len(m.recall("config settings")["items"]), 1)
        (self.proj / "README.md").write_text("Config lives in config.yaml\n", encoding="utf-8")
        self.assertEqual(m.recall("config settings")["items"], [])
        self.assertEqual(m.store.get(r["id"])["status"], "suspended")

    def test_fact_quoting_agent_written_text_is_provisional_until_baseline(self):
        m = self.mem()
        (self.proj / "calc.py").write_text("def add(a, b):\n    return a + b  # fast path\n", encoding="utf-8")
        r = m.propose("fact", "fast path", "add has a fast path", "", {"file": "calc.py", "quote": "# fast path"})
        self.assertEqual(r["status"], "provisional", r)
        # The change is later committed and a new session starts from that baseline.
        git(self.proj, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "next")
        m2 = self.mem()
        items = m2.recall("fast path")["items"]
        self.assertEqual(items[0]["status"], "adopted")

    def test_fact_with_wrong_quote_rejected(self):
        r = self.mem().propose("fact", "x", "y", "", {"file": "README.md", "quote": "not there"})
        self.assertEqual(r["status"], "rejected")

    def test_correction_cascades_to_descendants(self):
        m = self.mem()
        parent = m.propose("fact", "cfg", "config in settings.toml", "", {"file": "README.md", "quote": "settings.toml"})
        child = self.skill(m, derived_from=[parent["id"]])
        res = m.correct(parent["id"], "wrong")
        self.assertIn(child["id"], res["suspended_descendants"])
        self.assertEqual(m.store.get(child["id"])["status"], "suspended")

    def test_delete_erases_content_and_cascades(self):
        m = self.mem()
        parent = m.propose("lesson", "secret", "token is abc")
        child = m.propose("lesson", "derived", "uses the token", derived_from=[parent["id"]])
        m.delete(parent["id"], "privacy")
        self.assertEqual(m.store.get(parent["id"])["content"], "")
        self.assertEqual(m.store.get(child["id"])["status"], "suspended")
        self.assertEqual(m.recall("token")["items"], [])

    def test_supersede_replaces_old_version(self):
        m = self.mem()
        old = self.skill(m)
        new = self.skill(m, supersedes=old["id"])
        self.assertEqual(m.store.get(old["id"])["status"], "superseded")
        self.assertEqual(m.store.get(new["id"])["version"], 2)


class PreferenceTests(Base):
    def setUp(self):
        super().setUp()
        self.user_log = Path(self.tmp.name) / "user_messages.txt"
        self.user_log.write_text("Please always add a line to docs/versions.rst for every change.\n", encoding="utf-8")
        os.environ["MIHAD_USER_LOG"] = str(self.user_log)

    def tearDown(self):
        os.environ.pop("MIHAD_USER_LOG", None)
        super().tearDown()

    def test_preference_with_users_exact_words_is_adopted(self):
        r = self.mem().propose("preference", "changelog line", "add a docs/versions.rst line per change", "",
                               {"quote": "always add a line to docs/versions.rst"})
        self.assertEqual(r["status"], "adopted", r)

    def test_inferred_or_paraphrased_preference_stays_provisional(self):
        m = self.mem()
        self.assertEqual(m.propose("preference", "x", "user likes short commits")["status"], "provisional")
        r = m.propose("preference", "y", "changelog", "", {"quote": "add changelog entries"})
        self.assertEqual(r["status"], "provisional")

    def test_store_all_adopts_inferred_preference(self):
        r = self.mem("store_all").propose("preference", "x", "user likes debug prints")
        self.assertEqual(r["status"], "adopted")

    def test_preferences_returned_for_any_query(self):
        m = self.mem()
        m.propose("preference", "changelog line", "add a versions.rst line", "",
                  {"quote": "always add a line to docs/versions.rst"})
        out = m.recall("completely unrelated numeric_range bug")
        self.assertEqual(len(out["preferences"]), 1)
        self.assertTrue(out["preferences"][0]["trust"].startswith("stated by the user"))


class V02Tests(Base):
    def setUp(self):
        super().setUp()
        self.user_log = Path(self.tmp.name) / "user_messages.txt"
        self.user_log.write_text(
            "Fix the bug.\nSome standing preferences for all future sessions:\n"
            "1. Always add a changelog line.\n2. Always keep functions short.\n"
            "For this task only, add a debug print.\n", encoding="utf-8")
        os.environ["MIHAD_USER_LOG"] = str(self.user_log)

    def tearDown(self):
        os.environ.pop("MIHAD_USER_LOG", None)
        super().tearDown()

    def test_standing_preferences_captured_one_off_skipped(self):
        m = self.mem()
        prefs = m.recall("anything")["preferences"]
        texts = [p["content"] for p in prefs]
        self.assertEqual(len(prefs), 2, texts)
        self.assertFalse(any("debug print" in t for t in texts))
        self.assertTrue(all(p["trust"].startswith("stated by the user") for p in prefs))

    def test_capture_is_idempotent_across_sessions(self):
        self.mem()
        self.assertEqual(len(self.mem().recall("x")["preferences"]), 2)

    def test_agent_cannot_suspend_user_preference_but_human_can(self):
        m = self.mem()
        pid = m.recall("x")["preferences"][0]["id"]
        with self.assertRaises(MemoryError):
            m.correct(pid, "agent thinks it conflicts")
        m.correct(pid, "user changed their mind", by_human=True)
        self.assertEqual(m.store.get(pid)["status"], "suspended")

    def test_store_all_does_not_auto_capture(self):
        self.assertEqual(self.mem("store_all").recall("x")["preferences"], [])

    def test_paraphrased_duplicates_collapse_in_recall(self):
        m = self.mem()
        for title in ("range reversed crashes on empty ranges", "empty ranges crash range reversed"):
            m.propose("fact", title, "numeric_range reversed raised IndexError on empty ranges config",
                      "", {"file": "README.md", "quote": "settings.toml"})
        items = m.recall("numeric_range reversed empty")["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["repeats"], 2)


class RelevanceGateTests(Base):
    def test_generic_words_alone_do_not_recall_and_k_is_capped(self):
        m = self.mem()
        topics = ["parser tokens grammar", "cache eviction ttl", "socket retry backoff",
                  "widget3 render layout", "queue worker lease", "schema migration rollback"]
        for i, topic in enumerate(topics):
            # Distinct items (low similarity) that share only generic words: size, check, value.
            title = "note 3 about widget3" if i == 3 else f"note about {topic.split()[0]}"
            m.propose("lesson", title, f"{topic} needs a size check and a value")
        # Only generic words ("size", "check", "value") shared with every item: nothing returned.
        self.assertEqual(m.recall("size check value")["items"], [])
        # A distinctive word returns its item; never more than RECALL_K items.
        self.assertEqual(m.recall("widget3 size check")["items"][0]["title"], "note 3 about widget3")
        self.assertLessEqual(len(m.recall("parser cache socket widget3 queue schema")["items"]), 3)


class McpProtocolTests(Base):
    def test_stdio_roundtrip(self):
        env = dict(os.environ, MIHAD_PROJECT=str(self.proj), MIHAD_DB=str(self.db), MIHAD_POLICY="verified",
                   PYTHONPATH=str(ROOT))
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_propose", "arguments": {
                "kind": "fact", "title": "cfg", "content": "config in settings.toml",
                "verify": {"file": "README.md", "quote": "settings.toml"}}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "memory_recall", "arguments": {"query": "config"}}},
        ]
        inp = "".join(json.dumps(x) + "\n" for x in msgs)
        res = subprocess.run([PY, "-m", "mihad_memory"], input=inp, capture_output=True, text=True,
                             encoding="utf-8", env=env, cwd=str(self.proj), timeout=60)
        replies = [json.loads(line) for line in res.stdout.splitlines() if line.strip()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4], res.stderr)
        self.assertEqual(len(replies[1]["result"]["tools"]), 4)
        self.assertIn('"adopted"', replies[2]["result"]["content"][0]["text"])
        self.assertIn("cfg", replies[3]["result"]["content"][0]["text"])
        log = (self.db.parent / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn("session_start", log)
        self.assertIn("tool_call", log)


if __name__ == "__main__":
    unittest.main()
