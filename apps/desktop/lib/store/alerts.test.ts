import { describe, it, expect, beforeEach, beforeAll, vi } from "vitest";
import { useAlertsStore, ALERTS_STORAGE_KEY } from "./alerts";

vi.mock("@/lib/api/intelligence", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api/intelligence")>();
  return { ...mod, syncAlertRules: vi.fn() };
});

import { syncAlertRules } from "@/lib/api/intelligence";

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
  channel_id: "telegram",
};

beforeEach(() => {
  window.localStorage.clear();
  useAlertsStore.setState({ rules: [], draft: null, hydrated: false, syncError: null });
  vi.clearAllMocks();
});

describe("useAlertsStore(预警规则本地持久)", () => {
  it("addRule → 规则加入列表 + localStorage 持久", () => {
    useAlertsStore.getState().addRule(draft);
    expect(useAlertsStore.getState().rules).toHaveLength(1);
    const stored = JSON.parse(window.localStorage.getItem(ALERTS_STORAGE_KEY)!);
    expect(stored[0].name).toBe("BTC 突破");
    expect(stored[0].id).toMatch(/^alert-/);
  });

  it("toggleRule → enabled 翻转且立即生效", () => {
    const rule = useAlertsStore.getState().addRule(draft);
    useAlertsStore.getState().toggleRule(rule.id);
    expect(useAlertsStore.getState().rules[0].enabled).toBe(false);
    expect(JSON.parse(window.localStorage.getItem(ALERTS_STORAGE_KEY)!)[0].enabled).toBe(false);
  });

  it("deleteRule → 从列表移除 + localStorage 同步", () => {
    const rule = useAlertsStore.getState().addRule(draft);
    useAlertsStore.getState().deleteRule(rule.id);
    expect(useAlertsStore.getState().rules).toHaveLength(0);
    expect(window.localStorage.getItem(ALERTS_STORAGE_KEY)).toBe("[]");
  });

  it("hydrate → 从 localStorage 恢复(刷新仍在)", () => {
    window.localStorage.setItem(ALERTS_STORAGE_KEY, JSON.stringify([{ id: "x1", ...draft }]));
    useAlertsStore.getState().hydrate();
    expect(useAlertsStore.getState().rules[0].id).toBe("x1");
    expect(useAlertsStore.getState().hydrated).toBe(true);
  });

  it("syncFromBackend → GET 合并按 id 去重(本地优先,后端补充非 concat)", async () => {
    useAlertsStore.getState().addRule({ ...draft, name: "本地规则" });
    const local = useAlertsStore.getState().rules[0];

    vi.mocked(syncAlertRules).mockResolvedValue({
      rules: [
        { ...draft, id: local.id, name: "后端同名规则" },
        { ...draft, id: "be1", name: "后端规则" },
      ],
      version: "1",
    });

    await useAlertsStore.getState().syncFromBackend();

    // 去重:同 id 后端不重复添加(本地1 + 新增be1 = 2)
    expect(useAlertsStore.getState().rules).toHaveLength(2);
    const ids = useAlertsStore.getState().rules.map((r) => r.id);
    expect(ids).toContain("be1");
    // 本地优先:同 id 的 name 保持本地,不被后端覆盖
    expect(useAlertsStore.getState().rules.find((r) => r.id === local.id)?.name).toBe("本地规则");
    // 合并结果也落 localStorage
    expect(JSON.parse(window.localStorage.getItem(ALERTS_STORAGE_KEY)!)).toHaveLength(2);
  });

  it("syncFromBackend → GET 失败设 syncError 且不崩", async () => {
    vi.mocked(syncAlertRules).mockRejectedValue(new Error("ECONNREFUSED"));
    await useAlertsStore.getState().syncFromBackend();
    expect(useAlertsStore.getState().rules).toHaveLength(0);
    expect(useAlertsStore.getState().syncError).toContain("同步失败");
  });
});
