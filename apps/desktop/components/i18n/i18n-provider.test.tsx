import { describe, it, expect, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nProvider, useI18nContext } from "./i18n-provider";

function LocaleSwitcher() {
  const { setLocale } = useI18nContext();
  return <button onClick={() => setLocale("en")}>to-en</button>;
}

describe("I18nProvider(locale → data-locale)", () => {
  afterEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.locale;
    document.documentElement.lang = "";
  });

  it("默认 zh-CN:documentElement.data-locale=zh-CN", () => {
    render(
      <I18nProvider>
        <LocaleSwitcher />
      </I18nProvider>
    );
    expect(document.documentElement.dataset.locale).toBe("zh-CN");
  });

  it("切 en → data-locale=en 且 lang 同步", () => {
    render(
      <I18nProvider>
        <LocaleSwitcher />
      </I18nProvider>
    );
    fireEvent.click(screen.getByText("to-en"));
    expect(document.documentElement.dataset.locale).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("已存 en → 挂载后恢复(en 优先于默认 zh-CN)", () => {
    localStorage.setItem("selenespace-locale", "en");
    render(
      <I18nProvider>
        <LocaleSwitcher />
      </I18nProvider>
    );
    expect(document.documentElement.dataset.locale).toBe("en");
  });
});
