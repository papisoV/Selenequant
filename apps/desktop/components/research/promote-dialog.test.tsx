import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { PromoteDialog } from "@/components/research/promote-dialog";
import {
  getPromoteDraft,
  promoteRevision,
  type PromoteDraft,
} from "@/lib/api/research-graph";

vi.mock("@/lib/api/research-graph", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/api/research-graph")
  >("@/lib/api/research-graph");
  return {
    ...actual,
    getPromoteDraft: vi.fn(),
    promoteRevision: vi.fn(),
  };
});

const mockGetDraft = vi.mocked(getPromoteDraft);
const mockPromote = vi.mocked(promoteRevision);

function draft(overrides: Partial<PromoteDraft> = {}): PromoteDraft {
  return {
    mission: {
      title: "BTC 1h funding 延迟衰减",
      question: "背景",
      subject_type: "strategy",
      subject_id: "entry-delay-ic-probe",
    },
    pillar: {
      claim: "BTC 1h funding 延迟衰减:half_delay 在哪一档",
      priority: "core",
      baseline: "d0=-0.0058",
      break_threshold: "|d1|/|d0|≥0.5 则翻案",
      next_proof_point: "算 block bootstrap 分布",
      next_proof_source: "rev-x 新问题1",
      model_line: "council v1 rev-x",
      provenance: "draft",
      threshold_provenance: "draft",
    },
    subject_suggestion: { subject_type: "strategy", subject_id: "entry-delay-ic-probe" },
    subject_reason: "取自 anchor.strategy_ids[0]",
    segments_found: ["时间线", "反向检验", "新问题"],
    revision_id: "rev-x",
    revision_seq: 1,
    warnings: ["claim 目前是问题标题的问号剥离版,不是可证伪陈述 — 提交会被拒绝;请改写为 revision 结论句"],
    existing_mission_id: null,
    existing_pillars: [],
    ...overrides,
  };
}

const noop = () => {};

describe("PromoteDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("打开即预填 claim 与 subject,并标红未改写的 claim", async () => {
    mockGetDraft.mockResolvedValue(draft());
    render(<PromoteDialog questionId="q-1" revisionSeq={1} onClose={noop} onPromoted={noop} />);

    const claim = await screen.findByTestId("promote-claim");
    await waitFor(() => expect((claim as HTMLTextAreaElement).value).toContain("half_delay"));
    // 未改写 → 显示标红提示
    expect(screen.getByTestId("promote-claim-hint")).toBeInTheDocument();
    // subject 预填
    expect(screen.getByTestId("promote-subject-id")).toHaveValue("entry-delay-ic-probe");
    // 段落识别明示
    expect(screen.getByTestId("promote-segments")).toHaveTextContent("3/3");
    // 证伪条件展示
    expect(screen.getByTestId("promote-break-threshold")).toHaveTextContent("|d1|/|d0|≥0.5");
  });

  it("claim 改写后标红提示消失", async () => {
    mockGetDraft.mockResolvedValue(draft());
    render(<PromoteDialog questionId="q-1" revisionSeq={1} onClose={noop} onPromoted={noop} />);
    const claim = await screen.findByTestId("promote-claim");
    fireEvent.change(claim, { target: { value: "BTC 1h 上 half_delay=1,但仅条件性成立。" } });
    expect(screen.queryByTestId("promote-claim-hint")).not.toBeInTheDocument();
  });

  it("有活跃支柱时渲染警告并可退役", async () => {
    mockGetDraft.mockResolvedValue(
      draft({
        existing_mission_id: "mis-old",
        existing_pillars: [
          { id: "pil-old", claim: "旧结论:half_delay=5", status: "untested", priority: "core" },
        ],
      }),
    );
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
    render(<PromoteDialog questionId="q-1" revisionSeq={1} onClose={noop} onPromoted={noop} />);

    const warn = await screen.findByTestId("promote-existing-warning");
    expect(warn).toHaveTextContent("已有 1 根活跃支柱");
    expect(warn).toHaveTextContent("旧结论:half_delay=5");

    fireEvent.click(screen.getByTestId("retire-pil-old"));
    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining("/missions/mis-old/pillars/pil-old/status"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
    fetchSpy.mockRestore();
  });

  it("subject 为空时提交被禁", async () => {
    mockGetDraft.mockResolvedValue(draft({ subject_suggestion: null, subject_reason: "anchor 无可映射键" }));
    render(<PromoteDialog questionId="q-1" revisionSeq={1} onClose={noop} onPromoted={noop} />);
    // 等草稿加载(elided:subject_suggestion null 时 mission.subject_* 仍是 strategy?
    // 草稿里 mission.subject_type 为 null 才禁;此处显式清空)
    const sel = await screen.findByTestId("promote-subject-type");
    fireEvent.change(sel, { target: { value: "" } });
    await waitFor(() => expect(screen.getByTestId("promote-submit")).toBeDisabled());
  });

  it("提交成功回调带 mission/pillar id", async () => {
    mockGetDraft.mockResolvedValue(draft());
    mockPromote.mockResolvedValue({
      mission_id: "mis-new",
      pillar_id: "pil-new",
      reused: false,
      edge_written: true,
      revision_id: "rev-x",
    });
    const onPromoted = vi.fn();
    render(
      <PromoteDialog questionId="q-1" revisionSeq={1} onClose={noop} onPromoted={onPromoted} />,
    );
    const claim = await screen.findByTestId("promote-claim");
    fireEvent.change(claim, { target: { value: "改写过的可证伪陈述。" } });

    await waitFor(() => expect(screen.getByTestId("promote-submit")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("promote-submit"));
    await waitFor(() =>
      expect(onPromoted).toHaveBeenCalledWith(
        expect.objectContaining({ mission_id: "mis-new", pillar_id: "pil-new" }),
      ),
    );
  });

  it("提交失败显示错误,不关闭", async () => {
    mockGetDraft.mockResolvedValue(draft());
    mockPromote.mockRejectedValue(new Error("claim 须是可证伪陈述"));
    render(<PromoteDialog questionId="q-1" revisionSeq={1} onClose={noop} onPromoted={noop} />);
    const claim = await screen.findByTestId("promote-claim");
    fireEvent.change(claim, { target: { value: "改写过的陈述。" } });
    await waitFor(() => expect(screen.getByTestId("promote-submit")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("promote-submit"));

    expect(await screen.findByTestId("promote-submit-error")).toHaveTextContent("可证伪陈述");
  });

  it("取消不发 POST", async () => {
    mockGetDraft.mockResolvedValue(draft());
    const onClose = vi.fn();
    render(<PromoteDialog questionId="q-1" revisionSeq={1} onClose={onClose} onPromoted={noop} />);
    await screen.findByTestId("promote-cancel");
    fireEvent.click(screen.getByTestId("promote-cancel"));
    expect(onClose).toHaveBeenCalled();
    expect(mockPromote).not.toHaveBeenCalled();
  });

  it("手写 revision(无五段)明示需手填", async () => {
    mockGetDraft.mockResolvedValue(draft({ segments_found: [] }));
    render(<PromoteDialog questionId="q-1" revisionSeq={1} onClose={noop} onPromoted={noop} />);
    expect(await screen.findByTestId("promote-segments")).toHaveTextContent("0/3");
    expect(screen.getByTestId("promote-segments")).toHaveTextContent("手写");
  });
});
