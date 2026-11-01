"use client";

import { useEffect, useState } from "react";
import { apiPost } from "@/lib/api-client";
import { useApiPage } from "@/lib/hooks/use-api-page";
import type { EpisodeStateResponse } from "./strategy-types";
import { BlkLabel, PanelHead, ProvChip, fmtEpTime, fmtPct } from "./strategy-parts";
import { AttributionBlock } from "./strategy-attribution";
import { useUiText } from "@/components/i18n/use-ui-text"

/**
 * REPLAY 全页 — 错误调查现场(0907-3 选项②换脑)。
 * 认识论铁律:Outcome 默认密封;换 episode 自动重新密封;
 * T 时刻三选一真落盘(POST /decision,UNIQUE 策略+episode);
 * Reveal 后三方对比 You vs Selene vs Reality。
 */
export function ReplayView({
  strategyId,
  episodeN,
  total,
  onEpisodeChange,
  onExit,
}: {
  strategyId: string;
  episodeN: number;
  total: number;
  onEpisodeChange: (n: number) => void;
  onExit: () => void;
}) {
  const tr = useUiText()

  const { useApi } = useApiPage({ distinguish: true });
  const api = useApi<EpisodeStateResponse>(
    `/api/v1/research/strategy/${strategyId}/state/${episodeN}`,
  );
  const [sealed, setSealed] = useState(true);
  const [decideErr, setDecideErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 换 episode = 换一个 T:结局重新密封(防偷看)
  useEffect(() => {
    setSealed(true);
    setDecideErr(null);
  }, [episodeN]);

  async function decide(d: "execute" | "skip" | "investigate") {
    setBusy(true);
    setDecideErr(null);
    try {
      await apiPost(`/api/v1/research/strategy/${strategyId}/state/${episodeN}/decision`, {
        decision: d,
      });
      await api.mutate();
    } catch {
      setDecideErr("决策落盘失败 — 确认后端已启动");
    }
    setBusy(false);
  }

  const d = api.data;
  const st = d?.state_at_t;
  const ud = st?.user_decision ?? null;

  const pos = total > 1 ? (episodeN - 1) / (total - 1) : 0;
  const posPct = `${(pos * 90).toFixed(2)}%`;

  return (
    <div className="flex flex-col gap-2.5">
      {/* 顶栏 */}
      <div className="flex flex-wrap items-baseline gap-2.5">
        <span className="text-[13px] font-semibold tracking-wide text-ink">
          ERROR EPISODE · WHY DID THIS TRADE FAIL?
        </span>
        <span className="text-[10px] text-ink-3">
          {tr("回到 T 时刻的决策现场 — 只看当时可见的信息,复原当时 Selene 的认知,再研究为什么")}
        </span>
        <span className="ml-auto inline-flex items-center gap-2 text-[9px] text-ink-4">
          <span className="rounded bg-surface-3 px-1.5 py-0.5 text-ink-2">{tr("T 冻结 · 调查现场")}</span>
          <ProvChip p="RECORDED" />
          <ProvChip p="RECONSTRUCTED" />
          <button type="button" onClick={onExit} className="ml-1 text-[10px] text-accent hover:underline">
            {tr("← 返回工作区")}
          </button>
        </span>
      </div>

      {/* Scrubber:Temporal Cursor + YOU ARE HERE */}
      <div className="flex items-center gap-3 rounded-lg border border-border bg-surface-2 px-3 py-2">
        <button
          type="button"
          disabled={episodeN <= 1}
          onClick={() => onEpisodeChange(episodeN - 1)}
          className="h-6 w-6 rounded border border-border bg-surface-3 text-[11px] text-ink-2 hover:border-accent disabled:opacity-40"
        >
          ←
        </button>
        <div className="relative flex h-6 flex-1 items-center">
          <span className="absolute left-0 right-0 h-0.5 bg-border" />
          <span className="absolute h-0.5 w-[25px] bg-accent" style={{ left: posPct }} />
          <span
            className="pointer-events-none absolute top-1/2 h-[26px] w-0.5 -translate-y-1/2 bg-accent shadow-[0_0_8px] shadow-accent/55"
            style={{ left: posPct }}
          >
            <span className="absolute -top-3 left-1/2 -translate-x-1/2 whitespace-nowrap text-[7px] font-bold tracking-[0.1em] text-accent">
              YOU ARE HERE
            </span>
          </span>
          {Array.from({ length: total }, (_, i) => i + 1).map((n) => {
            const p = total > 1 ? (n - 1) / (total - 1) : 0;
            return (
              <button
                key={n}
                type="button"
                onClick={() => onEpisodeChange(n)}
                className={`absolute h-2.5 w-2.5 -translate-y-1/2 rounded-full border-[1.5px] ${
                  n === episodeN
                    ? "border-accent bg-accent shadow-[0_0_0_3px] shadow-accent/20"
                    : "border-ink-4 bg-neg/45"
                }`}
                style={{ left: `${(p * 90).toFixed(2)}%` }}
              />
            );
          })}
        </div>
        <button
          type="button"
          disabled={episodeN >= total}
          onClick={() => onEpisodeChange(episodeN + 1)}
          className="h-6 w-6 rounded border border-border bg-surface-3 text-[11px] text-ink-2 hover:border-accent disabled:opacity-40"
        >
          →
        </button>
        <span className="text-[10px] text-ink-2">
          Episode <span className="num font-semibold text-ink">{episodeN}</span>/{total} ·{" "}
          <span className="num font-semibold text-accent">{tr("T 冻结")}</span>
        </span>
      </div>

      {api.isLoading && d === null ? (
        <div className="flex h-48 items-center justify-center text-[11px] text-ink-3">
          {tr("Episode State(T) 恢复中…")}
        </div>
      ) : api.error ? (
        <div className="rounded-lg border border-neg/40 bg-surface-2 p-3.5 text-[11px] text-ink-2">
          {tr("State(T) 恢复失败 — 确认后端已启动。")}
          <button type="button" onClick={onExit} className="ml-2 text-accent hover:underline">
            {tr("返回工作区")}
          </button>
        </div>
      ) : d && st ? (
        <>
          {/* Market State 窄带(通道截面 — RECONSTRUCTED 重算) */}
          <div className="rounded-lg border border-border bg-surface-1 p-3">
            <div className="flex items-center justify-between gap-3">
              <BlkLabel>
                MARKET STATE · T <span className="num">{fmtEpTime(d.episode.entry_time)}</span>
              </BlkLabel>
              <ProvChip p="RECONSTRUCTED" label={tr("下轨 · RECONSTRUCTED 重算")} />
            </div>
            <MarketBand state={d} />
            <div className="mt-2 flex flex-wrap items-baseline gap-4">
              <ReadItem k="Entry" v={d.episode.entry_price != null ? d.episode.entry_price.toFixed(2) : "—"} />
              <ReadItem
                k={tr("下轨 · 重算")}
                v={st.market_state.keltner ? st.market_state.keltner.entry_bar.lower_band.toFixed(2) : "—"}
              />
              <ReadItem
                k="Exit"
                v={d.episode.exit_price != null ? d.episode.exit_price.toFixed(2) : "—"}
              />
              <ReadItem k={tr("收益")} v={fmtPct(d.episode.return)} />
              {st.market_state.keltner && (
                <ReadItem
                  k={tr("ADX {adx} / 阈值 {thr}", {
                    adx: st.market_state.keltner.entry_bar.adx,
                    thr: st.market_state.keltner.entry_bar.adx_threshold,
                  })}
                  v={st.market_state.keltner.entry_bar.t ? fmtEpTime(st.market_state.keltner.entry_bar.t) : ""}
                />
              )}
            </div>
            <p className="mt-1.5 text-[8.5px] leading-relaxed text-ink-4">
              {st.market_state.bars.length > 0
                ? tr("{n} 根 K 线真切片(RECORDED · klines.db)+ 模板同式重算(RECONSTRUCTED · 可回源)。", {
                    n: st.market_state.bars.length,
                  })
                : tr("K 线切片不可用(klines.db 无该窗口数据)。")}
            </p>
          </div>

          {/* 双栏:当时信号 | 当时认知环境 */}
          <div className="grid grid-cols-1 items-start gap-2 lg:grid-cols-2">
            {/* 左:Signal 为什么触发 */}
            <div className="rounded-lg border border-border bg-surface-1 p-3">
              <PanelHead name={tr("当时信号 · 为什么进")} anchor={`E${d.episode.n} · ${fmtEpTime(d.episode.entry_time)}`} />
              <div className="mt-2 flex flex-col gap-1.5">
                {st.signal_state.rule ? (
                  <>
                    <CondLine
                      k={tr("触发条件")}
                      ok
                      v={st.signal_state.rule.entry}
                      prov={<ProvChip p={st.signal_state.provenance} />}
                    />
                    <CondLine k={tr("规则集")} ok v={st.signal_state.rule.exit} prov={null} />
                    <CondLine k={tr("执行")} ok={false} v={tr("回测环境 · 非实时决策")} prov={null} />
                  </>
                ) : (
                  <div className="text-[10.5px] text-ink-3">
                    {tr("规则重算不可用(K 线窗口不足)— 触发条件留空。")}
                  </div>
                )}
              </div>
              <div className="mt-2.5">
                <BlkLabel>{tr("规则如何判定")}</BlkLabel>
                <p className="mt-1 text-[10px] leading-relaxed text-ink-2">
                  {tr("触发条件由同数据同配置")}<b className="font-semibold">{tr("确定性重算")}</b>{tr("(RECONSTRUCTED),非 AI 重演判断 — 不装作知道后续走势。")}
                </p>
                <p className="num mt-1.5 text-[9px] leading-relaxed text-ink-4">{st.signal_state.note}</p>
              </div>
            </div>

            {/* 右:当时认知环境双清单 + Outcome + 决策 */}
            <div className="rounded-lg border border-border bg-surface-1 p-3">
              <PanelHead name="SELENE · AT THAT TIME" anchor={tr("当时知道 vs 现在知道")} />
              <div className="mt-2 flex flex-col gap-1">
                <BlkLabel>{tr("RECORDED AT T(当时真实存储)")}</BlkLabel>
                <DualRow
                  ok
                  v={tr("{sym} · {tf} K 线({n} bars)", {
                    sym: d.as_run.symbol,
                    tf: d.as_run.interval,
                    n: st.market_state.bars.length,
                  })}
                />
                <DualRow ok v={tr("{id} 回测 trade 记录", { id: d.episode.backtest_id })} />
                <DualRow
                  ok
                  v={tr("声明 vs 实跑配置({state})", { state: d.drift ? tr("漂移已确认") : tr("一致") })}
                />

                <div className="mt-1.5">
                  <BlkLabel>{tr("NOT AVAILABLE THEN(当时不知道)")}</BlkLabel>
                </div>
                <DualRow ok={false} v={tr("后续走势 / 后续新闻")} />
                <DualRow ok={false} v={tr("这笔结果(盈利或亏损)")} />
                <DualRow ok={false} v={tr("funding / open interest / 事件流(当时不落盘)")} />
              </div>

              <div className="mt-2">
                <BlkLabel>{tr("当时可用记忆(Memory · consulted)")}</BlkLabel>
                <div className="mt-1 rounded-md border border-dashed border-ink-5 px-3 py-2.5 text-[10.5px] leading-relaxed text-ink-4">
                  {st.memory_state.note} {tr("— 当时还没有可参考的策略记忆。")}
                </div>
              </div>

              {/* Outcome — 密封/揭示 */}
              <div
                className={`mt-2 border-t border-border-soft pt-2 transition-all ${
                  sealed ? "pointer-events-none select-none opacity-25 blur-[4px]" : ""
                }`}
              >
                <div className="flex items-baseline gap-1.5">
                  <BlkLabel>{tr("Outcome(现在才知道 — T 时刻不可见)")}</BlkLabel>
                  <ProvChip p="RECORDED" />
                </div>
                <div className={`num mt-0.5 text-[12px] font-semibold ${((d.episode.pnl ?? 0) < 0 ? "text-neg" : "text-pos")}`}>
                  E{d.episode.n} · {d.episode.symbol} · {d.episode.side ?? "—"} · {fmtPct(d.episode.return)}
                </div>
                <p className="mt-0.5 text-[10.5px] leading-relaxed text-ink-2">
                  {tr("入场")} {d.episode.entry_price?.toFixed(2)} @ {fmtEpTime(d.episode.entry_time)} {tr("→ 出场")}{" "}
                  {d.episode.exit_price?.toFixed(2)} @ {fmtEpTime(d.episode.exit_time)} · PnL{" "}
                  {d.episode.pnl?.toFixed(2)} {tr("· 数量")} {d.episode.quantity?.toFixed(2)}
                </p>
              </div>
              {sealed && (
                <p className="mt-0.5 text-[9px] text-ink-4">
                  {tr("未 Reveal 前不展示结局 — 认识论铁律:T 时刻你和当时的 Selene 知道得一样多。")}
                </p>
              )}

              {/* T 时刻决策区 — 真落盘 */}
              <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-dashed border-border pt-2.5">
                <span className="mr-1 text-[8.5px] font-bold tracking-wider text-ink-4">{tr("T 时刻 · 假如是你:")}</span>
                <span className="text-[10px] text-ink-2">{tr("只凭当时可见的信息,这笔你会 —")}</span>
                {(["execute", "skip", "investigate"] as const).map((k) => {
                  const chosen = ud?.decision === k;
                  const style =
                    k === "execute"
                      ? "border-pos/45 text-pos bg-pos/10"
                      : k === "skip"
                        ? "border-warn/45 text-warn bg-warn/10"
                        : "border-accent/55 text-accent bg-accent/10";
                  const label = k === "execute" ? tr("Execute · 跟随信号") : k === "skip" ? tr("Skip · 跳过这笔") : tr("Investigate · 先查再决定");
                  return (
                    <button
                      key={k}
                      type="button"
                      disabled={busy}
                      onClick={() => decide(k)}
                      className={`rounded border px-2 py-[3px] text-[9.5px] transition-colors disabled:opacity-50 ${
                        chosen ? `${style} font-semibold` : "border-border bg-surface-2 text-ink-2 hover:border-accent/50 hover:text-accent"
                      }`}
                    >
                      {chosen ? `✓ ${label}` : label}
                    </button>
                  );
                })}
                {ud && (
                  <span className="num w-full text-[8.5px] text-ink-4">
                    {tr("已落盘")} {ud.decision} · {fmtEpTime(ud.decided_at)} {tr("· RECORDED(Reveal 后三方对比:你 vs Selene vs 现实)")}
                  </span>
                )}
                {decideErr && <span className="w-full text-[9px] text-neg">{tr(decideErr)}</span>}
                {!ud && !decideErr && (
                  <span className="w-full text-[8.5px] text-ink-4">
                    {tr("选择即落盘(user_decisions 表)· 答案在 Reveal 之后才存在")}
                  </span>
                )}
              </div>

              {/* Reveal + 三方对比 post-mortem */}
              <div className="mt-2 flex items-center gap-2 border-t border-border-soft pt-2">
                <button
                  type="button"
                  onClick={() => setSealed((s) => !s)}
                  className="rounded-md bg-accent px-3.5 py-1.5 text-[11px] font-medium text-accent-foreground transition-colors hover:bg-accent/85"
                >
                  {sealed ? "Reveal Outcome" : tr("已揭示 · 重新密封")}
                </button>
                <span className="text-[9.5px] text-ink-4">{tr("点一下才揭示结局 — 做决定时绝不偷看未来")}</span>
              </div>
              {!sealed && (
                <div className="mt-2 border-t border-border-soft pt-2">
                  <BlkLabel>{tr("POST-MORTEM · 当时 vs 实际 vs 学到")}</BlkLabel>
                  <PmRow
                    k={tr("当时")}
                    v={
                      st.signal_state.rule
                        ? tr("规则成立:{rule}(回测环境,非实时决策)", { rule: st.signal_state.rule.entry })
                        : tr("规则重算不可用")
                    }
                  />
                  <PmRow
                    k={tr("实际")}
                    v={tr("{ret} · PnL {pnl}(trades_json 真值)", {
                      ret: fmtPct(d.episode.return),
                      pnl: d.episode.pnl?.toFixed(2),
                    })}
                  />
                  <PmRow
                    k={tr("学到")}
                    v={
                      d.drift
                        ? tr("这一笔亏损不可归因于策略 — 实验被配置漂移污染(声明 vs 实跑不一致)。你的 T 时刻选择已与 Selene 认知、现实结果并排入账,供下次同类场景对照。")
                        : tr("配置一致,该笔亏损按常规归因路径处理 — 待更多样本累计证据。")
                    }
                    bold
                  />
                  <AttributionBlock strategyId={strategyId} episodeN={episodeN} drift={d.drift} />
                  <p className="num mt-1 text-[9px] leading-relaxed text-ink-4">{st.selene_decision.note}</p>
                </div>
              )}
            </div>
          </div>

          <p className="text-center text-[9.5px] leading-relaxed text-ink-4">
            {tr("错误调查现场 · 拖动进度条 = 把整个世界拨回 T 时刻;调查结论在 REVALIDATE 后进 Mission 链路。")}
          </p>
        </>
      ) : null}
    </div>
  );
}

/* ---------- 子块 ---------- */

function ReadItem({ k, v }: { k: string; v: string }) {
  return (
    <span className="inline-flex items-baseline gap-1.5 text-[9.5px] text-ink-3">
      <b className="font-semibold text-ink-2">{k}</b>
      <span className="num">{v}</span>
    </span>
  );
}

function CondLine({ k, ok, v, prov }: { k: string; ok: boolean; v: string; prov: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-1.5 text-[10.5px] text-ink-2">
      <span className="shrink-0 text-[9.5px] text-ink-3">{k}</span>
      <span className={ok ? "text-pos" : "text-neg"}>{ok ? "✓" : "✗"}</span>
      <span className="num">{v}</span>
      {prov}
    </div>
  );
}

function DualRow({ ok, v }: { ok: boolean; v: string }) {
  return (
    <div className={`flex items-baseline gap-1.5 text-[10.5px] ${ok ? "text-ink-2" : "text-ink-4"}`}>
      <span className={`shrink-0 text-[10px] ${ok ? "text-pos" : "text-ink-4"}`}>{ok ? "✓" : "✗"}</span>
      <span className="num">{v}</span>
    </div>
  );
}

function PmRow({ k, v, bold }: { k: string; v: string; bold?: boolean }) {
  return (
    <div className="mt-1 flex items-baseline gap-2 text-[10px] leading-relaxed text-ink-2">
      <span className="w-7 shrink-0 text-[9px] tracking-wide text-ink-4">{k}</span>
      <span className={bold ? "font-medium text-ink" : ""}>{v}</span>
    </div>
  );
}

/** Market State 几何窄带 — 通道截面(entry/exit 点位按真价在 band 内定位) */
function MarketBand({ state }: { state: EpisodeStateResponse }) {
  const tr = useUiText()

  const kn = state.state_at_t.market_state.keltner;
  if (!kn) {
    return (
      <div className="mt-2 flex h-[52px] items-center justify-center rounded-md border border-border-soft text-[10px] text-ink-4">
        {tr("通道重算不可用 — 该窗口无法重算几何。")}
      </div>
    );
  }
  const { upper_band, lower_band } = kn.entry_bar;
  const span = upper_band - lower_band;
  const clamp = (x: number) => Math.max(0, Math.min(100, ((x - lower_band) / span) * 100));
  const entryPct = clamp(state.episode.entry_price ?? lower_band);
  const exitPct = clamp(state.episode.exit_price ?? lower_band);
  return (
    <div className="relative mt-2 h-[52px] rounded-md border border-border-soft bg-gradient-to-b from-accent/[0.07] via-ink-5/[0.04] to-transparent">
      <span className="absolute left-0 right-0 top-0 h-0.5 bg-accent/85">
        <span className="absolute right-1 -top-3.5 text-[8.5px] tracking-wide text-ink-4">{tr("上轨")}</span>
      </span>
      <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-accent/85">
        <span className="absolute right-1 -top-3.5 text-[8.5px] tracking-wide text-ink-4">{tr("下轨")}</span>
      </span>
      <span
        className="absolute h-2.5 w-2.5 -translate-x-1/2 translate-y-1/2 rounded-full bg-pos shadow-[0_0_0_2px] shadow-surface-1"
        style={{ left: "18%", top: `${100 - entryPct}%` }}
      />
      <span
        className="absolute h-2.5 w-2.5 -translate-x-1/2 translate-y-1/2 rounded-full bg-neg opacity-90 shadow-[0_0_0_2px] shadow-surface-1"
        style={{ left: "40%", top: `${100 - exitPct}%` }}
      />
    </div>
  );
}
