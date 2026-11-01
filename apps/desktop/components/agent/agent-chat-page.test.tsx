import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";

// B2: AgentChatPage 与 FAB 弹层同源(useAgentChat)。历史缺陷:page 面缺 B0 审批处理
// ——收到 tool_approval_request 静默挂起,人永远看不到确认卡。本测锁死「页也能审批」。

const h = vi.hoisted(() => ({
  emitApproval: true,
  approveResult: { ok: true } as { ok: boolean; errorCode?: string },
  approveCalls: [] as Array<{ sessionId: string; toolCallId: string; approve: boolean }>,
  // 事件脚本:默认只发审批;推理链用例覆盖自己的脚本。
  script: null as null | Array<{ type: string; data: unknown }>,
  // 历史会话侧栏(B2 验收项)
  sessions: [] as Array<{ id: string; title: string; created_at: string; updated_at: string; strategy_id: string | null }>,
  sessionMessages: [] as Array<{ id: string; session_id: string; role: string; content: string; extra: Record<string, unknown>; created_at: string }>,
  deletedSessions: [] as string[],
  listCalls: 0,
}));

vi.mock("@/lib/store/market", () => ({
  useMarketStore: () => ({ pageFilter: { symbol: "BTC-USDT", tf: "1h", exchange: "binance" } }),
}));
vi.mock("@/components/shared/page-filter-bar", () => ({
  V17Card: ({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>
      {right}
      {children}
    </div>
  ),
}));
vi.mock("@/components/agent/agent-analysis-status-page", () => ({
  AgentAnalysisStatusPage: () => <div />,
}));
vi.mock("@/components/agent/agent-analysis-stream-page", () => ({
  AgentAnalysisStreamPage: () => <div />,
}));

vi.mock("@/lib/api/chat", () => ({
  sendAgentChat: vi.fn(async (_msg: string, onEvent: (e: { type: string; data: unknown }) => void) => {
    if (h.script) {
      for (const ev of h.script) onEvent(ev);
      return { sessionId: "sess-9" };
    }
    if (h.emitApproval) {
      const ev = {
        type: "tool_approval_request",
        data: {
          tool_call_id: "tc-9",
          tool_name: "strategy_deploy",
          arguments: { strategy: "bb_v3" },
        },
      };
      onEvent(ev);
      onEvent(ev); // 重放:须按 tool_call_id 去重
    }
    return { sessionId: "sess-9" };
  }),
  approveToolCall: vi.fn(async (sessionId: string, toolCallId: string, approve: boolean) => {
    h.approveCalls.push({ sessionId, toolCallId, approve });
    return h.approveResult;
  }),
  // 历史会话侧栏(plan §六.3 B2 验收项):page 面须能列出/载入/删除历史会话。
  listChatSessions: vi.fn(async () => {
    h.listCalls += 1;
    return h.sessions;
  }),
  getChatSessionMessages: vi.fn(async () => h.sessionMessages),
  deleteChatSession: vi.fn(async (sid: string) => {
    h.deletedSessions.push(sid);
    return { status: "ok" };
  }),
}));

import { AgentChatPage } from "./agent-chat-page";

const APPROVAL_HEADING = /Agent 请求执行以下操作，需要确认/;

async function driveTurn() {
  render(<AgentChatPage />);
  const ta = screen.getByPlaceholderText(/向 AI 提问/);
  fireEvent.change(ta, { target: { value: "部署 bb_v3" } });
  fireEvent.click(screen.getByText("发送"));
}

describe("AgentChatPage — B0 确认门(page 面曾漏,须与 FAB 面同源)", () => {
  beforeEach(() => {
    h.emitApproval = true;
    h.approveResult = { ok: true };
    h.approveCalls.length = 0;
    h.script = null;
    h.sessions = [];
    h.sessionMessages = [];
    h.deletedSessions.length = 0;
    h.listCalls = 0;
  });

  it("收到 tool_approval_request → 渲染确认卡(工具名+参数),同 id 只一张", async () => {
    await driveTurn();
    expect(await screen.findByText(APPROVAL_HEADING)).toBeInTheDocument();
    expect(screen.getAllByText("strategy_deploy")).toHaveLength(1);
    expect(screen.getByText(/"strategy":"bb_v3"/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批准" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeInTheDocument();
  });

  it("点『批准』→ 以 (sessionId, toolCallId, true) 调端点,ok 后卡片清除", async () => {
    await driveTurn();
    fireEvent.click(await screen.findByRole("button", { name: "批准" }));
    await waitFor(() => expect(h.approveCalls).toHaveLength(1));
    expect(h.approveCalls[0]).toEqual({ sessionId: "sess-9", toolCallId: "tc-9", approve: true });
    await waitFor(() => expect(screen.queryByText(APPROVAL_HEADING)).toBeNull());
  });

  it("点『拒绝』→ 以 approve=false 调端点", async () => {
    await driveTurn();
    fireEvent.click(await screen.findByRole("button", { name: "拒绝" }));
    await waitFor(() => expect(h.approveCalls).toHaveLength(1));
    expect(h.approveCalls[0]).toEqual({ sessionId: "sess-9", toolCallId: "tc-9", approve: false });
  });

  it("对照:本轮无审批事件 → 不渲染确认卡", async () => {
    h.emitApproval = false;
    await driveTurn();
    await screen.findByText("无响应");
    expect(screen.queryByText(APPROVAL_HEADING)).toBeNull();
  });

  it("推理链按后端契约字段渲染(name/arguments/output,非 tool/args/result)", async () => {
    // 回归锁:renderStepLine 曾读 d.tool/d.args/d.result —— 后端实际发
    // name/arguments/output,导致工具名与结果静默渲染为空。
    h.emitApproval = false;
    h.script = [
      { type: "thinking", data: {} },
      { type: "tool_call", data: { name: "get_klines", arguments: { symbol: "BTC" } } },
      { type: "tool_result", data: { name: "get_klines", output: "500 bars" } },
      { type: "text_delta", data: { text: "看多。" } },
    ];
    await driveTurn();
    expect(await screen.findByText(/get_klines/)).toBeInTheDocument();
    expect(screen.getByText(/500 bars/)).toBeInTheDocument();
  });
});

describe("AgentChatPage — 历史会话侧栏(plan §六.3 B2 验收项)", () => {
  beforeEach(() => {
    h.emitApproval = false;
    h.script = null;
    h.sessions = [
      { id: "sess-a", title: "布林带讨论", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-02T00:00:00Z", strategy_id: null },
      { id: "sess-b", title: "", created_at: "2026-10-03T00:00:00Z", updated_at: "2026-10-04T00:00:00Z", strategy_id: null },
    ];
    h.sessionMessages = [
      { id: "m-1", session_id: "sess-a", role: "user", content: "上次问到哪了", extra: {}, created_at: "2026-10-02T00:00:00Z" },
      { id: "m-2", session_id: "sess-a", role: "assistant", content: "停在止损位", extra: {}, created_at: "2026-10-02T00:00:01Z" },
    ];
    h.deletedSessions.length = 0;
    h.listCalls = 0;
  });

  it("点『历史』→ 打开侧栏并列出会话;无标题回落到 id 前 16 位", async () => {
    render(<AgentChatPage />);
    fireEvent.click(screen.getByText("历史"));

    expect(await screen.findByText("布林带讨论")).toBeInTheDocument();
    expect(screen.getByText("sess-b")).toBeInTheDocument();
    expect(h.listCalls).toBe(1);
  });

  it("点一个会话 → 拉该会话消息并回到对话面展示", async () => {
    render(<AgentChatPage />);
    fireEvent.click(screen.getByText("历史"));
    fireEvent.click(await screen.findByText("布林带讨论"));

    expect(await screen.findByText("上次问到哪了")).toBeInTheDocument();
    expect(screen.getByText("停在止损位")).toBeInTheDocument();
    // 侧栏已关(回到对话面标题)
    expect(screen.queryByText("历史会话")).toBeNull();
  });

  it("点会话行的删除按钮 → 调删除端点,且不打开该会话", async () => {
    render(<AgentChatPage />);
    fireEvent.click(screen.getByText("历史"));
    const row = (await screen.findByText("布林带讨论")).closest("div")!.parentElement!;
    fireEvent.click(within(row).getByTitle("删除会话"));

    await waitFor(() => expect(h.deletedSessions).toEqual(["sess-a"]));
    // stopPropagation 生效:没被当成"打开会话"
    expect(screen.queryByText("上次问到哪了")).toBeNull();
    await waitFor(() => expect(screen.queryByText("布林带讨论")).toBeNull());
  });

  it("侧栏内点『返回对话』→ 回到对话面(不载入任何会话)", async () => {
    render(<AgentChatPage />);
    fireEvent.click(screen.getByText("历史"));
    await screen.findByText("布林带讨论");
    fireEvent.click(screen.getByText("返回对话"));

    expect(screen.queryByText("历史会话")).toBeNull();
    // V17Card 把 title 渲染成 data-testid,不落到文本节点 —— 按 testid 断言回到对话面
    expect(screen.getByTestId(/^card-Agent 对话/)).toBeInTheDocument();
    expect(screen.queryByText("上次问到哪了")).toBeNull();
  });

  it("对照:不开侧栏 → 不拉会话列表", async () => {
    render(<AgentChatPage />);
    expect(screen.queryByText("历史会话")).toBeNull();
    expect(h.listCalls).toBe(0);
  });
});
