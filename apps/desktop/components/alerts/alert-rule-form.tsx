"use client";

import { useEffect, useState } from "react";
import {
  DEMO_CHANNELS,
  getNotificationChannels,
  type AlertRule,
  type AlertRuleDraft,
  type NotificationChannel,
} from "@/lib/api/intelligence";
import { useUiText } from "@/components/i18n/use-ui-text";

export const ALERT_TYPES = ["price", "indicator", "large_trade", "voice"] as const;
export type AlertType = (typeof ALERT_TYPES)[number];

const TYPE_LABEL: Record<string, string> = {
  price: "价格",
  indicator: "指标信号",
  large_trade: "大单",
  voice: "语音",
};

function defaultValueFor(type: string): Record<string, unknown> {
  switch (type) {
    case "price":
      return { operator: "gt", value: 0 };
    case "indicator":
      return { indicator: "RSI", value: 0 };
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
  min_notional_usd: "最小名义金额(USD)",
  keyword: "关键词",
};

// 与后端 alert_rule_schema.TYPES_REQUIRING_SYMBOL 保持一致:large_trade 匹
// 配的是检测器报出的所有标的,voice 是新闻全网,两者都不针对单一标的。
const TYPES_REQUIRING_SYMBOL: ReadonlySet<string> = new Set(["price", "indicator"]);

export function AlertRuleForm({
  initial,
  onSubmit,
  onCancel,
}: {
  initial?: AlertRule;
  onSubmit: (draft: AlertRuleDraft) => void;
  onCancel?: () => void;
}) {
  const tr = useUiText();
  const [name, setName] = useState(initial?.name ?? "");
  const [type, setType] = useState<string>(initial?.type ?? "price");
  const [threshold, setThreshold] = useState<Record<string, unknown>>(
    initial?.threshold ?? defaultValueFor(initial?.type ?? "price"),
  );
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [symbol, setSymbol] = useState(initial?.symbol ?? "");
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
    if (TYPES_REQUIRING_SYMBOL.has(type) && !symbol.trim()) next.symbol = "标的必填";
    const key = primaryField(type);
    const v = threshold[key];
    if (type === "voice") {
      if (typeof v !== "string" || !v.trim()) next.threshold = "关键词必填";
    } else if (typeof v !== "number" || Number.isNaN(v)) {
      next.threshold = "阈值必须是数字";
    }
    setErrors(next);
    if (Object.keys(next).length) return;
    // 不需要标的的类型不带 symbol 出去:后端会把它存成空串,而一条「大单 ≥5万」
    // 的规则挂着个 BTC 会让人以为它只管 BTC。
    onSubmit({
      name: name.trim(),
      type,
      threshold,
      enabled,
      symbol: TYPES_REQUIRING_SYMBOL.has(type) ? symbol.trim() : undefined,
      channel_id: channelId,
    });
  };

  const selChannel = channels.find((c) => String(c.id) === String(channelId)) ?? channels[0];

  const renderTypeFields = () => {
    switch (type) {
      case "price":
        return (
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-[10px] text-ink-3">
              {tr("条件")}
              <select
                value={String(threshold.operator ?? "gt")}
                onChange={(e) => setThreshold((p) => ({ ...p, operator: e.target.value }))}
                className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
              >
                <option value="gt">{tr("突破(≥)")}</option>
                <option value="lt">{tr("跌破(≤)")}</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[10px] text-ink-3">
              {tr(THRESHOLD_LABEL[primaryField(type)])}
              <input
                aria-label={tr("价格阈值")}
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
              {tr("指标")}
              <select
                value={String(threshold.indicator ?? "RSI")}
                onChange={(e) => setThreshold((p) => ({ ...p, indicator: e.target.value }))}
                className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
              >
                <option value="RSI">RSI</option>
                <option value="MACD">MACD</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[10px] text-ink-3">
              {tr(THRESHOLD_LABEL[primaryField(type)])}
              <input
                aria-label={tr("阈值")}
                type="number"
                value={String(threshold.value ?? "")}
                onChange={(e) => setNum("value", e.target.value)}
                className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
              />
            </label>
          </div>
        );
      case "large_trade":
        return (
          <label className="flex flex-col gap-1 text-[10px] text-ink-3">
            {tr(THRESHOLD_LABEL[primaryField(type)])}
            <input
              aria-label={tr("最小名义金额(USD)")}
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
            {tr(THRESHOLD_LABEL[primaryField(type)])}
            <input
              aria-label={tr("关键词")}
              type="text"
              value={String(threshold.keyword ?? "")}
              onChange={(e) => setThreshold((p) => ({ ...p, keyword: e.target.value }))}
              placeholder={tr("如: 爆仓 / 巨额转账")}
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
        {tr("规则名称")}
        <input
          aria-label={tr("规则名称")}
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={tr("如: BTC 突破 10 万")}
          className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
        />
        {errors.name && <span className="text-[9px] text-destructive">{tr(errors.name)}</span>}
      </label>

      <label className="flex flex-col gap-1 text-[10px] text-ink-3">
        {tr("规则类型")}
        <select
          aria-label={tr("规则类型")}
          value={type}
          onChange={(e) => switchType(e.target.value)}
          className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
        >
          {ALERT_TYPES.map((t) => (
            <option key={t} value={t}>
              {tr(TYPE_LABEL[t])}
            </option>
          ))}
        </select>
      </label>

      {TYPES_REQUIRING_SYMBOL.has(type) && (
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          {tr("监控标的")}
          <input
            aria-label={tr("监控标的")}
            type="text"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder={tr("如: BTC/USDT、600519")}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
          />
          {errors.symbol && (
            <span className="text-[9px] text-destructive">{tr(errors.symbol)}</span>
          )}
        </label>
      )}

      {renderTypeFields()}
      {errors.threshold && (
        <span className="text-[9px] text-destructive">{tr(errors.threshold)}</span>
      )}

      <label className="flex flex-col gap-1 text-[10px] text-ink-3">
        {tr("通知渠道")}
        <select
          aria-label={tr("通知渠道")}
          value={String(channelId)}
          onChange={(e) => setChannelId(e.target.value)}
          className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink"
        >
          {channels.map((c) => (
            <option key={String(c.id)} value={String(c.id)}>
              {tr(c.name ?? c.label ?? String(c.id))}
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
        {tr("启用")}
      </label>

      <div className="flex justify-end gap-2 pt-1">
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded border border-border-soft px-2 py-1 text-[10px] text-ink-3 hover:bg-surface-2"
          >
            {tr("取消")}
          </button>
        )}
        <button
          type="button"
          onClick={submit}
          className="rounded bg-gold px-2 py-1 text-[10px] font-medium text-black transition-opacity hover:opacity-90"
        >
          {tr("保存规则")}
        </button>
      </div>
    </div>
  );
}
