import { describe, expect, it } from "vitest";

import {
  DEFAULT_DRAWING_PREFS,
  DRAWING_PREFS_KEY,
  parseDrawingPrefs,
  parseDrawingPrefsJson,
  serializeDrawingPrefs,
} from "@/lib/chart/drawing-prefs";

describe("drawing-prefs", () => {
  it("默认:连续绘制开(沿用本仓库既有手感),磁吸关(同 TradingView)", () => {
    expect(DEFAULT_DRAWING_PREFS.keepDrawing).toBe(true);
    expect(DEFAULT_DRAWING_PREFS.magnet).toBe("off");
  });

  it("偏好与绘图对象分开存档 —— key 不同,坏一边不拖垮另一边", () => {
    expect(DRAWING_PREFS_KEY).toBe("selenespace-drawing-prefs");
    expect(DRAWING_PREFS_KEY).not.toBe("selenespace-fib-drawings");
  });

  it("往返相等", () => {
    const prefs = { keepDrawing: false, magnet: "strong" as const };
    expect(parseDrawingPrefsJson(serializeDrawingPrefs(prefs))).toEqual(prefs);
  });

  it("坏字段只回退那一个,不整包重置", () => {
    // 用户只把 magnet 写坏了,不该连带把 keepDrawing 也重置掉。
    expect(parseDrawingPrefs({ keepDrawing: false, magnet: "sideways" })).toEqual({
      keepDrawing: false,
      magnet: "off",
    });
    expect(parseDrawingPrefs({ keepDrawing: "yes", magnet: "weak" })).toEqual({
      keepDrawing: true,
      magnet: "weak",
    });
  });

  it("非对象 / 坏 JSON / 空串一律收敛为默认,不抛", () => {
    for (const bad of [null, undefined, 1, "x", [], true]) {
      expect(parseDrawingPrefs(bad)).toEqual(DEFAULT_DRAWING_PREFS);
    }
    for (const bad of [null, "", "{", "null", "[1,2]"]) {
      expect(parseDrawingPrefsJson(bad)).toEqual(DEFAULT_DRAWING_PREFS);
    }
  });

  it("返回的是新对象,改它不污染默认常量", () => {
    const prefs = parseDrawingPrefs(null);
    prefs.magnet = "strong";
    expect(DEFAULT_DRAWING_PREFS.magnet).toBe("off");
  });
});
