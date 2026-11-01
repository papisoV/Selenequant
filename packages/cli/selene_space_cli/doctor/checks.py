"""Environment sanity checks backing `selenespace doctor`.

Contract: every ``check_*`` callable returns a :class:`CheckResult` and
never raises — failures are data, not exceptions. Registration happens at
decoration time into an ordered registry, so report order equals definition
order below; ``run_all_checks`` simply maps over ``ALL_CHECKS``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata as pkg_meta
from pathlib import Path

import httpx
import yaml

from selene_space_cli.constants import SCHEMA_VERSION
from selene_space_core.config import load_config as _read_config
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS as _MIGRATION_LEDGER


@dataclass(frozen=True)
class CheckResult:
    """One doctor probe outcome; ``repairable`` marks auto-fixable fails.

    ``status`` is one of pass / warn / fail / skip. The runner maps each to
    a colored marker; the summary line counts them by status.
    """

    name: str
    status: str
    message: str
    repairable: bool = False


def _pass(name: str, good: str) -> CheckResult:
    return CheckResult(name, "pass", good)


def _skip(name: str, why: str) -> CheckResult:
    return CheckResult(name, "skip", why)


def _warn(name: str, what: str, fixable: bool = False) -> CheckResult:
    return CheckResult(name, "warn", what, repairable=fixable)


def _fail(name: str, why: str, fixable: bool = False) -> CheckResult:
    return CheckResult(name, "fail", why, repairable=fixable)


def _verdict(
    name: str, healthy: bool, good: str, bad: str, fixable: bool = False
) -> CheckResult:
    """Collapse a boolean probe into a pass/fail result in one call."""
    if healthy:
        return _pass(name, good)
    return _fail(name, bad, fixable)


APP_DIR = Path.home() / ".selenespace"
MIN_FREE_MB = 100
_EXPECTED_MIGRATIONS = frozenset(m.id for m in _MIGRATION_LEDGER)
_CORE_DISTRIBUTIONS = (
    ("selene-space-core", "0.1"),
    ("selene-space-types", "0.1"),
    ("selene-space-risk", "0.1"),
    ("pandas", "2"),
)
_BINANCE_ENV_KEYS = ("BINANCE_API_KEY", "SELENE_BINANCE_API_KEY")
_LLM_ENV_KEYS = ("OPENAI_API_KEY", "SELENE_LLM_API_KEY")


def _config_file() -> Path:
    return APP_DIR / "config.yaml"


def _absolute(raw: str | Path) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else candidate.resolve()


def _credential_ready(env_keys: tuple[str, ...], secret_rel: tuple[str, ...]) -> bool:
    if any(os.environ.get(k) for k in env_keys):
        return True
    return APP_DIR.joinpath(*secret_rel).is_file()


def _disk_free_mb(target: Path) -> int:
    import shutil

    return shutil.disk_usage(target).free // (1024 * 1024)


class _SqliteHandle:
    """Minimal sync wrapper so checks never touch the async storage stack."""

    def __init__(self, db_file: Path) -> None:
        import sqlite3

        self._con = sqlite3.connect(str(db_file))

    def table_exists(self, table: str) -> bool:
        row = self._con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None

    def scalar_set(self, sql: str) -> set:
        return {row[0] for row in self._con.execute(sql)}

    def rows(self, sql: str) -> list[tuple]:
        return self._con.execute(sql).fetchall()

    def probe(self) -> None:
        self._con.execute("SELECT 1")

    def close(self) -> None:
        self._con.close()


class _SocketProbe:
    """One-shot connect/close reachability check with an OSError trap."""

    def __init__(self, host: str, port: int, timeout_s: float = 4.0) -> None:
        import socket

        self.open_ = False
        self.detail = ""
        try:
            sock = socket.create_connection((host, port), timeout=timeout_s)
            sock.close()
            self.open_ = True
        except OSError as failure:
            self.detail = str(failure)


# ------------------------------------------------------------------ checks --

# Registration: each check appends itself to the ordered registry at
# decoration time, so report order equals definition order below and a new
# check is added by decorating — no central list to maintain.

_ORDERED: list[Callable[[], "CheckResult"]] = []


def _registered(fn):
    _ORDERED.append(fn)
    return fn


@_registered
def check_python_version() -> CheckResult:
    """Reject interpreters older than the floor the platform supports."""
    label = "Python version"
    vi = sys.version_info
    text = f"{vi.major}.{vi.minor}.{vi.micro}"
    return _verdict(label, vi >= (3, 11), text, f"{text} (need >= 3.11)")


@_registered
def check_config_yaml_format() -> CheckResult:
    """Parse config.yaml strictly; a mapping at top level is required."""
    label = "Config file format"
    path = _config_file()
    if not path.is_file():
        return _warn(label, "No config file yet", True)
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as problem:
        return _fail(label, f"Invalid YAML: {problem}")
    mapping_ok = parsed is None or isinstance(parsed, dict)
    return _verdict(label, mapping_ok, "Valid", "Top level must be a mapping")


@_registered
def check_config_schema_version() -> CheckResult:
    """Compare the schema sidecar against the compiled-in version."""
    label = "Config schema version"
    sidecar = APP_DIR / "schema_version"
    fresh_install = not _config_file().is_file() and not sidecar.is_file()
    if fresh_install:
        return _skip(label, "No config file")
    if not sidecar.is_file():
        return _warn(label, "schema_version sidecar missing", True)
    recorded = sidecar.read_text(encoding="utf-8").strip()
    if recorded == SCHEMA_VERSION:
        return _pass(label, f"Compatible ({SCHEMA_VERSION})")
    return _warn(label, f"Sidecar {recorded!r} may not match {SCHEMA_VERSION}", True)


class _RestOutcome:
    """Result of a plain HTTP GET probe (transport errors are distinct)."""

    __slots__ = ("ok", "detail", "transport_error")

    def __init__(self, ok: bool, detail: str = "", transport_error: bool = False) -> None:
        self.ok = ok
        self.detail = detail
        self.transport_error = transport_error


def _ping_rest(url: str) -> _RestOutcome:
    client = httpx.Client(timeout=5.0)
    try:
        code = client.get(url).status_code
    except httpx.HTTPError as problem:
        client.close()
        return _RestOutcome(False, f"Network error: {problem}", True)
    client.close()
    if code == 200:
        return _RestOutcome(True)
    return _RestOutcome(False, f"Unexpected HTTP {code}")


@_registered
def check_exchange_rest() -> CheckResult:
    """Ping the spot REST endpoint when credentials allow network calls."""
    label = "Exchange connectivity (REST)"
    if not _credential_ready(_BINANCE_ENV_KEYS, ("secrets", "binance", "api_key")):
        return _skip(label, "Skipped (no API key)")
    outcome = _ping_rest("https://api.binance.com/api/v3/ping")
    if outcome.ok:
        return _pass(label, "Binance reachable")
    severity = "warn" if outcome.transport_error else "fail"
    return CheckResult(label, severity, outcome.detail)


@_registered
def check_exchange_key_exists() -> CheckResult:
    label = "Exchange API key"
    ready = _credential_ready(_BINANCE_ENV_KEYS, ("secrets", "binance", "api_key"))
    return _verdict(label, ready, "Present", "Not configured")


@_registered
def check_exchange_ws_connect() -> CheckResult:
    """TCP-dial the market stream endpoint once."""
    label = "Exchange WS (connect)"
    probe = _SocketProbe("stream.binance.com", 9443)
    note = "stream.binance.com:9443 reachable" if probe.open_ else probe.detail
    return CheckResult(label, "pass" if probe.open_ else "warn", note)


@_registered
def check_exchange_ws_heartbeat() -> CheckResult:
    """Second dial confirming the stream host stays responsive."""
    label = "Exchange WS (heartbeat)"
    probe = _SocketProbe("stream.binance.com", 9443)
    note = "Endpoint responsive" if probe.open_ else probe.detail
    return CheckResult(label, "pass" if probe.open_ else "warn", note)


def _is_local_llm(base_url: str) -> bool:
    return "ollama" in base_url.lower() or "11434" in base_url


@_registered
def check_llm_key_exists() -> CheckResult:
    """Cloud keys satisfy this; a local Ollama base URL counts too."""
    label = "LLM API key"
    if _credential_ready(_LLM_ENV_KEYS, ("secrets", "openai", "api_key")):
        return _pass(label, "Present")
    base_url = (_read_config().llm_base_url or "").strip()
    if base_url and _is_local_llm(base_url):
        return _pass(label, "Ollama (local) — no cloud key required")
    return _fail(label, "Not configured")


@_registered
def check_llm_works() -> CheckResult:
    """Probe the local LLM endpoint; cloud keys are considered configured."""
    label = "LLM provider"
    base_url = (_read_config().llm_base_url or "").strip()
    cloud_ready = _credential_ready(_LLM_ENV_KEYS, ("secrets", "openai", "api_key"))
    if not base_url and not cloud_ready:
        return _skip(label, "Skipped (not configured)")
    if not base_url or not _is_local_llm(base_url):
        return _skip(label, "Skipped (optional cloud probe)")

    def _ask_ollama() -> bool | str:
        client = httpx.Client(timeout=3.0)
        try:
            answered = client.get(_ollama_tags_endpoint(base_url)).status_code == 200
        except httpx.HTTPError as problem:
            return str(problem)
        finally:
            client.close()
        return answered

    outcome = _ask_ollama()
    if outcome is True:
        return _pass(label, "Ollama responded")
    if outcome is False:
        return _warn(label, "Ollama endpoint did not answer")
    return _warn(label, str(outcome))


def _ollama_tags_endpoint(base_url: str) -> str:
    if "/v1" in base_url:
        return base_url.split("/v1")[0] + "/api/tags"
    return "http://127.0.0.1:11434/api/tags"


def _db_location() -> Path:
    return _absolute(_read_config().db_path)


@_registered
def check_sqlite_exists() -> CheckResult:
    """Open the configured database and issue a trivial statement."""
    label = "SQLite database"
    db_file = _db_location()
    if not db_file.is_file():
        return _fail(label, f"Missing: {db_file}", True)
    try:
        handle = _SqliteHandle(db_file)
        handle.probe()
        handle.close()
    except Exception as problem:  # sqlite3.Error surfaces as generic here
        return _fail(label, str(problem))
    return _pass(label, "Opens OK")


@_registered
def check_sqlite_migrations() -> CheckResult:
    """Diff applied migration ids against the packaged ledger."""
    label = "SQLite schema version"
    db_file = _db_location()
    if not db_file.is_file():
        return _skip(label, "No database file")
    try:
        handle = _SqliteHandle(db_file)
        ledger_present = handle.table_exists("_migrations")
        applied = handle.scalar_set("SELECT id FROM _migrations") if ledger_present else set()
        handle.close()
    except Exception as problem:
        return _fail(label, str(problem))
    if not ledger_present:
        return _warn(label, "Migrations table missing", True)
    pending = _EXPECTED_MIGRATIONS.difference(applied)
    if pending:
        return _warn(label, f"Pending migrations: {', '.join(sorted(pending))}", True)
    return _pass(label, "Matches expected migrations")


@_registered
def check_strategy_migration() -> CheckResult:
    """Validate that stored strategy rows carry the required fields."""
    label = "Strategy configs migration"
    db_file = _db_location()
    if not db_file.is_file():
        return _skip(label, "No database")
    try:
        handle = _SqliteHandle(db_file)
        rows: list[tuple] = []
        if handle.table_exists("strategies"):
            rows = handle.rows("SELECT id, config_json FROM strategies")
        handle.close()
    except Exception as problem:
        return _warn(label, f"Could not read strategies: {problem}")
    table_present = _SqliteHandle(db_file).table_exists("strategies")
    if not rows and not table_present:
        return _skip(label, "No strategies table")

    mandatory = {"id", "name", "type"}

    def parses(payload_text: str) -> bool:
        try:
            payload = json.loads(payload_text)
        except ValueError:
            return False
        return isinstance(payload, dict) and mandatory.issubset(payload)

    stale_count = sum(1 for _, config_json in rows if not parses(config_json))
    if stale_count:
        return _warn(label, f"{stale_count} strategy row(s) may need migration")
    return _pass(label, "Stored strategies look valid")


@_registered
def check_paper_consistency() -> CheckResult:
    """Detect orders referencing accounts that no longer exist."""
    label = "Paper trading consistency"
    paper_dir = APP_DIR / "paper"
    ledger_file = paper_dir / "orders.json"
    if not ledger_file.is_file():
        return _pass(label, "No orders persisted")
    known_ids: set[str] = set()
    accounts_file = paper_dir / "accounts.json"
    if accounts_file.is_file():
        try:
            known_ids = set(json.loads(accounts_file.read_text(encoding="utf-8")))
        except (ValueError, OSError) as problem:
            return _warn(label, str(problem))
    try:
        ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
    except (ValueError, OSError) as problem:
        return _warn(label, str(problem))
    orphan_total = sum(
        len(bucket)
        for owner, bucket in (ledger.get("account_orders") or {}).items()
        if owner not in known_ids
    )
    if orphan_total:
        return _fail(label, f"Orphan orders detected ({orphan_total})")
    return _pass(label, "No orphan orders")


@_registered
def check_log_dir_writable() -> CheckResult:
    """Create the log directory and drop/remove a canary file."""
    label = "Log directory"

    def _write_canary(dir_path: Path) -> None:
        canary = dir_path / ".selenespace_write_test"
        canary.write_text("ok", encoding="utf-8")
        canary.unlink(missing_ok=True)

    target = _absolute(_read_config().log_dir)
    try:
        target.mkdir(parents=True, exist_ok=True)
        _write_canary(target)
    except OSError as problem:
        return _fail(label, str(problem), True)
    return _pass(label, "Writable")


@_registered
def check_disk_space() -> CheckResult:
    """Warn when the app root sits on a nearly-full volume."""
    label = "Disk space"
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        free_mb = _disk_free_mb(APP_DIR)
    except OSError as problem:
        return _warn(label, str(problem))
    scarce = free_mb < MIN_FREE_MB
    note = (
        f"Only {free_mb} MiB free (< {MIN_FREE_MB})" if scarce else f"{free_mb} MiB free"
    )
    return CheckResult(label, "warn" if scarce else "pass", note)


@_registered
def check_dependencies() -> CheckResult:
    """Version-pin the core distribution set at import time."""
    label = "Dependency versions"

    def _probe(dist_name: str, prefix: str) -> str | None:
        try:
            installed = pkg_meta.version(dist_name)
        except pkg_meta.PackageNotFoundError:
            return f"{dist_name} missing"
        if not installed.startswith(prefix):
            return f"{dist_name}=={installed}"
        return None

    problems = [
        finding
        for dist_name, prefix in _CORE_DISTRIBUTIONS
        if (finding := _probe(dist_name, prefix))
    ]
    if problems:
        return _warn(label, "; ".join(problems))
    return _pass(label, "Core packages OK")


@_registered
def check_selenespace_permissions() -> CheckResult:
    """Flag group/world-writable bits on POSIX installs."""
    label = ".selenespace permissions"
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        mode_bits = APP_DIR.stat().st_mode & 0o777
    except OSError as problem:
        return _fail(label, str(problem), True)
    loose_on_posix = sys.platform != "win32" and bool(mode_bits & 0o077)
    if loose_on_posix:
        return _warn(label, f"World/group writable ({mode_bits:o})", True)
    return _pass(label, "OK")


_KEYRING_SERVICE = "selenespace"
_KEYRING_PROBE_ID = "__doctor_probe__"


@_registered
def check_keychain() -> CheckResult:
    """Verify an OS secret backend responds to a harmless read probe."""
    label = "Secret storage (keychain)"
    if importlib.util.find_spec("keyring") is None:
        return _skip(label, "keyring package not installed")
    trouble = _read_keychain_slot()
    if trouble is not None:
        return _warn(label, trouble)
    return _pass(label, "Keyring backend available")


def _read_keychain_slot() -> str | None:
    """Return an error message when the keychain backend misbehaves."""
    import keyring  # noqa: PLC0415 — optional third-party dependency

    try:
        keyring.get_password(_KEYRING_SERVICE, _KEYRING_PROBE_ID)  # noqa: S105
    except Exception as problem:  # noqa: BLE001 — unknown backend variety
        return f"Keyring not usable: {problem}"
    return None


def _api_health_live(port_number: int) -> bool:
    endpoint = f"http://127.0.0.1:{port_number}/api/v1/health"
    client = httpx.Client(timeout=2.0)
    try:
        return client.get(endpoint).status_code == 200
    except httpx.HTTPError:
        return False
    finally:
        client.close()


class _PortClaim:
    """Try to bind a port; failure implies someone else holds it."""

    def __init__(self, port_number: int) -> None:
        import socket

        binder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            binder.bind(("127.0.0.1", port_number))
            self.claimed = False
        except OSError:
            self.claimed = True
        finally:
            binder.close()


@_registered
def check_api_port() -> CheckResult:
    """Prefer a live health answer; fall back to a bind probe for occupancy."""
    label = "Local API port"
    port_number = int(_read_config().api_port)
    if _api_health_live(port_number):
        return _pass(label, f"API healthy on :{port_number}")
    if _PortClaim(port_number).claimed:
        return _warn(label, f"Port {port_number} in use but API health check failed")
    return _pass(label, f"Port {port_number} available (API not running)")


ALL_CHECKS = _ORDERED  # tested alias for the ordered registry
