import { describe, it, expect } from "vitest";
import { computeHoverInfo, type DisplayRow } from "./orderbook-panel";

// 行为锁: orderbook-panel 悬停信息计算契约。重写必须保持; 撤实现 → 必红。

const row = (
  price: number,
  qty: number,
  levelUsdt: number,
  cumUsdt: number,
  cumQty: number,
): DisplayRow => ({ price, qty, levelUsdt, cumUsdt, cumQty });

const ASKS = [
  row(101, 2, 202, 202, 2),
  row(102, 3, 306, 508, 5),
  row(103, 4, 412, 920, 9),
];
const BIDS = [
  row(100, 1, 100, 100, 1),
  row(99, 2, 198, 298, 3),
];

describe("computeHoverInfo", () => {
  it("null hover → null", () => {
    expect(computeHoverInfo(null, ASKS, BIDS)).toBeNull();
  });

  it("ask: 从该档累加到末档, 均价 = cumUsdt/cumQty", () => {
    const out = computeHoverInfo({ side: "ask", displayIndex: 1 }, ASKS, BIDS)!;
    // 从 idx1 起: levelUsdt 306+412=718; qty 3+4=7 → avg=718/7
    expect(out.cumUsdt).toBe(718);
    expect(out.levelUsdt).toBe(306);
    expect(out.avgPrice).toBeCloseTo(718 / 7, 6);
  });

  it("ask: 越界 index → null", () => {
    expect(computeHoverInfo({ side: "ask", displayIndex: 9 }, ASKS, BIDS)).toBeNull();
  });

  it("bid: 用该档已算好的累计", () => {
    const out = computeHoverInfo({ side: "bid", displayIndex: 1 }, ASKS, BIDS)!;
    expect(out.cumUsdt).toBe(298);
    expect(out.levelUsdt).toBe(198);
    expect(out.avgPrice).toBeCloseTo(298 / 3, 6);
  });

  it("bid: 越界 → null", () => {
    expect(computeHoverInfo({ side: "bid", displayIndex: 9 }, ASKS, BIDS)).toBeNull();
  });
});
