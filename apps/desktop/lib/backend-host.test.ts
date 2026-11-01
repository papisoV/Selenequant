import { afterEach, describe, expect, it, vi } from "vitest";
import { resolveBackendHost } from "./backend-host";

const originalEnv = { ...process.env };

afterEach(() => {
  process.env = { ...originalEnv };
  delete process.env.NEXT_PUBLIC_API_HOST;
  delete process.env.NEXT_PUBLIC_LOCAL_API_BASE;
});

describe("resolveBackendHost", () => {
  it("默认 127.0.0.1:8088(防 IPv6,对齐 uvicorn 绑定)", () => {
    delete process.env.NEXT_PUBLIC_API_HOST;
    delete process.env.NEXT_PUBLIC_LOCAL_API_BASE;
    expect(resolveBackendHost()).toBe("127.0.0.1:8088");
  });

  it("NEXT_PUBLIC_API_HOST 显式覆盖优先", () => {
    process.env.NEXT_PUBLIC_API_HOST = "192.168.1.10:9000";
    process.env.NEXT_PUBLIC_LOCAL_API_BASE = "http://127.0.0.1:8090";
    expect(resolveBackendHost()).toBe("192.168.1.10:9000");
  });

  it("LOCAL_API_BASE 注入时去协议前缀复用(多副本主开关)", () => {
    delete process.env.NEXT_PUBLIC_API_HOST;
    process.env.NEXT_PUBLIC_LOCAL_API_BASE = "http://127.0.0.1:8090";
    expect(resolveBackendHost()).toBe("127.0.0.1:8090");
  });

  it("LOCAL_API_BASE 带 https 也正确去前缀", () => {
    delete process.env.NEXT_PUBLIC_API_HOST;
    process.env.NEXT_PUBLIC_LOCAL_API_BASE = "https://api.replica-a.internal:8443";
    expect(resolveBackendHost()).toBe("api.replica-a.internal:8443");
  });

  it("LOCAL_API_BASE 无协议时原样返回", () => {
    delete process.env.NEXT_PUBLIC_API_HOST;
    process.env.NEXT_PUBLIC_LOCAL_API_BASE = "127.0.0.1:8090";
    expect(resolveBackendHost()).toBe("127.0.0.1:8090");
  });
});
