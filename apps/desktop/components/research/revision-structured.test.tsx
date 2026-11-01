import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { RevisionStructured } from "@/components/research/revision-structured";

describe("RevisionStructured", () => {
  it("三段齐全时逐项显示,置信度按百分比格式化", () => {
    render(
      <RevisionStructured
        structured={{
          confidence: 0.65,
          falsified_when: "1h 收盘站上 78000 且三根同向实体",
          applies_when: "BTC/USDT 1h 趋势行情",
        }}
      />,
    );
    expect(
      screen.getByTestId("revision-structured-confidence").textContent,
    ).toBe("65%");
    expect(
      screen.getByTestId("revision-structured-falsified").textContent,
    ).toContain("78000");
    expect(
      screen.getByTestId("revision-structured-applies").textContent,
    ).toContain("BTC/USDT 1h");
    expect(screen.queryByTestId("revision-structured-empty")).toBeNull();
  });

  it("非数字置信度原样显示(模型可能写「无法给」)", () => {
    render(
      <RevisionStructured
        structured={{ confidence: "无法给", applies_when: "仅 BTC" }}
      />,
    );
    expect(
      screen.getByTestId("revision-structured-confidence").textContent,
    ).toBe("无法给");
  });

  it("置信度为 0 也要显示,不能被 falsy 判断吃掉", () => {
    render(<RevisionStructured structured={{ confidence: 0 }} />);
    expect(
      screen.getByTestId("revision-structured-confidence").textContent,
    ).toBe("0%");
    expect(screen.queryByTestId("revision-structured-empty")).toBeNull();
  });

  it("一项都没有时只说「本版未结构化」,不编造占位文案", () => {
    render(<RevisionStructured structured={undefined} />);
    const el = screen.getByTestId("revision-structured-empty");
    expect(el.textContent).toBe("本版未结构化");
    // 不能出现空标签,否则读起来像"有字段但没值"
    expect(screen.queryByTestId("revision-structured")).toBeNull();
    expect(screen.queryByText("置信度")).toBeNull();
    expect(screen.queryByText("什么会推翻")).toBeNull();
    expect(screen.queryByText("适用范围")).toBeNull();
  });

  it("空对象与 undefined 同义(旧库的行)", () => {
    render(<RevisionStructured structured={{}} />);
    expect(screen.getByTestId("revision-structured-empty")).toBeTruthy();
  });
});
