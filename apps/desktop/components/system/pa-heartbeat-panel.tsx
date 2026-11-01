"use client";

import { useState } from "react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { putPaHeartbeatSettings, type PaHeartbeatSettings } from "@/lib/api/settings";
import { useUiText } from "@/components/i18n/use-ui-text";

// GET /api/v1/settings → data.pa_heartbeat 段(settings_service 注入)。
// 这是无人值守 PA 诊断的总开关 + 节奏:后端调度器常驻,每 tick 重读本段,
// 所以保存后下一分钟(最多)生效,不需要重启 —— UI 不写"重启生效"。
// 关 = 零 LLM、零诊断;开启即意味着系统会在无人盯着时自己调模型、写审计表。
const INTERVALS = ["15m", "30m", "1h", "2h", "4h", "1d"] as const;

interface SettingsData {
  pa_heartbeat?: Partial<PaHeartbeatSettings>;
}

export function PaHeartbeatPanel() {
  const tr = useUiText();
  const { useApi } = useApiPage();
  const { data, mutate } = useApi<SettingsData | null>("/api/v1/settings");
  const saved: PaHeartbeatSettings = {
    enabled: data?.pa_heartbeat?.enabled === true,
    daily_max: Number(data?.pa_heartbeat?.daily_max ?? 20),
    interval: String(data?.pa_heartbeat?.interval ?? "1h"),
    hour_utc: Number(data?.pa_heartbeat?.hour_utc ?? 0),
  };
  const ready = data !== null;

  const [form, setForm] = useState<PaHeartbeatSettings>(saved);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // 后端数据到达/更新时同步表单 —— render-adjust 模式(同 NetworkSettingsPanel)
  const [prevSaved, setPrevSaved] = useState<string>("");
  const savedKey = JSON.stringify(saved);
  if (savedKey !== prevSaved) {
    setPrevSaved(savedKey);
    setForm(saved);
  }

  const set = <K extends keyof PaHeartbeatSettings>(key: K, value: PaHeartbeatSettings[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const handleSave = async () => {
    if (!ready) {
      setMsg("数据加载中");
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      await putPaHeartbeatSettings({
        enabled: form.enabled,
        daily_max: Math.max(0, Math.floor(Number(form.daily_max) || 0)),
        interval: form.interval,
        hour_utc: Math.min(23, Math.max(0, Math.floor(Number(form.hour_utc) || 0))),
      });
      setMsg(form.enabled ? "已保存,下一 tick 起生效" : "已保存,已停");
      mutate();
    } catch {
      setMsg("保存失败");
    } finally {
      setSaving(false);
      setTimeout(() => setMsg(null), 3000);
    }
  };

  return (
    <div className="set-group rounded border border-border-soft bg-surface-2 p-2">
      <h2 className="set-group-title text-[12px] font-medium text-gold mb-1.5">
        {tr("PA 心跳(无人值守诊断)")}
      </h2>
      <div className="space-y-1.5">
        <div className="set-row flex items-center justify-between gap-2">
          <span className="set-key text-ink-3 text-[11px]">{tr("启用")}</span>
          <button
            type="button"
            onClick={() => set("enabled", !form.enabled)}
            aria-pressed={form.enabled}
            className={`set-btn font-mono px-2 rounded border text-[11px] ${
              form.enabled
                ? "border-gold text-gold bg-surface-3"
                : "border-border-soft text-ink-4 bg-surface-2"
            }`}
          >
            {form.enabled ? "ON" : "OFF"}
          </button>
        </div>
        <div className="set-row flex items-center gap-2">
          <label htmlFor="pa-hb-daily-max" className="set-key text-ink-3 text-[11px] shrink-0">
            {tr("每日上限")}
          </label>
          <input
            id="pa-hb-daily-max"
            type="number"
            min={0}
            step={1}
            value={form.daily_max}
            onChange={(e) => set("daily_max", Number(e.target.value))}
            className="set-input w-16 text-center"
          />
          <span className="text-ink-4 text-[11px]">{tr("0 = 不设上限")}</span>
        </div>
        <div className="set-row flex items-center gap-2">
          <label htmlFor="pa-hb-interval" className="set-key text-ink-3 text-[11px] shrink-0">
            {tr("诊断周期")}
          </label>
          <select
            id="pa-hb-interval"
            value={form.interval}
            onChange={(e) => set("interval", e.target.value)}
            className="set-input flex-1"
          >
            {INTERVALS.map((iv) => (
              <option key={iv} value={iv}>
                {iv}
              </option>
            ))}
          </select>
        </div>
        <div className="set-row flex items-center gap-2">
          <label htmlFor="pa-hb-hour" className="set-key text-ink-3 text-[11px] shrink-0">
            {tr("触发小时 (UTC)")}
          </label>
          <input
            id="pa-hb-hour"
            type="number"
            min={0}
            max={23}
            step={1}
            value={form.hour_utc}
            onChange={(e) => set("hour_utc", Number(e.target.value))}
            className="set-input w-16 text-center"
          />
        </div>
        <div className="text-ink-4 text-[11px] leading-snug">
          {tr(
            "开启后每个 UTC 日到达触发小时即对自选列表跑一轮两阶段诊断(每标的两次 LLM 调用,真花钱)。诊断只产出记录与任务建议,不会自动下单。保存后下一分钟内生效,无需重启后端。",
          )}
        </div>
      </div>
      <div className="flex items-center gap-1.5 mt-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || !ready}
          className="set-btn flex-1 rounded border border-gold bg-surface-3 px-2 text-[11px] text-gold disabled:opacity-50"
        >
          {saving ? tr("保存中...") : tr("保存")}
        </button>
        {msg && <span className="text-[11px] text-ink-3">{tr(msg)}</span>}
      </div>
    </div>
  );
}
