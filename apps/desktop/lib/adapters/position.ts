// Adapters — backend shape → frontend shape
// 参考 memory/feedback_frontend_adapter_layer.md 的规则

import type { Position, Order, Fill } from "@/lib/types/backend";
import type {
  FrontendPosition,
  FrontendPendingOrder,
  FrontendClosedPosition,
} from "@/lib/types/frontend";

// Position: backend {symbol, side, quantity, avg_entry_price, current_price, unrealized_pnl, ...}
//         → frontend {sym, side('多'/'空'), qty, avg, now, pnl(带+/-), cls('pos'/'neg'), market}
export function adaptPosition(p: Position, market = "futures"): FrontendPosition {
  const pnl = Number(p.unrealized_pnl ?? 0);
  return {
    sym: p.symbol ?? "",
    side: p.side === "buy" ? "多" : p.side === "sell" ? "空" : String(p.side ?? ""),
    qty: String(p.quantity_base || p.quantity || 0),
    avg: String(p.avg_entry_price ?? 0),
    now: p.current_price != null ? String(p.current_price) : "—",
    pnl: (pnl >= 0 ? "+" : "") + pnl,
    cls: pnl >= 0 ? "pos" : "neg",
    market,
  };
}

// Order (pending): backend {symbol, side, type, quantity, price, status, ...}
//                → frontend {sym, side('买'/'卖'), qty, price, tif, status, market}
export function adaptPendingOrder(o: Order, market = "futures"): FrontendPendingOrder {
  return {
    sym: o.symbol ?? "",
    side: o.side === "buy" ? "买" : o.side === "sell" ? "卖" : String(o.side ?? ""),
    qty: String(o.quantity ?? 0),
    price: String(o.price ?? 0),
    tif: String(o.type ?? ""),
    status: String(o.status ?? ""),
    market,
  };
}

// Fill → ClosedPosition (近似)
export function adaptClosedPosition(f: Fill, market = "futures"): FrontendClosedPosition {
  const pnl = Number(f.realized_pnl ?? 0);
  const dur = "—";
  return {
    time: new Date(f.timestamp).toLocaleString("zh-CN", { hour12: false }),
    sym: f.symbol ?? "",
    side: f.side === "buy" ? "多" : f.side === "sell" ? "空" : String(f.side ?? ""),
    qty: String(f.quantity ?? 0),
    price: String(f.price ?? 0),
    pnl: (pnl >= 0 ? "+" : "") + pnl,
    cls: pnl >= 0 ? "pos" : "neg",
    dur,
    market,
  };
}
