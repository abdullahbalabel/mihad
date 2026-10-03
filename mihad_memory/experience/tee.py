"""Run a command, show its output in this console and save it to a file (UTF-8).

    python -m mihad_memory.experience.tee OUTFILE COMMAND [ARG ...]

Used for visible practice sessions of print-mode agents (Claude Code -p, Codex exec): the user can
watch the session while the engine keeps the event log. Arguments go to the program as a list, so
quotes inside them (for example Codex -c overrides) arrive intact.
"""
import subprocess
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out, cmd = argv[0], argv[1:]
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with open(out, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace")
            fh.write(line)
            fh.flush()
            sys.stdout.write(line)
            sys.stdout.flush()
        return proc.wait()


if __name__ == "__main__":
    sys.exit(main())
