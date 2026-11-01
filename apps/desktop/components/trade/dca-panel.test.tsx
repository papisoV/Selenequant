import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DcaPanel } from "./dca-panel";

describe("DcaPanel(DCA 定投面板)", () => {
  it("默认 100/6 → 生成计划表 → 6 行", () => {
    const { container } = render(<DcaPanel />);
    fireEvent.click(screen.getByRole("button", { name: "生成计划表" }));
    expect(container.querySelectorAll(".dca-row")).toHaveLength(6);
    expect(screen.getByText("成本均价")).toBeTruthy();
  });

  it("每期金额=100 期数=3 → 3 行,首行均价 100.0000", () => {
    const { container } = render(<DcaPanel />);
    fireEvent.change(screen.getByLabelText("每期金额"), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText("期数"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "生成计划表" }));
    const rows = container.querySelectorAll(".dca-row");
    expect(rows).toHaveLength(3);
    // 第 1 期:价格 100,单位 1,均价 100
    expect(rows[0].textContent).toContain("1");
    expect(rows[0].textContent).toContain("100");
  });

  it("期数输入非数字 → 兜底为 1 行", () => {
    const { container } = render(<DcaPanel />);
    fireEvent.change(screen.getByLabelText("期数"), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: "生成计划表" }));
    expect(container.querySelectorAll(".dca-row")).toHaveLength(1);
  });
});
