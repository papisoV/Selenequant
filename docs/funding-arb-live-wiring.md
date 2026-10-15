# 期现套利双腿执行 — 上线装配文档

> 状态：**能力库已就绪（有测试），main.py 装配未做（"先文档后接线"）**。
> 本文档是唯一的装配说明书。何时有第二把 API key 再照此接线，接线后删掉本节标注"TODO wiring"的段落。

## 1. 现状：哪些已就绪（都有测试）

| 能力 | 位置 | 测试 |
|---|---|---|
| 双腿 delta 中性执行器（open/close/flatten + 腿差恢复 + 裸空保护） | `packages/exchange-sdk/selene_space_exchange/execution/dual_leg_executor.py` | `packages/exchange-sdk/tests/test_dual_leg_executor.py`（9 例） |
| IOC 循环平仓（market-IOC → limit-IOC 升级） | `packages/exchange-sdk/selene_space_exchange/execution/ioc_close.py` | `packages/exchange-sdk/tests/test_ioc_close.py` |
| `reduce_only` / `pos_side` 转发（BinanceFuturesTradingAdapter） | `packages/exchange-sdk/selene_space_exchange/trading.py` L62-63/L866-869 | `test_trading_adapters.py` 内 2 例 |
| funding_arb 信号路由（BUY→open / SELL→close，close_qty=net_position） | `services/local-api/app/core/strategy_runner.py` `_execute_funding_arb_pair` L1375+，分支 L1288-1293 | `tests/test_strategy_runner_signal_routing.py` 内 4 例 |
| 费率反转监控（独立 watchdog → KillSwitch STRATEGY scope + flatten） | `services/local-api/app/core/funding_reversal_monitor.py` | `tests/test_funding_reversal_monitor.py`（13 例） |

**策略循环本身已能驱动双腿执行器**：`_execute_funding_arb_pair` 在 runner 里已接线，只要 `set_engines(dual_leg_executor=...)` 传入实例，funding_arb 的 live 信号就会走双腿路径。

## 2. 阻塞点：双 API key 约束（为什么必须两把 key）

`main.py` 当前只解析**一把** key（L1157-1165，keyring 单一 `_key_env`），且 Amendment 4 权限门（L1264-1338）是**白名单式严格**的：

- 默认 `SELENE_ENABLE_FUTURES=0`：key 的 `enableFutures` 必须为 **False**，否则拒绝启动 → 只有**现货**权限
- `SELENE_ENABLE_FUTURES=1`：key 必须 `enableFutures=True` 且其余危险权限全 False → 只有**合约**权限

**一把 key 无法同时过两个门**。双腿套利现货腿+合约腿需要：

1. **现货 key**（enableFutures=False，spot trade only）→ `BinanceTradingAdapter`
2. **合约 key**（enableFutures=True，futures trade only）→ `BinanceFuturesTradingAdapter`

两把 key 各自的 permission 门都要单独过一遍（复用 L1264-1338 逻辑，但不能再"全局 refuse"，要 fail-open 到"该腿不可用"并告警）。

## 3. TODO wiring：装配步骤（第二把 key 就位后执行）

所有接线点都在 `services/local-api/app/main.py`。三段新增：

### 3.1 构造第二个客户端（合约腿）

在第一把 key 的 client 构造块（L1232-1262）旁，加合约 key 的解析 + `BinanceFuturesTradingAdapter`。独立 rate limiter（现货/合约 IP 相同但配额独立）：

```python
# 第二把 key（futures-only）—— keyring id 例如 selenespace.exchange.{KEY}_futures
_arb_fut_creds = ExchangeCredentials(...)  # 从 keyring 解析 _futures 后缀 ref
_fut_client = BinanceFuturesTradingAdapter(
    BinanceFuturesRESTClient(credentials=_arb_fut_creds, testnet=testnet_flag,
                             rate_limiter=_binance_limiter)
)
# 过 Amendment 4 futures 白名单门（SELENE_ENABLE_FUTURES=1 语义），失败 → 告警 + 不接双腿
```

现货腿复用主 `client`（需为 spot adapter）。若主 client 是 futures 而现货 key 是第二把，则调换 —— 原则：**一把现货 + 一把合约，各自过对应的门**。

### 3.2 构造执行器 + 注入 runner

在 `set_engines()` 两处调用（L1528 live rebind、L1559 通用）的 `dual_leg_executor=` 参数：

```python
from selene_space_exchange.execution.dual_leg_executor import DualLegExecutor
from selene_space_risk.kill_switch import KillSwitch  # 单例，与 L948/L1380 同一实例

_arb_executor = DualLegExecutor(
    spot_client=_spot_adapter,
    perp_client=_fut_adapter,
    kill_switch=KillSwitch(),  # 同一单例，is_active_for(strategy_id="funding_arb") 生效
)
strategy_runner_instance.set_engines(
    live_engine=_le, shadow_engine=shadow_engine, mode_provider=_get_mode,
    dual_leg_executor=_arb_executor,
)
```

> runner 侧 `_execute_signal` 路由条件：`strategy_id == "funding_arb"` 且 mode ∈ confirm/limited_auto/full_auto。传入 executor 后，funding_arb 的 live 信号自动走双腿路径，无需改 runner。

### 3.3 起费率反转监控（独立于策略循环）

在 startup 尾部（`set_strategy_runner` 之后）创建一个后台 watchdog：

```python
from app.core.dependencies import get_funding_rate_fetcher
from app.core.funding_reversal_monitor import FundingReversalMonitor

PERP_SYMBOL = "BTCUSDT"  # 与部署的 funding_arb 策略 perp_symbol 一致

async def _fetch_arb_funding():
    items = await get_funding_rate_fetcher().get_all(force=False)
    return next((it for it in items if it.symbol == PERP_SYMBOL), None)

_arb_monitor = FundingReversalMonitor(
    _fetch_arb_funding,
    exit_threshold=0.0001,          # 与 funding_arb 策略 exit_threshold 一致
    strategy_id="funding_arb",
    kill_switch=KillSwitch(),        # 同一单例 → 与 runner/executor 联动
    flatten=_arb_flatten_closure,    # 见下
    poll_interval_s=60.0,
)
_arb_monitor.start()
app.state.arb_monitor = _arb_monitor  # 生命周期随 app（stop() 在 shutdown 段）
```

`_arb_flatten_closure` 要带仓位上下文（每条部署的 spot_qty/perp_qty/价格），绑定到当前 funding_arb 部署的持仓：

```python
async def _arb_flatten_closure():
    pair = _current_funding_arb_position()   # 从 state_repo 读该部署现持仓
    if pair is None:
        return
    await _arb_executor.flatten(
        account_id=pair.account_id, strategy_id="funding_arb",
        spot_symbol=pair.spot_symbol, perp_symbol=PERP_SYMBOL,
        spot_qty=pair.spot_qty, perp_qty=pair.perp_qty,
        spot_price=pair.spot_price, perp_price=pair.perp_price,
        perp_pos_side=pair.perp_pos_side,
    )
```

## 4. 联动语义（接线后自动成立）

- **反转触发**：`funding_rate < 0.0001` → ① KillSwitch `activate_scope(STRATEGY, "funding_arb")`（同一单例）→ ② `_arb_executor.flatten(...)`。此后 `open_delta_neutral` 的 `_require_trading_allowed` 会拒绝新开仓，防反转后再入场。
- **幂等**：monitor 触发一次后不再触发，直到显式 `reset()`（确认 flat / 新入场时 re-arm）。
- **失败降级**：flatten 失败只记 ERROR 日志，KillSwitch 已激活仍挡住 re-entry；KillSwitch 激活失败则继续 flatten（best-effort 双保险）。
- **策略循环正常时双通道**：`get_signal` 的 exit_threshold 先触发 → 策略正常平仓；monitor 是"策略不在线"时的兜底，不是竞争路径。

## 5. 验证清单（接线后）

1. `packages/exchange-sdk/tests/test_dual_leg_executor.py` + `test_ioc_close.py` + `test_trading_adapters.py` 全绿
2. `services/local-api/tests/test_strategy_runner_signal_routing.py` + `test_funding_reversal_monitor.py` 全绿
3. 手工：funding_arb live 部署，信号 BUY → 日志出现 spot BUY + perp SELL 双腿；费率模拟打到 <0.0001 → 日志出现 "Funding reversal … force flat" + KillSwitch 激活
4. `SELENE_ENABLE_FUTURES=1` 环境下手动过合约 key 权限门，确认双门各自通过
