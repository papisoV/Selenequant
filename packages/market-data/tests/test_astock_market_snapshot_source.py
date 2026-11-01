"""Tests for AstockMarketSnapshotSource (whole-market A-share cross-section).

All offline: the HTTP call is mocked and payloads are captured verbatim from a
live ``hq.sinajs.cn`` response (GB18030 body, one ``var hq_str_<code>="...";``
line per symbol). The fixture deliberately spans the boards and the degenerate
rows the parser must drop — a pre-open/halted row (``last == 0``) and an empty
payload — so the "keep only quotable stocks" contract is pinned by real bytes,
not by a hand-written stub.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import patch

import pytest

from selene_space_market.sources.astock_market_snapshot_source import (
    AstockMarketSnapshotSource,
    SnapshotRow,
    _code_to_symbol,
    _in_session_window,
    _sina_prefix,
    _tencent_volume_in_shares,
    parse_snapshot_response,
    parse_tencent_response,
)

# Captured live from hq.sinajs.cn on 2026-10-15. Boards covered: Shanghai main
# (600519), Shenzhen main (000001), ChiNext (300750), STAR (688981), BSE
# halted-zero (bj430047) and BSE active (bj920819); plus an index row and an
# unknown-code empty payload. Field order: name,open,prev_close,last,high,low,...
_LIVE_BODY = "\n".join(
    [
        'var hq_str_sh600519="贵州茅台,1286.000,1286.000,1305.680,1305.900,1282.000,'
        "1305.680,1305.690,1011354,1311293427.000,400,1305.680,200,1305.670,100,"
        "1305.570,200,1305.560,1200,1305.550,200,1305.690,200,1305.790,400,1305.800,"
        '1400,1305.880,1000,1305.890,2026-10-15,09:44:14,00,";',
        'var hq_str_sz000001="平安银行,11.500,11.500,11.440,11.520,11.410,11.430,'
        "11.440,19171006,219588717.840,71900,11.430,164300,11.420,755500,11.410,"
        "2311800,11.400,657500,11.390,6000,11.440,161400,11.450,56600,11.460,91400,"
        '11.470,83200,11.480,2026-10-15,09:44:15,00";',
        'var hq_str_sz300750="宁德时代,274.980,280.120,278.250,279.220,272.000,'
        "278.250,278.270,7794033,2153089357.770,300,278.250,2100,278.210,1100,"
        "278.200,100,278.130,2000,278.120,300,278.270,1500,278.280,300,278.290,4700,"
        '278.300,3200,278.320,2026-10-15,09:44:15,00";',
        'var hq_str_sh688981="中芯国际,99.560,102.550,99.950,101.000,99.100,99.910,'
        "99.950,7992712,799303753.000,25299,99.910,6499,99.900,1461,99.890,2600,"
        "99.880,3433,99.870,555,99.950,114,99.960,972,99.970,5341,99.980,3490,99.990,"
        '2026-10-15,09:44:14,00,";',
        # BSE halted: every price slot 0, status -3. Must be dropped, not zeroed.
        'var hq_str_bj430047="诺思兰德,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0,'
        "0.000,0,0.000,0,0.000,0,0.000,0,0.000,0,0.000,0,0.000,0,0.000,0,0.000,0,"
        '0.000,0,0.000,2026-09-22,11:45:01,-3";',
        # BSE active: real tradeable quote.
        'var hq_str_bj920819="颖泰生物,3.050,3.050,3.020,3.050,3.000,3.010,3.020,'
        "950459,2873513.420,129788,3.010,74502,3.000,57768,2.990,61278,2.980,15500,"
        "2.970,8010,3.020,11151,3.030,78415,3.040,12716,3.050,33686,3.060,2026-10-15,"
        '09:44:11,00,0.0000,0.0000,0,9325914,B,T";',
        # Index row (not a stock) — the parser is agnostic; the universe never
        # contains it in production (get_a_share_list returns stocks only).
        'var hq_str_sh000001="上证指数,3710.6954,3767.2271,3708.7124,3719.8987,'
        "3698.3190,0,0,110805686,166928866719,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,"
        '2026-10-15,09:44:14,00,";',
        'var hq_str_s_badcode="";',
    ]
)


# ---------------------------------------------------------------------------
# Exchange prefix / symbol mapping


def test_sina_prefix_covers_all_boards() -> None:
    assert _sina_prefix("600519") == "sh"  # Shanghai main
    assert _sina_prefix("688981") == "sh"  # STAR
    assert _sina_prefix("000001") == "sz"  # Shenzhen main
    assert _sina_prefix("300750") == "sz"  # ChiNext
    assert _sina_prefix("430047") == "bj"  # BSE
    assert _sina_prefix("830799") == "bj"  # BSE
    assert _sina_prefix("920819") == "bj"  # BSE


def test_code_to_symbol_renders_canonical_suffix() -> None:
    assert _code_to_symbol("600519") == "600519.SH"
    assert _code_to_symbol("000001") == "000001.SZ"
    assert _code_to_symbol("920819") == "920819.BJ"


def test_sina_prefix_rejects_non_ashare() -> None:
    with pytest.raises(ValueError):
        _sina_prefix("BTCUSDT")
    with pytest.raises(ValueError):
        _sina_prefix("60051")  # 5 digits
    with pytest.raises(ValueError):
        _sina_prefix("6005190")  # 7 digits


# ---------------------------------------------------------------------------
# Parsing


def test_parse_snapshot_maps_fields_for_real_rows() -> None:
    rows = parse_snapshot_response(_LIVE_BODY)
    assert set(rows) == {
        "600519.SH",
        "000001.SZ",
        "300750.SZ",
        "688981.SH",
        "920819.BJ",
        "000001.SH",  # the index row — parsed, absent from the production universe
    }
    moutai = rows["600519.SH"]
    assert isinstance(moutai, SnapshotRow)
    assert moutai.name == "贵州茅台"
    assert moutai.open == 1286.000
    assert moutai.prev_close == 1286.000
    assert moutai.last == 1305.680
    assert moutai.high == 1305.900
    assert moutai.low == 1282.000
    assert moutai.volume == 1011354.0
    assert moutai.amount == 1311293427.0
    # 2026-10-15 09:44:14 at +08:00.
    from datetime import datetime, timedelta, timezone

    expected = datetime(2026, 10, 15, 9, 44, 14, tzinfo=timezone(timedelta(hours=8)))
    assert moutai.timestamp == int(expected.timestamp() * 1000)


def test_parse_snapshot_keeps_high_and_low_apart_from_last() -> None:
    """The ladder folds 炸板 (high) as well as 封板 (last); both must survive.

    A parser that collapsed high/low onto last would make every touched-limit
    day look sealed and every sealed day look untouched — the two limit flags
    the ladder distinguishes would become one. This pins them distinct.
    """
    rows = parse_snapshot_response(_LIVE_BODY)
    catl = rows["300750.SZ"]
    assert catl.high == 279.220
    assert catl.low == 272.000
    assert catl.last == 278.250
    assert catl.high != catl.last and catl.low != catl.last


def test_parse_snapshot_drops_halted_zero_price_row() -> None:
    """A halted/pre-open row (last == 0) is omitted, never emitted as zero.

    Emitting it would put a 0.0 price into the cross-section, and the limit
    checks would read it as a -100% move.
    """
    rows = parse_snapshot_response(_LIVE_BODY)
    assert "430047.BJ" not in rows  # halted BSE name


def test_parse_snapshot_drops_empty_payload() -> None:
    rows = parse_snapshot_response('var hq_str_s_badcode="";')
    assert rows == {}


def test_parse_snapshot_drops_malformed_rows() -> None:
    body = 'var hq_str_sh600519="only,three,fields";'
    assert parse_snapshot_response(body) == {}


def test_parse_snapshot_returns_empty_on_format_break() -> None:
    assert parse_snapshot_response("garbage with no hq_str line") == {}


# ---------------------------------------------------------------------------
# Fetch cycle (batching / merging / failure)


class _FakeResp:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text
        self.encoding = "ISO-8859-1"  # overwritten by the source

    def raise_for_status(self) -> None:
        return None


def test_fetch_cycle_batches_and_merges(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, headers: Any = None, timeout: Any = None) -> _FakeResp:
        calls.append(url)
        return _FakeResp(_LIVE_BODY)

    import selene_space_market.sources.astock_market_snapshot_source as mod

    monkeypatch.setattr(mod.requests, "get", fake_get)
    src = AstockMarketSnapshotSource(batch_size=2, throttle_sec=0.0)
    # 5 codes → 3 batches.
    rows = src._fetch_cycle(["600519", "000001", "300750", "688981", "920819"])
    assert len(calls) == 3
    # Every batch returned the same body, so the union is the full fixture.
    assert "600519.SH" in rows and "920819.BJ" in rows


def test_fetch_batch_uses_board_prefixed_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_get(url: str, headers: Any = None, timeout: Any = None) -> _FakeResp:
        seen.append(url)
        return _FakeResp(_LIVE_BODY)

    import selene_space_market.sources.astock_market_snapshot_source as mod

    monkeypatch.setattr(mod.requests, "get", fake_get)
    src = AstockMarketSnapshotSource(throttle_sec=0.0)
    src._fetch_cycle(["600519", "920819"])
    assert "sh600519" in seen[0]
    assert "bj920819" in seen[0]


def test_fetch_cycle_logs_dropped_batch_not_silent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A batch that fails after retry must be logged, not swallowed.

    The repository's existing clist caller does ``except: return EMPTY`` — the
    silent-degradation pattern this source exists to avoid. A whole-market
    source that quietly returns partial data reads as "the market is quiet".
    """
    import selene_space_market.sources.astock_market_snapshot_source as mod

    def boom(*a: Any, **k: Any) -> _FakeResp:
        raise ConnectionError("upstream down")

    monkeypatch.setattr(mod.requests, "get", boom)
    src = AstockMarketSnapshotSource(batch_size=2, throttle_sec=0.0)
    with caplog.at_level(logging.WARNING):
        rows = src._fetch_cycle(["600519", "000001"])
    assert rows == {}
    assert any("failed after retry" in r.getMessage() for r in caplog.records)


def test_fetch_batch_retries_once_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import selene_space_market.sources.astock_market_snapshot_source as mod

    attempts = {"n": 0}

    def flaky(*a: Any, **k: Any) -> _FakeResp:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("transient")
        return _FakeResp(_LIVE_BODY)

    monkeypatch.setattr(mod.requests, "get", flaky)
    src = AstockMarketSnapshotSource(throttle_sec=0.0)
    rows = src._fetch_cycle(["600519"])
    assert attempts["n"] == 2  # one failure, one success
    assert "600519.SH" in rows


def test_one_dead_batch_does_not_abort_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single failing batch leaves the rest of the market intact.

    The dead batch is placed *first* on purpose: with it last, aborting the
    sweep on failure and skipping only that batch produce identical results, so
    the test would pass either way. Leading with the failure forces the
    surviving batch to be fetched after it.
    """
    import selene_space_market.sources.astock_market_snapshot_source as mod

    def by_code(url: str, **k: Any) -> _FakeResp:
        # Answer only with the line for the code(s) this batch asked for, so a
        # surviving batch cannot silently supply the dead batch's symbol.
        wanted = url.split("list=")[1].split(",")
        lines = [
            ln
            for ln in _LIVE_BODY.splitlines()
            if any(ln.startswith(f'var hq_str_{w}="') for w in wanted)
        ]
        if "sz000001" in wanted:
            raise ConnectionError("this batch is down")
        return _FakeResp("\n".join(lines))

    monkeypatch.setattr(mod.requests, "get", by_code)
    src = AstockMarketSnapshotSource(batch_size=1, throttle_sec=0.0)
    rows = src._fetch_cycle(["000001", "600519"])
    assert "600519.SH" in rows  # batch after the dead one was still fetched
    assert "000001.SZ" not in rows


# ---------------------------------------------------------------------------
# Pull-only contract


def test_registers_as_market_snapshot() -> None:
    src = AstockMarketSnapshotSource()
    assert src.config.exchange == "astock"
    assert src.config.market_type == "market_snapshot"


def test_ticker_kline_orderbook_are_no_ops() -> None:
    src = AstockMarketSnapshotSource()
    assert src.get_ticker("600519.SH") is None
    assert src.get_kline("600519.SH") is None
    assert src.get_klines("600519.SH") == []
    assert src.get_orderbook("600519.SH") is None


def test_on_ticker_is_inert() -> None:
    """The bus-wired ticker callback must never be invoked.

    ``register_source`` wires ``on_ticker`` to ``event_bus.publish`` for every
    source; a whole-market cross-section published there would flood the shared
    bus the anomaly detector reads. Publishing is not a thing this source does.
    """
    src = AstockMarketSnapshotSource()
    received: list[Any] = []

    def cb(event: Any) -> None:
        received.append(event)
        raise AssertionError("snapshot source must not publish tickers")

    src.on_ticker(cb)
    # Even after a successful cycle, nothing is emitted.
    src._rows = parse_snapshot_response(_LIVE_BODY)
    assert received == []


def test_get_market_rows_is_the_read_face() -> None:
    src = AstockMarketSnapshotSource()
    assert src.get_market_rows() == {}
    src._rows = parse_snapshot_response(_LIVE_BODY)
    assert "600519.SH" in src.get_market_rows()


def test_subscribe_seeds_the_universe_and_validates() -> None:
    src = AstockMarketSnapshotSource()
    asyncio.run(src.subscribe("600519"))
    asyncio.run(src.subscribe("000001.SZ"))  # canonical symbol accepted too
    assert src.get_symbols() == ["000001.SZ", "600519.SH"]
    with pytest.raises(ValueError):
        asyncio.run(src.subscribe("BTC/USDT"))


def test_set_universe_replaces_wholesale() -> None:
    src = AstockMarketSnapshotSource()
    asyncio.run(src.subscribe("600519"))
    src.set_universe(["000001", "300750"])
    assert src.get_symbols() == ["000001.SZ", "300750.SZ"]


def test_set_universe_starts_polling_when_already_running() -> None:
    """The production seeding path: start() first, set_universe() after.

    The composition layer starts the service (which starts the source with the
    still-empty universe) and only later seeds the code list. If set_universe
    did not spawn the poll loop, the source would sit registered-but-inert —
    exactly the "部署了≠在跑" failure the seed loop exists to avoid.
    """

    async def scenario() -> None:
        src = AstockMarketSnapshotSource()
        await src.start()  # empty universe → no poll task yet
        assert src._poll_task is None
        src.set_universe(["600519"])
        assert src._poll_task is not None
        await src.stop()

    asyncio.run(scenario())


def test_set_universe_does_not_start_polling_before_start() -> None:
    # Seeding before start() must not spawn a task; start() picks it up.
    src = AstockMarketSnapshotSource()
    src.set_universe(["600519"])
    assert src._poll_task is None


# ---------------------------------------------------------------------------
# Poll loop


async def _run_passes(src: AstockMarketSnapshotSource, *, passes: int = 1) -> None:
    src._running = True
    done = {"n": 0}
    real_sleep = asyncio.sleep

    async def _sleep(_sec: float) -> None:
        done["n"] += 1
        if done["n"] >= passes:
            src._running = False
        await real_sleep(0)

    import selene_space_market.sources.astock_market_snapshot_source as mod

    with patch.object(mod.asyncio, "sleep", side_effect=_sleep):
        task = asyncio.create_task(src._poll_loop())
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.CancelledError:
            pass


def test_poll_loop_populates_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    import selene_space_market.sources.astock_market_snapshot_source as mod

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResp(_LIVE_BODY))
    src = AstockMarketSnapshotSource(throttle_sec=0.0)
    src.set_universe(["600519", "000001"])

    asyncio.run(_run_passes(src, passes=2))

    rows = src.get_market_rows()
    assert "600519.SH" in rows and "000001.SZ" in rows


def test_poll_loop_replaces_wholesale_dropping_absent_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symbol absent from a cycle must not survive as a stale row.

    The cross-section is the ladder's whole input; a lingering row from a
    previous cycle would fold a halted name into today's breadth.
    """
    import selene_space_market.sources.astock_market_snapshot_source as mod

    full = _LIVE_BODY
    # Second cycle: only 000001 answers (600519's batch dies).
    only_sz = "\n".join(
        ln for ln in _LIVE_BODY.splitlines() if "000001" in ln and "sh000001" not in ln
    )
    bodies = [full, only_sz]

    def fake_get(url: str, **k: Any) -> _FakeResp:
        return _FakeResp(bodies.pop(0) if bodies else only_sz)

    monkeypatch.setattr(mod.requests, "get", fake_get)
    src = AstockMarketSnapshotSource(throttle_sec=0.0)
    src.set_universe(["600519", "000001"])

    asyncio.run(_run_passes(src, passes=2))
    rows = src.get_market_rows()
    assert "000001.SZ" in rows
    assert "600519.SH" not in rows  # dropped, not stale


def test_poll_loop_warns_once_when_cycle_yields_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import selene_space_market.sources.astock_market_snapshot_source as mod

    monkeypatch.setattr(
        mod.requests, "get", lambda *a, **k: _FakeResp('var hq_str_other="x";')
    )
    src = AstockMarketSnapshotSource(throttle_sec=0.0)
    src.set_universe(["600519"])

    with caplog.at_level(logging.WARNING):
        asyncio.run(_run_passes(src, passes=3))

    warnings = [r for r in caplog.records if "no quotes for" in r.getMessage()]
    assert len(warnings) == 1  # once per episode, not once per cycle


def test_poll_task_cleared_when_loop_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    import selene_space_market.sources.astock_market_snapshot_source as mod

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _FakeResp(_LIVE_BODY))
    src = AstockMarketSnapshotSource(throttle_sec=0.0)
    src.set_universe(["600519"])

    def boom_interval() -> float:
        raise RuntimeError("interval failed")

    monkeypatch.setattr(src, "_interval_for_now", boom_interval)

    async def scenario() -> None:
        task = asyncio.create_task(src._poll_loop())
        src._poll_task = task
        with pytest.raises(RuntimeError):
            await task
        assert src._poll_task is None

    src._running = True
    asyncio.run(scenario())


def test_off_hours_interval_used_outside_session() -> None:
    src = AstockMarketSnapshotSource()
    with patch(
        "selene_space_market.sources.astock_market_snapshot_source.datetime"
    ) as fake_dt:
        from datetime import datetime as _dt

        fake_dt.now.return_value = _dt(2026, 10, 15, 22, 0)
        assert src._interval_for_now() == src._off_hours_interval


def test_in_session_window_bounds() -> None:
    from datetime import datetime as _dt

    assert _in_session_window(_dt(2026, 10, 15, 9, 20))  # call auction
    assert _in_session_window(_dt(2026, 10, 15, 10, 0))
    assert _in_session_window(_dt(2026, 10, 15, 15, 0))
    assert not _in_session_window(_dt(2026, 10, 15, 9, 0))  # too early
    assert not _in_session_window(_dt(2026, 10, 15, 15, 30))
    assert not _in_session_window(_dt(2026, 10, 11, 10, 0))  # 2026-10-11 is Sunday


# ---------------------------------------------------------------------------
# 腾讯 fallback (degradation path)
#
# Captured live from qt.gtimg.cn on 2026-10-15, same boards as the Sina fixture
# plus the suspended BSE name (bj430047). The 腾讯 payload is `~`-separated
# inside `v_<prefix><code>="...";` assignments — a different format from Sina's,
# which is why it gets its own parser rather than a shared field map.
_TENCENT_BODY = ";".join(
    [
        'v_sh600519="1~贵州茅台~600519~1309.88~1286.00~1286.00~46878~26036~20800~1308.91~1~1308.90~4~1308.86~2~1308.85~1~1308.52~1~1309.88~8~1309.89~2~1309.90~3~1309.95~1~1309.96~1~~20261015143535~23.88~1.86~1317.76~1282.00~1309.88/46878/6120167074~46878~612017~0.37~20.11~~1317.76~1282.00~2.78~16374.57~16374.57~6.52~1414.60~1157.40~1.83~-6~1305.57~18.39~19.89~~~0.07~612016.7074~0.0000~0~   A~GP-A~-2.91~4.31~3.97~32.41~27.30~1539.98~1151.01~4.69~1.47~4.70~1250081601~1250081601~-25.00~-5.29~1250081601~~~-7.10~0.14~~CNY~0~___D__F__N~1310.00~-265~"',
        'v_sz000001="51~平安银行~000001~11.35~11.50~11.50~1278146~467625~810475~11.34~490~11.33~914~11.32~1620~11.31~911~11.30~1344~11.35~136~11.36~2501~11.37~1705~11.38~1798~11.39~2733~~20261015143536~-0.15~-1.30~11.52~11.26~11.35/1278146/1452948999~1278146~145295~0.66~5.07~~11.52~11.26~2.26~2202.55~2202.57~0.48~12.65~10.35~1.27~-3594~11.37~4.29~5.17~~~0.18~145294.8999~0.0000~0~   A~GP-A~5.08~-3.65~5.37~7.93~0.72~11.89~9.74~-0.01~-0.88~7.17~19405684991~19405918198~-25.40~4.50~19405684991~~~5.18~0.09~~CNY~0~~11.25~13646~"',
        'v_sz300750="51~宁德时代~300750~275.60~280.12~274.98~349863~204458~145317~275.60~1~275.56~1~275.54~1~275.49~5~275.47~1~275.76~2~275.77~16~275.80~10~275.81~4~275.85~10~~20261015143536~-4.52~-1.61~279.85~272.00~275.60/349863/9633388877~349863~963339~0.82~15.00~~279.85~272.00~2.80~11742.26~12752.70~3.42~336.14~224.10~0.94~-33~275.35~14.73~17.66~~~0.97~963338.8877~0.0000~0~ A A~GP-A-CYB~-23.22~-3.89~2.98~22.41~8.03~467.35~272.00~-8.44~-18.18~-25.83~4260617083~4627249686~-64.71~-21.41~4260617083~~~-23.65~0.02~~CNY~0~~275.40~21~"',
        'v_sh688981="1~中芯国际~688981~102.14~102.55~99.56~37179050~19464668~17711052~102.14~8~102.08~21~102.07~24~102.06~62~102.05~94~102.16~423~102.17~108~102.18~1~102.19~19~102.20~3~~20261015143535~-0.41~-0.40~102.65~99.10~102.14/37179050/3731128837~37179050~373113~1.86~121.34~~102.65~99.10~3.46~2043.41~8745.62~5.10~123.06~82.04~1.19~-345~100.36~97.88~173.50~~~1.99~373112.8837~0.0000~0~A RA~GP-A-KCB~-16.84~-4.61~0.00~4.20~2.64~176.34~91.20~-15.86~-15.24~-33.52~2000593909~8562381909~-45.22~-17.38~2000593909~~~-21.16~0.80~~CNY~0~___D__F__NY~102.02~72~100"',
        'v_bj920819="62~颖泰生物~920819~2.96~3.05~3.05~41162~12715~28446~2.95~1436~2.94~930~2.93~542~2.92~1500~2.91~683~2.96~929~2.97~574~2.98~1224~2.99~52~3.00~837~~20261015143511~-0.09~-2.95~3.05~2.95~2.96/41162/12308554~41162~1230.86~0.34~-13.61~~3.05~2.95~3.28~35.86~36.28~0.76~3.96~2.14~1.06~1475~2.99~317.43~-14.12~~~~1230.8554~0.0000~0~ ~GP~-23.51~-2.95~0.00~-5.60~-2.44~4.49~2.52~-2.95~-13.20~10.04~1211625000~1225800000~16.94~-26.91~1211625000~~~-30.02~0.00~~CNY~4~NBFND~0.00~0"',
        # Suspended BSE name: 腾讯 keeps the previous close in the last-price
        # slot (8.17) with zero volume — unlike Sina, which zeroes the price.
        'v_bj430047="62~诺思兰德~430047~8.17~8.17~0.00~0~0~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~~20261015090000~0.00~0.00~0.00~0.00~8.17/0/0~0~0.00~0.00~-35.46~C~0.00~0.00~0.00~14.71~22.41~9.03~-1~-1~0.00~0~0.00~-38.40~-41.71~~~~0.0000~0.0000~0~ ~GP~0.00~0.00~0.00~-25.45~-12.62~~~0.00~0.00~0.00~180106427~274271974~~0.00~180106427~~~0.00~0.00~~CNY~4~~0.00~0"',
    ]
)


def test_tencent_volume_helper_normalises_per_board() -> None:
    """STAR reports shares; every other board reports 手.

    A flat ``* 100`` (the naive reading of the payload) inflates STAR — ~580
    symbols — by 100x. Pinned directly so the board branch cannot be deleted.
    """
    assert _tencent_volume_in_shares("600519", 46878.0) == 4687800.0
    assert _tencent_volume_in_shares("000001", 1278146.0) == 127814600.0
    assert _tencent_volume_in_shares("300750", 349863.0) == 34986300.0
    assert _tencent_volume_in_shares("920819", 41162.0) == 4116200.0
    assert _tencent_volume_in_shares("688981", 37179050.0) == 37179050.0  # shares
    assert _tencent_volume_in_shares("689009", 12345.0) == 12345.0  # STAR CDR


def test_parse_tencent_normalises_to_sina_units() -> None:
    """腾讯's row must land in the *same* units as Sina's, board for board.

    The cross-section is consumed without knowing which endpoint produced it, so
    a units mismatch corrupts breadth silently — and 腾讯 differs from Sina on
    two independent units (volume: 手→shares, amount: 万元→元) with a
    board-dependent twist on the first.

    The check is the turnover identity ``amount / volume ∈ [low, high]``: volume
    weighted by price gives turnover, so the implied average trade price must sit
    inside the day's range. That is exactly true regardless of intraday drift
    (unlike ``volume * last``, which is only true if the stock never moved), and
    it fails loudly by 100x on either unit — a wrong volume factor or a missing
    万元→元 factor puts the implied price far outside ``[low, high]``.
    """
    rows = parse_tencent_response(_TENCENT_BODY)
    assert set(rows) == {
        "600519.SH",
        "000001.SZ",
        "300750.SZ",
        "688981.SH",
        "920819.BJ",
    }
    moutai = rows["600519.SH"]
    assert moutai.name == "贵州茅台"
    assert moutai.last == 1309.88
    assert moutai.prev_close == 1286.00
    assert moutai.open == 1286.00
    assert moutai.high == 1317.76
    assert moutai.low == 1282.00
    # 手 → shares, and 万元 → 元.
    assert moutai.volume == 46878.0 * 100
    assert moutai.amount == 612017.0 * 1.0e4

    for sym in ("600519.SH", "000001.SZ", "300750.SZ", "688981.SH", "920819.BJ"):
        row = rows[sym]
        implied_avg_price = row.amount / row.volume
        assert row.low <= implied_avg_price <= row.high, (
            f"{sym}: implied avg price {implied_avg_price} outside "
            f"[{row.low}, {row.high}] — volume/amount units are off"
        )


def test_parse_tencent_timestamp_is_cst_epoch_ms() -> None:
    rows = parse_tencent_response(_TENCENT_BODY)
    # 2026-10-15 14:35:35 at +08:00.
    assert rows["600519.SH"].timestamp == 1792046135000


def test_tencent_timestamp_is_host_tz_independent() -> None:
    """The stamped epoch must not move with the host timezone.

    On a UTC+8 dev box ``datetime.strptime(...).timestamp()`` (naive → local)
    equals the Beijing value, so the assertion above passes even with the naive
    bug present. This fallback would run on a UTC cloud server, where naive
    shifts every row 8h. Forcing a child process into UTC makes the two
    disagree: the naive stamp becomes 2026-10-15 14:35:35 *UTC* = 1792074935000,
    the correct aware stamp stays 1792046135000.  (Same idiom as
    test_poll_resilience.test_parse_rows_is_host_tz_independent.)
    """
    import os
    import subprocess
    import sys

    snippet = (
        "from selene_space_market.sources.astock_market_snapshot_source import "
        "_tencent_timestamp_to_epoch_ms;"
        "print(_tencent_timestamp_to_epoch_ms('20261015143535'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", snippet],
        env={**os.environ, "TZ": "UTC"},
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    # 2026-10-15 14:35:35 Beijing = 06:35:35 UTC = 1792046135000 ms.
    assert out.stdout.strip() == "1792046135000"


def test_parse_tencent_drops_halted_on_zero_volume_not_zero_price() -> None:
    """腾讯 signals a halted name with zero volume, not a zero price.

    腾讯 leaves the last price at the previous close (measured 8.17 for the
    suspended bj430047), so a ``last <= 0`` filter — correct for Sina — would
    emit a stale price as today's close. Volume is the not-quotable signal here.
    """
    rows = parse_tencent_response(_TENCENT_BODY)
    assert "430047.BJ" not in rows
    # The row really was present in the payload with a non-zero last price —
    # so this is not passing by the code being absent.
    assert "430047" in _TENCENT_BODY and "~8.17~" in _TENCENT_BODY


def test_parse_tencent_drops_short_and_garbage() -> None:
    assert parse_tencent_response('v_sh600519="1~name~600519~1.0";') == {}
    assert parse_tencent_response("no assignment here") == {}
    assert parse_tencent_response("") == {}


# -- cycle-level degradation ------------------------------------------------


def test_cycle_degrades_to_tencent_when_sina_returns_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A wholly-empty Sina cycle falls back to 腾讯 and says so once."""
    import selene_space_market.sources.astock_market_snapshot_source as mod

    sina_prefix = mod._SINA_HQ_URL.split("{")[0]

    def fake_get(url: str, **k: Any) -> _FakeResp:
        if url.startswith(sina_prefix):
            return _FakeResp('var hq_str_sh600519="";')  # empty payload
        # Answer only the codes this batch asked for (the url carries them).
        wanted = [w[2:] for w in url.split("q=")[1].split(",")]
        lines = [
            ln for ln in _TENCENT_BODY.split(";") if any(f"~{c}~" in ln for c in wanted)
        ]
        return _FakeResp(";".join(lines))

    monkeypatch.setattr(mod.requests, "get", fake_get)
    src = AstockMarketSnapshotSource(throttle_sec=0.0)
    with caplog.at_level(logging.WARNING):
        rows = src._fetch_cycle(["600519", "000001", "688981"])
    assert set(rows) == {"600519.SH", "000001.SZ", "688981.SH"}
    assert any("degrading this cycle to 腾讯" in r.getMessage() for r in caplog.records)


def test_cycle_does_not_degrade_on_partial_sina_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial Sina success is real data — do not overwrite it with 腾讯.

    Only a *wholly* empty cycle is an outage. If one batch answered, those rows
    are trustworthy; degrading would discard them for no reason.
    """
    import selene_space_market.sources.astock_market_snapshot_source as mod

    tencent_calls = {"n": 0}

    def fake_get(url: str, **k: Any) -> _FakeResp:
        if url.startswith(mod._SINA_HQ_URL.split("{")[0]):
            wanted = url.split("list=")[1].split(",")
            if "sz000001" in wanted:
                raise ConnectionError("this batch is down")
            lines = [
                ln
                for ln in _LIVE_BODY.splitlines()
                if any(ln.startswith(f'var hq_str_{w}="') for w in wanted)
            ]
            return _FakeResp("\n".join(lines))
        tencent_calls["n"] += 1
        return _FakeResp(_TENCENT_BODY)

    monkeypatch.setattr(mod.requests, "get", fake_get)
    src = AstockMarketSnapshotSource(batch_size=1, throttle_sec=0.0)
    rows = src._fetch_cycle(["000001", "600519"])
    assert "600519.SH" in rows  # the batch that answered
    assert "000001.SZ" not in rows  # the batch that died; not backfilled
    assert tencent_calls["n"] == 0  # 腾讯 never touched


def test_degraded_warning_is_once_per_episode(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A standing outage logs one line, not one per 8s cycle."""
    import selene_space_market.sources.astock_market_snapshot_source as mod

    def fake_get(url: str, **k: Any) -> _FakeResp:
        if url.startswith(mod._SINA_HQ_URL.split("{")[0]):
            return _FakeResp('var hq_str_sh600519="";')
        return _FakeResp(_TENCENT_BODY)

    monkeypatch.setattr(mod.requests, "get", fake_get)
    src = AstockMarketSnapshotSource(throttle_sec=0.0)
    with caplog.at_level(logging.WARNING):
        src._fetch_cycle(["600519"])
        src._fetch_cycle(["600519"])
    degraded = [r for r in caplog.records if "degrading this cycle" in r.getMessage()]
    assert len(degraded) == 1


def test_degraded_flag_resets_after_sina_recovers(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Recovery clears the once-flag, so the *next* outage logs again."""
    import selene_space_market.sources.astock_market_snapshot_source as mod

    healthy = {"on": False}

    def fake_get(url: str, **k: Any) -> _FakeResp:
        if url.startswith(mod._SINA_HQ_URL.split("{")[0]):
            return _FakeResp(_LIVE_BODY if healthy["on"] else 'var hq_str_x="";')
        return _FakeResp(_TENCENT_BODY)

    monkeypatch.setattr(mod.requests, "get", fake_get)
    src = AstockMarketSnapshotSource(throttle_sec=0.0)
    with caplog.at_level(logging.WARNING):
        src._fetch_cycle(["600519"])  # outage 1 → degrade
        healthy["on"] = True
        src._fetch_cycle(["600519"])  # recovered
        healthy["on"] = False
        src._fetch_cycle(["600519"])  # outage 2 → must degrade again
    degraded = [r for r in caplog.records if "degrading this cycle" in r.getMessage()]
    assert len(degraded) == 2


# ---------------------------------------------------------------------------
# Level-1 depth (the sealed-at-limit signal)


def test_parse_snapshot_reads_level1_depth() -> None:
    """Sina's best bid/ask quantities ride along in the quote payload.

    Sina packs depth as ``(vol, price)`` pairs — buy1..5 from field 10, sell1..5
    from field 20 — so the *volume* of level 1 sits at 10 and 20. Reading the
    adjacent slot instead would yield a price where a quantity is expected, and
    the sealed check (``ask1 == 0``) would then be comparing a price to zero.
    """
    rows = parse_snapshot_response(_LIVE_BODY)
    moutai = rows["600519.SH"]
    assert moutai.bid1_vol == 400.0  # field 10
    assert moutai.ask1_vol == 200.0  # field 20


def test_parse_snapshot_empty_depth_slot_is_none_not_zero() -> None:
    """An empty depth slot yields ``None``, never a fabricated ``0``.

    ``0`` is the sealed signal; a payload whose depth slots are blank (a
    degraded or partial answer) must not be read as a sealed board.
    """
    fields = (
        [
            "贵州茅台",
            "1286.000",
            "1286.000",
            "1305.680",
            "1305.900",
            "1282.000",
            "1305.680",
            "1305.690",
            "1011354",
            "1311293427.000",
        ]
        + [""] * 20
        + ["2026-10-15", "09:44:14", "00"]
    )
    body = f'var hq_str_sh600519="{",".join(fields)}";'
    rows = parse_snapshot_response(body)
    assert "600519.SH" in rows
    assert rows["600519.SH"].bid1_vol is None
    assert rows["600519.SH"].ask1_vol is None


def test_parse_snapshot_malformed_depth_does_not_drop_row() -> None:
    """One bad depth slot must not cost an otherwise-quotable row."""
    fields = (
        [
            "贵州茅台",
            "1286.000",
            "1286.000",
            "1305.680",
            "1305.900",
            "1282.000",
            "1305.680",
            "1305.690",
            "1011354",
            "1311293427.000",
        ]
        + ["x"] * 20
        + ["2026-10-15", "09:44:14", "00"]
    )
    body = f'var hq_str_sh600519="{",".join(fields)}";'
    rows = parse_snapshot_response(body)
    assert "600519.SH" in rows
    assert rows["600519.SH"].bid1_vol is None
    assert rows["600519.SH"].ask1_vol is None


def test_parse_snapshot_explicit_zero_depth_reads_zero() -> None:
    """An explicit ``0`` reads ``0.0`` — the sealed signal, distinct from absent.

    Shape of a real sealed 涨停: a large resting buy at the limit price and an
    empty sell side (``sell1_vol == 0``).
    """
    depth = ["4941181", "14.750"] + ["0", "0.000"] * 4  # buy1..5
    depth += ["0", "0.000"] + ["0", "0.000"] * 4  # sell1..5
    body = (
        'var hq_str_sz000099="测试股,13.410,13.410,14.750,14.750,14.740,'
        "14.750,0.000,60000000,60000000.000,"
        + ",".join(depth)
        + ',2026-10-16,14:30:00,00";'
    )
    rows = parse_snapshot_response(body)
    assert rows["000099.SZ"].bid1_vol == 4941181.0
    assert rows["000099.SZ"].ask1_vol == 0.0


def test_parse_tencent_reads_level1_depth_in_shares() -> None:
    """腾讯 depth is ``price~vol`` (the transpose of Sina's) and board-dependent.

    腾讯 quotes level quantities in 手 — except STAR, which reports shares — so
    the same 100x trap that governs ``volume`` governs depth. Reading it raw
    would leave every non-zero depth 100x small while the sealed ``0`` still
    looked right, hiding the bug exactly where it matters least.
    """
    rows = parse_tencent_response(_TENCENT_BODY)
    moutai = rows["600519.SH"]
    assert moutai.bid1_vol == 1.0 * 100  # 手 → shares
    assert moutai.ask1_vol == 8.0 * 100
    star = rows["688981.SH"]
    assert star.bid1_vol == 8.0  # STAR already in shares
    assert star.ask1_vol == 423.0


def test_parse_tencent_depth_agrees_with_volume_unit_convention() -> None:
    """Depth and volume must use one unit convention, board for board.

    If the two diverged, a深度-derived 封单量 could not be compared with the
    day's volume — the ratio would be off by 100x for exactly one board.
    """
    rows = parse_tencent_response(_TENCENT_BODY)
    for sym in rows:
        row = rows[sym]
        assert row.bid1_vol is not None and row.ask1_vol is not None
        # Depth is a tiny fraction of the day's volume, never 100x more.
        assert row.bid1_vol <= row.volume * 10
