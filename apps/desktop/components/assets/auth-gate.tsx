"use client";

import { AccountGate } from "@/components/shared/account-gate";
import { useUiText } from "@/components/i18n/use-ui-text";
import { useAssetsStore } from "@/lib/store/assets";

// 薄封装:资产壳场景委托通用 AccountGate。
// mode 受控于 useAssetsStore(AssetOverview 既有 demo/empty 流),contextId 隔离本上下文
// AccountGate 内部对 title/description 已包 tr(),这里传中文源串即可。
export function AuthGate({ onDemo }: { onDemo: () => void }) {
  const mode = useAssetsStore((s) => s.mode);
  const tr = useUiText();
  return (
    <AccountGate
      contextId="assets-overview"
      title={tr("暂无资产数据")}
      description={tr("连接交易所后此处汇总持仓与余额")}
      mode={mode}
      onEnterDemo={onDemo}
    />
  );
}
