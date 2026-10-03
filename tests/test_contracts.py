"""The contract ontology: kinds are inferred from interfaces, and each kind is checked against its own
contract only (a mapping is never checked as a sequence)."""
import io
import sys
import unittest
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mihad_memory.experience import contracts  # noqa: E402

ALL = set(contracts.BEHAVIOUR)


class Seq:
    """A sequence whose negative indexing wraps around wrongly (x[-n-1] returns an item)."""

    def __init__(self, items):
        self.items = list(items)

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __getitem__(self, i):
        if isinstance(i, slice):
            return self.items[i]
        return self.items[i % len(self.items)]


class MultiDict(dict):
    """A mapping: x[-3] raising KeyError is correct here."""


class BadCopyMap(dict):
    def __copy__(self):
        return BadCopyMap({k: None for k in self})


class FlatSet:
    """A set-like object whose <= is wrong (it compares lengths)."""

    def __init__(self, items):
        self.s = set(items)

    def __iter__(self):
        return iter(self.s)

    def __len__(self):
        return len(self.s)

    def __contains__(self, x):
        return x in self.s

    def __or__(self, other):
        return FlatSet(self.s | set(other))

    def __le__(self, other):
        return len(self) <= len(other)

    def issubset(self, other):
        return self.s <= set(other)


class Vec:
    def __init__(self, x):
        self.x = x

    def __eq__(self, other):
        return isinstance(other, Vec) and abs(self.x) == abs(other.x)

    def __hash__(self):
        return hash(self.x)


class ContractTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(contracts.kinds(Seq([1, 2])), {"sequence"})
        self.assertIn("mapping", contracts.kinds(MultiDict(a=1)))
        self.assertNotIn("sequence", contracts.kinds(MultiDict(a=1)))
        self.assertIn("set", contracts.kinds(FlatSet([1])))
        self.assertIn("stream", contracts.kinds(io.BytesIO(b"abc")))
        self.assertEqual(contracts.kinds(5), set())

    def test_out_of_range_index_must_raise(self):
        found = contracts.check(Seq([1, 2, 3]), "Seq([1, 2, 3])", ALL)
        self.assertTrue(any(t == "getitem" and "IndexError" in d for t, d in found), found)
        self.assertEqual(contracts.check([1, 2, 3], "[1, 2, 3]", ALL), [])

    def test_mapping_is_not_checked_as_a_sequence(self):
        self.assertEqual(contracts.check(MultiDict(a=1, b=2, c=3), "md", ALL), [])
        self.assertEqual(contracts.check(OrderedDict(a=1), "od", ALL), [])

    def test_mapping_copy(self):
        found = contracts.check(BadCopyMap(a=1), "m", ALL)
        self.assertTrue(any(t == "m_copy" for t, _ in found), found)

    def test_set_compare(self):
        a, b = FlatSet([1, 2]), FlatSet([3, 4, 5])
        found = contracts.check(a, "a", ALL, others=[("b", b)])
        self.assertTrue(any(t == "s_compare" for t, _ in found), found)
        self.assertEqual(contracts.check(frozenset([1, 2]), "f", ALL, others=[("g", frozenset([2, 3]))]), [])

    def test_stream_is_quiet_when_correct(self):
        self.assertEqual(contracts.check(io.BytesIO(b"hello"), "b", ALL), [])
        self.assertEqual(contracts.check(io.StringIO("hello"), "s", ALL), [])

    def test_values_eq_hash(self):
        found = contracts.values([("Vec(1)", Vec(1)), ("Vec(-1)", Vec(-1))], ALL)
        self.assertTrue(any(t == "eq_hash" for t, _ in found), found)


class SizedIterator:
    def __init__(self, n):
        self.n, self.i = n, 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.i >= self.n:
            raise StopIteration
        self.i += 1
        return self.i

    def __len__(self):
        return self.n - self.i


class ReviewFindingsTests(unittest.TestCase):
    def test_iterator_is_not_consumed(self):
        it = SizedIterator(5)
        self.assertEqual(contracts.check(it, "it", ALL), [])
        self.assertEqual(it.i, 0)

    def test_nan_items_are_not_mismatches(self):
        self.assertEqual(contracts.check([float("nan"), 1.0], "l", ALL), [])
        self.assertEqual(contracts.check({"a": float("nan")}, "d", ALL), [])

    def test_mapping_without_value_equality_skips_copy(self):
        class Cfg(dict):
            pass

        class Plain:
            def keys(self):
                return ["a"]

            def items(self):
                return [("a", 1)]

            def __getitem__(self, k):
                return 1
        self.assertEqual(contracts.check(Plain(), "p", ALL), [])

    def test_or_alone_does_not_make_a_set(self):
        class Interval:
            def __init__(self, a, b):
                self.a, self.b = a, b

            def __or__(self, o):
                return Interval(min(self.a, o.a), max(self.b, o.b))

            def __contains__(self, x):
                return self.a <= x <= self.b

            def __iter__(self):
                return iter(range(self.a, self.b + 1))
        self.assertNotIn("set", contracts.kinds(Interval(1, 3)))

    def test_wrappers_keep_kinds_and_new_only_classes(self):
        import inspect
        import json
        import os
        import subprocess
        import tempfile
        mod = ("import collections\n"
               "def gen(xs):\n    for x in xs:\n        yield x\n"
               "class Pair(collections.namedtuple('P', 'a b')):\n    pass\n"
               "class Pt(tuple):\n    def __new__(cls, a, b):\n        return super().__new__(cls, (a, b))\n")
        test = ("import inspect, m\n"
                "assert inspect.isgeneratorfunction(m.gen)\n"
                "assert list(m.gen([1, 2])) == [1, 2]\n"
                "assert m.Pair(1, 2).a == 1\n"
                "assert m.Pt(1, 2) == (1, 2)\n"
                "print('OK')\n")
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "m.py"), "w").write(mod)
            open(os.path.join(tmp, "t.py"), "w").write(test)
            open(os.path.join(tmp, "sitecustomize.py"), "w").write(
                "from mihad_memory.experience import propcheck as _p\n_p.install()\n")
            env = dict(os.environ, PYTHONPATH=os.pathsep.join([tmp, str(Path(__file__).resolve().parents[1])]),
                       MIHAD_PROP_TARGETS=json.dumps([["m", "gen"], ["m", "Pair"], ["m", "Pt"]]),
                       MIHAD_PROP_TEMPLATES=json.dumps(sorted(ALL)), MIHAD_PROP_OUT=tmp)
            res = subprocess.run([sys.executable, "t.py"], cwd=tmp, env=env, capture_output=True, text=True)
            self.assertIn("OK", res.stdout, res.stderr)
            self.assertTrue(inspect)


class BehaviourTests(unittest.TestCase):
    def test_task_behaviours(self):
        from mihad_memory.experience import probes
        self.assertIn("copying", probes.task_behaviours("Fix deepcopy of the mapping"))
        self.assertIn("seeking", probes.task_behaviours("accept relative seek at EOF"))
        self.assertTrue(probes.relevant("m_copy", "copy.copy loses values"))
        self.assertFalse(probes.relevant("m_copy", "rename a parameter"))


if __name__ == "__main__":
    unittest.main()
