import { describe, it, expect } from "vitest";
import { aggregateEvidence, evidenceLineText } from "@/lib/hooks/use-strategy-evidence";

const DAY = 86400;

describe("aggregateEvidence", () => {
  it("只认 completed 行,按 strategy_id 归约 count/bestSharpe/lastDate", () => {
    const map = aggregateEvidence([
      { status: "completed", strategy_id: "bb_v3", created_at: 10 * DAY, sharpe_ratio: 1.2 },
      { status: "completed", strategy_id: "bb_v3", created_at: 12 * DAY, sharpe_ratio: 1.9 },
      { status: "failed", strategy_id: "bb_v3", created_at: 13 * DAY, sharpe_ratio: 9.9 },
      { status: "running", strategy_id: "bb_v3", created_at: 14 * DAY },
      { status: "completed", strategy_id: "rsi", created_at: 5 * DAY, sharpe_ratio: -0.3 },
      { status: "completed", created_at: 5 * DAY, sharpe_ratio: 1.0 }, // 无 strategy_id → 忽略
    ]);
    const bb = map.get("bb_v3")!;
    expect(bb.count).toBe(2);
    expect(bb.bestSharpe).toBe(1.9);
    expect(bb.lastEpoch).toBe(12 * DAY);
    expect(map.get("rsi")!.count).toBe(1);
    expect(map.get("rsi")!.bestSharpe).toBe(-0.3);
    expect(map.size).toBe(2);
  });

  it("null/空数组 → 空 Map(未验证态),completed 无 sharpe 不污染 bestSharpe", () => {
    expect(aggregateEvidence(null).size).toBe(0);
    const map = aggregateEvidence([{ status: "completed", strategy_id: "x", created_at: DAY }]);
    expect(map.get("x")!.count).toBe(1);
    expect(map.get("x")!.bestSharpe).toBeNull();
  });
});

describe("evidenceLineText", () => {
  it("有证据 = N 次回测 · sharpe · MM-DD;无 sharpe 省略段", () => {
    expect(evidenceLineText({ count: 3, bestSharpe: 1.83, lastDate: "10-02", lastEpoch: 1 }))
      .toBe("3 次回测 · sharpe 1.83 · 10-02");
    expect(evidenceLineText({ count: 1, bestSharpe: null, lastDate: null, lastEpoch: null }))
      .toBe("1 次回测");
  });

  it("未验证 = 诚实空态", () => {
    expect(evidenceLineText(null)).toBe("未验证");
    expect(evidenceLineText(undefined)).toBe("未验证");
    expect(evidenceLineText({ count: 0, bestSharpe: null, lastDate: null, lastEpoch: null }))
      .toBe("未验证");
  });
});
