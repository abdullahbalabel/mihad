# Research summary

The full paper is [paper/MIHAD_Research_Paper_EN_v1.8.md](../paper/MIHAD_Research_Paper_EN_v1.8.md); a Word version is in the same folder.

## The question

Can a coding agent be given memory and experience that make it better over time, without it learning and spreading its own mistakes?

## Main findings

1. **Adopt only with independent evidence.** In the evidence-independence test the gate exceeded the strongest of four required baselines by 12.8 points (92.4% vs 79.6%). It matched origin-level aggregation, which is a known method, so the contribution is bringing that method into agent-memory admission. On real commits, wrong items planted in memory were never followed.
2. **Preferences need capture, not storage.** When the agent was left to save preferences itself, nothing carried over (0/15). Capturing your verbatim words, labelled "stated by the user — follow it", carried them into every later task (15/15), and one-off instructions did not leak.
3. **Code knowledge does not transfer.** On fourteen newer, different tasks, verified memory and project notes left success unchanged (11/14 and 10/14 vs 11/14) and cost 16–17% more.
4. **Operational experience does.** The experience engine kept success (11/14) at 14.4% lower cost with no quality loss (judge 3.93 vs 3.79). In A/B practice its lessons cut tokens by 43%.
5. **Generic advice is noise.** An edge-case checklist plus an advisor gave 10/14, cost more and lowered quality.

## Limits

- One main repository (more-itertools) and one run per cell. A difference of one task, or of 10–15% in cost, may be noise.
- The quality judge is a language model not yet calibrated against human ratings.
- None of the versions improved the model's ability on the tasks it already failed.
- The generalized tool has been tested live only on small sample projects.

## Integrity

Every protocol and its decision rule were committed before the run. Faults and mistakes, including the author's own, are logged with their cause and fix.

One contaminated session was found during the work: an agent entered another arm's workspace and installed it globally. The session was removed and rerun in isolation, which lowered one reported result from 11/14 to 10/14, and workspaces were isolated from then on.
