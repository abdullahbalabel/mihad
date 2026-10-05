"""Idea 4: failure-path memory with live detection.

Mining: the same error signature, seen in at least two independent tasks, together with what the
agent did next that worked, becomes a failure-path lesson.
Detection: called on every tool result during a session (from the OMP extension); it answers with
short guidance when a known failure recurs, a command keeps failing, a file is re-read again and
again, or errors pile up. Each warning fires at most once per session.
"""
import json
import re
from collections import defaultdict
from pathlib import Path

from .common import normalize_command, normalize_error
from .engine import lesson_id

MIN_TASKS = 2
REPEAT_COMMAND = 2
REPEAT_READ = 4
ERROR_STREAK = 4
# Errors about the memory tools themselves say nothing about this project or the engine's sessions.
IGNORE = ("mihad_memory", "memory_recall", "memory_propose", "memory_report_outcome", "(no output)")


# Test-run commands of every supported runner (failing tests are the work itself, not a tool pitfall;
# a passing run after the last edit is what the review checks for).
# Output of an agent's own check that reports a mismatch: "Match: False", "equal? False", "MISMATCH", a cross.
SELF_CHECK_FAIL = re.compile(r"(?im)^.*(\b(match(es)?|equal|same|consistent|correct|ok|valid|pass(ed)?)\b[^\n]{0,20}"
                             r"[:=?]\s*False\b|\bMISMATCH\b|✗|❌).*$")
TEST_RUN = re.compile(r"\b(unittest|pytest|node --test|npm (run )?test|jest|vitest|mocha|mvn( -q)? test|gradlew?(\.bat)? test"
                      r"|dotnet test|go test|cargo test|phpunit|rspec|rake test|ctest|make test)\b")


def _mask(value):
    """Lessons must not carry absolute paths (they point into old workspaces or the run directory)."""
    v = re.sub(r"^(cd\s+(\"[^\"]*\"|\S+)\s*(&&|;)\s*)+", "", str(value))
    return re.sub(r"(/[a-z]/|[A-Za-z]:[\\/])[^\s'\"]+", "<abs-path>", v)


def _describe(tool, args):
    if not args:
        return None
    shown = {k: _mask(v) for k, v in args.items() if k in ("path", "command", "pattern")}
    return f"{tool} with " + ", ".join(f"{k}={v!r}" for k, v in shown.items()) if shown else None


def mine(engine):
    groups = defaultdict(lambda: {"tasks": set(), "examples": [], "resolutions": []})
    for ep in engine.episodes():
        for err in ep["errors"]:
            sig = err["signature"]
            blob = sig + json.dumps(err["args"])
            if not sig or any(x in blob for x in IGNORE) or len(re.findall(r"[A-Za-z]{3,}", sig)) < 2:
                continue
            if err["tool"] == "bash" and TEST_RUN.search(err["args"].get("command", "")):
                continue  # failing tests are the work itself, not a tool pitfall
            g = groups[(err["tool"], sig)]
            g["tasks"].add(ep["task_id"])
            if len(g["examples"]) < 2:
                g["examples"].append(err["args"])
                g.setdefault("messages", []).append(err.get("message") or sig)
            if err.get("resolution") and len(g["resolutions"]) < 2:
                g["resolutions"].append(err["resolution"])
    lessons = []
    for (tool, sig), g in groups.items():
        if len(g["tasks"]) < MIN_TASKS or not g["resolutions"]:
            continue
        bad = _describe(tool, g["examples"][0])
        good = _describe(tool, g["resolutions"][0])
        if not good:
            continue
        text = (f"Tool pitfall seen in {len(g['tasks'])} past tasks: {bad[:160] if bad else tool} failed with "
                f"\"{g['messages'][0][:120]}\". What worked instead: {good[:200]}.")
        lessons.append({"id": lesson_id("failure", f"{tool}|{sig}"), "kind": "failure",
                        "trigger": {"tool": tool, "signature": sig}, "text": text,
                        "support": sorted(g["tasks"]), "status": "adopted", "ab": {"pairs": []}})
    return engine.merge_lessons("failure", lessons)


# ---------------------------------------------------------------- live detection

def _load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"reads": {}, "failing": {}, "streak": 0, "warned": [], "last_edit": 0, "last_test": 0, "step": 0}


def detect(engine, event, state_path, cwd=None):
    """event: {"toolName", "input", "isError", "text"}. Returns guidance text ('' if none).
    With cwd, a stuck session may also get one consultation from a stronger model (advisor.py)."""
    st = _load_state(state_path)
    st["step"] += 1
    tool, args, is_error = event.get("toolName"), event.get("input") or {}, bool(event.get("isError"))
    text = event.get("text") or ""
    out, fired = [], []

    def once(key, message, ids=()):
        if key not in st["warned"]:
            st["warned"].append(key)
            out.append(message)
            fired.extend(ids)

    if tool in ("edit", "write") and not is_error:
        st["last_edit"] = st["step"]
    if tool == "bash" and TEST_RUN.search(str(args.get("command", ""))) and not is_error:
        st["last_test"] = st["step"]
    st["streak"] = st["streak"] + 1 if is_error else 0
    if is_error:  # every mistake, remembered with a short signature (the selective advisor looks for patterns)
        sig = normalize_error(text) or normalize_command(str(args.get("command", ""))) or str(tool)
        st["mistakes"] = (st.get("mistakes", []) + [{"step": st["step"], "sig": sig[:120], "tool": tool}])[-20:]
    if tool == "bash" and args.get("command"):
        cmd = str(args["command"])
        st.setdefault("commands", []).append(cmd[:200])
        st["commands"] = st["commands"][-10:]
        if TEST_RUN.search(cmd):
            if is_error:
                st["test_fails"] = st.get("test_fails", 0) + 1
                st["fails"] = (st.get("fails", []) + [text[-1500:]])[-2:]
            else:
                st["last_test_ok"] = st["step"]
        else:
            # The agent's own ad-hoc check said something is wrong (e.g. "Match: False"); the review raises it
            # if the agent finishes without editing afterwards.
            m = SELF_CHECK_FAIL.search(text)
            if m:
                line = text[max(0, text.rfind("\n", 0, m.start()) + 1):].split("\n", 1)[0].strip()
                st["self_check_fail"] = {"step": st["step"], "line": line[:200], "command": cmd[:120]}
                # A check of its own that reports a mismatch is a mistake too, though the command succeeded.
                st["mistakes"] = (st.get("mistakes", []) + [{"step": st["step"], "sig": "own check: " + line[:100], "tool": tool}])[-20:]
    if is_error:
        sig = normalize_error(text)
        for l in engine.active_lessons():
            if l["kind"] == "failure" and l["trigger"]["tool"] == tool and l["trigger"]["signature"] == sig:
                once(l["id"], l["text"], [l["id"]])
        if tool == "bash" and args.get("command"):
            tpl = normalize_command(str(args["command"]))
            st["failing"][tpl] = st["failing"].get(tpl, 0) + 1
            if st["failing"][tpl] >= REPEAT_COMMAND:
                once("cmd:" + tpl, f"This command has now failed {st['failing'][tpl]} times ({sig or 'error'}). "
                                   "Retrying it unchanged will not help: read the error, then change the approach.")
        if st["streak"] >= ERROR_STREAK:
            once(f"streak:{st['step'] // 10}", f"{st['streak']} tool calls in a row failed. Stop, re-read the "
                                               "last error, and re-plan before the next call.")
    if tool == "read" and args.get("path") and not is_error:
        base = str(args["path"]).split("#")[0].split(":")[0]
        st["reads"][base] = st["reads"].get(base, 0) + 1
        if st["reads"][base] >= REPEAT_READ:
            once("read:" + base, f"You have read {base} {st['reads'][base]} times. Locate what you need with "
                                 "grep -n and read only that line range.")
    if cwd:
        from . import advisor
        reason = advisor.due(st)
        if reason:
            advice = advisor.advise(engine, cwd, advisor.task_text_for(state_path), st, reason)
            if advice:
                out.append(advice)
    from . import question
    if cwd and question.enabled(cwd):  # detail doubt: nudge the agent to ask once (question.py)
        nudge = question.detail_block(st)
        if nudge:
            out.append(nudge)
    # The conscience (conscience.py, on when a conscience model is configured): maybe start a background consultation,
    # and hand over the notes earlier ones produced.
    from . import conscience
    conscience.maybe_consult(st, event, state_path, cwd)
    note = conscience.pending_notes(state_path)
    if note:
        out.append(note)
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(state_path).write_text(json.dumps(st), encoding="utf-8")
    if out:
        engine.fired("detect", fired)
        engine.log("detect", {"tool": tool, "messages": out})
    return ("[experience] " + " ".join(out)) if out else ""


def tests_after_last_edit(state_path):
    st = _load_state(state_path)
    return st["last_test"] >= st["last_edit"]
