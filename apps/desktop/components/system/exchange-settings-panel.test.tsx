import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ExchangeSettingsPanel } from "@/components/system/exchange-settings-panel";

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

const mockPutExchangeSettings = vi.fn(
  async (_cfg: Record<string, unknown>) => undefined,
);
const mockTestExchangeCredentials = vi.fn(async () => ({
  tested: true,
  provider: "binance",
  market_type: "spot",
  testnet: true,
  message: "Credentials valid",
  account_summary: { can_trade: true, balances: 3 },
}));
vi.mock("@/lib/api/settings", () => ({
  putExchangeSettings: (cfg: Record<string, unknown>) =>
    mockPutExchangeSettings(cfg),
  testExchangeCredentials: () => mockTestExchangeCredentials(),
}));

function setSettings(exchange: unknown) {
  mockApiData["/api/v1/settings"] = { exchange };
}

describe("ExchangeSettingsPanel", () => {
  beforeEach(() => {
    mockApiData["/api/v1/settings"] = null;
    mockPutExchangeSettings.mockClear();
    mockTestExchangeCredentials.mockClear();
  });

  it("renders the api_key_hint so the user can tell which key is in effect", async () => {
    // 审计点:api_key_hint 此前零消费 —— 用户无法确认系统里存的是哪个 key
    setSettings({
      provider: "binance",
      market_type: "spot",
      testnet: true,
      api_key_configured: true,
      api_secret_configured: true,
      api_key_masked: "••••••••",
      api_secret_masked: "••••••••",
      api_key_hint: "ABCD...9012",
    });

    render(<ExchangeSettingsPanel />);
    expect(await screen.findByText("ABCD...9012")).toBeTruthy();
    // 有 secret 配对,不该报警
    expect(screen.getByText("(密钥已配对)")).toBeTruthy();
  });

  it("flags unconfigured credentials and disables the probe", async () => {
    setSettings({
      provider: "binance",
      market_type: "spot",
      testnet: true,
      api_key_configured: false,
      api_secret_configured: false,
      api_key_hint: "",
    });

    render(<ExchangeSettingsPanel />);
    expect(await screen.findByText("未配置")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "测试连接" }).hasAttribute("disabled"),
    ).toBe(true);
  });

  it("probes stored credentials and surfaces the account summary", async () => {
    setSettings({
      provider: "binance",
      market_type: "spot",
      testnet: true,
      api_key_configured: true,
      api_secret_configured: true,
      api_key_hint: "ABCD...9012",
    });

    render(<ExchangeSettingsPanel />);
    const btn = await screen.findByRole("button", { name: "测试连接" });
    expect(btn.hasAttribute("disabled")).toBe(false);
    btn.click();

    await waitFor(() => expect(mockTestExchangeCredentials).toHaveBeenCalled());
    expect(await screen.findByText(/凭证可用/)).toBeTruthy();
    // account_summary 必须落到 UI,否则用户只看到"成功"不知道验证到什么
    expect(await screen.findByText(/can_trade=true/)).toBeTruthy();
  });

  it("saves without overwriting the stored key when the field is left blank", async () => {
    setSettings({
      provider: "binance",
      market_type: "spot",
      testnet: false,
      api_key_configured: true,
      api_secret_configured: true,
      api_key_hint: "ABCD...9012",
    });

    render(<ExchangeSettingsPanel />);
    (await screen.findByRole("button", { name: "保存" })).click();

    await waitFor(() => expect(mockPutExchangeSettings).toHaveBeenCalled());
    const sent = mockPutExchangeSettings.mock.calls[0][0];
    // 留空 = 不传 api_key/api_secret,绝不覆盖 keyring 里的存量凭证
    expect(sent.api_key).toBeUndefined();
    expect(sent.api_secret).toBeUndefined();
    expect(sent.clear_api_key).toBeUndefined();
  });
});
