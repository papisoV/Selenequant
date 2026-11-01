"use client";

import { useCallback } from "react";
import { apiPost } from "@/lib/api-client";
import { toastSuccess, toastError, toastInfo } from "@/lib/toast";
import { useUiText } from "@/components/i18n/use-ui-text";
import { useTradingModeStore } from "@/lib/store/trading-mode";
import type { TradingPosition } from "@/lib/types/live-trading";
import type { PaperPositionData } from "@/lib/types/paper";

// P5-1 symbol 双向 normalize:positions.symbol → trd-page symbol 格式(BTC-USDT-SWAP)
// positions 可能是 BTC-PERP / BTC-USDT-SWAP / BTC/USDT:USDT,统一转成 BTC-USDT-SWAP
export function normalizeSymbol(s: string | undefined | null): string {
  if (!s) return "";
  if (s.endsWith("-PERP")) return s.replace(/-PERP$/, "-USDT-SWAP");
  if (s.includes("/")) return s.split("/")[0] + "-USDT-SWAP";
  return s;
}

// pos_side 映射:优先用 currentPosition.pos_side,fallback side→long/short
function derivePosSide(pos: TradingPosition | PaperPositionData): "long" | "short" {
  if (pos.pos_side === "long" || pos.pos_side === "short") return pos.pos_side;
  return pos.side === "buy" ? "long" : "short";
}

type PositionLike = TradingPosition | PaperPositionData;

export interface UsePositionActionsArgs {
  accountId: string;
  leverage: number; // state 输入框杠杆(fallback)
  mutateOrders?: () => Promise<unknown> | Promise<void>;
  mutatePositions?: () => Promise<unknown> | Promise<void>;
}

export function usePositionActions(args: UsePositionActionsArgs) {
  const { accountId, leverage, mutateOrders, mutatePositions } = args;
  const mode = useTradingModeStore((s) => s.mode);
  const tr = useUiText();

  // 平仓:按 mode 切端点 + body shape
  const closePosition = useCallback(
    async (side: "long" | "short" | "all", currentPosition: PositionLike | null) => {
      if (!currentPosition) {
        toastError(tr("无持仓可平"));
        return;
      }
      const sym = normalizeSymbol(currentPosition.symbol);
      try {
        if (mode === "live") {
          // live: POST /api/v1/trading/positions/close, body {symbol, account_id, amount?}
          // ClosePositionRequest 无 pos_side(trading.py L748-751)
          await apiPost("/api/v1/trading/positions/close", { symbol: sym, account_id: accountId });
        } else {
          // paper: POST /api/v1/paper/close-position, body {account_id, symbol, pos_side, quantity?, mark_price?}
          // pos_side 必填 enum(paper.py L181);closePosition("all") 必须拆 long+short 两次调用(paper.py L651 按 pos_side 单边匹配)
          if (side === "all") {
            const sides: ("long" | "short")[] = ["long", "short"];
            const errors: string[] = [];
            for (const ps of sides) {
              try {
                await apiPost("/api/v1/paper/close-position", {
                  account_id: accountId,
                  symbol: sym,
                  pos_side: ps,
                });
              } catch (e) {
                errors.push(`${ps}: ${e instanceof Error ? e.message : String(e)}`);
              }
            }
            if (errors.length === 2) {
              throw new Error(`两边平仓均失败: ${errors.join("; ")}`);
            }
          } else {
            await apiPost("/api/v1/paper/close-position", {
              account_id: accountId,
              symbol: sym,
              pos_side: side,
            });
          }
        }
        toastSuccess(tr("平仓请求已发送 ({side})", { side }));
        if (mutatePositions) await mutatePositions();
        if (mutateOrders) await mutateOrders();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        toastError(tr("平仓失败: {msg}", { msg }));
      }
    },
    [mode, accountId, mutatePositions, mutateOrders, tr]
  );

  // 对冲/反手
  const hedgeOrReverse = useCallback(
    async (action: "hedge" | "reverse", currentPosition: PositionLike | null) => {
      if (!currentPosition) {
        toastError(tr("无持仓可操作"));
        return;
      }
      const sym = normalizeSymbol(currentPosition.symbol);
      const qty = Number(currentPosition.quantity_base || currentPosition.quantity || 0);
      // leverage 取原持仓优先(Critic 必须改点 #4);TradingPosition.leverage 存在(L31),PaperPositionData.leverage 存在(L27)
      // 若原持仓 leverage 未知(undefined),fallback state leverage + toast 提示
      const origLev = currentPosition.leverage;
      const useLev = origLev ?? leverage;
      if (origLev === undefined) {
        toastInfo(tr("原持仓杠杆未知,使用当前输入框杠杆 {lev}x", { lev: leverage }));
      }
      const oppositeSide = derivePosSide(currentPosition) === "long" ? "sell" : "buy";

      // reverse 两步非原子兜底(Critic 必须改点 #3):close 独立 try/catch
      let closeOk = false;
      try {
        if (mode === "live") {
          await apiPost("/api/v1/trading/positions/close", { symbol: sym, account_id: accountId });
        } else {
          // paper: 按 pos_side 平当前边
          await apiPost("/api/v1/paper/close-position", {
            account_id: accountId,
            symbol: sym,
            pos_side: derivePosSide(currentPosition),
          });
        }
        closeOk = true;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        toastError(tr("平仓失败: {msg}", { msg }));
        return; // close 失败则不继续开仓
      }

      if (action === "reverse") {
        // close 成功 + 开仓失败 → toast 兜底
        try {
          await apiPost("/api/v1/trading/orders", {
            symbol: sym,
            side: oppositeSide,
            order_type: "market",
            quantity: qty,
            leverage: useLev,
            account_id: accountId,
          });
          toastSuccess(tr("反手请求已发送"));
          if (mutatePositions) await mutatePositions();
          if (mutateOrders) await mutateOrders();
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          // closeOk === true 但开仓失败
          toastError(tr("持仓已平,反手开仓失败,请手动开仓: {msg}", { msg }));
          // 记录日志(console + 可选上报)
          console.error("[hedgeOrReverse] reverse open failed after close", {
            symbol: sym,
            side: oppositeSide,
            qty,
            leverage: useLev,
            closeOk,
            error: msg,
          });
        }
      } else {
        // hedge: 只平 + toast(不再开反向仓;若需开反向仓后续单独 plan)
        toastSuccess(tr("对冲请求已发送"));
        if (mutatePositions) await mutatePositions();
        if (mutateOrders) await mutateOrders();
      }
    },
    [mode, accountId, leverage, mutatePositions, mutateOrders, tr]
  );

  return { closePosition, hedgeOrReverse, normalizeSymbol, derivePosSide };
}
