"""Executable review checks: evidence about the agent's actual change, never generic advice.

The experiments showed that advice added to every session is noise, while a concrete finding (a
failure that really happened) changes what the agent does. Each check here runs something and
reports only what it observed:

    mutation adequacy   break each changed line slightly in a temporary copy; if the tests still
                        pass, that line is not tested
    format and lint     the changed files no longer match the project's formatter, or gained new
                        lint findings (compared with the same files at the starting commit)
    preference check    a checkable standing preference ("always add a regression test") is not
                        met, or the new tests also pass on the old code
    iterator probe      (Python) a changed function whose parameter is named `iterable` fails when
                        it receives a one-shot iterator instead of a list

All of them work on the files the agent changed and stay within a time budget.
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .. import langs, project
from .common import git, is_lib, top_level_defs
from .dream import C_FAMILY_MUTATIONS, MUTATIONS, PYTHON_ONLY

COPY_SKIP = {".git", "node_modules", "vendor", ".venv", "venv", "__pycache__", ".mihad", ".omp", ".claude",
             ".codex", "target", "build", "dist", ".pytest_cache", ".ruff_cache"}
MAX_MUTANTS = 8
MUTATION_BUDGET = 150  # seconds
TEST_TIMEOUT = 120


# ---------------------------------------------------------------- helpers

def added_lines(ws):
    """{path: [new line numbers of added lines]} for the project's changed source files."""
    out, cur, ln = {}, None, 0
    for line in git(ws, "diff", "HEAD", "--unified=0", check=False).splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            cur = out.setdefault(path, []) if is_lib(path, ws) else None
        elif line.startswith("+++ /dev/null"):
            cur = None
        elif line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
            ln = int(m.group(1)) if m else 0
        elif cur is not None and line.startswith("+") and not line.startswith("+++"):
            cur.append(ln)
            ln += 1
    return {p: v for p, v in out.items() if v}


def changed_tests(ws, cfg):
    files = git(ws, "diff", "HEAD", "--name-only", check=False).splitlines()
    files += git(ws, "ls-files", "--others", "--exclude-standard", check=False).splitlines()
    return sorted({f for f in files if project.is_test(f, cfg) and (Path(ws) / f).exists()})


def copy_workspace(ws, dest):
    ws, dest = Path(ws), Path(dest)

    def ignore(d, names):
        return [n for n in names if n in COPY_SKIP]

    shutil.copytree(ws, dest, ignore=ignore, dirs_exist_ok=True)
    from .checkers import prepare_tree
    prepare_tree(dest, ws)
    return dest


def run(cmd, cwd, timeout=TEST_TIMEOUT, env=None):
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout, env=env)
        return res.returncode, res.stdout + res.stderr
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as exc:
        return 127, str(exc)


def test_ids(ws, cfg, symbols, tests):
    """Focused test ids for the changed functions plus the tests the agent changed."""
    from .skills import test_classes_for
    ids = []
    for s in symbols:
        ids += test_classes_for(ws, s)
    runner = cfg.get("test_runner")
    for t in tests:
        path = Path(ws) / t
        if runner == "unittest":
            ids.append(".".join(Path(t).with_suffix("").parts))
        elif runner == "pytest":
            ids.append(t)
        else:
            src = path.read_text(encoding="utf-8", errors="replace")
            ids += langs.test_ids_for_file(path, ws, runner, langs.test_function_names(src, langs.language_of(t)))
    return list(dict.fromkeys(ids))


def run_tests(ws, ids, env=None):
    from .skills import _test_cmd, resolve
    import shlex
    cfg = project.load(ws)
    cmd = _test_cmd(ws, ids) if ids else resolve(shlex.split(cfg["test_command"]), cfg)
    return run(cmd, ws, env=env)


def changed_symbols(ws):
    out = {}
    for path, lines in added_lines(ws).items():
        src = (Path(ws) / path).read_text(encoding="utf-8", errors="replace")
        lang = langs.language_of(path)
        defs = top_level_defs(src, lang)
        out[path] = sorted(n for n, (a, b) in defs.items() if any(a <= x <= b for x in lines))
    return out


# ---------------------------------------------------------------- 1. mutation adequacy

def _ops(lang):
    if lang in (None, "python"):
        return MUTATIONS
    return [m for m in MUTATIONS if m[0] not in PYTHON_ONLY] + C_FAMILY_MUTATIONS


PY_COND = re.compile(r"^(\s*)(if|elif|while)\s+(.+?):(\s*(#.*)?)$")
C_COND = re.compile(r"^(\s*(?:\}\s*else\s+)?)(if|while)\s*\((.+)\)(\s*\{?\s*)$")


def mutate_line(line, lang):
    """The strongest mutant of one line: a negated condition (never equivalent to the original, so a
    survivor means the branch is untested), else the first small operator change that applies."""
    body = line.rstrip("\r\n")
    eol = line[len(body):]
    m = (PY_COND if lang in (None, "python") else C_COND).match(body)
    if m:
        if lang in (None, "python"):
            return f"{m.group(1)}{m.group(2)} not ({m.group(3)}):{m.group(4) or ''}{eol}"
        return f"{m.group(1)}{m.group(2)} (!({m.group(3)})){m.group(4)}{eol}"
    if lang in (None, "python") and not body.rstrip().endswith((":", "(", "[", "{", ",", "\\")) \
            and not body.lstrip().startswith((")", "]", "}", "else", "elif", "except", "finally")) \
            and body.strip() not in ("return", "pass", "continue", "break", "...", "yield") \
            and not re.fullmatch(r"return\s+(None|False|NotImplemented)", body.strip()):
        # (deleting a bare return/pass/continue/break is usually an equivalent mutant: no test can see it;
        # so is deleting `return None/False/NotImplemented`, since falling through returns None, also falsy)
        # Statement deletion: if a new line can disappear and every test still passes, it is untested.
        indent = body[:len(body) - len(body.lstrip())]
        return f"{indent}pass{eol}"
    for pat, rep in _ops(lang):
        if re.search(pat, line):
            return re.sub(pat, rep, line, count=1)
    return None


def mutation_adequacy(ws, cfg, budget=MUTATION_BUDGET, max_mutants=MAX_MUTANTS, seed=0):
    """Survived mutants of the changed lines: places where the change is not tested."""
    ws = Path(ws)
    lines_by_file = added_lines(ws)
    if not lines_by_file:
        return []
    symbols = sorted({s for v in changed_symbols(ws).values() for s in v})
    ids = test_ids(ws, cfg, symbols, changed_tests(ws, cfg))
    candidates = []
    for path, lines in lines_by_file.items():
        lang = langs.language_of(path)
        prefixes = langs.comment_prefixes(lang)
        text = (ws / path).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        for ln in lines:
            s = text[ln - 1].strip() if ln - 1 < len(text) else ""
            if not s or s.startswith(prefixes) or s.startswith(("def ", "class ", "import ", "from ", "@")):
                continue
            mutated_line = mutate_line(text[ln - 1], lang)  # one mutant per line keeps it fast and spread out
            if mutated_line and mutated_line != text[ln - 1]:
                candidates.append((path, ln, mutated_line))
    if not candidates:
        return []
    random.Random(seed).shuffle(candidates)
    candidates = candidates[:max_mutants]
    findings = []
    started = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        copy = copy_workspace(ws, Path(tmp) / "ws")
        code, out = run_tests(copy, ids)
        if code != 0:
            return []  # the tests do not pass as they are; the review reports that through other checks
        # With no test naming the changed functions (private helpers are tested through public ones),
        # the mutants run against the whole suite (ids empty -> the project's test command).
        for path, ln, mutated_line in candidates:
            if time.time() - started > budget:
                break
            target = copy / path
            original = target.read_text(encoding="utf-8")
            lines = original.splitlines(keepends=True)
            lines[ln - 1] = mutated_line
            mutated = "".join(lines)
            if langs.language_of(path) in (None, "python"):
                try:
                    compile(mutated, path, "exec")
                except SyntaxError:
                    continue
            target.write_text(mutated, encoding="utf-8")
            try:
                code, _ = run_tests(copy, ids)
            finally:
                target.write_text(original, encoding="utf-8")
            if code == 0:
                findings.append({"kind": "survived", "path": path, "line": ln,
                                 "before": original.splitlines()[ln - 1].strip(), "after": mutated_line.strip()})
    return findings


# ---------------------------------------------------------------- 2. format and lint

def _ruff(args, cwd):
    return run([sys.executable, "-m", "ruff", *args], cwd, timeout=60)


def format_and_lint(ws, cfg):
    """Formatting that the starting commit had and the change lost; lint findings the change added."""
    ws = Path(ws)
    files = [p for p in added_lines(ws) if p.endswith(".py")]
    if not files or _ruff(["--version"], ws)[0] != 0:
        return []
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for name in ("pyproject.toml", "ruff.toml", ".ruff.toml", "setup.cfg"):
            if (ws / name).exists():
                shutil.copy2(ws / name, base / name)
        for f in files:
            head = git(ws, "show", f"HEAD:{f}", check=False)
            if not head:
                continue
            (base / f).parent.mkdir(parents=True, exist_ok=True)
            (base / f).write_text(head, encoding="utf-8")
        head_fmt = {f: _ruff(["format", "--check", f], base)[0] == 0 for f in files if (base / f).exists()}
        head_lint = {f: _lint_set(base, f) for f in files if (base / f).exists()}
    for f in files:
        if head_fmt.get(f) and _ruff(["format", "--check", f], ws)[0] != 0:
            out.append({"kind": "format", "path": f})
        new = _lint_set(ws, f) - head_lint.get(f, set())
        if new:
            out.append({"kind": "lint", "path": f, "codes": sorted(new)[:4]})
    return out


def _lint_set(cwd, f):
    code, text = _ruff(["check", "--output-format", "json", f], cwd)
    try:
        return {(x["code"], x["message"]) for x in json.loads(text[text.find("["):] or "[]")}
    except (ValueError, KeyError):
        return set()


# ---------------------------------------------------------------- 3. preference compliance

TEST_PREFERENCE = re.compile(r"\b(add|write|include|create)\b.*\b(regression\s+)?tests?\b", re.I)


def preference_checks(ws, cfg):
    """Checkable standing preferences that the change does not meet."""
    db = project.path_in(cfg, "memory_db")
    if not db.exists():
        return []
    from ..store import Store
    store = Store(str(db))
    try:
        prefs = [r for r in store.records(["adopted"]) if r["kind"] == "preference"]
    finally:
        store.close()  # never keep the user's memory database open (Windows locks open files)
    wants_test = next((p for p in prefs if TEST_PREFERENCE.search(p["content"])), None)
    if not wants_test or not added_lines(ws):
        return []
    tests = changed_tests(ws, cfg)
    if not tests:
        return [{"kind": "preference", "preference": wants_test["content"], "detail": "the change adds no test"}]
    # The new tests should fail on the old code: otherwise they do not test the fix.
    with tempfile.TemporaryDirectory() as tmp:
        copy = copy_workspace(ws, Path(tmp) / "ws")
        for path in added_lines(ws):
            head = git(ws, "show", f"HEAD:{path}", check=False)
            if head:
                (copy / path).write_text(head, encoding="utf-8")
        code, _ = run_tests(copy, test_ids(copy, cfg, [], tests))
    if code == 0:
        return [{"kind": "preference", "preference": wants_test["content"],
                 "detail": "the new or changed tests also pass on the old code, so they do not test the fix"}]
    return []


# ---------------------------------------------------------------- 4. iterator probe (Python)

ITER_PARAMS = ("iterable", "iterables")
SITECUSTOMIZE = r'''
import functools, importlib, inspect, json, os, sys
for _mod, _name in json.loads(os.environ.get("MIHAD_PROBE_TARGETS", "[]")):
    try:
        _m = importlib.import_module(_mod)
        _f = getattr(_m, _name)
        _sig = inspect.signature(_f)
    except Exception:
        continue
    _names = [n for n in _sig.parameters if n in ("iterable", "iterables")]
    def _wrap(f=_f, sig=_sig, names=_names):
        @functools.wraps(f)
        def w(*a, **k):
            try:
                b = sig.bind(*a, **k)
            except TypeError:
                return f(*a, **k)
            for n in names:
                v = b.arguments.get(n)
                if sig.parameters[n].kind == inspect.Parameter.VAR_POSITIONAL:
                    b.arguments[n] = tuple(iter(x) if isinstance(x, (list, tuple, str)) else x for x in v)
                elif isinstance(v, (list, tuple, str)):
                    b.arguments[n] = iter(v)
            return f(*b.args, **b.kwargs)
        return w
    _w = _wrap()
    for _mm in list(sys.modules.values()):
        try:
            if getattr(_mm, _name, None) is _f:
                setattr(_mm, _name, _w)
        except Exception:
            pass
'''


def iterator_probe(ws, cfg):
    """Changed Python functions taking `iterable` that break when given a one-shot iterator."""
    ws = Path(ws)
    targets = []
    for path, names in changed_symbols(ws).items():
        if not path.endswith(".py"):
            continue
        src = (ws / path).read_text(encoding="utf-8", errors="replace")
        for n in names:
            m = re.search(rf"^def {re.escape(n)}\((.*?)\)\s*(->[^:]*)?:", src, re.M | re.S)
            if m and re.search(r"\*?\b(iterable|iterables)\b", m.group(1)):
                parts = Path(path).with_suffix("").parts
                root = ws
                if parts[0] == "src" and len(parts) > 1:  # src layout: the package is imported from src/
                    root, parts = ws / "src", parts[1:]
                targets.append((".".join(parts), n, root))
    out = []
    for module, name, root in targets:
        ids = test_ids(ws, cfg, [name], [])
        if not ids:
            continue
        code, _ = run_tests(ws, ids)
        if code != 0:
            continue
        code, text = _probe_run(ws, root, module, name, ids)
        if code == 0:
            continue
        # Only a regression counts: some functions need sized input by design. If the starting commit's
        # version also fails with one-shot iterators, the change did not cause it.
        rel_root = root.relative_to(ws)
        with tempfile.TemporaryDirectory() as tmp:
            base = copy_workspace(ws, Path(tmp) / "ws")
            for path in added_lines(ws):
                head = git(ws, "show", f"HEAD:{path}", check=False)
                if head:
                    (base / path).write_text(head, encoding="utf-8")
            base_code, _ = _probe_run(base, base / rel_root, module, name, ids)
        if base_code == 0:
            first = next((ln.strip() for ln in text.splitlines()
                          if ln.startswith(("FAIL:", "ERROR:")) or "Error" in ln), "a test failed")
            out.append({"kind": "iterator", "function": name, "detail": first[:200]})
    return out


def _probe_run(ws, root, module, name, ids):
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
        env = dict(os.environ, MIHAD_PROBE_TARGETS=json.dumps([[module, name]]),
                   PYTHONPATH=os.pathsep.join([tmp, str(root), str(ws), os.environ.get("PYTHONPATH", "")]))
        return run_tests(ws, ids, env=env)


# ---------------------------------------------------------------- 5. property checks (Python)

TEMPLATES = ("len", "reversed", "getitem", "contains", "eq_hash", "reference", "single_pass")
PROP_SITECUSTOMIZE = "from mihad_memory.experience import propcheck as _p\n_p.install()\n"


def experience_dir(cfg):
    env = os.environ.get("MIHAD_EXPERIENCE_DIR")
    return Path(env) if env else project.path_in(cfg, "experience_dir")


def adopted_templates(cfg):
    """Templates adopted for this project by calibration (properties.calibrate); all of them before."""
    f = experience_dir(cfg) / "properties.json"
    if f.exists():
        try:
            return list(json.loads(f.read_text(encoding="utf-8"))["adopted"])
        except (ValueError, KeyError):
            pass
    return list(TEMPLATES)


def prop_targets(ws):
    """(module, name, import root) for the changed top-level Python functions and classes."""
    ws = Path(ws)
    out = []
    for path, names in changed_symbols(ws).items():
        if not path.endswith(".py"):
            continue
        parts = Path(path).with_suffix("").parts
        root = ws
        if parts[0] == "src" and len(parts) > 1:
            root, parts = ws / "src", parts[1:]
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out += [(".".join(parts), n, root) for n in names if not n.startswith("_")]
    return out


def prop_run(ws, root, targets, templates, ids):
    """Run the tests with the property checks loaded; {function: [findings]}."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "sitecustomize.py").write_text(PROP_SITECUSTOMIZE, encoding="utf-8")
        out = Path(tmp) / "out"
        out.mkdir()
        env = dict(os.environ, MIHAD_PROP_TARGETS=json.dumps([[m, n] for m, n, _ in targets]),
                   MIHAD_PROP_TEMPLATES=json.dumps(templates), MIHAD_PROP_OUT=str(out),
                   PYTHONPATH=os.pathsep.join([tmp, str(root), str(ws), str(project.MIHAD_ROOT),
                                               os.environ.get("PYTHONPATH", "")]))
        run_tests(ws, ids, env=env)
        res = {}
        for f in out.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                res[d["function"]] = d["findings"]
            except (ValueError, KeyError):
                continue
        return res


# Words in a task that make an already-existing property failure relevant to it.
RELEVANCE = {"len": ("len", "length", "size", "count"), "reversed": ("revers",),
             "getitem": ("index", "getitem", "slice", "item"), "contains": ("contain", "index", "membership"),
             "eq_hash": ("eq", "hash", "equal"),
             "reference": ("eq", "hash", "equal", "consistent", "match", "behav", "like"),
             "single_pass": ("iter", "reuse", "stream", "once", "consum", "generator")}


def relevant(template, task_text):
    if not task_text:
        return True
    text = task_text.lower()
    return any(w in text for w in RELEVANCE.get(template, ()))


def property_checks(ws, cfg, templates=None, task_text=None):
    """Counterexamples to properties of the changed functions, found by calling them with variations of
    the arguments their tests use. Reported even when the starting commit fails too: the changed
    function is what the task is about, and the counterexample may be the bug itself."""
    ws = Path(ws)
    templates = templates if templates is not None else adopted_templates(cfg)
    targets = prop_targets(ws)
    if not targets or not templates:
        return []
    ids = test_ids(ws, cfg, sorted({n for _, n, _ in targets}), changed_tests(ws, cfg))
    root = targets[0][2]
    now = prop_run(ws, root, targets, templates, ids)
    if not any(now.values()):
        return []
    rel_root = root.relative_to(ws)
    with tempfile.TemporaryDirectory() as tmp:
        base = copy_workspace(ws, Path(tmp) / "ws")
        for path in added_lines(ws):
            head = git(ws, "show", f"HEAD:{path}", check=False)
            if head:
                (base / path).write_text(head, encoding="utf-8")
        before = prop_run(base, base / rel_root, targets, templates, ids)
    out = []
    for name, found in now.items():
        old = {f["template"] for f in before.get(name, [])}
        for f in found:
            at_start = f["template"] in old
            # A failure the change introduced is always reported; one the starting commit already had only
            # when the task is about that behaviour (otherwise it is an unrelated old bug, a distraction).
            if not at_start or relevant(f["template"], task_text):
                out.append({"kind": "property", **f, "at_start": at_start})
    return out


# ---------------------------------------------------------------- all checks

def describe(f):
    if f["kind"] == "survived":
        if f.get("status") == "survives":
            return (f"Still untested: with `{f['before']}` in {f['path']}:{f['line']} replaced by `{f['after']}`, "
                    f"your tests (including the new ones) still all pass. A test for this line must take an input "
                    f"that reaches it and assert a value that changes when the line is replaced.")
        return (f"Executed on your change: if `{f['before']}` in {f['path']}:{f['line']} became "
                f"`{f['after']}`, all tests would still pass, so this line is untested. Add a test that reaches "
                f"this line and fails for that replacement.")
    if f["kind"] == "self_check":
        return (f"Your own check printed `{f['line']}` (command: {f['command']}) and you made no edit after it. "
                f"Find out why it reported a mismatch before finishing.")
    if f["kind"] == "untested":
        return f"Your change is not tested: {f['detail']}. Add a test for it."
    if f["kind"] == "format":
        return f"{f['path']} no longer matches the project's formatting; run the formatter on it (e.g. `ruff format {f['path']}`)."
    if f["kind"] == "lint":
        return f"New lint findings in {f['path']}: " + "; ".join(f"{c} {m}" for c, m in f["codes"])
    if f["kind"] == "preference":
        return f"Your standing preference \"{f['preference']}\" is not met: {f['detail']}."
    if f["kind"] == "property":
        where = (" The code at the starting commit fails this too, so it may be the bug the task is about."
                 if f.get("at_start") else " The code at the starting commit did not fail this: your change broke it.")
        return (f"Executed on your change: {f['detail']}.{where} Fix the code (not the check), and add this "
                f"call as a test.")
    if f["kind"] == "iterator":
        return (f"`{f['function']}` takes an iterable but fails when it receives a one-shot iterator instead of "
                f"a list ({f['detail']}). Do not read the input twice or call len() on it.")
    return str(f)


def run_all(ws, cfg, max_findings=4, props=False, task_text=None):
    findings = []
    checks = [preference_checks, iterator_probe, mutation_adequacy, format_and_lint]
    if props:
        checks.insert(0, lambda w, c: property_checks(w, c, task_text=task_text))
    for check in checks:
        try:
            found = check(ws, cfg)
        except Exception as exc:  # a check that cannot run must never block the agent
            found = [{"kind": "error", "check": getattr(check, "__name__", "check"), "detail": str(exc)[:200]}]
        findings += [f for f in found if f["kind"] != "error"]
    survived = [f for f in findings if f["kind"] == "survived"]
    others = [f for f in findings if f["kind"] != "survived"]
    return (others + survived[:2])[:max_findings]


def recheck_survived(ws, cfg, previous):
    """Re-run earlier surviving mutants against the tests as they are now: killed, still surviving, or the
    line is gone (changed by the agent)."""
    ws = Path(ws)
    out = []
    symbols = sorted({s for v in changed_symbols(ws).values() for s in v})
    ids = test_ids(ws, cfg, symbols, changed_tests(ws, cfg))
    with tempfile.TemporaryDirectory() as tmp:
        copy = copy_workspace(ws, Path(tmp) / "ws")
        if run_tests(copy, ids)[0] != 0:
            return [{**f, "status": "tests_fail"} for f in previous]
        for f in previous:
            target = copy / f["path"]
            if not target.exists():
                out.append({**f, "status": "gone"})
                continue
            original = target.read_text(encoding="utf-8")
            lines = original.splitlines(keepends=True)
            idx = next((i for i, l in enumerate(lines) if l.strip() == f["before"]), None)
            if idx is None:
                out.append({**f, "status": "gone"})
                continue
            indent = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())]
            eol = lines[idx][len(lines[idx].rstrip("\r\n")):]
            lines[idx] = indent + f["after"] + eol
            target.write_text("".join(lines), encoding="utf-8")
            try:
                code, _ = run_tests(copy, ids)
            finally:
                target.write_text(original, encoding="utf-8")
            out.append({**f, "line": idx + 1, "status": "survives" if code == 0 else "killed"})
    return out
