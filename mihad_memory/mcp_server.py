"""Minimal MCP server over stdio (newline-delimited JSON-RPC 2.0), standard library only."""
import json
import os
import sys
import time
import traceback

from . import __version__
from .service import Memory, MemoryError

PROTOCOL_VERSION = "2025-06-18"

VERIFY_SCHEMA = {
    "type": "object",
    "description": ("skill: {command, test_files} - tests must already exist unchanged in the git baseline. "
                    "fact: {file, quote} - exact text in a project file that supports the fact. "
                    "preference: {quote} - the user's exact words stating the preference."),
    "properties": {
        "command": {"type": "string"},
        "test_files": {"type": "array", "items": {"type": "string"}},
        "file": {"type": "string"},
        "quote": {"type": "string"},
    },
}

TOOLS = [
    {
        "name": "memory_recall",
        "description": ("Search the project's persistent memory before starting work. Returns verified "
                        "skills, facts and lessons from earlier sessions. Items marked provisional or "
                        "unverified have NOT passed independent checks - treat them as hints only. "
                        "'preferences' are the user's standing working preferences: follow them."),
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What you are about to work on."},
            "k": {"type": "integer", "default": 5}}, "required": ["query"]},
    },
    {
        "name": "memory_propose",
        "description": ("Propose something worth remembering for future sessions. kind=skill: a reusable "
                        "technique or code pattern, verified by running existing project tests. kind=fact: "
                        "a fact about this codebase, verified by quoting a project file. kind=preference: how "
                        "the user wants work done in future sessions; verify.quote must be the user's exact "
                        "words. kind=lesson: a "
                        "general lesson; stays provisional until a human approves it. Only adopted items "
                        "count as verified knowledge."),
        "inputSchema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["skill", "fact", "lesson", "preference"]},
            "title": {"type": "string"},
            "content": {"type": "string", "description": "The knowledge itself, self-contained."},
            "tags": {"type": "string"},
            "verify": VERIFY_SCHEMA,
            "derived_from": {"type": "array", "items": {"type": "string"},
                             "description": "IDs of memory items this builds on."},
            "supersedes": {"type": "string", "description": "ID of an older item this replaces."}},
            "required": ["kind", "title", "content"]},
    },
    {
        "name": "memory_correct",
        "description": "Mark a memory item as wrong. It and everything derived from it is suspended.",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["id", "reason"]},
    },
    {
        "name": "memory_report_outcome",
        "description": ("At the end of a task, report which memory items you used and whether they helped "
                        "or caused problems."),
        "inputSchema": {"type": "object", "properties": {
            "task_id": {"type": "string"},
            "success": {"type": "boolean"},
            "used_ids": {"type": "array", "items": {"type": "string"}},
            "helpful_ids": {"type": "array", "items": {"type": "string"}},
            "harmful_ids": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"}}, "required": ["success"]},
    },
]

# Exposed only when MIHAD_ASK_USER is set (experiments: the user is simulated; see experience/question.py).
ASK_USER_TOOL = {
    "name": "ask_user",
    "description": ("Ask the user one precise question when the task text leaves open what is required (which "
                    "behaviour, which case, which exception). The answer may come from the project's own "
                    "documentation or from the user. At most two questions per session; do not ask what the code "
                    "or the task already settles."),
    "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
}


class Server:
    def __init__(self, memory):
        self.memory = memory

    def call(self, name, args):
        m = self.memory
        if name == "ask_user":
            from .experience import question
            return question.ask(os.environ.get("MIHAD_PROJECT") or os.getcwd(), args.get("question", ""),
                                os.environ.get("MIHAD_EXPERIENCE_STATE") or os.path.join(m.project, ".mihad", "question"))
        if name == "memory_recall":
            return m.recall(args["query"], int(args.get("k", 5)))
        if name == "memory_propose":
            return m.propose(args["kind"], args["title"], args["content"], args.get("tags", ""),
                             args.get("verify"), args.get("derived_from"), args.get("supersedes"))
        if name == "memory_correct":
            return m.correct(args["id"], args["reason"])
        if name == "memory_report_outcome":
            return m.report_outcome(args.get("task_id", ""), args.get("success"), args.get("used_ids"),
                                    args.get("helpful_ids"), args.get("harmful_ids"), args.get("notes", ""))
        raise MemoryError(f"unknown tool {name}")

    def handle(self, msg):
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:  # notification
            return None
        if method == "initialize":
            client = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
            return self._ok(mid, {"protocolVersion": client, "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "mihad-memory", "version": __version__}})
        if method == "ping":
            return self._ok(mid, {})
        if method == "tools/list":
            return self._ok(mid, {"tools": TOOLS + ([ASK_USER_TOOL] if os.environ.get("MIHAD_ASK_USER") else [])})
        if method == "tools/call":
            params = msg.get("params") or {}
            name, args = params.get("name"), params.get("arguments") or {}
            started = time.time()
            try:
                result, is_error = self.call(name, args), False
            except (MemoryError, KeyError, ValueError, TypeError) as exc:
                result, is_error = {"error": str(exc)}, True
            except Exception as exc:  # keep the server alive; log the traceback
                result, is_error = {"error": f"internal error: {exc}"}, True
                self.memory.log("internal_error", {"tool": name, "trace": traceback.format_exc()})
            self.memory.log("tool_call", {"tool": name, "args": args, "is_error": is_error,
                                          "ms": round((time.time() - started) * 1000)})
            text = json.dumps(result, ensure_ascii=False, indent=1)
            return self._ok(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}

    @staticmethod
    def _ok(mid, result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    project = os.environ.get("MIHAD_PROJECT") or os.getcwd()
    memory = Memory(project)
    server = Server(memory)
    stdin = open(sys.stdin.fileno(), "r", encoding="utf-8", newline="\n", closefd=False)
    stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", newline="\n", closefd=False)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = server.handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    memory.log("session_end", {})


if __name__ == "__main__":
    main()
