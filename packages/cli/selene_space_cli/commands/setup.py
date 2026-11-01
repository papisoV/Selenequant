"""Interactive first-run wizard (`selenespace setup`).

Walks six skippable stages: environment gate, exchange credentials (keyring),
LLM provider, on-disk layout + database bootstrap, an optional synthetic-data
demo backtest, and a closing summary. Every stage degrades gracefully so the
wizard never leaves a half-broken install behind.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import click
import pandas as pd

import selene_space_backtest.engine as _bt_engine
import selene_space_strategy.compiler as _st_compiler
import selene_space_strategy.models as _st_models
import selene_space_strategy.runtime as _st_runtime
from selene_space_cli import _defaults
from selene_space_cli.constants import SCHEMA_VERSION, cli_version
from selene_space_storage.sqlite import DEFAULT_DB_PATH


def _default_config_yaml() -> str:
    """Tested seam: canonical first-run config text (api_port pinned at 8088)."""
    return _defaults.compose_config_text()


def _write_binance_key(key: str) -> None:
    """Legacy plaintext fallback writer (deprecated C1 path).

    Only used when the OS keyring is unavailable; readers prefer keyring ids
    ``{env}_api_key`` from provider selenespace.exchange over this file.
    """
    target = _defaults.app_root() / "secrets" / "binance" / "api_key"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(key.strip(), encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass


def _store_binance_secrets(api_key: str, api_secret: str, passphrase: str = "", env: str = "mainnet") -> bool:
    """Persist Binance credentials to the OS keyring (encrypted, C1).

    Readers resolve SecretRef(provider="selenespace.exchange",
    id="{env}_api_key" / "{env}_api_secret" / "{env}_passphrase"). mainnet and
    testnet entries coexist under distinct ids. Returns False when keyring is
    unavailable so callers can fall back with a warning.
    """
    from selene_space_security.secrets import SecretManager, SecretRef, SecretSource

    manager = SecretManager()
    if not manager.keyring_available():
        return False
    provider = "selenespace.exchange"

    def _ref(value_id: str) -> SecretRef:
        return SecretRef(source=SecretSource.KEYRING, provider=provider, id=value_id)

    async def _write_all() -> None:
        for value_id, raw in (
            (f"{env}_api_key", api_key.strip()),
            (f"{env}_api_secret", api_secret.strip()),
            (f"{env}_passphrase", passphrase.strip()),
        ):
            if raw:
                await manager.store(_ref(value_id), raw)

    try:
        asyncio.run(_write_all())
    except Exception:
        return False
    return True


def _synthetic_ohlcv() -> pd.DataFrame:
    bar_count = 120
    t0 = pd.Timestamp("2024-01-01", tz="UTC")
    ramp = 40_000.0
    step = 10.0
    stamps_ms = [
        int((t0 + pd.Timedelta(hours=i)).timestamp() * 1000) for i in range(bar_count)
    ]
    levels = [ramp + i * step for i in range(bar_count)]
    body = pd.Series(levels)
    return pd.DataFrame(
        {
            "timestamp": stamps_ms,
            "open": body,
            "high": body * 1.001,
            "low": body * 0.999,
            "close": body,
            "volume": 1.0,
        }
    )


def _demo_strategy_document() -> dict:
    sma_block = lambda period: {"indicator": "sma", "params": {"period": period}}  # noqa: E731
    return {
        "id": "demo-sma",
        "name": "Demo SMA",
        "type": "sma_cross",
        "description": "Setup wizard demo",
        "symbols": ["BTC/USDT"],
        "interval": "1h",
        "parameters": {"sma_short": 10, "sma_long": 30},
        "entry_rules": {},
        "exit_rules": {},
        "risk_params": {},
        "parsed_entry_rules": {
            "long": [{**sma_block(10), "operator": "crosses_above", "comparator": sma_block(30)}],
            "short": [],
        },
        "parsed_exit_rules": {
            "close_long": [{**sma_block(10), "operator": "crosses_below", "comparator": sma_block(30)}],
            "close_short": [],
        },
        "parsed_risk_params": {},
    }


def _demo_backtest() -> None:
    """Run a tiny SMA backtest on synthetic bars (no fixture files needed)."""
    with tempfile.TemporaryDirectory() as scratch:
        yml = Path(scratch) / "sma_demo.yaml"
        import yaml

        yml.write_text(yaml.safe_dump(_demo_strategy_document()), encoding="utf-8")
        strat = _st_models.load_strategy(yml)
        runtime = _st_runtime.StrategyRuntime(_st_compiler.compile(strat))
        engine = _bt_engine.BacktestEngine(_bt_engine.BacktestConfig(strategy_id=strat.id))
        outcome = engine.run(strategy=runtime, data=_synthetic_ohlcv())
        stats = outcome.metrics
        report = ", ".join(
            (
                f"return={stats.total_return:.2%}",
                f"Sharpe={stats.sharpe_ratio:.2f}",
                f"MDD={stats.max_drawdown:.2%}",
                f"trades={outcome.trades_count}",
            )
        )
        click.echo(f"  Demo backtest finished: {report}")


def _stage_environment() -> None:
    click.echo("\n[1/6] Environment")
    vi = sys.version_info
    click.echo(f"  Python {vi.major}.{vi.minor}.{vi.micro}")
    if vi < (3, 11):  # noqa: UP036
        click.echo("  This wizard needs Python 3.11 or newer — aborting.")
        raise click.Abort()
    if not click.confirm("  Ready to configure SeleneQuant?", default=True):
        click.echo("Setup cancelled.")
        raise click.Abort()


_EXCHANGE_INTRO = [
    "[2/6] Exchange (Binance) — optional",
    "  C1: credentials stored in OS keyring (encrypted), NOT plaintext files.",
    "  Multi-env: mainnet and testnet keys stored under separate ids",
    "    (mainnet_api_key / testnet_api_key). Both can coexist; toggle via",
    "    SELENE_EXCHANGE_TESTNET env var at startup.",
]


def _prompt_credential_pair(env: str) -> None:
    label = env.capitalize()
    key = click.prompt(f"  {label} API key", hide_input=True)
    secret = click.prompt(f"  {label} API secret", hide_input=True)
    phrase = click.prompt(
        "  Passphrase (OKX only, blank for Binance)",
        default="",
        hide_input=True,
        show_default=False,
    )
    if not key.strip():
        return
    if not secret.strip():
        click.echo(f"  WARNING: {env} API key without secret is incomplete — skipping.")
        return
    _handle_credential_store(env, key, secret, phrase)


def _collect_exchange_credentials() -> None:
    for line in _EXCHANGE_INTRO:
        click.echo(line)
    for env in ("mainnet", "testnet"):
        wants = click.confirm(
            f"  Configure Binance {env} API key now?", default=False
        )
        if wants:
            _prompt_credential_pair(env)


def _handle_credential_store(env: str, key: str, secret: str, phrase: str) -> None:
    if _store_binance_secrets(key, secret, phrase, env=env):
        click.echo(f"  Saved {env} credentials to OS keyring (id={env}_api_key)")
        return
    click.echo(f"  WARNING: keyring unavailable — {env} credentials NOT saved.")
    click.echo("  Run `selenespace setup` on a machine with keyring.")


def _place_llm_key(key_text: str) -> None:
    session_only = click.confirm("  Store key in environment only (this session)?", default=False)
    if session_only:
        os.environ["OPENAI_API_KEY"] = key_text.strip()
        click.echo("  Set OPENAI_API_KEY for this process only.")
        return
    secret_file = _defaults.app_root() / "secrets" / "openai" / "api_key"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(key_text.strip(), encoding="utf-8")
    try:
        secret_file.chmod(0o600)
    except OSError:
        pass
    click.echo(f"  Wrote {secret_file}")


def _llm_patch_openai() -> dict[str, object]:
    base = click.prompt("  Base URL (e.g. https://api.openai.com/v1)", default="")
    model_name = click.prompt("  Model name", default="")
    key = click.prompt("  API key", default="", hide_input=True)
    patch: dict[str, object] = {
        "llm_provider": "openai_compatible",
        "llm_base_url": base,
        "llm_model": model_name,
    }
    if key.strip():
        _place_llm_key(key)
    return patch


def _llm_patch_ollama() -> dict[str, object]:
    base = click.prompt("  Ollama base URL", default="http://127.0.0.1:11434/v1")
    model_name = click.prompt("  Model name", default="llama3")
    return {
        "llm_provider": "openai_compatible",
        "llm_base_url": base,
        "llm_model": model_name,
    }


def _collect_llm_settings() -> dict[str, object]:
    click.echo("\n[3/6] LLM — optional")
    click.echo("  Provider: OpenAI-compatible / Ollama / Skip")
    choice = click.prompt(
        "  Pick [openai_compatible / ollama / skip]",
        default="skip",
        show_default=True,
    ).lower()
    builders = {
        "openai_compatible": _llm_patch_openai,
        "openai": _llm_patch_openai,
        "ollama": _llm_patch_ollama,
    }
    builder = builders.get(choice)
    return builder() if builder else {}


def _materialize_dirs_and_db(llm_patch: dict[str, object]) -> None:
    click.echo("\n[4/6] Data directory and database")
    root = _defaults.app_root()
    for leaf in ("data", "logs", "paper", "secrets"):
        (root / leaf).mkdir(parents=True, exist_ok=True)

    cfg_path = root / "config.yaml"
    sidecar = root / "schema_version"
    if not cfg_path.exists():
        text = _defaults.compose_config_text(llm_patch or None)
        cfg_path.write_text(text, encoding="utf-8")
        sidecar.write_text(SCHEMA_VERSION + "\n", encoding="utf-8")
        click.echo(f"  Wrote {cfg_path}")
    else:
        click.echo(f"  Config already exists: {cfg_path}")
        if not sidecar.is_file():
            sidecar.write_text(SCHEMA_VERSION + "\n", encoding="utf-8")
        wants_merge = bool(llm_patch) and click.confirm(
            "  Merge LLM settings into existing config?", default=True
        )
        if wants_merge:
            _defaults.merge_patch_into_file(cfg_path, llm_patch)

    asyncio.run(_bootstrap_db(DEFAULT_DB_PATH))
    click.echo(f"  SQLite ready at {DEFAULT_DB_PATH}")


async def _bootstrap_db(db_path) -> None:
    from selene_space_storage.sqlite import AsyncSQLiteManager

    manager = AsyncSQLiteManager(db_path)
    await manager.connect()
    await manager.close()


@click.command("setup")
def setup() -> None:
    """Interactive first-time setup (all steps skippable)."""
    click.echo(f"SeleneQuant Setup — CLI {cli_version()}")
    click.echo("=" * 40)
    _stage_environment()
    _collect_exchange_credentials()
    llm_patch = _collect_llm_settings()
    _materialize_dirs_and_db(llm_patch)
    _offer_demo_backtest()

    click.echo("\n[6/6] Done")
    click.echo("  SeleneQuant is ready. Try: selenespace status")
    click.echo("=" * 40)


def _offer_demo_backtest() -> None:
    """Optional synthetic-data backtest; failures never abort the wizard."""
    wants_demo = click.confirm(
        "\n[5/6] Demo backtest (optional)\n  Run a quick SMA check on generated candles?",
        default=False,
    )
    if not wants_demo:
        return
    try:
        _demo_backtest()
    except Exception as problem:
        click.echo(f"  Demo failed: {problem}")
