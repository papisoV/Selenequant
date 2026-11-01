"use client";

import { useState } from "react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import {
  extractShadowProfile, renderShadowEngine, backtestShadowProfile,
  type ShadowExtractResult,
} from "@/lib/api/shadow-profile";
import { toastSuccess, toastError } from "@/lib/toast";
import { Loader2, Upload, FileCode, Play } from "lucide-react";
import { useUiText } from "@/components/i18n/use-ui-text"

export function ShadowProfilePanel() {
  const tr = useUiText()

  const [extractOpen, setExtractOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [minSupport, setMinSupport] = useState(3);
  const [maxRules, setMaxRules] = useState(5);
  const [extractResult, setExtractResult] = useState<ShadowExtractResult | null>(null);

  const [renderOpen, setRenderOpen] = useState(false);
  const [renderShadowId, setRenderShadowId] = useState("");
  const [renderSource, setRenderSource] = useState<string | null>(null);

  const [btOpen, setBtOpen] = useState(false);
  const [btShadowId, setBtShadowId] = useState("");
  const [btWindowStart, setBtWindowStart] = useState("");
  const [btWindowEnd, setBtWindowEnd] = useState("");
  const [btResult, setBtResult] = useState<Record<string, unknown> | null>(null);

  async function doExtract() {
    if (!file) {
      toastError(tr("必须选择 CSV 文件"));
      return;
    }
    setBusy(true);
    try {
      const r = await extractShadowProfile(file, minSupport, maxRules);
      setExtractResult(r);
      toastSuccess(tr("已提取 shadow_id: {id}", { id: r.shadow_id }));
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doRender() {
    if (!renderShadowId) {
      toastError(tr("必须填 shadow_id"));
      return;
    }
    setBusy(true);
    try {
      const r = await renderShadowEngine(renderShadowId);
      setRenderSource(r.source);
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doBacktest() {
    if (!btShadowId || !btWindowStart || !btWindowEnd) {
      toastError(tr("shadow_id 和 window_start/end 必填"));
      return;
    }
    setBusy(true);
    try {
      const r = await backtestShadowProfile(btShadowId, {
        window_start: btWindowStart,
        window_end: btWindowEnd,
      });
      setBtResult(r);
      toastSuccess(tr("回测完成"));
    } catch (e: unknown) {
      toastError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <V17Card title={tr("Shadow Profile (影子画像)")}>
      <div className="grid grid-cols-3 gap-2 mb-2">
        <StatBlock label={tr("状态")} value="env-gated" cls="text-gold" />
        <StatBlock label={tr("依赖")} value="vibe_shadow" cls="text-ink-3" />
        <StatBlock label={tr("开关")} value="VIBE_SHADOW_ENABLED" cls="text-ink-4" />
      </div>

      <div className="text-[9px] text-ink-4 mb-2 leading-relaxed">
        {tr("从券商交易日志 CSV 提取行为规则,生成独立 SignalEngine,可多市场回测。 默认禁用(404),需设 VIBE_SHADOW_ENABLED=1 启用。")}
      </div>

      <div className="flex gap-1 flex-wrap">
        <button
          onClick={() => setExtractOpen(true)}
          className="rounded bg-sky-500/15 text-sky-400 px-2 py-1 text-[10px] hover:bg-sky-500/25 flex items-center gap-1"
        >
          <Upload className="h-3 w-3" />{tr("提取 profile")}
        </button>
        <button
          onClick={() => setRenderOpen(true)}
          className="rounded bg-surface-3 text-ink-3 px-2 py-1 text-[10px] hover:bg-surface-4 flex items-center gap-1"
        >
          <FileCode className="h-3 w-3" />{tr("渲染 engine")}
        </button>
        <button
          onClick={() => setBtOpen(true)}
          className="rounded bg-surface-3 text-ink-3 px-2 py-1 text-[10px] hover:bg-surface-4 flex items-center gap-1"
        >
          <Play className="h-3 w-3" />{tr("回测")}
        </button>
      </div>

      {/* Extract 对话框 */}
      <Dialog open={extractOpen} onOpenChange={setExtractOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Upload className="h-4 w-4 text-sky-400" />
              {tr("提取 Shadow Profile")}
            </DialogTitle>
            <DialogDescription>
              {tr("上传券商交易日志 CSV,提取行为规则。")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-[11px]">
            <div>
              <div className="text-ink-4 mb-0.5">{tr("CSV 文件")}</div>
              <input
                type="file"
                accept=".csv"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="w-full text-[10px] text-ink-3"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-ink-4 mb-0.5">min_support</div>
                <input
                  type="number"
                  min={1}
                  value={minSupport}
                  onChange={(e) => setMinSupport(parseInt(e.target.value) || 3)}
                  className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
                />
              </div>
              <div>
                <div className="text-ink-4 mb-0.5">max_rules</div>
                <input
                  type="number"
                  min={1}
                  value={maxRules}
                  onChange={(e) => setMaxRules(parseInt(e.target.value) || 5)}
                  className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={() => setExtractOpen(false)}
                disabled={busy}
                className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[10px] text-ink-3"
              >
                {tr("取消")}
              </button>
              <button
                onClick={doExtract}
                disabled={busy || !file}
                className="rounded border border-sky-400/40 bg-sky-500/15 px-2 py-1 text-[10px] text-sky-400 flex items-center gap-1"
              >
                {busy && <Loader2 className="h-3 w-3 animate-spin" />}
                {tr("提取")}
              </button>
            </div>
            {extractResult && (
              <div className="border-t border-border-soft pt-2 mt-2">
                <div className="text-[10px] text-gold font-mono">shadow_id: {extractResult.shadow_id}</div>
                <div className="text-[10px] text-ink-3">{tr("规则:{n} 条", { n: extractResult.rules.length })}</div>
                <pre className="text-[9px] text-ink-4 font-mono bg-surface-2 rounded p-1.5 max-h-32 overflow-auto mt-1">
                  {JSON.stringify(extractResult, null, 2).slice(0, 1500)}
                </pre>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Render 对话框 */}
      <Dialog open={renderOpen} onOpenChange={setRenderOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileCode className="h-4 w-4 text-gold" />
              {tr("渲染 SignalEngine")}
            </DialogTitle>
            <DialogDescription>
              {tr("从持久化的 shadow profile 生成独立 Python SignalEngine 源码。")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-[11px]">
            <div>
              <div className="text-ink-4 mb-0.5">shadow_id</div>
              <input
                value={renderShadowId}
                onChange={(e) => setRenderShadowId(e.target.value)}
                placeholder="shadow-xxx"
                className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
              />
            </div>
            <button
              onClick={doRender}
              disabled={busy || !renderShadowId}
              className="rounded bg-gold/15 text-gold px-2 py-1 text-[10px] hover:bg-gold/25 flex items-center gap-1"
            >
              {busy && <Loader2 className="h-3 w-3 animate-spin" />}
              {tr("渲染")}
            </button>
            {renderSource && (
              <pre className="text-[9px] text-ink-3 font-mono bg-surface-2 rounded p-1.5 max-h-72 overflow-auto">
                {renderSource.slice(0, 3000)}
                {renderSource.length > 3000 ? tr("\n… (截断)") : ""}
              </pre>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Backtest 对话框 */}
      <Dialog open={btOpen} onOpenChange={setBtOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Play className="h-4 w-4 text-emerald-400" />
              {tr("Shadow Profile 回测")}
            </DialogTitle>
            <DialogDescription>
              {tr("多市场回测持久化的 shadow profile。")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 text-[11px]">
            <div>
              <div className="text-ink-4 mb-0.5">shadow_id</div>
              <input
                value={btShadowId}
                onChange={(e) => setBtShadowId(e.target.value)}
                placeholder="shadow-xxx"
                className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-ink-4 mb-0.5">window_start</div>
                <input
                  value={btWindowStart}
                  onChange={(e) => setBtWindowStart(e.target.value)}
                  placeholder="2026-01-01"
                  className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
                />
              </div>
              <div>
                <div className="text-ink-4 mb-0.5">window_end</div>
                <input
                  value={btWindowEnd}
                  onChange={(e) => setBtWindowEnd(e.target.value)}
                  placeholder="2026-06-01"
                  className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 font-mono text-ink"
                />
              </div>
            </div>
            <button
              onClick={doBacktest}
              disabled={busy || !btShadowId || !btWindowStart || !btWindowEnd}
              className="rounded bg-emerald-500/15 text-emerald-400 px-2 py-1 text-[10px] hover:bg-emerald-500/25 flex items-center gap-1"
            >
              {busy && <Loader2 className="h-3 w-3 animate-spin" />}
              {tr("运行回测")}
            </button>
            {btResult && (
              <pre className="text-[9px] text-ink-3 font-mono bg-surface-2 rounded p-1.5 max-h-72 overflow-auto">
                {JSON.stringify(btResult, null, 2).slice(0, 3000)}
              </pre>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </V17Card>
  );
}
