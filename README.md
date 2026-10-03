# MIHAD Developmental Memory

**ذاكرة مهاد النمائية** — verified memory and an experience engine for coding agents.

Coding agents start every session like a new hire on day one: they forget yesterday's corrections and repeat the same mistakes. MIHAD gives an agent a memory and a body of experience that grow with use, without letting it learn and spread its own errors.

- **Memory that only adopts what passed an independent check.** A skill must pass the project's own tests; a fact must quote the code as it was before the session; a preference must be the user's own words. Everything else stays provisional.
- **Your preferences, kept.** Say "Always add a regression test" once, in any message, and it is captured verbatim and followed in every later session.
- **An experience engine.** It learns operational experience from finished sessions (tool pitfalls, the corrections you make after the agent, verified skills, verifier scripts), briefs the agent at the start of each request, warns it live, and reviews its change before it finishes.
- **Dreaming.** Between sessions it practises on mutated versions of past fixes, with and without its lessons, and keeps a lesson only if the A/B test shows it helps.
- **Ten languages.** Python, JavaScript/TypeScript, Java, Kotlin, C#, Go, Rust, PHP, Ruby, C/C++.
- **Three agents.** [OMP](https://www.npmjs.com/package/@oh-my-pi/pi-coding-agent), Claude Code (terminal and desktop) and Codex, with live warnings and a mandatory review in each, through an extension or hooks.
- **No dependencies.** Standard-library Python.

The design and every experiment behind it are in the research paper: [paper/MIHAD_Research_Paper_EN_v1.8.md](paper/MIHAD_Research_Paper_EN_v1.8.md).

## What the experiments showed

| Finding | Evidence |
|---|---|
| Captured user preferences carried into every later task | 15/15 vs 0/15 without the capture |
| Wrong items planted in memory were never followed | Two contaminated-memory runs on real commits |
| Knowledge *about the code* did not transfer to different tasks | Memory and notes: same success, +16–17% cost |
| *Operational* experience did transfer | Experience engine: same success (11/14), 14.4% fewer tokens, blind quality score 3.93 vs 3.79 |
| Generic advice in every session is noise | Edge-case checklist + advisor: 10/14, more cost, lower quality — now off by default |

All results are exploratory (one run per cell, one main repository). See the paper for methods and limits.

## Quick start

Requirements: Python 3.12+, git, and at least one of OMP, Claude Code or Codex.

```bash
git clone https://github.com/abdullahbalabel/mihad.git
cd mihad
python -m pip install -e .
```

Install it into one of your projects (a git repository). Preview first, then install:

```bash
mihad-install D:/path/to/your/project --dry-run
mihad-install D:/path/to/your/project                    # OMP
mihad-install D:/path/to/your/project --agent claude     # Claude Code
mihad-install D:/path/to/your/project --agent codex      # Codex (trust the project in Codex once)
```

Then work with your agent inside the project as usual. To see what the engine learned and what the memory kept:

```bash
mihad report
mihad-memory list
```

Full guides:

- [Installation](docs/INSTALL.md): requirements, install, uninstall, what is written where
- [Usage](docs/USAGE.md): daily workflow, commands, configuration, dreaming
- [Agents](docs/AGENTS.md): OMP, Claude Code and Codex: what each gets, setup, what has been verified
- [Capabilities](docs/CAPABILITIES.md): every part, what it does and the evidence behind it
- [Architecture](docs/ARCHITECTURE.md): modules, data files and how the parts connect
- [Research](docs/RESEARCH.md): summary of the findings, with links to the paper

## Language support

| Language | Function lookup | Test runners | Verified live |
|---|---|---|---|
| Python | AST | pytest, unittest | Yes |
| JavaScript / TypeScript | Declaration scanner | node --test, Jest, Vitest, Mocha | Yes |
| Java / Kotlin | Declaration scanner | Maven, Gradle | Analysis tested; runtime not yet |
| C# | Declaration scanner | dotnet test | Analysis tested; runtime not yet |
| Go | Declaration scanner | go test | Analysis tested; runtime not yet |
| Rust | Declaration scanner | cargo test | Analysis tested; runtime not yet |
| PHP / Ruby | Declaration scanner | PHPUnit, RSpec, Minitest | Analysis tested; runtime not yet |
| C / C++ | Declaration scanner | CTest, make test | Analysis only; no verifier scripts |

## نظرة عامة بالعربية

«ذاكرة مهاد النمائية» أداة تعطي وكيل البرمجة ذاكرة وخبرة تنموان مع الاستعمال، دون أن يتعلم أخطاءه:

- **ذاكرة لا تعتمد إلا ما له دليل مستقل:** اختبارات المشروع للمهارة، ونص الكود قبل الجلسة للحقيقة، وكلامك الحرفي لتفضيلاتك.
- **تفضيلاتك تُحفظ:** تقولها مرة في أي رسالة، فتُلتقط بنصها وتُتبع في كل جلسة لاحقة.
- **محرك خبرة:** يتعلم من جلساتك ومن تصحيحاتك بعد الوكيل، ويوجز للوكيل في بداية كل طلب، وينبهه أثناء العمل، ويراجع تغييره قبل أن ينهي.
- **الحلم:** بين الجلسات يتدرب على نسخ معدلة من إصلاحات سابقة، بالدروس وبدونها، ولا يبقي درسًا إلا إن أثبتت التجربة فائدته.

التثبيت بأمر واحد على أي مشروع، والإرشادات الكاملة في مجلد `docs`، والبحث الكامل في مجلد `paper`.

## License

Copyright © 2026 Abdullah Mohammed Balabel.

Licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE): free for research, personal and other non-commercial use. Commercial use requires a separate licence from the author.

## Citation

If you use MIHAD in research, please cite the paper (see [CITATION.cff](CITATION.cff)):

> Balabel, A. M. (2026). *Adopting Reasoning Outputs Only After Verification: The MIHAD Architecture, a Verified Memory and an Experience Engine for Coding Agents* (Version 1.8).
