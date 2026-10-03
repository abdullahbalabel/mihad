"""The engine on non-Python projects: detection for each language, and a full run on JavaScript
(node --test is available wherever Node.js is; the other toolchains are only detected here)."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mihad_memory import install, project  # noqa: E402
from mihad_memory.experience import checkers, dream, practice, skills  # noqa: E402
from mihad_memory.experience.common import library_symbols, symbols_in_ranges  # noqa: E402
from mihad_memory.experience.engine import Engine  # noqa: E402

CART_JS = """export function total(prices, discount = 0) {
  if (discount < 0 || discount > 100) {
    throw new RangeError("discount must be between 0 and 100");
  }
  const sum = prices.reduce((a, b) => a + b, 0);
  return sum - (sum * discount) / 100;
}

export function firstWord(text) {
  const parts = text.split(" ");
  return parts[0];
}
"""
CART_TEST = """import { test } from "node:test";
import assert from "node:assert/strict";
import { total, firstWord } from "../src/cart.mjs";

test("total applies the discount", () => {
  assert.equal(total([10, 10], 50), 10);
});

test("firstWord", () => {
  assert.equal(firstWord("a b"), "a");
});
"""


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=u@u", "-c", "user.name=u", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def make_js(root):
    root = Path(root)
    (root / "src").mkdir(parents=True)
    (root / "test").mkdir()
    (root / "src" / "cart.mjs").write_text(CART_JS, encoding="utf-8")
    (root / "test" / "cart.test.mjs").write_text(CART_TEST, encoding="utf-8")
    (root / "package.json").write_text(json.dumps({"name": "cart", "type": "module",
                                                   "scripts": {"test": "node --test"}}), encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    project.clear_cache()
    return root


def files(root, mapping):
    for rel, text in mapping.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    project.clear_cache()
    return root


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        project.clear_cache()
        self.tmp.cleanup()

    def check(self, name, mapping, lang, runner, sources, source_file, test_file):
        root = files(self.base / name, mapping)
        cfg = project.load(root)
        self.assertEqual((cfg["language"], cfg["test_runner"], cfg["source_dirs"]), (lang, runner, sources))
        self.assertTrue(project.is_source(source_file, cfg), source_file)
        self.assertFalse(project.is_source(test_file, cfg), test_file)
        self.assertTrue(project.is_test(test_file, cfg), test_file)

    def test_go(self):
        self.check("g", {"go.mod": "module x\n", "pkg/cart/cart.go": "package cart\n",
                         "pkg/cart/cart_test.go": "package cart\n"},
                   "go", "go", ["pkg"], "pkg/cart/cart.go", "pkg/cart/cart_test.go")

    def test_rust(self):
        self.check("r", {"Cargo.toml": "[package]\n", "src/lib.rs": "", "tests/cart.rs": ""},
                   "rust", "cargo", ["src"], "src/lib.rs", "tests/cart.rs")

    def test_java_maven(self):
        self.check("j", {"pom.xml": "<project/>", "src/main/java/shop/Cart.java": "package shop;",
                         "src/test/java/shop/CartTest.java": "package shop;"},
                   "java", "maven", ["src/main/java"], "src/main/java/shop/Cart.java",
                   "src/test/java/shop/CartTest.java")

    def test_csharp(self):
        self.check("c", {"Shop.sln": "", "Shop/Shop.csproj": "<Project/>", "Shop/Cart.cs": "",
                         "Shop.Tests/Shop.Tests.csproj": "<Project/>", "Shop.Tests/CartTests.cs": ""},
                   "csharp", "dotnet", ["Shop"], "Shop/Cart.cs", "Shop.Tests/CartTests.cs")

    def test_typescript_jest(self):
        self.check("t", {"tsconfig.json": "{}", "package.json": json.dumps({"devDependencies": {"jest": "1"},
                                                                         "scripts": {"test": "jest"}}),
                         "src/cart.ts": "", "src/cart.test.ts": ""},
                   "typescript", "jest", ["src"], "src/cart.ts", "src/cart.test.ts")


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class JavaScriptRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_js(Path(self.tmp.name) / "cart")

    def tearDown(self):
        project.clear_cache()
        self.tmp.cleanup()

    def test_detect_symbols_and_focused_tests(self):
        cfg = project.load(self.root)
        self.assertEqual((cfg["language"], cfg["test_runner"]), ("javascript", "node"))
        self.assertEqual(library_symbols(self.root), {"total", "firstWord"})
        self.assertEqual(symbols_in_ranges(CART_JS, [(5, 1)], "javascript"), {"total"})
        self.assertEqual(skills.test_classes_for(self.root, "firstWord"), ["test/cart.test.mjs"])
        code, out = skills.skill_test_symbol(self.root, "firstWord")
        self.assertEqual(code, 0, out)

    def test_test_file_checker_gate_and_mutants(self):
        base = git(self.root, "rev-parse", "HEAD")
        fixed = CART_JS.replace('const parts = text.split(" ");', "const parts = text.trim().split(/\\s+/);")
        (self.root / "src" / "cart.mjs").write_text(fixed, encoding="utf-8")
        git(self.root, "commit", "-qam", "firstWord on empty text")
        fixed_sha = git(self.root, "rev-parse", "HEAD")
        eng = Engine(self.root / ".mihad" / "experience")
        eng.put("config", {"repo": str(self.root)})
        src = Path(self.tmp.name) / "check.test.mjs"
        src.write_text('import { test } from "node:test";\nimport assert from "node:assert/strict";\n'
                       'import { firstWord } from "../src/cart.mjs";\n'
                       'test("leading spaces", () => { assert.equal(firstWord("  a b"), "a"); });\n', encoding="utf-8")
        target = {"file": "test/mihad_check_t1.test.mjs", "source": str(src), "ids": ["test/mihad_check_t1.test.mjs"]}
        v = checkers.validate(target, self.root, base, fixed_sha)
        self.assertTrue(v["adopted"], v)
        self.assertFalse((self.root / "test" / "mihad_check_t1.test.mjs").exists())  # removed after the run
        (eng.root / "checkers").mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, eng.root / "checkers" / "t1__check.test.mjs")
        eng.put("checkers", {"t1": {"status": "adopted", "path": "checkers/t1__check.test.mjs", "families": ["firstWord"],
                                    "file": target["file"], "ids": target["ids"], "attempts": []}})
        eng.add_episode({"source": "live", "task_id": "t1", "subject": "firstWord on empty text", "body": "",
                         "parent": base, "commit": fixed_sha, "hidden_test_ids": None, "model": None,
                         "hidden_pass": True, "tokens": 1, "turns": 1, "families": ["firstWord"],
                         "agent_files": ["src/cart.mjs"], "ref_files": ["src/cart.mjs"], "commands": [],
                         "errors": [], "reads": {}, "n_steps": 1})
        spec = dream.make_tasks(eng, str(self.root), n=1, seed=2)
        self.assertEqual(len(spec["tasks"]), 1)
        task = spec["tasks"][0]
        self.assertIn("src/cart.mjs", task["base_patch"])
        self.assertEqual(task["hidden_test_files"], ["test/cart.test.mjs"])

        def fake_agent(cmd, ws, out_dir, env, seconds, visible, busy):
            subprocess.run(["git", "apply", "-R", "-"], input=task["base_patch"], text=True, cwd=ws, check=True)
            (out_dir / "agent.jsonl").write_text("", encoding="utf-8")

        install.install(self.root, log=lambda *_: None)
        orig = practice._run_agent
        practice._run_agent = fake_agent
        try:
            rows = practice.run(eng, eng.root / "dreams" / "js1", cfg=project.load(self.root))
        finally:
            practice._run_agent = orig
        self.assertTrue(rows and all(r["hidden_pass"] and r["checker_pass"] for r in rows), rows)


if __name__ == "__main__":
    unittest.main()
