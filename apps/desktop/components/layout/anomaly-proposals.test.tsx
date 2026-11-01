import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";

// 底部抽屉依赖一堆 store / ws / 拖拽 / cmdk,本测试只关心异动待批条的行为。
// mock 清单与既有 bottom-drawer.test.tsx 保持一致。
vi.mock("zustand/react/shallow", () => ({ useShallow: (fn: unknown) => fn }));
vi.mock("@/lib/store/context", () => ({
  useContextStore: () => ({ setSymBase: vi.fn(), pageFilter: {} }),
}));
vi.mock("@/lib/store/paper", () => ({
  usePaperStore: (sel?: (s: unknown) => unknown) =>
    sel ? sel({ positions: [], orders: [], fills: [] }) : {},
}));
vi.mock("@/lib/store/live-trading", () => ({
  useLiveTradingStore: (sel?: (s: unknown) => unknown) =>
    sel ? sel({ positions: [], orders: [], fills: [] }) : {},
}));
vi.mock("@/lib/store/trading-mode", () => ({
  useTradingModeStore: (sel?: (s: unknown) => unknown) =>
    sel ? sel({ mode: "paper" }) : { mode: "paper" },
}));
vi.mock("@/lib/hooks/use-account-id", () => ({ useAccountId: () => "acc-1" }));
vi.mock("@/lib/ws/use-intelligence-ws", () => ({
  useIntelligenceWs: () => ({ state: { streamState: "connecting" } }),
}));
vi.mock("@/lib/hooks/use-resize", () => ({
  useResize: () => ({ onMouseDown: vi.fn(), onDoubleClick: vi.fn() }),
}));
vi.mock("@/lib/toast", () => ({
  toastSuccess: vi.fn(), toastError: vi.fn(), toastInfo: vi.fn(),
  useToastContainer: () => ({ toasts: [] }),
}));
vi.mock("@/lib/api-client", () => ({
  api: vi.fn(async () => []),
  apiPost: vi.fn(async () => null),
  apiDelete: vi.fn(async () => null),
}));
vi.mock("./cmdk-panel", () => ({ CmdkPanel: () => null }));
vi.mock("./agent-chat/agent-chat-panel", () => ({
  AgentChatPanel: () => <div data-testid="agent-chat-panel">panel</div>,
}));

import { BottomDrawer } from "./bottom-drawer";
import { apiPost } from "@/lib/api-client";

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

function envelope(data: unknown): Response {
  return new Response(
    JSON.stringify({ data, meta: { timestamp: "2026-10-14T02:00:00Z" } }),
    { status: 200, headers: { "Content-Type": "application/json" } }
  );
}

const stubProposals = (body: unknown) =>
  vi.stubGlobal("fetch", vi.fn(async () => envelope(body)));

/**
 * 每个用例一个空 SWR 缓存。
 *
 * 不换 provider 的话第二个用例会直接吃上一条的缓存 —— fetch 一次不调、
 * 界面上还挂着上一个用例的提案,断言测的是空气(badge 用例就这样假绿过)。
 */
function wrapper({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      {children}
    </SWRConfig>
  );
}

const renderDrawer = () => render(<BottomDrawer />, { wrapper });

/** 打开抽屉的「事件流」tab —— 待批条落在这里。 */
function openEventsTab() {
  const el = Array.from(document.querySelectorAll(".dtab")).find((e) =>
    (e.textContent ?? "").includes("事件流")
  );
  if (!el) throw new Error("未找到「事件流」tab");
  fireEvent.click(el);
}

beforeEach(() => {
  vi.mocked(apiPost).mockResolvedValue(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("异动待批条 — 只在有料/出错时现身", () => {
  it("suggest 档有待批提案 → 渲染卡片,并明写「不下单」", async () => {
    stubProposals({ proposals: [PROPOSAL], mode: "suggest", wired: true });

    renderDrawer();
    openEventsTab();

    await waitFor(() => expect(document.querySelector(".ap-row")).toBeTruthy());
    // 诚实声明是这块面板的存在理由:操作员不能以为点批准=成交
    expect(screen.getByText(/批准仅记录人工确认,不下单/)).toBeInTheDocument();
    expect(document.querySelector(".ap-sym")?.textContent).toBe("300750.SZ");
  });

  it("observe 档无提案 → 明说档位是 observe,不装成「暂无」", async () => {
    stubProposals({ proposals: [], mode: "observe", wired: true });

    renderDrawer();
    openEventsTab();

    await waitFor(() =>
      expect(document.querySelector(".ap-empty")?.textContent).toContain("observe")
    );
  });

  it("密码货币-only 部署(wired=false)→ 整块不出现", async () => {
    stubProposals({ proposals: [], mode: null, wired: false });

    renderDrawer();
    openEventsTab();

    // 等一次读完成,确认不是"还在加载所以没有"
    await waitFor(() =>
      expect(document.querySelector(".event-subtabs")).toBeTruthy()
    );
    expect(document.querySelector(".anomaly-proposals")).toBeNull();
  });

  it("读接口挂掉 → 明说「不可达」,不降级成「暂无待批」", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            data: null,
            meta: { timestamp: "2026-10-14T02:00:00Z" },
            error: { code: "internal_error", message: "决策消费器炸了" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      )
    );

    renderDrawer();
    openEventsTab();

    await waitFor(() =>
      expect(document.querySelector(".ap-error")?.textContent).toContain("不可达")
    );
    // 关键:不能说"暂无待批"——后端挂了不是没有提案
    expect(document.querySelector(".ap-empty")).toBeNull();
  });
});

describe("异动待批条 — 裁定按钮", () => {
  it("点「批准」→ POST approve=true 且带正确 dedup_id", async () => {
    stubProposals({ proposals: [PROPOSAL], mode: "suggest", wired: true });

    renderDrawer();
    openEventsTab();
    await waitFor(() => expect(document.querySelector(".ap-row")).toBeTruthy());

    fireEvent.click(screen.getByText("批准"));

    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    expect(apiPost).toHaveBeenCalledWith("/api/v1/anomaly/approve", {
      dedup_id: PROPOSAL.dedup_id,
      approve: true,
    });
  });

  it("点「拒绝」→ POST approve=false", async () => {
    stubProposals({ proposals: [PROPOSAL], mode: "suggest", wired: true });

    renderDrawer();
    openEventsTab();
    await waitFor(() => expect(document.querySelector(".ap-row")).toBeTruthy());

    fireEvent.click(screen.getByText("拒绝"));

    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    expect(apiPost).toHaveBeenCalledWith("/api/v1/anomaly/approve", {
      dedup_id: PROPOSAL.dedup_id,
      approve: false,
    });
  });

  it("点「批准」→ 调 apiPost(批准路径,参数正确)", async () => {
    // 边界:apiPost 在本文件被 mock,所以这条**不**覆盖真网络。它证明的是
    // 「点击 → 调用 apiPost(正确路径 + 参数)」这一段。真网络路径由两侧夹住:
    // 客户端 envelope 解析在 lib/api-client.test.ts,端点行为在后端
    // test_api_anomaly_approve.py(ASGI in-process,同样不是真 socket)。
    // 此用例显式记录边界,标题不再声称"打到网络"——避免"以为测了网络"。
    stubProposals({ proposals: [PROPOSAL], mode: "suggest", wired: true });

    renderDrawer();
    openEventsTab();
    await waitFor(() => expect(document.querySelector(".ap-row")).toBeTruthy());

    fireEvent.click(screen.getByText("批准"));

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith("/api/v1/anomaly/approve", {
      dedup_id: PROPOSAL.dedup_id,
      approve: true,
    });
  });
});

describe("异动待批条 — 折叠态可见性", () => {
  it("抽屉折叠时,待批数挂在「事件流」tab badge 上", async () => {
    stubProposals({ proposals: [PROPOSAL], mode: "suggest", wired: true });

    renderDrawer();

    // 默认折叠(不点开 tab),badge 就该出现 —— 否则提案要等人碰巧展开抽屉
    await waitFor(() => {
      const eventsTab = Array.from(document.querySelectorAll(".dtab")).find((e) =>
        (e.textContent ?? "").includes("事件流")
      );
      expect(eventsTab?.querySelector(".dtab-badge")?.textContent).toBe("1");
    });
  });

  it("无待批提案时不挂 badge(不制造假红点)", async () => {
    stubProposals({ proposals: [], mode: "suggest", wired: true });

    renderDrawer();

    await waitFor(() =>
      expect(document.querySelector(".event-subtabs") || document.querySelector(".dtab"))
        .toBeTruthy()
    );
    const eventsTab = Array.from(document.querySelectorAll(".dtab")).find((e) =>
      (e.textContent ?? "").includes("事件流")
    );
    expect(eventsTab?.querySelector(".dtab-badge")).toBeNull();
  });
});
