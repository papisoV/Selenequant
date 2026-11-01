import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QuestionAnswerForm } from "@/components/research/question-answer-form";
import {
  addRevision,
  recallLedger,
  type RecallCandidate,
} from "@/lib/api/research-graph";

vi.mock("@/lib/api/research-graph", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/api/research-graph")
  >("@/lib/api/research-graph");
  return {
    ...actual,
    addRevision: vi.fn(),
    recallLedger: vi.fn(),
  };
});

const mockAdd = vi.mocked(addRevision);
const mockRecall = vi.mocked(recallLedger);

const CANARY_A = "金丝雀A:上一版说突破确认";
const CANARY_B = "金丝雀B:候选假设 48 小时内回踩";

const cand = (over: Partial<RecallCandidate>): RecallCandidate => ({
  id: "rev-1",
  kind: "revision",
  text: CANARY_A,
  question_id: "q-1",
  score: 1,
  matched_on: [],
  recall_class: "research_record",
  evidence_chain: [],
  created_at: "2026-09-16T08:05:00",
  ...over,
});

beforeEach(() => {
  mockAdd.mockReset();
  mockRecall.mockReset();
  mockAdd.mockResolvedValue({} as never);
});

describe("QuestionAnswerForm", () => {
  it("resolve 前提交 disabled、无候选控件、cited 为空", () => {
    mockRecall.mockReturnValue(new Promise(() => {}));
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={() => {}} />);
    expect(screen.getByText("正在取回历史…")).toBeInTheDocument();
    expect(screen.queryByText(CANARY_A)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /写下这一版/ })).toBeDisabled();
  });

  it("2 候选渲染 2 个 checkbox,id 与 recall 响应一致", async () => {
    mockRecall.mockResolvedValue({
      candidates: [
        cand({ id: "rev-1", text: CANARY_A }),
        cand({ id: "hyp-9", kind: "hypothesis", text: CANARY_B }),
      ],
      learned: 1,
      records: 1,
    });
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={() => {}} />);
    await screen.findByText(CANARY_A);
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(2);
  });

  it("提交时 cited 恰好等于选中集(addRevision 收到选中 id)", async () => {
    mockRecall.mockResolvedValue({
      candidates: [
        cand({ id: "rev-1", text: CANARY_A }),
        cand({ id: "hyp-9", kind: "hypothesis", text: CANARY_B }),
      ],
      learned: 1,
      records: 1,
    });
    const onSubmitted = vi.fn();
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={onSubmitted} />);
    await screen.findByText(CANARY_A);

    fireEvent.input(screen.getByLabelText("回答内容"), {
      target: { value: "第二版:突破确认" },
    });
    fireEvent.click(screen.getAllByRole("checkbox")[1]); // 只选 hyp-9

    fireEvent.click(screen.getByRole("button", { name: /写下这一版/ }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalledTimes(1));
    expect(mockAdd).toHaveBeenCalledWith("q-1", {
      content: "第二版:突破确认",
      cited: ["hyp-9"],
      horizon: "structural",
    });
  });

  it("默认 horizon=structural;切到 short 期后提交带 tactical", async () => {
    mockRecall.mockResolvedValue({
      candidates: [cand({ id: "rev-1", text: CANARY_A })],
      learned: 0,
      records: 1,
    });
    const onSubmitted = vi.fn();
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={onSubmitted} />);
    await screen.findByText(CANARY_A);

    // 默认选中:structural 亮,tactical 不亮
    expect(screen.getByTestId("horizon-structural")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByTestId("horizon-tactical")).toHaveAttribute(
      "aria-checked",
      "false",
    );

    fireEvent.input(screen.getByLabelText("回答内容"), {
      target: { value: "短期观察性结论" },
    });
    fireEvent.click(screen.getByTestId("horizon-tactical"));
    fireEvent.click(screen.getByRole("button", { name: /写下这一版/ }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalledTimes(1));
    expect(mockAdd).toHaveBeenLastCalledWith("q-1", {
      content: "短期观察性结论",
      cited: [],
      horizon: "tactical",
    });
  });

  it("tactical 候选渲染「短期」徽章;structural 候选不渲染", async () => {
    mockRecall.mockResolvedValue({
      candidates: [
        cand({ id: "rev-1", text: CANARY_A, horizon: "tactical" }),
        cand({ id: "rev-2", text: CANARY_B, horizon: "structural" }),
        cand({ id: "hyp-3", kind: "hypothesis", text: "金丝雀C:无 horizon 键" }),
      ],
      learned: 0,
      records: 2,
    });
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={() => {}} />);
    await screen.findByText(CANARY_A);
    await screen.findByText(CANARY_B);
    // 只有 tactical 候选带徽章;structural 与 hypothesis(无 horizon 键)不带
    expect(screen.getAllByTestId("horizon-tactical-badge")).toHaveLength(1);
  });

  it("空内容时提交 disabled", async () => {
    mockRecall.mockResolvedValue({ candidates: [], learned: 0, records: 0 });
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={() => {}} />);
    await screen.findByText(/本次未取回历史/);
    expect(screen.getByRole("button", { name: /写下这一版/ })).toBeDisabled();
  });

  it("有实证链的候选点开溯源显示完整跳路径", async () => {
    mockRecall.mockResolvedValue({
      candidates: [
        cand({
          id: "rev-1",
          recall_class: "learned_experience",
          evidence_chain: [
            "revision:rev-1",
            "evidence:ev-1",
            "episode:ep-1",
          ],
        }),
      ],
      learned: 1,
      records: 0,
    });
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={() => {}} />);
    await screen.findByText(CANARY_A);
    // 默认收起,只显示开关;点开显示整条链
    expect(screen.queryByTestId("evidence-chain-path")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("evidence-chain-toggle"));
    const path = screen.getByTestId("evidence-chain-path");
    expect(path).toHaveTextContent("回答:rev-1");
    expect(path).toHaveTextContent("证据:ev-1");
    expect(path).toHaveTextContent("实跑段:ep-1");
  });

  it("空链候选显示降级说明(无实证链),不渲染开关", async () => {
    mockRecall.mockResolvedValue({
      candidates: [cand({ id: "rev-2", evidence_chain: [] })],
      learned: 0,
      records: 1,
    });
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={() => {}} />);
    await screen.findByText(CANARY_A);
    expect(screen.getByTestId("evidence-chain-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("evidence-chain-toggle")).not.toBeInTheDocument();
  });
});


// ---------------------------------------------------------------------------
// 账本回放(N1 消费侧刀 Step 4)
// ---------------------------------------------------------------------------

describe("账本回放", () => {
  it("默认态:recallLedger 调用不带 as_of 键(请求与今天同形)", async () => {
    mockRecall.mockResolvedValue({ candidates: [], learned: 0, records: 0 } as never);
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={() => {}} />);
    await waitFor(() => expect(mockRecall).toHaveBeenCalled());
    const arg = mockRecall.mock.calls[0][0];
    expect(arg).toEqual({ question_id: "q-1" });
    expect("as_of" in arg).toBe(false);
  });

  it("选回放日 → 带 as_of(UTC 日终)重调,且渲染回放条", async () => {
    mockRecall.mockResolvedValue({ candidates: [], learned: 0, records: 0 } as never);
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={() => {}} />);
    await waitFor(() => expect(mockRecall).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByTestId("replay-date"), {
      target: { value: "2026-09-27" },
    });

    await waitFor(() => expect(mockRecall).toHaveBeenCalledTimes(2));
    const arg = mockRecall.mock.calls[1][0];
    expect(arg.question_id).toBe("q-1");
    // localDateToIso:UTC 日历日终 23:59:59
    expect(arg.as_of).toBe("2026-09-27T23:59:59.000Z");
    expect(await screen.findByTestId("replay-bar")).toBeTruthy();
  });

  it("回放日无记录 → 回放专属空态文案", async () => {
    mockRecall.mockResolvedValue({ candidates: [], learned: 0, records: 0 } as never);
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={() => {}} />);
    await waitFor(() => expect(mockRecall).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByTestId("replay-date"), {
      target: { value: "2026-01-01" },
    });
    const empty = await screen.findByTestId("replay-empty");
    expect(empty.textContent).toContain("当时还不存在");
  });

  it("回放态渲染数据窗标注与记录时间;evidence 标签正确", async () => {
    mockRecall.mockResolvedValue({
      candidates: [
        cand({
          id: "ev-1",
          kind: "evidence",
          text: "探针证据",
          data_window: { start: "2026-09-22T00:00:00+00:00", end: "2026-09-22T23:59:59+00:00" },
        }),
      ],
      learned: 1,
      records: 0,
    } as never);
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={() => {}} />);
    // 先选回放日(触发带 as_of 的第二次请求,响应同 mock)
    fireEvent.change(screen.getByTestId("replay-date"), {
      target: { value: "2026-09-27" },
    });
    const win = await screen.findByTestId("data-window-label");
    expect(win.textContent).toContain("09-22");
    // 用容器 textContent 断言(JSX 把标签拆成多节点,getByText 匹配不到)
    const row = screen.getByTestId("ledger-item");
    expect(row.textContent).toContain("记录于");
    expect(row.textContent).toContain("证据");
  });

  it("清除回放 → 回到默认请求(无 as_of)", async () => {
    mockRecall.mockResolvedValue({ candidates: [], learned: 0, records: 0 } as never);
    render(<QuestionAnswerForm questionId="q-1" onSubmitted={() => {}} />);
    await waitFor(() => expect(mockRecall).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByTestId("replay-date"), {
      target: { value: "2026-09-27" },
    });
    await waitFor(() => expect(mockRecall).toHaveBeenCalledTimes(2));
    fireEvent.click(await screen.findByTestId("replay-clear"));
    await waitFor(() => expect(mockRecall).toHaveBeenCalledTimes(3));
    expect("as_of" in mockRecall.mock.calls[2][0]).toBe(false);
  });
});
