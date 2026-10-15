"use client";

// SWR wrapper around getKlineWithCache + IndexedDB 预填 + 缺口补齐
// 职责边界：
// - IndexedDB 预填 + 增量 fetch + 缺口补齐
// - interval 切换时清旧 cache key（避免跨 interval 数据污染）
// - WS interval 切换不在此 hook 范围（归 P0b use-markets-ws）
// - WS 断线重连后自动 gap 补齐（监听 marketsStreamState: recovering→live 触发 mutate）
// - WS kline 实时注入：监听 market store 的 lastWsKline，乐观更新 SWR cache

import { useCallback, useEffect, useRef } from "react";
import useSWR, { useSWRConfig } from "swr";
import { getKlineWithCache } from "@/lib/api/markets";
import { clearCachedKlines } from "@/lib/kline-cache";
import { useMarketStore } from "@/lib/store/market";
import type { Kline } from "@/lib/types/backend";

// 时间周期对应的毫秒数
const INTERVAL_MS: Record<string, number> = {
  "1m": 60_000,
  "5m": 300_000,
  "15m": 900_000,
  "30m": 1_800_000,
  "1h": 3_600_000,
  "4h": 14_400_000,
  "1d": 86_400_000,
  "1w": 604_800_000,
};

function getIntervalMs(interval: string): number {
  return INTERVAL_MS[interval] ?? 60_000;
}

// 自适应轮询：短周期行情变化快、长周期没必要 30s 打一次。
// 照 QD KlineChart.vue getRealtimePollingInterval 精神（0922 kf 流畅性调查）。
const REFRESH_MS: Record<string, number> = {
  "1m": 10_000,
  "5m": 20_000,
  "15m": 30_000,
  "30m": 30_000,
  "1h": 60_000,
  "4h": 60_000,
  "1d": 120_000,
  "1w": 120_000,
};

function getRefreshMs(interval: string): number {
  return REFRESH_MS[interval] ?? 60_000;
}

function klineTs(k: Kline): number {
  return k.timestamp ?? k.open_time ?? 0;
}

// 检测 K 线时间戳间隙,但**不造数据**。
//
// 历史(2026-10-09):本函数曾用上一根收盘价复制出最多 100 根假 bar 填平缺口 ——
// 图表看着连续,数据是假的(9.4 天真洞被 100 根占位 bar 掩盖)。现改为纯检测:
// 只在"缺口大到补不完"时返回 needsFullRefetch 让调用方清缓存重拉,数据本身
// 保持诚实稀疏。缺口在图表上的呈现(whitespace 真空隙)由 kf-chart 负责。
//
// CAP:超过此根数的缺口视为"缓存带洞"(如分区间长期未回填的中段洞),触发全量重拉。
const GAP_REFETCH_THRESHOLD = 100;

function detectOversizedGap(klines: Kline[], interval: string): boolean {
  if (klines.length < 2) return false;
  const intervalMs = getIntervalMs(interval);
  let largestGap = 0;
  for (let i = 0; i < klines.length - 1; i++) {
    const gap = klineTs(klines[i + 1]) - klineTs(klines[i]) - intervalMs;
    if (gap > largestGap) largestGap = gap;
  }
  // 缺失根数 = (两端时间差 - 一个周期) / 周期 = largestGap / intervalMs
  return largestGap / intervalMs > GAP_REFETCH_THRESHOLD;
}

export interface UseKlinesResult {
  klines: Kline[];
  fromCache: boolean;
  source: string;
  error: Error | null;
  isLoading: boolean;
  mutate: () => Promise<unknown>;
  /** 向后翻页加载更早历史（滚动到最左时触发）。返回本次新增的根数；0 表示没有更早数据。 */
  loadMore: () => Promise<number>;
}

export function useKlines(
  symbol: string | null,
  interval: string,
  exchange = "binance",
  marketType = "futures",
): UseKlinesResult {
  // symbol 为 null 时 key 为 null，SWR 不发请求
  // key 含 exchange/marketType/interval，切换时自动重新 fetch 且不污染旧 key
  const key =
    symbol && interval
      ? ["api:klines", symbol, interval, exchange, marketType]
      : null;

  // WS 实时 bar 记录：WS 注入当前（未收盘）bar 时同步写入此 ref，
  // SWR fetcher 返回后用它保护当前 bar 不被迟到的 REST（22-25s 慢）覆盖。
  // 详见 project_kf_realtime_update_regression：REST 走 Binance GFW 慢链路，
  // 返回的当前 bar OHLCV 落后 WS 实时值 20+s，SWR revalidate 会把 WS 实时值回滚。
  const wsFreshBarRef = useRef<{ ts: number; ohlcv: Kline } | null>(null);

  // WS 断流（marketsStreamState=degraded）时把 SWR 轮询收紧到 10s：
  // 正常路径 WS 逐秒推送、SWR 只是权威快照（10-120s 按 tf）；WS 断流后 SWR
  // 成为唯一更新源，按 tf 的间隔最长 120s 不可接受，统一收紧到 10s。
  // live 恢复后回到按 tf 间隔（gap 补齐由上方 recovering→live mutate 覆盖）。
  const marketsStreamStateForPoll = useMarketStore((s) => s.marketsStreamState);
  const refreshMs = marketsStreamStateForPoll === "degraded" ? 10_000 : getRefreshMs(interval);

  const { data, error, isLoading, mutate } = useSWR(
    key,
    async () => {
      // 首次：正常走 IndexedDB 缓存 + since 增量。若缓存带一个**超大**缺口
      //（如 SQLite 分区长期未回填的中段洞,> GAP_REFETCH_THRESHOLD 根),说明
      // 缓存本身脏 —— 清掉该 key 重拉一次,让后端给完整窗口。
      // 注意:小缺口不再"补平",而是保持稀疏交给图表画空白(诚实呈现)。
      //
      // 二次仍超大 = 缺口是真的(后端确实没有这段)。旧行为会再等 3s 重拉一次
      // 以"避免图断开",但那正是要消除的掩盖 —— 现在直接返回稀疏数据,让缺口
      // 如实可见(2026-10-09 Task A)。后端修补靠 L2 sweeper 周期补,不靠前端等。
      let r = await getKlineWithCache(symbol as string, interval, exchange, marketType);
      if (detectOversizedGap(r.klines, interval)) {
        await clearCachedKlines(exchange, marketType, symbol as string, interval);
        r = await getKlineWithCache(symbol as string, interval, exchange, marketType);
      }
      let filled = r.klines;
      // 保护当前 bar：REST 返回的最后一根（未收盘）bar 若与 WS 实时注入的 bar 同 timestamp，
      // 用 WS 的 OHLCV 覆盖 REST 的（WS 每 ~1s 推，REST 落后 20+s）。
      // 否则 SWR revalidate 会把 WS 实时更新的当前 bar 回滚成 REST 的旧快照，
      // 表现为 1h 等长周期 K 线"不实时更新"（1m 因 30s 内波动大不易察觉）。
      const wsFresh = wsFreshBarRef.current;
      if (wsFresh && filled.length > 0) {
        const last = filled[filled.length - 1];
        const lastTs = last.timestamp ?? last.open_time ?? 0;
        if (lastTs === wsFresh.ts) {
          filled = filled.map((k, i) =>
            i === filled.length - 1
              ? { ...k, open: wsFresh.ohlcv.open, high: wsFresh.ohlcv.high, low: wsFresh.ohlcv.low, close: wsFresh.ohlcv.close, volume: wsFresh.ohlcv.volume }
              : k,
          );
        }
      }
      return { klines: filled, fromCache: r.fromCache, source: r.source };
    },
    {
      revalidateOnFocus: false,
      dedupingInterval: refreshMs,
      refreshInterval: refreshMs,
      errorRetryCount: 3,
    },
  );

  const { mutate: globalMutate } = useSWRConfig();
  // 最新数据快照：loadMore 从 ref 读（SWR cache key 是数组，swrCache.get 只收 string，
  // 且 callback 保持稳定避免滚动监听频繁重建）
  const dataRef = useRef(data);
  useEffect(() => {
    dataRef.current = data;
  }, [data]);

  // WS 断线重连后 gap 补齐：监听 marketsStreamState 从 recovering → live 转换
  // use-markets-ws 重连成功后会把 streamState 写进 market store
  // 这里检测到 live 时立即 mutate（触发 getKlineWithCache 用 since=latestTs 增量 fetch 补缺口）
  // 不等 30s refreshInterval，让用户立即看到断线期间缺失的 K 线
  const marketsStreamState = useMarketStore((s) => s.marketsStreamState);
  const prevStreamStateRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    const prev = prevStreamStateRef.current;
    prevStreamStateRef.current = marketsStreamState;
    // 首次跳过（初始 recovering 不触发）
    if (prev === undefined) return;
    // 只在 recovering/degraded → live 转换时触发 mutate
    if (prev !== "live" && marketsStreamState === "live") {
      mutate();
    }
  }, [marketsStreamState, mutate]);

  // WS kline 实时注入：监听 market store 的 lastWsKline，乐观更新 SWR cache。
  // kf-page onMessageBatch 收到 type:"kline" 后写 store，这里消费：
  // - symbol/interval 匹配当前 key 才注入（避免污染别的 symbol/interval cache）
  // - 已有同 timestamp 的 bar → 替换 OHLCV（当前 bar 实时更新）
  // - 无匹配 → 追加（新 bar 开始）
  // - 乐观写入（revalidate=false）：不触发网络请求，避免和 30s 轮询打架
  //   下次 30s refreshInterval 会用真实后端数据覆盖，WS 只是中间实时填充
  //
  // 性能红线（kf卡vs QD 2026-09-22）：本 effect 每 ~1s 触发。当前 bar 逐秒
  // 更新不进 SWR（同引用短路）；SWR 只承接新 bar 追加（fill/sort 仅此路径）。
  const lastWsKline = useMarketStore((s) => s.lastWsKline);
  useEffect(() => {
    if (!key || !lastWsKline) return;
    const wk = lastWsKline.kline;
    // 严格匹配 interval（WS 订阅 interval 跟随 tf，切 tf 时只注入匹配的）
    if (wk.interval !== interval) return;
    // symbol 归一化校验：WS kline.symbol 形如 "BTC/USDT"，useKlines symbol 形如 "BTCUSDT"
    // 去掉斜杠/横杠后大写对比，避免格式差异误拒
    if (wk.symbol) {
      const normWs = wk.symbol.replace(/[/\-]/g, "").toUpperCase();
      const normCur = (symbol ?? "").replace(/[/\-]/g, "").toUpperCase();
      if (normWs !== normCur) return;
    }

    // 记录 WS 实时 bar，供 SWR fetcher 保护当前 bar 不被迟到的 REST 覆盖
    const wkTs = wk.timestamp ?? wk.open_time ?? 0;
    wsFreshBarRef.current = {
      ts: wkTs,
      ohlcv: { open_time: wk.open_time, timestamp: wk.timestamp, open: wk.open, high: wk.high, low: wk.low, close: wk.close, volume: wk.volume },
    };

    // 乐观更新当前 key 的 cache
    type KlineCacheData = { klines: Kline[]; fromCache: boolean; source: string };
    globalMutate(
      key,
      (cur: KlineCacheData | undefined) => {
        if (!cur) return cur;
        const ts = wk.timestamp ?? wk.open_time ?? 0;
        const existing = cur.klines;
        const last = existing[existing.length - 1];
        const lastTs = last ? klineTs(last) : 0;

        if (lastTs === ts) {
          // 当前 bar 的逐秒实时更新不进 SWR（返回同引用 → SWR 跳过通知 →
          // 订阅者不重渲染）。权威值由 REST 轮询回填 + wsFreshBarRef 防回滚；
          // 视觉层实时性由 KfChart 的 WS tick 直达路径负责（kf卡vs QD 0922）。
          return cur;
        }

        const idx = existing.findIndex(
          (k: Kline) => (k.timestamp ?? k.open_time ?? 0) === ts,
        );
        let nextKlines: Kline[];
        if (idx >= 0) {
          // 中段替换同 timestamp bar 的 OHLCV（罕见：REST 补洞后乱序）
          nextKlines = existing.map((k: Kline, i: number) =>
            i === idx
              ? {
                  ...k,
                  open: wk.open,
                  high: wk.high,
                  low: wk.low,
                  close: wk.close,
                  volume: wk.volume,
                }
              : k,
          );
        } else if (ts > lastTs) {
          // 新 bar：尾部追加（时间轴天然有序，免 sort）。
          // 不在 WS 路径补洞 —— 缺口的空白呈现由图表负责；此处只如实追加真实 bar。
          nextKlines = [...existing, {
            open_time: wk.open_time,
            timestamp: wk.timestamp,
            open: wk.open,
            high: wk.high,
            low: wk.low,
            close: wk.close,
            volume: wk.volume,
          }];
        } else {
          // 乱序旧消息兜底：合并重排（最罕见路径）
          nextKlines = [...existing, {
            open_time: wk.open_time,
            timestamp: wk.timestamp,
            open: wk.open,
            high: wk.high,
            low: wk.low,
            close: wk.close,
            volume: wk.volume,
          }].sort(
            (a, b) =>
              (a.timestamp ?? a.open_time ?? 0) -
              (b.timestamp ?? b.open_time ?? 0),
          );
        }
        return { ...cur, klines: nextKlines };
      },
      false, // revalidate=false：纯乐观更新，不触发网络请求
    );
  }, [lastWsKline, key, interval, globalMutate]);

  // 向后翻页加载更早历史（滚动到最左时触发）
  const loadMore = useCallback(async () => {
    if (!symbol || !interval || !key) return 0;
    const cur = dataRef.current as
      | { klines: Kline[]; fromCache: boolean; source: string }
      | undefined;
    if (!cur || cur.klines.length === 0) return 0;
    const oldest = cur.klines[0];
    const oldestTs = klineTs(oldest);
    if (!oldestTs) return 0;
    // end_time 拉 oldestTs 之前的更早一批（后端原样透传 end_time 走 REST）
    const r = await getKlineWithCache(symbol, interval, exchange, marketType, {
      endTime: oldestTs,
    });
    if (!r || r.klines.length === 0) return 0;
    // 只取真正更早的新增项合并进 SWR cache，避免旧值覆盖 WS 注入的当前 bar
    const map = new Map<number, Kline>();
    for (const k of cur.klines) map.set(klineTs(k), k);
    let added = 0;
    for (const k of r.klines) {
      const ts = klineTs(k);
      if (ts < oldestTs && !map.has(ts)) {
        map.set(ts, k);
        added++;
      }
    }
    if (added === 0) return 0;
    const merged = Array.from(map.values()).sort((a, b) => klineTs(a) - klineTs(b));
    await globalMutate(
      key,
      { ...cur, klines: merged },
      false, // revalidate=false：历史追加纯本地合并，不触发网络
    );
    return added;
  }, [symbol, interval, exchange, marketType, key, globalMutate, dataRef]);

  return {
    klines: data?.klines ?? [],
    fromCache: data?.fromCache ?? false,
    source: data?.source ?? "exchange",
    error: error instanceof Error ? error : error ? new Error(String(error)) : null,
    isLoading,
    mutate,
    loadMore,
  };
}
