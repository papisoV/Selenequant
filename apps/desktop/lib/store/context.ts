"use client";

import { useShallow } from "zustand/react/shallow";
import { useMarketStore } from "./market";
import { useTradingStore } from "./trading";

// Context aggregation hook — 向后兼容原 useContextStore API
// 内部代理到 2 个独立 slice store：market / trading
// 消费者无需改动，仍用 useContextStore() 取所有字段
//
// 拆分动机：原单 store 把市场/交易状态混在一起，任一字段变更触发所有消费者 re-render
// 拆分后：symbol 切换只触发 market 消费者 re-render，tick 跳动只触发 trading 消费者
//
// 新代码推荐直接用具体 store：
//   useMarketStore() — symbol/tf/exchange
//   useTradingStore() — layout/tick/gridParams

export type LayoutPreset =
  | "default"
  | "compact"
  | "focus"
  | "grid2"
  | "grid4"
  | "charts6"
  | "charts9"
  | "minimal"
  | "pro";

export interface PageFilter {
  tf: string;
  symbol: string;
  exchange: string;
}

export interface TickState {
  equity: number;
  pnlToday: number;
  flash: "equity" | "pnl" | null;
}

export function useContextStore() {
  // useShallow:浅比较选中的字段,避免无 selector 订阅整个 store。
  // 修复:lastWsKline 每 ~1s 被 WS kline 写入(新对象)会触发所有 useContextStore 消费者整树重渲染,
  // 现在只订阅业务真正用到的字段,高频 WS 字段(kline 乐观更新用)不再牵连页面。
  const market = useMarketStore(
    useShallow((s) => ({
      symBase: s.symBase,
      pageFilter: s.pageFilter,
      setSymBase: s.setSymBase,
      setPageFilter: s.setPageFilter,
    })),
  );
  const trading = useTradingStore(
    useShallow((s) => ({
      gridParams: s.gridParams,
      layoutPreset: s.layoutPreset,
      tick: s.tick,
      setGridParams: s.setGridParams,
      setLayoutPreset: s.setLayoutPreset,
      setTick: s.setTick,
    })),
  );
  return { ...market, ...trading };
}
