"""Tests for context_blocks orchestration (all mocked, no network)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from selene_space_ashare.context_blocks import (
    BLOCK_EMPTY,
    BLOCK_FAILED,
    BLOCK_OK,
    BLOCK_SKIPPED,
    BlockSpec,
    build_astock_context,
    capital_flow_block,
    coverage_of,
    default_blocks,
    run_blocks,
)


def _df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class TestRunBlocks:
    def test_ok_block(self) -> None:
        df = _df([{"a": 1}])
        results = run_blocks([BlockSpec("b", lambda: df)], budget_seconds=10.0)
        assert results["b"].status == BLOCK_OK
        assert results["b"].data is df
        assert results["b"].error is None
        assert results["b"].elapsed_ms >= 0.0

    def test_failing_block_does_not_stop_others(self) -> None:
        def boom() -> Any:
            raise RuntimeError("network down")

        calls: list[str] = []
        specs = [
            BlockSpec("first", lambda: calls.append("first") or _df([{"a": 1}])),
            BlockSpec("broken", boom),
            BlockSpec("last", lambda: calls.append("last") or _df([{"a": 2}])),
        ]
        results = run_blocks(specs, budget_seconds=10.0)
        assert results["broken"].status == BLOCK_FAILED
        assert results["broken"].error is not None
        assert "network down" in results["broken"].error
        assert results["broken"].data is None
        assert calls == ["first", "last"]
        assert results["first"].status == BLOCK_OK
        assert results["last"].status == BLOCK_OK

    def test_none_result_is_empty(self) -> None:
        results = run_blocks([BlockSpec("b", lambda: None)], budget_seconds=10.0)
        assert results["b"].status == BLOCK_EMPTY

    def test_empty_dataframe_is_empty(self) -> None:
        results = run_blocks(
            [BlockSpec("b", lambda: pd.DataFrame())], budget_seconds=10.0
        )
        assert results["b"].status == BLOCK_EMPTY

    def test_budget_exhaustion_skips_remaining(self) -> None:
        calls: list[str] = []

        def slow() -> pd.DataFrame:
            calls.append("slow")
            return _df([{"a": 1}])

        def never() -> pd.DataFrame:
            calls.append("never")
            return _df([{"a": 1}])

        # now_fn returns cumulative consumed time; first block burns the budget.
        ticks = iter([0.0, 100.0, 100.0, 100.0])
        specs = [BlockSpec("slow", slow), BlockSpec("never", never)]
        results = run_blocks(specs, budget_seconds=5.0, now_fn=lambda: next(ticks))
        assert results["slow"].status == BLOCK_OK
        assert results["never"].status == BLOCK_SKIPPED
        assert calls == ["slow"]

    def test_zero_budget_means_unlimited(self) -> None:
        calls: list[str] = []
        specs = [
            BlockSpec(f"b{i}", lambda i=i: calls.append(f"b{i}") or _df([{"a": i}]))
            for i in range(3)
        ]
        results = run_blocks(specs, budget_seconds=0.0)
        assert calls == ["b0", "b1", "b2"]
        assert all(r.status == BLOCK_OK for r in results.values())

    def test_negative_budget_means_unlimited(self) -> None:
        specs = [BlockSpec("b", lambda: _df([{"a": 1}]))]
        results = run_blocks(specs, budget_seconds=-1.0)
        assert results["b"].status == BLOCK_OK

    def test_results_preserve_input_order(self) -> None:
        specs = [BlockSpec(n, lambda: _df([{"a": 1}])) for n in ["c", "a", "b"]]
        results = run_blocks(specs, budget_seconds=0.0)
        assert list(results) == ["c", "a", "b"]


class TestCoverageOf:
    def test_shape(self) -> None:
        results = run_blocks(
            [
                BlockSpec("ok", lambda: _df([{"a": 1}])),
                BlockSpec("empty", lambda: None),
                BlockSpec("fail", lambda: (_ for _ in ()).throw(ValueError("x"))),
            ],
            budget_seconds=10.0,
        )
        cov = coverage_of(results)
        assert cov == {
            "ok": BLOCK_OK,
            "empty": BLOCK_EMPTY,
            "fail": BLOCK_FAILED,
        }


class TestCapitalFlowBlock:
    def test_aggregates_recent_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        df = _df(
            [
                {
                    "date": "20260101",
                    "主力净流入-净额": 100.0,
                    "超大单净流入-净额": 60.0,
                    "大单净流入-净额": 40.0,
                },
                {
                    "date": "20260102",
                    "主力净流入-净额": 200.0,
                    "超大单净流入-净额": 110.0,
                    "大单净流入-净额": 90.0,
                },
            ]
        )
        monkeypatch.setattr(
            "selene_space_ashare.context_blocks.get_fund_flow",
            lambda symbol: df,
        )
        spec = capital_flow_block("600519")
        assert spec.name == "capital_flow"
        result = run_blocks([spec], budget_seconds=5.0)["capital_flow"]
        assert result.status == BLOCK_OK
        summary = result.data
        assert summary["days"] == 2
        assert summary["主力净流入-净额"] == pytest.approx(300.0)
        assert summary["超大单净流入-净额"] == pytest.approx(170.0)
        assert summary["大单净流入-净额"] == pytest.approx(130.0)

    def test_empty_fetch_is_empty_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "selene_space_ashare.context_blocks.get_fund_flow",
            lambda symbol: pd.DataFrame(),
        )
        result = run_blocks([capital_flow_block("600519")], budget_seconds=5.0)
        assert result["capital_flow"].status == BLOCK_EMPTY

    def test_fetcher_error_is_failed_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(symbol: str) -> pd.DataFrame:
            raise TimeoutError("fetch timeout")

        monkeypatch.setattr("selene_space_ashare.context_blocks.get_fund_flow", boom)
        result = run_blocks([capital_flow_block("600519")], budget_seconds=5.0)
        assert result["capital_flow"].status == BLOCK_FAILED


class TestBuildAstockContext:
    def test_injected_specs_no_network(self) -> None:
        specs = [BlockSpec("fake", lambda: _df([{"a": 1}]))]
        ctx = build_astock_context("600519", specs=specs, budget_seconds=5.0)
        assert ctx["symbol"] == "600519"
        assert ctx["budget_seconds"] == 5.0
        assert ctx["coverage"] == {"fake": BLOCK_OK}
        assert ctx["blocks"]["fake"].status == BLOCK_OK

    def test_one_failure_keeps_dict_shape(self) -> None:
        def boom() -> Any:
            raise ValueError("bad")

        specs = [
            BlockSpec("good", lambda: _df([{"a": 1}])),
            BlockSpec("bad", boom),
        ]
        ctx = build_astock_context("600519", specs=specs, budget_seconds=5.0)
        assert set(ctx) == {"symbol", "blocks", "coverage", "budget_seconds"}
        assert ctx["coverage"] == {"good": BLOCK_OK, "bad": BLOCK_FAILED}
        assert ctx["blocks"]["bad"].error == "bad"

    def test_default_blocks_named(self) -> None:
        names = [s.name for s in default_blocks("600519")]
        assert names == [
            "capital_flow",
            "dragon_tiger",
            "limit_up_pool",
            "valuation",
            "northbound",
            "margin",
        ]
