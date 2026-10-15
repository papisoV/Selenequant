import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { AuthProvider, useAuth, isTokenExpired, needsTokenRefresh } from "./AuthProvider";
import * as authLib from "@/lib/auth";

// 行为锁: AuthProvider 的可观察契约(auth 关闭时的状态 / token 过期判定 / 登出)。
// cleanroom 重写必须保持; 撤实现 → 必红。

vi.mock("@/lib/auth");

const mocked = vi.mocked(authLib);

function Probe() {
  const { isReady, isAuthenticated, userId, role, authEnabled } = useAuth();
  return (
    <div>
      <span data-testid="ready">{String(isReady)}</span>
      <span data-testid="authed">{String(isAuthenticated)}</span>
      <span data-testid="uid">{userId}</span>
      <span data-testid="role">{role}</span>
      <span data-testid="enabled">{String(authEnabled)}</span>
    </div>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocked.isAuthEnabled.mockReturnValue(false);
  mocked.getTokenPayload.mockReturnValue(null);
  mocked.getAccessToken.mockReturnValue(null);
  mocked.getUserId.mockReturnValue("local");
  mocked.getUserRole.mockReturnValue("admin");
});

afterEach(cleanup);

describe("isTokenExpired", () => {
  it("无 payload → true", () => {
    mocked.getTokenPayload.mockReturnValue(null);
    expect(isTokenExpired()).toBe(true);
  });
  it("payload 无 exp → false(不过期)", () => {
    mocked.getTokenPayload.mockReturnValue({} as never);
    expect(isTokenExpired()).toBe(false);
  });
  it("exp 已过 → true", () => {
    const past = Math.floor(Date.now() / 1000) - 1000;
    mocked.getTokenPayload.mockReturnValue({ exp: past } as never);
    expect(isTokenExpired()).toBe(true);
  });
  it("exp 在未来 → false", () => {
    const future = Math.floor(Date.now() / 1000) + 100000;
    mocked.getTokenPayload.mockReturnValue({ exp: future } as never);
    expect(isTokenExpired()).toBe(false);
  });
});

describe("AuthProvider", () => {
  it("auth 关闭时: ready=true 且始终已认证(userId=local, role=admin)", () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );
    expect(screen.getByTestId("ready").textContent).toBe("true");
    expect(screen.getByTestId("authed").textContent).toBe("true");
    expect(screen.getByTestId("uid").textContent).toBe("local");
    expect(screen.getByTestId("role").textContent).toBe("admin");
    expect(screen.getByTestId("enabled").textContent).toBe("false");
  });
});

describe("needsTokenRefresh", () => {
  it("已过期 → true", () => {
    const past = Math.floor(Date.now() / 1000) - 1000;
    mocked.getTokenPayload.mockReturnValue({ exp: past } as never);
    expect(needsTokenRefresh()).toBe(true);
  });
  it("无 exp → 未过期, 不刷(false)", () => {
    mocked.getTokenPayload.mockReturnValue({} as never);
    expect(needsTokenRefresh()).toBe(false);
  });
  it("临近 300s 到期 → true", () => {
    const soon = Math.floor(Date.now() / 1000) + 100;
    mocked.getTokenPayload.mockReturnValue({ exp: soon } as never);
    expect(needsTokenRefresh()).toBe(true);
  });
  it("远期到期 → false", () => {
    const far = Math.floor(Date.now() / 1000) + 100000;
    mocked.getTokenPayload.mockReturnValue({ exp: far } as never);
    expect(needsTokenRefresh()).toBe(false);
  });
});
