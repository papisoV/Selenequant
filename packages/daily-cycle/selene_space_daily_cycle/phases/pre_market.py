"""Phase 1: Pre-Market -- macro indicators + overnight signals.

Pulls macro indicators from selene_space_macrodata provider (batch 1).
Series IDs come from WorkflowConfig.provider_configs["macrodata"]["series_ids"]
(default: ["POLICY_RATE", "CPI"]).

data_quality rules:
- All requested series returned successfully -> "clean"
- Some series returned, some missing -> "degraded"
- No series returned at all -> "missing"

data_quality = "missing" -> disposition = "halt_graceful": degraded data is not
catastrophic (halt_urgent), but workflow should halt gracefully -- downstream
phases still execute (to record state), but final WorkflowResult.status is HALTED.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from selene_space_daily_cycle.observability import track_compute
from selene_space_daily_cycle.types import (
    PhaseOutcome,
    PreMarketReport,
    WorkflowConfig,
    WorkflowContext,
    WorkflowPhase,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)


def _read_watchlist_file(path: str) -> list[str]:
    """Read a watchlist file: one symbol per line, '#' comments and blanks skipped.

    Fail-open per DSA refresh_stock_list tolerance: a missing/unreadable file
    logs a warning and yields an empty list instead of raising.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.warning("Watchlist file %s unreadable: %s", path, exc)
        return []
    symbols: list[str] = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        symbols.append(text)
    return symbols


class PreMarketPhase:
    """Phase 1: fetch macro indicators + build overnight signals."""

    def __init__(
        self,
        macrodata_provider: Any | None = None,
        cfg: WorkflowConfig | None = None,
    ) -> None:
        self._provider = macrodata_provider
        self._cfg = cfg or WorkflowConfig()

    @property
    def name(self) -> WorkflowPhase:
        return WorkflowPhase.PRE_MARKET

    async def execute(
        self, trading_date: date, context: WorkflowContext
    ) -> PhaseOutcome:
        started_at = datetime.utcnow()
        with track_compute(
            provider="daily_cycle.pre_market",
            action="fetch",
        ) as ctx:
            try:
                # Watchlist hot-reload (absorbed from daily_stock_analysis
                # refresh_stock_list pattern, MIT — re-read every execute so
                # file edits apply without restart). Unconfigured = feature
                # off: silent skip, no artifact, no quality penalty. A missing
                # or unreadable file fails open: warning + empty list.
                if "watchlist" in context.artifacts:
                    del context.artifacts["watchlist"]  # stale prior-cycle value
                watchlist_symbols: list[str] | None = None
                watchlist_cfg = self._cfg.provider_configs.get("watchlist", {})
                if isinstance(watchlist_cfg, dict) and watchlist_cfg.get("path"):
                    watchlist_symbols = _read_watchlist_file(str(watchlist_cfg["path"]))
                    context.artifacts["watchlist"] = watchlist_symbols

                macro_indicators: dict[str, float] = {}
                series_ids: list[str] = ["POLICY_RATE", "CPI"]
                macro_cfg = self._cfg.provider_configs.get("macrodata", {})
                if isinstance(macro_cfg, dict):
                    cfg_ids = macro_cfg.get("series_ids")
                    if isinstance(cfg_ids, list) and cfg_ids:
                        series_ids = [str(s) for s in cfg_ids]

                if self._provider is not None:
                    end = datetime.utcnow()
                    start = end - timedelta(days=30)
                    for sid in series_ids:
                        try:
                            series = await self._provider.fetch_series(
                                sid, start=start, end=end
                            )
                            obs = series.observations
                            if obs is not None and len(obs) > 0:
                                val = float(obs["value"].iloc[-1])
                                macro_indicators[sid] = val
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("Failed to fetch %s: %s", sid, exc)

                # Determine data quality
                if len(macro_indicators) == 0:
                    data_quality = "missing"
                elif len(macro_indicators) < len(series_ids):
                    data_quality = "degraded"
                else:
                    data_quality = "clean"

                # Build overnight signals (empty list -- signals come from
                # external sources in a real workflow; this is the empty default)
                overnight_signals: list[Any] = []

                report = PreMarketReport(
                    macro_indicators=macro_indicators,
                    overnight_signals=overnight_signals,
                    data_quality=data_quality,  # type: ignore[arg-type]
                )
                context.artifacts["pre_market"] = report

                # disposition: missing -> halt_graceful (let post_market record state)
                disposition = (
                    "halt_graceful" if data_quality == "missing" else "continue"
                )
                status = WorkflowStatus.SUCCESS
                ctx["success"] = True
                ctx["metrics_keys"] = list(report.metrics_keys()) if hasattr(report, "metrics_keys") else [
                    "series_count",
                    "data_quality",
                ]
            except Exception as exc:  # noqa: BLE001
                ctx["success"] = False
                ctx["error"] = str(exc)
                return PhaseOutcome(
                    phase=WorkflowPhase.PRE_MARKET,
                    status=WorkflowStatus.FAILED,
                    disposition="halt_urgent",
                    started_at=started_at,
                    ended_at=datetime.utcnow(),
                    error=str(exc),
                )

        return PhaseOutcome(
            phase=WorkflowPhase.PRE_MARKET,
            status=status,
            disposition=disposition,  # type: ignore[arg-type]
            started_at=started_at,
            ended_at=datetime.utcnow(),
            metrics={
                "series_requested": len(series_ids),
                "series_fetched": len(macro_indicators),
                "data_quality": data_quality,
                **(
                    {"watchlist_count": len(watchlist_symbols)}
                    if watchlist_symbols is not None
                    else {}
                ),
            },
            artifacts={"pre_market_report": report},
        )
