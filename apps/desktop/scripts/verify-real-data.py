"""Verify frontend pages get real data (not mock fallback) when backend is up."""

import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

# Page slug -> primary endpoint (根路径，前端 useApiOrMock 调用的)
# 从 page-manifest.ts 的 rest 字段派生
PAGE_ENDPOINTS = {
    "watch/kf": "http://localhost:3000/api/v1/markets/BTCUSDT/kline",
    "watch/kfi": "http://localhost:3000/api/v1/kf-index/list",
    "watch/mk": "http://localhost:3000/api/v1/markets",
    "watch/liqmap": "http://localhost:3000/api/v1/liqmap/current",
    "watch/liqratio": "http://localhost:3000/api/v1/liqratio/current",
    "watch/gex": "http://localhost:3000/api/v1/gex/current",
    "watch/tac": "http://localhost:3000/api/v1/derivatives/funding-rates",
    "watch/sig": "http://localhost:3000/api/v1/signals/current",
    "watch/vchart": "http://localhost:3000/api/v1/vchart",
    "watch/poly": "http://localhost:3000/api/v1/polymarket/markets",
    "watch/dex": "http://localhost:3000/api/v1/dex/pairs",
    "watch/onchain": "http://localhost:3000/api/v1/onchain",
    "watch/us-radar": "http://localhost:3000/api/v1/us-radar",
    "watch/hyperliquid": "http://localhost:3000/api/v1/hyperliquid",
    "watch/cvd": "http://localhost:3000/api/v1/cvd/current",
    "watch/heatmap": "http://localhost:3000/api/v1/heatmap/current",
    "watch/liquidations": "http://localhost:3000/api/v1/liquidations/stats",
    "research/rsch": "http://localhost:3000/api/v1/strategies/factory-templates",
    "research/strat": "http://localhost:3000/api/v1/strategies",
    "research/wid": "http://localhost:3000/api/v1/widgets",
    "research/alpha_mine": "http://localhost:3000/api/v1/alpha/formulas",
    "research/portfolio": "http://localhost:3000/api/v1/portfolio",
    "research/serenity": "http://localhost:3000/api/v1/serenity/scores",
    "research/qevolve": "http://localhost:3000/api/v1/qevolve/test",
    "research/factors": "http://localhost:3000/api/v1/research/factors",
    "trade/pos": "http://localhost:3000/api/v1/trading/positions",
    "trade/paper": "http://localhost:3000/api/v1/paper/accounts",
    "trade/trd": "http://localhost:3000/api/v1/trading/orders",
    "trade/grid": "http://localhost:3000/api/v1/grid/state",
    "trade/tof": "http://localhost:3000/api/v1/tof/current",
    "trade/live_log": "http://localhost:3000/api/v1/trading/live-log",
    "trade/backtests": "http://localhost:3000/api/v1/backtests/test",
    "trade/dex-trading": "http://localhost:3000/api/v1/dex-trading/balance",
    "trade/optimizations": "http://localhost:3000/api/v1/optimizations/test",
    "system/dash": "http://localhost:3000/api/v1/dashboard",
    "system/skill": "http://localhost:3000/api/v1/skills",
    "system/mcp": "http://localhost:3000/api/v1/mcp/servers",
    "system/set": "http://localhost:3000/api/v1/settings",
    "system/notifications": "http://localhost:3000/api/v1/notifications/channels",
    "system/memory": "http://localhost:3000/api/v1/memory/market-patterns",
    "system/marketplace": "http://localhost:3000/api/v1/marketplace/listings",
    "system/chat-sessions": "http://localhost:3000/api/v1/chat/sessions",
}


def check(item):
    page, url = item
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read(500).decode("utf-8", errors="ignore")
            status = resp.status
            return (page, status, len(body), body[:120].replace("\n", " "))
    except urllib.error.HTTPError as e:
        body = e.read(500).decode("utf-8", errors="ignore") if e.fp else ""
        return (page, e.code, len(body), body[:120].replace("\n", " "))
    except Exception as e:
        return (page, "ERR", 0, str(e)[:120])


results = []
with ThreadPoolExecutor(max_workers=8) as ex:
    for r in ex.map(check, list(PAGE_ENDPOINTS.items())):
        results.append(r)

results.sort(key=lambda x: x[0])
real = [r for r in results if r[1] == 200 and r[2] > 10]
mock_fallback = [r for r in results if r[1] != 200 or r[2] <= 10]
print(f"=== Total pages: {len(results)} ===")
print(f"=== Real data (200 + body): {len(real)} ===")
print(f"=== Mock fallback / error: {len(mock_fallback)} ===\n")

print("=== REAL DATA ===")
for page, status, size, preview in real:
    print(f"  {page:30} {status} {size:5}B  {preview[:80]}")

print("\n=== MOCK FALLBACK / ERROR ===")
for page, status, size, preview in mock_fallback:
    print(f"  {page:30} {status} {size:5}B  {preview[:80]}")
