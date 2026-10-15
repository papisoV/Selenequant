/**
 * Landing 专属 loading —— 静态骨架,不用产品壳的 MOCK 横幅。
 * (landing-root rem 基准 16px;产品壳 loading 的 11px 密度横幅在首屏闪烁是假象。)
 */
export default function Loading() {
  return (
    <div className="landing-root min-h-screen bg-bg">
      <div className="lp-container pt-24 pb-16">
        <div className="flex flex-col gap-5" aria-hidden>
          <div className="h-3 w-40 animate-pulse rounded bg-surface-3" />
          <div className="h-10 w-[min(28rem,80vw)] animate-pulse rounded bg-surface-3" />
          <div className="h-10 w-[min(22rem,70vw)] animate-pulse rounded bg-surface-3" />
          <div className="mt-4 h-3 w-[min(30rem,75vw)] animate-pulse rounded bg-surface-2" />
          <div className="h-3 w-[min(26rem,60vw)] animate-pulse rounded bg-surface-2" />
        </div>
        <div className="mt-20 h-56 w-full animate-pulse rounded-lg bg-surface-1" />
      </div>
    </div>
  );
}
