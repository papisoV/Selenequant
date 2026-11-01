import { describe, expect, it, beforeEach } from "vitest";
import { useAccountGateStore } from "./account-gate";

beforeEach(() => {
  useAccountGateStore.setState({ modes: {} });
});

describe("useAccountGateStore(per-context 授权模式)", () => {
  it("未设置 context → 默认 empty", () => {
    expect(useAccountGateStore.getState().getMode("ctx-a")).toBe("empty");
  });

  it("setMode 后 → demo,且各 context 互相隔离", () => {
    useAccountGateStore.getState().setMode("ctx-a", "demo");
    expect(useAccountGateStore.getState().getMode("ctx-a")).toBe("demo");
    expect(useAccountGateStore.getState().getMode("ctx-b")).toBe("empty");
  });

  it("同一 context 可 live ↔ demo 切换", () => {
    useAccountGateStore.getState().setMode("ctx-a", "demo");
    useAccountGateStore.getState().setMode("ctx-a", "live");
    expect(useAccountGateStore.getState().getMode("ctx-a")).toBe("live");
  });

  it("reset → 回到 empty", () => {
    useAccountGateStore.getState().setMode("ctx-a", "demo");
    useAccountGateStore.getState().reset("ctx-a");
    expect(useAccountGateStore.getState().getMode("ctx-a")).toBe("empty");
    expect(useAccountGateStore.getState().modes).toEqual({});
  });

  it("reset 不存在的 context → 不改状态", () => {
    useAccountGateStore.getState().setMode("ctx-a", "demo");
    useAccountGateStore.getState().reset("nope");
    expect(useAccountGateStore.getState().getMode("ctx-a")).toBe("demo");
  });
});
