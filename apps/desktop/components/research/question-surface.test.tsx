import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent, waitFor } from "@testing-library/react";
import { QuestionSurface } from "@/components/research/question-surface";
import {
  getQuestion,
  getEpisode,
  recallLedger,
  investigateQuestion,
  listQuestions,
  listDueQuestions,
  setRevisit,
  type QuestionDetail,
  type RecallCandidate,
} from "@/lib/api/research-graph";

const mockPush = vi.fn();
const mockGetEpisode = vi.mocked(getEpisode);

vi.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: (k: string) => (k === "q" ? QID : null) }),
  useRouter: () => ({ push: mockPush }),
}));

vi.mock("@/lib/api/research-graph", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/api/research-graph")
  >("@/lib/api/research-graph");
  return {
    ...actual,
    getQuestion: vi.fn(),
    recallLedger: vi.fn(),
    investigateQuestion: vi.fn(),
    listQuestions: vi.fn(),
    getEpisode: vi.fn(),
    listDueQuestions: vi.fn(),
    setRevisit: vi.fn(),
  };
});

const mockGet = vi.mocked(getQuestion);
const mockRecall = vi.mocked(recallLedger);
const mockInvestigate = vi.mocked(investigateQuestion);
const mockList = vi.mocked(listQuestions);
const mockListDue = vi.mocked(listDueQuestions);
const mockSetRevisit = vi.mocked(setRevisit);

let QID = "q-1111-2222-3333";

const CANARY_A = "金丝雀A:上一版说突破确认";
const CANARY_B = "金丝雀B:候选假设 48 小时内回踩";

const POLL_INTERVAL_MS = 5000;

const detail: QuestionDetail = {
  question: {
    id: QID,
    title: "BTC 突破是否有效",
    body: "",
    status: "open",
    anchor: {},
    revision_count: 2,
    opened_at: "2026-09-16T08:00:00",
    closed_at: null,
    updated_at: "2026-09-17T08:00:00",
    created_by: "user",
    parent_question_id: null,
    next_revisit_at: null,
  },
  revisions: [
    {
      id: "rev-1",
      question_id: QID,
      seq: 1,
      content: "第一版:突破成立",
      rationale: [],
      cited: [],
      actor: "selene",
      created_at: "2026-09-16T08:05:00",
    },
    {
      id: "rev-2",
      question_id: QID,
      seq: 2,
      content: "第二版:突破确认,回踩后延续",
      rationale: [],
      cited: ["rev-1"],
      actor: "user",
      created_at: "2026-09-17T08:05:00",
    },
  ],
  events: [
    {
      id: "evt-1",
      question_id: QID,
      kind: "created",
      detail: null,
      occurred_at: "2026-09-16T08:00:00",
    },
  ],
  edges: [],
  investigate_running: false,
  cited_refs: [],
};

const candidate = (over: Partial<RecallCandidate>): RecallCandidate => ({
  id: "rev-1",
  kind: "revision",
  text: CANARY_A,
  question_id: QID,
  score: 1,
  matched_on: [],
  recall_class: "research_record",
  evidence_chain: [],
  created_at: "2026-09-16T08:05:00",
  ...over,
});

beforeEach(() => {
  mockGet.mockReset();
  mockRecall.mockReset();
  mockInvestigate.mockReset();
  mockList.mockReset();
  mockPush.mockReset();
  mockGetEpisode.mockReset();
  mockListDue.mockReset();
  mockSetRevisit.mockReset();
  // 列表视图场景(无 ?q)的 due 拉取默认空;不 reset 的泄漏会串测试
  mockListDue.mockResolvedValue({ due: [] });
});

/** v5:账本在第二层(撰写面板)内,先点「自己写一版」展开再断言 */
async function openCompose() {
  fireEvent.click(screen.getByRole("button", { name: "自己写一版" }));
  return await screen.findByTestId("recall-ledger");
}

describe("QuestionSurface v5 渐进披露", () => {
  it("第一屏 5 元素齐:标题卡 + NOW BAR + 当前判断卡 + 折叠条 + 结案轻条", async () => {
    mockGet.mockResolvedValue(detail);
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    expect(screen.getByTestId("section-current-answer")).toBeInTheDocument();
    expect(screen.getByTestId("investigate-bar")).toBeInTheDocument();
    expect(screen.getByTestId("current-answer")).toBeInTheDocument();
    expect(screen.getByTestId("history-toggle")).toBeInTheDocument();
    expect(screen.getByTestId("section-actions")).toBeInTheDocument();
    // 桥入口:有 revision 时结案条出现「立为支柱」
    expect(screen.getByTestId("promote-btn")).toBeInTheDocument();
  });

  it("v5 减法:撰写面板与账本默认不渲染,页面零候选占位", async () => {
    mockGet.mockResolvedValue(detail);
    mockRecall.mockReturnValue(new Promise(() => {})); // 永不 resolve
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    // 不点「自己写一版」→ 全页无 recall-ledger 节点、无候选文本、无 pending 占位
    expect(screen.queryByTestId("recall-ledger")).not.toBeInTheDocument();
    expect(screen.queryByText(CANARY_A)).not.toBeInTheDocument();
    expect(screen.queryByText("正在取回历史…")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ledger-item")).not.toBeInTheDocument();
    // QuestionAnswerForm 未挂载 → recallLedger 一次都没被调
    expect(mockRecall).not.toHaveBeenCalled();
  });

  it("J1: 点开撰写面板,账本 resolve 前零条目只显示 pending 占位", async () => {
    mockGet.mockResolvedValue(detail);
    mockRecall.mockReturnValue(new Promise(() => {})); // 永不 resolve
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    const ledger = await openCompose();
    expect(within(ledger).getByText("正在取回历史…")).toBeInTheDocument();
    expect(within(ledger).queryByText(CANARY_A)).not.toBeInTheDocument();
    expect(within(ledger).queryAllByTestId("ledger-item")).toHaveLength(0);
  });

  it("J1: stub 空数组渲染「本次未取回历史」且无 candidate 文本", async () => {
    mockGet.mockResolvedValue(detail);
    mockRecall.mockResolvedValue({ candidates: [], learned: 0, records: 0 });
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    const ledger = await openCompose();
    await within(ledger).findByText(/本次未取回历史/);
    expect(
      within(ledger).getByText(/之前的回答与假设/),
    ).toBeInTheDocument();
    expect(within(ledger).queryByText(CANARY_A)).not.toBeInTheDocument();
    expect(within(ledger).queryAllByTestId("ledger-item")).toHaveLength(0);
  });

  it("J1: stub 2 条渲染恰好 2 条且含 CANARY 文本", async () => {
    mockGet.mockResolvedValue(detail);
    mockRecall.mockResolvedValue({
      candidates: [
        candidate({ id: "rev-1", text: CANARY_A, recall_class: "research_record" }),
        candidate({
          id: "hyp-9",
          kind: "hypothesis",
          text: CANARY_B,
          recall_class: "learned_experience",
        }),
      ],
      learned: 1,
      records: 1,
    });
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    const ledger = await openCompose();
    await within(ledger).findByText(CANARY_A);
    expect(within(ledger).getByText(CANARY_B)).toBeInTheDocument();
    expect(within(ledger).getAllByTestId("ledger-item")).toHaveLength(2);
  });

  it("J4: settled 空态 textContent 非空且 pending 占位已消失", async () => {
    mockGet.mockResolvedValue(detail);
    mockRecall.mockResolvedValue({ candidates: [], learned: 0, records: 0 });
    const { container } = render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    const ledger = await openCompose();
    await within(ledger).findByText(/本次未取回历史/);
    expect(within(ledger).queryByText("正在取回历史…")).not.toBeInTheDocument();
    expect((container.textContent ?? "").trim().length).toBeGreaterThan(0);
  });

  it("J4: 有历史态 textContent 非空且 pending 占位已消失", async () => {
    mockGet.mockResolvedValue(detail);
    mockRecall.mockResolvedValue({
      candidates: [candidate({ id: "rev-1", text: CANARY_A })],
      learned: 0,
      records: 1,
    });
    const { container } = render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    const ledger = await openCompose();
    await within(ledger).findByText(CANARY_A);
    expect(within(ledger).queryByText("正在取回历史…")).not.toBeInTheDocument();
    expect((container.textContent ?? "").trim().length).toBeGreaterThan(0);
  });

  it("折叠条:点开显示旧版链(逐版卡 + ✓ 被当前版引用),再点收起", async () => {
    mockGet.mockResolvedValue(detail);
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    // 收起态:旧版卡不在 DOM
    expect(screen.queryByTestId("section-timeline")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("history-toggle"));
    const tl = screen.getByTestId("section-timeline");
    expect(within(tl).getByText("第一版:突破成立")).toBeInTheDocument();
    expect(within(tl).getByText("✓ 被 v2 引用")).toBeInTheDocument();
    expect(within(tl).getByText("时间线")).toBeInTheDocument();
    // 再点收起
    fireEvent.click(screen.getByTestId("history-toggle"));
    expect(screen.queryByTestId("section-timeline")).not.toBeInTheDocument();
  });

  it("无 ?q= 渲染问题列表(open 区 + 立问链接),失败态诚实显示", async () => {
    mockGet.mockReset();
    QID = "";
    mockList.mockResolvedValue({ questions: [] });
    render(<QuestionSurface />);
    expect(screen.getByText("问题")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /立一个新问题/ }),
    ).toHaveAttribute("href", "/research/questions/new");
    expect(await screen.findByTestId("empty-open")).toBeInTheDocument();
    QID = "q-1111-2222-3333";
  });

  it("P3 investigate:POST 202 → 轮询视角逐个亮起 → finished ok 归来条出新问题", async () => {
    // fake timers 推进 5s 轮询,避免真实等待;shouldAdvanceTime 让 waitFor 正常工作
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      // 初始 detail:无调查
      mockGet.mockResolvedValue(detail);
      mockInvestigate.mockResolvedValue({
        question_id: QID,
        status: "investigating",
      });
      render(<QuestionSurface />);
      await screen.findByText("BTC 突破是否有效");

      fireEvent.click(
        within(screen.getByTestId("investigate-bar")).getByRole("button", {
          name: /让 Selene 继续查/,
        }),
      );

      // POST 202 后立刻拉一次 detail(running=true)→ 进行中条出现,chip 未到=⋯
      await waitFor(() => expect(mockGet.mock.calls.length).toBe(2));
      mockGet.mockResolvedValue({
        ...detail,
        investigate_running: true,
        events: [
          ...detail.events,
          {
            id: "101",
            question_id: QID,
            kind: "investigate_perspective",
            detail: {
              perspective: "empiricist",
              status: "ok",
              content: "经验派全文:突破确认。",
              cited: [],
            },
            occurred_at: "2026-09-17T09:00:00",
          },
        ],
      });
      // 首个轮询 tick
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS + 10);

      // 进行中条:经验派 ✓ 且可展开全文,未到派=⋯(tick 后 React 刷新需 waitFor)
      const returned = await screen.findByTestId("council-return-bar");
      await waitFor(() =>
        expect(returned.textContent).toContain("经验派 ✓"),
      );
      expect(returned.textContent).toContain("调查会进行中");
      expect(returned.textContent).toContain("记录派 ⋯");
      expect(returned.textContent).toContain("挑战派 ⋯");
      fireEvent.click(within(returned).getByTestId("perspective-chip-empiricist"));
      expect(
        within(returned).getByTestId("perspective-full-empiricist").textContent,
      ).toContain("经验派全文");

      // 下一轮轮询:finished ok 落地 → running=false,新问题进归来条
      mockGet.mockResolvedValue({
        ...detail,
        investigate_running: false,
        revisions: [
          ...detail.revisions,
          { ...detail.revisions[1], seq: 3, actor: "selene" },
        ],
        events: [
          ...detail.events,
          {
            id: "101",
            question_id: QID,
            kind: "investigate_perspective",
            detail: {
              perspective: "empiricist",
              status: "ok",
              content: "经验派全文:突破确认。",
              cited: [],
            },
            occurred_at: "2026-09-17T09:00:00",
          },
          {
            id: "evt-fin",
            question_id: QID,
            kind: "investigate_finished",
            detail: {
              status: "ok",
              revision_id: "rev-3",
              new_questions: [
                { id: "q-child-1", title: "金丝雀C:主持人的新问题" },
              ],
            },
            occurred_at: "2026-09-17T09:05:00",
          },
        ],
      });
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS + 10);
      const done = await screen.findByTestId("council-return-bar");
      await waitFor(() =>
        expect(done.textContent).toContain("Selene 提议 1 个新问题"),
      );
      expect(done.textContent).toContain("调查会归来");
      expect(mockPush).not.toHaveBeenCalled();
      expect(
        within(done).getByRole("link", { name: /金丝雀C/ }),
      ).toHaveAttribute("href", "/research/questions?q=q-child-1");
    } finally {
      vi.useRealTimers();
    }
  });

  it("A2 investigate:citation_migration 渲染新增/丢弃引用计数", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockGet.mockResolvedValue(detail);
      mockInvestigate.mockResolvedValue({
        question_id: QID,
        status: "investigating",
      });
      render(<QuestionSurface />);
      await screen.findByText("BTC 突破是否有效");

      fireEvent.click(
        within(screen.getByTestId("investigate-bar")).getByRole("button", {
          name: /让 Selene 继续查/,
        }),
      );
      await waitFor(() => expect(mockGet.mock.calls.length).toBe(2));

      // finished ok 落地,带 citation_migration → 归来条渲染引用迁移行
      mockGet.mockResolvedValue({
        ...detail,
        investigate_running: false,
        events: [
          ...detail.events,
          {
            id: "evt-fin",
            question_id: QID,
            kind: "investigate_finished",
            detail: {
              status: "ok",
              revision_id: "rev-3",
              new_questions: [],
              citation_migration: {
                question_id: QID,
                from_revision_id: "rev-2",
                to_revision_id: "rev-3",
                added: ["ev-a", "ev-b"],
                dropped: ["ev-c"],
                retained: ["ev-d", "ev-e", "ev-f"],
              },
            },
            occurred_at: "2026-09-17T09:05:00",
          },
        ],
      });
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS + 10);
      const row = await screen.findByTestId("citation-migration-row");
      expect(row.textContent).toContain("新增");
      expect(row.textContent).toContain("丢弃");
      expect(row.textContent).toContain("保留");
      expect(row.textContent).toContain("2"); // added 计数
      expect(row.textContent).toContain("3"); // retained 计数
    } finally {
      vi.useRealTimers();
    }
  });

  it("P3 investigate:失败视角 chip 灰显 ✗,failed 事件落地显示错误文案", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockGet.mockResolvedValue(detail);
      mockInvestigate.mockResolvedValue({
        question_id: QID,
        status: "investigating",
      });
      render(<QuestionSurface />);
      await screen.findByText("BTC 突破是否有效");

      fireEvent.click(
        within(screen.getByTestId("investigate-bar")).getByRole("button", {
          name: /让 Selene 继续查/,
        }),
      );
      await waitFor(() => expect(mockGet.mock.calls.length).toBe(2));

      mockGet.mockResolvedValue({
        ...detail,
        investigate_running: true,
        events: [
          ...detail.events,
          {
            id: "101",
            question_id: QID,
            kind: "investigate_perspective",
            detail: {
              perspective: "empiricist",
              status: "ok",
              content: "ok 文",
              cited: [],
            },
            occurred_at: "2026-09-17T09:00:00",
          },
          {
            id: "102",
            question_id: QID,
            kind: "investigate_perspective",
            detail: {
              perspective: "recordist",
              status: "failed",
              content: "",
              cited: [],
            },
            occurred_at: "2026-09-17T09:00:01",
          },
          {
            id: "103",
            question_id: QID,
            kind: "investigate_perspective",
            detail: {
              perspective: "challenger",
              status: "ok",
              content: "挑战派文",
              cited: [],
            },
            occurred_at: "2026-09-17T09:00:02",
          },
        ],
      });
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS + 10);

      const returned = await screen.findByTestId("council-return-bar");
      await waitFor(() =>
        expect(returned.textContent).toContain("经验派 ✓"),
      );
      expect(returned.textContent).toContain("调查会进行中");
      // chip =「label + 空格 + ✓/✗」多文本节点,统一断言整条 textContent
      expect(returned.textContent).toContain("记录派 ✗");
      expect(returned.textContent).toContain("挑战派 ✓");
      expect(within(returned).queryByText(/Selene 提议/)).not.toBeInTheDocument();

      // finished failed → 错误文案,进行中条收起
      mockGet.mockResolvedValue({
        ...detail,
        investigate_running: false,
        events: [
          ...detail.events,
          {
            id: "evt-fin",
            question_id: QID,
            kind: "investigate_finished",
            detail: { status: "failed", error: "LLM 不可用" },
            occurred_at: "2026-09-17T09:05:00",
          },
        ],
      });
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS + 10);
      await screen.findByText(/自动调查失败 — LLM 不可用/);
    } finally {
      vi.useRealTimers();
    }
  });

  it("P3 investigate:POST 端点失败显示错误提示且不进入轮询", async () => {
    mockGet.mockResolvedValue(detail);
    mockRecall.mockResolvedValue({ candidates: [], learned: 0, records: 0 });
    mockInvestigate.mockRejectedValue(new Error("503"));
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");

    const bar = screen.getByTestId("investigate-bar");
    fireEvent.click(within(bar).getByRole("button", { name: /让 Selene 继续查/ }));
    await within(bar).findByText(/触发调查失败/);
    expect(mockGet.mock.calls.length).toBe(1);
  });

  it("引证据徽章:当前判断卡 cited 含 ev- 时出现,只引 rev- 不出现", async () => {
    mockGet.mockResolvedValue({
      ...detail,
      revisions: [
        ...detail.revisions,
        {
          id: "rev-3",
          question_id: QID,
          seq: 3,
          content: "第三版:引用了新证据",
          rationale: [],
          cited: ["rev-1", "ev-abc123"],
          actor: "selene",
          created_at: "2026-09-18T08:05:00",
        },
      ],
    });
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    // latest = rev-3,cited 含 ev- → 徽章出现
    expect(screen.getByTestId("cites-evidence-badge")).toBeInTheDocument();
  });

  it("引证据徽章:latest 只引旧 revision(rev-)时不渲染", async () => {
    mockGet.mockResolvedValue(detail); // rev-2 cited = ["rev-1"]
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    expect(screen.getByTestId("current-answer")).toBeInTheDocument();
    expect(screen.queryByTestId("cites-evidence-badge")).not.toBeInTheDocument();
  });

  it("引证据徽章:历史折叠条内逐版渲染", async () => {
    mockGet.mockResolvedValue({
      ...detail,
      revisions: [
        {
          id: "rev-1",
          question_id: QID,
          seq: 1,
          content: "第一版:突破成立",
          rationale: [],
          cited: ["ep-evidence9"],
          actor: "selene",
          created_at: "2026-09-16T08:05:00",
        },
        ...detail.revisions.slice(1),
      ],
    });
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    fireEvent.click(screen.getByTestId("history-toggle"));
    const tl = screen.getByTestId("section-timeline");
    expect(within(tl).getByTestId("cites-evidence-badge-history")).toBeInTheDocument();
  });

  it("探针徽章:cited_refs 标 RECORDED 时 latest 卡渲染,非探针不渲染", async () => {
    mockGet.mockResolvedValue({
      ...detail,
      revisions: [
        ...detail.revisions,
        {
          id: "rev-3",
          question_id: QID,
          seq: 3,
          content: "第三版:引用探针证据",
          rationale: [],
          cited: ["ev-probe1"],
          actor: "selene",
          created_at: "2026-09-18T08:05:00",
        },
      ],
      cited_refs: [
        { id: "ev-probe1", mode: "RECORDED", episode_id: "ep-probe1" },
      ],
    });
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    expect(screen.getByTestId("probe-badge")).toBeInTheDocument();
    expect(screen.getByTestId("cites-evidence-badge")).toBeInTheDocument();
  });

  it("探针徽章:mode 非 RECORDED(转述)不渲染", async () => {
    mockGet.mockResolvedValue({
      ...detail,
      revisions: [
        ...detail.revisions,
        {
          id: "rev-3",
          question_id: QID,
          seq: 3,
          content: "第三版:引用转述证据",
          rationale: [],
          cited: ["ev-rec1"],
          actor: "selene",
          created_at: "2026-09-18T08:05:00",
        },
      ],
      cited_refs: [
        { id: "ev-rec1", mode: "RECONSTRUCTED", episode_id: "ep-rec1" },
      ],
    });
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    expect(screen.queryByTestId("probe-badge")).not.toBeInTheDocument();
  });

  it("探针徽章可点击:展开 episode 抽屉(刀1 lifecycle 闭环)", async () => {
    mockGet.mockResolvedValue({
      ...detail,
      revisions: [
        ...detail.revisions,
        {
          id: "rev-3",
          question_id: QID,
          seq: 3,
          content: "第三版:引用探针证据",
          rationale: [],
          cited: ["ev-probe1"],
          actor: "selene",
          created_at: "2026-09-18T08:05:00",
        },
      ],
      cited_refs: [
        { id: "ev-probe1", mode: "RECORDED", episode_id: "ep-probe1" },
      ],
    });
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    expect(screen.queryByTestId("episode-drawer")).not.toBeInTheDocument();
    mockGetEpisode.mockResolvedValue({
      episode: {
        id: "ep-probe1",
        subject_type: "factor",
        subject_id: "funding_z",
        title: "funding_z 探针 BTC 1h",
        recorded: {
          probe: "funding_ic",
          lifecycle: {
            status: "DISCOVERED",
            transitions: [
              {
                from: "DISCOVERED",
                to: "DISCOVERED",
                at: "2026-09-18T08:00:00",
                actor: "system",
              },
            ],
          },
        },
        occurred_at: "2026-09-18T08:00:00",
      },
      evidence: [],
    });
    fireEvent.click(screen.getByTestId("probe-badge"));
    const drawer = await screen.findByTestId("episode-drawer");
    // 抽屉拉的是 episode 详情(lifecycle 子键),渲染徽章与事件流
    await within(drawer).findByTestId("episode-lifecycle-badge");
    expect(within(drawer).getByText(/新发现/)).toBeInTheDocument();
  });

  it("v025 复访行:open 问题显示未设态 + 三个预设键;点 1 周后调 setRevisit", async () => {
    mockGet.mockResolvedValue(detail);
    mockSetRevisit.mockResolvedValue({ question: detail.question });
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");

    const bar = screen.getByTestId("revisit-bar");
    expect(within(bar).getByTestId("revisit-status")).toHaveTextContent(
      "未设复访时点",
    );
    fireEvent.click(within(bar).getByTestId("revisit-1w"));
    await waitFor(() => expect(mockSetRevisit).toHaveBeenCalledTimes(1));
    // 发出的时点是 UTC ISO(与后端 _now() 同族,due() 字典序比较可用)
    const [qid, iso] = mockSetRevisit.mock.calls[0];
    expect(qid).toBe(QID);
    expect(iso).toMatch(/^\d{4}-\d{2}-\d{2}T.*Z$/);
    // 提交后 onDone → formTick 重拉详情
    await waitFor(() => expect(mockGet.mock.calls.length).toBeGreaterThan(1));
  });

  it("v025 复访行:已设未到期显示日期 + 清除键;清除调 setRevisit(null)", async () => {
    mockGet.mockResolvedValue({
      ...detail,
      question: {
        ...detail.question,
        // 未来时点:2099 年,测试环境恒未到期
        next_revisit_at: "2099-01-01T00:00:00+00:00",
      },
    });
    mockSetRevisit.mockResolvedValue({ question: detail.question });
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");

    const bar = screen.getByTestId("revisit-bar");
    expect(within(bar).getByTestId("revisit-status")).toHaveTextContent(
      "01-01 复访",
    );
    expect(within(bar).queryByTestId("revisit-1w")).not.toBeInTheDocument();
    fireEvent.click(within(bar).getByTestId("revisit-clear"));
    await waitFor(() =>
      expect(mockSetRevisit).toHaveBeenCalledWith(QID, null),
    );
  });

  it("v025 复访行:已到期 accent 高亮 + 「已到期」文案", async () => {
    mockGet.mockResolvedValue({
      ...detail,
      question: {
        ...detail.question,
        // 过去时点:恒已到期
        next_revisit_at: "2000-01-01T00:00:00+00:00",
      },
    });
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");

    expect(within(screen.getByTestId("revisit-bar")).getByTestId("revisit-status")).toHaveTextContent(
      "已到期",
    );
  });

  it("v025 复访行:终态(sufficiently_answered)不渲染", async () => {
    mockGet.mockResolvedValue({
      ...detail,
      question: { ...detail.question, status: "sufficiently_answered" },
    });
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");
    expect(screen.queryByTestId("revisit-bar")).not.toBeInTheDocument();
  });

  it("v025 复访行:setRevisit 失败显示行内错误", async () => {
    mockGet.mockResolvedValue(detail);
    mockSetRevisit.mockRejectedValue(new Error("400"));
    render(<QuestionSurface />);
    await screen.findByText("BTC 突破是否有效");

    fireEvent.click(
      within(screen.getByTestId("revisit-bar")).getByTestId("revisit-1w"),
    );
    await screen.findByText(/复访计划设置失败/);
  });

  it("v025 列表视图:due 非空渲染复访条 + open 行带该复访徽章;空则不渲染", async () => {
    mockGet.mockReset();
    QID = "";
    mockList.mockResolvedValue({
      questions: [
        { ...detail.question, next_revisit_at: "2000-01-01T00:00:00+00:00" },
      ],
    });
    mockListDue.mockResolvedValue({
      due: [
        {
          id: detail.question.id,
          title: detail.question.title,
          status: "open",
          next_revisit_at: "2000-01-01T00:00:00+00:00",
          opened_at: detail.question.opened_at,
          updated_at: detail.question.updated_at,
          anchor: {},
        },
      ],
    });
    render(<QuestionSurface />);
    const bar = await screen.findByTestId("revisit-due-bar");
    expect(bar.textContent).toContain("1 个问题到了你定的复访时点");
    expect(screen.getAllByTestId("revisit-due-badge")).toHaveLength(1);
    QID = "q-1111-2222-3333";
  });

  it("v025 列表视图:due 拉取失败静默降级,列表照常", async () => {
    mockGet.mockReset();
    QID = "";
    mockList.mockResolvedValue({ questions: [detail.question] });
    mockListDue.mockRejectedValue(new Error("404 — 旧后端"));
    render(<QuestionSurface />);
    await screen.findByTestId("open-questions");
    expect(screen.queryByTestId("revisit-due-bar")).not.toBeInTheDocument();
    QID = "q-1111-2222-3333";
  });

  // ---- N1 第二刀:council 回放调查控件(F2/F3) --------------------------

  it("N1-F2:选回放日期后触发调查,investigateQuestion 收到 localDateToIso 的 as_of;清除回默认", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockGet.mockResolvedValue(detail);
      mockInvestigate.mockResolvedValue({
        question_id: QID,
        status: "investigating",
      });
      render(<QuestionSurface />);
      await screen.findByText("BTC 突破是否有效");

      const dateInput = screen
        .getByTestId("investigate-bar")
        .querySelector('input[type="date"]');
      expect(dateInput).not.toBeNull();
      fireEvent.change(dateInput!, { target: { value: "2026-10-05" } });

      // 徽章出现,显示所选日期
      const bar = screen.getByTestId("council-replay-bar");
      expect(bar.textContent).toContain("回放 2026-10-05");

      fireEvent.click(
        within(screen.getByTestId("investigate-bar")).getByRole("button", {
          name: /让 Selene 继续查/,
        }),
      );
      await waitFor(() => expect(mockInvestigate).toHaveBeenCalledTimes(1));
      expect(mockInvestigate).toHaveBeenCalledWith(QID, {
        as_of: "2026-10-05T23:59:59.000Z",
      });

      // 轮询拿到 finished → investigating 复位,按钮才能再点
      mockGet.mockResolvedValue({
        ...detail,
        investigate_running: false,
        events: [
          ...detail.events,
          {
            id: "evt-fin",
            question_id: QID,
            kind: "investigate_finished",
            detail: {
              status: "ok",
              revision_id: "rev-1",
              new_questions: [{ id: "q-child-x", title: "金丝雀X" }],
            },
            occurred_at: "2026-10-06T09:05:00",
          },
        ],
      });
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS + 10);
      await screen.findByTestId("council-return-bar");

      // 清除 → 徽章消失;再触发 = 无 payload(默认调查)
      fireEvent.click(screen.getByTestId("council-replay-clear"));
      expect(screen.queryByTestId("council-replay-bar")).not.toBeInTheDocument();
      fireEvent.click(
        within(screen.getByTestId("investigate-bar")).getByRole("button", {
          name: /让 Selene 继续查/,
        }),
      );
      await waitFor(() => expect(mockInvestigate).toHaveBeenCalledTimes(2));
      // 第二参数 undefined = 无 payload → client 层 body 为 {}(F1 已证)
      expect(mockInvestigate).toHaveBeenLastCalledWith(QID, undefined);
    } finally {
      vi.useRealTimers();
    }
  });

  it("N1-F3:默认不渲染回放 chip;finished detail 带 as_of 渲染归来条 chip,as_of_replay rationale 渲染 revision chip", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockGet.mockResolvedValue(detail);
      mockInvestigate.mockResolvedValue({
        question_id: QID,
        status: "investigating",
      });
      render(<QuestionSurface />);
      await screen.findByText("BTC 突破是否有效");

      // 默认 fixture(rationale 无 as_of_replay 前缀):零 chip
      expect(
        screen.queryByTestId("revision-asof-badge"),
      ).not.toBeInTheDocument();
      expect(screen.queryByTestId("return-asof-badge")).not.toBeInTheDocument();

      fireEvent.click(
        within(screen.getByTestId("investigate-bar")).getByRole("button", {
          name: /让 Selene 继续查/,
        }),
      );
      // finished ok + as_of;新 revision rationale 带 as_of_replay 前缀;
      // new_questions 非空保归来条挂载(finished 后 investigating=false)
      mockGet.mockResolvedValue({
        ...detail,
        investigate_running: false,
        revisions: [
          ...detail.revisions,
          {
            id: "rev-3",
            question_id: QID,
            seq: 3,
            content: "回放轮的判断",
            rationale: [
              "multi-perspective council",
              "as_of_replay:2026-10-05T23:59:59.000Z",
            ],
            cited: ["rev-2"],
            actor: "selene",
            created_at: "2026-10-06T09:00:00",
          },
        ],
        events: [
          ...detail.events,
          {
            id: "evt-fin",
            question_id: QID,
            kind: "investigate_finished",
            detail: {
              status: "ok",
              revision_id: "rev-3",
              new_questions: [{ id: "q-child-x", title: "金丝雀X" }],
              as_of: "2026-10-05T23:59:59.000Z",
            },
            occurred_at: "2026-10-06T09:05:00",
          },
        ],
      });
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS + 10);

      // 归来条 chip:回放 + 日期部分
      const done = await screen.findByTestId("council-return-bar");
      await waitFor(() =>
        expect(within(done).getByTestId("return-asof-badge").textContent).toBe(
          "回放 2026-10-05",
        ),
      );
      // 当前判断卡(rev-3 是 latest):rationale 前缀 → 回放 chip
      await waitFor(() =>
        expect(screen.getAllByTestId("revision-asof-badge")[0].textContent).toBe(
          "回放 2026-10-05",
        ),
      );
    } finally {
      vi.useRealTimers();
    }
  });
});
