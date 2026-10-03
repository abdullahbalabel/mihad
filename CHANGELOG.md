# Changelog

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
