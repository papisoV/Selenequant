from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass

from . import notify
from .config import AppConfig, NotificationTarget
from .market_data import provider_can_handle, supported_providers
from .models import Asset


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str


def run_doctor(config: AppConfig) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    providers = config.data.providers
    for provider in providers:
        checks.append(_provider_check(provider))
    unknown = [provider for provider in providers if provider.lower() not in supported_providers()]
    for provider in unknown:
        checks.append(DoctorCheck(f"provider:{provider}", "fail", "Unsupported provider name."))
    for asset in config.assets:
        capable = [provider for provider in providers if provider_can_handle(provider, asset)]
        status = "ok" if capable else "fail"
        detail = ", ".join(capable) if capable else "No configured provider can handle this symbol."
        checks.append(DoctorCheck(f"asset:{asset.symbol}", status, detail))
    if config.benchmarks.enabled:
        for asset in _benchmark_assets(config):
            capable = [provider for provider in providers if provider_can_handle(provider, asset)]
            status = "ok" if capable else "fail"
            detail = ", ".join(capable) if capable else "No configured provider can handle this benchmark."
            checks.append(DoctorCheck(f"benchmark:{asset.symbol}", status, detail))
    checks.append(_llm_check(config))
    for target in config.notifications:
        check = _notification_check(target)
        if check is not None:
            checks.append(check)
    return checks


def format_doctor(checks: list[DoctorCheck]) -> str:
    lines = ["AI Market Pulse Doctor", ""]
    for check in checks:
        marker = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(check.status, check.status.upper())
        lines.append(f"[{marker}] {check.name}: {check.detail}")
    return "\n".join(lines)


def _provider_check(provider: str) -> DoctorCheck:
    name = provider.strip().lower()
    if name in {"yfinance", "yf"}:
        return _module_check("provider:yfinance", "yfinance", "Install with `pip install -e .`.")
    if name == "akshare":
        return _module_check("provider:akshare", "akshare", "Optional. Install with `pip install -e .[cn]`.")
    if name == "baostock":
        return _module_check("provider:baostock", "baostock", "Optional. Install with `pip install -e .[cn]`.")
    if name == "tushare":
        module = _module_check("provider:tushare", "tushare", "Optional. Install with `pip install -e .[tushare]`.")
        if module.status != "ok":
            return module
        if not os.getenv("TUSHARE_TOKEN"):
            return DoctorCheck("provider:tushare", "warn", "Installed, but TUSHARE_TOKEN is not configured.")
        return DoctorCheck("provider:tushare", "ok", "Installed and token is configured.")
    return DoctorCheck(f"provider:{provider}", "fail", "Unsupported provider.")


def _module_check(name: str, module: str, hint: str) -> DoctorCheck:
    if importlib.util.find_spec(module):
        return DoctorCheck(name, "ok", "Installed.")
    return DoctorCheck(name, "warn", f"Not installed. {hint}")


def _llm_check(config: AppConfig) -> DoctorCheck:
    if not config.llm.enabled:
        return DoctorCheck("llm", "warn", "Disabled. Reports will use rule-based summaries only.")
    key = os.getenv(config.llm.api_key_env)
    if not key:
        return DoctorCheck("llm", "warn", f"Enabled, but {config.llm.api_key_env} is not configured.")
    if not config.llm.model:
        return DoctorCheck("llm", "warn", "Enabled, but model is empty.")
    return DoctorCheck("llm", "ok", f"Configured for model {config.llm.model}.")


_URL_BASED_NOTIFICATION_TYPES = {"feishu", "webhook", "slack", "discord", "wecom"}


def _notification_check(target: NotificationTarget) -> DoctorCheck | None:
    if not target.enabled:
        return None
    kind = target.type.lower()
    check_name = f"notification:{target.name or target.type}"
    if kind == "telegram":
        return _telegram_notification_check(check_name, target.settings)
    if kind in _URL_BASED_NOTIFICATION_TYPES:
        return _url_notification_check(check_name, kind, target.settings)
    if kind == "email":
        return _email_notification_check(check_name, target.settings)
    return None


def _telegram_notification_check(check_name: str, settings: dict) -> DoctorCheck:
    token = notify.resolve_setting(settings, "token", "token_env")
    if not token:
        return DoctorCheck(check_name, "fail", _missing_var_detail(settings, "token", "token_env"))
    chat_id = notify.resolve_setting(settings, "chat_id", "chat_id_env")
    if not chat_id:
        return DoctorCheck(check_name, "fail", _missing_var_detail(settings, "chat_id", "chat_id_env"))
    return DoctorCheck(check_name, "ok", "Telegram token and chat_id are configured.")


def _url_notification_check(check_name: str, kind: str, settings: dict) -> DoctorCheck:
    url = notify.resolve_setting(settings, "url", "url_env")
    if not url:
        return DoctorCheck(check_name, "fail", _missing_var_detail(settings, "url", "url_env"))
    if not url.startswith("https://"):
        return DoctorCheck(check_name, "fail", f"{kind} url must use https://, got: {url!r}")
    return DoctorCheck(check_name, "ok", f"{kind} url is configured with a secure scheme.")


def _email_notification_check(check_name: str, settings: dict) -> DoctorCheck:
    required = [
        ("smtp_host", "smtp_host_env"),
        ("sender", "sender_env"),
        ("to", "to_env"),
    ]
    for literal_key, env_key in required:
        if not notify.resolve_setting(settings, literal_key, env_key):
            return DoctorCheck(check_name, "fail", _missing_var_detail(settings, literal_key, env_key))
    return DoctorCheck(check_name, "ok", "Email smtp_host, sender, and to are configured.")


def _missing_var_detail(settings: dict, literal_key: str, env_key: str) -> str:
    env_name = settings.get(env_key)
    if env_name:
        return f"{env_key} is set to {env_name!r}, but env var {env_name} is not set (or empty)."
    return f"Missing {literal_key} (or {env_key} pointing to an env var)."


def _benchmark_assets(config: AppConfig) -> list[Asset]:
    symbols: list[str] = []
    symbols.extend(config.benchmarks.symbols)
    symbols.extend(config.benchmarks.compare.values())
    markets = {asset.market.upper() for asset in config.assets}
    symbols.extend(
        benchmark
        for market, benchmark in config.benchmarks.default_by_market.items()
        if market.upper() in markets
    )
    seen: set[str] = set()
    assets: list[Asset] = []
    for symbol in symbols:
        key = symbol.upper()
        if key in seen:
            continue
        seen.add(key)
        assets.append(Asset(symbol=symbol, market=_infer_market(symbol)))
    return assets


def _infer_market(symbol: str) -> str:
    upper = symbol.upper()
    if upper.endswith((".SS", ".SZ")) or upper.startswith(("000", "399")):
        return "CN"
    if upper.endswith(".HK") or upper.startswith("^HSI"):
        return "HK"
    if upper.endswith("-USD"):
        return "CRYPTO"
    return "US"
