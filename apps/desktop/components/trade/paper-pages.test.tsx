import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { PaperAccountsPage } from "./paper-accounts";
import { PaperAccountDetailPage } from "./paper-accounts-account-id";
import { PaperEquityHistoryPage } from "./paper-accounts-account-id-equity-history";
import { PaperFillsPage } from "./paper-fills";
import { PaperOrdersPage } from "./paper-orders";
import { PaperPnlPage } from "./paper-pnl";
import { PaperPositionsPage } from "./paper-positions";
import { PaperSettingsPage } from "./paper-settings";
import * as authModule from "@/lib/auth";

// next/navigation useRouter — paper-page 用,子页不用但统一 mock 以防引用
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/trade/paper",
}));

const envelope = (data: unknown) => ({ data, meta: { offset: 0, limit: 50, total: 0 }, error: null });

const ACCOUNT_DEFAULT = {
  id: "paper-default",
  name: "Default Paper Account",
  account_type: "manual",
  initial_balance: 100000,
  balance: 99950,
  equity: 100250,
  unrealized_pnl: 300,
  realized_pnl: 150,
  total_fee: 2.5,
  maker_fee_rate: 0.0002,
  taker_fee_rate: 0.0005,
  status: "active",
  created_at: 1711000000000,
};

const FETCH_ROUTES: Record<string, unknown> = {
  "/api/v1/paper/accounts": envelope([
    ACCOUNT_DEFAULT,
    {
      id: "pa-abc123",
      name: "Test Account",
      account_type: "strategy",
      initial_balance: 50000,
      balance: 51000,
      equity: 51200,
      unrealized_pnl: 200,
      realized_pnl: 50,
      total_fee: 1.0,
      maker_fee_rate: 0.0002,
      taker_fee_rate: 0.0005,
      status: "active",
    },
  ]),
  "/api/v1/paper/accounts/paper-default": envelope(ACCOUNT_DEFAULT),
  "/api/v1/paper/accounts/paper-default/equity-history?limit=200": envelope([
    { timestamp: 1711000000000, equity: 100000 },
    { timestamp: 1711000300000, equity: 100150 },
    { timestamp: 1711000600000, equity: 100250 },
  ]),
  "/api/v1/paper/fills?account_id=paper-default": envelope([
    {
      id: "fill-001",
      order_id: "ord-001",
      price: 67000,
      quantity: 100,
      fee: 0.05,
      fee_currency: "USDT",
      fee_rate: 0.0005,
      realized_pnl: 0,
      exec_type: "taker",
      side: "buy",
      pos_side: "long",
      symbol: "BTC-USDT-SWAP",
      leverage: 1,
      reduce_only: false,
      timestamp: 1711000001000,
    },
  ]),
  "/api/v1/paper/orders?account_id=paper-default": envelope([
    {
      id: "ord-001",
      symbol: "BTC-USDT-SWAP",
      side: "buy",
      type: "market",
      status: "filled",
      quantity: 100,
      price: 67000,
      filled_quantity: 100,
      avg_fill_price: 67000,
      leverage: 1,
      margin_mode: "cross",
      pos_side: "long",
      reduce_only: false,
      created_at: 1711000000000,
    },
  ]),
  "/api/v1/paper/orders?account_id=paper-default&status=open": envelope([]),
  "/api/v1/paper/pnl?account_id=paper-default": envelope([
    {
      symbol: "BTC-USDT-SWAP",
      realized_pnl: 150,
      unrealized_pnl: 300,
      total_pnl: 450,
      fees: 2.5,
      timestamp: 1711000000000,
    },
  ]),
  "/api/v1/paper/positions?account_id=paper-default": envelope([
    {
      symbol: "BTC-USDT-SWAP",
      side: "buy",
      pos_side: "long",
      quantity: 100,
      quantity_base: 0.0015,
      avg_entry_price: 67000,
      leverage: 10,
      margin_mode: "cross",
      margin: 10,
      liquidation_price: 60300,
      unrealized_pnl: 300,
      unrealized_pnl_pct: 3,
      realized_pnl: 0,
      current_price: 69000,
      opened_at: 1711000000000,
    },
  ]),
  "/api/v1/paper/settings?account_id=paper-default": envelope({
    account_id: "paper-default",
    maker_fee_rate: 0.0002,
    taker_fee_rate: 0.0005,
  }),
};

function mockFetch() {
  // 最长路径优先,避免 /accounts/paper-default 被 /accounts 的 startsWith 吞掉
  const routes = Object.entries(FETCH_ROUTES).sort((a, b) => b[0].length - a[0].length);
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    for (const [path, body] of routes) {
      if (url === path || url.startsWith(path)) {
        return { ok: true, status: 200, statusText: "OK", json: async () => body } as Response;
      }
    }
    // 操作类 POST/DELETE 未匹配 → 成功空信封
    return { ok: true, status: 200, statusText: "OK", json: async () => envelope([]) } as Response;
  });
}

describe("paper 子页组件", () => {
  beforeEach(() => {
    vi.spyOn(authModule, "getAccessToken").mockReturnValue(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("PaperAccountsPage: 打开有数 — 渲染账户列表 + 净值统计", async () => {
    mockFetch();
    render(<PaperAccountsPage />);
    await waitFor(() => {
      expect(screen.getByText("Default Paper Account")).toBeTruthy();
    });
    expect(screen.getByText("Test Account")).toBeTruthy();
    // 总净值 = 100250 + 51200 = 151450.00
    expect(screen.getByText("151450.00 USDT")).toBeTruthy();
    expect(screen.getByLabelText("账户名")).toBeTruthy();
    // 创建按钮存在且有接线(空名 disabled,填名后可点)
    const createBtn = screen.getByRole("button", { name: /创建账户/ });
    expect((createBtn as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("账户名"), { target: { value: "新账户" } });
    expect((screen.getByRole("button", { name: /创建账户/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("PaperAccountDetailPage: 打开有数 — 渲染账户详情净值 + 重置按钮", async () => {
    mockFetch();
    render(<PaperAccountDetailPage />);
    await waitFor(() => {
      expect(screen.getByText("100250.00 USDT")).toBeTruthy(); // 账户净值 StatBlock
    });
    // 账户选择下拉包含默认账户
    expect(screen.getAllByText("Default Paper Account").length).toBeGreaterThan(0);
    const resetBtn = screen.getByRole("button", { name: /重置账户/ });
    expect((resetBtn as HTMLButtonElement).disabled).toBe(false);
    // 重置按钮点击 → 发 POST /reset(不抛错)
    await act(async () => { fireEvent.click(resetBtn); });
  });

  it("PaperEquityHistoryPage: 打开有数 — 渲染 SVG 折线图 + 权益明细", async () => {
    mockFetch();
    render(<PaperEquityHistoryPage />);
    await waitFor(() => {
      expect(screen.getAllByText(/100250\.00/).length).toBeGreaterThan(0); // max/latest/末行
    });
    const svg = document.querySelector("svg");
    expect(svg).toBeTruthy();
    // 点数 3(3 个数据点)
    expect(screen.getByText("点数:")).toBeTruthy();
    // 刷新按钮有接线
    const refreshBtn = screen.getByRole("button", { name: /刷新/ });
    fireEvent.click(refreshBtn);
  });

  it("PaperFillsPage: 打开有数 — 渲染成交记录表", async () => {
    mockFetch();
    render(<PaperFillsPage />);
    await waitFor(() => {
      expect(screen.getByText("BTC-USDT-SWAP")).toBeTruthy();
    });
    expect(screen.getByText("fill-001")).toBeTruthy();
    expect(screen.getByRole("button", { name: /刷新/ })).toBeTruthy();
  });

  it("PaperOrdersPage: 打开有数 — 渲染订单表 + 下单表单接线", async () => {
    mockFetch();
    render(<PaperOrdersPage />);
    await waitFor(() => {
      expect(screen.getByText("ord-001")).toBeTruthy();
    });
    // 下单按钮存在且真接线(默认品种/数量 → 可点)
    const placeBtn = screen.getByRole("button", { name: /下单/ });
    expect((placeBtn as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByLabelText("品种")).toBeTruthy();
    // 状态筛选切换 → 触发带 status 请求(不抛错)
    await act(async () => {
      fireEvent.change(screen.getByLabelText("状态筛选"), { target: { value: "open" } });
    });
  });

  it("PaperPnlPage: 打开有数 — 渲染盈亏汇总", async () => {
    mockFetch();
    render(<PaperPnlPage />);
    await waitFor(() => {
      expect(screen.getByText("BTC-USDT-SWAP")).toBeTruthy();
    });
    // 总 PnL = 150 + 300 = 450 → "+450.00 USDT"
    expect(screen.getByText("+450.00 USDT")).toBeTruthy();
    expect(screen.getByRole("button", { name: /刷新/ })).toBeTruthy();
  });

  it("PaperPositionsPage: 打开有数 — 渲染持仓 + 平仓按钮", async () => {
    mockFetch();
    render(<PaperPositionsPage />);
    await waitFor(() => {
      expect(screen.getByText("BTC-USDT-SWAP")).toBeTruthy();
    });
    const closeBtn = screen.getByRole("button", { name: /平仓 BTC-USDT-SWAP/ });
    expect((closeBtn as HTMLButtonElement).disabled).toBe(false);
    // 平仓点击 → POST /close-position(不抛错)
    await act(async () => { fireEvent.click(closeBtn); });
  });

  it("PaperSettingsPage: 打开有数 — 渲染费率 + 保存按钮", async () => {
    mockFetch();
    render(<PaperSettingsPage />);
    await waitFor(() => {
      expect(screen.getByLabelText("Maker 费率")).toBeTruthy();
    });
    const saveBtn = screen.getByRole("button", { name: /保存费率/ });
    expect(saveBtn).toBeTruthy();
    // 初始不脏 → 保存 disabled(真接线,非死按钮)
    expect((saveBtn as HTMLButtonElement).disabled).toBe(true);
    // 改值 → 变可点
    fireEvent.change(screen.getByLabelText("Maker 费率"), { target: { value: "0.0003" } });
    expect((screen.getByRole("button", { name: /保存费率/ }) as HTMLButtonElement).disabled).toBe(false);
  });
});
