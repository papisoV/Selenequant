"use client";

import { useAccountGateStore, type GateMode } from "@/lib/store/account-gate";
import { useUiText } from "@/components/i18n/use-ui-text";

// 通用账户授权门槛:空态 + 添加授权(disabled) + 查看演示
// mode 控制:
//   受控(props 传 mode/onModeChange)→ 交给外层 store 驱动(AssetOverview 场景,不破坏既有 demo 流)
//   非受控 → 按 contextId 用内部 account-gate store 隔离多业务上下文
export function AccountGate({
  contextId,
  title = "暂无数据",
  description = "添加授权账户后此处汇总数据",
  mode,
  onModeChange,
  onEnterDemo,
}: {
  contextId: string;
  title?: string;
  description?: string;
  mode?: GateMode;
  onModeChange?: (m: GateMode) => void;
  onEnterDemo?: () => void;
}) {
  const storeMode = useAccountGateStore((s) => s.getMode(contextId));
  const setStoreMode = useAccountGateStore((s) => s.setMode);
  const tr = useUiText();

  const current = mode ?? storeMode;
  if (current !== "empty") return null;

  const enterDemo = () => {
    if (onEnterDemo) {
      onEnterDemo();
      return;
    }
    if (onModeChange) onModeChange("demo");
    else setStoreMode(contextId, "demo");
  };

  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8">
      <div className="text-[12px] text-ink-3">{tr(title)}</div>
      <div className="text-[9px] text-ink-4">{tr(description)}</div>
      <button
        disabled
        className="mt-1 rounded border border-border-soft bg-surface-2 px-3 py-1 text-[10px] text-ink-4 opacity-60"
      >
        {tr("添加授权")}
      </button>
      <button
        onClick={enterDemo}
        className="mt-1 rounded border border-border-soft bg-surface-2 px-3 py-1 text-[10px] text-gold hover:bg-surface-3"
      >
        {tr("查看演示数据")}
      </button>
    </div>
  );
}
