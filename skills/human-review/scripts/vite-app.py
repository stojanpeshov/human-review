#!/usr/bin/env python3
"""
vite-app — one Vite dev server per commit, for the design-system audit's two sides.

The audit compares the branch with its merge-base, both running at once. A front end
served by Vite needs no build and no container for that: a dev server on the commit's
tree, on a port nobody else holds, is the whole instance. This is `steps.dsaudit.app`'s
`up` / `down` / `url` for such an app — `run-steps.py` expands `{sha}` (twice: HEAD's and
the merge-base's) and `{scripts}` (this folder):

    "app": {"up":   "python3 {scripts}/vite-app.py up {sha}",
            "down": "python3 {scripts}/vite-app.py down {sha}",
            "url":  "python3 {scripts}/vite-app.py url {sha}"}

`up` serves the checkout itself when `sha` is its HEAD — so what is audited is what the
reviewer has, uncommitted edits included — and a detached `git worktree` of `sha`
otherwise, with the checkout's `node_modules` linked into it (installing per commit would
cost minutes and a registry login; a base whose dependencies differ is the one case this
gets wrong, and the log says which tree was served). It prints `http://127.0.0.1:<port>`
once the server answers. `down` stops it and removes the worktree.

Defaults fit mitt-ludvig: the app in `frontend/`, served with its API and its login mocked
(`VITE_USE_MOCKS`, `VITE_USE_MOCK_AUTH`) and over plain http (`VITE_STAGE=jest` is what its
`vite.config.ts` reads to leave the local certificate out). `--dir`, `--env K=V` and
`--cmd` change them. Vite's dependency cache goes to the instance's own folder, never
into the shared `node_modules/.vite`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_ENV = {"VITE_STAGE": "jest", "VITE_USE_MOCKS": "true", "VITE_USE_MOCK_AUTH": "true",
               "BROWSER": "none"}
DEFAULT_CMD = "npx vite --config {config} --port {port} --strictPort --host 127.0.0.1"
CONFIGS = ("vite.config.ts", "vite.config.mts", "vite.config.js", "vite.config.mjs")


def wrapper_config(app: Path, st: Path) -> Path | None:
    """A config that is the app's own with one change: Vite's dependency cache in this
    instance's folder. By default it lives in `node_modules/.vite`, and the two instances
    share one `node_modules` (the worktree's is a link to the checkout's): two servers
    optimising into one cache, and the checkout's own `npm run dev` re-optimising after
    every audit. Imported by absolute path — Vite bundles a config with the files it
    imports, each keeping its own `__dirname` — so nothing is written into the tree."""
    own = next((app / c for c in CONFIGS if (app / c).is_file()), None)
    if own is None:
        return None
    out = st / "vite.config.mjs"
    out.write_text(
        f"import base from {json.dumps(str(own))};\n"
        "export default async (env) => {\n"
        "  const c = typeof base === 'function' ? await base(env) : await base;\n"
        f"  return {{ ...c, cacheDir: {json.dumps(str(st / 'vite-cache'))} }};\n"
        "};\n")
    return out


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def state_dir(root: Path, sha: str) -> Path:
    """Where one commit's instance keeps its worktree, pid, port and log: under the temp
    folder, keyed on the checkout and the commit, so two checkouts never share one."""
    key = hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:8]
    base = Path(os.environ.get("TMPDIR") or "/tmp") / "human-review-vite"
    return base / f"{root.resolve().name}-{key}-{sha[:12]}"


def answers(url: str, timeout: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout).close()
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def running(st: Path) -> str | None:
    """The URL of this commit's server, when it is up and answering."""
    try:
        pid = int((st / "pid").read_text())
        url = (st / "url").read_text().strip()
    except (OSError, ValueError):
        return None
    return url if _alive(pid) and answers(url) else None


def tree_for(root: Path, sha: str, st: Path) -> tuple[Path, str]:
    """The tree to serve `sha` from: the checkout when it is HEAD, else a worktree."""
    full = _git(root, "rev-parse", sha) or sha
    if full == _git(root, "rev-parse", "HEAD"):
        return root, "the checkout (HEAD, working tree as it is)"
    wt = st / "wt"
    if not (wt / ".git").exists():
        st.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach", "--force",
                            str(wt), full], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"vite-app: git worktree add {full[:12]} failed: {r.stderr.strip()}")
    return wt, f"a worktree of {full[:12]}"


def link_modules(root: Path, tree: Path, rel: str) -> None:
    if tree == root:
        return
    src, dst = root / rel / "node_modules", tree / rel / "node_modules"
    if src.is_dir() and not dst.exists() and dst.parent.is_dir():
        dst.symlink_to(src.resolve(), target_is_directory=True)


def up(args) -> int:
    root = Path(args.root).resolve()
    st = state_dir(root, args.sha)
    url = running(st)
    if url:
        print(url)
        return 0
    tree, what = tree_for(root, args.sha, st)
    link_modules(root, tree, args.dir)
    cwd = tree / args.dir
    if not cwd.is_dir():
        sys.exit(f"vite-app: no {args.dir}/ in {tree}")
    port = free_port()
    env = dict(os.environ, **DEFAULT_ENV)
    for kv in args.env:
        k, _, v = kv.partition("=")
        env[k] = v
    st.mkdir(parents=True, exist_ok=True)
    log = open(st / "log", "w")
    config = wrapper_config(cwd, st)
    cmd = args.cmd.replace("{port}", str(port))
    cmd = cmd.replace("--config {config} ", "") if config is None else \
        cmd.replace("{config}", str(config))
    log.write(f"# {what}: {cwd}\n# {cmd}\n")
    log.flush()
    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, stdout=log,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            start_new_session=True)
    url = f"http://127.0.0.1:{port}"
    (st / "pid").write_text(str(proc.pid))
    (st / "url").write_text(url)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            sys.stderr.write((st / "log").read_text()[-2000:])
            sys.exit(f"vite-app: the dev server for {args.sha[:12]} exited ({proc.returncode})")
        if answers(url):
            print(f"vite-app: {what} on {url}", file=sys.stderr)
            print(url)
            return 0
        time.sleep(0.5)
    _stop(st)
    sys.exit(f"vite-app: nothing answered on {url} after {args.timeout}s — see {st / 'log'}")


def _stop(st: Path) -> None:
    try:
        pid = int((st / "pid").read_text())
    except (OSError, ValueError):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        pass
    for _ in range(20):
        if not _alive(pid):
            break
        time.sleep(0.1)
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
    (st / "pid").unlink(missing_ok=True)


def down(args) -> int:
    root = Path(args.root).resolve()
    st = state_dir(root, args.sha)
    _stop(st)
    wt = st / "wt"
    if wt.exists():
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(wt)],
                       capture_output=True)
    return 0


def url(args) -> int:
    got = running(state_dir(Path(args.root).resolve(), args.sha))
    if got:
        print(got)
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("up", "down", "url"))
    ap.add_argument("sha", help="the commit to serve (run-steps passes {sha})")
    ap.add_argument("--root", default=".", help="the checkout (default: the current folder)")
    ap.add_argument("--dir", default="frontend", help="the app's folder in the tree")
    ap.add_argument("--env", action="append", default=[], metavar="K=V",
                    help="added to the server's environment, over the defaults "
                         f"({' '.join(f'{k}={v}' for k, v in DEFAULT_ENV.items())})")
    ap.add_argument("--cmd", default=DEFAULT_CMD,
                    help=f"the server command, {{port}} filled in (default: {DEFAULT_CMD!r})")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args(argv)
    return {"up": up, "down": down, "url": url}[args.action](args)


if __name__ == "__main__":
    sys.exit(main())
