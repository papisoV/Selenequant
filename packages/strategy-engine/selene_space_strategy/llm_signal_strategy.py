"""LLM signal strategy — LLM-driven TradeIntent generation.

Strategy flow (v3 Gap #5 three-tier degradation + v4 Mi1/Mi2/A2):

    evaluate(ctx)
      → _build_prompt(ctx)
      → _call_llm_with_fallback(prompt, schema)
         1. Provider fallback (router chain)
         2. Exponential backoff (max_retries, retry_backoff_base_s)
         3. Exhaustion → WARNING + skip; ≥ threshold in window → CRITICAL
      → LLMSignal(**dict)
      → cache (signal, ts) + history
      → TradeIntent with risk_params["strategy_id"] (v4 A2)

Hardening:
  - Provider / prompt bias: the prompt is strictly neutral (no long bias,
    no "avoid being cautious" nudges). Removed all ds commercial hooks:
    cryptoracle.network API, broker tags, 10x leverage, cancer jailbreak.
  - Sizing: ``suggested_size_pct`` clamped to ``max_position_pct`` (not 10x).
  - KillSwitch scoping (v4 A2): ``strategy_id`` lands in intent.risk_params;
    ``LLMAdvisoryRule`` copies it into the risk context dict so the engine's
    auto-trigger activates STRATEGY scope (only this strategy is blocked,
    A1 four-tier trailing stop and A2 limit pegging keep trading).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from pydantic import ValidationError

from selene_space_types.agent import TradeIntent
from selene_space_types.llm_signal import LLMSignal, LLMSignalConfig
from selene_space_types.trading import OrderSide, OrderType

try:  # pragma: no cover - guardrail is sibling-module, always available
    from selene_space_strategy.phase_guardrail import (
        apply_phase_guardrail as _apply_phase_guardrail,
    )
except ImportError:  # pragma: no cover
    _apply_phase_guardrail = None  # type: ignore[assignment]

try:  # pragma: no cover - import guarded for environments without llm-adapter
    from selene_space_llm.base import (
        LLMError,
        LLMMessage,
        LLMRole,
        LLMProvider,
    )
    from selene_space_llm.router import LLMRouter
except ImportError as _exc:  # pragma: no cover
    LLMError = Exception  # type: ignore[assignment,misc]
    LLMMessage = None  # type: ignore[assignment]
    LLMRole = None  # type: ignore[assignment]
    LLMProvider = Any  # type: ignore[assignment,misc]
    LLMRouter = None  # type: ignore[assignment]
    _IMPORT_ERROR = _exc
else:
    _IMPORT_ERROR = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLMSignalStrategy
# ---------------------------------------------------------------------------


class LLMSignalStrategy:
    """Strategy that generates TradeIntents from LLM structured output.

    Args:
        router: Configured ``LLMRouter`` with a fallback chain.
        config: ``LLMSignalConfig`` runtime parameters.
        clock: Optional callable returning epoch seconds (for testing).
    """

    def __init__(
        self,
        router: "LLMRouter | None",
        config: LLMSignalConfig | None = None,
        *,
        clock: Any = None,
    ) -> None:
        self._config = config or LLMSignalConfig()
        self._router = router
        self._clock = clock or time.time

        # Cached latest signal + timestamp (epoch seconds)
        self._latest_signal: LLMSignal | None = None
        self._latest_signal_ts: float = 0.0

        # Ring buffer of recent signals for introspection
        self._history: deque[LLMSignal] = deque(
            maxlen=max(1, self._config.max_signal_history)
        )

        # Fallback exhaustion event timestamps (epoch seconds)
        self._exhausted_events: deque[float] = deque()

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> LLMSignalConfig:
        return self._config

    @property
    def strategy_id(self) -> str:
        return self._config.strategy_id

    @property
    def router(self) -> "LLMRouter | None":
        return self._router

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate(self, ctx: dict[str, Any]) -> "TradeIntent | None":
        """Generate a TradeIntent from the LLM, or ``None`` if no trade.

        Steps:
          1. Build a neutral market-analysis prompt.
          2. Call ``_call_llm_with_fallback`` (3-tier degradation).
          3. On exhaustion: record event, return ``None``. If threshold
             exceeded in window, the ``LLMAdvisoryRule`` will raise CRITICAL.
          4. On success: validate via ``LLMSignal``, cache, build intent.
          5. Inject ``strategy_id`` into ``intent.risk_params`` (v4 A2).

        Args:
            ctx: Strategy context with keys:
                symbol (str): e.g. "BTC/USDT".
                klines (list[dict]): recent kline OHLCV (length = data_points).
                equity (float, optional): current account equity for sizing.
                current_price (float, optional): latest mark price.

        Returns:
            TradeIntent for long/short, or None for hold / LLM failure.
        """
        if self._router is None:
            logger.warning("LLMSignalStrategy: no router configured, skipping.")
            return None

        prompt = self._build_prompt(ctx)
        schema = LLMSignal.model_json_schema()

        signal_dict = await self._call_llm_with_fallback(prompt, schema)
        if signal_dict is None:
            # Exhaustion already recorded inside _call_llm_with_fallback.
            logger.warning(
                "LLMSignalStrategy: LLM fallback exhausted for symbol=%s, skipping.",
                ctx.get("symbol", "?"),
            )
            return None

        # Validate structured output against the schema.
        try:
            signal = LLMSignal(**signal_dict)
        except ValidationError as exc:
            logger.warning(
                "LLMSignalStrategy: LLM output failed validation: %s. Skipping.",
                exc,
            )
            self._record_fallback_exhausted()
            return None

        # Cache + history.
        self._latest_signal = signal
        self._latest_signal_ts = self._clock()
        self._history.append(signal)

        # Hold → no trade intent.
        if signal.action == "hold":
            return None

        return self._build_intent(signal, ctx)

    def fallback_exhausted_count(self) -> int:
        """Count of fallback-exhaustion events in the sliding window (v4 Mi1).

        Used by ``LLMAdvisoryRule`` to decide whether to raise a CRITICAL
        alert. Events older than ``fallback_exhausted_window_s`` are evicted.
        """
        self._evict_old_events()
        return len(self._exhausted_events)

    def get_latest_signal(self) -> LLMSignal | None:
        """Return the most recent cached signal if still within TTL (v4 Mi2).

        Returns ``None`` if no signal cached or TTL has expired.
        """
        if self._latest_signal is None:
            return None
        age = self._clock() - self._latest_signal_ts
        if age > self._config.signal_ttl_s:
            # Stale — discard.
            self._latest_signal = None
            self._latest_signal_ts = 0.0
            return None
        return self._latest_signal

    # ------------------------------------------------------------------
    # Prompt construction (neutral, no ds commercial bias)
    # ------------------------------------------------------------------

    def _build_prompt(self, ctx: dict[str, Any]) -> list["LLMMessage"]:
        """Build a strictly neutral LLM prompt.

        Excludes all ds commercial hooks (v4 final §阶段4):
          - No cryptoracle.network API.
          - No broker tags (f1ee03b510d5SUDE / 60bb4a8d3416BCDE).
          - No long bias ("做多权重大一点", "避免过度谨慎").
          - No 10x leverage.
          - No cancer jailbreak narrative.
        """
        symbol = str(ctx.get("symbol", "UNKNOWN"))
        klines = ctx.get("klines", []) or []
        timeframe = self._config.timeframe
        data_points = self._config.data_points
        equity = ctx.get("equity", self._config.base_usdt)
        current_price = ctx.get("current_price", 0.0)

        # Compact kline summary: close/high/low/volume per row.
        kline_rows = []
        for k in klines[-data_points:]:
            try:
                row = (
                    f"  o={float(k.get('open', 0)):.4f} "
                    f"h={float(k.get('high', 0)):.4f} "
                    f"l={float(k.get('low', 0)):.4f} "
                    f"c={float(k.get('close', 0)):.4f} "
                    f"v={float(k.get('volume', 0)):.2f}"
                )
            except (TypeError, ValueError):
                continue
            kline_rows.append(row)
        klines_block = "\n".join(kline_rows) if kline_rows else "  (no kline data)"

        system_prompt = (
            "You are a disciplined crypto quant analyst. Analyze the provided "
            "market data and return a JSON object matching the schema. Be "
            "strictly neutral — no directional bias, no leverage assumptions, "
            "no risk-taking nudges. When evidence is mixed or weak, return "
            "'hold'. Size conservatively. Never assume leverage beyond 1x."
        )

        user_prompt = (
            f"Symbol: {symbol}\n"
            f"Timeframe: {timeframe}\n"
            f"Current price: {current_price}\n"
            f"Account equity (USDT): {equity}\n"
            f"Recent {len(kline_rows)} klines (OHLCV):\n"
            f"{klines_block}\n\n"
            "Return JSON with keys: action, confidence, rationale, "
            "suggested_size_pct, risk_notes. action ∈ {long, short, hold}. "
            "suggested_size_pct ∈ [0, 0.2] (fraction of equity). "
            "rationale ≤ 500 chars. risk_notes ≤ 500 chars (optional)."
        )

        if LLMMessage is None or LLMRole is None:  # pragma: no cover
            raise RuntimeError("llm-adapter not available")

        return [
            LLMMessage(role=LLMRole.SYSTEM, content=system_prompt),
            LLMMessage(role=LLMRole.USER, content=user_prompt),
        ]

    # ------------------------------------------------------------------
    # Three-tier fallback (provider chain + exponential backoff + exhaustion)
    # ------------------------------------------------------------------

    async def _call_llm_with_fallback(
        self,
        messages: list["LLMMessage"],
        output_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Three-tier degradation (v3 Gap #5).

        Returns the structured dict on success, or ``None`` on exhaustion.
        Records exhaustion events for the advisory rule to read.
        """
        if self._router is None:
            return None

        max_retries = self._config.max_retries
        base = self._config.retry_backoff_base_s

        # Tier 1+2: router.provider chain + per-provider exponential backoff.
        # The router itself does provider fallback (router.py:155-167); we
        # add retries around the whole router call for transient errors.
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                result = await self._router.generate_structured(messages, output_schema)
                if isinstance(result, dict):
                    return result
                # Some providers may return a pydantic-like object; coerce.
                if hasattr(result, "model_dump"):
                    return result.model_dump()  # type: ignore[no-any-return]
                logger.warning(
                    "LLMSignalStrategy: unexpected structured result type %s",
                    type(result).__name__,
                )
                last_exc = TypeError(f"unexpected result type {type(result).__name__}")
            except LLMError as exc:
                last_exc = exc
                logger.warning(
                    "LLMSignalStrategy: LLMError on attempt %d/%d: %s",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                )
            except Exception as exc:  # pragma: no cover - defensive
                last_exc = exc
                logger.warning(
                    "LLMSignalStrategy: unexpected error on attempt %d/%d: %s",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                )

            # Backoff before next attempt (skip after last attempt).
            if attempt < max_retries:
                import asyncio

                delay = base * (2**attempt)
                await asyncio.sleep(delay)

        # Tier 3: exhaustion.
        self._record_fallback_exhausted()
        if last_exc is not None:
            logger.warning(
                "LLMSignalStrategy: fallback exhausted after %d attempts: %s",
                max_retries + 1,
                last_exc,
            )
        return None

    # ------------------------------------------------------------------
    # Exhaustion tracking
    # ------------------------------------------------------------------

    def _record_fallback_exhausted(self) -> None:
        """Record a fallback-exhaustion event and evict stale entries."""
        now = self._clock()
        self._exhausted_events.append(now)
        self._evict_old_events(now=now)

    def _evict_old_events(self, *, now: float | None = None) -> None:
        if now is None:
            now = self._clock()
        window = self._config.fallback_exhausted_window_s
        while self._exhausted_events and (now - self._exhausted_events[0]) > window:
            self._exhausted_events.popleft()

    # ------------------------------------------------------------------
    # Intent construction
    # ------------------------------------------------------------------

    def _build_intent(
        self,
        signal: LLMSignal,
        ctx: dict[str, Any],
        phase: str | None = None,
        guardrail_enabled: bool = False,
    ) -> TradeIntent:
        """Convert an LLMSignal into a TradeIntent.

        Embeds ``strategy_id`` in ``risk_params`` so the risk engine's
        KillSwitch auto-trigger uses STRATEGY scope (v4 A2).

        Phase guardrail: OFF by default (crypto 7x24 paths unaffected).
        Only when ``guardrail_enabled`` is True AND ``phase`` is provided does
        ``apply_phase_guardrail`` run; a "wait" outcome produces a flat
        no-op intent (quantity=0) with the adjustments recorded in
        ``risk_params["guardrail_adjustments"]``.
        """
        symbol = str(ctx.get("symbol", "UNKNOWN"))
        current_price = float(ctx.get("current_price", 0.0) or 0.0)
        equity = float(ctx.get("equity", self._config.base_usdt))

        # --- Phase guardrail (opt-in) ---------------------------------------
        guardrail_adjustments: list[str] = []
        guardrail_applied = False
        action = signal.action
        confidence = signal.confidence
        size_pct = max(
            0.0,
            min(signal.suggested_size_pct, self._config.max_position_pct),
        )

        if (
            guardrail_enabled
            and phase is not None
            and _apply_phase_guardrail is not None
        ):
            outcome = _apply_phase_guardrail(
                phase,
                data_quality_degraded=bool(ctx.get("data_quality_degraded", False)),
                action=action,
                confidence=confidence,
                suggested_size_pct=size_pct,
            )
            guardrail_applied = True
            guardrail_adjustments = list(outcome.adjustments)
            action = outcome.adjusted_action
            confidence = outcome.adjusted_confidence
            size_pct = max(
                0.0, min(outcome.adjusted_size_pct, self._config.max_position_pct)
            )

        suppressed = guardrail_applied and action == "wait"

        # Side reflects the original signal direction even when suppressed —
        # the intent stays flat via quantity=0, not by flipping side.
        side = OrderSide.BUY if signal.action == "long" else OrderSide.SELL

        notional_usdt = max(0.0, equity) * size_pct
        # Quantity in base currency; fallback to 0 if no price.
        quantity = notional_usdt / current_price if current_price > 0 else 0.0
        if quantity <= 0:
            # Cannot size without a price — fall back to base notional fraction.
            quantity = self._config.base_usdt * size_pct / max(current_price, 1e-9)
        if suppressed:
            quantity = 0.0

        reasoning = signal.rationale
        if signal.risk_notes:
            reasoning = f"{rationale_keep(reasoning)} | risk: {signal.risk_notes}"
        if guardrail_adjustments:
            reasoning = (
                f"{reasoning} | guardrail: phase={phase} "
                f"({', '.join(guardrail_adjustments)})"
            )

        risk_params: dict[str, Any] = {
            "strategy_id": self._config.strategy_id,
            "llm_confidence": confidence,
            "suggested_size_pct": size_pct,
            "max_position_pct": self._config.max_position_pct,
            "signal_action": action,
            "llm_provider": self._config.llm_provider,
        }
        if guardrail_adjustments:
            risk_params["guardrail_adjustments"] = guardrail_adjustments

        return TradeIntent(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=current_price if current_price > 0 else None,
            order_type=OrderType.MARKET,
            reasoning=reasoning,
            confidence=confidence,
            risk_params=risk_params,
            timestamp=int(time.time() * 1000),
        )


def rationale_keep(text: str) -> str:
    """Truncate rationale to a reasonable display length (defensive)."""
    cap = 480
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + "…"
