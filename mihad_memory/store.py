"""SQLite store for records, evidence, lineage, outcomes and events."""
import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('skill', 'fact', 'lesson', 'preference')),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN
        ('provisional', 'adopted', 'rejected', 'suspended', 'superseded', 'deleted')),
    status_reason TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    supersedes TEXT,
    verify_spec TEXT NOT NULL DEFAULT '{}',
    shadow_decision TEXT,
    policy TEXT NOT NULL,
    session_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES records(id),
    kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    passed INTEGER NOT NULL,
    independent INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lineage (
    parent_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);
CREATE TABLE IF NOT EXISTS outcomes (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    policy TEXT NOT NULL,
    success INTEGER,
    used_ids TEXT NOT NULL DEFAULT '[]',
    helpful_ids TEXT NOT NULL DEFAULT '[]',
    harmful_ids TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session_id TEXT NOT NULL,
    policy TEXT NOT NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, db_path, log_path=None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path = Path(log_path) if log_path else self.db_path.with_name("events.jsonl")
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    # --- records -------------------------------------------------------
    def add_record(self, kind, title, content, tags, status, policy, session_id,
                   verify_spec=None, supersedes=None, version=1):
        rid = new_id(kind)
        now = time.time()
        self.db.execute(
            "INSERT INTO records (id, kind, title, content, tags, status, version, supersedes,"
            " verify_spec, policy, session_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, kind, title, content, tags, status, version, supersedes,
             json.dumps(verify_spec or {}, ensure_ascii=False), policy, session_id, now, now))
        self.db.commit()
        return rid

    def get(self, rid):
        row = self.db.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else None

    def set_status(self, rid, status, reason=""):
        self.db.execute("UPDATE records SET status=?, status_reason=?, updated_at=? WHERE id=?",
                        (status, reason, time.time(), rid))
        self.db.commit()

    def set_shadow(self, rid, decision):
        self.db.execute("UPDATE records SET shadow_decision=? WHERE id=?", (decision, rid))
        self.db.commit()

    def erase_content(self, rid):
        self.db.execute("UPDATE records SET content='', title='[deleted]', updated_at=? WHERE id=?",
                        (time.time(), rid))
        self.db.commit()

    def bump_use(self, ids):
        for rid in ids:
            self.db.execute("UPDATE records SET use_count = use_count + 1 WHERE id=?", (rid,))
        self.db.commit()

    def records(self, statuses=None):
        if statuses:
            marks = ",".join("?" * len(statuses))
            rows = self.db.execute(f"SELECT * FROM records WHERE status IN ({marks})", list(statuses))
        else:
            rows = self.db.execute("SELECT * FROM records ORDER BY created_at")
        return [dict(r) for r in rows]

    # --- evidence ------------------------------------------------------
    def add_evidence(self, rid, kind, source_id, passed, independent, detail):
        self.db.execute(
            "INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)",
            (new_id("ev"), rid, kind, source_id, int(passed), int(independent),
             json.dumps(detail, ensure_ascii=False), time.time()))
        self.db.commit()

    def evidence(self, rid):
        rows = self.db.execute("SELECT * FROM evidence WHERE record_id=? ORDER BY created_at", (rid,))
        return [dict(r) for r in rows]

    # --- lineage -------------------------------------------------------
    def link(self, parent_id, child_id):
        self.db.execute("INSERT OR IGNORE INTO lineage VALUES (?,?)", (parent_id, child_id))
        self.db.commit()

    def descendants(self, rid):
        seen, frontier = set(), [rid]
        while frontier:
            cur = frontier.pop()
            for (child,) in self.db.execute("SELECT child_id FROM lineage WHERE parent_id=?", (cur,)):
                if child not in seen:
                    seen.add(child)
                    frontier.append(child)
        return seen

    # --- outcomes and events --------------------------------------------
    def add_outcome(self, task_id, session_id, policy, success, used, helpful, harmful, notes):
        oid = new_id("out")
        self.db.execute(
            "INSERT INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (oid, task_id, session_id, policy, None if success is None else int(success),
             json.dumps(used), json.dumps(helpful), json.dumps(harmful), notes, time.time()))
        self.db.commit()
        return oid

    def log(self, session_id, policy, event, payload):
        ts = time.time()
        text = json.dumps(payload, ensure_ascii=False, default=str)
        self.db.execute("INSERT INTO events (ts, session_id, policy, event, payload) VALUES (?,?,?,?,?)",
                        (ts, session_id, policy, event, text))
        self.db.commit()
        line = json.dumps({"ts": ts, "session_id": session_id, "policy": policy,
                           "event": event, "payload": payload}, ensure_ascii=False, default=str)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
