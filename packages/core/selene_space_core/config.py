"""Layered configuration: YAML file -> environment variables -> explicit kwargs."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class SeleneSpaceConfig(BaseSettings):
    """Runtime configuration with SELENE_-prefixed env overrides."""

    model_config = SettingsConfigDict(
        env_prefix="SELENE_",
        extra="ignore",
        # 空字符串 env 视为"未设置":交给默认值,别让 pydantic 去解析 ``''``
        # (bool 字段会直接 ValidationError)。与下面 env 循环里的跳过一致。
        env_ignore_empty=True,
    )

    env: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8088
    log_level: str = "INFO"
    log_dir: Path = Path("logs")
    db_path: Path = Path("data/selenespace.db")
    llm_base_url: str = ""
    enable_real_trading: bool = False


def load_config(
    config_path: str | Path | None = None,
    **overrides,
) -> SeleneSpaceConfig:
    """Build a config: defaults <- YAML keys <- SELENE_* env vars <- kwargs.

    A missing/unreadable YAML file is not an error — defaults apply."""
    yaml_values: dict = {}
    if config_path is not None:
        path = Path(config_path)
        if path.is_file():
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    yaml_values = {
                        k: v
                        for k, v in raw.items()
                        if k in SeleneSpaceConfig.model_fields and v is not None
                    }
            except yaml.YAMLError:
                yaml_values = {}

    data: dict = {**yaml_values}
    # Env vars beat YAML: read them explicitly so ordering is deterministic.
    env_fields = {
        "env": "SELENE_ENV",
        "api_host": "SELENE_HOST",
        "api_port": "SELENE_API_PORT",
        "log_level": "SELENE_LOG_LEVEL",
        "log_dir": "SELENE_LOG_DIR",
        "db_path": "SELENE_KLINES_DB",
        "llm_base_url": "SELENE_LLM_BASE_URL",
        "enable_real_trading": "SELENE_ENABLE_REAL_TRADING",
    }
    import os

    for field, var in env_fields.items():
        value = os.environ.get(var)
        # 空字符串视为"未设置"(TA `_apply_env_overrides` 同款):否则
        # ``SELENE_X=`` 会被下面的类型校验误判成非法值。
        if value is not None and value != "":
            annotation = SeleneSpaceConfig.model_fields[field].annotation
            try:
                data[field] = _coerce(value, annotation)
            except ValueError as exc:
                raise ValueError(f"Invalid value for {var}: {exc}") from exc

    data.update(overrides)
    return SeleneSpaceConfig(**data)


_BOOL_TRUE = ("1", "true", "yes", "on")
_BOOL_FALSE = ("0", "false", "no", "off")


def _coerce(raw: str, annotation) -> object:
    """按 field 的声明类型转换 env 字符串。

    非法值抛 ``ValueError`` 而不是静默落到默认值 —— 拼错的布尔(如 ``treu``)
    或非数字的 int 应在启动时响亮地失败,而不是悄悄地把无人值守的运行配错
    (对齐 open-source-reference ``default_config.py`` 的 ``_coerce``)。
    """
    if annotation is int or annotation == "int":
        return int(raw)
    if annotation is bool or annotation == "bool":
        normalized = raw.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise ValueError(
            f"expected a boolean ({'/'.join(_BOOL_TRUE + _BOOL_FALSE)}), got {raw!r}"
        )
    if annotation is Path or annotation == "Path":
        return Path(raw)
    return raw
