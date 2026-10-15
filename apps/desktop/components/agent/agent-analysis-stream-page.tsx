"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Play, Square, Loader2 } from "lucide-react";
import { V17Card } from "@/components/shared/page-filter-bar";

// 分析流 — GET /api/v1/agent/analysis/stream/{analysis_id} (SSE)
// 事件:status{running/completed/failed}, error, done
// 动态页:analysis_id 从 URL ?analysis_id= 取,否则用户输入

interface StreamEvent {
  type?: string;
  status?: string;
  result?: unknown;
  error?: string;
  analysis_id?: string;
}

function evtCls(evt: StreamEvent): string {
  if (evt.status === "completed" || evt.type === "done") return "text-success";
  if (evt.status === "failed" || evt.type === "error") return "text-destructive";
  if (evt.status === "running") return "text-gold";
  return "text-ink-3";
}

function fmtTs(): string {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

export function AgentAnalysisStreamPage() {
  const params = useSearchParams();
  const urlId = params.get("analysis_id") ?? undefined;
  const [analysisId, setAnalysisId] = useState<string | undefined>(urlId);
  const [inputId, setInputId] = useState<string>(urlId ?? "");
  const [events, setEvents] = useState<Array<{ ts: string; text: string; cls: string }>>([]);
  const [connected, setConnected] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (urlId) {
      setAnalysisId(urlId);
      setInputId(urlId);
    }
  }, [urlId]);

  useEffect(() => {
    // 自动滚动到底
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [events]);

  const push = (text: string, cls: string) => {
    setEvents((prev) => [...prev, { ts: fmtTs(), text, cls }]);
  };

  const handleStart = async () => {
    const id = inputId.trim();
    if (!id || connected) return;
    setAnalysisId(id);
    setEvents([]);
    const controller = new AbortController();
    abortRef.current = controller;
    setConnected(true);

    try {
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      const res = await fetch(
        `/api/v1/agent/analysis/stream/${encodeURIComponent(id)}`,
        { headers, signal: controller.signal },
      );
      if (!res.ok || !res.body) {
        push(`连接失败 HTTP ${res.status}`, "text-destructive");
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6);
          try {
            const evt = JSON.parse(raw) as StreamEvent;
            const label =
              evt.type === "done"
                ? "✅ 完成"
                : evt.status === "running"
                  ? `▶ 运行中 ${evt.analysis_id ? `(${evt.analysis_id})` : ""}`
                  : evt.status === "completed"
                    ? `✅ 完成${evt.analysis_id ? ` (${evt.analysis_id})` : ""}`
                    : evt.status === "failed"
                      ? `❌ 失败: ${evt.error ?? ""}`
                      : evt.type === "error"
                        ? `⚠ 错误: ${evt.error ?? raw}`
                        : raw.slice(0, 200);
            push(label, evtCls(evt));
            if (evt.status === "completed" && evt.result != null) {
              push(
                typeof evt.result === "string"
                  ? evt.result
                  : JSON.stringify(evt.result, null, 2).slice(0, 2000),
                "text-ink-2",
              );
            }
          } catch {
            push(raw.slice(0, 200), "text-ink-4");
          }
        }
      }
      push("连接关闭", "text-ink-4");
    } catch (err) {
      const aborted = err instanceof DOMException && err.name === "AbortError";
      if (!aborted) push(`读取失败: ${err instanceof Error ? err.message : String(err)}`, "text-destructive");
    } finally {
      setConnected(false);
      abortRef.current = null;
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
    setConnected(false);
    push("已停止", "text-ink-4");
  };

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="分析流">
        <div className="flex items-center gap-2">
          <input
            value={inputId}
            onChange={(e) => setInputId(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleStart(); }}
            placeholder="输入 analysis_id"
            className="flex-1 rounded border border-border-soft bg-surface-2 px-2 py-1.5 text-[10px] text-ink placeholder:text-ink-4 focus:outline-none focus:border-gold/40"
          />
          {connected ? (
            <button
              type="button"
              onClick={handleStop}
              className="flex items-center gap-1 rounded bg-destructive/15 px-2 py-1.5 text-[10px] text-destructive hover:bg-destructive/25"
            >
              <Square className="h-3 w-3" /> 停止
            </button>
          ) : (
            <button
              type="button"
              onClick={handleStart}
              disabled={!inputId.trim()}
              className="flex items-center gap-1 rounded bg-gold/15 px-2 py-1.5 text-[10px] text-gold hover:bg-gold/25 disabled:opacity-40"
            >
              <Play className="h-3 w-3" /> 连接
            </button>
          )}
        </div>
        {connected && (
          <div className="mt-1 flex items-center gap-1.5 text-[9px] text-gold">
            <Loader2 className="h-2.5 w-2.5 animate-spin" /> 监听中…
          </div>
        )}
      </V17Card>

      <V17Card title="事件流">
        <div ref={scrollRef} className="max-h-[420px] overflow-auto rounded bg-surface-2 p-2">
          {events.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-6">
              输入 analysis_id 后点击"连接"查看分析进度
            </div>
          )}
          {events.map((e, i) => (
            <div key={i} className="flex gap-2 py-0.5 text-[10px]">
              <span className="w-16 flex-shrink-0 font-mono text-ink-4">{e.ts}</span>
              <span className={`whitespace-pre-wrap break-all ${e.cls}`}>{e.text}</span>
            </div>
          ))}
        </div>
      </V17Card>
    </div>
  );
}
