from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_market_pulse.cli import _load_run_config, main
from ai_market_pulse.sample import custom_watchlist_config, parse_symbols


def test_custom_watchlist_config_normalizes_symbols_and_markets() -> None:
    config = yaml.safe_load(
        custom_watchlist_config(
            ["aapl", "600519", "0700.HK", "BTC-USD"],
            title="Client Watchlist",
            providers=["yfinance"],
        )
    )

    assert config["title"] == "Client Watchlist"
    assert config["data"]["providers"] == ["yfinance"]
    assert config["assets"] == [
        {"symbol": "AAPL", "market": "US"},
        {"symbol": "600519.SS", "market": "CN"},
        {"symbol": "0700.HK", "market": "HK"},
        {"symbol": "BTC-USD", "market": "CRYPTO"},
    ]
    assert config["benchmarks"]["compare"]["AAPL"] == "QQQ"


def test_parse_symbols_accepts_commas_spaces_and_chinese_commas() -> None:
    assert parse_symbols("AAPL, MSFT  NVDA，600519") == ["AAPL", "MSFT", "NVDA", "600519"]


def test_init_symbols_writes_custom_watchlist(tmp_path: Path) -> None:
    output = tmp_path / "watchlist.yaml"

    main(["init", "--symbols", "AAPL,MSFT,600519", "--path", str(output), "--title", "Real Portfolio"])

    config = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert config["title"] == "Real Portfolio"
    assert [asset["symbol"] for asset in config["assets"]] == ["AAPL", "MSFT", "600519.SS"]


def test_run_symbols_builds_inline_config() -> None:
    config, label = _load_run_config(
        Path("unused.yaml"),
        "AAPL,MSFT,600519",
        "Inline Pulse",
        "Asia/Shanghai",
        "zh-CN",
        "yfinance",
    )

    assert label == "inline --symbols"
    assert config.title == "Inline Pulse"
    assert config.timezone == "Asia/Shanghai"
    assert [asset.symbol for asset in config.assets] == ["AAPL", "MSFT", "600519.SS"]
    assert [asset.market for asset in config.assets] == ["US", "US", "CN"]


def test_custom_watchlist_config_with_telegram_uses_env_indirection() -> None:
    config = yaml.safe_load(
        custom_watchlist_config(
            ["AAPL"],
            telegram_token="123:abc",
            telegram_chat_id="456",
        )
    )

    assert config["notifications"] == [
        {
            "type": "telegram",
            "name": "telegram",
            "enabled": True,
            "token_env": "TELEGRAM_BOT_TOKEN",
            "chat_id_env": "TELEGRAM_CHAT_ID",
        }
    ]
    dumped = custom_watchlist_config(["AAPL"], telegram_token="123:abc", telegram_chat_id="456")
    assert "123:abc" not in dumped
    assert "456" not in dumped


def test_custom_watchlist_config_with_feishu_uses_env_indirection() -> None:
    config = yaml.safe_load(
        custom_watchlist_config(
            ["AAPL"],
            feishu_webhook="https://open.feishu.cn/hook/xyz",
        )
    )

    assert config["notifications"] == [
        {
            "type": "feishu",
            "name": "feishu",
            "enabled": True,
            "url_env": "FEISHU_WEBHOOK_URL",
        }
    ]
    dumped = custom_watchlist_config(["AAPL"], feishu_webhook="https://open.feishu.cn/hook/xyz")
    assert "https://open.feishu.cn/hook/xyz" not in dumped


def test_custom_watchlist_config_without_notification_args_stays_empty() -> None:
    config = yaml.safe_load(custom_watchlist_config(["AAPL"]))

    assert config["notifications"] == []


def test_custom_watchlist_config_with_only_telegram_token_skips_incomplete_target() -> None:
    config = yaml.safe_load(custom_watchlist_config(["AAPL"], telegram_token="123:abc"))

    assert config["notifications"] == []


def test_custom_watchlist_config_with_only_telegram_chat_id_skips_incomplete_target() -> None:
    config = yaml.safe_load(custom_watchlist_config(["AAPL"], telegram_chat_id="456"))

    assert config["notifications"] == []


def test_init_symbols_with_telegram_writes_notifications_and_prints_exports(tmp_path, capsys) -> None:
    output = tmp_path / "watchlist.yaml"

    main(
        [
            "init",
            "--symbols",
            "AAPL",
            "--path",
            str(output),
            "--telegram-token",
            "abc",
            "--telegram-chat-id",
            "123",
            "--force",
        ]
    )

    config = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert config["notifications"] == [
        {
            "type": "telegram",
            "name": "telegram",
            "enabled": True,
            "token_env": "TELEGRAM_BOT_TOKEN",
            "chat_id_env": "TELEGRAM_CHAT_ID",
        }
    ]

    captured = capsys.readouterr()
    assert "export TELEGRAM_BOT_TOKEN=abc" in captured.out
    assert "export TELEGRAM_CHAT_ID=123" in captured.out
    assert "test-notify" in captured.out
    assert str(output) in captured.out


def test_init_rejects_telegram_token_without_chat_id(tmp_path) -> None:
    output = tmp_path / "watchlist.yaml"

    with pytest.raises(SystemExit, match="--telegram-token and --telegram-chat-id"):
        main(["init", "--symbols", "AAPL", "--path", str(output), "--telegram-token", "abc", "--force"])

    assert not output.exists()


def test_init_rejects_telegram_chat_id_without_token(tmp_path) -> None:
    output = tmp_path / "watchlist.yaml"

    with pytest.raises(SystemExit, match="--telegram-token and --telegram-chat-id"):
        main(["init", "--symbols", "AAPL", "--path", str(output), "--telegram-chat-id", "123", "--force"])

    assert not output.exists()
