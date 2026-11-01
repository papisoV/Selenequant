import { describe, it, expect, beforeEach, beforeAll, vi } from "vitest";
import { ApiError } from "@/lib/api-client";
import { useAlertsStore, ALERTS_STORAGE_KEY } from "./alerts";

vi.mock("@/lib/api/intelligence", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api/intelligence")>();
  return {
    ...mod,
    syncAlertRules: vi.fn(),
    seedAlertRules: vi.fn(),
    createAlertRule: vi.fn(),
    updateAlertRule: vi.fn(),
    deleteAlertRule: vi.fn(),
  };
});

import {
  createAlertRule,
  deleteAlertRule,
  seedAlertRules,
  syncAlertRules,
  updateAlertRule,
} from "@/lib/api/intelligence";

// 测试用 localStorage mock(与 workspace-layouts.test.ts 同模式)
beforeAll(() => {
  const store: Record<string, string> = {};
  Object.defineProperty(window, "localStorage", {
    value: {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => {
        store[key] = value;
      },
      removeItem: (key: string) => {
        delete store[key];
      },
      clear: () => {
        for (const k of Object.keys(store)) delete store[k];
      },
    },
    writable: true,
  });
});

const draft = {
  name: "BTC 突破",
  type: "price",
  threshold: { operator: "gt", value: 100000 },
  enabled: true,
  symbol: "BTC/USDT",
  channel_id: "telegram",
};

/** 让挂起的写请求走廊走完(mock 全部立即 resolve,几轮微任务足够)。 */
async function flush(): Promise<void> {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}

const stored = (): unknown[] =>
  JSON.parse(window.localStorage.getItem(ALERTS_STORAGE_KEY) ?? "[]");

beforeEach(() => {
  window.localStorage.clear();
  useAlertsStore.setState({ rules: [], draft: null, hydrated: false, syncError: null });
  vi.clearAllMocks();
  vi.mocked(syncAlertRules).mockResolvedValue({ rules: [], seeded: true });
  vi.mocked(seedAlertRules).mockResolvedValue({ rules: [], seeded: true });
  vi.mocked(deleteAlertRule).mockResolvedValue(undefined);
});

describe("useAlertsStore(预警规则:后端为准)", () => {
  it("hydrate → 从 localStorage 恢复(刷新仍在)", () => {
    window.localStorage.setItem(ALERTS_STORAGE_KEY, JSON.stringify([{ id: "x1", ...draft }]));
    useAlertsStore.getState().hydrate();
    expect(useAlertsStore.getState().rules[0].id).toBe("x1");
    expect(useAlertsStore.getState().hydrated).toBe(true);
  });

  it("addRule → 立即本地可见,后端成功后换成后端 id 且清掉 localOnly", async () => {
    vi.mocked(createAlertRule).mockResolvedValue({ ...draft, id: "be-1" });

    const local = useAlertsStore.getState().addRule(draft);
    // 乐观更新:用户立刻看到规则,而不是等一次网络往返
    expect(useAlertsStore.getState().rules).toHaveLength(1);
    expect(local.localOnly).toBe(true);

    await flush();

    const rules = useAlertsStore.getState().rules;
    expect(rules).toHaveLength(1);
    expect(rules[0].id).toBe("be-1");
    expect(rules[0].localOnly).toBeUndefined();
    expect(stored()).toHaveLength(1);
    expect((stored()[0] as { id: string }).id).toBe("be-1");
  });

  it("addRule 后端失败 → 规则仍在本地并标 syncError(不丢用户输入)", async () => {
    vi.mocked(createAlertRule).mockRejectedValue(new Error("ECONNREFUSED"));

    useAlertsStore.getState().addRule(draft);
    await flush();

    const state = useAlertsStore.getState();
    expect(state.rules).toHaveLength(1);
    expect(state.rules[0].localOnly).toBe(true);
    expect(state.syncError).toContain("写入失败");
    // 未同步标记必须落盘 —— 否则刷新后这条规则会被当成「后端已删除」抹掉
    expect((stored()[0] as { localOnly?: boolean }).localOnly).toBe(true);
  });

  it("addRule 排在进行中的首次迁移之后(避免同一条被上传两次)", async () => {
    let releaseSeed: (v: { rules: [] ; seeded: true }) => void = () => {};
    vi.mocked(syncAlertRules).mockResolvedValue({ rules: [], seeded: false });
    vi.mocked(seedAlertRules).mockImplementation(
      () => new Promise((resolve) => {
        releaseSeed = resolve;
      }),
    );
    vi.mocked(createAlertRule).mockResolvedValue({ ...draft, id: "be-1" });

    const syncing = useAlertsStore.getState().syncFromBackend();
    useAlertsStore.getState().addRule(draft);
    await flush();

    // seed 还没回来,POST 不能抢先发出
    expect(createAlertRule).not.toHaveBeenCalled();

    releaseSeed({ rules: [], seeded: true });
    await syncing;
    await flush();

    expect(createAlertRule).toHaveBeenCalledTimes(1);
    // 迁移只上传进页面时已存在的规则(此刻为空),所以不会重复
    expect(seedAlertRules).toHaveBeenCalledWith([]);
  });

  it("toggleRule → enabled 翻转并 PUT 整条规则", async () => {
    useAlertsStore.setState({ rules: [{ id: "be-1", ...draft }] });
    vi.mocked(updateAlertRule).mockResolvedValue({ id: "be-1", ...draft, enabled: false });

    useAlertsStore.getState().toggleRule("be-1");
    expect(useAlertsStore.getState().rules[0].enabled).toBe(false);

    await flush();
    expect(updateAlertRule).toHaveBeenCalledTimes(1);
    const [id, body] = vi.mocked(updateAlertRule).mock.calls[0];
    expect(id).toBe("be-1");
    // PUT 是整条替换:必须带全阈值,否则后端会把阈值清空
    expect(body).toMatchObject({ name: "BTC 突破", type: "price", enabled: false });
    expect(body.threshold).toEqual({ operator: "gt", value: 100000 });
    expect((body as Record<string, unknown>).localOnly).toBeUndefined();
  });

  it("deleteRule → 已同步规则会删后端", async () => {
    useAlertsStore.setState({ rules: [{ id: "be-1", ...draft }] });
    useAlertsStore.getState().deleteRule("be-1");
    await flush();

    expect(useAlertsStore.getState().rules).toHaveLength(0);
    expect(deleteAlertRule).toHaveBeenCalledWith("be-1");
    expect(stored()).toEqual([]);
  });

  it("deleteRule → 未同步规则只删本地(不发请求,不报假错)", async () => {
    vi.mocked(createAlertRule).mockRejectedValue(new Error("offline"));
    useAlertsStore.getState().addRule(draft);
    await flush();
    const localId = useAlertsStore.getState().rules[0].id;

    useAlertsStore.getState().deleteRule(localId);
    await flush();

    expect(useAlertsStore.getState().rules).toHaveLength(0);
    expect(deleteAlertRule).not.toHaveBeenCalled();
    expect(useAlertsStore.getState().syncError).toBeNull();
  });

  it("更新一条后端不存在的规则 → 404 后退回 POST", async () => {
    useAlertsStore.setState({
      rules: [{ id: "ghost", ...draft, localOnly: true }],
    });
    vi.mocked(updateAlertRule).mockRejectedValue(new ApiError("not found", 404));
    vi.mocked(createAlertRule).mockImplementation(async (body) => ({
      ...(body as typeof draft),
      id: "be-2",
    }));

    useAlertsStore.getState().updateRule("ghost", { name: "改名" });
    await flush();

    expect(createAlertRule).toHaveBeenCalledTimes(1);
    expect(useAlertsStore.getState().rules[0].id).toBe("be-2");
    expect(useAlertsStore.getState().rules[0].name).toBe("改名");
  });

  it("syncFromBackend 未迁移 → 上传本地旧规则并采纳后端返回", async () => {
    const legacy = { id: "alert-old", ...draft };
    useAlertsStore.setState({ rules: [legacy] });
    vi.mocked(syncAlertRules).mockResolvedValue({ rules: [], seeded: false });
    vi.mocked(seedAlertRules).mockResolvedValue({
      rules: [{ ...draft, id: "be-9" }],
      seeded: true,
      created: 1,
    });

    await useAlertsStore.getState().syncFromBackend();

    expect(seedAlertRules).toHaveBeenCalledWith([legacy]);
    expect(useAlertsStore.getState().rules[0].id).toBe("be-9");
    expect(useAlertsStore.getState().syncError).toBeNull();
  });

  it("syncFromBackend 未迁移且本地为空 → 仍发 seed(打上已迁移标记)", async () => {
    vi.mocked(syncAlertRules).mockResolvedValue({ rules: [], seeded: false });
    await useAlertsStore.getState().syncFromBackend();
    // 不发的话标记永远不会有,用户清空规则后下次进页面又被「迁移」回来
    expect(seedAlertRules).toHaveBeenCalledWith([]);
  });

  it("syncFromBackend 已迁移 → 后端为准,本地多出来的规则消失", async () => {
    useAlertsStore.setState({ rules: [{ id: "stale", ...draft }] });
    vi.mocked(syncAlertRules).mockResolvedValue({
      rules: [{ ...draft, id: "be-1" }],
      seeded: true,
    });

    await useAlertsStore.getState().syncFromBackend();

    const ids = useAlertsStore.getState().rules.map((r) => r.id);
    expect(ids).toEqual(["be-1"]);
    expect(stored()).toHaveLength(1);
  });

  it("syncFromBackend 已迁移 → 保住写失败的 localOnly 规则", async () => {
    useAlertsStore.setState({
      rules: [{ id: "alert-local", ...draft, localOnly: true }],
    });
    vi.mocked(syncAlertRules).mockResolvedValue({
      rules: [{ ...draft, id: "be-1" }],
      seeded: true,
    });

    await useAlertsStore.getState().syncFromBackend();

    const ids = useAlertsStore.getState().rules.map((r) => r.id);
    expect(ids).toEqual(["be-1", "alert-local"]);
  });

  it("syncFromBackend 迁移被拒收的规则留在列表里并提示", async () => {
    // 最典型:旧 price 规则没有 symbol,新 schema 要求必填 ⇒ 后端跳过它
    const legacy = { id: "alert-nosymbol", ...draft, symbol: undefined };
    useAlertsStore.setState({ rules: [legacy] });
    vi.mocked(syncAlertRules).mockResolvedValue({ rules: [], seeded: false });
    vi.mocked(seedAlertRules).mockResolvedValue({
      rules: [],
      seeded: true,
      created: 0,
      skipped: [{ id: "alert-nosymbol", errors: ["symbol is required"] }],
    });

    await useAlertsStore.getState().syncFromBackend();

    const state = useAlertsStore.getState();
    expect(state.rules).toHaveLength(1);
    expect(state.rules[0].id).toBe("alert-nosymbol");
    // 它确实不在后端,必须如实标记「不会触发」,而不是假装在运行
    expect(state.rules[0].localOnly).toBe(true);
    expect(state.syncError).toContain("未迁移");
  });

  it("syncFromBackend 请求失败 → 设 syncError 且保留现有规则", async () => {
    useAlertsStore.setState({ rules: [{ id: "be-1", ...draft }] });
    vi.mocked(syncAlertRules).mockRejectedValue(new Error("ECONNREFUSED"));

    await useAlertsStore.getState().syncFromBackend();

    expect(useAlertsStore.getState().rules).toHaveLength(1);
    expect(useAlertsStore.getState().syncError).toContain("同步失败");
  });
});
