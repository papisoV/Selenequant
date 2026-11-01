import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MissionDetailView } from "@/components/research/missions-detail-view";
import type { ActivityRow, MissionRow } from "@/components/research/missions-types";

vi.mock("@/lib/api-client", () => ({
  api: vi.fn(),
  apiPost: vi.fn(),
}));

import { apiPost } from "@/lib/api-client";

const mission: MissionRow = {
  id: "msn-note-1",
  title: "验证 bb_v3 在 BTC/USDT 的表现",
  question: "是否有真实边缘?",
  subject_type: "strategy",
  subject_id: "strat-bb-v3",
  phase: "investigation",
  stage: "evidence",
  created_by: "user",
  opened_at: "2026-10-18T08:00:00",
  updated_at: "2026-10-19T08:00:00",
  resolved_at: null,
  context: null,
  outcome: null,
};

const activity: ActivityRow[] = [
  {
    id: 1,
    mission_id: mission.id,
    occurred_at: "2026-10-18T08:00:00",
    actor: "system",
    kind: "mission_opened",
    detail: { question: mission.question },
  },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe("MissionDetailView 结案反馈", () => {
  it("结案响应里的决策/子问题/复访原样显示(不再只显示一句'已结案')", async () => {
    const validationMission: MissionRow = { ...mission, phase: "validation" };
    vi.mocked(apiPost).mockResolvedValueOnce({
      mission: { ...validationMission, phase: "resolved" },
      settlement: {
        decision_id: "dec-abc123",
        child_question_id: "q-child456",
        revisit_at: "2026-12-01T00:00:00+00:00",
        window_start: "2026-10-25T00:00:00+00:00",
        window_end: "2026-11-24T00:00:00+00:00",
        warnings: [],
      },
    });
    render(
      <MissionDetailView mission={validationMission} activity={activity} />,
    );

    fireEvent.click(screen.getByText(/结案 · Resolve/));

    await screen.findByText(/dec-abc123/);
    expect(screen.getByText(/q-child456/)).toBeInTheDocument();
    expect(screen.getByText(/12月|12-01|2026/)).toBeInTheDocument();
  });

  it("没有归属问题时把警告显示出来,不静默", async () => {
    const validationMission: MissionRow = { ...mission, phase: "validation" };
    vi.mocked(apiPost).mockResolvedValueOnce({
      mission: { ...validationMission, phase: "resolved" },
      settlement: {
        decision_id: null,
        child_question_id: null,
        revisit_at: null,
        window_start: null,
        window_end: null,
        warnings: ["mission 没有 context.question_id —— 未进 research_decisions"],
      },
    });
    render(
      <MissionDetailView mission={validationMission} activity={activity} />,
    );

    fireEvent.click(screen.getByText(/结案 · Resolve/));
    await screen.findByText(/未落账/);
    expect(screen.getByText(/research_decisions/)).toBeInTheDocument();
  });
});

describe("MissionDetailView 人工留言", () => {
  it("提交留言:POST /note 带原文,成功后清空输入并提示", async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ mission });
    const onChanged = vi.fn();
    render(
      <MissionDetailView mission={mission} activity={activity} onChanged={onChanged} />,
    );

    const input = screen.getByPlaceholderText(/资金费率转负/);
    fireEvent.change(input, { target: { value: "  昨天资金费率转负了,带上这一层  " } });
    fireEvent.click(screen.getByTestId("mission-note-submit"));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        `/api/v1/research/missions/${mission.id}/note`,
        { note: "昨天资金费率转负了,带上这一层" },
      ),
    );
    await screen.findByText(/已留言/);
    expect(input).toHaveValue("");
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("空白留言:提交键禁用,不发请求", async () => {
    render(<MissionDetailView mission={mission} activity={activity} />);
    const btn = screen.getByTestId("mission-note-submit");
    expect(btn).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText(/资金费率转负/), {
      target: { value: "   " },
    });
    expect(btn).toBeDisabled();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("提交失败:显示错误文案,输入保留(不丢用户写的内容)", async () => {
    vi.mocked(apiPost).mockRejectedValueOnce(new Error("503"));
    render(<MissionDetailView mission={mission} activity={activity} />);

    const input = screen.getByPlaceholderText(/资金费率转负/);
    fireEvent.change(input, { target: { value: "保留我" } });
    fireEvent.click(screen.getByTestId("mission-note-submit"));

    await screen.findByText(/留言失败/);
    expect(input).toHaveValue("保留我");
  });

  it("活动流渲染 user_note:内容来自 detail.note", () => {
    render(
      <MissionDetailView
        mission={mission}
        activity={[
          ...activity,
          {
            id: 2,
            mission_id: mission.id,
            occurred_at: "2026-10-19T09:00:00",
            actor: "user",
            kind: "user_note",
            detail: { note: "金丝雀:人工留言内容" },
          },
        ]}
      />,
    );
    // actSummary 把 <b>你的留言</b> 与正文拼在同一节点,整体断言
    expect(screen.getByText(/你的留言/)).toBeInTheDocument();
    expect(screen.getByText(/金丝雀:人工留言内容/)).toBeInTheDocument();
  });
});

describe("MissionPulse 渲染 worker 产出", () => {
  const workerActivity: ActivityRow[] = [
    {
      id: 10,
      mission_id: mission.id,
      occurred_at: "2026-10-20T09:00:00",
      actor: "selene",
      kind: "worker_analysis",
      detail: {
        proposal: "建议动作:approve — 材料支持继续验证\n信号:mildly_weakening",
        recommendation: "approve",
        reason: "杠杆拥挤度下降",
        signal: "mildly_weakening",
        evidence_id: "ev-1",
        evidence_error: null,
      },
    },
  ];

  it("分析的人话结论与建议动作出现在时间线上,而不是裸 kind", () => {
    render(<MissionDetailView mission={mission} activity={workerActivity} />);
    // 回归:actSummary 此前没有 worker_analysis 分支,整条渲染成
    // "worker_analysis" 六个英文单词 —— 管线"提出决策"这一步在界面上不存在。
    expect(screen.getByText(/分析已出/)).toBeInTheDocument();
    expect(screen.getByText(/建议 进入验证/)).toBeInTheDocument();
    expect(screen.getByText(/杠杆拥挤度下降/)).toBeInTheDocument();
    expect(screen.getByText(/信号 mildly_weakening/)).toBeInTheDocument();
    expect(screen.queryByText(/^worker_analysis$/)).toBeNull();
  });

  it("证据写入失败要说出来,不能只留一条看起来正常的分析", () => {
    render(
      <MissionDetailView
        mission={mission}
        activity={[
          {
            ...workerActivity[0],
            detail: { ...workerActivity[0].detail, evidence_error: "unknown pillar" },
          },
        ]}
      />,
    );
    expect(screen.getByText(/证据未入账/)).toBeInTheDocument();
  });

  it("失败与预算耗尽也有可读的说明", () => {
    render(
      <MissionDetailView
        mission={mission}
        activity={[
          {
            id: 11,
            mission_id: mission.id,
            occurred_at: "2026-10-20T09:05:00",
            actor: "selene",
            kind: "worker_failed",
            detail: { reason: "llm_unavailable", rounds: 1 },
          },
          {
            id: 12,
            mission_id: mission.id,
            occurred_at: "2026-10-20T09:10:00",
            actor: "system",
            kind: "worker_budget_exhausted",
            detail: { rounds: 3, max_rounds: 3 },
          },
        ]}
      />,
    );
    expect(screen.getByText(/本轮调查失败/)).toBeInTheDocument();
    expect(screen.getByText(/模型没有响应/)).toBeInTheDocument();
    expect(screen.getByText(/本轮调查预算用完/)).toBeInTheDocument();
  });

  it("复访到期与实验完成不再是裸 kind — 复访是要人动手的提醒", () => {
    render(
      <MissionDetailView
        mission={mission}
        activity={[
          {
            id: 13,
            mission_id: mission.id,
            occurred_at: "2026-10-20T09:15:00",
            actor: "system",
            kind: "revisit_due",
            detail: {
              key: "q1|2026-10-20",
              question_id: "q1",
              question_title: "拥挤度是否已见顶",
              hint: "复访到期:请复核该问题,决定是否补证据/改判/结案",
            },
          },
          {
            id: 14,
            mission_id: mission.id,
            occurred_at: "2026-10-20T09:20:00",
            actor: "system",
            kind: "experiment_completed",
            detail: { task_id: "t1", experiment_kind: "walk_forward", summary: "3/4 窗口为正" },
          },
        ]}
      />,
    );
    expect(screen.getByText("复访到期")).toBeInTheDocument();
    expect(screen.getByText(/请复核该问题/)).toBeInTheDocument();
    expect(screen.getByText(/拥挤度是否已见顶/)).toBeInTheDocument();
    expect(screen.getByText(/实验跑完/)).toBeInTheDocument();
    expect(screen.getByText(/walk_forward/)).toBeInTheDocument();
    expect(screen.queryByText(/^revisit_due$/)).toBeNull();
    expect(screen.queryByText(/^experiment_completed$/)).toBeNull();
  });
});

describe("MissionDetailView validation 阶段文案诚实性", () => {
  it("不承诺自动结案 — 没有任何生产路径会写 resolved", () => {
    render(
      <MissionDetailView
        mission={{ ...mission, phase: "validation", stage: "validation" }}
        activity={[]}
      />,
    );
    const body = document.body.textContent ?? "";
    // 实库里两条 mission 在 validation 停留 8 天和 20 天;UI 曾保证"自动入账并结案"。
    expect(body).not.toMatch(/自动入账并结案/);
    expect(body).not.toMatch(/无需你的输入/);
    expect(body).toMatch(/跑完需你点「结案」/);
  });
});
