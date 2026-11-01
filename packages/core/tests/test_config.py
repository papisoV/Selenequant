"""Config locks for selene_space_core.config — env/yaml/kwargs precedence
and safe defaults."""

import pytest

from selene_space_core.config import SeleneSpaceConfig, load_config

LEAKY_VARS = (
    "SELENE_ENV",
    "SELENE_API_PORT",
    "SELENE_LOG_LEVEL",
    "SELENE_ENABLE_REAL_TRADING",
)


class TestDefaultsAndEnv:
    def test_construction_defaults(self, monkeypatch):
        # Clear env vars that may leak from other tests' .env files
        for var in LEAKY_VARS:
            monkeypatch.delenv(var, raising=False)
        cfg = SeleneSpaceConfig()
        trio = (cfg.env, cfg.api_port, cfg.enable_real_trading)
        assert trio == ("development", 8088, False)

    def test_environment_variables_win_over_defaults(self, monkeypatch):
        monkeypatch.setenv("SELENE_API_PORT", "9090")
        monkeypatch.setenv("SELENE_LOG_LEVEL", "DEBUG")
        cfg = SeleneSpaceConfig()
        pair = (cfg.api_port, cfg.log_level)
        assert pair == (9090, "DEBUG")


YAML_BODY = "api_port: {port}\nlog_level: {level}\n"


class TestLoadConfigPrecedence:
    def test_yaml_file_supplies_values(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SELENE_API_PORT", raising=False)
        monkeypatch.delenv("SELENE_LOG_LEVEL", raising=False)
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(YAML_BODY.format(port=7777, level="WARNING"))
        cfg = load_config(config_path=yaml_file)
        pair = (cfg.api_port, cfg.log_level)
        assert pair == (7777, "WARNING")

    def test_env_beats_yaml(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SELENE_API_PORT", raising=False)
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(YAML_BODY.format(port=7777, level="WARNING"))
        monkeypatch.setenv("SELENE_API_PORT", "5555")
        assert load_config(config_path=yaml_file).api_port == 5555

    def test_kwargs_beat_yaml(self, tmp_path):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("api_port: 7777\n")
        assert load_config(config_path=yaml_file, api_port=3333).api_port == 3333

    def test_absent_yaml_falls_back_to_defaults(self, tmp_path):
        cfg = load_config(config_path=tmp_path / "nowhere.yaml")
        assert cfg.api_port == 8088


class TestFailLoudEnvCoercion:
    """F1 (open-source-reference 吸收): 拼错的布尔值启动即报错,不静默降级成 False。

    TA `default_config.py:41-61` 的 `_coerce` 按类型自省,`treu` 这类拼写错误
    抛 `ValueError` 而非静默当假 —— 无人值守的运行宁可在启动时炸,也不要把
    配置错当成用户意图。"""

    def test_misspelled_bool_raises_not_silently_false(self, monkeypatch):
        monkeypatch.setenv("SELENE_ENABLE_REAL_TRADING", "treu")
        with pytest.raises(ValueError):
            load_config()

    def test_invalid_bool_error_names_the_env_var(self, monkeypatch):
        monkeypatch.setenv("SELENE_ENABLE_REAL_TRADING", "treu")
        with pytest.raises(ValueError, match="SELENE_ENABLE_REAL_TRADING"):
            load_config()

    def test_valid_bool_true_still_parses(self, monkeypatch):
        monkeypatch.setenv("SELENE_ENABLE_REAL_TRADING", "true")
        assert load_config().enable_real_trading is True

    def test_valid_bool_off_still_parses(self, monkeypatch):
        monkeypatch.setenv("SELENE_ENABLE_REAL_TRADING", "off")
        assert load_config().enable_real_trading is False

    def test_empty_value_means_unset_not_an_error(self, monkeypatch):
        # 空字符串 = 未设置(TA `_apply_env_overrides` 的 `raw == ""` 跳过),
        # 落到默认值而不是崩;否则 ``SELENE_X=`` 会被新校验误伤。
        monkeypatch.setenv("SELENE_ENABLE_REAL_TRADING", "")
        assert load_config().enable_real_trading is False
