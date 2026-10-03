"""Install (or remove) مهاد memory, the experience engine and dreaming on any project.

    mihad-install PROJECT [--agent omp|claude|codex ...] [--dry-run]
    mihad-install PROJECT --uninstall [--agent ...] [--purge]

What it writes, all inside PROJECT and none of it tracked by git:
    .mihad/project.json      detected settings (source and test folders, test command, dream settings)
    .mihad/experience/       the engine's store (episodes, lessons, checkers, dreams)
    .mihad/hook.py           (Claude Code, Codex) launcher that forwards hook events to mihad_memory.hooks
    OMP:         .omp/mcp.json (memory server), .omp/settings.json (experience extension)
    Claude Code: .mcp.json (memory server), .claude/settings.json (hooks)
    Codex:       .codex/config.toml (memory server, in a marked block), .codex/hooks.json (hooks)
    .git/info/exclude        .mihad/ and any agent file this installer created
Existing entries are kept, and changed files are backed up to .mihad/backup/ first.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from . import project

SERVER = "mihad_memory"
EXTENSION = project.PACKAGE_DIR / "omp" / "mihad-experience.ts"


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path, data, dry, log):
    log(f"write {path}")
    if not dry:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _backup(root, path, dry, log):
    if Path(path).exists():
        dest = root / ".mihad" / "backup" / f"{Path(path).name}.{time.strftime('%Y%m%d-%H%M%S')}"
        log(f"backup {path} -> {dest}")
        if not dry:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)


def _exclude(root, entries, dry, log):
    f = root / ".git" / "info" / "exclude"
    if not (root / ".git").is_dir():
        return
    have = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    add = [e for e in entries if e not in have]
    if add:
        log(f"git exclude {add}")
        if not dry:
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "a", encoding="utf-8") as fh:
                fh.write("".join(e + "\n" for e in add))


RUNNER_COMMANDS = {
    "pytest": "python -m pytest;pytest", "unittest": "python -m unittest;python -m pytest;pytest",
    "jest": "npx jest;npm test", "vitest": "npx vitest;npm test", "mocha": "npx mocha;npm test",
    "node": "node --test;npm test", "npm": "npm test", "maven": "mvn test;mvn -q test",
    "gradle": "gradle test;./gradlew test;gradlew.bat test", "dotnet": "dotnet test", "go": "go test",
    "cargo": "cargo test", "phpunit": "vendor/bin/phpunit", "rspec": "bundle exec rspec",
    "minitest": "bundle exec rake test;ruby -Itest", "ctest": "ctest;make test",
}


def allowed_commands(cfg):
    """Commands the memory may run to verify a proposed skill: the project's own test runner."""
    return RUNNER_COMMANDS.get(cfg.get("test_runner"), "python -m unittest;python -m pytest;pytest")


AGENTS = ("omp", "claude", "codex")
LAUNCHER = "hook.py"
TOML_BEGIN = "# >>> mihad (managed by mihad-install; do not edit inside) >>>"
TOML_END = "# <<< mihad <<<"
# Hook events and their time limits in seconds (Codex caps SessionEnd at 3 seconds).
HOOK_EVENTS = {"SessionStart": 30, "UserPromptSubmit": 120, "PostToolUse": 360, "Stop": 600, "SessionEnd": 60}


def _memory_server(cfg, root):
    return {"command": sys.executable, "args": ["-m", "mihad_memory"],
            "env": {"PYTHONPATH": str(project.MIHAD_ROOT), "MIHAD_PROJECT": str(root),
                    "MIHAD_DB": str(project.path_in(cfg, "memory_db")), "MIHAD_POLICY": "verified",
                    "MIHAD_ALLOWED_COMMANDS": allowed_commands(cfg),
                    "MIHAD_USER_LOG": str(project.path_in(cfg, "user_log"))}}


def _launcher(root, dry, log):
    """.mihad/hook.py: runs the hook bridge with the tool's own Python and package path."""
    path = root / ".mihad" / LAUNCHER
    text = ("# Written by mihad-install: forwards agent hook events to mihad_memory.hooks.\n"
            "import sys\n"
            f"sys.path.insert(0, {str(project.MIHAD_ROOT)!r})\n"
            "from mihad_memory.hooks import main\n"
            "sys.exit(main())\n")
    log(f"write {path}")
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return path


def _is_ours(group):
    return any(LAUNCHER in str(h.get("command", "")) + " ".join(map(str, h.get("args", [])))
               for h in group.get("hooks", []))


def _merge_hooks(doc, handler):
    """Add one handler group per event; earlier mihad groups are replaced, other hooks are kept."""
    hooks = doc.setdefault("hooks", {})
    for event, timeout in HOOK_EVENTS.items():
        groups = [g for g in hooks.get(event, []) if not _is_ours(g)]
        group = {"hooks": [dict(handler(event), timeout=timeout)]}
        if event == "PostToolUse":
            group["matcher"] = "*"
        groups.append(group)
        hooks[event] = groups
    return doc


def _strip_hooks(doc):
    for event in list((doc.get("hooks") or {})):
        doc["hooks"][event] = [g for g in doc["hooks"][event] if not _is_ours(g)]
        if not doc["hooks"][event]:
            del doc["hooks"][event]
    return doc


def _install_omp(root, cfg, dry, log, created):
    mcp_path = root / ".omp" / "mcp.json"
    if not mcp_path.exists():
        created.append(".omp/mcp.json")
    _backup(root, mcp_path, dry, log)
    mcp = _read(mcp_path, {})
    mcp.setdefault("mcpServers", {})[SERVER] = dict(_memory_server(cfg, root), cwd=str(root))
    _write(mcp_path, mcp, dry, log)
    settings_path = root / ".omp" / "settings.json"
    if not settings_path.exists():
        created.append(".omp/settings.json")
    _backup(root, settings_path, dry, log)
    settings = _read(settings_path, {})
    exts = [e for e in settings.get("extensions", []) if Path(e).name != EXTENSION.name]
    settings["extensions"] = exts + [str(EXTENSION)]
    _write(settings_path, settings, dry, log)


def _install_claude(root, cfg, launcher, dry, log, created):
    mcp_path = root / ".mcp.json"
    if not mcp_path.exists():
        created.append(".mcp.json")
    _backup(root, mcp_path, dry, log)
    mcp = _read(mcp_path, {})
    mcp.setdefault("mcpServers", {})[SERVER] = _memory_server(cfg, root)
    _write(mcp_path, mcp, dry, log)
    settings_path = root / ".claude" / "settings.json"
    if not settings_path.exists():
        created.append(".claude/settings.json")
    _backup(root, settings_path, dry, log)
    settings = _merge_hooks(_read(settings_path, {}), lambda event: {
        "type": "command", "command": sys.executable, "args": [str(launcher), "claude"]})
    # Pre-approve the memory server from .mcp.json (the user installed it on purpose); without this
    # Claude Code may load project MCP servers only after an approval prompt that some clients skip.
    approved = settings.setdefault("enabledMcpjsonServers", [])
    if SERVER not in approved:
        approved.append(SERVER)
    _write(settings_path, settings, dry, log)


def _toml_str(value):
    return json.dumps(str(value))  # a TOML basic string uses the same escapes as JSON


def _codex_block(cfg, root):
    srv = _memory_server(cfg, root)
    lines = [TOML_BEGIN, f"[mcp_servers.{SERVER}]", f"command = {_toml_str(srv['command'])}",
             "args = [" + ", ".join(_toml_str(a) for a in srv["args"]) + "]",
             f"[mcp_servers.{SERVER}.env]"]
    lines += [f"{k} = {_toml_str(v)}" for k, v in srv["env"].items()]
    return "\n".join(lines + [TOML_END]) + "\n"


def _without_block(text):
    if TOML_BEGIN not in text:
        return text
    head, _, rest = text.partition(TOML_BEGIN)
    _, _, tail = rest.partition(TOML_END)
    return (head.rstrip("\n") + "\n" + tail.lstrip("\n")).strip("\n") + ("\n" if head.strip() or tail.strip() else "")


def codex_hook_commands(launcher):
    """(command, commandWindows) for a Codex hook. On Windows Codex runs hook commands through PowerShell,
    where a quoted program path is only executed with the call operator `&`."""
    posix = f'"{sys.executable}" "{launcher}" codex'
    return posix, "& " + posix


def codex_inline_hooks(launcher):
    """`-c` overrides that define the hook bridge for one Codex run (no project files, no trust needed)."""
    command, windows = codex_hook_commands(launcher)
    cmd = windows if sys.platform == "win32" else command
    out = []
    for event, timeout in HOOK_EVENTS.items():
        timeout = 3 if event == "SessionEnd" else timeout
        out += ["-c", f"hooks.{event}=[{{hooks=[{{type=\"command\", command={json.dumps(cmd)}, "
                      f"timeout={timeout}}}]}}]"]
    return out


def _install_codex(root, cfg, launcher, dry, log, created):
    toml_path = root / ".codex" / "config.toml"
    if not toml_path.exists():
        created.append(".codex/config.toml")
    _backup(root, toml_path, dry, log)
    old = toml_path.read_text(encoding="utf-8") if toml_path.exists() else ""
    new = _without_block(old)
    new = (new.rstrip("\n") + "\n\n" if new.strip() else "") + _codex_block(cfg, root)
    log(f"write {toml_path}")
    if not dry:
        toml_path.parent.mkdir(parents=True, exist_ok=True)
        toml_path.write_text(new, encoding="utf-8")
    hooks_path = root / ".codex" / "hooks.json"
    if not hooks_path.exists():
        created.append(".codex/hooks.json")
    _backup(root, hooks_path, dry, log)
    command, windows = codex_hook_commands(launcher)
    doc = _merge_hooks(_read(hooks_path, {}), lambda event: {
        "type": "command", "command": command, "commandWindows": windows})
    doc["hooks"]["SessionEnd"][-1]["hooks"][0]["timeout"] = 3  # Codex's maximum for SessionEnd
    _write(hooks_path, doc, dry, log)


def install(root, agents=("omp",), dry=False, log=print):
    root = Path(root).resolve()
    if not (root / ".git").is_dir():
        raise SystemExit(f"{root} is not a git repository: the memory and the engine need git history")
    unknown = set(agents) - set(AGENTS)
    if unknown:
        raise SystemExit(f"unknown agent(s) {sorted(unknown)}; choose from {AGENTS}")
    project.clear_cache()
    cfg_path = root / project.CONFIG
    if cfg_path.exists():
        # Keep the user's settings; refresh only where the tool and its Python live (they move on upgrade).
        user = _read(cfg_path, {})
        if user.get("mihad_root") != str(project.MIHAD_ROOT) or user.get("python") != sys.executable:
            _backup(root, cfg_path, dry, log)
            user.update(mihad_root=str(project.MIHAD_ROOT), python=sys.executable)
            _write(cfg_path, user, dry, log)
        project.clear_cache()
        cfg = project.load(root)
        log(f"keep existing settings in {cfg_path}")
    else:
        cfg = project.detect(root)
        _write(cfg_path, cfg, dry, log)
    cfg["root"] = str(root)
    _write(project.path_in(cfg, "experience_dir") / "config.json", {"repo": str(root)}, dry, log)
    created = []
    launcher = _launcher(root, dry, log) if set(agents) & {"claude", "codex"} else None
    if "omp" in agents:
        _install_omp(root, cfg, dry, log, created)
    if "claude" in agents:
        _install_claude(root, cfg, launcher, dry, log, created)
    if "codex" in agents:
        _install_codex(root, cfg, launcher, dry, log, created)
    _exclude(root, [".mihad/"] + created, dry, log)
    log(f"installed for {cfg['name']} ({', '.join(agents)}): language {cfg.get('language', 'python')}, "
        f"sources {cfg['source_dirs']}, tests {cfg['test_dirs']}, runner {cfg['test_runner']}, "
        f"test command `{cfg['test_command']}`")
    if "codex" in agents:
        log("Codex runs project hooks only for trusted projects: trust this folder in Codex once.")
    if "claude" in agents:
        log("Claude Code: the memory server in .mcp.json is pre-approved (enabledMcpjsonServers).")
    return cfg


def uninstall(root, agents=AGENTS, purge=False, dry=False, log=print):
    root = Path(root).resolve()

    def edit_json(path, change):
        if path.exists():
            _backup(root, path, dry, log)
            _write(path, change(_read(path, {})), dry, log)

    def drop_server(doc):
        doc.get("mcpServers", {}).pop(SERVER, None)
        return doc

    if "omp" in agents:
        edit_json(root / ".omp" / "mcp.json", drop_server)
        edit_json(root / ".omp" / "settings.json", lambda d: dict(d, extensions=[
            e for e in d.get("extensions", []) if Path(e).name != EXTENSION.name]))
    if "claude" in agents:
        edit_json(root / ".mcp.json", drop_server)
        def strip_claude(doc):
            doc = _strip_hooks(doc)
            if SERVER in doc.get("enabledMcpjsonServers", []):
                doc["enabledMcpjsonServers"].remove(SERVER)
                if not doc["enabledMcpjsonServers"]:
                    del doc["enabledMcpjsonServers"]
            return doc

        edit_json(root / ".claude" / "settings.json", strip_claude)
    if "codex" in agents:
        edit_json(root / ".codex" / "hooks.json", _strip_hooks)
        toml_path = root / ".codex" / "config.toml"
        if toml_path.exists():
            _backup(root, toml_path, dry, log)
            log(f"write {toml_path}")
            if not dry:
                toml_path.write_text(_without_block(toml_path.read_text(encoding="utf-8")), encoding="utf-8")
    if purge:
        log(f"remove {root / '.mihad'}")
        if not dry:
            shutil.rmtree(root / ".mihad", ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mihad-install")
    ap.add_argument("project")
    ap.add_argument("--agent", action="append", choices=AGENTS,
                    help="agent to wire up (repeatable): omp, claude, codex; default omp")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--purge", action="store_true", help="with --uninstall: also delete .mihad/ (memory and experience)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.uninstall:
        uninstall(a.project, tuple(a.agent or AGENTS), a.purge, a.dry_run)
    else:
        install(a.project, tuple(a.agent or ("omp",)), a.dry_run)


if __name__ == "__main__":
    main()
