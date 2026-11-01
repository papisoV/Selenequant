"use client";

/**
 * Episode 抽屉 —— 账本 episode 的内联消费面(挂在 question-surface 引用徽章上)。
 *
 * 打开才拉 /episodes/{id}(懒加载);渲染 lifecycle 徽章(观察周期)、
 * transitions 事件流、流转按钮(推进/退役)。payload 分型渲染与重跑链
 * 见后续刀(本文件是刀1 的最小闭环)。
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  getEpisode,
  getSubjectDerivation,
  transitionEpisode,
  type EpisodeDetail,
  type EpisodeLifecycleTransition,
  type SubjectDerivation,
} from "@/lib/api/research-graph";
import { walkDerivedChain } from "@/lib/research/derived-chain";
import { useUiText } from "@/components/i18n/use-ui-text"

// 观察周期徽章:五态色 + 口语标签(加"周期:"词头,与 alpha-mining 的
// "审读:"词头区分 —— 三套生命周期词汇并存,UI 层消歧义,零迁移)
const LIFECYCLE_BADGE: Record<string, { label: string; cls: string }> = {
  DISCOVERED: { label: "周期:新发现", cls: "border-pos/40 bg-pos/5 text-pos" },
  INSPECTED: { label: "周期:已查验", cls: "border-accent/40 bg-accent/5 text-accent" },
  PAPER_TRADING: {
    label: "周期:模拟盘",
    cls: "border-gold/40 bg-gold/5 text-gold",
  },
  LIVE: { label: "周期:实盘", cls: "border-pos bg-pos/10 text-pos font-semibold" },
  RETIRED: { label: "周期:已退役", cls: "border-border bg-surface-3 text-ink-4" },
};

// 当前状态允许流转到的下一步(与 storage ALLOWED_TRANSITIONS 一致;
// RETIRED 是终态无出边 → 无按钮)
const NEXT_TRANSITIONS: Record<string, { to: string; label: string }[]> = {
  DISCOVERED: [{ to: "INSPECTED", label: "推进到已查验" }],
  INSPECTED: [{ to: "PAPER_TRADING", label: "推进到模拟盘" }],
  PAPER_TRADING: [{ to: "LIVE", label: "推进到实盘" }],
  LIVE: [],
  RETIRED: [],
};

function formatTs(iso: string): string {
  return iso.slice(0, 16).replace("T", " ");
}

// trend_90d_vs_prior 三分支阈值(Architect S5):超阈着色,阈内中性
export const TREND_THRESHOLD = 0.005;

function trendCls(trend: number | null | undefined): string {
  if (trend == null) return "text-ink-4";
  if (trend > TREND_THRESHOLD) return "text-pos";
  if (trend < -TREND_THRESHOLD) return "text-neg";
  return "text-ink-4";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Payload = Record<string, any>;

const fmt = (v: number | null | undefined): string =>
  v == null ? "—" : v >= 0 ? `+${v.toFixed(4)}` : v.toFixed(4);

/** 分段符号一致率 → "3/4"；缺值/无方向 → "—"。
 *  诊断字段(不参与门禁):显示"这个信号是全程成立还是只在某段行情成立"。 */
const fmtSubperiod = (sp: unknown): string => {
  if (!sp || typeof sp !== "object") return "—";
  const o = sp as { consistent?: unknown; n_nonempty?: unknown; sign_ratio?: unknown };
  if (o.sign_ratio == null) return "—"; // 无方向可言(整体 IC 为零或样本不足)
  if (typeof o.consistent === "number" && typeof o.n_nonempty === "number") {
    return `${o.consistent}/${o.n_nonempty}`;
  }
  return "—";
};

/** funding_ic / vol_estimators 形状:candidates.{factor}.{overall, recent90, trend} */
function IcCandidatesTable({ candidates }: { candidates: Payload }) {
  const tr = useUiText()

  const rows = Object.entries(candidates).filter(
    ([, v]) => v && typeof v === "object" && "overall_mean_ic" in v,
  );
  if (rows.length === 0) return null;
  return (
    <table className="mt-1.5 w-full text-[10px]" data-testid="ic-candidates-table">
      <thead>
        <tr className="text-left text-ink-4">
          <th className="font-normal">factor</th>
          <th className="font-normal">overall</th>
          <th className="font-normal">last90d</th>
          <th className="font-normal">trend</th>
          <th className="font-normal">{tr("分段")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([factor, v]) => (
          <tr key={factor} data-testid="ic-candidates-row">
            <td className="font-mono text-ink-2">{factor}</td>
            <td className="font-mono text-ink-2">{fmt(v.overall_mean_ic)}</td>
            <td className="font-mono text-ink">{fmt(v.recent_90d_mean_ic)}</td>
            <td className={`font-mono ${trendCls(v.trend_90d_vs_prior)}`}>
              {v.trend_90d_vs_prior == null ? "—" : fmt(v.trend_90d_vs_prior)}
            </td>
            <td className="font-mono text-ink-4" data-testid="ic-row-subperiod">
              {fmtSubperiod(v.subperiod_sign_consistency)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** entry_delay_ic 形状:candidates.delay_{N}.funding_z.{ic} + ic_half_delay */
function DelayRows({ candidates }: { candidates: Payload }) {
  const tr = useUiText()

  const delays = Object.keys(candidates)
    .filter((k) => k.startsWith("delay_"))
    .sort((a, b) => Number(a.slice(6)) - Number(b.slice(6)));
  if (delays.length === 0) return null;
  const half = candidates.ic_half_delay_funding_z;
  return (
    <div className="mt-1.5 text-[10px]" data-testid="delay-rows">
      {delays.map((k) => {
        const ic = candidates[k]?.funding_z?.ic;
        const n = candidates[k]?.n_valid_bars;
        const sp = candidates[k]?.funding_z?.subperiod_sign_consistency;
        return (
          <div key={k} className="flex items-center gap-3" data-testid="delay-row">
            <span className="w-10 font-mono text-ink-4">{k.replace("delay_", "d")}</span>
            <span className="font-mono text-ink">{fmt(ic)}</span>
            <span className="text-ink-4">n={n ?? "—"}</span>
            <span className="text-ink-4" data-testid="delay-row-subperiod">
              {tr("分段")} {fmtSubperiod(sp)}
            </span>
          </div>
        );
      })}
      <div className="mt-1 text-ink-3" data-testid="half-delay">
        {tr("半衰档:")}{half == null ? "—" : `d${half}`}
      </div>
    </div>
  );
}

/** payload 分型渲染(后端不整形,前端按 payload.probe 分支) */
function ProbePayloadPanel({
  evidencePayload,
}: {
  evidencePayload: Payload | null;
}) {
  const tr = useUiText()

  if (!evidencePayload || typeof evidencePayload !== "object") return null;
  const { summary, probe, target, candidates, family, replay_overlap: replayOverlap } =
    evidencePayload;
  if (typeof summary !== "string") return null;
  return (
    <div className="mt-3 border-t border-border-soft pt-2" data-testid="probe-payload-panel">
      <p className="text-[10px] text-ink-3">
        {tr("探针数据")} <span className="font-mono text-ink-4">{probe}</span>
      </p>
      {family && typeof family === "object" && (
        <p className="mt-1 text-[10px] text-ink-4" data-testid="payload-family">
          {tr("家族已试")} {family.n_attempts} {tr("次 · 本次过线")} {family.n_sig}/{family.n_candidates}
          {tr("(阈 |last90d|≥")}{family.threshold_abs}{tr(") · 二项 p=")}{typeof family.binom_p === "number" ? family.binom_p.toFixed(3) : "—"}
          {family.exceeds_random_baseline && (
            <span className="ml-1 text-gold">{tr("超随机基线")}</span>
          )}
        </p>
      )}
      <p className="mt-1 whitespace-pre-wrap text-[11px] leading-relaxed text-ink-2">
        {summary}
      </p>
      {replayOverlap && typeof replayOverlap === "object" && (
        <p
          className="mt-1 text-[10px] text-warn"
          data-testid="payload-replay-overlap"
        >
          {tr("这段窗口")} {(Number(replayOverlap.ratio) * 100).toFixed(1)}{tr("% 已被看过")}
          {typeof replayOverlap.prior_episode_count === "number" &&
            replayOverlap.prior_episode_count > 1 && (
              <span className="text-ink-4">
                {" "}
                {tr("(共")} {replayOverlap.prior_episode_count} {tr("次)")}
              </span>
            )}
          {typeof replayOverlap.latest_prior_episode_id === "string" && (
            <span className="text-ink-4">
              {" "}
              {tr("(上次:")}{replayOverlap.latest_prior_episode_id})
            </span>
          )}
          {tr("—— 不构成样本外确认")}
        </p>
      )}
      {typeof target?.definition === "string" && (
        <p className="mt-1 text-[10px] text-ink-4" data-testid="payload-target-definition">
          target: {target.definition as string}
        </p>
      )}
      {candidates && typeof candidates === "object" && (
        <>
          <IcCandidatesTable candidates={candidates} />
          <DelayRows candidates={candidates} />
        </>
      )}
    </div>
  );
}

function TransitionRow({ t }: { t: EpisodeLifecycleTransition }) {
  return (
    <li className="text-[11px] text-ink-2" data-testid="lifecycle-transition-row">
      <span className="font-mono text-[10px] text-ink-4">{formatTs(t.at)}</span>
      <span className="ml-2">
        {t.from} → <b className="font-semibold text-ink">{t.to}</b>
      </span>
      <span className="ml-2 text-ink-4">
        {t.actor}
        {t.reason ? ` — ${t.reason}` : ""}
      </span>
    </li>
  );
}

export function EpisodeDrawer({
  episodeId,
  onClose,
}: {
  episodeId: string;
  onClose: () => void;
}) {
  const tr = useUiText()

  const [detail, setDetail] = useState<EpisodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [retireReason, setRetireReason] = useState("");
  const [retireOpen, setRetireOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [chain, setChain] = useState<string[] | null>(null);
  const [chainTruncated, setChainTruncated] = useState(false);
  const [derivation, setDerivation] = useState<SubjectDerivation | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await getEpisode(episodeId);
      setDetail(d);
      setError(null);
    } catch {
      setError("episode 加载失败(可能不存在或后端不可用)");
    }
  }, [episodeId]);

  useEffect(() => {
    setChain(null);
    setChainTruncated(false);
    setDerivation(null);
    void load();
  }, [load]);

  // 因子级生命周期推导:按当前 episode 的 subject 拉链上证据聚合(读侧,
  // 建议到人眼不自动流转)。失败静默降级 —— 横幅缺席 ≠ 没有建议可给。
  useEffect(() => {
    if (!detail) return;
    let alive = true;
    (async () => {
      try {
        const d = await getSubjectDerivation(
          detail.episode.subject_type,
          detail.episode.subject_id,
        );
        if (alive) setDerivation(d);
      } catch {
        if (alive) setDerivation(null);
      }
    })();
    return () => {
      alive = false;
    };
  }, [detail]);

  // 重跑链:沿 derived_from 子→父逐跳(链失败不挡抽屉主体,静默降级);
  // 第一跳直接用已加载的 detail.edges,不重复请求起点 episode
  useEffect(() => {
    if (!detail) return;
    let alive = true;
    (async () => {
      try {
        const r = await walkDerivedChain(episodeId, async (id) => {
          if (id === episodeId) return detail.edges ?? [];
          const d = await getEpisode(id);
          return d.edges ?? [];
        });
        if (alive) {
          setChain(r.chain);
          setChainTruncated(r.truncated);
        }
      } catch {
        if (alive) setChain(null);
      }
    })();
    return () => {
      alive = false;
    };
  }, [detail, episodeId]);

  const runTransition = async (to: string, reason?: string) => {
    if (busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await transitionEpisode(episodeId, to, reason);
      setRetireOpen(false);
      setRetireReason("");
      await load(); // append-only transitions 是唯一真相,整体重拉
    } catch {
      setActionError("流转失败 — 非法流转或后端不可用");
    } finally {
      setBusy(false);
    }
  };

  const lifecycle = detail?.episode.recorded.lifecycle;
  const badge = lifecycle ? LIFECYCLE_BADGE[lifecycle.status] : undefined;
  const nexts = lifecycle ? (NEXT_TRANSITIONS[lifecycle.status] ?? []) : [];

  return (
    <div
      data-testid="episode-drawer"
      className="mt-2 rounded-lg border border-border bg-surface-1 p-3.5"
    >
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] text-ink-3">{episodeId}</span>
        {badge && (
          <span
            data-testid="episode-lifecycle-badge"
            className={`rounded border px-2 py-0.5 text-[9px] ${badge.cls}`}
          >
            {badge.label}
          </span>
        )}
        <button
          type="button"
          onClick={onClose}
          className="ml-auto text-[10px] text-ink-4 hover:text-ink-2"
          data-testid="episode-drawer-close"
        >
          {tr("收起")}
        </button>
      </div>

      {error && <p className="mt-2 text-xs text-destructive">{tr(error)}</p>}

      {/* 链上推导建议横幅:依据亮到人眼,不自动触发任何流转按钮 */}
      {derivation && derivation.suggestions.length > 0 && (
        <div
          className="mt-2 rounded border border-gold/40 bg-gold/5 px-2.5 py-1.5"
          data-testid="derivation-banner"
        >
          {derivation.suggestions.map((s) => (
            <p key={s.action} className="text-[11px] text-gold">
              {tr("链建议:")}
              {s.action === "retire" ? tr("退役") : tr("复查")}
              <span className="ml-1 text-ink-3">{s.reason}</span>
              <span className="ml-1 font-mono text-[10px] text-ink-4">
                {tr("(依据")} {s.basis_episode_id})
              </span>
            </p>
          ))}
        </div>
      )}
      {derivation?.retired_in_history && (
        <p className="mt-2 text-[10px] text-ink-4" data-testid="derivation-gray-zone">
          {tr("链中曾有已退役 episode,其后又有重测 —— 复活与否请人工判断")}
        </p>
      )}

      {detail && (
        <>
          <p className="mt-2 text-[11px] text-ink-2">
            {detail.episode.title || detail.episode.subject_id}
            <span className="ml-2 font-mono text-[10px] text-ink-4">
              {formatTs(detail.episode.occurred_at)}
            </span>
          </p>

          {detail.evidence
            .filter((e) => e.mode === "RECORDED" && e.payload)
            .slice(0, 1)
            .map((e) => (
              <ProbePayloadPanel key={e.id} evidencePayload={e.payload} />
            ))}

          {/* 流转按钮:RETIRED 必填 reason(前端拦空 + storage 兜底) */}
          {(nexts.length > 0 || lifecycle?.status !== "RETIRED") &&
            lifecycle && (
              <div className="mt-2.5 flex items-center gap-2 flex-wrap">
                {nexts.map((n) => (
                  <Button
                    key={n.to}
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    data-testid={`transition-${n.to.toLowerCase()}`}
                    onClick={() => void runTransition(n.to)}
                  >
                    {n.label}
                  </Button>
                ))}
                {lifecycle.status !== "RETIRED" && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    data-testid="transition-retired"
                    onClick={() => setRetireOpen((v) => !v)}
                  >
                    {tr("退役")}
                  </Button>
                )}
              </div>
            )}
          {retireOpen && (
            <div className="mt-2 flex items-center gap-2">
              <input
                data-testid="retire-reason-input"
                value={retireReason}
                onChange={(e) => setRetireReason(e.target.value)}
                placeholder={tr("退役原因(必填)…")}
                className="min-w-0 flex-1 rounded border border-border bg-surface-2 px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-4 focus:border-accent/50 focus:outline-none"
              />
              <Button
                size="sm"
                disabled={busy || !retireReason.trim()}
                data-testid="retire-submit"
                onClick={() => void runTransition("RETIRED", retireReason.trim())}
              >
                {tr("确认退役")}
              </Button>
            </div>
          )}
          {actionError && (
            <p className="mt-2 text-xs text-destructive" data-testid="transition-error">
              {tr(actionError)}
            </p>
          )}

          {/* transitions 事件流:append-only,唯一真相 */}
          {lifecycle && (
            <div className="mt-3 border-t border-border-soft pt-2">
              <p className="text-[10px] text-ink-3">{tr("观察周期事件")}</p>
              <ol className="mt-1.5 space-y-1">
                {lifecycle.transitions.map((t, i) => (
                  <TransitionRow key={`${t.at}-${i}`} t={t} />
                ))}
                {lifecycle.transitions.length === 0 && (
                  <li className="text-[11px] text-ink-4">{tr("尚无流转记录")}</li>
                )}
              </ol>
            </div>
          )}

          {/* 重跑链:derived_from 子→父线性列表(刀3) */}
          {chain && chain.length > 1 && (
            <div className="mt-3 border-t border-border-soft pt-2" data-testid="derived-chain">
              <p className="text-[10px] text-ink-3">{tr("重跑链")}</p>
              <p className="mt-1.5 font-mono text-[10px] leading-relaxed text-ink-2">
                {chain.map((id, i) => (
                  <span key={id}>
                    {i > 0 && <span className="text-ink-4"> → </span>}
                    {id}
                  </span>
                ))}
              </p>
              {chainTruncated && (
                <p className="mt-1 text-[10px] text-gold" data-testid="chain-truncated">
                  {tr("链已截断(超 10 跳)")}
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
