"""Protocol loader — the ONLY sanctioned entry point for frozen experiment protocols.

Loads a protocol YAML into a typed frozen object. No eval/exec: YAML values
must be plain scalars/lists/dicts (gap_ratio is a numeric field, not a string
expression — the injection surface stays closed).

Experiments (v7, v6 baseline reruns) consume parameters exclusively through
load_protocol(); hardcoding protocol values in experiment code violates the
frozen-protocol discipline recorded in backtest_v1.yaml.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROTOCOLS_DIR = Path(__file__).parent
PROJECT_ROOT = PROTOCOLS_DIR.parent.parent


@dataclass(frozen=True)
class WalkForwardSpec:
    windows: int
    train_pct: float
    gap_ratio: float
    test_ratio: float


@dataclass(frozen=True)
class CostSpec:
    maker_fee: float
    taker_fee: float
    slippage_bps: float
    assume_taker: bool


@dataclass(frozen=True)
class UniverseSpec:
    pipeline: str
    symbols: list[str]
    intervals: list[str]
    exchange: str
    market_type: str


@dataclass(frozen=True)
class ReplicationSegment:
    symbol: str
    interval: str
    exchange: str
    market_type: str


@dataclass(frozen=True)
class Protocol:
    protocol_id: str
    frozen_at: str
    primary_metric: str
    direction: str
    aggregation: str
    seeds: list[int]
    walk_forward: WalkForwardSpec
    cost: CostSpec
    min_trades: int
    min_trades_enforced: bool
    regime_split: Any
    universe: UniverseSpec
    replication_segment: ReplicationSegment
    source_path: Path
    content_hash: str

    @property
    def protocol_hash(self) -> str:
        return self.content_hash


def _require(mapping: dict, key: str) -> Any:
    if key not in mapping:
        raise ValueError(f"protocol missing required key: {key}")
    return mapping[key]


def load_protocol(name: str = "backtest_v1.yaml") -> Protocol:
    """Load and validate a protocol file. Raises on missing/invalid fields."""
    path = PROTOCOLS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"protocol not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    wf = _require(raw, "walk_forward")
    cost = _require(raw, "cost")
    uni = _require(raw, "universe")
    seg = _require(raw, "independent_replication_segment")

    protocol = Protocol(
        protocol_id=_require(raw, "protocol_id"),
        frozen_at=_require(raw, "frozen_at"),
        primary_metric=_require(raw, "primary_metric"),
        direction=_require(raw, "direction"),
        aggregation=_require(raw, "aggregation"),
        seeds=list(_require(raw, "seeds")),
        walk_forward=WalkForwardSpec(
            windows=int(_require(wf, "windows")),
            train_pct=float(_require(wf, "train_pct")),
            gap_ratio=float(_require(wf, "gap_ratio")),
            test_ratio=float(wf.get("test_ratio", 0.25)),
        ),
        cost=CostSpec(
            maker_fee=float(cost["maker_fee"]),
            taker_fee=float(_require(cost, "taker_fee")),
            slippage_bps=float(_require(cost, "slippage_bps")),
            assume_taker=bool(cost.get("assume_taker", True)),
        ),
        min_trades=int(raw.get("min_trades", 20)),
        min_trades_enforced=bool(raw.get("min_trades_enforced", False)),
        regime_split=raw.get("regime_split"),
        universe=UniverseSpec(
            pipeline=_require(uni, "pipeline"),
            symbols=list(_require(uni, "symbols")),
            intervals=list(_require(uni, "intervals")),
            exchange=uni.get("exchange", "binance"),
            market_type=uni.get("market_type", "futures"),
        ),
        replication_segment=ReplicationSegment(
            symbol=_require(seg, "symbol"),
            interval=_require(seg, "interval"),
            exchange=seg.get("exchange", "binance"),
            market_type=seg.get("market_type", "futures"),
        ),
        source_path=path,
        content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
    )

    if protocol.direction not in ("maximize", "minimize"):
        raise ValueError(f"invalid direction: {protocol.direction}")
    if protocol.primary_metric != "sharpe_ratio":
        raise ValueError(
            f"backtest_v1 expects sharpe_ratio, got {protocol.primary_metric}"
        )
    if len(protocol.seeds) != 3:
        raise ValueError(f"backtest_v1 expects exactly 3 seeds, got {protocol.seeds}")
    return protocol


def diff_against_v6_long_defaults(protocol: Protocol) -> list[dict[str, Any]]:
    """Item-by-item diff of protocol walk-forward params vs v6 long call site.

    v6 long actuals (scripts/optuna_formula_search_v6.py, _run_formula_search
    long invocation, verified 2026-09-02): windows=3, train_ratio=0.60,
    gap_ratio=0.10, test_ratio=0.25. Returns rows where mismatch=True on any
    divergence — the acceptance criterion requires this list to be empty of
    mismatches.
    """
    v6_long_actuals = {
        "windows": 3,
        "train_pct": 0.60,
        "gap_ratio": 0.10,
        "test_ratio": 0.25,
    }
    proto_vals = {
        "windows": protocol.walk_forward.windows,
        "train_pct": protocol.walk_forward.train_pct,
        "gap_ratio": protocol.walk_forward.gap_ratio,
        "test_ratio": protocol.walk_forward.test_ratio,
    }
    return [
        {
            "param": k,
            "protocol": proto_vals[k],
            "v6_long_actual": v6_long_actuals[k],
            "match": proto_vals[k] == v6_long_actuals[k],
        }
        for k in v6_long_actuals
    ]


if __name__ == "__main__":
    p = load_protocol()
    print(f"protocol_id: {p.protocol_id}")
    print(f"protocol_hash: {p.protocol_hash}")
    print(f"seeds: {p.seeds}")
    print(
        f"walk_forward: windows={p.walk_forward.windows} train_pct={p.walk_forward.train_pct} "
        f"gap_ratio={p.walk_forward.gap_ratio} test_ratio={p.walk_forward.test_ratio}"
    )
    print(
        f"cost: taker_fee={p.cost.taker_fee} slippage_bps={p.cost.slippage_bps} "
        f"maker_fee={p.cost.maker_fee} assume_taker={p.cost.assume_taker}"
    )
    print(
        f"universe: {p.universe.pipeline} {p.universe.symbols} {p.universe.intervals}"
    )
    print(
        f"replication: {p.replication_segment.symbol} {p.replication_segment.interval}"
    )
    print("diff vs v6-long actuals:")
    for row in diff_against_v6_long_defaults(p):
        flag = "OK" if row["match"] else "MISMATCH"
        print(
            f"  [{flag}] {row['param']}: protocol={row['protocol']} vs v6={row['v6_long_actual']}"
        )
