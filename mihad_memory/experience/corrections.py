"""Idea 3: learn from the user's corrections.

The user's final version of the work (in replay: the commit the maintainers made) is compared with
what the agent produced. What the user had to add or undo is the most trustworthy lesson source we
have, because the user wrote it. Rules are generalized only across independent tasks.
"""
import json
from collections import defaultdict
from itertools import permutations

from .common import is_test
from .engine import lesson_id

MIN_TASKS = 2
MIN_CONFIDENCE = 0.6


def episode_corrections(ep):
    agent, ref = set(ep["agent_files"]), set(ep["ref_files"])
    return {"task_id": ep["task_id"], "source": ep["source"],
            "missed": sorted(p for p in ref - agent if not is_test(p)),
            "extra": sorted(p for p in agent - ref if not is_test(p)),
            "hidden_pass": ep["hidden_pass"]}


def mine(engine):
    eps = engine.episodes()
    corrections = [episode_corrections(e) for e in eps]
    (engine.root / "corrections.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in corrections), encoding="utf-8")
    # Co-change rules from the user's final versions, one vote per task (not per session).
    ref_by_task = {}
    for e in eps:
        ref_by_task[e["task_id"]] = {p for p in e["ref_files"] if not is_test(p)}
    has, both = defaultdict(set), defaultdict(set)
    for tid, files in ref_by_task.items():
        for a in files:
            has[a].add(tid)
        for a, b in permutations(files, 2):
            both[(a, b)].add(tid)
    missed = defaultdict(set)
    for c, e in zip(corrections, eps):
        for b in c["missed"]:
            for a in set(e["agent_files"]) & set(e["ref_files"]):
                missed[(a, b)].add(e["task_id"])
    lessons = []
    for (a, b), tids in both.items():
        conf = len(tids) / len(has[a])
        if len(tids) < MIN_TASKS or conf < MIN_CONFIDENCE:
            continue
        miss = len(missed[(a, b)])
        text = (f"When you change {a}, also update {b}: the user's final version changed both in "
                f"{len(tids)} of {len(has[a])} past fixes")
        text += f", and earlier sessions missed {b} {miss} time(s)." if miss else "."
        lessons.append({"id": lesson_id("co_change", f"{a}=>{b}"), "kind": "co_change",
                        "trigger": {"files": [a]}, "partner": b, "text": text,
                        "support": sorted(tids), "missed": sorted(missed[(a, b)]), "confidence": round(conf, 2),
                        "status": "adopted", "ab": {"pairs": []}})
    return engine.merge_lessons("co_change", lessons)
