"use client";

import { useUiText } from "@/components/i18n/use-ui-text";
import type { ApiError } from "@/lib/api-client";
import {
  fmtCny,
  fmtRatioPct,
  type OverviewData,
} from "@/lib/api/ashare-overview";
import { AshareFreshness } from "./ashare-freshness";
import { SessionStamp } from "@/lib/watch/session-stamp";

// A股大盘总览带 —— 整页唯一一块「全市场」层,其余十块都是涨停/板块口径。
//
// 为什么是**带**而不是第 11 张卡:
//   1. 它回答的是"今天是什么日子"(指数/广度/情绪),其余块回答"谁在动";
//      同层级的卡片会把这两类问题摆成并列,读者要先读标题才知道自己在看哪层。
//   2. 页面已有 10 张同构 V17Card,再来一张即「相同卡片网格」反模式。
//
// 刻意**不渲染**涨停/跌停家数:本 payload 的 limit_up 是扣掉假封后的修正值,
// 而下方「涨停概览」卡来自 limit-up-pool 的另一路数据。同一页两个「今日涨停」
// 数字会互相矛盾 —— 宁可少一格,不给读者两个真值。

const fmtSigned = (v: number | null | undefined, digits = 2): string => {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
};

// A 股惯例:红涨绿跌。与页面其余部分同源(不用 pos/neg token —— 那两个随
// locale 翻转,本页是 A 股专用,口径必须固定)。
const chgClass = (v: number | null | undefined): string => {
  if (v == null || !Number.isFinite(v) || v === 0) return "text-ink-3";
  return v > 0 ? "text-red-400" : "text-emerald-400";
};

/** 六轴情绪雷达(指数/赚钱/量能/投机/抗跌/主线),0-100。 */
function EmotionRadar({ radar }: { radar: OverviewData["radar"] }) {
  const tr = useUiText();
  const W = 148;
  const H = 104;
  const cx = W / 2;
  const cy = H / 2 + 2;
  const r = 34;
  const n = radar.length;
  const at = (i: number, radius: number) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
    return [cx + radius * Math.cos(a), cy + radius * Math.sin(a)] as const;
  };
  const polygon = (radius: number) =>
    radar.map((_, i) => at(i, radius).join(",")).join(" ");
  const shape = radar
    .map((item, i) => at(i, (Math.max(0, Math.min(100, item.value)) / 100) * r).join(","))
    .join(" ");

  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={tr("情绪雷达:{p0}", {
        p0: radar.map((r2) => `${tr(r2.label)} ${r2.value}`).join(" · "),
      })}
      className="shrink-0"
    >
      {[1, 0.66, 0.33].map((f) => (
        <polygon
          key={f}
          points={polygon(r * f)}
          fill="none"
          stroke="var(--color-border)"
          strokeWidth={0.75}
        />
      ))}
      {radar.map((item, i) => {
        const [x, y] = at(i, r);
        return (
          <line
            key={item.key}
            x1={cx}
            y1={cy}
            x2={x}
            y2={y}
            stroke="var(--color-border)"
            strokeWidth={0.75}
          />
        );
      })}
      <polygon
        points={shape}
        fill="var(--color-gold)"
        fillOpacity={0.14}
        stroke="var(--color-gold)"
        strokeWidth={1}
        strokeLinejoin="round"
      />
      {radar.map((item, i) => {
        const [x, y] = at(i, r + 13);
        const anchor = Math.abs(x - cx) < 8 ? "middle" : x > cx ? "start" : "end";
        return (
          <text
            key={item.key}
            x={x}
            y={y + 3}
            textAnchor={anchor}
            className="fill-ink-4"
            style={{ fontSize: 9 }}
          >
            {`${tr(item.label)} ${item.value}`}
          </text>
        );
      })}
    </svg>
  );
}

function BreadthBar({ data }: { data: OverviewData }) {
  const tr = useUiText();
  const { up, flat, down, total } = data.breadth;
  const pct = (n: number) => (total > 0 ? (n / total) * 100 : 0);
  return (
    <div>
      <div
        className="flex h-[6px] w-full overflow-hidden rounded-sm bg-surface-3"
        role="img"
        aria-label={tr("涨跌家数:涨 {up} · 平 {flat} · 跌 {down}", { up, flat, down })}
      >
        <span className="h-full bg-red-400/75" style={{ width: `${pct(up)}%` }} />
        <span className="h-full bg-ink-5" style={{ width: `${pct(flat)}%` }} />
        <span className="h-full bg-emerald-400/75" style={{ width: `${pct(down)}%` }} />
      </div>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[10px]">
        <span className="text-ink-3">
          {tr("涨")} <span className="num text-red-400">{up}</span>
        </span>
        <span className="text-ink-3">
          {tr("平")} <span className="num text-ink-3">{flat}</span>
        </span>
        <span className="text-ink-3">
          {tr("跌")} <span className="num text-emerald-400">{down}</span>
        </span>
        <span className="text-ink-4">
          {tr("强(≥3%)")} <span className={`num ${data.breadth.strong_up > 0 ? "text-red-400" : "text-ink-3"}`}>{data.breadth.strong_up}</span>
        </span>
        <span className="text-ink-4">
          {tr("弱(≤-3%)")} <span className={`num ${data.breadth.strong_down > 0 ? "text-emerald-400" : "text-ink-3"}`}>{data.breadth.strong_down}</span>
        </span>
        <span className="text-ink-4">
          {tr("均")} <span className={`num ${chgClass(data.breadth.avg_pct)}`}>{fmtSigned(data.breadth.avg_pct)}</span>
        </span>
        <span className="text-ink-4">
          {tr("中位")} <span className={`num ${chgClass(data.breadth.median_pct)}`}>{fmtSigned(data.breadth.median_pct)}</span>
        </span>
      </div>
    </div>
  );
}

export function AshareOverviewBand({
  data,
  error,
  isLoading,
  today = null,
}: {
  data: OverviewData | null;
  error?: ApiError;
  isLoading: boolean;
  /** 今天(`YYYYMMDD`,取自 `/session.as_of`);null = 未知,不判过期。 */
  today?: string | null;
}) {
  const tr = useUiText();

  if (error) {
    return (
      <section
        data-testid="ashare-overview-band"
        className="rounded border border-border-soft bg-surface px-3 py-2"
      >
        <BandHead right={null} />
        <p className="py-2 text-[11px] text-ink-4">
          {tr("大盘总览不可用:{p0}", { p0: error.message })}
        </p>
      </section>
    );
  }

  if (!data) {
    return (
      <section
        data-testid="ashare-overview-band"
        className="rounded border border-border-soft bg-surface px-3 py-2"
      >
        <BandHead right={null} />
        <p className="py-2 text-[11px] text-ink-4">
          {isLoading ? tr("装配全市场截面…(首次约 15–25 秒)") : tr("暂无大盘总览数据")}
        </p>
      </section>
    );
  }

  const degraded = data.status === "degraded";
  const coveragePct = data.coverage?.ratio;

  // 降级必须占一行正文,不进 tooltip —— 读者要先知道"这页数可信到什么程度"。
  const notes: string[] = [];
  if (data.quote_source === "daily") {
    notes.push(tr("行情来自日线回退(实时快照不可用),涨跌按最近收盘口径"));
  }
  if (degraded && data.reason === "partial_coverage") {
    notes.push(
      tr("截面覆盖不足:{p0} 只有 {p1}/{p2} 只取到报价,广度与情绪读数偏保守", {
        p0: fmtRatioPct(coveragePct),
        p1: data.coverage?.quoted ?? 0,
        p2: data.coverage?.universe ?? 0,
      }),
    );
  } else if (data.status === "empty") {
    notes.push(tr("全市场截面为空:指数与情绪不出读数,不代表「中性」"));
  }
  if (data.indices_reason) {
    notes.push(tr("指数行情缺失:{p0}", { p0: data.indices_reason }));
  }
  if (data.snapshot_reason && data.quote_source !== "daily") {
    notes.push(tr("快照说明:{p0}", { p0: data.snapshot_reason }));
  }

  return (
    <section
      data-testid="ashare-overview-band"
      className="rounded border border-border-soft bg-surface px-3 py-2"
    >
      <BandHead
        right={
          <span className="flex items-center gap-2">
            <SessionStamp asOf={data.as_of} />
            {/* as_of 在快照缺失时退回日线库的最后已收盘会话 —— 那时页面展示的是
                昨日截面,不是实时快照。标注让"日线回退"这枚 chip 说得清楚日期。 */}
            <AshareFreshness sessionDate={data.as_of} today={today} />
            <span className="rounded bg-surface-3 px-1 py-0.5 text-[9px] text-ink-3">
              {data.quote_source === "snapshot"
                ? tr("实时快照")
                : data.quote_source === "daily"
                  ? tr("日线回退")
                  : tr("无行情")}
            </span>
          </span>
        }
      />

      {data.indices.length > 0 ? (
        <div className="flex flex-wrap items-stretch divide-x divide-border-soft">
          {data.indices.map((idx) => (
            <div key={idx.symbol} className="min-w-[104px] flex-1 px-2 first:pl-0">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[9px] text-ink-3">{tr(idx.name)}</span>
                <span className={`num text-[10px] ${chgClass(idx.change_pct)}`}>
                  {fmtSigned(idx.change_pct)}
                </span>
              </div>
              <div className={`num text-[13px] leading-tight ${chgClass(idx.change_pct)}`}>
                {idx.last_price == null ? "—" : idx.last_price.toFixed(2)}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="py-1 text-[10px] text-ink-4">{tr("暂无指数行情")}</p>
      )}

      <div className="mt-2 grid gap-3 border-t border-border-soft pt-2 lg:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0">
          <BreadthBar data={data} />

          <div className="mt-1.5 flex flex-wrap items-baseline gap-x-4 gap-y-0.5 text-[10px] text-ink-4">
            <span>
              {tr("成交额")}{" "}
              <span className="num text-ink-2">
                {data.amount.available ? fmtCny(data.amount.total) : "—"}
              </span>
            </span>
            <span>
              {tr("站上 MA5")}{" "}
              <span className="num text-ink-2">{fmtSigned(data.trend.above_ma5_pct, 0)}</span>
            </span>
            <span>
              {tr("MA20")}{" "}
              <span className="num text-ink-2">{fmtSigned(data.trend.above_ma20_pct, 0)}</span>
            </span>
            <span>
              {tr("MA60")}{" "}
              <span className="num text-ink-2">{fmtSigned(data.trend.above_ma60_pct, 0)}</span>
            </span>
            <span>
              {tr("60 日新高/低")}{" "}
              <span className="num text-red-400">{data.trend.new_high}</span>
              <span className="text-ink-4">/</span>
              <span className="num text-emerald-400">{data.trend.new_low}</span>
            </span>
            <span>
              {tr("均量比")}{" "}
              <span className="num text-ink-2">
                {data.activity.avg_vol_ratio == null
                  ? "—"
                  : data.activity.avg_vol_ratio.toFixed(2)}
              </span>
            </span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="text-right">
            <div className="text-[9px] text-ink-3">{tr("情绪")}</div>
            {/* score === null 是「截面为空」,不是 0 分。零轴平均会造出一个假温度。 */}
            <div
              data-testid="overview-emotion-score"
              className={`num text-[22px] leading-none ${
                data.emotion.score == null ? "text-ink-4" : "text-gold"
              }`}
            >
              {data.emotion.score ?? "—"}
            </div>
            <div className="mt-0.5 text-[9px] text-ink-4">{tr(data.emotion.label)}</div>
          </div>
          {data.radar.length > 0 ? (
            <EmotionRadar radar={data.radar} />
          ) : (
            <p className="w-[120px] text-[9px] leading-snug text-ink-4">
              {tr("截面为空,不出情绪读数")}
            </p>
          )}
        </div>
      </div>

      {notes.length > 0 ? (
        <div data-testid="overview-notes" className="mt-1.5 space-y-0.5">
          {notes.map((note) => (
            <p key={note} className="text-[9px] leading-snug text-ink-4">
              {note}
            </p>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function BandHead({ right }: { right: React.ReactNode }) {
  const tr = useUiText();
  return (
    <div className="flex items-center justify-between border-b border-border-soft pb-1 mb-2">
      <span className="flex items-baseline gap-2">
        <span className="text-[10px] font-medium text-ink">{tr("大盘总览")}</span>
        <span className="text-[9px] text-ink-4">
          {tr("指数 · 广度 · 情绪")}
        </span>
      </span>
      {right ? <span className="text-[9px] text-ink-4">{right}</span> : null}
    </div>
  );
}
