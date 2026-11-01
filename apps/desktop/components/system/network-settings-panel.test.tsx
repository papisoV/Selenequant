import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NetworkSettingsPanel } from "./network-settings-panel";

/**
 * 这组断言针对的是用户提的三个具体问题，不是实现细节：
 *  (a) 代理设置没有指引 → 四层优先级可展开、每层能看出是否命中；
 *  (b) 保存后「重启才生效」过去只闪 3 秒 → 现在是持久横幅；
 *  (c) 找不到东西 → 面板自带探测/测试按钮，不需要用户自己去翻日志。
 */

const mockPutNetworkSettings = vi.fn();
const mockFetchProxyStatus = vi.fn();
const mockDetectLocalProxies = vi.fn();
const mockTestProxyConnectivity = vi.fn();

let apiData: Record<string, unknown> = {};

vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({
    useApi: (url: string) => ({
      data: apiData[url] ?? null,
      error: null,
      isLoading: false,
      mutate: vi.fn(),
    }),
  }),
}));

vi.mock("@/lib/api/settings", () => ({
  putNetworkSettings: (cfg: unknown) => mockPutNetworkSettings(cfg),
  fetchProxyStatus: () => mockFetchProxyStatus(),
  detectLocalProxies: () => mockDetectLocalProxies(),
  testProxyConnectivity: (url: string) => mockTestProxyConnectivity(url),
}));

const SETTINGS_URL = "/api/v1/settings";
const STATUS_URL = "/api/v1/settings/network/status";

function status(overrides: Record<string, unknown> = {}) {
  return {
    env: { selene_proxy_url: false, https_proxy: false },
    saved: "http://127.0.0.1:7899",
    effective: "http://127.0.0.1:7899",
    saved_masked: "http://127.0.0.1:7899",
    effective_masked: "http://127.0.0.1:7899",
    source: "saved",
    pending_restart: false,
    effective_known: true,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiData = {
    [SETTINGS_URL]: { network: { proxy_url: "http://127.0.0.1:7899" } },
    [STATUS_URL]: status(),
  };
  mockPutNetworkSettings.mockResolvedValue(undefined);
});

describe("NetworkSettingsPanel", () => {
  it("shows the live saved value in the input, not a hard-coded example", () => {
    render(<NetworkSettingsPanel />);
    const input = screen.getByLabelText("代理地址") as HTMLInputElement;
    expect(input.value).toBe("http://127.0.0.1:7899");
    // The old placeholder advertised a dead 7890.
    expect(input.getAttribute("placeholder")).not.toContain("7890");
  });

  it("states which layer is in effect", () => {
    render(<NetworkSettingsPanel />);
    expect(screen.getByText("当前生效：")).toBeTruthy();
    expect(screen.getByText("http://127.0.0.1:7899")).toBeTruthy();
  });

  it("shows a persistent restart banner when the saved value is not running yet", () => {
    apiData[STATUS_URL] = status({
      saved: "http://127.0.0.1:7900",
      saved_masked: "http://127.0.0.1:7900",
      source: "saved-stale",
      pending_restart: true,
    });
    render(<NetworkSettingsPanel />);

    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText("已保存待重启")).toBeTruthy();
  });

  it("hides the restart banner when saved and running agree", () => {
    render(<NetworkSettingsPanel />);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("refuses to claim a layer when the backend never reported one", () => {
    apiData[STATUS_URL] = status({
      source: "unknown",
      effective: "",
      effective_masked: "",
      pending_restart: null,
      effective_known: false,
    });
    render(<NetworkSettingsPanel />);

    expect(screen.getByText("未知（后端未报告运行中的设置）")).toBeTruthy();
    expect(screen.queryByText(/直连（无代理）/)).toBeNull();
  });

  it("keeps the restart message after saving instead of flashing it", () => {
    render(<NetworkSettingsPanel />);
    fireEvent.click(screen.getByText("保存"));

    return waitFor(() => {
      expect(mockPutNetworkSettings).toHaveBeenCalledWith({
        proxy_url: "http://127.0.0.1:7899",
      });
      // Still on screen; the old panel cleared this after 3s via setTimeout.
      expect(screen.getByText("已保存，重启后生效")).toBeTruthy();
    });
  });

  it("fills the input with a detected local proxy", async () => {
    mockDetectLocalProxies.mockResolvedValue({
      candidates: [
        { url: "http://127.0.0.1:7899", port: 7899, listening: true, reachable: true },
      ],
      recommended: "http://127.0.0.1:7899",
    });
    apiData[SETTINGS_URL] = { network: { proxy_url: "" } };
    apiData[STATUS_URL] = status({ saved: "", saved_masked: "", pending_restart: false });

    render(<NetworkSettingsPanel />);
    fireEvent.click(screen.getByText("探测本机"));

    await waitFor(() => {
      expect((screen.getByLabelText("代理地址") as HTMLInputElement).value).toBe(
        "http://127.0.0.1:7899",
      );
    });
    expect(screen.getByText(/本机检测到/)).toBeTruthy();
  });

  it("says so when nothing is listening rather than showing an empty result", async () => {
    mockDetectLocalProxies.mockResolvedValue({ candidates: [], recommended: "" });
    render(<NetworkSettingsPanel />);
    fireEvent.click(screen.getByText("探测本机"));

    await waitFor(() => {
      expect(screen.getByText(/未发现本机代理/)).toBeTruthy();
    });
  });

  it("reports a port that listens but is not a proxy", async () => {
    mockDetectLocalProxies.mockResolvedValue({
      candidates: [
        { url: "http://127.0.0.1:8080", port: 8080, listening: true, reachable: false },
      ],
      recommended: "",
    });
    render(<NetworkSettingsPanel />);
    fireEvent.click(screen.getByText("探测本机"));

    await waitFor(() => {
      expect(screen.getByText(/个端口在监听，但都无法通过代理访问外网/)).toBeTruthy();
    });
  });

  it("shows per-target test results", async () => {
    mockTestProxyConnectivity.mockResolvedValue({
      proxy: "http://127.0.0.1:7899",
      direct: false,
      results: [
        { name: "binance", ok: true, status: 200, ms: 120, error: null },
        { name: "github", ok: false, status: null, ms: 3000, error: "ConnectTimeout" },
      ],
      reachable: 1,
      total: 2,
    });
    render(<NetworkSettingsPanel />);
    fireEvent.click(screen.getByText("测试连接"));

    await waitFor(() => {
      expect(screen.getByText(/1\/2 个目标可达/)).toBeTruthy();
    });
    expect(screen.getByText("binance")).toBeTruthy();
    expect(screen.getByText(/ConnectTimeout/)).toBeTruthy();
    expect(mockTestProxyConnectivity).toHaveBeenCalledWith("http://127.0.0.1:7899");
  });

  it("explains the four precedence layers on demand", () => {
    render(<NetworkSettingsPanel />);
    expect(screen.queryByText(/1\. 环境变量/)).toBeNull();

    fireEvent.click(screen.getByText(/优先级（从高到低）/));

    // 每层 li 里还嵌了 <code>,所以按包含匹配而不是整串相等。
    expect(screen.getByText(/1\. 环境变量/)).toBeTruthy();
    expect(screen.getByText(/2\. 保存的代理/)).toBeTruthy();
    expect(screen.getByText(/3\. 系统代理/)).toBeTruthy();
    expect(screen.getByText(/4\. 直连/)).toBeTruthy();
  });

  it("says which save failed instead of failing silently", async () => {
    mockPutNetworkSettings.mockRejectedValue(new Error("boom"));
    render(<NetworkSettingsPanel />);
    fireEvent.click(screen.getByText("保存"));

    await waitFor(() => {
      expect(screen.getByText("保存失败")).toBeTruthy();
    });
  });
});
