import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { lineNumbersFor, useSaveShortcut } from "./yaml-editor";

// 行为锁: yaml-editor 抽出的两个 helper 契约。cleanroom 重写必须保持; 撤实现 → 必红。

describe("lineNumbersFor", () => {
  it("单行 → [1]", () => expect(lineNumbersFor("a")).toEqual([1]));
  it("n 行 → [1..n]", () => {
    expect(lineNumbersFor("a\nb\nc")).toEqual([1, 2, 3]);
    expect(lineNumbersFor("a\nb\nc\nd\ne")).toEqual([1, 2, 3, 4, 5]);
  });
});

describe("useSaveShortcut", () => {
  it("Ctrl+S 触发并 preventDefault", () => {
    const save = vi.fn();
    const { result } = renderHook(() => useSaveShortcut(save));
    const e = { key: "s", ctrlKey: true, metaKey: false, preventDefault: vi.fn() };
    act(() => result.current(e as never));
    expect(save).toHaveBeenCalledTimes(1);
    expect(e.preventDefault).toHaveBeenCalledTimes(1);
  });

  it("Cmd+S 触发", () => {
    const save = vi.fn();
    const { result } = renderHook(() => useSaveShortcut(save));
    act(() => result.current({ key: "s", ctrlKey: false, metaKey: true, preventDefault: vi.fn() } as never));
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("普通按键不触发", () => {
    const save = vi.fn();
    const { result } = renderHook(() => useSaveShortcut(save));
    act(() => result.current({ key: "a", ctrlKey: false, metaKey: false, preventDefault: vi.fn() } as never));
    act(() => result.current({ key: "s", ctrlKey: false, metaKey: false, preventDefault: vi.fn() } as never));
    expect(save).not.toHaveBeenCalled();
  });
});
