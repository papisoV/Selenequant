"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { V17Card } from "@/components/shared/page-filter-bar";
import { useWorkflowStore, type OptimizationResult } from "@/lib/store/workflow";
import { useRouter, useSearchParams } from "next/navigation";
import { toastError, toastSuccess } from "@/lib/toast";
import { formatTime } from "@/lib/format";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  getBacktestDetail, listFactoryTemplates, runBacktest, deleteBacktest,
  type BacktestDetail, type BacktestTrade, type FactoryTemplate, type RunBacktestBody,
} from "@/lib/api/backtests";
import { buildBacktestDetailOption } from "@/lib/chart/backtest-detail-chart";
import { barEstimateTone, estimateBars } from "@/lib/chart/trade-review";
import { useChartTheme } from "@/components/chart/use-chart-theme";
import { BacktestTradeReview } from "./backtest-trade-review";
import { Loader2, Info, Play, RotateCcw, ShieldCheck, ShieldAlert, Trash2 } from "lucide-react";

// 业务域插入页 — 回测中心
interface Backtest {
  task_id?: string;
  symbol?: string;
  strategy?: string;
  strategy_name?: string;
  strategy_id?: string;
  interval?: string;
  start_date?: string;
  end_date?: string;
  sharpe?: number;
  sharpe_ratio?: number;
  total_return?: number;
  total_trades?: number;
  status?: string;
  created_at?: number;
}

/** 取第一个非空字符串;全空回退 "—"。?? 兜不住 "",所以显式判空。 */
const nonEmpty = (...vals: (string | undefined | null)[]) =>
  vals.find((v) => typeof v === "string" && v.trim() !== "") ?? "—";

/**
 * 缺失指标渲染 "—",绝不伪造 0。
 * 0.00% / 0.00 会被读成「这策略收益 0% 且零成交」——一个合法且糟糕的结论;
 * 而真相往往是「这条记录没存下结果」。二者必须可区分。
 */
const fmtMetric = (
  v: number | null | undefined,
  render: (n: number) => string,
): string => (typeof v === "number" && Number.isFinite(v) ? render(v) : "—");

/**
 * 数量(币本位小数)按量级取有效位,避免 19 位浮点尾巴(`0.02050493439890924`)
 * 撑爆列宽把相邻列挤在一起。小额保留 6 位、常规 4 位、大额 2 位,均够复盘用。
 */
const fmtQty = (v: number | null | undefined): string => {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs === 0) return "0";
  if (abs < 1) return v.toFixed(6);
  if (abs < 1000) return v.toFixed(4);
  return v.toFixed(2);
};

/**
 * 列表列宽:显式 px,不用 rem-based `w-16`/`w-14`。
 * 根字号 11px 会把 rem 类缩到 0.6875× —— 任务列 `w-16` 只落 44px,
 * "bt-4121951a" 折成两行把整行撑高;收益列更窄,`+1685.02%` 直接溢出撞进相邻列。
 * 同源缺陷见详情弹窗内的交易表(已用 `grid-cols-[...]` 显式 px)。
 *
 * 「周期」「区间」两列是必需的可区分性维度:同一策略同一标的常跑出多条记录,
 * 只靠 标的+策略 完全分不清谁是谁,而区分它们的正是 蜡烛周期 + 回测区间。
 */
const LIST_GRID =
  "grid grid-cols-[104px_74px_minmax(120px,1fr)_52px_150px_84px_64px_84px_204px] gap-x-3 items-center";

/**
 * 区间短格式:`YYYY-MM-DD` → `YYYY/MM/DD`,非 ISO 串原样回退。
 * 列表列窄,带时分秒的时间戳会溢出折行,这里只取日期段。
 */
const fmtRange = (start?: string, end?: string): string => {
  const day = (s?: string) => (s && s.length >= 10 ? s.slice(0, 10).replace(/-/g, "/") : "");
  const a = day(start);
  const b = day(end);
  if (!a && !b) return "—";
  if (a && b) return `${a}→${b}`;
  return a || b;
};

export function BacktestsPage() {
  const { data, mutate } = useApi<Backtest[] | null>(
    "/api/v1/backtests",
    { refreshInterval: 5000 }
  );
  const rows = data ?? [];
  const setBacktest = useWorkflowStore((s) => s.setBacktest);
  const setDeployTarget = useWorkflowStore((s) => s.setDeployTarget);
  const optimization = useWorkflowStore((s) => s.optimization);
  const router = useRouter();
  const searchParams = useSearchParams();

  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<BacktestDetail | null>(null);
  const [highlightTaskId, setHighlightTaskId] = useState<string | null>(null);
  const [prefillFlash, setPrefillFlash] = useState(false);
  const [reviewIndex, setReviewIndex] = useState<number | null>(null); // 复盘卡展开的交易下标
  const [now, setNow] = useState(() => Date.now());
  const chartHostRef = useRef<HTMLDivElement | null>(null);

  // 运行中任务的耗时秒表(1s tick;无运行中任务时空转的 interval 开销可忽略,不做条件化增加复杂度)
  useEffect(() => {
    if (!rows.some((r) => r.status === "running")) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [rows]);

  // --- 发起区 ---
  const { data: templates, mutate: mutateList } = useApi<FactoryTemplate[]>(
    "/api/v1/strategies/factory-templates"
  );
  const [launching, setLaunching] = useState(false);
  const [form, setForm] = useState<RunBacktestBody>({
    strategy_id: "bb_v3",
    symbol: "BTC/USDT",
    interval: "1d",
    exchange: "binance",
    market_type: "spot",
    start_date: new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10),
    end_date: new Date().toISOString().slice(0, 10),
    initial_capital: 10000,
    commission_rate: 0.001,
    slippage_bps: 5,
  });
  const setField = (patch: Partial<RunBacktestBody>) =>
    setForm((f) => ({ ...f, ...patch }));

  // 发车区间预估(后端无 bar 守卫,这是提交前唯一预警)。区间非法→null 不显示。
  const estBars = useMemo(
    () =>
      estimateBars(
        Date.parse(form.start_date ?? ""),
        Date.parse(form.end_date ?? ""),
        form.interval ?? "1h",
      ),
    [form.start_date, form.end_date, form.interval],
  );
  const estTone = estBars != null ? barEstimateTone(estBars) : "ok";

  async function handleLaunch() {
    if (!form.strategy_id) {
      toastError("请选择策略模板");
      return;
    }
    setLaunching(true);
    try {
      const res = await runBacktest(form);
      toastSuccess(`回测已提交 ${res.task_id ?? ""}`);
      await mutateList();
    } catch (e) {
      toastError(e instanceof Error ? e.message : "回测提交失败");
    } finally {
      setLaunching(false);
    }
  }

  const templateOptions = useMemo(
    () => templates ?? [],
    [templates]
  );
  // --- 发起区结束 ---

  // 历史回填重跑:detail(task 行 + config_snapshot)→ 发车表单。
  // 旧快照缺键留表单现值,不造假;回填后滚到发车区并闪一下提示。
  function prefillFromDetail(d: BacktestDetail) {
    const snap = d.config_snapshot;
    const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
    setField({
      ...(d.strategy_id ? { strategy_id: d.strategy_id } : {}),
      ...(d.symbol ? { symbol: d.symbol } : {}),
      ...(d.interval ? { interval: d.interval } : {}),
      ...(d.start_date ? { start_date: d.start_date.slice(0, 10) } : {}),
      ...(d.end_date ? { end_date: d.end_date.slice(0, 10) } : {}),
      ...(num(snap?.initial_capital) != null ? { initial_capital: num(snap?.initial_capital) } : {}),
      ...(num(snap?.commission_rate) != null ? { commission_rate: num(snap?.commission_rate) } : {}),
      ...(num(snap?.slippage_bps) != null ? { slippage_bps: num(snap?.slippage_bps) } : {}),
    });
    setDetailOpen(false);
    router.replace("/trade/backtests", { scroll: false });
    document.querySelector("#backtest-launch-form")?.scrollIntoView({ behavior: "smooth", block: "center" });
    setPrefillFlash(true);
    setTimeout(() => setPrefillFlash(false), 1600);
  }

  // 幽灵交接:调参/优化后点"去回测"跳转过来,自动选中该 taskId 行并弹详情
  useEffect(() => {
    if (!optimization?.taskId) return;
    const target = rows.find((r) => r.task_id === optimization.taskId);
    if (target) {
      setHighlightTaskId(optimization.taskId);
      openBacktestDetail(optimization.taskId);
    }
    // 交接是一次性的,读完后清掉避免重复弹
    useWorkflowStore.setState({ optimization: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, optimization?.taskId]);

  async function openBacktestDetail(taskId: string) {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetail(null);
    // URL 同步:?task_id= 让详情可分享/可刷新直达(关 Dialog 时清除)
    router.replace(`/trade/backtests?task_id=${encodeURIComponent(taskId)}`, { scroll: false });
    setReviewIndex(null);
    try {
      const data = await getBacktestDetail(taskId);
      setDetail(data);
    } catch {
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }

  // ?task_id= 直达:URL 带参(分享链接/刷新)自动弹详情
  useEffect(() => {
    const urlTaskId = searchParams.get("task_id");
    if (urlTaskId && !detailOpen) {
      openBacktestDetail(urlTaskId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const handleRowClick = (r: Backtest) => {
    if (r.status !== "completed") {
      toastError("回测未完成，无法部署");
      return;
    }
    setBacktest({
      taskId: r.task_id ?? "?",
      sharpe: r.sharpe,
      status: r.status,
      pickedAt: Date.now(),
    });
    setDeployTarget("paper");
    router.push("/trade/paper");
  };

  const handleLiveDeploy = (r: Backtest) => {
    if (r.status !== "completed") {
      toastError("回测未完成，无法部署");
      return;
    }
    setBacktest({
      taskId: r.task_id ?? "?",
      sharpe: r.sharpe,
      status: r.status,
      pickedAt: Date.now(),
    });
    setDeployTarget("live");
    router.push("/trade/trd");
  };

  // 实盘确认弹窗目标行(非 completed 不可部署,按钮已禁用,弹窗只接 confirmed 行)
  const [liveConfirm, setLiveConfirm] = useState<Backtest | null>(null);

  // 删除:行内两段确认(task_id)→ 确认后调 DELETE。busy 期禁用防重复提交。
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  async function handleDelete(taskId: string) {
    setDeletingId(taskId);
    try {
      await deleteBacktest(taskId);
      toastSuccess(`已删除 ${taskId}`);
      // 删掉的正是当前打开的详情 → 一并关掉,不留悬空 URL
      if (detail?.task_id === taskId || detail?.id === taskId) {
        setDetailOpen(false);
        router.replace("/trade/backtests", { scroll: false });
      }
      await mutate();
    } catch (e) {
      toastError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setDeletingId(null);
      setConfirmDelete(null);
    }
  }

  // echarts 三层图(权益+基准 / 回撤)— 动态 import 防 SSR,颜色走图表 token 契约。
  // theme 来自 useChartTheme():locale 变化返回新引用 → 本 effect 重跑重绘
  // (此前只 getComputedStyle 读一次,locale 切换后滞留旧色 = 缺陷范式)。
  const theme = useChartTheme();
  useEffect(() => {
    const host = chartHostRef.current;
    if (!detailOpen || !host || !detail) return;
    const eq = (detail.equity_curve ?? []).filter((v) => Number.isFinite(v));
    if (eq.length < 2) return;

    let disposed = false;
    let instance: { dispose: () => void } | null = null;

    import("echarts").then((echarts) => {
      if (disposed) return;
      const chart = echarts.init(host, "dark", { renderer: "canvas" });
      instance = chart;
      chart.setOption(
        buildBacktestDetailOption({
          timestamps: detail.timestamps ?? [],
          equityCurve: detail.equity_curve ?? [],
          buyHoldCurve: detail.buy_hold_curve ?? undefined,
          drawdownCurve: detail.drawdown_curve ?? undefined,
          exposureCurve: detail.exposure_curve ?? undefined,
          colors: {
            gold: theme.gold,
            ink3: theme.ink3,
            // 回撤是**判定轴**语义(恶化程度),恒定不翻转 —— 非方向轴
            destructive: theme.destructive,
            borderSoft: theme.borderSoft,
          },
        })
      );
    });
    return () => {
      disposed = true;
      instance?.dispose();
    };
  }, [detailOpen, detail, theme]);

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="发起回测">
        {/* group: 两组字段用竖分隔线断开 —— 左=选什么(标的/周期/区间),右=按什么成本跑(资金/费率/滑点)。
            原先 14 个控件挤在一行无分组,用户读不出字段归属。 */}
        <div id="backtest-launch-form" className={`flex flex-wrap items-end gap-x-3 gap-y-2 text-[10px] rounded transition-shadow ${prefillFlash ? "ring-1 ring-sky-500/60" : ""}`}>
          <div className="flex flex-wrap items-end gap-2">
            <span className="self-end pb-1 text-[9px] uppercase tracking-wider text-ink-4">标的 · 周期 · 区间</span>
            <label className="flex flex-col gap-0.5">
              <span className="text-ink-4">策略模板</span>
              <select
                className="h-7 rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] text-ink w-[170px]"
                value={form.strategy_id}
                onChange={(e) => setField({ strategy_id: e.target.value })}
              >
                {templateOptions.length === 0 && <option value={form.strategy_id}>{form.strategy_id}</option>}
                {templateOptions.map((t) => (
                  <option key={t.id} value={t.id}>{t.name} ({t.id})</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-ink-4">标的</span>
              <select
                className="h-7 rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] text-ink w-[100px]"
                value={form.symbol}
                onChange={(e) => setField({ symbol: e.target.value })}
              >
                {["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"].map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-ink-4">周期</span>
              <select
                className="h-7 rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] text-ink w-[70px]"
                value={form.interval}
                onChange={(e) => setField({ interval: e.target.value })}
              >
                {["1m", "5m", "15m", "1h", "4h", "1d"].map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-ink-4">开始</span>
              <input
                type="date"
                className="h-7 rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] text-ink w-[120px]"
                value={form.start_date}
                onChange={(e) => setField({ start_date: e.target.value })}
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-ink-4">结束</span>
              <input
                type="date"
                className="h-7 rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] text-ink w-[120px]"
                value={form.end_date}
                onChange={(e) => setField({ end_date: e.target.value })}
              />
            </label>
          </div>
          {/* 区间预估:后端无 bar 守卫,这里提醒 1m×长区间会拖慢/卡死。
              颜色用 text-warn/ink-4 —— 语义色,不用方向轴 token(P0-A 双轴正典:
              判定轴语义恒用 destructive/success/warn,方向轴 pos/neg 只表数值涨跌)。 */}
          {estBars != null && (
            <span
              className={`self-end pb-1.5 text-[10px] whitespace-nowrap ${
                estTone === "heavy"
                  ? "text-warn font-medium"
                  : estTone === "warn"
                    ? "text-warn"
                    : "text-ink-4"
              }`}
              title="按自然日直算,未扣休市日"
            >
              约 {estBars.toLocaleString()} 根{estTone === "heavy" ? " · 建议缩短区间或放大周期" : estTone === "warn" ? " · 可能较慢" : ""}
            </span>
          )}
          <div className="self-stretch w-px bg-border-soft mx-0.5" aria-hidden />
          <div className="flex flex-wrap items-end gap-2">
            <span className="self-end pb-1 text-[9px] uppercase tracking-wider text-ink-4">资金 · 成本</span>
            <label className="flex flex-col gap-0.5">
              <span className="text-ink-4">初始资金</span>
              <input
                type="number"
                min={100}
                step={1000}
                className="h-7 rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] text-ink w-[90px]"
                value={form.initial_capital}
                onChange={(e) => setField({ initial_capital: Number(e.target.value) })}
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-ink-4">费率</span>
              <input
                type="number"
                min={0}
                max={0.02}
                step={0.0001}
                className="h-7 rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] text-ink w-[80px]"
                value={form.commission_rate}
                onChange={(e) => setField({ commission_rate: Number(e.target.value) })}
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-ink-4">滑点bps</span>
              <input
                type="number"
                min={0}
                max={100}
                step={1}
                className="h-7 rounded border border-border-soft bg-surface-2 px-1.5 text-[10px] text-ink w-[70px]"
                value={form.slippage_bps}
                onChange={(e) => setField({ slippage_bps: Number(e.target.value) })}
              />
            </label>
          </div>
          <button
            className="h-7 rounded bg-pos px-3 text-[10px] font-medium text-bg hover:bg-pos/85 disabled:opacity-50 flex items-center gap-1 ml-auto"
            onClick={handleLaunch}
            disabled={launching}
          >
            {launching ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
            开始回测
          </button>
        </div>
      </V17Card>
      <V17Card
        title="回测列表"
        right={
          <div className="flex items-center gap-3 text-[10px]">
            <span className="text-ink-4">
              总 <span className="font-mono text-ink-2">{rows.length}</span>
            </span>
            <span className="text-success">
              完成 <span className="font-mono">{rows.filter((r) => r.status === "completed").length}</span>
            </span>
            <span className="text-gold">
              运行中 <span className="font-mono">{rows.filter((r) => r.status === "running").length}</span>
            </span>
            <span className="text-destructive">
              失败 <span className="font-mono">{rows.filter((r) => r.status === "failed").length}</span>
            </span>
          </div>
        }
      >
        <div className="flex flex-col">
          {rows.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-6">
              还没有回测记录 — 在上方「发起回测」选一个策略模板提交
            </div>
          )}
          {rows.length > 0 && (
            <div className={`${LIST_GRID} text-[9px] uppercase tracking-wider text-ink-4 pb-1 border-b border-border-soft`}>
              <span className="truncate">任务</span>
              <span className="truncate">标的</span>
              <span className="truncate">策略</span>
              <span className="truncate">周期</span>
              <span className="truncate">区间</span>
              <span className="truncate text-right">收益</span>
              <span className="truncate text-right">夏普</span>
              <span className="truncate text-right">时间</span>
              <span className="truncate text-right">操作</span>
            </div>
          )}
          {rows.map((r, i) => {
            const cls = r.status === "completed" ? "text-success" : r.status === "running" ? "text-gold" : "text-destructive";
            const isDeployable = r.status === "completed";
            const ret = r.total_return;
            const hasRet = typeof ret === "number" && Number.isFinite(ret);
            const rowActions = `h-[24px] rounded px-2 text-[10px] whitespace-nowrap border border-border-soft bg-surface-3 text-ink-3 hover:bg-sky-500/15 hover:text-sky-400`;
            return (
              <div
                key={i}
                className={`${LIST_GRID} text-[10px] py-1 border-b border-border-soft cursor-pointer hover:bg-surface-2 ${r.task_id && r.task_id === highlightTaskId ? "bg-sky-500/10" : ""}`}
                onClick={() => { setHighlightTaskId(null); if (r.task_id) openBacktestDetail(r.task_id); }}
              >
                <span className="truncate text-ink-4 font-mono">{r.task_id}</span>
                <span className="truncate text-ink font-medium">{nonEmpty(r.symbol)}</span>
                <span className="truncate text-ink-2">{nonEmpty(r.strategy_name, r.strategy_id, r.strategy)}</span>
                <span className="truncate text-ink-3 font-mono">{nonEmpty(r.interval)}</span>
                <span
                  className="truncate text-ink-4 font-mono num"
                  title={r.start_date && r.end_date ? `${r.start_date} → ${r.end_date}` : undefined}
                >
                  {fmtRange(r.start_date, r.end_date)}
                </span>
                <span
                  className={`text-right font-mono num whitespace-nowrap ${hasRet ? (ret! >= 0 ? "text-pos" : "text-neg") : "text-ink-4"}`}
                  title={hasRet ? undefined : r.status === "completed" ? "该记录未保存指标" : undefined}
                >
                  {fmtMetric(ret, (n) => `${n >= 0 ? "+" : ""}${(n * 100).toFixed(2)}%`)}
                </span>
                <span
                  className={`text-right font-mono num whitespace-nowrap ${r.sharpe == null && r.sharpe_ratio == null ? "text-ink-4" : "text-gold"}`}
                  title={r.sharpe == null && r.sharpe_ratio == null ? "该记录未保存指标" : undefined}
                >
                  {fmtMetric(r.sharpe ?? r.sharpe_ratio, (n) => n.toFixed(2))}
                </span>
                {r.status === "running" && r.created_at ? (
                  <span className="text-right text-gold font-mono whitespace-nowrap" title={`提交于 ${new Date(r.created_at).toLocaleString()}`}>
                    {Math.max(0, Math.floor((now - r.created_at) / 1000))}s
                  </span>
                ) : (
                  <span
                    className={`text-right whitespace-nowrap ${cls}`}
                    title={r.created_at != null ? new Date(r.created_at).toLocaleString() : undefined}
                  >
                    {r.created_at != null ? formatTime(r.created_at) : r.status}
                  </span>
                )}
                <div className="flex items-center justify-end gap-1">
                  {confirmDelete === r.task_id ? (
                    <>
                      <button
                        onClick={(e) => { e.stopPropagation(); if (r.task_id) void handleDelete(r.task_id); }}
                        className="h-[24px] rounded px-2 text-[10px] whitespace-nowrap bg-neg/25 text-neg hover:bg-neg/35 disabled:opacity-50"
                        disabled={deletingId === r.task_id}
                        title="确认删除该回测记录(不可撤销)"
                      >
                        {deletingId === r.task_id ? "…" : "确认"}
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); setConfirmDelete(null); }}
                        className={rowActions}
                      >
                        取消
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={(e) => { e.stopPropagation(); if (r.task_id) openBacktestDetail(r.task_id); }}
                        className={`${rowActions} flex items-center gap-0.5`}
                        title="查看回测详情与复盘"
                        disabled={!r.task_id}
                      >
                        <Info className="h-2.5 w-2.5" />
                        复盘
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleRowClick(r); }}
                        className={`${rowActions} ${isDeployable ? "" : "cursor-not-allowed opacity-60"}`}
                        title="部署到模拟盘"
                        disabled={!isDeployable}
                      >
                        模拟
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); setLiveConfirm(r); }}
                        className={`h-[24px] rounded px-2 text-[10px] whitespace-nowrap border ${isDeployable ? "border-border-soft bg-surface-3 text-success hover:bg-surface-4" : "border-border-soft bg-surface-2 text-ink-4 cursor-not-allowed"}`}
                        disabled={!isDeployable}
                      >
                        实盘
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); if (r.task_id) setConfirmDelete(r.task_id); }}
                        className="h-[24px] w-[24px] shrink-0 rounded flex items-center justify-center border border-border-soft bg-surface-3 text-ink-3 hover:bg-neg/15 hover:text-neg"
                        title="删除该回测记录"
                        disabled={!r.task_id}
                        aria-label={`删除 ${r.task_id}`}
                      >
                        <Trash2 className="h-2.5 w-2.5" />
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </V17Card>

      <Dialog
        open={detailOpen}
        onOpenChange={(open) => {
          setDetailOpen(open);
          if (!open) {
            router.replace("/trade/backtests", { scroll: false });
            setHighlightTaskId(null);
          }
        }}
      >
        {/* 详情是三图叠四表的阅读型面板:max-w-4xl 在 11px 根字号下只落 616px
            (56rem×0.6875),图表被压成细带。改用显式 px + vw 兜底,脱离 rem 缩放。 */}
        <DialogContent className="max-w-[min(1280px,94vw)] max-h-[92vh] overflow-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Info className="h-4 w-4 text-sky-400" />
              回测详情 — {detail?.task_id ?? detail?.id ?? "—"}
            </DialogTitle>
            <DialogDescription>
              {detail?.strategy_name ?? "—"}
              {detail?.symbol ? ` | ${detail.symbol}` : ""}
              {detail?.interval ? ` | ${detail.interval}` : ""}
              {detail?.status ? ` | ${detail.status}` : ""}
            </DialogDescription>
          </DialogHeader>
          {detailLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : detail ? (
            <div className="space-y-3">
              {/* 回填重跑: 把本次 run 的完整配置回填进发车表单(标的/周期/区间/资金/费率/滑点) */}
              <div className="flex justify-end">
                <button
                  className="rounded border border-border-soft bg-surface-3 px-2 py-0.5 text-[10px] text-ink-2 hover:bg-sky-500/15 hover:text-sky-400 flex items-center gap-1"
                  onClick={() => prefillFromDetail(detail)}
                  title="把本次回测配置回填进发车表单"
                >
                  <RotateCcw className="h-2.5 w-2.5" />
                  按此配置重跑
                </button>
              </div>
              {/* 可信度行: lookahead_warnings 是引擎真字段,0 条才显示绿。
                  信任锚 = config_snapshot 非空(只在引擎真跑的内存结果上写入;
                  DB 重启重建的结果两字段皆失,不得宣称"无警示") */}
              {(() => {
                const warns: string[] = detail.lookahead_warnings ?? [];
                const snap = detail.config_snapshot;
                const trusted = !!snap && Object.keys(snap).length > 0;
                const clean = warns.length === 0;
                if (!trusted && clean) {
                  return (
                    <div className="flex items-center gap-1.5 text-[10px] text-ink-4">
                      <ShieldAlert className="h-3 w-3" />
                      未来函数警示: 无记录(旧结果)
                    </div>
                  );
                }
                const Shield = clean ? ShieldCheck : ShieldAlert;
                return (
                  <div
                    className={`flex items-center gap-1.5 text-[10px] ${clean ? "text-success" : "text-gold"}`}
                    title={clean ? undefined : warns.join("\n")}
                  >
                    <Shield className="h-3 w-3" />
                    {clean
                      ? "无未来函数警示"
                      : `${warns.length} 条未来函数警示 — 悬停看明细`}
                  </div>
                );
              })()}

              {/* 执行假设条: config_snapshot 为空(老结果)只显示已知项,不造假 */}
              <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-ink-4">
                <span>成交规则: <span className="font-mono text-ink-2">次根开盘撮合</span></span>
                {detail.start_date && detail.end_date && (
                  <span>区间: <span className="font-mono text-ink-2">{detail.start_date.slice(0, 10)} → {detail.end_date.slice(0, 10)}</span></span>
                )}
                {(() => {
                  const snap = detail.config_snapshot;
                  if (!snap || Object.keys(snap).length === 0) return null;
                  const cells: React.ReactNode[] = [];
                  if (snap.initial_capital != null)
                    cells.push(<span key="cap">初始资金: <span className="font-mono text-ink-2">{Number(snap.initial_capital).toLocaleString()}</span></span>);
                  if (snap.commission_rate != null)
                    cells.push(<span key="com">费率: <span className="font-mono text-ink-2">{(Number(snap.commission_rate) * 100).toFixed(2)}%</span></span>);
                  if (snap.slippage_bps != null)
                    cells.push(<span key="slip">滑点: <span className="font-mono text-ink-2">{Number(snap.slippage_bps)}bps</span></span>);
                  return cells;
                })()}
              </div>

              {(() => {
                // 结果完整度:任一指标为有限数即视为"引擎真跑过"。全缺 → 只有任务记录,
                // 指标格全部渲染 "—",并在上方给出原因(不把缺失伪装成 0)。
                const metricVals = [
                  detail.total_return, detail.sharpe_ratio, detail.max_drawdown,
                  detail.win_rate, detail.annual_return, detail.profit_factor,
                  detail.calmar_ratio, detail.total_trades,
                ];
                const hasMetrics = metricVals.some(
                  (v) => typeof v === "number" && Number.isFinite(v),
                );
                const cellClass = "font-mono";
                const dim = !hasMetrics ? "text-ink-4" : "";
                return (
                  <>
                    {!hasMetrics && detail.status === "completed" && (
                      <div className="flex items-start gap-1.5 rounded border border-border-soft bg-surface-2 px-2 py-1.5 text-[10px] text-ink-4 leading-relaxed">
                        <Info className="h-3 w-3 mt-px shrink-0" />
                        该任务已完成,但引擎指标未随记录保存(旧任务/服务重启),仅保留提交信息。按下方「按此配置重跑」可重新生成完整结果。
                      </div>
                    )}
                    <div className="grid grid-cols-4 gap-x-3 gap-y-2">
                      <div>
                        <div className="text-[10px] text-muted-foreground">总收益</div>
                        <div className={`text-base ${cellClass} ${dim} ${hasMetrics ? (Number(detail.total_return) >= 0 ? "text-pos" : "text-neg") : ""}`}>
                          {fmtMetric(detail.total_return, (n) => `${(n * 100).toFixed(2)}%`)}
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] text-muted-foreground">夏普</div>
                        <div className={`text-base ${cellClass} ${dim}`}>{fmtMetric(detail.sharpe_ratio, (n) => n.toFixed(2))}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-muted-foreground">最大回撤</div>
                        <div className={`text-base ${cellClass} ${hasMetrics ? "text-destructive" : "text-ink-4"}`}>
                          {fmtMetric(detail.max_drawdown, (n) => `${(n * 100).toFixed(2)}%`)}
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] text-muted-foreground">胜率</div>
                        <div className={`text-base ${cellClass} ${dim}`}>{fmtMetric(detail.win_rate, (n) => `${(n * 100).toFixed(1)}%`)}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-muted-foreground">年化</div>
                        <div className={`text-base ${cellClass} ${dim}`}>{fmtMetric(detail.annual_return, (n) => `${(n * 100).toFixed(2)}%`)}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-muted-foreground">盈亏比</div>
                        <div className={`text-base ${cellClass} ${dim}`}>{fmtMetric(detail.profit_factor, (n) => n.toFixed(2))}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-muted-foreground">Calmar</div>
                        <div className={`text-base ${cellClass} ${dim}`}>{fmtMetric(detail.calmar_ratio, (n) => n.toFixed(2))}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-muted-foreground">交易数</div>
                        <div className={`text-base ${cellClass} ${dim}`}>{fmtMetric(detail.total_trades, (n) => String(n))}</div>
                      </div>
                    </div>
                  </>
                );
              })()}

              {detail.start_date && detail.end_date && (
                <div className="text-sm text-muted-foreground">
                  区间: {detail.start_date} → {detail.end_date}
                </div>
              )}

              {(detail.equity_curve?.length ?? 0) >= 2 ? (
                <div>
                  <div className="text-sm text-muted-foreground mb-1">权益 / 基准 / 回撤 / 敞口</div>
                  {/* h-72(18rem)在 11px 根字号下只剩 198px,三格图挤成细带。
                      改显式 px:三格(主图/回撤/敞口)各需可读高度,含图例与轴标签。 */}
                  <div ref={chartHostRef} className="h-[420px] w-full" />
                </div>
              ) : (
                <div className="rounded border border-border-soft bg-surface-1 px-3 py-6 text-center text-[11px] text-ink-4">
                  无权益曲线 — 该记录未保存逐 bar 结果
                </div>
              )}

              {detail.trades && detail.trades.length > 0 ? (
                <div>
                  <div className="text-sm text-muted-foreground mb-1">
                    交易记录 ({detail.trades.length}) — <span className="text-ink-4">点行看K线复盘</span>
                  </div>
                  {/* max-h-48(12rem)在 11px 根字号下实高 132px,而 100 行内容 ~241px
                      → 滚区长期半截。改显式 px 容纳约 10 行,列宽同步放大。
                      价格/数量列留足位数(数量经 fmtQty 收敛有效位),tabular-nums 保证数字对齐。 */}
                  <div className="max-h-[300px] overflow-auto border border-border-soft rounded">
                    <div className="grid grid-cols-[96px_96px_52px_104px_104px_104px_92px] gap-x-3 text-[11px] text-ink-4 px-2 py-1.5 border-b border-border-soft font-mono tabular-nums whitespace-nowrap sticky top-0 bg-surface-1">
                      <span>入场时间</span><span>出场时间</span><span>方向</span><span className="text-right">入场价</span><span className="text-right">出场价</span><span className="text-right">数量</span><span className="text-right">PnL</span>
                    </div>
                    {detail.trades.slice(0, 100).map((t, ti) => {
                      const sideCls = (t.side || "").toLowerCase() === "buy" || (t.side || "").toLowerCase() === "long" ? "text-pos" : "text-neg";
                      const pnlNum = Number(t.pnl ?? 0);
                      const pnlCls = pnlNum >= 0 ? "text-pos" : "text-neg";
                      const fmtTime = (ts: number | undefined) =>
                        ts != null
                          ? new Date(ts).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
                          : "—";
                      const active = reviewIndex === ti;
                      return (
                        <div
                          key={ti}
                          className={`grid grid-cols-[96px_96px_52px_104px_104px_104px_92px] gap-x-3 text-[11px] px-2 py-1 border-b border-border-soft font-mono tabular-nums whitespace-nowrap cursor-pointer hover:bg-surface-2 ${active ? "bg-sky-500/10" : ""}`}
                          onClick={() => setReviewIndex(active ? null : ti)}
                        >
                          <span className="text-ink-4">{fmtTime(t.entry_time)}</span>
                          <span className="text-ink-4">{fmtTime(t.exit_time)}</span>
                          <span className={sideCls}>{t.side ?? "—"}</span>
                          <span className="text-right">{Number(t.entry_price ?? 0).toFixed(2)}</span>
                          <span className="text-right">{Number(t.exit_price ?? 0).toFixed(2)}</span>
                          <span className="text-right">{fmtQty(t.quantity as number | undefined)}</span>
                          <span className={`text-right ${pnlCls}`}>{pnlNum >= 0 ? "+" : ""}{pnlNum.toFixed(2)}</span>
                        </div>
                      );
                    })}
                    {detail.trades.length > 100 && (
                      <div className="text-center text-ink-4 text-[10px] py-1">… 还有 {detail.trades.length - 100} 条</div>
                    )}
                  </div>
                  {reviewIndex != null && detail.trades[reviewIndex] && (() => {
                    const snap = detail.config_snapshot;
                    const ex = typeof snap?.exchange === "string" ? snap.exchange : undefined;
                    const mt = typeof snap?.market_type === "string" ? snap.market_type : undefined;
                    const startMs = detail.start_date ? Date.parse(detail.start_date) : null;
                    const endMs = detail.end_date ? Date.parse(detail.end_date) : null;
                    return (
                      <div className="mt-2">
                        <BacktestTradeReview
                          symbol={detail.symbol ?? ""}
                          backtestInterval={detail.interval ?? "1h"}
                          startMs={Number.isFinite(startMs) ? startMs : null}
                          endMs={Number.isFinite(endMs) ? endMs : null}
                          exchange={ex}
                          marketType={mt}
                          trades={detail.trades}
                          index={reviewIndex}
                          onIndexChange={setReviewIndex}
                          snapshotSource={ex != null && mt != null}
                        />
                      </div>
                    );
                  })()}
                </div>
              ) : (
                <div className="rounded border border-border-soft bg-surface-1 px-3 py-4 text-center text-[11px] text-ink-4">
                  无成交记录 — 该记录未保存交易明细
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground text-sm">加载失败</div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={liveConfirm != null} onOpenChange={(open) => { if (!open) setLiveConfirm(null); }}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>部署到实盘</DialogTitle>
            <DialogDescription>
              回测 {liveConfirm?.task_id ?? "—"} — {liveConfirm ? nonEmpty(liveConfirm.strategy_name, liveConfirm.strategy_id, liveConfirm.strategy) : "—"}
              {" | "}
              {liveConfirm ? nonEmpty(liveConfirm.symbol) : "—"}
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-start gap-2 rounded-lg border border-warn/40 bg-warn/5 p-3 text-[11px] leading-relaxed text-muted-foreground">
            将跳转到实盘下单页，使用真实资金。本操作不会自动下单，但实盘页提交的订单将真实成交。
          </div>
          <DialogFooter className="gap-2">
            <button
              className="rounded border border-border-soft bg-surface-3 px-2 py-1 text-[11px] text-ink-2 hover:bg-surface-4"
              onClick={() => setLiveConfirm(null)}
            >
              取消
            </button>
            <button
              className="rounded border border-warn/40 bg-warn/10 px-2 py-1 text-[11px] text-warn hover:bg-warn/20"
              onClick={() => {
                const r = liveConfirm;
                setLiveConfirm(null);
                if (r) handleLiveDeploy(r);
              }}
            >
              确认前往
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
