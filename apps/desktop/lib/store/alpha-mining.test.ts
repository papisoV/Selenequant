import { describe, expect, it, beforeEach, vi } from "vitest";
import { useAlphaMiningStore, selectActiveSessions, isActive } from "./alpha-mining";
import * as alphaApi from "@/lib/api/alpha";

vi.mock("@/lib/api/alpha", () => ({
  listMiningSessions: vi.fn(),
}));

const running = { session_id: "alpha_run", status: "running" as const, step: 5 };
const stopped = { session_id: "alpha_stop", status: "stopped" as const, step: 10 };
const completed = { session_id: "alpha_done", status: "completed" as const, step: 100 };

beforeEach(() => {
  useAlphaMiningStore.setState({ sessions: [], error: false });
  vi.mocked(alphaApi.listMiningSessions).mockReset();
});

describe("useAlphaMiningStore(挖矿后台任务可见性)", () => {
  it("初始为空", () => {
    expect(useAlphaMiningStore.getState().sessions).toEqual([]);
  });

  it("refresh 拉取会话写入 store", async () => {
    vi.mocked(alphaApi.listMiningSessions).mockResolvedValue([running, stopped]);
    await useAlphaMiningStore.getState().refresh();
    expect(useAlphaMiningStore.getState().sessions).toHaveLength(2);
  });

  it("selectActiveSessions 只留 starting/running/stopping", () => {
    const active = selectActiveSessions({ sessions: [running, stopped, completed] });
    expect(active.map((s) => s.session_id)).toEqual(["alpha_run"]);
  });

  it("isActive 判定各状态", () => {
    expect(isActive(running)).toBe(true);
    expect(isActive({ ...running, status: "starting" })).toBe(true);
    expect(isActive({ ...running, status: "stopping" })).toBe(true);
    expect(isActive(stopped)).toBe(false);
    expect(isActive(completed)).toBe(false);
  });

  it("refresh 失败保留旧 sessions 并置 error", async () => {
    useAlphaMiningStore.setState({ sessions: [running], error: false });
    vi.mocked(alphaApi.listMiningSessions).mockRejectedValue(new Error("boom"));
    await useAlphaMiningStore.getState().refresh();
    expect(useAlphaMiningStore.getState().sessions).toEqual([running]);
    expect(useAlphaMiningStore.getState().error).toBe(true);
  });

  it("hasSession:活跃 id 命中,终态/未知 id 不命中", async () => {
    vi.mocked(alphaApi.listMiningSessions).mockResolvedValue([running, completed]);
    await useAlphaMiningStore.getState().refresh();
    const { hasSession } = useAlphaMiningStore.getState();
    expect(hasSession("alpha_run")).toBe(true);
    expect(hasSession("alpha_done")).toBe(false);
    expect(hasSession("nope")).toBe(false);
  });
});
