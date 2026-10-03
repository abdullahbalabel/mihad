"""Property checks that run inside the project's own test process (Python; standard library only).

The review starts the tests that call the changed function with this module loaded (through a
sitecustomize file). Every real call the tests make to the function becomes a seed: the module then
calls the function with variations of those arguments and checks properties every correct version
must keep. Counterexamples are written to a JSON file. Nothing here is advice: each finding is a call
that was made and a result that was observed.

Properties (templates); the project's adopted subset is passed in MIHAD_PROP_TEMPLATES:
    len          len(x) equals the number of items iteration yields
    reversed     reversed(x) yields exactly the items of x in reverse order
    getitem      x[i] equals the i-th item yielded, for positive and negative i
    contains     every yielded item is `in` x, and x.index(item) finds it
    eq_hash      equal objects have equal hashes
    reference    an object whose docstring calls it an extension of a built-in (range) behaves like
                 the built-in where both accept the arguments (items, len, equality, hashing)
    single_pass  a function given an iterable opens it only once (iter() called once)

Argument variations come from the seeds: numbers are replaced by random numbers of the same type and
sign, and for any three numbers (a, b, c) also by b = a + k*c, the boundaries of an arithmetic
progression where off-by-one errors live.
"""
import functools
import inspect
import itertools
import json
import os
import random
import sys
import time

CAP = 2000          # items materialized from one object
SEEDS = 12          # distinct seed calls per target
VARIANTS = 150      # argument variations per seed
BUDGET = 6.0        # seconds per phase (object properties; reference; single pass)
MAX_FINDINGS = 3


class TwiceIterated(Exception):
    pass


class OncePerIter:
    """An iterable that may be opened (iter()) only once, like a file or a network stream."""

    def __init__(self, items):
        self._items = list(items)
        self._opened = False

    def __iter__(self):
        if self._opened:
            raise TwiceIterated("the iterable was opened a second time with iter()")
        self._opened = True
        return iter(self._items)

    def __repr__(self):
        return f"<iterable that can be opened once: {self._items[:5]!r}{'...' if len(self._items) > 5 else ''}>"


def _short(v, n=60):
    r = repr(v)
    return r if len(r) <= n else r[:n - 3] + "..."


def _call_text(name, args, kwargs):
    parts = [_short(a) for a in args] + [f"{k}={_short(v)}" for k, v in kwargs.items()]
    return f"{name}({', '.join(parts)})"


def _materialize(x):
    items = list(itertools.islice(iter(x), CAP + 1))
    return items if len(items) <= CAP else None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def variations(args, rng):
    """Argument tuples derived from one seed call: random same-type numbers and progression boundaries."""
    args = list(args)
    pos = [i for i, a in enumerate(args) if _num(a)]
    if not pos:
        return []
    out = []
    for _ in range(VARIANTS // 2):
        v = list(args)
        for i in pos:
            a = args[i]
            if isinstance(a, int):
                mag = rng.randint(0, 30)
                v[i] = mag if a > 0 else -mag if a < 0 else rng.randint(-5, 5)
            else:
                mag = round(rng.uniform(0, 40), rng.choice([1, 2, 3, 6, 12]))
                v[i] = mag if a > 0 else -mag if a < 0 else round(rng.uniform(-5, 5), 3)
        out.append(tuple(v))
    if len(pos) >= 3:
        for _ in range(VARIANTS // 2):
            i, j, k = pos[0], pos[1], pos[2]
            v = list(args)
            floaty = any(isinstance(args[p], float) for p in (i, j, k))
            if floaty:
                start = round(rng.uniform(-30, 30), rng.choice([1, 2, 6, 12]))
                step = round(rng.uniform(0.05, 5), rng.choice([1, 2, 6, 12])) * rng.choice([1, -1])
            else:
                start, step = rng.randint(-20, 20), rng.choice([1, 2, 3, 5, 7]) * rng.choice([1, -1])
            v[i], v[k] = start, step
            v[j] = start + rng.randint(0, 15) * step
            out.append(tuple(v))
    rng.shuffle(out)
    return out


# ------------------------------------------------------------------ properties of an object

def check_object(obj, label, templates):
    """Counterexamples for the sequence-like properties of one object (an instance or a result)."""
    found = []
    has_len = hasattr(type(obj), "__len__")
    if not hasattr(type(obj), "__iter__") or isinstance(obj, (str, bytes)):
        return found
    try:
        items = _materialize(obj)
    except Exception:
        return found
    if items is None:
        return found
    if "len" in templates and has_len:
        try:
            n = len(obj)
            if n != len(items):
                found.append(("len", f"{label}: len() is {n}, but iterating yields {len(items)} items"))
        except Exception as exc:
            found.append(("len", f"{label}: len() raised {type(exc).__name__}: {exc}, but iterating yields "
                                 f"{len(items)} items"))
    if "reversed" in templates and hasattr(type(obj), "__reversed__"):
        try:
            rev = list(itertools.islice(reversed(obj), CAP + 1))
            if rev != items[::-1]:
                diff = next((i for i, (a, b) in enumerate(zip(rev, items[::-1])) if a != b), min(len(rev), len(items)))
                got = rev[diff] if diff < len(rev) else "(nothing)"
                want = items[::-1][diff] if diff < len(items) else "(nothing)"
                found.append(("reversed", f"{label}: reversed() yields {_short(got)} at position {diff}, "
                                          f"but the items in reverse order have {_short(want)} there"))
        except Exception:
            pass
    if "getitem" in templates and has_len and hasattr(type(obj), "__getitem__") and items:
        for i in sorted({0, len(items) - 1, len(items) // 2, -1, -len(items)}):
            try:
                if obj[i] != items[i]:
                    found.append(("getitem", f"{label}: x[{i}] is {_short(obj[i])}, but item {i} of the "
                                             f"iteration is {_short(items[i])}"))
                    break
            except Exception as exc:
                found.append(("getitem", f"{label}: x[{i}] raised {type(exc).__name__}: {exc}"))
                break
    if "contains" in templates and hasattr(type(obj), "__contains__"):
        for i, it in enumerate(items[:40]):
            try:
                if it not in obj:
                    found.append(("contains", f"{label}: {_short(it)} is yielded (item {i}) but `in` says False"))
                    break
                if hasattr(obj, "index") and items.index(it) != obj.index(it):
                    found.append(("contains", f"{label}: index({_short(it)}) is {obj.index(it)}, but the "
                                              f"item is yielded at position {items.index(it)}"))
                    break
            except Exception as exc:
                found.append(("contains", f"{label}: checking item {_short(it)} raised {type(exc).__name__}: {exc}"))
                break
    return found


def check_eq_hash(objs, templates):
    if "eq_hash" not in templates:
        return []
    for (la, a), (lb, b) in itertools.combinations(objs, 2):
        try:
            if a == b and hash(a) != hash(b):
                return [("eq_hash", f"{la} == {lb}, but their hashes differ")]
        except TypeError:
            return []
        except Exception:
            continue
    return []


REFERENCES = {"range": range}


def reference_of(target):
    doc = inspect.getdoc(target) or ""
    for name, ref in REFERENCES.items():
        if f"{name}()" in doc or f"built-in {name}" in doc or f"builtin {name}" in doc:
            return name, ref
    return None


def check_reference(cls, name, seeds, rng, templates, deadline):
    """Compare a class with the built-in its docstring names, on integer arguments both accept."""
    if "reference" not in templates:
        return []
    ref = reference_of(cls)
    if not ref:
        return []
    rname, rfun = ref
    arg_counts = sorted({len(a) for a, k in seeds if not k and 1 <= len(a) <= 3}) or [3]
    pool = []
    for _ in range(300):
        n = rng.choice(arg_counts)
        args = tuple(rng.randint(-6, 6) for _ in range(n))
        try:
            pool.append((args, cls(*args), rfun(*args)))
        except Exception:
            continue
    for args, mine, theirs in pool:
        if time.time() > deadline:
            break
        label = _call_text(name, args, {})
        try:
            if list(mine) != list(theirs):
                return [("reference", f"{label} yields {_short(list(mine))}, {rname}{args} yields {_short(list(theirs))}")]
            if len(mine) != len(theirs):
                return [("reference", f"len({label}) is {len(mine)}, len({rname}{args}) is {len(theirs)}")]
        except Exception:
            continue
    for (a1, m1, t1), (a2, m2, t2) in itertools.combinations(pool[:120], 2):
        if time.time() > deadline:
            break
        try:
            if (m1 == m2) != (t1 == t2):
                return [("reference", f"{_call_text(name, a1, {})} == {_call_text(name, a2, {})} is {m1 == m2}, "
                                      f"but {rname}{a1} == {rname}{a2} is {t1 == t2}")]
            if t1 == t2 and hash(m1) != hash(m2):
                return [("reference", f"{_call_text(name, a1, {})} and {_call_text(name, a2, {})} are equal like "
                                      f"{rname}{a1} and {rname}{a2}, but their hashes differ")]
        except Exception:
            continue
    return []


# ------------------------------------------------------------------ single-pass inputs

def _iterable_arg(v):
    return isinstance(v, (list, tuple, set, frozenset, dict)) or (
        hasattr(v, "__iter__") and not isinstance(v, (str, bytes, bytearray, OncePerIter)))


def check_single_pass(fn, name, seeds, templates):
    """A function that opens an argument iterable twice: works with lists, fails with streams."""
    if "single_pass" not in templates:
        return []
    for args, kwargs in seeds:
        if not all(isinstance(a, (list, tuple)) or not _iterable_arg(a) for a in args):
            continue  # seed arguments that are already one-shot cannot be replayed safely
        idx = [i for i, a in enumerate(args) if isinstance(a, (list, tuple))]
        if not idx:
            continue
        new = tuple(OncePerIter(a) if i in idx else a for i, a in enumerate(args))
        try:
            res = fn(*new, **kwargs)
            if hasattr(res, "__iter__") and not isinstance(res, (str, bytes)):
                _materialize(res)
        except TwiceIterated:
            return [("single_pass", f"{_call_text(name, new, kwargs)} opens an input iterable twice "
                                    f"(iter() called a second time); with a list it works, with a stream it fails")]
        except Exception:
            continue
    return []


# ------------------------------------------------------------------ driver

class Target:
    def __init__(self, module, name, obj, templates, out_path):
        self.module, self.name, self.obj = module, name, obj
        self.templates, self.out_path = templates, out_path
        self.seeds, self.keys, self.findings = [], set(), []
        self.rng = random.Random(1234)
        self.done = False

    def add_seed(self, args, kwargs):
        if self.done or len(self.seeds) >= SEEDS:
            return
        key = tuple((type(a).__name__, (a > 0) - (a < 0) if _num(a) else None) for a in args) + tuple(sorted(kwargs))
        if key in self.keys:
            return
        self.keys.add(key)
        # Copies: the function under test may consume or change its arguments.
        self.seeds.append((tuple(list(a) if isinstance(a, list) else a for a in args), dict(kwargs)))

    def run(self):
        if self.done or not self.seeds:
            return
        self.done = True
        found = []
        self.save(found)
        is_class = inspect.isclass(self.obj)
        if is_class:  # cheap and decisive, so first and with its own budget
            found += check_reference(self.obj, self.name, self.seeds, self.rng, self.templates, time.time() + BUDGET)
        else:
            found += check_single_pass(self.obj, self.name, self.seeds, self.templates)
        made = []
        # Bounded by count, not time, so the run on the starting commit tries exactly the same calls.
        per_seed = max(20, VARIANTS * 2 // max(1, len(self.seeds)))
        for args, kwargs in self.seeds:
            for v in ([args] + variations(args, self.rng))[:per_seed]:
                if len(made) > 600:
                    break
                try:
                    x = self.obj(*v, **kwargs)
                except Exception:
                    continue
                label = _call_text(self.name, v, kwargs)
                if not is_class and inspect.isgenerator(x):
                    continue
                made.append((label, x))
                for f in check_object(x, label, self.templates):
                    if f[0] not in {k for k, _ in found}:
                        found.append(f)
                        self.save(found)
        found += check_eq_hash(made[:200], self.templates)
        self.save(found)

    def save(self, found):
        """Written after every new finding, so a check that hangs (an infinite input) loses nothing."""
        seen, out = set(), []
        for kind, text in found:
            if kind not in seen:
                seen.add(kind)
                out.append({"template": kind, "function": self.name, "detail": text})
        self.findings = out  # all kinds: the comparison with the starting commit needs every one
        try:
            with open(self.out_path, "w", encoding="utf-8") as fh:
                json.dump({"function": self.name, "seeds": len(self.seeds), "findings": self.findings}, fh)
        except OSError:
            pass


def install():
    """Called from sitecustomize: wrap each target so the tests' own calls become seeds."""
    spec = json.loads(os.environ.get("MIHAD_PROP_TARGETS", "[]"))
    templates = set(json.loads(os.environ.get("MIHAD_PROP_TEMPLATES", "[]")))
    out_dir = os.environ.get("MIHAD_PROP_OUT", "")
    targets = []
    import importlib
    for module, name in spec:
        try:
            m = importlib.import_module(module)
            obj = getattr(m, name)
        except Exception:
            continue
        t = Target(module, name, obj, templates, os.path.join(out_dir, f"{module}.{name}.json"))
        targets.append(t)
        if inspect.isclass(obj):
            orig_init = obj.__init__

            @functools.wraps(orig_init)
            def init(self, *a, __orig=orig_init, __t=t, __cls=obj, **k):
                __orig(self, *a, **k)
                if type(self) is __cls:
                    __t.add_seed(a, k)
            try:
                obj.__init__ = init
            except (TypeError, AttributeError):
                continue
        else:
            def wrapper(*a, __f=obj, __t=t, **k):
                __t.add_seed(a, k)
                return __f(*a, **k)
            functools.update_wrapper(wrapper, obj)
            for mm in list(sys.modules.values()):
                try:
                    if getattr(mm, name, None) is obj:
                        setattr(mm, name, wrapper)
                except Exception:
                    pass
    if targets:
        import atexit

        def finish():
            import threading

            def watchdog():  # a check stuck on an infinite input must not hold the test process
                time.sleep(2 * BUDGET * len(targets) + 5)
                os._exit(0)
            threading.Thread(target=watchdog, daemon=True).start()
            for t in targets:
                t.run()
        atexit.register(finish)
    return targets
