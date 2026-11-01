"use client";

import { useEffect, useRef, useState } from "react";
import { apiDirect } from "@/lib/api-client";
import { useUiText, useLocale } from "@/components/i18n/use-ui-text";
import {
  INDICATORS,
  buildDemoBacktestResult,
  buildFormulaString,
  type BacktestResult,
  type IndicatorMeta,
  type IndicatorParam,
} from "@/lib/indicators";
import { IndicatorLibrary } from "./indicator-library";
import { BacktestResultPanel } from "./backtest-result-panel";

export const DEMO_FALLBACK_MS = 8000;

export function IndicatorEditorPage() {
  const tr = useUiText();
  const locale = useLocale();
  const showNameZh = locale !== "en";
  const [selected, setSelected] = useState<IndicatorMeta>(INDICATORS[0]);
  const [values, setValues] = useState<Record<string, number | string | boolean>>({});
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [source, setSource] = useState<"live" | "demo">("live");
  const [running, setRunning] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const liveRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  const select = (meta: IndicatorMeta) => {
    setSelected(meta);
    setValues({});
    setResult(null);
    setSource("live");
    setSubmitted(false);
    liveRef.current = false;
  };

  const setParam = (p: IndicatorParam, v: number | string | boolean) => {
    setValues((prev) => ({ ...prev, [p.key]: v }));
  };

  const runBacktest = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setRunning(true);
    setResult(null);
    setSource("live");
    setSubmitted(false);
    liveRef.current = false;

    const payload = {
      strategy_id: selected.id,
      indicator_type: selected.type,
      params: values,
      symbol: "BTC/USDT",
      interval: selected.interval,
    };
    apiDirect("/api/v1/backtests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(() => {
        liveRef.current = true;
        setRunning(false);
        setSubmitted(true);
      })
      .catch(() => {
        // 后端不可达 → 走 demo 兜底
      });

    timerRef.current = setTimeout(() => {
      if (liveRef.current) return;
      const idx = Math.max(0, INDICATORS.findIndex((i) => i.id === selected.id));
      setResult(buildDemoBacktestResult(idx));
      setSource("demo");
      setRunning(false);
    }, DEMO_FALLBACK_MS);
  };

  return (
    <div className="indicator-editor-page flex h-full flex-col gap-3 overflow-y-auto">
      <h1 className="text-sm font-semibold text-ink">{tr("自定义指标编辑器")}</h1>
      <p className="text-[10px] text-ink-4">
        {tr("从 strategy-engine 模板库选指标、调参数,生成可读公式串并运行回测。")}
      </p>
      <div className="grid grid-cols-[260px_1fr] gap-3">
        <div className="rounded border border-border-soft bg-surface-1 p-2">
          <div className="mb-1 text-[10px] font-medium text-ink-3">{tr("模板画廊({n})", { n: INDICATORS.length })}</div>
          <IndicatorLibrary
            onSelect={select}
            selectedId={selected.id}
          />
        </div>
        <div className="flex flex-col gap-2 rounded border border-border-soft bg-surface-1 p-2">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-ink">{selected.name}</span>
            {showNameZh && <span className="text-[10px] text-ink-4">{selected.nameZh}</span>}
            <span className="ml-auto rounded bg-surface-3 px-1 text-[9px] text-ink-3">{selected.interval}</span>
          </div>
          <p className="text-[10px] text-ink-4">{tr(selected.description)}</p>
          <div className="grid grid-cols-2 gap-2">
            {selected.params.map((p) => (
              <ParamField key={p.key} param={p} value={values[p.key] ?? p.default} onChange={setParam} />
            ))}
          </div>
          <div className="rounded border border-border-soft bg-surface-2 px-2 py-1.5 font-mono text-[10px] text-ink">
            {buildFormulaString(selected, values, tr)}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={runBacktest}
              disabled={running}
              className="rounded bg-gold px-3 py-1 text-[10px] font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {running ? tr("回测运行中…") : tr("运行回测")}
            </button>
            {submitted && source === "live" && (
              <span className="text-[9px] text-ink-4">{tr("回测任务已提交(202),结果可在回测中心查看")}</span>
            )}
          </div>
          {result && <BacktestResultPanel result={result} source={source} />}
        </div>
      </div>
    </div>
  );
}

function ParamField({
  param,
  value,
  onChange,
}: {
  param: IndicatorParam;
  value: number | string | boolean;
  onChange: (p: IndicatorParam, v: number | string | boolean) => void;
}) {
  const tr = useUiText();
  if (param.type === "boolean") {
    return (
      <label className="flex items-center gap-2 text-[10px] text-ink">
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(param, e.target.checked)} />
        {tr(param.label)}
      </label>
    );
  }
  if (param.type === "string") {
    return (
      <label className="flex flex-col gap-0.5 text-[10px] text-ink-3">
        {tr(param.label)}
        <input
          type="text"
          value={String(value)}
          onChange={(e) => onChange(param, e.target.value)}
          className="rounded border border-border-soft bg-surface-2 px-1.5 py-0.5 text-[10px] text-ink"
        />
      </label>
    );
  }
  return (
    <label className="flex flex-col gap-0.5 text-[10px] text-ink-3">
      {tr(param.label)}
      <input
        type="number"
        value={String(value)}
        step={param.step}
        min={param.min}
        max={param.max}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") return;
          onChange(param, Number(raw));
        }}
        className="rounded border border-border-soft bg-surface-2 px-1.5 py-0.5 text-[10px] text-ink"
      />
    </label>
  );
}
