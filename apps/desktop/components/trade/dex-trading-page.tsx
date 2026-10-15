"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { api } from "@/lib/api-client";
import { Search } from "lucide-react";

// 业务域插入页 — DEX 交易
interface DexPosition {
  pair?: string;
  size?: number;
  pnl?: number;
}
interface DexData {
  balance?: number;
  positions?: DexPosition[];
}

interface ScanToken {
  address?: string;
  score?: number;
}

// K33(判定表 r5):扫描是操作不是页面 — GET /api/v1/dex-trading/scan 落为页内按钮
interface ScanResult {
  tokens?: ScanToken[];
  message?: string;
}

export function DexTradingPage() {
  const { data } = useApi<DexData | null>(
    "/api/v1/dex-trading/portfolio"
  );
  const [scanning, setScanning] = useState(false);
  const [scan, setScan] = useState<ScanResult | null>(null);

  async function runScan() {
    setScanning(true);
    try {
      setScan(await api<ScanResult>("/api/v1/dex-trading/scan"));
    } catch {
      setScan({ tokens: [] });
    } finally {
      setScanning(false);
    }
  }
  const d = data ?? null;
  const positions = d?.positions ?? [];
  const totalPnl = positions.reduce((sum, p) => sum + (p.pnl ?? 0), 0);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="DEX 账户">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label="余额" value={d?.balance != null ? `$${d.balance.toLocaleString()}` : "—"} cls="text-gold" />
          <StatBlock label="持仓数" value={String(positions.length)} />
          <StatBlock
            label="总 PnL"
            value={positions.length > 0 ? `${totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(0)}` : "—"}
            cls={totalPnl >= 0 ? "text-pos" : "text-neg"}
          />
        </div>
      </V17Card>
      <V17Card
        title="持仓"
        right={
          <button
            type="button"
            onClick={runScan}
            disabled={scanning}
            className="flex items-center gap-1 rounded bg-gold/15 px-2 py-0.5 text-[9px] text-gold hover:bg-gold/25 disabled:opacity-40"
          >
            <Search className="h-2.5 w-2.5" /> {scanning ? "扫描中…" : "AI 扫描"}
          </button>
        }
      >
        {scan && (
          <div className="mb-2 rounded border border-border-soft bg-surface-2 p-2">
            {scan.message ? (
              <div className="text-[10px] text-ink-4">{scan.message}</div>
            ) : (scan.tokens?.length ?? 0) === 0 ? (
              <div className="text-[10px] text-ink-4">扫描完成，无结果</div>
            ) : (
              <div className="flex flex-col">
                {(scan.tokens ?? []).map((t, i) => (
                  <div key={i} className="flex items-center justify-between text-[10px] py-1 border-b border-border-soft last:border-b-0">
                    <span className="font-mono text-ink break-all">{t.address}</span>
                    <span className="ml-2 font-mono num text-gold">{t.score?.toFixed(4)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        <div className="flex flex-col">
          {positions.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
          )}
          {positions.map((p, i) => {
            const cls = (p.pnl ?? 0) >= 0 ? "text-pos" : "text-neg";
            return (
              <div key={i} className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft">
                <span className="w-20 text-ink font-medium">{p.pair}</span>
                <span className="flex-1 text-ink-3 font-mono num">size {p.size?.toFixed(2)}</span>
                <span className={`w-14 text-right font-mono num ${cls}`}>
                  {(p.pnl ?? 0) >= 0 ? "+" : ""}{(p.pnl ?? 0).toFixed(0)}
                </span>
              </div>
            );
          })}
        </div>
      </V17Card>
    </div>
  );
}
