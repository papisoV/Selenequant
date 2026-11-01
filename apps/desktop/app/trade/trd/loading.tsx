import { Skeleton } from "@/components/ui/skeleton"

export default function Loading() {
  return (
    <div className="flex h-full flex-col gap-3 p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Skeleton className="h-7 w-32" />
          <Skeleton className="h-5 w-20" />
        </div>
        <Skeleton className="h-7 w-40" />
      </div>

      <div className="grid flex-1 grid-cols-[280px_minmax(0,1fr)_360px] gap-3 overflow-hidden">
        <div className="flex flex-col gap-2 rounded-xl border border-border bg-card p-3">
          <Skeleton className="h-6 w-24" />
          <div className="space-y-1.5">
            {Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="flex items-center justify-between gap-2">
                <Skeleton className="h-4 w-16" />
                <Skeleton className="h-4 w-20" />
                <Skeleton className="h-4 w-12" />
              </div>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4">
          <div className="flex gap-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-8 flex-1" />
            ))}
          </div>
          <div className="flex gap-2">
            <Skeleton className="h-9 w-20 bg-emerald-500/15" />
            <Skeleton className="h-9 w-20 bg-red-500/15" />
          </div>
          <Skeleton className="h-10 w-full" />
          <div className="grid grid-cols-2 gap-3">
            <Skeleton className="h-9" />
            <Skeleton className="h-9" />
          </div>
          <div className="flex gap-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-8 flex-1" />
            ))}
          </div>
          <div className="flex gap-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-7 flex-1" />
            ))}
          </div>
          <Skeleton className="h-10 w-full" />
          <div className="mt-auto space-y-2">
            <Skeleton className="h-3 w-32" />
            <Skeleton className="h-3 w-40" />
          </div>
        </div>

        <div className="flex flex-col gap-2 rounded-xl border border-border bg-card p-3">
          <Skeleton className="h-6 w-28" />
          <Skeleton className="h-9 w-full" />
          <div className="space-y-1.5">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="flex items-center gap-2">
                <Skeleton className="h-4 w-10" />
                <Skeleton className="h-4 w-12" />
                <Skeleton className="h-4 flex-1" />
                <Skeleton className="h-4 w-14" />
                <Skeleton className="h-4 w-16" />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
