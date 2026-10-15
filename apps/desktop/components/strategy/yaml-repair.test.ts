// US-104 AI 自修复环 — 验证模块单测(纯函数,无 React)。
// 集成路径(done→validate→repair→revalidate)在 ai-sidebar 内,由 yaml-repair
// 模块的状态机驱动;这里测验证判据与修复 prompt 组装。
import { describe, it, expect } from "vitest";
import { validateStrategyYaml, buildRepairPrompt } from "./yaml-repair";

const GOOD_YAML = `name: EMA Cross
type: sma_cross
description: test
symbols:
  - BTC/USDT
interval: 1h
parameters:
  fast: 10
  slow: 50
entry_rules: {}
exit_rules: {}
risk_params: {}
tags: []
`;

describe("validateStrategyYaml", () => {
  it("完整合法 YAML → ok=true 带 partial", () => {
    const r = validateStrategyYaml(GOOD_YAML);
    expect(r.ok).toBe(true);
    expect(r.error).toBeNull();
    expect(r.partial?.name).toBe("EMA Cross");
    expect(r.partial?.symbols).toEqual(["BTC/USDT"]);
    expect(r.partial?.parameters).toEqual({ fast: 10, slow: 50 });
  });

  it("空串 → ok=false", () => {
    const r = validateStrategyYaml("   ");
    expect(r.ok).toBe(false);
    expect(r.error).toContain("为空");
  });

  it("缺 name → ok=false 错误含字段名", () => {
    const bad = GOOD_YAML.replace("name: EMA Cross\n", "");
    const r = validateStrategyYaml(bad);
    expect(r.ok).toBe(false);
    expect(r.error).toContain("name");
  });

  it("缺 symbols → ok=false", () => {
    const bad = GOOD_YAML.replace("symbols:\n  - BTC/USDT\n", "symbols: []\n");
    const r = validateStrategyYaml(bad);
    expect(r.ok).toBe(false);
    expect(r.error).toContain("symbols");
  });

  it("缺 interval 和 type → 错误一次列全(不逐个暴露)", () => {
    const bad = GOOD_YAML
      .replace("type: sma_cross\n", "")
      .replace("interval: 1h\n", "");
    const r = validateStrategyYaml(bad);
    expect(r.ok).toBe(false);
    expect(r.error).toContain("type");
    expect(r.error).toContain("interval");
  });

  it("嵌套块(symbols 列表)被极简解析器跳过 → 报缺 symbols(诚实,不静默丢)", () => {
    // 极简解析器只吃 indent=0 行内值;symbols 列表块解析后 symbols=[] → 必须报错
    // 而不是当成功应用。这是 US-104 的立案动机:静默丢字段比拒绝更糟。
    const r = validateStrategyYaml(GOOD_YAML);
    // 注意:GOOD_YAML 的 symbols 是块列表。若解析器能吃它,ok=true;
    // 吃不到就必须 false —— 两种行为里不允许"ok=true 但 symbols 丢了"。
    if (r.ok) {
      expect(r.partial?.symbols?.length).toBeGreaterThan(0);
    } else {
      expect(r.error).toContain("symbols");
    }
  });
});

describe("buildRepairPrompt", () => {
  it("含错误全文 + 原始 YAML + 只输出 YAML 的指令", () => {
    const p = buildRepairPrompt("name: x", "缺少必填字段 type");
    expect(p).toContain("缺少必填字段 type");
    expect(p).toContain("```yaml");
    expect(p).toContain("name: x");
    expect(p).toContain("完整 YAML");
  });
});
