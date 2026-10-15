# grid-engine

Neutral two-leg grid engine (intra-bar 格子触达撮合) for research replays.

> **撮合语义与本仓主引擎不同**：本包用 intra-bar high/low 格子触达撮合（research-only）；
> 主引擎 `packages/backtest-engine` 的 broker 是 bar-close 撮合。两套数字体系并存是
> 共识计划 `internal-plan` 的显式 ADR 决策，勿混用对比。
> `dca_grid` 策略模板（strategy-engine）是 DCA 阶梯（bar-close），与本中性双腿网格是不同物种。

## 安装方式（铁律）

**绝不 `pip install -e .`**。本包经 PYTHONPATH 接线：

- 跑脚本前 `call activate.bat`（会把本包目录加入 PYTHONPATH —— 见根 `activate.bat`）
- pytest 无需激活（根 `pyproject.toml` 的 `pythonpath` 已含各包目录）

## 依赖声明

- `selene-space-types`（OrderType）
- `selene-space-backtest`（MakerTakerCommission —— 手续费唯一方言，网格 fill 不自算 fee）

## 移植来源

用户本人的实验代码 QuantDinger `scripts/grid_sim.py` @ 43eee46（Apache-2.0 上游，
作者 = 用户 git 身份 papisoV）。语义保持一致以支撑 fixture 逐位对齐验证
（`tests/fixtures/`，US-2）。

## 模块

| 模块 | 内容 |
|---|---|
| `cell.py` | GridSpec / Cell（5 态 FSM：entry_ready/entry_pending/exit_ready/exit_pending/disabled）/ FillEvent |
| `grid_build.py` | compute_levels（中性双腿展开，count 强制偶数）+ seed_initial |
| `grid_fill.py` | check_fills（intra-bar 触达）/ fill_entry / fill_exit（target_qty 跨 exit 持久化）/ fill_exit_market / waterfall_exit / rearm |
| `grid_engine.py` | GridEngine 薄门面（`GridEngine(spec, ledger)` 源调用形状，离线驱动模式对齐） |
