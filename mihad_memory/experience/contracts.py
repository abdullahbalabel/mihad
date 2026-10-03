"""The contract ontology: what kind of object something is, and what every correct object of that kind
must satisfy (Python; standard library only; runs inside the project's test process).

Kinds are inferred from the object itself, never written by hand: membership in collections.abc first,
then the methods its type defines. A mapping is never treated as a sequence, a set never as a mapping.

    kind       inferred from                                   contract templates
    sequence   abc.Sequence, or __len__ + int __getitem__      len, reversed, getitem, slice, contains
               (not a mapping or set), x[0] is the first item
    collection __len__ + __iter__ (none of the above)          len, reversed
    mapping    abc.Mapping, or keys + items + __getitem__       m_keys, m_get, m_copy
    set        abc.Set, or __contains__ + issubset/__or__       s_len, s_ops, s_compare
    stream     read + seek + tell                               seek_tell, read_end
    value      __eq__ defined by the type                       eq_hash, eq_symmetric

Each template returns counterexamples: (template, text). Templates are named so a project can adopt or
drop each one (properties.json), and each belongs to a behaviour (BEHAVIOUR) that task metadata can match.
"""
import collections.abc as abc
import copy
import itertools
import time

CAP = 2000

# template -> behaviour (the task-metadata taxonomy in probes.TASK_BEHAVIOURS uses the same names)
BEHAVIOUR = {
    "len": "length", "reversed": "reversal", "getitem": "indexing", "slice": "slicing", "contains": "membership",
    "m_keys": "membership", "m_get": "indexing", "m_copy": "copying",
    "s_len": "length", "s_ops": "set_ops", "s_compare": "set_ops",
    "seek_tell": "seeking", "read_end": "termination",
    "eq_hash": "hashing", "eq_symmetric": "equality",
    "reference": "equality", "single_pass": "iteration",
}


def _same(a, b):
    """a and b are the same value (NaN is the same as itself; a comparison that fails is not a mismatch)."""
    if a is b:
        return True
    try:
        return bool(a == b)
    except Exception:
        return True


def _short(v, n=60):
    try:
        r = repr(v)
    except Exception:
        r = f"<{type(v).__name__}>"
    return r if len(r) <= n else r[:n - 3] + "..."


def _defines(t, name):
    return any(name in vars(k) for k in t.__mro__[:-1])


def _has(t, name):
    """The type itself (or a base) defines the method. Not hasattr(t, ...): since Python 3.10 every class
    has __or__ through its metaclass (for `int | None`), which would make everything look like a set."""
    return any(name in vars(k) for k in t.__mro__)


def kinds(obj):
    """The kinds of an object, from its interfaces. Conservative: when unsure, no kind."""
    t = type(obj)
    out = set()
    if isinstance(obj, (str, bytes, bytearray, int, float, complex, bool, type(None))):
        return out
    if _has(t, "__next__"):  # an iterator: checking it would consume it (and change what the tests left)
        return {"stream"} if all(_has(t, m) for m in ("read", "seek", "tell")) else out
    is_map = isinstance(obj, abc.Mapping) or all(_has(t, m) for m in ("keys", "items", "__getitem__"))
    is_seq = not is_map and isinstance(obj, abc.Sequence)
    is_set = not is_map and not is_seq and (isinstance(obj, abc.Set) or (
        _has(t, "__contains__") and _has(t, "__iter__") and _has(t, "issubset")))
    if is_map:
        out.add("mapping")
    elif is_set:
        out.add("set")
    elif is_seq or all(_has(t, m) for m in ("__len__", "__getitem__", "__iter__")):
        try:  # a sequence is indexed by position: x[0] is the first item
            first = next(iter(obj), _NOTHING)
            if first is _NOTHING or _same(obj[0], first):
                out.add("sequence")
        except Exception:
            pass
    if not out and all(_has(t, m) for m in ("__len__", "__iter__")):
        out.add("collection")  # sized and iterable: length (and reversal, if defined) must agree with iteration
    if all(_has(t, m) for m in ("read", "seek", "tell")):
        out.add("stream")
    if _defines(t, "__eq__") and t.__eq__ is not object.__eq__:
        out.add("value")
    return out


_NOTHING = object()


def _items(obj):
    items = list(itertools.islice(iter(obj), CAP + 1))
    return items if len(items) <= CAP else None


# ------------------------------------------------------------------ sequence

def sequence(obj, label, templates):
    found = []
    try:
        items = _items(obj)
    except Exception:
        return found
    if items is None:
        return found
    t = type(obj)
    if "len" in templates and _has(t, "__len__"):
        try:
            n = len(obj)
            if n != len(items):
                found.append(("len", f"{label}: len() is {n}, but iterating yields {len(items)} items"))
        except Exception as exc:
            found.append(("len", f"{label}: len() raised {type(exc).__name__}: {exc}, but iterating yields "
                                 f"{len(items)} items"))
    if "reversed" in templates and _has(t, "__reversed__"):
        try:
            rev = list(itertools.islice(reversed(obj), CAP + 1))
            if rev != items[::-1]:
                i = next((i for i, (a, b) in enumerate(zip(rev, items[::-1])) if a != b), min(len(rev), len(items)))
                got = _short(rev[i]) if i < len(rev) else "(nothing)"
                want = _short(items[::-1][i]) if i < len(items) else "(nothing)"
                found.append(("reversed", f"{label}: reversed() yields {got} at position {i}, but the items in "
                                          f"reverse order have {want} there"))
        except Exception:
            pass
    n = len(items)
    if "getitem" in templates and n:
        for i in sorted({0, n - 1, n // 2, -1, -n}):
            try:
                if not _same(obj[i], items[i]):
                    found.append(("getitem", f"{label}: x[{i}] is {_short(obj[i])}, but item {i} of the iteration "
                                             f"is {_short(items[i])}"))
                    break
            except Exception as exc:
                found.append(("getitem", f"{label}: x[{i}] raised {type(exc).__name__}: {exc}"))
                break
        else:
            for i in (n, -n - 1, -n - 3):  # out of range must raise IndexError, not wrap around
                try:
                    v = obj[i]
                    found.append(("getitem", f"{label}: x[{i}] returned {_short(v)}, but the object has {n} items "
                                             f"(an out-of-range index should raise IndexError)"))
                    break
                except IndexError:
                    continue
                except Exception:
                    break
    if "slice" in templates and n:
        # Forward and backward slices, inside and beyond the bounds (beyond-bounds slices clamp, never raise).
        for a, b, c in ((1, None, None), (None, -1, None), (None, None, 2), (None, None, -1), (-2, None, None),
                        (n + 2, None, None), (1, n - 1, 2), (n - 1, 0, -1), (None, 2, -1), (2, None, -1),
                        (-1, -n - 5, -2), (n + 5, -n - 5, -3), (-3, 1, -1), (-n - 5, n + 5, 3), (n // 2, n // 2, 1)):
            try:
                got = obj[a:b:c]
                got_items = list(itertools.islice(iter(got), CAP))
            except (TypeError, NotImplementedError):
                break  # no slicing at all
            except Exception as exc:
                found.append(("slice", f"{label}: x[{a}:{b}:{c}] raised {type(exc).__name__}: {exc}"))
                break
            if got_items != items[a:b:c]:
                found.append(("slice", f"{label}: x[{a}:{b}:{c}] gives {_short(got_items)}, but the items sliced the "
                                       f"same way are {_short(items[a:b:c])}"))
                break
    if "contains" in templates and _has(t, "__contains__"):
        for i, it in enumerate(items[:40]):
            try:
                if it not in obj:
                    found.append(("contains", f"{label}: {_short(it)} is yielded (item {i}) but `in` says False"))
                    break
                if hasattr(obj, "index") and items.index(it) != obj.index(it):
                    found.append(("contains", f"{label}: index({_short(it)}) is {obj.index(it)}, but the item is "
                                              f"yielded at position {items.index(it)}"))
                    break
            except Exception as exc:
                found.append(("contains", f"{label}: checking item {_short(it)} raised {type(exc).__name__}: {exc}"))
                break
    return found


# ------------------------------------------------------------------ mapping

def mapping(obj, label, templates):
    found = []
    try:
        keys = list(itertools.islice(iter(obj.keys()), CAP + 1))
    except Exception:
        return found
    if len(keys) > CAP:
        return found
    # `in` without __contains__ or __iter__ falls back to x[0], x[1], ... and may never stop: check membership
    # only when the type defines it.
    if "m_keys" in templates and (_has(type(obj), "__contains__") or _has(type(obj), "__iter__")):
        try:
            for k in keys[:40]:
                if k not in obj:
                    found.append(("m_keys", f"{label}: {_short(k)} is in keys() but `in` says False"))
                    break
            else:
                if isinstance(obj, abc.Mapping) and _has(type(obj), "__len__") and len(obj) != len(keys):
                    found.append(("m_keys", f"{label}: len() is {len(obj)}, but keys() yields {len(keys)} keys"))
        except Exception as exc:
            found.append(("m_keys", f"{label}: checking keys raised {type(exc).__name__}: {exc}"))
    if "m_get" in templates and hasattr(obj, "get"):
        for k in keys[:40]:
            try:
                if not _same(obj.get(k), obj[k]):
                    found.append(("m_get", f"{label}: get({_short(k)}) is {_short(obj.get(k))}, but x[{_short(k)}] is "
                                           f"{_short(obj[k])}"))
                    break
            except Exception as exc:
                found.append(("m_get", f"{label}: reading key {_short(k)} raised {type(exc).__name__}: {exc}"))
                break
    if "m_copy" in templates and type(obj).__eq__ is not object.__eq__:
        for how, fn in (("copy.copy", copy.copy), ("copy.deepcopy", copy.deepcopy)):
            try:
                c = fn(obj)
            except Exception:
                continue
            try:
                if not _same(c, obj) or not _same(list(c.items()), list(obj.items())):
                    found.append(("m_copy", f"{how}({label}) is not equal to the original: {_short(list(c.items()))} vs "
                                            f"{_short(list(obj.items()))}"))
                    break
            except Exception:
                continue
    return found


def _hashable(xs):
    try:
        set(xs)
        return True
    except TypeError:
        return False


# ------------------------------------------------------------------ set

def set_like(obj, label, templates, others=()):
    found = []
    try:
        items = _items(obj)
    except Exception:
        return found
    if items is None or not _hashable(items):
        return found
    mine = set(items)
    if "s_len" in templates and _has(type(obj), "__len__"):
        try:
            if len(obj) != len(mine):
                found.append(("s_len", f"{label}: len() is {len(obj)}, but it holds {len(mine)} distinct items"))
        except Exception as exc:
            found.append(("s_len", f"{label}: len() raised {type(exc).__name__}: {exc}"))
    for olabel, other in others[:8]:
        try:
            theirs = set(_items(other) or [])
        except Exception:
            continue
        if "s_ops" in templates:
            for sym, op in (("|", lambda a, b: a | b), ("&", lambda a, b: a & b), ("-", lambda a, b: a - b),
                            ("^", lambda a, b: a ^ b)):
                try:
                    got = set(_items(op(obj, other)) or [])
                except (TypeError, NotImplementedError):
                    continue
                except Exception as exc:
                    found.append(("s_ops", f"{label} {sym} {olabel} raised {type(exc).__name__}: {exc}"))
                    break
                if got != op(mine, theirs):
                    found.append(("s_ops", f"{label} {sym} {olabel} holds {_short(sorted(got, key=repr))}, but as sets "
                                           f"it is {_short(sorted(op(mine, theirs), key=repr))}"))
                    break
        if "s_compare" in templates:
            for sym, op, meth in (("<=", lambda a, b: a <= b, "issubset"), (">=", lambda a, b: a >= b, "issuperset")):
                try:
                    got = op(obj, other)
                except (TypeError, NotImplementedError):
                    continue
                except Exception:
                    continue
                try:
                    differs = bool(got) != op(mine, theirs)
                except Exception:
                    continue
                if differs:
                    found.append(("s_compare", f"{label} {sym} {olabel} is {_short(got)}, but as sets it is "
                                               f"{op(mine, theirs)}"))
                    break
                if hasattr(obj, meth):
                    try:
                        if bool(getattr(obj, meth)(other)) != op(mine, theirs):
                            found.append(("s_compare", f"{label}.{meth}({olabel}) is {getattr(obj, meth)(other)}, but "
                                                       f"as sets it is {op(mine, theirs)}"))
                            break
                    except Exception:
                        continue
    return found


# ------------------------------------------------------------------ stream

def stream(obj, label, templates, deadline):
    found = []
    try:
        if getattr(obj, "closed", False) or not (obj.seekable() if hasattr(obj, "seekable") else True):
            return found
        start = obj.tell()
    except Exception:
        return found
    try:
        if "seek_tell" in templates:
            obj.seek(0, 2)
            end = obj.tell()
            for p in sorted({0, 1, end // 2, end}):
                if p > end:
                    continue
                obj.seek(p)
                if obj.tell() != p:
                    found.append(("seek_tell", f"{label}: after seek({p}), tell() is {obj.tell()}"))
                    break
        if "read_end" in templates and time.time() < deadline:
            obj.seek(0, 2)
            t0 = time.time()
            rest = obj.read()
            if rest not in ("", b"", None):
                found.append(("read_end", f"{label}: read() at the end of the stream returned {_short(rest)}"))
            elif time.time() - t0 > 2:
                found.append(("read_end", f"{label}: read() at the end of the stream took {time.time() - t0:.1f}s"))
    except Exception:
        pass
    finally:
        try:
            obj.seek(start)
        except Exception:
            pass
    return found


# ------------------------------------------------------------------ value

def values(objs, templates):
    """Equality and hashing across objects of the same type."""
    found = []
    for (la, a), (lb, b) in itertools.combinations(objs[:120], 2):
        try:
            ab, ba = bool(a == b), bool(b == a)
        except Exception:
            continue
        if "eq_symmetric" in templates and ab != ba:
            found.append(("eq_symmetric", f"{la} == {lb} is {ab}, but {lb} == {la} is {ba}"))
            break
        if "eq_hash" in templates and ab:
            try:
                if hash(a) != hash(b):
                    found.append(("eq_hash", f"{la} == {lb}, but their hashes differ"))
                    break
            except TypeError:
                break
            except Exception:
                continue
    return found


def check(obj, label, templates, others=(), deadline=None):
    """All contract counterexamples for one object, by its kinds."""
    try:
        k = kinds(obj)
    except Exception:
        return []
    deadline = deadline or time.time() + 3
    found = []
    if "sequence" in k:
        found += sequence(obj, label, templates)
    elif "collection" in k:
        found += sequence(obj, label, set(templates) & {"len", "reversed"})
    if "mapping" in k:
        found += mapping(obj, label, templates)
    if "set" in k:
        found += set_like(obj, label, templates, [(lb, o) for lb, o in others if o is not obj and "set" in kinds(o)])
    if "stream" in k:
        found += stream(obj, label, templates, deadline)
    return found
