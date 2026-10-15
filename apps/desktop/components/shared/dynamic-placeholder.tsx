"use client";

// 动态详情页(task_id 等非 symbol 占位符)统一特殊态:提示从列表进入。
// 4 个通用渲染器共用,消除重复 JSX。

export function DynamicPlaceholder({ rest, params }: { rest: string; params?: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6">
      <p className="text-[12px] text-ink-2">此页面需从列表进入</p>
      <p className="text-[9px] text-ink-4">{rest}</p>
      {params && (
        <p className="max-w-md text-center text-[9px] text-ink-4">{params}</p>
      )}
    </div>
  );
}
