"""Learning from ordinary sessions on a real project (no replay, no reference commit).

At the start of a session the extension records a snapshot of the working tree; at the end it
records another one (both are commit objects made with `git stash create`, so nothing in the
user's branch, index or stash list changes). Later, once the user has committed work after the
session, that commit is the user's final version: the episode compares what the agent left with
what the user kept, which is exactly the correction signal the engine learns from.
"""
import time
from pathlib import Path

from .. import langs
from .common import git, is_lib, parse_diff, read_jsonl, symbols_in_ranges, tool_steps, session_usage
from .episodes import summarize_steps

MIN_AGE_SECONDS = 60


def snapshot(cwd):
    """A commit id for the current working tree (HEAD itself if the tree is clean)."""
    sha = git(cwd, "stash", "create", check=False).strip()
    return sha or git(cwd, "rev-parse", "HEAD", check=False).strip()


def record(engine, phase, cwd, session_file=None, task_text=None, tests_ok=None):
    rec = {"phase": phase, "ts": time.time(), "cwd": str(cwd), "snapshot": snapshot(cwd),
           "head": git(cwd, "rev-parse", "HEAD", check=False).strip(), "session_file": session_file,
           "task": (task_text or "")[:4000], "tests_ok": tests_ok}
    engine.log("live_sessions", rec)
    return rec


def _pairs(engine):
    """(start, last end) records per session: an end is written after every agent turn, the last one wins."""
    starts, ends, order = {}, {}, []
    for r in read_jsonl(engine.root / "live_sessions.jsonl"):
        key = r.get("session_file") or r["cwd"]
        if r["phase"] == "start":
            k = (key, r["ts"])
            starts[key] = k
            order.append(k)
            ends[k] = None
            starts[k] = r
        elif r["phase"] == "end" and key in starts:
            ends[starts[key]] = r
    return [(starts[k], ends[k]) for k in order if ends[k]]


def _kept_fraction(agent_files, final_files):
    added = [l.strip() for v in agent_files.values() for l in v["added"] if l.strip()]
    if not added:
        return None
    final_added = {l.strip() for v in final_files.values() for l in v["added"]}
    return round(sum(1 for l in added if l in final_added) / len(added), 2)


def ingest(engine, force=False):
    """Turn finished sessions into episodes once the user has committed after them."""
    done = {e.get("live_key") for e in engine.episodes()}
    added = []
    for start, end in _pairs(engine):
        key = f"{start.get('session_file') or start['cwd']}@{start['ts']}"
        if key in done:
            continue
        cwd = end["cwd"]
        head_now = git(cwd, "rev-parse", "HEAD", check=False).strip()
        if not force and (head_now == end["head"] or time.time() - end["ts"] < MIN_AGE_SECONDS):
            continue  # the user has not committed since the session: no final version yet
        final = head_now
        agent_files = parse_diff(git(cwd, "diff", start["snapshot"], end["snapshot"], check=False))
        if not agent_files:
            continue
        final_files = parse_diff(git(cwd, "diff", start["snapshot"], final, check=False))
        correction = parse_diff(git(cwd, "diff", end["snapshot"], final, "--", *agent_files, check=False))
        families = set()
        for path, v in final_files.items():
            if is_lib(path, cwd):
                families |= symbols_in_ranges(git(cwd, "show", f"{final}:{path}", check=False), v["new_ranges"],
                                              langs.language_of(path))
        steps = tool_steps(end["session_file"]) if end.get("session_file") and Path(end["session_file"]).exists() else []
        commands, errors, reads = summarize_steps(steps)
        kept = _kept_fraction(agent_files, final_files)
        ep = {"source": "live", "live_key": key, "task_id": f"live-{int(start['ts'])}",
              "subject": start["task"].splitlines()[0][:200] if start["task"] else "(session)",
              "body": start["task"][:2000], "parent": start["snapshot"], "commit": final,
              "hidden_test_ids": None, "model": None,
              # Outcome without hidden tests: the agent's tests passed at the end and the user kept most of it.
              "hidden_pass": bool(end.get("tests_ok")) and (kept is None or kept >= 0.5),
              "kept_fraction": kept, "corrected_files": sorted(correction),
              "tokens": session_usage(end["session_file"]) if steps else None, "turns": None,
              "families": sorted(families), "agent_files": sorted(agent_files), "ref_files": sorted(final_files),
              "commands": commands, "errors": errors, "reads": reads, "n_steps": len(steps)}
        engine.add_episode(ep)
        added.append(ep)
    return added


def status(engine):
    pairs = _pairs(engine)
    eps = [e for e in engine.episodes() if e["source"] == "live"]
    return {"sessions_recorded": len(pairs), "episodes": len(eps),
            "waiting_for_user_commit": len(pairs) - len(eps)}

