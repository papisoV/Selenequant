// B0 确认门 —— ai-sidebar 与其余 agent 面同源。历史缺陷:本面 onEvent 只认
// thinking/tool_call/tool_result/reflection,收到 tool_approval_request 直接丢
// —— 后端 producer 阻塞等裁决、流不关,人不审批就静默挂到 TTL 超时。
//
// 另外一个只在真流下才暴露的时序问题:X-Session-ID 随响应头先到,而 session id
// 的 state 更新要等 re-render。审批卡在流未结束时就要可点,若 getSessionId() 此刻
// 读不到 id,点「批准」会静默 no-op。故 sendAgentChat 的 onSessionId 回调必须接通。
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { AiSidebar } from "./ai-sidebar";
import type { StrategyData } from "./types";

type EventSink = (event: { type: string; data: unknown }) => void;
type SendOpts = { resume?: boolean; onSessionId?: (sid: string) => void };

const sendAgentChatMock = vi.fn();
const approveToolCallMock = vi.fn();
const createChatSessionMock = vi.fn();

vi.mock("./api-bridge", async (importOriginal) => {
  const orig = await importOriginal<typeof import("./api-bridge")>();
  return {
    ...orig,
    sendAgentChat: (...a: unknown[]) => sendAgentChatMock(...a),
    createChatSession: (...a: unknown[]) => createChatSessionMock(...a),
    saveChatSessionMessages: () => Promise.resolve({ data: null, error: null }),
    listChatSessions: () => Promise.resolve({ data: [], error: null }),
    getChatSessionMessages: () => Promise.resolve({ data: [], error: null }),
  };
});

vi.mock("@/lib/api/chat", async (importOriginal) => {
  const orig = await importOriginal<typeof import("@/lib/api/chat")>();
  return { ...orig, approveToolCall: (...a: unknown[]) => approveToolCallMock(...a) };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const STRATEGY: StrategyData = {
  id: "strat-a1",
  name: "Approval Target",
  type: "sma_cross",
  symbols: ["BTC/USDT"],
  interval: "1h",
} as StrategyData;

const APPROVAL_HEADING = /Agent 请求执行以下操作，需要确认/;

function renderSidebar() {
  return render(
    <AiSidebar
      strategyId="strat-a1"
      strategyName="Approval Target"
      strategy={STRATEGY}
      onStrategyChange={() => {}}
    />,
  );
}

function fireEventSend(text: string) {
  const textarea = screen.getByPlaceholderText("描述你想做的策略...");
  fireEvent.change(textarea, { target: { value: text } });
  fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
}

/** 模拟真流:响应头先到(onSessionId)→ 再吐事件。
 *  刻意不发 done —— 生产里申请审批时 producer 阻塞等裁决,流保持开启,
 *  done 只在裁决之后才到。发 done 会把待审批卡清掉(useToolApproval 归约)。 */
function sseScript(events: Array<{ type: string; data: unknown }>) {
  sendAgentChatMock.mockImplementation(
    (_msg: string, onEvent: EventSink, _ctx: unknown, _sid: unknown, _sig: unknown, opts?: SendOpts) => {
      const result = Promise.resolve({ sessionId: "sess-a1" });
      void result.then(async () => {
        await act(async () => {
          opts?.onSessionId?.("sess-a1");
          for (const ev of events) onEvent(ev);
        });
      });
      return result;
    },
  );
}

beforeEach(() => {
  sendAgentChatMock.mockReset();
  approveToolCallMock.mockReset();
  approveToolCallMock.mockResolvedValue({ ok: true });
  createChatSessionMock.mockReset().mockResolvedValue({
    data: { id: "sess-a1", title: "t" },
    error: null,
  });
});

describe("AiSidebar — B0 工具确认门(本面曾完全不处理审批事件)", () => {
  it("收到 tool_approval_request → 渲染确认卡(工具名+参数),同 id 只一张", async () => {
    const ev = {
      type: "tool_approval_request",
      data: { tool_call_id: "tc-a1", tool_name: "paper_place_order", arguments: { symbol: "BTCUSDT" } },
    };
    sseScript([ev, ev]); // 重连重放同一事件,须按 tool_call_id 去重
    renderSidebar();
    fireEventSend("帮我下单");

    expect(await screen.findByText(APPROVAL_HEADING)).toBeInTheDocument();
    expect(screen.getByText("paper_place_order")).toBeInTheDocument();
    expect(screen.getByText(/BTCUSDT/)).toBeInTheDocument();
    expect(screen.getAllByText("paper_place_order")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "批准" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeInTheDocument();
  });

  it("点『批准』→ 以 (sessionId, toolCallId, true) 调端点", async () => {
    sseScript([
      { type: "tool_approval_request", data: { tool_call_id: "tc-a1", tool_name: "paper_place_order", arguments: {} } },
    ]);
    renderSidebar();
    fireEventSend("帮我下单");
    fireEvent.click(await screen.findByRole("button", { name: "批准" }));

    await waitFor(() => expect(approveToolCallMock).toHaveBeenCalledTimes(1));
    expect(approveToolCallMock.mock.calls[0]).toEqual(["sess-a1", "tc-a1", true]);
  });

  it("点『拒绝』→ 以 approve=false 调端点", async () => {
    sseScript([
      { type: "tool_approval_request", data: { tool_call_id: "tc-a1", tool_name: "paper_place_order", arguments: {} } },
    ]);
    renderSidebar();
    fireEventSend("帮我下单");
    fireEvent.click(await screen.findByRole("button", { name: "拒绝" }));

    await waitFor(() => expect(approveToolCallMock).toHaveBeenCalledTimes(1));
    expect(approveToolCallMock.mock.calls[0]).toEqual(["sess-a1", "tc-a1", false]);
  });

  it("onSessionId 回调已接通(审批卡在流未结束时就能拿到 session id)", async () => {
    sseScript([
      { type: "tool_approval_request", data: { tool_call_id: "tc-a1", tool_name: "paper_place_order", arguments: {} } },
    ]);
    renderSidebar();
    fireEventSend("帮我下单");
    await screen.findByText(APPROVAL_HEADING);

    const opts = sendAgentChatMock.mock.calls[0][5] as SendOpts | undefined;
    expect(typeof opts?.onSessionId).toBe("function");
  });

  it("对照:无审批事件 → 不渲染确认卡", async () => {
    sseScript([{ type: "text_delta", data: { text: "好的" } }]);
    renderSidebar();
    fireEventSend("帮我下单");

    await waitFor(() => expect(sendAgentChatMock).toHaveBeenCalled());
    expect(screen.queryByText(APPROVAL_HEADING)).toBeNull();
  });
});
