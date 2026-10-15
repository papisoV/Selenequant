"use client";

import { useState } from "react";
import useSWR from "swr";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import {
  cancelGridOrders,
  closeGridPosition,
  resetGridStats,
  getGridSentinel,
  runGridSentinel,
  gridChat,
  type SentinelStatus,
  type SentinelTool,
  type GridChatResult,
} from "@/lib/api/grid";

const EXCHANGES = ["de", "ex", "rs"] as const;
type Exchange = (typeof EXCHANGES)[number];

export function GridControlPanel() {
  const [exchange, setExchange] = useState<Exchange>("ex");
  const [busy, setBusy] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const { data: sentinel, mutate: reloadSentinel } = useSWR<SentinelStatus>(
    "/api/v1/grid/sentinel",
    getGridSentinel,
    { refreshInterval: 15000 },
  );

  const [sentinelTool, setSentinelTool] = useState<SentinelTool>("sentinel");
  const [sentinelExchange, setSentinelExchange] = useState<Exchange>("ex");
  const [sentinelOutput, setSentinelOutput] = useState<string | null>(null);

  const [chatInput, setChatInput] = useState("");
  const [chatHistory, setChatHistory] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [chatBusy, setChatBusy] = useState(false);

  async function runDangerous(op: "cancel" | "close" | "reset") {
    setBusy(op);
    setLastResult(null);
    setLastError(null);
    try {
      const res =
        op === "cancel" ? await cancelGridOrders(exchange) :
        op === "close" ? await closeGridPosition(exchange) :
        await resetGridStats(exchange);
      setLastResult(JSON.stringify(res, null, 2));
    } catch (e) {
      setLastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function runSentinelManual() {
    setBusy("sentinel-run");
    setSentinelOutput(null);
    setLastError(null);
    try {
      const res = await runGridSentinel(sentinelTool, sentinelExchange);
      setSentinelOutput(res.output ?? "(empty output)");
    } catch (e) {
      setLastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
      reloadSentinel();
    }
  }

  async function sendChat() {
    const msg = chatInput.trim();
    if (!msg || chatBusy) return;
    setChatBusy(true);
    setChatInput("");
    const userTurn = { role: "user" as const, content: msg };
    const nextHistory = [...chatHistory, userTurn];
    setChatHistory(nextHistory);
    try {
      const res: GridChatResult = await gridChat({
        message: msg,
        history: chatHistory.map((h) => ({ role: h.role, content: h.content })),
      });
      setChatHistory([...nextHistory, { role: "assistant" as const, content: res.output ?? "(no reply)" }]);
    } catch (e) {
      setChatHistory([
        ...nextHistory,
        { role: "assistant" as const, content: `ERROR: ${e instanceof Error ? e.message : String(e)}` },
      ]);
    } finally {
      setChatBusy(false);
    }
  }

  return (
    <V17Card title="Grid 引擎控制">
      <div className="text-[10px] text-ink-4 mb-2">
        exchange:
        <select
          className="ml-1 rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-ink"
          value={exchange}
          onChange={(e) => setExchange(e.target.value as Exchange)}
        >
          {EXCHANGES.map((x) => (
            <option key={x} value={x}>{x}</option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-3 gap-2 mb-2">
        <button
          onClick={() => runDangerous("cancel")}
          disabled={busy !== null}
          className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[10px] text-ink hover:bg-surface-3 disabled:opacity-50"
        >
          {busy === "cancel" ? "…" : "Cancel Orders"}
        </button>
        <button
          onClick={() => runDangerous("close")}
          disabled={busy !== null}
          className="rounded border border-neg/40 bg-neg/10 px-2 py-1 text-[10px] text-neg hover:bg-neg/20 disabled:opacity-50"
        >
          {busy === "close" ? "…" : "Close Position"}
        </button>
        <button
          onClick={() => runDangerous("reset")}
          disabled={busy !== null}
          className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[10px] text-ink hover:bg-surface-3 disabled:opacity-50"
        >
          {busy === "reset" ? "…" : "Reset Stats"}
        </button>
      </div>

      {(lastResult || lastError) && (
        <div className="mb-2 rounded border border-border-soft bg-surface-2 p-2">
          <div className="text-[9px] text-ink-4 mb-1">{lastError ? "错误" : "返回"}</div>
          <pre className={`text-[9px] font-mono whitespace-pre-wrap break-all max-h-32 overflow-auto ${lastError ? "text-neg" : "text-ink-3"}`}>
            {lastError ?? lastResult}
          </pre>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 mb-2 pt-2 border-t border-border-soft">
        <StatBlock
          label="Sentinel"
          value={sentinel?.running ? "RUNNING" : "IDLE"}
          cls={sentinel?.running ? "text-pos" : "text-ink-4"}
        />
        <StatBlock label="运行中工具" value={sentinel?.running ? String((sentinel as any)?.tool ?? "—") : "—"} />
      </div>

      <div className="mb-2">
        <div className="text-[9px] text-ink-4 mb-1">手动触发 Sentinel 工具</div>
        <div className="flex items-center gap-1">
          <select
            className="rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-[10px] text-ink"
            value={sentinelTool}
            onChange={(e) => setSentinelTool(e.target.value as SentinelTool)}
          >
            <option value="sentinel">sentinel</option>
            <option value="market">market</option>
            <option value="report">report</option>
          </select>
          <select
            className="rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-[10px] text-ink"
            value={sentinelExchange}
            onChange={(e) => setSentinelExchange(e.target.value as Exchange)}
          >
            {EXCHANGES.map((x) => (
              <option key={x} value={x}>{x}</option>
            ))}
          </select>
          <button
            onClick={runSentinelManual}
            disabled={busy !== null}
            className="rounded border border-gold/40 bg-gold/10 px-2 py-0.5 text-[10px] text-gold hover:bg-gold/20 disabled:opacity-50"
          >
            {busy === "sentinel-run" ? "…" : "Run"}
          </button>
        </div>
        {sentinelOutput && (
          <pre className="mt-1 text-[9px] font-mono text-ink-3 whitespace-pre-wrap break-all max-h-32 overflow-auto rounded border border-border-soft bg-surface-2 p-1.5">
            {sentinelOutput}
          </pre>
        )}
      </div>

      <div className="pt-2 border-t border-border-soft">
        <div className="text-[9px] text-ink-4 mb-1">Grid AI 控制助手</div>
        <div className="max-h-32 overflow-auto mb-1.5 rounded border border-border-soft bg-surface-2 p-1.5">
          {chatHistory.length === 0 ? (
            <div className="text-[9px] text-ink-4 text-center">无对话</div>
          ) : (
            chatHistory.map((m, i) => (
              <div key={i} className={`text-[10px] py-0.5 ${m.role === "user" ? "text-sky" : "text-emerald"}`}>
                <span className="font-mono text-[9px] text-ink-4 mr-1">{m.role === "user" ? "›" : "‹"}</span>
                {m.content}
              </div>
            ))
          )}
        </div>
        <div className="flex items-center gap-1">
          <input
            type="text"
            className="flex-1 rounded border border-border-soft bg-surface-2 px-1.5 py-1 text-[10px] text-ink"
            placeholder="输入指令…"
            value={chatInput}
            disabled={chatBusy}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !chatBusy && chatInput.trim()) sendChat();
            }}
          />
          <button
            onClick={sendChat}
            disabled={chatBusy || !chatInput.trim()}
            className="rounded border border-border-soft bg-surface-2 px-2 py-1 text-[10px] text-ink hover:bg-surface-3 disabled:opacity-50"
          >
            {chatBusy ? "…" : "Send"}
          </button>
        </div>
      </div>
    </V17Card>
  );
}
