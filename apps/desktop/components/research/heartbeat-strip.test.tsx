import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { HeartbeatStrip } from "@/components/research/heartbeat-strip";

const { getHeartbeatStatusMock } = vi.hoisted(() => ({ getHeartbeatStatusMock: vi.fn() }));

vi.mock("@/lib/api/decision-workflow", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/decision-workflow")>(
    "@/lib/api/decision-workflow",
  );
  return { ...actual, getHeartbeatStatus: getHeartbeatStatusMock };
});

// 未来 2 小时开外的窗口 —— 断言只钉"有倒计时",不钉具体秒数(渲染与取数之间
// 会走掉几毫秒,精确字符串在整点边界上必翻车)
function futureStatus(overrides: Record<string, unknown> = {}) {
  return {
    enabled: true,
    attempts_today: 3,
    budget_day: "2026-09-14",
    busy: false,
    next_fire_at: Math.floor(Date.now() / 1000) + 7200,
    config: { hour_utc: 2, daily_max: 30, interval: "1h", htf_interval: null },
    ...overrides,
  };
}

async function renderStrip(status: Record<string, unknown>) {
  getHeartbeatStatusMock.mockResolvedValue(status);
  render(<HeartbeatStrip />);
  // 数据是 mount 后异步到的,等首帧落地
  await screen.findByTestId("heartbeat-strip");
}

beforeEach(() => {
  getHeartbeatStatusMock.mockReset();
});

describe("HeartbeatStrip — US-1B 心跳时间语义", () => {
  it("enabled=false → 整块不渲染(关闭的特性不留空壳)", async () => {
    getHeartbeatStatusMock.mockResolvedValue({ enabled: false });
    render(<HeartbeatStrip />);
    // 给异步 setData 一个落地机会
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId("heartbeat-strip")).not.toBeInTheDocument();
  });

  it("enabled=true → 渲染倒计时 + 上轮结果 + 今日预算", async () => {
    await renderStrip(
      futureStatus({
        last_round: {
          day: "2026-09-14",
          symbols: 3,
          outcomes: { ok: 2, skipped: 1, failed: 0 },
          attempts_today: 3,
          at: Math.floor(Date.now() / 1000) - 300,
        },
      }),
    );

    expect(screen.getByText(/下次诊断窗口/)).toBeInTheDocument();
    expect(screen.getByText(/\d+ 小时/)).toBeInTheDocument();
    expect(screen.getByText(/成功 2 \/ 跳过 1 \/ 失败 0/)).toBeInTheDocument();
    expect(screen.getByText("预算 3/30")).toBeInTheDocument();
  });

  it("next_fire_at 已过期 → 显示'窗口已开'而非负数倒计时", async () => {
    await renderStrip(futureStatus({ next_fire_at: Math.floor(Date.now() / 1000) - 30 }));
    expect(screen.getByText("窗口已开")).toBeInTheDocument();
  });

  it("busy=true → 显示诊断中标记", async () => {
    await renderStrip(futureStatus({ busy: true }));
    expect(screen.getByTestId("heartbeat-busy")).toBeInTheDocument();
    expect(screen.getByText(/诊断中/)).toBeInTheDocument();
  });

  it("last_round=null → 省略上轮一行,预算仍显示", async () => {
    await renderStrip(futureStatus({ last_round: null }));
    expect(screen.queryByText(/上轮/)).not.toBeInTheDocument();
    expect(screen.getByText("预算 3/30")).toBeInTheDocument();
  });

  it("next_fire_at 缺失 → 不渲染倒计时(不编造数字)", async () => {
    await renderStrip(futureStatus({ next_fire_at: null }));
    expect(screen.queryByText(/下次诊断窗口/)).not.toBeInTheDocument();
    expect(screen.getByTestId("heartbeat-strip")).toBeInTheDocument();
  });
});
