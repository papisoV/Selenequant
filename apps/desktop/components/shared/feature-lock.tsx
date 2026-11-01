"use client";

import { TIER_LABELS, TIER_FEATURES, type FeatureKey } from "@/lib/tiers";
import { useUiText } from "@/components/i18n/use-ui-text";
import { useTierStore } from "@/lib/store/tier";

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V7a4 4 0 1 1 8 0v4" />
    </svg>
  );
}

// 功能墙的可视化锁层:锁图标 + 等级徽章 + "查看演示"按钮(一键切到解锁层级)
// lockMode="blur":children 模糊+锁覆盖;="replace":仅渲染锁
export function FeatureLock({
  feature,
  lockMode = "replace",
  children,
}: {
  feature: FeatureKey;
  lockMode?: "blur" | "replace";
  children?: React.ReactNode;
}) {
  const tr = useUiText();
  const required = TIER_FEATURES[feature];
  const switchDemo = useTierStore((s) => s.switchDemo);
  const label = required === "vip" ? tr("VIP 专享") : tr("PRO 专享");

  const lock = (
    <div className="flex flex-col items-center justify-center gap-1.5 py-6 text-center">
      <div className="flex items-center gap-1.5 text-ink-3">
        <LockIcon />
        <span className="text-[12px] font-medium">{label}</span>
      </div>
      <span className="rounded bg-surface-2 px-1.5 py-0.5 text-[9px] text-gold border border-border-soft">
        {tr(TIER_LABELS[required])} {tr("功能")}
      </span>
      <button
        onClick={() => switchDemo(feature)}
        className="mt-1 rounded border border-border-soft bg-surface-2 px-3 py-1 text-[10px] text-gold hover:bg-surface-3"
      >
        {tr("查看演示")}
      </button>
    </div>
  );

  if (lockMode === "replace" || !children) return lock;

  return (
    <div className="relative">
      <div className="pointer-events-none select-none opacity-50 blur-[3px]">{children}</div>
      <div className="absolute inset-0 flex items-center justify-center bg-surface-1/40">{lock}</div>
    </div>
  );
}
