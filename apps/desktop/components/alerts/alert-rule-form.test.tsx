import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AlertRuleForm, ALERT_TYPES } from "./alert-rule-form";

describe("AlertRuleForm(预警规则表单)", () => {
  it("空名称提交 → 校验错误,onSubmit 不被调用", () => {
    const onSubmit = vi.fn();
    render(<AlertRuleForm onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: "保存规则" }));
    expect(screen.getByText("名称必填")).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("价格阈值非数字 → 阈值校验错误", () => {
    const onSubmit = vi.fn();
    render(<AlertRuleForm onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("规则名称"), { target: { value: "测试" } });
    fireEvent.change(screen.getByLabelText("价格阈值"), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: "保存规则" }));
    expect(screen.getByText("阈值必须是数字")).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("type 切换 → 每 type 阈值字段集不同", () => {
    render(<AlertRuleForm onSubmit={() => {}} />);
    // 默认 price → 价格阈值
    expect(screen.getByLabelText("价格阈值")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "indicator" } });
    expect(screen.getByLabelText("阈值")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "large_trade" } });
    expect(screen.getByLabelText("最小名义金额(USD)")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "voice" } });
    expect(screen.getByLabelText("关键词")).toBeTruthy();
  });

  it("onchain 已下架:不在枚举里,选项里也没有", () => {
    // 无 Glassnode key 时 onchain 数据源返回的是陈旧缓存,拿它做监控是假监控。
    expect(ALERT_TYPES).not.toContain("onchain");
    render(<AlertRuleForm onSubmit={() => {}} />);
    const options = Array.from(
      (screen.getByLabelText("规则类型") as HTMLSelectElement).options,
    ).map((o) => o.value);
    expect(options).toEqual(["price", "indicator", "large_trade", "voice"]);
    expect(options).not.toContain("onchain");
  });

  it("indicator 只留 K 线可算的 RSI/MACD", () => {
    // SOPR/NVT 是 Glassnode 链上指标,与 onchain 同源,混在指标下拉里会
    // 悄悄造出第二条假监控路径。
    render(<AlertRuleForm onSubmit={() => {}} />);
    fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "indicator" } });
    const metricSelect = screen.getByDisplayValue("RSI") as HTMLSelectElement;
    const options = Array.from(metricSelect.options).map((o) => o.value);
    expect(options).toEqual(["RSI", "MACD"]);
  });

  it("提交 → onSubmit 收到完整 draft", () => {
    const onSubmit = vi.fn();
    render(<AlertRuleForm onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("规则名称"), { target: { value: "BTC 突破 10 万" } });
    fireEvent.change(screen.getByLabelText("监控标的"), { target: { value: "BTC/USDT" } });
    fireEvent.change(screen.getByLabelText("价格阈值"), { target: { value: "100000" } });
    fireEvent.click(screen.getByRole("button", { name: "保存规则" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const draft = onSubmit.mock.calls[0][0];
    expect(draft.name).toBe("BTC 突破 10 万");
    expect(draft.type).toBe("price");
    expect(draft.threshold.value).toBe(100000);
    expect(draft.enabled).toBe(true);
    expect(draft.channel_id).toBeTruthy();
  });

  it("voice 类型关键词为空 → 校验错误", () => {
    const onSubmit = vi.fn();
    render(<AlertRuleForm onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("规则名称"), { target: { value: "语音预警" } });
    fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "voice" } });
    fireEvent.click(screen.getByRole("button", { name: "保存规则" }));
    expect(screen.getByText("关键词必填")).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("price/indicator 没有标的 → 校验错误(没标的的阈值规则无法求值)", () => {
    // 后端 alert_rule_schema.TYPES_REQUIRING_SYMBOL 同样拒绝这类规则。前端先拦
    // 住,免得用户填完表单才收到一条 400。
    const onSubmit = vi.fn();
    render(<AlertRuleForm onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("规则名称"), { target: { value: "无标的" } });
    fireEvent.change(screen.getByLabelText("价格阈值"), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "保存规则" }));
    expect(screen.getByText("标的必填")).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("price 提交 → draft 带 symbol", () => {
    const onSubmit = vi.fn();
    render(<AlertRuleForm onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("规则名称"), { target: { value: "BTC 突破" } });
    fireEvent.change(screen.getByLabelText("监控标的"), { target: { value: " BTC/USDT " } });
    fireEvent.change(screen.getByLabelText("价格阈值"), { target: { value: "100000" } });
    fireEvent.click(screen.getByRole("button", { name: "保存规则" }));
    expect(onSubmit.mock.calls[0][0].symbol).toBe("BTC/USDT");
  });

  it("large_trade 不需要标的:不渲染标的输入,提交也不带 symbol", () => {
    // 「大单 ≥5万」匹配的是检测器报出的所有标的;给它挂个 BTC 会让人以为
    // 这条规则只管 BTC。
    const onSubmit = vi.fn();
    render(<AlertRuleForm onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("规则名称"), { target: { value: "大单" } });
    fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "large_trade" } });
    expect(screen.queryByLabelText("监控标的")).toBeNull();
    fireEvent.change(screen.getByLabelText("最小名义金额(USD)"), { target: { value: "50000" } });
    fireEvent.click(screen.getByRole("button", { name: "保存规则" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0].symbol).toBeUndefined();
  });

  it("编辑已有规则 → 标的回填", () => {
    render(
      <AlertRuleForm
        initial={{
          id: "r1",
          name: "BTC 突破",
          type: "price",
          threshold: { operator: "gt", value: 100000 },
          enabled: true,
          symbol: "BTC/USDT",
        }}
        onSubmit={() => {}}
      />,
    );
    expect((screen.getByLabelText("监控标的") as HTMLInputElement).value).toBe("BTC/USDT");
  });
});
