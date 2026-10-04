# Research summary

The full paper is [paper/MIHAD_Research_Paper_EN_v2.0.md](../paper/MIHAD_Research_Paper_EN_v2.0.md); a Word version is in the same folder.

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

## Limits

- One main repository (more-itertools), and one run per cell except the executable-checks experiment (three repetitions). A difference of one task, or of 10–15% in cost, may be noise.
- The quality judge is a language model not yet calibrated against human ratings.
- Only the executable checks solved a task that every earlier version failed (once in three). The task with a nearly empty description was never solved.
- The generalized tool has been tested live only on small sample projects.

## Integrity

Every protocol and its decision rule were committed before the run. Faults and mistakes, including the author's own, are logged with their cause and fix.

One contaminated session was found during the work: an agent entered another arm's workspace and installed it globally. The session was removed and rerun in isolation, which lowered one reported result from 11/14 to 10/14, and workspaces were isolated from then on.
