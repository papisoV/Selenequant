"""AlphaFormulaStrategy - bridges AI-discovered formulas into SeleneQuant's template system.

This is the core integration point: a BacktestStrategyTemplate subclass
that uses PandasStackVM to execute AI-discovered formula token sequences
on pandas DataFrames, producing discrete SignalType values.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .vm import PandasStackVM
from .vocab import FORMULA_VOCAB, FormulaVocab


def _as_name_tuple(value: Any) -> tuple[str, ...] | None:
    """Coerce a declared vocab field to a tuple of names, or None if unusable.

    The block is written by hand in strategy JSON, so a field may be a plain
    string ("CEX_FEATURE_NAMES (11)") instead of a list. ``tuple()`` on a string
    char-splits it — a 21-character "feature set" whose indices decode to
    garbage and whose operator map ends up empty, so ``PandasStackVM.execute``
    returns None and the backtest 500s. Anything that isn't a sequence of
    strings counts as absent.
    """
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        return None
    names = tuple(str(v) for v in value)
    return names or None


def _mining_torch_vocab() -> FormulaVocab:
    """The vocab the torch mining run builds its tokens against.

    ``scripts/research-archive/run_alpha_mining_sharpe.py`` constructs
    ``FormulaVocab(CEX_FEATURE_NAMES, tuple(cfg[0] for cfg in OPS_CONFIG))`` —
    the 11 CEX features plus the 21 torch-ordered operators (GATE included).
    An artifact tagged ``vocab.kind == "mining_torch"`` carries tokens in *that*
    space; decoding them with the V4 fallback shifts every operator index past
    slot 10 and the StackVM returns None.

    ``OPERATOR_DEFS[:21]`` mirrors the torch ``OPS_CONFIG`` names exactly
    (asserted in test_strategy_bridge_vocab) without importing torch, so this
    stays usable on the pandas-only backtest path.
    """
    from .ops.registry import OPERATOR_DEFS
    from .real_data_loader import CEX_FEATURE_NAMES

    return FormulaVocab(
        feature_names=tuple(CEX_FEATURE_NAMES),
        operator_names=tuple(name for name, _arity in OPERATOR_DEFS[:21]),
    )


def resolve_artifact_vocab(payload: dict[str, Any] | None) -> FormulaVocab:
    """The vocab an alpha artifact declares for its own token list.

    Mined formulas are searched in ``FORMULA_VOCAB`` (``LIQ_SCORE`` at slot 1,
    plus the FIB/FIB2/FIB3/TF families). The legacy CEX_V4 set is a 27-feature
    subset, so decoding a mined token list with it shifts every index — the
    backtest then runs a *different* formula without raising. An artifact's own
    ``vocab`` block therefore wins when present; the CEX_V4 fallback exists only
    for legacy archives written before the block existed.

    Empty, absent, or non-list fields count as absent, so a writer that has
    nothing (or only prose) to declare inherits the default rather than decoding
    against a char-split string. A block tagged ``kind: "mining_torch"`` names
    the vocab structurally (its token space is the mining run's, not V4), so the
    kind wins over the V4 fallback even when the name fields are prose.
    """
    # Local imports: real_data_loader pulls torch, and most strategy_bridge
    # users (training, live signals) never resolve an artifact vocab.
    from .ops.ops_pandas import OPS_CONFIG_PANDAS
    from .real_data_loader import CEX_FEATURE_NAMES_V4

    block = (payload or {}).get("vocab") or {}
    if block.get("kind") == "mining_torch":
        return _mining_torch_vocab()

    v4_ops = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    return FormulaVocab(
        feature_names=_as_name_tuple(block.get("feature_names"))
        or CEX_FEATURE_NAMES_V4,
        operator_names=_as_name_tuple(block.get("operator_names")) or v4_ops,
    )


class AlphaFormulaStrategy:
    """Strategy powered by an AI-discovered formula token sequence.

    Can be used standalone or adapted to work with SeleneQuant's
    BacktestStrategyTemplate system.

    Usage:
        strategy = AlphaFormulaStrategy(formula_tokens=[0, 6, 3, 1])
        signal = strategy.get_signal(data)  # -> "BUY" | "SELL" | "HOLD"
        score = strategy.compute_score(data)  # -> float 0..1
    """

    def __init__(
        self,
        formula_tokens: list[int],
        vocab: FormulaVocab | None = None,
        buy_threshold: float = 0.85,
        sell_threshold: float = 0.45,
        name: str = "alpha_formula",
        aux_interval: str | None = None,
    ):
        self._formula_tokens = formula_tokens
        self._vocab = vocab or FORMULA_VOCAB
        self._vm = PandasStackVM(self._vocab)
        self._buy_threshold = buy_threshold
        self._sell_threshold = sell_threshold
        self._name = name
        self._aux_interval = aux_interval
        self._aux_buffer: list[dict] = []
        self._kline_buffer: list[dict] = []
        self._current_position: int = 0
        self._fast_scores: pd.Series | None = None
        self._fast_pos: int = 0

    @property
    def formula_tokens(self) -> list[int]:
        return self._formula_tokens

    @property
    def formula_readable(self) -> str:
        return self._vocab.decode(self._formula_tokens)

    @property
    def buy_threshold(self) -> float:
        return self._buy_threshold

    @property
    def sell_threshold(self) -> float:
        return self._sell_threshold

    def reset(self) -> None:
        """Reset internal state (compatible with BacktestEngine's StrategyRunner protocol)."""
        self._kline_buffer = []
        self._aux_buffer = []
        self._current_position = 0
        self._fast_scores = None
        self._fast_pos = 0

    @property
    def bar_count(self) -> int:
        """Number of bars accumulated (used by StrategyRunner.get_slot_status)."""
        return len(getattr(self, "_kline_buffer", []))

    def prepare_full_series(self, df: pd.DataFrame, *, verify: bool = True) -> bool:
        """Pre-compute the score for every bar in one vectorized pass.

        The backtest engine replays bars in order, so a formula whose value at
        bar *t* depends only on bars ≤ *t* yields the same score whether it is
        recomputed over a trailing 200-row window (the bar-by-bar path) or run
        once over the whole series. arming this path turns an O(bars × window)
        backtest into one vectorized pass plus O(1) lookups per bar.

        Spot-check equivalence before arming: sample positions in the prepared
        series and compare against the bar-by-bar path (a trailing-200 frame).
        The check is done by construction of the prepared series itself — if
        any sampled position disagrees, the fast path silently carries wrong
        numbers, so on mismatch we refuse and the caller keeps the slow path.
        Windowless operators (RANK/JUMP rank over the whole given frame) shift
        semantics on a longer frame, and the spot check catches them here too —
        no operator whitelist to maintain.
        """
        if self._aux_interval:
            return False
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            return False
        if len(df) < 80:
            return False

        full = df.reset_index(drop=True).copy()
        full["liquidity"] = full.get("liquidity", 0.0)
        full["fdv"] = full.get("fdv", 0.0)
        scores = self._vm.execute(self._formula_tokens, full)
        if scores is None or len(scores) == 0:
            return False

        if verify:
            for pos in self._verify_positions(len(full)):
                window = full.iloc[max(0, pos - 200) : pos + 1]
                slow = self._vm.execute(self._formula_tokens, window)
                if slow is None or len(slow) == 0:
                    return False
                fast_val = float(scores.iloc[pos])
                slow_val = float(slow.iloc[-1])
                if math.isnan(fast_val) != math.isnan(slow_val):
                    return False
                # Rolling operators keep a running sum whose rounding history
                # depends on frame length, so the trailing-200 recompute can
                # differ from the full-series pass in the last ULP. That noise
                # floor is ~1e-16 relative; windowless operators (RANK/JUMP)
                # diverge at ~1e-2 — the tight tolerance separates them by
                # 14 orders of magnitude.
                if not math.isclose(fast_val, slow_val, rel_tol=1e-9, abs_tol=1e-12):
                    return False

        self._fast_scores = pd.Series(scores.to_numpy(), index=range(len(full)))
        self._fast_pos = 0
        return True

    @staticmethod
    def _verify_positions(n: int) -> list[int]:
        """Sample positions to verify: early warmup, a few mid, and the tail."""
        return sorted({0, 59, 60, 199, 200, n // 4, n // 2, 3 * n // 4, n - 2, n - 1})

    @property
    def position(self) -> int:
        """Current position: 1=long, -1=short, 0=flat (used by StrategyRunner.get_slot_status)."""
        return getattr(self, "_current_position", 0)

    def on_kline(self, kline: Any) -> Any | None:
        """Process a single kline and return a Signal (compatible with BacktestEngine).

        Takes a KlineEvent, builds a minimal DataFrame window, and returns
        a Signal object for BUY/SELL or None for HOLD.

        If aux_interval is set, checks incoming kline interval to route
        base vs aux klines to separate buffers, then merges them.
        """
        from selene_space_types.strategy import Signal as BtSignal, OrderSide

        kline_interval = getattr(kline, "interval", None)

        # Route to aux buffer if this kline matches aux_interval
        if self._aux_interval and kline_interval == self._aux_interval:
            self._aux_buffer.append(
                {
                    "open": float(kline.open),
                    "high": float(kline.high),
                    "low": float(kline.low),
                    "close": float(kline.close),
                    "volume": float(kline.volume),
                }
            )
            if len(self._aux_buffer) > 200:
                self._aux_buffer = self._aux_buffer[-200:]
            # Aux klines don't produce signals — only base klines do
            return None

        # Base interval kline
        self._kline_buffer.append(
            {
                "open": float(kline.open),
                "high": float(kline.high),
                "low": float(kline.low),
                "close": float(kline.close),
                "volume": float(kline.volume),
                "liquidity": 0.0,
                "fdv": 0.0,
            }
        )

        # Keep a reasonable window (need enough for rolling z-score)
        if len(self._kline_buffer) > 200:
            self._kline_buffer = self._kline_buffer[-200:]

        # Every base bar consumes exactly one slot of the prepared series,
        # counted from the very first bar so warmup bars (buffer < 60) stay
        # aligned with their frame positions.
        self._fast_pos += 1

        # Need at least 60 bars for meaningful z-score
        if len(self._kline_buffer) < 60:
            return None

        score = self._fast_score_at() if self._fast_scores is not None else None

        if score is None:
            self._fast_scores = None
            df = pd.DataFrame(self._kline_buffer)

            # Merge aux timeframe data if available
            if self._aux_interval and self._aux_buffer:
                df = self._merge_aux(df)

            # One VM pass covers both calls: get_signal and compute_score each
            # re-execute the same formula on the same frame, and feature
            # computation (entropy loops, EMA chains) dominates the cost.
            score_series = self._vm.execute(self._formula_tokens, df)
            if score_series is None or len(score_series) == 0:
                sig_str = "HOLD"
                strength = 0.5
            else:
                latest_raw = float(score_series.iloc[-1])
                strength = 1.0 / (1.0 + math.exp(-latest_raw))
                sig_str = (
                    "BUY"
                    if strength > self._buy_threshold
                    else "SELL"
                    if strength < self._sell_threshold
                    else "HOLD"
                )
            if sig_str == "SELL":
                strength = 1.0 - strength
        else:
            sig_str = self._signal_from_score(score)
            # Same strength semantics as the slow path: sigmoid of the raw
            # score, inverted for SELL so strength always measures conviction.
            strength = 1.0 / (1.0 + math.exp(-score))
            if sig_str == "SELL":
                strength = 1.0 - strength

        if sig_str == "BUY":
            self._current_position = 1
            return BtSignal(
                strategy_id=self._name,
                symbol=kline.symbol,
                side=OrderSide.BUY,
                strength=strength,
                timestamp=kline.timestamp,
                reason=f"alpha-formula {sig_str}",
            )
        elif sig_str == "SELL":
            self._current_position = -1
            return BtSignal(
                strategy_id=self._name,
                symbol=kline.symbol,
                side=OrderSide.SELL,
                strength=strength,
                timestamp=kline.timestamp,
                reason=f"alpha-formula {sig_str}",
            )
        return None

    def _fast_score_at(self) -> float | None:
        """O(1) score lookup for the current bar from the prepared series.

        ``_fast_pos`` counts every base bar consumed (1-based after the
        increment in :meth:`on_kline`), so the score for the bar just appended
        sits at frame position ``_fast_pos - 1``.
        """
        if self._fast_scores is None:
            return None
        pos = self._fast_pos - 1
        if pos < 0 or pos >= len(self._fast_scores):
            return None
        return float(self._fast_scores.iloc[pos])

    def _signal_from_score(self, score: float) -> str:
        sigmoid = 1.0 / (1.0 + math.exp(-score))
        if sigmoid > self._buy_threshold:
            return "BUY"
        if sigmoid < self._sell_threshold:
            return "SELL"
        return "HOLD"

    def _merge_aux(self, df: pd.DataFrame) -> pd.DataFrame:
        """Merge auxiliary timeframe columns into the base DataFrame.

        Uses forward-fill to align lower-frequency aux data with base bars,
        matching the backtest endpoint's merge logic.
        """
        assert self._aux_interval is not None  # 调用点 L133 truthy 守卫已排除 None
        aux_df = pd.DataFrame(self._aux_buffer)
        if aux_df.empty or len(aux_df) < 2:
            return df

        prefix = f"TF{self._aux_interval.replace('m', 'M') if 'm' in self._aux_interval else self._aux_interval}_"

        # Forward-fill: repeat each aux bar to cover the base bars it applies to
        # Simple approach: tile aux rows to match base length
        n_base = len(df)
        n_aux = len(aux_df)
        ratio = max(1, n_base // n_aux + 1)
        aux_tiled = (
            aux_df.loc[aux_df.index.repeat(ratio)].head(n_base).reset_index(drop=True)
        )
        aux_tiled.columns = [f"{prefix}{c}" for c in aux_tiled.columns]

        return pd.concat([df.reset_index(drop=True), aux_tiled], axis=1)

    def compute_score(self, data: pd.DataFrame) -> float:
        """Compute the raw sigmoid score for the latest bar.

        Args:
            data: DataFrame with OHLCV + liquidity/fdv columns.

        Returns:
            Score between 0 and 1, or 0.5 on error.
        """
        score_series = self._vm.execute(self._formula_tokens, data)
        if score_series is None or len(score_series) == 0:
            return 0.5
        latest_raw = float(score_series.iloc[-1])
        return 1.0 / (1.0 + math.exp(-latest_raw))

    def get_signal(self, data: pd.DataFrame) -> str:
        """Generate a trading signal.

        Args:
            data: DataFrame with OHLCV + liquidity/fdv columns.

        Returns:
            "BUY", "SELL", or "HOLD"
        """
        score = self.compute_score(data)
        if score > self._buy_threshold:
            return "BUY"
        if score < self._sell_threshold:
            return "SELL"
        return "HOLD"

    def get_score_series(self, data: pd.DataFrame) -> pd.Series:
        """Raw numeric factor score per bar — the IC-decay input.

        Unlike ``get_signal_series`` (BUY/SELL/HOLD labels), this exposes the
        un-thresholded score series so downstream analyses can rank-correlate
        it against forward returns. VM failure degrades to NaN (not HOLD
        labels), which downstream stats skip.
        """
        score_series = self._vm.execute(self._formula_tokens, data)
        if score_series is None:
            return pd.Series(float("nan"), index=data.index)
        return pd.Series(score_series, index=data.index)

    def get_signal_series(self, data: pd.DataFrame) -> pd.Series:
        """Generate signals for every bar in the DataFrame.

        Args:
            data: DataFrame with OHLCV + liquidity/fdv columns.

        Returns:
            Series of "BUY"/"SELL"/"HOLD" strings.
        """
        score_series = self._vm.execute(self._formula_tokens, data)
        if score_series is None:
            return pd.Series("HOLD", index=data.index)

        sigmoid = 1.0 / (1.0 + np.exp(-score_series.values))
        signals = np.where(
            sigmoid > self._buy_threshold,
            "BUY",
            np.where(sigmoid < self._sell_threshold, "SELL", "HOLD"),
        )
        return pd.Series(signals, index=data.index)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the strategy for storage."""
        result = {
            "name": self._name,
            "formula_tokens": self._formula_tokens,
            "formula_readable": self.formula_readable,
            "buy_threshold": self._buy_threshold,
            "sell_threshold": self._sell_threshold,
            "vocab_feature_names": list(self._vocab.feature_names),
            "vocab_operator_names": list(self._vocab.operator_names),
        }
        if self._aux_interval:
            result["aux_interval"] = self._aux_interval
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlphaFormulaStrategy":
        """Deserialize a strategy from storage."""
        vocab = FormulaVocab(
            feature_names=tuple(
                data.get("vocab_feature_names", FORMULA_VOCAB.feature_names)
            ),
            operator_names=tuple(
                data.get("vocab_operator_names", FORMULA_VOCAB.operator_names)
            ),
        )
        return cls(
            formula_tokens=data["formula_tokens"],
            vocab=vocab,
            buy_threshold=data.get("buy_threshold", 0.85),
            sell_threshold=data.get("sell_threshold", 0.45),
            name=data.get("name", "alpha_formula"),
            aux_interval=data.get("aux_interval"),
        )

    def __repr__(self) -> str:
        return (
            f"AlphaFormulaStrategy("
            f"formula='{self.formula_readable}', "
            f"buy>{self._buy_threshold}, sell<{self._sell_threshold})"
        )
