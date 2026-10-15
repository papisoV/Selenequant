import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AlertRuleForm } from "./alert-rule-form";

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
    fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "onchain" } });
    expect(screen.getByLabelText("最小值")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "large_trade" } });
    expect(screen.getByLabelText("最小名义金额(USD)")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("规则类型"), { target: { value: "voice" } });
    expect(screen.getByLabelText("关键词")).toBeTruthy();
  });

  it("提交 → onSubmit 收到完整 draft", () => {
    const onSubmit = vi.fn();
    render(<AlertRuleForm onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("规则名称"), { target: { value: "BTC 突破 10 万" } });
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
});
