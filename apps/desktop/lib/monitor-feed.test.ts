import { describe, expect, it } from "vitest";
import {
  adaptAnomaly,
  adaptFundingRate,
  adaptLargeTrade,
  adaptLiquidation,
  adaptSignal,
  FEED_CAP,
  FEED_SUBSCRIPTIONS,
  filterByTab,
  filterFeed,
  makeSampleItems,
  mergeFeedItem,
  resolveMinUsd,
  type FeedDraft,
  type FeedItem,
} from "./monitor-feed";

const NOW = new Date(2026, 0, 1, 12, 34).getTime();

describe("adaptLiquidation", () => {
  it("做多爆单 → 红项文案", () => {
    const d = adaptLiquidation({ symbol: "BTC", side: "long", notional_usd: 2_500_000, price: 61230 });
    expect(d.kind).toBe("liquidation");
    expect(d.dir).toBe("long");
    expect(d.text).toBe("爆单 BTC 多 $2.50M @61230");
    expect(d.symbol).toBe("BTC");
    expect(d.usd).toBe(2_500_000);
  });

  it("做空爆单 + 无价格 → 不渲染 @", () => {
    const d = adaptLiquidation({ symbol: "ETH/USDT", side: "short", notional_usd: 980_000 });
    expect(d.dir).toBe("short");
    expect(d.text).toBe("爆单 ETH 空 $980.0K");
    expect(d.text).not.toContain("@");
  });

  it("缺 symbol/notional → 兜底不崩", () => {
    const d = adaptLiquidation({});
    expect(d.text).toBe("爆单 ? 空 $0");
  });
});

describe("adaptLargeTrade", () => {
  it("主力买 → 绿", () => {
    const d = adaptLargeTrade({ symbol: "SOL", side: "buy", notional_usd: 1_200_000 });
    expect(d.kind).toBe("large_trade");
    expect(d.dir).toBe("buy");
    expect(d.text).toBe("主力 买 SOL $1.20M");
    expect(d.symbol).toBe("SOL");
    expect(d.usd).toBe(1_200_000);
  });

  it("主力卖 → 红", () => {
    const d = adaptLargeTrade({ symbol: "XRP", side: "sell", notional_usd: 540_000 });
    expect(d.dir).toBe("sell");
    expect(d.text).toBe("主力 卖 XRP $540.0K");
  });
});

describe("adaptSignal", () => {
  it("买入信号", () => {
    const d = adaptSignal({ symbol: "DOGE", side: "buy", strength: 78 });
    expect(d.kind).toBe("anomaly");
    expect(d.dir).toBe("buy");
    expect(d.text).toBe("买入信号 DOGE ★ 78");
  });

  it("卖出信号", () => {
    const d = adaptSignal({ symbol: "ADA", side: "sell", strength: 62 });
    expect(d.dir).toBe("sell");
    expect(d.text).toBe("卖出信号 ADA ★ 62");
  });

  it("中性", () => {
    const d = adaptSignal({ symbol: "DOT", side: "neutral", strength: 30 });
    expect(d.dir).toBe("neutral");
    expect(d.text).toBe("DOT 信号强度 30");
  });
});

describe("adaptFundingRate", () => {
  it("正费率 → buy 方向", () => {
    const d = adaptFundingRate({ symbol: "BNB", rate: 0.00021 });
    expect(d.kind).toBe("anomaly");
    expect(d.dir).toBe("buy");
    expect(d.text).toBe("BNB 资金费率 +0.0210%");
  });

  it("负费率 → sell 方向,兼容 funding_rate 字段", () => {
    const d = adaptFundingRate({ symbol: "LTC", funding_rate: -0.0005 });
    expect(d.dir).toBe("sell");
    expect(d.text).toBe("LTC 资金费率 -0.0500%");
  });
});

describe("FEED_SUBSCRIPTIONS", () => {
  it("含 A 股异动频道 astock/intraday,且 market_type 与检测器一致", () => {
    expect(FEED_SUBSCRIPTIONS).toContainEqual({ exchange: "astock", market_type: "intraday" });
    // 加密所仍是 futures —— 防止误改成 spot/intraday 断掉现有订阅
    expect(FEED_SUBSCRIPTIONS).toContainEqual({ exchange: "binance", market_type: "futures" });
    expect(FEED_SUBSCRIPTIONS).toContainEqual({ exchange: "okx", market_type: "futures" });
  });
});

describe("adaptAnomaly", () => {
  it("涨停 → 绿向,渲染代码+名称+价+涨幅", () => {
    const d = adaptAnomaly({
      exchange: "astock",
      symbol: "600519.SH",
      name: "贵州茅台",
      kind: "limit_up",
      price: 1786.5,
      prev_close: 1624.09,
    });
    expect(d.kind).toBe("anomaly");
    expect(d.dir).toBe("buy");
    expect(d.symbol).toBe("600519");
    expect(d.text).toBe("涨停 600519 贵州茅台 @1786.50 +10.00%");
  });

  it("跌停 → 红向", () => {
    const d = adaptAnomaly({ symbol: "000001.SZ", kind: "limit_down", price: 9.0, prev_close: 10.0 });
    expect(d.dir).toBe("sell");
    expect(d.text).toBe("跌停 000001 @9.00 -10.00%");
  });

  it("炸板 → 红向,翘板 → 绿向", () => {
    expect(adaptAnomaly({ symbol: "600000.SH", kind: "broken_limit_up", price: 9.9 }).dir).toBe("sell");
    expect(adaptAnomaly({ symbol: "600000.SH", kind: "limit_down_recovery", price: 9.9 }).dir).toBe("buy");
  });

  it("放量 → 中性(N 日新高/新低同理有专属标签)", () => {
    const v = adaptAnomaly({ symbol: "300750.SZ", kind: "volume_surge", price: 200 });
    expect(v.dir).toBe("neutral");
    expect(v.text).toBe("放量 300750 @200.00");
    expect(adaptAnomaly({ symbol: "600519.SH", kind: "n_day_high", price: 1800 }).text).toContain("N日新高");
    expect(adaptAnomaly({ symbol: "600519.SH", kind: "n_day_low", price: 1500 }).text).toContain("N日新低");
  });

  it("无 prev_close 不渲染涨幅;未知 kind 兜底为原样标签,不崩", () => {
    const noPrev = adaptAnomaly({ symbol: "600519.SH", kind: "limit_up", price: 11.0 });
    expect(noPrev.text).toBe("涨停 600519 @11.00");
    expect(noPrev.text).not.toContain("%");

    const unknown = adaptAnomaly({ symbol: "600519.SH", kind: "weird_future_kind", price: 11.0 });
    expect(unknown.text).toBe("weird_future_kind 600519 @11.00");
    expect(unknown.dir).toBe("neutral");

    const empty = adaptAnomaly({});
    expect(empty.symbol).toBe("?");
    expect(empty.dir).toBe("neutral");
  });
});

describe("mergeFeedItem", () => {
  const draft = (kind: FeedDraft["kind"], seq: number): FeedDraft => ({
    kind,
    text: `${kind}-${seq}`,
    dir: "neutral",
  });

  it("新条目在前,时间已盖章", () => {
    const a = mergeFeedItem([], draft("liquidation", 1), 1, NOW);
    const b = mergeFeedItem(a, draft("large_trade", 2), 2, NOW);
    expect(b.map((f) => f.kind)).toEqual(["large_trade", "liquidation"]);
    expect(b[0].id).toBe("large_trade-2");
    expect(b[0].time).toBe("12:34");
  });

  it("同 id 去重", () => {
    const a = mergeFeedItem([], draft("liquidation", 1), 1, NOW);
    const b = mergeFeedItem(a, draft("liquidation", 1), 1, NOW);
    expect(b).toHaveLength(1);
  });

  it("超出 FEED_CAP 截断旧条目", () => {
    let acc: FeedItem[] = [];
    for (let i = 1; i <= FEED_CAP + 10; i += 1) {
      acc = mergeFeedItem(acc, draft("anomaly", i), i, NOW);
    }
    expect(acc).toHaveLength(FEED_CAP);
    expect(acc[0].id).toBe(`anomaly-${FEED_CAP + 10}`);
  });
});

describe("filterByTab", () => {
  const items: FeedItem[] = [
    { id: "l1", kind: "liquidation", text: "a", dir: "long", time: "12:00" },
    { id: "t1", kind: "large_trade", text: "b", dir: "buy", time: "12:01" },
    { id: "n1", kind: "anomaly", text: "c", dir: "neutral", time: "12:02" },
  ];

  it("全部 = 并集", () => {
    expect(filterByTab(items, "all")).toHaveLength(3);
  });

  it("分类过滤", () => {
    expect(filterByTab(items, "liquidation").map((f) => f.id)).toEqual(["l1"]);
    expect(filterByTab(items, "large_trade").map((f) => f.id)).toEqual(["t1"]);
    expect(filterByTab(items, "anomaly").map((f) => f.id)).toEqual(["n1"]);
  });
});

describe("filterFeed", () => {
  const items: FeedItem[] = [
    { id: "l1", kind: "liquidation", text: "爆单 ETH 多 $2.50M", dir: "long", symbol: "ETH", usd: 2_500_000, time: "12:00" },
    { id: "l2", kind: "liquidation", text: "爆单 SOL 空 $90K", dir: "short", symbol: "SOL", usd: 90_000, time: "12:01" },
    { id: "t1", kind: "large_trade", text: "主力 买 BTC $1.20M", dir: "buy", symbol: "BTC", usd: 1_200_000, time: "12:02" },
    { id: "t2", kind: "large_trade", text: "主力 卖 BTC $300K", dir: "sell", symbol: "BTC", usd: 300_000, time: "12:03" },
    { id: "n1", kind: "anomaly", text: "BTC 资金费率 +0.02%", dir: "buy", symbol: "BTC", time: "12:04" },
  ];

  it("无过滤 = 全量", () => {
    expect(filterFeed(items)).toHaveLength(5);
    expect(filterFeed(items, {})).toHaveLength(5);
  });

  it("minUsd 只砍有金额的类别,anomaly 恒通过", () => {
    const r = filterFeed(items, { minUsd: 500_000 });
    expect(r.map((f) => f.id)).toEqual(["l1", "t1", "n1"]);
  });

  it("minUsd 更大档位只留 whale", () => {
    const r = filterFeed(items, { minUsd: 1_000_000 });
    expect(r.map((f) => f.id)).toEqual(["l1", "t1", "n1"]);
  });

  it("dir=buy 同时收 buy 与 long", () => {
    const r = filterFeed(items, { dir: "buy" });
    expect(r.map((f) => f.id)).toEqual(["l1", "t1", "n1"]);
  });

  it("dir=sell 同时收 sell 与 short", () => {
    const r = filterFeed(items, { dir: "sell" });
    expect(r.map((f) => f.id)).toEqual(["l2", "t2"]);
  });

  it("symbol 大小写不敏感", () => {
    expect(filterFeed(items, { symbol: "btc" }).map((f) => f.id)).toEqual(["t1", "t2", "n1"]);
    expect(filterFeed(items, { symbol: "ETH" }).map((f) => f.id)).toEqual(["l1"]);
  });

  it("组合:tab + minUsd + dir + symbol 叠加", () => {
    const r = filterFeed(items, { tab: "large_trade", minUsd: 1_000_000, dir: "buy", symbol: "BTC" });
    expect(r.map((f) => f.id)).toEqual(["t1"]);
  });

  it("无匹配 → 空数组", () => {
    expect(filterFeed(items, { symbol: "DOGE" })).toEqual([]);
  });
});

describe("resolveMinUsd", () => {
  it("预设档位 → 对应阈值", () => {
    expect(resolveMinUsd("all", "")).toBe(0);
    expect(resolveMinUsd("100k", "")).toBe(100_000);
    expect(resolveMinUsd("500k", "")).toBe(500_000);
    expect(resolveMinUsd("1m", "")).toBe(1_000_000);
  });

  it("custom 档 → 取输入值;空/非法回退 0", () => {
    expect(resolveMinUsd("custom", "250000")).toBe(250_000);
    expect(resolveMinUsd("custom", "0")).toBe(0);
    expect(resolveMinUsd("custom", "")).toBe(0);
    expect(resolveMinUsd("custom", "abc")).toBe(0);
  });

  it("未知 key → 0", () => {
    expect(resolveMinUsd("whatever", "")).toBe(0);
  });
});

describe("makeSampleItems", () => {
  it("确定性输出:8 条、覆盖 3 类、demo 前缀、时间按秒递减", () => {
    const a = makeSampleItems(NOW);
    const b = makeSampleItems(NOW);
    expect(a).toEqual(b);
    expect(a).toHaveLength(8);
    const kinds = new Set(a.map((f) => f.kind));
    expect(kinds).toEqual(new Set(["liquidation", "large_trade", "anomaly"]));
    expect(a.every((f) => f.id.startsWith("demo-"))).toBe(true);
    expect(a[0].time).toBe("12:33");
  });
});
