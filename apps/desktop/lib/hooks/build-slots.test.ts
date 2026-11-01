import { describe, it, expect } from "vitest";
import { buildSlots, gapMissing, findRowGaps } from "@/components/watch/kf-chart";
import type { KfRow } from "@/components/watch/kf-chart";
import type { UTCTimestamp } from "lightweight-charts";

// Task A: 缺口必须变可见(撑开),而非被压缩。
// 2026-10-09 实测背景:lightweight-charts 按数据点索引排布,不按时间 —— 纯稀疏数据
// 会把洞压成 1 根宽。故必须插 whitespace 槽。这些测试守 buildSlots 的契约。

const H = 3600; // 秒(rows[].time 是 epoch 秒)
const T0 = 1_720_000_000;

function row(time: number, close = 100): KfRow & { time: UTCTimestamp } {
  return { time: time as UTCTimestamp, open: close, high: close, low: close, close };
}

describe("buildSlots — 缺口撑开", () => {
  it("连续数据 → 无空白槽,每槽都有 row", () => {
    const rows = [row(T0), row(T0 + H), row(T0 + 2 * H)];
    const slots = buildSlots(rows, H);
    expect(slots.length).toBe(3);
    expect(slots.every((s) => s.row !== null)).toBe(true);
  });

  it("★跨洞 → 洞内每个缺失时刻补一个空白槽(row=null)", () => {
    // 00:00, 01:00, [跳 02..05], 06:00 → 中间应有 4 个空白槽
    const rows = [row(T0), row(T0 + H), row(T0 + 6 * H)];
    const slots = buildSlots(rows, H);
    expect(slots.length).toBe(7); // 3 real + 4 whitespace
    const blanks = slots.filter((s) => s.row === null);
    expect(blanks.length).toBe(4);
    expect(blanks.map((s) => s.time)).toEqual([
      T0 + 2 * H, T0 + 3 * H, T0 + 4 * H, T0 + 5 * H,
    ] as never);
    // 首尾仍真实
    expect(slots[0].row).not.toBeNull();
    expect(slots[slots.length - 1].row).not.toBeNull();
  });

  it("空白槽不携带 OHLCV(不造假价格)", () => {
    const rows = [row(T0), row(T0 + 6 * H)];
    const slots = buildSlots(rows, H);
    for (const s of slots) {
      if (s.row === null) {
        expect(Object.keys(s).sort()).toEqual(["row", "time"]);
      }
    }
  });

  it("9.4 天大洞(5m)= 2712 空白槽,按周期铺满", () => {
    const step5m = 300; // 秒
    const rows = [row(T0), row(T0 + 2713 * step5m)]; // 缺 2712 根
    const slots = buildSlots(rows, step5m);
    const blanks = slots.filter((s) => s.row === null).length;
    expect(blanks).toBe(2712);
  });

  it("周期未知(NaN)→ 原样返回,不留空(宁可不画也不造错位)", () => {
    const rows = [row(T0), row(T0 + H), row(T0 + 6 * H)];
    const slots = buildSlots(rows, NaN);
    expect(slots.length).toBe(3);
    expect(slots.every((s) => s.row !== null)).toBe(true);
  });

  it("容差:1.5× 内的抖动不算缺口(非整倍周期不误判)", () => {
    const rows = [row(T0), row(T0 + Math.floor(H * 1.4))];
    const slots = buildSlots(rows, H);
    expect(slots.filter((s) => s.row === null).length).toBe(0);
  });

  it("单根/空 → 不崩", () => {
    expect(buildSlots([], H)).toEqual([]);
    expect(buildSlots([row(T0)], H).length).toBe(1);
  });
});

describe("gapMissing / findRowGaps — 缺口计数(供提示条)", () => {
  it("相邻根 → 0", () => {
    expect(gapMissing(T0, T0 + H, H)).toBe(0);
  });

  it("1.5× 容差内 → 0(抖动不算)", () => {
    expect(gapMissing(T0, T0 + Math.floor(H * 1.4), H)).toBe(0);
  });

  it("★跨 6 根 → 5(缺失根数,不是时间差)", () => {
    // 00:00 → 06:00,1h:缺 01,02,03,04,05 = 5 根
    expect(gapMissing(T0, T0 + 6 * H, H)).toBe(5);
  });

  it("非法周期 → 0(不猜)", () => {
    expect(gapMissing(T0, T0 + 6 * H, NaN)).toBe(0);
    expect(gapMissing(T0, T0 + 6 * H, 0)).toBe(0);
  });

  it("findRowGaps 汇总每处缺口 + 缺失总数", () => {
    // 三根:00, 06(缺5), 08(缺1)
    const rows = [row(T0), row(T0 + 6 * H), row(T0 + 8 * H)];
    const gaps = findRowGaps(rows, H);
    expect(gaps.length).toBe(2);
    expect(gaps[0]).toEqual({ from: T0, to: T0 + 6 * H, missing: 5 });
    expect(gaps[1]).toEqual({ from: T0 + 6 * H, to: T0 + 8 * H, missing: 1 });
    expect(gaps.reduce((a, g) => a + g.missing, 0)).toBe(6);
  });

  it("连续数据 → 空数组(提示条不显示)", () => {
    const rows = [row(T0), row(T0 + H), row(T0 + 2 * H)];
    expect(findRowGaps(rows, H)).toEqual([]);
  });

  it("★buildSlots 与 findRowGaps 保持一致(同缺口同计数)", () => {
    const rows = [row(T0), row(T0 + 6 * H), row(T0 + 8 * H)];
    const slots = buildSlots(rows, H);
    const gaps = findRowGaps(rows, H);
    const blankCount = slots.filter((s) => s.row === null).length;
    expect(blankCount).toBe(gaps.reduce((a, g) => a + g.missing, 0));
  });
});
