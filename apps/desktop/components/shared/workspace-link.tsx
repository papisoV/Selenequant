"use client";

import Link from "next/link";
import { useContextStore } from "@/lib/store/context";
import { useUiText } from "@/components/i18n/use-ui-text";

interface Props {
  /** 信号源标识 */
  source: "vchart" | "gex" | "liqmap" | "sig" | "kf";
  /** 可选 symbol 覆盖,缺省用 context.pageFilter.symbol */
  symbol?: string;
  /** 按钮文案,默认 "加入 workspace" */
  label?: string;
  /** 额外 className */
  className?: string;
}

/**
 * WorkspaceLink — watch 页面"加入 workspace"入口
 *
 * 跳转 /workspace/signal?symbol=X&source=Y
 * signal 页面读 query:左侧替换为 source 对应 watch panel,右侧 OrderForm 预填 symbol
 */
export function WorkspaceLink({ source, symbol, label = "加入 workspace", className }: Props) {
  const tr = useUiText();
  const { pageFilter } = useContextStore();
  const sym = symbol ?? pageFilter.symbol ?? "BTC";
  const href = `/workspace/signal?symbol=${encodeURIComponent(sym)}&source=${source}`;
  return (
    <Link
      href={href}
      className={`ws-link-btn inline-flex items-center gap-1 rounded border border-gold/40 bg-gold/10 px-2 py-0.5 text-[9px] text-gold transition-colors hover:bg-gold/20 ${className ?? ""}`}
      title={tr("在 workspace 中以 {sym} 为标的下单", { sym })}
    >
      <span aria-hidden="true">⊞</span>
      {tr(label)}
    </Link>
  );
}
