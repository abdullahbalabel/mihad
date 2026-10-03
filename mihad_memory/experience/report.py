"""A readable report of what the engine has learned and what it keeps: for the user, not the agent."""
import time
from collections import Counter

from .. import project
from .checkers import adopted
from .common import read_jsonl


def _memory_section(engine):
    """What the project memory (the MCP server's database) holds, if this engine belongs to a project."""
    repo = (engine.get("config", {}) or {}).get("repo")
    if not repo:
        return []
    cfg = project.load(repo)
    db = project.path_in(cfg, "memory_db")
    if not db.exists():
        return []
    from ..store import Store
    store = Store(str(db))
    try:
        recs = store.records()
    finally:
        store.close()
    out = [f"\n## Project memory: {len(recs)} items\n\nOnly adopted items reach the agent as trusted.\n"]
    for status in ("adopted", "provisional", "suspended", "rejected", "superseded", "deleted"):
        rs = [r for r in recs if r["status"] == status]
        if rs:
            out.append(f"### {status} ({len(rs)})\n")
            out += [f"- [{r['kind']}] {r['title']}" for r in rs[:30]]
    return out


def render(engine):
    eps = engine.episodes()
    lessons = engine.lessons()
    fired = Counter()
    for rec in read_jsonl(engine.root / "fired.jsonl"):
        fired.update(rec["ids"])
    out = [f"# What the experience engine learned\n\nEngine: `{engine.root}`\n"]
    out.append("## Sessions it learned from\n")
    by_source = Counter(e["source"] for e in eps)
    out += [f"- {src}: {n} sessions, {sum(1 for e in eps if e['source'] == src and e['hidden_pass'])} succeeded"
            for src, n in sorted(by_source.items())]
    out.append("\n## Lessons\n\nStatus: adopted = passed its evidence gate; promoted = also won an A/B test; "
               "retired = lost one.\n")
    for kind in ("failure", "edge_case", "co_change"):
        ls = [l for l in lessons if l["kind"] == kind]
        if not ls:
            continue
        out.append(f"### {kind.replace('_', ' ')} ({len(ls)})\n")
        for l in sorted(ls, key=lambda l: (l["status"] != "promoted", -len(l["support"]))):
            ab = l.get("ab") or {}
            ab_txt = (f"; A/B: {len(ab.get('pairs', []))} pairs, token gain {ab['token_gain']:+.0%}, pass diff "
                      f"{ab['pass_diff']:+d}") if "token_gain" in ab else ""
            out.append(f"- **{l['status']}** `{l['id']}` (evidence: {len(l['support'])} tasks{ab_txt}; "
                       f"used in {fired[l['id']]} sessions)\n  {l['text']}")
    chk = adopted(engine)
    out.append(f"\n## Verifier scripts: {len(chk)} adopted\n\nEach fails on the code before a past fix and passes "
               "after it; they guard against regressions.\n")
    out += [f"- `{tid}`: {', '.join(r['families']) or '-'} (attempts: {len(r.get('attempts', []))})" for tid, r in sorted(chk.items())]
    skills = (engine.get("skills", {}) or {}).get("skills", {})
    out.append("\n## Skills\n")
    out += [f"- **{v['status']}** `{k}`: {v['description']} (seen in {len(v['support'])} tasks, verified on "
            f"{len(v['verified_on'])})" for k, v in skills.items()]
    comp = engine.get("competence", {}) or {}
    fams = comp.get("families", {})
    out.append("\n## Competence\n")
    for level in ("mastered", "competent", "novice"):
        names = sorted(f for f, r in fams.items() if r["level"] == level)
        if names:
            out.append(f"- {level}: {', '.join(names)}")
    for model, t in (comp.get("tooling") or {}).items():
        rate = f"{t['error_rate']:.1%}" if t.get("error_rate") is not None else "unknown"
        out.append(f"- tool use, {model}: {t['level']} (error rate {rate} over {t['sessions']} sessions)")
    dreams = read_jsonl(engine.root / "dream_log.jsonl")
    if dreams:
        d = dreams[-1]
        w = sum(p["tokens_with"] for p in d["pairs"])
        wo = sum(p["tokens_without"] for p in d["pairs"])
        out.append(f"\n## Last dream ({d['run']})\n\n{len(d['pairs'])} practice pairs; tokens with lessons {w:,}, "
                   f"without {wo:,}; lesson statuses {d['statuses']}.")
    out += _memory_section(engine)
    cycles = read_jsonl(engine.root / "cycles.jsonl")
    if cycles:
        out.append(f"\n## Dream cycles: {len(cycles)}\n")
        out += [f"- {time.strftime('%Y-%m-%d %H:%M', time.localtime(c['started']))}: live sessions ingested "
                f"{c.get('ingested_live', 0)}, practice tasks {c.get('practice_tasks', 0)}, lesson statuses after "
                f"{c.get('evaluation', '-')}" for c in cycles[-10:]]
    advice = read_jsonl(engine.root / "advice.jsonl")
    if advice:
        out.append(f"\n## Advisor consultations: {len(advice)}\n")
        out += [f"- {a['tag']}: {a['reason']}" for a in advice[-10:]]
    return "\n".join(out) + "\n"
