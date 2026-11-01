"use client";

import { useState } from "react";
import useSWR from "swr";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import {
  exportUserData, getDeletionStatus, requestDeletion, cancelDeletion,
  type UserDataExport,
} from "@/lib/api/account";
import { toastSuccess, toastError } from "@/lib/toast";
import { Loader2, Download, Trash2, Undo2, AlertTriangle } from "lucide-react";
import { useUiText } from "@/components/i18n/use-ui-text"

function fmtTs(ts: number | null | undefined): string {
  if (ts == null) return "—";
  return new Date(ts).toLocaleString("zh-CN");
}

export function AccountPanel() {
  const tr = useUiText()

  const { data: deletion, mutate: mutateDeletion } = useSWR(
    "/api/v1/me/deletion-status",
    getDeletionStatus,
    { refreshInterval: 60000 },
  );

  const [exportOpen, setExportOpen] = useState(false);
  const [exportLoading, setExportLoading] = useState(false);
  const [exportData, setExportData] = useState<UserDataExport | null>(null);

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteStep2, setDeleteStep2] = useState(false);
  const [busy, setBusy] = useState(false);

  async function doExport() {
    setExportLoading(true);
    try {
      const data = await exportUserData();
      setExportData(data);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `account-export-${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toastSuccess(tr("已导出 (策略 {n} / 会话 {m})", { n: data.strategies_count, m: data.chat_sessions_count }));
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setExportLoading(false);
    }
  }

  async function doDelete() {
    setBusy(true);
    try {
      const r = await requestDeletion();
      toastSuccess(r.message ?? tr("已请求删除"));
      setDeleteOpen(false);
      setDeleteStep2(false);
      await mutateDeletion();
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doCancel() {
    setBusy(true);
    try {
      await cancelDeletion();
      toastSuccess(tr("已撤销删除请求"));
      await mutateDeletion();
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const deletionRequested = deletion?.deletion_requested ?? false;

  return (
    <V17Card title={tr("账户管理")}>
      <div className="grid grid-cols-3 gap-2">
        <StatBlock
          label={tr("删除请求")}
          value={deletionRequested ? "PENDING" : "NONE"}
          cls={deletionRequested ? "text-neg" : "text-pos"}
        />
        <StatBlock label={tr("请求时间")} value={fmtTs(deletion?.requested_at)} cls="text-ink-3" />
        <StatBlock
          label={tr("宽限期")}
          value={tr("{n} 天", { n: deletion?.grace_period_days ?? 7 })}
          cls="text-gold"
        />
      </div>

      <div className="mt-2 flex gap-2 flex-wrap">
        <button
          onClick={() => { setExportOpen(true); doExport(); }}
          disabled={exportLoading}
          className="set-btn rounded border border-border-soft bg-surface-3 text-ink-3 px-2 text-[11px] hover:bg-surface-4 gap-1"
        >
          {exportLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
          {tr("导出数据")}
        </button>

        {!deletionRequested ? (
          <button
            onClick={() => { setDeleteOpen(true); setDeleteStep2(false); }}
            className="set-btn rounded border border-neg/40 bg-neg/15 text-neg px-2 text-[11px] hover:bg-neg/25 gap-1"
          >
            <Trash2 className="h-3 w-3" />
            {tr("请求删除")}
          </button>
        ) : (
          <button
            onClick={doCancel}
            disabled={busy}
            className="set-btn rounded border border-pos/40 bg-pos/15 text-pos px-2 text-[11px] hover:bg-pos/25 gap-1"
          >
            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Undo2 className="h-3 w-3" />}
            {tr("撤销删除")}
          </button>
        )}
      </div>

      <div className="mt-1 text-[11px] text-ink-4">
        {tr("GDPR 风格数据导出 + 7 天宽限期删除。")}
      </div>

      {/* Export 对话框 */}
      <Dialog open={exportOpen} onOpenChange={setExportOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Download className="h-4 w-4 text-sky-400" />
              {tr("账户数据导出")}
            </DialogTitle>
            <DialogDescription>
              {tr("GDPR 风格全量数据导出,JSON 文件已自动下载。")}
            </DialogDescription>
          </DialogHeader>
          {exportLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : exportData ? (
            <div className="space-y-2 text-[11px]">
              <div className="grid grid-cols-3 gap-2">
                <StatBlock label={tr("策略数")} value={exportData.strategies_count} />
                <StatBlock label={tr("回测数")} value={exportData.backtests_count} />
                <StatBlock label={tr("会话数")} value={exportData.chat_sessions_count} />
              </div>
              <div className="text-[11px] text-ink-4">
                {tr("导出时间: {ts}", { ts: fmtTs(exportData.exported_at) })}
              </div>
              <pre className="text-[11px] text-ink-3 font-mono bg-surface-2 rounded p-1.5 max-h-48 overflow-auto">
                {JSON.stringify(exportData.data, null, 2).slice(0, 2000)}
                {JSON.stringify(exportData.data).length > 2000 ? tr("\n… (截断)") : ""}
              </pre>
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground text-sm">{tr("无数据")}</div>
          )}
        </DialogContent>
      </Dialog>

      {/* Delete 两步确认 */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Trash2 className="h-4 w-4 text-neg" />
              {tr("请求账户删除")}
            </DialogTitle>
            <DialogDescription>
              {tr("7 天宽限期,期间可撤销。宽限期过后账户及所有数据永久删除。")}
            </DialogDescription>
          </DialogHeader>
          {!deleteStep2 ? (
            <div className="space-y-3">
              <div className="text-[11px] text-ink-3">
                {tr("确认要请求删除账户吗?此操作启动 7 天倒计时,期间可随时撤销。")}
              </div>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setDeleteOpen(false)}
                  className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[11px] text-ink-3"
                >
                  {tr("取消")}
                </button>
                <button
                  onClick={() => setDeleteStep2(true)}
                  className="rounded border border-gold/40 bg-gold/15 px-2 py-1 text-[11px] text-gold"
                >
                  {tr("下一步")}
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="text-[11px] text-neg flex items-start gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                <span>{tr("最后确认:启动 7 天删除倒计时。确定继续?")}</span>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setDeleteStep2(false)}
                  disabled={busy}
                  className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[11px] text-ink-3"
                >
                  {tr("上一步")}
                </button>
                <button
                  onClick={doDelete}
                  disabled={busy}
                  className="rounded border border-neg/40 bg-neg/15 px-2 py-1 text-[11px] text-neg flex items-center gap-1"
                >
                  {busy && <Loader2 className="h-3 w-3 animate-spin" />}
                  {tr("确认请求删除")}
                </button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </V17Card>
  );
}
