"""Property checks that run inside the project's own test process (Python; standard library only).

The review starts the tests that call the changed function with this module loaded (through a
sitecustomize file). Every real call the tests make to the function becomes a seed: the module then
calls the function with variations of those arguments and checks properties every correct version
must keep. Counterexamples are written to a JSON file. Nothing here is advice: each finding is a call
that was made and a result that was observed.

Properties come from the contract ontology (contracts.py): each object is classified by its interfaces
(sequence, mapping, set, stream, value) and checked against the contract of its kind only. Besides:
    reference    an object whose docstring calls it an extension of a built-in (range) behaves like
                 the built-in where both accept the arguments (items, len, equality, hashing)
    single_pass  a function given an iterable opens it only once (iter() called once)
The project's adopted templates are passed in MIHAD_PROP_TEMPLATES.

Objects checked: those made by calling the target with variations of its test calls, and (for classes)
the instances the tests themselves built and changed, in the state the tests left them.

Argument variations come from the seeds: numbers are replaced by random numbers of the same type and
sign, and for any three numbers (a, b, c) also by b = a + k*c, the boundaries of an arithmetic
progression where off-by-one errors live.
"""
import functools
import inspect

try:
    from . import contracts
except ImportError:  # loaded as a top-level module
    import contracts
import itertools
import json
import os
import pickle
import random
import sys
import time
import weakref

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


# ------------------------------------------------------------------ reference twin

REFERENCES = {"range": range}


def reference_of(target):
    doc = inspect.getdoc(target) or ""
    for name, ref in REFERENCES.items():
        if f"{name}()" in doc or f"built-in {name}" in doc or f"builtin {name}" in doc:
            return name, ref
    return None


def check_reference(cls, name, seeds, rng, templates):
    """Compare a class with the built-in its docstring names, on integer arguments both accept."""
    if "reference" not in templates:
        return []
    ref = reference_of(cls)
    if not ref:
        return []
    rname, rfun = ref
    arg_counts = sorted({len(sd[0]) for sd in seeds if not sd[1] and 1 <= len(sd[0]) <= 3}) or [3]
    pool = []
    for _ in range(300):
        n = rng.choice(arg_counts)
        args = tuple(rng.randint(-6, 6) for _ in range(n))
        try:
            pool.append((args, cls(*args), rfun(*args)))
        except Exception:
            continue
    for args, mine, theirs in pool:  # bounded by count (no deadline): both runs try the same calls
        label = _call_text(name, args, {})
        try:
            if list(mine) != list(theirs):
                return [("reference", f"{label} yields {_short(list(mine))}, {rname}{args} yields {_short(list(theirs))}")]
            if len(mine) != len(theirs):
                return [("reference", f"len({label}) is {len(mine)}, len({rname}{args}) is {len(theirs)}")]
        except Exception:
            continue
    for (a1, m1, t1), (a2, m2, t2) in itertools.combinations(pool[:120], 2):
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
    for args, kwargs, *_ in seeds:
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

def _caller_module(depth=2):
    try:
        return sys._getframe(depth).f_globals.get("__name__", "")
    except ValueError:
        return ""


class Target:
    def __init__(self, module, name, obj, templates, out_path, replay=None):
        self.module, self.name, self.obj = module, name, obj
        self.templates, self.out_path = templates, out_path
        self.seeds, self.keys, self.findings = [], set(), []
        self.rng = random.Random(1234)
        self.done = False
        self.live = []  # weak references to instances the tests built (classes), checked as the tests left them
        self.replayed = replay is not None
        if replay is not None:  # the run on the starting commit replays exactly the seeds of the run on the change
            self.seeds = replay

    def add_seed(self, args, kwargs, cls=None):
        # Only calls made by the tests count: the library calling itself (e.g. type(self)(...) inside a method)
        # depends on the code under test, so the two runs would see different seeds.
        if self.done or self.replayed or len(self.seeds) >= SEEDS or _caller_module(3) == self.module:
            return
        key = (cls.__name__ if cls else "",) + tuple((type(a).__name__, (a > 0) - (a < 0) if _num(a) else None)
                                                     for a in args) + tuple(sorted(kwargs))
        if key in self.keys:
            return
        self.keys.add(key)
        # Copies: the function under test may consume or change its arguments.
        self.seeds.append((tuple(list(a) if isinstance(a, list) else a for a in args), dict(kwargs), cls))

    def add_live(self, obj):
        if self.done or len(self.live) >= 30 or _caller_module(3) == self.module:
            return
        try:
            self.live.append(weakref.ref(obj))
        except TypeError:
            pass  # no weak references (e.g. __slots__ without __weakref__): skip rather than keep it alive

    def run(self):
        if self.done or not self.seeds:
            self.save([])
            return
        self.done = True
        found = []
        self.save(found)
        is_class = inspect.isclass(self.obj)
        if is_class:
            found += check_reference(self.obj, self.name, self.seeds, self.rng, self.templates)
        else:
            found += check_single_pass(self.obj, self.name, self.seeds, self.templates)
        made = []

        def add(fs):
            for f in fs:
                if f[0] not in {k for k, _ in found}:
                    found.append(f)
                    self.save(found)
        # Bounded by count, not time, and the same seeds on both runs: the run on the starting commit tries
        # exactly the same calls.
        per_seed = max(20, VARIANTS * 2 // max(1, len(self.seeds)))
        for args, kwargs, *rest in self.seeds:
            ctor = (rest[0] if rest and rest[0] is not None else self.obj)
            for v in ([args] + variations(args, self.rng))[:per_seed]:
                if len(made) > 600:
                    break
                try:
                    x = ctor(*v, **kwargs)
                except Exception:
                    continue
                if not is_class and inspect.isgenerator(x):
                    continue
                label = _call_text(ctor.__name__ if is_class else self.name, v, kwargs)
                made.append((label, x))
                add(contracts.check(x, label, self.templates, others=made[-6:-1]))
        live = [r() for r in self.live]
        live = [x for x in live if x is not None]
        for i, x in enumerate(live):
            label = f"{type(x).__name__} instance #{i + 1} as a test left it ({contracts._short(x, 50)})"
            others = [(f"{self.name} instance #{j + 1}", o) for j, o in enumerate(live) if o is not x][:4] + made[:4]
            add(contracts.check(x, label, self.templates, others=others))
        add(contracts.values([(lb, x) for lb, x in made if "value" in contracts.kinds(x)][:200], self.templates))
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
                json.dump({"function": self.name, "seeds": len(self.seeds), "replayed": self.replayed,
                           "done": self.done, "findings": self.findings}, fh)
        except OSError:
            pass
        if not self.replayed:
            try:  # the seeds, for the run on the starting commit
                with open(self.out_path + ".seeds", "wb") as fh:
                    pickle.dump(self.seeds, fh)
            except Exception:
                pass


def _plain_function(obj):
    return (inspect.isfunction(obj) and not inspect.iscoroutinefunction(obj)
            and not inspect.isgeneratorfunction(obj) and not inspect.isasyncgenfunction(obj))


def install():
    """Called from sitecustomize: wrap each target so the tests' own calls become seeds."""
    spec = json.loads(os.environ.get("MIHAD_PROP_TARGETS", "[]"))
    templates = set(json.loads(os.environ.get("MIHAD_PROP_TEMPLATES", "[]")))
    out_dir = os.environ.get("MIHAD_PROP_OUT", "")
    seeds_from = os.environ.get("MIHAD_PROP_SEEDS_FROM", "")
    # Subprocesses the tests start inherit this environment: only this process checks.
    os.environ["MIHAD_PROP_TARGETS"] = "[]"
    targets = []
    import importlib
    for module, name in spec:
        try:
            m = importlib.import_module(module)
            obj = getattr(m, name)
        except Exception:
            continue
        replay = None
        if seeds_from:
            try:
                with open(os.path.join(seeds_from, f"{module}.{name}.json.seeds"), "rb") as fh:
                    replay = pickle.load(fh)
            except Exception:
                replay = []  # nothing to replay: the comparison is unknown, not "no failure at the start"
        t = Target(module, name, obj, templates, os.path.join(out_dir, f"{module}.{name}.json"), replay)
        targets.append(t)
        if inspect.isclass(obj):
            if "__init__" in vars(obj):
                orig_init = obj.__init__

                @functools.wraps(orig_init)
                def init(self, *a, __orig=orig_init, __t=t, __cls=obj, **k):
                    __orig(self, *a, **k)
                    # The changed class is often a base class and the tests build its subclasses; their
                    # objects run the changed code too. Only the outermost __init__ call records (a subclass
                    # __init__ calling super().__init__ with other arguments would give wrong seeds).
                    if isinstance(self, __cls) and type(self).__init__ is __cls.__init__:
                        __t.add_seed(a, k, type(self))
                        __t.add_live(self)
                try:
                    obj.__init__ = init
                except (TypeError, AttributeError):
                    continue
            elif "__new__" in vars(obj):  # immutable classes (tuple subclasses) build in __new__
                orig_new = obj.__new__

                def new(cls, *a, __orig=orig_new, __t=t, __cls=obj, **k):
                    inst = __orig(cls, *a, **k)
                    if issubclass(cls, __cls) and cls.__new__ is __cls.__new__:
                        __t.add_seed(a, k, cls)
                        __t.add_live(inst)
                    return inst
                try:
                    obj.__new__ = staticmethod(new)
                except (TypeError, AttributeError):
                    continue
        elif _plain_function(obj) or (inspect.isgeneratorfunction(obj) and not inspect.isasyncgenfunction(obj)):
            if inspect.isgeneratorfunction(obj):
                # A generator wrapper keeps the function a generator function (inspect sees the same kind)
                # and keeps it lazy, like the original.
                def wrapper(*a, __f=obj, __t=t, **k):
                    __t.add_seed(a, k)
                    return (yield from __f(*a, **k))
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
                os._exit(0)  # the exit code of this run is never used: the tests' result comes from other runs
            threading.Thread(target=watchdog, daemon=True).start()
            for t in targets:
                try:
                    t.run()
                except Exception:
                    t.save(list((f["template"], f["detail"]) for f in t.findings))
        atexit.register(finish)
    return targets
