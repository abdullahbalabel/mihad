"""Independent verifiers for each claim kind.

The rule that matters: evidence must not come from the same agent turn that
produced the claim. For skills, test files only count when they existed,
unchanged, in the baseline commit captured when the memory server started.
"""
import hashlib
import shlex
import subprocess
from pathlib import Path

OUTPUT_LIMIT = 4000


def _git(project, *args):
    return subprocess.run(["git", *args], cwd=project, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def baseline_commit(project):
    res = _git(project, "rev-parse", "HEAD")
    return res.stdout.strip() if res.returncode == 0 else None


def test_file_status(project, base, path):
    """Return (independent, source_id, reason) for one test file."""
    if not base:
        return False, f"test:{path}", "project has no git baseline"
    blob = _git(project, "rev-parse", f"{base}:{path}")
    if blob.returncode != 0:
        return False, f"test:{path}", "file did not exist in baseline commit (authored this session)"
    changed = _git(project, "diff", "--quiet", base, "--", path)
    if changed.returncode != 0:
        return False, f"test:{path}@{blob.stdout.strip()[:12]}", "file modified since baseline commit"
    return True, f"test:{path}@{blob.stdout.strip()[:12]}", "unchanged since baseline"


def run_command(project, command, timeout):
    args = shlex.split(command, posix=True)
    try:
        res = subprocess.run(args, cwd=project, capture_output=True, text=True, timeout=timeout,
                             encoding="utf-8", errors="replace")
        out = (res.stdout + "\n" + res.stderr).strip()
        return res.returncode, out[-OUTPUT_LIMIT:]
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout}s"
    except OSError as exc:
        return -2, f"could not run command: {exc}"


def verify_skill(project, base, spec, timeout):
    """spec: {"command": "...", "test_files": ["tests/test_x.py", ...]}"""
    command = (spec or {}).get("command")
    files = (spec or {}).get("test_files") or []
    if not command or not files:
        return [], "skill needs verify.command and verify.test_files"
    code, output = run_command(project, command, timeout)
    passed = code == 0
    results = []
    for path in files:
        independent, source_id, reason = test_file_status(project, base, path)
        results.append({"kind": "test_run", "source_id": source_id, "passed": passed,
                        "independent": independent,
                        "detail": {"command": command, "exit_code": code, "file_status": reason,
                                   "output_tail": output}})
    return results, None


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_fact(project, base, spec):
    """spec: {"file": "path/in/repo", "quote": "exact text that supports the fact"}

    The quote is independent evidence only if it already existed in the baseline
    commit; text the agent wrote this session cannot vouch for itself.
    """
    rel = (spec or {}).get("file")
    quote = (spec or {}).get("quote")
    if not rel or not quote:
        return [], "fact needs verify.file and verify.quote"
    path = (Path(project) / rel).resolve()
    if not str(path).startswith(str(Path(project).resolve())) or not path.is_file():
        return [{"kind": "source_match", "source_id": f"file:{rel}", "passed": False,
                 "independent": True, "detail": {"reason": "file not found in project"}}], None
    text = path.read_text(encoding="utf-8", errors="replace")
    found = quote in text
    in_base = False
    if base:
        res = _git(project, "show", f"{base}:{rel.replace(chr(92), '/')}")
        in_base = res.returncode == 0 and quote in res.stdout
    if not found:
        reason = "quote not found in file"
    elif in_base:
        reason = "quote present in baseline commit"
    else:
        reason = "quote only in text written this session (not independent)"
    return [{"kind": "source_match", "source_id": f"file:{rel}", "passed": found, "independent": in_base,
             "detail": {"sha256": file_digest(path), "quote": quote, "in_baseline": in_base,
                        "reason": reason}}], None


def verify_user_quote(user_log, spec):
    """spec: {"quote": "the user's exact words"}. The user log is written by the harness
    (the user's own messages), never by the agent, so a verbatim match is independent evidence."""
    quote = (spec or {}).get("quote")
    if not quote:
        return [], "preference needs verify.quote: the user's exact words"
    if not user_log or not Path(user_log).is_file():
        return [], "no user message log configured (MIHAD_USER_LOG)"
    found = quote.strip() in Path(user_log).read_text(encoding="utf-8", errors="replace")
    return [{"kind": "user_quote", "source_id": "user:messages", "passed": found, "independent": True,
             "detail": {"quote": quote, "reason": "found in user messages" if found
                        else "not found verbatim in user messages"}}], None


def fact_still_valid(project, spec, recorded_sha):
    """Re-check a fact at recall time. Returns (valid, new_sha, reason)."""
    rel, quote = spec.get("file"), spec.get("quote")
    path = Path(project) / rel
    if not path.is_file():
        return False, None, "source file removed"
    sha = file_digest(path)
    if sha == recorded_sha:
        return True, sha, "source unchanged"
    if quote in path.read_text(encoding="utf-8", errors="replace"):
        return True, sha, "source changed but quote still present"
    return False, sha, "source changed and quote no longer present"
