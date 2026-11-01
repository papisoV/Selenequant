"use client";

import { useEffect, useRef } from "react";
import { useApi } from "@/lib/hooks/use-api";

/**
 * 后端 `/api/v1/ashare/session` 的响应。
 *
 * `polling` 是粗粒度那一位 —— 前端只关心「此刻的截面还会不会变」；
 * `phase` 是精确标签，用来区分「午休」「已收盘」「非交易日」，文案不同。
 */
export interface AshareSessionData {
  status: string;
  phase:
    | "PRE_OPEN"
    | "AUCTION"
    | "OPEN"
    | "LUNCH_BREAK"
    | "CLOSING_AUCTION"
    | "CLOSED";
  polling: boolean;
  is_trading_day: boolean;
  /** 判定所用的北京时间（带 +08:00）。显示"截至 xx:xx"用它，不要用浏览器本地时间。 */
  as_of: string;
}

/**
 * A 股交易时段判定 —— 决定看盘台是否轮询。
 *
 * 为什么放在服务端：前端要自己判断就得复刻 XSHG 交易日历 + 东八区换算，
 * 节假日表一过时，页面要么收盘后还在空转轮询，要么整个交易日一片死寂。
 * 后端 `selene_space_risk.trading_calendar.market_phase` 是日历感知的权威。
 *
 * 为什么不复用页面已有的 `/phase`：那个 `phase` 是**情绪周期**
 * （冰点/启动/主升/高潮/退潮/修复），与交易时段是两个正交的问题，
 * 只是函数重名。用情绪周期去决定轮询间隔会把两件事绑死。
 *
 * 失败时 `polling` 恒为 false —— 服务挂了时轮询只会把后台打得更死；
 * 用户手动重开页面即可恢复。`unknown` 让调用方能区分"确认收盘"与"没问到"。
 *
 * 每 30s 重新问一次：时段边界（09:15 / 09:30 / 11:30 / 13:00 / 15:00）
 * 最多晚 30 秒发现，而 30s 一次的轻量请求比把边界写死在客户端安全得多。
 */
export function useAshareSession() {
  const { data, error } = useApi<AshareSessionData>(
    "/api/v1/ashare/session",
    { refreshInterval: 30_000 }
  );

  const phase = data?.phase ?? null;
  // 数据未到时按"不轮询"处理：宁可晚 30 秒开始轮询，也不要在开盘前
  // 用一次失败的判定把九个端点全放出去。
  const sessionOpen = data?.polling === true;

  return {
    data,
    phase,
    sessionOpen,
    /**
     * 拿不到判定 —— 首帧还没到，或者请求失败且没有缓存。
     * SWR 在 loading 期把 data 留在 undefined（不是 null），所以判 falsey
     * 就够，不必区分两者：对调用方而言「还没有可信的判定」是同一件事。
     */
    unknown: data == null,
    error,
  };
}

/**
 * 页面打开时立即重取一次看盘台数据。
 *
 * 页面已设 `revalidateOnFocus: false`（避免切窗回来就刷一屏），代价是：
 * 标签页切走十分钟再回来，看到的是十分钟前的帧，而下一个轮询点最多还要
 * 等一整档（慢档 60s）。对一张"看盘"页面，十分钟前的涨停数比没数据更糟 ——
 * 它看起来是对的。
 *
 * 这里只在**挂载**时重取一次（包含切回本页时的重挂载），不接管焦点事件：
 * 焦点重取是每次 Alt+Tab 都触发，那正是 revalidateOnFocus 当初要关掉的。
 *
 * 只在交易时段生效：收盘后重取得到的是同一份冻结截面，白白打一次后端。
 */
export function useRevalidateAshareOnOpen(
  sessionOpen: boolean,
  mutators: Array<{ mutate: () => Promise<unknown> }>
): void {
  const mutatorsRef = useRef(mutators);
  mutatorsRef.current = mutators;

  useEffect(() => {
    if (!sessionOpen) return;
    // fire-and-forget：重取失败由各块自己的 error 态呈现，这里不重复上报。
    for (const m of mutatorsRef.current) {
      void m.mutate();
    }
  }, [sessionOpen]);
}
