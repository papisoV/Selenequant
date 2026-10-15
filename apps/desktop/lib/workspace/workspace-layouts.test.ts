import { describe, it, expect, beforeEach, afterEach, beforeAll, vi } from "vitest";
import {
  WORKSPACE_LAYOUT_STORAGE_KEY,
  WORKSPACE_LAYOUT_SCHEMA_VERSION,
  GRID_TRACK_MIN,
  WORKSPACE_LAST_USED_LAYOUT_KEY,
  saveLayout,
  loadLayout,
  listLayouts,
  deleteLayout,
  renameLayout,
  duplicateLayout,
  generateLayoutId,
  ensurePresetsSeeded,
  ensureMigrated,
  migrateLayoutsV1ToV2,
  getLastUsedLayoutId,
  setLastUsedLayoutId,
  getPresetLayouts,
  importLayoutFromJSON,
} from "./workspace-layouts";

// 测试用 localStorage mock(不污染真实存储)
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

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("workspace-layouts CRUD", () => {
  it("saveLayout + loadLayout — 存取一致", () => {
    const layout = {
      id: "test-1",
      name: "测试布局",
      panels: [{ id: "alpha_mine" as const }, { id: "factors" as const }],
      columns: 2,
      schemaVersion: 1,
      createdAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
    };
    saveLayout(layout);
    const loaded = loadLayout("test-1");
    expect(loaded).not.toBeNull();
    expect(loaded?.name).toBe("测试布局");
    expect(loaded?.panels).toHaveLength(2);
    expect(loaded?.schemaVersion).toBe(WORKSPACE_LAYOUT_SCHEMA_VERSION);
  });

  it("listLayouts — 多布局列表", () => {
    saveLayout({
      id: "a",
      name: "A",
      panels: [],
      schemaVersion: 1,
      createdAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
    });
    saveLayout({
      id: "b",
      name: "B",
      panels: [],
      schemaVersion: 1,
      createdAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
    });
    const list = listLayouts();
    expect(list).toHaveLength(2);
    expect(list.map((l) => l.id).sort()).toEqual(["a", "b"]);
  });

  it("saveLayout — 重复 id 覆盖(更新而非新增)", () => {
    // 第一次 save(不传 createdAt,让源码生成)
    saveLayout({
      id: "dup",
      name: "原名称",
      panels: [],
      schemaVersion: 1,
      createdAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
    });
    const firstLoad = loadLayout("dup")!;
    const firstCreatedAt = firstLoad.createdAt;
    // 第二次 save(重复 id,源码应保留首次的 createdAt)
    saveLayout({
      id: "dup",
      name: "新名称",
      panels: [{ id: "alpha_mine" as const }],
      schemaVersion: 1,
      createdAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
    });
    const list = listLayouts();
    expect(list).toHaveLength(1);
    expect(list[0].name).toBe("新名称");
    expect(list[0].panels).toHaveLength(1);
    // createdAt 保留首次值(不被第二次 save 覆盖)
    expect(list[0].createdAt).toBe(firstCreatedAt);
  });

  it("deleteLayout — 删除后 listLayouts 不含", () => {
    saveLayout({
      id: "del",
      name: "待删",
      panels: [],
      schemaVersion: 1,
      createdAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
    });
    deleteLayout("del");
    expect(loadLayout("del")).toBeNull();
    expect(listLayouts()).toHaveLength(0);
  });

  it("renameLayout — 重命名不丢 panels", () => {
    saveLayout({
      id: "r1",
      name: "原名",
      panels: [{ id: "alpha_mine" as const }, { id: "factors" as const }],
      schemaVersion: 1,
      createdAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
    });
    const renamed = renameLayout("r1", "新名");
    expect(renamed?.name).toBe("新名");
    expect(renamed?.panels).toHaveLength(2);
  });

  it("duplicateLayout — 复制生成新 id", () => {
    saveLayout({
      id: "src",
      name: "源",
      panels: [{ id: "alpha_mine" as const }],
      schemaVersion: 1,
      columns: 2,
      createdAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
    });
    const dup = duplicateLayout("src", "copy-1", "源 副本");
    expect(dup?.id).toBe("copy-1");
    expect(dup?.name).toBe("源 副本");
    expect(dup?.panels).toHaveLength(1);
    expect(dup?.columns).toBe(2);
    // 原布局仍在
    expect(loadLayout("src")).not.toBeNull();
  });

  it("loadLayout — 不存在返回 null", () => {
    expect(loadLayout("nonexistent")).toBeNull();
  });

  it("generateLayoutId — 唯一性(连续调用不等)", () => {
    const ids = new Set<string>();
    for (let i = 0; i < 20; i++) {
      ids.add(generateLayoutId());
    }
    expect(ids.size).toBe(20);
  });
});

describe("workspace-layouts schema version", () => {
  it("saveLayout 强制 schemaVersion = 当前版本", () => {
    saveLayout({
      id: "v",
      name: "测试",
      panels: [],
      schemaVersion: 999, // 故意传错版本
      createdAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
    });
    const loaded = loadLayout("v");
    expect(loaded?.schemaVersion).toBe(WORKSPACE_LAYOUT_SCHEMA_VERSION);
  });

  it("存储 key 带 v1 后缀(未来 schema 变化时可迁移)", () => {
    expect(WORKSPACE_LAYOUT_STORAGE_KEY).toMatch(/:v1$/);
  });
});

describe("workspace-layouts presets", () => {
  it("getPresetLayouts — 4 个预设(signal/research/trade-watch/onchain)", () => {
    const presets = getPresetLayouts();
    expect(presets).toHaveLength(4);
    const ids = presets.map((p) => p.id).sort();
    expect(ids).toEqual(["onchain", "research", "signal", "trade-watch"]);
  });

  it("ensurePresetsSeeded — 空存储时初始化 4 预设", () => {
    expect(listLayouts()).toHaveLength(0);
    ensurePresetsSeeded();
    const list = listLayouts();
    expect(list).toHaveLength(4);
  });

  it("ensurePresetsSeeded — 已有布局时不覆盖(返回不补)", () => {
    saveLayout({
      id: "user-1",
      name: "用户布局",
      panels: [],
      schemaVersion: 1,
      createdAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
    });
    ensurePresetsSeeded();
    const list = listLayouts();
    // 用户布局已存在,ensurePresetsSeeded 不补预设
    expect(list).toHaveLength(1);
    expect(list.find((l) => l.id === "user-1")?.name).toBe("用户布局");
  });

  it("signal 预设 — 含 watch_vchart + order_form", () => {
    ensurePresetsSeeded();
    const signal = loadLayout("signal");
    expect(signal).not.toBeNull();
    const panelIds = signal?.panels.map((p) => p.id);
    expect(panelIds).toContain("watch_vchart");
    expect(panelIds).toContain("order_form");
  });

  it("research 预设 — 含 alpha_mine + factors", () => {
    ensurePresetsSeeded();
    const research = loadLayout("research");
    expect(research).not.toBeNull();
    const panelIds = research?.panels.map((p) => p.id);
    expect(panelIds).toContain("alpha_mine");
    expect(panelIds).toContain("factors");
  });

  it("onchain 预设 — 含 onchain_whale + onchain_sm", () => {
    ensurePresetsSeeded();
    const onchain = loadLayout("onchain");
    expect(onchain).not.toBeNull();
    const panelIds = onchain?.panels.map((p) => p.id);
    expect(panelIds).toContain("onchain_whale");
    expect(panelIds).toContain("onchain_sm");
  });
});

describe("P3 onchain panels(union/VALID/导入白名单)", () => {
  it("onchain 预设直接来自 getPresetLayouts(无 seed 也可见)", () => {
    const preset = getPresetLayouts().find((p) => p.id === "onchain");
    expect(preset).toBeTruthy();
    const ids = preset!.panels.map((p) => p.id);
    expect(ids).toEqual(["onchain_whale", "onchain_sm"]);
  });

  it("导入白名单接受 onchain_whale/onchain_sm(证明在 VALID_PANEL_IDS)", () => {
    const res = importLayoutFromJSON(
      JSON.stringify({
        id: "imp-onchain",
        name: "链上导入",
        panels: [{ id: "onchain_whale" }, { id: "onchain_sm" }],
        columns: 2,
        schemaVersion: 2,
      }),
    );
    expect(res.ok).not.toBeNull();
    expect(res.ok!.panels.map((p) => p.id)).toEqual(["onchain_whale", "onchain_sm"]);
  });

  it("保存/加载布局可用 onchain panel id(tsc 强校验 union 成员)", () => {
    saveLayout({
      id: "onchain-saved",
      name: "链上",
      panels: [{ id: "onchain_whale" }, { id: "onchain_sm" }],
      schemaVersion: 2,
      createdAt: "2026-08-12T00:00:00Z",
      updatedAt: "2026-08-12T00:00:00Z",
    });
    const loaded = loadLayout("onchain-saved");
    expect(loaded?.panels).toHaveLength(2);
  });
});

describe("workspace-layouts 容错", () => {
  it("listLayouts — localStorage 损坏(非 JSON)返回空", () => {
    window.localStorage.setItem(WORKSPACE_LAYOUT_STORAGE_KEY, "not-json{");
    expect(listLayouts()).toEqual([]);
  });

  it("listLayouts — layouts 字段非数组返回空", () => {
    window.localStorage.setItem(
      WORKSPACE_LAYOUT_STORAGE_KEY,
      JSON.stringify({ layouts: "not-array" }),
    );
    expect(listLayouts()).toEqual([]);
  });
});

describe("P7 schema v2 迁移 + GRID_TRACK_MIN + lastUsed", () => {
  it("GRID_TRACK_MIN 导出 480(JS/CSS 共用)", () => {
    expect(GRID_TRACK_MIN).toBe(480);
  });

  it("migrateLayoutsV1ToV2 — v1 数据升 v2(schemaVersion + w/h undefined)", () => {
    const v1Stored = {
      layouts: [
        {
          id: "old",
          name: "旧布局",
          panels: [{ id: "alpha_mine" as const }],
          schemaVersion: 1,
          createdAt: "2026-08-01T00:00:00Z",
          updatedAt: "2026-08-01T00:00:00Z",
        },
      ],
    };
    const v2 = migrateLayoutsV1ToV2(v1Stored);
    expect(v2.layouts[0].schemaVersion).toBe(WORKSPACE_LAYOUT_SCHEMA_VERSION);
    expect(v2.layouts[0].panels[0].w).toBeUndefined();
    expect(v2.layouts[0].panels[0].h).toBeUndefined();
  });

  it("ensureMigrated — v1 数据读后写回 v2,第二次读不迁移", () => {
    // 模拟 v1 数据(schemaVersion=1)
    const v1Data = {
      layouts: [
        {
          id: "v1-layout",
          name: "v1 布局",
          panels: [],
          schemaVersion: 1,
          createdAt: "2026-08-01T00:00:00Z",
          updatedAt: "2026-08-01T00:00:00Z",
        },
      ],
    };
    window.localStorage.setItem(
      WORKSPACE_LAYOUT_STORAGE_KEY,
      JSON.stringify(v1Data),
    );

    // 第一次调 ensureMigrated,应写回 v2
    ensureMigrated();
    const afterFirst = JSON.parse(
      window.localStorage.getItem(WORKSPACE_LAYOUT_STORAGE_KEY)!,
    );
    expect(afterFirst.layouts[0].schemaVersion).toBe(WORKSPACE_LAYOUT_SCHEMA_VERSION);

    // 第二次调 ensureMigrated,schemaVersion 已是 2,不应触发迁移
    // (无直接 spy,但通过 listLayouts 调 ensureMigrated 后数据稳定验证)
    listLayouts();
    const afterSecond = JSON.parse(
      window.localStorage.getItem(WORKSPACE_LAYOUT_STORAGE_KEY)!,
    );
    expect(afterSecond.layouts[0].schemaVersion).toBe(WORKSPACE_LAYOUT_SCHEMA_VERSION);
  });

  it("ensureMigrated — v2 数据不触发迁移(已是当前版本)", () => {
    saveLayout({
      id: "already-v2",
      name: "v2 布局",
      panels: [],
      schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
      createdAt: "2026-08-04T00:00:00Z",
      updatedAt: "2026-08-04T00:00:00Z",
    });
    const before = window.localStorage.getItem(WORKSPACE_LAYOUT_STORAGE_KEY);
    ensureMigrated();
    const after = window.localStorage.getItem(WORKSPACE_LAYOUT_STORAGE_KEY);
    expect(before).toBe(after);
  });

  it("ensureMigrated 不调 saveLayout(避免死循环)— v1 数据读 100 次不栈溢出", () => {
    const v1Data = {
      layouts: [
        {
          id: "v1",
          name: "v1",
          panels: [],
          schemaVersion: 1,
          createdAt: "2026-08-01T00:00:00Z",
          updatedAt: "2026-08-01T00:00:00Z",
        },
      ],
    };
    window.localStorage.setItem(
      WORKSPACE_LAYOUT_STORAGE_KEY,
      JSON.stringify(v1Data),
    );
    // 读 100 次,若死循环会栈溢出 RangeError
    for (let i = 0; i < 100; i++) {
      expect(() => listLayouts()).not.toThrow();
    }
  });

  it("getLastUsedLayoutId / setLastUsedLayoutId — 读写独立 key", () => {
    expect(getLastUsedLayoutId()).toBeNull();
    setLastUsedLayoutId("signal");
    expect(getLastUsedLayoutId()).toBe("signal");
    // 独立 key,不污染 layouts 列表
    const layoutsKey = window.localStorage.getItem(WORKSPACE_LAYOUT_STORAGE_KEY);
    const lastUsedKey = window.localStorage.getItem(WORKSPACE_LAST_USED_LAYOUT_KEY);
    expect(lastUsedKey).toBe("signal");
    expect(layoutsKey).toBeNull();
  });
});
