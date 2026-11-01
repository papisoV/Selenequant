import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { GovernancePane } from "@/components/research/alpha-mining-governance";
import type { DeployLogEntry } from "@/components/research/alpha-mining-governance";

// getValidationReport 404 → 治理 tab 右栏显示"未生成",不影响左栏部署历史分组测试。
vi.mock("@/lib/api/alpha", () => ({
  getValidationReport: vi.fn(async () => {
    throw new Error("No validation report");
  }),
}));

function renderPane(log: DeployLogEntry[]) {
  return render(
    <GovernancePane
      formulas={[]}
      deployLog={log}
      onDeployFormula={async () => {}}
      onGoValidation={() => {}}
    />,
  );
}

describe("GovernancePane 部署日志批量分组", () => {
  it("batch: 前缀条目归组显示(组标题+成员行),无前缀条目保持平铺", () => {
    const log: DeployLogEntry[] = [
      { target: "alpha_1_abcdef01_0", ok: true, at: "2026-09-21T10:00:00Z", note: "batch:batch_1_abcdef01" },
      { target: "alpha_1_abcdef01_1", ok: false, at: "2026-09-21T10:00:01Z", note: "batch:batch_1_abcdef01 T4 门控拒绝部署 (hold)" },
      { target: "solo_formula", ok: true, at: "2026-09-21T10:00:02Z", note: "T4 放行" },
    ];
    renderPane(log);
    // 批量组标题:批量尾号 + 2 条 + 1 成功
    const group = screen.getByTestId("dep-batch-batch_1_abcdef01");
    expect(within(group).getByText(/批量 abcdef01 · 2 条部署 · 1 成功/)).toBeInTheDocument();
    expect(within(group).getByText("alpha_1_abcdef01_0")).toBeInTheDocument();
    expect(within(group).getByText("alpha_1_abcdef01_1")).toBeInTheDocument();
    // 单会话平铺:未被吸进组
    expect(screen.getByTestId("dep-row-single-0")).toHaveTextContent("solo_formula");
  });

  it("空日志显示引导文案", () => {
    renderPane([]);
    expect(screen.getByText(/尚无部署动作/)).toBeInTheDocument();
  });
});
