"""The automatic learning cycle: learn from finished sessions, then dream.

    1. ingest finished live sessions the user has since committed after (live.py)
    2. mine lessons, edge cases, skills and competence from all episodes
    3. write verifier scripts for new episodes (adopted only if they fail before and pass after)
    4. make practice tasks by mutating past fixes, run them with and without lessons (practice.py)
    5. promote or retire lessons by that A/B result, ingest the practice sessions, mine again

One cycle at a time (a lock file); the extension starts it in the background every N sessions when
the project config asks for it (dream.auto_after_sessions), or the user runs it by hand.
"""
import json
import os
import subprocess
import sys
import time

from .. import project
from . import checkers, competence, corrections, dream, edges, failures, live, practice, skills
from .episodes import ingest_run

LOCK_STALE = 6 * 3600


def mine_all(engine, verify=True):
    return {"co_change": len(corrections.mine(engine)), "edge_case": len(edges.mine(engine)),
            "failure": len(failures.mine(engine)),
            "skills": {k: v["status"] for k, v in skills.mine(engine, verify=verify)["skills"].items()},
            "families": len(competence.mine(engine)["families"])}


def _lock(engine):
    lock = engine.root / "dream.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < LOCK_STALE:
        return None
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return lock


def run_cycle(engine, cfg=None, practice_tasks=None, checker_limit=5, log=print):
    cfg = cfg or project.load(engine.get("config", {}).get("repo"))
    repo = engine.get("config", {}).get("repo") or cfg["root"]
    lock = _lock(engine)
    if not lock:
        log("another cycle is running; skipped")
        return None
    try:
        summary = {"started": time.time()}
        summary["ingested_live"] = len(live.ingest(engine))
        summary["mined"] = mine_all(engine)
        have = set((engine.get("checkers", {}) or {}))
        new = [e["task_id"] for e in engine.episodes()
               if e["task_id"] not in have and e.get("families") and not e["task_id"].startswith("dream-")]
        if new:
            reg = checkers.generate(engine, repo, only=new[:checker_limit])
            summary["checkers_adopted"] = sum(1 for r in reg.values() if r.get("status") == "adopted")
        n = practice_tasks if practice_tasks is not None else cfg["dream"]["tasks_per_cycle"]
        cycle_name = f"cycle-{time.strftime('%Y%m%d-%H%M%S')}"
        spec = dream.make_tasks(engine, repo, n=n, seed=int(time.time()) % 100000)
        summary["practice_tasks"] = len(spec["tasks"])
        if spec["tasks"]:
            run_dir = engine.root / "dreams" / cycle_name
            practice.run(engine, run_dir, cfg=cfg)
            summary["evaluation"] = dream.evaluate(engine, run_dir)["statuses"]
            summary["ingested_practice"] = len(ingest_run(engine, run_dir, repo=repo))
            summary["mined_after"] = mine_all(engine)
        summary["finished"] = time.time()
        engine.log("cycles", summary)
        log(json.dumps(summary, indent=2))
        return summary
    finally:
        lock.unlink(missing_ok=True)


def spawn(engine, cfg):
    """Start a cycle in its own background process and return at once."""
    log = engine.root / "cycle.log"
    cmd = [sys.executable, "-m", "mihad_memory.experience", "--dir", str(engine.root), "dream-cycle"]
    flags = 0
    if os.name == "nt":
        # The cycle itself runs in the background; its practice sessions open their own visible
        # windows when dream.visible is set.
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with open(log, "a", encoding="utf-8") as fh:
        subprocess.Popen(cmd, cwd=str(project.MIHAD_ROOT), stdout=fh, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=flags,
                         env=dict(os.environ, MIHAD_PROJECT=cfg["root"]))
    return str(log)


def session_closed(engine, cfg):
    """Called by the extension when a live session closes: count it, maybe start a dream cycle."""
    counter = engine.get("sessions", {"count": 0}) or {"count": 0}
    counter["count"] += 1
    engine.put("sessions", counter)
    every = int(cfg["dream"].get("auto_after_sessions") or 0)
    if every and counter["count"] % every == 0:
        return f"dream cycle started: {spawn(engine, cfg)}"
    return ""
