import { describe, it, expect } from "vitest";
import {
  isLocale,
  formatMessage,
  resolveMessage,
  messages,
  DEFAULT_LOCALE,
} from "./index";

// 行为锁: i18n 三个纯函数 + 常量契约。cleanroom 重写必须保持; 撤实现 → 必红。

describe("常量", () => {
  it("默认 zh-CN(项目母语), 且 messages 含 en/zh-CN", () => {
    expect(DEFAULT_LOCALE).toBe("zh-CN");
    expect(Object.keys(messages).sort()).toEqual(["en", "zh-CN"]);
  });
  it("messages[DEFAULT_LOCALE] 即回落源 —— 每个 key 都有值", () => {
    for (const key of Object.keys(messages.en) as Array<keyof typeof messages.en>) {
      expect(typeof messages[DEFAULT_LOCALE][key]).toBe("string");
    }
  });
});

describe("isLocale", () => {
  it("已知 locale → true", () => {
    expect(isLocale("en")).toBe(true);
    expect(isLocale("zh-CN")).toBe(true);
  });
  it("其它 → false", () => {
    expect(isLocale("fr")).toBe(false);
    expect(isLocale("")).toBe(false);
    expect(isLocale("EN")).toBe(false);
  });
});

describe("formatMessage", () => {
  it("无 params → 原样", () => {
    expect(formatMessage("hello {name}")).toBe("hello {name}");
  });
  it("替换命名占位", () => {
    expect(formatMessage("hi {name}, you have {n}", { name: "Ada", n: 3 })).toBe(
      "hi Ada, you have 3"
    );
  });
  it("缺参 → 保留占位", () => {
    expect(formatMessage("{a}-{b}", { a: 1 })).toBe("1-{b}");
  });
  it("值 undefined(运行时可能) → 保留占位", () => {
    const params = { x: undefined } as unknown as { x: string };
    expect(formatMessage("{x}", params)).toBe("{x}");
  });
});

describe("resolveMessage", () => {
  it("命中当前 locale 的键", () => {
    const key = Object.keys(messages.en)[0] as never;
    expect(typeof resolveMessage("en", key)).toBe("string");
  });
  it("未知 key → 回落 key 本身", () => {
    expect(resolveMessage("en", "__nope__" as never)).toBe("__nope__");
  });
});
