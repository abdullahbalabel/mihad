"""The session brief: what experience says about this task, sized by competence (idea 7).

Relevance is strict: past fixes and rules are included only for library functions the task names;
tool-failure warnings only for the model's own weak spots. With nothing relevant, the brief is short.
"""
import os
from pathlib import Path

from .. import langs, project
from .common import library_symbols, mentioned_symbols, top_level_defs
from .competence import family_level, tooling_level
from .edges import applicable

MAX_FAILURE_TIPS = 3
MAX_PAST = 2


def _module_of(ws, symbol):
    for p in project.source_files(ws, project.load(ws)):
        if symbol in top_level_defs(p.read_text(encoding="utf-8", errors="replace"), langs.language_of(p)):
            return p.relative_to(Path(ws)).as_posix()
    return None


def symbol_source(ws, symbol, limit=1500):
    for p in project.source_files(ws, project.load(ws)):
        src = p.read_text(encoding="utf-8", errors="replace")
        rng = top_level_defs(src, langs.language_of(p)).get(symbol)
        if rng:
            return "\n".join(src.splitlines()[rng[0] - 1:rng[1]])[:limit]
    return ""


def brief(engine, ws, task_text, model=None):
    fams = sorted(mentioned_symbols(task_text, library_symbols(ws)))
    lessons = engine.active_lessons()
    eps = engine.episodes()
    skills = {k: v for k, v in (engine.get("skills", {}) or {}).get("skills", {}).items()
              if v["status"] == "adopted"}
    levels = {f: family_level(engine, f) for f in fams}
    parts, fired = [], []
    # Past fixes of the same functions.
    for f in fams:
        past = [e for e in eps if f in e["families"] and e["hidden_pass"]]
        seen, lines = set(), []
        for e in past:
            if e["task_id"] in seen:
                continue
            seen.add(e["task_id"])
            files = ", ".join(p for p in e["ref_files"])
            lines.append(f"  - \"{e['subject']}\": the final version changed {files}.")
        if lines and levels[f] == "mastered":
            parts.append(f"You have fixed {f} before ({len(seen)} times); same files and tests apply.")
        elif lines:
            parts.append(f"Past fixes of {f} on this project:\n" + "\n".join(lines[:MAX_PAST]))
    # The user's co-change rules for the files these functions live in.
    if any(levels[f] != "mastered" for f in fams):
        mods = {_module_of(ws, f) for f in fams} - {None}
        for l in lessons:
            if l["kind"] == "co_change" and set(l["trigger"]["files"]) & mods:
                parts.append(l["text"])
                fired.append(l["id"])
    # Tool failures this model is prone to.
    if tooling_level(engine, model) == "novice":
        tips = sorted((l for l in lessons if l["kind"] == "failure"),
                      key=lambda l: (l["status"] != "promoted", -len(l["support"])))[:MAX_FAILURE_TIPS]
        for l in tips:
            parts.append(l["text"])
            fired.append(l["id"])
    # Edge cases that past fixes in this project were about, for the functions this task names.
    if fams and os.environ.get("MIHAD_EXPERIENCE_EDGES"):
        edges = applicable(engine, [symbol_source(ws, f) for f in fams])
        if edges:
            parts.append("Edge cases to check on the function before you finish (spec-free invariants from past "
                         "fixes):\n" + "\n".join(f"    * {l['text']}" for l in edges))
            fired += [l["id"] for l in edges]
    if skills:
        parts.append("Verified skills (tool experience_skill): " +
                     "; ".join(f"{k}: {v['description']}" for k, v in skills.items()))
    parts.append("Before you finish, call experience_review to check your change against past work.")
    engine.fired("brief", fired)
    engine.log("brief", {"families": fams, "levels": levels, "lessons": fired})
    return "Experience from earlier work on this project:\n" + "\n".join(f"- {p}" if not p.startswith("Past")
                                                                         else p for p in parts)
