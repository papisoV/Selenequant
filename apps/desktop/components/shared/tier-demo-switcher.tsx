"use client";

import { DEMO_TIER_OPTIONS, TIER_LABELS, type UserTier } from "@/lib/tiers";
import { useTierStore } from "@/lib/store/tier";
import { useUiText } from "@/components/i18n/use-ui-text"

// 演示层级切换下拉(free/pro/vip),开发/演示时手动换墙
export function TierDemoSwitcher() {
  const tr = useUiText()

  const tier = useTierStore((s) => s.tier);
  const setTier = useTierStore((s) => s.setTier);
  return (
    <label className="flex items-center gap-1.5 text-[10px] text-ink-3">
      {tr("层级")}
      <select
        aria-label={tr("演示层级切换")}
        value={tier}
        onChange={(e) => setTier(e.target.value as UserTier)}
        className="rounded border border-border-soft bg-surface-2 px-1.5 py-0.5 text-[10px] text-foreground"
      >
        {DEMO_TIER_OPTIONS.map((t) => (
          <option key={t} value={t}>
            {TIER_LABELS[t]}
          </option>
        ))}
      </select>
    </label>
  );
}
