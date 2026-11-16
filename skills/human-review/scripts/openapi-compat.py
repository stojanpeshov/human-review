#!/usr/bin/env python3
"""Is the new REST contract backward compatible? — answered by a tool, not by us.

This script asks an independent, open-source differ and puts its verdict at the top
of the contract tab:

    no_changes  ·  compatible  ·  incompatible

**One differ, and not ours.** The skill used to run its own classifier,
`openapi-diff.py`, as a second opinion beside the tool, and turned the band red as
"Verdict disputed" whenever the two disagreed. An eval on 121 spec pairs (7 Oct 2026,
`reference/openapi-differ-eval.md`) settled it: ours missed 31 breaking changes oasdiff
caught, raised 15 false alarms to oasdiff's 2, and caught 5 oasdiff missed — the clear ones
reported upstream rather than kept as a private patch. A home-made second opinion that
is wrong far more often than the tool it checks only teaches a reviewer to ignore red.

**Which differ, and why it matters more than it sounds.** The list of affected
operations comes from **[oasdiff](https://github.com/oasdiff/oasdiff)**, which resolves
`$ref`s and reports one record per change *per operation it lands on*. A differ that reads
one `paths` entry at a time would not: on a generated spec the whole delta lives in
`components.schemas`, the `paths` section is byte-identical, and every operation that
serves the moved schema is invisible. On the change this was found with, such a differ
listed 4 operations; 11 had actually moved. A short list under a confident COMPATIBLE seal
is worse than no list.

oasdiff is the only differ. It is a Homebrew / `go install` binary (`OASDIFF_BIN` names
another one); when it is missing, or refuses the pair, the script exits non-zero with a
message saying so, and the API step fails rather than print a verdict nobody checked.

Emits an HTML fragment for `build-review-html.py` to `includeHtml`, in the same
shape as its siblings; `--css` prints the stylesheet it needs.

Usage (from the repository root — the project is resolved from the CWD):
    openapi-compat.py --base origin/main --out .human-review/assets/openapi-compat.html
    openapi-compat.py --css > .human-review/assets/openapi-compat.css
    openapi-compat.py --base origin/main --panel \
        --out .human-review/assets/openapi-verdict.html   # the one-line verdict panel
    openapi-compat.py before.yaml after.yaml --state
"""
from __future__ import annotations

import argparse
import functools
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_page  # noqa: E402 - resolved from next to this file, not from site-packages
import schema_tree  # noqa: E402 - resolved from next to this file, not from the project

try:
    import yaml
except ImportError:  # pragma: no cover - the message is the whole handling
    raise SystemExit("[openapi-compat] needs PyYAML: python3 -m pip install pyyaml")

OASDIFF = os.environ.get("OASDIFF_BIN", "oasdiff")
# oasdiff's own severity ladder, straight out of its JSON: 3 ERR, 2 WARN, 1 INFO.
# `oasdiff breaking` is exactly "changelog, level >= 2", so that is where the line goes.
LEVEL_ERR, LEVEL_WARN, LEVEL_INFO = 3, 2, 1
LEVEL_CHIP = {LEVEL_ERR: ("oac-lv-err", "breaking"),
              LEVEL_WARN: ("oac-lv-warn", "possibly breaking")}

NO_CHANGES, COMPATIBLE, INCOMPATIBLE, NOT_RUN = (
    "no_changes", "compatible", "incompatible", "not_run",
)
VERDICT_HEADLINE = {
    NO_CHANGES: "The contract did not move",
    COMPATIBLE: "Backward compatible",
    INCOMPATIBLE: "Not backward compatible",
    NOT_RUN: "The compatibility check did not run",
}

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def repo_root() -> Path:
    return Path(run(["git", "rev-parse", "--show-toplevel"], check=True).stdout.strip())


# ── the differ that follows a $ref: oasdiff ───────────────────────────────────────
def oasdiff_version() -> str | None:
    """The installed oasdiff's version, or None when it is not on PATH at all."""
    if not shutil.which(OASDIFF):
        return None
    proc = run([OASDIFF, "--version"])
    if proc.returncode != 0:
        return None
    found = re.search(r"\d+\.\d+\.\d+", proc.stdout)
    return found.group(0) if found else (proc.stdout.strip() or "unknown")


#: What to do about a missing oasdiff, said once for every way of finding out.
INSTALL = ("install it (`brew install oasdiff`, or `go install "
           "github.com/oasdiff/oasdiff@latest`), or point OASDIFF_BIN at one")


class OasdiffError(SystemExit):
    """oasdiff is missing or refused the pair. There is no second differ to fall back
    on, so the run stops here, with the reason, instead of printing a verdict."""


def oasdiff_changelog(before: Path, after: Path) -> list:
    """One record per change, each already attributed to the operation it lands on.

    `{"id": "response-optional-property-added", "operation": "GET",
      "path": "/api/owners", "level": 1, "text": "added the optional property …"}`

    Raises `OasdiffError` when oasdiff is absent or refuses the pair: `[]` means
    "nothing changed", and must never also mean "we could not look".
    """
    if not shutil.which(OASDIFF):
        raise OasdiffError(f"[openapi-compat] oasdiff ({OASDIFF}) is not on PATH — {INSTALL}")
    proc = run([OASDIFF, "changelog", str(before), str(after), "-f", "json"])
    if proc.returncode != 0:
        raise OasdiffError(f"[openapi-compat] oasdiff failed (exit {proc.returncode}): "
                           f"{proc.stderr.strip()[:400]}")
    try:
        entries = json.loads(proc.stdout.strip() or "[]")
    except json.JSONDecodeError:
        raise OasdiffError("[openapi-compat] oasdiff did not emit JSON") from None
    if not isinstance(entries, list):
        raise OasdiffError("[openapi-compat] oasdiff emitted JSON that is not a changelog list")
    return entries


def oas_reason(entry: dict) -> str:
    """One change, in the currency a caller thinks in, tagged only when it can hurt."""
    level = int(entry.get("level") or LEVEL_INFO)
    text = inline(entry.get("text") or entry.get("id") or "changed")
    chip = ""
    if level in LEVEL_CHIP:
        cls, label = LEVEL_CHIP[level]
        chip = f'<span class="oac-lv {cls}">{label}</span> '
    rule = entry.get("id")
    tail = f' <span class="oac-rule">{html.escape(str(rule))}</span>' if rule else ""
    return f"{chip}{text}{tail}"


@functools.lru_cache(maxsize=1)
def _visual_diff():
    """`openapi-visual-diff.py`, next door, loaded by path (its name has a hyphen).

    None when it is missing — the band then counts oasdiff's raw entries, as it did."""
    import importlib.util
    path = Path(__file__).with_name("openapi-visual-diff.py")
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("openapi_visual_diff_for_compat", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - a broken sibling must not take the verdict down
        return None
    return module


def fold_swaps(found: list) -> list:
    """One operation's entries, each swapped media type folded back into one line.

    oasdiff writes `*/*` → `application/problem+json` as two entries (removed, added), and
    the visual diff below the band draws it as one row. Counting the raw entries put
    "8 changes" over a diff listing 7 and a toggle reading "expand 7 impacted". Both now
    fold with the visual diff's own `merge_pairs`: one rule, one count."""
    vd = _visual_diff()
    if vd is None or not all(isinstance(e.get("id"), str) and isinstance(e.get("text"), str)
                             for e in found):
        return found
    for e in found:
        e.setdefault("level", LEVEL_INFO)
    return vd.merge_pairs(found)


def read_changelog(entries: list) -> dict:
    """oasdiff's changelog, flattened into what the fragment and the panel render.

    `entries` is already keyed by operation, and an operation lands in it because of
    *any* schema it reaches, not only because its own `paths` entry moved.
    """
    ops: dict = {}          # (METHOD, path) -> [entry, …], in oasdiff's order
    elsewhere: list = []    # info.version, a server URL — changes that belong to no operation
    deprecated: list = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        method = str(e.get("operation") or "").upper()
        path = str(e.get("path") or "")
        if e.get("id") == "endpoint-deprecated":
            deprecated.append({"method": method, "path": path})
            continue
        if not method or not path:
            elsewhere.append(e)
            continue
        ops.setdefault((method, path), []).append(e)

    breaks, additive = [], []
    folded = 0  # entries the fold removed — what oasdiff's own count has over ours
    for (method, path), found in ops.items():
        raw = len(found)
        found = fold_swaps(found)
        folded += raw - len(found)
        subject = {"method": method, "path": path}
        # Split per *entry*, not per operation. An operation with one breaking change and
        # five optional additions used to land whole under "What breaks", and every count
        # read off it said "6 breaking" over a diff showing one: oasdiff's own `breaking`
        # command is "level >= WARN", entry by entry, and so is this. The INFO entries of
        # the same operation are still shown — as its compatible movement.
        hurts = [e for e in found if int(e.get("level") or LEVEL_INFO) >= LEVEL_WARN]
        safe = [e for e in found if int(e.get("level") or LEVEL_INFO) < LEVEL_WARN]
        if hurts:
            breaks.append({**subject,
                           "reasons": [([e.get("id", "")], oas_reason(e)) for e in hurts]})
        if safe:
            n = len(safe)
            additive.append({**subject,
                             "reasons": [([e.get("id", "")], oas_reason(e)) for e in safe],
                             "note": f"{n} compatible change{'s' if n != 1 else ''}"
                                     + (" besides the break above" if hurts else "")})

    if not entries:
        state = NO_CHANGES
    elif breaks:
        state = INCOMPATIBLE
    else:
        state = COMPATIBLE
    return {"state": state, "breaks": breaks, "additive": additive,
            "deprecated": deprecated,
            "elsewhere": [oas_reason(e) for e in elsewhere],
            "folded": folded,
            "source": "oasdiff"}


# ── rendering ─────────────────────────────────────────────────────────────────────
VERB_CLASS = {"GET": "get", "POST": "post", "PUT": "put", "PATCH": "put", "DELETE": "delete"}


def op_label(method: str, path: str) -> str:
    verb = VERB_CLASS.get(method.upper(), "get")
    return (f'<span class="oac-verb oac-{verb}">{html.escape(method.upper())}</span>'
            f'<code class="oac-path">{html.escape(path)}</code>')


def render(result: dict, provenance: str,
           before: dict | None = None, after: dict | None = None) -> str:
    state = result["state"]
    breaks, additive = result["breaks"], result["additive"]
    n_reasons = sum(len(b["reasons"]) for b in breaks)
    n_additive = sum(len(a.get("reasons") or []) for a in additive)

    if state == INCOMPATIBLE:
        sub = (f"{n_reasons} change{'s' if n_reasons != 1 else ''} across "
               f"{len(breaks)} operation{'s' if len(breaks) != 1 else ''} would break a client "
               "that is already calling this API.")
    elif state == COMPATIBLE:
        moved = (f"{n_additive} change{'s' if n_additive != 1 else ''} across "
                 f"{len(additive)} operation{'s' if len(additive) != 1 else ''}"
                 if n_additive else
                 f"{len(additive)} operation{'s' if len(additive) != 1 else ''}")
        sub = (f"{moved} moved, and none of it can break a client compiled against the "
               "base spec.")
    elif result.get("identical", True):
        sub = "The two specs are structurally identical."
    else:
        # "No changes" is a claim about callers, not about bytes. The specs *did* move —
        # saying they are identical when the neighbouring tab lists a reworded description
        # is the same overstatement in miniature.
        sub = ("The specs differ, but nothing that moved can reach a client: no operation, "
               "payload, status or constraint changed. The rest — a description, a version, "
               "an example — is documentation, not contract.")

    seal = html.escape(state.replace("_", " ")).upper()
    headline = VERDICT_HEADLINE[state]

    parts = [
        f'<div class="oac oac-{state}">',
        '<div class="oac-verdict">'
        f'<span class="oac-seal">{seal}</span>'
        f'<div><div class="oac-headline">{headline}</div>'
        f'<div class="oac-sub">{sub}</div></div></div>',
        f'<p class="oac-prov">{provenance}</p>',
    ]

    def tree(method: str, path: str) -> str:
        # The verdict says whether it breaks; the tree says what it now looks like.
        if before is None or after is None:
            return ""
        return schema_tree.operation_tree(method, path, before, after)

    if breaks:
        parts.append('<div class="oac-kind">What breaks '
                     f'<span class="oac-count">{n_reasons}</span></div>')
        for b in breaks:
            items = "".join(f"<li>{text}</li>" for _, text in b["reasons"])
            parts.append(f'<div class="oac-row oac-bad"><div class="oac-head">'
                         f'{op_label(b["method"], b["path"])}</div>'
                         f'<ul class="oac-reasons">{items}</ul>'
                         f'{tree(b["method"], b["path"])}</div>')

    if result["deprecated"]:
        rows = "".join(f'<div class="oac-row oac-warn"><div class="oac-head">'
                       f'{op_label(d["method"], d["path"])}'
                       '<span class="oac-note">deprecated</span></div></div>'
                       for d in result["deprecated"])
        parts.append(f'<div class="oac-kind">Newly deprecated</div>{rows}')

    if additive:
        rows = []
        for a in additive:
            # The reasons are what turns "changed, compatibly" from a shrug into a
            # sentence. The differ already wrote them; there is no case for hiding them.
            items = "".join(f"<li>{text}</li>" for _, text in (a.get("reasons") or []))
            reasons = f'<ul class="oac-reasons">{items}</ul>' if items else ""
            rows.append(f'<div class="oac-row"><div class="oac-head">'
                        f'{op_label(a["method"], a["path"])}'
                        f'<span class="oac-note">{html.escape(a["note"])}</span></div>'
                        f'{reasons}{tree(a["method"], a["path"])}</div>')
        parts.append('<div class="oac-kind">Compatible movement '
                     f'<span class="oac-count">{len(additive)}</span></div>' + "".join(rows))

    if result.get("elsewhere"):
        items = "".join(f"<li>{text}</li>" for text in result["elsewhere"])
        parts.append('<div class="oac-kind">Elsewhere in the document '
                     f'<span class="oac-count">{len(result["elsewhere"])}</span></div>'
                     f'<div class="oac-row"><ul class="oac-reasons">{items}</ul></div>')

    parts.append("</div>")
    return "\n".join(p for p in parts if p)


# ── the one line at the top of the API tab ────────────────────────────────────────
# The first line of the tab is the only line every reviewer reads, so it is *generated*,
# never typed into the content file. A count authored by hand is a count that is wrong on
# the next commit and then says so with total confidence — which is precisely the failure
# this whole report exists to prevent.
#
# Three states, three colours: nothing moved (grey, struck through), it moved and nothing
# breaks (green), something breaks (red).
OASDIFF_URL = "https://github.com/oasdiff/oasdiff"
# The openable copy of the full report, written by `--report` in the same run that writes
# the fragment. Naming a differ in the band and giving the reader nowhere to check it is
# half an answer: the counts are a summary, and the working is on disk either way.
REPORTS = {"engine": "openapi-compat-report.html"}

PANEL_CSS = """<style>
/* Running text, not a flex row. As a row the label sat vertically centred beside a
   two-line count, so the count's second line hung under nothing (eval run 8: "Breaking
   changes" floating mid-band, the differ's "(report)" wrapped beneath the counts). Inline,
   the sentence wraps the way a sentence does: from the left edge, under the label.
   Slim, and close to the frame under it (Victor, 5 Oct 2026): the band is one line read
   once, and every pixel it takes is a pixel of the diff below — which fills the window. */
.apiverdict{display:block;width:100%;box-sizing:border-box;
 padding:.35rem 1.1rem;border-radius:8px;font-weight:600;font-size:1rem;line-height:1.4;
 margin:.2rem 0 .6rem}
.apiverdict .dot{display:inline-block;width:.7rem;height:.7rem;border-radius:50%;
 background:currentColor;margin-right:.5rem;vertical-align:.02em}
.apiverdict .v{white-space:nowrap}
.apiverdict .n{font-weight:400;opacity:.85;font-size:.94rem}
.apiverdict a{color:inherit;text-decoration:underline;text-underline-offset:2px}
/* Smaller and quieter than the name it follows: the verdict is what this band says, and
   the report is where to go and check it. Same colour, so it still reads as one clause. */
.apiverdict a.rep{font-size:.85em;opacity:.8}
.apiverdict .who{white-space:nowrap}
.apiverdict a.rep:hover,.apiverdict a.rep:focus-visible{opacity:1}
.apiverdict code{font:600 .86em/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
/* The same two treatments as the score chip beside the page title (.titlescore v-good /
   v-bad) and the tab dots — one palette on the page, not five. */
.apiverdict.green{background:rgba(46,158,91,.16);color:#1f7a45}
.apiverdict.red{background:rgba(215,38,61,.16);color:#d7263d}
/* Nothing moved: the page's own code-bg/line/muted tokens and a line through the sentence,
   so it reads as spent rather than as a verdict worth stopping on. The strike goes on the
   text spans, not the flex container — decoration propagation into flex items is not
   something to bet a state on. */
.apiverdict.none{background:var(--code-bg);border:1px solid var(--line);color:var(--muted);
 font-weight:500}
.apiverdict.none .v,.apiverdict.none .n{text-decoration:line-through}
@media (prefers-color-scheme:dark){
 .apiverdict.green{color:#6fce93}
 .apiverdict.red{color:#f0757f}}
</style>"""


def change_count(result: dict) -> int:
    """Every difference the engine reported, in the currency the rows below are counted in.

    An operation listed without itemised reasons counts as one: letting it contribute zero
    would print "0 changes" over a page full of rows.
    """
    n = 0
    for group in ("breaks", "additive"):
        for item in result.get(group) or []:
            n += len(item.get("reasons") or []) or 1
    return n + len(result.get("elsewhere") or []) + len(result.get("deprecated") or [])


def breaking_count(result: dict) -> int:
    """The breaking changes alone. `breaks[].reasons` holds only the entries the engine
    rated WARN or worse (`read_changelog`); an operation's compatible entries live on its
    `additive` row and are counted by `change_count`, never here."""
    return sum(len(b.get("reasons") or []) or 1 for b in result.get("breaks") or [])


def endpoint_count(result: dict) -> int:
    """The endpoints the engine saw move, each once. An operation with a break and a few
    compatible additions sits on both a `breaks` and an `additive` row; it is still one
    endpoint. Changes that belong to no operation (`info.version`) move none."""
    return len({(i.get("method"), i.get("path"))
                for group in ("breaks", "additive", "deprecated")
                for i in result.get(group) or []})


def endpoints(n: int) -> str:
    return f"{n} endpoint{'' if n == 1 else 's'}"


def engine_link(result: dict) -> str:
    """The differ that produced this result, linked."""
    return f'<a href="{OASDIFF_URL}">oasdiff</a>'


def report_link(which: str, assets: Path | None, prefix: str) -> str:
    """`(report ↗)` after a differ's name, when that differ's report is really on disk.

    Two paths, on purpose. `assets` is the real directory — right next to where this panel
    is being written — and exists only to check the file is there; `prefix` is how
    review.html, a directory above, will have to spell the href once the panel is inlined
    into it. Deriving the href from the filesystem path would produce a link that resolves
    from the wrong page.

    Emitted only when the file is present, which is the honest rendering: run standalone,
    or with the report step skipped, this would otherwise send a reader to a 404 and call
    it a report. `build-review-html.py` stamps `target="_blank"` on every anchor it does
    not recognise as page-local, so the arrow is a promise the build keeps — and the
    parentheses stay outside the anchor so only the word underlines."""
    if assets is None or not (assets / REPORTS[which]).is_file():
        return ""
    return (f' (<a class="rep" href="{html.escape(prefix + REPORTS[which])}">'
            f'report&nbsp;&#8599;</a>)')


def who(label: str) -> str:
    """A differ's name with its report link, kept on one line."""
    return f'<span class="who">{label}</span>'


def panel(result: dict, assets: Path | None = None, prefix: str = "assets/") -> str:
    """The verdict, how many endpoints it touches, and who checked it — on one line."""
    state = result["state"]
    # The name and its "(report ↗)" are one unbreakable unit: at 1440px the band wrapped
    # between them and left "(report ↗)" alone on a line of its own.
    engine = who(engine_link(result) + report_link("engine", assets, prefix))
    n_break = breaking_count(result)
    # The band counts *endpoints*, not changes: "N changes, M breaking across K endpoints"
    # made the reader do arithmetic the diff right below already does, row by row, and
    # pushed the band onto a second line. What a reader needs from the band is how many
    # endpoints a caller has to worry about; which changes, and how many, is what the
    # tab is for (Victor, 7 Oct 2026).
    n_broken = len(result.get("breaks") or [])
    n_changed = endpoint_count(result)

    checked = f"checked by {engine}"

    if state == INCOMPATIBLE:
        cls = "red"
        verdict = f"Breaking change{'' if n_break == 1 else 's'}"
        counts = f"{endpoints(n_broken)} broken"
    elif state == COMPATIBLE:
        cls, verdict = "green", "Backwards compatible"
        counts = f"{endpoints(n_changed)} changed"
    else:
        cls, verdict = "none", "No API changes"
        # "No changes" is a claim about callers, not about bytes: the spec may well have
        # moved. Saying it flat over a reworded description is the same overstatement the
        # seal below refuses to make.
        counts = ("0 endpoints changed" if result.get("identical", True)
                  else "0 endpoints changed for a caller")

    # The space between the spans is not cosmetic: a reader copying the line out would
    # get "Backwards compatible· …" with the dot glued on. It is a no-break space, and so is
    # the one after the dot, so a wrapped band never opens its second line on a stray '·'.
    return (f'{PANEL_CSS}\n<div class="apiverdict {cls}"><span class="dot"></span>'
            f'<span class="v">{verdict}</span>&nbsp;'
            f'<span class="n">·&nbsp;{counts} · {checked}</span></div>')


# ── the tool's text, as HTML we control ───────────────────────────────────────
def inline(text: str) -> str:
    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)
    return out


CSS = """
.oac { --oac-bad:#c62828; --oac-ok:#2e7d32; --oac-warn:#b56b00; --oac-flat:#6b6b78;
       margin:.4rem 0 1rem; }
.oac-verdict { display:flex; align-items:center; gap:.9rem; border:1px solid var(--line);
               border-left:4px solid var(--oac-flat); border-radius:10px; padding:.8rem 1rem;
               background:var(--card); }
.oac-incompatible .oac-verdict { border-left-color:var(--oac-bad); }
.oac-compatible   .oac-verdict { border-left-color:var(--oac-ok); }
.oac-seal { font:800 .7rem/1.9 inherit; letter-spacing:.08em; border-radius:5px;
            padding:.1rem .55rem; white-space:nowrap; background:#f0f0f4; color:#5d5d6b; }
.oac-incompatible .oac-seal { background:#fdeaea; color:#8a1c1c; }
.oac-compatible   .oac-seal { background:#eef7ef; color:#245c30; }
.oac-headline { font-weight:700; font-size:1rem; }
.oac-sub { color:var(--muted); font-size:.86rem; line-height:1.6; }
.oac-prov { color:var(--muted); font-size:.8rem; line-height:1.7; margin:.5rem 0 1rem; }
.oac-kind { font:600 .82rem/1.6 inherit; text-transform:uppercase; letter-spacing:.06em;
            color:var(--muted); border-bottom:1px solid var(--line); padding-bottom:.3rem;
            margin-top:1.2rem; }
.oac-count { background:var(--code-bg); border-radius:999px; padding:0 .4rem; margin-left:.3rem;
             font-size:.72rem; }
.oac-row { background:var(--card); border:1px solid var(--line); border-left:3px solid var(--line);
           border-radius:8px; padding:.55rem .8rem; margin:.5rem 0; }
.oac-row.oac-bad  { border-left-color:var(--oac-bad); }
.oac-row.oac-warn { border-left-color:var(--oac-warn); }
.oac-head { display:flex; align-items:center; gap:.5rem; flex-wrap:wrap; }
.oac-verb { font:700 10.5px/1.7 ui-monospace,Menlo,monospace; border-radius:4px; padding:0 .38rem;
            color:#fff; letter-spacing:.04em; }
.oac-get { background:#2f6fb5; } .oac-post { background:#2e7d32; }
.oac-put { background:#b56b00; } .oac-delete { background:#c62828; }
.oac-path { font:600 12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--fg); }
.oac-note { color:var(--muted); font-size:.8rem; }
.oac-reasons { list-style:none; margin:.45rem 0 0; padding:0; display:grid; gap:.28rem; }
.oac-reasons li { font-size:.88rem; line-height:1.6; }
.oac-reasons li::before { content:"↳"; color:var(--muted); margin-right:.45rem; }
.oac-lv { font:700 .64rem/1.8 inherit; text-transform:uppercase; letter-spacing:.04em;
          border-radius:3px; padding:0 .3rem; }
.oac-lv-err  { background:#fdeaea; color:#8a1c1c; }
.oac-lv-warn { background:#fdf3e2; color:#6b4a0f; }
/* The rule id is provenance, not prose: readable when looked for, invisible when not. */
.oac-rule { font:11px/1.5 ui-monospace,Menlo,monospace; color:var(--muted); opacity:.75; }
.oac code { font:600 12px/1.5 ui-monospace,Menlo,monospace; background:var(--code-bg);
            border-radius:3px; padding:0 .25rem; }
@media (prefers-color-scheme: dark) {
  .oac-seal { background:#26262f; color:#a5a5b4; }
  .oac-incompatible .oac-seal { background:#3a1f1f; color:#f2a0a0; }
  .oac-compatible   .oac-seal { background:#1b2c1f; color:#9ad3a5; }
  .oac-lv-err  { background:#3a1f1f; color:#f2a0a0; }
  .oac-lv-warn { background:#3a3018; color:#e6c07b; }
}
"""


def base_name(named: str, named_sha: str, base_ref: str) -> str:
    """What to call the commit the "before" spec was read from.

    `run-steps.py` hands every producer the page's one base (`run_base`): on a reviewed
    branch that is the commit the review audited, a commit already on this branch, and its
    merge-base with HEAD is itself. Calling it "the merge-base" there was wrong twice over —
    the real fork point from main is a different, older commit. So: the base named is
    already where the walk landed → "the review base"; a branch ref the branch forked from
    → "the merge-base with <ref>", which is what it is."""
    if named_sha and named_sha == base_ref:
        return "the review base"
    return f"the merge-base with <code>{html.escape(named)}</code>"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("before", nargs="?", help="spec to compare from (default: --base's copy)")
    ap.add_argument("after", nargs="?", help="spec to compare to (default: the working tree)")
    ap.add_argument("--base", default="origin/main", help="git ref to read the base spec from")
    ap.add_argument("--spec", default="openapi.yaml", help="repo-relative path to the spec")
    ap.add_argument("--out", help="write the HTML fragment here instead of stdout")
    ap.add_argument("--json", action="store_true", help="emit the flattened verdict as JSON")
    ap.add_argument("--state", action="store_true",
                    help="print only no_changes / compatible / incompatible")
    ap.add_argument("--panel", action="store_true",
                    help="emit only the one-line verdict panel for the top of the API tab "
                         "(carries its own <style>, so it needs no extraCss entry)")
    ap.add_argument("--css", action="store_true", help="print the stylesheet this fragment needs")
    ap.add_argument("--report", metavar="PATH",
                    help="also write a self-contained, openable copy here — same body, "
                         "wrapped in a document that carries the stylesheet with it")
    ap.add_argument("--asset-prefix", default="assets/",
                    help="how review.html spells the assets directory, for the "
                         "(report) links the --panel band puts after each differ")
    args = ap.parse_args(argv)

    if args.css:
        print(CSS)
        print(schema_tree.CSS)
        return 0

    # Where the report lands: right next to this panel, because the API step writes both
    # into the same assets directory. Without `--out` there is no directory to
    # look in, and `report_link` then emits nothing rather than guessing.
    assets = Path(args.out).parent if args.out else None

    spec_rel = args.spec
    with tempfile.TemporaryDirectory(prefix="openapi-compat-") as tmp:
        tmpdir = Path(tmp)
        if args.before and args.after:
            before, after = Path(args.before).resolve(), Path(args.after).resolve()
            spec_rel = args.after
            pair = (f"<code>{html.escape(before.name)}</code> → "
                    f"<code>{html.escape(after.name)}</code>")
        else:
            root = repo_root()
            target = root / spec_rel
            if not target.is_file():
                raise SystemExit(f"[openapi-compat] no spec at {target} — pass --spec")
            merge_base = run(["git", "merge-base", args.base, "HEAD"], cwd=root)
            base_ref = merge_base.stdout.strip() if merge_base.returncode == 0 else args.base
            base_spec = run(["git", "show", f"{base_ref}:{spec_rel}"], cwd=root)
            which = base_name(args.base, run(["git", "rev-parse", "--verify", "--quiet",
                                              f"{args.base}^{{commit}}"],
                                             cwd=root).stdout.strip(), base_ref)
            pair = (f"<code>{html.escape(spec_rel)}</code> at {which} "
                    f"<code>{html.escape(base_ref[:8])}</code> against the working tree")
            if base_spec.returncode != 0 or not base_spec.stdout.strip():
                # No spec at the base means no client compiled against one. Nothing to break.
                fresh = {"state": NO_CHANGES, "breaks": [], "additive": [],
                         "deprecated": [], "elsewhere": []}
                frag = (panel(fresh, assets, args.asset_prefix) if args.panel
                        else render(
                    fresh,
                    pair + f". The spec did not exist at {which}, so there is "
                    "no prior contract to break."))
                return emit(args, frag, {"state": NO_CHANGES})
            before = tmpdir / "before.yaml"
            before.write_text(base_spec.stdout, encoding="utf-8")
            after = tmpdir / "after.yaml"
            after.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")

        # oasdiff resolves `$ref`s, so an operation shows up here because of any schema it
        # reaches. Missing or refusing the pair, it raises: there is no other differ.
        result = read_changelog(oasdiff_changelog(before, after))
        provenance = (
            f"{pair}, read by <b>oasdiff {html.escape(oasdiff_version() or '')}</b> — an "
            "independent, open-source differ. It resolves <code>$ref</code>s, so an operation "
            "is listed here because of <i>any</i> schema it reaches, not only because its "
            "own <code>paths</code> entry moved. Every line below carries the rule id that "
            "produced it."
        )

        # Read inside the block: the temp copies are gone the moment it closes.
        before_spec = yaml.safe_load(before.read_text(encoding="utf-8")) or {}
        after_spec = yaml.safe_load(after.read_text(encoding="utf-8")) or {}
        result["identical"] = before_spec == after_spec

    if args.state:
        print(result["state"])
        return 0

    frag = (panel(result, assets, args.asset_prefix) if args.panel
            else render(result, provenance, before_spec, after_spec))
    if args.report and not args.panel:
        # Same body, second file — never a second run of oasdiff: a report that disagrees
        # with the fragment it came from is the one failure this page cannot afford to ship.
        rep = Path(args.report)
        rep.parent.mkdir(parents=True, exist_ok=True)
        rep.write_text(report_page.wrap(
            f"REST contract compatibility — {spec_rel}",
            CSS + "\n" + schema_tree.CSS, frag, provenance), encoding="utf-8")
        print(f"[openapi-compat] wrote {rep} — standalone", file=sys.stderr)
    return emit(args, frag, result)


def plain(markup: str) -> str:
    return html.unescape(re.sub("<[^>]+>", "", markup)).strip()


def emit(args, fragment: str, result: dict) -> int:
    if args.json:
        skip = ("breaks", "additive")
        payload = json.dumps(
            {k: v for k, v in result.items() if k not in skip} |
            {group: [{"method": o["method"], "path": o["path"]} |
                     ({"note": o["note"]} if o.get("note") else {}) |
                     {"reasons": [plain(t) for _, t in (o.get("reasons") or [])]}
                     for o in result.get(group, [])]
             for group in skip}, indent=1)
        print(payload)
    elif args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(fragment, encoding="utf-8")
        print(f"[openapi-compat] wrote {out} — {result['state']}", file=sys.stderr)
    else:
        print(fragment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
