"""Idea 0 (foundation): record every finished session as an episode, automatically, outside the agent.

An episode keeps what happened (tool steps, errors and what resolved them, commands), what was
changed, and how it ended (hidden tests, tokens). Families are the library symbols the change
touched; for replay runs the reference commit is the user's final version of the work.
"""
from pathlib import Path

from .. import langs
from .common import (git, is_lib, normalize_command, normalize_error, parse_diff, read_json, read_jsonl,
                     symbols_in_ranges, tool_steps, session_usage)


def _model(session_path):
    for e in read_jsonl(session_path):
        if e.get("type") == "model_change":
            return e.get("model")
    return None


def _resolution(steps, i):
    """The next successful call of the same tool within three steps: what worked after the error."""
    for s in steps[i + 1:i + 4]:
        if s["tool"] == steps[i]["tool"] and not s["error"]:
            return s["args"]
    return None


def _between(steps, i):
    """What the agent did between an error and the call that resolved it (e.g. re-read the file before the edit)."""
    out = []
    for s in steps[i + 1:i + 4]:
        if s["tool"] == steps[i]["tool"] and not s["error"]:
            return out
        arg = s["args"].get("path") or s["args"].get("command") or s["args"].get("pattern") or ""
        out.append(f"{s['tool']} {str(arg)[:100]}".strip())
    return None


def summarize_steps(steps):
    commands, errors, reads = [], [], {}
    for i, s in enumerate(steps):
        if s["tool"] == "bash" and s["args"].get("command"):
            commands.append({"template": normalize_command(s["args"]["command"]),
                             "raw": s["args"]["command"][:400], "error": s["error"]})
        if s["tool"] == "read" and s["args"].get("path"):
            base = str(s["args"]["path"]).split("#")[0].split(":")[0]
            reads[base] = reads.get(base, 0) + 1
        if s["error"]:
            first = next((l.strip() for l in s["text"].splitlines() if l.strip()), "")
            errors.append({"tool": s["tool"], "signature": normalize_error(s["text"]), "message": first[:160],
                           "args": {k: str(v)[:200] for k, v in s["args"].items() if k != "i"},
                           "resolution": {k: str(v)[:200] for k, v in (_resolution(steps, i) or {}).items()
                                          if k != "i"} or None,
                           "between": _between(steps, i)})
    return commands, errors, reads


def episode_from_run(run_dir, arm, row, task, repo):
    out_dir = Path(run_dir) / arm / row["task_id"]
    session = out_dir / "agent.jsonl"
    steps = tool_steps(session)
    commands, errors, reads = summarize_steps(steps)
    agent_diff = (out_dir / "agent.diff").read_text(encoding="utf-8", errors="replace") \
        if (out_dir / "agent.diff").exists() else ""
    agent_files = parse_diff(agent_diff)
    ref_files = parse_diff(git(repo, "diff", task["parent"], task["commit"])) if repo else {}
    families = set(task.get("families") or ())
    if task.get("synthetic"):
        # Practice task: the "user's version" is the tree before the mutation; nothing else changed.
        ref_files = {task["mutation"]["file"]: {"new_ranges": []}}
    for path, v in ref_files.items():
        if is_lib(path, repo):
            src = git(repo, "show", f"{task['commit']}:{path}", check=False)
            families |= symbols_in_ranges(src, v["new_ranges"], langs.language_of(path))
    return {
        "source": f"{Path(run_dir).name}/{arm}", "task_id": row["task_id"], "subject": task.get("subject", ""),
        "body": task.get("body", ""), "parent": task.get("parent"), "commit": task.get("commit"),
        "hidden_test_ids": task.get("hidden_test_ids"), "model": _model(session),
        "hidden_pass": row.get("hidden_pass"), "tokens": session_usage(session) or None,
        "turns": row.get("turns"), "families": sorted(families),
        "agent_files": sorted(agent_files), "ref_files": sorted(ref_files),
        "commands": commands, "errors": errors, "reads": reads, "n_steps": len(steps),
    }


def ingest_run(engine, run_dir, arms=None, repo=None):
    """Append episodes for every finished (task, arm) row of a replay run; skips ones already ingested."""
    run_dir = Path(run_dir).resolve()
    meta = read_json(run_dir / "run_meta.json")
    spec = read_json(meta["tasks_file"])
    repo = repo or spec["repo"]
    tasks = {t["task_id"]: t for t in spec["tasks"]}
    have = {(e["source"], e["task_id"]) for e in engine.episodes()}
    added = []
    for row in read_jsonl(run_dir / "results.jsonl"):
        if arms and row["arm"] not in arms:
            continue
        key = (f"{run_dir.name}/{row['arm']}", row["task_id"])
        if key in have or row["task_id"] not in tasks:
            continue
        ep = episode_from_run(run_dir, row["arm"], row, tasks[row["task_id"]], repo)
        engine.add_episode(ep)
        added.append(ep)
        have.add(key)
    return added
