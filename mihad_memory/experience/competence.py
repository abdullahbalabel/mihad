"""Idea 7: a competence record per family that decides how much scaffolding to give.

A family is a library function the user's final version changed. The record counts attempts,
successes and effort. Scaffolding fades as competence grows:
    novice     no successful past fix: full brief (warnings, rules, skills, review reminder)
    competent  one successful past fix: the past fix, the rules for its files, the skills
    mastered   two or more successful fixes with effort at or below the median: one line only
Tool handling is tracked separately per model, because it is the model, not the project, that
keeps tripping over the same tool mistakes.
"""
from collections import defaultdict
from statistics import median


def level(rec, effort_median):
    if rec["passes"] >= 2 and rec["best_tokens"] and effort_median and rec["best_tokens"] <= effort_median:
        return "mastered"
    if rec["passes"] >= 1:
        return "competent"
    return "novice"


def mine(engine):
    eps = engine.episodes()
    fam = defaultdict(lambda: {"attempts": 0, "passes": 0, "tokens": [], "tasks": []})
    tooling = defaultdict(lambda: {"sessions": 0, "errors": 0, "steps": 0})
    for e in eps:
        for f in e["families"]:
            r = fam[f]
            r["attempts"] += 1
            r["passes"] += 1 if e["hidden_pass"] else 0
            if e["tokens"]:
                r["tokens"].append(e["tokens"])
            if e["task_id"] not in r["tasks"]:
                r["tasks"].append(e["task_id"])
        t = tooling[e.get("model") or "unknown"]
        t["sessions"] += 1
        t["errors"] += len(e["errors"])
        t["steps"] += e["n_steps"]
    all_tokens = [x for r in fam.values() for x in r["tokens"]]
    med = median(all_tokens) if all_tokens else None
    out = {"effort_median": med, "families": {}, "tooling": {}}
    for f, r in fam.items():
        rec = {"attempts": r["attempts"], "passes": r["passes"], "tasks": r["tasks"],
               "best_tokens": min(r["tokens"]) if r["tokens"] else None}
        rec["level"] = level(rec, med)
        out["families"][f] = rec
    for m, t in tooling.items():
        rate = t["errors"] / t["steps"] if t["steps"] else None
        out["tooling"][m] = {**t, "error_rate": round(rate, 3) if rate is not None else None,
                             "level": "competent" if t["sessions"] >= 3 and rate is not None and rate < 0.05
                             else "novice"}
    engine.put("competence", out)
    return out


def family_level(engine, family):
    return (engine.get("competence", {}) or {}).get("families", {}).get(family, {}).get("level", "novice")


def tooling_level(engine, model):
    return (engine.get("competence", {}) or {}).get("tooling", {}).get(model or "", {}).get("level", "novice")
