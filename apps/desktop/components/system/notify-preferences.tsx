"use client";

import { useCallback, useMemo, useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { toastError, toastSuccess } from "@/lib/toast";
import {
  updatePreferences,
  type NotifyChannel,
} from "@/lib/api/notifications";
import { V17Card } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"

/** 事件类型中文标签 —— 后端返回英文 snake_case，UI 需可读。 */
const EVENT_LABELS: Record<string, string> = {
  order_filled: "订单成交",
  position_opened: "开仓",
  position_closed: "平仓",
  kill_switch_activated: "熔断触发",
  strategy_signal: "策略信号",
  risk_alert: "风险告警",
  smart_money_alert: "聪明钱告警",
  price_alert: "价格告警",
  alert_rule_hit: "预警规则命中",
};

function eventLabel(t: string): string {
  return EVENT_LABELS[t] ?? t;
}

/**
 * 事件 → 渠道 订阅矩阵。
 * 行 = 事件类型，列 = 渠道；勾选即该事件经该渠道推送。
 */
export function NotifyPreferences() {
  const tr = useUiText()

  const { data: eventTypes, mutate: mutateEvents } = useApi<string[] | null>(
    "/api/v1/notifications/event-types",
  );
  const { data: channels, mutate: mutateChannels } = useApi<
    { channels?: NotifyChannel[] } | null
  >("/api/v1/notifications/channels");
  const { data: prefs, mutate: mutatePrefs } = useApi<
    { preferences?: Record<string, string[]> } | null
  >("/api/v1/notifications/preferences");

  // 本地勾选态（PUT 前先乐观更新，失败回滚）
  const [draft, setDraft] = useState<Record<string, string[]> | null>(null);
  const [saving, setSaving] = useState(false);

  const events = useMemo(() => eventTypes ?? [], [eventTypes]);
  const chans = useMemo(
    () => (channels?.channels ?? []).filter((c) => !c.deleted),
    [channels],
  );
  const effective = useMemo(
    () => draft ?? prefs?.preferences ?? {},
    [draft, prefs],
  );

  const toggle = useCallback((eventType: string, channelId: string) => {
    setDraft((d) => {
      const base = { ...(d ?? {}) };
      const cur = base[eventType] ?? [];
      base[eventType] = cur.includes(channelId)
        ? cur.filter((c) => c !== channelId)
        : [...cur, channelId];
      return base;
    });
  }, []);

  const enabledCount = useMemo(
    () => Object.values(effective).reduce((n, ids) => n + (ids?.length ?? 0), 0),
    [effective],
  );

  const save = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const items = events.map((e) => ({
        event_type: e,
        channel_ids: draft[e] ?? [],
      }));
      await updatePreferences(items);
      toastSuccess(tr("订阅已保存"));
      setDraft(null);
      await Promise.all([mutatePrefs(), mutateChannels(), mutateEvents()]);
    } catch (e) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [draft, events, mutateChannels, mutateEvents, mutatePrefs]);

  if (chans.length === 0) {
    return (
      <V17Card title={tr("事件订阅")}>
        <div className="text-center text-ink-4 text-[11px] py-3">
          <span className="text-ink-3">{tr("先在「通知渠道」添加一个渠道，再回到这里配置订阅。")}</span>
        </div>
      </V17Card>
    );
  }

  return (
    <V17Card
      title={`事件订阅 (${enabledCount} 条绑定)`}
      right={
        <button
          onClick={() => void save()}
          disabled={!draft || saving}
          className="rounded border border-border px-1.5 py-0.5 text-[9px] text-ink-2 hover:bg-muted disabled:opacity-40"
        >
          {saving ? tr("保存中…") : draft ? tr("保存订阅") : tr("已保存")}
        </button>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full text-[10px]">
          <thead>
            <tr className="border-b border-border-soft">
              <th className="py-1 pr-2 text-left font-medium text-ink-3">{tr("事件")}</th>
              {chans.map((c) => (
                <th
                  key={c.id}
                  className="px-1.5 py-1 text-center font-medium text-ink-3"
                  title={c.name}
                >
                  <span className="inline-block max-w-[64px] truncate align-bottom">
                    {c.name}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e} className="border-b border-border-soft/60">
                <td className="py-1 pr-2 text-ink-2">{eventLabel(e)}</td>
                {chans.map((c) => (
                  <td key={c.id} className="px-1.5 py-1 text-center">
                    <input
                      type="checkbox"
                      aria-label={`${eventLabel(e)} → ${c.name}`}
                      checked={(effective[e] ?? []).includes(c.id)}
                      onChange={() => toggle(e, c.id)}
                      className="h-2.5 w-2.5"
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </V17Card>
  );
}
