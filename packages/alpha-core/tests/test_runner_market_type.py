"""market_type partition filtering for the klines loader (defect A).

Verifies :func:`~selene_space_factor_research.mining.runner.load_klines`
filters to a single market partition instead of mixing spot+futures rows, and
that the CLI ``--market-type`` flag threads through.
"""

from __future__ import annotations

import sqlite3

from selene_space_factor_research.mining import runner


def _write_db(
    path, *, symbols=("BTC/USDT",), markets=("futures", "spot"), n_bars: int = 20
) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE klines ("
        " symbol TEXT, exchange TEXT, market_type TEXT, interval TEXT,"
        " timestamp INTEGER, open REAL, high REAL, low REAL, close REAL,"
        " volume REAL)"
    )
    rows = []
    ts = 1_700_000_000_000
    for sym in symbols:
        for mt in markets:
            for i in range(n_bars):
                rows.append(
                    (
                        sym,
                        "binance",
                        mt,
                        "4h",
                        ts + i * 3_600_000,
                        100.0,
                        101.0,
                        99.0,
                        100.5,
                        1000.0,
                    )
                )
    conn.executemany("INSERT INTO klines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_load_klines_defaults_to_futures_only(tmp_path) -> None:
    db = tmp_path / "klines.db"
    _write_db(db)

    data = runner.load_klines(db, ["BTC/USDT"], "4h", max_bars=100)
    df = data["BTC/USDT"]
    # 20 futures rows, 0 spot rows (default market_type="futures").
    assert len(df) == 20
    assert df.index.is_monotonic_increasing


def test_load_klines_explicit_spot(tmp_path) -> None:
    db = tmp_path / "klines.db"
    _write_db(db)

    data = runner.load_klines(db, ["BTC/USDT"], "4h", max_bars=100, market_type="spot")
    assert len(data["BTC/USDT"]) == 20


def test_load_klines_dedupe_keeps_newest_per_timestamp(tmp_path) -> None:
    """Same-partition duplicate timestamps still collapse via the loader dedupe."""
    db = tmp_path / "klines.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE klines ("
        " symbol TEXT, exchange TEXT, market_type TEXT, interval TEXT,"
        " timestamp INTEGER, open REAL, high REAL, low REAL, close REAL,"
        " volume REAL)"
    )
    ts = 1_700_000_000_000
    for close in (1.0, 2.0):
        conn.execute(
            "INSERT INTO klines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "BTC/USDT",
                "binance",
                "futures",
                "4h",
                ts,
                close,
                close,
                close,
                close,
                close,
            ),
        )
    conn.commit()
    conn.close()

    data = runner.load_klines(db, ["BTC/USDT"], "4h", max_bars=10)
    assert len(data["BTC/USDT"]) == 1


def test_cli_market_type_flag_passes_through(tmp_path, capsys, monkeypatch) -> None:
    db = tmp_path / "klines.db"
    _write_db(db)
    # Run only the data-loading slice of main(): a huge search budget would
    # otherwise run the full GA. main() fails fast on --symbols with no rows;
    # we monkeypatch run_search to keep the test cheap and assert threading.
    # **kwargs: run_search grows optional keyword arguments over time
    # (seed_formulas / blocked_hashes / …). A signature-exact lambda breaks the
    # moment one is added, for a reason unrelated to what this test verifies
    # (that --market-type threads through to the data loader).
    monkeypatch.setattr(
        runner,
        "run_search",
        lambda data, ret, config, progress=None, **kwargs: type(
            "Result",
            (),
            {
                "top_formulas": [],
                "seed": config.seed,
                "evaluations_used": 0,
                "generations": 0,
                "stopped_reason": "monkeypatched",
            },
        )(),
    )

    code = runner.main(
        [
            "--symbols",
            "BTC/USDT",
            "--interval",
            "4h",
            "--db",
            str(db),
            "--budget",
            "1",
            "--seed",
            "42",
            "--market-type",
            "spot",
            # --out defaults to the real internal-archive/ dir; without this the run
            # writes a synthetic top_formulas.json over the checked-in data.
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "[runner] BTC/USDT: 20 unique bars" in out


def test_cli_wrong_market_type_no_rows(tmp_path) -> None:
    """A partition with no rows raises ValueError, not silent empty panels."""
    db = tmp_path / "klines.db"
    _write_db(db, markets=("futures",))

    try:
        runner.load_klines(db, ["BTC/USDT"], "4h", max_bars=10, market_type="spot")
    except ValueError as exc:
        assert "no klines" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing partition")
