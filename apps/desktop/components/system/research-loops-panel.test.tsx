import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ResearchLoopsPanel } from "@/components/system/research-loops-panel";

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

const mockPutWorker = vi.fn(async (_cfg: Record<string, unknown>) => undefined);
const mockPutAutorenew = vi.fn(
  async (_cfg: Record<string, unknown>) => undefined,
);
const mockPutSensor = vi.fn(async (_cfg: Record<string, unknown>) => undefined);
vi.mock("@/lib/api/settings", () => ({
  putMissionWorkerSettings: (cfg: Record<string, unknown>) =>
    mockPutWorker(cfg),
  putResearchAutorenewSettings: (cfg: Record<string, unknown>) =>
    mockPutAutorenew(cfg),
  putResearchSensorSettings: (cfg: Record<string, unknown>) =>
    mockPutSensor(cfg),
}));

function setSettings(worker: unknown, autorenew: unknown) {
  mockApiData["/api/v1/settings"] = {
    mission_worker: worker,
    research_autorenew: autorenew,
    research_sensor: { enabled: false, interval_minutes: 60, question_limit: 200 },
  };
}

const OFF_WORKER = {
  enabled: false,
  interval_minutes: 15,
  max_rounds_per_mission: 3,
};
const OFF_AUTORENEW = {
  enabled: false,
  interval_minutes: 30,
  max_per_round: 3,
  auto_promote: true,
};

describe("ResearchLoopsPanel", () => {
  beforeEach(() => {
    mockApiData["/api/v1/settings"] = null;
    mockPutWorker.mockClear();
    mockPutAutorenew.mockClear();
    mockPutSensor.mockClear();
  });

  it("shows both loops off and names the default in the title", async () => {
    setSettings(OFF_WORKER, OFF_AUTORENEW);

    render(<ResearchLoopsPanel />);

    const workerToggle = await screen.findByRole("button", {
      name: "调查 worker 启用",
    });
    const autorenewToggle = screen.getByRole("button", {
      name: "自动续研启用",
    });
    expect(workerToggle.getAttribute("aria-pressed")).toBe("false");
    expect(autorenewToggle.getAttribute("aria-pressed")).toBe("false");
    // 标题必须说清默认值 —— 否则用户以为坏了
    expect(screen.getByText(/默认全关/)).toBeTruthy();
  });

  it("saves both sections so the two-loop chain stays consistent", async () => {
    setSettings(OFF_WORKER, OFF_AUTORENEW);

    render(<ResearchLoopsPanel />);
    (await screen.findByRole("button", { name: "调查 worker 启用" })).click();
    screen.getByRole("button", { name: "自动续研启用" }).click();
    (await screen.findByRole("button", { name: "保存" })).click();

    await waitFor(() => expect(mockPutWorker).toHaveBeenCalled());
    await waitFor(() => expect(mockPutAutorenew).toHaveBeenCalled());
    expect(mockPutWorker.mock.calls[0][0]).toMatchObject({ enabled: true });
    expect(mockPutAutorenew.mock.calls[0][0]).toMatchObject({
      enabled: true,
      auto_promote: true,
    });
    expect(await screen.findByText("已保存,下一轮起生效")).toBeTruthy();
  });

  it("warns that autorenew alone will not advance the missions it opens", async () => {
    setSettings(OFF_WORKER, { ...OFF_AUTORENEW, enabled: true });

    render(<ResearchLoopsPanel />);

    // 这是这条链最容易误解的一点:自动开立了任务,但没人推进
    expect(
      await screen.findByText(/两个是一对/),
    ).toBeTruthy();
  });

  it("clamps out-of-range numbers instead of sending them", async () => {
    setSettings(OFF_WORKER, OFF_AUTORENEW);

    render(<ResearchLoopsPanel />);
    const interval = (await screen.findByLabelText(
      "调查 worker 轮询间隔(分钟)",
    )) as HTMLInputElement;
    fireEvent.change(interval, { target: { value: "0" } });
    const rounds = screen.getByLabelText(
      "单任务轮次上限",
    ) as HTMLInputElement;
    fireEvent.change(rounds, { target: { value: "999" } });
    (await screen.findByRole("button", { name: "保存" })).click();

    await waitFor(() => expect(mockPutWorker).toHaveBeenCalled());
    expect(mockPutWorker.mock.calls[0][0]).toMatchObject({
      interval_minutes: 1,
      max_rounds_per_mission: 20,
    });
  });

  it("says both loops are stopped when saving with both off", async () => {
    setSettings({ ...OFF_WORKER, enabled: true }, OFF_AUTORENEW);

    render(<ResearchLoopsPanel />);
    (await screen.findByRole("button", { name: "调查 worker 启用" })).click();
    (await screen.findByRole("button", { name: "保存" })).click();

    expect(await screen.findByText("已保存,循环都停着")).toBeTruthy();
  });
});
