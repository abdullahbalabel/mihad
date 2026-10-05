# Research summary

The full paper is [paper/MIHAD_Research_Paper_EN_v2.4.md](../paper/MIHAD_Research_Paper_EN_v2.4.md); a Word version is in the same folder.

## The question

Can a coding agent be given memory and experience that make it better over time, without it learning and spreading its own mistakes?

## Main findings

1. **Adopt only with independent evidence.** In the evidence-independence test the gate exceeded the strongest of four required baselines by 12.8 points (92.4% vs 79.6%). It matched origin-level aggregation, which is a known method, so the contribution is bringing that method into agent-memory admission. On real commits, wrong items planted in memory were never followed.
2. **Preferences need capture, not storage.** When the agent was left to save preferences itself, nothing carried over (0/15). Capturing your verbatim words, labelled "stated by the user — follow it", carried them into every later task (15/15), and one-off instructions did not leak.
3. **Code knowledge does not transfer.** On fourteen newer, different tasks, verified memory and project notes left success unchanged (11/14 and 10/14 vs 11/14) and cost 16–17% more.
4. **Operational experience does.** The experience engine kept success (11/14) at 14.4% lower cost with no quality loss (judge 3.93 vs 3.79). In A/B practice its lessons cut tokens by 43%.
5. **Generic advice is noise.** An edge-case checklist plus an advisor gave 10/14, cost more and lowered quality.
6. **Observed facts help.** Executable review checks run the agent's change: each added line is mutated to see whether the tests notice, and formatting, lint and preferences are checked against the starting commit. Over three repetitions (42 runs per arm) they raised success to 35/42 against 32/42 and cut format regressions from 10 to 0, for 2% more cost. The judge's score rose (3.98 vs 3.74) only because of the extra successes; among passing runs the arms differ by at most 0.11. Only one extra success is linked to a finding, and property tests show it is only partly correct, so the firm gain is formatting.

7. **Stronger checks do not generalize.** Property templates, a contract ontology and rules learned from the project's history were tested on two repositories the design had not seen (bidict with blindly chosen tasks). They raised no false alarms, but caught almost none of the failures there (0 of 47 on bidict) and did not raise success, at 11–48% more cost. On the data they were designed from they caught 23 of 24, which is why every mechanism is now evaluated on an unseen repository. They stay off.

8. **A stronger second model helps; a well-timed one is affordable.** With Haiku as the agent, OMP's built-in advisor (Sonnet 5.5 reviewing every turn) raised success from 37 to 45 of 63 at 3.5 times the cost. The **conscience**, which speaks up only after repeated mistakes and reads a short context, reached 44 of 63 at about 37% of the advisor's cost. With Haiku itself as the conscience there was no gain (38 of 63): the second voice must know more than the agent.
9. **Task text does not predict failure.** Local decision models (Qwen3-4B, OpenDecider) could not tell from a task's description which tasks the agent would fail (best AUC 0.60).

10. **More for the conscience to read did not help.** Giving the conscience the user's standing preferences and how the same errors were resolved before gave 43/63 against 44/63, with longer sessions and 19% more cost. Its notes spent corrections on reminders the agent did not need: stating the preferences clearly in the task had already raised compliance from 40% to 90%. The option stays off.

11. **A strong agent beats a cheap agent with a conscience on success, not on cost.** With the preferences stated, Sonnet 5.5 as the agent reached 51/63 at about $1.41 per success; Haiku with the conscience reached 44/63 at about $1.22 per success. The conscience is the economical road; a strong agent is the better one where its cost is acceptable. A final reading by the conscience when the agent finishes gave precise notes but only 45/63, late for a slow agent; it stays off.

12. **The value of the system shrinks as the model grows stronger.** Sonnet 5.5 as the agent reached 50/63 without MIHAD and 51/63 with it, at nearly the same cost (97.6%); MIHAD added about four minutes per session. A strong agent already writes the test, re-runs it and avoids tool slips. Not measured for strong agents: preferences and corrections carried between sessions.

## Limits

- One main repository (more-itertools), and one run per cell except the executable-checks experiment (three repetitions). A difference of one task, or of 10–15% in cost, may be noise.
- The quality judge is a language model not yet calibrated against human ratings.
- Only the executable checks solved a task that every earlier version failed (once in three). The task with a nearly empty description was never solved.
- The generalized tool has been tested live only on small sample projects.

## Integrity

Every protocol and its decision rule were committed before the run. Faults and mistakes, including the author's own, are logged with their cause and fix.

One contaminated session was found during the work: an agent entered another arm's workspace and installed it globally. The session was removed and rerun in isolation, which lowered one reported result from 11/14 to 10/14, and workspaces were isolated from then on.
