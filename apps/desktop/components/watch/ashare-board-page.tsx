"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// A股看盘台 — 一屏聚合 7 块(涨停概览 / 连板梯队 / 情绪周期 / 涨停池 / 板块排名 / 龙虎榜 / 北向)
// 数据源分两类契约:
//   · 看盘面 /api/v1/ashare/{limit-up-pool,ladder,phase,boards} —— fail-open:
//     单块空/失败只置 status=empty/degraded,不顶掉整页。
//   · 深度面 /api/v1/ashare/{lhb,northbound} —— ashare-deep 的 404/502 契约:
//     端点明确无数据抛 404 → useApi 得 undefined → 该块显示占位,不连累其他块。
// 情绪周期/连板梯队有**两档**,由响应里的 source 区分:
//   · live_snapshot —— 盘中:今日横截面接在近 ~20 日已收盘历史之后,最后一行是
//     **盘中暂定**(label_scope=intraday_provisional),标"盘中暂定 · 日期";
//   · backfill_panel —— 回填产物(ladder_panel.parquet),phase.as_of 是面板最后一日
//     (非自然今日),标"截至 X 日"。缺 label_scope 时按收盘口径渲染。
// 两档不可混同:盘中标签会变,读成收盘定版就是错的。
// 龙虎榜按自然今日查(收盘后才有当日榜,盘中/非交易日 → 占位)。
// 涨跌色按 A 股惯例:红涨绿跌(与加密页的 pnlClass 相反)。

type Row = Record<string, unknown>;

const todayStr = () => new Date().toISOString().slice(0, 10);

const fmt = (v: unknown): string => {
  if (v == null || v === "") return "—";
  if (typeof v === "number") return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return String(v);
};

// A 股涨跌色:正=红(up),负=绿(down)。此处不复用 pnlClass(加密惯例,方向相反)。
const chgClass = (v: unknown): string => {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n) || n === 0) return "text-ink-3";
  return n > 0 ? "text-red-400" : "text-emerald-400";
};

// 情绪周期六阶段 → 中文 + 强调色(热度:高潮/主升 红,退潮/冰点 绿,启动/修复 金)
const PHASE_CN: Record<string, string> = {
  ice: "冰点",
  ignite: "启动",
  rally: "主升",
  climax: "高潮",
  ebb: "退潮",
  repair: "修复",
};
const PHASE_CLS: Record<string, string> = {
  climax: "text-red-400",
  rally: "text-red-400",
  ignite: "text-gold",
  repair: "text-gold",
  ebb: "text-emerald-400",
  ice: "text-emerald-400",
};

function DataTable({ rows, max = 10, empty, chgKeys = [], tail = false }: {
  rows: Row[]; max?: number; empty: string; chgKeys?: string[]; tail?: boolean;
}) {
  if (!rows.length) return <p className="py-2 text-center text-[11px] text-ink-4">{empty}</p>;
  const keys = Object.keys(rows[0]).slice(0, 6);
  // tail: 时间升序序列(如北向,2014→今)取末尾 max 条 = 最近;否则取头部(按 rank/热度降序的榜)。
  const view = tail ? rows.slice(-max) : rows.slice(0, max);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] font-mono">
        <thead>
          <tr className="text-ink-4 text-left">
            {keys.map((k) => <th key={k} className="py-1 pr-3">{k}</th>)}
          </tr>
        </thead>
        <tbody>
          {view.map((r, i) => (
            <tr key={i} className="border-t border-border-soft text-ink">
              {keys.map((k) => (
                <td key={k} className={`py-1 pr-3 ${chgKeys.includes(k) ? chgClass(r[k]) : ""}`}>
                  {fmt(r[k])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// 情绪周期状态条 — 顶部醒目展示当前阶段 + 驱动指标
function PhaseBar({ phase }: { phase: PhaseData | null }) {
  if (!phase || phase.status === "degraded") {
    return (
      <V17Card title="情绪周期(连板梯队六阶段)">
        <p className="py-2 text-center text-[11px] text-ink-4">
          {phase?.status === "degraded"
            ? `暂不可用:${phase.reason ?? "面板缺失"}(需先跑 backfill_ladder_panel.py)`
            : "暂无情绪周期数据"}
        </p>
      </V17Card>
    );
  }
  const cur = phase.current;
  const label = cur ? (PHASE_CN[cur.phase as string] ?? String(cur.phase)) : "—";
  const cls = cur ? (PHASE_CLS[cur.phase as string] ?? "text-ink") : "text-ink-3";
  // 实时档最后一行是**盘中暂定**(快照截面还没收盘),必须与收盘定版区分开 ——
  // 否则 as_of=今日 会被读成"今天已定型"。回填面板才是收盘口径(标"截至 X 日")。
  const provisional = phase.label_scope === "intraday_provisional";
  const asOfText = provisional
    ? phase.as_of
      ? `盘中暂定 · ${phase.as_of}`
      : "盘中暂定"
    : phase.as_of
      ? `截至 ${phase.as_of}`
      : undefined;
  return (
    <V17Card
      title="情绪周期(连板梯队六阶段)"
      right={asOfText}
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className={`text-[22px] font-semibold ${cls}`}>{label}</span>
        <span className="text-[10px] text-ink-4">
          弱市未校验:大盘极弱时梯队可能仍标"主升"(B1 veto 未移植)
        </span>
      </div>
      {cur && (
        <div className="mt-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          <StatBlock label="最高连板" value={fmt(cur.max_consecutive)} />
          <StatBlock label="二板以上" value={fmt(cur.ge2_count)} />
          <StatBlock label="晋级率" value={fmt(cur.promo_rate)} />
          <StatBlock label="封板率" value={fmt(cur.seal_rate)} />
        </div>
      )}
    </V17Card>
  );
}

interface PoolData {
  status: string; date: string | null; pool: Row[]; pool_count: number;
  stats: Record<string, unknown>;
}
interface LadderData {
  status: string; date: string | null; today: Row | null; history: Row[]; history_count: number;
}
interface PhaseData {
  status: string; as_of: string | null; reason?: string;
  // 实时档:"live_snapshot" + label_scope="intraday_provisional"(最后一行未收盘);
  // 回填档:"backfill_panel"(as_of 是面板最后一日)。label_scope 缺省按收盘口径渲染。
  source?: string; label_scope?: string; live_reason?: string | null;
  current: Row | null; series: Row[];
}
interface BoardsData { status: string; kind: string; boards: Row[]; count: number }

export function AshareBoardPage() {
  const [boardKind, setBoardKind] = useState<"industry" | "concept">("concept");
  const [lhbDate, setLhbDate] = useState(todayStr());

  const pool = useApi<PoolData>("/api/v1/ashare/limit-up-pool");
  const ladder = useApi<LadderData>("/api/v1/ashare/ladder");
  const phase = useApi<PhaseData>("/api/v1/ashare/phase");
  const boards = useApi<BoardsData>(`/api/v1/ashare/boards?kind=${boardKind}`);
  // 深度面两块 —— 复用 ashare_deep 端点(404/502 契约,非 fail-open)。
  // date 为必填 query:裸调会 422,故显式传当日 YYYYMMDD。
  const lhb = useApi<Row[] | null>(`/api/v1/ashare/lhb?date=${lhbDate.replace(/-/g, "")}`);
  const northbound = useApi<Row[] | null>("/api/v1/ashare/northbound");

  const stats = pool.data?.stats ?? {};
  const today = ladder.data?.today;
  // 北向逐日数据 2024-08 起交易所停更,新日期行全空 —— 过滤掉空净买额,只留真实披露过的行,
  // 避免"最近 10 条"全是 "—"(akshare 返回的尾部空行)。
  const northboundRows = (northbound.data ?? []).filter((r) => {
    const v = r["当日成交净买额"];
    return v != null && v !== "" && Number.isFinite(Number(v));
  });
  const northboundLast = northboundRows.at(-1)?.date;

  return (
    <div className="v17-page-grid grid gap-2">
      <PhaseBar phase={phase.data ?? null} />

      <V17Card title="涨停概览" right={pool.data?.date ?? undefined}>
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          <StatBlock label="今日涨停" value={fmt(stats.limit_up_today)} cls="text-red-400" />
          <StatBlock label="封板率" value={fmt(stats.seal_rate)} />
          <StatBlock label="炸板数" value={fmt(stats.open_count)} />
          <StatBlock label="今日跌停" value={fmt(stats.limit_down_today)} cls="text-emerald-400" />
        </div>
      </V17Card>

      <V17Card title="连板梯队(今日)" right={ladder.data?.date ?? undefined}>
        {today ? (
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            <StatBlock label="最高连板" value={fmt(today.max_consecutive)} />
            <StatBlock label="首板家数" value={fmt(today.first_board)} />
            <StatBlock label="二板以上" value={fmt(today.ge2_count)} />
            <StatBlock label="三板以上" value={fmt(today.ge3_count)} />
          </div>
        ) : (
          <p className="py-2 text-center text-[11px] text-ink-4">暂无连板数据(非交易日或数据未发布)</p>
        )}
      </V17Card>

      <V17Card title="涨停池" right={`${pool.data?.pool_count ?? 0} 只`}>
        <DataTable rows={pool.data?.pool ?? []} max={15}
          empty="暂无涨停池数据(可能未装 akshare/10jqka 无此日)" chgKeys={["change_rate"]} />
      </V17Card>

      <V17Card
        title="板块排名"
        right={
          <span className="flex gap-1">
            {(["concept", "industry"] as const).map((k) => (
              <button key={k}
                aria-pressed={boardKind === k}
                onClick={() => setBoardKind(k)}
                className={`rounded px-1.5 py-0.5 text-[9px] transition-colors ${
                  boardKind === k ? "bg-surface-3 text-ink" : "text-ink-4 hover:text-ink-2"
                }`}>
                {k === "concept" ? "概念" : "行业"}
              </button>
            ))}
          </span>
        }
      >
        <DataTable rows={boards.data?.boards ?? []} max={15}
          empty={`暂无${boardKind === "concept" ? "概念" : "行业"}板块数据`} chgKeys={["change_pct"]} />
      </V17Card>

      <V17Card
        title="龙虎榜"
        right={
          <input
            type="date"
            aria-label="龙虎榜交易日"
            className="bg-surface-2 border border-border-soft rounded px-1 py-0.5 text-[9px] text-ink"
            value={lhbDate}
            onChange={(e) => e.target.value && setLhbDate(e.target.value)}
          />
        }
      >
        <DataTable rows={lhb.data ?? []} max={10}
          empty="暂无该日龙虎榜(非交易日/收盘前未发布/未装 akshare)" chgKeys={["change_rate"]} />
      </V17Card>

      <V17Card title="北向资金 (每日)" right={northboundLast ? `最近 ${northboundLast}` : undefined}>
        <DataTable rows={northboundRows} max={10} tail
          empty="暂无北向资金数据(未装 akshare 或数据源无此日)" chgKeys={["当日成交净买额"]} />
      </V17Card>
      <p className="px-2 text-[9px] text-ink-4">
        注:北向资金自 2024-08 起交易所停止逐日披露净买额,故仅显示最后披露日;空行已过滤。
      </p>
    </div>
  );
}
