import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemorySurfacePage } from "@/components/research/memory-surface-page";

const { useApiMock } = vi.hoisted(() => ({ useApiMock: vi.fn() }));

vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({ useApi: useApiMock }),
}));

function mockApi(data: unknown, overrides: Record<string, unknown> = {}) {
  useApiMock.mockReturnValue({
    data: data as never,
    error: undefined,
    isLoading: false,
    ...overrides,
  });
}

const episodeRow = {
  kind: "episode",
  id: "ep-1",
  title: "实跑段",
  subject_type: "strategy",
  subject_id: "strat-x",
  boundary_start: "2026-08-01T00:00:00+00:00",
  boundary_end: "2026-08-31T00:00:00+00:00",
  recorded: {},
  occurred_at: "2026-08-31T00:00:00+00:00",
  created_at: "2026-08-31T00:00:00+00:00",
};

const evidenceRow = {
  kind: "evidence",
  id: "ev-1",
  episode_id: "ep-1",
  mode: "RECORDED",
  payload: { pnl: -0.07 },
  source_ref: "bt-f4e357b6",
  observed_at: "2026-08-15T00:00:00+00:00",
  created_at: "2026-08-15T00:00:00+00:00",
};

const knowledgeEntry = {
  kind: "hypothesis",
  id: "hyp-1",
  text: "配置漂移导致亏损",
  status: "supported",
  falsified: false,
  created_at: "2026-09-01T00:00:00+00:00",
  question_id: "q-1",
  seq: null,
  origin: null,
  chain: [evidenceRow],
  used_by: [
    {
      revision_id: "rev-2",
      seq: 2,
      question_id: "q-1",
      content: "第二版:确认配置漂移",
      actor: "selene",
      created_at: "2026-09-02T00:00:00+00:00",
    },
  ],
  used_count: 1,
  last_used_at: "2026-09-02T00:00:00+00:00",
};

const clueEntry = {
  kind: "revision",
  id: "rev-1",
  text: "第一版:怀疑配置漂移",
  status: null,
  falsified: false,
  created_at: "2026-09-01T00:00:00+00:00",
  question_id: "q-1",
  seq: 1,
  origin: "user",
  chain: [],
  used_by: [],
  used_count: 0,
  last_used_at: null,
};

const falsifiedEntry = {
  ...knowledgeEntry,
  id: "hyp-f",
  text: "均值回归假设",
  status: "refuted",
  falsified: true,
  chain: [],
  used_by: [],
  used_count: 0,
  last_used_at: null,
};

const revisionEntry = {
  kind: "revision",
  id: "rev-3",
  text: "第三版:引用实跑 Episode",
  status: null,
  falsified: false,
  created_at: "2026-09-03T00:00:00+00:00",
  question_id: "q-1",
  seq: 3,
  origin: "selene",
  chain: [episodeRow],
  used_by: [],
  used_count: 0,
  last_used_at: null,
};

const payload = {
  knowledge: [knowledgeEntry, revisionEntry],
  clues: [clueEntry],
  recently_used: {
    revision_id: "rev-2",
    seq: 2,
    question_id: "q-1",
    content: "第二版:确认配置漂移",
    created_at: "2026-09-02T00:00:00+00:00",
  },
  counts: { knowledge: 2, clues: 1 },
};

describe("MemorySurfacePage", () => {
  beforeEach(() => {
    useApiMock.mockReset();
  });

  it("错误态:429 限流与后端挂文案区分", () => {
    mockApi(null, { error: Object.assign(new Error("429"), { status: 429 }), isLoading: false });
    render(<MemorySurfacePage />);
    expect(screen.getByText(/限流/)).toBeTruthy();
    mockApi(null, { error: Object.assign(new Error("down"), { status: 503 }), isLoading: false });
    render(<MemorySurfacePage />);
    expect(screen.getByText(/后端不可用/)).toBeTruthy();
  });

  it("分层渲染:既有知识上第一屏,线索收进折叠条", () => {
    mockApi(payload);
    render(<MemorySurfacePage />);
    expect(screen.getByText("配置漂移导致亏损")).toBeTruthy();
    // 线索在折叠条里,未展开时不可见
    expect(screen.queryByText("第一版:怀疑配置漂移")).toBeNull();
    expect(screen.getByText(/待检验线索 · 1 条/)).toBeTruthy();
  });

  it("行为后果行:used_count=1 显示金色次数;线索折叠条展开后可见 0 次条目", () => {
    mockApi(payload);
    render(<MemorySurfacePage />);
    expect(screen.getByText(/已改变 1 次回答/)).toBeTruthy();
    // 线索折叠条:展开后单行视图可见条目文本与时间
    fireEvent.click(screen.getByText(/待检验线索 · 1 条/));
    expect(screen.getByText("第一版:怀疑配置漂移")).toBeTruthy();
  });

  it("证伪条目:已证伪 badge 出现", () => {
    mockApi({
      ...payload,
      knowledge: [falsifiedEntry, revisionEntry],
      counts: { knowledge: 2, clues: 1 },
    });
    render(<MemorySurfacePage />);
    expect(screen.getByText("已证伪")).toBeTruthy();
  });

  it("展开态:点击展开证据链渲染 Episode/Evidence 卡,零二次请求", () => {
    mockApi(payload);
    render(<MemorySurfacePage />);
    // revision 条目的 chain=[episodeRow] → 展开revisionEntry 的卡
    fireEvent.click(screen.getByText(/引用实跑 Episode/));
    // 第二张卡是 revision 条目(chain=[episodeRow]) → 展开它
    fireEvent.click(screen.getAllByText("展开证据链 →")[1]);
    // 屏 B:episode 卡
    expect(screen.getByText(/已发生,不可改/)).toBeTruthy();
    expect(screen.getByText("实跑段")).toBeTruthy();
    // 行为后果时间线在展开面板内(rev-3 无引用 → 空态文案)
    expect(screen.getByText("尚无下游回答引用此条。")).toBeTruthy();
  });

  it("空态:两池皆空显示引导文案", () => {
    mockApi({
      knowledge: [],
      clues: [],
      recently_used: null,
      counts: { knowledge: 0, clues: 0 },
    });
    render(<MemorySurfacePage />);
    expect(screen.getByText(/尚无既有知识/)).toBeTruthy();
  });

  it("header:最近一次被用到(recently_used)显示", () => {
    mockApi(payload);
    render(<MemorySurfacePage />);
    expect(screen.getByText(/最近一次被用到/)).toBeTruthy();
  });

  it("origin chip:selene 渲染 AI 组织;hypothesis(null)无 chip", () => {
    mockApi(payload);
    render(<MemorySurfacePage />);
    // revisionEntry origin=selene → "AI 组织" chip 可见
    expect(screen.getByText("AI 组织")).toBeTruthy();
    // knowledgeEntry(hypothesis)origin=null → 无 chip
    expect(screen.queryByText("人写")).toBeNull();
    expect(screen.queryByText("系统")).toBeNull();
  });

  it("origin chip:user 渲染 人写", () => {
    mockApi({
      ...payload,
      knowledge: [
        {
          ...revisionEntry,
          id: "rev-u",
          text: "人工写的结论",
          origin: "user",
          chain: [],
        },
      ],
      counts: { knowledge: 1, clues: 1 },
    });
    render(<MemorySurfacePage />);
    expect(screen.getByText("人写")).toBeTruthy();
  });

  it("行为后果时间线:used_by 条目带 actor=selene 缀 · AI 来源", () => {
    mockApi(payload);
    render(<MemorySurfacePage />);
    // 展开 knowledgeEntry 的详情面板 → used_by 行(rev-2, actor=selene)
    fireEvent.click(screen.getAllByText("展开证据链 →")[0]);
    expect(screen.getByText("· AI")).toBeTruthy();
  });
});
