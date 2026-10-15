"use client";

import { useApi } from "@/lib/hooks/use-api";

// 后端 /api/v1/health 返回 {status, version, components: {<name>: {status, latency_ms}}}
// components 是 dict,key 为检查名(如 local_api / market_data),value 含 status+latency_ms
interface HealthComponent {
  status?: string;
  latency_ms?: number;
}
interface HealthResponse {
  status?: string;
  components?: Record<string, HealthComponent>;
}

interface Conn {
  name: string;
  status: "on" | "off" | "warn";
}

// /health 用 healthy/degraded/unhealthy;UI 用 on/warn/off
function adaptStatus(s?: string): "on" | "off" | "warn" {
  if (s === "healthy" || s === "online" || s === "on") return "on";
  if (s === "degraded" || s === "warn") return "warn";
  return "off";
}

export function VheroConn() {
  const { data: health } = useApi<HealthResponse>("/api/v1/health");

  // components dict → Conn[] array;非 internal/生产无 components → 空数组走 fallback
  const components = health?.components ?? {};
  const entries = Object.entries(components);
  const hasData = entries.length > 0;

  const conns: Conn[] = entries.slice(0, 8).map(([name, c]) => ({
    name,
    status: adaptStatus(c.status),
  }));

  const latencies = entries
    .map(([, c]) => Number(c.latency_ms ?? 0))
    .filter((n) => n > 0);
  const avgLatency = latencies.length > 0
    ? `${Math.round(latencies.reduce((s, n) => s + n, 0) / latencies.length)}ms`
    : "—";
  const latencyClass = latencies.length > 0
    ? (latencies.reduce((s, n) => s + n, 0) / latencies.length < 100 ? "pos" : "warn")
    : "off";

  return (
    <div
      className="vhero-conn"
      title={hasData ? "连接健康" : "暂无连接数据"}
    >
      <div className="conn-dots">
        {hasData ? (
          conns.map((c) => (
            <span
              key={c.name}
              className={`conn-dot ${c.status}`}
              title={`${c.name} · ${c.status}`}
            />
          ))
        ) : (
          <>
            <span className="conn-dot off" title="暂无连接数据" />
            <span className="conn-dot off" title="暂无连接数据" />
            <span className="conn-dot off" title="暂无连接数据" />
            <span className="conn-dot off" title="暂无连接数据" />
          </>
        )}
      </div>
      <span className="conn-label">CONN</span>
      <span className={`conn-latency ${latencyClass}`}>{avgLatency}</span>
    </div>
  );
}
