"""Project configuration shared by the memory, the experience engine and the dream cycle.

Everything that used to be specific to the trial repository (where the code lives, where the tests
live, how to run them, what the project is called) comes from `<project>/.mihad/project.json`.
Missing fields are detected from the repository itself, so a replay workspace with no config file
behaves exactly as before.
"""
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

from . import langs

CONFIG = Path(".mihad") / "project.json"
SKIP_DIRS = {"tests", "test", "docs", "doc", "examples", "build", "dist", "venv", ".venv", "env", "node_modules",
             "site-packages", "__pycache__", "vendor", "target", "bin", "obj", "out", "coverage", "__tests__",
             "__snapshots__", "__mocks__", "generated", "third_party"}
PACKAGE_DIR = Path(__file__).resolve().parent  # mihad_memory/ (holds omp/mihad-experience.ts)
MIHAD_ROOT = PACKAGE_DIR.parent  # the directory that contains the package: cwd/PYTHONPATH for engine calls


def find_root(start=None):
    """The nearest directory at or above `start` holding .mihad/project.json, else the git root, else start."""
    p = Path(start or os.environ.get("MIHAD_PROJECT") or os.getcwd()).resolve()
    for d in [p, *p.parents]:
        if (d / CONFIG).exists():
            return d
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d
    return p


def _uses_pytest(root):
    if (root / "pytest.ini").exists() or (root / "conftest.py").exists() or any(root.glob("tests/conftest.py")):
        return True
    for name in ("pyproject.toml", "setup.cfg", "tox.ini"):
        f = root / name
        if f.exists() and ("[tool.pytest" in f.read_text(encoding="utf-8", errors="replace")
                           or "[pytest]" in f.read_text(encoding="utf-8", errors="replace")
                           or "[tool:pytest]" in f.read_text(encoding="utf-8", errors="replace")):
            return True
    return False


SOURCE_CANDIDATES = ("src/main/java", "src/main/kotlin", "src", "lib", "app", "pkg", "internal", "cmd", "source",
                     "Sources")
TEST_CANDIDATES = ("test", "tests", "__tests__", "spec", "src/test")


def _detect_other(root, lang):
    """Source and test folders, runner and command for a non-Python project."""
    sources = [d for d in SOURCE_CANDIDATES if (root / d).is_dir()]
    if "src/main/java" in sources or "src/main/kotlin" in sources:
        sources = [d for d in sources if d.startswith("src/main")]
    elif "src" in sources:
        sources = ["src"] + [d for d in sources if d not in ("src",) and not d.startswith("src/")]
    if lang == "csharp":
        sources = sorted({p.parent.relative_to(root).as_posix() for p in root.rglob("*.csproj")
                          if not re.search(r"Tests?$", p.parent.name)}) or sources
    tests = [d for d in TEST_CANDIDATES if (root / d).is_dir()]
    if lang == "csharp":
        tests = sorted({p.parent.relative_to(root).as_posix() for p in root.rglob("*.csproj")
                        if re.search(r"Tests?$", p.parent.name)}) or tests
    runner, command = langs.detect_runner(root, lang)
    return sources or ["."], tests or (["."] if lang in ("go", "rust", "javascript", "typescript") else ["tests"]), \
        runner, command


def detect(root):
    root = Path(root)
    lang = langs.detect_language(root)
    if lang != "python":
        sources, tests, runner, command = _detect_other(root, lang)
        cfg = _defaults(root)
        cfg.update({"language": lang, "source_dirs": sources, "test_dirs": tests, "test_runner": runner,
                    "test_command": command})
        return cfg
    sources = []
    for base in (root, root / "src"):
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if d.is_dir() and d.name not in SKIP_DIRS and not d.name.startswith((".", "_")) \
                    and (d / "__init__.py").exists():
                sources.append(d.relative_to(root).as_posix())
    if not sources:
        sources = ["."]
    tests = [d for d in ("tests", "test") if (root / d).is_dir()]
    runner = "pytest" if _uses_pytest(root) else "unittest"
    if runner == "pytest":
        command = f"python -m pytest -q {' '.join(tests)}".strip()
    else:
        command = f"python -m unittest discover -s {tests[0]}" if tests else "python -m unittest discover"
    cfg = _defaults(root)
    cfg.update({"language": "python", "source_dirs": sources, "test_dirs": tests or ["tests"],
                "test_runner": runner, "test_command": command})
    return cfg


def _defaults(root):
    return {"name": Path(root).name, "experience_dir": ".mihad/experience", "memory_db": ".mihad/memory.db",
            "user_log": ".mihad/user_messages.txt", "python": sys.executable, "mihad_root": str(MIHAD_ROOT),
            # Off by default: in experiment engine2 a generic edge-case checklist in every session added
            # cost and lowered quality without raising success. Enable per project to try it.
            "edges": False, "advisor": False, "advisor_model": "anthropic/claude-sonnet-5",
            # Executable review checks (probes.py): off until experiment engine3 measures them.
            "checks": False,
            "dream": {"agent": "omp", "auto_after_sessions": 0, "tasks_per_cycle": 4,
                      "model": "anthropic/claude-haiku-4-5",
                      "max_time": "15m", "visible": True}}


@lru_cache(maxsize=32)
def _load(root_str):
    root = Path(root_str)
    cfg = detect(root)
    f = root / CONFIG
    if f.exists():
        user = json.loads(f.read_text(encoding="utf-8"))
        dream = {**cfg["dream"], **user.get("dream", {})}
        cfg.update(user)
        cfg["dream"] = dream
    cfg["root"] = str(root)
    return cfg


def load(root=None):
    return dict(_load(str(find_root(root))))


def clear_cache():
    _load.cache_clear()


def path_in(cfg, key):
    """A config path (relative to the project root unless absolute)."""
    p = Path(cfg[key])
    return p if p.is_absolute() else Path(cfg["root"]) / p


def _lang(cfg):
    return cfg.get("language") or "python"


def _family(lang):
    """Languages whose files belong together in one project (a TypeScript project also has .js)."""
    return {"typescript": ("typescript", "javascript"), "kotlin": ("kotlin", "java"),
            "java": ("java", "kotlin")}.get(lang, (lang,))


def is_source(path, cfg):
    path = path.replace("\\", "/")
    lang = _lang(cfg)
    if lang == "python":
        if not path.endswith((".py", ".pyi")) or is_test(path, cfg):
            return False
    elif langs.language_of(path) not in _family(lang) or is_test(path, cfg):
        return False
    if lang != "python" and any(part in SKIP_DIRS or part.startswith(".") for part in path.split("/")[:-1]):
        return False
    return any(d == "." or path.startswith(d.rstrip("/") + "/") for d in cfg["source_dirs"])


def is_test(path, cfg):
    path = path.replace("\\", "/")
    if _lang(cfg) != "python":
        return langs.is_test_path(path) or any(d != "." and path.startswith(d.rstrip("/") + "/")
                                               for d in cfg["test_dirs"])
    name = path.rsplit("/", 1)[-1]
    return any(path.startswith(d.rstrip("/") + "/") for d in cfg["test_dirs"]) or name.startswith("test_") \
        or name.endswith("_test.py")


def _files(root, dirs, keep, limit):
    root = Path(root)
    out, seen = [], set()
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p in seen:
                continue
            rel = p.relative_to(root).as_posix()
            # skip build/vendor/hidden folders below the listed folder (the folder itself may be "tests")
            if any(part in SKIP_DIRS or part.startswith(".") for part in p.relative_to(base).parts[:-1]):
                continue
            if keep(rel):
                out.append(p)
                seen.add(p)
            if len(out) >= limit:
                return out
    return out


def source_files(root, cfg, limit=2000):
    return _files(root, cfg["source_dirs"], lambda rel: is_source(rel, cfg), limit)


def test_files(root, cfg, limit=2000):
    if _lang(cfg) == "python":
        return _files(root, cfg["test_dirs"], lambda rel: rel.endswith(".py") and (
            rel.rsplit("/", 1)[-1].startswith("test_") or rel.endswith("_test.py")), limit)
    fam = _family(_lang(cfg))
    # Tests may sit beside the code (Go, JS/TS) or in test folders.
    return _files(root, list(cfg["test_dirs"]) + list(cfg["source_dirs"]),
                  lambda rel: langs.language_of(rel) in fam and is_test(rel, cfg), limit)


def language_of_path(path, cfg):
    return langs.language_of(path) or _lang(cfg)
