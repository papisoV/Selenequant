"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import { getChatSession, getChatSessionMessages, type ChatSession as ApiChatSession, type ChatMessageRecord } from "@/lib/api/chat";
import { Loader2, Info } from "lucide-react";
import { useUiText } from "@/components/i18n/use-ui-text"

// 业务域插入页 — Agent 会话
// 后端 chat_sessions 表(迁移 v003)仅: id / strategy_id / title / created_at / updated_at
// (无 message_count / last_active 列,列表端点不做 JOIN)
interface ChatSession {
  id?: string;
  title?: string;
  created_at?: string;
  updated_at?: string;
}

export function ChatSessionsPage() {
  const tr = useUiText()

  const { data } = useApi<ChatSession[] | null>(
    "/api/v1/chat/sessions"
  );
  const rows = data ?? [];

  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailSession, setDetailSession] = useState<ApiChatSession | null>(null);
  const [detailMessages, setDetailMessages] = useState<ChatMessageRecord[]>([]);

  async function openSessionDetail(sessionId: string) {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetailSession(null);
    setDetailMessages([]);
    try {
      const [sess, msgs] = await Promise.all([
        getChatSession(sessionId),
        getChatSessionMessages(sessionId, 200),
      ]);
      setDetailSession(sess);
      setDetailMessages(msgs);
    } catch {
      setDetailSession(null);
      setDetailMessages([]);
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title={tr("会话概览")}>
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(2, 1fr)" }}>
          <StatBlock label={tr("总会话")} value={String(rows.length)} />
          <StatBlock label={tr("今日活跃")} value="—" cls="text-pos" />
        </div>
      </V17Card>
      <V17Card title={tr("会话列表")}>
        <div className="flex flex-col">
          {rows.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-3">{tr("暂无数据")}</div>
          )}
          {rows.map((s, i) => (
            <div key={i} className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft">
              <span className="w-16 text-ink-4 font-mono">{s.id}</span>
              <span className="flex-1 text-ink">{s.title}</span>
              <span className="w-24 text-right text-ink-4 font-mono">
                {s.updated_at ? s.updated_at.slice(0, 16).replace("T", " ") : "—"}
              </span>
              {s.id && (
                <button
                  onClick={() => openSessionDetail(s.id!)}
                  className="rounded bg-surface-3 text-ink-3 px-1.5 py-0.5 text-[9px] hover:bg-sky-500/15 hover:text-sky-400 flex items-center gap-0.5"
                  title={tr("会话详情")}
                >
                  <Info className="h-2.5 w-2.5" />
                </button>
              )}
            </div>
          ))}
        </div>
      </V17Card>

      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Info className="h-4 w-4 text-sky-400" />
              {detailSession?.title || tr("会话 {id}", { id: detailSession?.id ?? "—" })}
            </DialogTitle>
            <DialogDescription>
              ID: {detailSession?.id ?? "—"}
              {detailSession?.strategy_id ? tr(" | 策略: {id}", { id: detailSession.strategy_id }) : ""}
              {" | "}{tr("消息数: {n}", { n: detailMessages.length })}
              {detailSession?.created_at ? tr(" | 创建: {ts}", { ts: detailSession.created_at.slice(0, 19).replace("T", " ") }) : ""}
            </DialogDescription>
          </DialogHeader>
          {detailLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : detailSession ? (
            <div className="space-y-1.5">
              {detailMessages.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground text-sm">{tr("暂无消息")}</div>
              ) : detailMessages.map((m) => (
                <div
                  key={m.id}
                  className={`p-2 rounded text-[11px] border border-border-soft ${m.role === "user" ? "bg-sky-500/5" : "bg-emerald-500/5"}`}
                >
                  <div className="flex items-center gap-2 mb-1 text-[9px] text-ink-4 font-mono">
                    <span className={m.role === "user" ? "text-sky-400" : "text-emerald-400"}>
                      {m.role === "user" ? "USER" : "ASSISTANT"}
                    </span>
                    {m.created_at && (
                      <span>{m.created_at.slice(11, 19)}</span>
                    )}
                  </div>
                  <div className="text-ink whitespace-pre-wrap break-words">{m.content}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground text-sm">{tr("加载失败")}</div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
