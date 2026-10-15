"""Non-sensitive settings persistence + keyring-backed secret state.

Settings live in ``~/.selenespace/settings.json`` (per-user under
``users/<id>/``).  Anything that would reveal which LLM/exchange/proxy the
user runs is kept out of that file and read from the OS keyring instead.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from app.core.crypto import KeyPairManager, decrypt_if_encrypted
from selene_space_core.infra.atomic_write import atomic_write
from selene_space_security.secrets import SecretManager, SecretRef, SecretSource

logger = logging.getLogger(__name__)

#: AI section 出厂默认(可落盘)。三档布尔开关各自命名, 便于按名引用。
_AI_DEFAULTS: dict[str, Any] = {
    "react_enabled": True,
    "show_thinking": True,
    "hallucination_check": True,
    "max_tool_rounds": 10,
    "compaction_threshold": 0.8,
}

# Fields moved from settings.json into Keyring for privacy.
# These reveal which LLM service and proxy the user is using.
# 这两个名字在运行期由 keyring 后端解析, 与 _AI_DEFAULTS 无耦合。
_LLM_KEYRING_FIELDS = ("base_url", "model", "provider")
_LLM_SMART_KEYRING_FIELDS = ("strategy", "analysis", "quick")

#: 若干 section 只允许从 payload 透传白名单内的键, 其余一律忽略。
_GENERAL_KEYS = frozenset(
    {"api_url", "default_symbol", "default_interval", "execution_mode"}
)
_RISK_STR_KEYS = frozenset(
    {"max_position_pct", "single_risk_pct", "daily_loss_limit_pct", "cooldown_seconds"}
)
_RISK_TIERED_KEYS = frozenset(
    {
        "tiered_hard_stop_pct",
        "tiered_low_tier_threshold",
        "tiered_first_tier_threshold",
        "tiered_second_tier_threshold",
        "tiered_low_tier_drop_pct",
        "tiered_first_tier_drop_pct",
        "tiered_second_tier_drop_pct",
        "tiered_min_emit_interval_seconds",
    }
)
_GRID_KEYS = frozenset(
    {
        "default_mode",
        "default_leverage",
        "default_grid_count",
        "default_out_of_range_action",
    }
)
_SENTINEL_KEYS = frozenset({"enabled", "sentinel_min", "market_min", "report_hour"})
_DEX_SUBKEYS = ("decibel", "extended", "risex")
#: 收藏标的与搜索上限(超出截断)。
_MAX_FAVORITES = 30
#: 已脱敏的占位符, 回写时视为"未改"。
_MASKED = "•" * 8

#: 各 section 的出厂默认值(可落盘的部分)。
_GENERAL_DEFAULTS: dict[str, Any] = {
    "api_url": "http://localhost:8088",
    "execution_mode": "paper",
    "default_symbol": "BTC/USDT",
    "default_interval": "1h",
}
_EXCHANGE_DEFAULTS: dict[str, Any] = {
    "provider": "binance",
    "market_type": "spot",
    "testnet": True,
}
_LLM_DEFAULTS: dict[str, Any] = {"smart_mode": False}
_RISK_DEFAULTS: dict[str, Any] = {
    "max_position_pct": "10",
    "daily_loss_limit_pct": "5",
    "single_risk_pct": "2",
    "cooldown_seconds": "300",
    # 分档追踪止损: 触发阈值 + 各级回撤比例 + 最小播报间隔。
    "tiered_hard_stop_pct": 0.02,
    "tiered_low_tier_threshold": 0.015,
    "tiered_first_tier_threshold": 0.03,
    "tiered_second_tier_threshold": 0.06,
    "tiered_low_tier_drop_pct": 0.5,
    "tiered_first_tier_drop_pct": 0.30,
    "tiered_second_tier_drop_pct": 0.20,
    "tiered_min_emit_interval_seconds": 30.0,
}
_GRID_DEFAULTS: dict[str, Any] = {
    "default_mode": "neutral",
    "default_leverage": 10,
    "default_grid_count": 10,
    "default_out_of_range_action": "close",
}
_SENTINEL_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "sentinel_min": 5,
    "market_min": 30,
    "report_hour": 20,
}


def _persistence_guarantee(encrypted: bool) -> str:
    """落盘加密保证的措辞(随加密是否开启而变)。"""
    return "fernet-encrypted" if encrypted else "best-effort-os-keychain"


def _security_note(encrypted: bool) -> str:
    """给用户看的加密状态说明。"""
    if encrypted:
        return (
            "Secrets are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) "
            "before storage."
        )
    return (
        "WARNING: SELENE_ENCRYPTION_KEY not set — secrets stored "
        "without application-layer encryption."
    )


def _is_local_user(user_id: str | None) -> bool:
    """无 user_id 或显式 "local" 都算本机用户(不分区)。"""
    return not user_id or user_id == "local"


def _api_key_hint(key: str) -> str:
    key = str(key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "***"
    return f"{key[:4]}...{key[-4:]}"


def _secret_ref(provider: str, field: str) -> SecretRef:
    """Keyring-backed SecretRef (the only source this service uses)."""
    return SecretRef(source=SecretSource.KEYRING, provider=provider, id=field)


def _pick_str_keys(values: dict[str, Any], allowed: frozenset[str]) -> dict[str, str]:
    """只保留白名单内的键, 值统一转字符串。"""
    return {k: str(v) for k, v in values.items() if k in allowed}


def _pick_raw_keys(values: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    """只保留白名单内的键, 值原样。"""
    return {k: v for k, v in values.items() if k in allowed}


def _clean_str_list(items: list[Any], limit: int | None = None) -> list[str]:
    """去空白、丢空串; 可选截断。"""
    cleaned = [str(s).strip() for s in items if str(s).strip()]
    return cleaned[:limit] if limit is not None else cleaned


def _persistable(section: str, values: Any) -> Any:
    """决定某 section 落盘时保留什么。

    LLM 只留 ``smart_mode``(其余都在 keyring); network 留 ``proxy_url``;
    非 dict 的 section 原样保留。

    network 曾整节丢弃（"代理 URL 属敏感"），但那让 UI 配置失效：写入只进
    keyring，而启动路径 ``main.py`` 只读 settings.json —— 用户在设置页填了
    代理，重启后读不到，等于没配。代理地址与 API key 的威胁模型不同：
    它通常是本机/局域网地址（127.0.0.1:7897 这类），泄露不造成损失。
    故落盘。
    """
    if not isinstance(values, dict):
        return values
    if section == "llm":
        return {k: v for k, v in values.items() if k == "smart_mode"}
    if section == "network":
        return {k: v for k, v in values.items() if k == "proxy_url"}
    return values


def _ai_defaults() -> dict[str, Any]:
    return dict(_AI_DEFAULTS)


def _risk_alert_defaults() -> dict[str, Any]:
    return {"enabled": True, "min_level": "info", "rules": {}}


def _skills_defaults() -> dict[str, Any]:
    return {"extra_dirs": [], "enabled": {}}


class SettingsService:
    """Persist non-sensitive settings and handle secret state in keyring."""

    def __init__(
        self,
        *,
        config_path: Path | None = None,
        secret_manager: SecretManager | None = None,
        key_pair_manager: KeyPairManager | None = None,
    ) -> None:
        self._config_path = config_path or (
            Path.home() / ".selenespace" / "settings.json"
        )
        self._secret_manager = secret_manager or SecretManager()
        self._key_pair_manager = key_pair_manager

    def _user_config_path(self, user_id: str | None) -> Path:
        """Return the settings.json path scoped to a user."""
        if _is_local_user(user_id):
            return self._config_path
        return Path.home() / ".selenespace" / "users" / str(user_id) / "settings.json"

    @staticmethod
    def _kr_provider(base: str, user_id: str | None) -> str:
        """Namespace a keyring provider string by user_id."""
        return base if _is_local_user(user_id) else f"{base}.u.{user_id}"

    def _providers(self, user_id: str | None) -> dict[str, str]:
        """本服务用到的四个 keyring 命名空间。"""
        return {
            "exchange": self._kr_provider("selenespace.exchange", user_id),
            "llm": self._kr_provider("selenespace.llm", user_id),
            "network": self._kr_provider("selenespace.network", user_id),
            "smart": self._kr_provider("selenespace.llm.smart", user_id),
        }

    async def _resolve_field(self, provider: str, field: str, fallback: Any) -> Any:
        """从 keyring 取值; 取不到(未配置/后端不可用)返回 fallback。"""
        try:
            resolved = await self._secret_manager.resolve(_secret_ref(provider, field))
            return resolved.use()
        except Exception:
            return fallback

    async def _exists(self, provider: str, field: str) -> bool:
        return bool(await self._secret_manager.exists(_secret_ref(provider, field)))

    # -- 读 ----------------------------------------------------------------

    async def get_settings(self, *, user_id: str | None = None) -> dict[str, Any]:
        settings = self._load_non_sensitive(user_id=user_id)
        providers = self._providers(user_id)
        await self._fill_exchange_state(settings, providers["exchange"])
        await self._fill_llm_state(settings, providers)
        await self._fill_network_state(settings, providers["network"])
        settings["security"] = self._security_state()
        return settings

    async def _fill_exchange_state(
        self, settings: dict[str, Any], ex_prov: str
    ) -> None:
        has_key = await self._exists(ex_prov, "api_key")
        has_secret = await self._exists(ex_prov, "api_secret")
        exchange = settings["exchange"]
        exchange["api_key_configured"] = has_key
        exchange["api_secret_configured"] = has_secret
        exchange["api_key_masked"] = _MASKED if has_key else ""
        exchange["api_secret_masked"] = _MASKED if has_secret else ""
        exchange["api_key_hint"] = ""
        if has_key:
            stored = await self._resolve_field(ex_prov, "api_key", "")
            exchange["api_key_hint"] = _api_key_hint(stored)

    async def _fill_llm_state(
        self, settings: dict[str, Any], providers: dict[str, str]
    ) -> None:
        llm_prov, smart_prov = providers["llm"], providers["smart"]
        has_key = await self._exists(llm_prov, "api_key")
        llm = settings["llm"]
        llm["api_key_configured"] = has_key
        llm["api_key_masked"] = _MASKED if has_key else ""
        for field in _LLM_KEYRING_FIELDS:
            llm[field] = await self._resolve_field(llm_prov, field, llm.get(field, ""))
        llm["smart_models"] = {
            field: await self._resolve_field(smart_prov, field, "")
            for field in _LLM_SMART_KEYRING_FIELDS
        }

    async def _fill_network_state(
        self, settings: dict[str, Any], net_prov: str
    ) -> None:
        network = settings["network"]
        network["proxy_url"] = await self._resolve_field(
            net_prov, "proxy_url", network.get("proxy_url", "")
        )

    def _security_state(self) -> dict[str, Any]:
        """加密后端自述(不含任何密钥)。"""
        keyring_info = self._secret_manager.keyring_backend_info()
        from selene_space_security.encryption import encryption_status

        encrypted = encryption_status()["field_encryption_active"]
        backend = self._backend_report(keyring_info)
        return {
            **backend,
            "field_encryption_active": encrypted,
            "persistence_guarantee": _persistence_guarantee(encrypted),
            "security_note": _security_note(encrypted),
        }

    def _backend_report(self, keyring_info: dict[str, Any]) -> dict[str, Any]:
        """密钥后端的基本描述(名称/可用性/是否安全后端)。"""
        report: dict[str, Any] = {"secret_backend": "keyring"}
        report["keyring_available"] = self._secret_manager.keyring_available()
        report["keyring_backend"] = keyring_info.get("backend", "unknown")
        report["is_secure_backend"] = keyring_info.get("is_secure_backend", False)
        return report

    # -- 写 ----------------------------------------------------------------

    async def update_settings(
        self, payload: dict[str, Any], *, user_id: str | None = None
    ) -> dict[str, Any]:
        non_sensitive = self._load_non_sensitive(user_id=user_id)
        providers = self._providers(user_id)
        for section, handler in self._section_handlers().items():
            incoming = payload.get(section)
            if isinstance(incoming, dict):
                await handler(incoming, non_sensitive, providers)
        self._save_non_sensitive(non_sensitive, user_id=user_id)
        return await self.get_settings(user_id=user_id)

    def _section_handlers(
        self,
    ) -> dict[str, Callable[[dict, dict, dict], Awaitable[None]]]:
        """payload section 名 → 处理函数(未知 section 直接忽略)。"""
        return {
            "general": self._apply_general,
            "exchange": self._apply_exchange,
            "llm": self._apply_llm,
            "risk": self._apply_risk,
            "risk_alerts": self._apply_risk_alerts,
            "grid": self._apply_grid,
            "dex": self._apply_dex,
            "sentinel": self._apply_sentinel,
            "network": self._apply_network,
            "skills": self._apply_skills,
            "ai": self._apply_ai,
            "favorites": self._apply_favorites,
        }

    async def _apply_general(
        self, incoming: dict, non_sensitive: dict, providers: dict
    ) -> None:
        non_sensitive["general"].update(_pick_str_keys(incoming, _GENERAL_KEYS))

    async def _apply_exchange(
        self, incoming: dict, non_sensitive: dict, providers: dict
    ) -> None:
        section = non_sensitive["exchange"]
        for key in ("provider", "market_type"):
            if key in incoming:
                section[key] = str(incoming[key])
        if "testnet" in incoming:
            section["testnet"] = bool(incoming["testnet"])
        await self._upsert_secret(
            _secret_ref(providers["exchange"], "api_key"),
            value=incoming.get("api_key"),
            clear=bool(incoming.get("clear_api_key", False)),
        )
        await self._upsert_secret(
            _secret_ref(providers["exchange"], "api_secret"),
            value=incoming.get("api_secret"),
            clear=bool(incoming.get("clear_api_secret", False)),
        )

    async def _apply_llm(
        self, incoming: dict, non_sensitive: dict, providers: dict
    ) -> None:
        section = non_sensitive["llm"]
        for field in _LLM_KEYRING_FIELDS:
            if field in incoming:
                await self._upsert_secret(
                    _secret_ref(providers["llm"], field),
                    value=str(incoming[field]),
                    clear=False,
                )
        if "smart_mode" in incoming:
            section["smart_mode"] = bool(incoming["smart_mode"])
        smart_payload = incoming.get("smart_models")
        if isinstance(smart_payload, dict):
            for field in _LLM_SMART_KEYRING_FIELDS:
                if field in smart_payload:
                    await self._upsert_secret(
                        _secret_ref(providers["smart"], field),
                        value=str(smart_payload[field]),
                        clear=False,
                    )
        await self._upsert_secret(
            _secret_ref(providers["llm"], "api_key"),
            value=incoming.get("api_key"),
            clear=bool(incoming.get("clear_api_key", False)),
        )

    async def _apply_risk(
        self, incoming: dict, non_sensitive: dict, providers: dict
    ) -> None:
        section = non_sensitive["risk"]
        section.update(_pick_str_keys(incoming, _RISK_STR_KEYS))
        for key in _RISK_TIERED_KEYS:
            if key in incoming:
                try:
                    section[key] = float(incoming[key])
                except (TypeError, ValueError):
                    pass

    async def _apply_risk_alerts(
        self, incoming: dict, non_sensitive: dict, providers: dict
    ) -> None:
        section = non_sensitive["risk_alerts"]
        if "enabled" in incoming:
            section["enabled"] = bool(incoming["enabled"])
        if "min_level" in incoming:
            section["min_level"] = str(incoming["min_level"])
        if isinstance(incoming.get("rules"), dict):
            section["rules"].update(incoming["rules"])

    async def _apply_grid(
        self, incoming: dict, non_sensitive: dict, providers: dict
    ) -> None:
        non_sensitive["grid"].update(_pick_raw_keys(incoming, _GRID_KEYS))

    async def _apply_dex(
        self, incoming: dict, non_sensitive: dict, providers: dict
    ) -> None:
        section = non_sensitive["dex"]
        for sub_key in _DEX_SUBKEYS:
            sub_payload = incoming.get(sub_key)
            if isinstance(sub_payload, dict):
                section[sub_key].update({str(k): v for k, v in sub_payload.items()})

    async def _apply_sentinel(
        self, incoming: dict, non_sensitive: dict, providers: dict
    ) -> None:
        non_sensitive["sentinel"].update(_pick_raw_keys(incoming, _SENTINEL_KEYS))

    async def _apply_network(
        self, incoming: dict, non_sensitive: dict, providers: dict
    ) -> None:
        if "proxy_url" not in incoming:
            return
        proxy = str(incoming["proxy_url"]).strip()
        # Two destinations on purpose:
        #   keyring   — the value the API reads back for display;
        #   settings.json (via non_sensitive, see _persistable) — what startup
        #               reads, because main.py builds proxy_url from
        #               _load_non_sensitive() long before any keyring call.
        # Writing only to the keyring is what made a configured proxy vanish on
        # restart.
        non_sensitive["network"]["proxy_url"] = proxy
        await self._upsert_secret(
            _secret_ref(providers["network"], "proxy_url"),
            value=proxy,
            clear=not proxy,
        )

    async def _apply_skills(
        self, incoming: dict, non_sensitive: dict, providers: dict
    ) -> None:
        _merge_skills(non_sensitive["skills"], incoming)

    async def _apply_ai(
        self, incoming: dict, non_sensitive: dict, providers: dict
    ) -> None:
        section = non_sensitive["ai"]
        for key in ("react_enabled", "show_thinking", "hallucination_check"):
            if key in incoming:
                section[key] = bool(incoming[key])
        if "max_tool_rounds" in incoming:
            section["max_tool_rounds"] = max(1, int(incoming["max_tool_rounds"]))
        if "compaction_threshold" in incoming:
            value = float(incoming["compaction_threshold"])
            section["compaction_threshold"] = max(0.1, min(1.0, value))

    async def _apply_favorites(
        self, incoming: dict, non_sensitive: dict, providers: dict
    ) -> None:
        symbols = incoming.get("symbols")
        if isinstance(symbols, list):
            non_sensitive["favorites"]["symbols"] = _clean_str_list(
                symbols, _MAX_FAVORITES
            )

    async def _upsert_secret(self, ref: SecretRef, *, value: Any, clear: bool) -> None:
        if clear:
            await self._delete_secret(ref)
            return
        candidate = value.strip() if isinstance(value, str) else None
        # 空串与脱敏占位符都视为"不修改", 只有真正的新值才落库。
        if not candidate or candidate == _MASKED:
            return
        plaintext = self._decrypt_or_reject(ref, candidate)
        if plaintext is not None:
            await self._secret_manager.store(ref, plaintext)

    async def _delete_secret(self, ref: SecretRef) -> None:
        """清除某个密钥。"""
        await self._secret_manager.delete(ref)

    def _decrypt_or_reject(self, ref: SecretRef, candidate: str) -> str | None:
        """尝试解密; 解密失败则拒绝存储(避免把密文当明文落库)。"""
        try:
            return decrypt_if_encrypted(self._key_pair_manager, candidate)
        except ValueError:
            logger.error(
                "Failed to decrypt secret for %s — rejecting storage to prevent plaintext leak",
                ref.id,
            )
            return None

    # -- 持久化 ------------------------------------------------------------

    @staticmethod
    def _defaults() -> dict[str, Any]:
        return {
            "general": dict(_GENERAL_DEFAULTS),
            "exchange": dict(_EXCHANGE_DEFAULTS),
            "llm": dict(_LLM_DEFAULTS),
            "risk": dict(_RISK_DEFAULTS),
            "risk_alerts": _risk_alert_defaults(),
            "grid": dict(_GRID_DEFAULTS),
            "dex": {
                sub: {"enabled": False, "trade_mode": "paper"} for sub in _DEX_SUBKEYS
            },
            "sentinel": dict(_SENTINEL_DEFAULTS),
            "network": {},
            "skills": _skills_defaults(),
            "ai": _ai_defaults(),
            "favorites": {"symbols": []},
        }

    def _load_non_sensitive(self, *, user_id: str | None = None) -> dict[str, Any]:
        cfg_path = self._user_config_path(user_id)
        defaults = self._defaults()
        stored_root = self._read_settings_file(cfg_path)
        for section, section_defaults in defaults.items():
            if section not in stored_root:
                continue
            stored = stored_root[section]
            if isinstance(section_defaults, dict) and isinstance(stored, dict):
                defaults[section] = {**section_defaults, **stored}
            else:
                defaults[section] = stored
        return defaults

    @staticmethod
    def _read_settings_file(cfg_path: Path) -> dict[str, Any]:
        """读 settings.json; 缺失或损坏都返回空 dict(调用方用默认值)。"""
        if not cfg_path.exists():
            return {}
        try:
            loaded: dict[str, Any] = json.loads(
                cfg_path.read_text(encoding="utf-8-sig")
            )
        except Exception:
            logger.warning("Failed to load settings from %s, using defaults", cfg_path)
            return {}
        return loaded

    def _save_non_sensitive(
        self, data: dict[str, Any], *, user_id: str | None = None
    ) -> None:
        """只把可落盘的部分写进 settings.json(密钥与代理不进文件)。"""
        safe = {
            section: _persistable(section, values) for section, values in data.items()
        }
        self._write_settings_file(self._user_config_path(user_id), safe)

    @staticmethod
    def _write_settings_file(cfg_path: Path, payload: dict[str, Any]) -> None:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(cfg_path, json.dumps(payload, ensure_ascii=False, indent=2))

    # -- 分节直读 ----------------------------------------------------------

    def _section(
        self, name: str, fallback: dict[str, Any], user_id: str | None
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._load_non_sensitive(user_id=user_id).get(name, fallback),
        )

    def get_llm_config(self, *, user_id: str | None = None) -> dict[str, Any]:
        """Return LLM config for internal use — not exposed via API."""
        return self._section("llm", {}, user_id)

    def get_mcp_config(self, *, user_id: str | None = None) -> dict[str, Any]:
        """Return only the MCP configuration section."""
        return self._section("mcp", {"servers": {}}, user_id)

    def get_skills_config(self, *, user_id: str | None = None) -> dict[str, Any]:
        """Return only the skills configuration section."""
        return self._section("skills", _skills_defaults(), user_id)

    def get_ai_config(self, *, user_id: str | None = None) -> dict[str, Any]:
        """Return the AI configuration section (PRD supplement E)."""
        return self._section("ai", _ai_defaults(), user_id)

    def get_risk_alerts_config(self, *, user_id: str | None = None) -> dict[str, Any]:
        """Return the risk alerts configuration section."""
        return self._section("risk_alerts", _risk_alert_defaults(), user_id)

    # -- 密钥解析 ----------------------------------------------------------

    async def resolve_exchange_config(
        self, *, user_id: str | None = None
    ) -> dict[str, Any]:
        """Resolve exchange credentials + venue config from keyring/settings.

        Returns dict with api_key/api_secret (plaintext, "" when unset),
        provider/market_type/testnet. Raises SecretResolutionError when
        the keyring backend itself is unavailable.
        """
        ex_prov = self._kr_provider("selenespace.exchange", user_id)
        exchange = self._load_non_sensitive(user_id=user_id).get("exchange", {})
        parsed = {
            field: await self._resolve_field(ex_prov, field, "")
            for field in ("api_key", "api_secret")
        }
        return {
            "provider": str(exchange.get("provider", "binance")),
            "market_type": str(exchange.get("market_type", "spot")),
            "testnet": bool(exchange.get("testnet", True)),
            **parsed,
        }

    async def resolve_llm_api_key(self, *, user_id: str | None = None) -> str | None:
        """Resolve the LLM API key from keyring for the given user."""
        llm_prov = self._kr_provider("selenespace.llm", user_id)
        return cast("str | None", await self._resolve_field(llm_prov, "api_key", None))

    async def resolve_llm_full_config(
        self, *, user_id: str | None = None
    ) -> dict[str, Any]:
        """Return the full LLM config including keyring secrets for the given user."""
        llm_prov = self._kr_provider("selenespace.llm", user_id)
        config = {
            field: await self._resolve_field(llm_prov, field, "")
            for field in _LLM_KEYRING_FIELDS
        }
        config["api_key"] = await self.resolve_llm_api_key(user_id=user_id)
        return config

    # -- skills / favorites ------------------------------------------------

    def update_skills_config(
        self, skills_payload: dict[str, Any], *, user_id: str | None = None
    ) -> dict[str, Any]:
        """Update skills configuration and persist."""
        non_sensitive = self._load_non_sensitive(user_id=user_id)
        _merge_skills(non_sensitive["skills"], skills_payload)
        self._save_non_sensitive(non_sensitive, user_id=user_id)
        skills: dict[str, Any] = non_sensitive["skills"]
        return skills

    def get_favorite_symbols(self, *, user_id: str | None = None) -> list[str]:
        """Return the list of favorite symbol strings."""
        symbols: list[str] = (
            self._load_non_sensitive(user_id=user_id)
            .get("favorites", {})
            .get("symbols", [])
        )
        return symbols

    def set_favorite_symbols(
        self, symbols: list[str], *, user_id: str | None = None
    ) -> list[str]:
        """Persist a favorites list and return the cleaned result."""
        non_sensitive = self._load_non_sensitive(user_id=user_id)
        cleaned = _dedupe_symbols(symbols)
        non_sensitive["favorites"]["symbols"] = cleaned
        self._save_non_sensitive(non_sensitive, user_id=user_id)
        return cleaned


def _merge_skills(section: dict[str, Any], payload: dict[str, Any]) -> None:
    """把 skills payload 合并进配置节(白名单字段)。"""
    extra_dirs = payload.get("extra_dirs")
    if isinstance(extra_dirs, list):
        section["extra_dirs"] = _clean_str_list(extra_dirs)
    enabled = payload.get("enabled")
    if isinstance(enabled, dict):
        section["enabled"].update({str(k): bool(v) for k, v in enabled.items()})


def _dedupe_symbols(symbols: list[str]) -> list[str]:
    """保序去重并截断。

    去重是必需的: PUT/toggle 竞态下前端可能送重复条, 重复 sym 会让前端
    React key 撞车(sidebar watchlist key={sym})。
    """
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in symbols:
        value = str(raw).strip()
        if value and value not in seen:
            seen.add(value)
            cleaned.append(value)
    return cleaned[:_MAX_FAVORITES]
