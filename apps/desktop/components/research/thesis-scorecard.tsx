"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import {
  AGGREGATE_LABELS,
  DRIFT_PATTERN_LABELS,
  PILLAR_STATUS_LABELS,
  SIGNAL_LABELS,
  type MissionEvidenceRow,
  type MissionPillar,
  type ThesisScorecard,
} from "./missions-thesis";

const AGGREGATE_TONE: Record<ThesisScorecard["aggregate"], string> = {
  strengthening: "text-pos-soft",
  intact: "text-pos-soft",
  watch: "text-warn",
  impaired: "text-warn",
  broken: "text-neg",
  changed: "text-neg",
  untested: "text-ink-3",
};

const SIGNAL_TONE: Record<keyof typeof SIGNAL_LABELS, string> = {
  strongly_confirming: "text-pos-soft",
  confirming: "text-pos-soft",
  mildly_confirming: "text-pos-soft",
  neutral: "text-ink-3",
  mildly_weakening: "text-warn",
  weakening: "text-warn",
  strongly_weakening: "text-neg",
  mixed: "text-warn",
  invalidating: "text-neg",
  untested: "text-ink-3",
};

function EvidenceRowItem({ row }: { row: MissionEvidenceRow }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-t border-border/60 pt-1.5 first:border-t-0 first:pt-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 text-left text-[11px]"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-ink-4" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-ink-4" />
        )}
        <span className={`shrink-0 ${SIGNAL_TONE[row.signal]}`}>{SIGNAL_LABELS[row.signal]}</span>
        <span className="num shrink-0 text-ink-4">{row.occurredAt}</span>
        <span className="truncate text-ink-2">{row.fact}</span>
      </button>
      {open && (
        <div className="ml-4 mt-1 flex flex-col gap-0.5 text-[10px] leading-relaxed">
          <div className="text-ink-3">
            解读:{row.interpretation}
          </div>
          <div className="text-ink-4">
            来源:{row.sourceType}
            {row.sourceRef ? ` · ${row.sourceRef}` : ""}
          </div>
          {row.followUp && <div className="text-ink-4">跟进:{row.followUp}</div>}
        </div>
      )}
    </div>
  );
}

function PillarCard({
  pillar,
  evidence,
}: {
  pillar: MissionPillar;
  evidence: MissionEvidenceRow[];
}) {
  const rows = evidence.filter((r) => r.pillarId === pillar.id);
  return (
    <div className="rounded-lg border border-border bg-surface-2 p-3">
      <div className="flex flex-wrap items-center gap-2 text-[10px]">
        <span
          className={`rounded px-1.5 py-0.5 ${
            pillar.priority === "core" ? "bg-accent/10 text-accent" : "bg-surface-3 text-ink-3"
          }`}
        >
          {pillar.priority === "core" ? "核心" : "支撑"}
        </span>
        <span className="text-ink-2">{PILLAR_STATUS_LABELS[pillar.status]}</span>
        <span className={`shrink-0 ${SIGNAL_TONE[pillar.signal]}`}>
          信号:{SIGNAL_LABELS[pillar.signal]}
        </span>
        <span className="text-ink-4">阈值溯源:{pillar.thresholdProvenance}</span>
      </div>
      <p className="mt-1 text-[12px] font-medium leading-snug text-ink">{pillar.claim}</p>
      {pillar.statusNote && (
        <p className="mt-1 text-[11px] leading-relaxed text-ink-2">{pillar.statusNote}</p>
      )}
      {pillar.nextProofPoint && (
        <p className="mt-1 text-[11px] leading-relaxed text-ink-3">
          <span className="text-ink-4">下次验证 </span>
          {pillar.nextProofPoint}
          {pillar.nextProofSource && (
            <span className="text-ink-4"> · {pillar.nextProofSource}</span>
          )}
        </p>
      )}
      {rows.length > 0 && (
        <div className="mt-2">
          <div className="text-[10px] text-ink-4">证据账本({rows.length} 行,append-only)</div>
          <div className="mt-1 flex flex-col gap-1.5">
            {rows.map((r) => (
              <EvidenceRowItem key={r.id} row={r} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function ThesisScorecard({
  scorecard,
}: {
  scorecard: ThesisScorecard;
}) {
  return (
    <div className="flex flex-col gap-2">
      {/* 聚合裁决行:一个词 + 理由句,无数字分值 */}
      <div className="rounded-lg border border-border bg-surface-2 p-3">
        <div className="text-[10px] font-medium uppercase tracking-wide text-ink-3">论点计分卡</div>
        <div className="mt-1.5 flex flex-wrap items-baseline gap-2">
          <span className={`text-[15px] font-semibold ${AGGREGATE_TONE[scorecard.aggregate]}`}>
            {AGGREGATE_LABELS[scorecard.aggregate]}
          </span>
          {scorecard.requiresOverride && (
            <span className="rounded bg-warn/10 px-1.5 py-0.5 text-[10px] text-warn">需人工复核</span>
          )}
        </div>
        <p className="mt-1 text-[11px] leading-relaxed text-ink-2">{scorecard.aggregateReason}</p>
      </div>

      {scorecard.pillars.map((p) => (
        <PillarCard key={p.id} pillar={p} evidence={scorecard.evidence} />
      ))}

      {scorecard.lastDriftCheck && (
        <div className="rounded-lg border border-border bg-surface-2 p-3">
          <div className="flex items-center gap-2 text-[10px] text-ink-3">
            <span className="font-medium uppercase tracking-wide">漂移核查</span>
            <span className="num">{scorecard.lastDriftCheck.occurredAt}</span>
            <span>by {scorecard.lastDriftCheck.checkedBy}</span>
          </div>
          <ul className="mt-1.5 flex flex-col gap-0.5">
            {(
              Object.keys(DRIFT_PATTERN_LABELS) as Array<keyof typeof DRIFT_PATTERN_LABELS>
            ).map((k) => {
              const entry = scorecard.lastDriftCheck?.patterns[k];
              if (!entry) return null;
              return (
                <li key={k} className="flex items-baseline gap-1.5 text-[11px]">
                  <span
                    className={`mt-1.5 h-1 w-1 shrink-0 rounded-full ${
                      entry.present ? "bg-warn" : "bg-ink-4"
                    }`}
                  />
                  <span className={entry.present ? "text-ink" : "text-ink-3"}>
                    {DRIFT_PATTERN_LABELS[k]}
                    {entry.note ? ` — ${entry.note}` : ""}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
