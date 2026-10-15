# SeleneQuant K线数据架构分析与改进方案

> 文档创建时间: 2026-05-21
> 状态: 规划中

---

## 一、现状分析

### 1.1 数据流架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    交易所 (Binance/OKX)                         │
│         REST API (/klines)     WebSocket (@kline_1m)            │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    后端处理层                                    │
│  BinanceSource ──→ KlineEvent ──→ Redis缓存 + EventBus          │
│  (binance_source.py)                                            │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    存储层                                        │
│  Redis Sorted Sets (24h TTL, 2000根/组合)                        │
│  内存 deque (实时, 200根)                                        │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    API层                                         │
│  GET /api/v1/markets/{symbol}/kline                             │
│  WS /api/v1/ws/markets (实时推送)                                │
└───────────────────────┬─────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    前端层                                        │
│  IndexedDB缓存 → useKlineHistory Hook → KLineChart组件          │
│  WebSocket → useMarketWS Hook → 合并到图表                       │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 数据获取方式

#### REST API（历史数据）

| 交易所 | 端点 | 参数 |
|--------|------|------|
| Binance 现货 | `https://api.binance.com/api/v3/klines` | symbol, interval, limit, endTime |
| Binance 合约 | `https://fapi.binance.com/fapi/v1/klines` | symbol, interval, limit, endTime |
| OKX 现货 | `https://www.okx.com/api/v5/market/candles` | instId, bar, limit |
| OKX 历史 | `https://www.okx.com/api/v5/market/history-candles` | instId, bar, before, after |

**原始数据格式**（Binance）：
```
[
  [1711000000000, "66800.0", "67200.0", "66700.0", "67000.0", "1234.56", ...],
  ...
]
```
索引映射：[0]=timestamp, [1]=open, [2]=high, [3]=low, [4]=close, [5]=volume

#### WebSocket（实时数据）

| 交易所 | 端点 | 订阅格式 |
|--------|------|----------|
| Binance | `wss://data-stream.binance.vision/ws` | `{"method":"SUBSCRIBE","params":["btcusdt@kline_1h"]}` |
| OKX | `wss://ws.okx.com:8443/ws/v5/business` | `{"op":"subscribe","args":[{"channel":"candle1H","instId":"BTC-USDT"}]}` |

### 1.3 存储容量

| 存储位置 | 技术 | 容量限制 | TTL | 用途 |
|----------|------|----------|-----|------|
| Redis | Sorted Set | 2000条/组合 | 24小时 | 后端缓存 |
| 内存 | deque | 200条/组合 | 会话期 | 实时缓冲 |
| IndexedDB | IndexedDB | 2000条/组合 | 持久化 | 前端缓存 |

### 1.4 时间周期

**15种周期**（`lib/intervals.ts`）：

| 分类 | 周期 |
|------|------|
| 分钟级 | 1m, 3m, 5m, 15m, 30m |
| 小时级 | 1h, 2h, 4h, 6h, 8h, 12h |
| 日级+ | 1d, 3d, 1w, 1M |

### 1.5 数据组合规模

```
组合数 = 交易所(2) × 市场类型(2) × 交易对(30) × 周期(15) = 1800 种组合
```

---

## 二、问题分析

### 2.1 核心问题

| 问题 | 严重性 | 说明 |
|------|--------|------|
| **无持久化存储** | 高 | Redis 24h TTL，历史数据丢失 |
| **容量限制** | 高 | 2000条/组合，回测不够用 |
| **WS断连丢数据** | 中 | 重连只获取snapshot，断开期间数据丢失 |
| **两套独立Hook** | 中 | useKlineHistory 和 useMarketWS 独立运行，手动合并 |
| **回测每次联网** | 中 | 每次回测都实时请求交易所API |

### 2.2 容量分析

**每种组合的K线数量**（按1年历史）：

| 周期 | 1年K线数 | 单条大小 | 存储大小 |
|------|----------|----------|----------|
| 1m | 525,600 | 48B | 25 MB |
| 5m | 105,120 | 48B | 5 MB |
| 15m | 35,040 | 48B | 1.7 MB |
| 1h | 8,760 | 48B | 420 KB |
| 1d | 365 | 48B | 18 KB |

**数据规模估算**：
- 单交易对全部周期：约 32 MB/年
- 30个交易对：约 960 MB/年
- 加上交易所/市场类型：约 3.8 GB/年
- **3年历史数据：约 11 GB**

### 2.3 WebSocket与REST同步问题

**当前合并逻辑**（`markets2/page.tsx`）：
```typescript
const marketKlines = useMemo(() => {
  const map = new Map<number, KlineData>()
  for (const k of historyKlines) map.set(k.timestamp, k)  // REST
  for (const k of wsKlines) map.set(k.timestamp, k)       // WS覆盖
  return Array.from(map.values()).sort(...)
}, [historyKlines, wsKlines])
```

**问题**：
1. WebSocket断连后不会主动从REST补齐缺失数据
2. 两个Hook独立运行，没有协调机制
3. 重连后只收到snapshot，可能丢失断开期间的K线

---

## 三、改进方案

### 3.1 设计原则

```
原则1: 不破坏现有流程（图表实时性不变）
原则2: 新增模块独立，渐进式接入
原则3: 统一数据源管理，减少重复逻辑
原则4: 支持离线回测，减少联网依赖
```

### 3.2 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      统一数据层 (新增)                           │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  KlineDataManager                                        │   │
│  │  ├── getKlines(symbol, interval, start, end) → 数据      │   │
│  │  ├── syncHistory() → 增量同步到 SQLite                   │   │
│  │  └── onWSDisconnect() → 自动补齐缺失数据                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│              ┌───────────────┼───────────────┐                 │
│              ▼               ▼               ▼                 │
│        ┌──────────┐   ┌──────────┐   ┌──────────┐             │
│        │ SQLite   │   │  Redis   │   │ WebSocket│             │
│        │ (持久化) │   │ (实时缓存)│   │ (实时推送)│             │
│        └──────────┘   └──────────┘   └──────────┘             │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │ 图表展示  │   │ 回测引擎  │   │ 策略监控  │
        │ (实时)   │   │ (历史)   │   │ (实时)   │
        └──────────┘   └──────────┘   └──────────┘
```

### 3.3 分层设计

#### Layer 1: SQLite持久化存储

**文件**: `packages/market-data/selene_space_market/kline_db.py`

```python
class KlineDB:
    """SQLite 持久化存储，用于回测"""

    def __init__(self, db_path: str | None = None):  # None -> default_klines_db_path()
        self._db = sqlite3.connect(db_path)
        self._init_tables()

    def _init_tables(self):
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS klines (
                exchange TEXT,
                market_type TEXT,
                symbol TEXT,
                interval TEXT,
                timestamp INTEGER,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                closed INTEGER,
                PRIMARY KEY (exchange, market_type, symbol, interval, timestamp)
            )
        """)
        self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_klines_time
            ON klines(exchange, market_type, symbol, interval, timestamp)
        """)

    def get_klines(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> pd.DataFrame:
        """查询K线数据"""
        query = """
            SELECT timestamp, open, high, low, close, volume
            FROM klines
            WHERE exchange = ? AND market_type = ? AND symbol = ? AND interval = ?
        """
        params = [exchange, market_type, symbol, interval]

        if start_date:
            query += " AND timestamp >= ?"
            params.append(int(start_date.timestamp() * 1000))
        if end_date:
            query += " AND timestamp <= ?"
            params.append(int(end_date.timestamp() * 1000))

        query += " ORDER BY timestamp ASC"

        df = pd.read_sql_query(query, self._db, params=params)
        return df

    def put_klines(self, klines: list[KlineEvent]) -> int:
        """批量写入K线数据"""
        rows = [
            (
                k.exchange, k.market_type, k.symbol, k.interval,
                k.timestamp, k.open, k.high, k.low, k.close, k.volume,
                1 if k.closed else 0
            )
            for k in klines
        ]
        self._db.executemany("""
            INSERT OR REPLACE INTO klines
            (exchange, market_type, symbol, interval, timestamp, open, high, low, close, volume, closed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        self._db.commit()
        return len(rows)

    def get_latest_timestamp(
        self, exchange: str, market_type: str, symbol: str, interval: str
    ) -> int | None:
        """获取最新K线时间戳"""
        cursor = self._db.execute("""
            SELECT MAX(timestamp) FROM klines
            WHERE exchange = ? AND market_type = ? AND symbol = ? AND interval = ?
        """, (exchange, market_type, symbol, interval))
        return cursor.fetchone()[0]

    def get_earliest_timestamp(
        self, exchange: str, market_type: str, symbol: str, interval: str
    ) -> int | None:
        """获取最早K线时间戳"""
        cursor = self._db.execute("""
            SELECT MIN(timestamp) FROM klines
            WHERE exchange = ? AND market_type = ? AND symbol = ? AND interval = ?
        """, (exchange, market_type, symbol, interval))
        return cursor.fetchone()[0]

    def count(
        self, exchange: str, market_type: str, symbol: str, interval: str
    ) -> int:
        """统计K线数量"""
        cursor = self._db.execute("""
            SELECT COUNT(*) FROM klines
            WHERE exchange = ? AND market_type = ? AND symbol = ? AND interval = ?
        """, (exchange, market_type, symbol, interval))
        return cursor.fetchone()[0]
```

#### Layer 2: 统一数据管理

**文件**: `packages/market-data/selene_space_market/kline_manager.py`

```python
class KlineDataManager:
    """统一K线数据管理"""

    def __init__(self, db: KlineDB, service: MarketDataService):
        self._db = db
        self._service = service

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        exchange: str = "binance",
        market_type: str = "futures",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        source: str = "auto",  # "auto" | "local" | "remote"
    ) -> pd.DataFrame:
        """
        获取K线数据

        Args:
            source:
                - "auto": 优先本地，没有则联网
                - "local": 只读本地
                - "remote": 强制联网
        """
        # 1. 尝试从本地获取
        if source != "remote":
            local = self._db.get_klines(
                exchange, market_type, symbol, interval, start_date, end_date
            )
            if len(local) > 0:
                return local

        if source == "local":
            return pd.DataFrame()  # 本地无数据，返回空

        # 2. 从交易所获取
        klines = await self._service.fetch_klines_rest(
            symbol=symbol,
            exchange=exchange,
            market_type=market_type,
            interval=interval,
            limit=1500,
        )

        if klines:
            # 3. 写入本地数据库
            self._db.put_klines(klines)
            return self._db.get_klines(
                exchange, market_type, symbol, interval, start_date, end_date
            )

        return pd.DataFrame()

    async def sync_incremental(
        self,
        symbol: str,
        interval: str,
        exchange: str = "binance",
        market_type: str = "futures",
        days: int = 365,
    ) -> int:
        """增量同步K线数据"""
        latest_ts = self._db.get_latest_timestamp(
            exchange, market_type, symbol, interval
        )

        now = datetime.now()
        if latest_ts:
            start = datetime.fromtimestamp(latest_ts / 1000)
        else:
            start = now - timedelta(days=days)

        klines = await self._service.fetch_klines_rest(
            symbol=symbol,
            exchange=exchange,
            market_type=market_type,
            interval=interval,
            limit=1500,
        )

        if klines:
            return self._db.put_klines(klines)
        return 0

    async def sync_history(
        self,
        symbols: list[str],
        intervals: list[str],
        exchange: str = "binance",
        market_type: str = "futures",
        days: int = 365 * 3,
    ) -> dict[str, int]:
        """批量同步历史数据"""
        results = {}
        for symbol in symbols:
            for interval in intervals:
                key = f"{exchange}:{market_type}:{symbol}:{interval}"
                count = await self.sync_incremental(
                    symbol, interval, exchange, market_type, days
                )
                results[key] = count
        return results

    async def on_ws_disconnect(
        self,
        symbol: str,
        interval: str,
        exchange: str,
        market_type: str,
        last_ts: int,
    ) -> int:
        """WebSocket断连后补齐缺失数据"""
        klines = await self._service.fetch_klines_rest(
            symbol=symbol,
            exchange=exchange,
            market_type=market_type,
            interval=interval,
            limit=200,
        )

        # 过滤出缺失的数据
        missing = [k for k in klines if k.timestamp > last_ts]

        if missing:
            return self._db.put_klines(missing)
        return 0
```

#### Layer 3: 同步脚本

**文件**: `scripts/sync_klines.py`

```python
#!/usr/bin/env python
"""K线数据同步脚本"""

import asyncio
from datetime import datetime
from selene_space_market.kline_db import KlineDB
from selene_space_market.service import MarketDataService
from selene_space_market.kline_manager import KlineDataManager

# 配置
SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
    "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT",
    # ... 更多交易对
]

INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"]

EXCHANGES = [
    ("binance", "futures"),
    ("binance", "spot"),
    ("okx", "futures"),
    ("okx", "spot"),
]

DAYS = 365 * 3  # 3年历史

async def main():
    db = KlineDB()  # canonical repo-root data/klines.db
    service = MarketDataService()
    manager = KlineDataManager(db, service)

    print(f"开始同步K线数据: {len(SYMBOLS)}个交易对, {len(INTERVALS)}个周期")

    total = 0
    for exchange, market_type in EXCHANGES:
        print(f"\n=== {exchange}/{market_type} ===")
        results = await manager.sync_history(
            symbols=SYMBOLS,
            intervals=INTERVALS,
            exchange=exchange,
            market_type=market_type,
            days=DAYS,
        )
        for key, count in results.items():
            if count > 0:
                print(f"  {key}: +{count}条")
                total += count

    print(f"\n同步完成，共 {total} 条K线")

if __name__ == "__main__":
    asyncio.run(main())
```

### 3.4 回测集成

**修改文件**: `services/local-api/app/api/v1/backtests.py`

```python
async def _load_kline_data(
    config: Any,
    body: RunBacktestRequest,
) -> pd.DataFrame:
    """加载K线数据 - 优先本地数据库"""

    symbol = body.symbol or config.symbols[0] if config.symbols else "BTC/USDT"
    interval = body.interval or config.interval or "1h"
    exchange = body.exchange or "binance"
    market_type = body.market_type or "futures"

    # 新增：优先从SQLite读取
    from selene_space_market.kline_db import KlineDB
    from selene_space_market.kline_manager import KlineDataManager

    db = KlineDB()
    manager = KlineDataManager(db, get_market_service())

    start_date = body.start_date
    end_date = body.end_date

    # 尝试从本地获取
    local_df = db.get_klines(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
    )

    if len(local_df) > 0:
        logger.info("从本地数据库加载 %d 条K线", len(local_df))
        return local_df

    # 本地没有，走原有流程（交易所API）
    logger.info("本地无数据，从交易所获取")
    # ... 原有逻辑 ...
```

### 3.5 前端优化（可选）

**新增文件**: `lib/hooks/use-kline-data.ts`

```typescript
/**
 * 统一K线数据Hook
 * 整合历史数据获取和WebSocket实时更新
 */

export function useKlineData(
  symbol: string,
  interval: string,
  exchange: string = "binance",
  marketType: string = "futures"
) {
  const [klines, setKlines] = useState<KlineData[]>([])
  const [source, setSource] = useState<'local' | 'remote'>('local')
  const [isLoading, setIsLoading] = useState(true)
  const [isStale, setIsStale] = useState(false)

  // 初始化：读IndexedDB + 增量获取
  useEffect(() => {
    const init = async () => {
      setIsLoading(true)

      // 1. 读本地缓存
      const cached = await getCachedKlines(exchange, marketType, symbol, interval)
      const latestTs = cached[cached.length - 1]?.timestamp

      // 2. 增量获取
      const fresh = await fetchKlineData(
        symbol, interval,
        since: latestTs,
        exchange, marketType
      )

      // 3. 合并
      const merged = mergeKlines(cached, fresh)
      setKlines(merged)
      setSource(fresh.length > 0 ? 'remote' : 'local')

      // 4. 写回缓存
      if (fresh.length > 0) {
        await putCachedKlines(exchange, marketType, symbol, interval, merged)
      }

      setIsLoading(false)
    }

    init()
  }, [symbol, interval, exchange, marketType])

  // WebSocket实时更新
  useMarketWS({
    symbol,
    interval,
    exchange,
    marketType,
    onKline: (kline) => {
      setKlines(prev => mergeKlines(prev, [kline]))
      setIsStale(false)
    },
    onConnect: () => setIsStale(false),
    onDisconnect: async (lastTs) => {
      setIsStale(true)
      // 断连补齐
      const missing = await fetchKlineData(
        symbol, interval,
        since: lastTs,
        exchange, marketType
      )
      if (missing.length > 0) {
        setKlines(prev => mergeKlines(prev, missing))
      }
    }
  })

  return { klines, source, isLoading, isStale }
}

function mergeKlines(existing: KlineData[], incoming: KlineData[]): KlineData[] {
  const map = new Map<number, KlineData>()
  for (const k of existing) map.set(k.timestamp, k)
  for (const k of incoming) map.set(k.timestamp, k)  // 覆盖
  return Array.from(map.values()).sort((a, b) => a.timestamp - b.timestamp)
}
```

---

## 四、实施计划

### 4.1 阶段划分

| 阶段 | 内容 | 工作量 | 优先级 |
|------|------|--------|--------|
| Phase 1 | SQLite持久化模块 | 1天 | P0 |
| Phase 2 | 同步脚本 | 1天 | P0 |
| Phase 3 | 回测集成 | 1天 | P0 |
| Phase 4 | 前端统一Hook | 1天 | P1 |
| Phase 5 | WS断连补齐 | 0.5天 | P1 |

### 4.2 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `packages/market-data/selene_space_market/kline_db.py` | 新增 | SQLite存储 |
| `packages/market-data/selene_space_market/kline_manager.py` | 新增 | 统一管理 |
| `scripts/sync_klines.py` | 新增 | 同步脚本 |
| `services/local-api/app/api/v1/backtests.py` | 修改 | 回测集成 |
| `apps/desktop/lib/hooks/use-kline-data.ts` | 新增 | 统一Hook |

### 4.3 测试计划

```python
# tests/test_kline_db.py

def test_put_and_get_klines():
    db = KlineDB(":memory:")
    klines = [
        KlineEvent(exchange="binance", market_type="futures", symbol="BTC/USDT",
                   timestamp=1711000000000, interval="1h",
                   open=66800, high=67200, low=66700, close=67000, volume=1000, closed=True)
    ]
    db.put_klines(klines)
    df = db.get_klines("binance", "futures", "BTC/USDT", "1h")
    assert len(df) == 1
    assert df.iloc[0]["close"] == 67000

def test_get_latest_timestamp():
    db = KlineDB(":memory:")
    assert db.get_latest_timestamp("binance", "futures", "BTC/USDT", "1h") is None
    # ... 插入数据后验证
```

---

## 五、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 数据不一致 | 中 | 使用timestamp作为主键，自动去重 |
| 存储空间不足 | 中 | 定期清理旧数据，压缩存储 |
| 同步失败 | 低 | 重试机制，日志记录 |
| 性能下降 | 低 | 批量写入，索引优化 |

---

## 六、关键文件索引

| 功能 | 文件路径 |
|------|----------|
| REST获取 | `packages/market-data/selene_space_market/sources/binance_source.py` |
| WebSocket处理 | `packages/market-data/selene_space_market/sources/binance_source.py` |
| Redis缓存 | `packages/market-data/selene_space_market/kline_store.py` |
| API端点 | `services/local-api/app/api/v1/markets.py` |
| IndexedDB | `apps/desktop/lib/kline-cache.ts` |
| 历史Hook | `apps/desktop/lib/hooks/use-klines.ts` |
| WebSocket Hook | `apps/desktop/lib/use-market-ws.ts` |
| 图表组件 | `apps/desktop/components/markets2/kline-chart.tsx` |
| 数据合并 | `apps/desktop/app/(dashboard)/markets2/page.tsx` |

---

## 七、总结

### 改进收益

| 收益 | 说明 |
|------|------|
| **离线回测** | 本地有数据时无需联网 |
| **回测加速** | 直接读取本地数据库，无需等待API |
| **数据完整** | WS断连自动补齐 |
| **代码简化** | 统一数据管理，减少重复逻辑 |

### 不变的部分

| 模块 | 说明 |
|------|------|
| 图表实时性 | WebSocket实时推送不变 |
| Redis缓存 | 实时数据仍用Redis |
| IndexedDB | 前端缓存不变 |
