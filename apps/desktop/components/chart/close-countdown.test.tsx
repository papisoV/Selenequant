import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { CloseCountdown, closeAtFrom, formatCountdown } from "@/components/chart/close-countdown";

describe("closeAtFrom — 末根开盘(ms) + 周期 → 收盘时刻(ms)", () => {
  it("1h:开盘 + 3_600_000ms", () => {
    expect(closeAtFrom(1_700_000_000_000, "1h")).toBe(1_700_000_000_000 + 3_600_000);
  });

  it("KfChart 两种 tf 来源的并集都有真时长(含 30m/1w)", () => {
    // 键集必须覆盖并集:pa-panel SUPPORTED_TFS(有 30m 无 1w)与 symbol-bar TFS(有 1w 无 30m)。
    // 缺任一个 → 该周期上倒计时静默消失(组件返回 null 且不报错)。
    expect(closeAtFrom(0, "1m")).toBe(60_000);
    expect(closeAtFrom(0, "5m")).toBe(300_000);
    expect(closeAtFrom(0, "15m")).toBe(900_000);
    expect(closeAtFrom(0, "30m")).toBe(1_800_000);
    expect(closeAtFrom(0, "1h")).toBe(3_600_000);
    expect(closeAtFrom(0, "4h")).toBe(14_400_000);
    expect(closeAtFrom(0, "1d")).toBe(86_400_000);
    expect(closeAtFrom(0, "1w")).toBe(604_800_000);
  });

  it("真未收录的周期 → null(不猜数字)", () => {
    expect(closeAtFrom(1_700_000_000_000, "2h")).toBeNull();
    expect(closeAtFrom(1_700_000_000_000, "3d")).toBeNull();
  });

  it("缺值 / 非法值 → null", () => {
    expect(closeAtFrom(null, "1h")).toBeNull();
    expect(closeAtFrom(undefined, "1h")).toBeNull();
    expect(closeAtFrom(Number.NaN, "1h")).toBeNull();
    expect(closeAtFrom(Number.POSITIVE_INFINITY, "1h")).toBeNull();
    expect(closeAtFrom(1_700_000_000_000, null)).toBeNull();
    expect(closeAtFrom(1_700_000_000_000, undefined)).toBeNull();
    expect(closeAtFrom(1_700_000_000_000, "")).toBeNull();
  });
});

describe("formatCountdown", () => {
  it("mm:ss 补零", () => {
    expect(formatCountdown(65_000)).toBe("01:05");
    expect(formatCountdown(0)).toBe("00:00");
    expect(formatCountdown(59_999)).toBe("00:59");
    expect(formatCountdown(3_600_000)).toBe("60:00");
  });

  it("负数不渲染成负号(夹到 00:00)", () => {
    expect(formatCountdown(-1)).toBe("00:00");
  });
});

describe("CloseCountdown", () => {
  // fake timers:now 由 vi.setSystemTime 之外的真实起点冻结,挂载与断言同源 → 确定。
  // 全程用同步 getBy(不用 findBy/waitFor,避免与 fake timers 死锁)。
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("closeAt 在未来 → 渲染 mm:ss 后收盘,每秒走动", () => {
    const closeAt = Date.now() + 65_000;
    render(<CloseCountdown closeAt={closeAt} />);

    expect(screen.getByText(/01:05 后收盘/)).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(1000); });
    expect(screen.getByText(/01:04 后收盘/)).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(2000); });
    expect(screen.getByText(/01:02 后收盘/)).toBeInTheDocument();
  });

  it("closeAt 已过 → 不渲染(收盘了就不报时)", () => {
    const { container } = render(<CloseCountdown closeAt={Date.now() - 1} />);
    expect(container.textContent).toBe("");
  });

  it("autoSubmit:归零触发 onElapsed 一次,不重复", () => {
    const onElapsed = vi.fn();
    render(<CloseCountdown closeAt={Date.now() + 1000} autoSubmit onElapsed={onElapsed} />);

    act(() => { vi.advanceTimersByTime(1000); });
    expect(onElapsed).toHaveBeenCalledTimes(1);
    act(() => { vi.advanceTimersByTime(5000); });
    expect(onElapsed).toHaveBeenCalledTimes(1);
  });

  it("不传 autoSubmit:归零也不触发 onElapsed", () => {
    const onElapsed = vi.fn();
    render(<CloseCountdown closeAt={Date.now() + 1000} onElapsed={onElapsed} />);

    act(() => { vi.advanceTimersByTime(5000); });
    expect(onElapsed).not.toHaveBeenCalled();
  });
});
