// @vitest-environment node
import { describe, it, expect } from "vitest";
import { parseAiActionChips } from "./events-panel";

describe("parseAiActionChips (P1-3 fail-open)", () => {
  it("deploy payload → 三 chip,strategy chip 带跳转 id", () => {
    const chips = parseAiActionChips(
      JSON.stringify({ strategy_id: "bb_v3", account_id: "acct-1", deployment_id: "dep-9" }),
    );
    expect(chips).toEqual([
      { label: "策略:bb_v3", strategyId: "bb_v3" },
      { label: "账户:acct-1", strategyId: undefined },
      { label: "部署:dep-9", strategyId: undefined },
    ]);
  });

  it("缺字段只渲染存在项", () => {
    expect(parseAiActionChips(JSON.stringify({ account_id: "a" }))).toEqual([
      { label: "账户:a", strategyId: undefined },
    ]);
  });

  it("坏 JSON/空/非对象 → 空数组(渲染与今天一致)", () => {
    expect(parseAiActionChips("not json{")).toEqual([]);
    expect(parseAiActionChips(undefined)).toEqual([]);
    expect(parseAiActionChips("null")).toEqual([]);
    expect(parseAiActionChips(JSON.stringify("str"))).toEqual([]);
  });
});
