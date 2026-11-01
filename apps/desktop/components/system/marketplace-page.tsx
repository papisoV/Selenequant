"use client";

import { useState } from "react";
import useSWR from "swr";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import {
  listListings,
  buyListing,
  publishListing,
  type MarketplaceListing,
} from "@/lib/api/marketplace";
import { toastSuccess, toastError } from "@/lib/toast";
import { useUiText } from "@/components/i18n/use-ui-text"

async function fetcher(): Promise<MarketplaceListing[]> {
  try {
    return await listListings({ limit: 50 });
  } catch {
    return [];
  }
}

export function MarketplacePage() {
  const tr = useUiText()

  const { data: rows, mutate } = useSWR<MarketplaceListing[]>(
    "/api/v1/marketplace/listings",
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 5000 }
  );
  const list = rows ?? [];
  const [busyId, setBusyId] = useState<string | null>(null);

  async function runAction(id: string, fn: () => Promise<unknown>, successMsg: string) {
    setBusyId(id);
    try {
      await fn();
      toastSuccess(successMsg);
      await mutate();
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("市场概览")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label={tr("在售")} value={String(list.length)} />
          <StatBlock label={tr("已发布")} value={String(list.filter((l) => l.status === "published").length)} cls="text-pos" />
          <StatBlock label={tr("总购买")} value={String(list.reduce((s, l) => s + (l.purchase_count ?? 0), 0))} cls="text-gold" />
        </div>
      </V17Card>
      <V17Card title={tr("策略列表")}>
        <div className="flex flex-col">
          {list.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
          )}
          {list.map((l, i) => {
            const id = String(l.id ?? `row-${i}`);
            const isPublished = l.status === "published";
            const isBusy = busyId === id;
            return (
              <div key={id} className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft">
                <span className="w-32 text-ink font-medium truncate">{l.description ?? l.id}</span>
                <span className="flex-1 text-ink-3 truncate">{(l.tags ?? []).join(", ") || "—"}</span>
                <span className="w-16 text-right text-gold font-mono num">{l.price ?? 0}</span>
                <span className="w-10 text-right text-pos font-mono num">{l.rating_avg?.toFixed(1) ?? "—"}</span>
                <span className={`w-14 text-right ${isPublished ? "text-pos" : "text-ink-3"}`}>
                  {l.status ?? "—"}
                </span>
                <span className="flex gap-1 w-32 justify-end">
                  {!isPublished && (
                    <button
                      disabled={isBusy}
                      onClick={() => runAction(id, () => publishListing(id), `已发布 ${id}`)}
                      className="rounded bg-sky-500/15 text-sky-400 px-1.5 py-0.5 text-[9px] hover:bg-sky-500/25 disabled:opacity-50"
                    >
                      {isBusy ? "…" : tr("发布")}
                    </button>
                  )}
                  {isPublished && (
                    <button
                      disabled={isBusy}
                      onClick={() => runAction(id, () => buyListing(id), `已购买 ${id}`)}
                      className="rounded bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5 text-[9px] hover:bg-emerald-500/25 disabled:opacity-50"
                    >
                      {isBusy ? "…" : tr("购买")}
                    </button>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </V17Card>
    </div>
  );
}
