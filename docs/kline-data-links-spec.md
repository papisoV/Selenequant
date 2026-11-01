# K线数据双链路规范

> 创建时间: 2026-05-21
> 目的: 明确两条数据链路的职责边界，避免后续开发混淆

---

## 一、双链路设计原理

两条数据链路**职责不同**，**不应合并**：

| 链路 | 核心需求 | 设计原因 |
|------|---------|---------|
| 图表链路 | 实时性、交互性 | WebSocket 推送 + IndexedDB 快速缓存 |
| 回测链路 | 历史深度、持久化 | SQLite 永久存储，支持离线回测 |

---

## 二、链路详细说明

### 图表链路

```
触发: 用户打开图表页面（手动触发）

数据流:
  REST API → IndexedDB → 图表渲染
  WebSocket → 实时更新 → 图表刷新

存储: IndexedDB (浏览器本地，最多 ~2000 条)

特点:
  - 实时性强（WebSocket 推送）
  - 快速显示（IndexedDB 缓存）
  - 按需获取，无后台自动

适用场景:
  - 实时监控
  - 交互式图表
  - 短期历史查看
```

### 回测链路

```
触发: 用户点击"运行回测"按钮（手动触发）

数据流:
  SQLite → 回测引擎
  (无数据时) REST API → SQLite → 回测引擎

存储: SQLite (后端文件，永久存储，无限制)

特点:
  - 历史深度（可存储数年）
  - 离线可用（本地持久化）
  - 自动积累（获取后保存）

适用场景:
  - 策略回测
  - 历史数据分析
  - 长期趋势研究
```

---

## 三、命名规范

### 图表链路命名（前缀: kline/chart）

```typescript
// Hooks
useKlineHistory()       // 图表历史数据
useKlineChart()         // 图表组件 hook
useMarketWS()           // WebSocket 实时数据

// 类型
KlineData               // 图表数据类型
KlineDataPoint          // 单条 K 线数据点

// 缓存
kline-cache.ts          // IndexedDB 缓存模块
getCachedKlines()       // 读缓存
putCachedKlines()       // 写缓存

// 组件
KLineChart              // 基础图表组件
SuperKLineChart         // 高级图表组件
```

### 回测链路命名（前缀: backtest/bt）

```python
# 存储
KlineDB                 # SQLite 存储类
kline_db.py             # SQLite 存储模块

# 管理
KlineDataManager        # 数据管理器
kline_manager.py       # 数据管理模块

# 回测
BacktestEngine          # 回测引擎
_load_kline_data()      # 回测数据加载函数
```

---

## 四、文件目录规范

```
apps/desktop/lib/
├── hooks/
│   ├── use-klines.ts           # 图表数据 hook ✅ 图表链路
│   ├── use-kline-data.ts       # 统一 hook（可选）✅ 图表链路
│   └── use-market-ws.ts        # WebSocket hook ✅ 图表链路
│   └── use-backtest-*.ts       # 回测相关 hook ✅ 回测链路
│
├── kline-cache.ts              # IndexedDB 缓存 ✅ 图表链路

packages/market-data/selene_space_market/
├── kline_db.py                 # SQLite 存储 ✅ 回测链路
├── kline_manager.py            # 数据管理器 ✅ 回测链路

services/local-api/app/api/v1/
├── markets.py                  # 图表 API ✅ 图表链路
├── backtests.py                # 回测 API ✅ 回测链路

scripts/
├── sync_klines.py              # 同步脚本 ✅ 回测链路（预加载）
```

---

## 五、新组件决策流程

```
新组件需要 K 线数据？

第一步：判断用途
├── 实时展示/交互？
│   └── ✅ 用图表链路
│       - useKlineHistory()
│       - IndexedDB ��存
│       - WebSocket 实时
│       - 文件放在 apps/desktop/lib/hooks/
│
├── 历史分析/回测？
│   └── ✅ 用回测链路
│       - SQLite (KlineDB)
│       - KlineDataManager
│       - 文件放在 packages/market-data/
│
└── 两者都需要？
    └── ⚠️ 分开处理，不要混用
        - 图表部分用图表链路
        - 回测部分用回测链路
        - 明确注释数据来源

第二步：命名检查
├── 图表相关 → 前缀 kline/chart
├── 回测相关 → 前缀 backtest/bt
└── 添加注释说明数据来源

第三步：文件位置
├── 图表组件 → apps/desktop/
├── 回测组件 → packages/market-data/ 或 services/local-api/
```

---

## 六、禁止事项

| 禁止行为 | 原因 |
|---------|------|
| 图表组件直接读取 SQLite | SQLite 在后端，图表在前端，无法直接访问 |
| 回测引擎读取 IndexedDB | IndexedDB 在浏览器，回测在后端，无法直接访问 |
| 混用两条链路的数据 | 数据可能不一致，导致错误 |
| 后台自动获取 K 线 | 交易所 API 有上限，无意义浪费资源 |

---

## 七、数据同步机制

两条链路**不需要主动同步**，但数据会**自动积累**：

```
图表链路获取数据 → IndexedDB 缓存（图表用）

回测链路获取数据 → SQLite 存储（回测用）
                  → 自动保存，下次可离线使用

同步脚本（可选） → 批量预加载到 SQLite
```

---

## 八、常见问题

### Q: 为什么不统一数据链路？

A: 职责不同，统一会引入风险：
- 图表需要实时性（WebSocket）
- 回测需要历史深度（SQLite）
- 统一需要大改现有组件，容易引入 bug

### Q: 回测时想看历史图表怎么办？

A: 图表有 `loadMore` 功能，可以加载更多历史数据（最多到交易所 API 上限）

### Q: SQLite 数据为空怎么办？

A: 运行回测时会自动从 API 获取并保存，或手动运行同步脚本预加载

---

## 九、参考文件

- 图表链路: `apps/desktop/lib/hooks/use-klines.ts`
- 回测链路: `packages/market-data/selene_space_market/kline_db.py`
- 同步脚本: `scripts/sync_klines.py`
- 计划文档: `internal-plan`