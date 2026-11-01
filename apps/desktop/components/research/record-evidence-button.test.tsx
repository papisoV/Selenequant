import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { RecordEvidenceButton } from "@/components/research/record-evidence-button";

// api-client mock:api=GET,apiPost=POST,按 path 分发
const getMock = vi.fn();
const postMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  api: (...args: unknown[]) => getMock(...args),
  apiPost: (...args: unknown[]) => postMock(...args),
}));

const PREFILL = {
  fact: "回测完成:总收益 12.00%,夏普 1.10",
  suggestedSignal: "confirming",
};

const MISSIONS = {
  missions: [
    {
      id: "mis-1",
      title: "Keltner 重验证",
      subject_type: "strategy",
      subject_id: "st-keltner",
    },
  ],
};

const THESIS = { pillars: [{ id: "pil-1", claim: "突破保持正期望" }] };

describe("RecordEvidenceButton", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    getMock.mockImplementation((path: string) => {
      if (path.includes("/missions?")) return Promise.resolve(MISSIONS);
      if (path.includes("/thesis")) return Promise.resolve(THESIS);
      return Promise.reject(new Error(`unexpected GET ${path}`));
    });
  });

  it("打开对话框 → 拉开放 missions + pillars → fact 预填", async () => {
    render(<RecordEvidenceButton prefill={PREFILL} />);
    fireEvent.click(screen.getByRole("button", { name: /记入 evidence/ }));
    await waitFor(() => {
      expect(getMock).toHaveBeenCalledWith("/api/v1/research/missions?phase=investigation&limit=50");
    });
    await waitFor(() => {
      expect((screen.getByTestId("evidence-fact-input") as HTMLTextAreaElement).value).toBe(
        PREFILL.fact
      );
    });
    const signal = screen.getByTestId("evidence-signal-select") as HTMLSelectElement;
    expect(signal.value).toBe("confirming");
  });

  it("确认写入 → POST evidence 带预填字段 + owner=user", async () => {
    postMock.mockResolvedValue({ evidence_id: "ev-9" });
    render(<RecordEvidenceButton prefill={PREFILL} />);
    fireEvent.click(screen.getByRole("button", { name: /记入 evidence/ }));
    await waitFor(() => {
      expect(screen.getByTestId("evidence-submit")).not.toBeDisabled();
    });
    fireEvent.click(screen.getByTestId("evidence-submit"));
    await waitFor(() => {
      expect(postMock).toHaveBeenCalledWith(
        "/api/v1/research/missions/mis-1/evidence",
        expect.objectContaining({
          pillar_id: "pil-1",
          fact: PREFILL.fact,
          signal: "confirming",
          source_type: "experiment",
          owner: "user",
        })
      );
    });
    expect(await screen.findByTestId("evidence-recorded")).toBeInTheDocument();
  });

  it("无调查中 mission → 显诚实空态,提交不可用", async () => {
    getMock.mockImplementation(() => Promise.resolve({ missions: [] }));
    render(<RecordEvidenceButton prefill={PREFILL} />);
    fireEvent.click(screen.getByRole("button", { name: /记入 evidence/ }));
    await waitFor(() => {
      expect(screen.getByText(/当前没有调查中的 mission/)).toBeInTheDocument();
    });
    expect(screen.queryByTestId("evidence-submit")).not.toBeInTheDocument();
  });
});
