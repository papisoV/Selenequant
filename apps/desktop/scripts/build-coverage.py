"""Build apps/desktop/lib/endpoint-coverage.ts from backend routes + frontend useApiOrMock calls.

Outputs:
- 297 backend endpoints with status: wired | pending | no_page | backend_only
- consumer_page: which frontend page consumes this endpoint (if any)
"""

import re
import os
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
API_DIR = REPO / "services" / "local-api" / "app" / "api" / "v1"
DESKTOP = REPO / "apps" / "desktop"
OUT_FILE = DESKTOP / "lib" / "endpoint-coverage.ts"

# 1. Backend endpoints
be_endpoints = []
for f in sorted(os.listdir(API_DIR)):
    if not f.endswith(".py") or f.startswith("__"):
        continue
    path = API_DIR / f
    content = path.read_text(encoding="utf-8")
    pm = re.search(r'APIRouter\(\s*prefix\s*=\s*["\x27]([^"\x27]+)["\x27]', content)
    prefix = pm.group(1) if pm else ""
    for m in re.finditer(
        r'@router\.(get|post|put|delete|patch)\(\s*["\x27]([^"\x27]*)["\x27]', content
    ):
        method, ep = m.group(1).upper(), m.group(2)
        full = "/api/v1" + prefix + ep
        be_endpoints.append({"file": f, "method": method, "endpoint": full})

# 2. Frontend wired endpoints (from useApiOrMock + lib/api/*)
wired_paths = set()
for sub in ["components", "lib/api"]:
    for root, _, files in os.walk(DESKTOP / sub):
        for f in files:
            if not (f.endswith(".tsx") or f.endswith(".ts")):
                continue
            content = (Path(root) / f).read_text(encoding="utf-8", errors="ignore")
            # Match a path prefix after any quote/backtick. Terminating on a
            # quote is NOT required: template literals often append `?${params}`
            # (`/x?${q}`) or a `${var}` segment, and demanding a closing quote
            # silently dropped those live calls to no_page. The char class stops
            # at whitespace, `?`, or the closing quote; `${...}` and `{name}`
            # are collapsed by normalize() below.
            for m in re.finditer(r'[`"\x27](/api/v1/[^`"\x27?\s]+)', content):
                wired_paths.add(m.group(1))


def normalize(ep):
    # Template literals produce `${expr}` placeholders (and `{name}` in plain
    # paths); both must collapse to `{}` so a frontend call matches the
    # backend route spec. A `$`-less `{...}` is covered by the second pass.
    ep = re.sub(r"\$\{[^}]*\}", "{}", ep)
    return re.sub(r"\{[^}]+\}", "{}", ep)


wired_norm = {normalize(w) for w in wired_paths}

# 3. Endpoint -> page map (页面维度，一页对多端点)
PAGE_MAP = {
    "kf": [
        "/api/v1/markets/{}/kline",
        "/api/v1/derivatives/funding-rates",
        "/api/v1/liqmap/current",
        "/api/v1/cvd/current",
        "/api/v1/signals/current",
    ],
    "kfi": [
        "/api/v1/kf-index/list",
        "/api/v1/kf-index/compute",
        "/api/v1/kf-index/compute-all",
    ],
    "mk": ["/api/v1/markets"],
    "liqmap": [
        "/api/v1/liqmap/current",
        "/api/v1/liqmap/all-tiers",
        "/api/v1/derivatives/liqmap",
    ],
    "liqratio": ["/api/v1/liqratio/current", "/api/v1/liqratio/all-tiers"],
    "gex": ["/api/v1/gex/current", "/api/v1/gex/levels"],
    "agg": ["/api/v1/agg-orderbook/current"],
    "tac": [
        "/api/v1/derivatives/funding-rates",
        "/api/v1/derivatives/funding-rates/all-exchanges",
    ],
    "vchart": ["/api/v1/vchart"],
    "dex": ["/api/v1/dex/pairs", "/api/v1/dex/search"],
    "beta": ["/api/v1/beta-play"],
    "vol_surface": ["/api/v1/options/vol-surface"],
    "smart_money": ["/api/v1/onchain/smart-money", "/api/v1/intelligence/whale-flow"],
    "onchain": ["/api/v1/onchain", "/api/v1/onchain/metrics", "/api/v1/onchain/whales"],
    "us-radar": [
        "/api/v1/us-radar",
        "/api/v1/us-radar/market/overview",
        "/api/v1/us-radar/snapshot",
        "/api/v1/us-radar/symbols/{}",
        "/api/v1/us-radar/scores/{}/breakdown",
        "/api/v1/us-radar/scanner/run",
        "/api/v1/us-radar/providers/readiness",
        "/api/v1/us-radar/providers/capabilities",
        "/api/v1/us-radar/research-agents/readiness",
        "/api/v1/us-radar/event-signals",
        "/api/v1/us-radar/universe",
        "/api/v1/us-radar/symbols/{}/ai-console",
        "/api/v1/us-radar/factorize",
        "/api/v1/us-radar/factorize/auto",
        "/api/v1/us-radar/flow-factors/preview",
        "/api/v1/us-radar/research-brief",
        "/api/v1/us-radar/filings/insight",
        "/api/v1/us-radar/alerts/preview",
        "/api/v1/us-radar/feedback",
        "/api/v1/us-radar/signals/{}/backtest-quality-preview",
        "/api/v1/us-radar/signals/{}/evolution-log-preview",
        "/api/v1/us-radar/signals/{}/strategy-intent",
        "/api/v1/us-radar/strategy-intents/{}/paper-trade-intent",
    ],
    "hyperliquid": ["/api/v1/hyperliquid"],
    "regime": ["/api/v1/regime"],
    "ashare-regime": ["/api/v1/ashare/regime"],
    "ashare-backtest": ["/api/v1/ashare/backtest"],
    "ashare-board": [
        "/api/v1/ashare/limit-up-pool",
        "/api/v1/ashare/ladder",
        "/api/v1/ashare/phase",
        "/api/v1/ashare/boards",
        "/api/v1/ashare/lhb",
        "/api/v1/ashare/northbound",
    ],
    # ashare-deep 无独立路由页(absorbedBy:"mk",经 mk 的"跨市场"tab 可达),
    # 但有物理组件 components/watch/ashare-deep-page.tsx 消费 4 端点:
    # fund-flow/{symbol} / margin/{symbol} / lhb?date= / northbound。
    # lhb/northbound 归看盘台(市场面);fund-flow/margin 为个股深度,归本页。
    "ashare-deep": [
        "/api/v1/ashare/fund-flow/{symbol}",
        "/api/v1/ashare/margin/{symbol}",
    ],
    "ashare-fundamentals": ["/api/v1/ashare/fundamentals-backfill"],
    "ashare-style": [
        "/api/v1/ashare/style-report",
        "/api/v1/ashare/style-report/refresh",
    ],
    "ashare-portfolio": ["/api/v1/ashare/portfolio-backtest"],
    "ashare-factor-report": [
        "/api/v1/ashare/factor-report",
        "/api/v1/ashare/factor-report/refresh",
    ],
    "cvd": ["/api/v1/cvd/current", "/api/v1/cvd/all"],
    "heatmap": ["/api/v1/heatmap/current"],
    "liquidations": [
        "/api/v1/liquidations/heatmap",
        "/api/v1/liquidations/stats",
        "/api/v1/derivatives/liquidation-stats",
        "/api/v1/derivatives/liquidations/recent",
        "/api/v1/derivatives/large-trades/recent",
        "/api/v1/derivatives/large-trades/stats",
    ],
    "rsch": ["/api/v1/strategies/factory-templates", "/api/v1/strategies"],
    "wid": ["/api/v1/widgets"],
    "alpha_mine": [
        "/api/v1/alpha/formulas",
        "/api/v1/alpha/vocab",
        "/api/v1/alpha/saved-strategies",
        "/api/v1/alpha/mining",
        "/api/v1/alpha/train",
        "/api/v1/alpha/train/{}/stop",
        "/api/v1/alpha/formulas/{}",
        "/api/v1/alpha/formulas/{}/backtest",
        "/api/v1/alpha/formulas/{}/deploy",
        "/api/v1/alpha/saved-strategies/{}/backtest",
        "/api/v1/alpha/saved-strategies/{}/deploy",
    ],
    "portfolio": ["/api/v1/portfolio"],
    "serenity": [
        "/api/v1/serenity/scores",
        "/api/v1/serenity/factors",
        "/api/v1/serenity/scan/{}",
        "/api/v1/serenity/deep/{}",
        "/api/v1/serenity/score/{}",
        "/api/v1/serenity/history/{}",
    ],
    "qevolve": ["/api/v1/qevolve/{}"],
    "factors": [
        "/api/v1/research/factors",
        "/api/v1/research/tuning/results",
        "/api/v1/research/factors/ic-ir",
        "/api/v1/research/tuning/results/{}",
        "/api/v1/research/tuning/start",
        "/api/v1/research/tuning/tasks",
        "/api/v1/research/tuning/tasks/{}",
        "/api/v1/research/tuning/tasks/{}/cancel",
        "/api/v1/research/tuning/smart-start",
        "/api/v1/research/tuning/tasks/{}/save-results",
    ],
    "pos": [
        "/api/v1/trading/positions",
        "/api/v1/trading/balances",
        "/api/v1/trading/pnl/{}",
        "/api/v1/trading/positions/close",
    ],
    "paper": [
        "/api/v1/paper/accounts",
        "/api/v1/paper/orders",
        "/api/v1/paper/positions",
        "/api/v1/paper/fills",
        "/api/v1/paper/pnl",
        "/api/v1/paper/settings",
    ],
    "trd": [
        "/api/v1/trading/orders",
        "/api/v1/trading/mode",
        "/api/v1/trading/kill-switch",
        "/api/v1/trading/kill-switch/reset",
        "/api/v1/trading/orders/{}",
        "/api/v1/trading/leverage",
        "/api/v1/trading/orders-preview",
        "/api/v1/trading/orders-confirm",
    ],
    "grid": [
        "/api/v1/grid/state",
        "/api/v1/grid/sentinel",
        "/api/v1/grid/markets/{}",
        "/api/v1/grid/start",
        "/api/v1/grid/stop",
        "/api/v1/grid/cancel-orders",
        "/api/v1/grid/adjust-range",
        "/api/v1/grid/recovery",
        "/api/v1/grid/close-position",
        "/api/v1/grid/reset-stats",
        "/api/v1/grid/sentinel/run",
        "/api/v1/grid/chat",
    ],
    "tof": ["/api/v1/tof/current", "/api/v1/tof/all"],
    "live_log": [
        "/api/v1/trading/live-log",
        "/api/v1/trading/history",
        "/api/v1/trading/latency-stats",
    ],
    "backtests": ["/api/v1/backtests/{}"],
    "dex-trading": [
        "/api/v1/dex-trading/balance",
        "/api/v1/dex-trading/portfolio",
        "/api/v1/dex-trading/scan",
        "/api/v1/dex-trading/trade/buy",
        "/api/v1/dex-trading/trade/sell",
        "/api/v1/dex-trading/emergency-stop",
        "/api/v1/dex-trading/emergency-stop/reset",
    ],
    "optimizations": [
        "/api/v1/optimizations/{}",
        "/api/v1/optimizations/distributed/status",
    ],
    "dash": ["/api/v1/dashboard"],
    "skill": ["/api/v1/skills"],
    "mcp": ["/api/v1/mcp/servers", "/api/v1/mcp/tools"],
    "set": [
        "/api/v1/settings",
        "/api/v1/settings/llm/models",
        "/api/v1/settings/public-key",
    ],
    "notifications": [
        "/api/v1/notifications/channels",
        "/api/v1/notifications/event-types",
        "/api/v1/notifications/preferences",
    ],
    "memory": ["/api/v1/memory/market-patterns", "/api/v1/memory/strategy-history/{}"],
    "marketplace": ["/api/v1/marketplace/listings", "/api/v1/marketplace/my-purchases"],
    "chat-sessions": ["/api/v1/chat/sessions"],
    "sig": [
        "/api/v1/signals/current",
        "/api/v1/signals/dashboard",
        "/api/v1/intelligence/news/summary",
        "/api/v1/intelligence/social/sentiment",
        "/api/v1/intelligence/social/trending",
        "/api/v1/intelligence/market-pulse/history",
        "/api/v1/intelligence/alerts/rules",
        "/api/v1/intelligence/news/factor-pipeline",
        "/api/v1/intelligence/news/token-signals",
        "/api/v1/intelligence/social/confirmation",
    ],
    "strat": [
        "/api/v1/strategies",
        "/api/v1/strategies/community",
        "/api/v1/strategies/{}/versions",
        "/api/v1/strategies/runner/{}/metrics",
        "/api/v1/strategies/import",
        "/api/v1/strategies/factory-templates/{}/create",
        "/api/v1/strategies/validate",
        "/api/v1/strategies/deployments/list",
        "/api/v1/strategies/runner/{}/signals",
        "/api/v1/strategies/{}",
        "/api/v1/strategies/{}/confirm",
        "/api/v1/strategies/{}/deploy-paper",
        "/api/v1/strategies/{}/stop-deployment",
        "/api/v1/strategies/{}/clone",
        "/api/v1/strategies/{}/export",
        "/api/v1/strategies/{}/deploy-live",
        "/api/v1/strategies/{}/pause-deployment",
        "/api/v1/strategies/{}/resume-deployment",
        "/api/v1/strategies/{}/deployment/flags",
    ],
    "poly": [
        "/api/v1/polymarket/events",
        "/api/v1/polymarket/markets",
        "/api/v1/polymarket/crypto-predictions",
        "/api/v1/polymarket/events/{}",
        "/api/v1/polymarket/markets/{}",
        "/api/v1/polymarket/orderbook/{}",
        "/api/v1/polymarket/price/{}",
        "/api/v1/polymarket/addresses/analyze",
        "/api/v1/polymarket/addresses/compare",
        "/api/v1/polymarket/addresses/screen",
        "/api/v1/polymarket/addresses/smart-money",
        "/api/v1/polymarket/addresses/pipeline",
        "/api/v1/polymarket/addresses/prefetch",
        "/api/v1/polymarket/addresses/watchlist",
        "/api/v1/polymarket/addresses/watchlist/{}",
        "/api/v1/polymarket/addresses/alerts",
        "/api/v1/polymarket/addresses/alerts/{}",
        "/api/v1/polymarket/addresses/alerts/matches",
        "/api/v1/polymarket/addresses/{}/details",
    ],
}

# PAGE_MAP is hand-maintained; a key can outlive the page it names (US-208/209
# deleted routes but left `beta`/`wid` here). Emitting such a key writes a
# `consumer_page` that points at no page — so drop any key that is not a real
# manifest slug and shout about it, rather than silently publishing a dangling
# reference.
_manifest_slugs = set(
    re.findall(
        r'slug:\s*["\x27]([a-z0-9_-]+)["\x27]',
        (DESKTOP / "lib" / "page-manifest.ts").read_text(encoding="utf-8"),
    )
)
_stale_keys = sorted(k for k in PAGE_MAP if k not in _manifest_slugs)
if _stale_keys:
    print(
        "WARN: PAGE_MAP keys with no page-manifest slug (dropped): "
        + ", ".join(_stale_keys)
    )

ep_to_page = {}
for page, eps in PAGE_MAP.items():
    if page not in _manifest_slugs:
        continue
    for ep in eps:
        ep_to_page[normalize(ep)] = page

# 4. Classify
# wired: 前端 useApiOrMock 调用过的端点 OR PAGE_MAP 里登记的端点（页面通过根路径拉数据，
#        子端点是同页面的详情/操作/扫描，都算 wired）
# pending: 未使用（保留概念，当前逻辑下不会产生）
# no_page: 后端有但既不在前端调用也不在 PAGE_MAP 里
# backend_only: admin/status/stream 基础设施
result = []
for be in be_endpoints:
    ep_norm = normalize(be["endpoint"])
    in_frontend = ep_norm in wired_norm or be["endpoint"] in wired_paths
    page = ep_to_page.get(ep_norm)
    if in_frontend or page:
        status = "wired"
    elif (
        "/status" in be["endpoint"]
        or "/stream" in be["endpoint"]
        or be["endpoint"].startswith("/api/v1/admin")
    ):
        status = "backend_only"
    else:
        status = "no_page"
    result.append({**be, "page": page, "status": status})

# 5. Write TS file
lines = [
    "// AUTO-GENERATED by scripts/build-coverage.py",
    "// Run: python scripts/build-coverage.py",
    f"// Total: {len(result)} backend endpoints. wired={sum(1 for r in result if r['status'] == 'wired')} "
    f"pending={sum(1 for r in result if r['status'] == 'pending')} "
    f"no_page={sum(1 for r in result if r['status'] == 'no_page')} "
    f"backend_only={sum(1 for r in result if r['status'] == 'backend_only')}",
    "",
    'export type EndpointStatus = "wired" | "pending" | "no_page" | "backend_only";',
    "",
    "export interface EndpointCoverage {",
    "  endpoint: string;       // full path: /api/v1/...",
    "  method: string;         // GET | POST | PUT | DELETE | PATCH",
    "  source_file: string;    // backend route file",
    "  consumer_page: string | null;  // frontend page slug that consumes this",
    "  status: EndpointStatus;",
    "}",
    "",
    "export const ENDPOINT_COVERAGE: EndpointCoverage[] = [",
]
for r in result:
    page = f'"{r["page"]}"' if r["page"] else "null"
    lines.append(
        f'  {{ endpoint: "{r["endpoint"]}", method: "{r["method"]}", source_file: "{r["file"]}", consumer_page: {page}, status: "{r["status"]}" }},'
    )
lines.append("];")
lines.append("")
lines.append(
    "// Stats: see header comment. pending = has page, not wired. no_page = no consumer page (backend internal or unowned). backend_only = admin/status/stream."
)

OUT_FILE.write_text("\n".join(lines), encoding="utf-8")
print(f"Written: {OUT_FILE}")
print(f"Total: {len(result)}")

stat = Counter(r["status"] for r in result)
for k, v in stat.items():
    print(f"  {k}: {v}")
