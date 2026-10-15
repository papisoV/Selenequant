"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { useAuth } from "@/components/auth/AuthProvider";
import { clickable } from "@/lib/a11y/clickable";

// 后端 /api/v1/health 返回 {status, version, components: {<name>: {status, latency_ms}}}
// components 是 dict,key 为检查名,value 含 status+latency_ms。不含 balance/exchange。
interface HealthComponent {
  status?: string;
  latency_ms?: number;
}
interface HealthResponse {
  status?: string;
  components?: Record<string, HealthComponent>;
}

interface Account {
  id: string;
  icon: string;
  name: string;
  bal: string;
  status: "on" | "off" | "warn";
  kind: "binance" | "okx" | "alpaca" | "qmt" | "unknown";
}

function adaptKind(name: string): Account["kind"] {
  const n = name.toLowerCase();
  if (n.includes("binance")) return "binance";
  if (n.includes("okx")) return "okx";
  if (n.includes("alpaca")) return "alpaca";
  if (n.includes("qmt")) return "qmt";
  return "unknown";
}

// /health 用 healthy/degraded/unhealthy;UI 用 on/warn/off
function adaptStatus(s?: string): Account["status"] {
  if (s === "healthy" || s === "online" || s === "on") return "on";
  if (s === "degraded" || s === "warn") return "warn";
  return "off";
}

export function VheroAvatar() {
  const [open, setOpen] = useState(false);
  const { data: health } = useApi<HealthResponse>("/api/v1/health");
  const { displayName } = useAuth();

  // components dict → Account[] array;非 internal/生产无 components → 空数组走 fallback
  const components = health?.components ?? {};
  const accounts: Account[] = Object.entries(components).slice(0, 8).map(
    ([name, c], i) => {
      const icon = name.charAt(0).toUpperCase();
      const status = adaptStatus(c.status);
      return {
        id: String(i),
        icon,
        name,
        bal: "—",
        status,
        kind: adaptKind(name),
      };
    },
  );

  const connectedCount = accounts.filter((a) => a.status === "on").length;
  const hasAccounts = accounts.length > 0;

  return (
    <div
      className="vhero-avatar"
      title="账户"
      aria-expanded={open}
      {...clickable(() => setOpen((v) => !v))}
    >
      <div className="avatar-ring">P</div>
      <span className="avatar-dot" />
      <div className={`account-popover ${open ? "show" : ""}`}>
        <div className="ap-head">
          <div className="ap-avatar-sm">P</div>
          <div>
            <div className="ap-user">{displayName || "—"}</div>
            <div className="ap-user-sub">
              {hasAccounts ? `${connectedCount} 账户已连接` : "未登录"}
            </div>
          </div>
        </div>
        <div className="ap-body">
          <div className="ap-section-title">已连接账户</div>
          {hasAccounts ? (
            accounts.map((a) => (
              <div key={a.id} className={`ap-account ${a.status === "on" ? "active" : ""}`}>
                <div className={`ap-acct-icon ${a.kind}`}>{a.icon}</div>
                <div className="ap-acct-info">
                  <div className="ap-acct-name">{a.name}</div>
                  <div className="ap-acct-bal">{a.bal}</div>
                </div>
                <span
                  className={`ap-acct-status ${a.status}`}
                  title={a.status === "on" ? "已连接" : "未连接"}
                />
              </div>
            ))
          ) : (
            <div className="ap-account ap-empty">暂无连接的交易所账户</div>
          )}
        </div>
        <div className="ap-footer">
          <div className="ap-add-btn">+ 添加账户</div>
        </div>
      </div>
    </div>
  );
}
