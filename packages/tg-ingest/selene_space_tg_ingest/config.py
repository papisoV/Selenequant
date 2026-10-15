"""Hybrid env + yaml config for selene_space_tg_ingest.

Resolution order (each field):
1. Environment variable (SELENE_TG_*) — highest priority, good for secrets/CI
2. YAML config file (telegram.{api_id,api_hash,phone}, signal.llm.{base_url,model,api_key},
   groups, proxy, storage.db_path, timezone.utc_offset_hours, signal.keywords)
3. config.example.yaml as last-resort template if no yaml_path provided and defaults exist

Env overrides (read first):
- SELENE_TG_API_ID (int)
- SELENE_TG_API_HASH (str)
- SELENE_TG_PHONE (str, e.g. '+8613800138000')
- SELENE_TG_LLM_API_KEY (str) — DeepSeek API key (also accepts legacy SIGNAL_LLM_API_KEY)
- SELENE_TG_DB_PATH (str) — override storage.db_path
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from selene_space_tg_ingest.config_schema import validate_config
from selene_space_tg_ingest.signal_filter import DEFAULT_KEYWORD_RULES

logger = logging.getLogger(__name__)

# Package directory; config.example.yaml lives next to this module.
_PKG_DIR = Path(__file__).resolve().parent
DEFAULT_EXAMPLE_YAML = _PKG_DIR / "config.example.yaml"


@dataclass(frozen=True)
class TgIngestConfig:
    """Fully resolved config for tg-ingest runtime."""
    api_id: int
    api_hash: str
    phone: str
    session_dir: Path
    groups: list[dict[str, Any]]
    proxy: dict[str, Any]
    db_path: Path
    utc_offset_hours: int
    llm_enabled: bool
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    llm_temperature: float
    llm_max_tokens: int
    llm_timeout_connect: float
    llm_timeout_read: float
    filter_min_text_length: int
    batch_size: int
    llm_batch_size: int
    llm_delay: float
    factor_version: int
    keywords: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_KEYWORD_RULES))
    # Multi-provider LLM pool (tgwatcher absorption). Defaults keep legacy
    # single-provider configs working: provider="openai", empty pool.
    llm_provider: str = "openai"
    llm_providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    llm_fallback_order: list[str] = field(default_factory=list)

    def to_runtime_dict(self) -> dict[str, Any]:
        """Build the dict that TGClient/SignalEngine/SignalLLMClient expect."""
        return {
            "telegram": {
                "api_id": self.api_id,
                "api_hash": self.api_hash,
                "phone": self.phone,
                "session_dir": str(self.session_dir),
            },
            "proxy": self.proxy,
            "storage": {"db_path": str(self.db_path)},
            "timezone": {"utc_offset_hours": self.utc_offset_hours},
            "crawl": {"min_delay": 1, "max_delay": 3},
            "signal": {
                "enabled": self.llm_enabled,
                "llm": {
                    "base_url": self.llm_base_url,
                    "model": self.llm_model,
                    "api_key": self.llm_api_key,
                    "temperature": self.llm_temperature,
                    "max_tokens": self.llm_max_tokens,
                    "timeout_connect": self.llm_timeout_connect,
                    "timeout_read": self.llm_timeout_read,
                    "provider": self.llm_provider,
                    "providers": self.llm_providers,
                    "fallback_order": self.llm_fallback_order,
                },
                "filter": {"min_text_length": self.filter_min_text_length},
                "batch_size": self.batch_size,
                "llm_batch_size": self.llm_batch_size,
                "llm_delay": self.llm_delay,
                "factor_version": self.factor_version,
                "keywords": self.keywords,
            },
        }

    @classmethod
    def load(cls, yaml_path: str | Path | None = None) -> "TgIngestConfig":
        # Resolve yaml path
        if yaml_path is None:
            # Try CWD/config.yaml first, then fall back to package's example
            cwd_yaml = Path.cwd() / "config.yaml"
            yaml_path = cwd_yaml if cwd_yaml.exists() else DEFAULT_EXAMPLE_YAML
        yaml_path = Path(yaml_path)

        raw: dict[str, Any] = {}
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

        # Advisory typo detection on the raw YAML keys the user typed
        # (logs warnings only; never rejects — defaults/env fill the rest).
        validate_config(raw)

        # Env overrides (take precedence over yaml)
        api_id_env = os.environ.get("SELENE_TG_API_ID")
        api_hash_env = os.environ.get("SELENE_TG_API_HASH")
        phone_env = os.environ.get("SELENE_TG_PHONE")
        llm_api_key_env = os.environ.get("SELENE_TG_LLM_API_KEY") or os.environ.get("SIGNAL_LLM_API_KEY")
        db_path_env = os.environ.get("SELENE_TG_DB_PATH")

        tg = raw.get("telegram", {}) or {}

        def _strip_placeholder(val: Any, placeholders: set[str]) -> Any:
            """Treat template placeholders from config.example.yaml as missing."""
            if isinstance(val, str) and (val.upper().startswith("YOUR_") or val in placeholders):
                return None
            return val

        raw_api_id = _strip_placeholder(tg.get("api_id"), {"0"})
        raw_api_hash = _strip_placeholder(tg.get("api_hash"), set())
        raw_phone = _strip_placeholder(tg.get("phone"), {"+1234567890"})

        try:
            api_id = int(api_id_env) if api_id_env else (int(raw_api_id) if raw_api_id else 0)
        except (TypeError, ValueError):
            api_id = 0

        api_hash = api_hash_env or (raw_api_hash or "")
        phone = phone_env or (raw_phone or "")
        session_dir = Path(tg.get("session_dir", "./sessions"))

        proxy = raw.get("proxy", {}) or {"enabled": False}

        storage_cfg = raw.get("storage", {}) or {}
        db_path = Path(db_path_env) if db_path_env else Path(storage_cfg.get("db_path", "./data/tgwatcher.db"))

        tz_cfg = raw.get("timezone", {}) or {}
        utc_offset_hours = int(tz_cfg.get("utc_offset_hours", 8))

        groups = raw.get("groups", []) or []

        signal_cfg = raw.get("signal", {}) or {}
        llm_cfg = signal_cfg.get("llm", {}) or {}
        filter_cfg = signal_cfg.get("filter", {}) or {}
        keywords = signal_cfg.get("keywords", DEFAULT_KEYWORD_RULES) or DEFAULT_KEYWORD_RULES

        return cls(
            api_id=api_id,
            api_hash=api_hash,
            phone=phone,
            session_dir=session_dir,
            groups=groups,
            proxy=proxy,
            db_path=db_path,
            utc_offset_hours=utc_offset_hours,
            llm_enabled=bool(signal_cfg.get("enabled", False)),
            llm_base_url=llm_cfg.get("base_url", "https://api.deepseek.com/v1"),
            llm_model=llm_cfg.get("model", "deepseek-chat"),
            llm_api_key=llm_api_key_env or llm_cfg.get("api_key", ""),
            llm_temperature=float(llm_cfg.get("temperature", 0)),
            llm_max_tokens=int(llm_cfg.get("max_tokens", 512)),
            llm_timeout_connect=float(llm_cfg.get("timeout_connect", 30)),
            llm_timeout_read=float(llm_cfg.get("timeout_read", 60)),
            filter_min_text_length=int(filter_cfg.get("min_text_length", 10)),
            batch_size=int(signal_cfg.get("batch_size", 50)),
            llm_batch_size=int(signal_cfg.get("llm_batch_size", 15)),
            llm_delay=float(signal_cfg.get("llm_delay", 1.0)),
            factor_version=int(signal_cfg.get("factor_version", 2)),
            keywords=keywords,
            llm_provider=str(llm_cfg.get("provider", "openai")),
            llm_providers=dict(llm_cfg.get("providers", {}) or {}),
            llm_fallback_order=list(llm_cfg.get("fallback_order", []) or []),
        )
