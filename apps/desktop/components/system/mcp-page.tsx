"use client";

import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useApi } from "@/lib/hooks/use-api";

interface McpServer {
  name: string;
  status: string;
  tools: number;
  calls: string;
}

interface BackendMcpServer {
  name?: string;
  status?: string;
  tools?: number;
  tool_count?: number;
  calls?: number;
  call_count?: number;
}

// 设计稿 L4803 renderPageMcp — MCP 管理
export function McpPage() {
  const { data: realServers } = useApi<BackendMcpServer[] | null>(
    "/api/v1/mcp/servers"
  );
  const servers: McpServer[] = (() => {
    if (realServers && Array.isArray(realServers) && realServers.length > 0) {
      return realServers.map((s) => ({
        name: s.name ?? "?",
        status: s.status ?? "unknown",
        tools: Number(s.tools ?? s.tool_count ?? 0),
        calls: (s.calls ?? s.call_count ?? 0) !== 0 ? String(s.calls ?? s.call_count ?? 0) : "—",
      }));
    }
    return [];
  })();

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="MCP 服务器">
        <div className="mcp-list flex flex-col">
          {servers.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
          )}
          {servers.map((s) => {
            const color =
              s.status === "connected"
                ? "text-pos"
                : s.status === "error"
                  ? "text-neg"
                  : "text-ink-4";
            return (
              <div
                key={s.name}
                className="mcp-row flex items-center gap-3 text-[10px] py-1 border-b border-border-soft"
              >
                <span className="mcp-name flex-1 text-ink-2 font-mono">{s.name}</span>
                <span className={`mcp-status w-28 ${color}`}>● {s.status}</span>
                <span className="mcp-tools num w-16 text-right text-ink-3">{s.tools} tools</span>
                <span className="mcp-calls num w-12 text-right text-ink-4 font-mono">{s.calls}</span>
              </div>
            );
          })}
        </div>
      </V17Card>
      <V17Card title="统计">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock label="总数" value={servers.length} />
          <StatBlock
            label="已连接"
            value={servers.filter((s) => s.status === "connected").length}
            cls="text-pos"
          />
          <StatBlock label="工具总数" value="—" />
          <StatBlock label="24h 调用" value="—" />
        </div>
      </V17Card>
    </div>
  );
}
