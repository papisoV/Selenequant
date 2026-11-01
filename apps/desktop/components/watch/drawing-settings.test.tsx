import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { DrawingSettings } from "./drawing-settings";
import { DEFAULT_DRAWING_PREFS, DRAWING_PREFS_KEY } from "@/lib/chart/drawing-prefs";
import { useDrawingsStore } from "@/lib/store/drawings";

const store = () => useDrawingsStore.getState();

beforeEach(() => {
  store().resetForTest();
  localStorage.clear();
});

/** 点「绘图设置」图标(1022 起按钮只有图形,名字在 aria-label 上)把面板开出来。 */
function openSettings(): HTMLElement {
  render(<DrawingSettings />);
  fireEvent.click(screen.getByRole("button", { name: "绘图设置" }));
  return screen.getByRole("dialog", { name: "绘图设置" });
}

describe("绘图设置面板", () => {
  it("默认关着,点「绘图设置」才出现", () => {
    render(<DrawingSettings />);
    expect(screen.queryByRole("dialog", { name: "绘图设置" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "绘图设置" }));
    expect(screen.getByRole("dialog", { name: "绘图设置" })).not.toBeNull();
  });

  it("连续绘制默认开着(不改变现有手感),取消勾选写进 store", () => {
    const dialog = openSettings();
    const box = Array.from(dialog.querySelectorAll('input[type="checkbox"]'))[0] as HTMLInputElement;
    expect(box.checked).toBe(true);

    fireEvent.click(box);
    expect(store().prefs.keepDrawing).toBe(false);
    expect(box.checked).toBe(false);

    fireEvent.click(box);
    expect(store().prefs.keepDrawing).toBe(true);
  });

  it("磁吸三档是单选:点哪档只有哪档 aria-checked", () => {
    const dialog = openSettings();
    const radios = Array.from(dialog.querySelectorAll('[role="radio"]')) as HTMLElement[];
    expect(radios.map((r) => r.textContent)).toEqual(["不吸附", "弱吸附", "强吸附"]);
    expect(radios.map((r) => r.getAttribute("aria-checked"))).toEqual(["true", "false", "false"]);

    fireEvent.click(radios[2]);
    expect(store().prefs.magnet).toBe("strong");
    expect(radios.map((r) => r.getAttribute("aria-checked"))).toEqual(["false", "false", "true"]);

    fireEvent.click(radios[1]);
    expect(store().prefs.magnet).toBe("weak");
  });

  it("偏好写进独立 key,不碰对象树的 key", () => {
    openSettings();
    const radios = Array.from(document.querySelectorAll('[role="radio"]')) as HTMLElement[];
    fireEvent.click(radios[1]);

    expect(localStorage.getItem(DRAWING_PREFS_KEY)).toContain('"magnet":"weak"');
    expect(localStorage.getItem("selenespace-fib-drawings")).toBeNull();
  });

  it("点面板外面 / 按 Escape 都关", () => {
    const dialog = openSettings();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog", { name: "绘图设置" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "绘图设置" }));
    expect(screen.queryByRole("dialog", { name: "绘图设置" })).not.toBeNull();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "绘图设置" })).toBeNull();
    expect(dialog).not.toBeNull();
  });

  it("面板本身收到 pointerdown 不会自己关掉", () => {
    const dialog = openSettings();
    fireEvent.pointerDown(dialog);
    expect(screen.getByRole("dialog", { name: "绘图设置" })).not.toBeNull();
  });

  it("重开面板时显示的是已保存的偏好", () => {
    store().setMagnet("strong");
    store().setKeepDrawing(false);
    const dialog = openSettings();
    const box = dialog.querySelector('input[type="checkbox"]') as HTMLInputElement;
    expect(box.checked).toBe(false);
    expect(
      (dialog.querySelector('[role="radio"][aria-checked="true"]') as HTMLElement).textContent,
    ).toBe("强吸附");
  });

  it("resetForTest 把偏好恢复成默认", () => {
    store().setMagnet("weak");
    store().resetForTest();
    expect(store().prefs).toEqual(DEFAULT_DRAWING_PREFS);
  });
});
