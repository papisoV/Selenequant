import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AlertRuleCard } from "./alert-rule-card";
import type { StoredAlertRule } from "@/lib/store/alerts";

function makeRule(overrides: Partial<StoredAlertRule> = {}): StoredAlertRule {
  return {
    id: "r1",
    name: "测试规则",
    type: "price",
    threshold: { operator: "gt", value: 100 },
    enabled: true,
    ...overrides,
  };
}

const noop = () => {};

function renderCard(rule: StoredAlertRule) {
  return render(
    <AlertRuleCard rule={rule} onEdit={noop} onDelete={noop} onToggle={noop} />,
  );
}

describe("AlertRuleCard(预警规则卡片)", () => {
  it("已下架类型(onchain)仍可见,并明说不再支持", () => {
    // localStorage 里可能还躺着 onchain 规则;无 Glassnode key 时它永远不会
    // 触发。默默显示成「链上」等于让用户以为自己还在被监控 —— 必须标出来。
    renderCard(makeRule({ type: "onchain", threshold: { metric: "sopr", min_value: 1 } }));

    const badge = screen.getByText("(不再支持)");
    expect(badge).toBeTruthy();
    // 行还在(用户需要能删掉它),不是被过滤掉
    expect(screen.getByText("测试规则")).toBeTruthy();
    expect(screen.getByRole("button", { name: "删除" })).toBeTruthy();
  });

  it("受支持的类型不带「不再支持」标", () => {
    renderCard(makeRule({ type: "large_trade", threshold: { min_notional_usd: 50000 } }));
    expect(screen.queryByText("(不再支持)")).toBeNull();
  });

  it("未知类型(后端新加的)按原样显示类型名,同样标不再支持", () => {
    renderCard(makeRule({ type: "brand_new_type", threshold: {} }));
    expect(screen.getByText("brand_new_type")).toBeTruthy();
    expect(screen.getByText("(不再支持)")).toBeTruthy();
  });

  it("阈值按 k=v 原样列出", () => {
    renderCard(makeRule({ threshold: { operator: "gt", value: 100 } }));
    expect(screen.getByText("operator=gt, value=100")).toBeTruthy();
  });

  it("无阈值 → 破折号占位", () => {
    renderCard(makeRule({ threshold: {} }));
    expect(screen.getByText("—")).toBeTruthy();
  });

  it("开关反映 enabled 并回调", () => {
    const onToggle = vi.fn();
    render(
      <AlertRuleCard rule={makeRule({ enabled: false })} onEdit={noop} onDelete={noop} onToggle={onToggle} />,
    );
    const sw = screen.getByRole("switch");
    expect(sw.getAttribute("aria-checked")).toBe("false");
    sw.click();
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("没写上后端的规则标「未同步,不会触发」", () => {
    // 求值器只读后端。一条停在本机的规则看着正常、其实永远不会触发 ——
    // 那正是「假监控」,必须让它自己说出来。
    renderCard(makeRule({ localOnly: true }));
    expect(screen.getByText("(未同步,不会触发)")).toBeTruthy();
  });

  it("已同步的规则不带「未同步」标", () => {
    renderCard(makeRule({ id: "be-1" }));
    expect(screen.queryByText("(未同步,不会触发)")).toBeNull();
  });
});
