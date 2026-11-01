"use client";

import { useApi } from "@/lib/hooks/use-api";
import { useContextStore } from "@/lib/store/context";
import { usePaperStore } from "@/lib/store/paper";
import type { PaperPositionData } from "@/lib/types/paper";

// 设计稿 L8474-8495 genKTData():
// - signals: [{ at: number(0-89), type: 'buy'|'sell' }]
// - ai:      [{ at: number, action, reason, tool, durationMs, confidence }]
// - positions: [{ from: number, to: number, side: 'long'|'short', pnl: number }]
//
// 90 根 K 线宽度，CW = 100/90 ≈ 1.11% 每根
// 真实数据源：
// - signals: GET /api/v1/signals/current?symbol=X&exchange=binance（CompositeSignal 单点）
// - ai:      无现成端点（agent/chat 是 POST 流式）→ 轨道空
// - positions: usePaperStore.positions（WS 实时持仓快照）

export interface KTSignal { at: number; type: "buy" | "sell"; }
export interface KTAi {
  at: number;
  action: "open_long" | "close_long" | "open_short" | "close_short";
  reason: string;
  tool: string;
  durationMs: number;
  confidence: number;
}
export interface KTPosition { from: number; to: number; side: "long" | "short"; pnl: number; }
export interface KTTracks {
  signals: KTSignal[];
  ai: KTAi[];
  positions: KTPosition[];
}

// 后端 CompositeSignal（packages/shared-types/selene_space_types/derivatives.py L457）
interface CompositeSignal {
  symbol?: string;
  side?: string; // buy|sell|neutral
  strength?: number;
  confidence?: number;
  timestamp?: number;
}

const N = 90;
const RIGHT_BAR = N - 1; // 当前 bar = 最右

function toBuySell(side: string | undefined): "buy" | "sell" | null {
  if (!side) return null;
  const s = side.toLowerCase();
  if (s === "buy" || s === "bullish" || s === "long") return "buy";
  if (s === "sell" || s === "bearish" || s === "short") return "sell";
  return null;
}

function adaptSignal(sig: CompositeSignal | null | undefined): KTSignal[] {
  if (!sig) return [];
  const t = toBuySell(sig.side);
  if (!t) return [];
  return [{ at: RIGHT_BAR, type: t }];
}

function adaptPositions(positions: PaperPositionData[]): KTPosition[] {
  if (!positions || positions.length === 0) return [];
  // 当前持仓只有当前快照，全部映射到最右 bar 的单点区间
  return positions
    .filter((p) => p && p.symbol)
    .map((p) => {
      const sideRaw = (p.pos_side || p.side || "").toLowerCase();
      const side: KTPosition["side"] = sideRaw === "short" || sideRaw === "sell" ? "short" : "long";
      const pnl = Number(p.unrealized_pnl ?? 0);
      return { from: RIGHT_BAR, to: RIGHT_BAR, side, pnl };
    });
}

export function useKTTracks(): KTTracks {
  const { pageFilter, symBase } = useContextStore();
  const sym = symBase || pageFilter.symbol || "BTC";
  // signals: GET /api/v1/signals/current?symbol=X&exchange=binance
  // 后端 symbol 格式 "BTC/USDT"，前端 symBase 是 "BTC"，需拼 /USDT
  const symbolParam = sym.includes("/") ? sym : `${sym}/USDT`;
  const { data: sigRaw } = useApi<CompositeSignal>(
    `/api/v1/signals/current?exchange=binance&symbol=${encodeURIComponent(symbolParam)}`
  );
  // positions: 从 paper store 拿实时快照
  const positions = usePaperStore((s) => s.positions);

  const signals = adaptSignal(sigRaw);
  const ai: KTAi[] = []; // 无 agent actions 端点
  const posAdapted = adaptPositions(positions);

  return { signals, ai, positions: posAdapted };
}

// 颜色映射（设计稿 L8526-8527）
export const AI_COLOR: Record<KTAi["action"], string> = {
  open_long: "var(--color-pos)",
  close_long: "#58905a",
  open_short: "var(--color-neg)",
  close_short: "#b46762",
};

export const AI_ABBR: Record<KTAi["action"], string> = {
  open_long: "OL",
  close_long: "CL",
  open_short: "OS",
  close_short: "CS",
};

export const AI_LABEL: Record<KTAi["action"], string> = {
  open_long: "开多",
  close_long: "平多",
  open_short: "开空",
  close_short: "平空",
};
