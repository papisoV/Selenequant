"""快照契约刀真库 smoke:真实 KlineSource + 临时文件库走 API 全链路。

①POST mine(默认 BTC/ETH/SOL 1h 500根)→ 200 + 快照元信息
②GET 复核 → status=ok,mine→get 数值一致,滚动窗口参数化生效
③改参数(period=10)→ no_snapshot
④payload 实测字节数(5MB 闸标定)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
# 与根 pyproject [tool.pytest.ini_options].pythonpath 对齐(全量灌入)
for _p in (
    "packages/cli",
    "packages/alpha-core",
    "packages/shared-types",
    "packages/backtest-engine",
    "packages/strategy-engine",
    "packages/stock",
    "packages/ashare",
    "packages/pulse",
    "packages/core",
    "packages/agent-runtime",
    "packages/exchange-sdk",
    "packages/market-data",
    "packages/paper-engine",
    "packages/risk-engine",
    "packages/security-gateway",
    "packages/storage",
    "packages/llm-adapter",
    "packages/compat-layer",
    "packages/benchmark",
    "packages/tg-ingest",
    "packages/decision-workflow",
    "packages/macrodata",
    "packages/optimize",
    "packages/agentstudio",
    "packages/daily-cycle",
    "packages/renintel",
    "packages/market-analysis",
    "packages/commodities-fx",
    "packages/factor-research",
    "packages/grid-engine",
    "packages/news-ingest",
    "packages/ashare-select",
    "packages/fund-ingest",
    "services/local-api",
):
    sys.path.insert(0, str(_REPO / _p))
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(Path(__file__).parent))

# E402 豁免:sys.path 灌入必须先于 import(python -m pytest pythonpath 的裸跑等价物)
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.api.v1.research_graph import get_graph_db  # noqa: E402
from app.core.dependencies import get_market_analysis_service  # noqa: E402
from app.main import create_app  # noqa: E402
from selene_space_storage.migrations import MigrationRunner  # noqa: E402
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS  # noqa: E402
from selene_space_storage.sqlite import AsyncSQLiteManager  # noqa: E402

CROSS = "BTC/USDT,ETH/USDT,SOL/USDT"
GET_QS = "symbols=BTC%2FUSDT%2CETH%2FUSDT%2CSOL%2FUSDT&interval=1h"


class _NoopSvc:
    async def get_record(self, record_id):  # pragma: no cover
        return None


async def main() -> int:
    smoke_db = Path(__file__).parent / "_smoke_snapshot.db"
    if smoke_db.exists():
        smoke_db.unlink()

    ok = True
    app = create_app()
    async with AsyncSQLiteManager(
        str(smoke_db), migration_runner=MigrationRunner(ALL_MIGRATIONS)
    ) as manager:
        app.dependency_overrides[get_market_analysis_service] = lambda: _NoopSvc()
        app.dependency_overrides[get_graph_db] = lambda: manager

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://smoke"
        ) as client:
            mine = await client.post(
                "/api/v1/market-analysis/records/ic-panel/mine",
                json={"symbols": CROSS, "interval": "1h"},
            )
            print(f"[1] POST mine -> {mine.status_code}")
            if mine.status_code != 200:
                print(mine.text)
                return 1
            mine_body = mine.json()
            snap = mine_body["snapshot"]
            print(
                f"    episode={snap['episode_id']} n_rows={snap['n_rows']} occurred_at={snap['occurred_at']}"
            )

            # payload 字节标定
            from app.services import factor_snapshot as fs

            loaded = await fs.load_latest_snapshot(manager, key=snap["key"])
            assert loaded is not None, "snapshot just mined must be loadable"
            payload_bytes = len(
                json.dumps(loaded["payload"], ensure_ascii=False).encode("utf-8")
            )
            print(
                f"[4] payload 实测 {payload_bytes} bytes ({payload_bytes / 1024:.0f} KB) / 闸 5MB"
            )
            print(
                f"    → 撞闸需 limit ≈ {int(500 * 5 * 1024 * 1024 / payload_bytes)} 根"
            )

            get1 = await client.get(
                f"/api/v1/market-analysis/records/ic-panel?{GET_QS}"
            )
            b1 = get1.json()
            get2 = await client.get(
                f"/api/v1/market-analysis/records/ic-panel?{GET_QS}&rolling_window=60"
            )
            b2 = get2.json()
            print(
                f"[2a] GET -> {get1.status_code} status={b1['status']} rolling_ic_days={len(b1['rolling_ic']['dates'])}"
            )
            same = (
                b1["rolling_ic"] == mine_body["rolling_ic"]
                and b1["rolling_ir"] == mine_body["rolling_ir"]
                and b1["ic_distribution"] == mine_body["ic_distribution"]
                and b1["autocorr"] == mine_body["autocorr"]
                and b1["monthly_heatmap"] == mine_body["monthly_heatmap"]
            )
            print(f"     mine→get 数值一致: {same}")
            rw = (
                b1["rolling_ir"] != b2["rolling_ir"]
                and b1["rolling_ic"] == b2["rolling_ic"]
            )
            print(f"[2b] rolling_window 参数化(20 vs 60): {rw}")
            ok = same and rw

            miss = await client.get(
                f"/api/v1/market-analysis/records/ic-panel?{GET_QS}&period=10"
            )
            bm = miss.json()
            print(
                f"[3] 改 period=10 GET -> {miss.status_code} status={bm['status']} (期望 no_snapshot)"
            )
            ok = ok and bm["status"] == "no_snapshot"

    if smoke_db.exists():
        smoke_db.unlink()
    print("SMOKE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
