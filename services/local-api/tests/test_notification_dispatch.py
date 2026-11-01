"""Tests for the notification dispatcher (settings-page channels -> transport)."""

from __future__ import annotations

import json

import pytest

from app.core import notification_dispatch as nd


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point ~/.selenespace at a temp dir for both channels + preferences."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    base = tmp_path / ".selenespace"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _write_channels(base, channels):
    (base / "notification_channels.json").write_text(
        json.dumps(channels), encoding="utf-8"
    )


def _write_prefs(base, prefs):
    (base / "notification_preferences.json").write_text(
        json.dumps(prefs), encoding="utf-8"
    )


def _channel(cid, ctype, name, config, enabled=True):
    return {
        "id": cid,
        "type": ctype,
        "name": name,
        "config": config,
        "enabled": enabled,
    }


def test_dispatch_routes_only_to_subscribed_channels(home, monkeypatch):
    _write_channels(
        home,
        [
            _channel("c1", "ntfy", "phone", {"url": "https://ntfy.sh/a"}),
            _channel("c2", "pushover", "other", {"user_key": "u", "token": "t"}),
        ],
    )
    _write_prefs(home, {"kill_switch_activated": ["c1"]})

    sent = {}

    def fake_send_to_targets(targets, text, noise=None):
        sent["targets"] = [(t.type, t.name) for t in targets]
        sent["text"] = text
        return [f"{t.type}:{t.name} sent" for t in targets]

    monkeypatch.setattr(
        "selene_space_pulse.notify.send_to_targets", fake_send_to_targets
    )

    results = nd.dispatch("kill_switch_activated", "BREACH")

    assert sent["targets"] == [("ntfy", "phone")]
    assert sent["text"] == "BREACH"
    assert results == ["ntfy:phone sent"]


def test_dispatch_skips_disabled_channel(home, monkeypatch):
    _write_channels(
        home, [_channel("c1", "ntfy", "phone", {"url": "https://ntfy.sh/a"}, enabled=False)]
    )
    _write_prefs(home, {"risk_alert": ["c1"]})

    called = {"n": 0}

    def fake_send_to_targets(targets, text):
        called["n"] += 1
        return []

    monkeypatch.setattr(
        "selene_space_pulse.notify.send_to_targets", fake_send_to_targets
    )
    # env fallback must not fire either when a subscription exists but is disabled
    monkeypatch.setattr(
        "selene_space_pulse.notify.send_trade_alert", lambda text: ["env-fallback"]
    )

    results = nd.dispatch("risk_alert", "x")

    # No enabled targets -> falls back to env path.
    assert results == ["env-fallback"]


def test_dispatch_falls_back_to_env_when_no_subscription(home, monkeypatch):
    _write_channels(home, [])
    _write_prefs(home, {})

    monkeypatch.setattr(
        "selene_space_pulse.notify.send_trade_alert",
        lambda text: [f"env:{text}"],
    )

    assert nd.dispatch("kill_switch_activated", "BREACH") == ["env:BREACH"]


def test_dispatch_skips_unknown_channel_id(home, monkeypatch):
    _write_channels(home, [_channel("c1", "ntfy", "phone", {"url": "https://ntfy.sh/a"})])
    _write_prefs(home, {"risk_alert": ["gone"]})

    monkeypatch.setattr(
        "selene_space_pulse.notify.send_trade_alert", lambda text: ["env-fallback"]
    )
    monkeypatch.setattr(
        "selene_space_pulse.notify.send_to_targets", lambda t, x: ["should-not-run"]
    )

    # Unknown id was the only subscriber -> no targets -> env fallback.
    assert nd.dispatch("risk_alert", "x") == ["env-fallback"]


def test_dispatch_maps_serverchan_alias(home, monkeypatch):
    _write_channels(
        home, [_channel("c1", "serverchan", "sc", {"sendkey": "SCT1"})]
    )
    _write_prefs(home, {"risk_alert": ["c1"]})

    seen = {}

    def fake(targets, text, noise=None):
        seen["type"] = targets[0].type
        seen["settings"] = targets[0].settings
        return []

    monkeypatch.setattr("selene_space_pulse.notify.send_to_targets", fake)

    nd.dispatch("risk_alert", "x")

    assert seen["type"] == "serverchan3"
    assert seen["settings"] == {"sendkey": "SCT1"}


def test_dispatch_never_raises_when_transport_explodes(home, monkeypatch):
    _write_channels(home, [_channel("c1", "ntfy", "p", {"url": "https://ntfy.sh/a"})])
    _write_prefs(home, {"risk_alert": ["c1"]})

    def boom(targets, text):
        raise RuntimeError("network down")

    monkeypatch.setattr("selene_space_pulse.notify.send_to_targets", boom)

    # Alert path must swallow transport failure, not break the caller.
    assert nd.dispatch("risk_alert", "x") == []


def test_dispatch_never_raises_when_preferences_corrupt(home, monkeypatch):
    _write_channels(home, [])
    (home / "notification_preferences.json").write_text("{not json", encoding="utf-8")

    monkeypatch.setattr(
        "selene_space_pulse.notify.send_trade_alert", lambda text: ["env"]
    )

    assert nd.dispatch("risk_alert", "x") == ["env"]


def test_dispatch_passes_noise_guard_to_send_to_targets(home, monkeypatch):
    _write_channels(home, [_channel("c1", "ntfy", "phone", {"url": "https://ntfy.sh/a"})])
    _write_prefs(home, {"risk_alert": ["c1"]})

    seen = {}

    class FakeNoise:
        pass

    def fake_build_noise():
        seen["built"] = True
        return FakeNoise()

    def fake_send_to_targets(targets, text, noise=None):
        seen["noise"] = noise
        return ["sent"]

    monkeypatch.setattr(nd, "_build_noise", fake_build_noise)
    monkeypatch.setattr(
        "selene_space_pulse.notify.send_to_targets", fake_send_to_targets
    )

    assert nd.dispatch("risk_alert", "x") == ["sent"]
    assert seen["built"] is True
    assert isinstance(seen["noise"], FakeNoise)


def test_dispatch_still_delivers_when_noise_guard_construction_fails(
    home, monkeypatch
):
    _write_channels(home, [_channel("c1", "ntfy", "phone", {"url": "https://ntfy.sh/a"})])
    _write_prefs(home, {"risk_alert": ["c1"]})

    seen = {}

    def fake_send_to_targets(targets, text, noise=None):
        seen["noise"] = noise
        return ["sent"]

    monkeypatch.setattr(
        nd, "_build_noise", lambda: (_ for _ in ()).throw(RuntimeError("db locked"))
    )
    monkeypatch.setattr(
        "selene_space_pulse.notify.send_to_targets", fake_send_to_targets
    )

    # Fail-open: a broken noise guard must not silence the alert.
    assert nd.dispatch("risk_alert", "x") == ["sent"]
    assert seen["noise"] is None


def test_dispatch_reads_quiet_hours_from_preferences(home, monkeypatch):
    _write_channels(home, [_channel("c1", "ntfy", "phone", {"url": "https://ntfy.sh/a"})])
    (home / "notification_preferences.json").write_text(
        json.dumps({"risk_alert": ["c1"], "quiet_hours": "23:00-07:00"}),
        encoding="utf-8",
    )

    seen = {}

    def fake_build():
        seen["quiet_hours"] = nd._load_quiet_hours()
        return None

    monkeypatch.setattr(nd, "_build_noise", fake_build)
    monkeypatch.setattr(
        "selene_space_pulse.notify.send_to_targets",
        lambda targets, text, noise=None: ["sent"],
    )

    assert nd.dispatch("risk_alert", "x") == ["sent"]
    assert seen["quiet_hours"] == "23:00-07:00"
