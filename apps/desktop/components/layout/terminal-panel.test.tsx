import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// B3: drawer「终端」tab = agent 循环的 GUI 投影(不是 PTY/shell)。
// 事件→行的符号语汇对齐 packages/cli/.../commands/ask.py:_render_event。
// 安全要害:写工具的 tool_approval_request 必须渲染成人点才放行的交互
// —— 终端面曾与 page 面一样漏处理,审批会静默挂起。

const h = vi.hoisted(() => ({
  emitApproval: true,
  approveCalls: [] as Array<{ sessionId: string; toolCallId: string; approve: boolean }>,
}));

vi.mock("@/lib/store/context", () => ({
  useContextStore: () => ({ pageFilter: { symbol: "BTC-USDT", tf: "1h", exchange: "binance" } }),
}));

vi.mock("@/lib/api/chat", () => ({
  sendAgentChat: vi.fn(async (_msg: string, onEvent: (e: { type: string; data: unknown }) => void) => {
    onEvent({ type: "thinking", data: { content: "先看行情" } });
    onEvent({ type: "tool_call", data: { tool: "get_klines" } });
    onEvent({ type: "tool_result", data: { summary: "返回 500 根 K 线" } });
    onEvent({ type: "text_delta", data: { text: "建议观望。" } });
    if (h.emitApproval) {
      // 真实后端在审批处暂停回合:done 只在人裁决后才来。故此处不发 done。
      const ev = {
        type: "tool_approval_request",
        data: { tool_call_id: "tc-t", tool_name: "paper_place_order", arguments: { side: "buy" } },
      };
      onEvent(ev);
      onEvent(ev);
      return { sessionId: "sess-t" };
    }
    onEvent({ type: "done", data: {} });
    return { sessionId: "sess-t" };
  }),
  approveToolCall: vi.fn(async (sessionId: string, toolCallId: string, approve: boolean) => {
    h.approveCalls.push({ sessionId, toolCallId, approve });
    return { ok: true };
  }),
}));

import { TerminalPanel } from "./terminal-panel";

async function ask(text: string) {
  render(<TerminalPanel />);
  const ta = screen.getByPlaceholderText(/向 Agent 输入指令/);
  fireEvent.change(ta, { target: { value: text } });
  fireEvent.keyDown(ta, { key: "Enter" });
}

describe("TerminalPanel — agent 循环的终端投影", () => {
  beforeEach(() => {
    h.emitApproval = true;
    h.approveCalls.length = 0;
  });

  it("回显输入,并把 thinking/tool_call/tool_result/text_delta 投影为带符号的行", async () => {
    await ask("跑一遍 bb_v3");
    // 输入回显
    expect(await screen.findByText("> 跑一遍 bb_v3")).toBeInTheDocument();
    // 对齐 ask.py 的符号:… 思考 / → tool: / ← 结果
    expect(screen.getByText(/… 先看行情/)).toBeInTheDocument();
    expect(screen.getByText(/→ tool: get_klines/)).toBeInTheDocument();
    expect(screen.getByText(/← 返回 500 根 K 线/)).toBeInTheDocument();
    // text_delta 落为亮色正文行
    expect(screen.getByText("建议观望。")).toBeInTheDocument();
  });

  it("收到 tool_approval_request → 渲染可点的 [批准]/[拒绝],同 id 只一组", async () => {
    await ask("帮我买一手");
    expect(await screen.findByText(/Agent 请求执行以下操作/)).toBeInTheDocument();
    expect(screen.getByText("paper_place_order")).toBeInTheDocument();
    expect(screen.getAllByText("paper_place_order")).toHaveLength(1);
    expect(screen.getByText(/\[批准\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[拒绝\]/)).toBeInTheDocument();
  });

  it("点 [批准] → 以 (sessionId, toolCallId, true) 调端点并清除审批块", async () => {
    await ask("帮我买一手");
    fireEvent.click(await screen.findByText(/\[批准\]/));
    await waitFor(() => expect(h.approveCalls).toHaveLength(1));
    expect(h.approveCalls[0]).toEqual({ sessionId: "sess-t", toolCallId: "tc-t", approve: true });
    await waitFor(() => expect(screen.queryByText(/Agent 请求执行以下操作/)).toBeNull());
  });

  it("点 [拒绝] → 以 approve=false 调端点", async () => {
    await ask("帮我买一手");
    fireEvent.click(await screen.findByText(/\[拒绝\]/));
    await waitFor(() => expect(h.approveCalls).toHaveLength(1));
    expect(h.approveCalls[0]).toEqual({ sessionId: "sess-t", toolCallId: "tc-t", approve: false });
  });

  it("对照:本轮无审批事件 → 不渲染审批块", async () => {
    h.emitApproval = false;
    await ask("看看行情");
    await screen.findByText("建议观望。");
    expect(screen.queryByText(/Agent 请求执行以下操作/)).toBeNull();
  });
});
