import { describe, it, expect, vi, afterEach } from "vitest";
import { render, waitFor, fireEvent } from "@testing-library/react";
import {
  buildAdvBand,
  buildStaircasePath,
  rankPairs,
  TrainingChartCard,
} from "@/components/research/alpha-mining-charts";
import type { TrainingHistory } from "@/lib/api/alpha";

// 与 alpha-mining-panel.test.tsx 同型 mock history(无 rank_step → 走 step%100 退化)
const baseHistory: TrainingHistory = {
  step: [0, 1, 2],
  avg_reward: [0.1, -0.05, 0.2],
  best_score: [-1, -0.5, 0.1],
  stable_rank: [],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("alpha-mining-charts 纯几何", () => {
  it("#1 优势带闭合:best 正向 + avg 反向,L..Z 围出多边形", () => {
    const d = buildAdvBand(
      [
        [0, 10],
        [400, 20],
      ],
      [
        [0, 30],
        [400, 40],
      ],
    );
    expect(d).toBe("M0.00,10.00 L400.00,20.00 L400.00,40.00 L0.00,30.00 Z");
  });

  it("#2 优势带渲染进 SVG 且以 Z 闭合", () => {
    const { container } = render(<TrainingChartCard history={baseHistory} useLord={false} />);
    const band = container.querySelector(".adv-band");
    expect(band).toBeTruthy();
    const d = band!.getAttribute("d") ?? "";
    expect(d.startsWith("M")).toBe(true);
    expect(d.endsWith("Z")).toBe(true);
  });

  it("#3 阶梯 path 仅 H/V,不假设采样等距", () => {
    expect(
      buildStaircasePath([
        [3, 4.7],
        [42.4, 5.4],
        [81.8, 6.4],
      ]),
    ).toBe("M3.00,4.70 H42.40 V5.40 H81.80 V6.40");
  });

  it("#4 rankPairs:有 rank_step 时 zip,缺失/长度不符时退化 step%100", () => {
    const withStep: TrainingHistory = {
      ...baseHistory,
      stable_rank: [4.2, 3.9],
      rank_step: [0, 250],
    };
    expect(rankPairs(withStep)).toEqual([
      [0, 4.2],
      [250, 3.9],
    ]);

    const legacy: TrainingHistory = { ...baseHistory, stable_rank: [4.2, 3.9, 3.1] };
    expect(rankPairs(legacy)).toEqual([
      [0, 4.2],
      [100, 3.9],
      [200, 3.1],
    ]);

    // 长度不符(半程 checkpoint 截断)→ 不敢信索引,退回退化
    const mismatch: TrainingHistory = {
      ...baseHistory,
      stable_rank: [4.2, 3.9, 3.1],
      rank_step: [0, 250],
    };
    expect(rankPairs(mismatch)).toEqual([
      [0, 4.2],
      [100, 3.9],
      [200, 3.1],
    ]);
  });
});

describe("alpha-mining-charts 渲染", () => {
  it("#5 live-read 三读数从线端几何反解(单一事实源)", () => {
    const { container } = render(<TrainingChartCard history={baseHistory} useLord={false} />);
    const nums = Array.from(container.querySelectorAll(".live-read .num")).map(
      (el) => el.textContent,
    );
    // best_score 末值 0.1 / avg_reward 末值 0.2 / gap = -0.1
    expect(nums).toEqual(["0.100", "0.200", "-0.100"]);
  });

  it("#6 阶梯 sparkline 渲染为纯 H/V(无 L 指令)+ 首末点", () => {
    const history: TrainingHistory = {
      ...baseHistory,
      stable_rank: [4.2, 3.9, 3.1],
      rank_step: [0, 250, 500],
    };
    const { container } = render(<TrainingChartCard history={history} useLord={true} />);
    const path = container.querySelector('svg[data-chart="rank-chart"] path');
    expect(path).toBeTruthy();
    const d = path!.getAttribute("d") ?? "";
    expect(d).not.toContain("L");
    expect(d).toMatch(/^M[\d.]+,[\d.]+( H[\d.]+ V[\d.]+)+$/);
    expect(container.querySelectorAll('svg[data-chart="rank-chart"] circle').length).toBe(2);
  });

  it("#7 use_lord=false 时整块诚实隐藏", () => {
    const history: TrainingHistory = {
      ...baseHistory,
      stable_rank: [4.2, 3.9, 3.1],
      rank_step: [0, 250, 500],
    };
    const { container } = render(<TrainingChartCard history={history} useLord={false} />);
    expect(container.querySelector('svg[data-chart="rank-chart"]')).toBeNull();
    expect(container.querySelector('svg[data-chart="train-chart"]')).toBeTruthy();
  });

  it("#8 缩放补偿:jsdom 0 宽回退 r=4,实测宽后 r = 4/scale", async () => {
    const { container } = render(<TrainingChartCard history={baseHistory} useLord={false} />);
    const svg = container.querySelector<SVGSVGElement>('svg[data-chart="train-chart"]')!;
    const dot = () => container.querySelector('circle[data-dot="best"]');
    // jsdom getBoundingClientRect().width === 0 → scale 回退 1(不是 NaN)
    expect(dot()!.getAttribute("r")).toBe("4");

    // 容器宽 800 / viewBox 宽 400 → scale 2 → r = 2
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({
      width: 800,
      height: 165,
      top: 0,
      left: 0,
      right: 800,
      bottom: 165,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    fireEvent(window, new Event("resize"));
    await waitFor(() => expect(dot()!.getAttribute("r")).toBe("2"));
  });

  it("#9 polyline/path 全挂 non-scaling-stroke(线宽不随 viewBox 拉伸)", () => {
    const history: TrainingHistory = {
      ...baseHistory,
      stable_rank: [4.2, 3.9, 3.1],
      rank_step: [0, 250, 500],
    };
    const { container } = render(<TrainingChartCard history={history} useLord={true} />);
    const strokes = Array.from(container.querySelectorAll("polyline, path, line")).filter(
      (el) => el.querySelector("stop") === null,
    );
    // 数据线/推进线(linearGradient 的 stop 不是 stroke 载体)
    const dataStrokes = strokes.filter((el) => el.getAttribute("stroke") !== null);
    expect(dataStrokes.length).toBeGreaterThan(0);
    for (const el of dataStrokes) {
      expect(el.getAttribute("vector-effect")).toBe("non-scaling-stroke");
    }
  });
});
