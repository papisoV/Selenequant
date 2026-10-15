"use client";

import { useEffect, useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import {
  putTieredConfig,
  TIERED_TRAILING_DEFAULTS,
  type TieredTrailingConfig,
} from "@/lib/api/settings";
import { LlmSettingsPanel } from "@/components/system/llm-settings-panel";
import { ExchangeSettingsPanel } from "@/components/system/exchange-settings-panel";
import { NetworkSettingsPanel } from "@/components/system/network-settings-panel";
import { AccountPanel } from "@/components/system/account-panel";

interface SettingsGroup {
  group: string;
  items: [string, string][];
}

const DEBUG_FLAG_KEY = "selenespace-debug";

function DebugToggle() {
  const [on, setOn] = useState(false);
  useEffect(() => {
    try {
      setOn(localStorage.getItem(DEBUG_FLAG_KEY) === "1");
    } catch {
      setOn(false);
    }
  }, []);
  const toggle = () => {
    const next = !on;
    setOn(next);
    try {
      if (next) localStorage.setItem(DEBUG_FLAG_KEY, "1");
      else localStorage.removeItem(DEBUG_FLAG_KEY);
    } catch {
      // ignore
    }
  };
  return (
    <div className="set-row flex justify-between items-center text-[10px] py-1 border-b border-border-soft">
      <span className="set-key text-ink-3">Realtime Debug 浮窗</span>
      <button
        onClick={toggle}
        className={`set-val font-mono px-2 py-0.5 rounded border ${
          on
            ? "border-gold text-gold bg-surface-3"
            : "border-border-soft text-ink-4 bg-surface-2"
        }`}
        title="显示 market/paper/live 三路 WebSocket 连接状态浮窗"
      >
        {on ? "ON" : "OFF"}
      </button>
    </div>
  );
}

interface TieredFieldSpec {
  key: keyof TieredTrailingConfig;
  label: string;
  min: number;
  max: number;
  step: number;
}

const TIERED_FIELDS: TieredFieldSpec[] = [
  { key: "tiered_hard_stop_pct", label: "硬止损比例", min: 0.005, max: 0.1, step: 0.005 },
  { key: "tiered_low_tier_threshold", label: "T1 触发阈值", min: 0.005, max: 0.05, step: 0.0025 },
  { key: "tiered_first_tier_threshold", label: "T2 触发阈值", min: 0.01, max: 0.1, step: 0.005 },
  { key: "tiered_second_tier_threshold", label: "T3 触发阈值", min: 0.02, max: 0.2, step: 0.01 },
  { key: "tiered_low_tier_drop_pct", label: "T1 回撤比例", min: 0.1, max: 0.9, step: 0.05 },
  { key: "tiered_first_tier_drop_pct", label: "T2 回撤比例", min: 0.1, max: 0.7, step: 0.05 },
  { key: "tiered_second_tier_drop_pct", label: "T3 回撤比例", min: 0.05, max: 0.5, step: 0.05 },
  { key: "tiered_min_emit_interval_seconds", label: "最小emit间隔(秒)", min: 5, max: 300, step: 5 },
];

function TieredTrailingForm() {
  const { data } = useApi<{ risk?: Record<string, number> } | null>("/api/v1/settings");
  const [local, setLocal] = useState<TieredTrailingConfig>(TIERED_TRAILING_DEFAULTS);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  useEffect(() => {
    const risk = data?.risk;
    if (!risk) return;
    setLocal({
      tiered_hard_stop_pct: Number(risk.tiered_hard_stop_pct ?? 0.02),
      tiered_low_tier_threshold: Number(risk.tiered_low_tier_threshold ?? 0.015),
      tiered_first_tier_threshold: Number(risk.tiered_first_tier_threshold ?? 0.03),
      tiered_second_tier_threshold: Number(risk.tiered_second_tier_threshold ?? 0.06),
      tiered_low_tier_drop_pct: Number(risk.tiered_low_tier_drop_pct ?? 0.5),
      tiered_first_tier_drop_pct: Number(risk.tiered_first_tier_drop_pct ?? 0.3),
      tiered_second_tier_drop_pct: Number(risk.tiered_second_tier_drop_pct ?? 0.2),
      tiered_min_emit_interval_seconds: Number(risk.tiered_min_emit_interval_seconds ?? 30),
    });
  }, [data]);

  const handleFieldChange = (key: keyof TieredTrailingConfig, next: number) => {
    setLocal((prev) => ({ ...prev, [key]: next }));
  };

  const handleSave = async () => {
    setSaving(true);
    setSavedMsg(null);
    try {
      await putTieredConfig(local);
      setSavedMsg("已保存");
    } catch {
      setSavedMsg("保存失败");
    } finally {
      setSaving(false);
      setTimeout(() => setSavedMsg(null), 2000);
    }
  };

  const handleReset = () => {
    setLocal(TIERED_TRAILING_DEFAULTS);
  };

  return (
    <div className="set-group rounded border border-border-soft bg-surface-2 p-2">
      <div className="set-group-title text-[10px] font-medium text-gold mb-1.5">
        四档止盈 (Tiered Trailing)
      </div>
      <div className="space-y-1.5">
        {TIERED_FIELDS.map((spec) => {
          const value = local[spec.key];
          return (
            <div key={spec.key} className="space-y-0.5">
              <div className="flex justify-between items-center text-[9px]">
                <span className="text-ink-3">{spec.label}</span>
                <span className="text-ink font-mono">{value}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <input
                  type="range"
                  min={spec.min}
                  max={spec.max}
                  step={spec.step}
                  value={value}
                  onChange={(e) => handleFieldChange(spec.key, parseFloat(e.target.value))}
                  className="h-1 flex-1 accent-gold"
                />
                <input
                  type="number"
                  min={spec.min}
                  max={spec.max}
                  step={spec.step}
                  value={value}
                  onChange={(e) => handleFieldChange(spec.key, parseFloat(e.target.value) || 0)}
                  className="h-5 w-16 rounded border border-border-soft bg-surface-3 px-1 text-center font-mono text-[9px]"
                />
              </div>
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-1.5 mt-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="flex-1 rounded border border-gold bg-surface-3 px-2 py-1 text-[9px] text-gold disabled:opacity-50"
        >
          {saving ? "保存中..." : "保存"}
        </button>
        <button
          type="button"
          onClick={handleReset}
          className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[9px] text-ink-4"
        >
          重置默认
        </button>
        {savedMsg && (
          <span className="text-[9px] text-ink-3">{savedMsg}</span>
        )}
      </div>
    </div>
  );
}

// 设计稿 L4820 renderPageSet — 设置
// 后端 /api/v1/settings 返回结构未知；空时展示空状态
export function SetPage() {
  const { data } = useApi<Record<string, unknown> | null>("/api/v1/settings");

  const groups: SettingsGroup[] = (() => {
    if (!data || typeof data !== "object") return [];
    // 后端返回形态未知；尝试按对象 key 分组
    // llm 段由 <LlmSettingsPanel /> 专门渲染,exchange 段由 <ExchangeSettingsPanel />
    // 专门渲染(否则 api_key_hint 等字段会显示成裸英文 key,用户读不懂)
    const SKIP_GROUPS = new Set(["llm", "exchange", "network"]);
    return Object.entries(data)
      .filter(([group]) => !SKIP_GROUPS.has(group))
      .map(([group, val]) => ({
        group,
        items: (val && typeof val === "object")
          ? Object.entries(val as Record<string, unknown>).map(([k, v]) => [k, String(v)] as [string, string])
          : [[group, String(val)] as [string, string]],
      }));
  })();

  return (
    <div
      className="v17-page-grid grid gap-2"
      style={{ gridTemplateColumns: "repeat(2, 1fr)" }}
    >
      {groups.length === 0 && (
        <div className="col-span-2 text-center text-ink-4 text-[11px] py-3">暂无数据</div>
      )}
      {groups.map((g) => (
        <div
          key={g.group}
          className="set-group rounded border border-border-soft bg-surface-2 p-2"
        >
          <div className="set-group-title text-[10px] font-medium text-gold mb-1.5">
            {g.group}
          </div>
          {g.items.map(([k, v]) => (
            <div
              key={k}
              className="set-row flex justify-between items-center text-[10px] py-1 border-b border-border-soft"
            >
              <span className="set-key text-ink-3">{k}</span>
              <span className="set-val text-ink font-mono">{v}</span>
            </div>
          ))}
        </div>
      ))}
      <TieredTrailingForm />
      <ExchangeSettingsPanel />
      <LlmSettingsPanel />
      <NetworkSettingsPanel />
      {/* K46(判定表 r5):注销状态=账户设置内状态行,me-deletion-status 并入 AccountPanel(已含注销请求/状态/取消) */}
      <div className="col-span-2">
        <AccountPanel />
      </div>
      <div className="set-group rounded border border-border-soft bg-surface-2 p-2">
        <div className="set-group-title text-[10px] font-medium text-gold mb-1.5">
          调试
        </div>
        <DebugToggle />
      </div>
    </div>
  );
}
