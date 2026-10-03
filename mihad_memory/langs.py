"""Language support: detection, symbols (functions, methods, classes and their line ranges),
test files, focused test commands, and comment syntax, for the most used languages.

Python uses the standard `ast` module. The other languages use a small, dependency-free scanner:
declaration patterns plus brace matching (with strings and comments masked), or `end` matching for
Ruby. It is not a compiler front end, but it is enough to find which function a changed line
belongs to, which is all the engine needs. Functions nested inside classes are reported by their
own name (the innermost declaration wins), so families are methods, not whole classes.
"""
import ast
import json
import re
from pathlib import Path

# name: extensions, marker files, test file patterns, line-comment prefixes
LANGS = {
    "python": {"exts": (".py",), "markers": ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"),
               "tests": (r"(^|/)test_[^/]*\.py$", r"_test\.py$"), "comment": ("#",)},
    "typescript": {"exts": (".ts", ".tsx", ".mts", ".cts"), "markers": ("tsconfig.json",),
                   "tests": (r"\.(test|spec)\.[cm]?tsx?$", r"(^|/)__tests__/"), "comment": ("//", "/*", "*")},
    "javascript": {"exts": (".js", ".jsx", ".mjs", ".cjs"), "markers": ("package.json",),
                   "tests": (r"\.(test|spec)\.[cm]?jsx?$", r"(^|/)__tests__/"), "comment": ("//", "/*", "*")},
    "java": {"exts": (".java",), "markers": ("pom.xml", "build.gradle", "build.gradle.kts"),
             "tests": (r"Tests?\.java$", r"(^|/)src/test/"), "comment": ("//", "/*", "*")},
    "kotlin": {"exts": (".kt", ".kts"), "markers": ("build.gradle.kts", "settings.gradle.kts"),
               "tests": (r"Tests?\.kt$", r"(^|/)src/test/"), "comment": ("//", "/*", "*")},
    "csharp": {"exts": (".cs",), "markers": (".csproj", ".sln"),
               "tests": (r"Tests?\.cs$", r"(^|/)[^/]*\.Tests?/"), "comment": ("//", "/*", "*")},
    "go": {"exts": (".go",), "markers": ("go.mod",), "tests": (r"_test\.go$",), "comment": ("//", "/*", "*")},
    "rust": {"exts": (".rs",), "markers": ("Cargo.toml",), "tests": (r"(^|/)tests/[^/]*\.rs$",),
             "comment": ("//", "/*", "*")},
    "php": {"exts": (".php",), "markers": ("composer.json",), "tests": (r"Test\.php$",),
            "comment": ("//", "#", "/*", "*")},
    "ruby": {"exts": (".rb",), "markers": ("Gemfile", ".gemspec"), "tests": (r"_spec\.rb$", r"_test\.rb$"),
             "comment": ("#",)},
    "cpp": {"exts": (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h", ".c"), "markers": ("CMakeLists.txt", "Makefile"),
            "tests": (r"(^|/)test_[^/]*\.(c|cc|cpp)$", r"_test\.(c|cc|cpp)$", r"(^|/)tests?/"),
            "comment": ("//", "/*", "*")},
}
KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "else", "do", "try", "new", "throw",
            "await", "typeof", "sizeof", "using", "lock", "foreach", "synchronized", "match", "loop", "unsafe",
            "constructor", "super", "this", "elif", "when", "select", "defer", "go", "case", "default"}
DECLS = {
    "typescript": [r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*[<(]",
                   r"\b(?:class|interface|enum)\s+([A-Za-z_$][\w$]*)",
                   r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)",
                   # class methods: "name(args) {" or "name(args): Type {" (a call has no ")" right before "{")
                   r"^\s*(?:(?:public|private|protected|static|async|readonly|override|abstract|get|set)\s+)*\*?\s*([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\([^;{]*\)\s*(?::\s*[^={;]+)?\{\s*$"],
    "java": [r"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)",
             r"^\s*(?!return\b|new\b|throw\b|else\b)(?:(?:public|private|protected|static|final|abstract|synchronized|native|default)\s+)*(?:<[^>]+>\s*)?[\w<>\[\],.?]+(?:\s+[\w<>\[\],.?]+)*?\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:throws\s+[\w.,\s]+)?\{?\s*$"],
    "kotlin": [r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)\s*\(",
               r"\b(?:class|interface|object|enum class|data class)\s+([A-Za-z_]\w*)"],
    "csharp": [r"\b(?:class|interface|enum|struct|record)\s+([A-Za-z_]\w*)",
               r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|async|sealed|abstract|extern|unsafe|new|partial)\s+)+[\w<>\[\],.?\s]+?\s+([A-Za-z_]\w*)\s*(?:<[^>]+>)?\s*\([^;]*$"],
    "go": [r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*[\[(]", r"^type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b"],
    "rust": [r"\bfn\s+([A-Za-z_]\w*)\s*[<(]", r"\b(?:struct|enum|trait|union)\s+([A-Za-z_]\w*)",
             r"\bimpl(?:<[^>]*>)?\s+(?:[\w:<>, ]+\s+for\s+)?([A-Za-z_]\w*)"],
    "php": [r"\bfunction\s+&?\s*([A-Za-z_]\w*)\s*\(", r"\b(?:class|interface|trait|enum)\s+([A-Za-z_]\w*)"],
    "cpp": [r"\b(?:class|struct|namespace)\s+([A-Za-z_]\w*)\s*(?:final\s*)?(?::[^{;]*)?\{?\s*$",
            r"^\s*(?:[\w:*&<>,~]+\s+)*?[*&]*([A-Za-z_~][\w]*(?:::[A-Za-z_~]\w*)*)\s*\([^;]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?\{?\s*$"],
}
DECLS["javascript"] = DECLS["typescript"]
STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')


def language_of(path):
    p = str(path).lower()
    for name, spec in LANGS.items():
        if p.endswith(spec["exts"]):
            if name == "javascript" and p.endswith((".ts", ".tsx")):
                continue
            return name
    return None


def detect_language(root):
    """The project's main language: marker files first, then the most common source extension."""
    root = Path(root)
    names = {p.name for p in root.iterdir()} if root.is_dir() else set()
    if "tsconfig.json" in names:
        return "typescript"
    if names & {"pyproject.toml", "setup.py", "setup.cfg"}:
        return "python"
    for lang in ("go", "rust", "csharp", "kotlin", "java", "php", "ruby", "javascript", "cpp"):
        marks = LANGS[lang]["markers"]
        if any(n in names or any(x.endswith(m) for x in names) for n in marks for m in [n]):
            if lang == "kotlin" and not any(root.rglob("*.kt")):
                continue
            return lang
    counts = {}
    for p in root.rglob("*"):
        if any(part in ("node_modules", ".git", "vendor", "target", "build", "dist", "bin", "obj") for part in p.parts):
            continue
        lang = language_of(p)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return max(counts, key=counts.get) if counts else "python"


def is_test_path(path, lang=None):
    path = str(path).replace("\\", "/")
    langs = [lang] if lang else LANGS
    return any(re.search(pat, path) for name in langs for pat in LANGS[name]["tests"])


# ---------------------------------------------------------------- symbols

def _python_defs(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            out[node.name] = (start, node.end_lineno)
    return out


def _mask(line, in_block):
    """Line with strings and comments blanked (same length), and whether a /* */ block is still open."""
    out, i = [], 0
    line = STRING_RE.sub(lambda m: '"' + " " * (len(m.group(0)) - 2) + '"' if len(m.group(0)) >= 2 else m.group(0), line)
    while i < len(line):
        if in_block:
            j = line.find("*/", i)
            if j < 0:
                out.append(" " * (len(line) - i))
                return "".join(out), True
            out.append(" " * (j + 2 - i))
            i, in_block = j + 2, False
        elif line.startswith("//", i):
            out.append(" " * (len(line) - i))
            break
        elif line.startswith("/*", i):
            in_block = True
        else:
            out.append(line[i])
            i += 1
    return "".join(out), in_block


def _brace_defs(source, lang):
    lines = source.splitlines()
    pats = [re.compile(p) for p in DECLS[lang]]
    found, stack, depth, pending, in_block = [], [], 0, None, False
    for ln, raw in enumerate(lines, 1):
        line, in_block = _mask(raw, in_block)
        if pending is None:
            for pat in pats:
                m = pat.search(line)
                if m and m.group(1).split("::")[-1] not in KEYWORDS:
                    pending = (m.group(1).split("::")[-1], ln)
                    break
        for ch in line:
            if ch == "{":
                if pending is not None:
                    stack.append((pending[0], pending[1], depth))
                    pending = None
                depth += 1
            elif ch == "}":
                depth -= 1
                if stack and stack[-1][2] == depth:
                    name, start, _ = stack.pop()
                    found.append((name, start, ln))
        if pending is not None and (line.rstrip().endswith(";") or ln - pending[1] > 6):
            pending = None  # a declaration without a body (prototype, abstract method, call)
    return found


def _ruby_defs(source):
    found, stack = [], []
    opener = re.compile(r"^\s*(def\s+(?:self\.)?([\w?!=]+)|class\s+([\w:]+)|module\s+([\w:]+))")
    for ln, line in enumerate(source.splitlines(), 1):
        m = opener.match(line)
        indent = len(line) - len(line.lstrip())
        if m:
            name = m.group(2) or m.group(3) or m.group(4)
            if not re.search(r"\bend\s*$", line) or m.group(3) or m.group(4):
                stack.append((name.split("::")[-1], ln, indent))
                continue
            found.append((name, ln, ln))
        if re.match(r"^\s*end\b", line) and stack and stack[-1][2] == indent:
            name, start, _ = stack.pop()
            found.append((name, start, ln))
    return found


def defs(source, lang):
    """{name: (first_line, last_line)}. Python: module-level defs (as before). Others: every declaration;
    for a repeated name (overloads, same method in several classes) the first one is kept."""
    if lang in (None, "python"):
        return _python_defs(source)
    found = _ruby_defs(source) if lang == "ruby" else _brace_defs(source, lang) if lang in DECLS else []
    out = {}
    for name, a, b in sorted(found, key=lambda t: (t[1], -t[2])):
        out.setdefault(name, (a, b))
    return out


def symbols_at(source, lang, lines):
    """Names of the innermost declarations that contain any of the given line numbers."""
    if lang in (None, "python"):
        d = _python_defs(source)
        return {n for n, (a, b) in d.items() if any(a <= x <= b for x in lines)}
    found = _ruby_defs(source) if lang == "ruby" else _brace_defs(source, lang) if lang in DECLS else []
    out = set()
    for x in lines:
        inner = [(b - a, n) for n, a, b in found if a <= x <= b]
        if inner:
            out.add(min(inner)[1])
    return out


def comment_prefixes(lang):
    return LANGS.get(lang or "python", LANGS["python"])["comment"]


# ---------------------------------------------------------------- tests

def _package_json(root):
    try:
        return json.loads((Path(root) / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def detect_runner(root, lang):
    """(runner, full test command) for the project."""
    root = Path(root)
    if lang in ("javascript", "typescript"):
        pkg = _package_json(root)
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        script = pkg.get("scripts", {}).get("test", "")
        for runner in ("vitest", "jest", "mocha"):
            if runner in deps or runner in script:
                return runner, "npm test" if script else f"npx {runner}{' run' if runner == 'vitest' else ''}"
        if "node --test" in script or not script:
            return "node", script or "node --test"
        return "npm", "npm test"
    if lang in ("java", "kotlin"):
        if (root / "pom.xml").exists():
            return "maven", "mvn -q test"
        wrapper = "gradlew.bat" if (root / "gradlew.bat").exists() else "./gradlew" if (root / "gradlew").exists() else "gradle"
        return "gradle", f"{wrapper} test"
    if lang == "csharp":
        return "dotnet", "dotnet test"
    if lang == "go":
        return "go", "go test ./..."
    if lang == "rust":
        return "cargo", "cargo test"
    if lang == "php":
        return "phpunit", "vendor/bin/phpunit"
    if lang == "ruby":
        return ("rspec", "bundle exec rspec") if (root / "spec").is_dir() else ("minitest", "bundle exec rake test")
    if lang == "cpp":
        return "ctest", "ctest --test-dir build" if (root / "build").is_dir() else "make test"
    return None, None


def test_ids_for_file(path, root, runner, test_names=()):
    """How a focused run names one test file (or its classes / test functions)."""
    rel = Path(path).relative_to(Path(root)).as_posix()
    if runner in ("jest", "vitest", "mocha", "node", "npm", "phpunit", "rspec", "minitest"):
        return [rel]
    if runner in ("maven", "gradle", "dotnet"):
        return [Path(path).stem]
    if runner == "go":
        d = Path(rel).parent.as_posix()
        return [f"./{d}::{'|'.join(test_names)}" if test_names else f"./{d}"]
    if runner == "cargo":
        return list(test_names) or [Path(path).stem]
    return [rel]


def focused_command(runner, ids):
    """Command (argv list) that runs only the given test ids."""
    ids = list(ids)
    if runner == "jest":
        return ["npx", "jest", *ids]
    if runner == "vitest":
        return ["npx", "vitest", "run", *ids]
    if runner == "mocha":
        return ["npx", "mocha", *ids]
    if runner == "node":
        return ["node", "--test", *ids]
    if runner == "npm":
        return ["npm", "test", "--", *ids]
    if runner == "maven":
        return ["mvn", "-q", "test", f"-Dtest={','.join(ids)}", "-Dsurefire.failIfNoSpecifiedTests=false"]
    if runner == "gradle":
        return ["gradle", "test", *[x for i in ids for x in ("--tests", i)]]
    if runner == "dotnet":
        return ["dotnet", "test", "--filter", "|".join(f"FullyQualifiedName~{i}" for i in ids)]
    if runner == "go":
        dirs = [i.split("::")[0] for i in ids]
        names = [n for i in ids if "::" in i for n in i.split("::")[1].split("|")]
        return ["go", "test", *dirs, *(["-run", "^(" + "|".join(names) + ")$"] if names else [])]
    if runner == "cargo":
        return ["cargo", "test", *ids[:1]]
    if runner == "phpunit":
        return ["vendor/bin/phpunit", *ids]
    if runner == "rspec":
        return ["bundle", "exec", "rspec", *ids]
    if runner == "minitest":
        return ["ruby", "-Itest", *ids]
    return None


def test_function_names(source, lang):
    """Test function names inside a test file (Go: TestX; Rust: #[test] fns; others: not needed)."""
    if lang == "go":
        return re.findall(r"^func\s+(Test\w*)\s*\(", source, re.M)
    if lang == "rust":
        return re.findall(r"#\[test\]\s*(?:#\[[^\]]*\]\s*)*fn\s+(\w+)", source)
    return []
