"""Escalation when the agent is stuck: one consultation with a stronger model.

The live detector already knows when a session is going badly (tests keep failing, a long session
with no passing test since the last edit, a streak of errors). At that moment, and at most once per
session by default, a stronger model sees what the agent sees (task, current diff, the code of the
functions involved, the last failing output, the recent commands) and answers with a short diagnosis.
It never sees hidden tests or the reference fix. The agent keeps working; it only gets advice.
"""
import os
import subprocess
import tempfile
from pathlib import Path

from .. import project
from .brief import symbol_source
from .common import git, library_symbols, mentioned_symbols

MODEL = os.environ.get("MIHAD_EXPERIENCE_ADVISOR_MODEL") or "anthropic/claude-sonnet-5"
MAX_ADVICE = int(os.environ.get("MIHAD_EXPERIENCE_ADVISOR_MAX", 1))
FAILING_TEST_RUNS = 2
LONG_SESSION = 40

PROMPT = """\
You are a senior maintainer of the {language} project {project}, advising a junior coding agent that
is stuck on a task. Diagnose the most likely root cause and the right approach in at most 150 words:
name the exact logic or lines that are wrong and what the correct behavior should be, including
edge cases the agent may be missing. Do not write a full patch.

TASK:
{task}

CODE OF THE FUNCTIONS INVOLVED (current workspace):
{code}

AGENT'S CURRENT CHANGE (git diff):
{diff}

LAST FAILING OUTPUT:
{fails}

RECENT COMMANDS:
{commands}
"""


def due(st):
    if not os.environ.get("MIHAD_EXPERIENCE_ADVISOR") or st.get("advised", 0) >= MAX_ADVICE:
        return None
    if st.get("test_fails", 0) >= FAILING_TEST_RUNS + st.get("advised_at_fails", 0):
        return "tests failed repeatedly"
    if st["step"] >= LONG_SESSION and st.get("last_test_ok", 0) < st.get("last_edit", 0):
        return "long session without a passing test after the last edit"
    if st.get("streak", 0) >= 4:
        return "error streak"
    return None


def ask_text(model, prompt, timeout=300):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(prompt)
        path = fh.name
    try:
        res = subprocess.run(["omp", "-p", "--no-session", "--no-extensions", "--no-skills", "--no-rules", "--no-tools",
                              "--model", model, f"@{path}"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", stdin=subprocess.DEVNULL, timeout=timeout)
        return res.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
    finally:
        Path(path).unlink(missing_ok=True)


def advise(engine, ws, task_text, st, reason):
    fams = sorted(mentioned_symbols(task_text, library_symbols(ws)))
    code = "\n\n".join(symbol_source(ws, f, 4000) for f in fams)[:9000] or "(no function named in the task)"
    diff = git(ws, "diff", "HEAD", check=False)[:6000] or "(no change yet)"
    cfg = project.load(ws)
    prompt = PROMPT.format(project=cfg["name"], language=cfg.get("language", "python").title(), task=task_text[:3000], code=code, diff=diff,
                           fails="\n---\n".join(st.get("fails", [])[-2:])[:3000] or "(none)",
                           commands="\n".join(st.get("commands", [])[-10:]))
    text = ask_text(MODEL, prompt)
    st["advised"] = st.get("advised", 0) + 1
    st["advised_at_fails"] = st.get("test_fails", 0)
    engine.log("advice", {"reason": reason, "model": MODEL, "advice": text[:2000]})
    if not text:
        return ""
    return f"[experience advisor, consulted because: {reason}] {text}"


def task_text_for(state_path):
    p = Path(f"{state_path}.task.txt")
    return p.read_text(encoding="utf-8") if p.exists() else ""

