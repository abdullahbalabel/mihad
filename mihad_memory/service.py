"""Memory service: adoption gate, policies, recall, correction and deletion.

Policies (MIHAD_POLICY):
  verified   - candidates are adopted only after independent verification (the gate).
  store_all  - candidates are adopted immediately; the gate runs in shadow for analysis.
  off        - nothing is stored or recalled; calls are only logged (no-memory baseline).
"""
import json
import os
import re
import shlex
import time
import uuid
from pathlib import Path

from . import verify
from .store import Store

POLICIES = ("verified", "store_all", "off")
KINDS = ("skill", "fact", "lesson", "preference")
DEFAULT_COMMANDS = ("python -m pytest", "pytest", "python -m unittest")
RECALLABLE = ("adopted", "provisional")


class MemoryError(Exception):
    pass


def tokenize(text):
    return [t for t in re.findall(r"[\w؀-ۿ]+", (text or "").lower()) if len(t) > 1]


DUPLICATE_JACCARD = 0.6
# Recall relevance gate (v0.3), calibrated on pilot-1 memory; override via environment.
RECALL_K = int(os.environ.get("MIHAD_RECALL_K", 3))
RECALL_RELATIVE = float(os.environ.get("MIHAD_RECALL_RELATIVE", 0.45))
RECALL_DISTINCTIVE = float(os.environ.get("MIHAD_RECALL_DISTINCTIVE", 0.25))
USER_ORIGIN_TAG = "origin:user"
ONE_OFF_SCOPE = re.compile(r"\b(this task only|for this task|only for this|just this time|this time only)\b", re.I)
STANDING_LINE = re.compile(r"^\s*(?:\d+[.)]\s*)?(always|never|from now on|in (all )?future)\b", re.I)
STANDING_HEADER = re.compile(r"(standing|future sessions|from now on|preferences? for how)", re.I)


def similar(a, b):
    """Token Jaccard similarity of two texts (used to collapse paraphrased duplicates)."""
    x, y = set(tokenize(a)), set(tokenize(b))
    return len(x & y) / len(x | y) if x and y else 0.0


def standing_preference_lines(text):
    """Lines of a user message that state a standing preference.

    A line qualifies when it starts with Always/Never/From now on, or is a numbered item
    under a header that announces standing/future preferences. Lines with a one-off scope
    ("for this task only") never qualify. Deterministic and auditable by design."""
    out, under_header = [], False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            under_header = False
            continue
        if STANDING_HEADER.search(line) and line.endswith(":"):
            under_header = True
            continue
        if ONE_OFF_SCOPE.search(line):
            continue
        numbered = re.match(r"^\d+[.)]\s+", line)
        if STANDING_LINE.match(line) or (under_header and numbered):
            out.append(line)
            continue
        # In ordinary chat a standing preference is often one sentence inside a longer line
        # ("Fix X. Always add a regression test."): take that sentence alone, verbatim.
        for sentence in re.split(r"(?<=[.!?])\s+", line)[1:]:
            if STANDING_LINE.match(sentence) and not ONE_OFF_SCOPE.search(sentence):
                out.append(sentence)
    return out


class Memory:
    def __init__(self, project, db_path=None, policy=None, min_evidence=None, timeout=None,
                 allowed_commands=None, session_id=None):
        self.project = str(Path(project).resolve())
        self.policy = policy or os.environ.get("MIHAD_POLICY", "verified")
        if self.policy not in POLICIES:
            raise MemoryError(f"unknown policy {self.policy!r}; expected one of {POLICIES}")
        db_path = db_path or os.environ.get("MIHAD_DB") or Path(self.project) / ".mihad" / "memory.db"
        self.store = Store(db_path)
        self.min_evidence = int(min_evidence or os.environ.get("MIHAD_MIN_EVIDENCE", 1))
        self.timeout = int(timeout or os.environ.get("MIHAD_VERIFY_TIMEOUT", 300))
        env_cmds = os.environ.get("MIHAD_ALLOWED_COMMANDS")
        self.allowed_commands = tuple(allowed_commands or (env_cmds.split(";") if env_cmds else DEFAULT_COMMANDS))
        self.session_id = session_id or os.environ.get("MIHAD_SESSION_ID") or uuid.uuid4().hex[:12]
        self.task_id = os.environ.get("MIHAD_TASK_ID", "")
        self.user_log = os.environ.get("MIHAD_USER_LOG", "")
        self.baseline = verify.baseline_commit(self.project)
        self.log("session_start", {"project": self.project, "baseline_commit": self.baseline,
                                   "min_evidence": self.min_evidence, "task_id": self.task_id})
        if self.policy == "verified" and os.environ.get("MIHAD_CAPTURE_PREFERENCES", "1") == "1":
            self.capture_preferences()

    def capture_preferences(self):
        """Adopt standing preferences straight from the user's own messages (the user log),
        without waiting for the agent to decide to save them. Each line is quoted verbatim,
        so the gate's evidence is the user's words; one-off instructions are skipped."""
        if not self.user_log or not Path(self.user_log).is_file():
            return []
        text = Path(self.user_log).read_text(encoding="utf-8", errors="replace")
        known = {json.loads(r["verify_spec"] or "{}").get("quote") for r in self.store.records()
                 if r["kind"] == "preference"}
        captured = []
        for line in standing_preference_lines(text):
            if line in known:
                continue
            rid = self.store.add_record("preference", line[:80], line, USER_ORIGIN_TAG, "provisional",
                                        self.policy, self.session_id, {"quote": line})
            decision, reason, results = self._evaluate(self.store.get(rid))
            self._apply(rid, decision, reason, results)
            captured.append(rid)
            known.add(line)
        if captured:
            self.log("capture_preferences", {"captured": captured})
        return captured

    def log(self, event, payload):
        self.store.log(self.session_id, self.policy, event, payload)

    # --- the gate ----------------------------------------------------------
    def _check_command(self, spec):
        command = (spec or {}).get("command", "")
        if not any(command == p or command.startswith(p + " ") for p in self.allowed_commands):
            raise MemoryError(f"verify.command must start with one of {list(self.allowed_commands)}")
        args = shlex.split(command)
        for f in (spec or {}).get("test_files") or []:
            if not any(a == f or a.startswith(f + "::") for a in args):
                raise MemoryError(f"test file {f!r} must appear in verify.command so it is actually run")

    def _evaluate(self, rec):
        """Run the verifier for a record and return (decision, reason, evidence list)."""
        spec = json.loads(rec["verify_spec"] or "{}")
        if rec["kind"] == "lesson":
            return "provisional", "lessons need human approval (mihad-memory approve)", []
        if rec["kind"] == "preference":
            results, err = verify.verify_user_quote(self.user_log, spec)
            if err:
                return "provisional", f"inferred preference: {err}; needs the user's words or approval", []
            if not results[0]["passed"]:
                return "provisional", "quote not found verbatim in the user's messages", results
            return "adopted", "stated by the user (verbatim quote)", results
        if rec["kind"] == "skill":
            results, err = verify.verify_skill(self.project, self.baseline, spec, self.timeout)
        else:
            results, err = verify.verify_fact(self.project, self.baseline, spec)
        if err:
            return "provisional", err, []
        independent_pass = {r["source_id"] for r in results if r["passed"] and r["independent"]}
        any_fail = any(not r["passed"] for r in results)
        if any_fail:
            return "rejected", "verification failed", results
        if len(independent_pass) >= self.min_evidence:
            return "adopted", f"{len(independent_pass)} independent passing source(s)", results
        return "provisional", (f"passed but only {len(independent_pass)} independent source(s);"
                               f" need {self.min_evidence}"), results

    def _apply(self, rid, decision, reason, results):
        for r in results:
            self.store.add_evidence(rid, r["kind"], r["source_id"], r["passed"], r["independent"], r["detail"])
        if self.policy == "verified":
            self.store.set_status(rid, decision, reason)
        else:
            self.store.set_shadow(rid, decision)

    # --- tools -------------------------------------------------------------
    def propose(self, kind, title, content, tags="", verify_spec=None, derived_from=None, supersedes=None):
        if kind not in KINDS:
            raise MemoryError(f"kind must be one of {KINDS}")
        if not title or not content:
            raise MemoryError("title and content are required")
        if self.policy == "off":
            self.log("propose", {"kind": kind, "title": title, "stored": False})
            return {"stored": False, "note": "memory is disabled in this session"}
        if kind == "skill":
            self._check_command(verify_spec)
        initial = "provisional" if self.policy == "verified" else "adopted"
        version = 1
        if supersedes:
            old = self.store.get(supersedes)
            if not old:
                raise MemoryError(f"unknown record {supersedes}")
            version = old["version"] + 1
        rid = self.store.add_record(kind, title, content, tags, initial, self.policy, self.session_id,
                                    verify_spec, supersedes, version)
        for parent in derived_from or []:
            if self.store.get(parent):
                self.store.link(parent, rid)
        started = time.time()
        decision, reason, results = self._evaluate(self.store.get(rid))
        self._apply(rid, decision, reason, results)
        if supersedes and self.store.get(rid)["status"] == "adopted":
            self.store.set_status(supersedes, "superseded", f"replaced by {rid}")
        rec = self.store.get(rid)
        self.log("propose", {"id": rid, "kind": kind, "title": title, "status": rec["status"],
                             "gate_decision": decision, "gate_reason": reason,
                             "evidence": [(r["source_id"], r["passed"], r["independent"]) for r in results],
                             "verify_seconds": round(time.time() - started, 3)})
        out = {"id": rid, "status": rec["status"], "gate_decision": decision, "reason": reason}
        if results:
            out["verify_output_tail"] = results[0]["detail"].get("output_tail", "")[-800:]
        return out

    def reverify(self, rid):
        rec = self._require(rid)
        if rec["status"] in ("deleted", "superseded"):
            raise MemoryError(f"record is {rec['status']}")
        decision, reason, results = self._evaluate(rec)
        self._apply(rid, decision, reason, results)
        self.log("reverify", {"id": rid, "gate_decision": decision, "gate_reason": reason})
        return {"id": rid, "status": self.store.get(rid)["status"], "gate_decision": decision, "reason": reason}

    def recall(self, query, k=5, include_provisional=True):
        if self.policy == "off":
            self.log("recall", {"query": query, "returned": []})
            return {"items": [], "note": "memory is disabled in this session"}
        q = tokenize(query)
        statuses = ["adopted"] + (["provisional"] if include_provisional and self.policy == "verified" else [])
        candidates = self.store.records(statuses)
        # Word frequencies are counted per group of near-duplicate items, not per copy, so
        # repeating the same knowledge does not make its words look generic (origin over copies).
        groups = []
        for rec in candidates:
            text = rec["title"] + " " + rec["content"]
            g = next((g for g in groups if similar(text, g["text"]) >= DUPLICATE_JACCARD), None)
            toks = set(tokenize(text + " " + rec["tags"]))
            if g is None:
                groups.append({"text": text, "toks": toks})
            else:
                g["toks"] |= toks
        n = max(len(groups), 1)
        df = {}
        for g in groups:
            for t in g["toks"]:
                df[t] = df.get(t, 0) + 1
        scored = []
        for rec in candidates:
            toks = tokenize(rec["title"] + " " + rec["title"] + " " + rec["content"] + " " + rec["tags"])
            if not toks:
                continue
            # Relevance gate (v0.3): an item must share at least one distinctive query word
            # (one found in at most RECALL_DISTINCTIVE of the items); generic words alone don't count.
            limit = max(1, RECALL_DISTINCTIVE * n)  # small memories: a word in one item is distinctive
            distinctive = [t for t in set(q) if t in toks and df.get(t, 0) <= limit]
            if not distinctive:
                continue
            score = sum(toks.count(t) / len(toks) * (1 + (n / df.get(t, n))) for t in set(q))
            if score > 0:
                scored.append((score, rec))
        scored.sort(key=lambda x: -x[0])
        if scored:
            # Keep only items close to the best match, and never more than RECALL_K.
            top = scored[0][0]
            scored = [(s, r) for s, r in scored if s >= RECALL_RELATIVE * top]
        k = min(k, RECALL_K)
        items, seen = [], []
        for score, rec in scored:
            if len(items) >= k:
                break
            if self.policy == "verified":
                # Same knowledge repeated in other words counts once (origin over copies).
                text = rec["title"] + " " + rec["content"]
                dup = next((i for i, t in seen if similar(text, t) >= DUPLICATE_JACCARD), None)
                if dup is not None:
                    dup["repeats"] = dup.get("repeats", 1) + 1
                    continue
            if rec["kind"] == "fact" and self.policy == "verified":
                if not self._fact_fresh(rec):
                    continue
                rec = self.store.get(rec["id"])
            items.append({"id": rec["id"], "kind": rec["kind"], "status": rec["status"],
                          "title": rec["title"], "content": rec["content"][:1500],
                          "version": rec["version"], "score": round(score, 4),
                          "trust": "verified" if rec["status"] == "adopted" and self.policy == "verified"
                          else ("unverified" if self.policy == "store_all" else "provisional - not verified")})
            if self.policy == "verified":
                seen.append((items[-1], rec["title"] + " " + rec["content"]))
        # Preferences are project-wide rules: always returned, whatever the query.
        prefs = []
        for rec in self.store.records(statuses):
            if rec["kind"] != "preference":
                continue
            prefs.append({"id": rec["id"], "status": rec["status"], "title": rec["title"],
                          "content": rec["content"][:800],
                          "trust": ("stated by the user (verbatim) - follow it" if rec["status"] == "adopted"
                                    and self.policy == "verified" else
                                    ("unverified" if self.policy == "store_all" else "provisional - not confirmed"))})
        items = [i for i in items if i["kind"] != "preference"]
        self.store.bump_use([i["id"] for i in items] + [p["id"] for p in prefs])
        self.log("recall", {"query": query, "returned": [(i["id"], i["status"], i["score"]) for i in items],
                            "preferences": [(p["id"], p["status"]) for p in prefs]})
        return {"preferences": prefs, "items": items}

    def _fact_fresh(self, rec):
        """Re-check a fact against the current project at recall time.

        A provisional fact whose quote has since become part of the baseline is
        promoted; a fact whose quote no longer exists is suspended with its
        descendants. Human-approved facts are left alone.
        """
        if any(e["kind"] == "human" for e in self.store.evidence(rec["id"])):
            return True
        results, err = verify.verify_fact(self.project, self.baseline, json.loads(rec["verify_spec"] or "{}"))
        if err or not results:
            return rec["status"] == "provisional"
        r = results[0]
        if not r["passed"]:
            self.store.set_status(rec["id"], "suspended", r["detail"]["reason"])
            self._cascade(rec["id"], f"source of {rec['id']} changed")
            self.log("auto_suspend", {"id": rec["id"], "reason": r["detail"]["reason"]})
            return False
        if rec["status"] == "provisional" and r["independent"]:
            self.store.add_evidence(rec["id"], r["kind"], r["source_id"], True, True, r["detail"])
            self.store.set_status(rec["id"], "adopted", "quote now present in baseline")
            self.log("auto_promote", {"id": rec["id"]})
        return True

    def _cascade(self, rid, reason):
        affected = []
        for child in self.store.descendants(rid):
            rec = self.store.get(child)
            if rec and rec["status"] in ("adopted", "provisional"):
                self.store.set_status(child, "suspended", reason)
                affected.append(child)
        return affected

    def correct(self, rid, reason, by_human=False):
        rec = self._require(rid)
        if USER_ORIGIN_TAG in (rec["tags"] or "") and not by_human:
            raise MemoryError("this preference was stated by the user; only the user can change it")
        self.store.set_status(rid, "suspended", f"corrected: {reason}")
        affected = self._cascade(rid, f"parent {rid} corrected")
        self.log("correct", {"id": rid, "reason": reason, "cascade": affected})
        return {"id": rid, "status": "suspended", "suspended_descendants": affected}

    def delete(self, rid, reason=""):
        self._require(rid)
        self.store.erase_content(rid)
        self.store.set_status(rid, "deleted", reason)
        affected = self._cascade(rid, f"parent {rid} deleted")
        self.log("delete", {"id": rid, "reason": reason, "cascade": affected})
        return {"id": rid, "status": "deleted", "suspended_descendants": affected}

    def report_outcome(self, task_id, success=None, used_ids=None, helpful_ids=None, harmful_ids=None, notes=""):
        oid = self.store.add_outcome(task_id or self.task_id, self.session_id, self.policy, success,
                                     used_ids or [], helpful_ids or [], harmful_ids or [], notes)
        self.log("outcome", {"task_id": task_id or self.task_id, "success": success, "used": used_ids,
                             "helpful": helpful_ids, "harmful": harmful_ids, "notes": notes})
        return {"id": oid}

    def approve(self, rid, by="human"):
        """Human-only: adopt a lesson or provisional record. Not exposed to the agent."""
        self._require(rid)
        self.store.add_evidence(rid, "human", f"human:{by}", True, True, {"note": "approved via CLI"})
        self.store.set_status(rid, "adopted", f"approved by {by}")
        self.log("approve", {"id": rid, "by": by})

    def status(self):
        counts = {}
        for rec in self.store.records():
            key = f"{rec['kind']}:{rec['status']}"
            counts[key] = counts.get(key, 0) + 1
        return {"policy": self.policy, "session_id": self.session_id, "baseline_commit": self.baseline,
                "counts": counts}

    def _require(self, rid):
        rec = self.store.get(rid)
        if not rec:
            raise MemoryError(f"unknown record {rid}")
        return rec
