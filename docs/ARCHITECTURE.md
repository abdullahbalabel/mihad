# Architecture

![The experience engine around a coding agent](../paper/figures/MIHAD_Experience_Engine_EN.png)

## Package layout

```
mihad_memory/
  project.py        project config: detection, source/test classification, file listing
  langs.py          languages: detection, symbols (functions/methods/classes), test runners, focused commands
  install.py        install/uninstall into a project (.mihad/, .omp/mcp.json, .omp/settings.json)
  store.py          SQLite store: records, evidence, lineage, outcomes, events
  verify.py         checkers: skill (project tests), fact (quote at baseline commit), user quote
  service.py        Memory: propose/evaluate/adopt, recall with the relevance gate, correct, delete,
                    preference capture
  mcp_server.py     MCP server (stdio JSON-RPC) exposing the memory tools
  cli.py            mihad-memory: the human side of the memory
  omp/mihad-experience.ts   OMP extension: brief, live detection, review, tools, live recording
  experience/
    engine.py       the experience store (files under .mihad/experience)
    episodes.py     episodes from sessions (replay runs and practice runs)
    live.py         live sessions: snapshots, ingestion once the user commits
    corrections.py  co-change rules from the user's final versions
    failures.py     failure-path lessons and live detection
    edges.py        edge-case kinds mined from verifier scripts (optional)
    checkers.py     verifier scripts/test files with the before/after gate
    review.py       review at the decision point
    brief.py        the brief, sized by competence
    competence.py   per-family and per-model competence
    skills.py       compiled skills and test discovery
    dream.py        practice tasks by mutation; A/B evaluation of lessons
    practice.py     runs practice tasks in OMP in isolated workspaces
    cycle.py        the full learning cycle; background start every N sessions
    advisor.py      stronger-model consultation when stuck (optional)
    report.py       the human-readable report
```

## The engine's files

All under `.mihad/experience/`:

| File | Content |
|---|---|
| `config.json` | The project root |
| `episodes.jsonl` | One line per finished session |
| `live_sessions.jsonl` | Start/end snapshots of live sessions |
| `corrections.jsonl` | What the user's final version changed relative to the agent |
| `lessons.json` | Adopted experience with status and A/B record |
| `checkers.json`, `checkers/` | Verifier records and their scripts or test files |
| `skills.json` | Compiled skills and their verification |
| `competence.json` | Family and tool competence |
| `dream_tasks.json`, `dreams/` | Practice tasks and practice runs |
| `fired.jsonl` | Which lesson fired in which session (for A/B credit) |
| `brief.jsonl`, `detect.jsonl`, `review.jsonl`, `advice.jsonl` | Logs of what the agent was told |
| `cycles.jsonl` | Summary of each dream cycle |
| `state/` | Per-session state of the extension |

Everything is plain JSON or text, so every decision can be inspected.

## How a session flows

1. OMP starts in the project. It reads `.omp/mcp.json`, which starts the memory server, and `.omp/settings.json`, which loads the extension. The extension finds `.mihad/project.json` by walking up from the working directory.
2. **On each user message** (`before_agent_start`), the extension:
   - appends the message to the user log, so the memory can capture standing preferences right away;
   - on the first message, records a start snapshot and ingests any older sessions you have committed after;
   - asks for the brief and injects it as a message.
3. **On each tool result** (`tool_result`), the extension sends the result to `detect`. Any warning is returned as additional context for the next step.
4. **When the agent stops** (`agent_end`), the extension records an end snapshot, then runs `review`. Findings start one more turn.
5. **When OMP closes** (`session_shutdown`), the session is counted. Every N sessions a dream cycle starts in the background.

## Trust boundaries

- **The memory never trusts the agent's own statement as evidence.** Facts must exist in the starting commit, skills must pass tests the agent did not change, and preferences must be the user's words.
- **Rules need independent tasks, and lessons need an A/B win to be promoted.**
- **Practice runs in random temporary folders.** Global package installs are blocked there, dependency folders are linked and unlinked rather than copied, and the folders are deleted after grading.
- **Snapshots never touch your branch, index or stash list** (`git stash create`).
