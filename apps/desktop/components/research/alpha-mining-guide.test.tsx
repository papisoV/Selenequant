import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { getKline } from "@/lib/api/markets";
import { AlphaMiningGuide } from "@/components/research/alpha-mining-guide";

describe("AlphaMiningGuide 教学文案", () => {
  it("渲染三个教学主题标题(可展开)", () => {
    render(<AlphaMiningGuide />);
    expect(screen.getByText(/新手引导/)).toBeInTheDocument();
    expect(screen.getByText(/怎么看训练曲线/)).toBeInTheDocument();
    expect(screen.getByText(/为什么不能跨周期套策略/)).toBeInTheDocument();
    expect(screen.getByText(/回测的指标是怎么算的/)).toBeInTheDocument();
  });

  it("点击主题展开正文,含 Selene 术语(best_score/avg_reward)", () => {
    render(<AlphaMiningGuide />);
    fireEvent.click(screen.getByText(/怎么看训练曲线/));
    expect(screen.getByText(/best_score 是「到目前为止出现过的最好成绩」的记录/)).toBeInTheDocument();
    expect(screen.getByText(/avg_reward 则是「最近一个训练批次的平均奖励」/)).toBeInTheDocument();
  });

  it("跨周期主题正文强调按周期回测 + 数据源覆盖", () => {
    render(<AlphaMiningGuide />);
    fireEvent.click(screen.getByText(/为什么不能跨周期套策略/));
    expect(screen.getByText(/base_interval/)).toBeInTheDocument();
    expect(screen.getByText(/合成数据/)).toBeInTheDocument();
  });

  it("回测指标主题展开含 8 指标术语", () => {
    render(<AlphaMiningGuide />);
    fireEvent.click(screen.getByText(/回测的指标是怎么算的/));
    expect(screen.getByText(/Sortino/)).toBeInTheDocument();
    expect(screen.getByText(/最大回撤/)).toBeInTheDocument();
    expect(screen.getByText(/滚动夏普/)).toBeInTheDocument();
  });

  it("收起按钮折叠所有正文", () => {
    render(<AlphaMiningGuide />);
    fireEvent.click(screen.getByText(/怎么看训练曲线/));
    expect(screen.getByText(/best_score 是「到目前为止出现过的最好成绩」的记录/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /收起/ }));
    expect(screen.queryByText(/best_score 是「到目前为止出现过的最好成绩」的记录/)).not.toBeInTheDocument();
  });
});
