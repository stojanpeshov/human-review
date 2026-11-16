"""The Structure tab's boxes as ways into their folders.

Build side: a package box (`component "src.core" as p_src_core <<src/core>>`, as
`structure-diagrams.py` writes it) resolves to the folder its stereotype names, and carries
that folder and a forge link to it. Server side: `/__reveal_folder__` takes a path relative
to the checkout, refuses anything that is not a folder inside it, and asks the owning
window's bridge to reveal it.
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import threading
import urllib.parse
from pathlib import Path

import pytest

from test_action_server import _call, server, srv  # noqa: F401  (fixture)
from hrbuild.shared import folders


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _write(root: Path, rel: str, text: str = "") -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


PACKAGES = """@startuml
component "src" as p_src <<src>>
component "src.core" as p_src_core <<src/core>>
component "src.handlers" as p_src_handlers <<src/handlers>>
component "libs.core" as p_libs_core <<libs/core>>
component "src.gone" as p_src_gone <<src/gone>>
component "Queue" as q <<queue>>
component "Out" as out <<../outside>>
p_src_handlers ..> p_src_core
@enduml
"""


@pytest.fixture
def repo(tmp_path):
    """A Python service: `src` with two packages, a `libs/core` beside it, a stack folder,
    and the Packages diagram `structure-diagrams.py` would write for it."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    for f in ("src/app.py", "src/core/__init__.py", "src/core/services/svc.py",
              "src/handlers/h.py", "libs/core/x.py"):
        _write(r, f, "x = 1")
    _write(r, "shop-api/serverless.yml", "service: shop-api\n")
    _write(r, "docs/Packages.puml", PACKAGES)
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "one")
    folders._tree_base.cache_clear()
    folders.forge_for.cache_clear()
    folders._branch_files.cache_clear()
    yield r
    folders._tree_base.cache_clear()
    folders.forge_for.cache_clear()


def test_a_package_box_maps_to_the_folder_its_stereotype_names(repo):
    got = folders.package_folders("docs/Packages.puml", repo)
    assert got == {"p_src": "src", "p_src_core": "src/core", "p_src_handlers": "src/handlers",
                   "p_libs_core": "libs/core"}


def test_a_box_whose_stereotype_names_no_folder_gets_none(repo):
    (repo.parent / "outside").mkdir(exist_ok=True)
    got = folders.package_folders("docs/Packages.puml", repo)
    assert "p_src_gone" not in got and "q" not in got and "out" not in got


def test_the_targets_pair_each_box_with_its_label(repo):
    got = folders.folder_targets("docs/Packages.puml", repo)
    assert got["p_libs_core"] == ("libs.core", "libs/core")
    assert got["p_src_core"] == ("src.core", "src/core")


def _entity(name: str, label: str) -> str:
    return (f'<g class="entity" data-qualified-name="{name}" id="e"><rect x="1"/>'
            f'<text>{label}</text></g>')


def test_a_box_links_to_its_folder_on_github_at_the_reviewed_commit(repo):
    _git(repo, "remote", "add", "origin", "git@github.com:acme/shop.git")
    head = _git(repo, "rev-parse", "HEAD")
    svg = _entity("p_src_core", "src.core") + _entity("nobody", "nobody")
    out = folders.link_folders(svg, "docs/Packages.puml", repo)
    assert 'data-folder="src/core" data-folder-name="src.core" data-quiet="">' in out
    assert (f'<a href="https://github.com/acme/shop/tree/{head}/src/core" '
            'data-tip="Open src.core on GitHub">') in out
    assert _entity("nobody", "nobody") in out
    assert " title=" not in out


def test_without_a_github_remote_the_box_has_its_folder_and_no_link(repo):
    out = folders.link_folders(_entity("p_libs_core", "libs.core"), "docs/Packages.puml", repo)
    assert 'data-folder="libs/core"' in out and "<a " not in out


def test_a_box_somebody_already_linked_is_left_alone(repo):
    svg = ('<g class="entity" data-qualified-name="p_src"><a href="x"><rect/></a></g>')
    assert folders.link_folders(svg, "docs/Packages.puml", repo) == svg


# --------------------------------------------------------------------------- #
# the server: which paths it accepts
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("rel", ["../outside", "/etc", "shop-api/../../x", "~/x", "a\\b",
                                 "", "  ", None, 3, "missing", "shop-api/serverless.yml"])
def test_anything_but_a_folder_inside_the_checkout_is_refused(repo, rel):
    (repo.parent / "outside").mkdir(exist_ok=True)
    assert srv.checkout_folder(repo, rel) is None


def test_a_symlink_out_of_the_checkout_is_refused(repo, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", repo / "link")
    assert srv.checkout_folder(repo, "link") is None


def test_a_folder_inside_the_checkout_resolves_both_ways(repo):
    spelt, real = srv.checkout_folder(repo, "libs/core")
    assert spelt == repo / "libs/core" and real == (repo / "libs/core").resolve()
    assert srv.checkout_folder(repo, ".")[1] == repo.resolve()
    assert srv.checkout_folder(None, "libs/core") is None


# --------------------------------------------------------------------------- #
# the server: the endpoint and the bridge
# --------------------------------------------------------------------------- #

def test_the_probe_says_it_can_reveal_a_folder(server, monkeypatch):
    monkeypatch.setattr(srv, "ROOT", Path("/somewhere"))
    assert json.loads(_call(server, "GET", srv.MARKER)[1])["revealFolder"] is True


def test_the_endpoint_reveals_the_folder_the_page_names(server, repo, monkeypatch):
    asked = []
    monkeypatch.setattr(srv, "ROOT", repo)
    monkeypatch.setattr(srv, "reveal_folder",
                        lambda *a: asked.append(a) or {"how": "revealed", "window": "repo"})
    status, payload = _call(server, "POST", srv.REVEAL_FOLDER, {"path": "libs/core"})
    assert status == 200, payload
    assert json.loads(payload)["how"] == "revealed"
    assert asked == [(repo / "libs/core", (repo / "libs/core").resolve())]


@pytest.mark.parametrize("headers, status", [
    ({"X-Human-Review-Token": "wrong"}, 403),
    ({"Sec-Fetch-Site": "cross-site"}, 403),
    ({"Origin": "https://evil.example"}, 403),
    ({"Content-Type": "text/plain"}, 415),
])
def test_the_endpoint_is_guarded_like_the_other_verbs(server, repo, monkeypatch, headers, status):
    asked = []
    monkeypatch.setattr(srv, "ROOT", repo)
    monkeypatch.setattr(srv, "reveal_folder", lambda *a: asked.append(a) or {"how": "revealed"})
    got, _ = _call(server, "POST", srv.REVEAL_FOLDER, {"path": "libs/core"}, headers)
    assert got == status and asked == []


def test_the_endpoint_refuses_a_path_out_of_the_checkout(server, repo, monkeypatch):
    monkeypatch.setattr(srv, "ROOT", repo)
    monkeypatch.setattr(srv, "reveal_folder", lambda *a: pytest.fail("must not be asked"))
    status, payload = _call(server, "POST", srv.REVEAL_FOLDER, {"path": "../"})
    assert status == 404 and "not a folder" in payload


def test_the_endpoint_says_so_when_no_window_has_the_checkout(server, repo, monkeypatch):
    monkeypatch.setattr(srv, "ROOT", repo)
    monkeypatch.setattr(srv, "reveal_folder", lambda *a: None)
    status, payload = _call(server, "POST", srv.REVEAL_FOLDER, {"path": "shop-api"})
    assert status == 409 and "logo in the header" in payload


def _bridge(tmp_path, monkeypatch, folder: Path, reveal_ok: bool):
    """A fake VS Code window on `folder`, recording the commands it is asked to run."""
    registry = tmp_path / "home" / ".walkie-talkie" / "ide"
    registry.mkdir(parents=True)
    seen = []
    ping = json.dumps({"ok": True, "app": "vscode", "folder": folder.name, "folders": [
        {"name": folder.name, "path": str(folder), "realPath": str(folder.resolve())}]}).encode()

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, code, body):
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(200, ping)

        def do_POST(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            seen.append({k: v[0] for k, v in q.items()})
            if q["id"][0] == "revealInExplorer" and not reveal_ok:
                self._send(400, b'{"ok":false}')
            else:
                self._send(200, b'{"ok":true}')

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    (registry / "vscode-0.json").write_text(json.dumps({"port": httpd.server_address[1],
                                                        "token": "t"}))
    monkeypatch.setattr(srv.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    return httpd, seen


def test_the_owning_window_reveals_the_folder_and_comes_forward(repo, tmp_path, monkeypatch):
    httpd, seen = _bridge(tmp_path, monkeypatch, repo, reveal_ok=True)
    try:
        got = srv.reveal_folder(*srv.checkout_folder(repo, "libs/core"))
    finally:
        httpd.shutdown()
    assert got == {"how": "revealed", "window": "repo"}
    assert seen == [{"id": "revealInExplorer", "uri": (repo / "libs/core").as_uri()},
                    {"id": "workbench.action.focusWindow"}]


def test_a_bridge_too_old_to_reveal_still_raises_the_window(repo, tmp_path, monkeypatch):
    httpd, seen = _bridge(tmp_path, monkeypatch, repo, reveal_ok=False)
    try:
        got = srv.reveal_folder(*srv.checkout_folder(repo, "shop-api"))
    finally:
        httpd.shutdown()
    assert got == {"how": "focused", "window": "repo"}
    assert [s["id"] for s in seen] == ["revealInExplorer", "workbench.action.focusWindow"]


def test_no_window_on_the_checkout_is_none(repo, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    httpd, seen = _bridge(tmp_path, monkeypatch, other, reveal_ok=True)
    try:
        assert srv.reveal_folder(*srv.checkout_folder(repo, "shop-api")) is None
    finally:
        httpd.shutdown()
    assert seen == []


@pytest.mark.skipif(not shutil.which("plantuml"),
                    reason="the folder links are stamped on the SVG plantuml draws")
def test_a_gallery_card_links_its_boxes_and_a_sequence_card_does_not(repo, tmp_path):
    """The Structure tab's delta gallery and its context cards go through one door."""
    from hrbuild.shared import diagrams
    out = tmp_path / "out"
    (out / "assets" / "diagrams").mkdir(parents=True)
    row = diagrams.unchanged_row("packages", "docs/Packages.puml")
    html_ = diagrams.render_diagrams({"manifest": "assets/diagrams/MANIFEST.tsv"}, repo, out,
                                     [row])
    assert 'data-folder="libs/core"' in html_
    seq = dict(row, kind="sequence")
    assert "data-folder=" not in diagrams.render_diagrams(
        {"manifest": "assets/diagrams/MANIFEST.tsv"}, repo, out, [seq])
    card = diagrams.render_puml({"src": "docs/Packages.puml", "name": "Packages"}, repo, out)
    assert 'data-folder-name="src.core"' in card


def _box(name: str, label: str) -> str:
    return (f'<g class="entity" data-qualified-name="{name}" id="e">'
            f'<rect fill="#ECEFF1" height="36" rx="2.5" ry="2.5" width="100" x="10" y="20"/>'
            f'<text>{label}</text></g>')


def test_a_box_is_shaded_by_its_share_of_the_lines_added_and_lists_its_files(repo, monkeypatch):
    """Victor, 14 Oct 2026: the packages the branch grew show it, as a bar behind the box as
    wide as its share of the lines added, and hovering one lists its files, each a diff link.
    A file is counted once, in the deepest box that holds it: src/core is not also src."""
    from hrbuild.shared import snippets
    import html as _html
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "src/handlers/h.py", "x = 1\n" + "y = 2\n" * 2)   # +3 −1: the line had no newline
    _write(repo, "src/core/d.py", "\n".join(["x"] * 6) + "\n")      # +6, added
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "two")
    monkeypatch.setattr(snippets, "SNIPPET_BASE", base)
    svg = (_box("p_src_handlers", "src.handlers") + _box("p_src_core", "src.core")
           + _box("p_src", "src"))
    out = folders.link_folders(svg, "docs/Packages.puml", repo)
    handlers, core, top = out.split("</g>")[:3]
    assert 'data-impact="0.333"' in handlers and 'data-impact="0.667"' in core
    assert "data-impact" not in top and "pkg-impact" not in top, "a box the branch left alone"
    assert 'class="pkg-impact" x="10" y="20" width="66.7" height="36"' in core
    # The bar is drawn over the box's own shape and under its name.
    assert core.index('class="pkg-impact"') < core.index("<text>")
    pop = _html.unescape(core.split('data-tip-pop="')[1].split('"')[0])
    assert pop.startswith('<p class="tippophead">Green bar: <b>67%</b> of the lines this PR '
                          "added in this diagram are in <b>src.core</b><br>+6 in 1 file")
    assert ">d.py</a>" in pop, "named relative to the box the reader hovered"
    assert 'data-diff-path="src/core/d.py"' in pop and f'data-diff-base="{base}"' in pop
    assert "d.py" not in _html.unescape(handlers), "counted in the deepest box only"


def test_a_box_the_branch_left_alone_is_drawn_quiet_and_can_be_filtered(repo, monkeypatch):
    """Victor, 29 Oct 2026: the eye should go to the boxes that carry the change. A box with
    no file changed in its folder is stamped `data-quiet`, unless the diff paints it green
    or red; a card with such a box gets the All / Impacted filter, a card without none."""
    from hrbuild.shared import snippets
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "src/handlers/h.py", "x = 1\ny = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "one")
    monkeypatch.setattr(snippets, "SNIPPET_BASE", base)
    added = _box("p_src_core", "src.core").replace(
        "<rect ", '<rect style="stroke:var(--dgm-diff-add);" ')
    svg = _box("p_src_handlers", "src.handlers") + added + _box("p_libs_core", "libs.core")
    out = folders.link_folders(svg, "docs/Packages.puml", repo)
    handlers, core, libs = out.split("</g>")[:3]
    assert "data-quiet" not in handlers, "the branch changed a file in it"
    assert "data-quiet" not in core, "the diff paints it: it is the change"
    assert 'data-quiet=""' in libs
    radios = folders.only_impacted(out)
    assert radios.count('type="radio"') == 2 and 'value="all" checked' in radios
    assert radios == folders.only_impacted(out), "the same page builds to the same bytes"
    assert radios != folders.only_impacted(out + " "), "and each card is its own radio group"
    assert folders.only_impacted(handlers + "</g>") == ""


def test_a_box_named_by_a_folder_path_maps_to_that_folder(tmp_path):
    """The frontend has folders, not packages: `[owners] <<src/app/owners>>`, relative to the
    module the diagram sits in (the nearest package.json)."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _write(r, "web/package.json", "{}")
    _write(r, "web/src/app/owners/owner.ts", "export {}")
    _write(r, "web/src/app/app.module.ts", "export {}")
    _write(r, "web/docs/frontend-packages.puml",
           "@startuml\n[owners] <<src/app/owners>>\n[app] <<src/app>>\n"
           "[ghost] <<src/app/ghost>>\n@enduml\n")
    got = folders.package_folders("web/docs/frontend-packages.puml", r)
    assert got == {"owners": "web/src/app/owners", "app": "web/src/app"}


def test_a_one_line_box_has_its_name_centred_on_the_box():
    """PlantUML pads the name from its own font metrics; the browser's font is narrower, so
    the name sat left in its box (Victor, 20 Oct 2026). A class box with fields is kept."""
    from hrbuild.shared.folders import _center_label
    one = '<rect x="7" y="44" width="136" height="36"/><text font-size="14" x="17" y="68">m</text>'
    assert '<text font-size="14" x="75.00" y="68" text-anchor="middle">' in _center_label(one)
    two = one + '<text x="17" y="90">id : Integer</text>'
    assert _center_label(two) == two
