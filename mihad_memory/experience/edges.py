"""Edge-case checklist learned from past fixes (raises success, not only efficiency).

Most fixes in a mature library are edge cases. The adopted checkers (and the fixes behind them)
say which kinds keep coming back in this project. Each kind seen in at least two independent
past fixes becomes an edge-case lesson with a concrete, spec-free check the agent can run on the
function it changed: an invariant that must hold whatever the intended behavior is.
"""
import re

from .. import project
from .checkers import adopted
from .engine import lesson_id

MIN_TASKS = 2

# kind: (evidence pattern in checker code/comments or task text, applies-to pattern on a signature/source, check)
KINDS = {
    "single_use_iterator": (
        r"\biter\(|single[- ]use|one[- ]shot|consumed|generator|slow path",
        r"iterable|iterator|\*args|\bseq",
        "Pass a one-shot iterator (iter(list_input)) as well as the list itself: results must be identical, "
        "and nothing may read an input twice or call len() on it."),
    "empty_input": (
        r"\(\s*\[\s*\]|\(\s*''|\(\s*\"\"|empty",
        r"iterable|iterator|\*args|\bseq",
        "Call it with empty inputs (every iterable argument empty, and each one empty in turn): no crash "
        "unless an error is documented, and the result matches the non-empty logic at size 0."),
    "negative_or_zero_size": (
        r"negative|n\s*=\s*-|,\s*-\d|\(\s*-\d|\bn\s*=\s*0|\b0\s*\)|zero",
        r"\bn\b|size|\bk\b|\br\b|count|stop|start|step|maxsplit|limit",
        "Try size-like arguments 0, -1 and a large value: negative values must raise ValueError with a clear "
        "message (not a confusing TypeError or a silent wrong result), and 0 must behave consistently."),
    "float_precision": (
        r"\bfloat|0\.1|Fraction|Decimal|nan|inf\b",
        r"step|start|stop|numeric|float|range",
        "With float arguments (e.g. 0.0, 1.0, 0.1), check that len(obj) == len(list(obj)) and that every item "
        "it yields is `in` it and has a valid .index(); derive these from the same arithmetic that yields "
        "the items, not from a separate division."),
    "falsy_and_none": (
        r"\bNone\b|falsy|\bFalse\b|== 0|\b0\b is",
        r"default|key|pred|fillvalue|too_short|too_long|exception|sentinel",
        "Pass falsy-but-valid values (0, '', False, an exception instance) where the code checks for a "
        "missing value: it must test `is None` / a sentinel, not truthiness."),
    "strict_comparison": (
        r"__lt__|__le__|strict|reverse=True|key=",
        r"strict|reverse|key",
        "Check strict/reverse/key combinations, and objects that only define __lt__."),
}

# The same idea for the other languages, worded without Python terms.
GENERIC_KINDS = {
    "empty_input": (
        r"\[\s*\]|\"\"|''|empty|\bsize\(\)\s*==\s*0|isEmpty|len\(\w+\)\s*==\s*0",
        r"\[\]|List|Array|Slice|Vec|Iterable|Collection|Enumerable|string|str\b|\bseq|items|values",
        "Call it with empty inputs (empty list/array/string, and each collection argument empty in turn): "
        "no crash or index error unless one is documented, and the result matches the general logic at size 0."),
    "null_or_missing": (
        r"\bnull\b|\bundefined\b|\bnil\b|Optional|None|NullPointer|\?\?|\?\.",
        r"\?|null|undefined|nil|Optional|Nullable|\*\w+|pointer|default",
        "Pass null/undefined/nil (or an empty optional) wherever a value may be missing, and falsy-but-valid "
        "values (0, '', false): missing must be detected explicitly, not by truthiness."),
    "negative_or_zero_size": (
        r"negative|zero|\(\s*-\d|,\s*-\d|=\s*-\d|\b0\s*\)|out of range|IndexOutOfRange|RangeError",
        r"\bn\b|size|count|length|len|limit|offset|index|start|end|step|width|capacity",
        "Try size-like arguments 0, -1 and a very large value: invalid values must fail with a clear, "
        "documented error (not a crash deep inside or a silent wrong result), and 0 must behave consistently."),
    "boundary_off_by_one": (
        r"off[- ]by[- ]one|boundary|last (element|item)|first (element|item)|inclusive|exclusive|<=|>=",
        r"index|start|end|from|to|range|slice|substring|offset|limit|page",
        "Check the boundaries: first and last element, a range that ends exactly at the length, and "
        "inclusive vs exclusive ends; the two ends must agree with the documentation."),
    "single_pass_input": (
        r"iterator|stream|generator|reader|consumed|one[- ]shot|single[- ]use|Iterator<|IEnumerable<|io\.Reader",
        r"Iterator|Iterable|Stream|Reader|Enumerable|Generator|AsyncIterable|chan\b",
        "Pass a single-pass input (an iterator, stream or reader) as well as a materialized collection: the "
        "results must be identical and nothing may read the input twice."),
    "unicode_and_whitespace": (
        r"unicode|utf-?8|emoji|whitespace|\\t|\\n|trim|strip|locale|encoding",
        r"string|str\b|String|text|name|title|line|word",
        "Try non-ASCII text (accents, emoji, right-to-left text) and whitespace-only or padded strings."),
}


def kinds_for(engine):
    repo = (engine.get("config", {}) or {}).get("repo")
    if repo and project.load(repo).get("language", "python") != "python":
        return GENERIC_KINDS
    return KINDS


def _evidence_text(engine, rec):
    path = engine.root / rec["path"]
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def mine(engine):
    eps = {e["task_id"]: e for e in engine.episodes()}
    kinds = kinds_for(engine)
    support = {k: set() for k in kinds}
    for tid, rec in adopted(engine).items():
        text = _evidence_text(engine, rec) + "\n" + eps.get(tid, {}).get("subject", "") + "\n" + \
            eps.get(tid, {}).get("body", "")
        for kind, (pat, _, _) in kinds.items():
            if re.search(pat, text, re.I):
                support[kind].add(tid)
    lessons = []
    for kind, tids in support.items():
        if len(tids) < MIN_TASKS:
            continue
        lessons.append({"id": lesson_id("edge_case", kind), "kind": "edge_case", "trigger": {"edge": kind},
                        "text": f"Edge case behind {len(tids)} past fixes ({kind.replace('_', ' ')}): {kinds[kind][2]}",
                        "applies": kinds[kind][1], "support": sorted(tids), "status": "adopted",
                        "ab": {"pairs": []}})
    return engine.merge_lessons("edge_case", lessons)


def applicable(engine, source_snippets, limit=4):
    """Edge-case lessons whose 'applies' pattern matches the signature/source of the functions at hand."""
    text = "\n".join(source_snippets)
    out = [l for l in engine.active_lessons() if l["kind"] == "edge_case" and re.search(l["applies"], text, re.I)]
    out.sort(key=lambda l: (l["status"] != "promoted", -len(l["support"])))
    return out[:limit]
