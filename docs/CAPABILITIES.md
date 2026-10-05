# Capabilities

Each part below says what it does, how it decides what to trust, and what the experiments showed about it. Numbers come from the research paper ([paper/](../paper/MIHAD_Research_Paper_EN_v2.7.md)); all are exploratory, with one run per cell except the executable-checks experiment (three repetitions).

## 1. Verified memory (MCP server)

The agent gets four tools: `memory_recall`, `memory_propose`, `memory_correct`, `memory_report_outcome`. Every proposal is a candidate that is adopted only through the check for its kind:

| Kind | Adopted when | Otherwise |
|---|---|---|
| Skill | The command passes the project's existing tests, and those test files are unchanged since the session's starting commit | Rejected or provisional |
| Fact | Its quotation exists in the file as it was at the session's starting commit (not in text the agent just wrote) | Provisional |
| Lesson | You approve it (`mihad-memory approve`) | Provisional |
| Preference | It quotes your own words verbatim from your messages | Rejected |

- **Retrieval is strict.** At most three items are returned, only if a distinctive word of the request matches, and only items close to the best match. Repeated paraphrases count once. Returning nothing is preferred to returning near misses.
- **Corrections cascade.** Correcting or deleting an item suspends everything derived from it.
- **Facts are re-checked when recalled.** A fact whose quotation no longer exists in the code is suspended automatically.
- **Evidence:** wrong items deliberately planted in memory were never followed on real commits. A strong agent gained no success from code knowledge in memory and paid 27–59% more tokens. That is why the engine below focuses on operational experience instead.

## 2. Preference capture

Your standing preferences are taken from your own messages, verbatim, sentence by sentence, and adopted at once with the label "stated by the user — follow it". Instructions limited to one task are skipped. The agent cannot suspend a preference; only you can correct or delete it.

- **Evidence:** 15/15 later tasks followed the captured preferences, against 0/15 without capture. This is the clearest effect in the research.

## 3. Brief at the start of each request

The brief is a short block with:

- your preferences and verified memory items;
- past fixes of the functions the request names;
- lessons for those functions' files;
- the tool pitfalls this model is prone to;
- the available skills.

It is sized by competence: a function never fixed before gets the most guidance, one fixed twice at low cost gets one line. Tool warnings stop once the model's tool error rate falls below 5% over at least three sessions.

## 4. Live detection during the session

On every tool result the engine checks for:

- a known failure path (an error signature seen in at least two past tasks, with what worked next);
- the same command failing twice;
- the same file read four times;
- four failed tool calls in a row.

Each warning is sent once per session, as context for the next step.

- **Evidence:** a promoted pitfall (how to address a line range when reading a file, seen in 18 of 20 past tasks) saved 29.5% of tokens on average in A/B practice. With the engine the weaker model made fewer tool errors (39 of 518 steps vs 51 of 536).

## 5. Review before the agent finishes

When the agent stops, the engine looks at its actual change. It checks for:

- regressions caught by verifier scripts from past fixes (scripts that pass at the starting commit but fail now);
- files your commits always change together;
- for Python, a new public function missing from `__all__` or from the `.pyi` stub, or a changed signature with an unchanged stub;
- whether tests ran after the last edit.

**Executable checks** (`checks`, on by default) run the agent's change instead of reading it. Each finding is something that actually happened:

- **Mutation adequacy.** On a temporary copy of the project, each line the agent added is broken in turn: a condition is negated, a statement is removed, an operator is changed. If the tests still pass, that line is untested, and the finding names it: *if `return count + 1` at more.py:2441 became `pass`, all tests would still pass.* Tests that name the function are run; if none does, the full suite.
- **Format and lint.** A file that matched the project's formatter at the starting commit and no longer does, or new lint findings in it.
- **Preference compliance.** A preference that can be checked, such as "add a regression test for every bug you fix": a new test must exist and fail on the old code.
- **Single-pass iterators** (Python). A function taking an `iterable` is called with a one-shot iterator; reported only if it worked at the starting commit and fails now.

At most four findings are reported.

With findings, the agent gets one more turn; without, nothing is said.

- **Evidence (experiment engine3, 42 runs per arm, Haiku):**

  | | Success | Judge (overall) | Format regressions | Cost |
  |---|---|---|---|---|
  | No memory | 32/42 | 3.71 | 7 | $10.41 |
  | Engine | 32/42 | 3.74 | 10 | $8.80 |
  | Engine with checks | **35/42** | **3.98** | **0** | $8.98 |

  The agents fixed 13 of 14 format findings. Only one of the three extra successes is linked to a finding, and property tests show that solution is only partly correct. The judge's higher score follows the extra successes: among passing runs the arms differ by at most 0.11. The firm gain is formatting. Whether untested-line findings were resolved was not measured, because the review was not re-run after the agent's reply; closing that loop is the next step.

## 6. Learning from your corrections

Each session is recorded with a snapshot of the working tree at its start and end. Once you commit, your commit is the final version:

- what you changed in the agent's files is recorded as a correction;
- the functions your commit touched become the episode's family;
- rules (for example "these two files change together") are adopted only when two independent tasks support them.

## 7. Verifier scripts

For each fix you commit, a stronger model writes a small check of the fixed behaviour. In Python it is a standalone script; in other languages it is a test file in your own framework. The check is adopted **only if it fails on the code before the fix and passes after it**. Adopted checks guard against regressions at review time and grade practice tasks.

- **Evidence:** 20 of 20 scripts passed the gate on real fixes. In two cases the first script was rejected because it did not separate the two versions, and a second attempt passed.

## 8. Dreaming: practice with A/B tests

Practice tasks are made by applying a small mutation to a function you fixed, for example `<` to `<=`, `==` to `!=`, or `&&` to `||`. A mutant is kept only if both its verifier and your real tests catch it; mutants that hang are rejected. Each task runs in OMP twice, with lessons and without. A lesson is:

- **promoted** if the sessions where it fired kept success and cost at least 10% less over at least two pairs;
- **retired** if success dropped or cost rose by 25% or more.

- **Evidence:** six practice pairs: 1.87M tokens with lessons vs 3.26M without (−43%), with every session successful.

## 9. Compiled skills

Command sequences that past sessions repeated by hand are compiled into two skills, `test_symbol` and `check_change`. Each is adopted only after it runs successfully on your final version of two past fixes.

## 10. Competence record

The engine keeps a record per function family (novice, competent, mastered), from attempts, successes and cost, and a record of tool competence per model. Scaffolding fades as competence grows.

## The conscience (optional)

A stronger model that speaks up when the agent keeps making mistakes, like a teacher who steps in when a student
repeats errors, not at every line:

- **When:** after two mistakes since its last word (a failed command or tool call, or the agent's own check printing a
  mismatch). The same mistake twice means stuck on a wrong idea; different ones may mean a wrong reading of the task.
- **What it reads:** the task, the change so far, and the last command with its output, about 5,000 tokens, never the
  whole transcript.
- **How:** in the background; its note reaches the agent with the next tool result. At most `conscience_max` (10) times
  per session.
- **Turn it on:** set `conscience_model` in `.mihad/project.json` to a model stronger than the agent's, e.g.
  `"anthropic/claude-sonnet-5-5"`. It calls that model, so it costs money; off by default.
- **Evidence (bidict, Haiku as the agent, 63 runs per arm):** no second model 37/63; OMP's advisor reviewing every turn
  45/63 at 3.5x the cost; the conscience **44/63 at about 1.3x** the cost; the conscience with Haiku itself 38/63 (no gain).

## Optional parts (off by default)

- **Edge-case checklist** (`edges`). Kinds of edge cases mined from your verifier scripts, added to the review.
- **Advisor** (`advisor`). One consultation of a stronger model when a session is stuck: tests failing repeatedly, a long session with no passing test, or an error streak.

**Why they are off:** in the experiment they did not raise success (10/14), cost 25% more than the engine without them, and lowered the quality score. The checklist was generic, nearly the same in every session. The advisor diagnosed the right lines once, but the weaker model still failed.

## What it does not do

- It does not make a model solve problems it cannot solve. In the experiments, no version fixed the tasks the model already failed, which were about unstated intent and edge cases.
- It does not change model weights.
- Function lookup outside Python uses declaration patterns, not a compiler. It finds the changed function in ordinary code but can err on unusual code.
- Runtime support for Java, Kotlin, C#, Go, Rust, PHP, Ruby and C/C++ has been tested at the analysis level only.
