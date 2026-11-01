"""Shim integrity tests (story P1M1-05).

The legacy ``factors/neutralize.py`` must be a thin re-export of the ported
implementations in ``preprocessing/neutralize.py``: same public surface, no
reverse import, and ``rank_neutralize`` must keep true-rank semantics (not the
AlphaPurify RANSAC that the registry exposes as ``ransac_neutralize``).
"""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from selene_space_alpha.factors import neutralize as shim
from selene_space_factor_research.preprocessing import (
    METHOD_REGISTRY,
    list_methods,
)
from selene_space_factor_research.preprocessing import neutralize as impl


def test_shim_re_exports_legacy_surface() -> None:
    assert shim.rank_neutralize is impl.rank_neutralize
    assert shim.cluster_neutralize is impl.cluster_neutralize
    assert shim.apply_ind_neutralize is impl.apply_ind_neutralize
    assert shim._demean_by_group is impl._demean_by_group


def test_shim_has_private_kmeans_helpers() -> None:
    # existing test_neutralize.py imports _kmeans_labels directly
    assert hasattr(shim, "_kmeans_labels")
    assert hasattr(shim, "_numpy_kmeans")


def test_no_reverse_import() -> None:
    # preprocessing must never import factors.neutralize (shim is one-way)
    src = inspect.getsource(impl)
    assert "factors.neutralize" not in src


def test_rank_neutralize_is_true_rank_not_ransac() -> None:
    s = pd.Series([10.0, 1.0, 5.0, 20.0, 8.0])
    out = shim.rank_neutralize(s)
    # monotonic in rank order: smallest input -> most negative output
    assert out[1] == out.min()  # value 1.0 ranks lowest
    assert out[3] == out.max()  # value 20.0 ranks highest
    # RANSAC would produce regression residuals, not a rank transform
    assert not np.allclose(out, s) or np.unique(out).size == np.unique(s).size


def test_ransac_not_registered_under_rank_key() -> None:
    assert "neutralize_rank" not in list_methods("neutralize")
    assert METHOD_REGISTRY["neutralize_ransac"].func is impl.ransac_neutralize


def test_shim_module_is_thin() -> None:
    # shim should only contain imports + __all__, no implementation bodies
    src = inspect.getsource(shim)
    for fn in ("def _demean_by_group", "def rank_neutralize", "def _kmeans_labels"):
        assert fn not in src
