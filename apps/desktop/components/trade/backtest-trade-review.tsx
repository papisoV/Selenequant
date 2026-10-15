"use client";

// 回测交易复盘卡 — 点交易行展开:真 K 线 + entry/exit 标记 + 聚焦交易窗口。
// 对齐 QD trade-review-card;超越:PnL 着色 / 全景+聚焦双层 / 自动周期降档 / 逐笔步进。
// 数据:按交易窗口懒取(不学 QD 随响应全量下发),getKlineWithCache endTime 向后翻页 + IndexedDB 缓存。

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, Map, ScanSearch } from "lucide-react";
import CandlestickChart from "@/components/trading/candlestick-chart";
import { useChartTheme } from "@/components/chart/use-chart-theme";
import { getKlineWithCache } from "@/lib/api/markets";
import type { Kline } from "@/lib/types/backend";
import {
  buildFocusMarkers,
  buildOverviewMarkers,
  clipToWindow,
  klineTs,
  overviewPlan,
  paginationPlan,
  pickReviewInterval,
  type ReviewMarker,
} from "@/lib/chart/trade-review";
import type { BacktestTrade } from "@/lib/api/backtests";

type Mode = "focus" | "overview";

export function BacktestTradeReview({
  symbol,
  backtestInterval,
  startMs,
  endMs,
  exchange = "binance",
  marketType = "spot",
  trades,
  index,
  onIndexChange,
  snapshotSource,
}: {
  symbol: string;
  backtestInterval: string;
  startMs: number | null; // 回测区间(全景取数边界);旧结果可能缺 → 全景禁用
  endMs: number | null;
  exchange?: string;
  marketType?: string;
  trades: BacktestTrade[];
  index: number; // 当前聚焦的交易下标
  onIndexChange: (i: number) => void;
  snapshotSource: boolean; // config_snapshot 有 exchange/market_type(新结果)→ false 时展示回退注记
}) {
  const [mode, setMode] = useState<Mode>("focus");
  const [klines, setKlines] = useState<Kline[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trade = trades[index];

  const interval = useMemo(() => {
    if (mode === "overview") return backtestInterval;
    if (!trade?.entry_time || !trade?.exit_time) return backtestInterval;
    return pickReviewInterval(trade.entry_time, trade.exit_time, backtestInterval);
  }, [mode, trade, backtestInterval]);

  const fetchWindow = useCallback(
    async (entryMs: number, exitMs: number, iv: string, maxPages: number) => {
      const plan = paginationPlan(entryMs, exitMs, iv, maxPages);
      const collected: Kline[] = [];
      let endTime: number | null = plan.endTime;
      for (let i = 0; i < plan.pages; i++) {
        const { klines: batch } = await getKlineWithCache(symbol, iv, exchange, marketType, {
          endTime: endTime ?? undefined,
        });
        if (batch.length === 0) break;
        const oldest = klineTs(batch[0]);
        collected.push(...batch.filter((k) => !collected.some((c) => klineTs(c) === klineTs(k))));
        // 一页没吃满 = 没有更早数据了
        if (batch.length < 1500) break;
        endTime = oldest;
      }
      return collected;
    },
    [symbol, exchange, marketType],
  );

  useEffect(() => {
    let disposed = false;
    async function run() {
      if (mode === "focus") {
        if (!trade?.entry_time || !trade?.exit_time) {
          setKlines([]);
          setError("该笔交易缺少时间戳,无法复盘");
          return;
        }
        setError(null);
        setLoading(true);
        try {
          const raw = await fetchWindow(trade.entry_time, trade.exit_time, interval, 5);
          if (!disposed) setKlines(clipToWindow(raw, trade.entry_time, trade.exit_time, interval));
        } catch {
          if (!disposed) setError("K线取数失败");
        } finally {
          if (!disposed) setLoading(false);
        }
      } else {
        if (startMs == null || endMs == null) {
          setKlines([]);
          setError("旧结果缺少回测区间,全景不可用");
          return;
        }
        setError(null);
        setLoading(true);
        try {
          const plan = overviewPlan(startMs, endMs, backtestInterval);
          const collected: Kline[] = [];
          let endTime: number | null = plan.endTime;
          for (let i = 0; i < plan.pages; i++) {
            const { klines: batch } = await getKlineWithCache(
              symbol,
              backtestInterval,
              exchange,
              marketType,
              { endTime: endTime ?? undefined },
            );
            if (batch.length === 0) break;
            collected.push(...batch.filter((k) => !collected.some((c) => klineTs(c) === klineTs(k))));
            if (batch.length < 1500) break;
            endTime = klineTs(batch[0]);
          }
          if (!disposed) setKlines(collected);
        } catch {
          if (!disposed) setError("K线取数失败");
        } finally {
          if (!disposed) setLoading(false);
        }
      }
    }
    void run();
    return () => {
      disposed = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, index, interval, backtestInterval, symbol, exchange, marketType, startMs, endMs]);

  // 盈亏标记色 = 方向轴(随 locale 翻转);palette 进依赖数组,locale 翻转后标记重算。
  const theme = useChartTheme();
  const markers: ReviewMarker[] = useMemo(
    () =>
      mode === "focus" && trade
        ? buildFocusMarkers(trade, { win: theme.pos, loss: theme.neg })
        : buildOverviewMarkers(trades, backtestInterval, { win: theme.pos, loss: theme.neg }),
    [mode, trade, trades, backtestInterval, theme],
  );

  const step = (delta: number) => {
    const next = index + delta;
    if (next >= 0 && next < trades.length) onIndexChange(next);
  };

  return (
    <div className="rounded border border-border-soft bg-surface-1 p-2">
      <div className="mb-1.5 flex items-center gap-2 text-[11px]">
        <span className="text-ink-2 font-medium">
          K线复盘 — {symbol} <span className="text-ink-4">@{interval}</span>
          {mode === "focus" && trades.length > 0 && (
            <span className="text-ink-4"> · 第 {index + 1}/{trades.length} 笔</span>
          )}
        </span>
        <div className="flex-1" />
        {mode === "focus" && (
          <>
            <button
              className="rounded bg-surface-3 px-1.5 py-0.5 text-ink-3 hover:bg-surface-4 disabled:opacity-40"
              onClick={() => step(-1)}
              disabled={index <= 0}
              title="上一笔"
            >
              <ChevronLeft className="h-3 w-3" />
            </button>
            <button
              className="rounded bg-surface-3 px-1.5 py-0.5 text-ink-3 hover:bg-surface-4 disabled:opacity-40"
              onClick={() => step(1)}
              disabled={index >= trades.length - 1}
              title="下一笔"
            >
              <ChevronRight className="h-3 w-3" />
            </button>
          </>
        )}
        <button
          className={`rounded px-1.5 py-0.5 flex items-center gap-0.5 ${mode === "overview" ? "bg-sky-500/15 text-sky-400" : "bg-surface-3 text-ink-3 hover:bg-surface-4"}`}
          onClick={() => setMode(mode === "focus" ? "overview" : "focus")}
          title={mode === "focus" ? "切换全景:全回测区间+全部交易标记" : "回到单笔聚焦"}
        >
          {mode === "focus" ? <Map className="h-2.5 w-2.5" /> : <ScanSearch className="h-2.5 w-2.5" />}
          {mode === "focus" ? "全景" : "聚焦"}
        </button>
      </div>
      {!snapshotSource && (
        <div className="mb-1 text-[9px] text-ink-4">
          旧结果未记录交易所/市场类型,按 {exchange}/{marketType} 行情复盘
        </div>
      )}
      {error ? (
        <div className="py-6 text-center text-[11px] text-ink-4">{error}</div>
      ) : loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div className="h-[420px]">
          <CandlestickChart
            data={klines}
            interval={interval}
            symbol={symbol}
            markers={markers}
          />
        </div>
      )}
    </div>
  );
}
