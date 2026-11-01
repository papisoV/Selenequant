import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { PaHeartbeatPanel } from "@/components/system/pa-heartbeat-panel";

// === mock useApiPage =================================
const mockApiData: Record<string, unknown> = {};
vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({
    useApi: (url: string) => ({
      data: mockApiData[url] ?? null,
      error: null,
      isLoading: false,
      mutate: vi.fn(),
    }),
  }),
}));

const mockPutPaHeartbeatSettings = vi.fn(
  async (_cfg: Record<string, unknown>) => undefined,
);
vi.mock("@/lib/api/settings", () => ({
  putPaHeartbeatSettings: (cfg: Record<string, unknown>) =>
    mockPutPaHeartbeatSettings(cfg),
}));

function setSettings(paHeartbeat: unknown) {
  mockApiData["/api/v1/settings"] = { pa_heartbeat: paHeartbeat };
}

describe("PaHeartbeatPanel", () => {
  beforeEach(() => {
    mockApiData["/api/v1/settings"] = null;
    mockPutPaHeartbeatSettings.mockClear();
  });

  it("shows the saved section and states 0 = 不设上限", async () => {
    setSettings({ enabled: true, daily_max: 0, interval: "4h", hour_utc: 6 });

    render(<PaHeartbeatPanel />);
    const toggle = await screen.findByRole("button", { name: "ON" });
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    // 预算 0 必须原样显示 —— 若被兜底成 20,用户就看不到自己设的"无上限"
    expect(
      (screen.getByLabelText("每日上限") as HTMLInputElement).value,
    ).toBe("0");
    expect(screen.getByText("0 = 不设上限")).toBeTruthy();
    expect(
      (screen.getByLabelText("诊断周期") as HTMLSelectElement).value,
    ).toBe("4h");
    expect(
      (screen.getByLabelText("触发小时 (UTC)") as HTMLInputElement).value,
    ).toBe("6");
  });

  it("saves the toggled state and says it applies on the next tick", async () => {
    setSettings({ enabled: false, daily_max: 0, interval: "1h", hour_utc: 0 });

    render(<PaHeartbeatPanel />);
    (await screen.findByRole("button", { name: "OFF" })).click();
    (await screen.findByRole("button", { name: "保存" })).click();

    await waitFor(() => expect(mockPutPaHeartbeatSettings).toHaveBeenCalled());
    expect(mockPutPaHeartbeatSettings.mock.calls[0][0]).toEqual({
      enabled: true,
      daily_max: 0,
      interval: "1h",
      hour_utc: 0,
    });
    // 承诺要具体:保存后不用重启,下一 tick 生效
    expect(await screen.findByText("已保存,下一 tick 起生效")).toBeTruthy();
  });

  it("saving while off says it stopped", async () => {
    setSettings({ enabled: true, daily_max: 5, interval: "2h", hour_utc: 3 });

    render(<PaHeartbeatPanel />);
    (await screen.findByRole("button", { name: "ON" })).click();
    (await screen.findByRole("button", { name: "保存" })).click();

    await waitFor(() => expect(mockPutPaHeartbeatSettings).toHaveBeenCalled());
    expect(mockPutPaHeartbeatSettings.mock.calls[0][0]).toMatchObject({
      enabled: false,
      daily_max: 5,
      interval: "2h",
      hour_utc: 3,
    });
    expect(await screen.findByText("已保存,已停")).toBeTruthy();
  });

  it("clamps a negative budget to 0 rather than sending a negative cap", async () => {
    setSettings({ enabled: true, daily_max: 20, interval: "1h", hour_utc: 0 });

    render(<PaHeartbeatPanel />);
    const input = (await screen.findByLabelText("每日上限")) as HTMLInputElement;
    // react 受控 input:用 fireEvent 改值,模拟用户键入 -3
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.change(input, { target: { value: "-3" } });
    (await screen.findByRole("button", { name: "保存" })).click();

    await waitFor(() => expect(mockPutPaHeartbeatSettings).toHaveBeenCalled());
    expect(mockPutPaHeartbeatSettings.mock.calls[0][0]).toMatchObject({
      daily_max: 0,
    });
  });
});
