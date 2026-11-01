// US-104 AI 自修复环集成测试:mock api-bridge 的 SSE 层,驱动 ai-sidebar 的
// done→validate→repair→revalidate 状态机。
// 场景:①坏 YAML → 自动发修复消息(第二次 sendAgentChat 带 repair prompt)
//       ②修复轮产出好 YAML → onApplyYaml 被调
//       ③双失败 → chat 里诚实展示"自动修复仍未通过"
//       ④好 YAML → 直接 apply,零修复轮
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";
import { AiSidebar } from "./ai-sidebar";
import type { StrategyData } from "./types";

type EventSink = (event: { type: string; data: unknown }) => void;

const sendAgentChatMock = vi.fn();
const createChatSessionMock = vi.fn();
const saveChatSessionMessagesMock = vi.fn();

vi.mock("./api-bridge", async (importOriginal) => {
  const orig = await importOriginal<typeof import("./api-bridge")>();
  return {
    ...orig,
    sendAgentChat: (...a: unknown[]) => sendAgentChatMock(...a),
    createChatSession: (...a: unknown[]) => createChatSessionMock(...a),
    saveChatSessionMessages: (...a: unknown[]) => saveChatSessionMessagesMock(...a),
    listChatSessions: () => Promise.resolve({ data: [], error: null }),
    getChatSessionMessages: () => Promise.resolve({ data: [], error: null }),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const STRATEGY: StrategyData = {
  id: "strat-r1",
  name: "Repair Target",
  type: "sma_cross",
  symbols: ["BTC/USDT"],
  interval: "1h",
} as StrategyData;

const BAD_YAML = "```yaml\nname: Broken\n```"; // 缺 type/symbols/interval
const GOOD_YAML = [
  "```yaml",
  "name: Fixed",
  "type: sma_cross",
  'symbols: ["BTC/USDT"]',
  "interval: 1h",
  "```",
].join("\n");

function chatScript(responses: string[]) {
  // 每次 sendAgentChat 调用消费下一条脚本;done 前吐 text_delta
  let call = 0;
  sendAgentChatMock.mockImplementation(
    (_msg: string, onEvent: EventSink) => {
      const content = responses[Math.min(call, responses.length - 1)];
      call++;
      const result = Promise.resolve({ sessionId: "sess-1" });
      void result.then(async () => {
        act(() => {
          onEvent({ type: "text_delta", data: { text: content } });
        });
        // text_delta 的 setMessages 要先 flush(messagesRef 在 render 时同步),
        // done 处理器读 messagesRef.current 才能看到刚到的 content
        await act(async () => {
          onEvent({ type: "done", data: {} });
        });
      });
      return result;
    },
  );
}

function renderSidebar(applyYaml: (y: string) => void) {
  return render(
    <AiSidebar
      strategyId="strat-r1"
      strategyName="Repair Target"
      strategy={STRATEGY}
      onStrategyChange={() => {}}
      onApplyYaml={applyYaml}
    />,
  );
}

beforeEach(() => {
  sendAgentChatMock.mockReset();
  createChatSessionMock.mockReset().mockResolvedValue({
    data: { id: "sess-1", title: "t" },
    error: null,
  });
  saveChatSessionMessagesMock.mockReset().mockResolvedValue({ data: null, error: null });
});

describe("US-104 YAML 自修复环", () => {
  it("好 YAML → 直接 apply,零修复轮", async () => {
    chatScript([`开头\n\n${GOOD_YAML}\n\n结尾`]);
    const applied: string[] = [];
    renderSidebar((y) => applied.push(y));
    fireEventSend("帮我生成策略");
    await waitFor(() =>
      expect(sendAgentChatMock).toHaveBeenCalled(),
    );
    await waitFor(() => expect(applied).toHaveLength(1));
    expect(applied[0]).toContain("type: sma_cross");
    expect(sendAgentChatMock).toHaveBeenCalledTimes(1);
  });

  it("坏 YAML → 自动追加修复轮(第二次 sendAgentChat 含错误与原 YAML)", async () => {
    chatScript([`修改如下\n\n${BAD_YAML}`]);
    const applied: string[] = [];
    renderSidebar((y) => applied.push(y));
    fireEventSend("帮我生成策略");
    await waitFor(() => expect(sendAgentChatMock).toHaveBeenCalledTimes(2));
    const repairMsg = sendAgentChatMock.mock.calls[1][0] as string;
    expect(repairMsg).toContain("无法应用");
    expect(repairMsg).toContain("type");
    expect(repairMsg).toContain("name: Broken");
    expect(applied).toHaveLength(0);
  });

  it("修复轮产出好 YAML → apply 被调", async () => {
    chatScript([BAD_YAML, `修好了\n\n${GOOD_YAML}`]);
    const applied: string[] = [];
    renderSidebar((y) => applied.push(y));
    fireEventSend("帮我生成策略");
    await waitFor(() => expect(applied).toHaveLength(1));
    expect(applied[0]).toContain("name: Fixed");
  });

  it("双失败 → 诚实展示「自动修复仍未通过」,不再第三轮", async () => {
    chatScript([BAD_YAML, BAD_YAML]);
    const applied: string[] = [];
    renderSidebar((y) => applied.push(y));
    fireEventSend("帮我生成策略");
    await waitFor(() =>
      expect(screen.getByText(/自动修复仍未通过/)).toBeInTheDocument(),
    );
    expect(sendAgentChatMock).toHaveBeenCalledTimes(2);
    expect(applied).toHaveLength(0);
  });
});

function fireEventSend(text: string) {
  const textarea = screen.getByPlaceholderText("描述你想做的策略...");
  fireEvent.change(textarea, { target: { value: text } });
  fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
}
