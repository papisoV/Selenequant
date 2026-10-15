"use client";

import type { EpisodeRow, StrategyStateResponse } from "./strategy-types";
import { BlkLabel, CondRow, EmptyFeed, PanelHead, ProvChip, fmtEpTime, fmtPct } from "./strategy-parts";
import { RuleBatchAttributionButton } from "./strategy-attribution";

/* ---------- 头部:身份 + 漂移并列(第一条 Intelligence demo) ---------- */

export function StratHeader({ state }: { state: StrategyStateResponse }) {
  const pack = state.episodes_pack;
  return (
    <header className="rounded-lg border border-border bg-surface-2 p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        {pack.drift && (
          <span className="inline-flex items-center gap-1.5 rounded border border-warn/55 bg-warn/10 px-1.5 py-0.5 text-[10px] font-medium text-warn">
            <span className="h-[5px] w-[5px] rounded-full bg-warn" />
            配置漂移 · 调查已结案
          </span>
        )}
        <span className="num text-[10px] text-ink-3">{state.strategy.id}</span>
        <span className="num ml-auto text-[10px] text-ink-3">{pack.backtest_id} · {fmtEpTime(pack.created_at)}</span>
      </div>
      <h1 className="mt-2 text-[16px] font-semibold leading-snug tracking-tight text-ink">{state.strategy.name}</h1>
      <div className="mt-1 text-[12px] text-ink-2">
        {pack.drift
          ? "声明配置与实跑环境不一致 — 逐笔回放调查这笔亏损到底是什么"
          : "策略运行记录与回放调查"}
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-x-3.5 gap-y-1.5 border-t border-border-soft pt-2.5">
        <span className="text-[9px] tracking-wider text-ink-4">INTENDED → ACTUAL</span>
        <span className="num text-[10.5px] text-ink">
          {pack.declared.symbol ?? "—"} · {pack.declared.interval ?? "—"}
          <span className="mx-1.5 text-ink-4">→</span>
          <span className={pack.drift ? "text-warn" : ""}>{pack.as_run.symbol} · {pack.as_run.interval}</span>
        </span>
        {pack.drift && (
          <span className="rounded border border-warn/45 bg-warn/10 px-1.5 py-px text-[9px] font-semibold text-warn">
            ⚠ DRIFT
          </span>
        )}
      </div>
    </header>
  );
}

/* ---------- NOW:驾驶舱主视图 ---------- */

export function NowSection({ state, onReplay }: { state: StrategyStateResponse; onReplay: (n: number) => void }) {
  const pack = state.episodes_pack;
  const m = pack.metrics;
  const openMission = state.missions.find((mi) => mi.phase !== "resolved" && mi.phase !== "parked");
  const allLoss = pack.episodes.every((e) => (e.pnl ?? 0) < 0);
  return (
    <section className="rounded-lg border border-border bg-surface-1 p-3">
      <PanelHead name="NOW · 这条策略到底还能不能信?" role="驾驶舱主视图" />
      <div className="mt-2.5 grid grid-cols-1 items-start gap-3.5 lg:grid-cols-[46fr_54fr]">
        <div>
          <BlkLabel>Strategy Condition · 定性</BlkLabel>
          <div>
            <CondRow
              k="Configuration 配置"
              state={pack.drift ? "⚠ mismatch" : "● aligned"}
              stateTone={pack.drift ? "warn" : "muted"}
              v={`${pack.declared.symbol ?? "—"} ${pack.declared.interval ?? "—"} vs ${pack.as_run.symbol} ${pack.as_run.interval}`}
            />
            <CondRow k="Evidence base 证据量" state="● LOW" stateTone="warn" v={`${pack.episodes.length} episodes`} />
            <CondRow
              k="Recent performance"
              state={allLoss ? "▼ WEAK" : "○ MIXED"}
              stateTone={allLoss ? "neg" : "muted"}
              v={`${fmtPct(m.total_return)} · ${m.total_trades} 笔`}
            />
            <CondRow k="Current market fit" state="? UNKNOWN" stateTone="muted" v="暂无判定依据" />
            <CondRow k="Execution 执行" state="— 未运行" stateTone="off" v="回测案例 · 无 live" />
          </div>
        </div>
        <div>
          <BlkLabel>当前信号 · 上下文 · analogues</BlkLabel>
          <div className="rounded-md border border-dashed border-ink-5 px-3 py-2.5 text-[11px] leading-relaxed text-ink-2">
            <b className="font-medium text-ink">{state.strategy.name} 目前未在运行</b> — 无实时信号,也没有可用的
            analogues({pack.episodes.length} 笔样本不足,证据档位 LOW)。
            {openMission ? (
              <>
                <br />
                当前活跃工作 = <span className="num">{openMission.id}</span> {openMission.title}。
              </>
            ) : (
              <>
                <br />
                当前无活跃 Mission。
              </>
            )}
          </div>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            {openMission && (
              <button
                type="button"
                onClick={() => window.open(`/research/missions?id=${encodeURIComponent(openMission.id)}`, "_self")}
                className="rounded-md bg-accent px-3.5 py-1.5 text-[11px] font-medium text-accent-foreground transition-colors hover:bg-accent/85"
              >
                Investigate · 打开现有调查
              </button>
            )}
            <button
              type="button"
              onClick={() => onReplay(pack.episodes.length)}
              className="rounded-md border border-border bg-surface-3 px-3 py-1.5 text-[11px] text-ink transition-colors hover:border-accent/50"
            >
              Replay latest →
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ---------- HISTORY:点阵 ribbon ---------- */

export function HistorySection({
  episodes,
  selected,
  onSelect,
}: {
  episodes: EpisodeRow[];
  selected: number | null;
  onSelect: (n: number) => void;
}) {
  if (episodes.length === 0) return null;
  return (
    <section className="rounded-lg border border-border bg-surface-1 p-3">
      <PanelHead name={`HISTORY · ${episodes.length} episodes`} role="点阵生命轨迹 · hover 看单笔 · 点击进入 REPLAY" />
      <div className="mt-2 flex items-center gap-1.5">
        {episodes.map((e) => {
          const loss = (e.pnl ?? 0) < 0;
          return (
            <button
              key={e.n}
              type="button"
              title={`E${e.n} · ${e.symbol} ${fmtPct(e.return)} · ${fmtEpTime(e.entry_time)}`}
              onClick={() => onSelect(e.n)}
              className={`h-3.5 w-3.5 rounded-full border transition-colors ${
                loss ? "border-neg-soft bg-neg/55" : "border-pos-soft bg-pos/55"
              } ${selected === e.n ? "outline outline-2 outline-offset-2 outline-accent" : ""}`}
            />
          );
        })}
        <div className="ml-2 flex items-center gap-2.5 text-[9px] text-ink-4">
          <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-neg" />loss</span>
          <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-pos" />win</span>
        </div>
      </div>
      <div className="num mt-1.5 flex justify-between text-[8.5px] text-ink-4">
        <span>{fmtEpTime(episodes[0].entry_time).slice(0, 5)}</span>
        <span>{fmtEpTime(episodes[episodes.length - 1].exit_time).slice(0, 5)}</span>
      </div>
    </section>
  );
}

/* ---------- ERRORS:失败事件卡(真值来自 trades_json) ---------- */

export function ErrorsSection({ state, onReplay }: { state: StrategyStateResponse; onReplay: (n: number) => void }) {
  const eps = state.episodes_pack.episodes;
  const driftMission = state.missions.find((mi) => mi.phase === "resolved");
  const totalReturn = state.episodes_pack.metrics.total_return;
  return (
    <section className="rounded-lg border border-border bg-surface-1 p-3">
      <PanelHead
        name="ERRORS · 哪里失败过"
        role="每笔都可点开回放调查"
        anchor={`${driftMission ? 1 : 0} 起结案 · 0 起在查`}
      />
      <div className="mt-2 flex flex-col gap-1.5">
        {eps.map((e) => (
          <button
            key={e.n}
            type="button"
            onClick={() => onReplay(e.n)}
            title="点击进入错误调查现场"
            className="flex items-center gap-2.5 rounded-md border border-border-soft bg-surface-1 px-2.5 py-1.5 text-left transition-colors hover:border-accent"
          >
            <span className="w-6 shrink-0 text-[9px] font-bold tracking-wide text-neg">E{e.n}</span>
            <span className="min-w-0 flex-1 truncate text-[10px] text-ink">
              <b className="font-semibold">{e.symbol} · {e.side ?? "—"}</b> — {fmtEpTime(e.entry_time)} 入场 {e.entry_price.toFixed(2)} → {fmtEpTime(e.exit_time)} 出场 {e.exit_price?.toFixed(2) ?? "—"}
            </span>
            <span className="flex shrink-0 items-baseline gap-2.5 text-[9px] text-ink-4">
              <span className={`num font-semibold ${((e.pnl ?? 0) < 0 ? "text-neg" : "text-pos")}`}>{fmtPct(e.return)}</span>
              <span className="text-accent">→ 调查</span>
            </span>
          </button>
        ))}
      </div>
      {driftMission && (
        <div className="mt-2 flex items-baseline gap-2 rounded-md border border-dashed border-warn/40 bg-warn/[0.04] px-2.5 py-1.5 text-[9.5px] leading-relaxed text-ink-2">
          <b className="shrink-0 font-semibold text-ink">共性根因(已结案 {driftMission.id}):</b>
          <span>
            {eps.length} 笔全亏<b className="font-semibold text-ink">不可归因于策略</b> —
            实验被配置漂移污染。修复 = 重验证,非 V3。合计 {fmtPct(totalReturn)}(逐笔为 trades_json 真值)。
          </span>
          <RuleBatchAttributionButton strategyId={state.strategy.id} />
        </div>
      )}
    </section>
  );
}

/* ---------- INTELLIGENCE:四桶(KNOWN FAILURE 唯一真知识) ---------- */

/* outcome.verdict → 标题映射;真实 outcome 契约由 record_outcome 写入方决定
 * (scripts/keltner_revalidate.py: verdict/declared_intent/in_sample/oos/method) */
const VERDICT_TITLES: Record<string, string> = {
  REVALIDATED: "修正配置后 OOS 为正 — 结论成立",
  FRAMEWORK_SUSPECT: "修正配置后 OOS 仍异常 — 结论存疑",
};

export function IntelSection({ state }: { state: StrategyStateResponse }) {
  const resolvedMissions = state.missions.filter((mi) => mi.phase === "resolved");
  const resolved = resolvedMissions[0];
  const outcome = (resolved?.outcome ?? {}) as Record<string, unknown>;
  const hasOutcome = Object.keys(outcome).length > 0;
  const oos = (outcome.oos ?? {}) as Record<string, unknown>;
  const oosRet = typeof oos.total_return === "number" ? oos.total_return : null;
  const verdict = typeof outcome.verdict === "string" ? outcome.verdict : null;
  const method = typeof outcome.method === "string" ? outcome.method : null;
  const knTitle = verdict
    ? (VERDICT_TITLES[verdict] ?? resolved!.title)
    : resolved?.title ?? null;
  return (
    <section className="rounded-lg border border-border bg-surface-1 p-3">
      <PanelHead
        name="INTELLIGENCE · Strategy Knowledge"
        role="每条知识附为什么知道 / 从哪来 / 结果"
        anchor={resolvedMissions.length > 0 ? `${resolvedMissions.length} 条已结案知识` : "暂无"}
      />
      <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2">
        <div className="rounded-lg border border-border bg-surface-2 p-2.5">
          <div className="flex items-baseline gap-1.5">
            <span className="text-[10px] font-semibold tracking-wide text-pos">KNOWN TO WORK</span>
            <span className="ml-auto text-[9px] text-ink-4">验证过有效的</span>
          </div>
          <EmptyFeed className="mt-2">
            还没有验证过成功的知识 — 第一次真实验证的实验入账后出现在这里。
          </EmptyFeed>
        </div>
        <div className="rounded-lg border border-border bg-surface-2 p-2.5">
          <div className="flex items-baseline gap-1.5">
            <span className="text-[10px] font-semibold tracking-wide text-neg">KNOWN FAILURE</span>
            <span className="ml-auto text-[9px] text-ink-4">验证过失败的</span>
          </div>
          {resolved ? (
            hasOutcome ? (
              <div className="mt-2 rounded-md border border-warn/35 bg-warn/[0.05] p-2.5">
                <div className="flex items-baseline gap-1.5">
                  <span className="text-[11.5px] font-semibold text-ink">{knTitle}</span>
                  <ProvChip p="RECORDED" />
                </div>
                {oosRet !== null && (
                  <p className="mt-1 text-[10px] leading-relaxed text-ink-2">
                    {verdict === "REVALIDATED" ? "为什么知道:修正配置后 OOS 回测 " : "OOS 回测 "}
                    {fmtPct(oosRet)}
                    {method ? ` — ${method}` : ""}
                  </p>
                )}
                <div className="num mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[9.5px] text-ink-3">
                  <span><b className="font-medium text-ink-2">Source</b> 1 Mission</span>
                  <span><b className="font-medium text-ink-2">Validation</b> {resolved.id}</span>
                  <span><b className="font-medium text-ink-2">Last observed</b> {fmtEpTime(resolved.updated_at).slice(0, 5)}</span>
                </div>
                <div className="mt-1.5 flex items-center gap-1.5 border-t border-border-soft pt-1.5 text-[9px] text-ink-4">
                  <span className="num">{resolved.id} · 已结案</span>
                  <span className="ml-auto text-accent">为什么知道 →</span>
                </div>
              </div>
            ) : (
              /* 军规②:mission 已结案但 outcome 为空 — 诚实空态,不编造 */
              <div className="mt-2 rounded-md border border-border-soft bg-surface-3 p-2.5 text-[10px] leading-relaxed text-ink-3">
                结案任务 <span className="num">{resolved.id}</span>(「{resolved.title}」)尚未写入 outcome — 结论文待记录。
              </div>
            )
          ) : (
            <EmptyFeed className="mt-2">
            还没有验证过失败的记录 — 证据不足时保持空白,不猜结论。
          </EmptyFeed>
          )}
        </div>
        <div className="rounded-lg border border-border bg-surface-2 p-2.5">
          <div className="flex items-baseline gap-1.5">
            <span className="text-[10px] font-semibold tracking-wide text-warn">UNCERTAIN</span>
            <span className="ml-auto text-[9px] text-ink-4">证据不足</span>
          </div>
          <EmptyFeed className="mt-2">
            还没有证据不足的条目 — 样本不够时结论不会写进这里。
          </EmptyFeed>
        </div>
        <div className="rounded-lg border border-border bg-surface-2 p-2.5">
          <div className="flex items-baseline gap-1.5">
            <span className="text-[10px] font-semibold tracking-wide text-accent">EXPERIMENTS</span>
            <span className="ml-auto text-[9px] text-ink-4">正在进行的实验</span>
          </div>
          <EmptyFeed className="mt-2">
            还没有正在进行的实验 — 下一次 REVALIDATE 执行后会出现在这里。
          </EmptyFeed>
        </div>
      </div>
    </section>
  );
}

/* ---------- EVOLUTION:Lineage 版本树 ---------- */

export function EvolutionSection({ state }: { state: StrategyStateResponse }) {
  const pack = state.episodes_pack;
  return (
    <section className="rounded-lg border border-border bg-surface-1 p-3">
      <PanelHead name="EVOLUTION · 下一版从哪里来" role="AI 提出 · 实验证明 · 你授权" anchor="暂无在途新版本" />
      <div className="mt-2 rounded-lg border border-border bg-surface-2 p-2.5">
        <BlkLabel>Lineage · 策略版本树</BlkLabel>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span className="inline-flex flex-col gap-0.5 rounded border border-accent/50 bg-surface-3 px-2 py-1">
            <span className="text-[11px] font-semibold text-accent">V2 · declared</span>
            <span className="num text-[8.5px] text-ink-4">{pack.declared.symbol} · {pack.declared.interval}</span>
          </span>
          <span className="text-[9px] text-ink-4">→</span>
          {pack.drift ? (
            <span className="inline-flex flex-col gap-0.5 rounded border border-warn/45 bg-surface-3 px-2 py-1">
              <span className="text-[11px] font-semibold text-warn">V2-as-run ⚠ 漂移</span>
              <span className="num text-[8.5px] text-ink-4">{pack.as_run.symbol} · {pack.as_run.interval} · 脏跑</span>
            </span>
          ) : (
            <span className="inline-flex flex-col gap-0.5 rounded border border-border bg-surface-3 px-2 py-1">
              <span className="text-[11px] font-semibold text-ink">V2-as-run</span>
              <span className="num text-[8.5px] text-ink-4">{pack.as_run.symbol} · {pack.as_run.interval}</span>
            </span>
          )}
          <span className="text-[9px] text-ink-4">→</span>
          <span className="inline-flex flex-col gap-0.5 rounded border border-border bg-surface-3 px-2 py-1">
            <span className="text-[11px] font-semibold text-ink">V2 · revalidate</span>
            <span className="text-[8.5px] text-ink-4">恢复声明配置</span>
          </span>
        </div>
        <p className="mt-1.5 text-[9.5px] leading-relaxed text-ink-4">
          这次漂移是执行异常,不是策略失败 — 修复路径是回到声明配置重新验证,
          不产生新版本。
        </p>
      </div>
      <div className="mt-2.5 border-t border-dashed border-border pt-2.5">
        <BlkLabel>下一版候选</BlkLabel>
        <EmptyFeed className="mt-1.5">
          暂无新版本计划 — 改进假设由分析提出、经实验证明后,才会出现在这里。
        </EmptyFeed>
      </div>
    </section>
  );
}
