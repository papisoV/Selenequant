"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toastSuccess, toastError, toastWarn } from "@/lib/toast";
import { createFromTemplate, deployPaper, deployLive } from "@/lib/api/strategies";
import { ParamPanel } from "./param-panel";
import type { StrategyData } from "./types";
import { useUiText, useLocale } from "@/components/i18n/use-ui-text"

// US-2 部署三步向导：模板卡部署按钮入口。两步链 = createFromTemplate(吃模板全套管道含
// entry_rules,前端拿不到) → PUT 覆盖参数/symbols/interval(UpdateStrategyRequest 原生支持)。
// deploy 不收初始资金(DeployStrategyRequest 只有 account_id)。

export interface WizardTemplate {
  id: string;
  name: string;
  nameZh?: string;
  category?: string;
  description?: string;
  defaultParams: Record<string, unknown>;
  riskParams: Record<string, unknown>;
  symbols: string[];
  interval: string;
}

const INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"];

interface StrategyLike {
  name?: string;
  description?: string;
  symbols?: string[];
  interval?: string;
  parameters?: Record<string, unknown>;
  entry_rules?: Record<string, unknown>;
  exit_rules?: Record<string, unknown>;
  risk_params?: Record<string, unknown>;
  tags?: string[];
  version?: number;
  direction?: string;
  symbol?: string;
  [k: string]: unknown;
}

export function DeployWizardDialog({
  template,
  initialMode,
  open,
  onOpenChange,
  onDeployed,
}: {
  template: WizardTemplate | null;
  initialMode: "paper" | "live";
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeployed: () => void;
}) {
  const tr = useUiText()
  // `name` / `nameZh` 是一对字段(英文名 + 中文名),不是同一条文案的两种译法。
  // 与 indicator-library / strategy-optimizer-panel 同一范式。
  const showNameZh = useLocale() !== "en"
  // 步骤名是展示文案,必须走 tr();外层拼出来的是 en 下仍为中文的根因之一。
  const STEP_LABELS = ["参数确认", "运行设定", "执行面"]

  const [step, setStep] = useState(0);
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [riskParams, setRiskParams] = useState<Record<string, unknown>>({});
  const [symbolsText, setSymbolsText] = useState("");
  const [interval, setInterval] = useState("1h");
  const [mode, setMode] = useState<"paper" | "live">(initialMode);
  const [liveConfirmed, setLiveConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  // 每次打开新模板 → 重置全部状态
  useEffect(() => {
    if (open && template) {
      setStep(0);
      setParams({ ...template.defaultParams });
      setRiskParams({ ...template.riskParams });
      setSymbolsText(template.symbols.join(", "));
      setInterval(template.interval);
      setMode(initialMode);
      setLiveConfirmed(false);
      setBusy(false);
      setErrMsg(null);
    }
  }, [open, template, initialMode]);

  const symbols = useMemo(
    () =>
      symbolsText
        .split(/[,，\s]+/)
        .map((s) => s.trim())
        .filter(Boolean),
    [symbolsText],
  );

  const strategyLike = useMemo<StrategyLike>(
    () => ({
      parameters: params,
      risk_params: riskParams,
      entry_rules: {},
      exit_rules: {},
    }),
    [params, riskParams],
  );

  const canNext = symbols.length > 0;
  const canDeploy = step === 2 && (mode === "paper" || liveConfirmed);

  const handleDeploy = async () => {
    if (!template || busy || !canDeploy) return;
    setBusy(true);
    setErrMsg(null);
    try {
      const detail = await createFromTemplate(template.id);
      // 覆盖用户在向导里改过的字段(模板默认值之外的部分)
      const overrides: Record<string, unknown> = {};
      if (JSON.stringify(params) !== JSON.stringify(template.defaultParams)) {
        overrides.parameters = params;
      }
      if (JSON.stringify(riskParams) !== JSON.stringify(template.riskParams)) {
        overrides.risk_params = riskParams;
      }
      if (JSON.stringify(symbols) !== JSON.stringify(template.symbols)) {
        overrides.symbols = symbols;
      }
      if (interval !== template.interval) {
        overrides.interval = interval;
      }
      if (Object.keys(overrides).length > 0) {
        const res = await fetch(`/api/v1/strategies/${detail.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...overrides, version_note: "部署向导覆盖" }),
        });
        const body = (await res.json()) as { error?: { message?: string } | null };
        if (!res.ok || body.error) {
          throw new Error(body.error?.message ?? tr("参数覆盖失败 HTTP {status}", { status: res.status }));
        }
      }
      if (mode === "paper") {
        const result = await deployPaper(detail.id);
        if (result.runner_status === "failed" || result.runner_status === "blocked") {
          setErrMsg(result.runner_error ?? tr("部署失败"));
          toastError(result.runner_error ?? tr("部署失败"));
          return;
        }
        if (result.backtest_warning) {
          toastWarn(result.backtest_warning);
        }
        toastSuccess(tr("{name} 已部署 Paper", { name: template.name }));
      } else {
        const result = await deployLive(detail.id);
        if (
          result.runner_status === "blocked" ||
          result.runner_status === "failed" ||
          result.deployed === false
        ) {
          const msg = result.runner_error ?? tr("部署被拒绝");
          setErrMsg(msg);
          toastError(msg);
          return;
        }
        toastSuccess(tr("{name} 已部署 Live", { name: template.name }));
      }
      onOpenChange(false);
      onDeployed();
    } catch (e: unknown) {
      setErrMsg(e instanceof Error ? e.message : String(e));
      toastError(tr("部署失败: {msg}", { msg: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(false);
    }
  };

  if (!template) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-[560px] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-sm">
            {tr("部署")} {showNameZh ? template.nameZh || template.name : template.name} — {tr(STEP_LABELS[step])}
          </DialogTitle>
          <DialogDescription className="text-xs">
            {step === 0 && tr("模板默认参数如下,可调整后进入下一步。")}
            {step === 1 && tr("交易对与周期;多交易对用逗号分隔。")}
            {step === 2 && tr("选择执行面。Paper=模拟盘;Live=实盘,需风险确认。")}
          </DialogDescription>
        </DialogHeader>

        {step === 0 && (
          <div className="max-h-[50vh] overflow-y-auto pr-1">
            <ParamPanel
              strategy={strategyLike as unknown as StrategyData}
              onChange={(u: Partial<StrategyLike>) => {
                if (u.parameters) setParams(u.parameters);
                if (u.risk_params) setRiskParams(u.risk_params);
              }}
            />
          </div>
        )}

        {step === 1 && (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">{tr("交易对")}</Label>
              <Input
                value={symbolsText}
                onChange={(e) => setSymbolsText(e.target.value)}
                placeholder="BTC/USDT, ETH/USDT"
                className="h-8 font-mono text-xs"
              />
              {symbols.length === 0 && (
                <p className="text-[11px] text-neg">{tr("至少填一个交易对")}</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">{tr("K线周期")}</Label>
              <select
                value={interval}
                onChange={(e) => setInterval(e.target.value)}
                className="h-8 w-full rounded border border-border bg-muted/30 px-2 font-mono text-xs"
              >
                {INTERVALS.map((iv) => (
                  <option key={iv} value={iv}>
                    {iv}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setMode("paper")}
                className={`rounded-lg border p-3 text-left transition-colors ${
                  mode === "paper"
                    ? "border-pos/60 bg-pos/10"
                    : "border-border hover:bg-muted/40"
                }`}
              >
                <div className="text-xs font-medium">{tr("Paper 模拟盘")}</div>
                <div className="mt-1 text-[11px] text-muted-foreground">
                  {tr("实时行情 + 模拟下单,不花真钱")}
                </div>
              </button>
              <button
                type="button"
                onClick={() => setMode("live")}
                className={`rounded-lg border p-3 text-left transition-colors ${
                  mode === "live"
                    ? "border-warn/60 bg-warn/10"
                    : "border-border hover:bg-muted/40"
                }`}
              >
                <div className="flex items-center gap-1.5 text-xs font-medium">
                  {tr("Live 实盘")}
                  <span className="rounded bg-warn/20 px-1 py-0.5 text-[9px] font-bold text-warn">
                    Pro
                  </span>
                </div>
                <div className="mt-1 text-[11px] text-muted-foreground">
                  {tr("真实下单;需先完成回测(发布门),社区版还会被拒绝")}
                </div>
              </button>
            </div>

            {mode === "live" && (
              <div className="flex items-start gap-2 rounded-lg border border-warn/40 bg-warn/5 p-3">
                <Checkbox
                  id="live-disclaimer"
                  checked={liveConfirmed}
                  onCheckedChange={(v) => setLiveConfirmed(v === true)}
                />
                <label htmlFor="live-disclaimer" className="text-[11px] leading-relaxed text-muted-foreground">
                  {tr("我理解实盘交易使用真实资金,策略信号将直接下单;亏损风险由我自行承担。")}
                </label>
              </div>
            )}

            {errMsg && <p className="text-[11px] text-neg">{tr(errMsg)}</p>}
          </div>
        )}

        <DialogFooter className="gap-2">
          {step > 0 && (
            <Button variant="outline" size="sm" className="h-7 px-3 text-xs" onClick={() => setStep((s) => s - 1)} disabled={busy}>
              {tr("上一步")}
            </Button>
          )}
          {step < 2 ? (
            <Button size="sm" className="h-7 px-3 text-xs" onClick={() => setStep((s) => s + 1)} disabled={!canNext}>
              {tr("下一步")}
            </Button>
          ) : (
            <Button
              size="sm"
              className={`h-7 px-3 text-xs ${mode === "paper" ? "bg-emerald-600 hover:bg-emerald-700" : "bg-amber-600 hover:bg-amber-700"}`}
              onClick={() => void handleDeploy()}
              disabled={!canDeploy || busy}
            >
              {busy ? (
                <>
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  {tr("部署中…")}
                </>
              ) : mode === "paper" ? (
                tr("部署到 Paper")
              ) : (
                tr("部署到 Live")
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
