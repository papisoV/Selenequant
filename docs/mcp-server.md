# MCP Server（对外暴露 Selene 工具）

> Phase 0 · 2026-10-09 · 计划 `internal-plan`

Selene 后端把**只读工具**通过 MCP 协议暴露出来，供 Claude Code / Codex / 任意 MCP 客户端调用。
挂在 **`/mcp-server`**（与既有 `/api/v1/mcp` 管理 API 不同 —— 那个是管*外部* MCP server 的 CRUD）。

## 暴露的工具（16 个，全部只读）

```
market_ticker      market_kline       market_orderbook    market_snapshot
risk_check         risk_report        risk_pressure_check explain_pnl
read_skill         event_kline_attribution
strategy_get_contract  strategy_explain   strategy_generate  backtest_result
tg_signal_lookup   tg_event_impact
```

**不暴露**（有副作用，即便标 SAFE）：
`paper_create_account`(写账户) · `paper_place_order`(下单) · `strategy_validate`(写 failure memory) ·
`backtest_run`(落库) · `strategy_save_version/deploy/stop` · **`python_exec`(任意代码执行)**。

> 白名单按**工具名硬编码**，不按 `risk_level` —— 后者会骗人（`paper_create_account` 标 SAFE 却写状态）。

## 前提

- **进程内挂载**：MCP server 复用后端已开的运行时与 DB。后端必须先起（`:8088`）。
- **只读**：P0 零副作用。写能力见计划 Phase 2。

## 客户端配置

### Claude Code

在项目 `.mcp.json` 或用户 `~/.claude.json` 的 `mcpServers` 加：

```json
{
  "mcpServers": {
    "selene": {
      "type": "http",
      "url": "http://127.0.0.1:8088/mcp-server/"
    }
  }
}
```

### 通用（Claude Desktop / Cursor 等，支持 streamable-http）

```json
{
  "mcpServers": {
    "selene": {
      "url": "http://127.0.0.1:8088/mcp-server/"
    }
  }
}
```

> 不同客户端字段名可能不同（`type` / `transport`）。**url 末尾的 `/` 别省** —— 路径不匹配会 404。

## 验证

```bash
# 后端必须已起
python dev.py

# 用 fastmcp.Client 拉工具清单(裸 curl 会 400 Missing session ID —— 协议是有状态的)
python - <<'PY'
import asyncio
from fastmcp import Client
async def m():
    async with Client("http://127.0.0.1:8088/mcp-server/") as c:
        tools = sorted(t.name for t in await c.list_tools())
        print(len(tools), tools)
        assert "python_exec" not in tools          # 危险工具永不暴露
        print(await c.call_tool("market_snapshot", {"symbol": "BTC/USDT"}))
asyncio.run(m())
PY
```

启动日志应含：
```
INFO:app.main: MCP sub-app mounted at /mcp-server
INFO:app.main: MCP: 16 read-only tools exposed at /mcp-server
```

## 故障排查

| 症状 | 原因 |
|---|---|
| 请求 500 `Task group is not initialized` | 父 app 没链 MCP 子应用的 lifespan（见 `main.py` lifespan 的 `_mcp_lifespan_cm`） |
| `400 Missing session ID` | 裸 POST `tools/list` —— 协议要求先 `initialize`。用 MCP 客户端 |
| 工具返回 `NO_DATA_AVAILABLE` | 后端没有该行情源的活连接（如无 WS），**不是 MCP 故障** |
| 启动日志 `MCP server mount skipped` | fastmcp 缺失或挂载异常 —— 后端照常起（降级不崩） |

## 架构

- 工具来自后端**同一个** `ToolCatalog`（`main.py` FX09 注释的意图），不新建第二份。
- `create_app()` 建空 server + mount；`lifespan` 里 catalog 就绪后 `register_tools()`。
- 白名单 / 包装逻辑：`packages/agent-runtime/selene_space_agent/mcp/server.py`。
- 测试：`packages/agent-runtime/tests/test_mcp_server.py`（含端到端挂载 + 白名单护栏 + 反向验证）。
