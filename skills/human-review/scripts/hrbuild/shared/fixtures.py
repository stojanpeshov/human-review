"""Which DB fixture each E2E test starts from, and the colour each fixture wears.

A fixture is a named dataset the review environment can reset its database to — a dataset
of its own, written on an empty database, never a delta on the seed: a ``<name>.sql`` in a
folder called ``fixtures``. The seed itself is the fixture called Default. The colours are the project's to choose, in a
``fixture-colors.json`` beside those files::

    {"minimal": "#2fa84f"}

Beside the SQL and not in ``.human-review/``: whoever adds ``blue.sql`` is standing in
that folder, and the reset sidecar only ever reads ``*.sql`` from it, so the map rides
along harmlessly. The map is optional: a fixture it does not name takes a free colour of
``FIXTURE_PALETTE`` picked by a hash of its name (`fixture_hue`), so it wears the same
colour on every build and on every page, whatever else the folder holds. The seed is
always ``FIXTURE_SEED`` grey.

Per test the answer is read off the test's own code, never guessed from its name — and
the answer is only ever what the test DECLARES (Victor, 21 Oct 2026: "If a test does not use
a certain DB fixture, it should start from an empty slate"). A test that declares nothing
starts on an empty database and gets no dot; one that declares a fixture wears its dot.
An E2E test declares one with a ``@fixture:<name>`` tag (Cucumber, on the scenario or its
Feature; or Playwright's ``{tag: ...}``), a ``test.use({dbFixture: '<name>'})`` in scope
(the file's, or the innermost ``test.describe`` around the test), or a call that loads one —
``__reset/<name>``, ``loadFixture('<name>')``, ``resetTo('<name>')``, ``fixture('<name>')``.
``default``/``seed`` name the seed.

Two fixtures named by one test is the one case left unsaid: not ours to pick.

"The code the test runs" is, for a Cucumber scenario, the feature's tags and the step
definitions its Background and its own steps match; for a Playwright test, its body, the
file's hooks and helpers, and the local modules it imports (DSLs, page objects) — not
``support/``, which every test imports and which says nothing about any one of them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .util import _git

FIXTURE_SEED = "#8b929c"
# Seven, readable on the dark and the light theme alike, and far enough apart in hue that
# two dots side by side never read as one colour.
FIXTURE_PALETTE = ("#2fa84f", "#3b82f6", "#e5a50a", "#d9485f", "#9b59d0", "#14a3a3", "#e8772e")
FIXTURE_COLORS_FILE = "fixture-colors.json"

_FX_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
_FX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20}|(?:rgb|hsl)a?\([0-9\s.,%]+\)")
_FX_SPEC = re.compile(r"\.(?:spec|test|e2e)\.[cm]?[jt]sx?$")


def _fx_ls(root: Path) -> list[str]:
    listed = _git(root, "ls-files")
    if listed:
        return listed.splitlines()
    return [str(p.relative_to(root)) for p in root.rglob("*")
            if p.is_file() and "node_modules" not in p.parts and ".git" not in p.parts]


def fixture_hue(name: str, palette) -> str:
    """The palette colour a fixture's name hashes to. Spelt out rather than ``hash()``, which
    Python salts per process, and mirrored by ``fixtures.js`` for a name the build never saw."""
    h = 0
    for c in name:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return palette[h % len(palette)]


def fixture_dirs(files: list[str]) -> list[str]:
    return sorted({str(Path(f).parent) for f in files
                   if Path(f).parent.name == "fixtures" and f.endswith(".sql")})


def fixture_colors(root: Path, files: list[str] | None = None) -> dict:
    """``{"seed": grey, "colors": {name: colour}}`` for every fixture in the project.

    Configured colours first; the rest hash their name onto the palette, minus any colour
    the configuration already spent, so a fallback never repeats a chosen one."""
    files = _fx_ls(root) if files is None else files
    names, chosen = [], {}
    for d in fixture_dirs(files):
        for f in sorted(files):
            p = Path(f)
            if str(p.parent) == d and p.suffix == ".sql" and _FX_NAME.fullmatch(p.stem):
                if p.stem not in names:
                    names.append(p.stem)
        try:
            cfg = json.loads((root / d / FIXTURE_COLORS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cfg = {}
        for k, v in (cfg.items() if isinstance(cfg, dict) else ()):
            if isinstance(v, str) and _FX_COLOUR.fullmatch(v.strip()):
                chosen[str(k)] = v.strip()
    seed = chosen.pop("seed", None) or chosen.pop("default", None) or FIXTURE_SEED
    free = [c for c in FIXTURE_PALETTE if c.lower() not in {v.lower() for v in chosen.values()}]
    free = free or list(FIXTURE_PALETTE)
    colors = {n: chosen.get(n) or fixture_hue(n, free) for n in names}
    for n, c in chosen.items():
        colors.setdefault(n, c)
    return {"seed": seed, "colors": colors, "palette": free}


def _fx_strip_comments(src: str) -> str:
    """Comments out, strings kept: a comment is the author musing, a string is what runs."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1]); i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i); i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2); i = n if j < 0 else j + 2
        else:
            out.append(c); i += 1
    return "".join(out)


def _fx_explicit(text: str, known: set[str]) -> set[str]:
    found = set()
    for m in re.finditer(r"@fixture[:=-]([a-z0-9][a-z0-9-]*)", text):
        found.add(m.group(1))
    for m in re.finditer(r"__reset/([a-z0-9][a-z0-9-]*)", text):
        found.add(m.group(1))
    for m in re.finditer(r"\b(?:loadFixture|resetTo|useFixture|fixture)\(\s*['\"`]"
                         r"([a-z0-9][a-z0-9-]*)['\"`]", text):
        found.add(m.group(1))
    for m in re.finditer(r"\bdbFixture\s*:\s*['\"`]([a-z0-9][a-z0-9-]*)['\"`]", text):
        found.add(m.group(1))
    out = set()
    for f in found:
        if f in ("seed", "default"):
            out.add("seed")
        elif f in known:
            out.add(f)
    return out


def _fx_verdict(tags: str, code: str, known: set[str]) -> str | None:
    """The fixture the test declares — its name, or ``"seed"`` — ``""`` when it declares
    none (it starts empty), None when it names two."""
    hits = _fx_explicit(tags + "\n" + code, known)
    if len(hits) == 1:
        return hits.pop()
    if len(hits) > 1:
        return None                          # two fixtures named: not ours to pick
    if re.search(r"(?<![\w-])@seed(?![\w-])", tags):
        return "seed"
    return ""


# --- Cucumber ----------------------------------------------------------------------------

_FX_STEPDEF = re.compile(r"\b(?:Given|When|Then|And|But|defineStep)\(\s*(['\"`])((?:\\.|(?!\1).)*)\1")


def _fx_expr_to_regex(expr: str) -> re.Pattern | None:
    out, i = [], 0
    while i < len(expr):
        c = expr[i]
        if c == "{":
            j = expr.find("}", i)
            kind = expr[i + 1:j] if j > 0 else ""
            out.append({"word": r"\S+", "string": r"(?:\"[^\"]*\"|'[^']*')",
                        "int": r"-?\d+", "float": r"-?[\d.]+"}.get(kind, r".*"))
            i = j + 1 if j > 0 else len(expr)
        else:
            out.append(re.escape(c)); i += 1
    try:
        return re.compile("^" + "".join(out) + "$")
    except re.error:
        return None


def _fx_step_defs(root: Path, files: list[str]) -> list[tuple[re.Pattern, str]]:
    defs = []
    for f in files:
        if not re.search(r"\.[cm]?[jt]s$", f) or "node_modules" in f:
            continue
        try:
            src = (root / f).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "@cucumber/cucumber" not in src:
            continue
        code = _fx_strip_comments(src)
        ms = list(_FX_STEPDEF.finditer(code))
        for k, m in enumerate(ms):
            rx = _fx_expr_to_regex(m.group(2).replace("\\'", "'").replace('\\"', '"'))
            if rx is not None:
                end = ms[k + 1].start() if k + 1 < len(ms) else len(code)
                defs.append((rx, code[m.start():end]))
    return defs


_FX_KW = re.compile(r"^\s*(?:Given|When|Then|And|But|\*)\s+(.*)$")
_FX_BLOCK = re.compile(r"^\s*(Feature|Background|Scenario Outline|Scenario Template|Scenario|"
                    r"Example|Rule|Examples|Scenarios):")


def _fx_feature_tests(rel: str, src: str, defs, known: set[str]) -> dict[str, str]:
    lines = src.splitlines()
    feature_tags, pending, background = "", "", []
    scenarios = []                              # (line, tags, steps)
    cur = None
    for no, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith("@"):
            pending += " " + s
            continue
        m = _FX_BLOCK.match(line)
        if m:
            kind = m.group(1)
            if kind == "Feature":
                feature_tags, cur = pending, None
            elif kind == "Background":
                cur = background
            elif kind.startswith("Scenario") or kind == "Example":
                cur = []
                scenarios.append((no, pending, cur))
            else:
                cur = None if kind in ("Examples", "Scenarios") else cur
            pending = ""
            continue
        k = _FX_KW.match(line)
        if k and cur is not None:
            cur.append(re.sub(r"<[^>]+>", "X", k.group(1).strip()))
    out = {}
    for no, tags, steps in scenarios:
        code = []
        for step in background + steps:
            for rx, body in defs:
                if rx.match(step):
                    code.append(body)
                    break
        own = _fx_explicit(tags, known)           # the scenario's own tag beats its Feature's
        v = own.pop() if len(own) == 1 else _fx_verdict(feature_tags + " " + tags,
                                                         "\n".join(code), known)
        if v is not None:
            out[f"{rel}:{no}"] = v
    return out


# --- Playwright --------------------------------------------------------------------------

_FX_TEST = re.compile(r"^\s*test(?:\.(?:only|fixme|slow))?\(\s*['\"`]", re.M)


def _fx_block_end(code: str, start: int) -> int:
    """End of the `(...)` call opened at the first `(` from `start`."""
    i = code.find("(", start)
    depth = 0
    while 0 <= i < len(code):
        c = code[i]
        if c in "'\"`":
            j = i + 1
            while j < len(code) and code[j] != c:
                j += 2 if code[j] == "\\" else 1
            i = j
        elif c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(code)


def _fx_local_imports(root: Path, rel: str, code: str) -> str:
    here = (root / rel).parent
    texts = []
    for m in re.finditer(r"""from\s+['"](\.{1,2}/[^'"]+)['"]""", code):
        spec = m.group(1)
        if "/support/" in spec + "/" or spec.startswith("./support"):
            continue
        for ext in ("", ".ts", ".js", ".tsx", ".mjs", "/index.ts", "/index.js"):
            p = (here / (spec + ext)).resolve()
            if p.is_file():
                try:
                    texts.append(_fx_strip_comments(p.read_text(encoding="utf-8")))
                except (OSError, UnicodeDecodeError):
                    pass
                break
    return "\n".join(texts)


_FX_DESCRIBE = re.compile(r"\btest\.describe(?:\.(?:serial|parallel|only|skip|fixme))?\(\s*['\"`]")
_FX_USE = re.compile(r"\btest\.use\(")


def _fx_blank(code: str, a: int, b: int) -> str:
    """`code` with [a, b) spaced out, newlines kept: offsets and line numbers stay put."""
    return code[:a] + re.sub(r"[^\n]", " ", code[a:b]) + code[b:]


def _fx_spec_tests(root: Path, rel: str, src: str, known: set[str]) -> dict[str, str]:
    if "@playwright/test" not in src and "trace-fixture" not in src:
        return {}
    code = _fx_strip_comments(src)
    # `test.use({...})` is scoped: the file's when at top level, else the innermost
    # `test.describe` around it, and an inner one wins. So each one is lifted out of the text
    # every test shares and handed only to the tests in its scope.
    describes = [(m.start(), _fx_block_end(code, m.start())) for m in _FX_DESCRIBE.finditer(code)]
    uses = []                                     # (scope start, scope end, text)
    for m in _FX_USE.finditer(code):
        end = _fx_block_end(code, m.start())
        scope = min((d for d in describes if d[0] < m.start() < d[1]),
                    key=lambda d: d[1] - d[0], default=(0, len(code)))
        uses.append((scope[0], scope[1], code[m.start():end]))
        code = _fx_blank(code, m.start(), end)
    # The comment-stripped text keeps every newline, so offsets map back to source lines
    # through the original — count them in `src` by matching the same test openers there.
    src_lines = [src[:m.start()].count("\n") + 1 + (m.group(0)[:1] == "\n")
                 for m in _FX_TEST.finditer(src)]
    spans = [(m.start(), _fx_block_end(code, m.start())) for m in _FX_TEST.finditer(code)]
    if len(spans) != len(src_lines):
        return {}
    shared, last = [], 0
    for a, b in spans:
        shared.append(code[last:a]); last = b
    shared.append(code[last:])
    shared_text = "".join(shared) + "\n" + _fx_local_imports(root, rel, code)
    out = {}
    for (a, b), line in zip(spans, src_lines):
        body = code[a:b]
        tags = " ".join(re.findall(r"@[\w:=-]+", body))
        use = min((u for u in uses if u[0] <= a < u[1] and _fx_explicit(u[2], known)),
                  key=lambda u: u[1] - u[0], default=None)
        v = _fx_verdict(tags + "\n" + (use[2] if use else ""), body + "\n" + shared_text, known)
        if v is not None:
            out[f"{rel}:{line}"] = v
    return out


def _fx_scan(root: Path, files: list[str], known: set[str]) -> dict[str, str]:
    """Every E2E test read: ``{id: fixture | "seed" | ""}`` (see `_fx_verdict`)."""
    out: dict[str, str] = {}
    features = [f for f in files if f.endswith(".feature") and "node_modules" not in f]
    defs = _fx_step_defs(root, files) if features else []
    for f in features:
        try:
            out.update(_fx_feature_tests(f, (root / f).read_text(encoding="utf-8"), defs, known))
        except (OSError, UnicodeDecodeError):
            continue
    for f in files:
        if _FX_SPEC.search(f) and "node_modules" not in f:
            try:
                out.update(_fx_spec_tests(root, f, (root / f).read_text(encoding="utf-8"), known))
            except (OSError, UnicodeDecodeError):
                pass
    return out


def fixtures_by_test(root: Path, files: list[str] | None = None) -> dict[str, str]:
    """``{"<repo-relative file>:<line>": fixture}`` — ``"seed"`` or a fixture's name —
    for every E2E test that starts on a dataset. Absent = it starts on an empty
    database (it declares none), or the answer is not ours to give."""
    root = Path(root)
    files = _fx_ls(root) if files is None else files
    known = set(fixture_colors(root, files)["colors"])
    return {k: v for k, v in _fx_scan(root, files, known).items() if v}


def fixture_registry(root: Path) -> dict:
    """The colours, and ``tests``: every test that starts on a dataset, by id. A test that
    starts empty is not in it, and its row gets no dot."""
    root = Path(root)
    files = _fx_ls(root)
    reg = fixture_colors(root, files)
    reg["tests"] = {k: v for k, v in _fx_scan(root, files, set(reg["colors"])).items() if v}
    return reg


def render_fixture_registry(root: Path) -> str:
    """The ``hr-fixtures`` JSON block FIXTURES_JS reads, or "" when the project has no
    fixtures and no test starts on the seed — then neither tab draws a dot."""
    reg = fixture_registry(root)
    if not reg["colors"] and not reg["tests"]:
        return ""
    return ('<script type="application/json" id="hr-fixtures">'
            + json.dumps(reg).replace("</", "<\\/") + "</script>")


if __name__ == "__main__":
    import sys
    print(json.dumps(fixture_registry(Path(sys.argv[1] if len(sys.argv) > 1 else ".")), indent=1))
