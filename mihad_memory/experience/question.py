"""The timely question: acquire what the model cannot know, at the moment of doubt, and keep it.

Research line (experiments/RESEARCH_main_goal_AR.md): the one add-on whose headroom does not shrink with model
strength is clarification of what the task left unsaid, and detection and timing are its bottleneck, not the
question itself. So doubt is detected from the agent's behaviour, never predicted from the task text:

  goal doubt     at the start: two cheap readings of the task that disagree  -> ask before coding
  detail doubt   mid-task: the agent's own check reports a mismatch, or its tests keep failing -> ask by mid-session
  assumption     before finishing: a test the agent wrote encodes a choice the task never stated -> confirm

A question is answered first from the project itself (docs, changelog, tests), then by the user; every answer is
kept in <state>.answers.jsonl so the same question is never asked twice. At most MAX_QUESTIONS per session.

Off by default. On with MIHAD_QUESTION=1 (experiments) or the project setting "question". The ask_user tool
(mcp_server.py) is exposed when MIHAD_ASK_USER=1; in experiments the user is simulated by a model that holds the
full task text (MIHAD_ORACLE_FILE, MIHAD_ORACLE_MODEL) and answers only what is asked.
"""
import json
import os
import re
import subprocess
from pathlib import Path

from .conscience import ask_text

MAX_QUESTIONS = int(os.environ.get("MIHAD_QUESTION_MAX") or 2)
CHEAP_MODEL = os.environ.get("MIHAD_QUESTION_MODEL") or "anthropic/claude-haiku-4-5"

GOAL_PROMPT = """\
A developer received this task for a Python library. Before any code is read, say whether the task can be
understood in two materially different ways (different behaviour the change must produce, different scope, or a
different case the task may or may not cover). If it can, write the two readings as:
A: <one or two sentences>
B: <one or two sentences>
If every competent developer would read it the same way, reply exactly SAME. No other text.

TASK:
{task}
"""

PROJECT_PROMPT = """\
A developer working on a Python library asks a question about what the project intends. Answer ONLY from the
evidence below (lines from the project's documentation, changelog and tests). If the evidence answers it, reply:
ANSWER: <at most two sentences> (evidence: <path:line>)
If it does not, reply exactly UNKNOWN. Never guess.

QUESTION:
{question}

EVIDENCE:
{evidence}
"""

ORACLE_PROMPT = """\
You are the user who requested this change in a Python library. Everything you know about what you want is the
text below. A developer asks you one question. Answer only that question, in at most two sentences, in plain
words, without code, and without volunteering anything the developer did not ask. If the text below does not
settle the question, reply exactly: I don't know; use your judgment.

WHAT YOU KNOW:
{full}

THE DEVELOPER ASKS:
{question}
"""

ASSUMPTION_PROMPT = """\
A developer finished a change for the task below and wrote these new test lines. Does any test assert a choice
about the intended behaviour that the task text does not state or imply (for example which exception, what
happens in an edge case, an order, a return value)? If yes, write ONE short yes/no question for the user that
would settle it, as: Q: <question>. If every asserted behaviour follows from the task, reply exactly NOTHING.

TASK:
{task}

NEW TEST LINES:
{tests}
"""


def enabled(ws=None):
    if os.environ.get("MIHAD_QUESTION"):
        return True
    if os.environ.get("MIHAD_EXPERIENCE_DIR"):
        return False
    try:
        from .. import project
        return bool(project.load(ws).get("question"))
    except Exception:
        return False


# ---------------------------------------------------------------- goal doubt (at the start)

def goal_doubt(task_text, model=CHEAP_MODEL):
    """Two readings of the task if it can be read two ways, else ''."""
    out = ask_text(model, GOAL_PROMPT.format(task=task_text[:3000]), timeout=120).strip()
    if not out or out.upper().startswith("SAME") or "B:" not in out:
        return ""
    return out[:800]


def goal_block(task_text):
    readings = goal_doubt(task_text)
    if not readings:
        return ""
    return ("\n[question] This task can be read in two ways:\n" + readings +
            "\nIf the repository does not settle which is meant, ask the user once with the ask_user tool "
            "before you change code. Then follow the answer.\n")


# ---------------------------------------------------------------- answering

def _identifiers(question):
    ids = set(re.findall(r"`([^`]{2,60})`", question))
    ids |= set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b", question))  # dotted
    ids |= set(re.findall(r"\b[a-z]+_[a-z_0-9]+\b|\b[A-Z][a-z]+[A-Z][A-Za-z]+\b", question))  # snake, Camel
    return [i.strip("().") for i in ids if len(i) > 2][:8]


def project_evidence(ws, question, limit=6000):
    """Lines of the project's docs, changelog and tests that mention the question's identifiers."""
    ws = Path(ws)
    ids = _identifiers(question)
    if not ids:
        return ""
    pattern = "|".join(re.escape(i) for i in ids)
    try:
        res = subprocess.run(["git", "grep", "-n", "-i", "-E", pattern, "--", "*.rst", "*.md", "*.txt", "docs", "tests"],
                             cwd=ws, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        lines = res.stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        lines = []
    out, size = [], 0
    for l in lines:
        if size + len(l) > limit:
            break
        out.append(l[:300])
        size += len(l)
    return "\n".join(out)


def project_answer(ws, question, model=CHEAP_MODEL):
    evidence = project_evidence(ws, question)
    if not evidence:
        return ""
    out = ask_text(model, PROJECT_PROMPT.format(question=question, evidence=evidence), timeout=120).strip()
    return out[7:].strip()[:600] if out.upper().startswith("ANSWER:") else ""


def user_answer(question):
    """The user, simulated in experiments by a model that holds the full task text."""
    full_file, model = os.environ.get("MIHAD_ORACLE_FILE"), os.environ.get("MIHAD_ORACLE_MODEL")
    if not full_file or not model:
        return ""
    full = Path(full_file).read_text(encoding="utf-8")
    return ask_text(model, ORACLE_PROMPT.format(full=full[:12000], question=question), timeout=180).strip()[:600]


def _answers_path(state_path):
    return Path(f"{state_path}.answers.jsonl")


def past_answers(state_path):
    p = _answers_path(state_path)
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def ask(ws, question, state_path, who="agent"):
    """Answer a question: from earlier answers, the project, then the user. Returns {"answer", "source"}."""
    question = (question or "").strip()
    if not question:
        return {"answer": "Ask one precise question about what the task requires.", "source": "none"}
    past = past_answers(state_path)
    for rec in past:
        if rec["question"].lower() == question.lower():
            return {"answer": rec["answer"], "source": "memory"}
    asked = sum(1 for r in past if r["source"] in ("project", "user"))
    if asked >= MAX_QUESTIONS:
        return {"answer": "No more questions are available in this session; use your best judgment and state "
                          "the assumption in your final message.", "source": "cap"}
    answer, source = project_answer(ws, question), "project"
    if not answer:
        answer, source = user_answer(question), "user"
    if not answer:
        answer, source = "The user is not available; use your best judgment and state the assumption.", "none"
    rec = {"who": who, "question": question[:500], "answer": answer, "source": source}
    with open(_answers_path(state_path), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"answer": answer, "source": source}


# ---------------------------------------------------------------- detail doubt (mid-task)

def detail_block(st):
    """A one-time nudge to ask when the agent's own evidence suggests a wrong reading of the task."""
    if st.get("question_nudged"):
        return ""
    why = None
    if st.get("self_check_fail") and st["self_check_fail"].get("step") == st.get("step"):
        why = "your own check just reported a mismatch"
    elif st.get("test_fails", 0) >= 3:
        why = "the tests you run keep failing"
    if not why:
        return ""
    st["question_nudged"] = True
    return (f"[question] {why}. If this hinges on what the task wants rather than on the code, ask the user "
            "one precise question with the ask_user tool now; if it is a code problem, continue.")


# ---------------------------------------------------------------- assumption check (before finishing)

def assumption_check(ws, task_text, state_path, model=CHEAP_MODEL):
    """Before the agent finishes: one yes/no question about a choice its new tests make that the task never stated,
    answered on the agent's behalf. Returns text for the review ('' if none)."""
    from .common import git
    diff = git(ws, "diff", "HEAD", "--", "tests", check=False)
    tests = "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))[:6000]
    if not tests.strip():
        return ""
    out = ask_text(model, ASSUMPTION_PROMPT.format(task=task_text[:3000], tests=tests), timeout=120).strip()
    if not out.startswith("Q:"):
        return ""
    q = out[2:].strip().splitlines()[0][:300]
    res = ask(ws, q, state_path, who="review")
    if res["source"] in ("cap", "none"):
        return ""
    return (f"[question] Your tests assume something the task did not state, so the user was asked: \"{q}\" "
            f"The answer ({res['source']}): {res['answer']} Fix what applies, then finish.")
