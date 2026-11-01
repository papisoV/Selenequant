"""Tests for industry neutralization module."""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_alpha.factors.neutralize import (
    _demean_by_group,
    apply_ind_neutralize,
    cluster_neutralize,
    rank_neutralize,
)


# ---------------------------------------------------------------------------
# rank_neutralize
# ---------------------------------------------------------------------------


class TestRankNeutralize:
    def test_mean_approx_zero(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = rank_neutralize(s)
        assert abs(result.mean()) < 1e-10

    def test_std_approx_one(self) -> None:
        s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        result = rank_neutralize(s)
        assert abs(result.std() - 1.0) < 1e-10

    def test_all_same_values(self) -> None:
        s = pd.Series([3.0, 3.0, 3.0, 3.0])
        result = rank_neutralize(s)
        assert (result == 0.0).all()

    def test_preserves_index(self) -> None:
        idx = pd.Index(["a", "b", "c"])
        s = pd.Series([1.0, 5.0, 9.0], index=idx)
        result = rank_neutralize(s)
        assert list(result.index) == ["a", "b", "c"]

    def test_ties_handled(self) -> None:
        s = pd.Series([1.0, 2.0, 2.0, 4.0])
        result = rank_neutralize(s)
        # Tied values at positions 1,2 should get the same output
        assert abs(result.iloc[1] - result.iloc[2]) < 1e-10


# ---------------------------------------------------------------------------
# _demean_by_group
# ---------------------------------------------------------------------------


class TestDemeanByGroup:
    def test_simple_demean(self) -> None:
        s = pd.Series([10.0, 20.0, 30.0, 40.0])
        groups = pd.Series(["A", "A", "B", "B"])
        result = _demean_by_group(s, groups)
        # Group A mean = 15, Group B mean = 35
        assert abs(result.iloc[0] - (-5.0)) < 1e-10
        assert abs(result.iloc[1] - 5.0) < 1e-10
        assert abs(result.iloc[2] - (-5.0)) < 1e-10
        assert abs(result.iloc[3] - 5.0) < 1e-10

    def test_single_group(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        groups = pd.Series(["X", "X", "X"])
        result = _demean_by_group(s, groups)
        # All in one group: demeaned values should sum to 0
        assert abs(result.sum()) < 1e-10

    def test_single_member_group(self) -> None:
        s = pd.Series([42.0])
        groups = pd.Series(["solo"])
        result = _demean_by_group(s, groups)
        assert abs(result.iloc[0]) < 1e-10


# ---------------------------------------------------------------------------
# cluster_neutralize
# ---------------------------------------------------------------------------


class TestClusterNeutralize:
    def test_within_cluster_mean_approx_zero(self) -> None:
        np.random.seed(0)
        df = pd.DataFrame(
            np.random.randn(30, 3),
            columns=["f1", "f2", "f3"],
        )
        result = cluster_neutralize(df, n_clusters=3)
        # We need the cluster labels to verify; re-derive them
        from selene_space_alpha.factors.neutralize import _kmeans_labels

        clusters = _kmeans_labels(df, n_clusters=3, features=None)
        for col in result.columns:
            for c in clusters.unique():
                group_vals = result.loc[clusters == c, col]
                assert abs(group_vals.mean()) < 1e-10

    def test_empty_dataframe(self) -> None:
        df = pd.DataFrame()
        result = cluster_neutralize(df, n_clusters=3)
        assert result.empty

    def test_preserves_shape(self) -> None:
        df = pd.DataFrame(np.random.randn(20, 4), columns=list("abcd"))
        result = cluster_neutralize(df, n_clusters=4)
        assert result.shape == df.shape
        assert list(result.columns) == list(df.columns)

    def test_custom_features_for_clustering(self) -> None:
        df = pd.DataFrame(
            {
                "cluster_feat": [0.0, 0.0, 100.0, 100.0],
                "value": [1.0, 3.0, 50.0, 70.0],
            }
        )
        result = cluster_neutralize(df, n_clusters=2, features=["cluster_feat"])
        # With 2 clear clusters, each group should be demeaned
        # The "value" column should have zero mean within each cluster
        from selene_space_alpha.factors.neutralize import _kmeans_labels

        clusters = _kmeans_labels(df, n_clusters=2, features=["cluster_feat"])
        for c in clusters.unique():
            group_vals = result.loc[clusters == c, "value"]
            assert abs(group_vals.mean()) < 1e-10


# ---------------------------------------------------------------------------
# apply_ind_neutralize
# ---------------------------------------------------------------------------


class TestApplyIndNeutralize:
    def test_with_explicit_groups(self) -> None:
        df = pd.DataFrame(
            {
                "factor_a": [10.0, 20.0, 30.0, 40.0],
                "factor_b": [1.0, 2.0, 3.0, 4.0],
            }
        )
        groups = pd.Series(["G1", "G1", "G2", "G2"])
        result = apply_ind_neutralize(df, group_series=groups)
        # Group G1 mean for factor_a = 15, G2 mean = 35
        assert abs(result.loc[0, "factor_a"] - (-5.0)) < 1e-10
        assert abs(result.loc[1, "factor_a"] - 5.0) < 1e-10
        assert abs(result.loc[2, "factor_a"] - (-5.0)) < 1e-10
        assert abs(result.loc[3, "factor_a"] - 5.0) < 1e-10

    def test_without_groups_uses_clustering(self) -> None:
        np.random.seed(42)
        df = pd.DataFrame(np.random.randn(25, 2), columns=["x", "y"])
        result = apply_ind_neutralize(df, n_clusters=5)
        # Should return a DataFrame of same shape
        assert result.shape == df.shape
        # Within each cluster, means should be ~0
        from selene_space_alpha.factors.neutralize import _kmeans_labels

        clusters = _kmeans_labels(df, n_clusters=5, features=None)
        for col in result.columns:
            for c in clusters.unique():
                group_vals = result.loc[clusters == c, col]
                assert abs(group_vals.mean()) < 1e-10

    def test_empty_dataframe(self) -> None:
        df = pd.DataFrame()
        result = apply_ind_neutralize(df)
        assert result.empty

    def test_single_group_all_same(self) -> None:
        df = pd.DataFrame(
            {
                "f1": [5.0, 5.0, 5.0],
                "f2": [10.0, 10.0, 10.0],
            }
        )
        groups = pd.Series(["A", "A", "A"])
        result = apply_ind_neutralize(df, group_series=groups)
        # All values same within group -> demeaned to 0
        assert (result == 0.0).all().all()


# ---------------------------------------------------------------------------
# Integration: neutralize a simple alpha factor DataFrame
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_neutralize_alpha_factors(self) -> None:
        """Simulate a cross-section of crypto assets with factor values."""
        assets = ["BTC", "ETH", "SOL", "DOGE", "AVAX"]
        factor_df = pd.DataFrame(
            {
                "momentum": [0.8, 0.5, 0.3, -0.2, -0.6],
                "volatility": [0.2, 0.4, 0.6, 0.8, 1.0],
                "volume_score": [1.0, 0.8, 0.5, 0.3, 0.1],
            },
            index=assets,
        )
        # Sector-like grouping: L1 vs L2 vs meme
        sectors = pd.Series(
            ["L1", "L1", "L1", "meme", "L1"],
            index=assets,
        )
        result = apply_ind_neutralize(factor_df, group_series=sectors)
        # Within L1 group (BTC, ETH, SOL, AVAX), each factor should have mean ~0
        l1_vals = result.loc[sectors == "L1"]
        for col in l1_vals.columns:
            assert abs(l1_vals[col].mean()) < 1e-10
        # Meme group (DOGE alone) should be 0
        assert abs(result.loc["DOGE", "momentum"]) < 1e-10

    def test_rank_then_neutralize(self) -> None:
        """Rank neutralize a factor, then apply group neutralization."""
        np.random.seed(7)
        raw = pd.Series(np.random.randn(20))
        ranked = rank_neutralize(raw)
        # After rank neutralization, mean ~0, std ~1
        assert abs(ranked.mean()) < 1e-10
        assert abs(ranked.std() - 1.0) < 0.05

        # Now put into a DataFrame and group-neutralize
        df = pd.DataFrame({"alpha": ranked.values})
        groups = pd.Series(["A"] * 10 + ["B"] * 10)
        result = apply_ind_neutralize(df, group_series=groups)
        for g in groups.unique():
            group_vals = result.loc[groups == g, "alpha"]
            assert abs(group_vals.mean()) < 1e-10
