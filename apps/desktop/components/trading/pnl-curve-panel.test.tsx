import { describe, it, expect } from "vitest";
import { mergeEquityHistory, buildCurvePaths, type PaperEquityPoint } from "./pnl-curve-panel";

// 行为锁: pnl-curve-panel 的两个纯函数契约(合并权益序列 / 生成 SVG 路径)。
// cleanroom 重写必须保持; 撤掉实现改动 → 本文件必红。

const pt = (ts: string, eq: number): PaperEquityPoint => ({ timestamp: ts, equity: eq });

describe("mergeEquityHistory", () => {
  it("无 WS 时 = REST 历史", () => {
    const rest = [pt("2024-01-01T00:00:00Z", 100)];
    expect(mergeEquityHistory(rest, undefined, [], undefined, "NOW")).toEqual(rest);
  });
  it("REST 空时 = WS", () => {
    const ws = [pt("2024-01-01T00:00:00Z", 100)];
    expect(mergeEquityHistory([], ws, [], undefined, "NOW")).toEqual(ws);
  });
  it("WS 只追加时间戳晚于 REST 末点的", () => {
    const rest = [pt("2024-01-01T00:00:00Z", 100)];
    const ws = [pt("2023-12-31T00:00:00Z", 50), pt("2024-01-02T00:00:00Z", 200)];
    expect(mergeEquityHistory(rest, ws, [], undefined, "NOW")).toEqual([
      rest[0],
      ws[1],
    ]);
  });
  it("WS 无更新点 → 保持 REST", () => {
    const rest = [pt("2024-01-02T00:00:00Z", 100)];
    const ws = [pt("2024-01-01T00:00:00Z", 50)];
    expect(mergeEquityHistory(rest, ws, [], undefined, "NOW")).toEqual(rest);
  });
  it("liveSamples 晚于末点才追加", () => {
    const rest = [pt("2024-01-02T00:00:00Z", 100)];
    const samples = [pt("2024-01-01T00:00:00Z", 1), pt("2024-01-03T00:00:00Z", 2)];
    const out = mergeEquityHistory(rest, undefined, samples, undefined, "NOW");
    expect(out.map((p) => p.equity)).toEqual([100, 2]);
  });
  it("liveEquity 追加为末点(timestamp=nowIso)", () => {
    const out = mergeEquityHistory([], undefined, [], 999, "2024-05-05T00:00:00Z");
    expect(out).toEqual([pt("2024-05-05T00:00:00Z", 999)]);
  });
  it("只保留末 200 点", () => {
    const rest = Array.from({ length: 250 }, (_, i) => pt(`t${String(i).padStart(4, "0")}`, i));
    const out = mergeEquityHistory(rest, undefined, [], undefined, "NOW");
    expect(out).toHaveLength(200);
    expect(out[0].equity).toBe(50); // 砍掉前 50 个
    expect(out[199].equity).toBe(249);
  });
});

describe("buildCurvePaths", () => {
  it("首点 M, 后续 L; 末点 x=w-padX", () => {
    const { path } = buildCurvePaths([1, 2, 3], 300, 100, 5, 10);
    const segs = path.split(" ");
    expect(segs[0]).toBe("M");
    expect(segs[3]).toBe("L");
    // 末点 x = 5 + (2/2)*(300-10) = 295
    expect(path).toContain("295.0");
  });
  it("isProfit: 末>=首", () => {
    expect(buildCurvePaths([1, 2, 3]).isProfit).toBe(true);
    expect(buildCurvePaths([3, 2, 1]).isProfit).toBe(false);
    expect(buildCurvePaths([2, 2, 2]).isProfit).toBe(true);
  });
  it("平线(range=0)不除零, 有合法 n 坐标", () => {
    const { path } = buildCurvePaths([5, 5, 5], 300, 100, 5, 10);
    expect(path).not.toContain("NaN");
    expect(path).not.toContain("Infinity");
  });
  it("areaPath 以 Z 收尾且含 path", () => {
    const { path, areaPath } = buildCurvePaths([1, 2, 3]);
    expect(areaPath.startsWith(path)).toBe(true);
    expect(areaPath.endsWith("Z")).toBe(true);
  });
});
