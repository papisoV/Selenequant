"""Advanced risk rules — research-backed risk controls for SeleneQuant.

Seven rules derived from cross-validated quantitative trading research
(sumubai.cc 120 articles + laoyulaoyu 700 articles):

  1. ATRDynamicStopRule    — 3-layer ATR stop: initial → ratchet → breakeven
  2. FloatingLossBlockRule — alert when existing position in loss
  3. DrawdownCircuitBreakerRule — HARD BLOCK when drawdown > 30% (CRITICAL)
  4. OnePercentRiskRule    — alert when single trade risk > 1% of equity
  5. KellyPositionSizingRule — Kelly criterion position sizing (half-Kelly)
  6. IOCLoopCloseRule      — suggest IOC with max slippage for stop-loss closes
  7. DailyLossFuseRule     — HARD BLOCK when daily loss hits threshold (CRITICAL)

Rules 3 and 7 return CRITICAL (hard block — trade is denied). Others return
advisory levels (DANGEROUS, RESTRICTED, WARNING, INFO).
"""

from __future__ import annotations

import time
from typing import Any

from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel


def _now_ms() -> int:
    return int(time.time() * 1000)


def _alert(rule_id: str, level: RiskLevel, message: str, **context: Any) -> RiskAlert:
    return RiskAlert(
        rule_id=rule_id,
        level=level,
        message=message,
        context=context,
        timestamp=_now_ms(),
    )


# ---------------------------------------------------------------------------
# 1. ATRDynamicStopRule (Three-Layer Engine)
# ---------------------------------------------------------------------------


class ATRDynamicStopRule:
    """Three-layer ATR dynamic stop loss engine.

    Layer 1 (Initial): SL = entry ± initial_atr_mult × ATR
    Layer 2 (Dynamic): SL only ratchets in favorable direction (never retreats)
    Layer 3 (Breakeven): When profit ≥ breakeven_atr_mult × ATR, move SL to entry

    Context keys:
        positions (dict[str, dict]): symbol → {entry_price, current_atr,
            highest_since_entry, lowest_since_entry, current_stop_price}
    """

    def __init__(
        self,
        initial_atr_mult: float = 1.5,
        breakeven_atr_mult: float = 2.0,
        *,
        enabled: bool = True,
    ) -> None:
        self._initial_mult = initial_atr_mult
        self._breakeven_mult = breakeven_atr_mult
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "atr_dynamic_stop"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def compute_stop_levels(
        self,
        entry_price: float,
        current_atr: float,
        direction: str,
        highest_since_entry: float,
        lowest_since_entry: float,
        current_stop_price: float | None = None,
    ) -> dict[str, Any]:
        """Compute three-layer ATR stop levels.

        Returns:
            Dict with stop_price, is_breakeven, layer.
        """
        if current_atr <= 0:
            return {
                "stop_price": current_stop_price or entry_price,
                "is_breakeven": False,
                "layer": 0,
            }

        if direction == "long":
            initial_sl = entry_price - self._initial_mult * current_atr
            best_price = highest_since_entry
            profit = best_price - entry_price
            dynamic_sl = best_price - self._initial_mult * current_atr
            breakeven_threshold = self._breakeven_mult * current_atr
        else:
            initial_sl = entry_price + self._initial_mult * current_atr
            best_price = lowest_since_entry
            profit = entry_price - best_price
            dynamic_sl = best_price + self._initial_mult * current_atr
            breakeven_threshold = self._breakeven_mult * current_atr

        # Layer 3: Breakeven trigger
        if profit >= breakeven_threshold:
            return {"stop_price": entry_price, "is_breakeven": True, "layer": 3}

        # Layer 2: Ratchet (only move in favorable direction)
        if current_stop_price is not None:
            if direction == "long":
                new_sl = max(current_stop_price, dynamic_sl)
            else:
                new_sl = min(current_stop_price, dynamic_sl)
            if new_sl != current_stop_price:
                return {"stop_price": new_sl, "is_breakeven": False, "layer": 2}
            return {"stop_price": current_stop_price, "is_breakeven": False, "layer": 2}

        # Layer 1: Initial stop
        return {"stop_price": initial_sl, "is_breakeven": False, "layer": 1}

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        positions: dict[str, dict] = context.get("positions", {})
        pos_data = positions.get(intent.symbol)
        if not pos_data or not isinstance(pos_data, dict):
            return None

        entry_price = pos_data.get("entry_price", 0.0)
        current_atr = pos_data.get("current_atr", 0.0)
        if not entry_price or not current_atr:
            return None

        current_price = pos_data.get("current_price", intent.price or 0.0)
        if not current_price:
            return None

        direction = "long" if pos_data.get("side") == "buy" else "short"
        highest = pos_data.get("highest_since_entry", current_price)
        lowest = pos_data.get("lowest_since_entry", current_price)
        current_sl = pos_data.get("current_stop_price")

        levels = self.compute_stop_levels(
            entry_price,
            current_atr,
            direction,
            highest,
            lowest,
            current_sl,
        )

        stop_price = levels["stop_price"]
        if direction == "long" and current_price <= stop_price:
            return _alert(
                self.rule_id,
                RiskLevel.DANGEROUS,
                f"ATR止损触发：价格 {current_price:.2f} <= 止损 {stop_price:.2f}（第{levels['layer']}层）",
                direction=direction,
                current_price=current_price,
                stop_price=stop_price,
                layer=levels["layer"],
            )
        if direction == "short" and current_price >= stop_price:
            return _alert(
                self.rule_id,
                RiskLevel.DANGEROUS,
                f"ATR止损触发：价格 {current_price:.2f} >= 止损 {stop_price:.2f}（第{levels['layer']}层）",
                direction=direction,
                current_price=current_price,
                stop_price=stop_price,
                layer=levels["layer"],
            )

        return None


# ---------------------------------------------------------------------------
# 2. FloatingLossBlockRule
# ---------------------------------------------------------------------------


class FloatingLossBlockRule:
    """Alert when opening new position while existing position is in loss.

    Research finding: reduces drawdown from 40% to 25% with minimal
    impact on returns (675% vs 719% annual).

    Context keys:
        positions (dict[str, dict]): symbol → {unrealized_pnl_pct}
    """

    def __init__(
        self, loss_threshold_pct: float = 0.0, *, enabled: bool = True
    ) -> None:
        self._loss_threshold = loss_threshold_pct
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "floating_loss_block"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        is_closing = intent.risk_params.get(
            "reduce_only", False
        ) or intent.risk_params.get("is_closing", False)
        if is_closing:
            return None

        positions: dict[str, dict] = context.get("positions", {})
        portfolio_leverage: float = context.get("portfolio_leverage", 1.0)
        for symbol, pos_data in positions.items():
            if not isinstance(pos_data, dict):
                continue
            pnl_pct = pos_data.get("unrealized_pnl_pct", 0.0)
            # Leverage-adjusted: effective loss is amplified
            effective_pnl_pct = pnl_pct * portfolio_leverage
            if effective_pnl_pct < self._loss_threshold:
                nominal_pct = pnl_pct
                return _alert(
                    self.rule_id,
                    RiskLevel.RESTRICTED,
                    f"当前持仓 {symbol} 杠杆调整后亏损中（名义 {nominal_pct:.1%} × {portfolio_leverage:.1f}x = {effective_pnl_pct:.1%}），确定要开新仓吗？",
                    symbol=symbol,
                    pnl_pct=nominal_pct,
                    effective_pnl_pct=effective_pnl_pct,
                    portfolio_leverage=portfolio_leverage,
                )

        return None


# ---------------------------------------------------------------------------
# 3. DrawdownCircuitBreakerRule
# ---------------------------------------------------------------------------


class DrawdownCircuitBreakerRule:
    """Alert when account drawdown exceeds threshold.

    Research finding: 30% drawdown is the survival line.

    Context keys:
        total_equity (float): Current account equity.
        peak_equity (float): Historical peak equity.
    """

    def __init__(self, max_drawdown_pct: float = 0.15, *, enabled: bool = True) -> None:
        self._max_dd = max_drawdown_pct
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "drawdown_circuit_breaker"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        total_equity: float = context.get("total_equity", 0.0)
        peak_equity: float = context.get("peak_equity", 0.0)
        if total_equity <= 0 or peak_equity <= 0:
            return None

        drawdown = (peak_equity - total_equity) / peak_equity

        # total_equity already includes leveraged unrealized PnL (exchange marks
        # the position to market), so drawdown is already leverage-adjusted.
        # Do NOT re-multiply by portfolio_leverage — that double-counts and
        # trips the breaker at nominal 5% when real drawdown is only 5%.
        portfolio_leverage: float = context.get("portfolio_leverage", 1.0)
        effective_drawdown = drawdown

        if effective_drawdown >= self._max_dd:
            return _alert(
                self.rule_id,
                RiskLevel.CRITICAL,
                f"回撤已达 {drawdown:.1%}（杠杆 {portfolio_leverage:.1f}x，名义回撤即有效回撤），超过警戒线 {self._max_dd:.0%}，强制暂停交易",
                drawdown=drawdown,
                effective_drawdown=effective_drawdown,
                max_dd=self._max_dd,
                total_equity=total_equity,
                peak_equity=peak_equity,
                portfolio_leverage=portfolio_leverage,
            )
        return None


# ---------------------------------------------------------------------------
# 4. OnePercentRiskRule
# ---------------------------------------------------------------------------


class OnePercentRiskRule:
    """Alert when single trade risk exceeds 1% of account equity.

    Research finding: 5 consecutive losses = only 5% drawdown.
    Stricter than MaxSingleRiskRule (2%) with explicit 1% rule naming.

    Context keys:
        total_equity (float): Total account equity.
    """

    def __init__(self, max_risk_pct: float = 0.01, *, enabled: bool = True) -> None:
        self._max_pct = max_risk_pct
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "one_percent_risk"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        total_equity: float = context.get("total_equity", 0.0)
        if total_equity <= 0:
            return None

        stop_loss: float | None = intent.risk_params.get("stop_loss")
        entry_price = intent.price or context.get("current_price", 0.0)
        if not stop_loss or not entry_price:
            return None

        loss_per_unit = abs(entry_price - stop_loss)
        potential_loss = loss_per_unit * intent.quantity
        leverage: int = intent.risk_params.get("leverage", 1)
        leveraged_loss = potential_loss * leverage
        max_allowed = total_equity * self._max_pct

        if leveraged_loss > max_allowed:
            pct = leveraged_loss / total_equity
            return _alert(
                self.rule_id,
                RiskLevel.RESTRICTED,
                f"杠杆调整后潜在亏损 {leveraged_loss:.2f}（{pct:.1%}，{leverage}x杠杆），1%规则建议上限 {self._max_pct:.0%}",
                potential_loss=potential_loss,
                leveraged_loss=leveraged_loss,
                leverage=leverage,
                pct=pct,
                max_pct=self._max_pct,
            )
        return None


# ---------------------------------------------------------------------------
# 5. KellyPositionSizingRule
# ---------------------------------------------------------------------------


class KellyPositionSizingRule:
    """Kelly criterion position sizing as an advisory alert.

    f* = (bp - q) / b where b=odds, p=win_rate, q=1-p.
    Uses half-Kelly by default for safety.

    Returns INFO-level alert with kelly_adjusted_quantity in context.

    Context keys:
        total_equity (float): Total account equity.
        trade_history (list[dict]): Recent trades with {pnl} keys.
    """

    def __init__(
        self,
        kelly_fraction: float = 0.5,
        min_trades_for_estimation: int = 10,
        *,
        enabled: bool = True,
    ) -> None:
        self._kelly_fraction = kelly_fraction
        self._min_trades = min_trades_for_estimation
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "kelly_sizing"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        total_equity: float = context.get("total_equity", 0.0)
        if total_equity <= 0:
            return None

        trade_history: list[dict] = context.get("trade_history", [])
        if len(trade_history) < self._min_trades:
            return None

        wins = [t for t in trade_history if t.get("pnl", 0) > 0]
        losses = [t for t in trade_history if t.get("pnl", 0) < 0]

        if not wins or not losses:
            return None

        win_rate = len(wins) / len(trade_history)
        avg_win = sum(t["pnl"] for t in wins) / len(wins)
        avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses))

        if avg_loss == 0:
            return None

        odds = avg_win / avg_loss
        kelly_f = (odds * win_rate - (1 - win_rate)) / odds

        if kelly_f <= 0:
            return _alert(
                self.rule_id,
                RiskLevel.WARNING,
                f"凯利值为负（{kelly_f:.4f}），当前策略无正期望，建议跳过",
                kelly_f=kelly_f,
                win_rate=win_rate,
                odds=odds,
            )

        adjusted_f = kelly_f * self._kelly_fraction
        adjusted_f = min(adjusted_f, 0.25)

        adjusted_quantity = intent.quantity * (
            adjusted_f / max(intent.risk_params.get("position_fraction", 1.0), 0.01)
        )

        return _alert(
            self.rule_id,
            RiskLevel.INFO,
            f"凯利公式建议仓位 {adjusted_f:.2%}，调整数量 {adjusted_quantity:.6f}",
            kelly_f=kelly_f,
            adjusted_f=adjusted_f,
            adjusted_quantity=adjusted_quantity,
        )


# ---------------------------------------------------------------------------
# 5b. VolatilityScaledKellyRule
# ---------------------------------------------------------------------------


class VolatilityScaledKellyRule:
    """Kelly sizing scaled by inverse realized volatility.

    Same Kelly math as ``KellyPositionSizingRule`` (half-Kelly, capped at
    25% of equity), but multiplies the adjusted fraction by a
    vol-scaling factor::

        scale = baseline_vol / realized_vol    (clamped to [min_scale, max_scale])

    So when realized vol exceeds the baseline (turbulent market), position
    size shrinks; when realized vol is below baseline (quiet market),
    position size grows up to ``max_scale``. This is the standard
    "vol-targeting" overlay applied to Kelly.

    Context keys:
        total_equity (float): Total account equity.
        trade_history (list[dict]): Recent trades with {pnl} keys.
        returns (list[float] | np.ndarray | pd.Series): Periodic returns
            used to compute realized volatility. At least
            ``min_returns_for_vol`` samples required.
    """

    def __init__(
        self,
        kelly_fraction: float = 0.5,
        min_trades_for_estimation: int = 10,
        baseline_vol: float = 0.20,
        min_scale: float = 0.25,
        max_scale: float = 1.5,
        min_returns_for_vol: int = 20,
        returns_periods_per_year: int = 252,
        *,
        enabled: bool = True,
    ) -> None:
        self._kelly_fraction = kelly_fraction
        self._min_trades = min_trades_for_estimation
        self._baseline_vol = baseline_vol
        self._min_scale = min_scale
        self._max_scale = max_scale
        self._min_returns_for_vol = min_returns_for_vol
        self._periods_per_year = returns_periods_per_year
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "vol_scaled_kelly"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def _compute_realized_vol(self, returns: list[float]) -> float | None:
        """Annualized stdev of returns. Returns None if insufficient data."""
        if returns is None or len(returns) < self._min_returns_for_vol:
            return None
        try:
            n = len(returns)
            mean = sum(returns) / n
            var = sum((r - mean) ** 2 for r in returns) / max(n - 1, 1)
            stdev = var**0.5
            return float(stdev * (self._periods_per_year**0.5))
        except Exception:
            return None

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        total_equity: float = context.get("total_equity", 0.0)
        if total_equity <= 0:
            return None

        trade_history: list[dict] = context.get("trade_history", [])
        if len(trade_history) < self._min_trades:
            return None

        wins = [t for t in trade_history if t.get("pnl", 0) > 0]
        losses = [t for t in trade_history if t.get("pnl", 0) < 0]
        if not wins or not losses:
            return None

        win_rate = len(wins) / len(trade_history)
        avg_win = sum(t["pnl"] for t in wins) / len(wins)
        avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses))
        if avg_loss == 0:
            return None

        odds = avg_win / avg_loss
        kelly_f = (odds * win_rate - (1 - win_rate)) / odds
        if kelly_f <= 0:
            return _alert(
                self.rule_id,
                RiskLevel.WARNING,
                f"凯利值为负（{kelly_f:.4f}），当前策略无正期望，建议跳过",
                kelly_f=kelly_f,
                win_rate=win_rate,
                odds=odds,
            )

        adjusted_f = kelly_f * self._kelly_fraction
        adjusted_f = min(adjusted_f, 0.25)

        # Volatility scaling overlay.
        returns = context.get("returns", []) or []
        realized_vol = self._compute_realized_vol(list(returns))
        if realized_vol is None or realized_vol <= 0:
            # No vol data — fall back to unscaled Kelly (scale = 1.0).
            vol_scale = 1.0
            scaled_f = adjusted_f
        else:
            vol_scale = self._baseline_vol / realized_vol
            vol_scale = max(self._min_scale, min(self._max_scale, vol_scale))
            scaled_f = adjusted_f * vol_scale

        scaled_f = max(0.0, min(scaled_f, 0.25))  # hard ceiling
        position_fraction = max(intent.risk_params.get("position_fraction", 1.0), 0.01)
        adjusted_quantity = intent.quantity * (scaled_f / position_fraction)

        msg = (
            f"波动率自适应凯利：kelly={kelly_f:.4f}，半凯利={adjusted_f:.2%}，"
            f"实现波动率={realized_vol:.2%}"
            if realized_vol is not None
            else f"波动率自适应凯利：kelly={kelly_f:.4f}，半凯利={adjusted_f:.2%}，无波动率数据"
        )
        msg += f"，缩放系数={vol_scale:.2f}，最终仓位={scaled_f:.2%}"

        return _alert(
            self.rule_id,
            RiskLevel.INFO,
            msg,
            kelly_f=kelly_f,
            adjusted_f=adjusted_f,
            scaled_f=scaled_f,
            vol_scale=vol_scale,
            realized_vol=realized_vol,
            baseline_vol=self._baseline_vol,
            adjusted_quantity=adjusted_quantity,
        )


# ---------------------------------------------------------------------------
# 6. IOCLoopCloseRule
# ---------------------------------------------------------------------------


class IOCLoopCloseRule:
    """Suggest IOC order type with max slippage for stop-loss closes.

    Research finding: market orders for stop loss cause excessive slippage.
    IOC with 0.2% max slippage ensures better fills.

    Context keys: (none required — checks intent.risk_params)
    """

    def __init__(
        self, max_slippage_pct: float = 0.002, *, enabled: bool = True
    ) -> None:
        self._max_slippage = max_slippage_pct
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "ioc_loop_close"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        is_stop_loss = intent.risk_params.get("is_stop_loss", False)
        if not is_stop_loss:
            return None

        order_type = (
            intent.order_type.value
            if hasattr(intent.order_type, "value")
            else str(intent.order_type)
        )
        if order_type == "market":
            return _alert(
                self.rule_id,
                RiskLevel.WARNING,
                f"止损单建议使用IOC而非市价单，最大滑点 {self._max_slippage:.1%}",
                order_type=order_type,
                max_slippage=self._max_slippage,
            )

        slippage = intent.risk_params.get("max_slippage", 0.0)
        if slippage > self._max_slippage:
            return _alert(
                self.rule_id,
                RiskLevel.WARNING,
                f"止损滑点 {slippage:.1%} 超过建议上限 {self._max_slippage:.1%}",
                slippage=slippage,
                max_slippage=self._max_slippage,
            )

        return None


# ---------------------------------------------------------------------------
# 7. DailyLossFuseRule
# ---------------------------------------------------------------------------


class DailyLossFuseRule:
    """Alert when daily loss hits threshold, with cooldown tracking.

    Unlike DailyLossLimitRule (alerts once), this rule tracks a cooldown
    period and continues alerting during the cooldown.
    Maintains internal state: _fuse_triggered_at timestamp.

    Context keys:
        total_equity (float): Total account equity.
        daily_realized_pnl (float): Today's realized PnL.
    """

    def __init__(
        self,
        daily_loss_pct: float = 0.01,
        cooldown_seconds: float = 3600.0,
        *,
        enabled: bool = True,
    ) -> None:
        self._daily_loss_pct = daily_loss_pct
        self._cooldown = cooldown_seconds
        self._enabled = enabled
        self._fuse_triggered_at: float | None = None

    @property
    def rule_id(self) -> str:
        return "daily_loss_fuse"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def reset(self) -> None:
        """Reset fuse state (e.g., at start of new trading day)."""
        self._fuse_triggered_at = None

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        total_equity: float = context.get("total_equity", 0.0)
        if total_equity <= 0:
            return None

        daily_pnl: float = context.get("daily_realized_pnl", 0.0)
        # total_equity already reflects leveraged PnL; do not re-multiply.
        portfolio_leverage: float = context.get("portfolio_leverage", 1.0)
        effective_loss = abs(daily_pnl)
        max_loss = total_equity * self._daily_loss_pct
        now = time.time()

        # If fuse was triggered, check cooldown
        if self._fuse_triggered_at is not None:
            elapsed = now - self._fuse_triggered_at
            if elapsed < self._cooldown:
                remaining = self._cooldown - elapsed
                return _alert(
                    self.rule_id,
                    RiskLevel.CRITICAL,
                    f"日亏损熔断中，冷却剩余 {remaining:.0f}s",
                    remaining=remaining,
                    cooldown=self._cooldown,
                )
            # Cooldown expired, reset fuse
            self._fuse_triggered_at = None

        # Check if daily loss triggers fuse (nominal loss IS the effective loss)
        if daily_pnl < 0 and effective_loss >= max_loss:
            self._fuse_triggered_at = now
            nominal_pct = abs(daily_pnl) / total_equity
            effective_pct = effective_loss / total_equity
            return _alert(
                self.rule_id,
                RiskLevel.CRITICAL,
                f"今日亏损已达 {effective_pct:.1%}（杠杆 {portfolio_leverage:.1f}x，名义亏损即有效亏损），触发熔断，强制暂停 {self._cooldown:.0f}s",
                daily_loss=abs(daily_pnl),
                effective_loss=effective_loss,
                nominal_pct=nominal_pct,
                effective_pct=effective_pct,
                cooldown=self._cooldown,
                portfolio_leverage=portfolio_leverage,
            )

        return None


# ---------------------------------------------------------------------------
# 7b. IvHvBubbleRule
# ---------------------------------------------------------------------------


class IvHvBubbleRule:
    """Options IV/HV bubble index as an advisory alert.

    ratio = implied_vol / realized_vol(historical).
    - ratio > sell_threshold (default 1.01): options are expensive ->
      selling premium is favored (WARNING).
    - ratio < buy_threshold (default 0.85): options are cheap ->
      buying options is favored (INFO).
    - In between or missing data: None (rule stays silent).

    Context keys:
        iv_annualized (float): ATM implied vol (decimal, e.g. 0.65),
            typically from MarketDataService.get_atm_iv.
        returns (list[float] | np.ndarray | pd.Series): Periodic returns
            used to compute realized volatility (same contract as
            VolatilityScaledKellyRule).
    """

    def __init__(
        self,
        sell_threshold: float = 1.01,
        buy_threshold: float = 0.85,
        min_returns_for_vol: int = 20,
        returns_periods_per_year: int = 252,
        *,
        enabled: bool = True,
    ) -> None:
        self._sell_threshold = sell_threshold
        self._buy_threshold = buy_threshold
        self._min_returns_for_vol = min_returns_for_vol
        self._periods_per_year = returns_periods_per_year
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "iv_hv_bubble"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def _compute_realized_vol(self, returns: list[float]) -> float | None:
        """Annualized stdev of returns. Returns None if insufficient data."""
        if returns is None or len(returns) < self._min_returns_for_vol:
            return None
        try:
            n = len(returns)
            mean = sum(returns) / n
            var = sum((r - mean) ** 2 for r in returns) / max(n - 1, 1)
            stdev = var**0.5
            return float(stdev * (self._periods_per_year**0.5))
        except Exception:
            return None

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        iv = context.get("iv_annualized")
        if not isinstance(iv, (int, float)) or iv <= 0:
            return None

        returns = context.get("returns")
        hv = self._compute_realized_vol(list(returns) if returns is not None else [])
        if hv is None or hv <= 0:
            return None

        ratio = iv / hv
        if ratio > self._sell_threshold:
            return _alert(
                self.rule_id,
                RiskLevel.WARNING,
                f"IV/HV={ratio:.2f}（IV {iv:.1%} vs HV {hv:.1%}），期权溢价明显，卖权收权利金有利",
                iv=iv,
                hv=hv,
                ratio=ratio,
            )
        if ratio < self._buy_threshold:
            return _alert(
                self.rule_id,
                RiskLevel.INFO,
                f"IV/HV={ratio:.2f}（IV {iv:.1%} vs HV {hv:.1%}），期权被低估，买权有利",
                iv=iv,
                hv=hv,
                ratio=ratio,
            )
        return None


# ---------------------------------------------------------------------------
# Convenience: advanced rule set
# ---------------------------------------------------------------------------


def create_advanced_rules() -> list:
    """Create the seven research-backed risk rules with default parameters."""
    return [
        ATRDynamicStopRule(),
        FloatingLossBlockRule(),
        DrawdownCircuitBreakerRule(),
        OnePercentRiskRule(),
        KellyPositionSizingRule(),
        IOCLoopCloseRule(),
        DailyLossFuseRule(),
    ]
