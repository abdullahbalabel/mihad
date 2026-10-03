"""Idea 6: compile repeated command sequences into verified skills.

Past sessions keep running the same sequences by hand: find the test class for the function they
changed, run it, then run the whole suite. Mining counts these sequences across independent tasks;
a sequence seen often enough is compiled into a parameterized skill, and the skill is adopted only
after it runs successfully on the user's final version of past fixes.
"""
import re
import shlex
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from .. import langs, project
from .common import export_tree, git, is_lib, parse_diff, symbols_in_ranges

MIN_TASKS = 2
MIN_VERIFIED = 2
TIMEOUT = 600


def _test_id(cwd, path, name, runner):
    rel = path.relative_to(Path(cwd))
    if runner == "pytest":
        return f"{rel.as_posix()}::{name}"
    return ".".join(rel.with_suffix("").parts) + f".{name}"


def _other_tests_for(cwd, cfg, symbol):
    """Non-Python: test files that mention the symbol, named the way the project's runner wants them."""
    found = []
    for path in project.test_files(cwd, cfg):
        src = path.read_text(encoding="utf-8", errors="replace")
        if not re.search(rf"\b{re.escape(symbol)}\b", src):
            continue
        lang = langs.language_of(path)
        names = [n for n in langs.test_function_names(src, lang)
                 if re.search(rf"\b{re.escape(symbol)}\b", _block_after(src, n))] or langs.test_function_names(src, lang)
        found += langs.test_ids_for_file(path, cwd, cfg["test_runner"], names)
    return sorted(set(found))


def _block_after(src, name, size=2500):
    i = src.find(name)
    return src[i:i + size] if i >= 0 else ""


def test_classes_for(cwd, symbol):
    """Tests whose body calls the symbol. Python: unittest dotted ids or pytest node ids (test classes,
    and module-level test functions for pytest). Other languages: see _other_tests_for."""
    cfg = project.load(cwd)
    if cfg.get("language", "python") != "python":
        return _other_tests_for(cwd, cfg, symbol)
    found = []
    for path in project.test_files(cwd, cfg):
        src = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"^class (\w+)\b[^:]*:\n(.*?)(?=^\S|\Z)", src, re.M | re.S):
            if re.search(rf"\b{re.escape(symbol)}\(", m.group(2)) or symbol.lower() + "tests" == m.group(1).lower():
                found.append(_test_id(cwd, path, m.group(1), cfg["test_runner"]))
        if cfg["test_runner"] == "pytest":
            for m in re.finditer(r"^def (test_\w+)\(.*?(?=^\S|\Z)", src, re.M | re.S):
                if re.search(rf"\b{re.escape(symbol)}\(", m.group(0)):
                    found.append(_test_id(cwd, path, m.group(1), "pytest"))
    return found


def _test_cmd(cwd, ids):
    cfg = project.load(cwd)
    if cfg["test_runner"] == "pytest":
        return [project.test_python(cfg), "-m", "pytest", "-q", *ids]
    if cfg["test_runner"] == "unittest":
        return [project.test_python(cfg), "-m", "unittest", *ids]
    return resolve(langs.focused_command(cfg["test_runner"], ids) or shlex.split(cfg["test_command"]), cfg)


def resolve(cmd, cfg=None):
    """argv with the executable resolved (npm, npx, gradle... are .cmd files on Windows); `python` is the
    project's test interpreter."""
    cmd = list(cmd)
    if cmd and cmd[0] in ("python", "python3"):
        cmd[0] = project.test_python(cfg or {})
    elif cmd:
        cmd[0] = shutil.which(cmd[0]) or cmd[0]
    return cmd


def _run(cmd, cwd):
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=TIMEOUT)
        return res.returncode, (res.stdout + res.stderr)
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def _tail(text, n=15):
    return "\n".join(text.strip().splitlines()[-n:])


def skill_test_symbol(cwd, symbol):
    classes = test_classes_for(cwd, symbol)
    if not classes:
        return 2, f"No test found that uses {symbol}."
    code, out = _run(_test_cmd(cwd, classes), cwd)
    return code, f"Ran {', '.join(classes)}\n{_tail(out)}"


def changed_symbols(cwd):
    diff = git(cwd, "diff", "HEAD", check=False)
    out = set()
    for path, v in parse_diff(diff).items():
        if is_lib(path, cwd) and (Path(cwd) / path).exists():
            out |= symbols_in_ranges((Path(cwd) / path).read_text(encoding="utf-8", errors="replace"),
                                     v["new_ranges"], langs.language_of(path))
    return sorted(out)


def skill_check_change(cwd):
    parts, worst = [], 0
    syms = changed_symbols(cwd)
    if not syms:
        parts.append("No changed library functions found in git diff.")
    for s in syms:
        code, out = skill_test_symbol(cwd, s)
        if code != 2:
            worst = max(worst, code)
        parts.append(f"[{s}] exit={code}\n{out}")
    cfg = project.load(cwd)
    code, out = _run(resolve(shlex.split(cfg["test_command"]), cfg), cwd)
    worst = max(worst, code)
    parts.append(f"[full suite] exit={code}\n{_tail(out, 6)}")
    return (1 if worst else 0), "\n\n".join(parts)


SKILLS = {
    "test_symbol": {"run": skill_test_symbol, "params": ["symbol"],
                    "description": "Run the test classes that exercise a library function. Args: symbol=<function name>."},
    "check_change": {"run": skill_check_change, "params": [],
                     "description": "Run focused tests for every function changed in git diff, then the full suite."},
}


def mine(engine, verify=True):
    eps = [e for e in engine.episodes()]
    templates = Counter()
    focused, sequence = defaultdict(set), defaultdict(set)
    for e in eps:
        seen = set()
        focused_at = None
        for i, c in enumerate(e["commands"]):
            if c["error"]:
                continue
            seen.add(c["template"])
            if "{test_target}" in c["template"] and "unittest" in c["template"]:
                focused["test_symbol"].add(e["task_id"])
                focused_at = i if focused_at is None else focused_at
            if "discover" in c["template"] and focused_at is not None:
                sequence["check_change"].add(e["task_id"])
        templates.update(seen)
    registry = {"observed_templates": [{"template": t, "tasks": n} for t, n in templates.most_common(15)],
                "skills": {}}
    support = {"test_symbol": focused["test_symbol"], "check_change": sequence["check_change"]}
    by_task = {e["task_id"]: e for e in eps}
    for name, tids in support.items():
        rec = {"description": SKILLS[name]["description"], "params": SKILLS[name]["params"],
               "support": sorted(tids), "verified_on": [], "status": "candidate"}
        if len(tids) >= MIN_TASKS and verify:
            for tid in sorted(tids):
                ep = by_task[tid]
                if len(rec["verified_on"]) >= MIN_VERIFIED or not ep.get("families"):
                    continue
                if _verify(engine, name, ep):
                    rec["verified_on"].append(tid)
            rec["status"] = "adopted" if len(rec["verified_on"]) >= MIN_VERIFIED else "candidate"
        registry["skills"][name] = rec
    engine.put("skills", registry)
    return registry


def _repo(engine):
    return engine.get("config", {}).get("repo")


def _verify(engine, name, ep):
    """The skill must succeed on the user's final version of a past fix."""
    repo = _repo(engine)
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        export_tree(repo, ep["parent"], ws)
        git(ws, "init", "-q")
        git(ws, "add", "-A")
        git(ws, "-c", "user.email=v@mihad", "-c", "user.name=v", "commit", "-qm", "baseline")
        export_tree(repo, ep["commit"], ws)
        if name == "test_symbol":
            code, _ = skill_test_symbol(ws, ep["families"][0])
        else:
            code, _ = skill_check_change(ws)
        return code == 0


def run_skill(engine, name, cwd, args):
    reg = engine.get("skills", {}).get("skills", {})
    if reg.get(name, {}).get("status") != "adopted":
        return 2, f"Skill {name!r} is not adopted. Adopted: {[k for k, v in reg.items() if v['status'] == 'adopted']}"
    engine.log("skill_runs", {"skill": name, "args": args})
    return SKILLS[name]["run"](cwd, **{k: v for k, v in args.items() if k in SKILLS[name]["params"]})
