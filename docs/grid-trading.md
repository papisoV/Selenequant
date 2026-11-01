# Grid Trading System

## Architecture

The grid trading system is a Python implementation inspired by a Node.js grid bot pattern (clean-room reimplementation, no source code copied), integrated into the SeleneQuant platform. It supports three on-chain perpetual contract DEXes:

| Exchange | Chain | Adapter |
|----------|-------|---------|
| Decibel | Aptos | `dex/decibel/adapter.py` |
| Extended | Starknet | `dex/extended/adapter.py` + `stark_crypto.py` |
| RISEx | Ethereum | `dex/risex/adapter.py` |

### Core Components

```
PerpetualDEXMixin (ABC)     ← 22 abstract methods for grid operations
  ├── DecibelTradingAdapter
  ├── ExtendedTradingAdapter
  └── RisexTradingAdapter

GridExecutionEngine          ← fill-driven runtime (NOT kline-driven)
  ├── grid_math.py           ← pure functions (build_grid, seed_orders, replacement_for)
  ├── grid_types.py          ← Pydantic models (GridConfig, GridState, GridHealth)
  └── grid_state_repo.py    ← SQLite persistence (snapshot/resume for crash recovery)

AI Sentinel Tools            ← LLM-powered risk management
  ├── GridSentinelTool       ← risk patrol (ok/warn/critical)
  ├── GridMarketAnalysisTool ← multi-timeframe regime analysis
  ├── GridDailyReportTool    ← daily PnL attribution
  ├── GridOutOfRangeAdviceTool ← OOR AI advice
  └── GridChatControlTool    ← natural language → action proposal (AI never executes)

SentinelScheduler            ← asyncio periodic runner for all AI tools
```

### Key Design Decision: Fill-Driven Runtime

Grids are **fill-driven**, not kline-driven. The `GridExecutionEngine` does not use `get_signal()` — it is a self-contained runtime:

1. Seed initial ladder of limit orders
2. On every fill → place the opposite order one rung away
3. On every price tick → check out-of-range, manage recovery
4. Periodically reconcile against the real exchange order book

This means the grid engine cannot be a standard `BacktestStrategyTemplate` — it implements the `ExecutionEngine` protocol directly.

### Safety Principles

1. **AI never enters the trading fast-path** — order placement, fill handling, and reconciliation remain pure rule-based
2. **AI never directly executes write operations** — `GridChatControlTool` only returns action proposals that the user must confirm in the UI
3. **All AI call failures degrade gracefully** — errors are logged but never block trading

## Grid Configuration

| Parameter | Type | Description |
|-----------|------|-------------|
| `market_id` | int | DEX market identifier |
| `mode` | string | `neutral` (bidirectional), `long` (buy low, sell high), `short` (sell high, buy low) |
| `lower` | float | Lower price bound |
| `upper` | float | Upper price bound |
| `grid_count` | int | Number of grid rungs |
| `size_base` | float | Base order size in USDT |
| `leverage` | int | Leverage multiplier |
| `out_of_range_action` | string | `close` (close position) or `recover` (reduce-only recovery ladder) |

## API Endpoints

### Grid Control

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/grid/start` | Start a grid |
| POST | `/api/v1/grid/stop` | Stop a grid (optionally close position) |
| POST | `/api/v1/grid/cancel-orders` | Cancel all orders |
| POST | `/api/v1/grid/adjust-range` | Adjust price range |
| POST | `/api/v1/grid/recovery` | Start reduce-only recovery ladder |
| POST | `/api/v1/grid/close-position` | Market-close position |
| GET | `/api/v1/grid/state` | Get grid state (all or single exchange) |
| POST | `/api/v1/grid/reset-stats` | Reset cumulative stats |

### AI Sentinel

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/grid/sentinel` | Sentinel scheduler status |
| POST | `/api/v1/grid/sentinel/run` | Manually trigger a sentinel tool |
| POST | `/api/v1/grid/chat` | Chat with AI control assistant |

### Markets

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/grid/markets/{exchange}` | Available markets for a DEX |

## LLM Configuration

Sentinel LLM calls resolve in this order:

1. `SELENE_LLM_*` env vars (project-namespaced, preferred)
2. `AI_*` env vars (deprecated fallback, logged when used)
3. **Keyring** — the same `selenespace.llm` chain the rest of the platform uses

If none yields an API key, the sentinel tools degrade gracefully: calls fail
with a logged error instead of crashing trading.

| Variable | Default | Description |
|----------|---------|-------------|
| `SELENE_LLM_API_KEY` | — | LLM API key (highest priority) |
| `SELENE_LLM_BASE_URL` | — | LLM API base URL (a missing `/v1` is appended) |
| `SELENE_LLM_MODEL` | `claude-opus-4-5-20251101` | LLM model for sentinel tools |
| `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` | — | Deprecated fallbacks (H17) |
| `SELENE_GRID_SENTINEL_AUTO` | off | Opt-in gate for background auto-patrol (see below) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token for grid alerts |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID for grid alerts |
| `NOTIFY_WEBHOOK` | — | Webhook URL for grid alerts |

## Auto-Patrol Gate (`SELENE_GRID_SENTINEL_AUTO`)

**The background sentinel scheduler is OFF by default.** Starting a grid does
*not* auto-start it unless `SELENE_GRID_SENTINEL_AUTO=1` (or `true`/`yes`) is
set. This keeps periodic LLM spend opt-in.

What the gate controls — when off, **all three** push paths are silenced:

| Push | Trigger | Source |
|------|---------|--------|
| Patrol advisory (critical / warn) | `_run_sentinel` → `_maybe_push_sentinel` | `sentinel_scheduler.py:341` |
| Daily report | `_run_report` | `sentinel_scheduler.py:301` |
| Out-of-range alert | `_advise_oor` | `sentinel_scheduler.py:316-319` |

Market analysis (`_run_market`) only caches its result — it does **not** push.
So 4 scheduler tools map to 3 push paths.

When enabled, the cost is roughly **15 LLM calls/hour** at default intervals
(patrol 5 min, market 30 min, report daily; OOR fires only on an
out-of-range transition).

**Manual triggers are never gated**: `POST /api/v1/grid/sentinel/run` and
`POST /api/v1/grid/chat` run on demand regardless of the flag.

The gate only controls *automatic* calls — it does not gate config reads (the
first resolution may still touch the keyring).

## Sentinel Configuration

The `SentinelScheduler` runs three periodic tasks:

| Task | Default Interval | Description |
|------|-----------------|-------------|
| Sentinel patrol | 5 minutes | Risk inspection across all grids |
| Market analysis | 30 minutes | BTC multi-timeframe regime report |
| Daily report | 20:00 local | PnL attribution and actionable advice |
| OOR check | 30 seconds | Out-of-range transition detection |

Notification push is de-duplicated: alerts are only sent when the risk level changes or 30 minutes have passed since the last notification.

## Grid Modes

### Neutral (Bidirectional)
- Buy orders below current price, sell orders above
- Profits from price oscillation within the range
- Best for ranging/sideways markets

### Long (Buy Low, Sell High)
- Opening buy orders at grid levels below price
- Closing sell orders at grid levels above price
- Best for uptrending markets

### Short (Sell High, Buy Low)
- Opening sell orders at grid levels above price
- Closing buy orders at grid levels below price
- Best for downtrending markets

## Crash Recovery

The grid engine supports crash-safe persistence:

- `snapshot()` — serializes full grid state to a dict
- `resume(snapshot)` — restores from a snapshot after process restart
- `GridStateRepository` — SQLite-backed persistence for snapshots

On restart, the engine:
1. Loads the latest snapshot from SQLite
2. Reconciles tracked orders against the exchange
3. Resumes the grid from its last known state
