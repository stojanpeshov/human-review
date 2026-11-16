#!/usr/bin/env python3
"""
react_routes — the static screens of a React Router app, read off its route modules.

The design-system audit shoots every screen of the app, so it needs the app's catalogue,
and on mitt-ludvig that catalogue is `frontend/src/routes.tsx`: an array of
`{path, element, subRoutes}` objects whose paths are calls into a helper object
(`AppRouteHelper.getCustomerPage()` → `/assignments/customers/${customerId}/customer-page`,
the parameter defaulted to `":customerId"`), with the sub-route arrays imported from
modules of their own.

The modules are parsed, never executed: running them needs the app's whole module graph
(Vite aliases, JSX, the private UI package), and what is wanted is a list of strings that
is already spelled out in the source. So this reads a small, honest subset of
TypeScript — string and template literals, a helper object of arrow functions with
default parameters, a ternary on a parameter, `let x = …; return x` — and anything
outside it (`AppConfig.POWER_BI_URL`) is reported unresolved rather than guessed.

    ./react_routes.py frontend/src/routes.tsx            # JSON: [{path, component, file}]

`plan_screens` turns the routes into the audit's `--screen NAME=PATH` catalogue: a static
route is a screen; a route with a `:param` needs a value from `steps.dsaudit.screens`
(`{"Customer page": "assignments/customers/1001/customer-page"}`) and is reported as not
visited until it has one.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

EXTS = (".tsx", ".ts", ".jsx", ".js")


# ── a scanner for the subset of TypeScript route modules are written in ────────────────

def _skip_string(src: str, i: int) -> int:
    """Index just past the string or template literal that starts at `src[i]`."""
    q = src[i]
    i += 1
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if q == "`" and src.startswith("${", i):
            i = _match(src, i + 1) + 1
            continue
        if c == q:
            return i + 1
        if c == "\n" and q != "`":
            return i + 1               # an unterminated quote: JSX text with an apostrophe
        i += 1
    return i


def strip_comments(src: str) -> str:
    """The source with `//` and `/* */` comments blanked, strings left alone."""
    out, i = [], 0
    while i < len(src):
        c = src[i]
        if c in "'\"`":
            j = _skip_string(src, i)
            out.append(src[i:j])
            i = j
        elif src.startswith("//", i) and (i == 0 or src[i - 1] != ":"):
            j = src.find("\n", i)
            i = len(src) if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = len(src) if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


_CLOSE = {"(": ")", "[": "]", "{": "}"}


def _match(src: str, i: int) -> int:
    """Index of the bracket closing the one at `src[i]` (strings skipped)."""
    stack = [_CLOSE[src[i]]]
    i += 1
    while i < len(src) and stack:
        c = src[i]
        if c in "'\"`":
            i = _skip_string(src, i)
            continue
        if c in _CLOSE:
            stack.append(_CLOSE[c])
        elif c in ")]}":
            if c == stack[-1]:
                stack.pop()
                if not stack:
                    return i
        i += 1
    return len(src) - 1


def split_top(text: str, sep: str = ",") -> list[str]:
    """`text` cut at every `sep` outside brackets and strings; empty pieces dropped."""
    out, depth, start, i = [], 0, 0, 0
    while i < len(text):
        c = text[i]
        if c in "'\"`":
            i = _skip_string(text, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(sep, i):
            out.append(text[start:i])
            i += len(sep)
            start = i
            continue
        i += 1
    out.append(text[start:])
    return [p.strip() for p in out if p.strip()]


def _top_index(text: str, ch: str) -> int:
    """First `ch` outside brackets and strings, or -1."""
    depth, i = 0, 0
    while i < len(text):
        c = text[i]
        if c in "'\"`":
            i = _skip_string(text, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c == ch:
            return i
        i += 1
    return -1


def object_entries(body: str) -> dict[str, str]:
    """`{a: x, b: y}`'s inside → `{"a": "x", "b": "y"}`. Shorthand and spreads are skipped."""
    out = {}
    for part in split_top(body):
        m = re.match(r"""^["']?([A-Za-z_$][\w$]*)["']?\s*:\s*""", part, re.S)
        if m:
            out[m.group(1)] = part[m.end():].strip()
    return out


# ── helper objects and the expressions that call them ─────────────────────────────────

_ARROW = re.compile(r"^(?:async\s+)?(?:\(([^)]*(?:\([^)]*\)[^)]*)*)\)|([A-Za-z_$][\w$]*))\s*"
                    r"(?::\s*[^=]+?)?=>\s*", re.S)


class Helper:
    """One arrow function of a route-helper object: its parameters (with defaults) and body."""

    def __init__(self, params: list[tuple[str, str | None]], body: str):
        self.params, self.body = params, body


def _params(text: str) -> list[tuple[str, str | None]]:
    out = []
    for p in split_top(text or ""):
        default = None
        eq = _top_index(p, "=")
        if eq >= 0:
            p, default = p[:eq], p[eq + 1:].strip()
        name = re.match(r"\s*([A-Za-z_$][\w$]*)", p)
        if name:
            out.append((name.group(1), default))
    return out


def parse_helpers(src: str) -> dict[str, dict[str, Helper]]:
    """Every `const Name = { fn: (a = ":a") => `…`, … }` in the module, by name."""
    found: dict[str, dict[str, Helper]] = {}
    for m in re.finditer(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*\{", src):
        start = m.end() - 1
        body = src[start + 1:_match(src, start)]
        fns = {}
        for key, value in object_entries(body).items():
            a = _ARROW.match(value)
            if a:
                fns[key] = Helper(_params(a.group(1) if a.group(1) is not None
                                          else a.group(2)), value[a.end():].strip())
        if fns:
            found[m.group(1)] = fns
    return found


def _unwrap(expr: str) -> str:
    expr = expr.strip().rstrip(";").strip()
    expr = re.sub(r"\s+as\s+const$", "", expr)
    while expr.startswith("(") and _match(expr, 0) == len(expr) - 1:
        expr = expr[1:-1].strip()
    return expr


class Scope:
    """What an expression can see: helper objects (this module's and those it imports)."""

    def __init__(self, helpers: dict[str, dict[str, Helper]]):
        self.helpers = helpers


def evaluate(expr: str, env: dict[str, str | None], scope: Scope) -> str | None:
    """A route path expression → its string, or None when it is outside the subset."""
    expr = _unwrap(expr)
    if not expr:
        return None
    if expr[0] in "'\"" and _skip_string(expr, 0) == len(expr):
        return expr[1:-1]
    if expr[0] == "`" and _skip_string(expr, 0) == len(expr):
        out, i, body = [], 0, expr[1:-1]
        while i < len(body):
            if body.startswith("${", i):
                j = _match(body, i + 1)
                v = evaluate(body[i + 2:j], env, scope)
                if v is None:
                    return None
                out.append(v)
                i = j + 1
            else:
                out.append(body[i])
                i += 1
        return "".join(out)
    q = _top_index(expr, "?")
    if q > 0 and not expr.startswith("?.", q):
        rest = expr[q + 1:]
        colon = _top_index(rest, ":")
        if colon >= 0:
            cond = _unwrap(expr[:q])
            truthy = bool(env.get(cond)) if re.fullmatch(r"[A-Za-z_$][\w$]*", cond) else False
            return evaluate(rest[:colon] if truthy else rest[colon + 1:], env, scope)
    parts = split_top(expr, "+")
    if len(parts) > 1:
        vals = [evaluate(p, env, scope) for p in parts]
        return None if any(v is None for v in vals) else "".join(vals)
    if re.fullmatch(r"[A-Za-z_$][\w$]*", expr):
        return env.get(expr)
    call = re.fullmatch(r"([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)\s*\((.*)\)", expr, re.S)
    if call:
        fn = scope.helpers.get(call.group(1), {}).get(call.group(2))
        if not fn:
            return None
        args = [evaluate(a, env, scope) for a in split_top(call.group(3))]
        inner: dict[str, str | None] = {}
        for k, (name, default) in enumerate(fn.params):
            if k < len(args):
                inner[name] = args[k]
            elif default is not None:
                inner[name] = evaluate(default, {}, scope)
            else:
                inner[name] = None
        return _run_body(fn.body, inner, scope)
    return None


def _drop_blocks(stmt: str) -> str:
    """`stmt` without the `if (…) { … } else { … }` blocks it starts with. Their effect
    (`path += "?x=1"` when an optional argument is given) is the optional part of a path,
    and the route table is the bare one; what follows them is the statement."""
    while True:
        m = re.match(r"^(?:else\s+if|if|else|for|while)\b\s*", stmt)
        if not m:
            return stmt
        i = m.end()
        if i < len(stmt) and stmt[i] == "(":
            i = _match(stmt, i) + 1
        rest = stmt[i:].lstrip()
        if rest.startswith("{"):
            stmt = rest[_match(rest, 0) + 1:].lstrip()
        else:
            return ""                    # a one-line body: the statement it was


def _run_body(body: str, env: dict[str, str | None], scope: Scope) -> str | None:
    """An arrow body: an expression, or a block of `let x = …` and a `return`."""
    body = body.strip()
    if not body.startswith("{"):
        return evaluate(body, env, scope)
    block = body[1:_match(body, 0)]
    env = dict(env)
    for stmt in split_top(block, ";"):
        stmt = _drop_blocks(stmt)
        decl = re.match(r"^(?:let|const|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*", stmt, re.S)
        if decl and decl.group(1) not in env:
            env[decl.group(1)] = evaluate(stmt[decl.end():], env, scope)
            continue
        ret = re.match(r"^return\b", stmt)
        if ret:
            return evaluate(stmt[ret.end():], env, scope)
    return None


# ── modules, imports and the route arrays ──────────────────────────────────────────────

class Module:
    def __init__(self, path: Path, src_root: Path, cache: dict):
        self.path, self.src_root, self.cache = path, src_root, cache
        self.src = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        self.imports: dict[str, tuple[str, str]] = {}      # local name → (spec, exported)
        for m in re.finditer(r"import\s+(?:type\s+)?([^;]*?)\s+from\s+[\"']([^\"']+)[\"']",
                             self.src, re.S):
            clause, spec = m.group(1), m.group(2)
            default = re.match(r"^([A-Za-z_$][\w$]*)", clause)
            if default:
                self.imports[default.group(1)] = (spec, "default")
            named = re.search(r"\{([^}]*)\}", clause)
            for item in split_top(named.group(1) if named else ""):
                item = re.sub(r"^type\s+", "", item)
                bits = re.split(r"\s+as\s+", item)
                self.imports[bits[-1].strip()] = (spec, bits[0].strip())
        self.local_helpers = parse_helpers(self.src)

    def resolve(self, spec: str) -> Path | None:
        if spec.startswith("@/"):
            base = self.src_root / spec[2:]
        elif spec.startswith("."):
            base = (self.path.parent / spec).resolve()
        else:
            return None
        for cand in [base] + [base.with_name(base.name + e) for e in EXTS] \
                + [base / f"index{e}" for e in EXTS]:
            if cand.is_file():
                return cand
        return None

    def module(self, spec: str) -> "Module | None":
        p = self.resolve(spec)
        if not p:
            return None
        if p not in self.cache:
            try:
                self.cache[p] = Module(p, self.src_root, self.cache)
            except OSError:
                return None
        return self.cache[p]

    def scope(self) -> Scope:
        helpers = dict(self.local_helpers)
        for local, (spec, exported) in self.imports.items():
            if local in helpers or not local[:1].isupper():
                continue
            mod = self.module(spec) if re.search(r"Helper|Routes?\b", local) else None
            if mod and exported in mod.local_helpers:
                helpers[local] = mod.local_helpers[exported]
        return Scope(helpers)

    def arrays(self) -> dict[str, str]:
        """Every `const name(: T)? = [ … ]` of the module: its name → the inside."""
        out = {}
        for m in re.finditer(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+?)?=\s*\[",
                             self.src):
            start = m.end() - 1
            out[m.group(1)] = self.src[start + 1:_match(self.src, start)]
        return out

    def default_export(self) -> str | None:
        m = re.search(r"export\s+default\s+([A-Za-z_$][\w$]*)\s*;?", self.src)
        return m.group(1) if m else None


#: Wrappers around a screen, not the screen: a context provider, a layout's outlet, a
#: fragment, an error boundary, a redirect.
_WRAPPER = re.compile(r"(Provider|Outlet|Fragment|Boundary|Suspense|Navigate)$")


def screen_component(element: str) -> str | None:
    """The component a route renders, read off its `element` JSX: the innermost capitalised
    tag that is not a provider or an outlet (`<Layout><CustomerMainPage /></Layout>` →
    `CustomerMainPage`). None for a layout that only provides and outlets."""
    names = [n for n in re.findall(r"<([A-Z][\w.]*)", element or "") if not _WRAPPER.search(n)]
    return names[-1] if names else None


def _routes_in(mod: Module, body: str, parent: str, out: list, seen: set):
    scope = mod.scope()
    for item in split_top(body):
        if not item.startswith("{"):
            continue
        entry = object_entries(item[1:_match(item, 0)])
        index = entry.get("index", "").strip() == "true"
        raw = entry.get("path")
        path = parent if index else (evaluate(raw, {}, scope) if raw else None)
        children = entry.get("subRoutes") or entry.get("children") or ""
        element = entry.get("element") or entry.get("Component") or ""
        component = screen_component(element) or (
            entry["Component"].strip() if entry.get("Component") else None)
        if path is not None and not path.startswith("/"):
            # Relative to a parent this reader could not resolve: as unknown as the parent.
            path = None if parent is None else (
                parent.rstrip("/") + "/" + path if path else parent)
        if raw and path is None:
            if component:
                out.append({"path": None, "expr": raw.strip(), "component": component,
                            "file": _file_of(mod, component), "unresolved": True})
        full = path if (path is not None or not raw) else None
        if full is None and not raw:
            full = parent
        if full and "*" not in full and component and not re.match(r"^<Navigate\b", element):
            key = full.rstrip("/") or "/"
            if key not in seen:
                seen.add(key)
                out.append({"path": key, "component": component,
                            "file": _file_of(mod, component)})
        children = children.strip()
        if children.startswith("["):
            _routes_in(mod, children[1:_match(children, 0)], full, out, seen)
        elif re.fullmatch(r"[A-Za-z_$][\w$]*", children):
            sub, name = _array_named(mod, children)
            if sub is not None:
                _routes_in(sub, name, full, out, seen)


def _array_named(mod: Module, ident: str) -> tuple[Module | None, str]:
    """The route array `ident` names, in this module or the one it imports it from."""
    here = mod.arrays()
    if ident in here:
        return mod, here[ident]
    spec = mod.imports.get(ident)
    sub = mod.module(spec[0]) if spec else None
    if not sub:
        return None, ""
    name = sub.default_export() if spec[1] == "default" else spec[1]
    arrays = sub.arrays()
    return (sub, arrays[name]) if name in arrays else (None, "")


def _file_of(mod: Module, component: str | None) -> str | None:
    if not component:
        return None
    spec = mod.imports.get(component.split(".")[0])
    p = mod.resolve(spec[0]) if spec else None
    return str(p) if p else None


def _src_root(entry: Path) -> Path:
    """The folder `@/` points at: the `src` the entry module lives in."""
    for p in [entry.parent, *entry.parents]:
        if p.name == "src":
            return p
    return entry.parent


def discover(entry: Path) -> list[dict]:
    """`[{path, component, file}]` for every route the entry module's arrays declare, in
    declaration order, nested sub-route modules followed. A path containing `*` (the
    catch-all) is left out, and so is a layout that renders nothing but its outlet: its
    children are the screens. A path outside the subset comes back with `unresolved`."""
    entry = entry.resolve()
    cache: dict = {}
    mod = Module(entry, _src_root(entry), cache)
    cache[entry] = mod
    out: list[dict] = []
    seen: set = set()
    arrays = mod.arrays()
    for name, body in arrays.items():
        if re.search(r"\{\s*path\s*:", body) or re.search(r"\{\s*index\s*:", body):
            _routes_in(mod, body, "", out, seen)
    root = entry.parent.parent if entry.parent.name == "src" else entry.parent
    for r in out:
        if r.get("file"):
            try:
                r["file"] = str(Path(r["file"]).relative_to(root.parent))
            except ValueError:
                pass
    return out


# ── routes → the audit's screen catalogue ──────────────────────────────────────────────

_PARAM = re.compile(r":([A-Za-z_]\w*)\??")


def params_of(path: str) -> list[str]:
    return _PARAM.findall(path or "")


def _pattern(path: str) -> re.Pattern:
    out = "".join(r"[^/]+" if _PARAM.fullmatch(seg) else re.escape(seg)
                  for seg in re.split(r"(:[A-Za-z_]\w*\??)", path.strip("/")))
    return re.compile(f"^{out}/?$")


def screen_name(path: str) -> str:
    """A route's screen name: the path itself, without its leading slash (`home`,
    `manage-customers/list-of-customers`); the root is `home page`."""
    return path.strip("/") or "home page"


def plan_screens(routes: list[dict], overrides: dict | None) -> tuple[dict, list[dict]]:
    """The audit's catalogue — `{name: path}` — and the routes it will not visit.

    `overrides` is `steps.dsaudit.screens`. A value is a concrete path: it names a screen
    of its own and fills every discovered route whose pattern it matches (a value for
    `:customerId`). `null`/`false` drops the route whose name (or pattern) is the key.
    Every other static route is a screen under its own name; a route with a parameter and
    no value is returned as not visited, with the parameters it is missing."""
    overrides = dict(overrides or {})
    dropped = {k for k, v in overrides.items() if not v}
    given = {k: str(v).lstrip("/") for k, v in overrides.items() if v}
    screens: dict[str, str] = {}
    unvisited: list[dict] = []
    placed = set()
    for r in routes:
        if r.get("unresolved"):
            unvisited.append({"route": r["expr"], "component": r.get("component") or "",
                              "file": r.get("file"), "reason": "unresolved"})
            continue
        name = screen_name(r["path"])
        if name in dropped or r["path"] in dropped:
            continue
        pat = _pattern(r["path"])
        hits = [k for k, v in given.items() if pat.match(v.split("?")[0])]
        if hits:
            for k in hits:
                if k not in placed:
                    screens[k] = given[k]
                    placed.add(k)
            continue
        if name in given:
            if name not in placed:
                screens[name] = given[name]
                placed.add(name)
            continue
        missing = params_of(r["path"])
        if missing:
            unvisited.append({"route": r["path"], "component": r.get("component") or "",
                              "file": r.get("file"), "reason": "param",
                              "params": missing})
            continue
        screens[name] = r["path"].lstrip("/")
    for k, v in given.items():
        if k not in placed:
            screens[k] = v
    return screens, unvisited


def find_entry(root: Path, configured: str | None = None) -> Path | None:
    """The app's route module: `steps.dsaudit.routes` when set, else the first of the usual
    places that exists."""
    if configured:
        p = root / configured
        return p if p.is_file() else None
    for rel in ("frontend/src/routes.tsx", "src/routes.tsx", "frontend/src/routes.ts",
                "src/routes.ts", "frontend/src/router.tsx", "src/router.tsx"):
        if (root / rel).is_file():
            return root / rel
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    print(json.dumps(discover(Path(argv[0])), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
