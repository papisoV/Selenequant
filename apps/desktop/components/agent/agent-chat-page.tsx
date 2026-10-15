"use client";

import { useCallback, useState } from "react";
import { Send, Square, RefreshCw, Sparkles, Plus, History, ChevronLeft, Trash2 } from "lucide-react";
import { useMarketStore } from "@/lib/store/market";
import { useAgentChat } from "@/lib/hooks/use-agent-chat";
import { useChatHistory } from "@/lib/hooks/use-chat-history";
import { V17Card } from "@/components/shared/page-filter-bar";
import { AgentAnalysisStatusPage } from "@/components/agent/agent-analysis-status-page";
import { AgentAnalysisStreamPage } from "@/components/agent/agent-analysis-stream-page";

// Agent Chat 主面板 — POST /api/v1/agent/chat (SSE 流式)
// 状态机与 FAB 弹层同源(useAgentChat),此处只负责 v17 卡片渲染。
// 事件类型:thinking / tool_call / tool_result / reflection / text_delta /
// done / error / reconnecting / reconnected / tool_approval_request

function bare(s: string): string {
  return s.replace(/[-/:].*$/, "");
}

export function AgentChatPage() {
  const { pageFilter } = useMarketStore();
  const symbol = bare(pageFilter.symbol) || "BTC";
  const tf = pageFilter.tf || "1h";

  const [toolsOpen, setToolsOpen] = useState<"status" | "stream" | null>(null);

  const buildContext = useCallback(
    () => ({
      symbol,
      timeframe: tf,
      exchange: pageFilter.exchange || "binance",
      market_type: "futures",
      intent: "market_analysis",
    }),
    [symbol, tf, pageFilter.exchange],
  );

  const {
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
  } = useAgentChat({ buildContext });

  // 历史会话侧栏(plan §六.3 B2 验收项) —— 与 FAB 面同源 hook,避免两份实现漂移。
  const {
    showHistory,
    sessions,
    historyLoading,
    openHistory,
    closeHistory,
    newChat,
    loadSession,
    deleteSession,
  } = useChatHistory({ currentSessionId: sessionId, loadMessages, clear });

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  if (showHistory) {
    return (
      <div className="v17-page-grid grid gap-2 h-full">
        <V17Card
          title="历史会话"
          right={
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={closeHistory}
                className="flex items-center gap-1 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:text-ink-2"
              >
                <ChevronLeft className="h-2.5 w-2.5" /> 返回对话
              </button>
            </div>
          }
        >
          <div className="h-[52vh] overflow-auto rounded bg-surface-2">
            {historyLoading ? (
              <div className="flex h-full items-center justify-center text-[11px] text-ink-4">
                加载中…
              </div>
            ) : sessions.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-ink-4">
                <History className="h-6 w-6 opacity-30" />
                <p className="text-[11px]">暂无历史会话</p>
              </div>
            ) : (
              <div className="divide-y divide-border-soft">
                {sessions.map((s) => (
                  <div
                    key={s.id}
                    onClick={() => void loadSession(s.id)}
                    className={`flex cursor-pointer items-start justify-between gap-2 px-3 py-2 transition-colors hover:bg-surface-3 ${
                      s.id === sessionId ? "bg-surface-3" : ""
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[11px] font-medium text-ink">
                        {s.title || s.id.slice(0, 16)}
                      </p>
                      <p className="mt-0.5 text-[9px] text-ink-4">
                        {new Date(s.updated_at).toLocaleDateString()}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={(e) => void deleteSession(s.id, e)}
                      title="删除会话"
                      className="shrink-0 rounded p-1 text-ink-4 hover:text-neg"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </V17Card>
      </div>
    );
  }

  return (
    <div className="v17-page-grid grid gap-2 h-full">
      <V17Card
        title={`Agent 对话 · ${symbol} ${tf}`}
        right={
          <div className="flex items-center gap-2">
            {status && <span className="text-[9px] text-gold animate-pulse">{status}</span>}
            {loading && (
              <button
                type="button"
                onClick={stop}
                className="flex items-center gap-1 rounded bg-neg/15 px-1.5 py-0.5 text-[9px] text-neg hover:bg-neg/25"
              >
                <Square className="h-2.5 w-2.5" /> 停止
              </button>
            )}
            <button
              type="button"
              onClick={openHistory}
              title="历史会话"
              className="flex items-center gap-1 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:text-ink-2"
            >
              <History className="h-2.5 w-2.5" /> 历史
            </button>
            <button
              type="button"
              onClick={newChat}
              title="新对话"
              className="flex items-center gap-1 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:text-ink-2"
            >
              <Plus className="h-2.5 w-2.5" /> 新对话
            </button>
            <button
              type="button"
              onClick={clear}
              className="flex items-center gap-1 rounded bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3 hover:text-ink-2"
            >
              <RefreshCw className="h-2.5 w-2.5" /> 清空
            </button>
          </div>
        }
      >
        <div className="flex h-[52vh] flex-col">
          <div className="flex-1 overflow-auto rounded bg-surface-2 p-2" ref={scrollRef}>
            {messages.length === 0 && (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-ink-4">
                <Sparkles className="h-6 w-6 text-gold/50" />
                <p className="text-[11px]">向 AI 交易助手提问 · 示例</p>
                <div className="flex flex-col gap-1 text-[10px]">
                  <button
                    type="button"
                    className="rounded bg-surface-3 px-2 py-1 text-ink-3 hover:bg-sky-500/10 hover:text-sky-400"
                    onClick={() => { setInput(`分析 ${symbol} 当前 ${tf} 周期行情与关键价位`); textareaRef.current?.focus(); }}
                  >
                    分析 {symbol} 当前行情
                  </button>
                  <button
                    type="button"
                    className="rounded bg-surface-3 px-2 py-1 text-ink-3 hover:bg-sky-500/10 hover:text-sky-400"
                    onClick={() => { setInput(`基于技术指标给出 ${symbol} 交易建议与风险提示`); textareaRef.current?.focus(); }}
                  >
                    交易建议与风险
                  </button>
                </div>
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`mb-2 flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[85%] rounded px-2 py-1.5 text-[10px] whitespace-pre-wrap break-words ${
                    m.role === "user"
                      ? "bg-sky-500/15 text-sky-200"
                      : "bg-surface-3 text-ink"
                  }`}
                >
                  {m.reasoningSteps && m.reasoningSteps.length > 0 && (
                    <div className="mb-1 font-mono text-[8px] text-ink-4 border-b border-border-soft pb-1 whitespace-pre-wrap">
                      {m.reasoningSteps.map((step, i) => (
                        <div key={i}>{renderStepLine(step)}</div>
                      ))}
                    </div>
                  )}
                  {m.content || (m.role === "assistant" && loading ? "…" : "")}
                </div>
              </div>
            ))}
          </div>

          {pendingApprovals.length > 0 && (
            <div className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5">
              <p className="mb-1 text-[10px] font-medium text-amber-200">
                Agent 请求执行以下操作，需要确认
              </p>
              {approvalError && <p className="mb-1 text-[9px] text-neg">{approvalError}</p>}
              <div className="space-y-1.5">
                {pendingApprovals.map((p) => (
                  <div key={p.toolCallId} className="rounded bg-surface-2 border border-border-soft px-2 py-1.5">
                    <p className="text-[10px] font-medium text-ink">{p.toolName}</p>
                    <p className="mt-0.5 text-[9px] text-ink-4 break-all line-clamp-3 font-mono">
                      {Object.keys(p.arguments).length > 0 ? JSON.stringify(p.arguments) : "（无参数）"}
                    </p>
                    <div className="mt-1.5 flex gap-1.5">
                      <button
                        type="button"
                        disabled={approvingIds.has(p.toolCallId)}
                        onClick={() => void approve(p.toolCallId, true)}
                        className="rounded bg-gold/15 px-2 py-0.5 text-[10px] text-gold hover:bg-gold/25 disabled:opacity-40"
                      >
                        批准
                      </button>
                      <button
                        type="button"
                        disabled={approvingIds.has(p.toolCallId)}
                        onClick={() => void approve(p.toolCallId, false)}
                        className="rounded bg-surface-3 px-2 py-0.5 text-[10px] text-ink-3 hover:text-neg disabled:opacity-40"
                      >
                        拒绝
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="mt-2 flex items-end gap-2">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={2}
              placeholder={`向 AI 提问 ${symbol}（Enter 发送，Shift+Enter 换行）`}
              className="flex-1 resize-none rounded border border-border-soft bg-surface-2 px-2 py-1.5 text-[10px] text-ink placeholder:text-ink-4 focus:outline-none focus:border-gold/40"
            />
            <button
              type="button"
              onClick={() => void send()}
              disabled={!input.trim() || loading}
              className="flex items-center gap-1 rounded bg-gold/15 px-2.5 py-1.5 text-[10px] text-gold hover:bg-gold/25 disabled:opacity-40"
            >
              <Send className="h-3 w-3" /> 发送
            </button>
          </div>
        </div>
      </V17Card>

      {/* K42/K43(判定表 r5):分析状态=进度指示、分析流=会话工具区,并入 agent 页 */}
      <div className="flex items-center gap-2">
        <span className="text-[9px] text-ink-4">分析工具</span>
        <button
          type="button"
          onClick={() => setToolsOpen((v) => (v === "status" ? null : "status"))}
          className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-2 hover:border-gold/50 hover:text-gold transition-colors"
        >
          分析状态
        </button>
        <button
          type="button"
          onClick={() => setToolsOpen((v) => (v === "stream" ? null : "stream"))}
          className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-2 hover:border-gold/50 hover:text-gold transition-colors"
        >
          分析流
        </button>
      </div>
      {toolsOpen === "status" && (
        <div className="min-h-[280px]">
          <AgentAnalysisStatusPage />
        </div>
      )}
      {toolsOpen === "stream" && (
        <div className="min-h-[280px]">
          <AgentAnalysisStreamPage />
        </div>
      )}
    </div>
  );
}

/** 推理链一行 — 对齐 ask.py:_render_event 的符号语汇。
 *  字段名以后端事件契约为准:react.py / runtime.py 的 TOOL_CALL 发
 *  `{name, arguments}`,TOOL_RESULT 发 `{name, output}`;THINKING/REFLECTION 的
 *  data 是空对象(正文走 text_delta)。 */
function renderStepLine(step: { type: string; data: Record<string, unknown> }): string {
  const d = step.data;
  if (step.type === "tool_call") {
    const args = d.arguments ? JSON.stringify(d.arguments).slice(0, 120) : "";
    return `🔧 ${d.name ?? ""} ${args}`.trim();
  }
  if (step.type === "tool_result") {
    return `▸ ${String(d.output ?? d.error ?? "").slice(0, 200)}`;
  }
  if (step.type === "thinking") return "… 思考";
  if (step.type === "reflection") return "⋯ 反思";
  return "";
}
