"""Claude Code and Codex: installation, and a full session driven through the hook launcher."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mihad_memory import hooks, install, project  # noqa: E402

CORE = '''def first_word(text):
    return text.split()[0]
'''
TESTS = '''import sys
import unittest
sys.path.insert(0, "src")
from shop.core import first_word


class WordsTests(unittest.TestCase):
    def test_first(self):
        self.assertEqual(first_word("a b"), "a")
'''


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=u@u", "-c", "user.name=u", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def make_project(root):
    root = Path(root)
    (root / "src" / "shop").mkdir(parents=True)
    (root / "src" / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "shop" / "core.py").write_text(CORE, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_core.py").write_text(TESTS, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    project.clear_cache()
    return root


class NormalizeTests(unittest.TestCase):
    def test_tool_names_and_inputs(self):
        self.assertEqual(hooks.normalize_tool("Bash", {"command": "npm test"}), ("bash", {"command": "npm test"}))
        self.assertEqual(hooks.normalize_tool("Read", {"file_path": "a.py", "offset": 10, "limit": 5}),
                         ("read", {"path": "a.py:10-15"}))
        self.assertEqual(hooks.normalize_tool("apply_patch", {"input": "..."})[0], "edit")
        self.assertEqual(hooks.normalize_tool("mcp__mihad_memory__memory_recall", {})[0], "memory_recall")

    def test_codex_apply_patch_path_and_windows_command(self):
        patch = "*** Begin Patch\n*** Update File: src/shop/core.py\n@@\n-a\n+b\n*** End Patch"
        self.assertEqual(hooks.normalize_tool("apply_patch", {"input": patch}), ("edit", {"path": "src/shop/core.py"}))
        posix, windows = install.codex_hook_commands(Path("x/hook.py"))
        self.assertEqual(windows, "& " + posix)  # PowerShell runs a quoted path only with the call operator
        inline = install.codex_inline_hooks(Path("x/hook.py"))
        self.assertEqual(len(inline), 2 * len(install.HOOK_EVENTS))
        self.assertTrue(all(v.startswith("hooks.") for v in inline[1::2]))

    def test_responses(self):
        self.assertEqual(hooks.response_text_and_error({"type": "text", "text": "ok"}), ("ok", False))
        self.assertTrue(hooks.response_text_and_error({"stdout": "", "stderr": "boom", "exit_code": 1})[1])
        self.assertTrue(hooks.response_text_and_error({"type": "error", "text": "File not found"})[1])
        self.assertTrue(hooks.response_text_and_error("Traceback (most recent call last):")[1])


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_project(Path(self.tmp.name) / "shop")
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "echo mine"}]}]}, "model": "x"}), encoding="utf-8")
        (self.root / ".codex").mkdir()
        (self.root / ".codex" / "config.toml").write_text('model = "y"\n', encoding="utf-8")

    def tearDown(self):
        project.clear_cache()
        self.tmp.cleanup()

    def test_install_claude_and_codex_keeps_user_config(self):
        for _ in range(2):  # idempotent
            install.install(self.root, ("claude", "codex"), log=lambda *_: None)
        claude = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(claude["model"], "x")
        self.assertEqual(len(claude["hooks"]["Stop"]), 2)  # the user's hook and ours, once
        self.assertEqual(set(claude["hooks"]), set(install.HOOK_EVENTS))
        self.assertIn("mihad_memory", json.loads((self.root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"])
        toml = (self.root / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertTrue(toml.startswith('model = "y"'))
        self.assertEqual(toml.count(install.TOML_BEGIN), 1)
        self.assertIn("[mcp_servers.mihad_memory.env]", toml)
        codex = json.loads((self.root / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(codex["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"], 3)
        self.assertTrue((self.root / ".mihad" / "hook.py").exists())
        install.uninstall(self.root, ("claude", "codex"), log=lambda *_: None)
        claude = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(claude["hooks"], {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]})
        self.assertEqual((self.root / ".codex" / "config.toml").read_text(encoding="utf-8"), 'model = "y"\n')


class SessionTests(unittest.TestCase):
    """A session as Claude Code would drive it, through the installed launcher."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_project(Path(self.tmp.name) / "shop")
        install.install(self.root, ("claude",), log=lambda *_: None)
        self.launcher = self.root / ".mihad" / "hook.py"

    def tearDown(self):
        project.clear_cache()
        self.tmp.cleanup()

    def fire(self, event, **fields):
        data = {"session_id": "s1", "cwd": str(self.root), "hook_event_name": event, **fields}
        res = subprocess.run([sys.executable, str(self.launcher), "claude"], input=json.dumps(data),
                             capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout) if res.stdout.strip() else None

    def test_full_session(self):
        self.assertIsNone(self.fire("SessionStart", source="startup", model="claude-haiku-4-5"))
        out = self.fire("UserPromptSubmit", prompt="Fix first_word for empty text. Always add a regression test.")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("Always add a regression test.", ctx)  # captured and adopted in the same turn
        read = {"tool_name": "Read", "tool_input": {"file_path": "src/shop/core.py"},
                "tool_response": {"type": "text", "text": "..."}}
        outs = [self.fire("PostToolUse", **read) for _ in range(4)]
        self.assertEqual(outs[:3], [None, None, None])
        self.assertIn("read src/shop/core.py 4 times", outs[3]["hookSpecificOutput"]["additionalContext"])
        (self.root / "src" / "shop" / "core.py").write_text(CORE.replace("[0]", "[0] if text.split() else ''"),
                                                             encoding="utf-8")
        self.fire("PostToolUse", tool_name="Edit", tool_input={"file_path": "src/shop/core.py"},
                  tool_response={"type": "text", "text": "ok"})
        stop = self.fire("Stop", stop_hook_active=False, last_assistant_message="done")
        self.assertEqual(stop["decision"], "block")
        self.assertIn("run the tests again", stop["reason"])
        self.assertIsNone(self.fire("Stop", stop_hook_active=True))  # one extra turn at most
        self.assertIsNone(self.fire("SessionEnd", reason="prompt_input_exit"))
        exp = self.root / ".mihad" / "experience"
        self.assertEqual(json.loads((exp / "sessions.json").read_text(encoding="utf-8"))["count"], 1)
        phases = [json.loads(line)["phase"] for line in (exp / "live_sessions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(phases[0], "start")
        self.assertIn("end", phases)
        steps = (exp / "state").glob("claude-s1.steps.jsonl")
        self.assertEqual(len(next(steps).read_text(encoding="utf-8").splitlines()), 5)

    def test_not_an_installed_project_is_silent(self):
        other = make_project(Path(self.tmp.name) / "other")
        data = {"session_id": "s", "cwd": str(other), "hook_event_name": "UserPromptSubmit", "prompt": "hi"}
        res = subprocess.run([sys.executable, str(self.launcher), "claude"], input=json.dumps(data),
                             capture_output=True, text=True, timeout=60)
        self.assertEqual((res.returncode, res.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
