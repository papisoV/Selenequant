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
import { Loader2, Plus } from "lucide-react";
import { apiPost } from "@/lib/api-client";

// 透明性批3-10: "开新实验验证" → POST /missions 创建新调查 mission。
// 认识论纪律同 record-evidence-button:用户确认后才写入,预填可改。

export interface MissionPrefill {
  title: string;
  question: string;
  subject_type: "strategy" | "market" | "event" | "system";
  subject_id: string;
}

export function CreateMissionButton({ prefill }: { prefill: MissionPrefill }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(prefill.title);
  const [question, setQuestion] = useState(prefill.question);
  const [subjectId, setSubjectId] = useState(prefill.subject_id);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [doneId, setDoneId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setError(null);
      setDoneId(null);
    }
  }, [open]);

  const canSubmit = Boolean(title.trim() && question.trim() && subjectId.trim()) && !submitting;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await apiPost<{ mission: { id: string } }>("/api/v1/research/missions", {
        title: title.trim(),
        question: question.trim(),
        subject_type: prefill.subject_type,
        subject_id: subjectId.trim(),
      });
      setDoneId(res.mission.id);
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
        <Plus className="h-3 w-3" /> 开新实验验证
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-sm">开新实验验证</DialogTitle>
            <DialogDescription className="text-[11px]">
              为当前结果开一个调查 mission——确认后创建,后续实验完成会自动挂进它的活动流。
            </DialogDescription>
          </DialogHeader>

          {doneId ? (
            <p className="text-[12px] text-pos" data-testid="mission-created">
              已创建 mission({doneId})。可在 Research → Today 或 mission 详情查看。
            </p>
          ) : (
            <div className="space-y-3 text-[12px]">
              <label className="block space-y-1">
                <span className="text-muted-foreground">标题</span>
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  className="h-7 w-full rounded-lg border border-border-soft bg-surface px-2"
                  data-testid="mission-title-input"
                />
              </label>

              <label className="block space-y-1">
                <span className="text-muted-foreground">研究问题</span>
                <textarea
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  rows={2}
                  className="w-full rounded-lg border border-border-soft bg-surface px-2 py-1"
                  data-testid="mission-question-input"
                />
              </label>

              <label className="block space-y-1">
                <span className="text-muted-foreground">
                  对象({prefill.subject_type})
                </span>
                <input
                  value={subjectId}
                  onChange={(e) => setSubjectId(e.target.value)}
                  className="h-7 w-full rounded-lg border border-border-soft bg-surface px-2"
                  data-testid="mission-subject-input"
                />
              </label>

              {error && <p className="text-[11px] text-destructive">{error}</p>}
            </div>
          )}

          <DialogFooter>
            {doneId ? (
              <Button size="sm" className="h-7 rounded-lg text-[11px]" onClick={() => setOpen(false)}>
                关闭
              </Button>
            ) : (
              <>
                <Button size="sm" variant="ghost" className="h-7 rounded-lg text-[11px]" onClick={() => setOpen(false)}>
                  取消
                </Button>
                <Button
                  size="sm"
                  className="h-7 rounded-lg text-[11px]"
                  disabled={!canSubmit}
                  onClick={submit}
                  data-testid="mission-submit"
                >
                  {submitting ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
                  确认创建
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
