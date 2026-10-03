"""Human-side CLI: inspect, approve, correct and delete memory items.

    python -m mihad_memory.cli --project PATH list [--status adopted]
    python -m mihad_memory.cli --project PATH show ID
    python -m mihad_memory.cli --project PATH approve ID
    python -m mihad_memory.cli --project PATH correct ID "reason"
    python -m mihad_memory.cli --project PATH delete ID "reason"
    python -m mihad_memory.cli --project PATH stats
"""
import argparse
import json
import os
import sys

from .service import Memory


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mihad-memory")
    ap.add_argument("--project", default=os.environ.get("MIHAD_PROJECT", os.getcwd()))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--status")
    p = sub.add_parser("show"); p.add_argument("id")
    p = sub.add_parser("approve"); p.add_argument("id")
    p = sub.add_parser("correct"); p.add_argument("id"); p.add_argument("reason")
    p = sub.add_parser("delete"); p.add_argument("id"); p.add_argument("reason", nargs="?", default="")
    sub.add_parser("stats")
    a = ap.parse_args(argv)
    # The CLI acts as the human reviewer, always under the verified policy.
    m = Memory(a.project, policy="verified", session_id="human-cli")
    out = sys.stdout
    if a.cmd == "list":
        for r in m.store.records([a.status] if a.status else None):
            out.write(f"{r['id']}  {r['kind']:6} {r['status']:11} v{r['version']} used={r['use_count']}  {r['title']}\n")
    elif a.cmd == "show":
        rec = m.store.get(a.id)
        if not rec:
            sys.exit(f"unknown id {a.id}")
        rec["evidence"] = m.store.evidence(a.id)
        rec["descendants"] = sorted(m.store.descendants(a.id))
        out.write(json.dumps(rec, ensure_ascii=False, indent=2) + "\n")
    elif a.cmd == "approve":
        m.approve(a.id, by=os.environ.get("USERNAME", "human"))
        out.write(f"approved {a.id}\n")
    elif a.cmd == "correct":
        out.write(json.dumps(m.correct(a.id, a.reason, by_human=True), ensure_ascii=False) + "\n")
    elif a.cmd == "delete":
        out.write(json.dumps(m.delete(a.id, a.reason), ensure_ascii=False) + "\n")
    elif a.cmd == "stats":
        out.write(json.dumps(m.status(), ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
