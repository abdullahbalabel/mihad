"""The experience store: file-based, inspectable, one directory per project.

    episodes.jsonl      every finished session (episodes.py)
    corrections.jsonl   what the user's final version changed relative to the agent (corrections.py)
    lessons.json        adopted experience: co-change rules, failure paths (with status and A/B record)
    skills.json         compiled, verified skill scripts (skills.py)
    competence.json     per-family record that sets how much scaffolding to give (competence.py)
    checkers.json       verifier scripts made from past fixes (checkers.py), files in checkers/
    dream_tasks.json    practice tasks made by mutating past fixes (dream.py)
    fired.jsonl         which lesson fired in which session (for causal credit)
"""
import hashlib
import json
import os
import time
from pathlib import Path

from .common import read_json, read_jsonl, write_json

STATUSES = ("candidate", "adopted", "promoted", "retired")


def lesson_id(kind, key):
    return kind[:2] + "-" + hashlib.sha1(f"{kind}|{key}".encode()).hexdigest()[:8]


class Engine:
    def __init__(self, root=None):
        if not (root or os.environ.get("MIHAD_EXPERIENCE_DIR")):
            # Inside an installed project: its configured engine directory, found from any subfolder.
            from .. import project
            cfg = project.load()
            root = project.path_in(cfg, "experience_dir")
        self.root = Path(root or os.environ.get("MIHAD_EXPERIENCE_DIR")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # episodes
    def episodes(self):
        return read_jsonl(self.root / "episodes.jsonl")

    def add_episode(self, ep):
        with open(self.root / "episodes.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ep, ensure_ascii=False) + "\n")

    # lessons
    def lessons(self):
        return read_json(self.root / "lessons.json", [])

    def save_lessons(self, lessons):
        write_json(self.root / "lessons.json", lessons)

    def merge_lessons(self, kind, fresh):
        """Replace lessons of one kind with freshly mined ones, keeping status and A/B record by id."""
        old = {l["id"]: l for l in self.lessons()}
        keep = [l for l in old.values() if l["kind"] != kind]
        for l in fresh:
            prev = old.get(l["id"])
            if prev:
                l["ab"] = prev.get("ab", l.get("ab"))
                if prev["status"] in ("promoted", "retired"):
                    l["status"] = prev["status"]
            keep.append(l)
        self.save_lessons(keep)
        return fresh

    def active_lessons(self, exclude=()):
        excluded = set(exclude) | set(filter(None, os.environ.get("MIHAD_EXPERIENCE_EXCLUDE", "").split(",")))
        return [l for l in self.lessons() if l["status"] in ("adopted", "promoted") and l["id"] not in excluded
                and not ("*" in excluded)]

    # other stores
    def get(self, name, default=None):
        return read_json(self.root / f"{name}.json", default)

    def put(self, name, data):
        write_json(self.root / f"{name}.json", data)

    def log(self, name, rec):
        rec = {"ts": time.time(), "tag": os.environ.get("MIHAD_EXPERIENCE_TAG", ""), **rec}
        with open(self.root / f"{name}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def fired(self, where, ids):
        if ids:
            self.log("fired", {"where": where, "ids": sorted(set(ids))})
