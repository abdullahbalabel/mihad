# Changelog

## 1.1.0 — 2026-10-03

- **Claude Code and Codex.** `mihad-install --agent claude|codex` wires the memory (MCP) and the experience engine through hooks:
  - the brief on `UserPromptSubmit`;
  - live warnings on `PostToolUse`;
  - a mandatory review on `Stop`, blocking once per request;
  - session counting and automatic dreaming on `SessionEnd`.

  One bridge, `mihad_memory.hooks`, serves both agents, and existing settings are kept.
- **Agent-neutral learning.** Tool steps are recorded in the engine's own log, so learning does not depend on transcript formats.
- **Practice with any agent.** `dream.agent` chooses `omp`, `claude` or `codex` for practice sessions. Print-mode sessions are shown in a visible window and their event log is saved.
- **Codex on Windows.** Hook commands use the PowerShell call operator, and practice runs pass the hooks inline because practice folders are never trusted projects.
- **Verified live with Codex:**
  - the brief, step recording and a mandatory review that blocked a stop;
  - a full practice pair.
- **Verified live with Claude Code (desktop app):**
  - the brief and preference capture;
  - a mandatory review that sent Claude back to rerun the tests.
- **Fixes from that session:**
  - hook input is decoded as UTF-8, so Arabic prompts are no longer garbled;
  - edits are detected as changes to the project, whatever tool made them (shell scripts included, writes outside the project excluded);
  - a paraphrased preference is not stored twice;
  - the memory server is pre-approved for Claude Code.

## 1.0.0 — 2026-10-03

First standalone release, separated from the experiments repository. It keeps what the experiments supported and turns off what they did not.

- **Verified memory (MCP).** Typed adoption gate for skills, facts, lessons and preferences; a relevance gate on recall; corrections and deletions that reach derived items; facts re-checked on recall.
- **Preference capture.** Taken from the user's own messages, sentence by sentence, right after each message is logged.
- **Experience engine.** Episodes, failure paths with live detection, review before finishing, learning from the user's commits, verifier scripts with the before/after gate, compiled skills, and a competence record.
- **Dreaming.** Practice tasks made by mutating past fixes; A/B promotion and retirement of lessons; a full cycle runnable by hand or in the background every N sessions.
- **Any project.** One-command install and uninstall for OMP, with configuration detected automatically and kept on reinstall.
- **Ten languages.** Python and JavaScript/TypeScript verified live; Java, Kotlin, C#, Go, Rust, PHP, Ruby and C/C++ at the analysis level.
- **Safety.** Isolated practice workspaces, global pip installs blocked, and dependency folders linked and unlinked rather than copied.
- **Defaults from the evidence.** The generic edge-case checklist and the advisor are off, because experiment engine2 showed added cost and lower quality without higher success.
- **Paper.** Research paper v1.8 in English.
