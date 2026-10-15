"use client";

import { useEffect, useState } from "react";
import {
  DEMO_CHANNELS,
  getNotificationChannels,
  type AlertRule,
  type AlertRuleDraft,
  type NotificationChannel,
} from "@/lib/api/intelligence";

export const ALERT_TYPES = ["price", "indicator", "onchain", "large_trade", "voice"] as const;
export type AlertType = (typeof ALERT_TYPES)[number];

const TYPE_LABEL: Record<string, string> = {
  price: "价格",
  indicator: "指标信号",
  onchain: "链上",
  large_trade: "大单",
  voice: "语音",
};

function defaultValueFor(type: string): Record<string, unknown> {
  switch (type) {
    case "price":
      return { operator: "gt", value: 0 };
    case "indicator":
      return { indicator: "RSI", value: 0 };
    case "onchain":
      return { metric: "active_addresses", min_value: 0 };
    case "large_trade":
      return { min_notional_usd: 0 };
    case "voice":
      return { keyword: "" };
    default:
      return {};
  }
}

// 当前 type 的主阈值字段(校验/渲染都用它)
function primaryField(type: string): string {
  switch (type) {
    case "price":
      return "value";
    case "indicator":
      return "value";
    case "onchain":
      return "min_value";
    case "large_trade":
      return "min_notional_usd";
    case "voice":
      return "keyword";
    default:
      return "value";
  }
}

const THRESHOLD_LABEL: Record<string, string> = {
  value: "价格阈值",
  min_value: "最小值",
  min_notional_usd: "最小名义金额(USD)",
  keyword: "关键词",
};

export function AlertRuleForm({
  initial,
  onSubmit,
  onCancel,
}: {
  initial?: AlertRule;
  onSubmit: (draft: AlertRuleDraft) => void;
  onCancel?: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [type, setType] = useState<string>(initial?.type ?? "price");
  const [threshold, setThreshold] = useState<Record<string, unknown>>(
    initial?.threshold ?? defaultValueFor(initial?.type ?? "price"),
  );
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [channelId, setChannelId] = useState<string | number>(initial?.channel_id ?? "telegram");
  const [channels, setChannels] = useState<NotificationChannel[]>(DEMO_CHANNELS);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    getNotificationChannels().then((list) => {
      if (list.length > 0) setChannels(list);
    });
  }, []);

  const setNum = (key: string, raw: string) => {
    const n = Number(raw);
    setThreshold((prev) => ({ ...prev, [key]: raw.trim() === "" || Number.isNaN(n) ? raw : n }));
  };

  const switchType = (next: string) => {
    setType(next);
    setThreshold(defaultValueFor(next));
  };

  const submit = () => {
    const next: Record<string, string> = {};
    if (!name.trim()) next.name = "名称必填";
    const key = primaryField(type);
    const v = threshold[key];
    if (type === "voice") {
      if (typeof v !== "string" || !v.trim()) next.threshold = "关键词必填";
    } else if (typeof v !== "number" || Number.isNaN(v)) {
      next.threshold = "阈值必须是数字";
    }
    setErrors(next);
    if (Object.keys(next).length) return;
    onSubmit({ name: name.trim(), type, threshold, enabled, channel_id: channelId });
  };

  const selChannel = channels.find((c) => String(c.id) === String(channelId)) ?? channels[0];

  const renderTypeFields = () => {
    switch (type) {
      case "price":
        return (
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-[10px] text-ink-3">
              条件
              <select
                value={String(threshold.operator ?? "gt")}
                onChange={(e) => setThreshold((p) => ({ ...p, operator: e.target.value }))}
                className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
              >
                <option value="gt">突破(≥)</option>
                <option value="lt">跌破(≤)</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[10px] text-ink-3">
              {THRESHOLD_LABEL[primaryField(type)]}
              <input
                aria-label="价格阈值"
                type="number"
                value={String(threshold.value ?? "")}
                onChange={(e) => setNum("value", e.target.value)}
                className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
              />
            </label>
          </div>
        );
      case "indicator":
        return (
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-[10px] text-ink-3">
              指标
              <select
                value={String(threshold.indicator ?? "RSI")}
                onChange={(e) => setThreshold((p) => ({ ...p, indicator: e.target.value }))}
                className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
              >
                <option value="RSI">RSI</option>
                <option value="MACD">MACD</option>
                <option value="SOPR">SOPR</option>
                <option value="NVT">NVT</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[10px] text-ink-3">
              {THRESHOLD_LABEL[primaryField(type)]}
              <input
                aria-label="阈值"
                type="number"
                value={String(threshold.value ?? "")}
                onChange={(e) => setNum("value", e.target.value)}
                className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
              />
            </label>
          </div>
        );
      case "onchain":
        return (
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-[10px] text-ink-3">
              指标
              <select
                value={String(threshold.metric ?? "active_addresses")}
                onChange={(e) => setThreshold((p) => ({ ...p, metric: e.target.value }))}
                className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
              >
                <option value="active_addresses">活跃地址</option>
                <option value="nvt_ratio">NVT 比率</option>
                <option value="sopr">SOPR</option>
                <option value="exchange_flow">交易所流向</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[10px] text-ink-3">
              {THRESHOLD_LABEL[primaryField(type)]}
              <input
                aria-label="最小值"
                type="number"
                value={String(threshold.min_value ?? "")}
                onChange={(e) => setNum("min_value", e.target.value)}
                className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
              />
            </label>
          </div>
        );
      case "large_trade":
        return (
          <label className="flex flex-col gap-1 text-[10px] text-ink-3">
            {THRESHOLD_LABEL[primaryField(type)]}
            <input
              aria-label="最小名义金额(USD)"
              type="number"
              value={String(threshold.min_notional_usd ?? "")}
              onChange={(e) => setNum("min_notional_usd", e.target.value)}
              className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
            />
          </label>
        );
      case "voice":
        return (
          <label className="flex flex-col gap-1 text-[10px] text-ink-3">
            {THRESHOLD_LABEL[primaryField(type)]}
            <input
              aria-label="关键词"
              type="text"
              value={String(threshold.keyword ?? "")}
              onChange={(e) => setThreshold((p) => ({ ...p, keyword: e.target.value }))}
              placeholder="如: 爆仓 / 巨额转账"
              className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
            />
          </label>
        );
      default:
        return null;
    }
  };

  return (
    <div className="alert-rule-form flex flex-col gap-2">
      <label className="flex flex-col gap-1 text-[10px] text-ink-3">
        规则名称
        <input
          aria-label="规则名称"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="如: BTC 突破 10 万"
          className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
        />
        {errors.name && <span className="text-[9px] text-destructive">{errors.name}</span>}
      </label>

      <label className="flex flex-col gap-1 text-[10px] text-ink-3">
        规则类型
        <select
          aria-label="规则类型"
          value={type}
          onChange={(e) => switchType(e.target.value)}
          className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
        >
          {ALERT_TYPES.map((t) => (
            <option key={t} value={t}>
              {TYPE_LABEL[t]}
            </option>
          ))}
        </select>
      </label>

      {renderTypeFields()}
      {errors.threshold && <span className="text-[9px] text-destructive">{errors.threshold}</span>}

      <label className="flex flex-col gap-1 text-[10px] text-ink-3">
        通知渠道
        <select
          aria-label="通知渠道"
          value={String(channelId)}
          onChange={(e) => setChannelId(e.target.value)}
          className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
        >
          {channels.map((c) => (
            <option key={String(c.id)} value={String(c.id)}>
              {c.name ?? c.label ?? String(c.id)}
            </option>
          ))}
        </select>
      </label>

      {selChannel?.event_types && selChannel.event_types.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          {selChannel.event_types.map((et) => (
            <span key={et} className="rounded bg-surface-2 px-1 py-0.5 text-[9px] text-ink-3">
              {et}
            </span>
          ))}
        </div>
      )}

      <label className="flex items-center gap-2 text-[10px] text-ink-3">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="h-3 w-3 accent-pos"
        />
        启用
      </label>

      <div className="flex justify-end gap-2 pt-1">
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded border border-border-soft px-2 py-1 text-[10px] text-ink-3 hover:bg-surface-2"
          >
            取消
          </button>
        )}
        <button
          type="button"
          onClick={submit}
          className="rounded bg-gold px-2 py-1 text-[10px] font-medium text-black transition-opacity hover:opacity-90"
        >
          保存规则
        </button>
      </div>
    </div>
  );
}
