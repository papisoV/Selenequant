import { describe, it, expect, vi } from "vitest";
import type { KeyboardEvent } from "react";
import { clickable } from "./clickable";

function keyEvent(key: string) {
  return { key, preventDefault: vi.fn() } as unknown as KeyboardEvent;
}

describe("clickable", () => {
  it("暴露 role=button 与 tabIndex=0,进入键盘 tab 序列", () => {
    const props = clickable(() => {});
    expect(props.role).toBe("button");
    expect(props.tabIndex).toBe(0);
  });

  it("保留原 onClick,鼠标行为不变", () => {
    const fn = vi.fn();
    clickable(fn).onClick?.();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("Enter 触发 handler", () => {
    const fn = vi.fn();
    clickable(fn).onKeyDown(keyEvent("Enter"));
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("空格触发 handler 并阻止默认滚动", () => {
    const fn = vi.fn();
    const e = keyEvent(" ");
    clickable(fn).onKeyDown(e);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(e.preventDefault).toHaveBeenCalled();
  });

  it("其他按键不触发", () => {
    const fn = vi.fn();
    clickable(fn).onKeyDown(keyEvent("a"));
    clickable(fn).onKeyDown(keyEvent("Escape"));
    expect(fn).not.toHaveBeenCalled();
  });

  it("onClick 缺省时不抛错(纯键盘路径)", () => {
    const e = keyEvent("Enter");
    expect(() => clickable(undefined).onKeyDown(e)).not.toThrow();
  });
});
