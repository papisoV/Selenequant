from __future__ import annotations

from ai_market_pulse.config import AppConfig, DataSettings, NotificationTarget
from ai_market_pulse.doctor import DoctorCheck, format_doctor, run_doctor
from ai_market_pulse.models import Asset


def _config(notifications: list[NotificationTarget] | None = None) -> AppConfig:
    return AppConfig(
        title="Doctor",
        timezone="UTC",
        assets=[Asset(symbol="AAPL"), Asset(symbol="600519.SS")],
        data=DataSettings(providers=["yfinance"]),
        notifications=notifications or [],
    )


def _notification_checks(checks: list[DoctorCheck]) -> list[DoctorCheck]:
    return [check for check in checks if check.name.startswith("notification:")]


def test_doctor_reports_asset_provider_capability() -> None:
    config = _config()

    output = format_doctor(run_doctor(config))

    assert "asset:AAPL" in output
    assert "asset:600519.SS" in output
    assert "provider:yfinance" in output


def test_doctor_reports_no_failures_when_no_notifications_configured() -> None:
    config = _config(notifications=[])

    checks = run_doctor(config)

    assert _notification_checks(checks) == []


def test_doctor_passes_fully_valid_telegram_target(monkeypatch) -> None:
    monkeypatch.setenv("TG_TOKEN", "12345:abcdef")
    monkeypatch.setenv("TG_CHAT_ID", "-100123456")
    target = NotificationTarget(
        type="telegram",
        name="main",
        enabled=True,
        settings={"token_env": "TG_TOKEN", "chat_id_env": "TG_CHAT_ID"},
    )
    config = _config(notifications=[target])

    checks = _notification_checks(run_doctor(config))

    assert len(checks) == 1
    assert checks[0].status == "ok"


def test_doctor_fails_telegram_target_missing_chat_id_env(monkeypatch) -> None:
    monkeypatch.setenv("TG_TOKEN", "12345:abcdef")
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    target = NotificationTarget(
        type="telegram",
        name="main",
        enabled=True,
        settings={"token_env": "TG_TOKEN", "chat_id_env": "TG_CHAT_ID"},
    )
    config = _config(notifications=[target])

    checks = _notification_checks(run_doctor(config))

    assert len(checks) == 1
    assert checks[0].status == "fail"
    assert "TG_CHAT_ID" in checks[0].detail


def test_doctor_fails_feishu_target_with_http_url() -> None:
    target = NotificationTarget(
        type="feishu",
        name="ops",
        enabled=True,
        settings={"url": "http://open.feishu.cn/webhook/abc"},
    )
    config = _config(notifications=[target])

    checks = _notification_checks(run_doctor(config))

    assert len(checks) == 1
    assert checks[0].status == "fail"
    assert "https://" in checks[0].detail


def test_doctor_ignores_disabled_notification_targets() -> None:
    target = NotificationTarget(type="telegram", enabled=False, settings={})
    config = _config(notifications=[target])

    checks = _notification_checks(run_doctor(config))

    assert checks == []


def test_doctor_passes_valid_feishu_https_url() -> None:
    target = NotificationTarget(
        type="feishu",
        settings={"url": "https://open.feishu.cn/webhook/abc"},
    )
    config = _config(notifications=[target])

    checks = _notification_checks(run_doctor(config))

    assert len(checks) == 1
    assert checks[0].status == "ok"


def test_doctor_passes_fully_valid_email_target() -> None:
    target = NotificationTarget(
        type="email",
        settings={"smtp_host": "smtp.example.com", "sender": "a@example.com", "to": "b@example.com"},
    )
    config = _config(notifications=[target])

    checks = _notification_checks(run_doctor(config))

    assert len(checks) == 1
    assert checks[0].status == "ok"


def test_doctor_fails_email_target_missing_fields() -> None:
    target = NotificationTarget(type="email", settings={"smtp_host": "smtp.example.com"})
    config = _config(notifications=[target])

    checks = _notification_checks(run_doctor(config))

    assert len(checks) == 1
    assert checks[0].status == "fail"


def test_doctor_ignores_unrecognized_notification_type() -> None:
    target = NotificationTarget(type="carrier-pigeon", settings={})
    config = _config(notifications=[target])

    checks = _notification_checks(run_doctor(config))

    assert checks == []
