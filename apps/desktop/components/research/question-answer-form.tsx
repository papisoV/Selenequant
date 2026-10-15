"use client";

/**
 * 回答出口 —— 写下一版回答 + 从账本引用(T5)。
 *
 * 引用纪律:候选唯一来源是 recallLedger 的响应,组件不提供自由 id 输入
 * (cited 焊点在 HTTP 层,但 UI 层也不给用户抄错 id 的机会)。
 * resolve 前:提交禁用且 cited 为空 —— 断言锚点。
 */

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { addRevision, recallLedger, type RecallCandidate } from "@/lib/api/research-graph";
import { localDateToIso } from "@/lib/research/date-iso";

const PENDING = "正在取回历史…";

/** ISO 时刻 → MM-DD(回放标注用;null/空 → "未知",不伪造日期)。 */
function fmtDay(iso: string | null | undefined): string {
  if (!iso) return "未知";
  return iso.slice(5, 10); // "2026-09-26T..." → "09-26"
}

// 溯源链节点的前缀 → 人话(evidence_chain 形如 ["revision:rev-x","evidence:ev-y","episode:ep-z"])
const CHAIN_LABEL: Record<string, string> = {
  revision: "回答",
  hypothesis: "假设",
  evidence: "证据",
  episode: "实跑段",
};

function EvidenceChain({ chain }: { chain: string[] }) {
  const [open, setOpen] = useState(false);
  // 空链 = 纯记录(走不到 Episode/Evidence)——显示降级说明而非空白
  if (chain.length === 0) {
    return (
      <span className="text-[10px] text-ink-4" data-testid="evidence-chain-empty">
        无实证链 — 记录
      </span>
    );
  }
  return (
    <span className="text-[10px]">
      <button
        type="button"
        data-testid="evidence-chain-toggle"
        onClick={() => setOpen((v) => !v)}
        className="text-accent hover:underline"
      >
        溯源 {open ? "▾" : "▸"} {chain.length} 跳
      </button>
      {open && (
        <span className="mt-0.5 flex flex-wrap items-center gap-1" data-testid="evidence-chain-path">
          {chain.map((hop, i) => {
            const [kind, id] = hop.split(":", 1) as [string, string];
            const rest = hop.slice(hop.indexOf(":") + 1);
            return (
              <span key={i} className="flex items-center gap-1">
                {i > 0 && <span className="text-ink-4">→</span>}
                <span className="rounded border border-border bg-surface-3 px-1 py-px font-mono text-ink-3">
                  {CHAIN_LABEL[kind] ?? kind}:{rest.slice(0, 10)}
                </span>
              </span>
            );
          })}
        </span>
      )}
    </span>
  );
}

export function QuestionAnswerForm({
  questionId,
  onSubmitted,
}: {
  questionId: string;
  onSubmitted: () => void;
}) {
  const [content, setContent] = useState("");
  const [candidates, setCandidates] = useState<RecallCandidate[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // v025 horizon 二分:structural=长期有效的判断 / tactical=短期观察性结论。
  // 默认 structural(保守 — 不确定时按长期口径入库)
  const [horizon, setHorizon] = useState<"structural" | "tactical">(
    "structural",
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 账本回放(N1):选中的回放日(YYYY-MM-DD,空 = 默认视图)。开启后召回集
  // 缩到"那天账本已知的东西"—— 认识论语义,不是"数据窗覆盖"。
  // 默认关闭:关闭时不传 as_of,请求与今天同形(硬约束)。
  const [replayDate, setReplayDate] = useState("");

  useEffect(() => {
    let alive = true;
    setCandidates(null);
    setSelected(new Set());
    // 回放日非空才带 as_of —— 省略键而非传 undefined,保持请求体与默认路径同形
    recallLedger(
      replayDate
        ? { question_id: questionId, as_of: localDateToIso(replayDate) }
        : { question_id: questionId },
    )
      .then((r) => {
        if (alive) setCandidates(r?.candidates ?? []);
      })
      .catch(() => {
        if (alive) setCandidates([]);
      });
    return () => {
      alive = false;
    };
  }, [questionId, replayDate]);

  const settled = candidates !== null;
  const canSubmit = settled && content.trim().length > 0 && !submitting;

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await addRevision(questionId, {
        content: content.trim(),
        cited: [...selected],
        horizon,
      });
      setContent("");
      setSelected(new Set());
      onSubmitted();
    } catch {
      setError("提交失败 — 确认后端可用后重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div id="next-answer" className="mt-4 rounded border border-border bg-surface-2 p-3">
      <p className="text-[11px] text-ink-3">写下一版回答</p>
      <textarea
        aria-label="回答内容"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={4}
        className="mt-2 w-full rounded border border-border bg-surface p-2 text-xs text-ink-2 outline-none focus:border-accent"
      />

      <div className="mt-3" data-testid="recall-ledger">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-[11px] text-ink-3">
            之前的回答与假设(自动列出 — 勾选本版要引用的)
          </p>
          {replayDate ? (
            <span
              data-testid="replay-bar"
              className="flex items-center gap-1.5 rounded border border-accent/40 bg-accent/5 px-1.5 py-0.5 text-[10px] text-accent"
            >
              回放 {replayDate}
              <button
                type="button"
                data-testid="replay-clear"
                onClick={() => setReplayDate("")}
                className="text-ink-3 hover:text-ink"
              >
                ✕
              </button>
            </span>
          ) : (
            <label className="flex items-center gap-1 text-[10px] text-ink-4">
              <input
                type="date"
                data-testid="replay-date"
                value={replayDate}
                onChange={(e) => setReplayDate(e.target.value)}
                className="rounded border border-border bg-surface-1 px-1.5 py-0.5 text-[10px] text-ink focus:border-accent/50 focus:outline-none"
              />
              回放那天的账本
            </label>
          )}
        </div>
        <div className="mt-1 space-y-1">
          {!settled ? (
            <p className="text-xs text-ink-3">{PENDING}</p>
          ) : candidates.length === 0 ? (
            <p className="text-[11px] text-ink-3" data-testid="replay-empty">
              {replayDate
                ? "回放日之前账本无记录 — 这段历史当时还不存在"
                : "本次未取回历史 — 第一次回答无需引用"}
            </p>
          ) : (
            candidates.map((c) => (
              <div key={c.id} data-testid="ledger-item">
                <label className="flex cursor-pointer items-center gap-2 text-xs text-ink-2">
                  <input
                    type="checkbox"
                    checked={selected.has(c.id)}
                    onChange={(e) => {
                      const next = new Set(selected);
                      if (e.target.checked) next.add(c.id);
                      else next.delete(c.id);
                      setSelected(next);
                    }}
                  />
                  <span>{c.text}</span>
                  <span className="text-[11px] text-ink-3">
                    ({c.kind === "revision"
                      ? "回答"
                      : c.kind === "evidence"
                        ? "证据"
                        : "假设"}{" "}
                    · {c.recall_class === "learned_experience" ? "经验" : "记录"})
                  </span>
                  {c.horizon === "tactical" && (
                    <span
                      data-testid="horizon-tactical-badge"
                      className="rounded border border-border bg-surface-3 px-1.5 py-0.5 text-[9px] text-ink-3"
                    >
                      短期
                    </span>
                  )}
                </label>
                {replayDate && c.data_window && (
                  <p
                    className="ml-5 text-[10px] text-ink-4"
                    data-testid="data-window-label"
                  >
                    数据窗 {fmtDay(c.data_window.start)} ~ {fmtDay(c.data_window.end)}
                  </p>
                )}
                {replayDate && (
                  <p className="ml-5 text-[10px] text-ink-4">
                    记录于 {fmtDay(c.created_at)}
                  </p>
                )}
                <div className="ml-5 mt-0.5">
                  <EvidenceChain chain={c.evidence_chain} />
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}

      <div className="mt-3 flex items-center gap-3 flex-wrap">
        <Button
          size="sm"
          disabled={!canSubmit}
          onClick={submit}
          data-testid="submit-revision"
        >
          {submitting ? "提交中…" : "写下这一版"}
        </Button>
        {/* v025 horizon 二段选择:这版判断是长期有效还是短期观察。默认长期 */}
        <div
          className="flex items-center gap-1"
          role="radiogroup"
          aria-label="判断时效"
          data-testid="horizon-picker"
        >
          <span className="text-[10px] text-ink-4">判断时效</span>
          {(
            [
              { value: "structural", label: "长期" },
              { value: "tactical", label: "短期" },
            ] as const
          ).map((opt) => (
            <button
              key={opt.value}
              type="button"
              role="radio"
              aria-checked={horizon === opt.value}
              data-testid={`horizon-${opt.value}`}
              onClick={() => setHorizon(opt.value)}
              className={`rounded border px-2 py-0.5 text-[10px] ${
                horizon === opt.value
                  ? "border-accent/40 bg-accent/5 text-accent"
                  : "border-border bg-surface-3 text-ink-3 hover:border-accent/40"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
