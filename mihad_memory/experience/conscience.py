"""The conscience: a stronger model that speaks up when the agent keeps making mistakes, with a short context.

Named by the user: like a conscience, it is not asked; it reproaches the agent when it errs, again if the error
repeats, and says what to do instead. In experiment "watch" (bidict, Haiku as the agent) it kept the gain of
OMP's always-on advisor (44/63 against 45/63; 37/63 without either) at about 37% of its cost; with Haiku itself as
the conscience there was no gain (38/63): it needs a stronger model than the agent.

OMP's own advisor re-reads the whole session after every turn (about 81 reviews of ~62k tokens per session in
experiment "advisor"); its value came from a few notes, mostly right after the agent ran something and saw a
result, and late in the session. This one is asked only when should_consult() says so, at most MAX_CONSULTS
times per session, and it reads a compact context (the task, the change so far, the last command and its output)
instead of the transcript.

With memory on (conscience_memory, MIHAD_CONSCIENCE_MEMORY), it also reads the user's standing preferences, to
hold the agent to them, and how the same errors were resolved in earlier sessions of the project (the episodes the
experience engine recorded; MIHAD_CONSCIENCE_PAST names a file of them instead).

With the final review on (conscience_final, MIHAD_CONSCIENCE_FINAL), it also reads the task and the change once
when the agent says it is done, as a teacher reads the answer sheet before it is handed in: the failures that never
raise an error (a misread task, a missed case) never wake it otherwise. Its note joins the review (review.py).

It never blocks the agent: a consultation runs in a background process and writes its note to
<state>.notes.jsonl; detect() hands pending notes to the agent with its next tool result.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .common import git, read_jsonl

# At most this many consultations per session (MIHAD_CONSCIENCE_MAX, or the config's conscience_max, overrides).
MAX_CONSULTS = int(os.environ.get("MIHAD_CONSCIENCE_MAX") or os.environ.get("MIHAD_WATCH_MAX") or 10)
NOTE_PROMPT = """\
You are a senior engineer advising a weaker coding agent that is fixing a task in a Python project. You see the task,
the agent's change so far, and what just happened ({reason}). Give at most two short, concrete corrections the
agent needs right now: what is wrong and what to do instead, with file and line where possible. If the agent is on
track, reply exactly NOTHING. Do not restate the task, do not praise, do not give generic advice.

TASK:
{task}

THE AGENT'S CHANGE SO FAR (git diff):
{diff}

WHAT JUST HAPPENED:
{event}
{memory}"""
FINAL_PROMPT = """You are a senior engineer reviewing the work of a weaker coding agent that says it has finished a task in a Python
project. Read the task and the agent's change. Does the change do what the task asks, for every case the task
describes? If it is correct and complete, reply exactly NOTHING. Otherwise give at most two short, concrete
corrections: what is wrong or missing and what to change, with file and line where possible. Do not comment on
style, tests or documentation unless the task asks for them. Do not praise, do not give generic advice.

TASK:
{task}

THE AGENT'S CHANGE (git diff):
{diff}
"""
PREFS_BLOCK = """
THE USER'S STANDING PREFERENCES (stated by the user; if the change breaks one, say so as one of your corrections):
{prefs}
"""
PAST_BLOCK = """
THE SAME ERRORS IN EARLIER SESSIONS OF THIS PROJECT (what the agent ran next that worked; use it only if it applies):
{past}
"""


def should_consult(st, event, consults):
    """Whether this moment is worth a consultation of the stronger model; returns a short reason, or None.

    st: the session state kept by failures.detect (step, last_edit, last_test, last_test_ok, test_fails, streak,
        commands, self_check_fail, ...); event: {"toolName", "input", "isError", "text"} of the tool call that just
        ended; consults: how many consultations this session already had (at most MAX_CONSULTS are allowed).
    """
    # Like a teacher: step in when the student keeps making mistakes, not at every line. Two mistakes since the
    # last consultation; the same one twice means stuck on a wrong idea, different ones may mean a wrong reading
    # of the task. (Design: the user's.)
    new = [m for m in st.get("mistakes", []) if m["step"] > st.get("conscience_seen_step", 0)]
    if len(new) < 2:
        return None
    st["conscience_seen_step"] = new[-1]["step"]
    if new[-1]["sig"] == new[-2]["sig"]:
        return f"the agent repeated the same mistake: {new[-1]['sig']}"
    return "the agent made several different mistakes: " + "; ".join(m["sig"] for m in new[-3:])


def standing_preferences(ws):
    """The user's standing preferences: MIHAD_CONSCIENCE_PREFS (a file, one per line), else the project memory's."""
    f = os.environ.get("MIHAD_CONSCIENCE_PREFS")
    if f:
        return [l.strip("-* ").strip() for l in Path(f).read_text(encoding="utf-8").splitlines() if l.strip()]
    try:
        from .. import project
        from ..store import Store
        db = project.path_in(project.load(ws), "memory_db")
        if not db.exists():
            return []
        store = Store(str(db))
        try:
            return [r["content"] for r in store.records(["adopted"]) if r["kind"] == "preference"]
        finally:
            store.close()
    except Exception:
        return []


def past_resolutions(mistakes, episodes, limit=3):
    """Earlier errors with the same tool and signature as the mistakes that called the conscience, with what the
    agent ran next that worked (episodes.summarize_steps: the next successful call of the same tool)."""
    from .failures import IGNORE, _mask
    wanted = {(m.get("tool"), m["sig"]) for m in mistakes}
    out, seen = [], set()
    for ep in episodes:
        for e in ep.get("errors", []):
            key = (e["tool"], (e.get("signature") or "")[:120])
            if key not in wanted or not e.get("resolution") or any(x in key[1] for x in IGNORE) or key in seen:
                continue
            seen.add(key)
            tried = _mask(json.dumps(e.get("args") or {}, ensure_ascii=False))[:200]
            worked = _mask(json.dumps(e["resolution"], ensure_ascii=False))[:300]
            first = "; ".join(_mask(b) for b in e.get("between") or [])
            out.append(f"- {e['tool']} failed with \"{_mask(e.get('message') or key[1])[:140]}\" after {tried}; "
                       + (f"the agent then ran: {first}; " if first else "") + f"then this worked: {worked}")
            if len(out) >= limit:
                return out
    return out


def memory_context(ws, mistakes, engine=None):
    """The memory block of the prompt, and what it held (for the log)."""
    prefs = standing_preferences(ws)
    f = os.environ.get("MIHAD_CONSCIENCE_PAST")
    episodes = read_jsonl(f) if f else (engine.episodes() if engine else [])
    past = past_resolutions(mistakes, episodes)
    text = (PREFS_BLOCK.format(prefs="\n".join(f"- {p}" for p in prefs)) if prefs else "")
    text += PAST_BLOCK.format(past="\n".join(past)) if past else ""
    return text, {"prefs": len(prefs), "past": len(past)}


def compact_context(ws, task, event, reason, memory=""):
    diff = git(ws, "diff", "HEAD", check=False)[:6000] or "(no change yet)"
    cmd = (event.get("input") or {}).get("command") or json.dumps(event.get("input") or {})[:300]
    happened = f"tool {event.get('toolName')} ({'error' if event.get('isError') else 'ok'}): {cmd}\n" \
               f"output (last lines):\n{(event.get('text') or '')[-2500:]}"
    return NOTE_PROMPT.format(reason=reason, task=task[:3000], diff=diff, event=happened, memory=memory)


def ask_text(model, prompt, timeout=240):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(prompt)
        path = fh.name
    try:
        res = subprocess.run(["omp", "-p", "--no-session", "--no-extensions", "--no-skills", "--no-rules", "--no-tools",
                              "--model", model, f"@{path}"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", stdin=subprocess.DEVNULL, timeout=timeout)
        return res.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""
    finally:
        Path(path).unlink(missing_ok=True)


def consult(ws, state_path, model, reason, event_file, engine=None):
    """Run in a background process: ask the model and append its note (if any) to <state>.notes.jsonl."""
    task_file = Path(f"{state_path}.task.txt")
    task = task_file.read_text(encoding="utf-8") if task_file.exists() else ""
    event = json.loads(Path(event_file).read_text(encoding="utf-8"))
    memory, held = memory_context(ws, event.get("_mistakes", []), engine) if event.get("_memory") else ("", None)
    note = ask_text(model, compact_context(ws, task, event, reason, memory))
    rec = {"reason": reason, "note": "" if note.strip().upper().startswith("NOTHING") else note[:1500],
           "memory": held}
    with open(f"{state_path}.notes.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def maybe_consult(st, event, state_path, ws):
    """Called by failures.detect after each tool call: start a background consultation when should_consult says so."""
    if not ws:
        return
    model = os.environ.get("MIHAD_CONSCIENCE_MODEL") or os.environ.get("MIHAD_WATCH_MODEL")
    cap, memory = MAX_CONSULTS, bool(os.environ.get("MIHAD_CONSCIENCE_MEMORY"))
    if not model and not os.environ.get("MIHAD_EXPERIENCE_DIR"):  # project mode: the config turns it on
        try:
            from .. import project
            cfg = project.load(ws)
            model, cap = cfg.get("conscience_model"), int(cfg.get("conscience_max") or MAX_CONSULTS)
            memory = bool(cfg.get("conscience_memory"))
        except Exception:
            model = None
    if not model:
        return
    consults = st.get("conscience_consults", 0)
    if consults >= cap:
        return
    new = [m for m in st.get("mistakes", []) if m["step"] > st.get("conscience_seen_step", 0)]
    reason = should_consult(st, event, consults)
    if not reason:
        return
    st["conscience_consults"] = consults + 1
    event_file = f"{state_path}.conscience-event-{consults + 1}.json"
    Path(event_file).write_text(json.dumps({**event, "_mistakes": new, "_memory": memory}), encoding="utf-8")
    subprocess.Popen([sys.executable, "-m", "mihad_memory.experience", "conscience-consult", "--cwd", str(ws),
                      "--state", str(state_path), "--model", model, "--reason", reason, "--event-file", event_file],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def final_model(ws):
    """The conscience model if its final review is on, else None."""
    model = os.environ.get("MIHAD_CONSCIENCE_MODEL") or os.environ.get("MIHAD_WATCH_MODEL")
    if os.environ.get("MIHAD_EXPERIENCE_DIR"):  # experiment mode: the environment decides
        return model if model and os.environ.get("MIHAD_CONSCIENCE_FINAL") else None
    try:
        from .. import project
        cfg = project.load(ws)
    except Exception:
        return None
    return (model or cfg.get("conscience_model")) if cfg.get("conscience_final") else None


def final_review(ws, state_path):
    """One reading of the task and the finished change; returns a note for the agent ('' if none or off)."""
    model = final_model(ws)
    if not model or not state_path:
        return ""
    done = Path(f"{state_path}.final.json")
    if done.exists():  # once per session: the answer sheet is read once
        return ""
    task_file = Path(f"{state_path}.task.txt")
    task = task_file.read_text(encoding="utf-8") if task_file.exists() else ""
    diff = git(ws, "diff", "HEAD", check=False)[:12000]
    note = ask_text(model, FINAL_PROMPT.format(task=task[:3000], diff=diff))
    note = "" if not note.strip() or note.strip().upper().startswith("NOTHING") else note.strip()[:1500]
    done.write_text(json.dumps({"note": note}, ensure_ascii=False), encoding="utf-8")
    return note


def pending_notes(state_path):
    """Notes written since the last delivery, as text for the agent ('' if none)."""
    p = Path(f"{state_path}.notes.jsonl")
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8").splitlines()
    seen_path = Path(f"{state_path}.notes.delivered")
    seen = int(seen_path.read_text()) if seen_path.exists() else 0
    new = [json.loads(l) for l in lines[seen:]]
    seen_path.write_text(str(len(lines)))
    notes = [r["note"] for r in new if r.get("note")]
    if not notes:
        return ""
    return "[conscience] A senior engineer reviewed what you just did:\n" + "\n\n".join(notes)
