"""Shared helpers for the experience engine: sessions, diffs, symbols and the store layout."""
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

from .. import langs, project


def git(cwd, *args, check=True):
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {res.stderr}")
    return res.stdout


def export_tree(repo, rev, dest):
    data = subprocess.run(["git", "archive", "--format=tar", rev], cwd=repo, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- sessions

def tool_steps(session_path):
    """Ordered tool steps of an OMP/Pi session file: name, arguments, error flag, result text."""
    calls, steps = {}, []
    for e in read_jsonl(session_path):
        if e.get("kind") == "step":  # the engine's own step log, written by the agent hooks (hooks.py)
            steps.append({"tool": e.get("tool"), "args": e.get("args") or {}, "error": bool(e.get("error")),
                          "text": e.get("text") or ""})
            continue
        # Session files store type=message; --mode json streams repeat it as message_start/_end.
        m = e.get("message") if e.get("type") in ("message", "message_end") else None
        if not isinstance(m, dict):
            continue
        if m.get("role") == "assistant":
            for b in m.get("content") or []:
                if b.get("type") == "toolCall":
                    calls[b.get("id")] = b
        elif m.get("role") == "toolResult":
            call = calls.get(m.get("toolCallId"), {})
            text = "".join(c.get("text", "") for c in m.get("content") or [] if c.get("type") == "text")
            steps.append({"tool": m.get("toolName") or call.get("name"), "args": call.get("arguments") or {},
                          "error": bool(m.get("isError")), "text": text})
    return steps


def session_usage(session_path):
    total = 0
    for e in read_jsonl(session_path):
        m = e.get("message") or {}
        if m.get("role") == "assistant":
            u = m.get("usage") or {}
            total += sum(u.get(k, 0) or 0 for k in ("input", "output", "cacheRead", "cacheWrite"))
    return total


# ---------------------------------------------------------------- diffs

def parse_diff(diff):
    """{path: {"added": [...], "removed": [...], "new_ranges": [(line, 1), ...]}}.

    new_ranges lists only the lines that really changed (numbered in the new file; a pure deletion
    is marked at the line that follows it), never hunk context, so that a change is not attributed
    to a neighbouring function.
    """
    files, cur, new_ln = {}, None, 0
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            cur = None
        elif line.startswith("+++ b/"):
            cur = files.setdefault(line[6:], {"added": [], "removed": [], "new_ranges": []})
        elif line.startswith("+++ /dev/null"):
            cur = None
        elif line.startswith("--- a/") and cur is None:
            cur = files.setdefault(line[6:], {"added": [], "removed": [], "new_ranges": []})
        elif cur is not None and line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            new_ln = int(m.group(1)) if m else 0
        elif cur is not None and line.startswith("+") and not line.startswith("+++"):
            cur["added"].append(line[1:])
            cur["new_ranges"].append((new_ln, 1))
            new_ln += 1
        elif cur is not None and line.startswith("-") and not line.startswith("---"):
            cur["removed"].append(line[1:])
            cur["new_ranges"].append((max(new_ln, 1), 1))
        elif cur is not None and (line.startswith(" ") or line == ""):
            new_ln += 1
    return files


def is_lib(path, root=None):
    """A source file of the project (not a test), per .mihad/project.json or detection."""
    return project.is_source(path, project.load(root))


def is_test(path, root=None):
    return project.is_test(path, project.load(root))


# ---------------------------------------------------------------- symbols

def top_level_defs(source, lang=None):
    """{name: (first_line, last_line)}: Python module-level functions and classes (ast); other languages:
    every function, method and type found by the scanner in langs.py."""
    return langs.defs(source, lang)


def symbols_in_ranges(source, ranges, lang=None):
    if lang not in (None, "python"):
        return langs.symbols_at(source, lang, [start + i for start, count in ranges for i in range(max(count, 1))])
    defs = top_level_defs(source)
    hit = set()
    for start, count in ranges:
        end = start + max(count, 1) - 1
        for name, (a, b) in defs.items():
            if start <= b and end >= a:
                hit.add(name)
    return hit


def library_symbols(root):
    """All public names (functions, methods, types) defined in the project's source files under root."""
    names = set()
    for p in project.source_files(root, project.load(root)):
        lang = langs.language_of(p)
        names |= {n for n in top_level_defs(p.read_text(encoding="utf-8", errors="replace"), lang)
                  if not n.startswith("_")}
    return names


AMBIGUOUS = {"first", "last", "one", "only", "always", "nth", "take", "tail", "head", "pad", "flatten", "unique",
             "split", "chunked", "windowed", "consume", "quantify", "repeat", "count", "zip", "map", "filter"}


def mentioned_symbols(text, known):
    """Library names mentioned in task text. Short common words count only when written as code."""
    found = set()
    for name in known:
        if not re.search(rf"(?<![\w.]){re.escape(name)}(?!\w)", text):
            continue
        as_code = re.search(rf"(`{re.escape(name)}`|{re.escape(name)}\(|\.{re.escape(name)}\b)", text)
        if name in AMBIGUOUS or (len(name) < 6 and "_" not in name):
            if as_code:
                found.add(name)
        else:
            found.add(name)
    return found


# ---------------------------------------------------------------- normalisation

def normalize_error(text):
    """A stable signature for an error message: first meaningful line, specifics masked."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return ""
    line = lines[0]
    if line.startswith("Traceback") or line in ("Error: {", "{"):
        tail = [l for l in lines if re.match(r"^[A-Za-z_.]*(Error|Exception)\b", l) or l.startswith('"error"')]
        line = (tail[-1] if tail else lines[-1])
    line = re.sub(r"'[^']*'", "'<s>'", line)
    line = re.sub(r'"[^"]*"', '"<s>"', line)
    line = re.sub(r"#[0-9A-Fa-f]{4}\b", "#<h>", line)
    line = re.sub(r"[A-Za-z]:[\\/][^\s'\"]+", "<path>", line)
    line = re.sub(r"\b\d+\b", "<n>", line)
    return line[:120]


def normalize_command(cmd):
    """Command template: workspace paths, test targets and quoted snippets masked."""
    c = cmd.strip()
    c = re.sub(r"^(cd\s+(\"[^\"]*\"|\S+)(\s+2>/dev/null)?\s*(&&|;)\s*)+", "", c)
    c = re.sub(r"[A-Za-z]:[\\/][^\s'\"]+", "<path>", c)
    c = re.sub(r"tests\.test_\w+\.\w+(\.\w+)?", "{test_target}", c)
    c = re.sub(r"tests/test_\w+\.py(::\w+)*", "{test_file}", c)
    c = re.sub(r"-c\s+(\"[^\"]*\"|'[^']*')", "-c {snippet}", c, flags=re.S)
    c = re.sub(r"\s+2>&1.*$", "", c)
    c = re.sub(r"\s*\|\s*(tail|head)\b.*$", "", c)
    return re.sub(r"\s+", " ", c)[:160]


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0
