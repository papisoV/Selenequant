"use client";

import { useState } from "react";
import useSWR from "swr";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import { getKillSwitch, tripKillSwitch, releaseKillSwitch } from "@/lib/api/admin";
import { toastSuccess, toastError } from "@/lib/toast";
import { Loader2, AlertTriangle, ShieldOff, ShieldCheck } from "lucide-react";

function fmtTs(ts: number | null): string {
  if (ts == null) return "—";
  return new Date(ts).toLocaleString("zh-CN");
}

export function KillSwitchPanel() {
  const { data, mutate, isLoading } = useSWR("/api/v1/admin/kill-switch", getKillSwitch, {
    refreshInterval: 5000,
  });
  const active = data?.active ?? false;

  const [tripOpen, setTripOpen] = useState(false);
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);

  async function doTrip() {
    if (!reason.trim()) {
      toastError("必须填 reason");
      return;
    }
    setBusy(true);
    try {
      await tripKillSwitch(reason.trim());
      toastSuccess("Kill switch 已激活");
      setTripOpen(false);
      setReason("");
      await mutate();
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doRelease() {
    if (!reason.trim()) {
      toastError("必须填 reason");
      return;
    }
    setBusy(true);
    try {
      await releaseKillSwitch(reason.trim(), force);
      toastSuccess("Kill switch 已释放");
      setReleaseOpen(false);
      setReason("");
      setForce(false);
      await mutate();
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <V17Card title="Kill Switch (全局熔断)">
      {isLoading && !data && (
        <div className="text-[11px] text-ink-3 py-2 animate-pulse">加载中…</div>
      )}
      <div className="grid grid-cols-3 gap-2">
        <StatBlock
          label="状态"
          value={active ? "ACTIVE" : "OFF"}
          cls={active ? "text-neg" : "text-pos"}
        />
        <StatBlock label="激活时间" value={fmtTs(data?.tripped_at ?? null)} cls="text-ink-3" />
        <StatBlock label="原因" value={data?.reason ?? "—"} cls="text-ink-3" />
      </div>

      <div className="mt-2 flex gap-2">
        <button
          onClick={() => setTripOpen(true)}
          disabled={active}
          className={`rounded border px-2 py-1 text-[10px] flex items-center gap-1 ${
            active
              ? "border-border-soft bg-surface-2 text-ink-4 cursor-not-allowed"
              : "border-neg/40 bg-neg/15 text-neg hover:bg-neg/25"
          }`}
        >
          <ShieldOff className="h-3 w-3" />
          激活熔断
        </button>
        <button
          onClick={() => setReleaseOpen(true)}
          disabled={!active}
          className={`rounded border px-2 py-1 text-[10px] flex items-center gap-1 ${
            !active
              ? "border-border-soft bg-surface-2 text-ink-4 cursor-not-allowed"
              : "border-pos/40 bg-pos/15 text-pos hover:bg-pos/25"
          }`}
        >
          <ShieldCheck className="h-3 w-3" />
          释放熔断
        </button>
      </div>

      <div className="mt-1 text-[9px] text-ink-4">
        需要 admin role。激活会自动撤销所有活跃订单并发送 trade alert。
      </div>

      {/* Trip 对话框 */}
      <Dialog open={tripOpen} onOpenChange={setTripOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-neg" />
              激活 Kill Switch
            </DialogTitle>
            <DialogDescription>
              全局交易熔断。激活后所有交易操作停止,活跃订单自动撤销。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-[11px]">
            <div>
              <div className="text-ink-4 mb-0.5">原因 (必填)</div>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="emergency halt reason"
                className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 text-ink"
              />
            </div>
            <div className="text-[10px] text-neg flex items-start gap-1">
              <AlertTriangle className="h-3 w-3 mt-0.5 flex-shrink-0" />
              <span>此操作立即生效,影响所有交易。确认继续?</span>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setTripOpen(false)}
                disabled={busy}
                className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-3"
              >
                取消
              </button>
              <button
                onClick={doTrip}
                disabled={busy}
                className="rounded border border-neg/40 bg-neg/15 px-2 py-1 text-[10px] text-neg hover:bg-neg/25 flex items-center gap-1"
              >
                {busy && <Loader2 className="h-3 w-3 animate-spin" />}
                确认激活
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Release 对话框 */}
      <Dialog open={releaseOpen} onOpenChange={setReleaseOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-pos" />
              释放 Kill Switch
            </DialogTitle>
            <DialogDescription>
              恢复交易操作。force=true 强制释放(即使仍检测到危险)。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-[11px]">
            <div>
              <div className="text-ink-4 mb-0.5">原因 (必填)</div>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="release reason"
                className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 text-ink"
              />
            </div>
            <label className="flex items-center gap-2 text-ink-3">
              <input
                type="checkbox"
                checked={force}
                onChange={(e) => setForce(e.target.checked)}
                className="h-3 w-3"
              />
              force (强制释放,即使仍检测到危险)
            </label>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setReleaseOpen(false)}
                disabled={busy}
                className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-3"
              >
                取消
              </button>
              <button
                onClick={doRelease}
                disabled={busy}
                className="rounded border border-pos/40 bg-pos/15 px-2 py-1 text-[10px] text-pos hover:bg-pos/25 flex items-center gap-1"
              >
                {busy && <Loader2 className="h-3 w-3 animate-spin" />}
                确认释放
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </V17Card>
  );
}
