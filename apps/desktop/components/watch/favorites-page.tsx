"use client";

import { useMemo } from "react";
import { useSymbolFavorites } from "@/lib/hooks/use-symbol-favorites";
import { useMarketStore } from "@/lib/store/market";
import { DEFAULT_SYMBOLS } from "@/lib/monitor-feed";
import { useUiText } from "@/components/i18n/use-ui-text"

const bare = (s: string): string => s.replace(/[-/:].*$/, "");

interface FavoritesRow {
  sym: string;
  demo: boolean;
}

export function FavoritesPage() {
  const tr = useUiText()

  const { favorites, toggleFavorite, isLoading } = useSymbolFavorites();
  const { setSymBase } = useMarketStore();

  // 空态注入默认 12 标 demo(与 DEFAULT_SYMBOLS 对齐);有自选则只显自选
  const rows = useMemo<FavoritesRow[]>(() => {
    if (favorites.length > 0) {
      return favorites.map((s) => ({ sym: s, demo: false }));
    }
    return DEFAULT_SYMBOLS.map((s) => ({ sym: s, demo: true }));
  }, [favorites]);

  return (
    <div className="favorites-list flex h-full flex-col overflow-auto p-3">
      <div className="mb-2 flex items-center gap-2 border-b border-border-soft pb-1">
        <span className="text-[11px] font-medium text-gold">{tr("自选")}</span>
        <span className="text-[9px] text-ink-4">{isLoading ? tr("同步中…") : tr("{length} 个标的", { length: rows.length })}</span>
      </div>
      {rows.length === 0 && (
        <div className="py-3 text-center text-[11px] text-ink-4">{tr("暂无自选品种")}</div>
      )}
      {rows.map(({ sym, demo }) => (
        <div
          key={sym}
          role="button"
          tabIndex={0}
          onClick={() => setSymBase(bare(sym))}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") setSymBase(bare(sym));
          }}
          className="fav-row flex cursor-pointer items-center justify-between border-b border-border-soft py-1 text-[10px]"
        >
          <span className="flex items-center gap-2">
            <span className="font-mono text-gold">{sym}</span>
            {demo && <span className="text-[9px] text-ink-4">{tr("演示")}</span>}
          </span>
          <button
            type="button"
            className="text-ink-3 hover:text-pos"
            onClick={(e) => {
              e.stopPropagation();
              toggleFavorite(sym);
            }}
          >
            {demo ? tr("+ 收藏") : tr("× 删除")}
          </button>
        </div>
      ))}
    </div>
  );
}
