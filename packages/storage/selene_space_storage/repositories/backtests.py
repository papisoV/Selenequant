"""Persistence for :class:`BacktestResult` objects.

Each curve/metric lives in its own JSON column.  On load, missing
buy-hold and drawdown curves are derived from the equity series so older
rows (or rows written without those columns populated) still surface
complete chart data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from selene_space_types.strategy import BacktestMetrics, BacktestResult

logger = logging.getLogger(__name__)


def _coerce_timestamp(value: Any) -> Any:
    """Normalise a stored ``created_at`` to epoch-ms int.

    Legacy rows wrote an ISO-8601 string (``'2026-07-14T09:41:57.934000+00:00'``)
    while :data:`Timestamp` is a plain ``int``; pydantic rejects the string, and
    the caller's per-row try/except would then drop the row from every listing.
    Numeric strings pass through ``int()``; unparseable values are returned
    unchanged so validation still surfaces them.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            try:
                return int(datetime.fromisoformat(value).timestamp() * 1000)
            except ValueError:
                return value
    return value


def _derive_drawdown(equity: list[float]) -> list[float]:
    out: list[float] = []
    peak = equity[0] if equity else 0.0
    for point in equity:
        peak = max(peak, point)
        out.append(point / peak - 1.0 if peak else 0.0)
    return out


def _derive_buy_hold(equity: list[float]) -> list[float]:
    base = equity[0] if equity else 1.0
    return [point / base * 10000.0 for point in equity]


class BacktestRepository:
    """CRUD facade over the ``backtests`` table."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def save(self, result: BacktestResult, *, user_id: str = "local") -> str:
        # backtests.strategy_id has an FK to strategies.id. Virtual strategy
        # ids (bridge:/qd:/alpha: prefixes, YAML ad-hoc ids) never exist in
        # the strategies table, so backfill a stub host row first or every
        # such save dies on FOREIGN KEY constraint failed. config_json must
        # pass StrategyConfig validation — StrategyRepository.list() parses
        # every row, so an empty "{}" stub would break app startup.
        stub_config = json.dumps(
            {
                "id": result.strategy_id,
                "name": result.strategy_id,
                "type": "custom",
                "symbols": [],
                "interval": getattr(result, "interval", None) or "1h",
                "source": "virtual",
            },
            ensure_ascii=False,
        )
        await self._db.execute(
            """
            INSERT OR IGNORE INTO strategies
                (id, name, type, config_json, created_at, updated_at)
            VALUES (?, ?, 'custom', ?, datetime('now'), datetime('now'))
            """,
            (result.strategy_id, result.strategy_id, stub_config),
        )
        await self._db.execute(
            """
            INSERT INTO backtests (
                id, strategy_id, strategy_version, start_date, end_date,
                metrics_json, equity_curve_json, buy_hold_curve_json,
                drawdown_curve_json, trades_json, trades_count,
                symbol, interval, created_at, user_id,
                exposure_curve_json, config_snapshot_json, lookahead_warnings_json,
                robustness_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)            ON CONFLICT(id) DO UPDATE SET
                strategy_id = excluded.strategy_id,
                strategy_version = excluded.strategy_version,
                start_date = excluded.start_date,
                end_date = excluded.end_date,
                metrics_json = excluded.metrics_json,
                equity_curve_json = excluded.equity_curve_json,
                buy_hold_curve_json = excluded.buy_hold_curve_json,
                drawdown_curve_json = excluded.drawdown_curve_json,
                trades_json = excluded.trades_json,
                trades_count = excluded.trades_count,
                symbol = excluded.symbol,
                interval = excluded.interval,
                created_at = excluded.created_at,
                user_id = excluded.user_id,
                exposure_curve_json = excluded.exposure_curve_json,
                config_snapshot_json = excluded.config_snapshot_json,
                lookahead_warnings_json = excluded.lookahead_warnings_json,
                robustness_json = excluded.robustness_json
            """,
            (
                result.id,
                result.strategy_id,
                getattr(result, "strategy_version", 1),
                result.start_date.isoformat(),
                result.end_date.isoformat(),
                json.dumps(result.metrics.model_dump(mode="json"), ensure_ascii=False),
                json.dumps(result.equity_curve),
                json.dumps(getattr(result, "buy_hold_curve", None) or []),
                json.dumps(getattr(result, "drawdown_curve", None) or []),
                json.dumps(getattr(result, "trades", None) or [], ensure_ascii=False),
                result.trades_count,
                getattr(result, "symbol", None) or "",
                getattr(result, "interval", None) or "1h",
                result.created_at,
                user_id,
                json.dumps(getattr(result, "exposure_curve", None) or []),
                json.dumps(
                    getattr(result, "config_snapshot", None) or {}, ensure_ascii=False
                ),
                json.dumps(
                    getattr(result, "lookahead_warnings", None) or [],
                    ensure_ascii=False,
                ),
                json.dumps(
                    getattr(result, "robustness", None) or {},
                    ensure_ascii=False,
                ),
            ),
        )
        return str(result.id)

    async def get(
        self, backtest_id: str, *, user_id: str | None = None
    ) -> BacktestResult | None:
        sql = "SELECT * FROM backtests WHERE id = ?"
        params: tuple = (backtest_id,)
        if user_id is not None:
            sql += " AND user_id = ?"
            params = (backtest_id, user_id)
        rows = await self._db.execute(sql, params)
        if not rows:
            return None
        r = rows[0]
        equity: list[float] = json.loads(r["equity_curve_json"])
        buy_hold: list[float] = json.loads(
            r["buy_hold_curve_json"]
        ) or _derive_buy_hold(equity)
        drawdown: list[float] = json.loads(r["drawdown_curve_json"])
        if not drawdown:
            drawdown = _derive_drawdown(equity)
        fields: dict[str, Any] = {
            "id": r["id"],
            "strategy_id": r["strategy_id"],
            "strategy_version": r["strategy_version"],
            "start_date": datetime.fromisoformat(r["start_date"]),
            "end_date": datetime.fromisoformat(r["end_date"]),
            "metrics": BacktestMetrics.model_validate(json.loads(r["metrics_json"])),
            "equity_curve": equity,
            "buy_hold_curve": buy_hold,
            "drawdown_curve": drawdown,
            "trades": json.loads(r["trades_json"] or "[]"),
            "trades_count": r["trades_count"],
            "symbol": r["symbol"] if "symbol" in r.keys() else "",
            "interval": r["interval"] if "interval" in r.keys() else "1h",
            "created_at": _coerce_timestamp(r["created_at"]),
            "exposure_curve": json.loads(r["exposure_curve_json"])
            if "exposure_curve_json" in r.keys()
            else [],
            "config_snapshot": json.loads(r["config_snapshot_json"] or "{}")
            if "config_snapshot_json" in r.keys()
            else {},
            "lookahead_warnings": json.loads(r["lookahead_warnings_json"] or "[]")
            if "lookahead_warnings_json" in r.keys()
            else [],
            "robustness": json.loads(r["robustness_json"] or "{}")
            if "robustness_json" in r.keys()
            else {},
        }
        return BacktestResult.model_validate(fields)

    async def get_raw(self, backtest_id: str) -> dict[str, Any] | None:
        """Raw row without model validation (replay reads trades_json itself)."""
        rows = await self._db.execute(
            "SELECT * FROM backtests WHERE id = ?", (backtest_id,)
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["trades"] = json.loads(row.get("trades_json") or "[]")
        return row

    async def _get_many(
        self, ids: list[str], *, user_id: str | None = None
    ) -> list[BacktestResult]:
        """Load many rows, isolating per-row failures.

        A single corrupt row (e.g. a legacy ``created_at`` shape pydantic
        rejects) must not abort the whole listing — that turns one bad row
        into an empty list. Skip-and-log so the healthy rows still surface.
        """
        results: list[BacktestResult] = []
        for row_id in ids:
            try:
                result = await self.get(row_id, user_id=user_id)
            except Exception:
                logger.warning(
                    "Skipping unloadable backtest row %s", row_id, exc_info=True
                )
                continue
            if result is not None:
                results.append(result)
        return results

    async def list_summaries(
        self,
        limit: int | None = 50,
        offset: int | None = 0,
        *,
        strategy_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Light listing: scalars + metrics only, no curve/trade JSON decoded.

        The list view renders ~10 fields per row, yet :meth:`get` is called for
        every row so the endpoint can build full detail dicts — decoding the
        whole table's ``equity_curve_json``/``trades_json``/… (tens of MB) on
        every request is what pushed the listing past the client's 12s abort.
        This returns just what a list row needs; the detail view keeps using
        :meth:`get` for the full row.
        """
        sql = (
            "SELECT id, strategy_id, strategy_version, start_date, end_date, "
            "metrics_json, symbol, interval, created_at, trades_count "
            "FROM backtests"
        )
        clauses: list[str] = []
        params: list[Any] = []
        if strategy_id is not None:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit if limit is not None else -1, offset or 0])
        rows = await self._db.execute(sql, tuple(params))

        summaries: list[dict[str, Any]] = []
        for r in rows:  # per-row guard: one bad metrics_json must not blank the list
            try:
                metrics = BacktestMetrics.model_validate(json.loads(r["metrics_json"]))
            except Exception:
                logger.warning(
                    "Skipping unloadable backtest summary %s", r["id"], exc_info=True
                )
                continue
            summaries.append(
                {
                    "id": r["id"],
                    "strategy_id": r["strategy_id"],
                    "strategy_version": r["strategy_version"],
                    "start_date": r["start_date"],
                    "end_date": r["end_date"],
                    "metrics": metrics.model_dump(),
                    "symbol": r["symbol"] if "symbol" in r.keys() else "",
                    "interval": r["interval"] if "interval" in r.keys() else "1h",
                    "created_at": _coerce_timestamp(r["created_at"]),
                    "trades_count": r["trades_count"],
                }
            )
        return summaries

    async def list_all(
        self,
        limit: int | None = 10,
        offset: int | None = 0,
        *,
        user_id: str | None = None,
    ) -> list[BacktestResult]:
        sql = "SELECT id FROM backtests"
        params: list[Any] = []
        if user_id is not None:
            sql += " WHERE user_id = ?"
            params.append(user_id)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit if limit is not None else -1, offset or 0])
        rows = await self._db.execute(sql, tuple(params))
        return await self._get_many([row["id"] for row in rows], user_id=user_id)

    async def list_by_strategy(
        self,
        strategy_id: str,
        limit: int | None = None,
        *,
        user_id: str | None = None,
    ) -> list[BacktestResult]:
        sql = "SELECT id FROM backtests WHERE strategy_id = ?"
        params: list[Any] = [strategy_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        sql += " ORDER BY created_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = await self._db.execute(sql, tuple(params))
        return await self._get_many([row["id"] for row in rows], user_id=user_id)

    async def delete(self, backtest_id: str, *, user_id: str | None = None) -> bool:
        sql = "DELETE FROM backtests WHERE id = ?"
        params: list[Any] = [backtest_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        before = await self._db.execute(
            "SELECT COUNT(*) AS n FROM backtests WHERE id = ?", (backtest_id,)
        )
        existed = bool(before and before[0]["n"])
        await self._db.execute(sql, tuple(params))
        return existed
