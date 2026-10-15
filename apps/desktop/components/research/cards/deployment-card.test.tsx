// US-3 部署卡状态 pill:圆点 + 中文标签(running/paused/stopped 三态)。
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { DeploymentCard } from "./deployment-card";
import type { DeploymentSummary } from "@/lib/api/strategies";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const stopMock = vi.fn(() => Promise.resolve());
vi.mock("@/lib/api/strategies", async (importOriginal) => {
  const orig = await importOriginal<typeof import("@/lib/api/strategies")>();
  return { ...orig, stopDeployment: () => stopMock() };
});

const d = (status: string): DeploymentSummary => ({
  id: `dep-${status}`,
  strategy_id: `strat-${status}`,
  strategy_name: "测试策略",
  status,
  mode: "paper",
  strategy_version: 1,
  account_id: "paper-default",
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("DeploymentCard US-3 状态 pill", () => {
  it("running → 圆点 + 「运行中」", () => {
    render(<DeploymentCard d={d("running")} />);
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByTestId("deploy-status-dot")).toBeInTheDocument();
  });

  it("paused → 「暂停」", () => {
    render(<DeploymentCard d={d("paused")} />);
    expect(screen.getByText("暂停")).toBeInTheDocument();
  });

  it("stopped → 「已停止」", () => {
    render(<DeploymentCard d={d("stopped")} />);
    expect(screen.getByText("已停止")).toBeInTheDocument();
  });

  it("未知 status → 原样显示且不抛错", () => {
    render(<DeploymentCard d={d("weird_state")} />);
    expect(screen.getByText("weird_state")).toBeInTheDocument();
    expect(screen.getByTestId("deploy-status-dot")).toBeInTheDocument();
  });
});

describe("P0 checkbox 真相回填(d.flags)", () => {
  it("d.flags 开着 → checkbox 初始 checked(不再硬编码 false 撒谎)", () => {
    render(
      <DeploymentCard
        d={{
          ...d("running"),
          flags: { ai_gate_enabled: true, bias_guard_enabled: false },
        }}
      />,
    );
    const [ai, bias] = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(ai.checked).toBe(true);
    expect(bias.checked).toBe(false);
  });

  it("d.flags 缺字段 → 默认 false(旧后端无 flags 注入时向后兼容)", () => {
    render(<DeploymentCard d={d("running")} />);
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.every((b) => !b.checked)).toBe(true);
  });
});

describe("US-102 运行时健康徽标", () => {
  it("runtime 有数 → 显示 bars/信号/错误", () => {
    render(
      <DeploymentCard
        d={{
          ...d("running"),
          runtime: {
            bar_count: 42,
            signals_emitted: 3,
            errors: 0,
            last_signal_ts: null,
          },
        }}
      />,
    );
    expect(screen.getByText(/42 bars · 3 信号/)).toBeInTheDocument();
    expect(screen.queryByText(/错误/)).not.toBeInTheDocument();
  });

  it("runtime.errors > 0 → 追加错误计数(诚实暴露)", () => {
    render(
      <DeploymentCard
        d={{
          ...d("running"),
          runtime: {
            bar_count: 10,
            signals_emitted: 1,
            errors: 2,
            last_signal_ts: null,
          },
        }}
      />,
    );
    expect(screen.getByText(/2 错误/)).toBeInTheDocument();
  });

  it("runtime=null 且 status=running → 显示「等待运行时数据」", () => {
    render(<DeploymentCard d={d("running")} />);
    expect(screen.getByText("等待运行时数据…")).toBeInTheDocument();
  });

  it("runtime 缺失(旧后端)且 status=running → 同样显示等待文案", () => {
    render(<DeploymentCard d={{ ...d("running"), runtime: undefined }} />);
    expect(screen.getByText("等待运行时数据…")).toBeInTheDocument();
  });

  it("runtime=null 且非 running → 不显示健康行", () => {
    render(<DeploymentCard d={d("stopped")} />);
    expect(screen.queryByText(/等待运行时数据/)).not.toBeInTheDocument();
    expect(screen.queryByText(/bars/)).not.toBeInTheDocument();
  });
});

describe("P1 停止部署两步确认(vhero-kill 同款)", () => {
  it("首击只进确认态(确认停止?),不发 stop 请求", () => {
    render(<DeploymentCard d={d("running")} />);
    fireEvent.click(screen.getByRole("button", { name: "停止" }));
    expect(screen.getByRole("button", { name: "确认停止?" })).toBeInTheDocument();
    expect(stopMock).not.toHaveBeenCalled();
  });

  it("3s 内二击 → 执行 stopDeployment", () => {
    render(<DeploymentCard d={d("running")} />);
    fireEvent.click(screen.getByRole("button", { name: "停止" }));
    fireEvent.click(screen.getByRole("button", { name: "确认停止?" }));
    expect(stopMock).toHaveBeenCalledTimes(1);
  });

  it("3s 超时 → 确认态归位回「停止」,此时单击不执行", () => {
    // 只 fake setTimeout/clearTimeout:vitest 4 默认连 queueMicrotask 一起 fake,
    // React 并发调度器死锁 → render 不提交、act 内 setState 不 flush。
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      render(<DeploymentCard d={d("running")} />);
      fireEvent.click(screen.getByRole("button", { name: "停止" }));
      expect(screen.getByRole("button", { name: "确认停止?" })).toBeInTheDocument();
      // setTimeout 回调里的 setState 必须包 act(),否则 React 批处理不跑、UI 不归位
      act(() => {
        vi.advanceTimersByTime(3000);
      });
      expect(screen.getByRole("button", { name: "停止" })).toBeInTheDocument();
      // 归位后再单击 = 新一轮首击,仍不执行
      fireEvent.click(screen.getByRole("button", { name: "停止" }));
      expect(screen.getByRole("button", { name: "确认停止?" })).toBeInTheDocument();
      expect(stopMock).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
