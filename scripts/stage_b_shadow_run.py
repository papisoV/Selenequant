"""Stage B 24h Shadow Run orchestrator.

Launches a uvicorn subprocess in shadow execution mode, deploys the bb-v3-stage-b
strategy (1m interval, short-period params), then polls the order journal SQLite
every 60s and writes observations to data/stage_b/observations.jsonl. Auto-restarts
uvicorn on crash. At T+duration, invokes scripts/stage_b_verdict.py.

Usage:
    # 5min smoke test
    python scripts/stage_b_shadow_run.py --smoke

    # Full 24h run
    python scripts/stage_b_shadow_run.py --duration=86400 --strategy-id=bb-v3-stage-b

    # Resume from existing observations.jsonl
    python scripts/stage_b_shadow_run.py --duration=86400 --resume
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_API_DIR = PROJECT_ROOT / "services" / "local-api"
STAGE_B_DIR = PROJECT_ROOT / "data" / "stage_b"
LOGS_DIR = PROJECT_ROOT / "logs"
JOURNAL_DIR = Path.home() / ".selenespace" / "journal"
JOURNAL_DB = JOURNAL_DIR / "order_journal.db"
OBSERVATIONS_FILE = STAGE_B_DIR / "observations.jsonl"
RESTARTS_FILE = STAGE_B_DIR / "restarts.jsonl"
BACKEND_LOG = LOGS_DIR / "stage_b_backend.log"

UVICORN_HOST = "127.0.0.1"
UVICORN_PORT = 8001
BASE_URL = f"http://{UVICORN_HOST}:{UVICORN_PORT}"
HEALTH_PATH = "/api/v1/health"
DEPLOY_PATH_TEMPLATE = "/api/v1/strategies/{sid}/deploy-paper"
FACTORY_CREATE_PATH = "/api/v1/strategies/factory-templates/{template_id}/create"
STRATEGY_UPDATE_PATH = "/api/v1/strategies/{sid}"
HEALTH_TIMEOUT_S = 600  # 2026-07-28: 300s 不够 — 16 add_symbol × 18-20s (含 WS stall + L2 recovery + reconnect 雪崩) = 320s+。放到 600s 给 proxy 环境留余量。
POLL_INTERVAL_S = 60

DEFAULT_STRATEGY_ID = "bb_v3"  # factory template id; script auto-creates a strategy instance from this template
# NOTE: deploy-paper / deploy-live bodies only carry account_id — the deployed
# config comes from the stored strategy. To actually run 1m + short-period bb_v3
# params, we must PUT /strategies/{id} (partial update) to override
# interval + parameters AFTER factory-create but BEFORE deploy.
DEFAULT_DEPLOY_BODY = {
    "symbol": "BTC/USDT",
    "interval": "1m",
    "bb_len": 5,
    "buy_len": 10,
    "buy_mult": 2.0,
    "sell_len": 10,
    "sell_mult": 2.0,
}

# Parameters forwarded to the BBV3Strategy constructor (bb_v3 template kwargs)
_DEPLOY_PARAM_KEYS = ("bb_len", "buy_len", "buy_mult", "sell_len", "sell_mult")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage B 24h shadow run orchestrator")
    parser.add_argument(
        "--duration",
        type=int,
        default=86400,
        help="Run duration in seconds (default 86400 = 24h)",
    )
    parser.add_argument(
        "--strategy-id",
        type=str,
        default=DEFAULT_STRATEGY_ID,
        help=f"Strategy ID to deploy (default {DEFAULT_STRATEGY_ID})",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="5min smoke test mode (overrides --duration to 300)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing observations.jsonl (append mode)",
    )
    args = parser.parse_args()
    if args.smoke:
        args.duration = 300
    return args


def ensure_dirs() -> None:
    STAGE_B_DIR.mkdir(parents=True, exist_ok=True)
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def start_uvicorn() -> subprocess.Popen[bytes]:
    # SELENE_KLINE_INTERVALS must include 1m — otherwise the kline sync/subscribe
    # chain is built for 30m/1h only and a 1m strategy deployment warns
    # "Signals will NOT fire" (strategy_runner._ensure_symbol_subscribed).
    env = {
        **os.environ,
        "SELENE_EXECUTION_MODE": "shadow",
        "SELENE_KLINE_INTERVALS": "1m,30m,1h",
    }
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        )
    log_fp = open(BACKEND_LOG, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(UVICORN_PORT)],
        cwd=str(LOCAL_API_DIR),
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    print(f"[uvicorn] started pid={proc.pid} mode=shadow port={UVICORN_PORT}")
    return proc


def wait_health(proc: subprocess.Popen[bytes], timeout_s: int = HEALTH_TIMEOUT_S) -> bool:
    deadline = time.time() + timeout_s
    url = f"{BASE_URL}{HEALTH_PATH}"
    while time.time() < deadline:
        if proc.poll() is not None:
            print(f"[uvicorn] process exited early with code={proc.returncode}")
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    print(f"[health] {url} -> 200 OK")
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(1)
    print(f"[health] timeout after {timeout_s}s waiting for {url}")
    return False


def create_strategy_from_template(template_id: str, max_attempts: int = 4, retry_delay: int = 30) -> str | None:
    """POST /api/v1/strategies/factory-templates/{template_id}/create.

    Returns the newly created strategy_id (e.g. 'strat-abc12345') on success,
    or None on failure. Retries up to max_attempts times with retry_delay between
    attempts — the uvicorn lifespan can take ~155s to complete (16 add_symbol
    subscribes + fetcher retries), during which factory create blocks and may
    time out at the urllib 30s default.
    """
    url = f"{BASE_URL}{FACTORY_CREATE_PATH.format(template_id=template_id)}"
    last_exc: str = ""
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(url, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                status = resp.status
                payload = resp.read().decode("utf-8", errors="replace")
                print(f"[factory] POST {url} -> {status} (attempt {attempt}/{max_attempts})")
                if status == 200:
                    data = json.loads(payload).get("data", {})
                    sid = data.get("id")
                    print(f"[factory] created strategy_id={sid}")
                    return sid
                print(f"[factory] non-200 body: {payload[:500]}")
                last_exc = f"HTTP {status}"
        except Exception as exc:
            last_exc = f"{type(exc).__name__}: {exc}"
            print(f"[factory] attempt {attempt}/{max_attempts} FAIL: {last_exc}")
        if attempt < max_attempts:
            print(f"[factory] retrying in {retry_delay}s...")
            time.sleep(retry_delay)
    print(f"[factory] exhausted {max_attempts} attempts. Last error: {last_exc}")
    return None


def update_strategy_config(strategy_id: str, max_attempts: int = 2, retry_delay: int = 5) -> bool:
    """PUT /strategies/{sid} — override interval + parameters on a factory-created strategy.

    deploy-paper deploys the STORED strategy config (interval/params from template
    defaults), so the DEFAULT_DEPLOY_BODY values are applied here via partial update
    before deployment. Returns True on 200.
    """
    url = f"{BASE_URL}{STRATEGY_UPDATE_PATH.format(sid=strategy_id)}"
    body = {
        "interval": DEFAULT_DEPLOY_BODY.get("interval", "1m"),
        "parameters": {k: v for k, v in DEFAULT_DEPLOY_BODY.items() if k in _DEPLOY_PARAM_KEYS},
        "version_note": "stage_b shadow run config override",
    }
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="PUT",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = resp.status
                print(
                    f"[config] PUT {url} -> {status} (attempt {attempt}/{max_attempts}) "
                    f"interval={body['interval']} params={body['parameters']}"
                )
                if status == 200:
                    return True
                payload = resp.read().decode("utf-8", errors="replace")
                print(f"[config] non-200 body: {payload[:400]}")
        except Exception as exc:
            print(f"[config] attempt {attempt}/{max_attempts} FAIL: {type(exc).__name__}: {exc}")
        if attempt < max_attempts:
            time.sleep(retry_delay)
    return False


def deploy_strategy(strategy_id: str, max_attempts: int = 4, retry_delay: int = 30) -> bool:
    """POST /api/v1/strategies/{sid}/deploy-paper. Retries on timeout."""
    url = f"{BASE_URL}{DEPLOY_PATH_TEMPLATE.format(sid=strategy_id)}"
    body = json.dumps(DEFAULT_DEPLOY_BODY).encode("utf-8")
    last_exc: str = ""
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                status = resp.status
                payload = resp.read().decode("utf-8", errors="replace")
                print(f"[deploy] POST {url} -> {status} (attempt {attempt}/{max_attempts})")
                if status == 200:
                    print(f"[deploy] response: {payload[:300]}")
                    return True
                print(f"[deploy] non-200 body: {payload[:500]}")
                last_exc = f"HTTP {status}"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                print(f"[deploy] strategy '{strategy_id}' not found (404)")
                return False  # don't retry 404 — caller should auto-create
            last_exc = f"HTTPError {exc.code}: {exc.reason}"
            print(f"[deploy] attempt {attempt}/{max_attempts} FAIL: {last_exc}")
        except Exception as exc:
            last_exc = f"{type(exc).__name__}: {exc}"
            print(f"[deploy] attempt {attempt}/{max_attempts} FAIL: {last_exc}")
        if attempt < max_attempts:
            print(f"[deploy] retrying in {retry_delay}s...")
            time.sleep(retry_delay)
    print(f"[deploy] exhausted {max_attempts} attempts. Last error: {last_exc}")
    return False


def deploy_strategy_with_auto_create(strategy_id_or_template: str) -> bool:
    """Try deploy; on 404, create strategy from factory template then deploy.

    Accepts either an existing strategy_id (e.g. 'strat-abc12345') or a
    factory template_id (e.g. 'bb_v3'). If deploy returns 404, treats the
    input as a template_id, creates a strategy, and deploys the new id.
    """
    if deploy_strategy(strategy_id_or_template):
        return True
    # 404 — treat input as template_id and create
    print(f"[stage_b] '{strategy_id_or_template}' not deployed; creating from template...")
    new_sid = create_strategy_from_template(strategy_id_or_template)
    if new_sid is None:
        return False
    # factory create freezes template defaults (interval=1h, buy_len=44 → 46h
    # warmup, no signals in a 24h window). Override to DEFAULT_DEPLOY_BODY config.
    if not update_strategy_config(new_sid):
        print(f"[stage_b] WARN: config override failed for {new_sid}; deploying template defaults")
    return deploy_strategy(new_sid)


def _connect_journal() -> sqlite3.Connection | None:
    if not JOURNAL_DB.exists():
        return None
    try:
        conn = sqlite3.connect(str(JOURNAL_DB), timeout=5)
        return conn
    except sqlite3.Error as exc:
        print(f"[journal] connect fail: {exc}")
        return None


def query_journal_status_counts() -> dict[str, int]:
    conn = _connect_journal()
    if conn is None:
        return {}
    try:
        cur = conn.execute("SELECT status, COUNT(*) FROM order_journal GROUP BY status")
        rows = cur.fetchall()
        return {str(status): int(count) for status, count in rows}
    except sqlite3.Error as exc:
        print(f"[journal] status query fail: {exc}")
        return {}
    finally:
        conn.close()


def query_signal_count() -> int:
    """Count shadow-origin orders by client_order_id prefix 'sso-'."""
    conn = _connect_journal()
    if conn is None:
        return 0
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM order_journal WHERE client_order_id LIKE 'sso-%'"
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error as exc:
        print(f"[journal] signal count query fail: {exc}")
        return 0
    finally:
        conn.close()


def query_fill_count() -> int:
    conn = _connect_journal()
    if conn is None:
        return 0
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM order_journal WHERE status='confirmed'"
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error as exc:
        print(f"[journal] fill count query fail: {exc}")
        return 0
    finally:
        conn.close()


LIVE_STATE_DB = Path.home() / ".selenespace" / "live" / "live_state.db"


def query_positions_count() -> int:
    """Count rows in live_positions table (shadow engine state)."""
    if not LIVE_STATE_DB.exists():
        return 0
    conn = sqlite3.connect(f"file:{LIVE_STATE_DB}?mode=ro", uri=True, timeout=5)
    try:
        cur = conn.execute("SELECT COUNT(*) FROM live_positions")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error as exc:
        print(f"[live_state] positions count query fail: {exc}")
        return 0
    finally:
        conn.close()


def query_latest_equity_snapshot() -> dict[str, float | None]:
    """Read latest shadow-vs-paper equity comparison from live_state.db.

    ShadowExecutionEngine writes one row per fill; we surface the most recent
    so each observation carries the current equity deviation (Stage B std 5).
    """
    empty = {"shadow_equity": None, "paper_mirror_equity": None, "equity_deviation_pct": None}
    if not LIVE_STATE_DB.exists():
        return empty
    conn = sqlite3.connect(f"file:{LIVE_STATE_DB}?mode=ro", uri=True, timeout=5)
    try:
        cur = conn.execute(
            "SELECT shadow_equity, paper_mirror_equity, deviation_pct "
            "FROM equity_snapshots ORDER BY timestamp DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return empty
        return {
            "shadow_equity": float(row[0]),
            "paper_mirror_equity": float(row[1]),
            "equity_deviation_pct": float(row[2]) if row[2] is not None else None,
        }
    except sqlite3.Error as exc:
        print(f"[live_state] equity snapshot query fail: {exc}")
        return empty
    finally:
        conn.close()


def read_restart_count() -> int:
    if not RESTARTS_FILE.exists():
        return 0
    count = 0
    try:
        with open(RESTARTS_FILE, "r", encoding="utf-8") as fh:
            for _ in fh:
                count += 1
    except OSError:
        pass
    return count


def tail_backend_log(n_lines: int = 50) -> str:
    if not BACKEND_LOG.exists():
        return ""
    try:
        with open(BACKEND_LOG, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return "".join(lines[-n_lines:])
    except OSError:
        return ""


def log_restart(reason: str) -> int:
    entry = {
        "timestamp": int(time.time() * 1000),
        "reason": reason,
        "stderr_tail": tail_backend_log(50),
    }
    try:
        with open(RESTARTS_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[restart] fail to log: {exc}")
    return read_restart_count()


def auto_restart_uvicorn(proc: subprocess.Popen[bytes]) -> subprocess.Popen[bytes]:
    if proc.poll() is None:
        return proc
    rc = proc.returncode
    restart_count = log_restart(f"uvicorn exited code={rc}")
    print(f"[auto-restart] uvicorn died (code={rc}), restart_count={restart_count}")
    new_proc = start_uvicorn()
    if not wait_health(new_proc):
        print("[auto-restart] new uvicorn failed health check; will retry next loop")
    return new_proc


def append_observation(observation: dict[str, Any]) -> None:
    try:
        with open(OBSERVATIONS_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(observation, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[observe] fail to append: {exc}")


def make_observation(uvicorn_alive: bool) -> dict[str, Any]:
    equity = query_latest_equity_snapshot()
    return {
        "ts": int(time.time() * 1000),
        "journal_status_counts": query_journal_status_counts(),
        "signal_count": query_signal_count(),
        "fill_count": query_fill_count(),
        "uvicorn_alive": uvicorn_alive,
        "restart_count": read_restart_count(),
        "positions_count": query_positions_count(),
        "shadow_equity": equity["shadow_equity"],
        "paper_mirror_equity": equity["paper_mirror_equity"],
        "equity_deviation_pct": equity["equity_deviation_pct"],
    }


def read_last_observation_ts() -> int | None:
    if not OBSERVATIONS_FILE.exists():
        return None
    try:
        with open(OBSERVATIONS_FILE, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    if not lines:
        return None
    try:
        return int(json.loads(lines[-1]).get("timestamp", 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def observation_loop(
    proc: subprocess.Popen[bytes],
    end_ts: float,
    smoke: bool,
) -> subprocess.Popen[bytes]:
    next_poll = time.time()
    while time.time() < end_ts:
        if proc.poll() is not None:
            proc = auto_restart_uvicorn(proc)
        now = time.time()
        if now >= next_poll:
            obs = make_observation(proc.poll() is None)
            append_observation(obs)
            print(
                f"[observe] ts={obs['ts']} "
                f"alive={obs['uvicorn_alive']} "
                f"status={obs['journal_status_counts']} "
                f"signal={obs['signal_count']} fill={obs['fill_count']} "
                f"positions={obs['positions_count']} "
                f"restarts={obs['restart_count']}"
            )
            next_poll = now + POLL_INTERVAL_S
        time.sleep(1)
    return proc


def stop_uvicorn(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    print(f"[uvicorn] stopping pid={proc.pid}")
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            proc.terminate()
    except Exception:
        # os.kill(CTRL_BREAK_EVENT) to a CREATE_NEW_PROCESS_GROUP child on
        # Windows can raise SystemError (WinError 6 handle invalid), not just
        # OSError — treat any failure as "graceful stop unavailable" and fall
        # through to the hard-kill path below.
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        print("[uvicorn] graceful stop timeout, killing")
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def run_verdict() -> int:
    verdict_script = PROJECT_ROOT / "scripts" / "stage_b_verdict.py"
    if not verdict_script.exists():
        print(f"[verdict] script not found: {verdict_script}")
        return 1
    print(f"[verdict] running {verdict_script}")
    try:
        result = subprocess.run(
            [sys.executable, str(verdict_script)],
            cwd=str(PROJECT_ROOT),
            check=False,
        )
        return result.returncode
    except OSError as exc:
        print(f"[verdict] fail to run: {exc}")
        return 1


def main() -> int:
    args = parse_args()
    duration = args.duration
    smoke = args.smoke

    ensure_dirs()
    print(f"[stage_b] mode={'smoke' if smoke else 'full'} duration={duration}s")
    print(f"[stage_b] strategy_id={args.strategy_id}")
    print(f"[stage_b] observations -> {OBSERVATIONS_FILE}")
    print(f"[stage_b] restarts     -> {RESTARTS_FILE}")
    print(f"[stage_b] backend log  -> {BACKEND_LOG}")

    resume_ts = None
    if args.resume:
        resume_ts = read_last_observation_ts()
        if resume_ts is not None:
            print(f"[stage_b] resume: last observation ts={resume_ts}")
        else:
            print("[stage_b] resume requested but no prior observations; fresh start")
    else:
        # Fresh start: clear prior observations/restarts so verdict.py
        # counts only signals/fills from this run, not prior runs.
        if OBSERVATIONS_FILE.exists():
            OBSERVATIONS_FILE.unlink()
        if RESTARTS_FILE.exists():
            RESTARTS_FILE.unlink()
        print(f"[stage_b] fresh start: cleared {OBSERVATIONS_FILE.name} + {RESTARTS_FILE.name}")

    proc = start_uvicorn()
    if not wait_health(proc):
        print("[stage_b] FAIL: uvicorn health check failed")
        stop_uvicorn(proc)
        return 2

    if not deploy_strategy_with_auto_create(args.strategy_id):
        print("[stage_b] FAIL: deploy_strategy returned non-200")
        if not smoke:
            stop_uvicorn(proc)
            return 3
        print("[stage_b] smoke mode: continuing despite deploy failure")

    end_ts = time.time() + duration

    def _signal_handler(signum: int, frame: Any) -> None:
        print(f"\n[stage_b] received signal {signum}, stopping...")
        stop_uvicorn(proc)
        sys.exit(130)

    signal.signal(signal.SIGINT, _signal_handler)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _signal_handler)

    print(f"[stage_b] observation loop starts now, ends at {time.ctime(end_ts)}")
    proc = observation_loop(proc, end_ts, smoke=smoke)

    print("[stage_b] duration elapsed, stopping uvicorn")
    try:
        stop_uvicorn(proc)
    except Exception as exc:
        print(f"[stage_b] stop_uvicorn failed (non-fatal): {exc}")

    if smoke:
        print("[stage_b] smoke mode: skipping verdict")
        return 0

    return run_verdict()


if __name__ == "__main__":
    sys.exit(main())
