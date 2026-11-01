import { Skeleton } from "@/components/ui/skeleton";

/** 总览页骨架: 对齐 dash-page 的 V17Card 双排 StatBlock 网格 + 主图区 */
export default function Loading() {
  return (
    <div className="v17-page-grid grid gap-2" aria-hidden>
      <div className="grid grid-cols-4 gap-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="rounded-lg border border-border bg-card p-2">
            <Skeleton className="h-2.5 w-12" />
            <Skeleton className="mt-1.5 h-4 w-16" />
          </div>
        ))}
      </div>
      <div className="h-[280px] w-full rounded-lg border border-border bg-card" />
    </div>
  );
}
