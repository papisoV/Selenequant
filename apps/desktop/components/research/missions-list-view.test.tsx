import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

import { MissionsListView } from "@/components/research/missions-list-view";
import type { MissionSummary } from "@/components/research/missions-types";

const m = (over: Partial<MissionSummary> & { id: string }): MissionSummary => ({
  title: "BTC PA 心跳诊断",
  question: "BTC 1h 当前结构是否转为下行?",
  phase: "needs_input",
  subject_type: "market",
  subject_id: "BTC/USDT",
  updated_at: "2026-10-24T10:00:00Z",
  ...over,
});

const MISSIONS: MissionSummary[] = [
  m({ id: "msn-1" }),
  m({ id: "msn-2", phase: "investigation", title: "ETH 上影线" }),
  m({ id: "msn-3", phase: "validation", title: "SOL 4h 缺口" }),
  m({ id: "msn-4", phase: "parked", title: "旧的心跳", subject_id: "DOGE/USDT" }),
  m({ id: "msn-5", phase: "parked", title: "另一条旧的" }),
  m({ id: "msn-6", phase: "resolved", title: "早结案的" }),
  m({
    id: "msn-7",
    phase: "investigation",
    title: "重复开案的代表卡",
    duplicates: 2,
    duplicate_ids: ["msn-7a", "msn-7b"],
  }),
];

beforeEach(() => {
  push.mockClear();
  // 筛选状态存在 URL 里 → 用例之间必须清地址栏,否则上一条的 ?phase=… 会泄漏过来。
  window.history.replaceState({}, "", "/research/missions");
});

describe("MissionsListView 管理面", () => {
  it("默认只显示进行中三态,parked/resolved 不铺满列表", () => {
    render(<MissionsListView missions={MISSIONS} loading={false} />);
    expect(screen.queryByText("旧的心跳")).toBeNull();
    expect(screen.queryByText("早结案的")).toBeNull();
    expect(screen.getByText("BTC PA 心跳诊断")).toBeTruthy();
    expect(screen.getByText("ETH 上影线")).toBeTruthy();
  });

  it("切到「已停放」只显示 parked,并给出总数", () => {
    render(<MissionsListView missions={MISSIONS} loading={false} />);
    fireEvent.click(screen.getByTestId("mission-phase-parked"));
    expect(screen.getByText("旧的心跳")).toBeTruthy();
    expect(screen.getByText("另一条旧的")).toBeTruthy();
    expect(screen.queryByText("BTC PA 心跳诊断")).toBeNull();
  });

  it("搜索匹配标题、标的与问句三个面", () => {
    render(<MissionsListView missions={MISSIONS} loading={false} />);

    fireEvent.change(screen.getByTestId("mission-search"), {
      target: { value: "eth" },
    });
    expect(screen.getByText("ETH 上影线")).toBeTruthy();
    expect(screen.queryByText("BTC PA 心跳诊断")).toBeNull();

    // 标的命中:DOGE 只出现在已停放那条的 subject_id 上,但默认档不含 parked
    fireEvent.change(screen.getByTestId("mission-search"), {
      target: { value: "DOGE" },
    });
    expect(screen.getByTestId("mission-no-match")).toBeTruthy();

    fireEvent.click(screen.getByTestId("mission-phase-all"));
    expect(screen.getByText("旧的心跳")).toBeTruthy();

    // 问句命中
    fireEvent.change(screen.getByTestId("mission-search"), {
      target: { value: "结构是否转为下行" },
    });
    expect(screen.getByText("BTC PA 心跳诊断")).toBeTruthy();
  });

  it("重复开案用后端 duplicates,可展开列出被折叠的 id", () => {
    render(<MissionsListView missions={MISSIONS} loading={false} />);
    expect(screen.getByTestId("mission-duplicates").textContent).toContain("×3");

    fireEvent.click(screen.getByTestId("mission-duplicates-toggle"));
    const ids = screen.getByTestId("mission-duplicate-ids");
    expect(ids.textContent).toContain("msn-7a");
    expect(ids.textContent).toContain("msn-7b");

    fireEvent.click(screen.getByText("msn-7a"));
    expect(push).toHaveBeenCalledWith("/research/missions?id=msn-7a");
  });

  it("没有任何重复时不渲染展开按钮(不造假分组)", () => {
    render(<MissionsListView missions={MISSIONS} loading={false} />);
    // 只有 msn-7 带 duplicates,其余卡片不该出现 x1 之类的假徽章
    expect(screen.getAllByTestId("mission-duplicates")).toHaveLength(1);
    expect(screen.getAllByTestId("mission-duplicates-toggle")).toHaveLength(1);
  });

  it("提示被隐藏的 parked/resolved 数量,而不是假装它们不存在", () => {
    render(<MissionsListView missions={MISSIONS} loading={false} />);
    const filters = screen.getByTestId("mission-filters");
    expect(filters.textContent).toContain("另有 2 条已停放");
    expect(filters.textContent).toContain("1 条已结案");
  });

  it("筛选写进 URL:刷新/分享链接都能还原同一屏", () => {
    render(<MissionsListView missions={MISSIONS} loading={false} />);
    fireEvent.click(screen.getByTestId("mission-phase-parked"));
    fireEvent.change(screen.getByTestId("mission-search"), {
      target: { value: "旧" },
    });

    const params = new URLSearchParams(window.location.search);
    expect(params.get("phase")).toBe("parked");
    expect(params.get("search")).toBe("旧");

    // 带着同一条 URL 重新挂载 = 用户刷新页面
    cleanup();
    render(<MissionsListView missions={MISSIONS} loading={false} />);
    expect(screen.getByTestId("mission-phase-parked").className).toContain(
      "bg-accent",
    );
    expect(
      (screen.getByTestId("mission-search") as HTMLInputElement).value,
    ).toBe("旧");
  });

  it("地址栏塞了非法 phase 值时回落默认档,不渲染空列表", () => {
    window.history.replaceState({}, "", "/research/missions?phase=bogus");
    render(<MissionsListView missions={MISSIONS} loading={false} />);
    expect(screen.getByText("BTC PA 心跳诊断")).toBeTruthy();
    expect(screen.getByTestId("mission-phase-active").className).toContain(
      "bg-accent",
    );
  });

  it("带参数的地址首帧就按 URL 渲染,不需要任何点击", () => {
    window.history.replaceState({}, "", "/research/missions?phase=parked&search=旧");
    render(<MissionsListView missions={MISSIONS} loading={false} />);
    expect(screen.getByTestId("mission-phase-parked").className).toContain(
      "bg-accent",
    );
    expect(screen.getByText("旧的心跳")).toBeTruthy();
    expect(screen.queryByText("BTC PA 心跳诊断")).toBeNull();
  });
});
