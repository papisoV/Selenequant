import { describe, it, expect } from "vitest";
import { buildKillzoneBands } from "@/lib/chart/killzone-primitive";
import type { KillzoneBarPoint } from "@/lib/chart/ict-structure-overlay";
import type { UTCTimestamp } from "lightweight-charts";

const pt = (t: number, value: 0 | 1): KillzoneBarPoint => ({
  time: t as UTCTimestamp,
  value,
  color: value === 1 ? "#fff" : "#000",
});

const seq = (...flags: (0 | 1)[]): KillzoneBarPoint[] =>
  flags.map((v, i) => pt(1000 + i * 3600, v));

describe("buildKillzoneBands", () => {
  it("连续 1 合成一段，首尾就是该段首末 bar 的时间", () => {
    const bands = buildKillzoneBands(seq(0, 1, 1, 1, 0));
    expect(bands).toEqual([{ from: 1000 + 3600, to: 1000 + 3 * 3600 }]);
  });

  it("中间只要有 0 就断成两段", () => {
    const bands = buildKillzoneBands(seq(1, 1, 0, 1));
    expect(bands).toEqual([
      { from: 1000, to: 1000 + 3600 },
      { from: 1000 + 3 * 3600, to: 1000 + 3 * 3600 },
    ]);
  });

  it("单个 1 退化成零宽段（from == to），由渲染端加半个 bar 宽兜底", () => {
    expect(buildKillzoneBands(seq(0, 1, 0))).toEqual([
      { from: 1000 + 3600, to: 1000 + 3600 },
    ]);
  });

  it("末尾仍在时段内时，最后一段要收口而不是丢掉", () => {
    expect(buildKillzoneBands(seq(0, 1, 1))).toEqual([
      { from: 1000 + 3600, to: 1000 + 2 * 3600 },
    ]);
  });

  it("全 0 或空输入 → 无段", () => {
    expect(buildKillzoneBands([])).toEqual([]);
    expect(buildKillzoneBands(seq(0, 0, 0))).toEqual([]);
  });

  it("时间非有限的 bar 被跳过，不产生 NaN 段", () => {
    const pts: KillzoneBarPoint[] = [
      { time: NaN as unknown as UTCTimestamp, value: 1, color: "#fff" },
      pt(5000, 1),
      pt(8600, 0),
    ];
    expect(buildKillzoneBands(pts)).toEqual([{ from: 5000, to: 5000 }]);
  });
});
