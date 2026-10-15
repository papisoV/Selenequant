"use client";

import { useCallback, useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { toastError, toastSuccess } from "@/lib/toast";
import {
  CHANNEL_TYPES,
  channelTypeSpec,
  createChannel,
  deleteChannel,
  DIALECT_LABELS,
  detectWebhookDialect,
  SIGNED_DIALECTS,
  testChannel,
  updateChannel,
  type NotifyChannel,
} from "@/lib/api/notifications";
import { StatBlock, V17Card } from "@/components/shared/page-filter-bar";
import { NotifyPreferences } from "./notify-preferences";

/** 新建表单草稿：字段由所选渠道类型的 spec 驱动，不再写死具体字段。 */
interface DraftState {
  name: string;
  type: string;
  fields: Record<string, string>;
}

const EMPTY_DRAFT: DraftState = { name: "", type: "webhook", fields: {} };

export function NotificationsPage() {
  const { data: rawChannels, mutate: mutateChannels } = useApi<{
    channels?: NotifyChannel[];
  } | null>("/api/v1/notifications/channels");
  const { data: prefsData } = useApi<{
    preferences?: Record<string, string[]>;
  } | null>("/api/v1/notifications/preferences");

  const [draft, setDraft] = useState<DraftState>(EMPTY_DRAFT);
  const [adding, setAdding] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});

  const channels = (rawChannels?.channels ?? []).filter((c) => !c.deleted);
  const bindingCount = Object.values(prefsData?.preferences ?? {}).reduce(
    (n, ids) => n + (ids?.length ?? 0),
    0,
  );

  const spec = channelTypeSpec(draft.type);
  // 方言即时识别 —— 后端 detect_webhook_dialect 的前端镜像，仅用于提示
  const dialect =
    draft.type === "webhook" ? detectWebhookDialect(draft.fields.url ?? "") : null;
  const needsSecret = dialect ? SIGNED_DIALECTS.includes(dialect) : false;

  const setField = useCallback((key: string, value: string) => {
    setDraft((s) => ({ ...s, fields: { ...s.fields, [key]: value } }));
  }, []);

  const add = useCallback(async () => {
    const name = draft.name.trim();
    if (!name) {
      toastError("请填写渠道名称");
      return;
    }
    const active = channelTypeSpec(draft.type);
    if (!active) {
      toastError("未知渠道类型");
      return;
    }
    const config: Record<string, string> = {};
    for (const f of active.fields) {
      const v = (draft.fields[f.key] ?? "").trim();
      if (!v && !f.optional) {
        toastError(`请填写 ${f.label}`);
        return;
      }
      if (v) config[f.key] = v;
    }
    setAdding(true);
    try {
      await createChannel({ type: draft.type, name, config });
      toastSuccess("渠道已创建");
      setDraft(EMPTY_DRAFT);
      await mutateChannels();
    } catch (e) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setAdding(false);
    }
  }, [draft, mutateChannels]);

  const toggleEnabled = useCallback(
    async (ch: NotifyChannel) => {
      try {
        await updateChannel(ch.id, { enabled: !ch.enabled });
        await mutateChannels();
      } catch (e) {
        toastError(e instanceof Error ? e.message : String(e));
      }
    },
    [mutateChannels],
  );

  const remove = useCallback(
    async (ch: NotifyChannel) => {
      try {
        await deleteChannel(ch.id, "hard");
        toastSuccess("渠道已删除");
        await mutateChannels();
      } catch (e) {
        toastError(e instanceof Error ? e.message : String(e));
      }
    },
    [mutateChannels],
  );

  const test = useCallback(async (ch: NotifyChannel) => {
    setTestingId(ch.id);
    setTestResult((m) => {
      const next = { ...m };
      delete next[ch.id];
      return next;
    });
    try {
      const r = await testChannel(ch.id);
      setTestResult((m) => ({ ...m, [ch.id]: r.message }));
      if (r.tested) toastSuccess("测试发送成功");
      else toastError(r.message);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setTestResult((m) => ({ ...m, [ch.id]: msg }));
      toastError(msg);
    } finally {
      setTestingId(null);
    }
  }, []);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="通知概览">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label="渠道数" value={String(channels.length)} />
          <StatBlock
            label="已启用"
            value={String(channels.filter((c) => c.enabled).length)}
          />
          <StatBlock label="订阅绑定" value={String(bindingCount)} cls="text-gold" />
        </div>
      </V17Card>

      <V17Card title="通知渠道">
        <div className="flex flex-col gap-2">
          {channels.length === 0 ? (
            <div className="text-center text-ink-4 text-[11px] py-3">
              暂无渠道。在下方选择类型添加（飞书/钉钉/企微/Slack/Discord 走
              Webhook 自动识别，另有 ntfy/Server酱/PushPlus/Gotify/Pushover/Telegram），
              即可接收策略信号与风险告警。
            </div>
          ) : (
            channels.map((ch) => (
              <div
                key={ch.id}
                className="flex items-center gap-2 border-b border-border-soft py-1 text-[10px]"
              >
                <span className="font-mono text-[9px] text-ink-4">{ch.type}</span>
                <span className="flex-1 truncate text-ink-2">{ch.name}</span>
                <span
                  className={ch.enabled ? "text-pos" : "text-ink-4"}
                  title={ch.enabled ? "已启用" : "已停用"}
                >
                  {ch.enabled ? "启用" : "停用"}
                </span>
                <button
                  onClick={() => void toggleEnabled(ch)}
                  className="rounded border border-border px-1.5 py-0.5 text-[9px] text-ink-3 hover:bg-muted"
                >
                  {ch.enabled ? "停用" : "启用"}
                </button>
                <button
                  onClick={() => void test(ch)}
                  disabled={testingId === ch.id || !ch.enabled}
                  className="rounded border border-border px-1.5 py-0.5 text-[9px] text-sky-400 hover:bg-muted disabled:opacity-40"
                  title="真发一条测试消息，验证 URL/密钥是否可用"
                >
                  {testingId === ch.id ? "发送中…" : "测试发送"}
                </button>
                <button
                  onClick={() => void remove(ch)}
                  className="rounded border border-border px-1.5 py-0.5 text-[9px] text-neg hover:bg-muted"
                >
                  删除
                </button>
                {testResult[ch.id] ? (
                  <span className="w-full text-[9px] text-ink-4">
                    {testResult[ch.id]}
                  </span>
                ) : null}
              </div>
            ))
          )}

          {/* 新增渠道 */}
          <div className="mt-1 flex flex-col gap-1.5 rounded border border-border-soft bg-surface-2 p-2">
            <div className="flex flex-wrap items-end gap-1.5">
              <label className="flex flex-col gap-0.5">
                <span className="text-[9px] text-ink-4">名称</span>
                <input
                  value={draft.name}
                  onChange={(e) => setDraft((s) => ({ ...s, name: e.target.value }))}
                  placeholder="如 风控飞书群"
                  className="h-7 w-32 rounded border border-border bg-muted/30 px-1.5 text-[10px]"
                />
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-[9px] text-ink-4">类型</span>
                <select
                  value={draft.type}
                  onChange={(e) =>
                    setDraft((s) => ({
                      ...s,
                      type: e.target.value,
                      fields: {},
                    }))
                  }
                  className="h-7 rounded border border-border bg-muted/30 px-1.5 text-[10px]"
                >
                  {CHANNEL_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </label>
              {spec?.fields.map((f) => (
                <label key={f.key} className="flex flex-1 flex-col gap-0.5">
                  <span className="text-[9px] text-ink-4">
                    {f.label}
                    {f.optional ? "（可选）" : ""}
                  </span>
                  <input
                    type={f.secret ? "password" : "text"}
                    value={draft.fields[f.key] ?? ""}
                    onChange={(e) => setField(f.key, e.target.value)}
                    placeholder={f.placeholder ?? ""}
                    className="h-7 w-full min-w-[160px] rounded border border-border bg-muted/30 px-1.5 font-mono text-[10px]"
                  />
                </label>
              ))}
              <button
                onClick={() => void add()}
                disabled={adding}
                className="h-7 rounded bg-sky-500/15 px-2 text-[10px] text-sky-400 hover:bg-sky-500/25 disabled:opacity-40"
              >
                {adding ? "添加中…" : "添加渠道"}
              </button>
            </div>
            {dialect ? (
              <div className="text-[9px] text-ink-3">
                自动识别为
                <span className="mx-1 text-gold">{DIALECT_LABELS[dialect]}</span>
                {needsSecret
                  ? "— 该平台要求签名，请填写密钥"
                  : dialect === "wecom" || dialect === "slack"
                    ? "— 密钥已含在 URL 中，无需额外填写"
                    : "— 将以通用 JSON 格式发送"}
              </div>
            ) : null}
            {spec?.fields.some((f) => f.hint) ? (
              <div className="text-[9px] text-ink-4">
                {spec.fields
                  .filter((f) => f.hint)
                  .map((f) => `${f.label}：${f.hint}`)
                  .join("；")}
              </div>
            ) : null}
          </div>
        </div>
      </V17Card>

      <NotifyPreferences />
    </div>
  );
}
