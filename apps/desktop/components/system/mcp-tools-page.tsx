"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

// MCP 工具 — GET /api/v1/mcp/tools
// shape: { tools[{server_name, tool_name, registered_name, description}] }

interface McpTool {
  server_name?: string;
  tool_name?: string;
  registered_name?: string;
  description?: string;
}
interface McpToolsData {
  tools?: McpTool[];
}

export function McpToolsPage() {
  const tr = useUiText()

  const { useApi } = useApiPage();
  const { data, isLoading, mutate } = useApi<McpToolsData | null>(
    "/api/v1/mcp/tools",
  );
  const [refreshing, setRefreshing] = useState(false);
  const d = data ?? null;
  const tools = d?.tools ?? [];
  const servers = new Set(tools.map((t) => t.server_name)).size;

  const handleRefresh = async () => {
    setRefreshing(true);
    await mutate();
    setRefreshing(false);
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card
        title={tr("MCP 工具")}
        right={
          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing || isLoading}
            aria-label={tr("刷新")}
            className="rounded px-1 py-0.5 text-ink-3 hover:text-ink-2"
          >
            <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        }
      >
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("工具总数")} value={String(tools.length)} cls="text-ink" />
          <StatBlock label={tr("服务器数")} value={String(servers)} cls="text-pos" />
          <StatBlock label={tr("已启用")} value="—" cls="text-ink-4" />
        </div>
      </V17Card>

      <V17Card title={tr("工具列表")}>
        {tools.length === 0 ? (
          <div className="text-center text-ink-4 text-[11px] py-3">
            {isLoading ? tr("加载中…") : tr("暂无 MCP 工具（未连接服务器或已初始化）")}
          </div>
        ) : (
          <div className="max-h-96 overflow-auto">
            <table className="w-full text-[10px]">
              <thead>
                <tr className="text-left text-ink-4 border-b border-border-soft">
                  <th className="py-1 pr-2 font-normal">{tr("服务器")}</th>
                  <th className="py-1 pr-2 font-normal">{tr("工具名")}</th>
                  <th className="py-1 pr-2 font-normal">{tr("注册名")}</th>
                  <th className="py-1 font-normal">{tr("描述")}</th>
                </tr>
              </thead>
              <tbody>
                {tools.map((t, i) => (
                  <tr key={i} className="border-b border-border-soft/50">
                    <td className="py-1 pr-2 text-ink-3 font-mono">{t.server_name}</td>
                    <td className="py-1 pr-2 text-ink-2 font-mono">{t.tool_name}</td>
                    <td className="py-1 pr-2 text-ink-4 font-mono break-all">{t.registered_name}</td>
                    <td className="py-1 text-ink-3 truncate max-w-[220px]">{t.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </V17Card>
    </div>
  );
}
