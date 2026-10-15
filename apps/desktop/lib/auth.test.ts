import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  isAuthEnabled,
  getAccessToken,
  setAccessToken,
  clearAccessToken,
  getRefreshToken,
  setRefreshToken,
  getTokenPayload,
  getUserRole,
  getUserId,
  login,
  handleCallback,
  refreshToken,
  logout,
  authFetch,
} from "./auth";

// 行为锁: Community 版 auth 助手契约。cleanroom 重写必须保持; 撤实现 → 必红。

beforeEach(() => {
  localStorage.clear();
});

describe("Community 版默认值", () => {
  it("auth 关闭", () => expect(isAuthEnabled()).toBe(false));
  it("角色/用户固定", () => {
    expect(getUserRole()).toBe("admin");
    expect(getUserId()).toBe("local");
  });
  it("login 静默完成", async () => {
    await expect(login("github")).resolves.toBeUndefined();
  });
  it("handleCallback → error", async () => {
    expect(await handleCallback("github", "code", "state")).toEqual({ status: "error" });
  });
  it("refreshToken → false", async () => {
    expect(await refreshToken()).toBe(false);
  });
});

describe("token 存储", () => {
  it("存取 access token", () => {
    expect(getAccessToken()).toBeNull();
    setAccessToken("tok-1");
    expect(getAccessToken()).toBe("tok-1");
  });
  it("存取 refresh token", () => {
    setRefreshToken("ref-1");
    expect(getRefreshToken()).toBe("ref-1");
  });
  it("clearAccessToken 同时清 refresh", () => {
    setAccessToken("a");
    setRefreshToken("b");
    clearAccessToken();
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });
});

describe("getTokenPayload", () => {
  it("无 token → null", () => expect(getTokenPayload()).toBeNull());

  it("合法 JWT 解出 payload", () => {
    const payload = { exp: 123, name: "Ada" };
    const b64 = btoa(JSON.stringify(payload)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    setAccessToken(`h.${b64}.s`);
    expect(getTokenPayload()).toEqual(payload);
  });

  it("非三段 → null", () => {
    setAccessToken("only-one-part");
    expect(getTokenPayload()).toBeNull();
  });

  it("坏 payload → null(不抛)", () => {
    setAccessToken("a.@@@notbase64@@@.c");
    expect(getTokenPayload()).toBeNull();
  });
});

describe("logout", () => {
  it("清 token 并跳首页", () => {
    setAccessToken("x");
    // jsdom 不实现跨文档导航(location.href 赋值被丢弃), 断言不到跳转;
    // 整只换可写假 location 才能真正观察到 logout 写的值。
    const real = window.location;
    const fake = { href: "/somewhere-else" };
    Object.defineProperty(window, "location", { configurable: true, value: fake });
    try {
      logout();
      expect(getAccessToken()).toBeNull();
      expect(window.location.href).toBe("/");
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: real });
    }
  });
});

describe("authFetch", () => {
  it("透传给 fetch(Community 无 token 注入)", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("ok"));
    await authFetch("/api/x");
    expect(spy).toHaveBeenCalledWith("/api/x", undefined);
    spy.mockRestore();
  });
});
