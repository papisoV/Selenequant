"use client";

import { useCallback, useState, type MouseEvent as ReactMouseEvent } from "react";
import {
  listChatSessions,
  getChatSessionMessages,
  deleteChatSession,
  type ChatSession,
  type ChatMessageRecord,
} from "@/lib/api/chat";
import type { ChatMessage } from "@/components/layout/agent-chat/message-bubble";
import type { ReasoningStep } from "@/components/layout/agent-chat/reasoning-chain";

// 历史会话侧栏的共享状态机 —— FAB 弹层(AgentChatPanel)与独立页(AgentChatPage)
// 同源消费。两端此前各写一份:FAB 有、页没有(plan §六.3 的 B2 验收项「对话页能看
// 到历史会话侧栏」因此落空)。列会话 / 载入 / 删除 / 新对话的归约逻辑只留这里。
//
// 只负责数据与状态,不负责渲染 —— 两面的设计语言不同(FAB 整面切换 / 页内左栏)。

export interface UseChatHistoryOptions {
  /** 当前会话 id(用于高亮、「删除当前会话后清空」判定)。 */
  currentSessionId?: string;
  /** 载入历史会话 —— 交给 useAgentChat.loadMessages。 */
  loadMessages: (messages: ChatMessage[], sessionId: string) => void;
  /** 清空当前会话(新对话 / 删除的就是当前会话时)。 */
  clear: () => void;
}

export interface UseChatHistoryResult {
  showHistory: boolean;
  sessions: ChatSession[];
  historyLoading: boolean;
  /** 打开侧栏并拉取列表。 */
  openHistory: () => void;
  closeHistory: () => void;
  /** 新对话:清空当前会话并关上侧栏。 */
  newChat: () => void;
  /** 载入一个历史会话(拉消息 → loadMessages → 关侧栏)。 */
  loadSession: (sessionId: string) => Promise<void>;
  /** 删除一个历史会话;删的是当前会话时一并 clear。 */
  deleteSession: (sessionId: string, e: ReactMouseEvent) => Promise<void>;
}

export function useChatHistory({
  currentSessionId,
  loadMessages,
  clear,
}: UseChatHistoryOptions): UseChatHistoryResult {
  const [showHistory, setShowHistory] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const data = await listChatSessions(undefined, 50);
      setSessions(data);
    } catch {
      // 拉不到列表不该把侧栏变成错误页 —— 留空列表,用户可重开侧栏重试。
    }
    setHistoryLoading(false);
  }, []);

  const openHistory = useCallback(() => {
    setShowHistory(true);
    void loadHistory();
  }, [loadHistory]);

  const closeHistory = useCallback(() => setShowHistory(false), []);

  const newChat = useCallback(() => {
    clear();
    setShowHistory(false);
  }, [clear]);

  const loadSession = useCallback(
    async (sessionId: string) => {
      try {
        const data = await getChatSessionMessages(sessionId, 200);
        const loaded: ChatMessage[] = data.map((r: ChatMessageRecord) => ({
          id: r.id,
          role: r.role,
          content: r.content,
          timestamp: new Date(r.created_at).getTime(),
          reasoningSteps: Array.isArray(r.extra?.reasoningSteps)
            ? (r.extra.reasoningSteps as ReasoningStep[])
            : undefined,
        }));
        loadMessages(loaded, sessionId);
      } catch {
        // 载入失败保持当前会话不动,侧栏照关 —— 比切到空白会话更可恢复。
      }
      setShowHistory(false);
    },
    [loadMessages],
  );

  const deleteSession = useCallback(
    async (sessionId: string, e: ReactMouseEvent) => {
      e.stopPropagation();
      try {
        await deleteChatSession(sessionId);
      } catch {
        // 与原 FAB 面一致:删除失败也把行移出本地列表(重开侧栏会重新拉取)。
      }
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      if (currentSessionId === sessionId) clear();
    },
    [currentSessionId, clear],
  );

  return {
    showHistory,
    sessions,
    historyLoading,
    openHistory,
    closeHistory,
    newChat,
    loadSession,
    deleteSession,
  };
}
