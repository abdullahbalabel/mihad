# Changelog

## 1.3.2 — 2026-10-05

- **Paper v2.3.** A final review by the conscience, and the stronger model as the agent (Table 26, Figure 8): Sonnet 5.5
  as the agent reached 51/63 at about $1.41 per success, against 44/63 at about $1.22 for Haiku with the conscience.
- **`conscience_final` (off).** One reading of the task and the finished change before the agent finishes. Its notes were
  precise, but it reached 45/63 against 44/63 at 15.5% more cost, so it stays off.

## 1.3.1 — 2026-10-05

- **Paper v2.2.** New title, *Better Coding Agents Without Retraining the Model*: the model stays fixed, and what learns is
  the memory and experience around it. Eight figures, including charts of every experiment.
- **`conscience_memory` (off).** The conscience can also read the standing preferences and how the same errors were
  resolved in earlier sessions; episodes now record what the agent did between an error and its fix. In the experiment
  it did not help (43/63 against 44/63, longer sessions, +19% cost), so it stays off.

## 1.3.0 — 2026-10-05

- **The conscience (optional).** A stronger model that speaks up after repeated mistakes, with a short context and in the
  background (`conscience_model`, `conscience_max`). Experiment (bidict, 63 runs per arm): 44/63 vs 37/63 without it, at
  about 37% of the cost of an always-on advisor that reached 45/63.
- **Review delivery fix (OMP).** OMP gives an extension handler 30 seconds; the executable review takes minutes, and a
  late review often never reached the agent. The review now runs on its own and starts a new turn when ready.
- **`test_python`.** The project's tests run with the project's own interpreter (its `.venv`, or the setting).
- **Review fixes:** no mutants of docstring lines or of `return None/False/NotImplemented`; a message when the agent
  finishes without any change; an alert when the agent's own check printed a mismatch.
- **Off by default, kept for research:** property checks with a contract ontology, task-behaviour metadata, closed-loop
  review rounds, rules learned from the project's history. Precise, but they caught almost no failures on unseen
  repositories.
- **Paper v2.1.**

## 1.2.0 — 2026-10-03

- **Executable review checks, on by default** (`checks: true`). Before the agent finishes, its change is run, not just read:
  - **mutation adequacy:** each added line is broken on a temporary copy, and the line is reported if the tests still pass;
  - **format and lint** regressions relative to the starting commit;
  - **checkable preferences**, such as a regression test that must fail on the old code;
  - **single-pass iterators** (Python), reported only as a regression.
- **Evidence (experiment engine3, 3 repetitions, 42 runs per arm, Haiku):**
  - success 35/42 vs 32/42;
  - judge 3.98 vs 3.74, a rise that follows the extra successes (passing runs differ by at most 0.11);
  - format regressions 0 vs 10;
  - +2% cost.
- **Upgrading:** projects installed with 1.1 keep `"checks": false` in `.mihad/project.json`; set it to `true`.
- **Paper v1.9:** adds the executable-review experiment.

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
- **Verified live with Claude Code (CLI):**
  - the memory server connected with its four tools;
  - a full practice pair with Claude Code as the practice agent.
- **Agent discovery** finds Claude Code inside the desktop app's package folder on Windows.

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
