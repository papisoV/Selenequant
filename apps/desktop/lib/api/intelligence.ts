"use client";

import { api, apiDirect } from "@/lib/api-client";

const BASE = "/api/v1/intelligence";

export interface AlertRule {
  id: string;
  name: string;
  type: string;
  threshold: Record<string, unknown>;
  enabled: boolean;
  /** 监控标的。price / indicator 必填:没有标的的阈值规则无法求值。 */
  symbol?: string;
  channel_id?: string | number;
  description?: string;
  [k: string]: unknown;
}

export interface AlertRulesResult {
  rules: AlertRule[];
  /** 后端是否已完成 localStorage 规则的一次性迁移。false = 还没迁过。 */
  seeded?: boolean;
  /** seed 时被拒的规则(如旧 price 规则没有标的)。只回 id/name/errors。 */
  skipped?: Array<{ id?: string; name?: string; errors?: string[]; reason?: string }>;
  created?: number;
  version?: string;
  [k: string]: unknown;
}

export interface AlertRuleDraft {
  name: string;
  type: string;
  threshold: Record<string, unknown>;
  enabled: boolean;
  symbol?: string;
  channel_id?: string | number;
  description?: string;
  [k: string]: unknown;
}

// 本地保存路径(前端先行:不调后端,由 store 持久化到 localStorage,id 本地生成)
export function saveAlertRuleLocal(draft: AlertRuleDraft): AlertRule {
  return {
    id: `alert-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
    ...draft,
  };
}

// 本地删除路径(无后端副作用,返回 id 供调用方确认)
export function deleteAlertRuleLocal(id: string): string {
  return id;
}

// 存量兼容:旧代理路径(GET 经 Next proxy,IPv6 bug 下不可用)。
// 新代码请用 syncAlertRules(apiDirect 直连)或直接读 useAlertsStore。
export async function getAlertRules(): Promise<AlertRulesResult> {
  return await api<AlertRulesResult>(`${BASE}/alerts/rules`);
}

// 新 GET 同步:直连解 envelope(禁代理)
export async function syncAlertRules(): Promise<AlertRulesResult> {
  return await apiDirect<AlertRulesResult>(`${BASE}/alerts/rules`);
}

// ---- 规则写路径(后端为真相源) ----
// 规则存在后端,是因为监控必须「人不在也跑」:留在 localStorage 的规则只在
// 页面打开时存在,那不叫监控。

export async function createAlertRule(draft: AlertRuleDraft): Promise<AlertRule> {
  return await apiDirect<AlertRule>(`${BASE}/alerts/rules`, {
    method: "POST",
    body: JSON.stringify(draft),
  });
}

export async function updateAlertRule(
  id: string,
  draft: AlertRuleDraft,
): Promise<AlertRule> {
  return await apiDirect<AlertRule>(`${BASE}/alerts/rules/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(draft),
  });
}

export async function deleteAlertRule(id: string): Promise<void> {
  await apiDirect<unknown>(`${BASE}/alerts/rules/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/** 一次性把本地规则推上后端。后端已迁过时是 no-op,所以可安全重试。 */
export async function seedAlertRules(rules: AlertRule[]): Promise<AlertRulesResult> {
  return await apiDirect<AlertRulesResult>(`${BASE}/alerts/rules/seed`, {
    method: "POST",
    body: JSON.stringify({ rules }),
  });
}

// ---- 通知渠道(渠道下拉数据源) ----
export interface NotificationChannel {
  id: string | number;
  name?: string;
  label?: string;
  type?: string;
  event_types?: string[];
  enabled?: boolean;
  [k: string]: unknown;
}

export const DEMO_CHANNELS: NotificationChannel[] = [
  { id: "telegram", name: "Telegram", event_types: ["price", "indicator", "large_trade", "voice"] },
  { id: "discord", name: "Discord", event_types: ["price", "indicator"] },
  { id: "email", name: "Email", event_types: ["price", "indicator"] },
  { id: "web", name: "Web 站内", event_types: ["price", "indicator", "large_trade", "voice"] },
];

/** 渠道列表(直连;后端不可用/空 → 空数组,调用方回退 DEMO_CHANNELS) */
export async function getNotificationChannels(): Promise<NotificationChannel[]> {
  try {
    const data = await apiDirect<unknown>("/api/v1/notifications/channels");
    if (Array.isArray(data)) return data as NotificationChannel[];
    if (data && typeof data === "object") {
      const c = (data as { channels?: unknown }).channels;
      if (Array.isArray(c)) return c as NotificationChannel[];
    }
    return [];
  } catch {
    return [];
  }
}
