import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  AGGREGATE_LABELS,
  SIGNAL_LABELS,
  toThesisScorecard,
  type Signal,
  type ThesisScorecardRaw,
} from "./missions-thesis";
import { THESIS_KELTNER_MOCK } from "./missions-thesis-mock";
import { ThesisScorecard } from "./thesis-scorecard";

const SIGNAL_CHINESE = new Set(Object.values(SIGNAL_LABELS));
const AGGREGATE_CHINESE = new Set(Object.values(AGGREGATE_LABELS));

describe("ThesisScorecard", () => {
  it("aggregate 文本 ∈ 八档聚合词集合(一个词+理由句,无数字分值)", () => {
    render(<ThesisScorecard scorecard={THESIS_KELTNER_MOCK} />);
    const el = screen.getByText(THESIS_KELTNER_MOCK.aggregateReason)
      .previousElementSibling?.querySelector("span");
    expect(el).not.toBeNull();
    expect(AGGREGATE_CHINESE.has(el!.textContent!)).toBe(true);
  });

  it("每个 signal 徽章文本 ∈ 十档中文映射值集", () => {
    render(<ThesisScorecard scorecard={THESIS_KELTNER_MOCK} />);
    for (const signal of THESIS_KELTNER_MOCK.pillars.map((p) => p.signal)) {
      const badges = screen.getAllByText(SIGNAL_LABELS[signal as Signal]);
      expect(badges.length).toBeGreaterThan(0);
      expect(SIGNAL_CHINESE.has(SIGNAL_LABELS[signal as Signal])).toBe(true);
    }
  });

  it("不出现任何合成评分(score/总分)", () => {
    const { container } = render(<ThesisScorecard scorecard={THESIS_KELTNER_MOCK} />);
    expect(container.textContent).not.toMatch(/score|总分/);
  });

  it("toThesisScorecard: snake_case 响应 → camelCase,过滤 superseded/retired 支柱", () => {
    const raw: ThesisScorecardRaw = {
      mission_id: "mis-x01",
      pillars: [
        {
          id: "pil-1",
          claim: "c1",
          priority: "core",
          baseline: null,
          expected_path: null,
          confirm_threshold: null,
          warning_threshold: null,
          break_threshold: null,
          threshold_provenance: "draft",
          status: "intact",
          signal: "confirming",
          status_note: null,
          superseded_by: null,
          next_proof_point: null,
          next_proof_source: null,
        },
        {
          id: "pil-2",
          claim: "c2",
          priority: "supporting",
          baseline: null,
          expected_path: null,
          confirm_threshold: null,
          warning_threshold: null,
          break_threshold: null,
          threshold_provenance: "draft",
          status: "retired",
          signal: "untested",
          status_note: null,
          superseded_by: "pil-1",
          next_proof_point: null,
          next_proof_source: null,
        },
      ],
      aggregate: "intact",
      aggregate_reason: "活跃支柱状态:intact×1",
      requires_override: false,
      signal_distribution: { confirming: 1 },
      evidence_count: 1,
      evidence: [
        {
          id: "ev-1",
          pillar_id: "pil-1",
          occurred_at: "2026-09-23T00:00:00+00:00",
          source_type: "backtest",
          source_ref: "bt-x",
          fact: "f",
          interpretation: "i",
          signal: "confirming",
          follow_up: null,
        },
      ],
      last_drift_check: {
        occurred_at: "2026-09-23T01:00:00+00:00",
        kind: "drift_check",
        detail: {
          patterns: {
            reason_changed: { present: false },
            catalyst_laundering: { present: false },
            valuation_substitution: { present: false },
            dismissal_by_horizon: { present: false },
            protection_as_reason: { present: false },
          },
          checked_by: "user",
        },
      },
    };
    const card = toThesisScorecard(raw);
    expect(card).not.toBeNull();
    expect(card!.missionId).toBe("mis-x01");
    expect(card!.aggregateReason).toBe("活跃支柱状态:intact×1");
    expect(card!.requiresOverride).toBe(false);
    expect(card!.pillars.map((p) => p.id)).toEqual(["pil-1"]);
    expect(card!.evidence[0].pillarId).toBe("pil-1");
    expect(card!.lastDriftCheck!.checkedBy).toBe("user");
    expect(card!.lastDriftCheck!.occurredAt).toBe("2026-09-23T01:00:00+00:00");
  });

  it("next_proof_point 渲染可见(此前数据存了但 UI 完全不显示)", () => {
    const { container } = render(<ThesisScorecard scorecard={THESIS_KELTNER_MOCK} />);
    for (const p of THESIS_KELTNER_MOCK.pillars) {
      if (!p.nextProofPoint) continue;
      expect(container.textContent).toContain(p.nextProofPoint);
      if (p.nextProofSource) {
        expect(container.textContent).toContain(p.nextProofSource);
      }
    }
    expect(container.textContent).toContain("下次验证");
  });

  it("无 nextProofPoint 的支柱不渲染该行", () => {
    const bare: typeof THESIS_KELTNER_MOCK = {
      ...THESIS_KELTNER_MOCK,
      pillars: THESIS_KELTNER_MOCK.pillars.map((p) => ({
        ...p,
        nextProofPoint: null,
        nextProofSource: null,
      })),
    };
    const { container } = render(<ThesisScorecard scorecard={bare} />);
    expect(container.textContent).not.toContain("下次验证");
  });

  it("toThesisScorecard: null/空响应 → null", () => {
    expect(toThesisScorecard(null)).toBeNull();
    expect(toThesisScorecard({ mission_id: "", pillars: [], aggregate: "untested", aggregate_reason: "", requires_override: false, signal_distribution: {}, evidence_count: 0, evidence: [], last_drift_check: null })).toBeNull();
  });
});
