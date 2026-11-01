import { describe, it, expect } from "vitest";
import {
  calculateSMA,
  calculateEMA,
  calculateBollingerBands,
  calculateRSI,
  calculateMACD,
  toSeriesData,
} from "./indicator-chart";

// 行为锁: 断言指标算法的输入→输出契约(确定性数学)。
// cleanroom 重写必须保持这些数值; 撤掉实现改动 → 本文件必红。

describe("calculateSMA", () => {
  it("前 period-1 位为 null, 其后为均值", () => {
    expect(calculateSMA([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
  });
  it("数据不足 → 全 null", () => {
    expect(calculateSMA([1, 2], 3)).toEqual([null, null]);
  });
  it("period=1 → 原值", () => {
    expect(calculateSMA([1, 2, 3], 1)).toEqual([1, 2, 3]);
  });
});

describe("calculateEMA", () => {
  it("首个有效位是前 period 的 SMA, 之后按 2/(period+1) 递推", () => {
    // multiplier=0.5: i=2→(1+2+3)/3=2; i=3→(4-2)*0.5+2=3; i=4→(5-3)*0.5+3=4
    expect(calculateEMA([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
  });
  it("前 period-1 位 null", () => {
    expect(calculateEMA([1, 2], 3)).toEqual([null, null]);
  });
});

describe("calculateBollingerBands", () => {
  it("middle=SMA; upper/lower = mean ± stdDev*样本标准差(总体方差)", () => {
    const { upper, middle, lower } = calculateBollingerBands([1, 2, 3, 4, 5], 3, 2);
    expect(middle).toEqual([null, null, 2, 3, 4]);
    // i=2: slice=[1,2,3], 方差=(1+0+1)/3, std=sqrt(2/3)≈0.8165
    expect(upper[2]).toBeCloseTo(2 + 2 * Math.sqrt(2 / 3), 6);
    expect(lower[2]).toBeCloseTo(2 - 2 * Math.sqrt(2 / 3), 6);
  });
  it("前 period-1 位三线皆 null", () => {
    const { upper, middle, lower } = calculateBollingerBands([1, 2, 3], 3, 2);
    expect([upper[0], middle[0], lower[0]]).toEqual([null, null, null]);
  });
});

describe("calculateRSI", () => {
  it("单调上涨 → avgLoss=0 → 100", () => {
    const data = Array.from({ length: 20 }, (_, i) => i + 1);
    const rsi = calculateRSI(data, 14);
    expect(rsi[0]).toBeNull();
    expect(rsi[14]).toBe(100);
    expect(rsi[19]).toBe(100);
  });
  it("单调下跌 → avgGain=0 → 0", () => {
    const data = Array.from({ length: 20 }, (_, i) => 100 - i);
    const rsi = calculateRSI(data, 14);
    expect(rsi[14]).toBe(0);
  });
  it("i<period 为 null (i>0)", () => {
    const data = Array.from({ length: 20 }, (_, i) => i + 1);
    const rsi = calculateRSI(data, 14);
    for (let i = 1; i < 14; i++) expect(rsi[i]).toBeNull();
  });
});

describe("calculateMACD", () => {
  it("常量序列 → 快慢 EMA 相等 → macd=0, signal=0, histogram=0", () => {
    const data = Array(40).fill(5);
    const { macd, signal, histogram } = calculateMACD(data, 12, 26, 9);
    expect(macd[25]).toBe(0);
    expect(macd[39]).toBe(0);
    expect(histogram[39]).toBe(0);
    expect(signal[39]).toBe(0);
  });
  it("macd 的 null 段与慢 EMA 对齐(前 slowPeriod-1 为 null)", () => {
    const data = Array.from({ length: 40 }, (_, i) => i + 1);
    const { macd } = calculateMACD(data, 12, 26, 9);
    expect(macd[24]).toBeNull();
    expect(macd[25]).not.toBeNull();
  });
});

describe("toSeriesData", () => {
  it("配对时间与值, 丢空值", () => {
    expect(toSeriesData([1, 2, 3], [10, null, 30])).toEqual([
      { time: 1, value: 10 },
      { time: 3, value: 30 },
    ]);
  });
  it("undefined 值也丢", () => {
    expect(toSeriesData([1, 2], [5, undefined as never])).toEqual([{ time: 1, value: 5 }]);
  });
  it("全有效 → 全保留", () => {
    expect(toSeriesData([1, 2], [0, 0])).toEqual([{ time: 1, value: 0 }, { time: 2, value: 0 }]);
  });
});
