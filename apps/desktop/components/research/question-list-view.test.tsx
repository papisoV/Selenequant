import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";

import { QuestionListView } from "@/components/research/question-list-view";
import {
  closeQuestion,
  listDueQuestions,
  listQuestions,
  reopenQuestion,
  type Question,
} from "@/lib/api/research-graph";

vi.mock("@/lib/api/research-graph", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/api/research-graph")
  >("@/lib/api/research-graph");
  return {
    ...actual,
    listQuestions: vi.fn(),
    listDueQuestions: vi.fn(),
    closeQuestion: vi.fn(),
    reopenQuestion: vi.fn(),
  };
});

const mockList = vi.mocked(listQuestions);
const mockDue = vi.mocked(listDueQuestions);
const mockClose = vi.mocked(closeQuestion);
const mockReopen = vi.mocked(reopenQuestion);

const q = (over: Partial<Question> & { id: string; title: string }): Question => ({
  body: "",
  status: "open",
  anchor: {},
  revision_count: 1,
  opened_at: "2026-10-20T00:00:00Z",
  closed_at: null,
  updated_at: "2026-10-20T00:00:00Z",
  created_by: "director",
  parent_question_id: null,
  ...over,
});

const QUESTIONS: Question[] = [
  q({ id: "q-open-1", title: "BTC 1h 资金费率领先性" }),
  q({
    id: "q-open-selene",
    title: "ETH 上影线是否预示反转",
    created_by: "selene",
    parent_question_id: "q-open-1",
  }),
  q({ id: "q-parked-1", title: "SOL 4h 缺口回补", status: "parked" }),
  q({ id: "q-parked-2", title: "跨所价差收敛", status: "parked" }),
  q({ id: "q-done-1", title: "旧问法", status: "falsified" }),
];

beforeEach(() => {
  vi.clearAllMocks();
  // 筛选状态存在 URL 里 → 同一个文件内的用例共用一条地址栏,
  // 不重置会让上一条用例留下的 ?status=… 渗透进下一条。
  window.history.replaceState({}, "", "/research/questions");
  mockList.mockResolvedValue({ questions: QUESTIONS });
  mockDue.mockResolvedValue({ due: [], total: 0 });
  mockClose.mockResolvedValue({ question: QUESTIONS[0] });
  mockReopen.mockResolvedValue({ question: QUESTIONS[2] });
});

describe("QuestionListView 管理面", () => {
  it("默认只显示未结案(open + parked),已结案折叠不计入", async () => {
    render(<QuestionListView />);
    await screen.findByTestId("open-questions");
    expect(screen.getByTestId("open-questions").textContent).toContain(
      "BTC 1h 资金费率领先性",
    );
    expect(screen.getByTestId("parked-questions").textContent).toContain(
      "SOL 4h 缺口回补",
    );
    // 已结案的那条不在默认视图里
    expect(screen.queryByText("旧问法")).toBeNull();
    expect(screen.getByText("4 / 5")).toBeTruthy();
  });

  it("按状态切到「已结案」只剩终态问题", async () => {
    render(<QuestionListView />);
    await screen.findByTestId("open-questions");

    fireEvent.click(screen.getByTestId("status-tab-terminal"));
    expect(screen.queryByTestId("open-questions")).toBeNull();
    expect(screen.queryByTestId("parked-questions")).toBeNull();
    expect(screen.getByTestId("closed-toggle").textContent).toContain("1");
  });

  it("按来源筛选 + 文本搜索可叠加", async () => {
    render(<QuestionListView />);
    await screen.findByTestId("open-questions");

    fireEvent.click(screen.getByTestId("source-tab-selene"));
    // 只剩 Selene 提议的那条。注意不能拿整段 textContent 判"不含 BTC":
    // 待拍板徽章里写着「源自<父问题标题>」,父子关系本来就该出现在那里。
    expect(screen.getAllByTestId("question-row")).toHaveLength(1);
    expect(screen.getByTestId("question-row").textContent).toContain("ETH 上影线");

    fireEvent.click(screen.getByTestId("source-tab-all"));
    fireEvent.change(screen.getByTestId("question-search"), { target: { value: "缺口" } });
    expect(screen.getByTestId("parked-questions").textContent).toContain(
      "SOL 4h 缺口回补",
    );
    expect(screen.queryByText("跨所价差收敛")).toBeNull();
  });

  it("搜索无结果时给出可清除的诚实空态", async () => {
    render(<QuestionListView />);
    await screen.findByTestId("open-questions");

    fireEvent.change(screen.getByTestId("question-search"), { target: { value: "zzz不存在" } });
    expect(screen.getByTestId("no-match")).toBeTruthy();
    expect(screen.queryByTestId("open-questions")).toBeNull();
  });

  it("终态问题不给 checkbox(批量操作不得改写历史)", async () => {
    render(<QuestionListView />);
    await screen.findByTestId("open-questions");

    fireEvent.click(screen.getByTestId("status-tab-all"));
    // 4 个 open/parked 可选,1 个 falsified 不可选
    expect(screen.getAllByTestId("question-select")).toHaveLength(4);
  });

  it("全选后批量停放:串行逐条 POST,只对 open/parked 发", async () => {
    render(<QuestionListView />);
    await screen.findByTestId("open-questions");

    fireEvent.click(screen.getByTestId("question-select-all"));
    expect(screen.getByTestId("batch-park-bar").textContent).toContain("已选 4 个");

    fireEvent.click(screen.getByTestId("batch-park"));
    fireEvent.click(screen.getByTestId("batch-park-confirm"));
    await waitFor(() =>
      expect(screen.getByTestId("batch-progress").textContent).toContain(
        "完成 4 / 4",
      ),
    );
    expect(mockClose).toHaveBeenCalledTimes(4);
    const ids = mockClose.mock.calls.map((c) => c[0]).sort();
    expect(ids).toEqual(
      ["q-open-1", "q-open-selene", "q-parked-1", "q-parked-2"].sort(),
    );
    for (const call of mockClose.mock.calls) {
      expect(call[1].terminal_status).toBe("parked");
    }
  });

  it("批量停放需要二次确认:第一次点只展开确认行,不发出任何 POST", async () => {
    render(<QuestionListView />);
    await screen.findByTestId("open-questions");

    fireEvent.click(screen.getByTestId("question-select-all"));
    fireEvent.click(screen.getByTestId("batch-park"));

    // 确认行出现,POST 一条都没发
    expect(screen.getByTestId("batch-park-confirm")).toBeTruthy();
    expect(screen.getByTestId("batch-park-bar").textContent).toContain("「已停放」里恢复调查");
    expect(mockClose).not.toHaveBeenCalled();

    // 反悔:收起确认行,仍不发请求
    fireEvent.click(screen.getByTestId("batch-park-cancel"));
    expect(screen.queryByTestId("batch-park-confirm")).toBeNull();
    expect(mockClose).not.toHaveBeenCalled();
  });

  it("部分失败:进度照走完,失败明细列出,成功项才移出选择", async () => {
    mockClose.mockImplementation(async (id: string) => {
      if (id === "q-parked-1") throw new Error("409 状态已变");
      return { question: QUESTIONS[0] };
    });
    render(<QuestionListView />);
    await screen.findByTestId("open-questions");

    fireEvent.click(screen.getByTestId("question-select-all"));
    fireEvent.click(screen.getByTestId("batch-park"));
    fireEvent.click(screen.getByTestId("batch-park-confirm"));

    await waitFor(() =>
      expect(screen.getByTestId("batch-progress").textContent).toContain(
        "完成 4 / 4",
      ),
    );
    expect(screen.getByTestId("batch-progress").textContent).toContain("失败 1");
    const failures = screen.getByTestId("batch-failures");
    expect(failures.textContent).toContain("SOL 4h 缺口回补");
    expect(failures.textContent).toContain("409 状态已变");
    // 失败那条仍在选中态 → 重试不用重新找
    expect(screen.getByTestId("batch-park-bar").textContent).toContain("已选 1 个");
  });

  it("已停放的问题有「恢复调查」按钮,直接重新打开", async () => {
    render(<QuestionListView />);
    await screen.findByTestId("parked-questions");

    fireEvent.click(screen.getAllByTestId("reopen-question")[0]);
    await waitFor(() => expect(mockReopen).toHaveBeenCalledTimes(1));
    expect(mockReopen.mock.calls[0][0]).toBe("q-parked-1");
    expect(mockReopen.mock.calls[0][1].reason).toBeTruthy();
  });

  it("筛选写进 URL:刷新/分享链接都能还原同一屏", async () => {
    render(<QuestionListView />);
    await screen.findByTestId("open-questions");

    fireEvent.click(screen.getByTestId("status-tab-terminal"));
    fireEvent.change(screen.getByTestId("question-search"), {
      target: { value: "旧问" },
    });

    const params = new URLSearchParams(window.location.search);
    expect(params.get("status")).toBe("terminal");
    expect(params.get("search")).toBe("旧问");

    // 卸载后带着同一条 URL 重新挂载 = 用户刷新页面的效果
    cleanup();
    render(<QuestionListView />);
    await screen.findByTestId("closed-toggle");
    expect(screen.getByTestId("status-tab-terminal").className).toContain(
      "bg-accent",
    );
    expect(
      (screen.getByTestId("question-search") as HTMLInputElement).value,
    ).toBe("旧问");
  });

  it("默认档不往 URL 写参数:干净地址即默认态", async () => {
    render(<QuestionListView />);
    await screen.findByTestId("open-questions");
    expect(window.location.search).toBe("");
  });
});
