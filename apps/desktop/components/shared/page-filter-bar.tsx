"use client";

import { useContextStore } from "@/lib/store/context";
import { useMarketStore } from "@/lib/store/market";
import type { Scene } from "@/lib/page-manifest";

interface Props {
  tfs?: string[];
  symbols?: string[];
  exchanges?: string[];
  showSym?: boolean;
  showEx?: boolean;
}

const DEFAULT_TFS = ["1h", "4h", "24h"];
const DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP"];
const DEFAULT_EXCHANGES = ["all", "Binance", "OKX", "Bybit", "Hyperliquid"];

// 设计稿 L4319-4347 renderPageFilterBar — tf + symbol + exchange chips
// 共享筛选条，读Selene.context.pageFilter，改写用 Zustand 替代直接 mutation
export function PageFilterBar({
  tfs = DEFAULT_TFS,
  symbols = DEFAULT_SYMBOLS,
  exchanges = DEFAULT_EXCHANGES,
  showSym = true,
  showEx = true,
}: Props) {
  const { pageFilter, setPageFilter } = useContextStore();

  return (
    <div className="pf-bar flex items-center gap-2 border-b border-border-soft bg-surface px-3 py-1.5 text-[10px]">
      <div className="flex items-center gap-1">
        <span className="text-ink-4">TF</span>
        {tfs.map((tf) => (
          <button
            key={tf}
            onClick={() => setPageFilter({ tf })}
            className={`pf-chip rounded px-1.5 py-0.5 transition-colors ${
              pageFilter.tf === tf
                ? "bg-gold/20 text-gold"
                : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
            }`}
          >
            {tf}
          </button>
        ))}
      </div>
      {showSym && (
        <div className="flex items-center gap-1">
          <span className="text-ink-4">SYM</span>
          {symbols.map((s) => (
            <button
              key={s}
              onClick={() => {
                const symBase = s;
                const symbol = `${s}-USDT-SWAP`;
                setPageFilter({ symbol });
                // 同时更新 symBase
                useMarketStore.setState({ symBase, pageFilter: { ...pageFilter, symbol } });
              }}
              className={`pf-chip rounded px-1.5 py-0.5 transition-colors ${
                pageFilter.symbol.startsWith(s)
                  ? "bg-gold/20 text-gold"
                  : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      )}
      {showEx && (
        <div className="flex items-center gap-1">
          <span className="text-ink-4">EX</span>
          {exchanges.map((e) => (
            <button
              key={e}
              onClick={() => setPageFilter({ exchange: e })}
              className={`pf-chip rounded px-1.5 py-0.5 transition-colors ${
                pageFilter.exchange === e
                  ? "bg-gold/20 text-gold"
                  : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
              }`}
            >
              {e}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// _v17Head — 节标题
export function V17Head({ t, sub }: { t: string; sub?: string }) {
  return (
    <div className="flex items-baseline justify-between border-b border-border-soft pb-1 mb-2">
      <span className="text-[11px] font-medium text-ink">{t}</span>
      {sub && <span className="text-[9px] text-ink-4">{sub}</span>}
    </div>
  );
}

// _v17Grid — n 列网格
export function V17Grid({ cols = 2, children }: { cols?: number; children: React.ReactNode }) {
  return (
    <div
      className="grid gap-2"
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
    >
      {children}
    </div>
  );
}

// _v17Card — 通用卡片
export function V17Card({
  title,
  children,
  right,
}: {
  title: string;
  children: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="rp-card rounded border border-border-soft bg-surface p-2">
      <div className="flex items-center justify-between border-b border-border-soft pb-1 mb-2">
        <span className="text-[10px] font-medium text-ink">{title}</span>
        {right && <span className="text-[9px] text-ink-4">{right}</span>}
      </div>
      {children}
    </div>
  );
}

// stat-block — 带金边的统计块（设计稿 L1919）
export function StatBlock({
  label,
  value,
  cls,
}: {
  label: string;
  value: string | number;
  cls?: string;
}) {
  return (
    <div className="stat-block border-l-2 border-gold bg-surface-2 px-2 py-1.5">
      <div className="text-[9px] text-ink-3">{label}</div>
      <div className={`num text-[12px] font-medium ${cls ?? "text-ink"}`}>{value}</div>
    </div>
  );
}

// page-host 覆盖层头（设计稿 L4196 page-head）
export function PageHostHead({
  title,
  origin,
  shortcut,
  rest,
}: {
  title: string;
  origin: string;
  shortcut?: string;
  rest?: string;
}) {
  return (
    <div className="flex items-center justify-between border-b border-border-soft bg-surface px-3 py-1.5">
      <div className="flex items-center gap-2">
        <h2 className="text-[12px] font-medium text-ink">{title}</h2>
        <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3">
          {origin === "design" ? "design" : "backend"}
        </span>
        {rest && <code className="text-[9px] text-ink-4">{rest}</code>}
      </div>
      {shortcut && (
        <kbd className="rounded border border-border-soft bg-surface-2 px-1.5 py-0.5 text-[9px] text-ink-3">
          {shortcut}
        </kbd>
      )}
    </div>
  );
}

export const SCENE_LABEL: Record<Scene, string> = {
  watch: "盯盘",
  research: "研策",
  trade: "执行",
  system: "系统",
  workspace: "工作台",
};
