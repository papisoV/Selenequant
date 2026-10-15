import { describe, it, expect, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nProvider, useI18nContext } from "./i18n-provider";

function LocaleSwitcher() {
  const { setLocale } = useI18nContext();
  return <button onClick={() => setLocale("zh-CN")}>to-zh</button>;
}

describe("I18nProvider(locale → data-locale)", () => {
  afterEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.locale;
    document.documentElement.lang = "";
  });

  it("默认 en:documentElement.data-locale=en", () => {
    render(
      <I18nProvider>
        <LocaleSwitcher />
      </I18nProvider>
    );
    expect(document.documentElement.dataset.locale).toBe("en");
  });

  it("切 zh-CN → data-locale=zh-CN 且 lang 同步", () => {
    render(
      <I18nProvider>
        <LocaleSwitcher />
      </I18nProvider>
    );
    fireEvent.click(screen.getByText("to-zh"));
    expect(document.documentElement.dataset.locale).toBe("zh-CN");
    expect(document.documentElement.lang).toBe("zh-CN");
  });
});
