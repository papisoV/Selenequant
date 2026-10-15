"use client";

import { useState } from "react";
import { V17Card } from "@/components/shared/page-filter-bar";
import { AssetOverview } from "@/components/assets/asset-overview";
import { PNLCurvePanel } from "@/components/trading/pnl-curve-panel";
import { PositionTableEmbedded, adaptPosition } from "@/components/trading/position-table-embedded";
import type { PositionTab } from "@/components/trading/position-table-embedded";
import { usePaperStore } from "@/lib/store/paper";
import { useShallow } from "zustand/react/shallow";
import { useAccountId } from "@/lib/hooks/use-account-id";
import type { PaperPositionData } from "@/lib/types/paper";

export function PosPage() {
  const [sub, setSub] = useState<PositionTab>("open");
  const accountId = useAccountId();
  const positions = usePaperStore(useShallow((s) => s.positions));
  const realPositions = positions as PaperPositionData[];
  const OPEN = realPositions && realPositions.length > 0 ? realPositions.map(adaptPosition) : [];

  return (
    <div className="v17-page-grid grid gap-2">
      <PositionTableEmbedded tab={sub} onTabChange={setSub} />
      {sub === "open" && (
        <V17Card title="SL-TP 管理">
          <div className="grid grid-cols-2 gap-2 text-[10px]">
            {OPEN.filter((r) => r.pnl).map((r) => (
              <div key={r.sym} className="rounded border border-border-soft bg-surface-2/50 px-2 py-1">
                <div className="flex justify-between">
                  <span className="text-gold font-mono">{r.sym}</span>
                  <span className={r.cls === "pos" ? "text-pos" : "text-neg"}>{r.pnl} USDT</span>
                </div>
                <div className="grid grid-cols-2 gap-1 mt-1 text-[9px]">
                  <div className="bg-surface-2 rounded px-1.5 py-0.5">
                    <span className="text-ink-4">SL</span>
                    <span className="text-neg ml-1 font-mono">-{(parseFloat(r.avg || "0") * 0.02).toFixed(1)}</span>
                  </div>
                  <div className="bg-surface-2 rounded px-1.5 py-0.5">
                    <span className="text-ink-4">TP</span>
                    <span className="text-pos ml-1 font-mono">+{(parseFloat(r.avg || "0") * 0.04).toFixed(1)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </V17Card>
      )}
      <V17Card title="资产聚合">
        <AssetOverview />
      </V17Card>
      <PNLCurvePanel accountId={accountId} />
    </div>
  );
}
