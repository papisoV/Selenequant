import { describe, it, expect } from "vitest";
import {
  deriveParamField,
  sliderBounds,
  PARAM_BOUNDS,
  PARAM_ENUM_OPTIONS,
} from "./param-schema";

describe("deriveParamField — 按值 typeof + 枚举白名单推导", () => {
  it("number → kind=number + bounds(PARAM_BOUNDS 命中优先)", () => {
    const f = deriveParamField("stop_loss_pct", 0.03);
    expect(f.kind).toBe("number");
    expect(f.bounds).toEqual({ min: 0, max: 1, step: 0.01 });
  });

  it("number → 未知参数走启发式 fallback", () => {
    const f = deriveParamField("sma_short", 20);
    expect(f.kind).toBe("number");
    // value>=10 → step 1, min=floor(20*0.2)=4, max=ceil(20*3)=60
    expect(f.bounds).toEqual({ min: 4, max: 60, step: 1 });
  });

  it("boolean → kind=boolean", () => {
    expect(deriveParamField("enable_long", true).kind).toBe("boolean");
    expect(deriveParamField("enable_short", false).kind).toBe("boolean");
  });

  it("已知枚举键(string) → kind=enum + 白名单 options", () => {
    const f = deriveParamField("ma_type", "ema");
    expect(f.kind).toBe("enum");
    expect(f.options).toEqual(["sma", "ema", "rma", "lsma"]);
  });

  it("枚举键当前值不在白名单 → 首项补当前值(引擎新增/手填保底)", () => {
    const f = deriveParamField("ma_type", "wma");
    expect(f.kind).toBe("enum");
    expect(f.options).toEqual(["wma", "sma", "ema", "rma", "lsma"]);
  });

  it("未知 string 键 → kind=string(Input fallback)", () => {
    expect(deriveParamField("tg_db_path", "x/tg.db").kind).toBe("string");
  });

  it("null/object/array → kind=unknown(Badge JSON 展示)", () => {
    expect(deriveParamField("weird", null).kind).toBe("unknown");
    expect(deriveParamField("weird", { a: 1 }).kind).toBe("unknown");
    expect(deriveParamField("weird", [1]).kind).toBe("unknown");
  });
});

describe("sliderBounds — PARAM_BOUNDS 边界语义(迁移自 param-panel,零行为变更)", () => {
  it("allowZero=true 且值 0 → min=0 允许拖到 0", () => {
    expect(sliderBounds(0, "stop_loss_pct")).toEqual({ min: 0, max: 1, step: 0.01 });
  });

  it("allowZero=false 且值 0 → clamp 到 bounds.min(不允许 0 拖动)", () => {
    expect(sliderBounds(0, "leverage")).toEqual({ min: 1, max: 125, step: 1 });
  });

  it("无 bounds 负值 → 镜像启发式", () => {
    const b = sliderBounds(-5);
    expect(b.min).toBeLessThanOrEqual(-5);
    expect(b.max).toBeLessThanOrEqual(-1);
  });

  it("小数值未知参数 → step 0.1", () => {
    expect(sliderBounds(3.5).step).toBe(0.1);
  });
});

describe("白名单数据完整性", () => {
  it("PARAM_BOUNDS 13 键 / PARAM_ENUM_OPTIONS 4 键", () => {
    expect(Object.keys(PARAM_BOUNDS).length).toBe(13);
    expect(Object.keys(PARAM_ENUM_OPTIONS).length).toBe(4);
  });
});
