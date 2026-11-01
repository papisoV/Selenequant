// Absorbed from tick-stock-panel (MIT License)
// Copyright (c) 2026 tick-stock-panel contributors
// Source: https://github.com/shy3130/tick-stock-panel
import { describe, expect, it, vi, beforeEach, beforeAll, afterAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

/**
 * jsdom 没有布局引擎:元素的 clientWidth/clientHeight 恒为 0,而虚拟列表正是
 * 用这两个值量滚动视口(@tanstack/virtual-core 的 getRect)。量到 0 就判定
 * 「可见区间为空」,一行都不会渲染 —— 生产浏览器里量得到真实尺寸,这里只是给
 * 测试补一个确定视口。只在本文件生效,不改全局 setup:其他测试不该被假装有
 * 布局,否则「元素不可见」类断言会静默失效。
 */
const VIEWPORT = { width: 640, height: 480 };
let restoreSize: (() => void) | undefined;
beforeAll(() => {
  const w = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
  const h = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight");
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => VIEWPORT.width,
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get: () => VIEWPORT.height,
  });
  restoreSize = () => {
    if (w) Object.defineProperty(HTMLElement.prototype, "clientWidth", w);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientWidth;
    if (h) Object.defineProperty(HTMLElement.prototype, "clientHeight", h);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientHeight;
  };
});
afterAll(() => restoreSize?.());

/**
 * 概念成分下钻面板的渲染契约。
 *
 * 这个面板最容易骗人的四处,每条都有测试钉住:
 *   1. 截断说成「共 N 只」而实际只给了前 1000 —— 切片被读成全体。
 *   2. 收盘后仍把日线回退的数字说成「实时」。
 *   3. 没取到价的成员被静默丢掉,概念看起来比实际小。
 *   4. 后端降级时把缺失字段当零值,印出一个从未算过的「涨 0 跌 0 平 0」。
 */

const mockApiData: Record<string, unknown> = {};
const mockApiErrors: Record<string, { message: string; status: number } | undefined> = {};
const requestedPaths: (string | null)[] = [];
/** 让个别用例拉出"加载中"这一帧(默认数据已就绪,便于其余用例断言终态)。 */
const mockState = { isLoading: false };

vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({
    useApi: (url: string | null) => {
      requestedPaths.push(url);
      return {
        data: url ? (mockApiData[url] ?? null) : null,
        error: url ? mockApiErrors[url] : undefined,
        isLoading: mockState.isLoading,
        mutate: async () => {},
      };
    },
  }),
}));

import {
  ConceptMembersDialog,
  ConceptMembersTrigger,
} from "./concept-members-dialog";

function member(over: Record<string, unknown> = {}) {
  return {
    symbol: "600000.SH",
    name: "浦发银行",
    last: 10.0,
    change_pct: 1.01,
    amount: 1.2e8,
    priced: true,
    ...over,
  };
}

function payload(over: Record<string, unknown> = {}) {
  const rows = (over.rows as unknown[]) ?? [
    member({ symbol: "600000.SH", name: "浦发银行", change_pct: 5.0, amount: 1.2e8 }),
    member({ symbol: "600036.SH", name: "招商银行", change_pct: -2.0, amount: 3.4e7 }),
    member({
      symbol: "000001.SZ",
      name: "平安银行",
      change_pct: null,
      last: null,
      amount: null,
      priced: false,
    }),
  ];
  return {
    status: "ok",
    reason: null,
    concept: "银行",
    limit: 1000,
    total: 3,
    returned: 3,
    truncated: false,
    quote_source: "snapshot",
    quote_reason: null,
    as_of: "2026-10-25",
    priced_count: 2,
    membership_note: "",
    ...over,
    rows,
  };
}

/**
 * 降级响应的**真实**形状:`services/local-api/app/api/v1/ashare_concept.py:279-298`
 * 的降级分支只回 `base`(concept/limit/membership_note)+ status + reason,
 * 既没有 `total` 也没有 `rows`/`returned`/`truncated`。
 *
 * 夹具必须照抄这个形状 —— 早先这里注入了 `total: 0, rows: []`,那是后端从不返回的
 * 组合,于是「副标题渲染字面 `共 {total} 只`」和「对空数组算出涨 0 跌 0 平 0」
 * 两个缺陷在绿的测试里结构性不可见。
 */
function degradedPayload(reason: string) {
  return {
    status: "degraded",
    reason,
    concept: "银行",
    limit: 1000,
    membership_note: "概念成分为当前快照",
  };
}

const url = (concept = "银行") =>
  `/api/v1/ashare/concept-members?concept=${encodeURIComponent(concept)}&limit=1000`;

function renderOpen(
  concept = "银行",
  props: { sessionToday?: string | null; expectedDailySession?: string | null } = {},
) {
  return render(
    <ConceptMembersDialog
      concept={concept}
      open
      onOpenChange={() => {}}
      {...props}
    />,
  );
}

const rowsOf = () =>
  screen.queryAllByTestId("member-row").map((el) => el.textContent ?? "");

describe("ConceptMembersDialog", () => {
  beforeEach(() => {
    for (const k of Object.keys(mockApiData)) delete mockApiData[k];
    for (const k of Object.keys(mockApiErrors)) delete mockApiErrors[k];
    requestedPaths.length = 0;
    mockState.isLoading = false;
  });

  it("does not hit the endpoint while the dialog is closed", () => {
    render(
      <ConceptMembersDialog concept="银行" open={false} onOpenChange={() => {}} />,
    );
    expect(requestedPaths.every((p) => p === null)).toBe(true);
  });

  it("requests the concept with the default 1000-row cap", () => {
    renderOpen();
    expect(requestedPaths).toContain(url());
  });

  it("states truncation instead of presenting the slice as the whole concept", () => {
    mockApiData[url()] = payload({ total: 3888, returned: 1000, truncated: true });
    renderOpen();
    const header = screen.getByText(/共 3888 只/);
    expect(header.textContent).toBe("共 3888 只,显示前 1000 只");
  });

  it("says a plain total when nothing was cut", () => {
    mockApiData[url()] = payload();
    renderOpen();
    expect(screen.getByText("共 3 只").textContent).toBe("共 3 只");
  });

  it("labels the price source so yesterday's close is never read as live", () => {
    mockApiData[url()] = payload({ quote_source: "daily", as_of: "2026-10-24" });
    renderOpen();
    const badge = screen.getByTestId("quote-source");
    expect(badge.textContent).toContain("日线回退");
    expect(badge.textContent).toContain("2026-10-24");
  });

  it("shows the live snapshot label when the tape is live", () => {
    mockApiData[url()] = payload();
    renderOpen();
    expect(screen.getByTestId("quote-source").textContent).toContain("实时快照");
  });

  it("keeps unpriced members visible and last", () => {
    mockApiData[url()] = payload();
    renderOpen();
    const text = rowsOf();
    // 无价的平安银行必须在最末 —— 丢掉它会让概念看起来只有 2 只。
    expect(text.at(-1)).toContain("000001.SZ");
    expect(text.some((t) => t.includes("000001.SZ"))).toBe(true);
  });

  it("counts up / down / flat / unpriced in the stats bar", () => {
    mockApiData[url()] = payload();
    renderOpen();
    const stats = screen.getByTestId("member-stats");
    expect(stats.textContent).toContain("涨 1");
    expect(stats.textContent).toContain("跌 1");
    expect(stats.textContent).toContain("无价 1");
  });

  it("formats change as a percentage and colors up red / down green", () => {
    mockApiData[url()] = payload();
    renderOpen();
    const up = screen.getByText("5.00");
    const down = screen.getByText("-2.00");
    // A 股惯例:涨=红、跌=绿(与加密的 pos/neg 相反)。
    expect(up.className).toContain("text-red-400");
    expect(down.className).toContain("text-emerald-400");
  });

  it("formats turnover in 亿 / 万 units", () => {
    mockApiData[url()] = payload();
    renderOpen();
    expect(screen.getByText("1.20亿")).toBeTruthy();
    expect(screen.getByText("3400万")).toBeTruthy();
  });

  it("filters case-insensitively on code and on name", () => {
    mockApiData[url()] = payload({
      rows: [
        member({ symbol: "600000.SH", name: "浦发银行" }),
        member({ symbol: "000001.SZ", name: "PingAn" }),
      ],
    });
    renderOpen();
    const box = screen.getByRole("searchbox");

    fireEvent.change(box, { target: { value: "pingan" } });
    expect(rowsOf()).toHaveLength(1);
    expect(rowsOf()[0]).toContain("000001.SZ");

    fireEvent.change(box, { target: { value: "600000" } });
    expect(rowsOf()).toHaveLength(1);
    expect(rowsOf()[0]).toContain("600000.SH");

    fireEvent.change(box, { target: { value: "没有这只" } });
    expect(screen.getByText("没有匹配的成分股")).toBeTruthy();
  });

  it("re-sorts by change ascending when asked", () => {
    mockApiData[url()] = payload();
    renderOpen();
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "change_asc" },
    });
    const text = rowsOf();
    // 无价行在**两个方向**都排最后 —— 否则「没取到价」会被读成跌幅最深。
    expect(text.at(-1)).toContain("000001.SZ");
    expect(text[0]).toContain("600036.SH");
    expect(text[1]).toContain("600000.SH");
  });

  it("sorts by turnover when asked, unpriced last", () => {
    mockApiData[url()] = payload();
    renderOpen();
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "amount_desc" },
    });
    const text = rowsOf();
    expect(text[0]).toContain("600000.SH");
    expect(text[1]).toContain("600036.SH");
    expect(text.at(-1)).toContain("000001.SZ");
  });

  it("distinguishes a degraded membership table from an empty concept", () => {
    mockApiData[url()] = degradedPayload("concept_membership_unavailable");
    renderOpen();
    expect(screen.getByText(/成分表暂不可用/)).toBeTruthy();
  });

  it("never renders a template placeholder when the backend omits `total`", () => {
    // 降级响应没有 total。formatMessage 对缺失参数回显 `{key}`,于是标题栏
    // 会印出字面 `共 {total} 只` —— 把内部模板泄漏给用户读。
    mockApiData[url()] = degradedPayload("concept_membership_unavailable");
    renderOpen();
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent ?? "").not.toMatch(/\{[a-z_]+\}/);
  });

  it("does not fabricate 涨 0 · 跌 0 · 平 0 when there are no rows to count", () => {
    // 降级响应没有 rows。把缺失当成空数组再统计,会得出一个从未被计算过的
    // 「0」—— 而 0/0/0 在看盘台上是一个合法的市场状态读数(全线停牌)。
    mockApiData[url()] = degradedPayload("concept_membership_unavailable");
    renderOpen();
    expect(screen.queryByTestId("member-stats")).toBeNull();
  });

  it("offers a retry on the degraded path, not only on the HTTP error path", () => {
    // 成分缓存故障是瞬时的(fail-open by contract),没有重试出口就只能刷整页。
    mockApiData[url()] = degradedPayload("concept_membership_unavailable");
    renderOpen();
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });

  it("shows a human sentence instead of the raw reason code", () => {
    mockApiData[url()] = degradedPayload("concept_membership_unavailable");
    renderOpen();
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent ?? "").not.toContain("concept_membership_");
  });

  it("marks a stale snapshot in gold instead of leaving it neutral", () => {
    // as_of 是数字所属会话,不是"现在"。快照停在两天前、今天已知 ⇒ 必须金色告警;
    // 否则「实时快照 · 2026-10-23」在作战面板里和今天的读数长得一样。
    mockApiData[url()] = payload({ quote_source: "snapshot", as_of: "2026-10-23" });
    renderOpen("银行", { sessionToday: "20261025" });
    expect(
      screen.getByTestId("quote-source").querySelector(".text-gold")?.textContent,
    ).toContain("20261023");
  });

  it("stays neutral when the snapshot is today's session", () => {
    mockApiData[url()] = payload({ quote_source: "snapshot", as_of: "2026-10-25" });
    renderOpen("银行", { sessionToday: "20261025" });
    expect(
      screen.getByTestId("quote-source").querySelector(".text-gold"),
    ).toBeNull();
  });

  it("judges a daily fallback against the expected daily session, not today", () => {
    // 日线盘中本来就停在上一交易日。用「今天」当基准会让这块每交易日
    // 9:30–15:00 常亮金色(假故障) —— 基准必须是 expectedDailySession。
    mockApiData[url()] = payload({ quote_source: "daily", as_of: "2026-10-23" });
    renderOpen("银行", {
      sessionToday: "20261025",
      expectedDailySession: "20261023",
    });
    expect(
      screen.getByTestId("quote-source").querySelector(".text-gold"),
    ).toBeNull();
  });

  it("flags a daily fallback older than the expected daily session", () => {
    mockApiData[url()] = payload({ quote_source: "daily", as_of: "2026-10-22" });
    renderOpen("银行", {
      sessionToday: "20261025",
      expectedDailySession: "20261023",
    });
    expect(
      screen.getByTestId("quote-source").querySelector(".text-gold")?.textContent,
    ).toContain("20261022");
  });

  it("announces a failure to assistive tech", () => {
    mockApiErrors[url()] = { message: "成分表数据库不可达", status: 500 };
    renderOpen();
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("announces the loading state to assistive tech", () => {
    // 打开弹窗到数据回来之间是一个异步状态跳变。没有 live region 的话,
    // 读屏用户只会听到一片沉默。
    mockState.isLoading = true;
    try {
      renderOpen();
      expect(screen.getByRole("status")).toBeTruthy();
    } finally {
      mockState.isLoading = false;
    }
  });

  it("reports an unknown concept as empty, not as a broken panel", () => {
    mockApiData[url()] = payload({
      status: "empty",
      reason: "unknown_concept",
      rows: [],
      total: 0,
      returned: 0,
    });
    renderOpen();
    expect(screen.getByText("该概念暂无成分股")).toBeTruthy();
  });

  it("labels a total outage of quotes instead of implying live prices", () => {
    mockApiData[url()] = payload({ quote_source: "none", as_of: null, priced_count: 0 });
    renderOpen();
    expect(screen.getByTestId("quote-source").textContent).toContain("无行情");
  });

  it("labels the scroll region so it is reachable and nameable", () => {
    // 滚动容器没有 role/label 时,axe 报 scrollable-region-focusable(serious):
    // Chromium 自带滚动容器可聚焦,其他引擎没有这个保证。
    mockApiData[url()] = payload();
    renderOpen();
    const region = screen.getByRole("region", { name: "成分股列表" });
    expect(region.getAttribute("tabindex")).toBe("0");
  });

  it("shows skeletons rather than a centred spinner while loading", () => {
    mockState.isLoading = true;
    try {
      renderOpen();
      const skeleton = screen.getByTestId("member-skeleton");
      expect(skeleton.getAttribute("aria-busy")).toBe("true");
      // 6 行占位,与真实行高一致 —— 版面在数据到达前后不该跳。
      expect(skeleton.querySelectorAll("div[style]").length).toBe(6);
    } finally {
      mockState.isLoading = false;
    }
  });

  it("clears the search when the dialog closes so reopening is not a blank table", () => {
    mockApiData[url()] = payload();
    const { rerender } = render(
      <ConceptMembersDialog concept="银行" open onOpenChange={() => {}} />,
    );
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "浦发" } });
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("浦发");

    rerender(<ConceptMembersDialog concept="银行" open={false} onOpenChange={() => {}} />);
    rerender(<ConceptMembersDialog concept="银行" open onOpenChange={() => {}} />);
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("");
  });

  it("keeps the sort mode across reopen (it is a preference, not a query)", () => {
    mockApiData[url()] = payload();
    const { rerender } = render(
      <ConceptMembersDialog concept="银行" open onOpenChange={() => {}} />,
    );
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "name" } });
    rerender(<ConceptMembersDialog concept="银行" open={false} onOpenChange={() => {}} />);
    rerender(<ConceptMembersDialog concept="银行" open onOpenChange={() => {}} />);
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("name");
  });

  it("keeps the controls on the design radius ladder", () => {
    // 11px 根字号下裸 rounded = 2.75px,不在 2/3/4/6 阶梯上,而且聚焦时会被
    // globals.css 的 :focus-visible{border-radius:2px} 掰成 2px(可见的形状跳变)。
    mockApiData[url()] = payload();
    renderOpen();
    for (const el of [screen.getByRole("searchbox"), screen.getByRole("combobox")]) {
      expect(el.className).toContain("rounded-xs");
      expect(el.className).not.toMatch(/(?<![-\w])rounded(?![-\w])/);
    }
  });

  it("sizes the close button in explicit px, not rem-based Tailwind steps", () => {
    // 计划 §3.1 要求热区 ≥24×24。根字号是 11px,所以 h-6/w-6(=1.5rem)只有 16.5px
    // —— 实测过。jsdom 没有布局引擎,量不到包围盒,只能钉住「用的是显式 px」这个
    // 决定;真实的 24×24 由 Playwright 探针(scripts/_concept-audit.mjs)负责验收。
    mockApiData[url()] = payload();
    renderOpen();
    const close = screen.getByRole("button", { name: "关闭" });
    expect(close.className).toContain("h-[24px]");
    expect(close.className).toContain("w-[24px]");
    expect(close.className).not.toMatch(/(?<![-\w])[hw]-6(?![-\w])/);
    // 不写 focus:outline-none,否则全局金色 :focus-visible 环被吃掉。
    expect(close.className).not.toContain("outline-none");
  });

  it("returns the list to the top when the filter or sort changes", () => {
    // 滚到底再搜索时,列表变短而 scrollTop 停在旧位置 ⇒ 虚拟窗口落在空区间,
    // 面板看起来像「一条都没有」。
    const rows = Array.from({ length: 60 }, (_, i) =>
      member({
        symbol: `60${String(i).padStart(4, "0")}.SH`,
        name: i % 2 === 0 ? "浦发银行" : "招商银行",
      }),
    );
    mockApiData[url()] = payload({ rows, total: 60, returned: 60 });
    renderOpen();

    const scroller = screen.getByTestId("member-scroll");
    scroller.scrollTop = 900;

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "浦发" } });
    expect(scroller.scrollTop).toBe(0);

    scroller.scrollTop = 900;
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "name" } });
    expect(scroller.scrollTop).toBe(0);
  });
});

describe("ConceptMembersTrigger", () => {
  beforeEach(() => {
    for (const k of Object.keys(mockApiData)) delete mockApiData[k];
    for (const k of Object.keys(mockApiErrors)) delete mockApiErrors[k];
    requestedPaths.length = 0;
  });

  it("returns keyboard focus to the trigger after the dialog closes", async () => {
    // Radix 关闭时走 triggerRef.current?.focus();triggerRef 只由 DialogTrigger 填充。
    // 用裸 <button> 时它恒为 null,焦点落到 <body> —— 键盘用户得从页首重新 Tab。
    mockApiData[url()] = payload();
    render(
      <ConceptMembersTrigger concept="银行">银行</ConceptMembersTrigger>,
    );

    const trigger = screen.getByRole("button", { name: "银行" });
    fireEvent.click(trigger);
    expect(await screen.findByRole("dialog")).toBeTruthy();

    fireEvent.keyDown(document.activeElement ?? document.body, {
      key: "Escape",
      code: "Escape",
    });

    await vi.waitFor(() => {
      expect(document.activeElement).toBe(trigger);
    });
  });

  // --- 从触发按钮驱动(不是直接渲染 Dialog) -------------------------------
  //
  // 上一组用例全都**显式**给 Dialog 传 sessionToday,所以它们证明不了「调用方
  // 传了」。实测把页面里的传参摘掉,86 个用例仍然全绿,而生产上陈旧标注会静默
  // 消失 —— freshnessBadge(as_of, null) 返回 null,既不金色也没有方括号。
  // 这两条走 ConceptMembersTrigger(生产里页面真正用的入口),钉住那一环。

  it("judges snapshot staleness from the session day the caller passes in", async () => {
    mockApiData[url()] = payload({ quote_source: "snapshot", as_of: "2026-10-19" });
    render(
      <ConceptMembersTrigger concept="银行" sessionToday="20261021">
        银行
      </ConceptMembersTrigger>,
    );

    fireEvent.click(screen.getByRole("button", { name: "银行" }));
    const source = await screen.findByTestId("quote-source");

    const stale = source.querySelector(".text-gold");
    expect(stale).not.toBeNull();
    expect(stale?.textContent).toContain("20261019");
  });

  it("keeps the daily fallback quiet when it matches the expected daily session", async () => {
    // 反方向:日线盘中停在上一交易日是**正常**的。基准传错(用今天)会让这块
    // 每个交易日 9:30–15:00 常亮金色 = 一天亮 5.5 小时,警示等于没有。
    mockApiData[url()] = payload({ quote_source: "daily", as_of: "2026-10-23" });
    render(
      <ConceptMembersTrigger
        concept="银行"
        sessionToday="20261025"
        expectedDailySession="20261023"
      >
        银行
      </ConceptMembersTrigger>,
    );

    fireEvent.click(screen.getByRole("button", { name: "银行" }));
    const source = await screen.findByTestId("quote-source");
    expect(source.querySelector(".text-gold")).toBeNull();
  });
});
