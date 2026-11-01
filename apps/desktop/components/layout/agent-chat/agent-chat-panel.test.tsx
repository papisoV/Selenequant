import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// jsdom 未必暴露 crypto.randomUUID(组件为消息 id 调用它)
if (!globalThis.crypto?.randomUUID) {
  Object.defineProperty(globalThis, "crypto", {
    value: { ...globalThis.crypto, randomUUID: () => `id-${Math.random().toString(16).slice(2)}` },
    configurable: true,
  });
}

const h = vi.hoisted(() => ({
  emitApproval: true,
  approveResult: { ok: true } as { ok: boolean; errorCode?: string },
  approveCalls: [] as Array<{ sessionId: string; toolCallId: string; approve: boolean }>,
}));

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({ isAuthenticated: true, authEnabled: false }),
}));
vi.mock("@/lib/store/context", () => ({ useContextStore: () => ({ pageFilter: {} }) }));

vi.mock("@/lib/api/chat", () => ({
  sendAgentChat: vi.fn(async (_msg: string, onEvent: (e: { type: string; data: unknown }) => void) => {
    if (h.emitApproval) {
      const ev = {
        type: "tool_approval_request",
        data: {
          tool_call_id: "tc-1",
          tool_name: "paper_place_order",
          arguments: { symbol: "BTCUSDT", side: "buy", qty: 1 },
        },
      };
      // 后端 SSE 重连会重放同一事件;组件须按 tool_call_id 去重
      onEvent(ev);
      onEvent(ev);
    }
    return { sessionId: "sess-1" };
  }),
  approveToolCall: vi.fn(async (sessionId: string, toolCallId: string, approve: boolean) => {
    h.approveCalls.push({ sessionId, toolCallId, approve });
    return h.approveResult;
  }),
  listChatSessions: vi.fn(async () => []),
  getChatSessionMessages: vi.fn(async () => []),
  deleteChatSession: vi.fn(async () => {}),
  saveChatSessionMessages: vi.fn(async () => {}),
}));

import { AgentChatPanel } from "./agent-chat-panel";

const APPROVAL_HEADING = /Agent 请求执行以下操作，需要确认/;

/** 驱动一轮对话:输入文本 → 回车提交 → 等 sendAgentChat 回放审批事件。 */
async function driveTurn() {
  render(<AgentChatPanel onClose={() => {}} />);
  const ta = screen.getByPlaceholderText(/问 Agent/);
  fireEvent.change(ta, { target: { value: "帮我买一手 BTC" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  return ta;
}

describe("AgentChatPanel — B0 工具确认门 UI(后端强制,模型不可自签)", () => {
  beforeEach(() => {
    h.emitApproval = true;
    h.approveResult = { ok: true };
    h.approveCalls.length = 0;
  });

  it("收到 tool_approval_request → 渲染琥珀色确认卡,展示工具名与参数,且同一 id 只渲染一张", async () => {
    await driveTurn();

    expect(await screen.findByText(APPROVAL_HEADING)).toBeInTheDocument();
    expect(screen.getByText("paper_place_order")).toBeInTheDocument();
    expect(screen.getByText(/BTCUSDT/)).toBeInTheDocument();
    // 事件回放两次,去重后只有一张卡
    expect(screen.getAllByText("paper_place_order")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "批准" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeInTheDocument();
  });

  it("点『批准』→ 以 (sessionId, toolCallId, true) 调端点,ok 后卡片清除", async () => {
    await driveTurn();
    fireEvent.click(await screen.findByRole("button", { name: "批准" }));

    await waitFor(() => expect(h.approveCalls).toHaveLength(1));
    expect(h.approveCalls[0]).toEqual({ sessionId: "sess-1", toolCallId: "tc-1", approve: true });
    await waitFor(() => expect(screen.queryByText(APPROVAL_HEADING)).toBeNull());
  });

  it("点『拒绝』→ 以 approve=false 调端点", async () => {
    await driveTurn();
    fireEvent.click(await screen.findByRole("button", { name: "拒绝" }));

    await waitFor(() => expect(h.approveCalls).toHaveLength(1));
    expect(h.approveCalls[0]).toEqual({ sessionId: "sess-1", toolCallId: "tc-1", approve: false });
  });

  it("端点回 no_pending_approval(重放/超时已被处理)→ 静默清除卡片,不报红", async () => {
    h.approveResult = { ok: false, errorCode: "no_pending_approval" };
    await driveTurn();
    fireEvent.click(await screen.findByRole("button", { name: "批准" }));

    await waitFor(() => expect(screen.queryByText(APPROVAL_HEADING)).toBeNull());
    expect(screen.queryByText(/审批请求失败/)).toBeNull();
  });

  it("对照:本轮不产生审批事件 → 不渲染确认卡", async () => {
    h.emitApproval = false;
    await driveTurn();

    // 等轮次收尾(出现"无响应"占位),再断言无卡
    await screen.findByText("无响应");
    expect(screen.queryByText(APPROVAL_HEADING)).toBeNull();
  });
});
