"""Run practice tasks (dreams) on any project with OMP, outside the experiment harness.

Each task runs in a fresh, isolated workspace (a random temp directory, deleted afterwards): the
project tree at the task's commit, the mutation applied, committed as the baseline. Arms:
  engine         the experience engine with its lessons
  engine_ablate  the same engine with all lessons switched off (the A/B control)
The output layout (run_meta.json, results.jsonl, <arm>/<task>/agent.jsonl and agent.diff) is the
one episodes.ingest_run and dream.evaluate read.
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .. import project
from .common import export_tree, git, read_json, read_jsonl
from .checkers import DEP_DIRS, prepare_tree, run_checker
from .skills import _test_cmd

EXTENSION = project.PACKAGE_DIR / "omp" / "mihad-experience.ts"
TOOLS = "read,bash,edit,write,grep,glob,todo,experience_review,experience_skill"
OVERLAY = """\
memory:
  backend: off
memories:
  enabled: false
advisor:
  enabled: false
exa:
  enabled: false
skills:
  enabled: false
autolearn:
  enabled: false
"""
PROMPT = """\
You are working in a checkout of the project {name}.

Task: {subject}
{body}

Fix this in the project code ({sources}). Run the existing tests with `{test_command}`
before you finish. Work only from this repository.
"""


def _seconds(text):
    text = str(text).strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600}.get(text[-1], 1)
    return int(float(text[:-1] if text[-1] in "smh" else text) * mult)


def _session_done(session_dir, busy, settle=8):
    if busy.exists() and time.time() - busy.stat().st_mtime < 900:
        return False
    files = sorted(session_dir.glob("*.jsonl"))
    if not files or time.time() - files[-1].stat().st_mtime < settle:
        return False
    last = None
    for e in read_jsonl(files[-1]):
        if e.get("type") == "message":
            last = e["message"]
    return bool(last and last.get("role") == "assistant"
                and last.get("stopReason") in ("stop", "length", "error", "aborted"))


def _kill(proc):
    if proc.poll() is None:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        proc.wait()


def find_agent(name, cfg=None):
    """Path of an agent's command: the config override, PATH, or the usual install folders."""
    override = ((cfg or {}).get("agent_commands") or {}).get(name)
    if override:
        return override
    found = shutil.which(name)
    if found:
        return found
    home = Path.home()
    patterns = {
        "claude": [Path(os.environ.get("APPDATA", home)) / "Claude" / "claude-code", home / ".local" / "bin"],
        "codex": [Path(os.environ.get("LOCALAPPDATA", home)) / "OpenAI" / "Codex" / "bin"],
    }.get(name, [])
    hits = [x for base in patterns if base.is_dir() for x in base.rglob(f"{name}.exe")]
    return str(max(hits, key=lambda x: x.stat().st_mtime)) if hits else name


def agent_model(agent, model):
    """The configured practice model in the form each agent's CLI expects."""
    if agent == "claude":
        return model.split("/", 1)[1] if model and model.startswith("anthropic/") else model
    if agent == "codex":
        return None if not model or model.startswith("anthropic/") else model  # None: the user's Codex default
    return model


def agent_command(agent, cfg, prompt, out_dir, run_dir, ws=None):
    """argv for one practice session, and whether it ends by itself (print modes) or must be watched."""
    dc = cfg["dream"]
    model = agent_model(agent, dc["model"])
    if agent == "claude":
        cmd = [find_agent("claude", cfg), "-p", "--output-format", "stream-json", "--verbose",
               "--dangerously-skip-permissions"] + (["--model", model] if model else []) + [prompt]
        return cmd, True
    if agent == "codex":
        from .. import install
        # Hooks are passed inline: a fresh practice folder is never a trusted Codex project, so its own
        # .codex/ layer would not load.
        hooks = install.codex_inline_hooks(Path(ws) / ".mihad" / install.LAUNCHER) if ws else []
        cmd = [find_agent("codex", cfg), "exec", "--json", "--skip-git-repo-check", "--dangerously-bypass-hook-trust",
               "--dangerously-bypass-approvals-and-sandbox", *hooks] + (["--model", model] if model else []) + [prompt]
        return cmd, True
    mode = ["--session-dir", str(out_dir / "session")] if dc.get("visible") else ["-p", "--mode", "json", "--no-session"]
    return ["omp", *mode, "--no-extensions", "--extension", str(EXTENSION), "--no-skills", "--no-rules",
            "--model", model, "--tools", TOOLS, "--config", str(run_dir / "omp_overlay.yml"),
            "--max-time", dc["max_time"], "--auto-approve", prompt], not dc.get("visible")


def wire_hooks(ws, agent):
    """Practice workspaces for Claude Code and Codex get the hook bridge, like an installed project."""
    from .. import install
    import sys as _sys
    launcher = ws / ".mihad" / install.LAUNCHER
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("import sys\n" f"sys.path.insert(0, {str(project.MIHAD_ROOT)!r})\n"
                        "from mihad_memory.hooks import main\nsys.exit(main())\n", encoding="utf-8")
    if agent == "claude":
        doc = install._merge_hooks({}, lambda e: {"type": "command", "command": _sys.executable,
                                                  "args": [str(launcher), "claude"]})
        target = ws / ".claude" / "settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    # Codex: the launcher is enough; the hooks themselves go on the command line (agent_command).


def _run_visible_print(cmd, ws, out_dir, env, seconds):
    """A print-mode agent in its own visible console: the output is shown there and saved to agent.jsonl."""
    log = out_dir / "agent.jsonl"
    env = dict(env, PYTHONPATH=str(project.MIHAD_ROOT) + os.pathsep + env.get("PYTHONPATH", ""))
    proc = subprocess.Popen([sys.executable, "-m", "mihad_memory.experience.tee", str(log), *cmd], cwd=ws, env=env,
                            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    try:
        proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        _kill(proc)


def _run_agent(cmd, ws, out_dir, env, seconds, visible, busy, self_ending=False):
    if visible and self_ending and os.name == "nt":
        return _run_visible_print(cmd, ws, out_dir, env, seconds)
    if visible and not self_ending:
        proc = subprocess.Popen(cmd, cwd=ws, env=env, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        start = time.time()
        while proc.poll() is None and time.time() - start < seconds:
            if _session_done(out_dir / "session", busy):
                time.sleep(10)
                break
            time.sleep(3)
        _kill(proc)
        sessions = sorted((out_dir / "session").glob("*.jsonl"))
        if sessions:
            shutil.copy2(sessions[-1], out_dir / "agent.jsonl")
        return
    with open(out_dir / "agent.jsonl", "w", encoding="utf-8") as so, \
            open(out_dir / "agent.err", "w", encoding="utf-8") as se:
        proc = subprocess.Popen(cmd, cwd=ws, env=env, stdin=subprocess.DEVNULL, stdout=so, stderr=se)
        try:
            proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            _kill(proc)


def _usage(path):
    """Token usage and turns from an OMP, Claude Code (stream-json) or Codex (exec --json) event log."""
    usage = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    turns = 0
    events = read_jsonl(path)
    result = next((e for e in events if e.get("type") == "result" and e.get("usage")), None)
    if result:  # Claude Code: the final result holds the session totals
        u = result["usage"]
        usage.update(input=u.get("input_tokens", 0), output=u.get("output_tokens", 0),
                     cacheRead=u.get("cache_read_input_tokens", 0), cacheWrite=u.get("cache_creation_input_tokens", 0))
        return usage, result.get("num_turns") or sum(1 for e in events if e.get("type") == "assistant")
    codex = [e for e in events if e.get("type") == "turn.completed" and e.get("usage")]
    if codex:
        for e in codex:
            u = e["usage"]
            usage["input"] += u.get("input_tokens", 0) - u.get("cached_input_tokens", 0)
            usage["cacheRead"] += u.get("cached_input_tokens", 0)
            usage["output"] += u.get("output_tokens", 0)
        return usage, len(codex)
    for e in events:
        m = e.get("message") if e.get("type") in ("message", "turn_end") else None
        if isinstance(m, dict) and m.get("role") == "assistant":
            turns += 1
            for k in usage:
                usage[k] += (m.get("usage") or {}).get(k, 0) or 0
    return usage, turns


def run(engine, run_dir, arms=("engine", "engine_ablate"), cfg=None, tasks=None):
    cfg = cfg or project.load(engine.get("config", {}).get("repo"))
    spec = read_json(engine.root / "dream_tasks.json")
    repo = spec["repo"]
    tasks = tasks if tasks is not None else spec["tasks"]
    dream_cfg = cfg["dream"]
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "omp_overlay.yml").write_text(OVERLAY, encoding="utf-8")
    meta = {"tasks_file": str(engine.root / "dream_tasks.json"), "arms": list(arms), "model": dream_cfg["model"],
            "max_time": dream_cfg["max_time"], "started": time.time(), "project": cfg["name"]}
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    done = {(r["task_id"], r["arm"]) for r in read_jsonl(run_dir / "results.jsonl")}
    ws_root = Path(tempfile.gettempdir()) / "mihad_ws"
    rows = []
    for i, task in enumerate(tasks):
        order = list(arms[i % len(arms):]) + list(arms[:i % len(arms)])
        for arm in order:
            if (task["task_id"], arm) in done:
                continue
            rows.append(run_one(engine, run_dir, task, arm, repo, cfg, ws_root))
    return rows


def run_one(engine, run_dir, task, arm, repo, cfg, ws_root):
    tid = task["task_id"]
    out_dir = run_dir / arm / tid
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    ws = ws_root / f"mw_{secrets.token_hex(4)}"
    ws.mkdir(parents=True)
    export_tree(repo, task["parent"], ws)
    git(ws, "init", "-q")
    (ws / ".git" / "info" / "exclude").write_text(".omp/\n.mihad/\n.claude/\n.codex/\n__pycache__/\n",
                                                  encoding="utf-8")
    if task.get("base_patch"):
        (ws / ".base.patch").write_text(task["base_patch"], encoding="utf-8")
        git(ws, "apply", ".base.patch")
        (ws / ".base.patch").unlink()
    cfg_file = Path(cfg["root"]) / project.CONFIG
    if cfg_file.exists():
        (ws / ".mihad").mkdir(exist_ok=True)
        shutil.copy2(cfg_file, ws / project.CONFIG)
    prepare_tree(ws, cfg["root"])  # installed dependencies (node_modules, vendor) linked, not copied
    with open(ws / ".git" / "info" / "exclude", "a", encoding="utf-8") as fh:
        fh.write("".join(f"{d}/\n" for d in DEP_DIRS))
    git(ws, "add", "-A")
    git(ws, "-c", "user.email=practice@mihad", "-c", "user.name=practice", "commit", "-qm", "baseline")
    prompt = PROMPT.format(name=cfg["name"], subject=task["subject"], body=task.get("body", ""),
                           sources=", ".join(cfg["source_dirs"]), test_command=cfg["test_command"])
    (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    dc = cfg["dream"]
    agent = dc.get("agent", "omp")
    if agent in ("claude", "codex"):
        wire_hooks(ws, agent)
    cmd, self_ending = agent_command(agent, cfg, prompt, out_dir, run_dir, ws)
    state = out_dir / "experience_state.json"
    env = dict(os.environ, PIP_REQUIRE_VIRTUALENV="true", MIHAD_EXPERIENCE_DIR=str(engine.root),
               MIHAD_EXPERIENCE_ROOT=str(project.MIHAD_ROOT), MIHAD_EXPERIENCE_PY=sys.executable,
               MIHAD_EXPERIENCE_STATE=str(state), MIHAD_EXPERIENCE_MODEL=dc["model"],
               MIHAD_EXPERIENCE_TAG=f"{run_dir.name}/{arm}/{tid}",
               MIHAD_EXPERIENCE_EXCLUDE="*" if arm == "engine_ablate" else "",
               MIHAD_EXPERIENCE_EDGES="1" if cfg.get("edges") else "",
               MIHAD_EXPERIENCE_ADVISOR="1" if cfg.get("advisor") else "",
               MIHAD_EXPERIENCE_PRACTICE="1")  # practice sessions are not recorded as live sessions
    started = time.time()
    _run_agent(cmd, ws, out_dir, env, _seconds(dc["max_time"]) + 60, dc.get("visible"), Path(f"{state}.busy"),
               self_ending)
    seconds = round(time.time() - started, 1)
    (out_dir / "agent.diff").write_text(git(ws, "diff", "HEAD", check=False), encoding="utf-8")
    for tf in task.get("hidden_test_files") or []:
        content = git(repo, "show", f"{task['commit']}:{tf}", check=False)
        if content:
            (ws / tf).write_text(content, encoding="utf-8")
    tests_ok = True
    if task.get("hidden_test_ids"):
        res = subprocess.run(_test_cmd(ws, task["hidden_test_ids"]), cwd=ws, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=900)
        tests_ok = res.returncode == 0
    checker_ok = None
    if task.get("checker"):
        checker_ok = run_checker(task["checker"], ws, timeout=120)[0] == 0
    usage, turns = _usage(out_dir / "agent.jsonl")
    row = {"run": run_dir.name, "task_id": tid, "arm": arm, "subject": task["subject"],
           "hidden_pass": tests_ok and checker_ok is not False, "tests_pass": tests_ok, "checker_pass": checker_ok,
           "usage": usage, "turns": turns, "agent_seconds": seconds, "ts": time.time()}
    with open(run_dir / "results.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    for d in DEP_DIRS:  # unlink the dependency links first, so the deletion never reaches the project's copy
        link = ws / d
        if link.is_symlink():
            os.unlink(link)
        elif link.is_junction():  # Windows directory junction: rmdir removes the link, not the target
            os.rmdir(link)
    shutil.rmtree(ws, onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))
    print(f"[practice] {arm:13} {tid} pass={row['hidden_pass']} turns={turns} {seconds}s", flush=True)
    return row
