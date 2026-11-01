"use client";

import { evidenceLineText, type StrategyEvidence } from "@/lib/hooks/use-strategy-evidence";
import { useUiText } from "@/components/i18n/use-ui-text";

/** 策略卡证据行 — 军规②:真数据/未验证两态,无第四种。gold=selene 自己的证据(先例:QD 对照卡 selene 列)。 */
export function EvidenceLine({ evidence }: { evidence: StrategyEvidence | null | undefined }) {
  const tr = useUiText();
  const hasEvidence = !!evidence && evidence.count > 0;
  return (
    <div
      className={`mt-1 font-mono text-[9px] ${hasEvidence ? "text-gold" : "text-ink-4"}`}
      data-testid="evidence-line"
    >
      {evidenceLineText(evidence, tr)}
    </div>
  );
}
