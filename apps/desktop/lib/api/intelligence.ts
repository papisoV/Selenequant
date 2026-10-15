"use client";

import { api, apiDirect } from "@/lib/api-client";

const BASE = "/api/v1/intelligence";

export interface AlertRule {
  id: string;
  name: string;
  type: string;
  threshold: Record<string, unknown>;
  enabled: boolean;
  channel_id?: string | number;
  description?: string;
  [k: string]: unknown;
}

export interface AlertRulesResult {
  rules: AlertRule[];
  version: string;
  [k: string]: unknown;
}

export interface AlertRuleDraft {
  name: string;
  type: string;
  threshold: Record<string, unknown>;
  enabled: boolean;
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
  { id: "telegram", name: "Telegram", event_types: ["price", "indicator", "onchain", "large_trade", "voice"] },
  { id: "discord", name: "Discord", event_types: ["price", "indicator"] },
  { id: "email", name: "Email", event_types: ["price", "indicator", "onchain"] },
  { id: "web", name: "Web 站内", event_types: ["price", "indicator", "onchain", "large_trade", "voice"] },
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
