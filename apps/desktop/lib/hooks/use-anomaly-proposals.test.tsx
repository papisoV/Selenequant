import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import { useAnomalyProposals } from "./use-anomaly-proposals";

// 真契约回归。
//
// 1) 这两个端点是 APIResponse 包装的(见 api/v1/anomaly.py 的 _ok),走
//    fetchEnvelope 解包 —— 桩 fetch 必须回 {data, meta} 信封,回裸 dict 会解成
//    null。反向验证:把 anomaly 加进 BARE_ENDPOINT_PATTERNS,本文件必红。
//
// 2) 后端把 no_pending_proposal / unavailable 装在 **HTTP 200** 的信封
//    error 字段里(见 tests/test_api_anomaly_approve.py:175/209 断言 200)。
//    桩成 404 会在 rawFetch 的 !res.ok 分支先炸,走不到信封解析。
//
// 3) SWR 全局缓存跨用例存活:不换 provider 的话,第二个用例直接吃上一条的
//    缓存 —— fetch 一次不调,断言测的是空气。
vi.mock("@/lib/auth", () => ({
  getAccessToken: () => "test-token",
  clearAccessToken: () => {},
  refreshToken: () => Promise.resolve(false),
}));

const PROPOSAL = {
  dedup_id: "astock-anomaly:300750.SZ:limit_up:2026-10-14",
  symbol: "300750.SZ",
  kind: "limit_up",
  session_date: "2026-10-14",
  price: 11.0,
  side: "buy",
  quantity: 900,
  created_at_ms: 1_762_000_000_000,
};

function envelope(data: unknown, status = 200): Response {
  return new Response(
    JSON.stringify({ data, meta: { timestamp: "2026-10-14T02:00:00Z" } }),
    { status, headers: { "Content-Type": "application/json" } }
  );
}

/** 后端业务错误:HTTP 200 + 信封里的 error(不是 4xx/5xx)。 */
function bizError(code: string, message: string): Response {
  return new Response(
    JSON.stringify({
      data: null,
      meta: { timestamp: "2026-10-14T02:00:00Z" },
      error: { code, message },
    }),
    { status: 200, headers: { "Content-Type": "application/json" } }
  );
}

/** 每个用例一个空缓存,杜绝跨用例串味。 */
function wrapper({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      {children}
    </SWRConfig>
  );
}

const renderProposals = () => renderHook(() => useAnomalyProposals(), { wrapper });

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useAnomalyProposals — 读侧", () => {
  it("解析信封里的待批提案与档位", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => envelope({ proposals: [PROPOSAL], mode: "suggest", wired: true }))
    );

    const { result } = renderProposals();

    await waitFor(() => expect(result.current.proposals).toHaveLength(1));
    expect(result.current.proposals[0].symbol).toBe("300750.SZ");
    expect(result.current.proposals[0].quantity).toBe(900);
    expect(result.current.mode).toBe("suggest");
    expect(result.current.wired).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("只读一次 /proposals,路径符合契约", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      envelope({ proposals: [], mode: "observe", wired: true })
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderProposals();

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toBe("/api/v1/anomaly/proposals");
    expect(result.current.wired).toBe(true);
    expect(result.current.mode).toBe("observe");
  });

  it("密码货币-only 部署:wired=false 且不炸(不是错误)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => envelope({ proposals: [], mode: null, wired: false }))
    );

    const { result } = renderProposals();

    await waitFor(() => expect(result.current.wired).toBe(false));
    expect(result.current.mode).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.proposals).toEqual([]);
  });

  it("读接口挂掉 → error 有值,不静默降级成「无待批」", async () => {
    // 这条是 distinguish:true 的存在理由。旧 useApi 会把错误吞成 null,
    // 面板就显示"暂无待批异动提案" —— 后端明明挂了却说没有提案。
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => bizError("internal_error", "决策消费器炸了"))
    );

    const { result } = renderProposals();

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error).toContain("决策消费器炸了");
    expect(result.current.proposals).toEqual([]);
  });
});

describe("useAnomalyProposals — 写侧(裁定)", () => {
  const stubReadAndWrite = (approveResponse: () => Response) => {
    const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/anomaly/approve") && init?.method === "POST") {
        return approveResponse();
      }
      return envelope({ proposals: [PROPOSAL], mode: "suggest", wired: true });
    });
    vi.stubGlobal("fetch", mock);
    return mock;
  };

  /** 取那次裁定 POST 的请求体;没 POST 过就直接失败,不靠 `!` 蒙混。 */
  const postedBody = (mock: ReturnType<typeof stubReadAndWrite>) => {
    const post = mock.mock.calls.find((c) => c[1]?.method === "POST");
    if (!post) throw new Error("没有发出裁定 POST");
    return JSON.parse(String(post[1]?.body));
  };

  it("批准 → POST /anomaly/approve 带 dedup_id 与 approve=true", async () => {
    const mock = stubReadAndWrite(() =>
      envelope({ resolved: true, result: "approved", executes_order: false })
    );

    const { result } = renderProposals();
    await waitFor(() => expect(result.current.proposals).toHaveLength(1));

    await act(async () => {
      await result.current.decide(PROPOSAL.dedup_id, true);
    });

    expect(postedBody(mock)).toEqual({
      dedup_id: PROPOSAL.dedup_id,
      approve: true,
    });
    const post = mock.mock.calls.find((c) => c[1]?.method === "POST");
    expect(String(post![0])).toBe("/api/v1/anomaly/approve");
  });

  it("拒绝 → approve=false", async () => {
    const mock = stubReadAndWrite(() =>
      envelope({ resolved: true, result: "rejected", executes_order: false })
    );

    const { result } = renderProposals();
    await waitFor(() => expect(result.current.proposals).toHaveLength(1));

    await act(async () => {
      await result.current.decide(PROPOSAL.dedup_id, false);
    });

    expect(postedBody(mock).approve).toBe(false);
  });

  it("裁定成功后重新拉取(不等下一个轮询周期)", async () => {
    const mock = stubReadAndWrite(() =>
      envelope({ resolved: true, result: "approved", executes_order: false })
    );

    const { result } = renderProposals();
    await waitFor(() => expect(result.current.proposals).toHaveLength(1));
    const before = mock.mock.calls.length;

    await act(async () => {
      await result.current.decide(PROPOSAL.dedup_id, true);
    });

    await waitFor(() => expect(mock.mock.calls.length).toBeGreaterThan(before));
  });

  it("裁定失败 → decideError 有值,deciding 不残留", async () => {
    stubReadAndWrite(() => bizError("no_pending_proposal", "该提案已失效"));

    const { result } = renderProposals();
    await waitFor(() => expect(result.current.proposals).toHaveLength(1));

    await act(async () => {
      await result.current.decide(PROPOSAL.dedup_id, true);
    });

    await waitFor(() => expect(result.current.decideError).not.toBeNull());
    expect(result.current.decideError).toContain("该提案已失效");
    // 按钮必须解锁:残留的 busy 会让操作员永远点不动那条
    expect(result.current.deciding.has(PROPOSAL.dedup_id)).toBe(false);
  });
});
