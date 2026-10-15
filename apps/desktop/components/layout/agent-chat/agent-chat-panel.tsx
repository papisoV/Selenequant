"use client";

import React, { useState, useCallback, useEffect } from "react";
import { usePathname } from "next/navigation";
import { X, Send, Trash2, Minus, History, Plus, ChevronLeft, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  saveChatSessionMessages,
  type AgentChatContext,
} from "@/lib/api/chat";
import { useContextStore } from "@/lib/store/context";
import { useAuth } from "@/components/auth/AuthProvider";
import { useAgentChat } from "@/lib/hooks/use-agent-chat";
import { useChatHistory } from "@/lib/hooks/use-chat-history";
import { MessageBubble, TypingIndicator } from "./message-bubble";
import type { ChatMessage } from "./message-bubble";

interface AgentChatPanelProps {
  onClose: () => void;
}

interface SkillRow {
  name: string;
  description: string;
  source: string;
  enabled: boolean;
}

/** Loaded-methodology visibility row (0930 audit P0): the skills pipeline
 * silently shapes every agent answer; surface it or users never know. */
function useLoadedSkills() {
  const [skills, setSkills] = useState<SkillRow[] | null>(null);
  useEffect(() => {
    let alive = true;
    // 页面首载请求风暴易把 /api/v1/skills 打成 429,单次 fetch 会永久空列表;429 时 60s 后重试一次
    const load = (retryOn429 = true): Promise<void> =>
      fetch("/api/v1/skills").then((r) => {
        if (r.status === 429 && retryOn429) {
          return new Promise<void>((resolve) => setTimeout(() => resolve(load(false)), 60_000));
        }
        return r.json().then((d: { skills?: SkillRow[] }) => {
          if (alive) setSkills(Array.isArray(d.skills) ? d.skills : []);
        });
      });
    load().catch(() => {
      if (alive) setSkills([]);
    });
    return () => {
      alive = false;
    };
  }, []);
  return skills;
}

export function AgentChatPanel({ onClose }: AgentChatPanelProps) {
  const { isAuthenticated, authEnabled } = useAuth();
  const { pageFilter } = useContextStore();
  const pathname = usePathname();
  const skills = useLoadedSkills();
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [showAuthPrompt, setShowAuthPrompt] = useState(false);

  const buildContext = useCallback(
    (): AgentChatContext => ({
      symbol: pageFilter.symbol,
      timeframe: pageFilter.tf,
      exchange: pageFilter.exchange,
    }),
    [pageFilter],
  );

  // 每轮结束自动落盘(FAB 面专属行为;独立页不传此回调)
  const onTurnComplete = useCallback(
    (msgs: ChatMessage[], sid: string | undefined) => {
      if (!sid) return;
      saveChatSessionMessages(
        sid,
        msgs.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          extra: m.reasoningSteps ? { reasoningSteps: m.reasoningSteps } : {},
        })),
      ).catch(() => {});
    },
    [],
  );

  const {
    messages,
    input,
    setInput,
    loading,
    sessionId,
    pendingApprovals,
    approvingIds,
    approvalError,
    send,
    approve,
    clear,
    loadMessages,
    scrollRef,
    textareaRef,
  } = useAgentChat({ buildContext, onTurnComplete });

  const needsAuth = authEnabled && !isAuthenticated;

  // 历史会话侧栏 —— 与独立页(AgentChatPage)同源 hook。
  const {
    showHistory,
    sessions,
    historyLoading,
    openHistory: handleShowHistory,
    closeHistory: handleCloseHistory,
    newChat: handleNewChat,
    loadSession: handleLoadSession,
    deleteSession: handleDeleteSession,
  } = useChatHistory({ currentSessionId: sessionId, loadMessages, clear });

  const handleSend = useCallback(() => {
    if (needsAuth) {
      setShowAuthPrompt(true);
      return;
    }
    void send();
  }, [needsAuth, send]);

  const handleClear = useCallback(() => {
    clear();
  }, [clear]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  // Global keyboard shortcut: Ctrl/Cmd + J to toggle
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "j") {
        e.preventDefault();
        onClose();
      }
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div className="flex flex-col w-full h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2.5">
          {showHistory ? (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={handleCloseHistory}
              title="返回对话"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </Button>
          ) : (
            <div className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center">
              <Sparkles className="h-5 w-5 opacity-80" />
            </div>
          )}
          <div>
            <h3 className="text-sm font-semibold">
              {showHistory ? "历史会话" : "Agent 助手"}
            </h3>
            <p className="text-[11px] text-muted-foreground leading-none mt-0.5">
              {showHistory ? "" : loading ? "思考中..." : "交易策略 AI 助手"}
            </p>
            {!showHistory && skills !== null && skills.length > 0 && (
              <button
                type="button"
                onClick={() => setSkillsOpen((v) => !v)}
                className="text-[10px] text-muted-foreground/80 hover:text-foreground leading-none mt-1 underline decoration-dotted underline-offset-2"
              >
                📖 已加载 {skills.length} 个方法论技能
              </button>
            )}
            {!showHistory && skillsOpen && skills !== null && (
              <div className="mt-1.5 max-h-40 overflow-y-auto rounded border border-border bg-surface-2 p-1.5 space-y-1">
                {skills.map((s) => (
                  <div key={s.name} className="text-[9.5px] leading-snug">
                    <span
                      className={cn(
                        "mr-1 rounded px-1 py-px text-[8px]",
                        s.source === "user"
                          ? "bg-primary/15 text-primary"
                          : "bg-surface-3 text-ink-3",
                      )}
                    >
                      {s.source === "user" ? "用户" : "内置"}
                    </span>
                    <span className={cn("font-medium", !s.enabled && "opacity-50")}>
                      {s.name}
                    </span>
                    {s.description && (
                      <span className="text-muted-foreground"> — {s.description}</span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {!showHistory && (
            <>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={handleNewChat}
                title="新对话"
              >
                <Plus className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={handleShowHistory}
                title="历史会话"
              >
                <History className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={handleClear}
                title="清空"
                disabled={messages.length === 0}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onClose}
            title="最小化"
          >
            <Minus className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onClose}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* History Sidebar */}
      {showHistory ? (
        <div className="flex-1 overflow-y-auto">
          {historyLoading ? (
            <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
              加载中...
            </div>
          ) : sessions.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-2 text-muted-foreground">
              <History className="h-8 w-8 opacity-30" />
              <p className="text-sm">暂无历史会话</p>
            </div>
          ) : (
            <div className="py-2 divide-y divide-border">
              {sessions.map((s) => (
                <div
                  key={s.id}
                  className={cn(
                    "w-full text-left px-4 py-3 hover:bg-muted/60 transition-colors flex items-start justify-between gap-2 cursor-pointer",
                    s.id === sessionId && "bg-muted/40"
                  )}
                  onClick={() => handleLoadSession(s.id)}
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium truncate">
                      {s.title || s.id.slice(0, 16)}
                    </p>
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      {new Date(s.updated_at).toLocaleDateString()}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
                    onClick={(e) => handleDeleteSession(s.id, e)}
                    title="删除会话"
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
      <>
        <div className="flex-1 overflow-y-auto px-4" ref={scrollRef}>
          <div className="py-4 space-y-3">
            {messages.length === 0 && (
              <div className="text-center py-12 space-y-3">
                <div className="mx-auto h-12 w-12 rounded-full bg-primary/10 flex items-center justify-center">
                  <Sparkles className="h-8 w-8 opacity-80" />
                </div>
                <div>
                  <p className="text-sm font-medium">你好，我是交易助手</p>
                  <p className="text-xs text-muted-foreground mt-1 max-w-[260px] mx-auto">
                    可以问我策略、分析行情、解释指标
                  </p>
                </div>
                <div className="flex flex-wrap gap-2 justify-center pt-2">
                  {[
                    "推荐一个策略",
                    "分析当前品种",
                    "解释 RSI 指标",
                  ].map((q) => (
                    <button
                      key={q}
                      className="text-xs px-3 py-1.5 rounded-full border border-border hover:bg-muted transition-colors"
                      onClick={() => {
                        setInput(q);
                        textareaRef.current?.focus();
                      }}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((msg, idx) => (
              <MessageBubble key={msg.id} msg={msg} isGenerating={loading && idx === messages.length - 1} />
            ))}
            {loading && <TypingIndicator />}
          </div>
        </div>

        {showAuthPrompt && (
          <div className="mx-4 mb-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2.5 flex items-center gap-2.5">
            <span className="text-sm text-amber-200">需要登录才能使用 Agent</span>
            <a href={`/login?return=${encodeURIComponent(pathname || "/dashboard")}`} className="ml-auto shrink-0 rounded-md bg-primary px-3 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors">
              登录
            </a>
            <button onClick={() => setShowAuthPrompt(false)} className="text-muted-foreground hover:text-foreground text-xs">✕</button>
          </div>
        )}

        {pendingApprovals.length > 0 && (
          <div className="mx-4 mb-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2.5">
            <p className="text-sm font-medium text-amber-200 mb-1.5">
              Agent 请求执行以下操作，需要确认
            </p>
            {approvalError && (
              <p className="text-xs text-destructive mb-1.5">{approvalError}</p>
            )}
            <div className="space-y-2">
              {pendingApprovals.map((p) => (
                <div key={p.toolCallId} className="rounded-md bg-surface-2 border border-border px-2.5 py-2">
                  <p className="text-xs font-medium text-foreground">{p.toolName}</p>
                  <p className="text-[10px] text-muted-foreground mt-0.5 break-all line-clamp-3">
                    {Object.keys(p.arguments).length > 0
                      ? JSON.stringify(p.arguments)
                      : "（无参数）"}
                  </p>
                  <div className="flex gap-2 mt-2">
                    <Button
                      size="sm"
                      className="h-6 px-2.5 text-xs"
                      disabled={approvingIds.has(p.toolCallId)}
                      onClick={() => void approve(p.toolCallId, true)}
                    >
                      批准
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-2.5 text-xs text-muted-foreground hover:text-destructive"
                      disabled={approvingIds.has(p.toolCallId)}
                      onClick={() => void approve(p.toolCallId, false)}
                    >
                      拒绝
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Input */}
        <div className="px-4 py-3 border-t border-border shrink-0">
          <form
            className="flex gap-2 items-end"
            onSubmit={(e) => {
              e.preventDefault();
              handleSend();
            }}
          >
            <textarea
              ref={textareaRef}
              placeholder={needsAuth ? "登录后开始对话" : "问 Agent… (品种上下文感知)"}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                const el = e.target;
                el.style.height = "0";
                el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
              }}
              onKeyDown={handleKeyDown}
              disabled={loading}
              rows={1}
              className="flex-1 max-h-[200px] min-h-[40px] resize-none overflow-y-auto rounded-xl bg-muted/50 border-0 px-3 py-2.5 text-sm leading-[1.6] text-foreground shadow-none outline-none placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-ring"
            />
            <Button
              type="submit"
              size="icon"
              className="h-10 w-10 rounded-xl shrink-0 mb-0.5"
              disabled={loading || !input.trim()}
            >
              <Send className="h-4 w-4" />
            </Button>
          </form>
          <p className="text-[10px] text-muted-foreground text-center mt-2">
            Ctrl+J 切换
          </p>
        </div>
      </>
      )}
    </div>
  );
}
