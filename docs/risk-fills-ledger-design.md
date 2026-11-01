# FillsLedger / EquityTracker 设计文档 (Phase 2a)

**Status:** Phase 2a 设计 + RED 单测 (2026-07-26)
**Plan:** `internal-plan` L200-223
**Goal:** 从零设计两个核心类, 让 daily_realized_pnl + peak_equity 从硬编码 0/total_equity 变成真实值, 使 MaxDailyLossRule / DrawdownRule 真实生效

---

## 1. FillsLedger

### 1.1 Schema (SQLite)

```sql
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,          -- exchange fill ID (e.g. Binance tradeId)
    order_id TEXT NOT NULL,            -- client_order_id (FK to order_journal)
    symbol TEXT NOT NULL,              -- BTCUSDT
    qty REAL NOT NULL,                 -- filled quantity (positive)
    price REAL NOT NULL,               -- fill price
    fee REAL NOT NULL DEFAULT 0.0,     -- fee paid (positive, in quote currency)
    realized_pnl REAL NOT NULL,        -- computed realized PnL for this fill (0 for buy-to-open, real value for sell-to-close)
    timestamp INTEGER NOT NULL,        -- exchange event timestamp (ms)
    account_id TEXT NOT NULL,          -- pa-1 / live-1
    created_at INTEGER NOT NULL        -- local insert timestamp (ms)
);

CREATE INDEX IF NOT EXISTS idx_fills_account_ts ON fills(account_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_fills_symbol_ts ON fills(symbol, timestamp);
```

**设计决策:**
- `fill_id` PK — exchange 返回的 tradeId, 幂等去重 (同一 fill 重复推送不会双倍记账)
- `realized_pnl` 存储而非动态算 — 跨日 fill 聚合快 (按 timestamp 索引), 不需要每次重算 position average
- `account_id` 索引 — 隔离 paper / live / shadow 账户
- `timestamp` 是 exchange event 时间, 不是本地 insert 时间 — 避免 restart 后时间漂移

### 1.2 接口

```python
class FillsLedger:
    def record_fill(self, fill: Fill) -> None:
        """Idempotent insert. If fill_id exists, no-op."""

    def get_daily_realized_pnl(self, account_id: str, date: date) -> float:
        """Sum realized_pnl for account on given UTC date.
        Returns 0.0 if no fills."""

    def get_fills_for_order(self, order_id: str) -> list[Fill]:
        """All fills belonging to a single order (for partial fill tracking)."""

    def get_all_fills(
        self, account_id: str, start_ts: int, end_ts: int
    ) -> list[Fill]:
        """Range query for reconciliation / reporting."""

    def compute_realized_pnl_for_fill(
        self, fill: Fill, position_avg_entry: float | None
    ) -> float:
        """Pure function: compute realized_pnl given current position avg entry.
        - Buy (open long): returns 0 (no realized PnL)
        - Sell (close long): returns (price - avg_entry) * qty - fee
        - Sell (open short): returns 0
        - Buy (close short): returns (avg_entry - price) * qty - fee
        """
```

### 1.3 realized_pnl 计算规则

| 方向 | fill side | position before | realized_pnl |
|---|---|---|---|
| Open long | BUY | flat | 0 |
| Add long | BUY | long | 0 (avg_entry 更新) |
| Close long (partial) | SELL | long | `(price - avg_entry) * qty - fee` |
| Close long (full) | SELL | long | `(price - avg_entry) * qty - fee` (position → flat) |
| Reverse long→short | SELL | long | long 部分全平 realized, 剩余开 short |
| Open short | SELL | flat | 0 |
| Close short | BUY | short | `(avg_entry - price) * qty - fee` |

**关键:** `compute_realized_pnl_for_fill` 需要传入 `position_avg_entry` (live_engine 在 fill 时已知 position state). FillsLedger 不维护 position, 只记账.

### 1.4 边界 case

- **部分成交 (partial fill):** 同一 order_id 多个 fill, 每个 fill 独立记账, realized_pnl 按 fill 时 position state 计算
- **跨日持仓 (position held overnight):** realized_pnl 只在 close 时确认. 开仓日的 open fill realized_pnl=0, 平仓日的 close fill realized_pnl=完整值. daily_realized_pnl 按平仓日归属
- **手续费 (fee):** 纳入 realized_pnl 扣减. `realized_pnl = (price - avg_entry) * qty - fee` (long close)
- **Restart 恢复:** FillsLedger 是 SQLite 持久化, restart 后 load_fills() 重建 in-memory state. fill_id PK 保证幂等.

---

## 2. EquityTracker

### 2.1 状态机

```
                    ┌──────────────────────────────────┐
                    │                                  │
                    ▼                                  │
    ┌────────────────────────────┐                    │
    │   INIT                     │                    │
    │   peak_equity = 0          │                    │
    │   peak_date = today (UTC)  │                    │
    └────────────┬───────────────┘                    │
                 │                                    │
                 │ update(equity, ts)                 │
                 ▼                                    │
    ┌────────────────────────────┐                    │
    │   update()                 │                    │
    │   if ts.date != peak_date: │──reset─────────►   │
    │     peak_equity = equity   │                    │
    │     peak_date = ts.date    │                    │
    │   elif equity > peak:      │                    │
    │     peak_equity = equity   │                    │
    └────────────────────────────┘                    │
```

**规则:**
1. **单调递增 (intraday):** 同一 UTC 日内, `peak_equity` 只增不减. equity 下降时 peak 不变
2. **跨日重置 (UTC 00:00):** 新 UTC 日开始, `peak_equity` 重置为当日首次 update 的 equity 值
3. **持久化:** `peak_equity` + `peak_date` 持久化到 LiveStateRepository, restart 后恢复

### 2.2 接口

```python
class EquityTracker:
    def __init__(self, repo: LiveStateRepository | None = None) -> None:
        self._peak: float = 0.0
        self._peak_date: date | None = None
        self._repo = repo

    def update(self, equity: float, timestamp: int) -> None:
        """Update peak_equity with monotonic + daily-reset logic.
        timestamp is ms epoch. Date derived as UTC date."""

    @property
    def peak(self) -> float:
        """Current peak equity (0.0 if never updated)."""

    @property
    def peak_date(self) -> date | None:
        """UTC date of current peak (None if never updated)."""

    def get_drawdown(self, current_equity: float) -> float:
        """Returns drawdown ratio: (peak - current) / peak. 0.0 if peak <= 0."""

    def reset_for_new_day(self, equity: float, timestamp: int) -> None:
        """Force reset peak to current equity (called at UTC 00:00 boundary)."""

    def save_state(self) -> None:
        """Persist peak + peak_date to LiveStateRepository."""

    def load_state(self) -> None:
        """Restore peak + peak_date from LiveStateRepository."""
```

### 2.3 持久化方案

LiveStateRepository 加 2 个方法:

```python
def save_equity_peak(self, account_id: str, peak: float, peak_date: str) -> None: ...
def load_equity_peak(self, account_id: str) -> tuple[float, str] | None: ...
```

存储为单行 (account_id PK):
```sql
CREATE TABLE IF NOT EXISTS equity_peak (
    account_id TEXT PRIMARY KEY,
    peak REAL NOT NULL,
    peak_date TEXT NOT NULL,  -- ISO format YYYY-MM-DD
    updated_at INTEGER NOT NULL
);
```

### 2.4 边界 case

- **restart 恢复:** load_state() 从 SQLite 读 peak + peak_date. 如果 peak_date == today (UTC), 恢复 peak. 如果 peak_date < today, 视为跨日, 等首次 update() 触发 reset
- **UTC 00:00 跨日:** update() 内部检查 `ts.date() != self._peak_date` → reset. 不依赖外部定时任务
- **equity <= 0:** 不更新 peak (避免破产时 peak 卡在 0). DrawdownRule 单独处理 equity <= 0 case
- **首次 update:** peak_equity 从 0 直接跳到 equity (init case)

---

## 3. live_engine.py risk context 集成方案 (Phase 2b)

**当前 (Phase 2a 之前):**
```python
# live_engine.py L356-359
context = {
    "total_equity": total_equity,
    "positions": positions_dict,
    "portfolio_leverage": portfolio_leverage,
    "daily_realized_pnl": 0.0,  # TODO: wire from fills ledger
    "peak_equity": total_equity,  # TODO: wire from equity tracker
    "current_price": current_price,
}
```

**Phase 2b 目标:**
```python
from datetime import datetime, timezone

now_ms = int(time.time() * 1000)
today_utc = datetime.now(timezone.utc).date()

context = {
    "total_equity": total_equity,
    "positions": positions_dict,
    "portfolio_leverage": portfolio_leverage,
    "daily_realized_pnl": self._fills_ledger.get_daily_realized_pnl(
        account_id, today_utc
    ),
    "peak_equity": self._equity_tracker.peak,
    "current_price": current_price,
}

# Update equity tracker after computing context
self._equity_tracker.update(total_equity, now_ms)
```

**Constructor 加:**
```python
self._fills_ledger: FillsLedger | None = fills_ledger
self._equity_tracker: EquityTracker | None = equity_tracker
```

**Setter (mirror set_kill_switch pattern):**
```python
def set_fills_ledger(self, fills_ledger: Any | None) -> None:
    self._fills_ledger = fills_ledger

def set_equity_tracker(self, equity_tracker: Any | None) -> None:
    self._equity_tracker = equity_tracker
```

---

## 4. DrawdownRule E2E 验证 (Phase 2a RED)

**当前 (Phase 2a 之前):** peak_equity = total_equity → drawdown = 0 → 永不触发
**Phase 2a RED 测试:** 用 EquityTracker stub 模拟 peak=100, current=70 (30% drawdown) → DrawdownRule 阈值 20% → 触发 KillSwitch SYSTEM

```python
# test_drawdown_rule_e2e.py (RED)
def test_drawdown_rule_triggers_killswitch_e2e():
    tracker = EquityTracker()
    tracker.update(100.0, ts1)  # peak = 100
    # current_equity drops to 70 (30% drawdown)
    tracker.update(70.0, ts2)   # peak stays 100

    context = {
        "total_equity": 70.0,
        "peak_equity": tracker.peak,  # 100
        ...
    }
    rule = DrawdownRule(threshold=0.20)  # 20% threshold
    decision = rule.evaluate(context)
    assert decision.block is True  # RED: 当前 peak_equity=total_equity 永远 0% drawdown
```

---

## 5. 状态机图 (文字版) — peak_equity 跨日重置

```
Day 1 (UTC):
  09:00 update(100) → peak=100, peak_date=Day1
  14:00 update(120) → peak=120 (new high)
  18:00 update(110) → peak=120 (no change, drawdown 8.3%)
  23:59 update(115) → peak=120 (no change)

Day 2 (UTC 00:00):
  00:00 update(115) → ts.date() != peak_date → RESET
         peak=115, peak_date=Day2

  09:00 update(130) → peak=130 (new high)
  14:00 update(125) → peak=130 (no change, drawdown 3.8%)

Restart between Day1 18:00 and Day2 09:00:
  load_state() → peak=120, peak_date=Day1
  update(115, Day2 00:00) → ts.date() != peak_date → RESET
         peak=115, peak_date=Day2
```

---

## 6. 验收标准

- 4 个 RED 测试文件 (test_fills_ledger / test_equity_tracker / test_drawdown_rule_e2e)
- 所有测试 RED (stub raise NotImplementedError 或 assert 失败)
- 设计文档评审通过 (architect APPROVE)
- 不动 production code (Phase 2b 才实现 GREEN)

---

## 7. Phase 2b 预览 (不在 2a 范围)

- FillsLedger 真实实现 (SQLite + realized_pnl 计算)
- EquityTracker 真实实现 (monotonic + daily reset + persistence)
- live_engine.py L356-359 集成 (替换硬编码 0/total_equity)
- LiveStateRepository 加 save_equity_peak / load_equity_peak
- main.py wire FillsLedger + EquityTracker 到 LiveExecutionEngine
- 单元测试 GREEN
