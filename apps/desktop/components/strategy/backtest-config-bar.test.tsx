import { describe, it, expect } from "vitest";
import { mergeBacktestConfig, type BacktestConfig } from "./backtest-config-bar";

// 行为锁: backtest-config-bar 的配置合并契约。重写必须保持; 撤实现 → 必红。

const DEFAULTS: BacktestConfig = {
  startTime: "2024-01-01",
  endTime: "2024-12-31",
  symbol: "BTC/USDT",
  period: "daily",
  benchmark: "BTC/USDT",
  initCash: 10000,
  stake: 100,
  slippage: 0.1,
  commission: 0.05,
  stampDuty: 0.1,
  exchange: "binance",
  marketType: "spot",
};

describe("mergeBacktestConfig", () => {
  it("空覆盖 → 等同默认", () => {
    expect(mergeBacktestConfig(DEFAULTS, {})).toEqual(DEFAULTS);
  });
  it("用户值优先", () => {
    const out = mergeBacktestConfig(DEFAULTS, { symbol: "ETH/USDT", initCash: 5000 });
    expect(out.symbol).toBe("ETH/USDT");
    expect(out.initCash).toBe(5000);
    expect(out.period).toBe("daily"); // 未覆盖的保持默认
  });
  it("不修改输入对象(不可变)", () => {
    const defaults = { ...DEFAULTS };
    const config = { symbol: "SOL/USDT" };
    mergeBacktestConfig(defaults, config);
    expect(defaults.symbol).toBe("BTC/USDT");
    expect(config).toEqual({ symbol: "SOL/USDT" });
  });
  it("返回新对象", () => {
    expect(mergeBacktestConfig(DEFAULTS, {})).not.toBe(DEFAULTS);
  });
});
