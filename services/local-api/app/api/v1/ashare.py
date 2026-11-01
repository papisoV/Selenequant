"""A-share factor mining and regime detection endpoints.

Error contract (research-data lens): empty upstream data -> 404 NOT_FOUND,
data-source failures -> 502 EXCHANGE_ERROR. No 200-with-error-dict.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

from selene_space_types.errors import ErrorCode, SeleneSpaceError

if TYPE_CHECKING:  # pragma: no cover - annotation-only imports
    import pandas as pd

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ashare", tags=["ashare"])


def _not_found(message: str) -> SeleneSpaceError:
    return SeleneSpaceError(code=ErrorCode.NOT_FOUND, message=message)


def _upstream(exc: Exception) -> SeleneSpaceError:
    return SeleneSpaceError(code=ErrorCode.EXCHANGE_ERROR, message=str(exc))


def build_ashare_panel(
    codes: list[str],
    *,
    start_date: str,
    factor_set: str,
    index_daily: "pd.DataFrame",
) -> tuple["pd.DataFrame", list[str], list[str]]:
    """Fetch per-symbol klines and assemble the backtest factor panel.

    Pure data construction shared by POST /ashare/backtest (and reusable by
    future panel consumers). ``index_daily`` is fetched by the caller and
    passed in — it feeds RESID_VOL_60D here and the caller's regime
    detection from the same frame.

    Returns ``(panel, failed_codes, fund_factor_names)``:
    - failed_codes: symbols excluded (empty/too-short klines or fetch error)
    - fund_factor_names: derived fundamental factor columns; non-empty only
      for factor_set="technical_fundamental" with usable data. Callers MUST
      append these to their factor list, otherwise the technical_fundamental
      path silently loses every fundamental factor.

    Raises SeleneSpaceError(NOT_FOUND) when no symbol yields factor data.
    """
    import pandas as pd

    from app.services.ashare_backfill import fetch_symbol_with_retry
    from selene_space_ashare.data_fetcher import get_daily_klines
    from selene_space_ashare.factor_calc import calculate_technical_factors
    from selene_space_ashare.panel_factors import (
        compute_crowding_score,
        compute_resid_vol_60d,
    )

    all_rows = []
    failed_codes = []
    for code in codes:
        klines, fetch_error = fetch_symbol_with_retry(
            lambda c: get_daily_klines(c, start_date=start_date), code
        )
        if klines.empty or len(klines) < 60:
            failed_codes.append(
                f"{code}({'error: ' + str(fetch_error) if fetch_error else 'no data'})"
            )
            continue
        factors = calculate_technical_factors(klines)
        # calculate_technical_factors drops the price columns; keep close
        # so the panel-level forward-return block below can use it
        factors["close"] = klines["close"].values
        factors["symbol"] = code
        for col in ("turnover", "amount"):
            if col in klines.columns:
                factors[col] = klines[col].values
        all_rows.append(factors)

    if not all_rows:
        raise _not_found("No factor data available")

    panel = pd.concat(all_rows, ignore_index=True)

    # panel-level factors (need index returns / cross-sectional ranks,
    # so they can't come from the per-symbol TECHNICAL_CALCULATORS loop)
    panel["RESID_VOL_60D"] = compute_resid_vol_60d(panel, index_daily)
    panel["CROWDING_SCORE"] = compute_crowding_score(panel)

    fund_factor_names: list[str] = []
    if factor_set == "technical_fundamental":
        from selene_space_ashare.fundamentals_pit import (
            attach_fundamentals,
            load_financials,
        )

        panel = attach_fundamentals(panel, load_financials())
        # BP activation: eastmoney records carry BPS (每股净资产, PIT-merged
        # above); pbMRQ = close/BPS feeds the existing 1/pbMRQ branch below
        if panel.get("BPS") is not None and panel["BPS"].notna().any():
            panel["pbMRQ"] = panel["close"] / panel["BPS"].where(panel["BPS"] > 0)
        # derive tradable fundamental factor columns from the PIT-merged
        # financial fields (NaN when financials cache is empty/missing);
        # each candidate is added only if its source field survived the
        # merge — missing fields would raise KeyError on derivation
        fund_candidates: list[str] = []
        if panel["epsTTM"].notna().any():
            panel["EP"] = panel["epsTTM"] / panel["close"]
            fund_candidates.append("EP")
        # BP via 1/pbMRQ (mathematically BP): pbMRQ derived above from
        # PIT-merged BPS × daily close
        if "pbMRQ" in panel.columns and panel["pbMRQ"].notna().any():
            panel["BP"] = 1.0 / panel["pbMRQ"].where(panel["pbMRQ"] > 0)
            fund_candidates.append("BP")
        else:
            logger.info("BP skipped: no pbMRQ field")
        if panel["YOYNI"].notna().any():
            panel["YOY_NI"] = panel["YOYNI"]
            fund_candidates.append("YOY_NI")
        if panel["roeAvg"].notna().any():
            panel["ROE"] = panel["roeAvg"]
            fund_candidates.append("ROE")
        # batch4 passthroughs: GP margin / leverage / current ratio
        if panel["gpMargin"].notna().any():
            panel["GP_MARGIN"] = panel["gpMargin"]
            fund_candidates.append("GP_MARGIN")
        if panel["liabilityToAsset"].notna().any():
            panel["LEVERAGE"] = panel["liabilityToAsset"]
            fund_candidates.append("LEVERAGE")
        if panel["currentRatio"].notna().any():
            panel["CURRENT_RATIO"] = panel["currentRatio"]
            fund_candidates.append("CURRENT_RATIO")
        # SIZE: pure kline-column derivation, single source of truth in
        # factor_calc (imported, never hand-rolled here — DRY per plan v3)
        if "turnover" in panel.columns and panel["turnover"].notna().any():
            from selene_space_ashare.factor_calc import (
                SIZE_KLINE_COLS,
                derive_size_factor,
            )

            panel["SIZE"] = (
                panel.groupby("symbol", group_keys=False)[SIZE_KLINE_COLS]
                .apply(lambda g: derive_size_factor(g))
                .rename("SIZE")
            )
            fund_candidates.append("SIZE")
        # drop candidates with no data at all: ir/ic would zero their
        # weight, but the walkforward rank path would poison composite_score
        usable = [
            c for c in fund_candidates if c in panel.columns and panel[c].notna().any()
        ]
        fund_factor_names = usable

    return panel, failed_codes, fund_factor_names


@router.get("/factors/{symbol}")
async def get_factors(symbol: str) -> dict:
    """Get technical factors for an A-share stock."""
    from selene_space_ashare.data_fetcher import get_daily_klines
    from selene_space_ashare.factor_calc import calculate_technical_factors

    try:
        klines = get_daily_klines(symbol, start_date="20240101")
        if klines.empty:
            raise _not_found(f"No data for {symbol}")
        factors = calculate_technical_factors(klines)
        if factors.empty:
            raise _not_found(f"Insufficient data for {symbol}")
        latest = factors.iloc[-1].to_dict()
        return {"symbol": symbol, "factors": latest}
    except SeleneSpaceError:
        raise
    except Exception as exc:
        raise _upstream(exc)


@router.get("/regime")
async def get_regime(index: str = "000300") -> dict:
    """Get current market regime from index data."""
    from selene_space_ashare.data_fetcher import get_index_daily
    from selene_space_ashare.regime import calc_index_regime, get_regime_on_date

    try:
        index_daily = get_index_daily(index)
        if index_daily.empty:
            raise _not_found(f"No data for index {index}")
        regime_df = calc_index_regime(index_daily)
        latest = regime_df.iloc[-1]
        regime = get_regime_on_date(regime_df, str(latest["date"]))
        return {
            "index": index,
            "date": str(latest["date"]),
            "trend": regime.trend.value,
            "vol": regime.vol.value,
            "close": regime.index_close,
            "ma20": regime.index_ma20,
            "ma60": regime.index_ma60,
            "realized_vol_20d": regime.realized_vol_20d,
        }
    except SeleneSpaceError:
        raise
    except Exception as exc:
        raise _upstream(exc)


@router.post("/backtest")
async def run_backtest(
    symbols: str = "600519,000858",
    strategy: str = "trend_follow",
    n_long: int = 5,
    weight_method: str = "equal",
    cost_bps: float = 0.0,
    factor_set: str = "technical",
    pool: str = "",
) -> dict:
    """Run a factor-based backtest for A-share stocks.

    pool=hs300|csi500 resolves the universe from index constituents,
    overriding the explicit symbols list.
    """
    from selene_space_ashare.data_fetcher import get_index_daily
    from selene_space_ashare.regime import calc_index_regime
    from selene_space_ashare.strategy import (
        make_trend_follow_strategy,
        make_mean_reversion_strategy,
    )
    from selene_space_ashare.composite import build_composite_panel
    from selene_space_ashare.backtest import backtest_full_strategy

    valid_weight_methods = {"equal", "ir", "ic", "ir_walkforward"}
    if weight_method not in valid_weight_methods:
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message=f"weight_method must be one of {sorted(valid_weight_methods)}, got {weight_method!r}",
        )

    valid_factor_sets = {"technical", "technical_fundamental"}
    if factor_set not in valid_factor_sets:
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message=f"factor_set must be one of {sorted(valid_factor_sets)}, got {factor_set!r}",
        )

    try:
        if pool:
            pool_norm = pool.strip().lower()
            if pool_norm == "hs300":
                codes = await _hs300_codes()
            elif pool_norm == "csi500":
                from selene_space_ashare.data_fetcher import get_csi500_constituents

                constituents = await asyncio.to_thread(get_csi500_constituents)
                if constituents.empty:
                    raise _not_found("csi500 constituents unavailable")
                codes = [str(c) for c in constituents["code"].tolist()]
            else:
                raise SeleneSpaceError(
                    code=ErrorCode.VALIDATION_ERROR,
                    message=f"pool must be hs300 or csi500, got {pool!r}",
                )
        else:
            codes = [s.strip() for s in symbols.split(",")]

        # index data feeds regime detection here and RESID_VOL_60D inside
        # build_ashare_panel — fetched once, shared with the helper
        index_daily = get_index_daily("000300")
        if index_daily.empty:
            raise _not_found("No index data for regime detection")
        regime_df = calc_index_regime(index_daily)

        panel, failed_codes, fund_factor_names = build_ashare_panel(
            codes,
            start_date="20240101",
            factor_set=factor_set,
            index_daily=index_daily,
        )

        if failed_codes:
            logger.warning(
                "ashare backtest: %d/%d symbols failed to fetch, excluded: %s",
                len(failed_codes),
                len(codes),
                ", ".join(failed_codes),
            )

        if strategy == "mean_reversion":
            config, factor_names = make_mean_reversion_strategy(n_long=n_long)
        else:
            config, factor_names = make_trend_follow_strategy(n_long=n_long)

        if factor_set == "technical_fundamental":
            if fund_factor_names:
                factor_names = factor_names + fund_factor_names
            else:
                logger.warning(
                    "factor_set=technical_fundamental requested but no "
                    "fundamental data available (run /ashare/fundamentals-backfill); "
                    "falling back to technical factors only"
                )

        # confluence filter (top-percentile + above-MA20) is designed for
        # full-market cross-sections; on a tiny watchlist where n_long ~=
        # universe size it screens out everything (3 stocks -> ~0 candidates).
        # Percentile 1.0 -> threshold = quantile(0) = keep all non-NaN.
        if len(codes) < 20:
            config = config.model_copy(
                update={
                    "min_composite_percentile": 1.0,
                    "require_price_above_ma20": False,
                    "require_volume_above_avg": False,
                }
            )

        # forward returns per symbol (holding_period horizon), so the
        # backtest has a target column — calculate_technical_factors
        # intentionally leaves them out
        holding = config.holding_period
        panel[f"fwd_{holding}d"] = panel.groupby("symbol")["close"].transform(
            lambda s: s.shift(-holding) / s - 1
        )

        # limit-board fillability gates (absorbed from TSP): judge each
        # symbol's per-bar seal BEFORE any per-date grouping — band judgement
        # needs each symbol's own prior closes. Without close there is no
        # gate; the panel here always has it (build_ashare_panel keeps it).
        from selene_space_ashare.backtest_gates import attach_limit_flags

        panel = attach_limit_flags(panel)

        forward_col = f"fwd_{config.holding_period}d"
        panel_c, _ = build_composite_panel(
            panel, factor_names, forward_col=forward_col, weight_method=weight_method
        )

        if forward_col not in panel_c.columns:
            raise _not_found(f"Forward column {forward_col} not in panel")

        result = backtest_full_strategy(
            panel_c, regime_df, config, forward_col=forward_col, cost_bps=cost_bps
        )
        metrics = dict(result["metrics"])
        daily = result["daily_returns"]
        if not daily.empty and "turnover" in daily.columns:
            metrics["avg_turnover"] = float(daily["turnover"].mean())
        return {
            "metrics": metrics,
            "universe_note": (
                "universe_note: 回测使用请求传入的当前成分股列表，存在生存偏差"
                "（已退市/调出股票不在池内）。PIT 历史成分可通过 "
                "universe/hs300_history.parquet 插槽喂入。"
            ),
        }
    except SeleneSpaceError:
        raise
    except Exception as exc:
        raise _upstream(exc)


@router.post("/portfolio-backtest")
async def run_portfolio_backtest_endpoint(
    symbols: str = "600519,000858",
    n_long: int = 20,
    rebalance_days: int = 5,
    cost_bps: float = 15.0,
    forward_col: str = "fwd_5d",
    benchmark_index: str = "000300",
    start_date: str = "20240101",
    pool: str = "",
    stock_weight: str = "equal",
) -> dict:
    """Periodic-rebalance portfolio backtest with benchmark excess.

    Distinct from POST /ashare/backtest: this drives the batch10 portfolio
    engine (fixed-period rebalance, walk-forward IC weights) and returns
    portfolio / benchmark / excess metric blocks. pool=hs300|csi500 resolves
    the universe from index constituents, overriding the symbols list.

    stock_weight picks how the held names are sized within each window:
    "equal" (default), "slsqp" (risk-parity style optimum) or "inv_vol"
    (inverse-volatility). This is orthogonal to weight_method on
    POST /ashare/backtest, which weights the *factors* inside the composite
    score, not the stocks in the portfolio.
    """
    from selene_space_ashare.composite import _forward_horizon
    from selene_space_ashare.data_fetcher import get_index_daily
    from selene_space_ashare.portfolio_rebal import run_portfolio_backtest

    # _forward_horizon returns 0 (never raises) for an unparseable name;
    # without this guard a bogus forward_col would surface downstream as a
    # slice error or a silent zero-step, not as a validation failure
    holding = _forward_horizon(forward_col)
    if holding <= 0:
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message=(
                f"cannot parse a holding horizon from forward_col "
                f"{forward_col!r}; expected a name like 'fwd_5d'"
            ),
        )
    if rebalance_days != holding:
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message=(
                f"rebalance_days ({rebalance_days}) must equal the forward "
                f"horizon of {forward_col} ({holding})"
            ),
        )
    if n_long < 1:
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message=f"n_long must be >= 1, got {n_long}",
        )
    valid_stock_weights = {"equal", "slsqp", "inv_vol"}
    if stock_weight not in valid_stock_weights:
        # reject rather than let the engine fall back to equal: a typo like
        # "invol" would otherwise silently return equal-weighted numbers
        # under a label the caller reads as risk-parity
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message=(
                f"stock_weight must be one of {sorted(valid_stock_weights)}, "
                f"got {stock_weight!r}"
            ),
        )
    if stock_weight == "slsqp":
        # make_slsqp_weight_fn caps any single name at max_w (default 0.06),
        # which is infeasible once n_long < 1/max_w — the optimizer then
        # returns equal weights, so the run still succeeds but is labelled
        # risk-parity while being equal-weighted. Log it; do not change the
        # math (it matches the param-lab grid's behaviour).
        import inspect

        from selene_space_ashare.portfolio_rebal import make_slsqp_weight_fn

        _max_w = inspect.signature(make_slsqp_weight_fn).parameters["max_w"].default
        if n_long * _max_w < 1.0:
            logger.warning(
                "ashare portfolio-backtest: stock_weight=slsqp with n_long=%d "
                "is infeasible under max_w=%s (needs n_long >= %d); the "
                "optimizer will return equal weights for every window",
                n_long,
                _max_w,
                int(-(-1 // _max_w)),
            )

    try:
        codes = await _resolve_pool_codes(pool, symbols)
        if not codes:
            raise _not_found("no symbols requested")

        index_daily = get_index_daily(benchmark_index)
        if index_daily.empty:
            raise _not_found(f"No index data for benchmark {benchmark_index}")

        panel, failed_codes, _fund_factor_names = build_ashare_panel(
            codes,
            start_date=start_date,
            factor_set="technical",
            index_daily=index_daily,
        )
        if panel.empty:
            raise _not_found("empty panel: no factor data for the requested symbols")

        if failed_codes:
            logger.warning(
                "ashare portfolio-backtest: %d/%d symbols failed to fetch, excluded: %s",
                len(failed_codes),
                len(codes),
                ", ".join(failed_codes),
            )

        # the engine scores against a forward-return column; technical
        # factors deliberately stop at the factor itself, so build it here
        # at the holding horizon the caller pinned via rebalance_days
        panel[forward_col] = panel.groupby("symbol")["close"].transform(
            lambda s: s.shift(-holding) / s - 1
        )

        try:
            result = run_portfolio_backtest(
                panel,
                benchmark_index=benchmark_index,
                index_fetcher=lambda _code: index_daily,
                n_long=n_long,
                rebalance_days=rebalance_days,
                forward_col=forward_col,
                cost_bps=cost_bps,
                stock_weight=stock_weight,
            )
        except ValueError as exc:
            raise SeleneSpaceError(
                code=ErrorCode.VALIDATION_ERROR, message=str(exc)
            ) from exc

        # a zero-window run is not a success: backtest_periodic_portfolio
        # skips windows where fewer than n_long names are scoreable, and
        # calc_performance_metrics then returns an all-NaN block that would
        # render as an empty page. Surface it instead.
        if result["windows"].empty:
            raise _not_found(
                "零个可结算窗口：样本不足或 n_long 过大"
                "（减少 n_long、放宽 start_date 或增加股票数后重试）"
            )

        return {
            "portfolio_metrics": result["portfolio_metrics"],
            "benchmark_metrics": result["benchmark_metrics"],
            "excess_metrics": result["excess_metrics"],
            "config": result["config"],
        }
    except SeleneSpaceError:
        raise
    except Exception as exc:
        raise _upstream(exc)


@router.post("/backfill")
async def run_backfill(
    symbols: str = "",
    max_retries: int = 3,
    reconnect_every: int = 200,
    resume: bool = True,
) -> dict:
    """Backfill daily klines for a symbol list with retry/reconnect/resume.

    Symbols are fetched one-by-one with exponential backoff (2/4/8s); every
    ``reconnect_every`` symbols a session-reset hook runs. Progress persists
    to a JSON state file — completed symbols are never re-fetched on
    restart. Failure here means the run itself broke; per-symbol failures
    are reported in the payload, not raised.
    """
    from selene_space_ashare.data_fetcher import get_daily_klines
    from app.services.ashare_backfill import (
        DEFAULT_PROGRESS_PATH,
        backfill as run_backfill_job,
    )

    codes = [s.strip() for s in symbols.split(",") if s.strip()]
    if not codes:
        raise _not_found("No symbols requested")

    def _store(symbol: str, df) -> str | None:
        # get_daily_klines already writes its parquet cache; the marker
        # records how far the backfilled range reaches for observability.
        return str(df["date"].max()) if "date" in df.columns else None

    try:
        progress = (
            DEFAULT_PROGRESS_PATH
            if resume
            else DEFAULT_PROGRESS_PATH.with_name(
                DEFAULT_PROGRESS_PATH.name + ".once.json"
            )
        )
        report = await asyncio.to_thread(
            run_backfill_job,
            codes,
            lambda sym: get_daily_klines(sym, force=True),
            _store,
            max_retries=max_retries,
            reconnect_every=reconnect_every,
            progress_path=progress,
        )
    except Exception as exc:
        raise _upstream(exc)

    body = {"report": report.to_dict()}
    if report.failed_symbols:
        logger.warning(
            "ashare backfill finished with %d failed symbols: %s",
            report.n_failed,
            ", ".join(report.failed_symbols),
        )
    return body


@router.get("/financials/{symbol}")
async def get_financials_timeline(symbol: str) -> dict:
    """Quarterly PIT financials for one symbol, sorted by report period."""
    from selene_space_ashare.fundamentals_pit import load_financials

    try:
        records = [r for r in load_financials() if r.get("symbol") == symbol]
        if not records:
            raise _not_found(
                f"No financials cached for {symbol} (run fundamentals-backfill)"
            )
        records.sort(key=lambda r: (r.get("year", 0), r.get("quarter", 0)))
        return {"symbol": symbol, "n_records": len(records), "records": records}
    except SeleneSpaceError:
        raise
    except Exception as exc:
        raise _upstream(exc)


@router.get("/financials-snapshot")
async def get_financials_snapshot(
    field: str = "BPS",
    year: int = 0,
    quarter: int = 0,
    top: int = 20,
) -> dict:
    """Cross-section of one financial field for one report quarter (desc).

    year/quarter = 0 means the latest quarter present in the cache.
    """
    from selene_space_ashare.fundamentals_pit import load_financials

    try:
        records = [r for r in load_financials() if r.get(field) is not None]
        if not records:
            raise _not_found(f"Field {field!r} has no data in financials cache")
        if year and quarter:
            records = [
                r
                for r in records
                if r.get("year") == year and r.get("quarter") == quarter
            ]
            if not records:
                raise _not_found(f"No data for {year}Q{quarter} / {field}")
        else:
            latest = max((r.get("year", 0), r.get("quarter", 0)) for r in records)
            records = [
                r for r in records if (r.get("year"), r.get("quarter")) == latest
            ]

        values = sorted(
            (
                (r["symbol"], float(r[field]))
                for r in records
                if r.get(field) is not None
            ),
            key=lambda t: t[1],
            reverse=True,
        )
        all_vals = [v for _, v in values]
        n = len(all_vals)
        mid = n // 2
        median = all_vals[mid] if n % 2 else (all_vals[mid - 1] + all_vals[mid]) / 2
        return {
            "field": field,
            "year": records[0].get("year"),
            "quarter": records[0].get("quarter"),
            "n_universe": n,
            "median": median,
            "max": {"symbol": values[0][0], "value": values[0][1]},
            "min": {"symbol": values[-1][0], "value": values[-1][1]},
            "top": [{"symbol": s, "value": v} for s, v in values[:top]],
        }
    except SeleneSpaceError:
        raise
    except Exception as exc:
        raise _upstream(exc)


@router.post("/fundamentals-backfill")
async def run_fundamentals_backfill(
    symbols: str = "",
    quarters: str = "",
) -> dict:
    """Backfill PIT financials for symbol list via baostock (resume via done-set).

    ``quarters`` format: "2026-2,2026-1" (empty = dynamic calendar default).
    """
    from selene_space_ashare.fundamentals_pit import fetch_financials_pit

    codes = [s.strip() for s in symbols.split(",") if s.strip()]
    if not codes:
        raise _not_found("No symbols requested")

    q_list: list[tuple[int, int]] | None = None
    if quarters.strip():
        q_list = [
            (int(item.split("-")[0]), int(item.split("-")[1]))
            for item in quarters.split(",")
            if item.strip()
        ]

    try:
        records = await asyncio.to_thread(fetch_financials_pit, codes, q_list)
        done_syms = {r.get("symbol") for r in records}
        return {
            "n_records": len(records),
            "n_symbols_with_data": len(done_syms),
            "requested": len(codes),
        }
    except Exception as exc:
        raise _upstream(exc)


async def _hs300_codes() -> list[str]:
    from selene_space_ashare.data_fetcher import get_hs300_constituents

    constituents = await asyncio.to_thread(get_hs300_constituents)
    if constituents.empty:
        raise _not_found("hs300 constituents unavailable")
    return [str(c) for c in constituents["code"].tolist()]


async def _resolve_pool_codes(pool: str, symbols: str) -> list[str]:
    """Resolve the hs300/csi500 pool or the comma-separated symbols fallback.

    Returns a possibly-empty list when neither is supplied — the caller decides
    whether that is a 404 or a 400. An unknown pool name always 400s.
    """
    if not pool:
        return [s.strip() for s in symbols.split(",") if s.strip()]
    pool_norm = pool.strip().lower()
    if pool_norm == "hs300":
        return await _hs300_codes()
    if pool_norm == "csi500":
        from selene_space_ashare.data_fetcher import get_csi500_constituents

        constituents = await asyncio.to_thread(get_csi500_constituents)
        if constituents.empty:
            raise _not_found("csi500 constituents unavailable")
        return [str(c) for c in constituents["code"].tolist()]
    raise SeleneSpaceError(
        code=ErrorCode.VALIDATION_ERROR,
        message=f"pool must be hs300 or csi500, got {pool!r}",
    )


# Single source of truth for style-report defaults, shared by GET/POST
# endpoints and the startup warmup in app.main (batch6 US-2).
DEFAULT_REPORT_PARAMS: dict[str, Any] = {
    "start_date": "20260305",
    "end_date": "",
    "forward_col": "fwd_5d",
    "lookback": 20,
}


@router.get("/style-report")
async def get_style_report(
    start_date: str = DEFAULT_REPORT_PARAMS["start_date"],
    end_date: str = DEFAULT_REPORT_PARAMS["end_date"],
    forward_col: str = DEFAULT_REPORT_PARAMS["forward_col"],
    lookback: int = DEFAULT_REPORT_PARAMS["lookback"],
) -> dict:
    """SIZE/VALUE/GROWTH daily long-short spread + regime summary (cached).

    First call builds the 300-symbol factor panel (~60s, in a worker thread);
    later calls hit the in-process cache. Empty data is negatively cached.
    """
    from app.services.ashare_style_report import get_style_report as _get

    try:
        codes = await _hs300_codes()
        report = await _get(codes, start_date, end_date, forward_col, lookback)
        if report is None:
            raise _not_found("no style report data (kline cache empty for this window)")
        return report
    except SeleneSpaceError:
        raise
    except Exception as exc:
        raise _upstream(exc)


@router.post("/style-report/refresh")
async def refresh_style_report(
    start_date: str = DEFAULT_REPORT_PARAMS["start_date"],
    end_date: str = DEFAULT_REPORT_PARAMS["end_date"],
    forward_col: str = DEFAULT_REPORT_PARAMS["forward_col"],
    lookback: int = DEFAULT_REPORT_PARAMS["lookback"],
) -> dict:
    """Drop the cached style report and rebuild it."""
    from app.services.ashare_style_report import refresh_style_report as _refresh

    try:
        codes = await _hs300_codes()
        report = await _refresh(codes, start_date, end_date, forward_col, lookback)
        return {
            "rebuilt": True,
            "params": {
                "start_date": start_date,
                "end_date": end_date,
                "forward_col": forward_col,
                "lookback": lookback,
                "n_symbols": len(codes),
            },
            "report": report,
        }
    except SeleneSpaceError:
        raise
    except Exception as exc:
        raise _upstream(exc)


@router.post("/factor-report")
async def run_factor_report(
    symbols: str = "",
    pool: str = "",
    factors: str = "",
    forward_col: str = DEFAULT_REPORT_PARAMS["forward_col"],
    neutralize: bool = True,
    start_date: str = DEFAULT_REPORT_PARAMS["start_date"],
    end_date: str = DEFAULT_REPORT_PARAMS["end_date"],
) -> dict:
    """IC / quantile / correlation report over a symbol universe (cached).

    The first call builds the factor panel (~60s for a full index, in a worker
    thread); later calls hit the in-process cache. Empty results are
    negatively cached. neutralize=True adds *_neutral columns (industry
    neutralization) alongside the raw statistics.
    """
    from app.services.ashare_factor_report import get_factor_report as _get

    try:
        codes = await _resolve_pool_codes(pool, symbols)
        if not codes:
            raise SeleneSpaceError(
                code=ErrorCode.VALIDATION_ERROR,
                message="either symbols or pool is required",
            )

        factor_list = [f.strip() for f in factors.split(",") if f.strip()] or None

        report = await _get(
            codes, start_date, end_date, forward_col, neutralize, factor_list
        )
        if report is None:
            raise _not_found(
                "no factor report data (kline cache empty for this window)"
            )
        return report
    except SeleneSpaceError:
        raise
    except Exception as exc:
        raise _upstream(exc)


@router.post("/factor-report/refresh")
async def refresh_factor_report(
    symbols: str = "",
    pool: str = "",
    factors: str = "",
    forward_col: str = DEFAULT_REPORT_PARAMS["forward_col"],
    neutralize: bool = True,
    start_date: str = DEFAULT_REPORT_PARAMS["start_date"],
    end_date: str = DEFAULT_REPORT_PARAMS["end_date"],
) -> dict:
    """Drop the cached factor report and rebuild it.

    The universe/factor/neutralize params mirror POST /factor-report: the cache
    key is the full parameter tuple, so a refresh must name the same entry it
    means to drop — otherwise it evicts nothing and just rebuilds a cold key.
    """
    from app.services.ashare_factor_report import refresh_factor_report as _refresh

    try:
        codes = await _resolve_pool_codes(pool, symbols)
        if not codes:
            raise SeleneSpaceError(
                code=ErrorCode.VALIDATION_ERROR,
                message="either symbols or pool is required",
            )

        factor_list = [f.strip() for f in factors.split(",") if f.strip()] or None

        report = await _refresh(
            codes, start_date, end_date, forward_col, neutralize, factor_list
        )
        if report is None:
            raise _not_found(
                "no factor report data (kline cache empty for this window)"
            )
        return {
            "rebuilt": True,
            "params": {
                "start_date": start_date,
                "end_date": end_date,
                "forward_col": forward_col,
                "neutralize": neutralize,
                "factors": factor_list,
                "n_symbols": len(codes),
            },
            "report": report,
        }
    except SeleneSpaceError:
        raise
    except Exception as exc:
        raise _upstream(exc)
