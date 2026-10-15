"use client";

import React, { useState, useCallback, useRef, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { BrainCircuit, Send, Square, AlertTriangle, CheckCircle2, Loader2, LineChart, Download, History, MessageSquare, RefreshCw, Bug, Copy, Check } from "lucide-react";
import { ApiError } from "@/lib/api-client";
import { chatMessage, downloadRecordCsv, getChatHistory, getRecord, listRecentRecords, sendNotify, streamAnalyzeMarket } from "@/lib/api/market-analysis";
import { DEFAULT_TOL_RATIO, computeSupportResistance } from "@/lib/chart/swing-levels";
import { ForecastEvalCard } from "@/components/research/forecast-eval-card";
import { ICDiagnosticCard } from "@/components/research/ic-diagnostic-card";
import { ReturnReportCard } from "@/components/research/return-report-card";
import { CloseCountdown, closeAtFrom } from "@/components/chart/close-countdown";
import type { AnalysisRecord, ChatMessage, KlineBar, Stage1Diagnosis, Stage2Decision, TradeTemperament } from "@/lib/api/market-analysis";

const EXCHANGES = ["binance", "okx"];
const MARKET_TYPES = ["futures", "spot"];
const INTERVALS = ["1h", "4h", "1d"];

// IC / 回报面板是跨截面语义：当前 symbol 缺失时用流动性默认集凑齐截面。
const _LIQUID_CROSS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"];
const crossSection = (symbol: string): string => {
  const s = symbol.trim() || "BTC/USDT";
  const set = _LIQUID_CROSS.includes(s) ? _LIQUID_CROSS : [s, ..._LIQUID_CROSS.filter((x) => x !== s)].slice(0, 3);
  return set.join(",");
};

const REGIME_VARIANT = { trend_up: "success", trend_down: "destructive", range: "warning", spike: "warning" } as const;
const GATE_VARIANT = { proceed: "success", wait: "warning", unknown: "warning" } as const;
const DECISION_VARIANT = { limit: "success", breakout: "success", market: "default", no_trade: "secondary" } as const;
const BIAS_VARIANT = { bullish: "success", bearish: "destructive", neutral: "secondary" } as const;

// US-004: 交易倾向作为输入参数（影响 Stage2 决策倾向），4 档映射展示。
const TEMPERAMENT_META = {
  conservative: { label: "保守", variant: "secondary" },
  balanced: { label: "均衡", variant: "default" },
  aggressive: { label: "激进", variant: "warning" },
  very_aggressive: { label: "极度激进", variant: "destructive" },
} as const;

// US-006: 等待收盘实时倒计时。closeAtOf 取末根 K 线时间戳 + 周期时长 → 收盘时刻
// （倒计时本体在 components/chart/close-countdown.tsx，与 K 线图同域共用）。
function closeAtOf(result: AnalysisRecord | null): number | null {
  if (!result) return null;
  const bars = result.input_data?.klines;
  if (!bars?.length) return null;
  const last = bars[bars.length - 1];
  if (typeof last?.timestamp !== "number") return null;
  return closeAtFrom(last.timestamp, result.input_data?.interval);
}

// A1: 上下文 token 预算条。消耗 = usage_total 各键求和，上限取参考预算 64k，
// 80% 琥珀 / 95% 红（与 PA token 进度条同语义，数据已下发零后端）。明细放 title。
const _TOKEN_BUDGET = 64_000;
function TokenBudgetBar({ usage }: { usage: Record<string, number> }) {
  const total = Object.values(usage).reduce((s, v) => s + (typeof v === "number" ? v : 0), 0);
  if (total <= 0) return null;
  const pct = Math.min(100, (total / _TOKEN_BUDGET) * 100);
  const color = pct >= 95 ? "var(--color-destructive)" : pct >= 80 ? "var(--color-warn)" : "var(--color-primary)";
  const breakdown = Object.entries(usage).map(([k, v]) => `${k}=${v}`).join(", ");
  return (
    <span className="inline-flex items-center gap-1.5" title={`token 明细: ${breakdown}`}>
      <span className="inline-block h-1.5 w-16 overflow-hidden rounded-full bg-secondary">
        <span className="block h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
      </span>
      <span className="font-mono">{(total / 1000).toFixed(1)}k / 64k tokens</span>
    </span>
  );
}

// US-304: 保存 K 线 SVG 为 PNG（浏览器侧渲染，无需后端）。
function downloadChartPng(selector: string, name: string): void {
  const node = document.querySelector<SVGSVGElement>(selector);
  if (!node) return;
  const clone = node.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  const svgUrl = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], { type: "image/svg+xml;charset=utf-8" }));
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 260;
    const ctx = canvas.getContext("2d");
    if (ctx) {
      ctx.drawImage(img, 0, 0, 640, 260);
      canvas.toBlob((png) => {
        if (!png) return;
        const pngUrl = URL.createObjectURL(png);
        const a = document.createElement("a");
        a.href = pngUrl;
        a.download = `${name}.png`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(pngUrl);
      }, "image/png");
    }
    URL.revokeObjectURL(svgUrl);
  };
  img.src = svgUrl;
}

// US-007: 前端计算 EMA（指数移动平均），K 线少于 period 根时不画。
function ema(values: number[], period: number): number[] {
  if (values.length === 0) return [];
  const k = 2 / (period + 1);
  const out = [values[0]];
  for (let i = 1; i < values.length; i++) {
    out.push(values[i] * k + out[i - 1] * (1 - k));
  }
  return out;
}

// N5: MVP 渲染 K 线卡 + 图表，但不渲染 next_bar_forecast 预测字段。
// US-007: EMA20 折线（primary 蓝）+ 交易决策水平虚线（入场白/止损红/止盈绿）。
function CandleChart({ bars, s2 }: { bars: KlineBar[]; s2: Stage2Decision | null | undefined }) {
  const W = 640, H = 260, PAD = 8;
  const highs = bars.map((b) => b.high);
  const lows = bars.map((b) => b.low);
  const minP = Math.min(...lows);
  const maxP = Math.max(...highs);
  const span = maxP - minP || 1;
  const yScale = (p: number) => PAD + (1 - (p - minP) / span) * (H - PAD * 2);
  const xStep = W / bars.length;
  const cw = Math.max(2, xStep * 0.6);
  const grid = [0, 1, 2, 3, 4].map((i) => {
    const y = PAD + ((H - PAD * 2) * i) / 4;
    return { y, p: maxP - ((maxP - minP) * i) / 4 };
  });

  const closes = bars.map((b) => b.close);
  const ema20 = bars.length >= 20 ? ema(closes, 20) : null;
  const emaPoints = ema20
    ? ema20.map((v, i) => `${i * xStep + xStep / 2},${yScale(v).toFixed(2)}`).join(" ")
    : null;

  // 交易决策线：仅当价格存在时画。
  const summary = (s2?.decision_summary ?? {}) as {
    entry_price?: number;
    stop_loss?: number;
    take_profit?: number;
    direction?: string;
  };
  const entryP = typeof summary.entry_price === "number" ? summary.entry_price : null;
  const stopP = typeof summary.stop_loss === "number" ? summary.stop_loss : null;
  const tpP = typeof summary.take_profit === "number" ? summary.take_profit : null;
  // A2: ▲▼ 方向标记，取自 validator 强制枚举的 trade_setup.direction（long/short），
  // 比 decision_summary.direction（LLM 自由文本）可靠；no_trade/wait 时 trade_setup 为 null → 无标记。
  const dirBadge =
    s2?.trade_setup?.direction === "long"
      ? { sym: "▲", label: "看多", color: "var(--color-pos)" }
      : s2?.trade_setup?.direction === "short"
        ? { sym: "▼", label: "看空", color: "var(--color-neg)" }
        : null;
  const levels: { p: number; color: string; label: string }[] = [];
  if (entryP != null) levels.push({ p: entryP, color: "var(--color-ink)", label: `入场 ${entryP}` });
  if (stopP != null) levels.push({ p: stopP, color: "var(--color-destructive)", label: `止损 ${stopP}` });
  if (tpP != null) levels.push({ p: tpP, color: "var(--color-pos)", label: `止盈 ${tpP}` });

  // B3: 确定性 swing 支撑/阻力（fractal pivot + 聚类）。线渲染在决策线旁，
  // 距最后收盘最近的一根 S/R 各画半透明价格带（band 在 K 线之下）。
  const { supports, resistances } = computeSupportResistance(bars);
  const lastClose = bars.length > 0 ? bars[bars.length - 1].close : null;
  const nearSupport = lastClose != null ? supports.find((p) => p <= lastClose) : undefined;
  const nearResistance = lastClose != null ? resistances.filter((p) => p >= lastClose).pop() : undefined;
  const srLines = [
    ...resistances.map((p) => ({ p, color: "var(--color-warn)", label: `R ${p.toFixed(0)}` })),
    ...supports.map((p) => ({ p, color: "var(--color-pos)", label: `S ${p.toFixed(0)}` })),
  ];
  const bands = [
    nearSupport != null && { p: nearSupport, color: "var(--color-pos)" },
    nearResistance != null && { p: nearResistance, color: "var(--color-warn)" },
  ].filter((b): b is { p: number; color: string } => Boolean(b));

  return (
    <svg
      id="ma-kline-chart"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      style={{ width: "100%", height: 220 }}
      role="img"
      aria-label="K线图"
    >
      {grid.map((g, i) => (
        <g key={i}>
          <line x1="0" y1={g.y} x2={W} y2={g.y} stroke="var(--color-border-soft)" strokeWidth="0.5" />
          <text x={W - 4} y={g.y + 9} textAnchor="end" fill="var(--color-ink-4)" fontSize="9" fontFamily="var(--font-mono)">
            {g.p.toFixed(0)}
          </text>
        </g>
      ))}
      {bands.map((b) => {
        const band = b.p * DEFAULT_TOL_RATIO;
        const y1 = yScale(b.p + band);
        const y2 = yScale(b.p - band);
        return (
          <rect
            key={`sr-band-${b.p}`}
            x="0" y={y1} width={W} height={Math.max(1, y2 - y1)}
            fill={b.color} opacity="0.08"
          />
        );
      })}
      {bars.map((b, i) => {
        const x = i * xStep + xStep / 2;
        const up = b.close >= b.open;
        const color = up ? "var(--color-pos)" : "var(--color-neg)";
        return (
          <g key={i}>
            <line
              x1={x} y1={yScale(b.high)} x2={x} y2={yScale(b.low)}
              stroke={color} strokeWidth="0.6"
            />
            <rect
              x={x - cw / 2}
              y={Math.min(yScale(b.open), yScale(b.close))}
              width={cw}
              height={Math.max(1, Math.abs(yScale(b.open) - yScale(b.close)))}
              fill={color}
              stroke={color}
            />
          </g>
        );
      })}
      {emaPoints && (
        <polyline
          points={emaPoints}
          fill="none"
          stroke="var(--color-primary)"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
      )}
      {levels.map((lv) => {
        const y = yScale(lv.p);
        return (
          <g key={lv.label}>
            <line
              x1="0" y1={y} x2={W} y2={y}
              stroke={lv.color} strokeWidth="1" strokeDasharray="5 4"
            />
            <text x={4} y={y - 3} fill={lv.color} fontSize="9" fontFamily="var(--font-mono)">
              {lv.label}
            </text>
          </g>
        );
      })}
      {srLines.map((lv) => {
        const y = yScale(lv.p);
        return (
          <g key={lv.label}>
            <line
              x1="0" y1={y} x2={W} y2={y}
              stroke={lv.color} strokeWidth="1" strokeDasharray="5 4"
            />
            <text x={W - 4} y={y - 3} textAnchor="end" fill={lv.color} fontSize="9" fontFamily="var(--font-mono)">
              {lv.label}
            </text>
          </g>
        );
      })}
      {dirBadge && (
        <text x={4} y={12} fill={dirBadge.color} fontSize="11" fontFamily="var(--font-mono)">
          {dirBadge.sym} {dirBadge.label}
        </text>
      )}
    </svg>
  );
}

function Stage1Card({ s1 }: { s1: Stage1Diagnosis | undefined }) {
  if (!s1) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4" />Stage 1 诊断
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground">无诊断结果</p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <CheckCircle2 className="h-4 w-4" />Stage 1 诊断
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={REGIME_VARIANT[s1.market_regime]}>{s1.market_regime}</Badge>
          <Badge variant={GATE_VARIANT[s1.entry_gate]}>entry_gate: {s1.entry_gate}</Badge>
        </div>
        {s1.signal_patterns.length > 0 && (
          <div className="space-y-1">
            {s1.signal_patterns.map((p, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2 text-xs">
                <Badge variant={BIAS_VARIANT[p.bias]}>{p.bias}</Badge>
                <span>{p.name}</span>
                <span className="text-muted-foreground">{(p.confidence * 100).toFixed(0)}%</span>
                {p.kline_range && <span className="text-muted-foreground font-mono">{p.kline_range}</span>}
              </div>
            ))}
          </div>
        )}
        {s1.evidence.length > 0 && (
          <div className="space-y-1">
            {s1.evidence.map((ev, i) => (
              <p key={i} className="text-xs text-muted-foreground">
                {ev.key}: {ev.statement}
              </p>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// G4: 决策路径自动播放 stepper。垂直时间线逐条点亮 decision_path 步骤；
// setInterval 每 600ms 渐进点亮一步（复用 CloseCountdown 模式：状态挂载时初始化 +
// setInterval 回调内 setState + useEffect cleanup）。调用方用 key 控制新路径重挂载。
function DecisionPathStepper({ steps }: { steps: string[] }) {
  const [revealed, setRevealed] = useState(1);
  useEffect(() => {
    if (steps.length <= 1) return;
    const id = window.setInterval(() => {
      setRevealed((r) => (r >= steps.length ? r : r + 1));
    }, 600);
    return () => window.clearInterval(id);
  }, [steps.length]);

  const handleReplay = useCallback(() => setRevealed(1), []);
  if (steps.length === 0) return null;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-muted-foreground">决策路径</p>
        <Button variant="ghost" size="sm" className="h-6 px-1 text-xs" onClick={handleReplay}>
          <RefreshCw className="h-3 w-3 mr-1" />重播
        </Button>
      </div>
      <ol className="space-y-0.5">
        {steps.map((step, i) => {
          const lit = revealed > i;
          const isLast = i === steps.length - 1;
          return (
            <li key={i} className="flex items-start gap-2">
              <div className="flex flex-col items-center self-stretch">
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${lit ? "bg-primary" : "bg-muted-foreground/30"}`} />
                {!isLast && <span className={`w-px flex-1 ${lit ? "bg-primary/40" : "bg-border-soft"}`} />}
              </div>
              <span className={`text-xs leading-6 ${lit ? "text-ink-2" : "text-muted-foreground"}`}>{step}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Stage2Card({ s2 }: { s2: Stage2Decision | null | undefined }) {
  const shortCircuited = (s2?.decision_summary as { short_circuited?: boolean } | undefined)?.short_circuited === true;
  const ts = s2?.trade_setup;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <BrainCircuit className="h-4 w-4" />Stage 2 决策
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {s2?.decision ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={DECISION_VARIANT[s2.decision]}>{s2.decision}</Badge>
              {shortCircuited && <Badge variant="warning">gate 短路</Badge>}
            </div>
            {/* G4: 决策路径自动播放（wait 短路记录同样渲染短路路径） */}
            {ts && ts.decision_path.length > 0 && (
              <DecisionPathStepper key={ts.decision_path.join("→")} steps={ts.decision_path} />
            )}
            {/* G1: 完整交易设置（direction/价格齐全时才渲染） */}
            {ts && (ts.direction || ts.entry_price != null) && (
              <div className="space-y-2 rounded-md border border-border-soft p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-xs font-medium text-ink-3">交易设置</p>
                  {ts.direction && (
                    <Badge variant={ts.direction === "long" ? "success" : "destructive"}>{ts.direction}</Badge>
                  )}
                  <Badge variant={ts.trader_equation_passed ? "success" : "warning"}>
                    {ts.trader_equation_passed ? "交易者方程通过" : "交易者方程未通过"}
                  </Badge>
                </div>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
                  {ts.entry_price != null && (
                    <div><dt className="text-muted-foreground">入场</dt><dd className="font-mono">{ts.entry_price.toFixed(2)}</dd></div>
                  )}
                  {ts.stop_loss_price != null && (
                    <div><dt className="text-muted-foreground">止损</dt><dd className="font-mono text-destructive">{ts.stop_loss_price.toFixed(2)}</dd></div>
                  )}
                  {ts.take_profit_price != null && (
                    <div><dt className="text-muted-foreground">止盈</dt><dd className="font-mono">{ts.take_profit_price.toFixed(2)}</dd></div>
                  )}
                  {ts.take_profit_price_2 != null && (
                    <div><dt className="text-muted-foreground">止盈2</dt><dd className="font-mono">{ts.take_profit_price_2.toFixed(2)}</dd></div>
                  )}
                  {ts.risk_reward_ratio != null && (
                    <div><dt className="text-muted-foreground">RR</dt><dd className="font-mono">{ts.risk_reward_ratio.toFixed(2)}</dd></div>
                  )}
                  <div><dt className="text-muted-foreground">预估胜率</dt><dd className="font-mono">{(ts.estimated_win_rate * 100).toFixed(0)}%</dd></div>
                  <div><dt className="text-muted-foreground">信心</dt><dd className="font-mono">{(ts.confidence * 100).toFixed(0)}%</dd></div>
                </dl>
              </div>
            )}
            {/* propose 交易但无交易设置 → 灰色提示（正常不应出现，validator 已兜底） */}
            {!ts && s2.decision !== "no_trade" && (
              <p className="text-xs text-muted-foreground">交易设置缺失</p>
            )}
            {s2.dispatch_playbooks.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {s2.dispatch_playbooks.map((p, i) => (
                  <Badge key={i} variant="outline" className="font-mono text-xs">{p}</Badge>
                ))}
              </div>
            )}
            {s2.evidence.length > 0 && (
              <div className="space-y-1">
                {s2.evidence.map((ev, i) => (
                  <p key={i} className="text-xs text-muted-foreground">
                    {ev.key}: {ev.statement}
                  </p>
                ))}
              </div>
            )}
          </>
        ) : (
          <p className="text-xs text-muted-foreground">Stage 2 未运行（entry_gate 未通过）</p>
        )}
      </CardContent>
    </Card>
  );
}

export function AnalysisPanel() {
  const [exchange, setExchange] = useState("binance");
  const [marketType, setMarketType] = useState("futures");
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [interval, setInterval] = useState("1h");
  // US-004: 交易倾向输入参数（默认均衡；随分析请求透传，影响 Stage2 决策倾向）。
  const [temperament, setTemperament] = useState<TradeTemperament>("balanced");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AnalysisRecord | null>(null);
  const [error, setError] = useState<string | null>(null);

  // US-003 流式渲染：当前阶段 + 已累积 LLM 文本（错误时不丢失）。
  const [streamPhase, setStreamPhase] = useState<"stage1" | "stage2" | null>(null);
  const [streamText, setStreamText] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  // US-302 Free Chat
  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);

  // US-304 演示回放 / 导出 / 通知
  const [recentRecords, setRecentRecords] = useState<AnalysisRecord[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [exporting, setExporting] = useState<"csv" | "png" | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  // A3: 收盘后自动增量分析 checkbox。
  const [autoSubmit, setAutoSubmit] = useState(false);
  // A4: 增量结果相对 prev 记录新增的 K 线数（null = 未知 / 非增量）。
  const [newBarCount, setNewBarCount] = useState<number | null>(null);

  const runAnalysis = useCallback(async (incremental: boolean) => {
    setError(null);
    setResult(null);
    setNewBarCount(null);
    setStreamText("");
    setStreamPhase(null);
    setChatMessages([]);
    const sym = symbol.trim();
    if (!sym) {
      setError("请输入交易对，如 BTC/USDT");
      return;
    }
    setLoading(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const data = await streamAnalyzeMarket(
        {
          exchange,
          market_type: marketType,
          symbol: sym,
          interval,
          trade_temperament: temperament,
          incremental,
        },
        (ev) => {
          if (ev.type === "stage1_start") setStreamPhase("stage1");
          else if (ev.type === "stage2_start") setStreamPhase("stage2");
          else if (ev.type === "stage1_text" || ev.type === "stage2_text") {
            setStreamText((t) => t + ev.delta);
          }
        },
        controller.signal,
      );
      if (data) setResult(data);
    } catch (e: unknown) {
      if (e instanceof ApiError) {
        const msg = (e.details as { message?: string } | undefined)?.message;
        setError(msg ?? `分析失败 (HTTP ${e.status})`);
      } else if (e instanceof Error && e.name === "AbortError") {
        setError("已取消分析");
      } else {
        setError(`分析失败: ${e instanceof Error ? e.message : String(e)}`);
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }, [exchange, marketType, symbol, interval, temperament]);

  const handleSubmit = useCallback(() => {
    void runAnalysis(false);
  }, [runAnalysis]);

  // US-005: 增量分析（复用最近同 symbol+interval 成功记录作为先验）。
  const handleIncremental = useCallback(() => {
    void runAnalysis(true);
  }, [runAnalysis]);

  // A3: 倒计时归零触发（CloseCountdown 通过 onElapsedRef 每次取最新闭包，loading 为当前值）。
  const handleAutoElapsed = useCallback(() => {
    setAutoSubmit(false);
    if (loading) {
      setActionMsg("收盘已到，但分析进行中，跳过自动提交");
      return;
    }
    void handleIncremental();
    setActionMsg("已到收盘，自动增量分析");
  }, [loading, handleIncremental]);

  // A4: 增量结果带 prev_record_id 时，拉 prev 记录末根 K 线时间戳，统计其后新增 K 线数。
  // setState 只在 .then 异步回调内（满足 react-hooks/set-state-in-effect）；失败静默（增强提示）。
  useEffect(() => {
    let cancelled = false;
    const prevId = result?.meta?.prev_record_id;
    const currentBars = result?.input_data?.klines;
    if (!prevId || !currentBars?.length) return;
    getRecord(prevId)
      .then((prev) => {
        if (cancelled) return;
        const prevBars = prev?.input_data?.klines;
        if (!prevBars?.length) return;
        const prevLastTs = prevBars[prevBars.length - 1].timestamp;
        setNewBarCount(
          currentBars.filter(
            (b) => typeof b.timestamp === "number" && b.timestamp > prevLastTs,
          ).length,
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [result]);

  const handleAbort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const recordId = result?.meta.record_id ?? null;

  // US-302 Free Chat
  const handleChatSubmit = useCallback(async () => {
    if (!recordId) return;
    const msg = chatInput.trim();
    if (!msg) return;
    setChatError(null);
    setChatLoading(true);
    try {
      const reply = await chatMessage(recordId, msg);
      setChatMessages(reply.messages);
      setChatInput("");
    } catch (e: unknown) {
      setChatError(e instanceof Error ? e.message : String(e));
    } finally {
      setChatLoading(false);
    }
  }, [recordId, chatInput]);

  const handleLoadChatHistory = useCallback(async () => {
    if (!recordId) return;
    setChatError(null);
    try {
      const { messages } = await getChatHistory(recordId);
      setChatMessages(messages);
    } catch (e: unknown) {
      setChatError(e instanceof Error ? e.message : String(e));
    }
  }, [recordId]);

  // US-304 演示回放 / 导出 / 通知
  const handleLoadRecent = useCallback(async () => {
    setRecordsLoading(true);
    try {
      const { records } = await listRecentRecords(undefined, 20);
      setRecentRecords(records);
    } catch (e: unknown) {
      setActionMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setRecordsLoading(false);
    }
  }, []);

  const handlePlayback = useCallback(async (id: string) => {
    try {
      const rec = await getRecord(id);
      if (rec) {
        setResult(rec);
        setError(null);
        setChatMessages([]);
        setActionMsg(`已回放记录 ${id}`);
      }
    } catch (e: unknown) {
      setActionMsg(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const handleNotify = useCallback(async () => {
    if (!recordId) return;
    setActionMsg(null);
    try {
      const res = await sendNotify(recordId);
      if (res.status === "disabled" || res.status === "skipped") {
        setActionMsg(`通知未发送：${res.reason ?? res.status}`);
      } else {
        setActionMsg(`通知已发送 feishu=${res.feishu ?? "-"} · pushplus=${res.pushplus ?? "-"}`);
      }
    } catch (e: unknown) {
      setActionMsg(e instanceof Error ? e.message : String(e));
    }
  }, [recordId]);

  const handleExportCsv = useCallback(async () => {
    if (!recordId) return;
    setExporting("csv");
    setActionMsg(null);
    try {
      await downloadRecordCsv(recordId);
      setActionMsg("CSV 已下载");
    } catch (e: unknown) {
      setActionMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(null);
    }
  }, [recordId]);

  const handleExportPng = useCallback(() => {
    setActionMsg(null);
    downloadChartPng("#ma-kline-chart", recordId ? `${recordId}-chart` : "ma-kline-chart");
    setActionMsg("PNG 已下载");
  }, [recordId]);

  const klines = result?.input_data?.klines ?? [];
  const s1 = result?.stage1;
  const s2 = result?.stage2;
  const playbooks =
    result?.dispatch_playbooks?.length
      ? result.dispatch_playbooks
      : (s2?.dispatch_playbooks ?? []);
  const usage = result?.usage_total ?? {};
  // US-006: 等待收盘实时倒计时（setInterval 每秒刷新 mm:ss；K 线收盘后自动消失）。
  // US-006: 等待收盘实时倒计时（子组件按 closeAt key 重挂载，now 状态挂载时初始化，无陈旧跳变）。
  const closeAt = closeAtOf(result);

  // US-006: 调试 / 原始视图数据（prompt / raw / exception / json_repaired，仅已有数据）。
  const promptData = result?.prompt as { stage1?: unknown; stage2?: unknown } | undefined;
  const rawData = result?.raw as { stage1?: unknown; stage2?: unknown } | undefined;
  const jsonRepaired = !!((result?.meta as Record<string, unknown> | undefined)?.json_repaired);
  const [copiedDebug, setCopiedDebug] = useState(false);
  const debugBlocks = [
    promptData?.stage1 != null && { label: "Stage 1 prompt", content: JSON.stringify(promptData.stage1, null, 2) },
    promptData?.stage2 != null && { label: "Stage 2 prompt", content: JSON.stringify(promptData.stage2, null, 2) },
    rawData?.stage1 != null && { label: "Stage 1 raw", content: JSON.stringify(rawData.stage1, null, 2) },
    rawData?.stage2 != null && { label: "Stage 2 raw", content: JSON.stringify(rawData.stage2, null, 2) },
    result?.exception != null && { label: "exception", content: JSON.stringify(result.exception, null, 2) },
  ].filter((b): b is { label: string; content: string } => Boolean(b));
  const handleCopyDebug = useCallback(async () => {
    const data = {
      prompt: promptData ?? null,
      raw: rawData ?? null,
      exception: result?.exception ?? null,
      json_repaired: jsonRepaired,
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
      setCopiedDebug(true);
      window.setTimeout(() => setCopiedDebug(false), 1500);
    } catch {
      /* clipboard 不可用时静默降级，不打断主流程 */
    }
  }, [promptData, rawData, result, jsonRepaired]);

  return (
    <div className="space-y-4 h-full overflow-auto">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <BrainCircuit className="h-4 w-4" />行情分析 / Market Analysis
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-col gap-3 md:flex-row md:items-end">
            <div className="flex flex-col gap-1 md:w-40">
              <label className="text-xs text-muted-foreground">交易所</label>
              <Select value={exchange} onValueChange={setExchange}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {EXCHANGES.map((x) => (
                    <SelectItem key={x} value={x}>{x}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1 md:w-32">
              <label className="text-xs text-muted-foreground">市场</label>
              <Select value={marketType} onValueChange={setMarketType}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {MARKET_TYPES.map((m) => (
                    <SelectItem key={m} value={m}>{m}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1 flex-1">
              <label className="text-xs text-muted-foreground">交易对</label>
              <input
                className="h-9 rounded-md border border-input bg-background px-3 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                value={symbol}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSymbol(e.target.value)}
                spellCheck={false}
                placeholder="BTC/USDT"
              />
            </div>
            <div className="flex flex-col gap-1 md:w-28">
              <label className="text-xs text-muted-foreground">周期</label>
              <Select value={interval} onValueChange={setInterval}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {INTERVALS.map((iv) => (
                    <SelectItem key={iv} value={iv}>{iv}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1 md:w-40">
              <label className="text-xs text-muted-foreground">交易倾向</label>
              <Select
                value={temperament}
                onValueChange={(v) => setTemperament(v as TradeTemperament)}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(TEMPERAMENT_META) as TradeTemperament[]).map((k) => (
                    <SelectItem key={k} value={k}>{TEMPERAMENT_META[k].label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex gap-2">
              <Button
                onClick={loading ? handleAbort : handleSubmit}
                variant={loading ? "destructive" : "default"}
                size="sm"
                className="md:mt-5"
              >
                {loading ? <Square className="h-4 w-4 mr-1" /> : <Send className="h-4 w-4 mr-1" />}
                {loading ? "停止" : "开始分析"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="md:mt-5"
                onClick={handleIncremental}
                disabled={loading}
                title="复用最近同交易对+周期的成功记录作为先验，快速重推"
              >
                <RefreshCw className="h-4 w-4 mr-1" />
                增量分析
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <Card>
          <CardContent className="py-3">
            <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3">
              <AlertTriangle className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
              <p className="text-sm font-medium text-destructive">{error}</p>
            </div>
            {streamText && (
              <details className="mt-2 rounded-md border border-border-soft bg-surface-2/40 p-2">
                <summary className="cursor-pointer text-xs text-muted-foreground">已流式输出（未完成）</summary>
                <p className="mt-1 whitespace-pre-wrap font-mono text-xs text-ink-3">{streamText}</p>
              </details>
            )}
          </CardContent>
        </Card>
      )}

      {loading && !result && (
        <Card>
          <CardContent className="py-6 space-y-3">
            <div className="flex items-center gap-2 text-sm">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span className="font-medium">
                {streamPhase === "stage2" ? "Stage 2 决策中…" : "Stage 1 诊断中…"}
              </span>
              <span className="ml-auto text-xs text-muted-foreground">
                {streamPhase === "stage2" ? "LLM 生成决策" : "LLM 生成诊断"}
              </span>
            </div>
            <div className="max-h-64 overflow-auto rounded-md border border-border-soft bg-surface-2/40 p-3">
              <p className="whitespace-pre-wrap font-mono text-xs text-ink-3">
                {streamText || "（等待 LLM 输出…）"}
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {result && (
        <>
          <Card>
            <CardContent className="py-3 flex items-start gap-2 text-xs text-muted-foreground">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <span>
                本页由 AI 两阶段分析生成，仅供研究参考，不构成投资建议。下一根 K 线预测能力为
                实验性 · 未充分回测（MVP 暂不展示预测字段）。
              </span>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="py-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-muted-foreground">交易倾向</span>
                <Badge variant={TEMPERAMENT_META[temperament].variant}>{TEMPERAMENT_META[temperament].label}</Badge>
                <span className="text-xs text-muted-foreground">
                  entry_gate={s1?.entry_gate} · decision={s2?.decision ?? "—"}
                </span>
              </div>
              {closeAt != null && (
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                  <CloseCountdown key={closeAt} closeAt={closeAt} autoSubmit={autoSubmit} onElapsed={handleAutoElapsed} />
                  <label className="flex cursor-pointer select-none items-center gap-1.5">
                    <Checkbox
                      checked={autoSubmit}
                      onCheckedChange={(v) => setAutoSubmit(v === true)}
                      disabled={loading}
                    />
                    收盘后自动增量分析
                  </label>
                </div>
              )}
            </CardContent>
          </Card>

          {/* US-006 调试 / 原始视图：prompt / raw / exception / json_repaired + 复制 */}
          <Card>
            <details className="group">
              <summary className="flex cursor-pointer select-none items-center gap-2 py-1 text-xs text-muted-foreground">
                <Bug className="h-3.5 w-3.5 shrink-0" />
                调试 / 原始视图
                {jsonRepaired && <Badge variant="warning" className="text-[10px]">JSON 修复</Badge>}
              </summary>
              <CardContent className="pt-1">
                <div className="rounded-md border border-border-soft bg-surface-2/40 p-3">
                  <div className="mb-2 flex items-center justify-end">
                    <Button
                      variant="outline" size="sm" className="h-6 text-xs"
                      onClick={handleCopyDebug} disabled={debugBlocks.length === 0}
                    >
                      {copiedDebug ? <Check className="h-3 w-3 mr-1" /> : <Copy className="h-3 w-3 mr-1" />}
                      {copiedDebug ? "已复制" : "复制"}
                    </Button>
                  </div>
                  {debugBlocks.length === 0 ? (
                    <p className="text-xs text-muted-foreground">无调试数据（本次未返回 prompt / raw / exception）</p>
                  ) : (
                    <div className="space-y-3">
                      {debugBlocks.map((b) => (
                        <div key={b.label}>
                          <p className="mb-1 font-medium text-ink-3">{b.label}</p>
                          <pre className="whitespace-pre-wrap break-all rounded bg-surface-3/60 p-2 font-mono text-[10px] leading-relaxed text-ink-3">
                            {b.content}
                          </pre>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </CardContent>
            </details>
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Stage1Card s1={s1} />
            <Stage2Card s2={s2} />
          </div>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <LineChart className="h-4 w-4" />K线
                <Badge variant="warning" className="text-[10px]">实验性 · 未充分回测</Badge>
                <span className="ml-auto flex items-center gap-1">
                  <Button
                    variant="outline" size="sm" className="h-7 text-xs"
                    onClick={handleExportCsv} disabled={!recordId || exporting === "csv"}
                  >
                    {exporting === "csv" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                    CSV
                  </Button>
                  <Button
                    variant="outline" size="sm" className="h-7 text-xs"
                    onClick={handleExportPng} disabled={exporting === "png"}
                  >
                    <Download className="h-3 w-3" />
                    PNG
                  </Button>
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {klines.length > 0
                ? <CandleChart bars={klines} s2={s2} />
                : <p className="text-xs text-muted-foreground">无 K 线数据</p>}
            </CardContent>
          </Card>

          {/* US-302 Free Chat 自由追问 */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <MessageSquare className="h-4 w-4" />Free Chat 自由追问
                {recordId && (
                  <Button variant="ghost" size="sm" className="ml-auto h-6 text-xs" onClick={handleLoadChatHistory}>
                    <RefreshCw className="h-3 w-3 mr-1" />刷新历史
                  </Button>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="max-h-64 space-y-2 overflow-auto rounded-md border border-border-soft p-3">
                {chatMessages.length === 0 && !chatLoading && (
                  <p className="text-xs text-muted-foreground">
                    围绕本次分析结果追问（如：为什么判定为趋势向上？下一根 K 线怎么看？）
                  </p>
                )}
                {chatMessages.map((m, i) => (
                  <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                    <div className={`max-w-[85%] whitespace-pre-wrap rounded-md px-3 py-2 text-xs ${m.role === "user" ? "bg-primary/10" : "bg-surface-3"}`}>
                      {m.content}
                    </div>
                  </div>
                ))}
                {chatLoading && <div className="text-xs text-muted-foreground">思考中…</div>}
              </div>
              {chatError && <p className="text-xs text-destructive">{chatError}</p>}
              <div className="flex gap-2">
                <input
                  className="h-9 flex-1 rounded-md border border-input bg-background px-3 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={chatInput}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setChatInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !chatLoading) void handleChatSubmit(); }}
                  placeholder="输入追问…"
                  disabled={!recordId || chatLoading}
                />
                <Button size="sm" onClick={handleChatSubmit} disabled={!recordId || chatLoading || !chatInput.trim()}>
                  {chatLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  发送
                </Button>
              </div>
            </CardContent>
          </Card>

          {playbooks.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Playbook</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-1">
                {playbooks.map((p, i) => (
                  <Badge key={i} variant="outline" className="font-mono text-xs">{p}</Badge>
                ))}
              </CardContent>
            </Card>
          )}

          <Card>
            <CardContent className="py-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>record: {result.meta.record_id ?? "-"}</span>
              {result.meta.prev_record_id && (
                <span className="text-ink-3" title="增量分析基于的记录">
                  prev: {result.meta.prev_record_id.slice(-12)}
                  {newBarCount != null && newBarCount > 0 && <span className="ml-1">+{newBarCount} 根新 K 线</span>}
                </span>
              )}
              <span>{symbol.trim() || "-"} · {interval} · {exchange}/{marketType}</span>
              {result.meta.timestamp && <span>{new Date(result.meta.timestamp).toLocaleString()}</span>}
              {Object.keys(usage).length > 0 && <TokenBudgetBar usage={usage} />}
              {actionMsg && <span className="w-full text-ink-3">{actionMsg}</span>}
              <span className="ml-auto">
                <Button variant="outline" size="sm" className="h-6 text-xs" onClick={handleNotify} disabled={!recordId}>
                  <Send className="h-3 w-3 mr-1" />通知
                </Button>
              </span>
            </CardContent>
          </Card>
        </>
      )}

      {/* US-304 历史记录（演示回放） */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <History className="h-4 w-4" />历史记录（演示回放）
            <Button variant="ghost" size="sm" className="ml-auto h-6 text-xs" onClick={handleLoadRecent} disabled={recordsLoading}>
              {recordsLoading ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <RefreshCw className="h-3 w-3 mr-1" />}
              刷新
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {recentRecords.length === 0 ? (
            <p className="text-xs text-muted-foreground">暂无历史记录（完成一次分析后点“刷新”可见）</p>
          ) : (
            <div className="max-h-64 space-y-1 overflow-auto">
              {recentRecords.map((r) => {
                const rid = r.meta.record_id ?? "";
                const isActive = rid === recordId;
                return (
                  <div key={rid} className="flex flex-wrap items-center gap-2 text-xs">
                    <Badge variant={isActive ? "default" : "outline"} className="font-mono">{rid.slice(-12)}</Badge>
                    <span className="text-ink-3">{r.input_data?.symbol ?? "-"} · {r.input_data?.interval ?? "-"}</span>
                    <span className="text-muted-foreground">
                      {r.meta.timestamp ? new Date(r.meta.timestamp).toLocaleString() : ""}
                    </span>
                    <span className="ml-auto">
                      <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => void handlePlayback(rid)} disabled={isActive}>
                        回放
                      </Button>
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <ForecastEvalCard symbol={symbol.trim() || "BTC/USDT"} interval={interval} />
      <ICDiagnosticCard symbols={crossSection(symbol)} interval={interval} />
      <ReturnReportCard symbols={crossSection(symbol)} interval={interval} />

      {!result && !loading && !error && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-16 text-muted-foreground">
            <BrainCircuit className="h-12 w-12 mb-3 opacity-30" />
            <p>选择交易对与周期，点击“开始分析”获取两阶段行情诊断</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
