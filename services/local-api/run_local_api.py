"""local-api 启动器:sys.path 灌入(与 smoke_snapshot.py 同款)后跑 uvicorn。

用法: python run_local_api.py [--port 8088]
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
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

if __name__ == "__main__":
    import uvicorn

    port = 8088
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info")
