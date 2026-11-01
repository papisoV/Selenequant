# 设计：运行复盘（Run Retrospective）—— 📋7 吸收落点

> 状态：**设计定稿，随 B5 批实现**。来源 DSA `src/services/run_diagnostics.py`
> + `src/services/run_flow.py`（MIT）。本文件是设计闸口（plan §7「先设计，再实现」、
> §9「设计文档先过用户拍板」）的产物：**先读它，再看代码**。

## 1. 为什么不能照搬 DSA

DSA 的 run 是一条**报告管道**：一次 `analyze` 请求里，四处抓数据、调 LLM、
发通知、落历史，`run_diagnostics.py` 用 `ContextVar` 把沿途的
`ProviderRun` / `LLMRun` / `NotificationRun` / `HistoryRun` 挂到同一个
`trace_id` 上，事后汇总成一份给用户看的「数据健康度」面板。

Selene 的 run 是一条**阶段管道**：`DailyTradingCycleWorkflow.run()` 顺序跑
5 个 phase，每个 phase 吐一个 `PhaseOutcome`，最后合成一个 `WorkflowResult`。
DSA 的 `NotificationRun` / `HistoryRun` 在 Selene 没有对应物（Selene 不发通知、
阶段自己落产物），`ContextVar` 挂载也没有对应物（阶段之间靠不可变返回值传递）。

**结论：DSA 的记录类型不能平移。** 要重新绑到 Selene 的 run 模型上。

## 2. 一次 run 是什么

| 问题 | 决定 | 依据 |
|---|---|---|
| **一次 run = ？** | **一次 `DailyTradingCycleWorkflow.run(trading_date)` 执行** | 这是 Selene 现存的、唯一的「跑完一趟分析」单元；已有活生产者 `POST /api/v1/daily-cycle/run` |
| **记录单元 = ？** | **`PhaseOutcome`**（`phase`/`status`/`disposition`/`started_at`/`ended_at`/`metrics`/`artifacts`/`error`） | `packages/daily-cycle/…/types.py:69`，冻结 dataclass，已是 run 的可审计单元 |
| **trace_id 从哪来？** | **`WorkflowResult.workflow_id`**（形如 `daily_cycle_2026-10-11_ab12cd34`） | 已由 `daily_cycle.py` 生成，全局唯一，天然就是这条 run 的身份 |
| **保留多久？** | **不保留**。复盘是**纯函数**：吃一个 `WorkflowResult`（+ 可选 provider/LLM 记录），当场吐一份复盘对象；调用方决定要不要留 | 见 §4 |
| **是否持久化？** | **本批：否**。无 migration、无新表、无 convergence point | 见 §4 |

## 3. 吃什么、吐什么

**输入（全部只读、可选者优雅降级）**

1. `WorkflowResult`（**必需**）—— 主料。给出阶段时间线、终态、halted_at。
2. `ProviderRunRecord` 序列（可选）—— 数据源尝试（延迟/回退/缓存命中/条数/错误）。
   Selene 今天**没有**这层记录；故定义为**additive、调用方自备**的可选输入：
   有则纳入复盘，无则复盘退化为「阶段级」。
3. `LLMRunRecord` 序列（可选）—— 同上，LLM 调用（模型/token/耗时/回退）。
4. `redact` 可调用（可选）—— 文本脱敏器，默认取 security-gateway 的
   `redact_text`（懒解析）。注入口仅为测试与替换。

**输出**：`RunRetrospective`（冻结 dataclass）

- `trace_id` / `trading_date` / `started_at` / `ended_at` / `duration_ms`
- `status` ∈ `normal | degraded | failed | unknown` + `status_label`（中文）
- `reason`：一句话说清为什么是这个 status
- `phases: tuple[PhaseSpan, …]`：每阶段的 `status`/`disposition`/`duration_ms`/
  `metric_keys`/`artifact_keys`/`error`
- `provider_runs` / `llm_runs`：原样带上（已脱敏），供上层渲染
- `limitations: tuple[str, …]`：**降级清单**（哪个阶段被跳过/失败、哪个源回退/失败），
  按出现顺序去重 —— 与 📋6 的「部分数据契约」同一哲学：**一个源挂了只降级它自己**

**状态汇总口径（决定 `status`）**

| 条件（按序判定） | status |
|---|---|
| 无阶段 | `unknown` |
| workflow `FAILED`，或任一阶段 `FAILED` | `failed` |
| workflow `HALTED`，或任一阶段 `SKIPPED` | `degraded` |
| 其余（全 `SUCCESS`） | `normal` |

> 口径与 DSA 的 `unknown/failed/degraded/normal` 四值一致，**映射表**换成
> Selene 的 `WorkflowStatus`。**不改** `WorkflowResult` 本身 —— 复盘是旁路视图。

## 4. 保留与持久化（设计决策）

**本批不持久化。** 理由三条：

1. **照 DSA 的立场**：DSA 明确声明 Phase 1 只在内存、fail-open，持久化留待
   专用的 diagnostic store —— Selene 没有这种 store，**不发明**。
2. **Selene 已有落产地**：`analysis_records`(v009) / backtest tasks(v014) /
   worker heartbeats(v013) —— 真要做持久化，应先与这些对齐，属于独立一项，
   不属于本次吸收。
3. **零风险**：不落盘 = 不涉 migration = 不动 4 个 convergence point 中任何一个。
   若将来要落盘，是**新增一项**（带 migration），不是改本项。

**fail-open**：`build_run_retrospective` 永不抛。内部任何异常 → 记 warning，
退化返回一个最小 `unknown` 复盘。

## 5. 流程追踪（flow）

DSA `run_flow.py` 是 4 条语义泳道（entry/data_source/analysis/artifact）。
Selene 是**阶段管道**，照搬泳道会「挂空」。故 `run_flow.py` 重新绑为：

- **节点** = 5 个阶段 + 挂在各阶段下的 provider/LLM 记录
- **边** = 规范阶段顺序（`WorkflowPhase` 声明序）：`pre_market → risk_review →
  ic_decision → execution → post_market`，被 `halt_urgent` 截断
- **每节点**带 `status` / `severity` / `duration_ms` / 关键消息

这是对「这条 run 卡在哪、哪步降级」的可视化，不是泳道重排。

## 6. 与 DSA 的差异（Selene 化，非逐字搬运）

| 维度 | DSA | Selene 本实现 |
|---|---|---|
| run 单元 | 一次报告请求 | 一次 daily_cycle 执行 |
| 记录载体 | `ContextVar` 挂载 | 不可变 `WorkflowResult` + 可选记录序列 |
| 记录类型 | Provider/LLM/Notification/History 四类 | Phase（内置）+ Provider/LLM（可选、调用方自备）；**无** Notification/History |
| 脱敏 | 模块内自带 regex 组 | **复用** `security-gateway.redact_text`（懒解析 + 极小兜底） |
| 时钟 | 记录 `now()` | **无时钟**：时长由阶段自带的起止推出，便于确定性测试 |
| trace 传播 | ContextVar | `workflow_id` 直传 |

## 7. 不做（诚实缺口）

- **不发通知、不落历史**：Selene 无对应物，不硬造。
- **不接端点**：本批是纯库模块，不动 `main.py`（plan §7「汇聚点：无」）。
  端到端暴露（如 `/daily-cycle/run` 响应内嵌复盘）是**后续独立决定**。
- **不做持久化/检索**：见 §4。
