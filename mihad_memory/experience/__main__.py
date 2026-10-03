"""Command line for the experience engine.

    python -m mihad_memory.experience [--dir D] init --repo PATH
    python -m mihad_memory.experience ingest RUN [--arms a,b]
    python -m mihad_memory.experience mine [--no-verify]
    python -m mihad_memory.experience checkers [--model M] [--only id,id]
    python -m mihad_memory.experience dream-make [--n 6]
    python -m mihad_memory.experience dream-eval RUN [--with engine --without engine_ablate]
    python -m mihad_memory.experience brief --cwd WS --task-file F [--model M]
    python -m mihad_memory.experience detect --event-file F --state S
    python -m mihad_memory.experience review --cwd WS [--state S] [--cache C]
    python -m mihad_memory.experience skill list | run NAME --cwd WS [--arg k=v ...]
    python -m mihad_memory.experience status

The engine directory comes from --dir, else MIHAD_EXPERIENCE_DIR, else .mihad/experience.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from . import brief as brief_mod
from .. import project
from . import checkers, competence, corrections, cycle, dream, edges, failures, live, report, review as review_mod, skills
from .engine import Engine
from .episodes import ingest_run


def memory_for(cfg):
    from ..service import Memory
    m = Memory(cfg["root"], db_path=str(project.path_in(cfg, "memory_db")), policy="verified")
    m.user_log = str(project.path_in(cfg, "user_log"))
    return m


def memory_block(cwd, task_text):
    """Verified memory items for the brief, so they reach the agent even if it never calls memory_recall."""
    cfg = project.load(cwd)
    if not project.path_in(cfg, "memory_db").exists():
        return ""
    res = memory_for(cfg).recall(task_text, k=3, include_provisional=False)
    lines = [f"- [preference, stated by the user - follow it] {p['content']}" for p in res["preferences"]
             if p["status"] == "adopted"]
    lines += [f"- [{i['kind']}, verified] {i['title']}: {i['content'][:400]}" for i in res["items"]
              if i["status"] == "adopted"]
    return ("\nProject memory (verified only):\n" + "\n".join(lines)) if lines else ""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mihad_memory.experience")
    ap.add_argument("--dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--repo", required=True)
    p = sub.add_parser("ingest"); p.add_argument("run"); p.add_argument("--arms")
    p = sub.add_parser("mine"); p.add_argument("--no-verify", action="store_true")
    p = sub.add_parser("checkers"); p.add_argument("--model", default="anthropic/claude-sonnet-5"); p.add_argument("--only")
    p = sub.add_parser("dream-make"); p.add_argument("--n", type=int, default=6); p.add_argument("--seed", type=int, default=20261003)
    p = sub.add_parser("dream-eval"); p.add_argument("run"); p.add_argument("--with", dest="w", default="engine")
    p.add_argument("--without", default="engine_ablate")
    p = sub.add_parser("brief"); p.add_argument("--cwd", required=True); p.add_argument("--task-file", required=True)
    p.add_argument("--model")
    p.add_argument("--memory", action="store_true", help="also include verified items from the project memory")
    p = sub.add_parser("detect"); p.add_argument("--event-file", required=True); p.add_argument("--state", required=True)
    p.add_argument("--cwd")
    p = sub.add_parser("review"); p.add_argument("--cwd", required=True); p.add_argument("--state"); p.add_argument("--cache")
    p.add_argument("--edges", action="store_true", help="add the edge-case checklist for changed functions")
    p = sub.add_parser("skill"); p.add_argument("action", choices=["list", "run"]); p.add_argument("name", nargs="?")
    p.add_argument("--cwd", default="."); p.add_argument("--arg", action="append", default=[])
    sub.add_parser("status")
    p = sub.add_parser("live-prompt"); p.add_argument("--cwd", required=True); p.add_argument("--task-file", required=True)
    p.add_argument("--session-file", default=""); p.add_argument("--first", action="store_true")
    p = sub.add_parser("live-checkpoint"); p.add_argument("--cwd", required=True); p.add_argument("--session-file", default="")
    p.add_argument("--state")
    p = sub.add_parser("live-close"); p.add_argument("--cwd", required=True)
    sub.add_parser("live-ingest")
    p = sub.add_parser("dream-cycle"); p.add_argument("--tasks", type=int)
    p = sub.add_parser("report"); p.add_argument("--out")
    a = ap.parse_args(argv)
    eng = Engine(a.dir)
    sys.stdout.reconfigure(encoding="utf-8")

    if a.cmd == "init":
        eng.put("config", {"repo": str(Path(a.repo).resolve())})
        print("engine at", eng.root)
    elif a.cmd == "ingest":
        added = ingest_run(eng, a.run, a.arms.split(",") if a.arms else None, eng.get("config", {}).get("repo"))
        print(f"ingested {len(added)} episodes")
    elif a.cmd == "mine":
        print("co_change", len(corrections.mine(eng)))
        print("edge_case", len(edges.mine(eng)))
        print("failure", len(failures.mine(eng)))
        reg = skills.mine(eng, verify=not a.no_verify)
        print("skills", {k: v["status"] for k, v in reg["skills"].items()})
        comp = competence.mine(eng)
        print("families", {f: r["level"] for f, r in comp["families"].items()})
    elif a.cmd == "checkers":
        reg = checkers.generate(eng, eng.get("config")["repo"], a.model, only=a.only.split(",") if a.only else None)
        print("adopted", sum(1 for r in reg.values() if r["status"] == "adopted"), "of", len(reg))
    elif a.cmd == "dream-make":
        spec = dream.make_tasks(eng, eng.get("config")["repo"], a.n, a.seed)
        print(len(spec["tasks"]), "practice tasks ->", eng.root / "dream_tasks.json")
    elif a.cmd == "dream-eval":
        print(json.dumps(dream.evaluate(eng, a.run, a.w, a.without), indent=2))
    elif a.cmd == "brief":
        text = Path(a.task_file).read_text(encoding="utf-8")
        out = brief_mod.brief(eng, a.cwd, text, a.model)
        if a.memory:
            out += memory_block(a.cwd, text)
        print(out)
    elif a.cmd == "detect":
        event = json.loads(Path(a.event_file).read_text(encoding="utf-8"))
        print(failures.detect(eng, event, a.state, a.cwd), end="")
    elif a.cmd == "review":
        tests_ok = failures.tests_after_last_edit(a.state) if a.state else None
        print(review_mod.review(eng, a.cwd, tests_ok, a.cache, a.state, a.edges)["text"], end="")
    elif a.cmd == "skill":
        if a.action == "list":
            print(json.dumps(eng.get("skills", {}), indent=2))
        else:
            args = dict(x.split("=", 1) for x in a.arg)
            code, out = skills.run_skill(eng, a.name, a.cwd, args)
            print(f"exit={code}\n{out}")
    elif a.cmd == "report":
        text = report.render(eng)
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
            print("written", a.out)
        else:
            print(text)
    elif a.cmd == "live-prompt":
        cfg = project.load(a.cwd)
        text = Path(a.task_file).read_text(encoding="utf-8")
        log = project.path_in(cfg, "user_log")
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:  # the user's own words: evidence for preferences
            fh.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n")
        captured = memory_for(cfg).capture_preferences()  # right after the user's words are logged
        if captured:
            print(f"captured {len(captured)} standing preference(s)")
        if a.first:
            live.record(eng, "start", a.cwd, a.session_file or None, text)
            live.ingest(eng)
    elif a.cmd == "live-checkpoint":
        tests_ok = failures.tests_after_last_edit(a.state) if a.state and Path(a.state).exists() else None
        live.record(eng, "end", a.cwd, a.session_file or None, tests_ok=tests_ok)
    elif a.cmd == "live-close":
        print(cycle.session_closed(eng, project.load(a.cwd)), end="")
    elif a.cmd == "live-ingest":
        print(f"ingested {len(live.ingest(eng))} live episodes; {json.dumps(live.status(eng))}")
    elif a.cmd == "dream-cycle":
        cycle.run_cycle(eng, practice_tasks=a.tasks)
    elif a.cmd == "status":
        ls = eng.lessons()
        print(json.dumps({"episodes": len(eng.episodes()),
                          "lessons": {k: sum(1 for l in ls if l["kind"] == k) for k in {l["kind"] for l in ls}},
                          "lesson_status": {s: sum(1 for l in ls if l["status"] == s) for s in
                                            ("candidate", "adopted", "promoted", "retired")},
                          "checkers": len(checkers.adopted(eng)),
                          "skills": {k: v["status"] for k, v in (eng.get("skills", {}) or {}).get("skills", {}).items()},
                          "dream_tasks": len((eng.get("dream_tasks", {}) or {}).get("tasks", []))}, indent=2))


if __name__ == "__main__":
    main()
