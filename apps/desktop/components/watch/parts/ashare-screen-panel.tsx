"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useUiText } from "@/components/i18n/use-ui-text";
import { ApiError } from "@/lib/api-client";
import { SessionStamp } from "@/lib/watch/session-stamp";
import {
  fmtScreenCny,
  fmtScreenPct,
  screenAshare,
  type ScreenBoard,
  type ScreenData,
  type ScreenMaWindow,
  type ScreenRequest,
  type ScreenRow,
  type ScreenSortKey,
} from "@/lib/api/ashare-screen";

// A股交互式选股 —— POST /api/v1/ashare/screen。
//
// **单位契约(与大盘总览相同,与异动偏离相反)**:本 payload 的 change_pct
// 是**百分数**(5 = +5%),金额是**元**;异动偏离那边是小数(0.05 = 5%)。
// 本文件只用 fmtScreenPct / fmtScreenCny,不与 deviation 的 pct() 混用。
//
// 三条硬规则,全部是"宁可说没跑,也不装出一个结果":
//   1. **不自动跑**。冷路径是全市场读 + 基线装配(实测 21–29 秒),进页面就跑
//      会拖住其它十几个面板;必须由点击发起。
//   2. **ok + matched 0 与 empty/degraded 分开写**。"没有票满足你的条件"
//      和"截面没取到"是两件事,混成一句会让数据问题读成"今天没机会"。
//   3. **成交额口径逐行标注**。日线回退下成交额是 close×volume 的估算
//      (turnover_est=true),量比按完整收盘日算 —— 与盘中口径不同,必须在
//      页脚说明,而不是让读者自己猜。
//
// 过滤条件在前端用**字符串**保存(空的数值输入 = 不限制),提交时才转换:
// 空串 → 不传,避免把「没填」当成 0 去筛。

type Status = "idle" | "running" | "done" | "error";

interface Filters {
  priceMin: string;
  priceMax: string;
  changeMin: string;
  changeMax: string;
  boardsMin: string;
  boardsMax: string;
  volumeRatioMin: string;
  turnoverMin: string;
  floatMvMin: string;
  floatMvMax: string;
  boardTypes: ScreenBoard[];
  excludeSt: boolean;
  aboveMa: "" | ScreenMaWindow;
  newHigh: boolean;
  newLow: boolean;
  limitUp: "" | "true" | "false";
  sortBy: ScreenSortKey;
  sortDesc: boolean;
  limit: string;
  date: string;
}

const DEFAULT_FILTERS: Filters = {
  priceMin: "",
  priceMax: "",
  changeMin: "",
  changeMax: "",
  boardsMin: "",
  boardsMax: "",
  volumeRatioMin: "",
  turnoverMin: "",
  floatMvMin: "",
  floatMvMax: "",
  boardTypes: [],
  excludeSt: false,
  aboveMa: "",
  newHigh: false,
  newLow: false,
  limitUp: "",
  sortBy: "change_pct",
  sortDesc: true,
  limit: "50",
  date: "",
};

const BOARD_OPTIONS: { key: ScreenBoard; label: string }[] = [
  { key: "main", label: "主板" },
  { key: "kcb", label: "科创板" },
  { key: "cyb", label: "创业板" },
  { key: "bse", label: "北交所" },
];

const SORT_OPTIONS: { key: ScreenSortKey; label: string }[] = [
  { key: "change_pct", label: "涨跌幅" },
  { key: "boards", label: "连板数" },
  { key: "volume_ratio", label: "量比" },
  { key: "turnover", label: "成交额" },
  { key: "float_mv", label: "流通市值" },
  { key: "total_mv", label: "总市值" },
  { key: "last", label: "最新价" },
];

const BOARD_LABEL: Record<ScreenBoard, string> = {
  main: "主板",
  kcb: "科创板",
  cyb: "创业板",
  bse: "北交所",
};

/** 输入框 → 数字;空串/非数字 → undefined(不发这个条件)。 */
function num(text: string): number | undefined {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : undefined;
}

/** 亿元输入 → 元。 */
function yi(text: string): number | undefined {
  const value = num(text);
  return value === undefined ? undefined : value * 1e8;
}

function toRequest(filters: Filters): ScreenRequest {
  const req: ScreenRequest = {
    sort_by: filters.sortBy,
    sort_desc: filters.sortDesc,
    limit: Number(filters.limit) || 50,
  };
  if (filters.date) req.date = filters.date;
  const priceMin = num(filters.priceMin);
  const priceMax = num(filters.priceMax);
  if (priceMin !== undefined) req.price_min = priceMin;
  if (priceMax !== undefined) req.price_max = priceMax;
  const changeMin = num(filters.changeMin);
  const changeMax = num(filters.changeMax);
  if (changeMin !== undefined) req.change_pct_min = changeMin;
  if (changeMax !== undefined) req.change_pct_max = changeMax;
  const boardsMin = num(filters.boardsMin);
  const boardsMax = num(filters.boardsMax);
  if (boardsMin !== undefined) req.boards_min = boardsMin;
  if (boardsMax !== undefined) req.boards_max = boardsMax;
  const volumeRatioMin = num(filters.volumeRatioMin);
  if (volumeRatioMin !== undefined) req.volume_ratio_min = volumeRatioMin;
  const turnoverMin = yi(filters.turnoverMin);
  if (turnoverMin !== undefined) req.turnover_min = turnoverMin;
  const floatMvMin = yi(filters.floatMvMin);
  const floatMvMax = yi(filters.floatMvMax);
  if (floatMvMin !== undefined) req.float_mv_min = floatMvMin;
  if (floatMvMax !== undefined) req.float_mv_max = floatMvMax;
  if (filters.boardTypes.length) req.board_types = filters.boardTypes;
  if (filters.excludeSt) req.exclude_st = true;
  if (filters.aboveMa) req.above_ma = filters.aboveMa;
  if (filters.newHigh) req.new_high = true;
  if (filters.newLow) req.new_low = true;
  if (filters.limitUp === "true") req.limit_up = true;
  if (filters.limitUp === "false") req.limit_up = false;
  return req;
}

const chgClass = (v: number | null | undefined): string => {
  if (v == null || !Number.isFinite(v) || v === 0) return "text-ink-3";
  return v > 0 ? "text-red-400" : "text-emerald-400";
};

export function AshareScreenPanel() {
  const tr = useUiText();
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [status, setStatus] = useState<Status>("idle");
  const [data, setData] = useState<ScreenData | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // 卸载时中止在途请求:冷路径要跑二十多秒,不能让它对着已卸载组件 setState。
  useEffect(() => () => abortRef.current?.abort(), []);

  const patch = useCallback((changes: Partial<Filters>) => {
    setFilters((prev) => ({ ...prev, ...changes }));
  }, []);

  const run = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setStatus("running");
    setErrorMessage(null);
    try {
      const result = await screenAshare(toRequest(filters), controller.signal);
      if (controller.signal.aborted) return;
      setData(result);
      setStatus("done");
    } catch (err) {
      if (controller.signal.aborted) {
        // 主动中止(重跑/卸载)不算失败。
        return;
      }
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : tr("未知错误");
      setErrorMessage(message);
      setStatus("error");
    }
  }, [filters, tr]);

  const busy = status === "running";
  const asOf = data?.as_of ?? null;

  return (
    <section
      data-testid="ashare-screen-panel"
      className="rounded border border-border-soft bg-surface p-2"
    >
      <div className="mb-2 flex items-center justify-between border-b border-border-soft pb-1">
        <span className="flex items-baseline gap-2">
          <span className="text-[10px] font-medium text-ink">{tr("条件选股")}</span>
          <span className="text-[9px] text-ink-4">
            {tr("全市场截面 · 结构化条件")}
          </span>
        </span>
        <span className="flex items-center gap-2 text-[9px] text-ink-4">
          <SessionStamp asOf={asOf} />
          <button
            type="button"
            data-testid="screen-run"
            onClick={run}
            disabled={busy}
            className="rounded bg-surface-3 px-2 py-0.5 text-[9px] text-ink transition-colors hover:text-ink-2 disabled:opacity-50"
          >
            {busy ? tr("筛选中…") : data ? tr("重新筛选") : tr("开始筛选")}
          </button>
        </span>
      </div>

      {/* ── 条件行 ─────────────────────────────────────────────────── */}
      <div className="mb-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[9px] sm:grid-cols-4">
        <RangeField
          label={tr("涨跌幅 %")}
          minValue={filters.changeMin}
          maxValue={filters.changeMax}
          onMin={(v) => patch({ changeMin: v })}
          onMax={(v) => patch({ changeMax: v })}
          testId="screen-change"
        />
        <RangeField
          label={tr("连板数")}
          minValue={filters.boardsMin}
          maxValue={filters.boardsMax}
          onMin={(v) => patch({ boardsMin: v })}
          onMax={(v) => patch({ boardsMax: v })}
          testId="screen-boards"
        />
        <RangeField
          label={tr("最新价 元")}
          minValue={filters.priceMin}
          maxValue={filters.priceMax}
          onMin={(v) => patch({ priceMin: v })}
          onMax={(v) => patch({ priceMax: v })}
          testId="screen-price"
        />
        <SingleField
          label={tr("量比 ≥")}
          value={filters.volumeRatioMin}
          onChange={(v) => patch({ volumeRatioMin: v })}
          testId="screen-volume-ratio"
        />
        <SingleField
          label={tr("成交额 ≥ 亿")}
          value={filters.turnoverMin}
          onChange={(v) => patch({ turnoverMin: v })}
          testId="screen-turnover"
        />
        <RangeField
          label={tr("流通市值 亿")}
          minValue={filters.floatMvMin}
          maxValue={filters.floatMvMax}
          onMin={(v) => patch({ floatMvMin: v })}
          onMax={(v) => patch({ floatMvMax: v })}
          testId="screen-float-mv"
        />
        <label className="flex flex-col gap-0.5">
          <span className="text-ink-4">{tr("均线上方")}</span>
          <select
            data-testid="screen-above-ma"
            value={filters.aboveMa}
            onChange={(e) => patch({ aboveMa: e.target.value as Filters["aboveMa"] })}
            className="rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-[9px] text-ink"
          >
            <option value="">{tr("不限")}</option>
            <option value="ma5">{tr("站上 MA5")}</option>
            <option value="ma20">{tr("站上 MA20")}</option>
            <option value="ma60">{tr("站上 MA60")}</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-ink-4">{tr("涨停")}</span>
          <select
            data-testid="screen-limit-up"
            value={filters.limitUp}
            onChange={(e) => patch({ limitUp: e.target.value as Filters["limitUp"] })}
            className="rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-[9px] text-ink"
          >
            <option value="">{tr("不限")}</option>
            <option value="true">{tr("仅涨停")}</option>
            <option value="false">{tr("仅未涨停")}</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-ink-4">{tr("交易日")}</span>
          <input
            type="date"
            data-testid="screen-date"
            value={filters.date}
            onChange={(e) => patch({ date: e.target.value })}
            className="rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-[9px] text-ink"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-ink-4">{tr("排序")}</span>
          <span className="flex gap-1">
            <select
              data-testid="screen-sort-by"
              value={filters.sortBy}
              onChange={(e) => patch({ sortBy: e.target.value as ScreenSortKey })}
              className="min-w-0 flex-1 rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-[9px] text-ink"
            >
              {SORT_OPTIONS.map((opt) => (
                <option key={opt.key} value={opt.key}>
                  {tr(opt.label)}
                </option>
              ))}
            </select>
            <button
              type="button"
              data-testid="screen-sort-dir"
              onClick={() => patch({ sortDesc: !filters.sortDesc })}
              className="rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-[9px] text-ink-3"
            >
              {filters.sortDesc ? tr("降序") : tr("升序")}
            </button>
          </span>
        </label>
      </div>

      {/* 板块多选 + 开关 */}
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px]">
        <span className="text-ink-4">{tr("市场板块")}</span>
        {BOARD_OPTIONS.map((opt) => {
          const active = filters.boardTypes.includes(opt.key);
          return (
            <button
              key={opt.key}
              type="button"
              aria-pressed={active}
              data-testid={`screen-board-${opt.key}`}
              onClick={() =>
                patch({
                  boardTypes: active
                    ? filters.boardTypes.filter((k) => k !== opt.key)
                    : [...filters.boardTypes, opt.key],
                })
              }
              className={`rounded px-1.5 py-0.5 transition-colors ${
                active ? "bg-surface-3 text-ink" : "text-ink-4 hover:text-ink-2"
              }`}
            >
              {tr(opt.label)}
            </button>
          );
        })}
        <label className="ml-2 flex items-center gap-1 text-ink-3">
          <input
            type="checkbox"
            data-testid="screen-exclude-st"
            checked={filters.excludeSt}
            onChange={(e) => patch({ excludeSt: e.target.checked })}
            className="h-3 w-3"
          />
          {tr("排除 ST")}
        </label>
        <label className="flex items-center gap-1 text-ink-3">
          <input
            type="checkbox"
            data-testid="screen-new-high"
            checked={filters.newHigh}
            onChange={(e) => patch({ newHigh: e.target.checked })}
            className="h-3 w-3"
          />
          {tr("60 日新高")}
        </label>
        <label className="flex items-center gap-1 text-ink-3">
          <input
            type="checkbox"
            data-testid="screen-new-low"
            checked={filters.newLow}
            onChange={(e) => patch({ newLow: e.target.checked })}
            className="h-3 w-3"
          />
          {tr("60 日新低")}
        </label>
        <label className="flex items-center gap-1 text-ink-3">
          {tr("返回")}
          <select
            data-testid="screen-limit"
            value={filters.limit}
            onChange={(e) => patch({ limit: e.target.value })}
            className="rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-[9px] text-ink"
          >
            {["20", "50", "100", "200"].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          {tr("条")}
        </label>
      </div>

      {/* ── 结果区 ─────────────────────────────────────────────────── */}
      {status === "idle" ? (
        <p data-testid="screen-idle" className="py-2 text-[11px] text-ink-4">
          {tr("设置条件后点击「开始筛选」。首次筛选要装配全市场截面(约 20–30 秒),之后每次约 0.1 秒。")}
        </p>
      ) : status === "error" ? (
        <p data-testid="screen-error" className="py-2 text-[11px] text-red-400">
          {errorMessage}
        </p>
      ) : !data ? (
        <p className="py-2 text-[11px] text-ink-4">{tr("筛选中…")}</p>
      ) : (
        <ScreenResult data={data} />
      )}

      <p className="mt-1 text-[9px] leading-snug text-ink-4">
        {tr(
          "口径:涨跌幅为百分数;连板数按收盘封板计。量比 = 今日成交量 ÷ 前 5 日均量,盘中读数天然偏低。日线回退下成交额是 收盘价×成交量 的估算(逐行标记)。缺昨收价的票不参与涨跌幅条件。",
        )}
      </p>
    </section>
  );
}

function ScreenResult({ data }: { data: ScreenData }) {
  const tr = useUiText();
  const coverage = data.coverage;

  if (data.status === "empty" || data.status === "degraded") {
    return (
      <p data-testid="screen-degraded" className="py-2 text-[11px] leading-relaxed text-ink-3">
        {data.status === "empty"
          ? tr("截面上没有任何行情行(不是「没有票满足条件」)—— 数据未取到,可用交易日选另一个会话。")
          : tr("截面覆盖不足({p0}),结果不可信;原因为 {p1}。", {
              p0: coverage ? `${coverage.quoted}/${coverage.universe}` : "—",
              p1: data.reason ?? "—",
            })}
      </p>
    );
  }

  return (
    <>
      <div
        data-testid="screen-summary"
        className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[9px] text-ink-4"
      >
        <span className="text-ink-2">
          {tr("命中 {p0} 只 · 截面 {p1}", {
            p0: data.matched,
            p1: data.scanned,
          })}
        </span>
        {coverage ? (
          <span>
            {tr("覆盖 {p0}/{p1}", { p0: coverage.quoted, p1: coverage.universe })}
          </span>
        ) : null}
        <span>
          {data.quote_source === "snapshot"
            ? tr("实时快照")
            : data.quote_source === "daily"
              ? tr("日线回退")
              : tr("无行情")}
        </span>
        {data.truncated ? (
          <span className="text-gold">
            {tr("仅显示前 {p0} 条", { p0: data.rows.length })}
          </span>
        ) : null}
      </div>

      {data.matched === 0 ? (
        <p data-testid="screen-no-match" className="py-2 text-[11px] text-ink-3">
          {tr("截面正常({p0} 只),但没有票满足当前条件。", { p0: data.scanned })}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] font-mono">
            <thead>
              <tr className="text-left text-ink-4">
                <th className="py-1 pr-3">{tr("代码")}</th>
                <th className="py-1 pr-3">{tr("名称")}</th>
                <th className="py-1 pr-3 text-right">{tr("最新价")}</th>
                <th className="py-1 pr-3 text-right">{tr("涨跌幅")}</th>
                <th className="py-1 pr-3 text-right">{tr("连板")}</th>
                <th className="py-1 pr-3 text-right">{tr("量比")}</th>
                <th className="py-1 pr-3 text-right">{tr("成交额")}</th>
                <th className="py-1 pr-3 text-right">{tr("流通市值")}</th>
                <th className="py-1">{tr("状态")}</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <ScreenTableRow key={row.symbol} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data.notes.length > 0 ? (
        <ul data-testid="screen-notes" className="mt-1 space-y-0.5 text-[9px] text-ink-4">
          {data.notes.map((note) => (
            <li key={note}>{tr(note)}</li>
          ))}
        </ul>
      ) : null}

      <p data-testid="screen-basis" className="mt-0.5 text-[9px] leading-snug text-ink-4">
        {tr("基线截止 {p0}({p1} 个交易日)", {
          p0: coverage?.baseline_as_of ?? "—",
          p1: coverage?.baseline_sessions ?? 0,
        })}
        {coverage?.amount_available
          ? tr(";成交额为快照真实值")
          : tr(";成交额为 收盘价×成交量 估算值")}
        {coverage?.market_cap_available === false
          ? tr(";市值表不可用,市值条件会排掉全部行")
          : ""}
      </p>
    </>
  );
}

function ScreenTableRow({ row }: { row: ScreenRow }) {
  const tr = useUiText();
  return (
    <tr className="border-t border-border-soft text-ink">
      <td className="py-1 pr-3">{row.symbol}</td>
      <td className="py-1 pr-3">{row.name}</td>
      <td className={`py-1 pr-3 text-right ${chgClass(row.change_pct)}`}>
        {row.last == null ? "—" : row.last.toFixed(2)}
      </td>
      <td className={`py-1 pr-3 text-right ${chgClass(row.change_pct)}`}>
        {fmtScreenPct(row.change_pct)}
      </td>
      <td className="py-1 pr-3 text-right">
        {row.boards > 0 ? (
          <span className="rounded bg-red-400/15 px-1 text-red-400">{tr("{p0} 板", { p0: row.boards })}</span>
        ) : (
          <span className="text-ink-4">—</span>
        )}
      </td>
      <td className="py-1 pr-3 text-right text-ink-3">
        {row.volume_ratio == null ? "—" : row.volume_ratio.toFixed(2)}
      </td>
      <td className="py-1 pr-3 text-right text-ink-3">
        {fmtScreenCny(row.turnover)}
        {row.turnover_est ? (
          <span
            data-testid="screen-turnover-est"
            title={tr("估算值:收盘价 × 成交量")}
            className="ml-0.5 text-[8px] text-ink-4"
          >
            {tr("估")}
          </span>
        ) : null}
      </td>
      <td className="py-1 pr-3 text-right text-ink-3">{fmtScreenCny(row.float_mv)}</td>
      <td className="py-1">
        {row.limit_up ? (
          <span className="rounded bg-red-400/15 px-1 text-[9px] text-red-400">{tr("涨停")}</span>
        ) : row.broken ? (
          <span className="rounded bg-gold/15 px-1 text-[9px] text-gold">{tr("炸板")}</span>
        ) : row.limit_down ? (
          <span className="rounded bg-emerald-400/15 px-1 text-[9px] text-emerald-400">
            {tr("跌停")}
          </span>
        ) : null}
      </td>
    </tr>
  );
}

function RangeField({
  label,
  minValue,
  maxValue,
  onMin,
  onMax,
  testId,
}: {
  label: string;
  minValue: string;
  maxValue: string;
  onMin: (v: string) => void;
  onMax: (v: string) => void;
  testId: string;
}) {
  const tr = useUiText();
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-ink-4">{label}</span>
      <span className="flex items-center gap-1">
        <input
          inputMode="decimal"
          data-testid={`${testId}-min`}
          placeholder={tr("起")}
          value={minValue}
          onChange={(e) => onMin(e.target.value)}
          className="min-w-0 flex-1 rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-[9px] text-ink"
        />
        <span className="text-ink-4">–</span>
        <input
          inputMode="decimal"
          data-testid={`${testId}-max`}
          placeholder={tr("止")}
          value={maxValue}
          onChange={(e) => onMax(e.target.value)}
          className="min-w-0 flex-1 rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-[9px] text-ink"
        />
      </span>
    </label>
  );
}

function SingleField({
  label,
  value,
  onChange,
  testId,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  testId: string;
}) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-ink-4">{label}</span>
      <input
        inputMode="decimal"
        data-testid={testId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-border-soft bg-surface-2 px-1 py-0.5 text-[9px] text-ink"
      />
    </label>
  );
}
