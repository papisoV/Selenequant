import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// --- mocks:底部抽屉依赖一堆 store / ws / 拖拽 hook,本测试只关心 tab chrome 与文案 ---
// zustand selector 在测试里就是普通函数,直接用 useShallow=identity 让 store mock 简单
vi.mock("zustand/react/shallow", () => ({ useShallow: (fn: unknown) => fn }));

vi.mock("@/lib/store/context", () => ({
  useContextStore: () => ({ setSymBase: vi.fn(), pageFilter: {} }),
}));
vi.mock("@/lib/store/paper", () => ({
  usePaperStore: (sel?: (s: unknown) => unknown) =>
    sel ? sel({ positions: [], orders: [], fills: [] }) : {},
}));
vi.mock("@/lib/store/live-trading", () => ({
  useLiveTradingStore: (sel?: (s: unknown) => unknown) =>
    sel ? sel({ positions: [], orders: [], fills: [] }) : {},
}));
vi.mock("@/lib/store/trading-mode", () => ({
  useTradingModeStore: (sel?: (s: unknown) => unknown) => (sel ? sel({ mode: "paper" }) : { mode: "paper" }),
}));
vi.mock("@/lib/hooks/use-account-id", () => ({ useAccountId: () => "acc-1" }));
vi.mock("@/lib/ws/use-intelligence-ws", () => ({
  useIntelligenceWs: () => ({ state: { streamState: "connecting" } }),
}));
vi.mock("@/lib/hooks/use-resize", () => ({
  useResize: () => ({ onMouseDown: vi.fn(), onDoubleClick: vi.fn() }),
}));
vi.mock("@/lib/toast", () => ({
  toastSuccess: vi.fn(), toastError: vi.fn(), toastInfo: vi.fn(),
  useToastContainer: () => ({ toasts: [] }),
}));
vi.mock("@/lib/api-client", () => ({
  api: vi.fn(async () => []),
  apiPost: vi.fn(async () => null),
  apiDelete: vi.fn(async () => null),
}));
vi.mock("./cmdk-panel", () => ({ CmdkPanel: () => null }));
vi.mock("./agent-chat/agent-chat-panel", () => ({
  AgentChatPanel: () => <div data-testid="agent-chat-panel">panel</div>,
}));

import { BottomDrawer } from "./bottom-drawer";
import { AgentFab } from "./agent-fab";

// tab 文字含图标字形(如 "≡日志"),按 textContent 取,别用 exact text 匹配
const tabTexts = () => Array.from(document.querySelectorAll(".dtab")).map((e) => e.textContent ?? "");
const clickTab = (label: string) => {
  const el = Array.from(document.querySelectorAll(".dtab")).find((e) => (e.textContent ?? "").includes(label));
  if (!el) throw new Error(`未找到 tab: ${label}`);
  fireEvent.click(el);
};

describe("B1 杀假 UI — drawer 去掉假「Agent 推理」面", () => {
  it("TABS 里不再有「Agent 推理」tab", () => {
    render(<BottomDrawer />);
    expect(tabTexts().some((t) => t.includes("Agent 推理"))).toBe(false);
  });

  it("假输入框整块消失(.ac-input / .ac-send 无 onClick 的死控件)", () => {
    render(<BottomDrawer />);
    // 动态枚举全部 tab 逐个点开扫——写死列表会让"复活一个假 tab"蒙混过关
    const count = document.querySelectorAll(".dtab").length;
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i++) {
      const el = document.querySelectorAll(".dtab")[i];
      const label = el.textContent ?? `#${i}`;
      fireEvent.click(el);
      expect(document.querySelector(".ac-input"), `tab=${label}`).toBeNull();
      expect(document.querySelector(".ac-send"), `tab=${label}`).toBeNull();
    }
  });

  it("点「日志」tab → 明说「日志功能未规划」(不是「端点暂未实现」)", () => {
    render(<BottomDrawer />);
    clickTab("日志");
    expect(screen.getByText("日志功能未规划")).toBeInTheDocument();
    expect(screen.queryByText(/系统日志端点暂未实现/)).toBeNull();
  });

  it("幸存的 tab 仍可达(持仓 / 事件流 / 日志 / 终端)", () => {
    render(<BottomDrawer />);
    const t = tabTexts().join("|");
    for (const label of ["持仓", "事件流", "日志", "终端"]) {
      expect(t).toContain(label);
    }
  });
});

describe("B1 杀假 UI — FAB 去掉无来源的未读徽章", () => {
  it("AgentFab 不渲染 .fab-badge(旧代码硬编码 '2',无任何数据源)", () => {
    render(<AgentFab />);
    expect(document.querySelector(".fab-badge")).toBeNull();
  });
});
