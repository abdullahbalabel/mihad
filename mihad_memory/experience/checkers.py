"""Idea 1: turn experience into executable verifier scripts.

For each past fix, a model writes a small check of the fixed behavior. The check is adopted only if
it FAILS on the code before the fix and PASSES on the user's final version: the same independence
rule as the memory's adoption gate, applied to code. Adopted checkers guard against regressions at
review time and grade practice tasks while dreaming.

Python projects get a standalone script (run with the interpreter). Other languages get a test file
in the project's own framework, placed at a fixed path for the run and removed afterwards, and run
with the project's focused test command (see langs.py).
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .. import langs, project
from .common import export_tree, git

PROMPT = """\
Write a standalone Python 3 script that checks the behavior changed by this fix in the Python
project {project}. Requirements:
- import only the project's own modules (the repository root is on sys.path; source folders: {sources})
  plus the standard library; do not import test files;
- check the fixed or added behavior with plain assert statements, including the edge case the fix is about;
- it must FAIL (raise) on the code before the fix and PASS on the code after the fix;
- do not check unrelated behavior; at most 40 lines; print "CHECK OK" as the last line.
Reply with only the script in one ```python block.

TASK: {subject}
{body}

THE FIX (diff from before to after):
{diff}
"""

PROMPT_TEST = """\
Write one test file for the {language} project {project}, using the project's own test framework
(test runner: {runner}), that checks the behavior changed by this fix. It will be saved as
`{file}`{extra}. Requirements:
- test only through the project's own code; no new dependencies;
- check the fixed or added behavior, including the edge case the fix is about;
- the test must FAIL on the code before the fix and PASS on the code after the fix;
- do not check unrelated behavior; at most 60 lines.
Reply with only the file content in one fenced code block.

TASK: {subject}
{body}

THE FIX (diff from before to after):
{diff}
"""

RETRY = """
Your previous script did not separate the versions. Before the fix: exit={before}. After the fix:
exit={after}. Output after the fix:
{out}
Write a corrected script.
"""
DEP_DIRS = ("node_modules", "vendor")
COMPILED = ("java", "kotlin", "csharp", "go", "rust", "cpp")


def ask(model, prompt, any_language=False):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(prompt)
        path = fh.name
    try:
        res = subprocess.run(["omp", "-p", "--no-session", "--no-extensions", "--no-skills", "--no-rules", "--no-tools",
                              "--model", model, f"@{path}"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", stdin=subprocess.DEVNULL, timeout=600)
    finally:
        Path(path).unlink(missing_ok=True)
    m = re.search(r"```[\w+-]*\n(.*?)```" if any_language else r"```python\n(.*?)```", res.stdout, re.S)
    return m.group(1) if m else None


def prepare_tree(tree, repo):
    """An exported tree behaves like the project: its config, and its installed dependencies (linked)."""
    tree, repo = Path(tree), Path(repo)
    cfg_file = repo / project.CONFIG
    if cfg_file.exists() and not (tree / project.CONFIG).exists():
        (tree / ".mihad").mkdir(exist_ok=True)
        shutil.copy2(cfg_file, tree / project.CONFIG)
    for name in DEP_DIRS:
        src, dst = repo / name, tree / name
        if src.is_dir() and not dst.exists():
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], capture_output=True)
            else:
                os.symlink(src, dst, target_is_directory=True)
    project.clear_cache()


def target_of(engine, rec):
    """What run_checker needs for an adopted record: a script path (Python) or a test-file spec."""
    if rec.get("file"):
        return {"file": rec["file"], "source": str(engine.root / rec["path"]), "ids": rec["ids"]}
    return engine.root / rec["path"]


def run_checker(target, tree, timeout=60):
    tree = Path(tree)
    if isinstance(target, dict):
        from .skills import _test_cmd
        dest = tree / target["file"]
        existed = dest.exists()
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target["source"], dest)
        try:
            lang = langs.language_of(dest)
            res = subprocess.run(_test_cmd(tree, target["ids"]), cwd=tree, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace",
                                 timeout=max(timeout, 900 if lang in COMPILED else 300))
            return res.returncode, (res.stdout + res.stderr)[-1500:]
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        finally:
            if not existed:
                dest.unlink(missing_ok=True)
    env = {**os.environ, "PYTHONPATH": str(tree)}
    try:
        res = subprocess.run([sys.executable, str(target)], cwd=tree, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout, env=env)
        return res.returncode, (res.stdout + res.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def validate(target, repo, parent, commit):
    with tempfile.TemporaryDirectory() as tmp:
        before, after = Path(tmp) / "before", Path(tmp) / "after"
        before.mkdir()
        after.mkdir()
        export_tree(repo, parent, before)
        export_tree(repo, commit, after)
        for t in (before, after):
            prepare_tree(t, repo)
        b, bout = run_checker(target, before)
        a, aout = run_checker(target, after)
    return {"before_exit": b, "after_exit": a, "before_out": bout[-400:], "after_out": aout[-400:],
            "adopted": b != 0 and a == 0}


def _alnum(text):
    return re.sub(r"[^A-Za-z0-9]", "", text.title())


def test_file_for(cfg, ep, tid, repo):
    """(relative path, extra prompt hint) for a test-file checker, or None when unsupported."""
    lang, runner = cfg.get("language", "python"), cfg.get("test_runner")
    tests = next((d for d in cfg["test_dirs"] if d != "."), "tests")
    src = next((p for p in ep.get("ref_files", []) if project.is_source(p, cfg)), None)
    name = _alnum(f"mihad check {tid}")
    if lang in ("javascript", "typescript"):
        existing = [p.name for p in project.test_files(repo, cfg)[:50]]
        ext = next((m.group(0) for n in existing for m in [re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", n)] if m),
                   ".test.ts" if lang == "typescript" else ".test.js")
        return f"{tests}/mihad_check_{tid}{ext}", " (import the code under test with a relative path from there)"
    if lang == "go" and src:
        d = Path(src).parent.as_posix()
        return f"{d}/mihad_check_{tid}_test.go", f" (same package as the code in {d}/)"
    if lang == "rust":
        return f"tests/mihad_check_{tid}.rs", " (an integration test using the crate's public API)"
    if lang in ("java", "kotlin") and src:
        text = git(repo, "show", f"{ep['commit']}:{src}", check=False)
        m = re.search(r"^\s*package\s+([\w.]+)", text, re.M)
        pkg = m.group(1) if m else ""
        root = "src/test/kotlin" if lang == "kotlin" else "src/test/java"
        ext = ".kt" if lang == "kotlin" else ".java"
        path = f"{root}/{pkg.replace('.', '/')}/{name}Test{ext}".replace("//", "/")
        return path, f" (package {pkg}; class {name}Test)" if pkg else f" (class {name}Test)"
    if lang == "csharp":
        return f"{tests}/{name}Tests.cs", f" (class {name}Tests, in the existing test project)"
    if lang == "php":
        return f"{tests}/{name}Test.php", f" (class {name}Test)"
    if lang == "ruby":
        return (f"spec/mihad_check_{tid}_spec.rb", "") if runner == "rspec" else (f"test/mihad_check_{tid}_test.rb", "")
    return None


def generate(engine, repo, model="anthropic/claude-sonnet-5", attempts=2, only=None):
    reg = engine.get("checkers", {})
    cdir = engine.root / "checkers"
    cdir.mkdir(exist_ok=True)
    seen = set()
    cfg = project.load(repo)
    lang = cfg.get("language", "python")
    for ep in engine.episodes():
        tid = ep["task_id"]
        if tid in seen or (only and tid not in only) or reg.get(tid, {}).get("status") == "adopted":
            continue
        seen.add(tid)
        diff = git(repo, "diff", ep["parent"], ep["commit"])[:14000]
        rec = {"task_id": tid, "families": ep["families"], "status": "rejected", "attempts": []}
        if lang == "python":
            prompt = PROMPT.format(project=cfg["name"], sources=", ".join(cfg["source_dirs"]),
                                   subject=ep["subject"], body=ep.get("body", ""), diff=diff)
            path, spec = cdir / f"{tid}.py", None
        else:
            spec = test_file_for(cfg, ep, tid, repo)
            if not spec:
                rec["status"] = "unsupported"
                reg[tid] = rec
                engine.put("checkers", reg)
                continue
            rel, extra = spec
            prompt = PROMPT_TEST.format(language=lang, project=cfg["name"], runner=cfg.get("test_runner"), file=rel,
                                        extra=extra, subject=ep["subject"], body=ep.get("body", ""), diff=diff)
            path = cdir / f"{tid}__{Path(rel).name}"
        for _ in range(attempts):
            code = ask(model, prompt, any_language=lang != "python")
            if not code:
                rec["attempts"].append({"error": "no code"})
                continue
            path.write_text(code, encoding="utf-8")
            if spec:
                names = langs.test_function_names(code, langs.language_of(spec[0]))
                ids = langs.test_ids_for_file(Path(repo) / spec[0], repo, cfg.get("test_runner"), names)
                target = {"file": spec[0], "source": str(path), "ids": ids}
            else:
                target = path
            v = validate(target, repo, ep["parent"], ep["commit"])
            rec["attempts"].append({k: v[k] for k in ("before_exit", "after_exit")})
            if v["adopted"]:
                rec.update(status="adopted", path=str(path.relative_to(engine.root)), parent=ep["parent"],
                           commit=ep["commit"])
                if spec:
                    rec.update(file=spec[0], ids=target["ids"], lang=lang)
                break
            prompt += RETRY.format(before=v["before_exit"], after=v["after_exit"], out=v["after_out"])
        if rec["status"] != "adopted":
            path.unlink(missing_ok=True)
        reg[tid] = rec
        engine.put("checkers", reg)
        print(tid, rec["status"], rec["attempts"], flush=True)
    return reg


def adopted(engine):
    return {tid: r for tid, r in (engine.get("checkers", {}) or {}).items() if r.get("status") == "adopted"}


def regressions(engine, ws, baseline_cache=None):
    """Adopted checkers that pass on the workspace's baseline commit but fail with the current changes.
    For compiled languages this is off unless the project config sets review_checkers (it is slow)."""
    ws = Path(ws)
    found = []
    base_ok = baseline_cache if baseline_cache is not None else {}
    cfg = project.load(ws)
    checks = adopted(engine)
    if not cfg.get("review_checkers", cfg.get("language", "python") not in COMPILED):
        checks = {}
    if not checks:
        return found, base_ok
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        if any(tid not in base_ok for tid in checks):
            base.mkdir()
            data = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ws, capture_output=True).stdout
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                tar.extractall(base, filter="data")
            prepare_tree(base, ws)
        for tid, r in checks.items():
            target = target_of(engine, r)
            if tid not in base_ok:
                base_ok[tid] = run_checker(target, base)[0] == 0
            if not base_ok[tid]:
                continue  # behavior changed since that fix, or the checker does not apply here
            code, out = run_checker(target, ws)
            if code != 0:
                found.append({"task_id": tid, "families": r["families"], "output": out[-600:]})
    return found, base_ok
