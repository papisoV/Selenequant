#!/usr/bin/env python3
"""SeleneQuant dev launcher — cross-platform (Windows / macOS / Linux).

Replaces ``dev.bat`` for anyone not on Windows. Same job:

  1. put every workspace package on PYTHONPATH (the ``activate.bat`` contract,
     reimplemented without a shell so it works on POSIX too),
  2. start the FastAPI backend and the Next.js web app as child processes,
  3. poll ``/api/v1/health`` until the backend is ready, then print the URLs,
  4. on Ctrl-C, tear both children down (whole process trees on Windows).

Usage::

    python dev.py                 # backend :8088 + web :3000 (next dev)
    python dev.py --prod          # same, but serve a production build (snappier)
    python dev.py --prod --rebuild  # force a fresh build first
    python dev.py --reload        # restart the backend when backend code changes
    python dev.py --backend-only  # just the API
    python dev.py --web-only      # just the web app
    python dev.py --port 8089 --web-port 3001

**Use ``--prod`` if you are running the app rather than editing it.** ``next
dev`` compiles each page the first time you open it; with ~142 routes that is
what makes the app feel slow. ``--prod`` builds once (skipped when the build is
already fresh) and then serves precompiled pages.

**``--reload`` is off by default and that is deliberate.** uvicorn's reloader
rescans the whole repo on every trigger; with 50k+ files that is a 6-10s lag
per request, which is why the default is a plain server. Turn it on when you
are iterating on backend code and want the process to restart on save.

Deliberately does NOT set ``SELENE_PROXY_URL``. The bundled ``.bat`` launchers
used to hardcode ``http://127.0.0.1:7897`` (a Clash default); on a machine
without that proxy running, every exchange call fails with a refused
connection. Set ``SELENE_PROXY_URL`` yourself if you do run a local proxy.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Mirrors the package list in activate.bat. Keep the two in sync.
WORKSPACE_PACKAGES = (
    "shared-types",
    "core",
    "cli",
    "strategy-engine",
    "backtest-engine",
    "market-data",
    "agent-runtime",
    "exchange-sdk",
    "llm-adapter",
    "paper-engine",
    "risk-engine",
    "security-gateway",
    "storage",
    "compat-layer",
    "alpha-core",
    "stock",
    "ashare",
    "pulse",
    "tg-ingest",
    "macrodata",
    "decision-workflow",
    "optimize",
    "agentstudio",
    "daily-cycle",
    "renintel",
    "market-analysis",
    "factor-research",
    "grid-engine",
)

BACKEND_DIR = ROOT / "services" / "local-api"
WEB_DIR = ROOT / "apps" / "desktop"


def build_env() -> dict[str, str]:
    """PYTHONPATH contract from activate.bat, plus the two env pins it sets."""
    entries = [str(ROOT)]
    entries += [str(ROOT / "packages" / name) for name in WORKSPACE_PACKAGES]
    entries.append(str(BACKEND_DIR))

    env = dict(os.environ)
    # Prepend rather than replace so an outer PYTHONPATH keeps working.
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(entries + ([existing] if existing else []))

    # LITELLM fetches a remote model-cost map on import; on networks where
    # raw.githubusercontent.com is unreachable that blocks startup for ~40s.
    # The bundled local map is used instead.
    env.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

    # Pin the klines DB to this replica's repo root. The code default
    # (selene_space_core.kline_paths) already resolves there, but the env pin
    # keeps each parallel worktree on its OWN data/ dir instead of the shared
    # default when several replicas run side by side.
    env.setdefault("SELENE_KLINES_DB", str(ROOT / "data" / "klines.db"))

    return env


def _npm_command() -> str:
    """npm is npm.cmd on Windows, plain npm elsewhere."""
    return "npm.cmd" if os.name == "nt" else "npm"


def start_backend(
    env: dict[str, str], port: int, *, reload: bool = False
) -> subprocess.Popen:
    """Start uvicorn.

    ``reload`` is off by default: the reloader rescans this repo's 50k+ files
    on every trigger (6-10s lag per request, see module docstring). Pass
    ``--reload`` when iterating on backend code and want restart-on-save.
    """
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    if reload:
        # uvicorn's reloader watches CWD, so scope it to the backend sources:
        # watching the repo root would scan every worktree copy on each change.
        cmd += ["--reload", "--reload-dir", str(BACKEND_DIR)]
    return subprocess.Popen(cmd, cwd=str(BACKEND_DIR), env=env)


def _web_needs_build() -> bool:
    """Does the prebuilt frontend need (re)building?

    Compares the newest source file against ``.next/BUILD_ID``.

    ``public/`` is deliberately NOT watched. Next serves that directory from
    disk at request time — it is not compiled into the build — so a change
    there (a swapped logo, say) shows up without a rebuild, and watching it
    would only trigger pointless builds.

    ``prod.bat`` keeps its own check in ``_buildcheck.ps1``, which watches a
    narrower set (no ``public/``, only ``next.config.ts``). The two therefore
    disagree on edge cases by design: this one errs toward rebuilding.
    """
    build_id = WEB_DIR / ".next" / "BUILD_ID"
    if not build_id.exists():
        return True
    built_at = build_id.stat().st_mtime
    watched = ("app", "components", "lib")
    newest = 0.0
    for sub in watched:
        base = WEB_DIR / sub
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
    for name in ("package.json", "next.config.mjs", "next.config.ts", "next.config.js"):
        p = WEB_DIR / name
        if p.exists():
            newest = max(newest, p.stat().st_mtime)
    return newest > built_at


def build_web(env: dict[str, str]) -> int:
    """Run ``npm run build`` once. Returns the exit code."""
    _say("[dev] building the web app (one-off, first prod start after a change)...")
    proc = subprocess.Popen(
        [_npm_command(), "run", "build"],
        cwd=str(WEB_DIR),
        env=env,
        shell=(os.name == "nt"),
    )
    return proc.wait()


def start_web(
    env: dict[str, str], port: int, *, prod: bool = False
) -> subprocess.Popen:
    """Start the web app.

    ``dev`` (default) is `next dev`: edit-and-refresh, but every page compiles
    on first visit — with 142 routes that reads as "the app is slow".

    ``prod`` is `next start` against an existing build: no per-page compile,
    noticeably snappier, and no dev-server worker growth. Right choice for
    actually using the app rather than editing it.
    """
    env = dict(env)
    env["LOCAL_API_BASE"] = env.get("LOCAL_API_BASE", "http://127.0.0.1:8088")
    if prod:
        return subprocess.Popen(
            [_npm_command(), "run", "start", "--", "-p", str(port)],
            cwd=str(WEB_DIR),
            env=env,
            shell=(os.name == "nt"),
        )
    return subprocess.Popen(
        [_npm_command(), "run", "dev", "--", "-p", str(port)],
        cwd=str(WEB_DIR),
        env=env,
        shell=(os.name == "nt"),  # npm.cmd needs a shell on Windows
    )


def wait_for_health(port: int, timeout_s: float = 180.0) -> bool:
    """Poll /api/v1/health until 200 or timeout. Returns whether it came up."""
    url = f"http://127.0.0.1:{port}/api/v1/health"
    deadline = time.monotonic() + timeout_s
    announced = False
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            if not announced:
                _say(f"[dev] waiting for backend at {url} ...")
                announced = True
        time.sleep(1.0)
    return False


def terminate(proc: subprocess.Popen | None) -> None:
    """Kill a child and (on Windows) its whole process tree."""
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        # uvicorn spawns workers; /T takes the tree, /F forces it.
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=8088, help="backend port (default 8088)")
    p.add_argument(
        "--web-port", type=int, default=3000, help="web app port (default 3000)"
    )
    p.add_argument("--backend-only", action="store_true", help="start only the API")
    p.add_argument("--web-only", action="store_true", help="start only the web app")
    p.add_argument(
        "--reload",
        action="store_true",
        help=(
            "restart the backend when its source files change. Off by default: "
            "the reloader rescans this repo's 50k+ files per trigger, which adds "
            "a 6-10s lag per request. Useful while iterating on backend code"
        ),
    )
    p.add_argument(
        "--prod",
        action="store_true",
        help=(
            "serve the web app from a production build instead of `next dev`. "
            "Builds once when stale, then starts fast and stays snappy — "
            "recommended for using the app rather than editing it"
        ),
    )
    p.add_argument(
        "--rebuild",
        action="store_true",
        help="with --prod, force a rebuild even if the existing build looks fresh",
    )
    p.add_argument(
        "--health-timeout",
        type=float,
        default=180.0,
        help="seconds to wait for backend readiness (default 180)",
    )
    return p.parse_args()


def _say(msg: str, *, err: bool = False) -> None:
    """Print + flush.

    When dev.py runs with stdout redirected (CI, `> log`), Python block-buffers
    it, so progress lines would not appear until exit — making a working startup
    look like a hang.
    """
    print(msg, file=sys.stderr if err else sys.stdout, flush=True)


def main() -> int:
    args = _parse_args()
    if args.backend_only and args.web_only:
        _say("[dev] --backend-only and --web-only are mutually exclusive", err=True)
        return 2
    if args.reload and args.web_only:
        _say(
            "[dev] --reload only affects the backend; --web-only starts none", err=True
        )
        return 2

    env = build_env()
    web_env = dict(env)
    web_env["LOCAL_API_BASE"] = f"http://127.0.0.1:{args.port}"

    backend: subprocess.Popen | None = None
    web: subprocess.Popen | None = None

    _say("=" * 48)
    _say("  SeleneQuant — dev launcher")
    _say("=" * 48)

    try:
        if not args.web_only:
            _say(f"[dev] backend  -> http://127.0.0.1:{args.port}")
            if args.reload:
                _say("[dev] reload enabled — backend restarts on source change")
            backend = start_backend(env, args.port, reload=args.reload)

        if not args.backend_only:
            _say(f"[dev] web app  -> http://localhost:{args.web_port}")
            if not (WEB_DIR / "node_modules").exists():
                _say(
                    "[dev] apps/desktop/node_modules missing — run "
                    "`npm install` in apps/desktop first",
                    err=True,
                )
            if args.prod:
                if args.rebuild or _web_needs_build():
                    if build_web(web_env) != 0:
                        _say("[dev] web build failed — see the output above", err=True)
                        return 1
                else:
                    _say("[dev] web build is up to date; skipping rebuild")
            web = start_web(web_env, args.web_port, prod=args.prod)

        if backend is not None:
            if wait_for_health(args.port, args.health_timeout):
                _say(f"[dev] backend ready: http://127.0.0.1:{args.port}/docs")
            else:
                _say(
                    f"[dev] backend not ready after {args.health_timeout:.0f}s "
                    "— still starting in the background. Cold start on first run is slow.",
                    err=True,
                )

        if not args.backend_only:
            if args.prod:
                _say(
                    f"\n[dev] open http://localhost:{args.web_port} "
                    "(production build — pages load without per-page compile)\n"
                )
            else:
                _say(
                    f"\n[dev] open http://localhost:{args.web_port} — use `localhost`, "
                    "NOT 127.0.0.1 (Next.js dev origin protection rejects the bare IP)"
                    "\n[dev] dev mode compiles each page on first visit; for a snappier "
                    "run use `python dev.py --prod`\n"
                )

        _say("[dev] Ctrl-C to stop.\n")
        # Park until interrupted, or until every child has died.
        #
        # A dead backend must NOT take the web app down with it. The frontend
        # is what shows the diagnostics ("Connecting to runtime", boot log,
        # Reconnect button) — killing it along with the backend leaves the user
        # with a blank terminal and no way to see why. So each side is reported
        # as it exits and the loop keeps running as long as one is alive.
        while True:
            time.sleep(1.0)
            if backend is not None and backend.poll() is not None:
                _say(
                    f"[dev] backend exited (code {backend.returncode}). "
                    "The web app is still up — its start screen will show the "
                    "connection error and keep retrying.",
                    err=True,
                )
                backend = None
            if web is not None and web.poll() is not None:
                _say(
                    f"[dev] web app exited (code {web.returncode}); stopping.",
                    err=True,
                )
                return web.returncode or 0
            if backend is None and web is None:
                return 0

    except KeyboardInterrupt:
        _say("\n[dev] stopping...")
        return 0

    finally:
        terminate(web)
        terminate(backend)


if __name__ == "__main__":
    sys.exit(main())
