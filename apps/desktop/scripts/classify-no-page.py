"""Classify no_page endpoints:归位到现有页面 or backend_only."""

import re

PATH = r"<local-path-redacted>"

with open(PATH, "r", encoding="utf-8") as f:
    content = f.read()

pattern = re.compile(
    r'endpoint:\s*"([^"]+)".*?method:\s*"([^"]+)".*?source_file:\s*"([^"]+)".*?status:\s*"no_page"'
)
no_page = pattern.findall(content)
print(f"Total no_page: {len(no_page)}")

# 页面 slug 映射: source_file 前缀 -> 已有页面 slug
# 从 page-manifest.ts 的 rest 字段派生
PAGE_ROUTE_PREFIX = {
    "us_radar": "us-radar",
    "polymarket": "poly",
    "strategies": "strat",
    "grid": "grid",
    "research": "rsch",
    "intelligence": "sig",
    "trading": "trd",
    "alpha": "alpha_mine",
    "skills_api": "skill",
    "paper": "paper",
    "optimizations": "optimizations",
    "derivatives": "liqmap",
    "agent": "chat-sessions",
    "chat_sessions": "chat-sessions",
    "options": "options-hub",
    "memory": "memory",
    "marketplace": "marketplace",
    "hyperliquid": "hyperliquid",
    "dex_trading": "dex-trading",
    "stock": "rsch",
    "onchain": "onchain",
    "notifications": "notifications",
    "mcp": "mcp",
    "account": "paper",
    "markets": "mk",
    "cvd": "cvd",
    "heatmap_api": "heatmap",
    "liquidations_api": "liquidations",
    "backtests": "backtests",
    "tof": "tof",
    "portfolio": "portfolio",
    "factors": "factors",
    "serenity": "serenity",
    "qevolve": "qevolve",
    "kf_index": "kfi",
    "liqmap": "liqmap",
    "liqratio": "liqratio",
    "gex": "gex",
    "agg_orderbook": "agg",
    "tac": "tac",
    "vchart": "vchart",
    "wid": "wid",
    "set": "set",
    "dash": "dash",
    "live_log": "live_log",
}

classified = {}
backend_only = []
for ep, method, src in no_page:
    prefix = src.replace(".py", "")
    slug = PAGE_ROUTE_PREFIX.get(prefix)
    if slug:
        classified.setdefault(slug, []).append((method, ep))
    else:
        backend_only.append((method, ep, src))

print(
    f"\n=== 归位到现有页面: {sum(len(v) for v in classified.values())} 端点 / {len(classified)} 页 ==="
)
for slug, eps in sorted(classified.items(), key=lambda x: -len(x[1])):
    print(f"  {slug:20} {len(eps):3} 端点")

print(f"\n=== 真 backend_only (无对应页面): {len(backend_only)} ===")
for method, ep, src in backend_only:
    print(f"  {method:6} {ep:60} {src}")
