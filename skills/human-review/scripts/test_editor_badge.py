"""The VS Code logo beside `Served`: whether a click on code opens in VS Code (green) or
copies its path (red), and, served, whether its window is on the reviewed commit (amber when
the reviewed branch is open at another commit)."""
from __future__ import annotations

import http.server
import json
import subprocess
import threading
from pathlib import Path

import pytest

from test_action_server import _call, server, srv  # noqa: F401  (fixture)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A checkout on `feature` two commits deep, and a worktree of it on the first commit."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "feature")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("1\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "one")
    first = _git(r, "rev-parse", "HEAD")
    (r / "a.txt").write_text("2\n")
    _git(r, "commit", "-qam", "two")
    return r, first, _git(r, "rev-parse", "HEAD")


def _windows(tmp_path, monkeypatch, *folders: Path):
    """One fake VS Code bridge per folder, registered where the server looks for them."""
    registry = tmp_path / "home" / ".walkie-talkie" / "ide"
    registry.mkdir(parents=True)
    servers = []
    for i, folder in enumerate(folders):
        body = json.dumps({"ok": True, "app": "vscode", "folders": [
            {"name": folder.name, "path": str(folder), "realPath": str(folder.resolve())}]}).encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self, body=body):
                assert self.path == "/ping" and self.headers["x-relay-token"] == "t"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        (registry / f"vscode-{i}.json").write_text(
            json.dumps({"port": httpd.server_address[1], "token": "t"}))
    monkeypatch.setattr(srv.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    return servers


def test_green_when_a_window_is_on_the_reviewed_commit(repo, tmp_path, monkeypatch):
    r, _, head = repo
    servers = _windows(tmp_path, monkeypatch, r)
    try:
        assert srv.editor_state(head, str(r), "feature")["state"] == "on"
    finally:
        [s.shutdown() for s in servers]


def test_amber_when_the_branch_is_open_at_another_commit(repo, tmp_path, monkeypatch):
    r, first, _ = repo
    servers = _windows(tmp_path, monkeypatch, r)
    try:
        got = srv.editor_state(first, str(r), "feature")
        assert got["state"] == "near" and first[:8] in got["tip"]
    finally:
        [s.shutdown() for s in servers]


def test_red_when_the_window_is_on_another_branch(repo, tmp_path, monkeypatch):
    r, first, _ = repo
    wt = tmp_path / "wt"
    _git(r, "worktree", "add", "-q", "-b", "other", str(wt), first)
    (wt / "a.txt").write_text("3\n")
    _git(wt, "commit", "-qam", "three")
    servers = _windows(tmp_path, monkeypatch, wt)
    try:
        got = srv.editor_state(_git(r, "rev-parse", "HEAD"), str(r), "feature")
        assert got["state"] == "off" and "wt (other @" in got["tip"]
    finally:
        [s.shutdown() for s in servers]


def test_a_window_on_a_folder_above_the_checkout_counts(repo, tmp_path, monkeypatch):
    r, _, head = repo
    servers = _windows(tmp_path, monkeypatch, tmp_path)
    try:
        assert srv.editor_state(head, str(r), "feature")["state"] == "on"
    finally:
        [s.shutdown() for s in servers]


def test_red_when_no_window_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(srv.Path, "home", classmethod(lambda cls: tmp_path))
    got = srv.editor_state("a" * 40, str(tmp_path), "feature")
    assert got["state"] == "off" and "victor-rentea.human-review" in got["tip"]


def test_the_endpoint_answers_the_page_and_is_not_use(server, monkeypatch):
    asked = []
    monkeypatch.setattr(srv, "ROOT", None)
    monkeypatch.setattr(srv, "editor_state", lambda *a: asked.append(a) or {"state": "on", "tip": "x"})
    before = srv.Handler.last_seen

    status, payload = _call(server, "GET", f"{srv.EDITOR}?sha=abc&root=/r&branch=b")

    assert status == 200 and json.loads(payload) == {"state": "on", "tip": "x"}
    assert asked == [("abc", "/r", "b")]
    # Polled every few seconds by an open tab: like the watch poll, it keeps no server alive.
    assert srv.Handler.last_seen == before


def test_pressing_the_badge_opens_the_servers_own_checkout_and_nothing_else(
        server, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(srv, "open_editor", opened.append)
    monkeypatch.setattr(srv, "ROOT", tmp_path)

    # A folder in the body is ignored: the page cannot point the server at another one.
    status, payload = _call(server, "POST", srv.EDITOR_OPEN, {"root": "/etc"})

    assert status == 200 and json.loads(payload) == {"opened": str(tmp_path)}
    assert opened == [tmp_path]


def test_the_badge_cannot_be_pressed_from_another_page(server, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(srv, "open_editor", opened.append)
    monkeypatch.setattr(srv, "ROOT", tmp_path)

    status, _ = _call(server, "POST", srv.EDITOR_OPEN, {}, {"X-Human-Review-Token": "guess"})

    assert status == 403 and opened == []


def test_pressing_the_footer_path_shows_the_served_page_in_finder(server, tmp_path, monkeypatch):
    revealed = []
    monkeypatch.setattr(srv, "reveal_in_finder", revealed.append)
    (tmp_path / "review.html").write_text("<p>")

    status, payload = _call(server, "POST", srv.REVEAL, {"page": "/review.html"})

    target = (tmp_path / "review.html").resolve()
    assert status == 200 and json.loads(payload) == {"revealed": str(target)}
    assert revealed == [target]


def test_the_footer_path_reveals_nothing_outside_the_served_folder(server, tmp_path, monkeypatch):
    revealed = []
    monkeypatch.setattr(srv, "reveal_in_finder", revealed.append)
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("x")

    for page in ("/../secret.txt", str(outside), "/missing.html"):
        status, _ = _call(server, "POST", srv.REVEAL, {"page": page})
        assert status == 404, page
    status, _ = _call(server, "POST", srv.REVEAL, {"page": "/review.html"},
                      {"X-Human-Review-Token": "guess"})
    assert status == 403 and revealed == []


# ---- the three states a press can be in, and what the press does in each ----------------

class _Bridge(http.server.BaseHTTPRequestHandler):
    """A VS Code window: answers /ping with its folders, and records /command presses."""
    folders: list = []
    focus_ok = True
    pressed: list = []

    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply({"ok": True, "app": "vscode", "folder": self.folders[0]["name"],
                     "folders": self.folders})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.pressed.append(self.path)
        if self.focus_ok:
            self._reply({"ok": True})
        else:  # a bridge from before the focus command was allowed
            self._reply({"ok": False, "error": "not allowed"}, 400)

    def log_message(self, *a):
        pass


def _bridge(tmp_path, monkeypatch, folder: Path, focus_ok=True):
    H = type("H", (_Bridge,), {"folders": [{"name": folder.name, "path": str(folder),
                                            "realPath": str(folder.resolve())}],
                               "focus_ok": focus_ok, "pressed": []})
    httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    registry = tmp_path / "home" / ".walkie-talkie" / "ide"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / "vscode-1.json").write_text(
        json.dumps({"port": httpd.server_address[1], "token": "t"}))
    monkeypatch.setattr(srv.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    return httpd, H


def test_state_one_window_on_the_reviewed_commit_no_prompt(repo, tmp_path, monkeypatch):
    r, _, head = repo
    httpd, _ = _bridge(tmp_path, monkeypatch, r)
    try:
        got = srv.editor_state(head, str(r), "feature")
    finally:
        httpd.shutdown()
    assert got["state"] == "on" and got["window"] == "repo" and got["prompt"] is None
    assert "reviewed commit" in got["tip"] and "front" in got["tip"]
    assert got["checkout"] == {"path": str(r), "branch": "feature", "head": head}


def test_state_two_window_on_another_branch_offers_a_checkout_prompt(repo, tmp_path, monkeypatch):
    r, first, head = repo
    _git(r, "checkout", "-q", "-b", "elsewhere", first)
    httpd, _ = _bridge(tmp_path, monkeypatch, r)
    try:
        got = srv.editor_state(head, str(r), "feature")
    finally:
        httpd.shutdown()
    assert got["state"] == "near" and got["window"] == "repo"
    assert "elsewhere @ " + first[:8] in got["tip"] and "behind" in got["tip"]
    assert got["checkout"]["branch"] == "elsewhere"
    assert got["prompt"] == (f"In {r}, check out branch feature at {head} (stash or commit "
                             "local changes first if any; don't discard them), then confirm "
                             "with git status.")
    # Nothing was moved to find that out.
    assert _git(r, "branch", "--show-current") == "elsewhere"


def test_state_three_no_window_on_the_checkout(repo, tmp_path, monkeypatch):
    r, _, head = repo
    other = tmp_path / "other"
    other.mkdir()
    httpd, _ = _bridge(tmp_path, monkeypatch, other)
    try:
        got = srv.editor_state(head, str(r), "feature")
    finally:
        httpd.shutdown()
    assert got["state"] == "off" and got["window"] is None and got["prompt"] is None
    # The press first, the state on a line of its own (Victor, 9 Oct: the whole paragraph,
    # path and all, ended with what the click does).
    press, state = got["tip"].split("\n")[:2]
    assert press == f"Click to open a new VS Code in {r.name}"
    assert state.startswith("Open now: ")


def test_the_colour_is_about_the_servers_checkout_not_the_one_the_page_names(
        server, repo, monkeypatch):
    r, _, _ = repo
    asked = []
    monkeypatch.setattr(srv, "ROOT", r)
    monkeypatch.setattr(srv, "editor_state", lambda *a: asked.append(a) or {"state": "off", "tip": ""})
    _call(server, "GET", f"{srv.EDITOR}?sha=abc&root=/elsewhere&branch=b")
    assert asked == [("abc", str(r), "b")]


def test_pressing_green_asks_that_window_to_raise_itself_and_opens_nothing(
        repo, tmp_path, monkeypatch):
    r, _, _ = repo
    launched = []
    monkeypatch.setattr(srv, "_launch", launched.append)
    httpd, H = _bridge(tmp_path, monkeypatch, tmp_path)   # a window on the folder above it
    try:
        got = srv.open_editor(r)
    finally:
        httpd.shutdown()
    assert got == {"how": "focused", "window": tmp_path.name}
    assert H.pressed == ["/command?id=workbench.action.focusWindow"] and launched == []


def test_an_older_bridge_gets_open_a_on_the_folder_it_has_not_on_the_checkout(
        repo, tmp_path, monkeypatch):
    r, _, _ = repo
    launched = []
    monkeypatch.setattr(srv, "_launch", launched.append)
    httpd, _ = _bridge(tmp_path, monkeypatch, tmp_path, focus_ok=False)
    try:
        got = srv.open_editor(r)
    finally:
        httpd.shutdown()
    # The one spelling VS Code matches to that window, so it is reused, not duplicated.
    assert got["how"] == "focused" and launched == [str(tmp_path)]


def test_pressing_red_opens_vs_code_on_the_checkout(repo, tmp_path, monkeypatch):
    r, _, _ = repo
    launched = []
    monkeypatch.setattr(srv, "_launch", launched.append)
    monkeypatch.setattr(srv.Path, "home", classmethod(lambda cls: tmp_path / "nobody"))
    assert srv.open_editor(r) == {"how": "opened", "window": None}
    assert launched == [r]


# ---- the bridge protocol: the page's `ide`, VS Code's windows only -------------------------
# The page sends `ide=vscode` while VS Code is connected; without one, the server routes
# exactly as it always did. A registry entry of any other editor (an `idea-<pid>.json` left
# by the dropped IntelliJ plugin) is never asked.

class _Ide(http.server.BaseHTTPRequestHandler):
    """One editor's bridge: /ping with its folders, and every POST recorded. A route in
    `not_yet` answers 501, the way a bridge that does not do it answers."""
    app = "vscode"
    folders: list = []
    focused = False
    not_yet: tuple = ()
    asked: list = []

    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        assert self.path == "/ping" and self.headers["x-relay-token"] == "t"
        self._reply({"ok": True, "app": self.app, "focused": self.focused,
                     "folders": self.folders})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.asked.append((self.path.split("?")[0], json.loads(raw) if raw else None))
        if self.path.split("?")[0] in self.not_yet:
            self._reply({"ok": False, "error": "not in this version"}, 501)
        elif self.path.split("?")[0] in self.answers:
            payload, status = self.answers[self.path.split("?")[0]]
            self._reply(payload, status)
        else:
            self._reply({"ok": True})

    def log_message(self, *a):
        pass


def _ide(tmp_path, monkeypatch, app, folder: Path, focused=False, not_yet=(), answers=None):
    H = type("H", (_Ide,), {"app": app, "focused": focused, "not_yet": not_yet, "asked": [],
                            "answers": answers or {},
                            "folders": [{"name": folder.name, "path": str(folder),
                                         "realPath": str(folder.resolve())}]})
    httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    registry = tmp_path / "home" / ".human-review" / "ide"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / f"{app}-{httpd.server_address[1]}.json").write_text(
        json.dumps({"app": app, "port": httpd.server_address[1], "token": "t", "pid": 1}))
    monkeypatch.setattr(srv.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    return httpd, H


def _src(repo: Path) -> Path:
    f = repo / "src" / "Visit.java"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("class Visit {}\n" * 5)
    return f


@pytest.fixture
def stop():
    servers = []
    yield servers.append
    [s.shutdown() for s in servers]


def test_only_vs_code_registry_entries_are_read(tmp_path, monkeypatch):
    registry = tmp_path / ".human-review" / "ide"
    registry.mkdir(parents=True)
    for name in ("idea-3.json", "vscode-9.json", "notes.json"):
        (registry / name).write_text("{}")
    monkeypatch.setattr(srv.Path, "home", classmethod(lambda cls: tmp_path))
    assert [f.name for f in srv.bridge_files()] == ["vscode-9.json"]


def test_the_connected_ide_takes_the_click_and_the_answer_says_so(
        server, repo, tmp_path, monkeypatch, stop):
    r, _, _ = repo
    f = _src(r)
    vsc, V = _ide(tmp_path, monkeypatch, "vscode", r)
    stop(vsc)
    monkeypatch.setattr(srv, "ROOT", r)
    status, payload = _call(server, "GET", f"{srv.OPEN}?path={f}&line=3&ide=vscode")
    assert status == 200 and json.loads(payload) == {"ide": "vscode", "fallback": False}
    assert V.asked == [("/open-file", {"path": str(f), "line": 3, "focus": True})]


def test_a_leftover_intellij_bridge_is_never_asked(server, repo, tmp_path, monkeypatch, stop):
    r, _, _ = repo
    f = _src(r)
    ij, I = _ide(tmp_path, monkeypatch, "idea", r)
    stop(ij)
    monkeypatch.setattr(srv, "ROOT", r)
    opened = []
    monkeypatch.setattr(srv.subprocess, "run", lambda cmd, **kw: opened.append(cmd))
    for ask in ("", "&ide=idea", "&ide=vscode"):
        opened.clear()
        status, _ = _call(server, "GET", f"{srv.OPEN}?path={f}&line=3{ask}")
        # No VS Code window, so the OS's vscode:// — and with no pick, a 204 as before.
        assert status == (200 if ask == "&ide=vscode" else 204) and I.asked == []
        assert opened == [["open", f"vscode://file/{f}:3:1"]]


def test_a_bridge_without_review_open_falls_back_to_open_file(
        server, repo, tmp_path, monkeypatch, stop):
    """501 is "not yet", not "no": the click still opens the file in the same editor."""
    r, _, head = repo
    f = _src(r)
    vsc, V = _ide(tmp_path, monkeypatch, "vscode", r, not_yet=("/review-open",))
    stop(vsc)
    monkeypatch.setattr(srv, "ROOT", r)
    status, payload = _call(server, "GET", f"{srv.OPEN}?path={f}&line=3&sha={head}&root={r}"
                                           "&branch=feature&ide=vscode")
    assert status == 200 and json.loads(payload)["ide"] == "vscode"
    assert [p for p, _ in V.asked] == ["/review-open", "/open-file"]


def test_a_bridge_with_diffs_takes_the_diff_and_nothing_else(
        server, repo, tmp_path, monkeypatch, stop):
    r, first, _ = repo
    vsc, V = _ide(tmp_path, monkeypatch, "vscode", r)
    stop(vsc)
    monkeypatch.setattr(srv, "ROOT", r)
    monkeypatch.setattr(srv, "code_cli", lambda: pytest.fail("VS Code's CLI was asked"))
    status, payload = _call(server, "GET",
                            f"{srv.OPEN_DIFF}?path=a.txt&base={first}&line=1&ide=vscode")
    assert status == 200 and json.loads(payload) == {"ide": "vscode", "fallback": False}
    assert V.asked == [("/open-diff", {"file": str(r / "a.txt"), "base": first, "line": 1,
                                       "focus": True})]


NO_WINDOW = {"ok": False, "error": "no-window", "message": "No open VS Code window is on this commit",
             "prompt": "Find a local checkout"}


def test_a_no_window_refusal_reaches_the_page_with_its_prompt(
        server, repo, tmp_path, monkeypatch, stop):
    """The bridge's 409 is relayed as it is, never an open-file."""
    r, _, head = repo
    f = _src(r)
    vsc, V = _ide(tmp_path, monkeypatch, "vscode", r, answers={"/review-open": (NO_WINDOW, 409)})
    stop(vsc)
    monkeypatch.setattr(srv, "ROOT", r)
    status, payload = _call(server, "GET", f"{srv.OPEN}?path={f}&line=3&sha={head}&root={r}"
                                           "&branch=feature&ide=vscode")
    assert status == 409 and json.loads(payload) == {**NO_WINDOW, "ide": "vscode", "fallback": False}
    assert [p for p, _ in V.asked] == ["/review-open"]


def test_a_refusal_from_a_bridge_that_lost_git_is_overruled_and_the_file_opens(
        server, repo, tmp_path, monkeypatch, stop):
    """An editor updated on disk under its own feet cannot start git, so its bridge says no
    window is on this commit with that very window open on it: the server checks for itself."""
    r, _, _ = repo
    f = _src(r)
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "src")
    head = _git(r, "rev-parse", "HEAD")
    vsc, V = _ide(tmp_path, monkeypatch, "vscode", r, answers={"/review-open": (NO_WINDOW, 409)})
    stop(vsc)
    monkeypatch.setattr(srv, "ROOT", r)
    status, payload = _call(server, "GET", f"{srv.OPEN}?path={f}&line=3&sha={head}&root={r}"
                                           "&branch=feature&ide=vscode&comment=1")
    assert status == 200
    assert json.loads(payload) == {"ok": True, "path": str(f), "edited": False,
                                   "ide": "vscode", "fallback": False}
    assert [p for p, _ in V.asked] == ["/review-open", "/open-file"]
    assert V.asked[1][1]["comment"] is True


def test_a_true_refusal_stands_when_the_file_changed_since_the_reviewed_commit(
        server, repo, tmp_path, monkeypatch, stop):
    r, _, _ = repo
    f = _src(r)
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "src")
    reviewed = _git(r, "rev-parse", "HEAD")
    f.write_text("class Moved {}\n")
    _git(r, "commit", "-qam", "moved")
    vsc, V = _ide(tmp_path, monkeypatch, "vscode", r, answers={"/review-open": (NO_WINDOW, 409)})
    stop(vsc)
    monkeypatch.setattr(srv, "ROOT", r)
    status, _ = _call(server, "GET", f"{srv.OPEN}?path={f}&line=3&sha={reviewed}&root={r}"
                                     "&branch=feature&ide=vscode")
    assert status == 409
    assert [p for p, _ in V.asked] == ["/review-open"]


def test_the_badge_poll_lists_vs_code_on_this_checkout_only(repo, tmp_path, monkeypatch, stop):
    r, _, head = repo
    other = tmp_path / "other"
    other.mkdir()
    for app, folder, focused in (("vscode", r, False), ("idea", r, True),
                                 ("vscode", other, True)):
        stop(_ide(tmp_path, monkeypatch, app, folder, focused=focused)[0])
    got = srv.editor_state(head, str(r), "feature")
    assert got["ides"] == [{"app": "vscode", "label": "VS Code", "focused": False}]
    assert got["state"] == "on" and got["tip"].startswith("Click to bring VS Code window")
    assert srv.editor_state(head, str(r), "feature", ide="vscode") == got


def test_the_poll_endpoint_passes_the_pick_on_only_when_sent(server, monkeypatch):
    asked = []
    monkeypatch.setattr(srv, "ROOT", None)
    monkeypatch.setattr(srv, "editor_state",
                        lambda *a, **k: asked.append((a, k)) or {"state": "on", "tip": "x"})
    _call(server, "GET", f"{srv.EDITOR}?sha=abc&root=/r&branch=b&ide=vscode")
    _call(server, "GET", f"{srv.EDITOR}?sha=abc&root=/r&branch=b&ide=idea")
    assert asked == [(("abc", "/r", "b"), {"ide": "vscode"}), (("abc", "/r", "b"), {})]


def test_pressing_the_badge_raises_or_opens_vs_code(repo, tmp_path, monkeypatch, stop):
    r, _, _ = repo
    launched = []
    monkeypatch.setattr(srv, "_launch", lambda *a: launched.append(a))
    vsc, V = _ide(tmp_path, monkeypatch, "vscode", r)
    stop(vsc)
    assert srv.open_editor(r, "vscode") == {"how": "focused", "window": r.name}
    assert V.asked == [("/command", None)] and launched == []
    monkeypatch.setattr(srv.Path, "home", classmethod(lambda cls: tmp_path / "nobody"))
    assert srv.open_editor(r, "vscode") == {"how": "opened", "window": None}
    assert launched == [(r,)]


def test_the_badge_press_endpoint_takes_the_pick_from_its_body(server, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(srv, "open_editor", lambda *a: opened.append(a) or {"how": "opened"})
    monkeypatch.setattr(srv, "ROOT", tmp_path)
    status, payload = _call(server, "POST", srv.EDITOR_OPEN, {"ide": "vscode"})
    assert status == 200 and json.loads(payload)["ide"] == "vscode"
    _call(server, "POST", srv.EDITOR_OPEN, {})
    _call(server, "POST", srv.EDITOR_OPEN, {"ide": "idea"})
    assert opened == [(tmp_path, "vscode"), (tmp_path,), (tmp_path,)]


def test_the_intellij_only_routes_are_gone(server, tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "ROOT", tmp_path)
    for route in ("/__open_pr__", "/__open_db__"):
        status, _ = _call(server, "POST", route, {"url": "https://github.com/o/r/pull/51"})
        assert status == 404, route


# ---- the page: the picker, and what a click sends ------------------------------------------

@pytest.fixture(scope="module")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                       # no chromium installed on this box
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        b.close()


def _picker_page(browser, ides, pick=None, extra="", state="on"):
    """A served page with the masthead logos, one source link and one *in VS Code* link,
    against a fake server whose badge poll answers `ides`. Returns the page and the
    requests it sent (`METHOD path`, and the body of a POST)."""
    from test_action_server import build
    sent = []
    link = build.gh_comment_link({"_ghUrl": "https://github.com/o/r/pull/51#discussion_r1",
                                  "_ghFile": "vscode://file//r/src/A.java:12:1",
                                  "_ghWhere": "src/A.java:12"})
    body = ('<!doctype html><html data-hr-head="abc" data-hr-root="/r"><head>'
            + '<style>' + build.CSS + '</style>' + build.SERVER_JS
            + '</head><body><div class="titlerow"><span class="titleside">'
            + build.IDE_CHIPS
            + '</span></div><a class="srcref" id="ref" href="vscode://file//r/src/A.java:3:1">'
            f'A.java:3</a><p id="gh">{link}</p>' + extra + build.TIP_JS + build.EDITOR_JS
            + '</body></html>')

    def serve(route):
        req = route.request
        url = req.url
        sent.append(req.method + " " + url.split("127.0.0.1:9", 1)[1]
                    + (" " + (req.post_data or "") if req.method == "POST" else ""))
        if url.endswith("/review.html"):
            return route.fulfill(body=body, content_type="text/html")
        if "/__human_review__" in url:
            return route.fulfill(body='{"humanReview": 1, "token": "t"}',
                                 content_type="application/json")
        if "/__editor__" in url:
            tip = "Click to open a new VS Code in r\nstate line" if state == "off" \
                else "Click\nstate line"
            return route.fulfill(body=json.dumps({"state": state, "tip": tip,
                                                  "ides": ides}),
                                 content_type="application/json")
        if "/__editor_open__" in url:
            return route.fulfill(body='{"how": "focused", "window": "r", "opened": "/r"}',
                                 content_type="application/json")
        if "/__open__" in url:
            return route.fulfill(body='{"ide": "vscode", "fallback": false}',
                                 content_type="application/json")
        return route.fulfill(status=404, body="")
    page = browser.new_page()
    if pick:
        page.add_init_script(f"localStorage.setItem('hr-ide', '{pick}')")
    page.route("http://127.0.0.1:9/**", serve)
    page.goto("http://127.0.0.1:9/review.html")
    return page, sent


IDES1 = [{"app": "vscode", "label": "VS Code", "focused": False}]


def _states(page):
    """Each logo as `app:state`, `app:hidden` when the masthead does not show it."""
    return page.eval_on_selector_all(
        ".chip-ide", "cs => cs.map(c => c.dataset.ide + ':' + (c.hidden ? 'hidden' : "
        "(['ide-on', 'ide-near', 'ide-off'].find(k => c.classList.contains(k)) || '-')))")


def test_the_logo_connects_and_disconnects_vs_code_and_clicks_follow_it(browser):
    page, sent = _picker_page(browser, IDES1)
    page.wait_for_timeout(300)
    # Not connected until the reader connects it, whatever has a window: red.
    assert _states(page) == ["vscode:ide-off"]
    assert page.evaluate("document.documentElement.classList.contains('ide-none')")
    assert page.get_attribute("#hr-ide-vscode", "data-tip") == "Click to connect VS Code"
    assert page.get_attribute("#hr-ide-vscode", "data-tip-html") is None
    # Red → connected, green.
    page.click("#hr-ide-vscode")
    assert _states(page) == ["vscode:ide-on"]
    assert page.get_attribute("#hr-ide-vscode", "aria-pressed") == "true"
    assert page.get_attribute("#hr-ide-vscode", "data-tip") == "Click to disconnect VS Code"
    assert page.locator("a.f-vsc .ide-word").text_content() == "VS Code"
    assert page.locator("a.f-vsc svg.vsc-ico").is_visible()
    page.wait_for_timeout(200)
    assert any(u.startswith("POST /__editor_open__") and '"ide":"vscode"' in u for u in sent), sent
    page.click("#ref")
    page.wait_for_timeout(200)
    assert any(u.startswith("GET /__open__?") and "&ide=vscode" in u for u in sent), sent
    assert any(u.startswith("GET /__editor__?") and "&ide=vscode" in u for u in sent), sent
    # The pick is the reader's, kept across a reload.
    page.reload()
    page.wait_for_function("document.querySelector('#hr-ide-vscode.ide-on')")
    assert _states(page) == ["vscode:ide-on"]
    # Green → disconnected: red; no IDE link, and a click on code copies.
    page.click("#hr-ide-vscode")
    assert _states(page) == ["vscode:ide-off"]
    assert not page.locator("a.f-vsc").is_visible()
    before = len([u for u in sent if "/__open__" in u])
    page.click("#ref")
    page.wait_for_timeout(200)
    assert len([u for u in sent if "/__open__" in u]) == before, sent
    assert "no IDE connected" in page.locator("#copy-toast").inner_text()
    page.close()


def test_a_connected_ide_with_no_window_on_the_checkout_is_red_and_a_press_opens_it(browser):
    """Victor, 29 Oct 2026: the IDE was green while its only window had another project,
    and every button failed until a disconnect and a reconnect."""
    page, sent = _picker_page(browser, [], pick="vscode", state="off")
    page.wait_for_function("document.querySelector('#hr-ide-vscode.ide-off')")
    assert _states(page) == ["vscode:ide-off"]
    assert page.get_attribute("#hr-ide-vscode", "data-tip") == "Click to open a new VS Code in r"
    page.click("#hr-ide-vscode")
    page.wait_for_timeout(200)
    assert any(u.startswith("POST /__editor_open__") and '"ide":"vscode"' in u for u in sent), sent
    assert page.evaluate("localStorage.getItem('hr-ide')") == "vscode", "still the pick"
    page.close()


def test_the_connected_logo_tip_says_only_what_a_click_does(browser):
    """Victor, 20 Oct 2026: "say click to disconnect, the rest should not be there"."""
    page, sent = _picker_page(browser, IDES1, pick="vscode")
    page.wait_for_function("document.querySelector('#hr-ide-vscode.ide-on')")
    page.wait_for_timeout(300)
    assert page.get_attribute("#hr-ide-vscode", "data-tip") == "Click to disconnect VS Code"
    assert page.get_attribute("#hr-ide-vscode", "data-tip-html") is None
    page.close()


def test_a_findings_title_opens_its_reference_in_the_picked_ide(browser):
    """Victor, 20 Oct 2026: a click on the card's title opens its lines; the GitHub and IDE
    icons sit at the right end of its header row, icons only."""
    from test_action_server import build
    card = build._render_finding_items([{
        "severity": "high", "title": "Unknown vetId", "body": "b",
        "_ghUrl": "https://github.com/o/r/pull/51#discussion_r1",
        "_ghFile": "vscode://file//r/src/A.java:12:1", "_ghWhere": "src/A.java:12"}])
    page, sent = _picker_page(browser, IDES1, pick="vscode", extra=f'<ol class="findings">{card}</ol>')
    page.wait_for_timeout(300)
    li = page.locator("ol.findings > li").first
    box, gh, ide = (li.bounding_box(), li.locator("> a.f-gh").bounding_box(),
                    li.locator("> a.f-vsc").bounding_box())
    assert gh["x"] + gh["width"] > box["x"] + box["width"] - 40, (box, gh)
    # Icons only, the same size, on one centre line, the IDE's left of GitHub's.
    assert ide["width"] < 20 and abs(ide["width"] - gh["width"]) < 1, (ide, gh)
    assert ide["x"] < gh["x"] and abs((ide["y"] + ide["height"] / 2)
                                      - (gh["y"] + gh["height"] / 2)) < 1.5, (ide, gh)
    page.click("ol.findings .f-title")
    page.wait_for_timeout(200)
    assert any(u.startswith("GET /__open__?") and "A.java" in u and "&ide=vscode" in u
               for u in sent), sent
    page.close()


def test_a_folder_box_names_the_ide_to_a_screen_reader_too(browser):
    """The Structure tab's package boxes name VS Code in their aria-label once it is
    connected, including a connection made after the page painted."""
    from test_action_server import build
    body = ('<!doctype html><html data-hr-head="abc" data-hr-root="/r"><head>'
            + '<style>' + build.CSS + '</style>' + build.SERVER_JS
            + '</head><body><div class="titlerow"><span class="titleside">'
            + build.IDE_CHIPS
            + '</span></div><svg><g id="box" data-folder="src/owner" data-folder-name="owner">'
            '<rect width="9" height="9"/></g></svg>'
            + build.TIP_JS + build.EDITOR_JS + build.FOLDERS_JS + '</body></html>')
    sent = []

    def serve(route):
        url = route.request.url
        sent.append(url)
        if url.endswith("/review.html"):
            return route.fulfill(body=body, content_type="text/html")
        if "/__human_review__" in url:
            return route.fulfill(body='{"humanReview": 1, "token": "t", "revealFolder": true}',
                                 content_type="application/json")
        if "/__editor__" in url:
            return route.fulfill(body=json.dumps({"state": "on", "tip": "x", "ides": IDES1}),
                                 content_type="application/json")
        return route.fulfill(status=404, body="")
    page = browser.new_page()
    page.route("http://127.0.0.1:9/**", serve)
    page.goto("http://127.0.0.1:9/review.html")
    page.wait_for_timeout(300)
    page.click("#hr-ide-vscode")
    assert page.get_attribute("#box", "aria-label") == "Reveal owner in VS Code"
    # Disconnected: the box raises no IDE.
    page.click("#hr-ide-vscode")
    page.click("#box")
    page.wait_for_timeout(200)
    assert not any("/__reveal_folder__" in u for u in sent), sent
    page.close()


def _offdisk_page(browser, tmp_path, pick=None):
    """The page read off disk (`file:`): no server, the pick only picks the link scheme."""
    from test_action_server import build
    f = tmp_path / "review.html"
    f.write_text('<!doctype html><html data-hr-head="abc" data-hr-root="/r"><head>'
                 + '<style>' + build.CSS + '</style>' + build.SERVER_JS + '</head><body>'
                 + '<div class="titlerow"><span class="titleside">' + build.IDE_CHIPS
                 + '</span></div>'
                 + build.gh_comment_link({"_ghUrl": "https://github.com/o/r/pull/51#discussion_r1",
                                          "_ghFile": "vscode://file//r/src/A.java:12:1",
                                          "_ghWhere": "src/A.java:12"})
                 + build.TIP_JS + build.EDITOR_JS + '</body></html>')
    page = browser.new_page()
    if pick:
        page.add_init_script(f"localStorage.setItem('hr-ide', '{pick}')")
    page.goto(f.as_uri())
    page.wait_for_timeout(200)
    return page


def test_off_disk_the_remembered_pick_words_the_links(browser, tmp_path):
    """No server: the switch still words the links. A pick left over from the dropped
    IntelliJ support reads as not connected."""
    for pick, word in (("vscode", "VS Code"), (None, "your IDE"), ("idea", "your IDE")):
        page = _offdisk_page(browser, tmp_path, pick)
        assert page.locator("a.f-vsc .ide-word").text_content() == word
        page.close()


def test_off_disk_the_logos_toggle_too(browser, tmp_path):
    page = _offdisk_page(browser, tmp_path)
    assert _states(page) == ["vscode:ide-off"]
    page.click("#hr-ide-vscode")
    assert _states(page) == ["vscode:ide-on"]
    assert page.locator("a.f-vsc .ide-word").text_content() == "VS Code"
    page.click("#hr-ide-vscode")
    assert _states(page) == ["vscode:ide-off"]
    assert page.evaluate("localStorage.getItem('hr-ide')") == "none"
    page.close()


def test_online_the_logos_are_greyed_and_a_press_changes_nothing(browser):
    """GitHub Pages: no IDE on the reader's machine is reachable from here."""
    from test_action_server import build
    body = ('<!doctype html><html data-hr-head="abc" data-hr-root="/r"><head>'
            + '<style>' + build.CSS + '</style>' + build.SERVER_JS + '</head><body>'
            + '<div class="titlerow"><span class="titleside">' + build.IDE_CHIPS
            + '</span></div>' + build.TIP_JS + build.EDITOR_JS + '</body></html>')
    page = browser.new_page()
    page.route("https://example.github.io/**",
               lambda route: route.fulfill(body=body, content_type="text/html"))
    page.goto("https://example.github.io/review.html")
    page.wait_for_timeout(200)
    assert _states(page) == ["vscode:-"]
    assert page.evaluate("getComputedStyle(document.getElementById('hr-ide-vscode')).opacity") == "0.45"
    page.click("#hr-ide-vscode", force=True)          # aria-disabled: Playwright would wait
    assert _states(page) == ["vscode:-"]
    assert "local copy" in page.locator("#copy-toast").inner_text()
    page.close()
