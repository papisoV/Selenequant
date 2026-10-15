# Ported from AlphaPurify (MIT)
"""Weight-drift quantile backtest — a faithful pandas port of AlphaPurify
``FactorAnalyzer.calc_stats_for_period`` (``FactorAnalyzer.py`` L529-1325).

AlphaPurify ranks the factor cross-sectionally each date, buckets it into
``n_bins`` quantile portfolios, and drifts the equal-start weights forward
inside each rebalance epoch ``g`` by compounding forward returns, re-normalizing
per (date, quantile). A boundary "bridge" carries each epoch's drifted weights
into the next epoch's first (rebalance) date. Turnover and A-share transaction
costs are then charged on the g-pair transitions between rebalances, and
long/short statistics are annualized.

The polars source is translated to pandas with exact numeric semantics:
default ``sum()`` (min_count=0) for group sums, because polars ``group_by``
``sum()`` returns 0.0 for all-null groups while pandas ``sum(min_count=1)``
returns NaN (the standalone polars ``Series.sum()`` → None differs — only the
group-agg path matters here); a masked ``cumprod`` for the ``cum_ret_net_*``
columns (polars emits null at null inputs but keeps compounding),
``(s[s.notna()] > 0).mean()`` for win rate (polars drops null comparisons), and
``np.maximum.accumulate`` NaN poisoning for drawdown (kept verbatim so stats
match bit-for-bit).

Two AlphaPurify constructor guards are intentionally *not* ported: ``bins >= 3``
(n_bins=1 is a supported boundary here) and ``map_freq``/freq-based rebalance
(string epochs). This port supports integer ``epoch`` only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .cost_models import AShareCostModel, CostModel, validate_tax_rate_direction
from .weight_drift_result import WeightDriftResult


@dataclass
class WeightDriftConfig:
    """Parameters for :class:`WeightDriftBacktest`.

    Faithful port of the AlphaPurify ``ResearchConfig`` knobs used inside
    ``calc_stats_for_period`` (L15-26): ``bins`` → ``n_bins``, the rebalance
    spacing → ``epoch`` (integer trading days), and the three independent cost
    rates with the directional mapping string.

    ``w0_halflife`` / ``forward_fill_w0`` / ``bridge_epoch_boundary`` are Selene
    extensions (not in AlphaPurify). ``w0_halflife`` decays the drifted base
    weight by ``0.5 ** (bars_since_rebalance / w0_halflife)`` before compounding;
    ``0`` disables decay (exact AlphaPurify parity). ``forward_fill_w0=False``
    disables the within-epoch forward-fill of the initial weight.
    ``bridge_epoch_boundary=False`` skips the epoch-boundary bridge rows.
    """

    n_bins: int = 5
    epoch: int = 20
    w0_halflife: int = 10
    forward_fill_w0: bool = True
    bridge_epoch_boundary: bool = True
    # Independent cost rates + direction — L21-25 faithful (see cost_models).
    tax_rate: float = 0.0
    fee_rate: float = 0.0003
    slippage_rate: float = 0.001
    tax_rate_direction: str = "sell"
    cost_model: CostModel = field(default_factory=AShareCostModel)
    # --- Selene funding-carry gate (FD5) ---
    # charge_funding=True charges periodic funding on the extreme buckets via
    # cost_model.compute_funding; default OFF keeps the funding block inert and
    # the ported output bit-identical to the pre-funding code. funding_notional
    # scales the (already ~1.0) bucket notional into a portfolio size.
    charge_funding: bool = False
    funding_notional: float = 1.0

    # --- Selene interface extras ---
    base_rate: float = 0.02
    factor_col: str = "factor"
    trade_date_col: str = "trade_date"
    symbol_col: str = "symbol"
    ret_col: str = "fut_ret_1"
    agg_freq: str | None = None
    group_by: dict | None = None


def _flatten_pivot_cols(cols) -> list[str]:
    """Flatten a pandas pivot MultiIndex (value, quantile) into ``value_q``.

    The first column (the reset index / date column) is a bare string; only the
    pivoted value columns are ``(value, quantile)`` tuples.
    """
    out = []
    for c in cols:
        out.append(f"{c[0]}_{c[1]}" if isinstance(c, tuple) else str(c))
    return out


def _polars_cumprod(s: pd.Series) -> pd.Series:
    """Replicate polars ``(1 + col).cum_prod()`` over a series with nulls.

    polars emits null at null input positions but keeps compounding the running
    product. pandas ``cumprod`` propagates NaN, so fill nulls with 1 (a neutral
    factor), cumprod, then re-mask the null positions.
    """
    return (1.0 + s).fillna(1.0).cumprod().where(s.notna())


class WeightDriftBacktest:
    """Weight-drift quantile portfolio backtest (single-process pandas port)."""

    def run(
        self,
        factor_df: pd.DataFrame,
        ret_df: pd.DataFrame,
        config: WeightDriftConfig | None = None,
    ) -> WeightDriftResult:
        cfg = config or WeightDriftConfig()
        validate_tax_rate_direction(cfg.tax_rate_direction)
        # M3 fail-fast (FD5): charge_funding=True with a cost model that has no
        # compute_funding (e.g. the default AShareCostModel) is a config error —
        # raise immediately instead of silently running without any funding.
        if cfg.charge_funding and not hasattr(cfg.cost_model, "compute_funding"):
            raise ValueError(
                "charge_funding=True requires a cost model with compute_funding "
                "(e.g. SeleneCryptoCostModel); AShareCostModel charges no funding"
            )

        date_col = cfg.trade_date_col
        sym_col = cfg.symbol_col
        factor_col = cfg.factor_col
        bins = cfg.n_bins
        epoch = cfg.epoch
        if not isinstance(epoch, int):
            raise ValueError(f"epoch must be an int (trading days), got {epoch!r}")

        buy_fee, sell_fee = cfg.cost_model.compute_buy_sell_fee(cfg)

        # L210-211: calendar span of the input panel (full date range).
        _dt = pd.to_datetime(factor_df[date_col])
        days = (_dt.max() - _dt.min()).days

        # ================= 贴标签分箱 (L540-563) =================
        df = factor_df[[date_col, sym_col, factor_col]].copy()
        df[date_col] = pd.to_datetime(df[date_col])
        df["rank"] = df.groupby(date_col)[factor_col].rank(method="average", ascending=True)
        df["n_stocks"] = df.groupby(date_col)[factor_col].transform("size")
        df = df.sort_values([date_col, "rank"])  # L547
        df["pos_index"] = df.groupby(date_col).cumcount()  # arange(0, len) over date
        df["quantile_temp"] = (np.floor(df["pos_index"] * bins / df["n_stocks"])).astype(int) + 1
        df["quantile"] = df["quantile_temp"].clip(upper=bins)  # L558-562 min(., bins)
        df = df.drop(columns=["rank", "n_stocks", "pos_index", "quantile_temp"])
        df = df[df[factor_col].notna()]  # L563

        # ================= forward returns (L566-578) =================
        # AlphaPurify derives fut_ret_1 from prices; here ret_df carries it.
        ret = ret_df[[date_col, sym_col, cfg.ret_col]].copy()
        ret[date_col] = pd.to_datetime(ret[date_col])
        df = df.merge(ret, on=[date_col, sym_col], how="left")
        df["fut_ret_1"] = df[cfg.ret_col].where(df[cfg.ret_col] != -1)  # L576-578: -1 -> null
        if cfg.ret_col != "fut_ret_1":  # avoid dropping the column just created
            df = df.drop(columns=[cfg.ret_col])
        df = df[df["fut_ret_1"].notna()]  # L572: drop rows without a forward return
        if df.empty:
            raise ValueError(
                "empty backtest: no rows with a finite factor and forward return"
            )

        # ================= rebalance dates & g (L580-634) =================
        # all_dates / rebalance_dates come from the post-drop panel (L581-582).
        all_dates = sorted(df[date_col].unique())
        rebalance_dates = all_dates[::epoch]  # L582
        date_id = {d: i for i, d in enumerate(all_dates)}
        df["date_id"] = df[date_col].map(date_id)  # L592-594 dense rank - 1
        df["g"] = df["date_id"] // epoch  # L596-604

        def rebalance_mask() -> pd.Series:
            # rebalance_mask is an expression re-evaluated on the current df (L586-588).
            return df[date_col].isin(rebalance_dates)

        # ================= initial weights (L636-657) =================
        rebalance_df = df[rebalance_mask()][[date_col, sym_col, "quantile", "g"]]
        # Inner join replaces df's quantile with the (symbol, g) rebalance-date
        # quantile — the bucket is fixed for the whole epoch (L643-650).
        df = df.merge(
            rebalance_df[[sym_col, "g", "quantile"]].rename(columns={"quantile": "_q_right"}),
            on=[sym_col, "g"],
            how="inner",
        )
        df["quantile"] = df["_q_right"]
        df = df.drop(columns=["_q_right"])

        group_sizes = df.groupby([date_col, "quantile"])[sym_col].transform("size")
        df["w"] = np.where(rebalance_mask(), 1.0 / group_sizes, 0.0)  # L652-656
        rebalance_df = rebalance_df.sort_values([date_col, "quantile", sym_col])  # L657

        # ================= cross-date holdings + weight drift (L661-726) =================
        date_g = df[[date_col, "g"]].drop_duplicates()  # L661
        holdings = rebalance_df[[sym_col, "g", "quantile"]].drop_duplicates()  # L663-667
        # AlphaPurify's df_small omits global_date_id (L671-677), but the
        # w0_halflife decay extension needs bars = date_id - g*epoch after the
        # join, so carry date_id through — harmless, it is dropped at L234 and
        # unused by any parity-relevant column.
        df_small = df[[sym_col, date_col, "g", "date_id", "fut_ret_1", "w"]]  # L671-677
        df = holdings.merge(date_g, on="g", how="inner")  # L669 cross join
        df = df.merge(
            df_small,
            on=[sym_col, date_col, "g"],
            how="left",
        )  # L679-683
        df["w"] = df["w"].fillna(0.0)
        df["fut_ret_1"] = df["fut_ret_1"].fillna(0.0)  # L685-688

        df = df.sort_values([sym_col, date_col])  # L690
        df["w0"] = df["w"].where(df["w"] > 0)  # L692-696
        if cfg.forward_fill_w0:
            df["w0"] = df.groupby([sym_col, "g"])["w0"].ffill()  # L697-700

        # Computed Series (1.0 + ...) loses the parent-DataFrame link, so the
        # groupers must be actual column Series, not column-name strings.
        df["cum_ret"] = (1.0 + df["fut_ret_1"]).groupby([df[sym_col], df["g"]]).cumprod()  # L702-704
        df["cum_ret_lag"] = df.groupby([sym_col, "g"])["cum_ret"].shift(1)  # L706-708
        df["cum_ret_lag"] = df["cum_ret_lag"].fillna(1.0)  # L710-713

        df = df.sort_values(["g", date_col, "quantile", sym_col])  # L714

        # w0_halflife decay (Selene extension; 0 -> no decay, exact parity).
        if cfg.w0_halflife > 0:
            bars = df["date_id"] - df["g"] * epoch
            df["decay"] = np.power(0.5, bars / cfg.w0_halflife)
        else:
            df["decay"] = 1.0

        df["raw_w"] = df["w0"] * df["decay"] * df["cum_ret_lag"]  # L716-718
        df["norm"] = df.groupby([date_col, "quantile"])["raw_w"].transform("sum")  # L720-722
        df["w"] = df["raw_w"] / df["norm"]  # L724-726

        # ================= epoch-boundary bridge (L727-775) =================
        if cfg.bridge_epoch_boundary:
            g_info = (
                df.groupby("g", sort=False)[date_col]
                .agg(g_last_date="max", g_first_date="min")
                .reset_index()
                .sort_values("g_first_date")
            )
            g_info["next_g_first_date"] = g_info["g_first_date"].shift(-1)  # L737
            last_rows = df.merge(g_info, on="g", how="inner")
            last_rows = last_rows[last_rows[date_col] == last_rows["g_last_date"]]
            last_rows = last_rows[last_rows["next_g_first_date"].notna()]  # L746-750

            df_new = last_rows.copy()
            df_new[date_col] = df_new["next_g_first_date"]  # L753-756
            df_new["cum_ret_lag"] = df_new["cum_ret"]
            df_new["raw_w"] = df_new["w0"] * df_new["decay"] * df_new["cum_ret_lag"]  # L758-760
            df_new["norm"] = df_new.groupby([date_col, "quantile"])["raw_w"].transform("sum")  # L762-764
            df_new["w"] = df_new["raw_w"] / df_new["norm"]  # L766-768
            df_new["fut_ret_1"] = np.nan  # L770-774
            df_new = df_new[["g", date_col, "quantile", sym_col, "w", "fut_ret_1"]]
            df_new = df_new.sort_values(["g", date_col, "quantile", sym_col])
            df = pd.concat([df, df_new], ignore_index=True)  # L775

        df = df.drop(columns=["date_id", "w0", "cum_ret", "cum_ret_lag", "raw_w", "norm", "decay"])

        # ================= turnover + cost (L778-920) =================
        df_reb = df[rebalance_mask()]  # L778
        df_reb = df_reb.sort_values([date_col, "quantile", "g", sym_col])  # L797
        min_dt = df_reb[date_col].min()  # L799

        first_rows = (df_reb[date_col] == min_dt) & (df_reb["g"] == 0)  # L801-806
        df_init = df_reb[first_rows].copy()
        df_init["buy"] = df_init["w"]
        df_init["sell"] = 0.0
        df_init["turnover"] = df_init["buy"]
        df_init["cost"] = df_init["buy"] * buy_fee  # L808-811

        df_main = df_reb[~first_rows].sort_values(["g", date_col, "quantile", sym_col])  # L814-816

        # g_pairs: at each rebalance date, pair the bridge (g_prev) with the
        # normal (g_next) epoch group — turnover is charged on this transition.
        g_pairs = df_main[[date_col, "quantile", "g"]].drop_duplicates().sort_values(
            [date_col, "quantile", "g"]
        )
        g_pairs["g_prev"] = g_pairs.groupby([date_col, "quantile"])["g"].shift(1)  # L823-825
        g_pairs["g_next"] = g_pairs["g"]
        g_pairs = g_pairs[g_pairs["g_prev"].notna()]  # L827

        df_prev = df_main[[date_col, "quantile", "g", sym_col, "w"]].rename(
            columns={"g": "g_prev", "w": "w_prev"}
        )
        df_next = df_main[[date_col, "quantile", "g", sym_col, "w"]].rename(
            columns={"g": "g_next", "w": "w_next"}
        )
        df_prev = df_prev.merge(g_pairs, on=[date_col, "quantile", "g_prev"], how="inner")
        df_next = df_next.merge(g_pairs, on=[date_col, "quantile", "g_next"], how="inner")

        common = df_prev.merge(
            df_next,
            on=[date_col, "quantile", "g_prev", "g_next", sym_col],
            how="inner",
        )  # L854-858
        common["diff"] = common["w_next"] - common["w_prev"]  # L860-862
        # when(diff > 0) then diff else 0 — NaN > 0 is False, so NaN diffs yield 0.
        common["buy"] = np.where(common["diff"] > 0, common["diff"], 0.0)  # L864-867
        common["sell"] = np.where(common["diff"] < 0, -common["diff"], 0.0)

        only_prev = df_prev.merge(
            df_next[[date_col, "quantile", "g_prev", "g_next", sym_col]],
            on=[date_col, "quantile", "g_prev", "g_next", sym_col],
            how="left",
            indicator=True,
        )
        only_prev = only_prev[only_prev["_merge"] == "left_only"].drop(columns=["_merge"])
        only_prev["buy"] = 0.0
        only_prev["sell"] = only_prev["w_prev"]  # L870-879

        only_next = df_next.merge(
            df_prev[[date_col, "quantile", "g_prev", "g_next", sym_col]],
            on=[date_col, "quantile", "g_prev", "g_next", sym_col],
            how="left",
            indicator=True,
        )
        only_next = only_next[only_next["_merge"] == "left_only"].drop(columns=["_merge"])
        only_next["buy"] = only_next["w_next"]
        only_next["sell"] = 0.0  # L882-891

        df_pair = pd.concat([common, only_prev, only_next], ignore_index=True)  # L894

        buy_df = df_pair[[date_col, "quantile", "g_next", sym_col, "buy"]].rename(
            columns={"g_next": "g"}
        )
        sell_df = df_pair[[date_col, "quantile", "g_prev", sym_col, "sell"]].rename(
            columns={"g_prev": "g"}
        )
        df_main = df_main.merge(buy_df, on=[date_col, "quantile", "g", sym_col], how="left")
        df_main = df_main.merge(sell_df, on=[date_col, "quantile", "g", sym_col], how="left")
        df_main["buy"] = df_main["buy"].fillna(0.0)
        df_main["sell"] = df_main["sell"].fillna(0.0)  # L906-912
        df_main["turnover"] = df_main["buy"] + df_main["sell"]
        df_main["cost"] = df_main["buy"] * buy_fee + df_main["sell"] * sell_fee  # L914-917

        df_reb = pd.concat([df_init, df_main], ignore_index=True)  # L920

        # ================= df_agg (L922-942) =================
        df_agg = (
            df_reb.groupby([date_col, "quantile"], sort=False)[["buy", "sell", "turnover", "cost"]]
            .sum(min_count=1)
            .reset_index()
            .pivot(index=date_col, columns="quantile", values=["buy", "sell", "turnover", "cost"])
        )
        df_agg.columns = _flatten_pivot_cols(df_agg.columns)
        df_agg = df_agg.reset_index().sort_values(date_col).reset_index(drop=True)
        # A date can lose its highest-ranked symbols to NaN factors, leaving a
        # quantile empty on every rebalance date → the pivot omits those columns
        # (AlphaPurify would crash on the same input). Treat a missing bucket as 0.
        for _v in ("buy", "sell", "turnover", "cost"):
            for _q in range(1, bins + 1):
                _c = f"{_v}_{_q}"
                if _c not in df_agg.columns:
                    df_agg[_c] = 0.0
        df_agg["turnover_ls"] = (df_agg["turnover_1"] + df_agg[f"turnover_{bins}"]) / 2
        df_agg["cost_ls"] = (df_agg["cost_1"] + df_agg[f"cost_{bins}"]) / 2
        df_agg["buy_ls"] = (df_agg["buy_1"] + df_agg[f"buy_{bins}"]) / 2
        df_agg["sell_ls"] = (df_agg["sell_1"] + df_agg[f"sell_{bins}"]) / 2  # L937-942

        # ================= per-date returns (L946-1037) =================
        df = df.merge(
            df_reb[[date_col, "quantile", "g", sym_col, "buy", "sell", "turnover", "cost"]],
            on=[date_col, "quantile", "g", sym_col],
            how="left",
        )  # L946-952
        df = df.sort_values(["g", date_col, "quantile", sym_col])  # L953
        df["ret"] = df["w"] * df["fut_ret_1"]  # L954

        df_g = (
            df.groupby([date_col, "g", "quantile"], sort=False)[["ret", "cost"]]
            .sum()
            .reset_index()
        )  # L1003-1009: polars group-agg sum → 0.0 for all-null groups (min_count=0)
        df_g["ret_s"] = -df_g["ret"]  # L1010
        df_g = df_g.drop(columns=["g"])

        df_barly_q = (
            df_g.groupby([date_col, "quantile"], sort=False)[["ret", "ret_s", "cost"]]
            .sum()
            .reset_index()
        )  # L1014-1021: polars group-agg sum → 0.0 for all-null groups (min_count=0)

        df_result = df_barly_q.pivot(
            index=date_col, columns="quantile", values=["ret", "ret_s", "cost"]
        )
        _result_dates = df_result.index.copy()
        df_result.columns = _flatten_pivot_cols(df_result.columns)
        df_result = df_result.reset_index().sort_values(date_col).reset_index(drop=True)
        # Same missing-quantile tolerance as df_agg: a quantile absent from every
        # date (only rebalance-date holdings enter the panel) → fill 0.
        for _v in ("ret", "ret_s", "cost"):
            for _q in range(1, bins + 1):
                _c = f"{_v}_{_q}"
                if _c not in df_result.columns:
                    df_result[_c] = 0.0

        # ================= funding carry — Phase 1 (FD1) =================
        # Charge periodic funding on the extreme buckets. The notional is the
        # sum of the return-bearing group's weights per (date, quantile) —
        # bridge rows (fut_ret_1 NaN) are excluded so boundary dates where the
        # bridge + normal groups coexist don't double-charge (v4).
        if cfg.charge_funding:
            _f = df[df["fut_ret_1"].notna()].copy()
            _f["_d"] = _f[date_col].map(pd.Timestamp)
            _notional = _f.groupby(["_d", "quantile"], sort=False)["w"].sum()
            # M3 already guaranteed the attribute; bind once for mypy-clean dispatch.
            _compute_funding = getattr(cfg.cost_model, "compute_funding")
            _funding_top: dict = {}
            _funding_bottom: dict = {}
            for _d in df_result[date_col]:  # ascending dates — stateful funding needs monotonic ts
                _key = pd.Timestamp(_d)
                _ts_ms = int(_key.value // 10**6)
                _n_top = float(_notional.get((_key, bins), 0.0)) * cfg.funding_notional
                _n_bot = float(_notional.get((_key, 1), 0.0)) * cfg.funding_notional
                # Unconditional call even at zero notional: an empty bucket must
                # still advance _last_applied_idx so catch-up stays consistent.
                _funding_top[_key] = _compute_funding(_n_top, "long", _ts_ms)
                _funding_bottom[_key] = _compute_funding(_n_bot, "short", _ts_ms)
            df_result["funding_top"] = df_result[date_col].map(
                lambda _d: _funding_top[pd.Timestamp(_d)]
            )
            df_result["funding_bottom"] = df_result[date_col].map(
                lambda _d: _funding_bottom[pd.Timestamp(_d)]
            )

        for q in range(1, bins + 1):
            df_result[f"ret_net_{q}"] = df_result[f"ret_{q}"] - df_result[f"cost_{q}"]
            df_result[f"ret_net_s_{q}"] = df_result[f"ret_s_{q}"] - df_result[f"cost_{q}"]

        # ================= funding carry — Phase 2 (FD1) =================
        # Adjust the extreme legs' net returns before the cum loop below, so the
        # cum_ret_net_{bins} / cum_ret_net_s_1 columns pick up the funding (and
        # stats_l / stats_s cum-side moves with them). Positive funding_top
        # reduces the long bucket; short-leg funding is negative when the rate
        # is positive, so subtracting funding_bottom credits the short leg.
        if cfg.charge_funding:
            df_result[f"ret_net_{bins}"] = (
                df_result[f"ret_net_{bins}"] - df_result["funding_top"]
            )
            df_result["ret_net_s_1"] = df_result["ret_net_s_1"] - df_result["funding_bottom"]

        # cum exprs (L1041-1063) — cum_ret / ret_ls have no nulls; cum_ret_net masked.
        for q in range(1, bins + 1):
            if q == bins:
                df_result[f"cum_ret_{q}"] = (1.0 + df_result[f"ret_{q}"]).cumprod()
            elif q == 1:
                df_result[f"cum_ret_s_{q}"] = (1.0 + df_result[f"ret_s_{q}"]).cumprod()
            df_result[f"cum_ret_net_{q}"] = _polars_cumprod(df_result[f"ret_net_{q}"])
            df_result[f"cum_ret_net_s_{q}"] = _polars_cumprod(df_result[f"ret_net_s_{q}"])
        df_result["ret_ls"] = (df_result[f"ret_{bins}"] - df_result["ret_1"]) / 2  # L1055
        df_result["ret_net_ls"] = (
            df_result["ret_ls"] - (df_result["cost_1"] + df_result[f"cost_{bins}"]) / 2
        )  # L1058
        # ================= funding carry — Phase 3 (FD1) =================
        # ret_net_ls is computed independently of the per-leg ret_net, so the LS
        # leg must explicitly deduct the average funding; this must precede the
        # cum_ret_net_ls recomputation below (stats_ls cum-side needs it).
        if cfg.charge_funding:
            df_result["ret_net_ls"] = df_result["ret_net_ls"] - (
                df_result["funding_top"] + df_result["funding_bottom"]
            ) / 2
        df_result["cum_ret_ls"] = (1.0 + df_result["ret_ls"]).cumprod()
        df_result["cum_ret_net_ls"] = _polars_cumprod(df_result["ret_net_ls"])  # L1060-1062
        df_result = df_result.drop(
            columns=[c for c in df_result.columns if c.startswith("cost_")]
        )  # L1063

        df_result[date_col] = df_result[date_col].shift(-1)  # L1065-1067

        if len(df_result) == 0:
            raise ValueError("empty backtest: no dates to evaluate")  # L1069

        n_periods = len(df_result)
        days_per_period = days / n_periods
        annual_factor = 365.25 / days_per_period  # L1071-1073

        avg_turnover_l = df_agg[f"turnover_{bins}"].dropna().mean()
        avg_turnover_s = df_agg["turnover_1"].dropna().mean()
        avg_turnover_ls = df_agg["turnover_ls"].dropna().mean()  # L1075-1077

        # ================= stats (L1080-1119) =================
        stats_ls = self._stats(df_result, "ret_net_ls", "cum_ret_net_ls", days, annual_factor, cfg, avg_turnover_ls)
        # ================= long / short legs (L1230-1306) =================
        stats_l = self._stats(
            df_result, f"ret_net_{bins}", f"cum_ret_net_{bins}", days, annual_factor, cfg, avg_turnover_l
        )
        stats_s = self._stats(
            df_result, "ret_net_s_1", "cum_ret_net_s_1", days, annual_factor, cfg, avg_turnover_s,
            "Ann. Turnover",  # L1301: S-leg quirk key
        )

        # ================= monthly aggregation (L1121-1139) =================
        if cfg.agg_freq:
            monthly = df_result.copy()
            monthly["agg_date"] = pd.to_datetime(monthly[date_col]).dt.to_period(cfg.agg_freq).dt.to_timestamp()
            _monthly_agg = monthly.groupby("agg_date")[["ret_net_ls", f"ret_net_{bins}", "ret_net_s_1"]].apply(
                lambda g: (1.0 + g).prod() - 1.0
            )
            monthly_ret_ls = _monthly_agg[["ret_net_ls"]].reset_index()
            monthly_ret_l = _monthly_agg[[f"ret_net_{bins}"]].reset_index()
            monthly_ret_s = _monthly_agg[["ret_net_s_1"]].reset_index()
        else:
            monthly_ret_ls = None
            monthly_ret_l = None
            monthly_ret_s = None

        # ================= industry attribution (L957-1001) =================
        df_indus = self._industry_attribution(df, cfg, bins)

        ret_ls = pd.Series(df_result["ret_ls"].to_numpy(), index=_result_dates, name="ret_ls")
        cum_ret_ls = pd.Series(
            df_result["cum_ret_ls"].to_numpy(), index=_result_dates, name="cum_ret_ls"
        )
        cum_ret_net_ls = pd.Series(
            df_result["cum_ret_net_ls"].to_numpy(), index=_result_dates, name="cum_ret_net_ls"
        )

        return WeightDriftResult(
            df_result=df_result,
            df_agg=df_agg,
            stats_ls=stats_ls,
            mean_ret=float(df_result["ret_net_ls"].mean()),
            avg_turnover_ls=avg_turnover_ls,
            ret_ls=ret_ls,
            cum_ret_ls=cum_ret_ls,
            cum_ret_net_ls=cum_ret_net_ls,
            stats_l=stats_l,
            stats_s=stats_s,
            df_indus=df_indus,
            monthly_ret_ls=monthly_ret_ls,
            monthly_ret_l=monthly_ret_l,
            monthly_ret_s=monthly_ret_s,
            config=cfg,
        )

    @staticmethod
    def _stats(
        df_result: pd.DataFrame,
        ret_col: str,
        cum_col: str,
        days: int,
        annual_factor: float,
        cfg: WeightDriftConfig,
        avg_turnover: float,
        turnover_key: str = "Mean Turnover",
    ) -> dict:
        """Annualized stats for one leg — verbatim L1080-1119 (NaN semantics kept).

        AlphaPurify's S-leg stats dict uses the key ``"Ann. Turnover"`` (L1301)
        while the LS/L legs use ``"Mean Turnover"`` — a source quirk kept for
        parity; ``turnover_key`` carries that override.
        """
        ret_s = df_result[ret_col]
        cum_nv = df_result[cum_col].to_numpy()

        mean_ret = ret_s.mean()
        mean_loss = ret_s[ret_s < 0].mean()
        pl = mean_ret / mean_loss
        ann_ret = cum_nv[-1] ** (365.25 / days) - 1
        vol = ret_s.std(ddof=1) * np.sqrt(annual_factor)
        if np.isnan(vol):
            vol = ret_s.std(ddof=0) * np.sqrt(annual_factor)

        excess_ret = ann_ret - cfg.base_rate
        sharpe = excess_ret / vol if vol != 0 and not np.isnan(vol) else np.nan

        running_max = np.maximum.accumulate(cum_nv)  # NaN-poisoning kept verbatim
        drawdown = cum_nv / running_max - 1
        max_dd = float(np.nanmin(drawdown)) if drawdown.size > 0 else np.nan

        win_rate = float((ret_s.dropna() > 0).mean())
        pnl = float(cum_nv[-1] - 1) if cum_nv.size > 0 else np.nan
        downside_std = ret_s[ret_s < 0].std(ddof=1)
        if downside_std is not None and not np.isnan(downside_std):
            downside_vol = downside_std * np.sqrt(annual_factor)
            sortino = excess_ret / downside_vol if downside_vol != 0 else np.nan
        else:
            sortino = np.nan
        calmar = excess_ret / abs(max_dd) if max_dd != 0 else np.nan

        return {
            "Ann. Return": ann_ret,
            "Ann. Std": vol,
            "Ann. Sharpe": sharpe,
            "Ann. Sortino": sortino,
            "Ann. Calmar": calmar,
            turnover_key: avg_turnover,
            "Max Drawdown": max_dd,
            "WinRate": win_rate,
            "P/L": pl,
            "PnL": pnl,
        }

    @staticmethod
    def _industry_attribution(df: pd.DataFrame, cfg: WeightDriftConfig, bins: int) -> pd.DataFrame | None:
        """Industry attribution (L957-1001); ``None`` when ``group_by`` unset."""
        if not cfg.group_by:
            return None
        date_col = cfg.trade_date_col
        df_ind = df.copy()
        df_ind["industry"] = df_ind[cfg.symbol_col].map(cfg.group_by).fillna("Unlabeled")
        df_ind["ret_s"] = -df_ind["ret"]
        df_ind["cost_filled"] = df_ind["cost"].fillna(0.0)
        indus_daily = (
            df_ind[df_ind["quantile"].isin([1, bins])]
            .groupby([date_col, "industry", "quantile"], sort=False)
            .apply(
                lambda g: pd.Series(
                    {
                        "ret_net_daily": g["ret"].sum() - g["cost_filled"].sum(),
                        "ret_net_s_daily": g["ret_s"].sum() - g["cost_filled"].sum(),
                    }
                ),
                include_groups=False,
            )
            .reset_index()
        )
        indus_avg = (
            indus_daily.groupby(["industry", "quantile"], sort=False)[["ret_net_daily", "ret_net_s_daily"]]
            .mean()
            .reset_index()
        )
        qb = indus_avg[indus_avg["quantile"] == 1][["industry", "ret_net_s_daily"]].rename(
            columns={"ret_net_s_daily": "ret_net_s_qb_mean"}
        )
        qt = indus_avg[indus_avg["quantile"] == bins][["industry", "ret_net_daily"]].rename(
            columns={"ret_net_daily": "ret_net_qt_mean"}
        )
        indus = qb.merge(qt, on="industry", how="left")
        indus["ret_net_ls_i_mean"] = indus["ret_net_qt_mean"] - indus["ret_net_s_qb_mean"]
        return indus
