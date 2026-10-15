"""anomaly_detector 测试: 合成 K 线的窗口计算/insufficient 两条件/未收盘丢弃/单位对齐。"""

from __future__ import annotations

import asyncio

import pytest

from selene_space_news_ingest import anomaly_detector as det
from selene_space_news_ingest.anomaly_detector import _analyze_symbol

_MIN = 60_000  # 1 分钟(毫秒)


def _kline(time_ms, volume=10.0, close=100.0, open_=100.0):
    return {
        "time": time_ms,
        "open": open_,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
    }


def _make_klines(published_at_ms, now_ms, baseline_vol=10.0, window_vols=None):
    """从 published 前基线到 now 的完整合成序列,时间单位全毫秒。"""
    news_minute = published_at_ms // _MIN * _MIN
    klines = []
    t = news_minute - 25 * _MIN
    while t < now_ms - _MIN:  # 最后 1 根当"未收盘"由调用方注入
        vol = baseline_vol
        if window_vols is not None and news_minute <= t < news_minute + 5 * _MIN:
            vol = (
                window_vols[t - news_minute]
                if t - news_minute < len(window_vols)
                else baseline_vol
            )
        klines.append(_kline(t, volume=vol))
        t += _MIN
    return klines


def test_analyze_symbol_full_baseline_and_window(monkeypatch):
    """基线 20 根 vol=10,窗口 5 根 vol=40 -> ratio=4.0 触发(>=3x)。"""
    pub = 100 * _MIN
    now = pub + 30 * _MIN
    window_vols = [40.0] * 5
    klines = _make_klines(pub, now, baseline_vol=10.0, window_vols=window_vols)
    monkeypatch.setattr(det, "fetch_klines_1m", lambda s, lim, sm: klines)

    ratio, triggered, insufficient, p1, p5, p15 = asyncio.run(
        _analyze_symbol("BTC", pub, now)
    )
    assert insufficient is False
    assert ratio == pytest.approx(4.0)
    assert triggered is True


def test_analyze_symbol_below_threshold_not_triggered(monkeypatch):
    pub = 100 * _MIN
    now = pub + 30 * _MIN
    klines = _make_klines(pub, now, baseline_vol=10.0, window_vols=[15.0] * 5)
    monkeypatch.setattr(det, "fetch_klines_1m", lambda s, lim, sm: klines)

    ratio, triggered, insufficient, *_ = asyncio.run(_analyze_symbol("BTC", pub, now))
    assert triggered is False
    assert ratio == pytest.approx(1.5)


def test_insufficient_when_baseline_short(monkeypatch):
    """基线不足 20 根(新闻发生在序列开头) -> insufficient_data=True,ratio=None。"""
    pub = 100 * _MIN
    now = pub + 30 * _MIN
    klines = _make_klines(pub, now)
    klines = [k for k in klines if k["time"] >= pub - 10 * _MIN]  # 只留 10 根基线
    monkeypatch.setattr(det, "fetch_klines_1m", lambda s, lim, sm: klines)

    ratio, triggered, insufficient, p1, p5, p15 = asyncio.run(
        _analyze_symbol("BTC", pub, now)
    )
    assert insufficient is True
    assert ratio is None and triggered is False
    assert p1 is None and p5 is None and p15 is None


def test_insufficient_when_window_short(monkeypatch):
    """窗口不足 5 根(新闻离 now 太近) -> insufficient_data=True。"""
    pub = 100 * _MIN
    now = pub + 3 * _MIN  # 窗口最多 3 根已收盘
    klines = _make_klines(pub, now + 30 * _MIN, window_vols=[50.0] * 5)
    # 截断到 now(未收盘行丢弃后窗口 < 5)
    monkeypatch.setattr(det, "fetch_klines_1m", lambda s, lim, sm: klines)

    ratio, triggered, insufficient, *_ = asyncio.run(_analyze_symbol("BTC", pub, now))
    assert insufficient is True
    assert ratio is None


def test_unclosed_candle_dropped(monkeypatch):
    """未收盘行(time+60s > now)不进基线也不进窗口——用一根假大量不触发异动。"""
    pub = 100 * _MIN
    now = pub + 30 * _MIN
    klines = _make_klines(pub, now, baseline_vol=10.0, window_vols=[10.0] * 5)
    # 注入一根未收盘的巨量 K 线(在 now 时刻,未收盘)
    klines.append(_kline(((now // _MIN) * _MIN), volume=99999.0))
    monkeypatch.setattr(det, "fetch_klines_1m", lambda s, lim, sm: klines)

    ratio, triggered, insufficient, *_ = asyncio.run(_analyze_symbol("BTC", pub, now))
    assert insufficient is False
    assert triggered is False  # 巨量被丢弃


def test_price_change_percentages(monkeypatch):
    """p1/p5/p15 = 基准价(新闻分钟 close)到对应分钟 close 的 pct。"""
    pub = 100 * _MIN
    now = pub + 30 * _MIN
    klines = _make_klines(pub, now, baseline_vol=10.0, window_vols=[10.0] * 5)
    by_time = {k["time"]: k for k in klines}
    by_time[pub]["close"] = 100.0
    by_time[pub + _MIN]["close"] = 101.0
    by_time[pub + 5 * _MIN]["close"] = 105.0
    by_time[pub + 15 * _MIN]["close"] = 90.0
    monkeypatch.setattr(det, "fetch_klines_1m", lambda s, lim, sm: klines)

    _, _, insufficient, p1, p5, p15 = asyncio.run(_analyze_symbol("BTC", pub, now))
    assert insufficient is False
    assert p1 == pytest.approx(1.0)  # 100 -> 101
    assert p5 == pytest.approx(5.0)  # 100 -> 105
    assert p15 == pytest.approx(-10.0)  # 100 -> 90


def test_fetch_klines_1m_maps_symbol_and_params(monkeypatch):
    """BTC -> BTCUSDT,interval=1m,limit 封顶 5000,startTime 直传。"""
    captured = {}

    class _Resp:
        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return [[1710000000000, "1", "2", "0.5", "1.5", "100"]]

    def fake_get(url, params, proxies, timeout):
        captured.update(url=url, params=params)
        return _Resp()

    monkeypatch.setattr(det.requests, "get", fake_get)
    out = det.fetch_klines_1m("btc", 99999, 123456)
    assert captured["params"]["symbol"] == "BTCUSDT"
    assert captured["params"]["interval"] == "1m"
    assert captured["params"]["limit"] == 5000
    assert captured["params"]["startTime"] == 123456
    assert out == [
        {
            "time": 1710000000000,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 100.0,
        }
    ]
