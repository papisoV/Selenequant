"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Loader2, NotebookPen } from "lucide-react";
import { api, apiPost } from "@/lib/api-client";
import { useUiText } from "@/components/i18n/use-ui-text"

// 透明性批2-8: 结果页 → "记入 evidence" 预填按钮。
// 认识论纪律:append 永远是用户确认后发生,不是静默自动写。
// 预填只暴露 fact + signal 两字段,其余由本组件默认填齐。

interface MissionLite {
  id: string;
  title: string;
  subject_type: string;
  subject_id: string;
}

interface ThesisPayload {
  pillars: { id: string; claim: string }[];
}

export interface EvidencePrefill {
  fact: string;
  /** 按结果正负映射十档信号,用户可改 */
  suggestedSignal: string;
  sourceRef?: string;
}

const SIGNALS = [
  "strongly_confirming",
  "confirming",
  "mildly_confirming",
  "neutral",
  "mildly_weakening",
  "weakening",
  "strongly_weakening",
  "mixed",
  "invalidating",
  "untested",
] as const;

export function RecordEvidenceButton({
  prefill,
}: {
  prefill: EvidencePrefill;
}) {
  const tr = useUiText()

  const [open, setOpen] = useState(false);
  const [missions, setMissions] = useState<MissionLite[] | null>(null);
  const [missionId, setMissionId] = useState<string>("");
  const [pillars, setPillars] = useState<{ id: string; claim: string }[]>([]);
  const [pillarId, setPillarId] = useState<string>("");
  const [fact, setFact] = useState(prefill.fact);
  const [signal, setSignal] = useState<string>(prefill.suggestedSignal);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    if (!open || missions !== null) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await api<{ missions: MissionLite[] }>(
          "/api/v1/research/missions?phase=investigation&limit=50"
        );
        if (!cancelled) {
          setMissions(data.missions ?? []);
          if (data.missions?.length) setMissionId(data.missions[0].id);
        }
      } catch {
        if (!cancelled) setMissions([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, missions]);

  useEffect(() => {
    if (!open || !missionId) {
      setPillars([]);
      setPillarId("");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const data = await api<ThesisPayload>(
          `/api/v1/research/missions/${encodeURIComponent(missionId)}/thesis`
        );
        if (!cancelled) {
          const ps = data.pillars ?? [];
          setPillars(ps);
          if (ps.length) setPillarId(ps[0].id);
        }
      } catch {
        if (!cancelled) setPillars([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, missionId]);

  useEffect(() => {
    if (!open) {
      setError(null);
      setDone(null);
    }
  }, [open]);

  const canSubmit = Boolean(missionId && pillarId && fact.trim()) && !submitting;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await apiPost<{ evidence_id: string }>(
        `/api/v1/research/missions/${encodeURIComponent(missionId)}/evidence`,
        {
          pillar_id: pillarId,
          occurred_at: new Date().toISOString(),
          source_type: "experiment",
          fact: fact.trim(),
          interpretation: fact.trim(),
          signal,
          source_ref: prefill.sourceRef ?? null,
          owner: "user",
        }
      );
      setDone(res.evidence_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        className="h-6 gap-1 rounded-lg text-[10px]"
        onClick={() => setOpen(true)}
      >
        <NotebookPen className="h-3 w-3" /> {tr("记入 evidence")}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-sm">{tr("记入 evidence")}</DialogTitle>
            <DialogDescription className="text-[11px]">
              {tr("把这次实验结果作为证据挂到一个调查中的 mission——确认后才写入。")}
            </DialogDescription>
          </DialogHeader>

          {done ? (
            <p className="text-[12px] text-pos" data-testid="evidence-recorded">
              {tr("已写入 evidence(")}{done}{tr(")。可在 mission 详情的 Thesis 区查看。")}
            </p>
          ) : missions !== null && missions.length === 0 ? (
            <p className="text-[12px] text-muted-foreground">
              {tr("当前没有调查中的 mission。先创建一个 mission 再来记录。")}
            </p>
          ) : (
            <div className="space-y-3 text-[12px]">
              <label className="block space-y-1">
                <span className="text-muted-foreground">{tr("目标 mission")}</span>
                <select
                  value={missionId}
                  onChange={(e) => setMissionId(e.target.value)}
                  className="h-7 w-full rounded-lg border border-border-soft bg-surface px-2"
                  data-testid="evidence-mission-select"
                >
                  {(missions ?? []).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.title}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block space-y-1">
                <span className="text-muted-foreground">{tr("支柱(thesis pillar)")}</span>
                {pillars.length === 0 ? (
                  <span className="block text-[11px] text-muted-foreground">
                    {tr("该 mission 暂无支柱 — 去详情页先建 pillar。")}
                  </span>
                ) : (
                  <select
                    value={pillarId}
                    onChange={(e) => setPillarId(e.target.value)}
                    className="h-7 w-full rounded-lg border border-border-soft bg-surface px-2"
                    data-testid="evidence-pillar-select"
                  >
                    {pillars.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.claim}
                      </option>
                    ))}
                  </select>
                )}
              </label>

              <label className="block space-y-1">
                <span className="text-muted-foreground">{tr("事实(fact)")}</span>
                <textarea
                  value={fact}
                  onChange={(e) => setFact(e.target.value)}
                  rows={2}
                  className="w-full rounded-lg border border-border-soft bg-surface px-2 py-1"
                  data-testid="evidence-fact-input"
                />
              </label>

              <label className="block space-y-1">
                <span className="text-muted-foreground">{tr("信号(signal)")}</span>
                <select
                  value={signal}
                  onChange={(e) => setSignal(e.target.value)}
                  className="h-7 w-full rounded-lg border border-border-soft bg-surface px-2"
                  data-testid="evidence-signal-select"
                >
                  {SIGNALS.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>

              {error && <p className="text-[11px] text-destructive">{error}</p>}
            </div>
          )}

          <DialogFooter>
            {done ? (
              <Button size="sm" className="h-7 rounded-lg text-[11px]" onClick={() => setOpen(false)}>
                {tr("关闭")}
              </Button>
            ) : missions !== null && missions.length === 0 ? null : (
              <>
                <Button size="sm" variant="ghost" className="h-7 rounded-lg text-[11px]" onClick={() => setOpen(false)}>
                  {tr("取消")}
                </Button>
                <Button
                  size="sm"
                  className="h-7 rounded-lg text-[11px]"
                  disabled={!canSubmit || pillars.length === 0}
                  onClick={submit}
                  data-testid="evidence-submit"
                >
                  {submitting ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
                  {tr("确认写入")}
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
