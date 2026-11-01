"""config 测试: env 覆写优先级/默认值/非法值兜底。

注意: config 是 import 时求值的常量模块,测试用 importlib.reload 在
monkeypatch env 后重载验证。
"""

from __future__ import annotations

import importlib


from selene_space_news_ingest import config


def _reload_with(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(config)


def test_defaults_when_env_absent(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith("SELENE_NI"):
            monkeypatch.delenv(name, raising=False)
    cfg = importlib.reload(config)
    assert cfg.NEWS_POLL_INTERVAL == 420
    assert cfg.TAGGER_POLL_INTERVAL == 30
    assert cfg.DETECTOR_POLL_INTERVAL == 60
    assert cfg.LLM_BASE_URL == "https://api.deepseek.com/v1"
    assert cfg.LLM_MODEL == "deepseek-chat"
    assert cfg.LLM_API_KEY == ""
    assert cfg.SYMBOL_WHITELIST == ["BTC", "ETH", "BNB", "SOL", "XRP"]
    assert cfg.ANOMALY_BASELINE_PERIODS == 20
    assert cfg.ANOMALY_RATIO_THRESHOLD == 3.0
    assert cfg.ANOMALY_WINDOW_MINUTES == 5
    assert cfg.NEWS_HTTP_PROXY == ""


def test_env_overrides_take_priority(monkeypatch):
    cfg = _reload_with(
        monkeypatch,
        SELENE_NI_POLL_INTERVAL="60",
        SELENE_NI_LLM_API_KEY="sk-test",
        SELENE_NI_LLM_MODEL="my-model",
        SELENE_NI_SYMBOL_WHITELIST="btc,eth",
        SELENE_NI_ANOMALY_RATIO_THRESHOLD="5.5",
        SELENE_NI_HTTP_PROXY="http://127.0.0.1:7897",
    )
    assert cfg.NEWS_POLL_INTERVAL == 60
    assert cfg.LLM_API_KEY == "sk-test"
    assert cfg.LLM_MODEL == "my-model"
    assert cfg.SYMBOL_WHITELIST == ["BTC", "ETH"]  # 大写归一
    assert cfg.ANOMALY_RATIO_THRESHOLD == 5.5
    assert cfg.NEWS_HTTP_PROXY == "http://127.0.0.1:7897"


def test_invalid_numeric_falls_back_to_default(monkeypatch):
    cfg = _reload_with(
        monkeypatch,
        SELENE_NI_POLL_INTERVAL="not-a-number",
        SELENE_NI_ANOMALY_RATIO_THRESHOLD="",
    )
    assert cfg.NEWS_POLL_INTERVAL == 420  # 非法回默认
    assert cfg.ANOMALY_RATIO_THRESHOLD == 3.0  # 空串回默认


def test_rss_urls_comma_split(monkeypatch):
    cfg = _reload_with(
        monkeypatch,
        SELENE_NI_RSS_URLS="https://a.com/rss, https://b.com/rss,,",
    )
    assert cfg.NEWS_RSS_URLS == ["https://a.com/rss", "https://b.com/rss"]


def test_no_global_env_mutation():
    """反 binance-quant 教训: config 绝不写 os.environ(只读 getenv)。"""
    import inspect

    src = inspect.getsource(config)
    assert "os.environ[" not in src  # 无赋值
    assert "putenv" not in src and "setenv" not in src.lower().replace("getenv", "")
