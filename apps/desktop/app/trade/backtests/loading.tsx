import { Skeleton } from "@/components/ui/skeleton";

/** 回测中心骨架: 对齐 backtests-page 的筛选行 + 计数 StatBlock + 任务列表 */
export default function Loading() {
  return (
    <div className="v17-page-grid grid gap-2" aria-hidden>
      <div className="flex items-center gap-2">
        <Skeleton className="h-7 w-[170px]" />
        <Skeleton className="h-7 w-24" />
        <Skeleton className="h-7 w-20" />
      </div>
      <div className="grid grid-cols-3 gap-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="rounded-lg border border-border bg-card p-2">
            <Skeleton className="h-2.5 w-10" />
            <Skeleton className="mt-1.5 h-4 w-12" />
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-1.5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-full rounded border border-border-soft" />
        ))}
      </div>
    </div>
  );
}
