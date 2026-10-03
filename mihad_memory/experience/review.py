"""Idea 2: an experience reviewer at the decision point (before the agent finishes).

It looks at the agent's actual change and checks it against experience, not against general
advice: adopted checkers from past fixes (regressions), the user's co-change rules, stub and
__all__ consistency that past fixes kept, and whether the tests were run after the last edit.
It reports only concrete findings; with no findings it says nothing.
"""
import ast
import json
from pathlib import Path

from .. import langs
from .checkers import regressions
from .edges import applicable
from .common import git, is_lib, parse_diff, symbols_in_ranges, top_level_defs


def _signature(source, name):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.dump(node.args, include_attributes=False)
        if isinstance(node, ast.ClassDef) and node.name == name:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "__init__":
                    return ast.dump(sub.args, include_attributes=False)
            return ""
    return None


def _all_names(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "__all__" for t in node.targets):
            try:
                return set(ast.literal_eval(node.value))
            except ValueError:
                return None
    return None


def consistency_issues(ws):
    ws = Path(ws)
    files = parse_diff(git(ws, "diff", "HEAD", check=False))
    issues = []
    for path, v in files.items():
        if not (is_lib(path, ws) and path.endswith(".py")) or not (ws / path).exists():
            continue
        now = (ws / path).read_text(encoding="utf-8", errors="replace")
        before = git(ws, "show", f"HEAD:{path}", check=False)
        stub_path = path + "i"
        stub_touched = stub_path in files
        stub = (ws / stub_path).read_text(encoding="utf-8", errors="replace") if (ws / stub_path).exists() else None
        before_defs = top_level_defs(before)
        names_all = _all_names(now)
        for name in sorted(symbols_in_ranges(now, v["new_ranges"])):
            if name.startswith("_"):
                continue
            is_new = name not in before_defs
            if is_new and names_all is not None and name not in names_all:
                issues.append(f"{name} is new in {path} but not listed in its __all__.")
            if stub is None or stub_touched:
                continue
            if is_new:
                issues.append(f"{name} is new in {path}; {stub_path} has no stub for it (past fixes added stubs).")
            elif _signature(before, name) != _signature(now, name) and (f"def {name}" in stub or
                                                                         f"class {name}" in stub):
                issues.append(f"The signature of {name} changed in {path} but {stub_path} was not updated.")
    return issues


def co_change_issues(engine, ws):
    touched = set(parse_diff(git(ws, "diff", "HEAD", check=False)))
    out, fired = [], []
    for l in engine.active_lessons():
        if l["kind"] == "co_change" and set(l["trigger"]["files"]) & touched and l["partner"] not in touched:
            out.append(l["text"] + " Check whether this change needs it too.")
            fired.append(l["id"])
    return out, fired


def edge_checklist(engine, ws, state_path=None):
    """Edge-case lessons that apply to the changed functions and were not yet shown in this session."""
    ws = Path(ws)
    files = parse_diff(git(ws, "diff", "HEAD", check=False))
    snippets = []
    for path, v in files.items():
        if is_lib(path, ws) and (ws / path).exists():
            src = (ws / path).read_text(encoding="utf-8", errors="replace")
            lang = langs.language_of(path)
            defs = top_level_defs(src, lang)
            for name in symbols_in_ranges(src, v["new_ranges"], lang):
                if name not in defs:
                    continue
                a, b = defs[name]
                snippets.append("\n".join(src.splitlines()[a - 1:b])[:1500])
    if not snippets:
        return []
    state = {}
    if state_path and Path(state_path).exists():
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    shown = set(state.get("edges_shown", []))
    out = [l for l in applicable(engine, snippets) if l["id"] not in shown]
    if state_path and out:
        state["edges_shown"] = sorted(shown | {l["id"] for l in out})
        Path(state_path).write_text(json.dumps(state), encoding="utf-8")
    return out


def _self_check(state_path):
    """The agent's own check reported a mismatch and no edit followed it."""
    if not state_path or not Path(state_path).exists():
        return []
    st = json.loads(Path(state_path).read_text(encoding="utf-8"))
    sc = st.get("self_check_fail")
    if sc and sc["step"] > st.get("last_edit", 0):
        return [{"kind": "self_check", "line": sc["line"], "command": sc["command"]}]
    return []


def recheck(engine, ws, tests_after_edit=None, state_path=None, props=False, final=False):
    """Review round 2+: re-run what the previous round found, on the change as it is now. Reports what is
    still wrong (a mutant the new tests still do not kill, a property that still fails). With final=True it
    only measures and logs: the agent gets no further turn."""
    from .. import project
    from . import probes
    ws = Path(ws)
    rpath = Path(f"{state_path}.review.json") if state_path else None
    prev = json.loads(rpath.read_text(encoding="utf-8")) if rpath and rpath.exists() else {"round": 1, "checks": []}
    cfg = project.load(ws)
    survived = [f for f in prev["checks"] if f["kind"] == "survived"]
    found = probes.recheck_survived(ws, cfg, survived) if survived else []
    still = [f for f in found if f["status"] == "survives"]
    issues_found = list(still)
    if any(f["kind"] == "property" for f in prev["checks"]) or props:
        task = Path(f"{state_path}.task.txt").read_text(encoding="utf-8") if state_path and \
            Path(f"{state_path}.task.txt").exists() else None
        issues_found += [f for f in probes.property_checks(ws, cfg, task_text=task)]
    if any(f["kind"] == "format" for f in prev["checks"]):
        issues_found += [f for f in probes.format_and_lint(ws, cfg) if f["kind"] == "format"]
    issues_found += _self_check(state_path)
    issues = [probes.describe(f) for f in issues_found]
    if tests_after_edit is False:
        issues.append("You edited files after the last test run: run the tests again.")
    rnd = prev.get("round", 1) + 1
    engine.log("review", {"round": rnd, "final": final, "issues": issues, "checks": issues_found,
                          "rechecked": found, "previous": prev["checks"]})
    if rpath:
        rpath.write_text(json.dumps({"round": rnd, "checks": issues_found}), encoding="utf-8")
    if final or not issues:
        return {"issues": issues, "text": ""}
    text = ("[experience review] Re-checked on your change after your last reply; these are still wrong:\n"
            + "\n".join(f"- {i}" for i in issues) + "\nFix them, then finish.")
    return {"issues": issues, "text": text}


def review(engine, ws, tests_after_edit=None, cache_path=None, state_path=None, edges=False, checks=False,
           props=False):
    ws = Path(ws)
    if not git(ws, "diff", "HEAD", "--stat", check=False).strip():
        if props and state_path:  # a fix task that ends with no change at all
            engine.log("review", {"issues": ["no change"], "checks": []})
            return {"issues": ["no change"],
                    "text": "[experience review] You are finishing without any change to the project's files. "
                            "If the task asks for a fix, the code must change: reproduce the problem with a call "
                            "that shows it, then fix it."}
        return {"issues": [], "text": ""}
    cache = {}
    if cache_path and Path(cache_path).exists():
        cache = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    regs, cache = regressions(engine, ws, cache)
    if cache_path:
        Path(cache_path).write_text(json.dumps(cache), encoding="utf-8")
    issues = [f"A check from a past fix now fails (task {r['task_id']}, {', '.join(r['families'])}): "
              f"{r['output'].strip().splitlines()[-1] if r['output'].strip() else 'failed'}" for r in regs]
    issues += consistency_issues(ws)
    co, fired = co_change_issues(engine, ws)
    issues += co
    if tests_after_edit is False:
        issues.append("You edited files after the last test run: run the tests again.")
    if edges:
        for l in edge_checklist(engine, ws, state_path):
            issues.append("Run this edge-case check on the function you changed and fix it if it fails: "
                          + l["text"])
            fired.append(l["id"])
    found = []
    if checks:  # executable checks: findings observed on this change, not advice (probes.py)
        from .. import project
        from . import probes
        task = Path(f"{state_path}.task.txt").read_text(encoding="utf-8") if state_path and \
            Path(f"{state_path}.task.txt").exists() else None
        found = probes.run_all(ws, project.load(ws), props=props, task_text=task)
        if props:
            found += _self_check(state_path)
        issues += [probes.describe(f) for f in found]
    engine.fired("review", fired)
    engine.log("review", {"round": 1, "issues": issues, "checks": found})
    if state_path:
        Path(f"{state_path}.review.json").write_text(json.dumps({"round": 1, "checks": found}), encoding="utf-8")
    text = ""
    if issues and props:
        text = ("[experience review] Before you finish: these were found by checking and running your change:\n"
                + "\n".join(f"- {i}" for i in issues) + "\nFix them, then finish.")
    elif issues:
        text = ("[experience review] Before you finish, check these points from past work on this project:\n"
                + "\n".join(f"- {i}" for i in issues)
                + "\nFix what applies; if a point does not apply, say why in one line. Then finish.")
    return {"issues": issues, "text": text}
