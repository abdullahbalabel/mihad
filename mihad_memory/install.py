"""Install (or remove) مهاد memory, the experience engine and dreaming on any project, for OMP.

    python -m mihad_memory.install PROJECT [--dry-run]
    python -m mihad_memory.install PROJECT --uninstall [--purge]

What it writes, all inside PROJECT and none of it tracked by git:
    .mihad/project.json   detected settings (source and test folders, test command, dream settings); edit freely
    .mihad/experience/    the engine's store (episodes, lessons, checkers, dreams)
    .omp/mcp.json         adds the "mihad_memory" MCP server (existing servers are kept)
    .omp/settings.json    adds the experience extension to "extensions" (existing entries are kept)
    .git/info/exclude     .mihad/ and any .omp file this installer created, so git ignores them locally
Existing .omp files are backed up to .mihad/backup/ before they are changed.
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


def install(root, dry=False, log=print):
    root = Path(root).resolve()
    if not (root / ".git").is_dir():
        raise SystemExit(f"{root} is not a git repository: the memory and the engine need git history")
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
    exp_dir = project.path_in(cfg, "experience_dir")
    _write(exp_dir / "config.json", {"repo": str(root)}, dry, log)
    created = []
    mcp_path = root / ".omp" / "mcp.json"
    if not mcp_path.exists():
        created.append(".omp/mcp.json")
    _backup(root, mcp_path, dry, log)
    mcp = _read(mcp_path, {})
    mcp.setdefault("mcpServers", {})[SERVER] = {
        "command": sys.executable, "args": ["-m", "mihad_memory"], "cwd": str(root),
        "env": {"PYTHONPATH": str(project.MIHAD_ROOT), "MIHAD_PROJECT": str(root),
                "MIHAD_DB": str(project.path_in(cfg, "memory_db")), "MIHAD_POLICY": "verified",
                "MIHAD_ALLOWED_COMMANDS": allowed_commands(cfg),
                "MIHAD_USER_LOG": str(project.path_in(cfg, "user_log"))}}
    _write(mcp_path, mcp, dry, log)
    settings_path = root / ".omp" / "settings.json"
    if not settings_path.exists():
        created.append(".omp/settings.json")
    _backup(root, settings_path, dry, log)
    settings = _read(settings_path, {})
    exts = [e for e in settings.get("extensions", []) if Path(e).name != EXTENSION.name]
    settings["extensions"] = exts + [str(EXTENSION)]
    _write(settings_path, settings, dry, log)
    _exclude(root, [".mihad/"] + created, dry, log)
    log(f"installed for {cfg['name']}: sources {cfg['source_dirs']}, tests {cfg['test_dirs']}, "
        f"runner {cfg['test_runner']}, test command `{cfg['test_command']}`")
    return cfg


def uninstall(root, purge=False, dry=False, log=print):
    root = Path(root).resolve()
    mcp_path, settings_path = root / ".omp" / "mcp.json", root / ".omp" / "settings.json"
    if mcp_path.exists():
        _backup(root, mcp_path, dry, log)
        mcp = _read(mcp_path, {})
        mcp.get("mcpServers", {}).pop(SERVER, None)
        _write(mcp_path, mcp, dry, log)
    if settings_path.exists():
        _backup(root, settings_path, dry, log)
        settings = _read(settings_path, {})
        settings["extensions"] = [e for e in settings.get("extensions", []) if Path(e).name != EXTENSION.name]
        _write(settings_path, settings, dry, log)
    if purge:
        log(f"remove {root / '.mihad'}")
        if not dry:
            shutil.rmtree(root / ".mihad", ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mihad_memory.install")
    ap.add_argument("project")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--purge", action="store_true", help="with --uninstall: also delete .mihad/ (memory and experience)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.uninstall:
        uninstall(a.project, a.purge, a.dry_run)
    else:
        install(a.project, a.dry_run)


if __name__ == "__main__":
    main()
