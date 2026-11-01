"use client";

// 数据加载中的骨架占位,替代"加载中…"文字,减少首次访问时空态→满屏的跳变
export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="flex h-full flex-col gap-2 p-3">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-4 animate-pulse rounded bg-surface-3"
          style={{ width: `${92 - (i % 4) * 10}%` }}
        />
      ))}
    </div>
  );
}

export function SkeletonGrid({ cols = 3, rows = 2 }: { cols?: number; rows?: number }) {
  return (
    <div
      className="grid gap-2 p-3"
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
    >
      {Array.from({ length: cols * rows }).map((_, i) => (
        <div key={i} className="h-9 animate-pulse rounded bg-surface-3" />
      ))}
    </div>
  );
}
