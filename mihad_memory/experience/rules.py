"""Project rules learned from the project's own history (adopted only with evidence).

The generic contracts (contracts.py) know what any sequence or mapping must do; they cannot know what
THIS project promises. Its history can: every past fix restored something the project promises. For each
past fix a model writes a GENERAL rule of the project (an invariant every correct version must keep,
checked over many inputs and every class it applies to, not only the fixed example). A rule is adopted
only if it
    1. FAILS on the code before the fix   (it catches that bug),
    2. PASSES on the code after the fix,
    3. PASSES on the latest accepted code  (no false alarm on what the maintainers kept),
the same independence rule as the memory's adoption gate, applied to the project's promises.

At review time the adopted rules run on the agent's change: a rule that fails says, concretely, which
promise of the project the change breaks (or does not yet keep).
"""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .checkers import ask, prepare_tree
from .common import export_tree

RULE_PROMPT = """\
Below is a past bug fix in the Python project {project}. Write a standalone Python 3 script that checks a
GENERAL RULE of this project which the fix established or restored: a promise that every correct version
of the project must keep, wider than the exact example in the fix.
Requirements:
- the first line is a comment `# RULE: <the rule in one sentence>`;
- check the rule over many inputs (loops over several sizes and values; deterministic: use random.Random(0)
  if you need randomness) and over every public class or function of the project the rule applies to,
  not only the one the fix changed;
- import only the project's own modules (the repository root is on sys.path; source folders: {sources})
  and the standard library; do not import test files or third-party packages;
- it must FAIL (raise) on the code before the fix and PASS on the code after the fix;
- at most 70 lines; it must finish within 20 seconds; print "RULE OK" as the last line.
Reply with only the script in one ```python block.

FIX: {subject}
{body}

THE FIX (diff from before to after, library code and tests):
{diff}
"""


def run_rule(script, tree, python, timeout=60):
    env = {**os.environ, "PYTHONPATH": str(tree), "PYTHONHASHSEED": "0"}
    try:
        res = subprocess.run([python, str(script)], cwd=tree, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout, env=env)
        return res.returncode, (res.stdout + res.stderr)[-1200:]
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def statement(code):
    m = re.search(r"^#\s*RULE:\s*(.+)$", code, re.M)
    return m.group(1).strip() if m else ""


def _tree(repo, rev, dest):
    dest.mkdir(parents=True)
    export_tree(repo, rev, dest)
    prepare_tree(dest, repo)
    return dest


def validate(script, repo, parent, commit, latest, python):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        b = run_rule(script, _tree(repo, parent, tmp / "before"), python)
        a = run_rule(script, _tree(repo, commit, tmp / "after"), python)
        l = run_rule(script, _tree(repo, latest, tmp / "latest"), python) if b[0] != 0 and a[0] == 0 else (None, "")
    return {"before_exit": b[0], "after_exit": a[0], "latest_exit": l[0], "before_out": b[1][-400:],
            "after_out": a[1][-300:], "latest_out": l[1][-300:],
            "adopted": b[0] != 0 and a[0] == 0 and l[0] == 0}


def learn(history, latest, out_dir, python, project_name, sources, model="anthropic/claude-sonnet-5", attempts=2,
          log=print):
    """history: task dicts (task_id, parent, commit, subject, body, code_files, hidden_test_files) of past fixes.
    latest: the newest accepted commit the rules must pass on. Writes out_dir/rules.json and the scripts."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "rules.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    for t in history:
        tid = t["task_id"]
        if tid in index and index[tid].get("status") in ("adopted", "rejected"):
            continue
        files = list(t.get("code_files", [])) + list(t.get("hidden_test_files", []))
        diff = subprocess.run(["git", "diff", t["parent"], t["commit"], "--", *files], cwd=t["_repo"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace").stdout[:14000]
        rec = {"task_id": tid, "subject": t["subject"], "attempts": []}
        for k in range(attempts):
            prompt = RULE_PROMPT.format(project=project_name, sources=", ".join(sources), subject=t["subject"],
                                        body=t.get("body", ""), diff=diff)
            if k:
                prompt += ("\nYour previous script did not meet the requirements: " + rec["attempts"][-1]["why"] +
                           "\nWrite it again.")
            code = ask(model, prompt)
            if not code:
                rec["attempts"].append({"why": "no script"})
                continue
            script = out_dir / f"rule_{tid}.py"
            script.write_text(code, encoding="utf-8")
            v = validate(script, t["_repo"], t["parent"], t["commit"], latest, python)
            why = ("it passed on the code before the fix (it does not catch the bug)" if v["before_exit"] == 0 else
                   "it failed on the code after the fix: " + v["after_out"][-200:] if v["after_exit"] != 0 else
                   "it fails on the project's latest accepted code: " + v["latest_out"][-200:]
                   if v["latest_exit"] != 0 else "")
            rec["attempts"].append({**{k2: v[k2] for k2 in ("before_exit", "after_exit", "latest_exit")}, "why": why})
            if v["adopted"]:
                rec.update(status="adopted", path=script.name, rule=statement(code))
                break
        else:
            rec["status"] = "rejected"
        index[tid] = rec
        index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"{tid} {rec['status']} {rec.get('rule', '')[:100]}")
    return index


def adopted(rules_dir):
    p = Path(rules_dir) / "rules.json"
    if not p.exists():
        return {}
    return {k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items() if v.get("status") == "adopted"}


def check(rules_dir, tree, python):
    """Adopted rules that fail on `tree`: [{task_id, rule, output}]."""
    out = []
    for tid, r in adopted(rules_dir).items():
        code, text = run_rule(Path(rules_dir) / r["path"], tree, python)
        if code != 0:
            out.append({"task_id": tid, "rule": r.get("rule", ""), "output": text[-500:]})
    return out
