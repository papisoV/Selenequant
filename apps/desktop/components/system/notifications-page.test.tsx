import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { NotificationsPage } from "@/components/system/notifications-page";

// === mocks ============================================================
const mockApiData: Record<string, unknown> = {};
vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string) => ({
    data: mockApiData[url] ?? null,
    error: null,
    isLoading: false,
    mutate: vi.fn(),
  }),
}));

const mockCreateChannel = vi.fn(async (_body: unknown) => ({}));
vi.mock("@/lib/api/notifications", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/notifications")>(
    "@/lib/api/notifications",
  );
  return {
    ...actual,
    createChannel: (body: unknown) => mockCreateChannel(body),
    deleteChannel: vi.fn(async () => ({ deleted: true, mode: "hard" })),
    updateChannel: vi.fn(async () => ({})),
    testChannel: vi.fn(async () => ({
      tested: true,
      channel_id: "x",
      status: "ok",
      message: "ok",
    })),
  };
});

const mockToastError = vi.fn();
vi.mock("@/lib/toast", () => ({
  toastError: (m: string) => mockToastError(m),
  toastSuccess: vi.fn(),
}));

function setChannels(channels: unknown[], prefs: Record<string, string[]> = {}) {
  mockApiData["/api/v1/notifications/channels"] = { channels };
  mockApiData["/api/v1/notifications/preferences"] = { preferences: prefs };
}

describe("NotificationsPage", () => {
  beforeEach(() => {
    mockCreateChannel.mockClear();
    mockToastError.mockClear();
    setChannels([]);
  });

  it("offers all absorbed channel types in the type selector", () => {
    render(<NotificationsPage />);
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    const options = Array.from(select.querySelectorAll("option")).map(
      (o) => (o as HTMLOptionElement).value,
    );
    for (const t of [
      "webhook",
      "ntfy",
      "serverchan3",
      "pushplus",
      "gotify",
      "pushover",
      "telegram",
    ]) {
      expect(options).toContain(t);
    }
  });

  it("swaps the field group when the channel type changes", () => {
    render(<NotificationsPage />);
    const select = screen.getByRole("combobox");

    // webhook (default) shows a URL field
    expect(screen.getByText("Webhook URL")).toBeTruthy();

    fireEvent.change(select, { target: { value: "pushover" } });

    // pushover shows its own two token fields, and no webhook URL
    expect(screen.queryByText("Webhook URL")).toBeNull();
    expect(screen.getByText("User Key")).toBeTruthy();
    expect(screen.getByText("应用 Token")).toBeTruthy();
  });

  it("blocks submit and reports which required field is missing", () => {
    render(<NotificationsPage />);
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "pushover" },
    });
    fireEvent.change(screen.getByLabelText("名称"), {
      target: { value: "手机告警" },
    });
    // only User Key filled — Token still missing
    fireEvent.change(screen.getByLabelText("User Key"), {
      target: { value: "uk-1" },
    });

    fireEvent.click(screen.getByText("添加渠道"));

    expect(mockCreateChannel).not.toHaveBeenCalled();
    expect(mockToastError).toHaveBeenCalledWith("请填写 应用 Token");
  });

  it("creates a channel with spec-driven config keys", async () => {
    render(<NotificationsPage />);
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "pushover" },
    });
    fireEvent.change(screen.getByLabelText("名称"), {
      target: { value: "手机告警" },
    });
    fireEvent.change(screen.getByLabelText("User Key"), {
      target: { value: "uk-1" },
    });
    fireEvent.change(screen.getByLabelText("应用 Token"), {
      target: { value: "at-1" },
    });

    fireEvent.click(screen.getByText("添加渠道"));

    await waitFor(() => expect(mockCreateChannel).toHaveBeenCalledTimes(1));
    expect(mockCreateChannel).toHaveBeenCalledWith({
      type: "pushover",
      name: "手机告警",
      config: { user_key: "uk-1", token: "at-1" },
    });
  });

  it("shows real counts, not the removed fake placeholders", () => {
    setChannels(
      [
        { id: "c1", type: "ntfy", name: "p", config: {}, enabled: true },
        { id: "c2", type: "pushover", name: "q", config: {}, enabled: false },
      ],
      { risk_alert: ["c1"], kill_switch_activated: ["c1", "c2"] },
    );
    render(<NotificationsPage />);

    expect(screen.getByText("渠道数")).toBeTruthy();
    expect(screen.getByText("已启用")).toBeTruthy();
    expect(screen.getByText("订阅绑定")).toBeTruthy();
    // 3 total bindings across the two event types
    expect(screen.getByText("3")).toBeTruthy();
    // the removed fake stat must not be rendered
    expect(screen.queryByText("待处理")).toBeNull();
  });

  it("does not render the dead '最近通知' panel", () => {
    render(<NotificationsPage />);
    expect(screen.queryByText("最近通知")).toBeNull();
  });
});
