import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CreateMissionButton } from "@/components/research/create-mission-button";

vi.mock("@/lib/api-client", () => ({
  api: vi.fn(),
  apiPost: vi.fn(),
}));

import { apiPost } from "@/lib/api-client";

describe("CreateMissionButton 透明性批3-10", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("确认后 POST /missions 且带预填字段", async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ mission: { id: "mis-abc123" } });
    render(
      <CreateMissionButton
        prefill={{
          title: "验证 bb_v3 在 BTC/USDT 的表现",
          question: "是否有真实边缘?",
          subject_type: "strategy",
          subject_id: "strat-bb-v3",
        }}
      />
    );
    fireEvent.click(screen.getByText("开新实验验证"));
    fireEvent.click(screen.getByTestId("mission-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("mission-created")).toBeInTheDocument()
    );
    expect(apiPost).toHaveBeenCalledWith("/api/v1/research/missions", {
      title: "验证 bb_v3 在 BTC/USDT 的表现",
      question: "是否有真实边缘?",
      subject_type: "strategy",
      subject_id: "strat-bb-v3",
    });
  });

  it("标题清空后提交按钮禁用", () => {
    render(
      <CreateMissionButton
        prefill={{
          title: "t",
          question: "q",
          subject_type: "market",
          subject_id: "BTC/USDT",
        }}
      />
    );
    fireEvent.click(screen.getByText("开新实验验证"));
    const input = screen.getByTestId("mission-title-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "" } });
    expect(
      (screen.getByTestId("mission-submit") as HTMLButtonElement).disabled
    ).toBe(true);
  });

  it("创建成功后显示 mission id", async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ mission: { id: "mis-x1" } });
    render(
      <CreateMissionButton
        prefill={{
          title: "t",
          question: "q",
          subject_type: "system",
          subject_id: "sys-1",
        }}
      />
    );
    fireEvent.click(screen.getByText("开新实验验证"));
    fireEvent.click(screen.getByTestId("mission-submit"));
    await waitFor(() =>
      expect(screen.getByText(/mis-x1/)).toBeInTheDocument()
    );
  });
});
