"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { sendAgentChat, type AgentChatContext } from "@/lib/api/chat";
import { useToolApproval, type PendingApproval } from "@/lib/hooks/use-tool-approval";
import type { ChatMessage } from "@/components/layout/agent-chat/message-bubble";
import type { ReasoningStep } from "@/components/layout/agent-chat/reasoning-chain";

// Agent 对话的共享状态机 —— FAB 弹层(AgentChatPanel)、独立页(AgentChatPage)、
// 终端(terminal-panel)同源消费,避免多份实现漂移:历史上 page 面漏了 B0 审批
// 处理,审批请求会静默挂起。
//
// 审批卡生命周期本身抽到 useToolApproval(三面共用);此处只负责消息/流式/gamma。
// 只负责状态与 SSE 事件归约,不负责渲染 —— 各面设计语言不同,各自渲染。

export type { PendingApproval };

export interface UseAgentChatOptions {
  /** 每轮发送时构造上下文(品种/周期/交易所…);调用方决定取哪个 store。 */
  buildContext: () => AgentChatContext;
  /** 首轮是否沿用已加载的会话 id(历史会话续聊)。 */
  initialSessionId?: string;
  /** 每轮结束时回调(带最终消息与解析出的 session id)。FAB 面用它自动落盘,页不关心。 */
  onTurnComplete?: (messages: ChatMessage[], sessionId: string | undefined) => void;
  /**
   * 原始 SSE 事件旁路 —— 终端面把同一路事件投影成 `.term-line`。
   * 面板/页不需要(它们读 messages/pendingApprovals);终端需要逐事件而非整理后的消息。
   */
  onEvent?: (event: { type: string; data: unknown }) => void;
}

export interface UseAgentChatResult {
  messages: ChatMessage[];
  input: string;
  setInput: (value: string) => void;
  /** 一轮进行中(含流式)。 */
  loading: boolean;
  /** 派生状态文案(思考中 / 调用工具 / 重连中…),无状态时为 null。 */
  status: string | null;
  sessionId: string | undefined;
  /** B0 确认门:待人工裁决的工具调用。 */
  pendingApprovals: PendingApproval[];
  approvingIds: Set<string>;
  approvalError: string | null;
  /** 发送当前 input(空/进行中时 no-op)。 */
  send: () => Promise<void>;
  /** 裁决一个待审批工具调用。 */
  approve: (toolCallId: string, approve: boolean) => Promise<void>;
  /** 中止本轮流(用户点停止)。 */
  stop: () => void;
  /** 清空消息与会话。 */
  clear: () => void;
  /** 用历史会话消息替换当前列表。 */
  loadMessages: (messages: ChatMessage[], sessionId: string) => void;
  scrollRef: React.RefObject<HTMLDivElement | null>;
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
}

function uid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `id-${Math.random().toString(16).slice(2)}`;
}

export function useAgentChat(options: UseAgentChatOptions): UseAgentChatResult {
  const { buildContext, initialSessionId, onTurnComplete, onEvent } = options;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | undefined>(initialSessionId);

  // sessionId 的最新值:approve 需要在流结束前就读到(见 useToolApproval 注释)。
  const sessionIdRef = useRef<string | undefined>(initialSessionId);
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  const {
    pendingApprovals,
    approvingIds,
    approvalError,
    handleApprovalEvent,
    approve,
    reset: resetApproval,
  } = useToolApproval({ getSessionId: () => sessionIdRef.current });

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // buildContext 每次渲染都是新函数,但 send 只应在调用时读取最新闭包 →
  // 用 ref 固定,避免把 buildContext 塞进 send 依赖导致 send 身份每轮变化。
  const buildContextRef = useRef(buildContext);
  useEffect(() => {
    buildContextRef.current = buildContext;
  }, [buildContext]);

  // 同理 onTurnComplete:调用方常传内联函数,固定以免 send 依赖漂移。
  const onTurnCompleteRef = useRef(onTurnComplete);
  useEffect(() => {
    onTurnCompleteRef.current = onTurnComplete;
  }, [onTurnComplete]);

  // 同理 onEvent(终端面的事件投影旁路)。
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading, scrollToBottom]);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "";
    const userMsg: ChatMessage = {
      id: uid(),
      role: "user",
      content: text,
      timestamp: Date.now(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);
    setStatus("思考中…");

    const controller = new AbortController();
    abortRef.current = controller;

    let response = "";
    let hasError = false;
    let memoryUsed: number | undefined;
    const assistantId = uid();
    const reasoningSteps: ReasoningStep[] = [];

    function updateAssistantMessage(extra?: { id?: string; text?: string; meta?: string }) {
      const id = extra?.id ?? assistantId;
      setMessages((prev) => {
        const updated = [...prev];
        const lastIdx = updated.findIndex((m) => m.id === id);
        const assistantMsg: ChatMessage = {
          id,
          role: "assistant",
          content: extra?.text ?? response,
          timestamp: Date.now(),
          reasoningSteps: [...reasoningSteps],
          memoryUsed,
        };
        if (lastIdx >= 0) updated[lastIdx] = assistantMsg;
        else updated.push(assistantMsg);
        return updated;
      });
    }

    const chatResult = await sendAgentChat(
      text,
      (event) => {
        // 旁路优先:终端面要的是原样事件流,不受下方归约分支增删影响。
        onEventRef.current?.(event);

        const data =
          typeof event.data === "object" && event.data !== null
            ? (event.data as Record<string, unknown>)
            : {};

        if (
          event.type === "thinking" ||
          event.type === "tool_call" ||
          event.type === "reflection"
        ) {
          reasoningSteps.push({
            type: event.type as ReasoningStep["type"],
            data,
            timestamp: Date.now(),
          });
          setStatus(event.type === "thinking" ? "推理中…" : event.type === "reflection" ? "反思中…" : statusText(data));
          updateAssistantMessage();
        } else if (event.type === "tool_result") {
          // 既归约进推理链,又清除对应待审批卡片。
          // (旧 panel 实现把 tool_result 先判进 isReasoningEvent,「清卡」分支成死代码 ——
          //  这里合并两件事,避免审批卡在 tool_result 到达后仍滞留直到 done。)
          reasoningSteps.push({
            type: "tool_result",
            data,
            timestamp: Date.now(),
          });
          updateAssistantMessage();
          handleApprovalEvent("tool_result", data);
        } else if (
          event.type === "text_delta" ||
          event.type === "text" ||
          event.type === "content"
        ) {
          const chunk = typeof data.text === "string" ? data.text : event.data;
          response += String(chunk ?? "");
          setStatus(null);
          updateAssistantMessage();
        } else if (event.type === "error") {
          hasError = true;
          setStatus(typeof event.data === "string" ? event.data : "请求出错");
        } else if (event.type === "tool_approval_request") {
          handleApprovalEvent("tool_approval_request", data);
        } else if (event.type === "done") {
          handleApprovalEvent("done", data);
          setStatus(null);
          const mu = data.memory_used;
          if (typeof mu === "number" && mu > 0) memoryUsed = mu;
        } else if (event.type === "reconnecting") {
          const attempt = typeof data.attempt === "number" ? data.attempt : "?";
          setStatus(`连接中断，重连中 (${attempt}/3)…`);
        } else if (event.type === "reconnected") {
          setStatus("已重连…");
        }
      },
      buildContextRef.current(),
      sessionId,
      controller.signal,
      {
        // 流未结束就先落 id:X-Session-ID 随响应头到,此时审批卡已可点,
        // 后端 producer 正 await 那个 Future。等到流收尾才落 → 首轮批准 no-op。
        onSessionId: (sid) => {
          sessionIdRef.current = sid;
          setSessionId(sid);
        },
      },
    );

    // sendAgentChat 是网络边界:异常/降级实现可能返回 undefined,不能假定形状。
    if (chatResult?.sessionId) {
      sessionIdRef.current = chatResult.sessionId;
      setSessionId(chatResult.sessionId);
    }

    if (!response) {
      setMessages((prev) => [
        ...prev,
        {
          id: assistantId,
          role: "assistant",
          content: hasError ? "发生错误" : "无响应",
          timestamp: Date.now(),
        },
      ]);
    }

    setLoading(false);
    setStatus(null);
    abortRef.current = null;

    // 落盘回调:在 updater 内读取最新消息(此时本轮所有 setMessages 已入队,
    // 队列按序执行到这一步即最终列表)。幂等(PUT 同一份列表),StrictMode 双调无害。
    const finalSessionId = chatResult?.sessionId ?? sessionIdRef.current;    setMessages((prev) => {
      onTurnCompleteRef.current?.(prev, finalSessionId);
      return prev;
    });
  }, [input, loading, sessionId, handleApprovalEvent]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    setStatus("已停止");
  }, []);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setMessages([]);
    setSessionId(undefined);
    sessionIdRef.current = undefined;
    setStatus(null);
    resetApproval();
  }, [resetApproval]);

  const loadMessages = useCallback((loaded: ChatMessage[], sid: string) => {
    setMessages(loaded);
    setSessionId(sid);
    sessionIdRef.current = sid;
    resetApproval();
  }, [resetApproval]);

  return {
    messages,
    input,
    setInput,
    loading,
    status,
    sessionId,
    pendingApprovals,
    approvingIds,
    approvalError,
    send,
    approve,
    stop,
    clear,
    loadMessages,
    scrollRef,
    textareaRef,
  };
}

function statusText(d: Record<string, unknown>): string {
  return typeof d.tool === "string" ? `调用工具 ${d.tool}` : "调用工具";
}
