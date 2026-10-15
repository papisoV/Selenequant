# Shadow 全链演练 Runbook（五档 SHADOW 档实弹验证清单）

> 目的：实盘做好了 = 这条链**实弹验证过**，不是代码全。SHADOW 档五档闸（mode_guard）现成，
> 本 runbook 逐段核对"部署→信号→8 层硬限→journal 落盘→审计哈希链→对账→断连恢复"。
> 每步记 PASS/FAIL + 证据路径，跑完在文末表格登记。

## 前置

### 硬前置：交易所凭据（缺失则整条链跑不起来）

LiveExecutionEngine 只在启动时读到交易所凭据才初始化。缺凭据时启动日志会出现：

```
INFO:app.main:Live engine skipped (dev mode, no exchange credentials)
WARNING:app.main:execution_mode=live but LiveExecutionEngine is None; StrategyRunner stays on paper
INFO:app.main:StrategyRunner wired: live_engine=no shadow_engine=yes
```

此时 `LiveDecisionPipeline` 不存在，步骤 1/2/4/5/6 全部无法执行（`/live/test/push_tick`
会返回 `live_engine_not_wired`）。**先配凭据再开跑**。演练建议用测试网 key，不碰真钱。

自检：`GET /api/v1/live/status` 的 `audit_chain_health.available` 为 `true`
表示 pipeline 的 `_audit_ledger` 已接线（本演练步骤 4 的读数来源）。

### 服务与环境

- [ ] local-api 启动正常（**端口 8088**，不是 8010 — runbook 早前版本写错）
      ```
      call activate.bat
      cd services\local-api
      python -m uvicorn app.main:app --host 127.0.0.1 --port 8088
      ```
      启动含多交易所 WS 订阅，从拉起至 `Application startup complete` 约需 90s
- [ ] 演练要用 test 端点（步骤 2 的 push_tick / 步骤 6 的断连），需开闸门
      `SELENE_ENABLE_TEST_ENDPOINTS=true`（默认关闭，端点返回 `test_endpoints_disabled`）
- [ ] 多会话并行时端口常被别的窗口占着 — 起之前先确认归属，或换独立端口：
      ```
      (Get-NetTCPConnection -LocalPort 8088 -State Listen).OwningProcess
      ```
      **不要切别人实例的档位**：`PUT /trading/mode` 会改到别人正在跑的执行模式
- [ ] `GET /api/v1/trading/mode` 确认当前模式；切 SHADOW 档
      （PAPER/SHADOW 无需 live engine；CONFIRM/LIMITED_AUTO/FULL_AUTO 需引擎+连通性检查）
- [ ] `~/.selenespace/` 目录可写

## 演练步骤

### 1. 部署

- [ ] 选一个最小策略执行 deploy（SHADOW 档）
- [ ] mode_guard singleton 同步确认：日志出现 `ExecutionModeGuard` 相关行，无 mode 不一致告警
- [ ] 部署成功后 `~/.selenespace/memory/decision_outcomes.json` 新增 record_pending 条目（B1）
- 证据：deploy API 响应 JSON

### 2. 信号 → 8 层硬限

- [ ] 等待或触发一次真实信号（可用测试信号注入）
- [ ] LiveDecisionPipeline 硬限短路序逐层放行记录：白名单→单笔名义→杠杆→强制止损→日笔数→日名义→单票仓位→冷却
- [ ] 故意构造越限信号（如超 `max_single_order_notional`）确认 BLOCKED + reason 正确
- 证据：pipeline 日志行（action/reason 字段）

### 3. OrderJournal 落盘

- [ ] `~/.selenespace/journal/` 出现新 journal 文件
- [ ] 状态机走完 INTENT→SUBMITTED→CONFIRMED（SHADOW 档下按其语义核对）
- 证据：journal 文件内容（JSONL 每行一条状态）

### 4. 审计哈希链（C1 新接线）

- [ ] `~/.selenespace/audit/live_decisions.jsonl` 出现 append 记录，含
      `action/reason/order_id/strategy_id/symbol/side` 字段
- [ ] 哈希链连续：python 校验
      ```python
      from pathlib import Path
      from selene_space_paper.audit_ledger import HashChainLedger
      # HashChainLedger 直接 Path(path)，不展开 ~ —— 必须 expanduser，
      # 否则会在 CWD 下建一个字面量 "~" 目录，读到空链还误报 PASS
      chain = HashChainLedger(
          Path.home() / ".selenespace" / "audit" / "live_decisions.jsonl"
      ).read_chain()
      print(len(chain), "entries, chain OK")
      ```
      （`LedgerCorruptionError` = 链断，FAIL）
- 也可直接读面板字段：`GET /api/v1/live/status` → `audit_chain_health`
      （`available` / `entries` / `chain_ok` / `latest_action`），本字段即该链的用户可见读口
- 证据：read_chain 输出

### 5. 对账看门狗

- [ ] 运行 ≥5 分钟后 `_reconcile_positions` 无偏离告警（5s 周期，独立源比对，偏离>1% 超 5s 才报）
- 证据：strategy_runner 日志无 reconcile 错误行

### 6. 断连恢复（破坏性步骤，放最后）

- [ ] 杀 WS 连接（断网或防火墙拦截）→ ws_health_monitor / disconnect_triage 按预期告警
- [ ] 重启 local-api → StartupReconciliation 出 ReconciliationReport，本地 vs 交易所一致
- 证据：ReconciliationReport 输出

## 演练登记

| 步骤 | 日期 | 结果 | 备注 |
|---|---|---|---|
| 1 部署 | | | |
| 2 硬限 | | | |
| 3 journal | | | |
| 4 哈希链 | | | |
| 5 对账 | | | |
| 6 断连恢复 | | | |

全 PASS = 实盘链路验证闭环。任何 FAIL 先修再重跑该步。
