"use client";

import { useState } from "react";
import { ArbPanel } from "./arb-panel";
import { DcaPanel } from "./dca-panel";
import { CopyTradePanel } from "./copy-trade-panel";
import { AiGridConfig } from "./ai-grid-config";
import { DrawOrderTool } from "./draw-order-tool";
import { useUiText } from "@/components/i18n/use-ui-text"

const TABS = [
  { key: "arb", label: "套利", Comp: ArbPanel },
  { key: "dca", label: "DCA", Comp: DcaPanel },
  { key: "copy", label: "跟单", Comp: CopyTradePanel },
  { key: "grid", label: "AI 网格", Comp: AiGridConfig },
  { key: "draw", label: "画线", Comp: DrawOrderTool },
] as const;

export function QuantToolsPage() {
  const tr = useUiText()

  const [tab, setTab] = useState<string>("arb");
  const Active = TABS.find((t) => t.key === tab)?.Comp ?? ArbPanel;

  return (
    <div className="quant-tools-page flex h-full flex-col gap-2 overflow-auto p-3">
      <h1 className="text-sm font-semibold text-ink">{tr("量化工具")}</h1>
      <div className="flex gap-1 border-b border-border-soft pb-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            data-tab={t.key}
            aria-selected={tab === t.key}
            className={`rounded px-2 py-1 text-[10px] transition-colors ${tab === t.key ? "bg-gold/20 text-gold" : "text-ink-3 hover:bg-surface-2"}`}
          >
            {tr(t.label)}
          </button>
        ))}
      </div>
      <div className="rounded border border-border-soft bg-surface-2 p-2">
        <Active />
      </div>
    </div>
  );
}
