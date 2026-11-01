"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { useApiPage } from "@/lib/hooks/use-api-page";
import {
  useAshareSession,
  useRevalidateAshareOnOpen,
} from "@/lib/hooks/use-ashare-session";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import { useUiText } from "@/components/i18n/use-ui-text"
import { AshareOverviewBand } from "./parts/ashare-overview-band";
import { AshareRecapPanel } from "./parts/ashare-recap-panel";
import { AshareScreenPanel } from "./parts/ashare-screen-panel";
import { AshareDeviationPanel } from "./parts/ashare-deviation-panel";
import { ConceptMembersTrigger } from "./parts/concept-members-dialog";
import {
  AshareFreshness,
  CardRight,
  todayFromSessionAsOf,
} from "./parts/ashare-freshness";
import { SessionStamp } from "@/lib/watch/session-stamp";
import type { OverviewData } from "@/lib/api/ashare-overview";
import type { DeviationData } from "@/lib/api/ashare-deviation";

// A股看盘台 — 一屏聚合 10 块(涨停概览 / 连板梯队 / 情绪周期 / 涨停池 / 板块排名 / 概念轮动 / 概念RPS / 主线识别 / 龙虎榜 / 北向)
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
  const tr = useUiText()
  if (!rows.length) return <p className="py-2 text-center text-[11px] text-ink-4">{empty}</p>;
  const keys = Object.keys(rows[0]).slice(0, 6);
  // tail: 时间升序序列(如北向,2014→今)取末尾 max 条 = 最近;否则取头部(按 rank/热度降序的榜)。
  const view = tail ? rows.slice(-max) : rows.slice(0, max);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] font-mono">
        <thead>
          <tr className="text-ink-4 text-left">
            {keys.map((k) => <th key={k} className="py-1 pr-3">{tr(k)}</th>)}
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

// 概念轮动矩阵 — 行为概念,列为最近 N 个交易日,单元格是当日等权涨幅。
// 概念集合取最近一日的前 topN(矩阵的读法是"最近谁在领先,领先了几天"),
// 不是整个概念空间 —— 1199 个概念 × 12 天在 11px 字号下无法阅读。
function RotationMatrix({ data }: { data: RotationData | null }) {
  const tr = useUiText()
  if (!data || data.status === "degraded") {
    return (
      <V17Card title={tr("概念轮动(近 N 日等权涨幅)")}>
        <p className="py-2 text-center text-[11px] text-ink-4">
          {data?.status === "degraded"
            ? tr("暂不可用:{p0}", { p0: data.reason ?? tr(STATUS_HINT.degraded) })
            : tr("暂无概念轮动数据")}
        </p>
      </V17Card>
    );
  }
  const dates = [...data.dates].reverse();          // 表头左→右 = 旧→新
  const latest = data.dates[0];
  const rows = (data.columns[latest] ?? []).slice(0, 20);
  if (!rows.length) {
    return (
      <V17Card title={tr("概念轮动(近 N 日等权涨幅)")}>
        <p className="py-2 text-center text-[11px] text-ink-4">
          {tr("暂无概念轮动数据{p0}", { p0: data.reason ? `(${data.reason})` : "" })}
        </p>
      </V17Card>
    );
  }
  return (
    <V17Card
      title={tr("概念轮动(近 N 日等权涨幅)")}
      right={data.as_of ? <SessionStamp asOf={data.as_of} /> : undefined}
    >
      <div className="overflow-x-auto">
        <table className="w-full text-[11px] font-mono">
          <thead>
            <tr className="text-ink-4 text-left">
              <th className="py-1 pr-3">{tr("概念")}</th>
              {dates.map((d) => <th key={d} className="py-1 pr-2 text-right">{d.slice(5)}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map(([concept]) => (
              <tr key={concept} className="border-t border-border-soft text-ink">
                <td className="py-1 pr-3">{concept}</td>
                {dates.map((d) => {
                  const hit = (data.columns[d] ?? []).find((r) => r[0] === concept);
                  return (
                    <td key={d} className={`py-1 pr-2 text-right ${chgClass(hit?.[1])}`}>
                      {hit ? fmt(hit[1]) : "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1 text-[9px] text-ink-4">
        {tr("{days} 个交易日 · {count} 个概念有成分覆盖。{note}", {
          days: data.days, count: data.concept_count, note: data.membership_note,
        })}
      </p>
    </V17Card>
  );
}

// 主线识别 — 当日按涨停梯队强度排名的概念,分数 0-100 为横截面 rank 归一化结果。
// 分数本身只在同一天内可比,跨日比较无意义(每天重新排名),所以只展示当日一列。
function MainlineBoard({ data }: { data: MainlineData | null }) {
  const tr = useUiText()
  if (!data || data.status === "degraded") {
    return (
      <V17Card title={tr("主线识别(涨停梯队强度)")}>
        <p className="py-2 text-center text-[11px] text-ink-4">
          {data?.status === "degraded"
            ? tr("暂不可用:{p0}", { p0: data.reason ?? tr(STATUS_HINT.degraded) })
            : tr("暂无主线数据")}
        </p>
      </V17Card>
    );
  }
  const latest = data.dates[0];
  const rows = latest ? (data.columns[latest] ?? []) : [];
  return (
    <V17Card
      title={tr("主线识别(涨停梯队强度)")}
      right={data.as_of ? <SessionStamp asOf={data.as_of} /> : undefined}
    >
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] font-mono">
            <thead>
              <tr className="text-ink-4 text-left">
                <th className="py-1 pr-3">{tr("概念")}</th>
                <th className="py-1 pr-3 text-right">{tr("强度")}</th>
                <th className="py-1 pr-3 text-right">{tr("涨停")}</th>
                <th className="py-1 pr-3 text-right">{tr("最高板")}</th>
                <th className="py-1 pr-3">{tr("领涨")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 10).map(([concept, score, ups, boards, leader]) => (
                <tr key={concept} className="border-t border-border-soft text-ink">
                  <td className="py-1 pr-3">{concept}</td>
                  <td className="py-1 pr-3 text-right">{fmt(score)}</td>
                  <td className="py-1 pr-3 text-right text-red-400">{fmt(ups)}</td>
                  <td className="py-1 pr-3 text-right">{fmt(boards)}</td>
                  <td className="py-1 pr-3">{leader}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="py-2 text-center text-[11px] text-ink-4">
          {tr("暂无主线数据{p0}", { p0: data.reason ? `(${data.reason})` : "" })}
        </p>
      )}
      <p className="mt-1 text-[9px] text-ink-4">
        {tr("连板数仅统计窗口内(窗口外历史段不可见)。{note}", { note: data.membership_note })}
      </p>
    </V17Card>
  );
}

// 概念 RPS — 近 N 个交易日等权成员涨幅的截面百分位。与「概念轮动」同源不同口径:
// 轮动是逐日矩阵(看节奏),RPS 是单一截面(看排名)。
// stability 三档来自实测的成分漂移敏感度(每概念去掉 1 只成员,排名平均移动多少):
//   solid(>=21 只)≈1.3 个百分位 / fair(8-20 只)≈3-5 / thin(3-7 只)≈7-10。
// **is_strong 只读服务端字段,前端不得用 rps>=90 复算** —— 那条规则还有
// "成员数落在 solid 档"这半个条件,漏掉就会给一个 4 只成员的概念打高亮。
// drift_note 必须作为正文渲染,不能塞进 tooltip:它解释了为什么榜首常常是 thin。
// thin 必须**灰显**而不是强调:它是最不可信的一档,给金色会让读者把"排名不稳"
// 误读成"特别值得看"。金色在这个页面上是 is_strong 的专用强调色。
const STABILITY_LABEL: Record<string, string> = {
  solid: "稳定",
  fair: "一般",
  thin: "不稳",
};
const STABILITY_CLS: Record<string, string> = {
  solid: "text-ink",
  fair: "text-ink-3",
  thin: "text-ink-4",
};

function ConceptRpsBoard({
  data,
  sessionToday,
}: {
  data: ConceptRpsData | null;
  /** 今天(北京时间 YYYYMMDD)，透传给成分下钻弹窗做快照新鲜度基准。 */
  sessionToday: string | null;
}) {
  const tr = useUiText()
  const title = tr("概念 RPS(近 N 日等权涨幅百分位)");
  if (!data || data.status === "degraded") {
    return (
      <V17Card title={title}>
        <p className="py-2 text-center text-[11px] text-ink-4">
          {data?.status === "degraded"
            ? tr("暂不可用:{p0}", { p0: data.reason ?? tr(STATUS_HINT.degraded) })
            : tr("暂无概念 RPS 数据")}
        </p>
      </V17Card>
    );
  }
  const rows = data.concepts.slice(0, 20);
  return (
    <V17Card title={title} right={data.as_of ? <SessionStamp asOf={data.as_of} /> : undefined}>
      {rows.length ? (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="text-ink-4 text-left">
                  <th className="py-1 pr-3">{tr("概念")}</th>
                  <th className="py-1 pr-3 text-right">RPS</th>
                  <th className="py-1 pr-3 text-right">{tr("区间涨幅")}</th>
                  <th className="py-1 pr-3 text-right">{tr("成员")}</th>
                  <th className="py-1 pr-3">{tr("稳定性")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.concept}
                    data-strong={r.is_strong ? "true" : "false"}
                    className={`border-t border-border-soft ${
                      r.is_strong ? "font-semibold text-gold" : "text-ink"
                    }`}
                  >
                    <td className="py-1 pr-3">
                      <ConceptMembersTrigger concept={r.concept} sessionToday={sessionToday}>
                        {r.concept}
                      </ConceptMembersTrigger>
                      {r.is_strong ? <span className="ml-1 text-[9px]">{tr("强")}</span> : null}
                    </td>
                    <td className="py-1 pr-3 text-right">{fmt(r.rps)}</td>
                    <td className={`py-1 pr-3 text-right ${chgClass(r.ret)}`}>{fmt(r.ret)}</td>
                    <td className="py-1 pr-3 text-right">{fmt(r.member_count)}</td>
                    <td className={`py-1 pr-3 ${STABILITY_CLS[r.stability] ?? "text-ink-3"}`}>
                      {r.stability === "thin" ? (
                        <span
                          data-testid="stability-thin-badge"
                          className="rounded bg-ink-4/10 px-1 text-[9px]"
                        >
                          {tr("不稳定")}
                        </span>
                      ) : (
                        tr(STABILITY_LABEL[r.stability] ?? r.stability)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="py-2 text-center text-[11px] text-ink-4">
          {tr("暂无概念 RPS 数据{p0}", { p0: data.reason ? `(${data.reason})` : "" })}
        </p>
      )}
      <p className="mt-1 text-[9px] text-ink-4" data-testid="concept-rps-footnote">
        {tr("{lookback} 个交易日 · {count} 个概念入选。{note}", {
          lookback: data.lookback, count: data.concept_count, note: data.drift_note,
        })}
      </p>
    </V17Card>
  );
}

// 情绪周期状态条 — 顶部醒目展示当前阶段 + 驱动指标
function PhaseBar({ phase, today }: { phase: PhaseData | null; today: string | null }) {
  const tr = useUiText()

  if (!phase || phase.status === "degraded") {
    return (
      <V17Card title={tr("情绪周期(连板梯队六阶段)")}>
        <p className="py-2 text-center text-[11px] text-ink-4">
          {phase?.status === "degraded"
            ? tr("暂不可用:{p0}(需先跑 backfill_ladder_panel.py)", { p0: phase.reason ?? tr("面板缺失") })
            : tr("暂无情绪周期数据")}
        </p>
      </V17Card>
    );
  }
  const cur = phase.current;
  const label = cur ? tr(PHASE_CN[cur.phase as string] ?? String(cur.phase)) : "—";
  const cls = cur ? (PHASE_CLS[cur.phase as string] ?? "text-ink") : "text-ink-3";
  // 实时档最后一行是**盘中暂定**(快照截面还没收盘),必须与收盘定版区分开 ——
  // 否则 as_of=今日 会被读成"今天已定型"。回填面板才是收盘口径(标"截至 X 日")。
  const provisional = phase.label_scope === "intraday_provisional";
  return (
    <V17Card
      title={tr("情绪周期(连板梯队六阶段)")}
      right={
        <span className="flex items-center gap-1">
          <SessionStamp
            asOf={phase.as_of}
            scope={provisional ? "provisional" : "settled"}
          />
          <AshareFreshness sessionDate={phase.as_of} today={today} />
        </span>
      }
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className={`text-[22px] font-semibold ${cls}`}>{label}</span>
        <span className="text-[10px] text-ink-4">
          {tr("弱市未校验:大盘极弱时梯队可能仍标\"主升\"(B1 veto 未移植)")}
        </span>
      </div>
      {cur && (
        <div className="mt-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          <StatBlock label={tr("最高连板")} value={fmt(cur.max_consecutive)} />
          <StatBlock label={tr("二板以上")} value={fmt(cur.ge2_count)} />
          <StatBlock label={tr("晋级率")} value={fmt(cur.promo_rate)} />
          <StatBlock label={tr("封板率")} value={fmt(cur.seal_rate)} />
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
  // 真封/假封 拆分,来自全市场快照自带的五档买一/卖一量(免费,无需付费 L2)。
  // 涨停真封 ⇔ 卖一无挂单;sell1_vol==0。null = 盘口不可用或处于集合竞价窗口,
  // 此时 sealed_reason 说明原因,UI 显示"未知"而不是 0(0 会被读成"全炸板")。
  sealed?: SealedCounts | null; sealed_reason?: string | null;
}
interface SealedCounts {
  real_up: number; fake_up: number; real_down: number; fake_down: number;
  undecided: number; ready: boolean;
}
interface PhaseData {
  status: string; as_of: string | null; reason?: string;
  // 实时档:"live_snapshot" + label_scope="intraday_provisional"(最后一行未收盘);
  // 回填档:"backfill_panel"(as_of 是面板最后一日)。label_scope 缺省按收盘口径渲染。
  source?: string; label_scope?: string; live_reason?: string | null;
  current: Row | null; series: Row[];
}
interface BoardsData { status: string; kind: string; boards: Row[]; count: number }

// 概念轮动: dates 新→旧;columns[date] = [[概念, 当日等权涨幅%], ...] 已降序。
// 与「板块排名」不是同一口径 —— 那个是东财板块指数当日涨跌,这个是按同花顺
// 成分股逐日等权复算的个股口径,可回看多日。
interface RotationData {
  status: string; reason: string | null; days: number;
  membership_note: string; dates: string[];
  columns: Record<string, [string, number][]>;
  concept_count: number; as_of: string | null;
}
// 主线: columns[date] = [[概念, 分数, 涨停家数, 最高连板, 领涨股], ...]。
// streak_capped 恒为 true —— 连板数只看得到窗口内,页面上必须照实说明,
// 否则"3 连板"会被读成真实历史连板数。
interface MainlineData {
  status: string; reason: string | null; days: number; streak_capped: boolean;
  membership_note: string; dates: string[];
  columns: Record<string, [string, number, number, number, string][]>;
  as_of: string | null;
}

const STATUS_HINT: Record<string, string> = {
  degraded: "上游不可用",
  empty: "暂无数据",
};

// 概念 RPS:concepts 已按 rps 降序;is_strong 由服务端算好,前端原样渲染。
interface ConceptRpsRow {
  concept: string; rps: number; ret: number;
  member_count: number; stability: "solid" | "fair" | "thin"; is_strong: boolean;
}
interface ConceptRpsData {
  status: string; reason: string | null; lookback: number;
  concepts: ConceptRpsRow[]; concept_count: number; as_of: string | null;
  membership_note: string; drift_note: string;
}

/**
 * 轮询档位(ms)。只在交易时段生效,收盘/非交易日一律停(见 useAshareSession)。
 *
 * 分档依据是**实测端点成本**与**盘中是否真的会变**,不是凭感觉:
 *   · live —— 实时档:涨停池/连板/情绪周期/板块排名/大盘总览/偏离。这些
 *     端点响应里的"今日"行盘中会变(``/ladder`` 甚至 ``force=True`` 绕过
 *     缓存,不吃陈旧帧),不轮询就等于打开后冻结在首帧。
 *   · slow —— 慢档:主线/概念RPS/轮动要读全市场日线重算(实测 5–15s),
 *     且它们统计的是**已收盘的会话**,盘中再刷也只是重算同一个答案。
 *     仍给 60s 是为了捕捉 15:00 收盘后新会话落库的那一次翻转。
 *   · 0 —— 日频/低频:龙虎榜(收盘后才发布)、北向(2024-08 已停更)。
 *     轮询它们纯粹是空转。
 *
 * 归类的判据是**这个数盘中会不会变**,不是端点多快 —— 大盘总览实测比三块慢
 * 档还贵(热态 3–4s,冷启动首个请求曾达 99s),但它读的是实时快照截面,仍归 live。
 *
 * 比 TSP 的 SectorRotationCard(盘中 30s / 盘外 300s)更保守:那两档是
 * 单个轻端点,这里是九个端点同时在线,聚合成本高一个量级。
 */
const POLL_LIVE_MS = 30_000;
const POLL_SLOW_MS = 60_000;

/**
 * 会话开关 → 两档轮询间隔。收盘/非交易时段一律 0（停轮询）。
 *
 * 抽成纯函数是因为"闸门"本身要能被单独钉住：页面里有九个端点各自传
 * `refreshInterval`，任何一个传错都是**静默**的 —— 页面照样显示数据，
 * 只是收盘后还在空转、或盘中冻在首帧，肉眼看不出来。
 */
export function pollIntervals(sessionOpen: boolean): { live: number; slow: number } {
  return sessionOpen
    ? { live: POLL_LIVE_MS, slow: POLL_SLOW_MS }
    : { live: 0, slow: 0 };
}

export function AshareBoardPage() {
  const tr = useUiText()

  const [boardKind, setBoardKind] = useState<"industry" | "concept">("concept");
  const [lhbDate, setLhbDate] = useState(todayStr());

  // 时段判定由**后端**给(见 useAshareSession):前端自己算要复刻交易日历和
  // 东八区换算,节假日表一过时就会收盘后空转、或整个交易日死寂。
  // sessionOpen 只在这一批端点上下文中读取 —— 它是轮询的闸门,不是展示值。
  const { sessionOpen, data: session } = useAshareSession();
  const { live, slow } = pollIntervals(sessionOpen);
  // 卡片上的"今天"只能来自服务端的 /session.as_of —— 浏览器时区与机器时钟
  // 都可能偏,拿它们判"数据是不是今天的"会把正确的数据标成过期。
  const sessionToday = todayFromSessionAsOf(session?.as_of);
  // 新代码走 useApiPage(distinguish):区分"服务挂"与"真无数据"。存量九块
  // 仍用 deprecated useApi(吞错为 null),不顺手改 —— 那会改变既有 9 块的
  // 空态语义,与本任务无关。
  const { useApi: usePageApi } = useApiPage({ distinguish: true });
  // 大盘总览是**实时档**:它的 as_of 取自快照行的时间戳,广度/情绪/涨停读数
  // 盘中一直在变。放到慢档会让总览带比下面的涨停概览落后一分钟以上,
  // 同一屏上两个"今日"读数互相矛盾。代价是它比别的实时档贵(实测热态
  // 3–4s,首次请求在股票列表缓存过期时要等上游重试,曾实测 99s),但 SWR
  // 会合并并发请求,30s 一刷不会堆积。
  const overview = usePageApi<OverviewData>("/api/v1/ashare/overview", {
    refreshInterval: live,
  });
  const deviation = usePageApi<DeviationData>("/api/v1/ashare/deviation?live=true", {
    refreshInterval: live,
  });

  const pool = useApi<PoolData>("/api/v1/ashare/limit-up-pool", {
    refreshInterval: live,
  });
  const ladder = useApi<LadderData>("/api/v1/ashare/ladder", { refreshInterval: live });
  const phase = useApi<PhaseData>("/api/v1/ashare/phase", { refreshInterval: live });
  const boards = useApi<BoardsData>(`/api/v1/ashare/boards?kind=${boardKind}`, {
    refreshInterval: live,
  });
  const rotation = useApi<RotationData>("/api/v1/ashare/rotation?days=12", {
    refreshInterval: slow,
  });
  const mainline = useApi<MainlineData>("/api/v1/ashare/mainline?days=20", {
    refreshInterval: slow,
  });
  const conceptRps = useApi<ConceptRpsData>("/api/v1/ashare/concept-rps?lookback=120", {
    refreshInterval: slow,
  });
  // 深度面两块 —— 复用 ashare_deep 端点(404/502 契约,非 fail-open)。
  // date 为必填 query:裸调会 422,故显式传当日 YYYYMMDD。
  // 日频:龙虎榜收盘后才发布,北向 2024-08 起停更 —— 都不轮询(refreshInterval
  // 缺省即 0)。
  const lhb = useApi<Row[] | null>(`/api/v1/ashare/lhb?date=${lhbDate.replace(/-/g, "")}`);
  const northbound = useApi<Row[] | null>("/api/v1/ashare/northbound");

  // 打开/切回本页时立刻重取一轮 —— 见 useRevalidateAshareOnOpen 的注释。
  // 只放实时档的六块:慢档与日频块重取的答案与缓存里的一样,不值一次冷计算。
  useRevalidateAshareOnOpen(sessionOpen, [overview, deviation, pool, ladder, phase, boards]);

  const stats = pool.data?.stats ?? {};
  const today = ladder.data?.today;
  // 盘口拆分:ready=false(全市场都没盘口)或整块 null 时显示"—",不渲染 0 ——
  // 0 与"全炸板"在页面上不可区分。undecided 单列,表示处涨停但盘口不可判定。
  const sealedCounts = ladder.data?.sealed ?? null;
  const sealedKnown = sealedCounts?.ready === true;
  const sealedValue = (key: "real_up" | "fake_up") =>
    sealedKnown ? fmt(sealedCounts![key]) : "—";
  // 北向逐日数据 2024-08 起交易所停更,新日期行全空 —— 过滤掉空净买额,只留真实披露过的行,
  // 避免"最近 10 条"全是 "—"(akshare 返回的尾部空行)。
  const northboundRows = (northbound.data ?? []).filter((r) => {
    const v = r["当日成交净买额"];
    return v != null && v !== "" && Number.isFinite(Number(v));
  });
  const northboundLast = northboundRows.at(-1)?.date;

  return (
    <div className="v17-page-grid grid gap-2">
      {/* 大盘总览带 = 全市场层(指数/广度/情绪),其余十块是涨停/板块口径。
          它不进卡片网格:两类问题并列会让读者先读标题才知道自己在看哪层。 */}
      <AshareOverviewBand
        data={overview.data}
        error={overview.error}
        isLoading={overview.isLoading}
        today={sessionToday}
      />
      {/* AI 复盘放在总览之下、涨停概览之上:先给"今天是什么日子",再给叙述。 */}
      <AshareRecapPanel asOf={overview.data?.as_of ?? null} />

      {/* 条件选股 = 全市场层的"查到具体票",紧跟总览与复盘:这三块读的是
          同一个截面(共用 build_quote_rows 与同一条基线),数字必须逐位一致。
          它自己发 POST(点「开始筛选」才跑),不占 SWR 请求。 */}
      <AshareScreenPanel />

      <PhaseBar phase={phase.data ?? null} today={sessionToday} />

      <V17Card
        title={tr("涨停概览")}
        right={
          <CardRight date={pool.data?.date}>
            <AshareFreshness sessionDate={pool.data?.date} today={sessionToday} />
          </CardRight>
        }
      >
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          <StatBlock label={tr("今日涨停")} value={fmt(stats.limit_up_today)} cls="text-red-400" />
          <StatBlock label={tr("封板率")} value={fmt(stats.seal_rate)} />
          <StatBlock label={tr("炸板数")} value={fmt(stats.open_count)} />
          <StatBlock label={tr("今日跌停")} value={fmt(stats.limit_down_today)} cls="text-emerald-400" />
        </div>
      </V17Card>

      <V17Card
        title={tr("连板梯队(今日)")}
        right={
          <CardRight date={ladder.data?.date}>
            <AshareFreshness sessionDate={ladder.data?.date} today={sessionToday} />
          </CardRight>
        }
      >
        {today ? (
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            <StatBlock label={tr("最高连板")} value={fmt(today.max_consecutive)} />
            <StatBlock label={tr("首板家数")} value={fmt(today.first_board)} />
            <StatBlock label={tr("二板以上")} value={fmt(today.ge2_count)} />
            <StatBlock label={tr("三板以上")} value={fmt(today.ge3_count)} />
          </div>
        ) : (
          <p className="py-2 text-center text-[11px] text-ink-4">{tr("暂无连板数据(非交易日或数据未发布)")}</p>
        )}
        {/* 真封/假封 拆分 —— 五档免费带出,「涨停」一列不再把假封板算成封住。
            ready=false 时整排显示 "—":此时是全市场盘口都取不到,不是"0 只真封"。 */}
        <div className="mt-1.5 grid grid-cols-2 gap-1.5 border-t border-line pt-1.5 sm:grid-cols-4">
          <StatBlock label={tr("真封(涨)")} value={sealedValue("real_up")} cls="text-red-400" />
          <StatBlock label={tr("假封(涨)")} value={sealedValue("fake_up")} />
          <StatBlock label={tr("真封(跌)")} value={sealedKnown ? fmt(sealedCounts!.real_down) : "—"}
            cls="text-emerald-400" />
          <StatBlock label={tr("假封(跌)")} value={sealedKnown ? fmt(sealedCounts!.fake_down) : "—"} />
          <StatBlock label={tr("盘口待定")}
            value={sealedKnown ? fmt(sealedCounts!.undecided) : "—"} />
        </div>
      </V17Card>

      <V17Card title={tr("涨停池")} right={tr("{p0} 只", { p0: pool.data?.pool_count ?? 0 })}>
        <DataTable rows={pool.data?.pool ?? []} max={15}
          empty={tr("暂无涨停池数据(可能未装 akshare/10jqka 无此日)")} chgKeys={["change_rate"]} />
      </V17Card>

      <V17Card
        title={tr("板块排名")}
        right={
          <span className="flex gap-1">
            {(["concept", "industry"] as const).map((k) => (
              <button key={k}
                aria-pressed={boardKind === k}
                onClick={() => setBoardKind(k)}
                className={`rounded px-1.5 py-0.5 text-[9px] transition-colors ${
                  boardKind === k ? "bg-surface-3 text-ink" : "text-ink-4 hover:text-ink-2"
                }`}>
                {k === "concept" ? tr("概念") : tr("行业")}
              </button>
            ))}
          </span>
        }
      >
        <DataTable rows={boards.data?.boards ?? []} max={15}
          empty={tr("暂无{p0}板块数据", { p0: boardKind === "concept" ? tr("概念") : tr("行业") })} chgKeys={["change_pct"]} />
      </V17Card>

      <RotationMatrix data={rotation.data ?? null} />
      <ConceptRpsBoard data={conceptRps.data ?? null} sessionToday={sessionToday} />
      <MainlineBoard data={mainline.data ?? null} />

      {/* 异动偏离 = "我的自选"的风险层,放在全市场层(涨停/板块)之后、
          深度面(龙虎榜/北向)之前 —— 这是从"市场在发生什么"转到
          "我的持仓有没有踩到披露红线"的接缝。 */}
      <AshareDeviationPanel
        data={deviation.data}
        error={deviation.error}
        isLoading={deviation.isLoading}
        today={sessionToday}
      />

      <V17Card
        title={tr("龙虎榜")}
        right={
          <input
            type="date"
            aria-label={tr("龙虎榜交易日")}
            className="bg-surface-2 border border-border-soft rounded px-1 py-0.5 text-[9px] text-ink"
            value={lhbDate}
            onChange={(e) => e.target.value && setLhbDate(e.target.value)}
          />
        }
      >
        <DataTable rows={lhb.data ?? []} max={10}
          empty={tr("暂无该日龙虎榜(非交易日/收盘前未发布/未装 akshare)")} chgKeys={["change_rate"]} />
      </V17Card>

      <V17Card title={tr("北向资金 (每日)")} right={northboundLast ? tr("最近 {northboundLast}", { northboundLast: String(northboundLast) }) : undefined}>
        <DataTable rows={northboundRows} max={10} tail
          empty={tr("暂无北向资金数据(未装 akshare 或数据源无此日)")} chgKeys={["当日成交净买额"]} />
      </V17Card>
      <p className="px-2 text-[9px] text-ink-4">
        {tr("注:北向资金自 2024-08 起交易所停止逐日披露净买额,故仅显示最后披露日;空行已过滤。")}
      </p>
    </div>
  );
}
