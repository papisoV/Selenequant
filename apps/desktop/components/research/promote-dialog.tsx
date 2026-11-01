"use client";

/**
 * 「立为支柱」对话框 —— question → pillar 桥的前端(计划 Phase 4)。
 *
 * 两步 confirm-submit:打开时 GET promote-draft 预填,G 用户改完 POST promote。
 * 取消 = 零写入。
 *
 * 三处刻意的设计:
 * 1. claim 字段打开即标红 —— 预填是"问题标题去问号"版,必然是问句不是可证伪
 *    陈述;等 422 才知道会让人白填一遍。
 * 2. 已有活跃支柱的红字警告 + 内联退役按钮 —— 否则旧败支柱会静默拖累
 *    mission verdict(aggregate 取活跃中最高 severity)。
 * 3. subject 未映射时必须手选 —— 永不静默默认。
 */

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  getPromoteDraft,
  promoteRevision,
  type PromoteDraft,
} from "@/lib/api/research-graph";
import { useUiText } from "@/components/i18n/use-ui-text"

const SUBJECT_TYPES = ["strategy", "market", "event", "system"] as const;

export function PromoteDialog({
  questionId,
  revisionSeq,
  onClose,
  onPromoted,
}: {
  questionId: string;
  revisionSeq: number;
  onClose: () => void;
  onPromoted: (result: { mission_id: string; pillar_id: string; reused: boolean }) => void;
}) {
  const tr = useUiText()

  const [draft, setDraft] = useState<PromoteDraft | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [claim, setClaim] = useState("");
  const [subjectType, setSubjectType] = useState("");
  const [subjectId, setSubjectId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [retiring, setRetiring] = useState<string | null>(null);

  // 预填:打开即取草稿。subject override 变化时重取(让 existing_pillars 警告
  // 跟着真实复用的 mission 刷新)。
  useEffect(() => {
    let cancelled = false;
    getPromoteDraft(questionId, {
      seq: revisionSeq,
      subject_type: subjectType || undefined,
      subject_id: subjectId || undefined,
    })
      .then((d) => {
        if (cancelled) return;
        setDraft(d);
        // 首次预填 claim;subject 变化的重取不覆盖用户已改的 claim
        setClaim((prev) => prev || d.pillar.claim);
        if (!subjectType && d.mission.subject_type) setSubjectType(d.mission.subject_type);
        if (!subjectId && d.mission.subject_id) setSubjectId(d.mission.subject_id);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e?.message ?? "草稿加载失败");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [questionId, revisionSeq, subjectType, subjectId]);

  const claimUnchanged = draft != null && claim.trim() === draft.pillar.claim.trim();
  const canSubmit =
    !submitting && claim.trim().length > 0 && !!subjectType && subjectId.trim().length > 0;

  async function submit() {
    if (!canSubmit || !draft) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const res = await promoteRevision(questionId, {
        mission: {
          title: draft.mission.title,
          question: draft.mission.question,
          subject_type: subjectType,
          subject_id: subjectId.trim(),
        },
        pillar: {
          claim: claim.trim(),
          priority: draft.pillar.priority,
          baseline: draft.pillar.baseline,
          break_threshold: draft.pillar.break_threshold,
          next_proof_point: draft.pillar.next_proof_point,
          next_proof_source: draft.pillar.next_proof_source,
        },
        revision_seq: revisionSeq,
      });
      onPromoted(res);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "提交失败";
      setSubmitError(msg);
      setSubmitting(false);
    }
  }

  async function retire(pillarId: string) {
    if (!draft?.existing_mission_id) return;
    setRetiring(pillarId);
    try {
      await fetch(
        `/api/v1/research/missions/${encodeURIComponent(draft.existing_mission_id)}/pillars/${encodeURIComponent(pillarId)}/status`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            status: "retired",
            status_note: `被 revision v${revisionSeq} 的结论取代`,
          }),
        },
      );
      // 重取草稿,活跃支柱列表随之刷新
      setDraft(null);
      setSubjectType((v) => v); // 触发 effect 重跑
    } finally {
      setRetiring(null);
    }
  }

  const fieldCls =
    "w-full rounded border border-border bg-surface-1 px-2.5 py-1.5 text-xs text-ink placeholder:text-ink-4 focus:border-accent/50 focus:outline-none";

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl" data-testid="promote-dialog">
        <DialogHeader>
          <DialogTitle className="text-sm">{tr("立为支柱")}</DialogTitle>
        </DialogHeader>

        {loadError && (
          <p className="text-xs text-neg" data-testid="promote-load-error">
            {tr(loadError)}
          </p>
        )}

        {!draft && !loadError && (
          <p className="text-xs text-ink-3">{tr("加载草稿…")}</p>
        )}

        {draft && (
          <div className="flex flex-col gap-3">
            {/* 段落识别情况 —— 手写 revision 无五段时明示,不虚报 */}
            <p className="text-[10px] text-ink-4" data-testid="promote-segments">
              {tr("识别到")} {draft.segments_found.length}{tr("/3 段")}
              {draft.segments_found.length === 0 && tr("(手写 revision,字段需手填)")}
            </p>

            {/* 已有活跃支柱警告(Architect 必修①) */}
            {draft.existing_pillars.length > 0 && (
              <div
                className="rounded border border-warn/30 bg-warn/5 px-3 py-2"
                data-testid="promote-existing-warning"
              >
                <p className="text-[11px] text-warn">
                  {tr("该 mission(")}{draft.existing_mission_id}{tr(")已有")}{" "}
                  {draft.existing_pillars.length} {tr("根活跃支柱,新结论是否推翻它们?")}
                </p>
                <ul className="mt-1.5 space-y-1">
                  {draft.existing_pillars.map((p) => (
                    <li key={p.id} className="flex items-center gap-2 text-[10px]">
                      <span className="text-ink-4">[{p.status}]</span>
                      <span className="min-w-0 flex-1 truncate text-ink-2">{p.claim}</span>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={retiring === p.id}
                        data-testid={`retire-${p.id}`}
                        onClick={() => retire(p.id)}
                      >
                        {retiring === p.id ? tr("退役中…") : tr("退役")}
                      </Button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* warnings:claim 预标 + 主体未映射 + 无五段 */}
            {draft.warnings.length > 0 && (
              <ul className="flex flex-col gap-0.5" data-testid="promote-warnings">
                {draft.warnings.map((w, i) => (
                  <li key={i} className="text-[10px] text-ink-3">
                    · {w}
                  </li>
                ))}
              </ul>
            )}

            <label className="flex flex-col gap-1">
              <span className="text-[10px] text-ink-3">
                {tr("claim(可证伪陈述;不能是问题标题的问号剥离版)")}
              </span>
              <textarea
                data-testid="promote-claim"
                value={claim}
                onChange={(e) => setClaim(e.target.value)}
                rows={3}
                className={`${fieldCls} ${claimUnchanged ? "border-warn/50" : ""}`}
              />
              {claimUnchanged && (
                <span className="text-[10px] text-warn" data-testid="promote-claim-hint">
                  {tr("仍是问题标题的剥离版 — 请改写为 revision 的结论句,否则提交会被拒绝")}
                </span>
              )}
            </label>

            <div className="flex items-end gap-2">
              <label className="flex flex-col gap-1">
                <span className="text-[10px] text-ink-3">{tr("主体类型")}</span>
                <select
                  data-testid="promote-subject-type"
                  value={subjectType}
                  onChange={(e) => setSubjectType(e.target.value)}
                  className={fieldCls}
                >
                  <option value="">{tr("— 选择 —")}</option>
                  {SUBJECT_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex min-w-0 flex-1 flex-col gap-1">
                <span className="text-[10px] text-ink-3">{tr("主体 ID")}</span>
                <input
                  data-testid="promote-subject-id"
                  value={subjectId}
                  onChange={(e) => setSubjectId(e.target.value)}
                  placeholder={tr("如 strat-6395c312")}
                  className={fieldCls}
                />
              </label>
            </div>
            <p className="text-[10px] text-ink-4" data-testid="promote-subject-reason">
              {draft.subject_reason}
            </p>

            {draft.pillar.break_threshold && (
              <div className="text-[10px] text-ink-4" data-testid="promote-break-threshold">
                <span className="text-ink-3">{tr("证伪条件(自 revision 提取):")}</span>{" "}
                {draft.pillar.break_threshold}
              </div>
            )}

            {submitError && (
              <p className="text-[11px] text-neg" data-testid="promote-submit-error">
                {submitError}
              </p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button size="sm" variant="ghost" onClick={onClose} data-testid="promote-cancel">
            {tr("取消")}
          </Button>
          <Button
            size="sm"
            disabled={!canSubmit}
            data-testid="promote-submit"
            onClick={submit}
          >
            {submitting ? tr("提升中…") : tr("立为支柱")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
