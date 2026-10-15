"use client";

import React, { useCallback, useMemo, useRef, useEffect } from "react";
import { useContextStore } from "@/lib/store/context";
import { useAgentChat } from "@/lib/hooks/use-agent-chat";
import type { AgentChatContext } from "@/lib/api/chat";

// 终端面板 —— agent 循环的第三张脸(`selenespace ask` 的 GUI 投影)。
//
// 定位(正典 docs/DESIGN.md §8.7):不是 PTY/shell,是 Selene 自己对象模型上的
// 命令行面。同一 react loop、同一 ToolCatalog、同一 SSE 事件流;此处只换渲染,
// 不换大脑。事件→行的映射对齐 `packages/cli/.../commands/ask.py:_render_event`
// 的符号语汇(text 亮 / 思考 `…` / 工具 `→` / 结果 `←` / 反思 `⋯`)。
//
// B0 确认门:写工具会在 tool_call 与 tool_result 之间插入 `tool_approval_request`。
// 历史缺陷是终端面(与 page 面)不处理它 → 审批静默挂起。此处渲染成人点才放行的
// 交互,与 FAB 面板同源(都用 useAgentChat),不各自实现一份。

type TermKind = "in" | "out" | "tool" | "result" | "think" | "reflect" | "err";

interface TermLine {
  kind: TermKind;
  text: string;
}

const MAX_LINES = 500;

export function TerminalPanel() {
  const { pageFilter } = useContextStore();
  const linesRef = useRef<HTMLDivElement>(null);

  const buildContext = useCallback(
    (): AgentChatContext => ({
      symbol: pageFilter.symbol,
      timeframe: pageFilter.tf,
      exchange: pageFilter.exchange,
      intent: "terminal",
    }),
    [pageFilter],
  );

  // 事件 → 行。终端要逐事件而不是整理后的消息,故走 onEvent 旁路。
  const [lines, setLines] = React.useState<TermLine[]>([]);
  // 流式 text_delta 累积成一条「正在生成」的行,遇非文本事件才落盘。
  const [streaming, setStreaming] = React.useState("");
  const streamingRef = useRef("");

  const onEvent = useCallback((event: { type: string; data: unknown }) => {
    const d =
      typeof event.data === "object" && event.data !== null
        ? (event.data as Record<string, unknown>)
        : {};

    const flush = () => {
      const text = streamingRef.current.trim();
      streamingRef.current = "";
      setStreaming("");
      if (text) {
        setLines((prev) => {
          const next = [...prev, { kind: "out" as TermKind, text }];
          return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
        });
      }
    };
    const push = (kind: TermKind, text: string) => {
      if (!text) return;
      setLines((prev) => {
        const next = [...prev, { kind, text }];
        return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
      });
    };

    switch (event.type) {
      case "text_delta": {
        const chunk = typeof d.text === "string" ? d.text : "";
        if (chunk) {
          streamingRef.current += chunk;
          setStreaming(streamingRef.current);
        }
        break;
      }
      case "thinking": {
        flush();
        push("think", `… ${typeof d.content === "string" ? d.content : ""}`.trim());
        break;
      }
      case "tool_call": {
        flush();
        push("tool", `→ tool: ${(d.tool as string) ?? (d.name as string) ?? "?"}`);
        break;
      }
      case "tool_result": {
        flush();
        const summary = String(d.summary ?? d.result ?? d.output ?? "").slice(0, 200);
        if (summary) push("result", `← ${summary}`);
        break;
      }
      case "reflection": {
        flush();
        push("reflect", `⋯ ${typeof d.content === "string" ? d.content : ""}`.trim());
        break;
      }
      case "error": {
        flush();
        push("err", `✗ ${typeof event.data === "string" ? event.data : "请求出错"}`);
        break;
      }
      case "done":
        flush();
        break;
      default:
        break;
    }
  }, []);

  const {
    input,
    setInput,
    loading,
    pendingApprovals,
    approvingIds,
    approvalError,
    send,
    approve,
    scrollRef,
    textareaRef,
  } = useAgentChat({ buildContext, onEvent });

  // 输入回显:.term-line(in)是亮色,由组件在此落。
  const submit = useCallback(async () => {
    const text = input.trim();
    if (!text || loading) return;
    setLines((prev) => [...prev, { kind: "in", text: `> ${text}` }]);
    streamingRef.current = "";
    setStreaming("");
    await send();
  }, [input, loading, send]);

  // 合并两处滚动容器(hook 持 scrollRef 供消息面;终端用 linesRef)
  useEffect(() => {
    const el = linesRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines, pendingApprovals, loading]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void submit();
      }
    },
    [submit],
  );

  const approvalBlock = useMemo(() => {
    if (pendingApprovals.length === 0) return null;
    return (
      <div className="term-line out" style={{ paddingLeft: 0 }}>
        <div style={{ color: "var(--color-warn)" }}>⚠ Agent 请求执行以下操作，需要确认</div>
        {approvalError && <div style={{ color: "var(--color-destructive)" }}>{approvalError}</div>}
        {pendingApprovals.map((p) => (
          <div key={p.toolCallId} style={{ marginTop: 2 }}>
            <div style={{ color: "var(--color-warn)" }}>{p.toolName}</div>
            <div style={{ color: "var(--color-ink-4)", paddingLeft: 12 }}>
              {Object.keys(p.arguments).length > 0 ? JSON.stringify(p.arguments) : "（无参数）"}
            </div>
            <div style={{ paddingLeft: 12, marginTop: 2, display: "flex", gap: 8 }}>
              <button
                type="button"
                disabled={approvingIds.has(p.toolCallId)}
                onClick={() => void approve(p.toolCallId, true)}
                style={{
                  color: "var(--color-success)",
                  background: "none",
                  border: "1px solid var(--color-border-soft)",
                  borderRadius: 3,
                  padding: "1px 8px",
                  cursor: "pointer",
                  font: "inherit",
                }}
              >
                [批准]
              </button>
              <button
                type="button"
                disabled={approvingIds.has(p.toolCallId)}
                onClick={() => void approve(p.toolCallId, false)}
                style={{
                  color: "var(--color-destructive)",
                  background: "none",
                  border: "1px solid var(--color-border-soft)",
                  borderRadius: 3,
                  padding: "1px 8px",
                  cursor: "pointer",
                  font: "inherit",
                }}
              >
                [拒绝]
              </button>
            </div>
          </div>
        ))}
      </div>
    );
  }, [pendingApprovals, approvingIds, approvalError, approve]);

  return (
    <div className="terminal-panel">
      <div ref={linesRef} className="term-lines" style={{ flex: 1, overflowY: "auto" }}>
        {lines.length === 0 && !loading && pendingApprovals.length === 0 && (
          <div className="term-line out">
            问 Agent 操控 Selene · 输入自然语言（如「跑一遍 bb_v3 回测」）· Enter 发送
          </div>
        )}
        {lines.map((l, i) => (
          <div
            key={i}
            className={l.kind === "in" ? "term-line" : `term-line out${l.kind === "err" ? " err" : ""}`}
            style={l.kind === "err" ? { color: "var(--color-destructive)" } : undefined}
          >
            {l.text}
          </div>
        ))}
        {streaming && <div className="term-line out">{streaming}</div>}
        {loading && <div className="term-line out"><span className="cursor">▊</span></div>}
        {approvalBlock}
      </div>
      <div className="term-input-row" style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 4 }}>
        <span style={{ color: "var(--color-pos)" }}>&gt;</span>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder="向 Agent 输入指令（Enter 发送，Shift+Enter 换行）"
          className="term-input"
          style={{
            flex: 1,
            resize: "none",
            background: "transparent",
            border: "none",
            outline: "none",
            color: "var(--color-ink)",
            font: "inherit",
            padding: 0,
          }}
        />
      </div>
      {/* scrollRef 归 hook 所有,终端用 linesRef;此隐藏锚点让 hook 的自动滚动
          不会因 ref 未挂而报空 —— 实际滚动由上方 effect 处理。 */}
      <div ref={scrollRef} style={{ display: "none" }} />
    </div>
  );
}
