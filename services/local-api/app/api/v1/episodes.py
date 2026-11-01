"""Episodes — read-only strategy experience ledger API (S7).

One row per completed API backtest (written by the backtests.py completion
hook). Read-only here: corrections are new episodes, not edits.
"""

import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_db_manager, get_episode_repo
from selene_space_core.kline_paths import default_klines_db_path

router = APIRouter(prefix="/episodes", tags=["episodes"])


def _require_repo(repo: Any = Depends(get_episode_repo)) -> Any:
    if repo is None:
        raise HTTPException(503, "Episode storage not available")
    return repo


@router.get("")
async def list_episodes(
    strategy_kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
    repo: Any = Depends(_require_repo),
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = await repo.list(
        limit=limit, offset=offset, strategy_kind=strategy_kind
    )
    return rows


@router.get("/world-model")
async def get_world_model(
    repo: Any = Depends(_require_repo),
) -> dict[str, Any]:
    """Batch→World Model aggregation (L3): episodes grouped by symbol/interval.

    Must stay registered BEFORE /{episode_id} or FastAPI swallows it as an id.
    """
    groups: list[dict[str, Any]] = await repo.aggregate_by_symbol_interval()
    total = sum(g["episode_count"] for g in groups)
    return {
        "groups": groups,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "total_episodes": total,
    }


@router.get("/{episode_id}")
async def get_episode(
    episode_id: str,
    repo: Any = Depends(_require_repo),
) -> dict[str, Any]:
    episode: dict[str, Any] | None = await repo.get(episode_id)
    if episode is None:
        raise HTTPException(404, f"Episode '{episode_id}' not found")
    return episode


# -- Time Machine v0: State(T) recovery --------------------------------------
#
# Canon (0908 heartbeat review): "先证明 State(T) 能真实恢复,再做 UI".
# Replay answers "what did the strategy see at moment T" from RECORDED rows
# only. RECONSTRUCTED re-judgment is deliberately NOT implemented — an LLM
# cannot honestly unknow the future. Outcome trades stay WITHHELD until
# ?reveal=true (Outcome Reveal semantics).

_BAR_LOOKBACK = 200  # T-pre window handed to the viewer
_MS_PER_HOUR = 3_600_000


def _interval_ms(interval: str) -> int:
    unit = interval[-1]
    n = int(interval[:-1])
    factor = {"m": 60_000, "h": _MS_PER_HOUR, "d": 24 * _MS_PER_HOUR}[unit]
    return n * factor


def _split_trades_at(
    trades: list[dict[str, Any]], at_ms: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition trades into closed-before-T and revealed-after-T.

    A trade is visible at T only when its exit_time <= T (the position was
    fully closed; entry_time alone would leak that a position still open at T
    eventually exits profitably).
    """
    before = [t for t in trades if int(t.get("exit_time", 0)) <= at_ms]
    after = [t for t in trades if int(t.get("exit_time", 0)) > at_ms]
    return before, after


def _bar(bars: pd.DataFrame, i: int) -> dict[str, Any]:
    return {
        "timestamp": int(bars.iloc[i]["timestamp"]),
        "open": float(bars.iloc[i]["open"]),
        "high": float(bars.iloc[i]["high"]),
        "low": float(bars.iloc[i]["low"]),
        "close": float(bars.iloc[i]["close"]),
        "volume": float(bars.iloc[i]["volume"]),
    }


@router.get("/{episode_id}/replay")
async def replay_episode(
    episode_id: str,
    at: int | None = None,
    reveal: bool = False,
    repo: Any = Depends(_require_repo),
) -> dict[str, Any]:
    episode: dict[str, Any] | None = await repo.get_raw(episode_id)
    if episode is None:
        raise HTTPException(404, f"Episode '{episode_id}' not found")

    symbol = episode["symbol"]
    interval = episode["interval"]
    backtest_id = episode["backtest_id"]
    params: Any = _load_json(episode.get("params_json"))
    metrics: Any = _load_json(episode.get("metrics_json"))

    # Signal state: the recorded trade blotter. MISSING (not NOT_AVAILABLE):
    # S0-fixed persistence means new backtests always land here; old bridge
    # rows predate it.
    trades: list[dict[str, Any]] = []
    signal_provenance = "NOT_AVAILABLE"
    result_row: dict[str, Any] | None = None
    if backtest_id:
        db = get_db_manager()
        if db is not None:
            from selene_space_storage.repositories.backtests import BacktestRepository

            result_row = await BacktestRepository(db).get_raw(backtest_id)
            if result_row is not None:
                trades = result_row["trades"]
                signal_provenance = "RECORDED"

    # Resolve T: explicit ?at, else last closed trade's exit (the "moment the
    # strategy last acted"), else the backtest window end.
    if at is not None:
        at_ms = at
        at_source = "REQUESTED"
    elif trades:
        at_ms = max(int(t["exit_time"]) for t in trades)
        at_source = "LAST_ACTION"
    elif result_row is not None:
        at_ms = int(datetime.fromisoformat(result_row["end_date"]).timestamp() * 1000)
        at_source = "BACKTEST_END"
    else:
        raise HTTPException(
            422,
            "No ?at given and no recorded trades/backtest window to derive it from",
        )

    # Market state from klines.db (the same partition the backtest ran on).
    market_bars: list[dict[str, Any]] = []
    market_provenance = "NOT_AVAILABLE"
    kdb = _get_kline_db()
    if kdb is not None:
        exchange = episode.get("exchange") or "binance"
        market_type = episode.get("market_type") or "futures"
        df = kdb.get_klines(
            exchange,
            market_type,
            symbol,
            interval,
            datetime.fromtimestamp(
                (at_ms - _BAR_LOOKBACK * _interval_ms(interval)) / 1000, tz=UTC
            ),
            datetime.fromtimestamp(at_ms / 1000, tz=UTC),
        )
        if not df.empty:
            market_bars = [
                _bar(df, i) for i in range(max(0, len(df) - _BAR_LOOKBACK), len(df))
            ]
            market_provenance = "RECORDED"

    before, after = _split_trades_at(trades, at_ms)
    open_trade = before[-1] if before else None

    payload: dict[str, Any] = {
        "episode_id": episode_id,
        "strategy_kind": episode["strategy_kind"],
        "strategy_id": episode["strategy_id"],
        "strategy_name": episode.get("strategy_name"),
        "symbol": symbol,
        "interval": interval,
        "exchange": episode.get("exchange"),
        "market_type": episode.get("market_type"),
        "at": at_ms,
        "at_iso": datetime.fromtimestamp(at_ms / 1000, tz=UTC).isoformat(),
        "at_source": at_source,
        "revealed": bool(reveal),
        "state": {
            "market": {
                "provenance": market_provenance,
                "bars": market_bars,
                "bar_count": len(market_bars),
                "last_close": market_bars[-1]["close"] if market_bars else None,
            },
            "strategy": {
                "provenance": "RECORDED" if params is not None else "NOT_AVAILABLE",
                "params": params,
                "kind": episode["strategy_kind"],
            },
            "signal": {
                "provenance": signal_provenance,
                "trades_before_t": before[-20:],
                "closed_trade_count": len(before),
                "last_trade": open_trade,
                "backtest_id": backtest_id,
            },
            "memory": {
                "provenance": "NOT_AVAILABLE",
                "note": "no prior-episode retrieval wired in v0",
            },
            "events": {
                "provenance": "NOT_AVAILABLE",
                "note": "no event feed captured at backtest time in v0",
            },
        },
    }

    if reveal:
        payload["outcome"] = {
            "provenance": "RECORDED"
            if after or result_row is not None
            else "NOT_AVAILABLE",
            "trades_after_t": after,
            "closed_trade_count": len(after),
            "metrics": metrics,
            "post_mortem": _post_mortem(before, after, at_ms),
        }
    else:
        payload["outcome"] = {
            "provenance": "WITHHELD",
            "trades_after_t": [],
            "note": "T-after trades exist but are withheld; pass ?reveal=true",
            "withheld_count": len(after),
        }
    return payload


def _post_mortem(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    at_ms: int,
) -> dict[str, Any] | None:
    """RECORDED-layer comparison: what the strategy did up to T vs what
    actually happened after. Both sides come from the same RECORDED blotter —
    no re-judgment, no reconstruction."""
    if not before or not after:
        return None
    realized = sum(float(t.get("pnl", 0.0)) for t in before)
    subsequent = sum(float(t.get("pnl", 0.0)) for t in after)
    last = before[-1]
    return {
        "as_of": at_ms,
        "last_action_side": last.get("entry_action"),
        "last_action_time": last.get("exit_time"),
        "realized_pnl_before_t": round(realized, 8),
        "realized_pnl_after_t": round(subsequent, 8),
        "direction_correct": (subsequent > 0) == (last.get("entry_action") == "buy"),
    }


def _load_json(raw: Any) -> Any:
    return json.loads(raw) if isinstance(raw, str) else raw


def _get_kline_db() -> Any:
    from selene_space_market.kline_db import KlineDB

    return KlineDB(db_path=str(default_klines_db_path()))
