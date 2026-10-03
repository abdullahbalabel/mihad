"""Idea 5: dreaming. Practice on new problems made from past fixes, and test lessons causally.

make_tasks: take the user's final version of a past fix, break the fixed function with a small
mutation, and keep the mutant only if both the adopted checker and the real tests catch it. Each
mutant becomes a practice task the replay runner can execute.
evaluate: practice runs come in pairs, the engine with its lessons and the engine with the lessons
switched off. A lesson is promoted only if the sessions where it fired did at least as well and
cost clearly less, and retired if they did worse. This is an A/B test, not a vote.
"""
import ast
import difflib
import random
import re
import subprocess
import tempfile
from pathlib import Path

from .checkers import adopted, prepare_tree, run_checker, target_of
from .skills import _test_cmd, test_classes_for
from .. import langs, project
from .common import export_tree, read_jsonl, top_level_defs

MUTATIONS = [
    (r" < ", " <= "), (r" <= ", " < "), (r" > ", " >= "), (r" >= ", " > "),
    (r" == ", " != "), (r" != ", " == "), (r" and ", " or "), (r" or ", " and "),
    (r"\bif not ", "if "), (r"\+ 1\b", "- 1"), (r"- 1\b", "+ 1"),
    (r"\breturn True\b", "return False"), (r"\breturn False\b", "return True"),
    (r"\bis None\b", "is not None"), (r"\bis not None\b", "is None"),
    # wider set for general projects (conditional expressions, indexing, arithmetic)
    (r" if (\w+) else ", r" if not \1 else "), (r"\[0\]", "[1]"), (r"\[-1\]", "[0]"),
    (r" \* ", " / "), (r" / ", " * "), (r" - ", " + "), (r" \+ ", " - "),
]
PROMOTE_GAIN = 0.10
RETIRE_LOSS = 0.25
MIN_PAIRS = 2


PYTHON_ONLY = {r" and ", r" or ", r"\bif not ", r"\breturn True\b", r"\breturn False\b", r"\bis None\b",
               r"\bis not None\b", r" if (\w+) else "}
C_FAMILY_MUTATIONS = [
    (r" && ", " || "), (r" \|\| ", " && "), (r" === ", " !== "), (r" !== ", " === "),
    (r"\bif \(!", "if ("), (r"\btrue\b", "false"), (r"\bfalse\b", "true"),
    (r"!= null\b", "== null"), (r"== null\b", "!= null"), (r"!= nil\b", "== nil"), (r"== nil\b", "!= nil"),
    (r"\.length - 1\b", ".length"), (r"\blen\((\w+)\) - 1\b", r"len(\1)"),
]


def _candidates(source, name, rng, lang=None):
    """(line index, pattern, replacement) inside the body of a def, its signature and comments excluded
    (and for Python the docstring)."""
    if lang not in (None, "python"):
        rng_def = langs.defs(source, lang).get(name)
        if not rng_def:
            return []
        lines = source.splitlines(keepends=True)
        prefixes = langs.comment_prefixes(lang)
        ops = [m for m in MUTATIONS if m[0] not in PYTHON_ONLY] + C_FAMILY_MUTATIONS
        out = []
        for ln in range(rng_def[0] + 1, rng_def[1]):  # skip the declaration line and the closing line
            s = lines[ln - 1].strip()
            if not s or s.startswith(prefixes):
                continue
            for pat, rep in ops:
                if re.search(pat, lines[ln - 1]):
                    out.append((ln - 1, pat, rep))
        rng.shuffle(out)
        return out
    node = next((n for n in ast.parse(source).body
                 if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name), None)
    if node is None:
        return []
    skip = set(range(node.lineno, node.body[0].lineno))
    first = node.body[0]
    if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant)             and isinstance(first.value.value, str):
        skip |= set(range(first.lineno, first.end_lineno + 1))
    lines = source.splitlines(keepends=True)
    out = []
    for ln in range(node.lineno, node.end_lineno + 1):
        s = lines[ln - 1].strip()
        if ln in skip or not s or s.startswith("#"):
            continue
        for pat, rep in MUTATIONS:
            if re.search(pat, lines[ln - 1]):
                out.append((ln - 1, pat, rep))
    rng.shuffle(out)
    return out


def _mutate(source, i, pat, rep):
    lines = source.splitlines(keepends=True)
    lines[i] = re.sub(pat, rep, lines[i], count=1)
    return "".join(lines)


def _tests_fail(tree, ids):
    """True if the real tests fail on the mutant; None if they hang (a hang is a poor practice task).
    With no test ids (no test touches the function), the checker alone has to catch the mutant."""
    if not ids:
        return True
    try:
        res = subprocess.run(_test_cmd(tree, ids), cwd=tree, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        return None
    return res.returncode != 0


def test_files_of(tree, ids):
    """Test files behind test ids: file paths, pytest/go node ids, unittest dotted ids, or class names."""
    out = set()
    for x in ids or []:
        head = x.split("::")[0]
        if (Path(tree) / head).is_file():
            out.add(head)
            continue
        if "::" in x or x.endswith(".py"):
            out.add(head)
            continue
        if "." not in x:  # a test class name (JUnit, NUnit...): find its file
            hit = next((p for p in Path(tree).rglob(f"{x}.*") if p.is_file()), None)
            if hit:
                out.add(hit.relative_to(tree).as_posix())
            continue
        parts = x.split(".")
        for k in range(len(parts), 0, -1):
            cand = "/".join(parts[:k]) + ".py"
            if (Path(tree) / cand).exists():
                out.add(cand)
                break
    return sorted(out)


def make_tasks(engine, repo, n=6, seed=20261003, tries_per_task=12):
    rng = random.Random(seed)
    eps = {e["task_id"]: e for e in engine.episodes()}
    tasks = []
    for tid, chk in sorted(adopted(engine).items()):
        ep = eps.get(tid)
        if not ep or not ep["families"] or len(tasks) >= n:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "t"
            tree.mkdir()
            export_tree(repo, ep["commit"], tree)
            prepare_tree(tree, repo)
            target = target_of(engine, chk)
            made = None
            for fam in ep["families"]:
                for mod in project.source_files(tree, project.load(repo)):
                    src = mod.read_text(encoding="utf-8")
                    lang = langs.language_of(mod)
                    if fam not in top_level_defs(src, lang):
                        continue
                    for i, pat, rep in _candidates(src, fam, rng, lang)[:tries_per_task]:
                        mutated = _mutate(src, i, pat, rep)
                        if lang in (None, "python"):
                            try:
                                compile(mutated, str(mod), "exec")
                            except SyntaxError:
                                continue
                        mod.write_text(mutated, encoding="utf-8")
                        code, out = run_checker(target, tree)
                        ids = ep.get("hidden_test_ids") or test_classes_for(tree, fam)
                        caught = code not in (0, 124) and _tests_fail(tree, ids) is True
                        mod.write_text(src, encoding="utf-8")
                        if not caught:
                            continue
                        rel = mod.relative_to(tree).as_posix()
                        patch = "".join(difflib.unified_diff(src.splitlines(keepends=True),
                                                             mutated.splitlines(keepends=True),
                                                             f"a/{rel}", f"b/{rel}"))
                        report = re.sub(r"[A-Za-z]:\\[^\s\"']+", "<check>", out.strip())[-700:]
                        made = {"task_id": f"dream-{tid}-{len(tasks)}", "synthetic": True,
                                "parent": ep["commit"], "commit": ep["commit"], "base_patch": patch,
                                "subject": f"Bug report: {fam} returns wrong results",
                                "body": "A user's check of this function fails on the current code:\n"
                                        f"{report}\nFind and fix the bug in the project code.",
                                "hidden_test_ids": ids, "hidden_test_files": test_files_of(tree, ids),
                                "checker": target if isinstance(target, dict) else str(target), "families": [fam],
                                "mutation": {"file": rel, "line": i + 1, "from": pat, "to": rep}}
                        break
                    if made:
                        break
                if made:
                    break
        if made:
            tasks.append(made)
            print("mutant", made["task_id"], made["mutation"], flush=True)
    spec = {"repo": repo, "tasks": tasks, "rejected": []}
    engine.put("dream_tasks", spec)
    return spec


def _tokens(row):
    u = row.get("usage") or {}
    return sum(u.get(k, 0) or 0 for k in ("input", "output", "cacheRead", "cacheWrite"))


def evaluate(engine, run_dir, with_arm="engine", without_arm="engine_ablate"):
    run_dir = Path(run_dir).resolve()
    rows = {}
    for r in read_jsonl(run_dir / "results.jsonl"):
        rows[(r["task_id"], r["arm"])] = r
    fired = {}
    # The runner gives each run its own copy of the engine; the fired log of that copy is the record.
    log = run_dir / "experience" / "fired.jsonl"
    for rec in read_jsonl(log if log.exists() else engine.root / "fired.jsonl"):
        fired.setdefault(rec["tag"], set()).update(rec["ids"])
    lessons = engine.lessons()
    by_id = {l["id"]: l for l in lessons}
    pairs = []
    for (tid, arm), w in rows.items():
        if arm != with_arm or (tid, without_arm) not in rows:
            continue
        wo = rows[(tid, without_arm)]
        pair = {"run": run_dir.name, "task_id": tid, "pass_with": bool(w["hidden_pass"]),
                "pass_without": bool(wo["hidden_pass"]), "tokens_with": _tokens(w), "tokens_without": _tokens(wo)}
        pairs.append(pair)
        for lid in fired.get(f"{run_dir.name}/{with_arm}/{tid}", ()):
            l = by_id.get(lid)
            if l is None:
                continue
            l.setdefault("ab", {"pairs": []})
            l["ab"]["pairs"] = [p for p in l["ab"]["pairs"] if (p["run"], p["task_id"]) != (run_dir.name, tid)]
            l["ab"]["pairs"].append(pair)
    for l in lessons:
        ps = (l.get("ab") or {}).get("pairs", [])
        if len(ps) < MIN_PAIRS or l["status"] not in ("adopted", "promoted"):
            continue
        pass_diff = sum(p["pass_with"] - p["pass_without"] for p in ps)
        gain = sum((p["tokens_without"] - p["tokens_with"]) / p["tokens_without"] for p in ps
                   if p["tokens_without"]) / len(ps)
        l["ab"]["pass_diff"], l["ab"]["token_gain"] = pass_diff, round(gain, 3)
        if pass_diff < 0 or gain <= -RETIRE_LOSS:
            l["status"] = "retired"
        elif pass_diff > 0 or gain >= PROMOTE_GAIN:
            l["status"] = "promoted"
    engine.save_lessons(lessons)
    summary = {"run": run_dir.name, "pairs": pairs,
               "statuses": {s: sum(1 for l in lessons if l["status"] == s) for s in
                            ("candidate", "adopted", "promoted", "retired")}}
    engine.log("dream_log", summary)
    return summary
