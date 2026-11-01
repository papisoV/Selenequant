import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect } from "vitest";
import {
  HAND_DRAWN_LEVELS,
  EXTENSION_LEVELS,
  NEGATIVE_EXTENSION_LEVELS,
  defaultVisibleLevels,
  isInteriorLevel,
  isExtensionLevel,
  formatLevelLabel,
  levelColorSlot,
} from "@/lib/chart/fib-levels";

/**
 * 策略侧的档位正典。
 *
 * 这个文件只在部分副本里存在(它是 yanjiu 副本的研究产出),所以**不能**做成硬依赖——
 * 硬 readFileSync 会让没有它的副本和 CI 直接红。存在就逐值比对,不存在就跳过并打印原因;
 * 但解析器本身永远被测(见下面「解析器自测」),避免"两边一致"变成一句空话。
 */
const FIB_CORNER_PATH = join(
  process.cwd(),
  "..",
  "..",
  "packages",
  "strategy-engine",
  "selene_space_strategy",
  "fib_corner.py",
);

const FIB_CORNER_EXISTS = existsSync(FIB_CORNER_PATH);

/** 从 fib_corner.py 源码里抠出 `FIB_LEVELS: tuple[float, ...] = (...)` 的数值。 */
function parseFibLevels(source: string): number[] {
  const m = source.match(
    /FIB_LEVELS\s*:\s*tuple\[\s*float\s*,\s*\.\.\.\s*\]\s*=\s*\(([\s\S]*?)\)/,
  );
  if (!m) return [];
  return m[1]
    .replace(/#.*$/gm, "")
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map((s) => Number(s))
    .filter((n) => Number.isFinite(n));
}

describe("parseFibLevels:解析器自测", () => {
  it("抠出带类型注解的多行元组", () => {
    const sample = [
      "FIB_LEVELS: tuple[float, ...] = (",
      "    0.0,",
      "    0.236,",
      "    0.5,  # 中轴",
      "    1.0,",
      ")",
    ].join("\n");
    expect(parseFibLevels(sample)).toEqual([0, 0.236, 0.5, 1]);
  });

  it("没有 FIB_LEVELS 时返回空数组(调用方据此判失败,不得当成功)", () => {
    expect(parseFibLevels("TOUCH_TOL = 0.003\n")).toEqual([]);
  });
});

describe("HAND_DRAWN_LEVELS 与 fib_corner.py 的 FIB_LEVELS 一致", () => {
  const source = FIB_CORNER_EXISTS ? readFileSync(FIB_CORNER_PATH, "utf8") : "";
  const parsed = FIB_CORNER_EXISTS ? parseFibLevels(source) : [];
  const parsedOk = parsed.length > 0;

  it.skipIf(!FIB_CORNER_EXISTS)("逐值一致(顺序与数值都不能漂移)", () => {
    expect(
      parsed,
      `fib_corner.py 存在但没解析出 FIB_LEVELS —— 格式变了,解析器要跟着改:${FIB_CORNER_PATH}`,
    ).not.toEqual([]);
    expect(parsed).toEqual([...HAND_DRAWN_LEVELS]);
  });

  it("缺失或解析失败时明确记录,不静默放过", () => {
    if (!FIB_CORNER_EXISTS) {
      console.warn(
        `[fib-levels] 跳过正典比对:${FIB_CORNER_PATH} 不在本副本 —— ` +
          `图上档位未被策略侧校验,合入含 fib_corner.py 的分支后应自动生效。`,
      );
      expect(parsedOk).toBe(false);
      return;
    }
    expect(parsedOk, "文件在但 FIB_LEVELS 解析为空,等于比对没发生").toBe(true);
  });
});

describe("档位族形状", () => {
  it("手绘族是 11 条,单调递增,含两端 0 与 1", () => {
    expect(HAND_DRAWN_LEVELS).toHaveLength(11);
    expect(HAND_DRAWN_LEVELS[0]).toBe(0);
    expect(HAND_DRAWN_LEVELS[HAND_DRAWN_LEVELS.length - 1]).toBe(1);
    const sorted = [...HAND_DRAWN_LEVELS].sort((a, b) => a - b);
    expect([...HAND_DRAWN_LEVELS]).toEqual(sorted);
  });

  it("延伸族全在 1 以上,负向族全在 0 以下,两族与手绘族无交集", () => {
    expect(EXTENSION_LEVELS.every((lv) => lv > 1)).toBe(true);
    expect(NEGATIVE_EXTENSION_LEVELS.every((lv) => lv < 0)).toBe(true);
    const all = new Set([...HAND_DRAWN_LEVELS, ...EXTENSION_LEVELS, ...NEGATIVE_EXTENSION_LEVELS]);
    expect(all.size).toBe(
      HAND_DRAWN_LEVELS.length + EXTENSION_LEVELS.length + NEGATIVE_EXTENSION_LEVELS.length,
    );
  });
});

describe("defaultVisibleLevels", () => {
  it("默认剔除两端 0 / 1,保留 9 条", () => {
    const visible = defaultVisibleLevels();
    expect(visible).toHaveLength(9);
    expect(visible).not.toContain(0);
    expect(visible).not.toContain(1);
    expect(visible).toContain(0.236);
    expect(visible).toContain(0.886);
  });

  it("返回新数组,改它不会污染常量", () => {
    const visible = defaultVisibleLevels();
    visible.push(99);
    expect(defaultVisibleLevels()).not.toContain(99);
    expect(HAND_DRAWN_LEVELS).toHaveLength(11);
  });
});

describe("isInteriorLevel / isExtensionLevel", () => {
  it("两端既不算内部也不算延伸", () => {
    for (const lv of [0, 1]) {
      expect(isInteriorLevel(lv)).toBe(false);
      expect(isExtensionLevel(lv)).toBe(false);
    }
  });

  it("内部档位与延伸档位互斥", () => {
    for (const lv of HAND_DRAWN_LEVELS) {
      expect(isInteriorLevel(lv)).toBe(true === lv > 0 && lv < 1);
      expect(isExtensionLevel(lv)).toBe(false);
    }
    for (const lv of [...EXTENSION_LEVELS, ...NEGATIVE_EXTENSION_LEVELS]) {
      expect(isExtensionLevel(lv)).toBe(true);
      expect(isInteriorLevel(lv)).toBe(false);
    }
  });
});

describe("formatLevelLabel", () => {
  it("去掉尾随零:0.2 不写成 0.200", () => {
    expect(formatLevelLabel(0.2)).toBe("0.2");
    expect(formatLevelLabel(0.5)).toBe("0.5");
    expect(formatLevelLabel(1)).toBe("1");
  });

  it("保留三位小数:0.236 / 1.272 / 0.886", () => {
    expect(formatLevelLabel(0.236)).toBe("0.236");
    expect(formatLevelLabel(1.272)).toBe("1.272");
    expect(formatLevelLabel(0.886)).toBe("0.886");
  });

  it("负数保留符号:-0.272", () => {
    expect(formatLevelLabel(-0.272)).toBe("-0.272");
  });
});

describe("levelColorSlot", () => {
  it("0.5 是唯一主线槽(-1)", () => {
    expect(levelColorSlot(0.5)).toBe(-1);
    const others = HAND_DRAWN_LEVELS.filter((lv) => lv !== 0.5);
    expect(others.every((lv) => levelColorSlot(lv) !== -1)).toBe(true);
  });

  it("手绘族按族内下标取色,彼此不撞", () => {
    const slots = HAND_DRAWN_LEVELS.map((lv) => levelColorSlot(lv));
    expect(new Set(slots).size).toBe(HAND_DRAWN_LEVELS.length);
    expect(levelColorSlot(0)).toBe(0);
    expect(levelColorSlot(0.236)).toBe(2);
  });

  it("延伸族接在手绘族之后,负向延伸落到兜底槽", () => {
    expect(levelColorSlot(1.272)).toBe(HAND_DRAWN_LEVELS.length);
    expect(levelColorSlot(2.618)).toBe(HAND_DRAWN_LEVELS.length + EXTENSION_LEVELS.length - 1);
    expect(levelColorSlot(-0.272)).toBe(
      HAND_DRAWN_LEVELS.length + EXTENSION_LEVELS.length,
    );
  });
});
