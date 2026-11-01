"""Tests for the shared vocab-selection helper (_pandas_vocab_for_version)."""

from __future__ import annotations

import app.api.v1.alpha as alpha_mod


def test_v7_mining_uses_mining_vocab_with_gate() -> None:
    features, ops = alpha_mod._pandas_vocab_for_version("v7_mining_csi300")
    assert len(features) == 11
    assert "GATE" in ops
    assert len(ops) == 21


def test_v6_uses_v6_features_without_gate() -> None:
    features, ops = alpha_mod._pandas_vocab_for_version("v6-15m-optimized")
    assert len(features) == 31
    assert "GATE" not in ops


def test_default_falls_back_to_v4() -> None:
    features, ops = alpha_mod._pandas_vocab_for_version("alpha_v5_adaptive")
    assert len(features) == 27
    assert "GATE" not in ops


def test_none_version_falls_back_to_v4() -> None:
    features, ops = alpha_mod._pandas_vocab_for_version(None)
    assert len(features) == 27
