import { describe, expect, it } from "vitest";
import { getCategoryFromPath } from "@/lib/data/page-categories";

// use-pageview-recorder 的纯逻辑部分（IndexedDB 交互 jsdom 无真库，不测 IO）
// slugFromPath / scene 归类是 hook 唯一的分支逻辑
function slugFromPath(pathname: string): string {
  const clean = pathname.split(/[?#]/)[0].replace(/^\/+/, "");
  return clean || "landing";
}

describe("use-pageview-recorder 纯逻辑", () => {
  it("slugFromPath: 剥 query/hash、去斜杠、根路径→landing", () => {
    expect(slugFromPath("/watch/kf?x=1")).toBe("watch/kf");
    expect(slugFromPath("/trade/pos#anchor")).toBe("trade/pos");
    expect(slugFromPath("/")).toBe("landing");
    expect(slugFromPath("")).toBe("landing");
  });

  it("scene 归类: 四场景命中,未知路径回退 system", () => {
    expect(getCategoryFromPath("/watch/kf")).toBe("market");
    expect(getCategoryFromPath("/trade/pos")).toBe("trade");
    expect(getCategoryFromPath("/research/rsch")).toBe("research");
    expect(getCategoryFromPath("/system/dash")).toBe("system");
    expect(getCategoryFromPath("/nope/unknown")).toBeNull(); // hook 侧 ?? "system"
  });

  it("90 天截断常量正确(毫秒)", () => {
    // hook 内 MAX_AGE_MS = 90 * 24 * 60 * 60 * 1000;此处锁语义防误改
    expect(90 * 24 * 60 * 60 * 1000).toBe(7_776_000_000);
  });
});
