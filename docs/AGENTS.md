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
| Brief at the start of each request | Yes (desktop app and CLI) | Yes |
| Preference capture and adoption | Yes (desktop app) | Through the same bridge |
| Live step recording and warnings | Yes | Yes |
| Mandatory review that sends the agent back to work | Yes: Claude went back and reran the tests | Yes: a stop was blocked |
| Memory server (MCP) loaded with its four tools | Yes (CLI, `connected`, source `project`) | Through the trusted project's config; not yet tested |
| Practice pair (with and without lessons) | Yes: both passed, hooks fired, usage parsed | Yes: both passed, hooks fired, usage parsed |
| Project-level hooks file | Yes | Not yet: tested with inline hooks; the project file needs the trust step above |

**Windows note for Claude Code.** The desktop app is a packaged app. Files it writes under `AppData\Roaming` are redirected to `AppData\Local\Packages\Claude_*\LocalCache\Roaming`, so a terminal outside the app does not find `claude.exe` at the Roaming path. The engine looks in both places, and in `~/.local/bin`, where the CLI installs itself after signing in.
