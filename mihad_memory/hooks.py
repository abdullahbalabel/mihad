"""Bridge from agent hooks (Claude Code, Codex) to the memory and the experience engine.

Both agents run a command for each hook event, pass one JSON object on stdin and read a JSON
object from stdout. The two use the same output shapes, so one bridge serves both:

    UserPromptSubmit  log the user's words (preference capture), record the session start, brief
                      -> {"hookSpecificOutput": {"hookEventName": ..., "additionalContext": brief}}
    PostToolUse       record the step, live detection (and the advisor when enabled)
                      -> additionalContext with the warning
    Stop              record the end snapshot, review the change; findings block the stop once
                      -> {"decision": "block", "reason": findings}
    SessionStart      remember the model (tool competence is per model)
    SessionEnd        count the session; start a background dream cycle every N sessions

Tool steps are written to a per-session log in the engine's own format, so learning does not
depend on each agent's transcript format.

    python .mihad/hook.py claude|codex      (the launcher the installer writes)
"""
import json
import re
import os
import sys
import traceback
from pathlib import Path

from . import project

TOOL_NAMES = {"bash": "bash", "shell": "bash", "exec_command": "bash", "local_shell": "bash",
              "read": "read", "read_file": "read", "view": "read",
              "edit": "edit", "multiedit": "edit", "apply_patch": "edit", "notebookedit": "edit",
              "write": "write", "create_file": "write",
              "grep": "grep", "glob": "glob", "search": "grep"}


def normalize_tool(name, tool_input):
    """Agent tool name and input -> the engine's (tool, args) vocabulary (bash/read/edit/write/grep)."""
    raw = (name or "").split("__")[-1]
    tool = TOOL_NAMES.get(raw.lower(), raw.lower())
    ti = tool_input if isinstance(tool_input, dict) else {"input": tool_input}
    args = {}
    if tool == "bash":
        cmd = ti.get("command") or ti.get("cmd") or ""
        args["command"] = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    elif tool in ("read", "edit", "write"):
        path = ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or ""
        if not path and isinstance(ti.get("input"), str):  # Codex apply_patch: "*** Update File: src/x.py"
            m = re.search(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", ti["input"], re.M)
            path = m.group(1).strip() if m else ""
        if tool == "read" and (ti.get("offset") or ti.get("limit")):
            start = int(ti.get("offset") or 1)
            path = f"{path}:{start}-{start + int(ti.get('limit') or 0)}"
        args["path"] = str(path)
    elif tool == "grep":
        args["pattern"] = str(ti.get("pattern") or ti.get("query") or "")
    return tool, args


def response_text_and_error(resp):
    """(text, is_error) from a tool_response of either agent."""
    if resp is None:
        return "", False
    if isinstance(resp, str):
        return resp[-4000:], resp.lstrip().lower().startswith(("error", "traceback"))
    if isinstance(resp, list):
        parts = [response_text_and_error(r)[0] for r in resp]
        return "\n".join(parts)[-4000:], False
    if isinstance(resp, dict):
        text = "\n".join(str(resp[k]) for k in ("text", "stdout", "stderr", "output", "content", "error")
                         if resp.get(k))
        code = resp.get("exit_code", resp.get("exitCode", resp.get("returncode")))
        err = bool(resp.get("is_error") or resp.get("isError") or resp.get("type") == "error"
                   or resp.get("interrupted") or (code not in (None, 0)) or resp.get("success") is False)
        return text[-4000:], err
    return str(resp)[-4000:], False


class Session:
    """Per-session files under <engine>/state/: detection state, step log, task text, cache."""

    def __init__(self, engine, agent, sid):
        base = engine.root / "state"
        base.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(sid or "unknown"))[:80]
        self.prefix = base / f"{agent}-{safe}"
        self.state = Path(f"{self.prefix}.json")
        self.steps = Path(f"{self.prefix}.steps.jsonl")
        self.meta = Path(f"{self.prefix}.meta.json")
        self.cache = Path(f"{self.prefix}.checkers.json")

    def get_meta(self):
        try:
            return json.loads(self.meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def put_meta(self, **kw):
        m = self.get_meta()
        m.update(kw)
        self.meta.write_text(json.dumps(m), encoding="utf-8")
        return m


def context(event_name, text):
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text[:9500]}}


def handle(agent, data):
    """One hook event -> the JSON to print (or None)."""
    from .experience import brief as brief_mod, cycle, failures, live, review as review_mod
    from .experience.engine import Engine
    from .experience.__main__ import memory_block, memory_for

    event = data.get("hook_event_name") or ""
    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    env_mode = bool(os.environ.get("MIHAD_EXPERIENCE_DIR"))  # practice sessions set the engine directly
    if not env_mode and not (project.find_root(cwd) / project.CONFIG).exists():
        return None  # not an installed project
    cfg = project.load(cwd)
    engine = Engine(os.environ.get("MIHAD_EXPERIENCE_DIR") or project.path_in(cfg, "experience_dir"))
    sid = data.get("session_id") or "unknown"
    s = Session(engine, agent, sid)
    os.environ.setdefault("MIHAD_EXPERIENCE_TAG", f"{agent}/{sid}")
    if not env_mode:
        if cfg.get("advisor"):
            os.environ["MIHAD_EXPERIENCE_ADVISOR"] = "1"
        if cfg.get("advisor_model"):
            os.environ["MIHAD_EXPERIENCE_ADVISOR_MODEL"] = cfg["advisor_model"]
    edges = bool(os.environ.get("MIHAD_EXPERIENCE_EDGES")) if env_mode else bool(cfg.get("edges"))
    if edges:
        os.environ["MIHAD_EXPERIENCE_EDGES"] = "1"
    if data.get("model"):
        s.put_meta(model=data["model"])

    if event == "SessionStart":
        return None

    if event == "UserPromptSubmit":
        prompt = data.get("prompt") or ""
        meta = s.get_meta()
        Path(f"{s.state}.task.txt").write_text(prompt, encoding="utf-8")  # the advisor reads the task here
        if not env_mode:
            log = project.path_in(cfg, "user_log")
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"--- {agent} {sid}\n{prompt}\n")
            memory_for(cfg).capture_preferences()
            if not meta.get("started"):
                live.record(engine, "start", cwd, str(s.steps), prompt)
                live.ingest(engine)
        s.put_meta(started=True, reviewed=False)
        model = os.environ.get("MIHAD_EXPERIENCE_MODEL") or s.get_meta().get("model")
        text = brief_mod.brief(engine, cwd, prompt, model)
        if not env_mode:
            text += memory_block(cwd, prompt)
        return context("UserPromptSubmit", text)

    if event == "PostToolUse":
        tool, args = normalize_tool(data.get("tool_name"), data.get("tool_input"))
        text, err = response_text_and_error(data.get("tool_response"))
        with open(s.steps, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "step", "tool": tool, "args": args, "error": err,
                                 "text": text[-2000:]}) + "\n")
        advice = failures.detect(engine, {"toolName": tool, "input": args, "isError": err, "text": text},
                                 str(s.state), cwd)
        return context("PostToolUse", advice) if advice else None

    if event == "Stop":
        if not env_mode:
            tests_ok = failures.tests_after_last_edit(str(s.state)) if s.state.exists() else None
            live.record(engine, "end", cwd, str(s.steps), tests_ok=tests_ok)
        meta = s.get_meta()
        # One extra turn at most: never block a stop that a block already caused.
        if data.get("stop_hook_active") or meta.get("reviewed"):
            return None
        s.put_meta(reviewed=True)
        tests_ok = failures.tests_after_last_edit(str(s.state)) if s.state.exists() else None
        res = review_mod.review(engine, cwd, tests_ok, str(s.cache), str(s.state), edges)
        return {"decision": "block", "reason": res["text"]} if res["text"] else None

    if event == "SessionEnd":
        if not env_mode and s.get_meta().get("started"):
            cycle.session_closed(engine, cfg)
        return None
    return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    agent = argv[0] if argv else "claude"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        data = json.loads(sys.stdin.read() or "{}")
        out = handle(agent, data)
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False))
    except Exception:  # a hook must never break the agent's session
        try:
            log = Path(os.environ.get("TEMP", "/tmp")) / "mihad-hook-errors.log"
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(traceback.format_exc() + "\n")
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
