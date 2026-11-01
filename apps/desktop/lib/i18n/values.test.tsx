import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  translateLogicValue,
  isChineseLogicValue,
  LOGIC_DISPLAY_EN_SOURCES,
} from "./values";
import { I18nProvider } from "@/components/i18n/i18n-provider";
import { useLogicText } from "@/components/i18n/use-ui-text";

// 逻辑值映射契约 —— 护栏类。这批中文串是**数据契约值**(A 类),包 tr() 会让
// en 模式下比较恒 false;本表是唯一被允许的翻法(渲染那一刻映射)。

describe("translateLogicValue", () => {
  it("zh-CN → 原样返回(逻辑值本身即中文展示)", () => {
    expect(translateLogicValue("zh-CN", "多")).toBe("多");
    expect(translateLogicValue("zh-CN", "信号")).toBe("信号");
  });

  it("en → 返回英文展示", () => {
    expect(translateLogicValue("en", "多")).toBe("Long");
    expect(translateLogicValue("en", "空")).toBe("Short");
    expect(translateLogicValue("en", "买")).toBe("Buy");
    expect(translateLogicValue("en", "信号")).toBe("Signal");
    expect(translateLogicValue("en", "爆仓")).toBe("Liquidation");
  });

  it("未登记的值 → 原样返回(优雅降级,不崩)", () => {
    expect(translateLogicValue("en", "从未登记的值")).toBe("从未登记的值");
    expect(translateLogicValue("en", "buy")).toBe("buy");
  });

  it("⚠️ 逻辑值在 en 下必须变,否则 en 界面残留中文", () => {
    for (const src of LOGIC_DISPLAY_EN_SOURCES) {
      expect(translateLogicValue("en", src), `"${src}" 未译`).not.toBe(src);
    }
  });
});

describe("isChineseLogicValue", () => {
  it("含中文 → true;纯 ASCII → false", () => {
    expect(isChineseLogicValue("多")).toBe(true);
    expect(isChineseLogicValue("信号")).toBe(true);
    expect(isChineseLogicValue("buy")).toBe(false);
    expect(isChineseLogicValue("")).toBe(false);
  });
});

describe("useLogicText", () => {
  function Probe({ value }: { value: string }) {
    const tl = useLogicText();
    return <span data-testid="v">{tl(value)}</span>;
  }

  it("provider 缺席(裸渲染) → 原样返回", () => {
    render(<Probe value="多" />);
    expect(screen.getByTestId("v").textContent).toBe("多");
  });

  it("provider 内默认 zh-CN → 原样", () => {
    render(
      <I18nProvider>
        <Probe value="多" />
      </I18nProvider>,
    );
    expect(screen.getByTestId("v").textContent).toBe("多");
  });

  it("provider 内已存 en → 译成英文", () => {
    localStorage.setItem("selenespace-locale", "en");
    render(
      <I18nProvider>
        <Probe value="多" />
      </I18nProvider>,
    );
    expect(screen.getByTestId("v").textContent).toBe("Long");
    localStorage.clear();
  });
});
