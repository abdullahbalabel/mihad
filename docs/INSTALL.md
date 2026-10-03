# Installation

## Requirements

- **Python 3.12 or newer.** The tool uses only the standard library.
- **git.** Your project must be a git repository: the memory checks evidence against the commit a session started from, and the engine learns from your commits.
- **OMP coding agent.** The `omp` command must be installed and signed in. The memory works with any MCP client, but the experience engine's live part is an OMP extension.
- **Your project's own test tool,** for the languages you use, for example `pytest`, `node`, `mvn`, `dotnet`, `go` or `cargo`. The engine runs your tests; it does not install them.

## Install the tool

```bash
git clone https://github.com/abdullahbalabel/mihad.git
cd mihad
python -m pip install -e .
```

`-e` (editable) keeps the tool where you cloned it, so `git pull` updates it. This adds four commands:

| Command | What it is |
|---|---|
| `mihad-install` | Installs or removes MIHAD in a project |
| `mihad` | The experience engine: report, dream cycle, skills, status |
| `mihad-memory` | Inspect, approve, correct and delete memory items |
| `mihad-mcp` | The memory's MCP server (OMP starts it for you) |

Without pip you can run the same modules directly with `PYTHONPATH` pointing to the clone, for example `python -m mihad_memory.install`.

## Install into a project

Preview what will be written, then install:

```bash
mihad-install D:/path/to/project --dry-run
mihad-install D:/path/to/project
```

The installer detects the project's language, source and test folders, test runner and test command, and prints them. Check them; you can change anything in `.mihad/project.json` afterwards.

### What is written, and where

Everything stays inside the project, and none of it is tracked by git.

| Path | Content |
|---|---|
| `.mihad/project.json` | Detected settings; edit freely (see [Usage](USAGE.md#configuration)) |
| `.mihad/memory.db` | The memory (SQLite), created on first use |
| `.mihad/experience/` | The engine: episodes, lessons, verifier scripts, skills, dreams |
| `.mihad/user_messages.txt` | Your messages to the agent: the evidence for your preferences |
| `.mihad/backup/` | Copies of any `.omp` file or config the installer changed |
| `.omp/mcp.json` | Adds the `mihad_memory` server; your other servers are kept |
| `.omp/settings.json` | Adds the experience extension; your other extensions are kept |
| `.git/info/exclude` | Lists `.mihad/` and any `.omp` file the installer created |

Running the installer again is safe. It keeps your settings and only refreshes where the tool and its Python live, which matters after you move or upgrade the tool.

## Check that it works

Start `omp` in the project and give it a small task. At the start you should see a message titled **Experience from earlier work on this project**, and the tools `experience_review` and `experience_skill` should be available. After the session:

```bash
cd D:/path/to/project
mihad report          # what the engine recorded and learned
mihad-memory list     # what the memory holds
```

## Uninstall

```bash
mihad-install D:/path/to/project --uninstall            # remove the OMP entries, keep the data
mihad-install D:/path/to/project --uninstall --purge    # also delete .mihad/ (memory and experience)
```

Your other `.omp` entries are left as they are, and the originals are kept in `.mihad/backup/` unless you purge.

## Safety notes

- Practice sessions (dreaming) run in random temporary folders outside your project and are deleted afterwards. Your working tree is never edited by a dream.
- Session snapshots use `git stash create`, which writes no branch, index or stash-list entry.
- Agent processes started by the engine run with `PIP_REQUIRE_VIRTUALENV=true`, so they cannot install packages into your global Python.
- Installed dependencies (`node_modules`, `vendor`) are linked into practice folders, not copied, and unlinked before deletion.
