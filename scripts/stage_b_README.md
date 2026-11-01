# Stage B 24h Shadow Run 使用说明

## 前置
1. 项目根目录（本仓库根）
2. Python 环境（`call activate.bat` 后 selene_space_* 可用）
3. （可选）需要代理时自行设置：`set SELENE_PROXY_URL=http://<host>:<port>`

## 一键启动 24h run

```bash
# Windows
call activate.bat
python scripts/stage_b_shadow_run.py --duration=86400 --strategy-id=bb-v3-stage-b
```

## Smoke test (5min, 不要求 signal/fill)

```bash
python scripts/stage_b_shadow_run.py --smoke
```

## 手动跑 verdict

```bash
python scripts/stage_b_verdict.py
```

## 输出文件

- `data/stage_b/observations.jsonl` — 每 60s 一条 observation
- `data/stage_b/restarts.jsonl` — uvicorn 重启记录（若有）
- `data/stage_b/stage_b_report.md` — 验收报告（24h 后自动生成）
- `logs/stage_b_backend.log` — uvicorn stdout/stderr
- `~/.selenespace/journal/order_journal.db` — shadow engine journal WAL

## 验收标准（6 条）

| # | 标准 | 期望 |
|---|------|------|
| 1 | Shadow engine 无崩溃 | PASS |
| 2 | Journal WAL 完整 | PASS |
| 3 | Signal count > 0 | PASS (24h 预期 5-20) |
| 4 | Fill count > 0 | PASS |
| 5 | KillSwitch 触发 | EXPECTED FAIL (known gap) |
| 6 | 重启后 state 恢复 | PASS |

通过门槛: ≥5 条 PASS (标准 5 EXPECTED FAIL 不计)

## 失败诊断

若 verdict 报告任一 FAIL（非 EXPECTED FAIL），参考 plan v2 Step 5 的诊断流程：
- 标准 1 FAIL: 读 `restarts.jsonl` 的 stderr
- 标准 2 FAIL: SQL 找滞留 intent
- 标准 3 FAIL: 确认 `execution_mode=shadow` 路由 + `interval=1m`
- 标准 4 FAIL: 检查 `_bridge_price_to_shadow` + mark_price
- 标准 6 FAIL: 对比 `LiveStateRepository` positions 表

## 已知 gap

- 标准 5 KillSwitch: `shadow_engine` 未注册到 `_kill_switch_clients` (main.py:984)，Phase 2 修复
- 原 standard 5 equity 偏差: main.py:649-653 路由互斥，移至 Phase 2 双引擎对比
- 标准 6 positions_count: `make_observation()` 未写该字段，无重启时 trivially PASS，有重启会 mismatch (Phase 2 补)

## 扩展用法

### 自定义策略 / 间隔

```bash
# 换策略 + 短时长快速验证
python scripts/stage_b_shadow_run.py --strategy-id=rsi-ma-delta-stage-b --duration=3600

# 跑多个策略 (顺序，每个 1h)
for sid in bb-v3-stage-b rsi-ma-delta-stage-b ema-triple-cross-stage-b; do
  python scripts/stage_b_shadow_run.py --strategy-id=$sid --duration=3600
done
```

### 恢复中断的 24h run

```bash
# Ctrl+C 或 uvicorn 崩溃后，追加模式继续 (不覆盖 observations.jsonl)
python scripts/stage_b_shadow_run.py --duration=86400 --strategy-id=bb-v3-stage-b --resume
```

### 仅启动 uvicorn (不开 observation loop)

用于手动测试 /api/v1/health 或手动部署策略：

```bash
python scripts/stage_b_shadow_run.py --duration=0  # 0 = 立即退出 after deploy
```

### 独立验证 journal (不跑 verdict 全套)

```python
# Python REPL
import sqlite3
con = sqlite3.connect("file:~/.selenespace/journal/order_journal.db?mode=ro", uri=True)
print(con.execute("SELECT status, COUNT(*) FROM order_journal WHERE client_order_id LIKE 'sso-%' GROUP BY status").fetchall())
```

## 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `SELENE_EXECUTION_MODE` | (script 设为 `shadow`) | main.py 路由用，覆盖 paper/live |
| `SELENE_PROXY_URL` | (无) | GFW 代理，Binance/OKX API 需要 |
| `SELENE_STRATEGY_BB_LEN` | 5 | BB_v3 周期，Stage B 固定 1m |
| `SELENE_BB_BUY_MULT` | 2.0 | BB_v3 买倍率 |
| `SELENE_BB_SELL_MULT` | 2.0 | BB_v3 卖倍率 |
| `PYTHONUNBUFFERED` | 1 | uvicorn 日志不缓冲，实时写 log |

## 故障速查

| 症状 | 排查 |
|---|---|
| `wait_health` 超时 | 读 `logs/stage_b_backend.log` 找 uvicorn 启动报错 |
| deploy-paper 404 | 确认 strategy-id 在 `/api/v1/strategies/factory-templates` 列表里 |
| 标准 3 signal=0 | 确认 BB_v3 1m 间隔 + 60min warmup 已过 |
| 标准 4 fill=0 | 读 log 找 `_bridge_price_to_shadow` 或 `on_price_tick` 报错 |
| uvicorn 频繁重启 | 读 `restarts.jsonl` + log，可能 OOM / 端口占用 / proxy 断 |
