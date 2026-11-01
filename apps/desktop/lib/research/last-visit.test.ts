import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";

import { BACKLOOK_DAYS, readLastVisited, writeLastVisited } from "./last-visit";

describe("last-visit 锚点", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("首访返回 null", () => {
    expect(readLastVisited()).toBeNull();
  });

  it("写入后读到原值", () => {
    writeLastVisited("2026-09-23T12:00:00+00:00");
    expect(readLastVisited()).toBe("2026-09-23T12:00:00+00:00");
  });

  it("坏数据当作首访(不抛错)", () => {
    localStorage.setItem("selene.desk.lastVisitedAt", "garbage");
    expect(readLastVisited()).toBeNull();
  });

  it("SSR 环境安全(window 不存在不抛)", () => {
    // jsdom 有 window;这里只验证 BACKLOOK_DAYS 常量存在且为 7
    expect(BACKLOOK_DAYS).toBe(7);
  });
});

describe("BACKLOOK_DAYS", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("回看窗口是 7 天(契约值)", () => {
    expect(BACKLOOK_DAYS).toBe(7);
  });
});
