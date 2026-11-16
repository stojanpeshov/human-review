#!/usr/bin/env python3
"""vite-app: one dev server per commit — the checkout for HEAD, a worktree for the base.

The server is a stand-in (`python3 -m http.server {port}`): what is under test is which
tree it is started in, that `node_modules` is linked rather than installed, that it
answers before `up` returns, and that `down` leaves nothing behind.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("vite_app", HERE / "vite-app.py")
va = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(va)


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "app"
    (root / "frontend" / "node_modules" / "vite").mkdir(parents=True)

    def git(*a):
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True,
                              check=True).stdout.strip()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (root / ".gitignore").write_text("node_modules\n")
    (root / "frontend" / "vite.config.ts").write_text("export default {};\n")
    (root / "frontend" / "index.html").write_text("base\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (root / "frontend" / "index.html").write_text("branch\n")
    git("commit", "-qam", "branch")
    return root, base, git("rev-parse", "HEAD")


def _run(root: Path, *args: str, tmp: Path) -> subprocess.CompletedProcess:
    cmd = f"{sys.executable} -m http.server {{port}} --bind 127.0.0.1"
    return subprocess.run([sys.executable, str(HERE / "vite-app.py"), *args, "--root",
                           str(root), "--cmd", cmd, "--timeout", "20"],
                          capture_output=True, text=True, env={"TMPDIR": str(tmp),
                                                               "PATH": "/usr/bin:/bin"})


def test_the_base_is_served_from_a_worktree_and_head_from_the_checkout(tmp_path, monkeypatch):
    root, base, head = _repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("TMPDIR", str(state))
    try:
        new = _run(root, "up", head, tmp=state)
        old = _run(root, "up", base, tmp=state)
        assert new.returncode == 0 and old.returncode == 0, new.stderr + old.stderr
        new_url, old_url = new.stdout.strip(), old.stdout.strip()
        assert new_url != old_url and new_url.startswith("http://127.0.0.1:")
        import urllib.request

        def read(u):
            return urllib.request.urlopen(u + "/index.html").read().decode()
        assert read(new_url) == "branch\n" and read(old_url) == "base\n"
        assert "the checkout" in new.stderr and "a worktree of" in old.stderr
        wt = va.state_dir(root, base) / "wt"
        assert (wt / "frontend" / "node_modules").is_symlink()
        # Vite's cache is the instance's own, not node_modules/.vite.
        cfg = (va.state_dir(root, base) / "vite.config.mjs").read_text()
        assert str(wt / "frontend" / "vite.config.ts") in cfg and "vite-cache" in cfg
        # Asked again, the running instance answers instead of a second one starting.
        again = _run(root, "up", base, tmp=state)
        assert again.stdout.strip() == old_url
        assert _run(root, "url", base, tmp=state).stdout.strip() == old_url
    finally:
        _run(root, "down", head, tmp=state)
        _run(root, "down", base, tmp=state)
    assert not (va.state_dir(root, base) / "wt").exists()
    assert _run(root, "url", base, tmp=state).returncode == 1
