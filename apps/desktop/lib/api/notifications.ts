"use client";

import { api, apiDelete, apiPatch, apiPost, apiPut } from "@/lib/api-client";

const NOTIFY_BASE = "/api/v1/notifications";

/** 后端 list_channels 会对 url/bot_token/secret 打码为 "***"。 */
export interface NotifyChannel {
  id: string;
  type: string;
  name: string;
  config: Record<string, string>;
  enabled: boolean;
  created_at?: number;
  deleted?: boolean;
}

export interface ChannelListData {
  channels?: NotifyChannel[];
}

export interface CreateChannelBody {
  type: string;
  name: string;
  config: Record<string, string>;
}

export async function listChannels(): Promise<NotifyChannel[]> {
  const data = await api<ChannelListData>(`${NOTIFY_BASE}/channels`);
  return data.channels ?? [];
}

export async function createChannel(
  body: CreateChannelBody,
): Promise<NotifyChannel> {
  return apiPost<NotifyChannel>(`${NOTIFY_BASE}/channels`, body);
}

export async function updateChannel(
  channelId: string,
  body: { name?: string; config?: Record<string, string>; enabled?: boolean },
): Promise<NotifyChannel> {
  return apiPatch<NotifyChannel>(
    `${NOTIFY_BASE}/channels/${encodeURIComponent(channelId)}`,
    body,
  );
}

export async function deleteChannel(
  channelId: string,
  mode: "soft" | "hard" = "soft",
): Promise<{ deleted: boolean; mode: string }> {
  return apiDelete<{ deleted: boolean; mode: string }>(
    `${NOTIFY_BASE}/channels/${encodeURIComponent(channelId)}?mode=${mode}`,
  );
}

export interface TestChannelResult {
  tested: boolean;
  channel_id: string;
  status: string;
  message: string;
}

export async function testChannel(
  channelId: string,
): Promise<TestChannelResult> {
  return apiPost<TestChannelResult>(
    `${NOTIFY_BASE}/channels/${encodeURIComponent(channelId)}/test`,
  );
}

export interface EventTypesData {
  event_types?: string[];
  total?: number;
}

export async function listEventTypes(): Promise<string[]> {
  const data = await api<EventTypesData>(`${NOTIFY_BASE}/event-types`);
  return data.event_types ?? [];
}

/** event_type -> channel_ids[] */
export type PreferenceMap = Record<string, string[]>;

export interface PreferencesBody {
  items: { event_type: string; channel_ids: string[] }[];
}

export async function getPreferences(): Promise<PreferenceMap> {
  const data = await api<{ preferences?: PreferenceMap }>(
    `${NOTIFY_BASE}/preferences`,
  );
  return data.preferences ?? {};
}

export async function updatePreferences(
  items: PreferencesBody["items"],
): Promise<PreferenceMap> {
  const data = await apiPut<{ preferences?: PreferenceMap }>(
    `${NOTIFY_BASE}/preferences`,
    { items },
  );
  return data.preferences ?? {};
}

// ---------------------------------------------------------------------------
// 方言识别（镜像后端 detect_webhook_dialect — 供 UI 即时提示，非发送路径）
// ---------------------------------------------------------------------------

export type WebhookDialect =
  | "discord"
  | "feishu"
  | "dingtalk"
  | "wecom"
  | "slack"
  | "generic";

export const DIALECT_LABELS: Record<WebhookDialect, string> = {
  discord: "Discord",
  feishu: "飞书",
  dingtalk: "钉钉",
  wecom: "企业微信",
  slack: "Slack",
  generic: "通用 Webhook",
};

/** 需要 secret 才签名的方言（feishu/dingtalk/generic）；wecom/slack 密钥在 URL 内。 */
export const SIGNED_DIALECTS: WebhookDialect[] = [
  "feishu",
  "dingtalk",
  "generic",
];

/** 与后端 notify_channels.detect_webhook_dialect 同规则，纯前端镜像用于即时反馈。 */
export function detectWebhookDialect(url: string): WebhookDialect {
  let parsed: URL | null = null;
  try {
    parsed = new URL(url);
  } catch {
    return "generic";
  }
  const host = parsed.hostname.toLowerCase();
  const raw = url.toLowerCase();
  if (raw.includes("discord.com/api/webhooks")) return "discord";
  if (host === "open.feishu.cn" || host.endsWith("larksuite.com")) return "feishu";
  if (host === "oapi.dingtalk.com") return "dingtalk";
  if (host === "qyapi.weixin.qq.com" || host === "work.weixin.qq.com")
    return "wecom";
  if (host === "hooks.slack.com") return "slack";
  return "generic";
}

// ---------------------------------------------------------------------------
// 渠道类型定义（后端 send_channel_test + pulse 传输层支持的 type 全集）
// ---------------------------------------------------------------------------

/** 渠道类型 + 每个类型需要的字段。字段组由 type 驱动，表单不必写死。 */
export interface ChannelFieldSpec {
  key: string;
  label: string;
  placeholder?: string;
  secret?: boolean; // 渲染为 password
  optional?: boolean;
  hint?: string;
}

export interface ChannelTypeSpec {
  value: string;
  label: string;
  fields: ChannelFieldSpec[];
}

/** 事件 → 渠道 的渠道类型目录。URL 族靠方言自动识别，token 族各有字段。 */
export const CHANNEL_TYPES: ChannelTypeSpec[] = [
  {
    value: "webhook",
    label: "Webhook（飞书/钉钉/企微/Slack/Discord 自动识别）",
    fields: [
      {
        key: "url",
        label: "Webhook URL",
        placeholder: "https://open.feishu.cn/open-apis/bot/v2/hook/...",
      },
      {
        key: "secret",
        label: "签名密钥",
        optional: true,
        secret: true,
        hint: "留空则不签名；飞书/钉钉/通用签名需要",
      },
    ],
  },
  {
    value: "ntfy",
    label: "ntfy",
    fields: [
      {
        key: "url",
        label: "ntfy URL",
        placeholder: "https://ntfy.sh/你的topic",
        hint: "URL 末段就是 topic",
      },
      { key: "token", label: "访问令牌", optional: true, secret: true },
    ],
  },
  {
    value: "serverchan3",
    label: "Server酱³",
    fields: [
      {
        key: "sendkey",
        label: "SendKey",
        placeholder: "SCT...",
        secret: true,
      },
    ],
  },
  {
    value: "pushplus",
    label: "PushPlus",
    fields: [
      { key: "token", label: "Token", secret: true },
      { key: "topic", label: "群组编码", optional: true },
    ],
  },
  {
    value: "gotify",
    label: "Gotify",
    fields: [
      { key: "url", label: "服务地址", placeholder: "https://gotify.example.com" },
      { key: "token", label: "应用令牌", secret: true },
    ],
  },
  {
    value: "pushover",
    label: "Pushover",
    fields: [
      { key: "user_key", label: "User Key", secret: true },
      { key: "token", label: "应用 Token", secret: true },
    ],
  },
  {
    value: "telegram",
    label: "Telegram",
    fields: [
      { key: "bot_token", label: "bot_token", secret: true },
      { key: "chat_id", label: "chat_id" },
    ],
  },
];

export function channelTypeSpec(value: string): ChannelTypeSpec | undefined {
  return CHANNEL_TYPES.find((t) => t.value === value);
}
