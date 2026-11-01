import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { DeskPage } from "@/components/research/desk-page";
import { fetchChanges, type ChangeItem } from "@/lib/research/changes";
import { LAST_VISIT_KEY } from "@/lib/research/last-visit";

vi.mock("@/lib/research/changes", async () => {
  const actual = await vi.importActual<typeof import("@/lib/research/changes")>(
    "@/lib/research/changes",
  );
  return { ...actual, fetchChanges: vi.fn() };
});

vi.mock("@/lib/api/research-graph", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/api/research-graph")
  >("@/lib/api/research-graph");
  return { ...actual, getGraphIntegrity: vi.fn() };
});

import { getGraphIntegrity } from "@/lib/api/research-graph";

const mockIntegrity = vi.mocked(getGraphIntegrity);

const okIntegrity = {
  ok: true,
  violations: [],
  scope_pressure: [],
  counts: { questions: 3, episodes: 12, evidence: 20, edges: 18, decisions: 1 },
};

const mockFetch = vi.mocked(fetchChanges);

const item = (over: Partial<ChangeItem>): ChangeItem => ({
  id: "chg-1",
  tier: 2,
  occurred_at: "2026-09-23T08:00:00",
  source: "event",
  kind: "closed",
  question_id: "q-1",
  question_title: "BTC 突破是否有效",
  question_status: "closed",
  actor: "user",
  detail: {},
  epistemic: {
    source: "research_record",
    actor: "user",
    cites_evidence: false,
    evidence_mode: null,
  },
  consequence: "这个问题当前判断已定稿",
  needs_director: false,
  ...over,
});

const result = (changes: ChangeItem[]) => ({
  changes,
  counts: {
    t1: changes.filter((c) => c.tier === 1).length,
    t2: changes.filter((c) => c.tier === 2).length,
    t3: changes.filter((c) => c.tier === 3).length,
  },
  server_now: "2026-09-23T12:00:00+00:00",
});

beforeEach(() => {
  mockFetch.mockReset();
  mockIntegrity.mockReset();
  mockIntegrity.mockResolvedValue(okIntegrity);
  localStorage.clear();
});

it("空 changes → 空态文案", async () => {
  mockFetch.mockResolvedValue(result([]));
  render(<DeskPage />);
  expect(
    await screen.findByText("这段时间没有变化"),
  ).toBeInTheDocument();
  expect(screen.queryByTestId("desk-change")).toBeNull();
});

it("tier1/2/3 三区块渲染,tier3 默认收起", async () => {
  mockFetch.mockResolvedValue(
    result([
      item({ id: "p", tier: 1, kind: "decision_pending", source: "decision" }),
      item({ id: "r", tier: 2, kind: "revision", source: "revision" }),
      item({ id: "e", tier: 3, kind: "evidence", source: "evidence" }),
    ]),
  );
  render(<DeskPage />);
  expect(await screen.findByText("待 Director 处理")).toBeInTheDocument();
  expect(screen.getByText("判断变化")).toBeInTheDocument();
  expect(screen.getByText("展开进展记录(1)")).toBeInTheDocument();
  // tier3 折叠态:条目不在 DOM
  expect(screen.queryByText("新证据")).toBeNull();

  fireEvent.click(screen.getByText("展开进展记录(1)"));
  expect(screen.getByText("新证据")).toBeInTheDocument();
});

it("点击条目 → 深链 /research/questions?q={id}", async () => {
  mockFetch.mockResolvedValue(result([item({ question_id: "q-deep-9" })]));
  render(<DeskPage />);
  const row = await screen.findByTestId("desk-change");
  expect(row.getAttribute("href")).toBe("/research/questions?q=q-deep-9");
});

it("加载成功后写入 lastVisitedAt 锚点", async () => {
  mockFetch.mockResolvedValue(result([]));
  render(<DeskPage />);
  await screen.findByText("这段时间没有变化");
  expect(localStorage.getItem(LAST_VISIT_KEY)).toBe(
    "2026-09-23T12:00:00+00:00",
  );
});

it("首访(无锚点)→ since 是 7 天前,标题提示回看窗口", async () => {
  mockFetch.mockResolvedValue(result([]));
  render(<DeskPage />);
  await screen.findByText(`最近 7 天的变化`);
  const since = mockFetch.mock.calls[0][0];
  expect(since).not.toBeNull();
  expect(Date.now() - Date.parse(since as string)).toBeGreaterThan(
    6.9 * 86400_000,
  );
});

it("有锚点 → since 等于锚点值", async () => {
  localStorage.setItem(LAST_VISIT_KEY, "2026-09-23T10:00:00+00:00");
  mockFetch.mockResolvedValue(result([]));
  render(<DeskPage />);
  await screen.findByText("自上次以来的变化");
  expect(mockFetch.mock.calls[0][0]).toBe("2026-09-23T10:00:00+00:00");
});

it("epistemic 徽章:研究记录/经验推断/探针/引证据", async () => {
  mockFetch.mockResolvedValue(
    result([
      item({
        id: "b1",
        epistemic: {
          source: "learned_experience",
          actor: "selene",
          cites_evidence: true,
          evidence_mode: "RECORDED",
        },
      }),
      item({
        id: "b2",
        epistemic: {
          source: "research_record",
          actor: "user",
          cites_evidence: false,
          evidence_mode: null,
        },
      }),
    ]),
  );
  render(<DeskPage />);
  await screen.findAllByTestId("desk-change");
  expect(screen.getByText("探针")).toBeInTheDocument();
  expect(screen.getByText("引证据")).toBeInTheDocument();
  expect(screen.getByText("研究记录")).toBeInTheDocument();
});

it("防退化红线:无 Alert/Notification/未读数词汇", async () => {
  mockFetch.mockResolvedValue(
    result([item({ tier: 1, source: "decision" })]),
  );
  const { container } = render(<DeskPage />);
  await screen.findByTestId("desk-change");
  expect(container.textContent).not.toMatch(/alert|notification|未读/i);
});

it("轮询:30s 重取恒用 mount 时的同一 since,锚点不被轮询推进", async () => {
  vi.useFakeTimers();
  try {
    localStorage.setItem(LAST_VISIT_KEY, "2026-09-23T10:00:00+00:00");
    mockFetch.mockResolvedValue(result([]));
    render(<DeskPage />);
    await vi.advanceTimersByTimeAsync(0); // 冲刷首次加载微任务
    expect(screen.getByText("自上次以来的变化")).toBeInTheDocument();
    expect(mockFetch).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(30_000 + 10);
    await vi.advanceTimersByTimeAsync(30_000 + 10);
    // 3 次调用,全部同一 since(锚点纪律:轮询不改写锚点)
    expect(mockFetch).toHaveBeenCalledTimes(3);
    for (const call of mockFetch.mock.calls) {
      expect(call[0]).toBe("2026-09-23T10:00:00+00:00");
    }
    // 轮询成功不写锚点(仍是最初那次写的 server_now)
    expect(localStorage.getItem(LAST_VISIT_KEY)).toBe(
      "2026-09-23T12:00:00+00:00",
    );
  } finally {
    vi.useRealTimers();
  }
});

it("unmount 时锚点推进到最后一次 server_now", async () => {
  vi.useFakeTimers();
  try {
    mockFetch.mockResolvedValue(result([]));
    const { unmount } = render(<DeskPage />);
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText("最近 7 天的变化")).toBeInTheDocument();
    // 首次加载写了 12:00;再让一次轮询带回来更新的 server_now
    mockFetch.mockResolvedValue({
      changes: [],
      counts: { t1: 0, t2: 0, t3: 0 },
      server_now: "2026-09-23T13:30:00+00:00",
    });
    await vi.advanceTimersByTimeAsync(30_000 + 10);
    unmount();
    expect(localStorage.getItem(LAST_VISIT_KEY)).toBe(
      "2026-09-23T13:30:00+00:00",
    );
  } finally {
    vi.useRealTimers();
  }
});

it("integrity ok → 页脚一行账本完整 + counts", async () => {
  mockFetch.mockResolvedValue(result([]));
  render(<DeskPage />);
  const section = await screen.findByTestId("desk-integrity");
  expect(section).toHaveTextContent("账本完整性 ✓");
  expect(section).toHaveTextContent("questions=3");
  expect(section).toHaveTextContent("episodes=12");
  expect(screen.queryByTestId("integrity-violation")).toBeNull();
});

it("integrity 违规 → 标题警示色 + violations 列表;scope_pressure 折叠一行", async () => {
  mockIntegrity.mockResolvedValue({
    ok: false,
    violations: [
      {
        rule: "dangling_edge",
        object_id: "edge-9",
        detail: "from endpoint not found: q-ghost",
      },
    ],
    scope_pressure: [
      {
        rule: "scope_pressure",
        object_id: "q-1",
        detail: "propose EXPAND, do not auto-absorb",
      },
    ],
    counts: { questions: 3, episodes: 12, evidence: 20, edges: 18 },
  });
  mockFetch.mockResolvedValue(result([]));
  render(<DeskPage />);
  const section = await screen.findByTestId("desk-integrity");
  expect(section).toHaveTextContent("账本完整性 · 1 条违规");
  const v = await screen.findByTestId("integrity-violation");
  expect(v).toHaveTextContent("dangling_edge");
  expect(v).toHaveTextContent("edge-9");
  expect(screen.getByTestId("scope-pressure")).toHaveTextContent(
    "scope_pressure:1 条(建议扩问,非错误)",
  );
});

it("integrity 接口失败 → 显示查询失败文案,不炸页面", async () => {
  mockIntegrity.mockRejectedValue(new Error("backend down"));
  mockFetch.mockResolvedValue(result([]));
  render(<DeskPage />);
  expect(await screen.findByText("完整性查询失败")).toBeInTheDocument();
  expect(await screen.findByText("这段时间没有变化")).toBeInTheDocument();
});
