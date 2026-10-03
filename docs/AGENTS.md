# Agents: OMP, Claude Code and Codex

MIHAD works with three coding agents. The memory and the experience engine are the same for all of them; only the wiring differs.

```bash
mihad-install D:/path/to/project --agent omp       # default
mihad-install D:/path/to/project --agent claude
mihad-install D:/path/to/project --agent codex
mihad-install D:/path/to/project --agent claude --agent codex   # several at once
```

## What each agent gets

| Part | OMP | Claude Code | Codex |
|---|---|---|---|
| Memory (MCP server) | `.omp/mcp.json` | `.mcp.json` | `.codex/config.toml` (a marked block) |
| Brief at the start of each request | extension | `UserPromptSubmit` hook | `UserPromptSubmit` hook |
| Live warnings after each tool | extension | `PostToolUse` hook | `PostToolUse` hook |
| Mandatory review before finishing | extension (one extra turn) | `Stop` hook, `decision: "block"` (one extra turn) | `Stop` hook, `decision: "block"` (one extra turn) |
| Session counting, automatic dreaming | extension | `SessionEnd` hook | `SessionEnd` hook |
| Skills `test_symbol`, `check_change` | extension tools | run `mihad skill run …` | run `mihad skill run …` |
| Configuration files | `.omp/` | `.claude/settings.json`, `.mcp.json` | `.codex/hooks.json`, `.codex/config.toml` |

All hooks call one launcher, `.mihad/hook.py`, which passes the event to `mihad_memory.hooks`. The bridge answers in the format both agents share (`hookSpecificOutput.additionalContext`, and `decision: "block"` with a `reason` for Stop).

The review blocks a stop at most once per request. It never blocks a stop that a previous block caused (`stop_hook_active`). Tool steps are written to the engine's own log, so learning does not depend on each agent's transcript format.

## Claude Code (terminal and desktop app)

1. Install: `mihad-install <project> --agent claude`.
2. Open the project in Claude Code (terminal or desktop app). The memory server in `.mcp.json` is pre-approved for this project through `enabledMcpjsonServers` in `.claude/settings.json`, so no approval prompt is needed.
3. The hooks are in the project's `.claude/settings.json`, which both the terminal and the desktop app read. Your own hooks in that file are kept.

**Practice sessions (dreaming) with Claude Code** run `claude -p`. The standalone `claude` command needs to be signed in once: run `claude` in a terminal and use `/login`. The desktop app's sign-in is separate.

## Codex (CLI and app)

1. Install: `mihad-install <project> --agent codex`.
2. **Trust the project once in Codex.** Codex loads a project's `.codex/` folder (hooks and config) only for trusted projects; this is Codex's own safety rule. Open the project in Codex and accept the trust prompt, or add it in your Codex settings.
3. On Windows, Codex runs hook commands through PowerShell. The installer writes `commandWindows` with the PowerShell call operator (`& "python.exe" ".mihad/hook.py" codex`); without it PowerShell would print the command instead of running it.

**Practice sessions (dreaming) with Codex** run `codex exec` in isolated temporary folders. These are never trusted projects, so the hooks are passed on the command line (`-c hooks.…`) together with `--dangerously-bypass-hook-trust`. This applies only to the practice copies the engine creates, never to your project.

## Choosing the practice agent

In `.mihad/project.json`:

```json
"dream": { "agent": "codex", "model": "" }
```

| `dream.agent` | Runs | `dream.model` |
|---|---|---|
| `omp` (default) | `omp` in a visible window | an OMP model id, e.g. `anthropic/claude-haiku-4-5` |
| `claude` | `claude -p` (stream-json), shown in a visible window | e.g. `haiku`; an `anthropic/…` id is converted |
| `codex` | `codex exec --json`, shown in a visible window | a Codex model; empty uses your Codex default |

If an agent's command is not on `PATH`, the engine looks in the usual install folders. You can also set the path yourself:

```json
"agent_commands": { "claude": "C:/path/to/claude.exe", "codex": "C:/path/to/codex.exe" }
```

## Verified so far

| Check | Claude Code 2.1.286 | Codex CLI 0.159.2 |
|---|---|---|
| Install alongside existing settings, reinstall, uninstall | Yes (tests) | Yes (tests) |
| Hooks fire in a real session | Yes: brief, step recording, and a mandatory review that sent Claude back to rerun the tests | Yes: brief, live step recording, mandatory review that blocked a stop |
| A full live session | Yes, in the desktop app; the preference was captured and adopted | Yes |
| Memory server (MCP) loaded by the agent | Pre-approval added after the first desktop test; to be confirmed | Through the trusted project's config |
| Practice pair (with and without lessons) | Not yet | Yes: both sessions passed, hooks fired, usage parsed |
| Project-level hooks after trusting the project | Yes (the hooks above came from the project file) | Not yet: tested with inline hooks; the project file needs the trust step above |
