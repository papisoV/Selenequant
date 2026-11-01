"use client";

import { useUiText } from "@/components/i18n/use-ui-text";
import type { ApiError } from "@/lib/api-client";
import type {
  DeviationData,
  DeviationRow,
  DeviationStatus,
} from "@/lib/api/ashare-deviation";
import { AshareFreshness } from "./ashare-freshness";
import { SessionStamp } from "@/lib/watch/session-stamp";

// A股异动偏离 —— GET /api/v1/ashare/deviation。
//
// **单位契约(与大盘总览相反,别混)**:本 payload 的 value / threshold /
// closeness 全是**小数**(0.20 = 20%);大盘总览那边是百分数。两个模块的
// 格式化函数刻意分开,不共用一个 fmtPct。
//
// 口径(packages/ashare/selene_space_ashare/abnormal.py):
//   偏离值 = 个股 N 日累计涨跌 − 对应基准指数同期涨跌;
//   接近度 = |偏离值| ÷ 该方向阈值;≥1.0 已触发 / ≥0.7 边缘 / 其余观察。
//   3 日阈值按板块(主板 ±20% / 创业科创 ±30% / 北交所 ±40%),
//   10 日 +100%/−50%、30 日 +200%/−70%(负向更严)。
//   阈值为交易所披露标准的近似,仅供风险提示,不构成监管认定 —— 这句必须在页面上。
//
// 数据诚实条款:
//   · status === "empty" + reason === "no_ashare_favorites" → 展示服务端 hint,
//     不显示空表(空表会被读成"今天没有异动")。
//   · reason === "kline_manager_unavailable" → 服务端说是服务缺件,不是无风险。
//   · basis: "spot" | "astock" + basis_date → 历史来自哪个分区、截止哪天。
//   · intraday: true 表示今日实时涨跌已叠加;false 表示纯日线口径。逐行标注。

const STATUS_LABEL: Record<DeviationStatus, string> = {
  triggered: "已触发",
  edge: "边缘",
  watch: "观察",
};
// 已触发 = 真的越线,红;边缘 = 差一步,金(本页身份强调);观察 = 正文灰。
const STATUS_CLS: Record<DeviationStatus, string> = {
  triggered: "bg-red-400/15 text-red-400",
  edge: "bg-gold/15 text-gold",
  watch: "bg-surface-3 text-ink-3",
};

const BOARD_LABEL: Record<string, string> = {
  main: "主板",
  kcb: "科创板",
  cyb: "创业板",
  bse: "北交所",
};

const WINDOW_KEYS = ["3d", "10d", "30d"] as const;

/** 小数 → 百分数。用于阈值与接近度。 */
const pct = (v: number | null | undefined, digits = 0): string => {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
};

/** 偏离值带符号(小数 → 百分数)。 */
const signedPct = (v: number | null | undefined, digits = 2): string => {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;
};

const chgClass = (v: number | null | undefined): string => {
  if (v == null || !Number.isFinite(v) || v === 0) return "text-ink-3";
  return v > 0 ? "text-red-400" : "text-emerald-400";
};

/** 三档里接近度最高的一档 —— 行状态就取它,该档要加重显示。 */
function dominantWindow(row: DeviationRow): string | null {
  let best: string | null = null;
  let bestVal = -Infinity;
  for (const key of WINDOW_KEYS) {
    const info = row.windows[key];
    if (info && info.closeness > bestVal) {
      best = key;
      bestVal = info.closeness;
    }
  }
  return best;
}

function EmptyState({ data }: { data: DeviationData }) {
  const tr = useUiText();
  if (data.reason === "no_ashare_favorites") {
    // 这是教学型空态:hint 直接告诉用户"往自选里加 A股代码"。
    return (
      <p data-testid="deviation-empty" className="py-2 text-[11px] leading-relaxed text-ink-3">
        {tr(data.hint ?? "自选里加入 A股代码后此表才有内容")}
      </p>
    );
  }
  if (data.reason === "kline_manager_unavailable") {
    return (
      <p data-testid="deviation-empty" className="py-2 text-[11px] text-ink-4">
        {tr("日线库不可用,无法计算偏离(不是「无异动」)")}
      </p>
    );
  }
  return (
    <p data-testid="deviation-empty" className="py-2 text-[11px] text-ink-4">
      {tr("暂无异动偏离数据")}
    </p>
  );
}

export function AshareDeviationPanel({
  data,
  error,
  isLoading,
  today = null,
}: {
  data: DeviationData | null;
  error?: ApiError;
  isLoading: boolean;
  /** 今天(`YYYYMMDD`,取自 `/session.as_of`);null = 未知,不判过期。 */
  today?: string | null;
}) {
  const tr = useUiText();

  const title = tr("异动偏离(自选 A股)");

  return (
    <section
      data-testid="ashare-deviation-panel"
      className="rounded border border-border-soft bg-surface p-2"
    >
      <div className="mb-2 flex items-center justify-between border-b border-border-soft pb-1">
        <span className="flex items-baseline gap-2">
          <span className="text-[10px] font-medium text-ink">{title}</span>
          <span className="text-[9px] text-ink-4">
            {tr("3/10/30 日累计偏离 vs 对应指数")}
          </span>
        </span>
        <span className="flex items-center gap-2 text-[9px] text-ink-4">
          {data && data.status !== "empty" && data.status !== "degraded" ? (
            <>
              <CountChip label={tr("已触发")} count={data.counts.triggered} status="triggered" />
              <CountChip label={tr("边缘")} count={data.counts.edge} status="edge" />
              <CountChip label={tr("观察")} count={data.counts.watch} status="watch" />
            </>
          ) : null}
          <SessionStamp asOf={data?.as_of} />
          <AshareFreshness sessionDate={data?.as_of} today={today} />
        </span>
      </div>

      {error ? (
        <p className="py-2 text-[11px] text-ink-4">{tr("异动偏离不可用:{p0}", { p0: error.message })}</p>
      ) : !data ? (
        <p className="py-2 text-[11px] text-ink-4">
          {isLoading ? tr("计算偏离中…") : tr("暂无数据")}
        </p>
      ) : data.status === "empty" || data.status === "degraded" || data.rows.length === 0 ? (
        <EmptyState data={data} />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="text-left text-ink-4">
                  <th className="py-1 pr-3">{tr("代码")}</th>
                  <th className="py-1 pr-3">{tr("所属板块")}</th>
                  <th className="py-1 pr-3 text-right">{tr("接近度")}</th>
                  {WINDOW_KEYS.map((k) => (
                    <th key={k} className="py-1 pr-3 text-right">
                      {tr("{p0} 日偏离", { p0: k.replace("d", "") })}
                    </th>
                  ))}
                  <th className="py-1 pr-3 text-right">{tr("基准")}</th>
                  <th className="py-1">{tr("状态")}</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => {
                  const dominant = dominantWindow(row);
                  return (
                    <tr key={row.symbol} className="border-t border-border-soft text-ink">
                      <td className="py-1 pr-3">
                        <span>{row.symbol}</span>
                        {row.st ? (
                          <span className="ml-1 rounded bg-red-400/10 px-1 text-[9px] text-red-400">
                            ST
                          </span>
                        ) : null}
                        {row.intraday ? (
                          <span
                            data-testid="deviation-intraday-badge"
                            title={tr("已叠加今日实时涨跌")}
                            className="ml-1 rounded bg-surface-3 px-1 text-[9px] text-ink-3"
                          >
                            {tr("盘中")}
                          </span>
                        ) : null}
                      </td>
                      <td className="py-1 pr-3 text-ink-3">
                        {tr(BOARD_LABEL[row.board] ?? row.board)}
                      </td>
                      <td className="py-1 pr-3 text-right">
                        <span className={STATUS_CLS[row.status].split(" ")[1]}>
                          {pct(row.max_closeness)}
                        </span>
                      </td>
                      {WINDOW_KEYS.map((k) => {
                        const info = row.windows[k];
                        const isDominant = dominant === k;
                        if (!info) {
                          return (
                            <td key={k} className="py-1 pr-3 text-right text-ink-4">
                              —
                            </td>
                          );
                        }
                        return (
                          <td
                            key={k}
                            title={tr("阈值 {p0} · 接近度 {p1}", {
                              p0: pct(info.threshold),
                              p1: pct(info.closeness),
                            })}
                            className={`py-1 pr-3 text-right ${
                              isDominant ? chgClass(info.value) : "text-ink-4"
                            }`}
                          >
                            {signedPct(info.value)}
                          </td>
                        );
                      })}
                      <td className="py-1 pr-3 text-right text-[9px] text-ink-4">{row.bench}</td>
                      <td className="py-1">
                        <span
                          data-testid={`deviation-status-${row.status}`}
                          className={`rounded px-1 text-[9px] ${STATUS_CLS[row.status]}`}
                        >
                          {tr(STATUS_LABEL[row.status])}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p className="mt-1 text-[9px] leading-snug text-ink-4">
            {tr(
              "口径:偏离值 = 个股 N 日累计涨跌 − 对应指数同期涨跌;接近度 = |偏离值| ÷ 该方向阈值。阈值为交易所异常波动披露标准的近似值,仅供风险提示,不构成监管认定。",
            )}
          </p>
          <p data-testid="deviation-basis-note" className="mt-0.5 text-[9px] leading-snug text-ink-4">
            {tr("历史来源 {p0}(截止 {p1})", {
              p0: tr(row0Basis(data)),
              p1: data.rows[0]?.basis_date ?? "—",
            })}
            {data.live
              ? tr(";盘中实时涨跌已叠加(逐行「盘中」标记)")
              : tr(";纯日线口径,今日实时涨跌未叠加")}
          </p>
          {data.skipped.length > 0 ? (
            <p className="mt-0.5 text-[9px] text-ink-4">
              {tr("跳过 {p0} 只:{p1}", {
                p0: data.skipped.length,
                p1: data.skipped
                  .slice(0, 3)
                  .map((s) => `${s.symbol}(${s.reason})`)
                  .join("、"),
              })}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

function row0Basis(data: DeviationData): string {
  const b = data.rows[0]?.basis;
  if (b === "spot") return "实时分区 spot";
  if (b === "astock") return "导入分区 astock";
  return String(b ?? "—");
}

function CountChip({
  label,
  count,
  status,
}: {
  label: string;
  count: number;
  status: DeviationStatus;
}) {
  return (
    <span
      data-testid={`deviation-count-${status}`}
      className={`rounded px-1 py-0.5 ${STATUS_CLS[status]}`}
    >
      {label} <span className="num">{count}</span>
    </span>
  );
}
