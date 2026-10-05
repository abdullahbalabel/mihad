# Usage

## Daily workflow

After `mihad-install`, use your agent (OMP, Claude Code or Codex) in the project as you normally would. MIHAD works around each request.

1. **Brief.** At the start of each request the agent receives a short brief. It includes:
   - the verified memory items, with your preferences first;
   - lessons relevant to the functions the request names;
   - past fixes of those functions;
   - the available skills.

   A function the project has fixed many times gets one line; one it has never seen gets more.
2. **Live warnings.** While the agent works, known tool pitfalls are flagged the moment they recur. So are a command failing twice in the same way and the same file being re-read again and again. Each warning fires at most once per session.
3. **Review before finishing.** When the agent stops, the engine reviews its actual change. It checks for:
   - regressions caught by verifier scripts from past fixes;
   - files the user always changes together;
   - stub and `__all__` consistency (Python);
   - whether tests ran after the last edit;
   - executable checks on the change itself: lines no test covers (each added line is broken on a copy and the tests rerun), lost formatting or new lint findings, checkable preferences, and single-pass iterators (Python).

   If it finds something, the agent gets one more turn; otherwise it stays silent.
4. **Your commit is the lesson.** Every session is recorded. When you commit afterwards, your commit is treated as the final version, and what you changed after the agent becomes experience. Commit as usual; there is nothing extra to do.

### Preferences

State a standing preference in any message, in plain words, starting the sentence with *Always*, *Never*, *From now on* or *In future*:

> Fix the date parser. Always add a regression test for every bug you fix.

The sentence is captured verbatim, adopted because it is your own words, and shown in every later brief as "stated by the user — follow it". Instructions scoped to one task ("for this task only", "just this time") are never stored. A preference can only be corrected or deleted by you, through `mihad-memory`.

## Seeing what was learned

```bash
mihad report      # lessons and their status, verifier scripts, skills, competence, memory, dream cycles
mihad status      # counts only
```

Lesson status:

| Status | Meaning |
|---|---|
| `adopted` | Passed its evidence gate (for example, seen in two independent tasks) |
| `promoted` | Also won an A/B test while dreaming; ranked first in briefs |
| `retired` | Lost an A/B test; no longer used |

The memory:

```bash
mihad-memory list                    # all items with kind, status, version and use count
mihad-memory list --status adopted
mihad-memory show <id>               # content, evidence and everything derived from it
mihad-memory approve <id>            # approve a provisional lesson
mihad-memory correct <id> "reason"   # mark wrong; suspends it and its derivatives
mihad-memory delete <id> "reason"    # remove it and its derivatives from retrieval
mihad-memory stats
```

## Dreaming

A dream cycle learns from new sessions and then practises. It runs these steps:

1. Ingest the sessions you have committed after.
2. Mine lessons, skills and competence.
3. Write verifier scripts for new fixes. A script is kept only if it fails before the fix and passes after.
4. Make practice tasks by breaking those fixes slightly.
5. Run each practice task twice in OMP, with the lessons and without them.
6. Promote or retire lessons by the result, and learn from the practice sessions too.

```bash
mihad dream-cycle              # one cycle, in this window
mihad dream-cycle --tasks 2    # fewer practice tasks
```

Practice sessions open in their own visible windows by default (`dream.visible`). Only one cycle runs at a time.

**Cost.** Each practice task is two agent sessions (with `dream.model`, Haiku by default), and each new verifier script is one call to a stronger model. With the default of 4 tasks a cycle uses about 8 sessions. Automatic dreaming is off until you enable it.

To dream automatically in the background every N closed sessions, set this in `.mihad/project.json`:

```json
"dream": { "auto_after_sessions": 5 }
```

A cycle needs code with conditions or arithmetic to mutate. Code that only chains calls gives no practice tasks.

## Configuration

`.mihad/project.json` is created by the installer from what it detects. Every key can be edited.

| Key | Default | Meaning |
|---|---|---|
| `name` | folder name | Project name used in prompts |
| `language` | detected | `python`, `typescript`, `javascript`, `java`, `kotlin`, `csharp`, `go`, `rust`, `php`, `ruby`, `cpp` |
| `source_dirs` | detected | Folders with the code under work |
| `test_dirs` | detected | Folders with tests (tests beside the code are found too) |
| `test_runner` | detected | `pytest`, `unittest`, `node`, `jest`, `vitest`, `mocha`, `npm`, `maven`, `gradle`, `dotnet`, `go`, `cargo`, `phpunit`, `rspec`, `minitest`, `ctest` |
| `test_command` | detected | The full test command |
| `experience_dir` | `.mihad/experience` | The engine's store |
| `memory_db` | `.mihad/memory.db` | The memory database |
| `user_log` | `.mihad/user_messages.txt` | Your messages: the evidence for preferences |
| `checks` | `true` | Executable checks at review: untested lines, formatting and lint, checkable preferences, single-pass iterators. Projects installed with 1.1 have `false` written in their config; set it to `true` |
| `conscience_model` | none | A model stronger than the agent's (e.g. `anthropic/claude-sonnet-5-5`) turns on the conscience: it speaks up after repeated mistakes. Calls a paid model |
| `conscience_memory` | `false` | Also give the conscience the standing preferences and past resolutions of the same errors. Did not help in the experiment (43/63 vs 44/63, +19% cost) |
| `conscience_max` | `10` | At most this many conscience notes per session |
| `edges` | `false` | Add a generic edge-case checklist to the review. Off because it added cost without raising success |
| `advisor` | `false` | Let a stuck session consult a stronger model once |
| `advisor_model` | `anthropic/claude-sonnet-5` | The advisor's model |
| `review_checkers` | on for Python and JS/TS | Run verifier scripts at review; slow for compiled languages |
| `dream.agent` | `omp` | Agent for practice sessions: `omp`, `claude` or `codex` (see [Agents](AGENTS.md)) |
| `dream.auto_after_sessions` | `0` (off) | Start a background dream cycle every N sessions |
| `dream.tasks_per_cycle` | `4` | Practice tasks per cycle |
| `dream.model` | `anthropic/claude-haiku-4-5` | Model for practice sessions |
| `dream.max_time` | `15m` | Time limit per practice session |
| `dream.visible` | `true` | Open practice sessions in visible windows |
| `agent_commands` | none | Paths to agent commands not on PATH, e.g. `{"codex": "C:/…/codex.exe"}` |
| `python`, `mihad_root` | set by the installer | Where the tool runs from; refreshed on reinstall |

Memory retrieval can be tuned with environment variables on the MCP server in `.omp/mcp.json`:

| Variable | Default | Meaning |
|---|---|---|
| `MIHAD_RECALL_K` | `3` | At most this many items per recall |
| `MIHAD_RECALL_RELATIVE` | `0.45` | Drop items scoring below this fraction of the top item |
| `MIHAD_RECALL_DISTINCTIVE` | `0.25` | A query word must be this rare to count as distinctive |
| `MIHAD_POLICY` | `verified` | `verified`, `store_all` (no gate) or `off` |

## Skills

Two skills are compiled from repeated command sequences once they have been verified on your past fixes. The agent calls them through the `experience_skill` tool, and you can call them too:

```bash
mihad skill list
mihad skill run test_symbol --arg symbol=parse_date   # tests that exercise one function
mihad skill run check_change                          # focused tests for every changed function, then the suite
```

## All engine commands

| Command | What it does |
|---|---|
| `mihad report` / `status` | What was learned / counts |
| `mihad dream-cycle` | A full learning and practice cycle |
| `mihad live-ingest` | Turn sessions you have committed after into episodes now |
| `mihad mine` | Re-mine lessons, skills, competence from all episodes |
| `mihad checkers` | Write verifier scripts for episodes without one |
| `mihad dream-make` / `dream-eval RUN` | Make practice tasks / evaluate a practice run |
| `mihad brief`, `detect`, `review` | Called by the extension during sessions |
| `mihad skill list` / `run` | Verified skills |

## Troubleshooting

- **No brief appears.** Check that `.omp/settings.json` lists the extension, and that `.mihad/project.json` exists in the project or a parent folder. Re-run `mihad-install`.
- **"No test found that uses X."** The skill finds tests by the function name. Check `test_dirs` and `test_runner` in the config.
- **A dream cycle makes no practice tasks.** It needs adopted verifier scripts, which need committed fixes. It also needs functions with conditions or arithmetic. Run `mihad report` to see what exists.
- **"another cycle is running".** A lock file `.mihad/experience/dream.lock` is removed when the cycle ends. If a cycle crashed, it expires after six hours, or you can delete it.
