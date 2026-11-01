import { describe, it, expect, beforeEach, afterEach, beforeAll, vi } from "vitest";
import {
  WORKSPACE_LAYOUT_STORAGE_KEY,
  WORKSPACE_LAYOUT_SCHEMA_VERSION,
  WORKSPACE_LAYOUT_TOMBSTONES_KEY,
  MAX_LAYOUT_TOMBSTONES,
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
  readTombstones,
  writeTombstones,
  mergeLayouts,
  subscribeLayouts,
  applyMergedLayouts,
  type WorkspaceLayout,
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

describe("ensurePresetsSeeded reconcile(预设随版本更新)", () => {
  /** 模拟 2723fa02 之前种下的老存储:3 预设 + trade-watch 为 v1(2 栏 2 面板)
   *  直接写 raw storage —— saveLayout 会给新 id 盖 createdAt=now,且老版本没有种子状态 */
  function seedLegacyStore(): void {
    const at = "2026-06-01T00:00:00Z";
    const legacy = (l: {
      id: string;
      name: string;
      panels: { id: string }[];
      columns: number;
    }) => ({
      ...l,
      schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
      createdAt: at,
      updatedAt: at,
    });
    window.localStorage.setItem(
      WORKSPACE_LAYOUT_STORAGE_KEY,
      JSON.stringify({
        layouts: [
          legacy({
            id: "signal",
            name: "信号 → 下单",
            panels: [{ id: "watch_vchart" }, { id: "order_form" }],
            columns: 2,
          }),
          legacy({
            id: "research",
            name: "研究双栏",
            panels: [{ id: "alpha_mine" }, { id: "factors" }],
            columns: 2,
          }),
          legacy({
            id: "trade-watch",
            name: "盯盘 + 下单",
            panels: [{ id: "watch_kf" }, { id: "order_form" }],
            columns: 2,
          }),
        ],
      }),
    );
  }

  it("老存储:未改过的旧预设刷新到当前内容,createdAt 保留", () => {
    seedLegacyStore();
    ensurePresetsSeeded();

    const tw = loadLayout("trade-watch");
    expect(tw?.panels.map((p) => p.id)).toEqual([
      "watch_kf",
      "watch_ob",
      "order_form",
    ]);
    expect(tw?.columns).toBe(3);
    expect(tw?.name).toBe("盯盘 + 盘口 + 下单");
    expect(tw?.createdAt).toBe("2026-06-01T00:00:00Z");
  });

  it("老存储:目录新增的预设被补上", () => {
    seedLegacyStore();
    expect(loadLayout("onchain")).toBeNull();

    ensurePresetsSeeded();
    expect(loadLayout("onchain")).not.toBeNull();
    expect(listLayouts()).toHaveLength(4);
  });

  it("用户编辑过的预设不被覆盖", () => {
    ensurePresetsSeeded();
    const edited = loadLayout("signal")!;
    saveLayout({ ...edited, panels: [{ id: "watch_sig" }], columns: 1 });

    ensurePresetsSeeded();

    const after = loadLayout("signal")!;
    expect(after.panels.map((p) => p.id)).toEqual(["watch_sig"]);
    expect(after.columns).toBe(1);
  });

  it("用户删掉的预设不复活", () => {
    ensurePresetsSeeded();
    deleteLayout("onchain");

    ensurePresetsSeeded();
    ensurePresetsSeeded();

    expect(loadLayout("onchain")).toBeNull();
    expect(listLayouts()).toHaveLength(3);
  });

  it("老存储里被删过的预设(无种子状态)也不复活", () => {
    seedLegacyStore(); // 只有 signal / research / trade-watch
    ensurePresetsSeeded(); // 补 onchain;signal+research 本就是最新
    expect(loadLayout("onchain")).not.toBeNull();

    // 再删 onchain 后重复调用 —— 种子状态已记过,不再复活
    deleteLayout("onchain");
    ensurePresetsSeeded();
    expect(loadLayout("onchain")).toBeNull();
  });

  it("老存储缺了曾经种过的预设 = 用户删的,不复活", () => {
    // 模拟用户在上个版本删掉 signal 后留下的存储:无种子状态,只剩 research
    window.localStorage.setItem(
      WORKSPACE_LAYOUT_STORAGE_KEY,
      JSON.stringify({
        layouts: [
          {
            id: "research",
            name: "研究双栏",
            panels: [{ id: "alpha_mine" }, { id: "factors" }],
            columns: 2,
            schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
            createdAt: "2026-06-01T00:00:00Z",
            updatedAt: "2026-06-01T00:00:00Z",
          },
        ],
      }),
    );

    ensurePresetsSeeded();
    expect(loadLayout("signal")).toBeNull();

    ensurePresetsSeeded();
    expect(loadLayout("signal")).toBeNull();
    // onchain 是后来才加入目录的,不算「用户删的」→ 补上
    expect(loadLayout("onchain")).not.toBeNull();
  });

  it("稳态零写入:连续调用不改存储、不动 updatedAt", () => {
    ensurePresetsSeeded();
    const layoutsSnapshot = window.localStorage.getItem(
      WORKSPACE_LAYOUT_STORAGE_KEY,
    );
    const signalUpdatedAt = loadLayout("signal")!.updatedAt;
    const setItemSpy = vi.spyOn(window.localStorage, "setItem");

    ensurePresetsSeeded();
    ensurePresetsSeeded();

    expect(setItemSpy).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(WORKSPACE_LAYOUT_STORAGE_KEY)).toBe(
      layoutsSnapshot,
    );
    expect(loadLayout("signal")!.updatedAt).toBe(signalUpdatedAt);
    setItemSpy.mockRestore();
  });

  it("用户自建布局的存储不注入预设(也没有种子状态落盘)", () => {
    saveLayout({
      id: "user-only",
      name: "我的布局",
      panels: [{ id: "factors" }],
      columns: 1,
      schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
      createdAt: "2026-08-03T00:00:00Z",
      updatedAt: "2026-08-03T00:00:00Z",
    });
    const setItemSpy = vi.spyOn(window.localStorage, "setItem");

    ensurePresetsSeeded();

    expect(listLayouts().map((l) => l.id)).toEqual(["user-only"]);
    expect(setItemSpy).not.toHaveBeenCalled();
    setItemSpy.mockRestore();
  });

  it("用户把布局全删光 → 不补种(删除是显式意图)", () => {
    ensurePresetsSeeded();
    for (const id of getPresetLayouts().map((p) => p.id)) deleteLayout(id);
    expect(listLayouts()).toHaveLength(0);

    ensurePresetsSeeded();
    expect(listLayouts()).toHaveLength(0);
  });

  it("存储损坏 → 补种预设(不让用户面对空白工作台)", () => {
    ensurePresetsSeeded();
    window.localStorage.setItem(WORKSPACE_LAYOUT_STORAGE_KEY, "{不是 JSON");

    ensurePresetsSeeded();

    expect(listLayouts()).toHaveLength(4);
    expect(loadLayout("signal")).not.toBeNull();
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

describe("跨设备同步 — 墓碑", () => {
  it("deleteLayout 写墓碑", () => {
    ensurePresetsSeeded();
    deleteLayout("onchain");

    const tombstones = readTombstones();
    expect(Object.keys(tombstones)).toEqual(["onchain"]);
    expect(typeof tombstones.onchain).toBe("string");
  });

  it("删除后再保存同 id → 墓碑被清掉(重建优先)", () => {
    ensurePresetsSeeded();
    const signal = loadLayout("signal")!;
    deleteLayout("signal");
    expect(readTombstones().signal).toBeTruthy();

    saveLayout(signal);

    expect(readTombstones().signal).toBeUndefined();
  });

  it("墓碑存在时 ensurePresetsSeeded 不复活被删的预设", () => {
    ensurePresetsSeeded();
    deleteLayout("onchain");
    // 抹掉种子状态签名,只剩墓碑能阻止复活 —— 证明墓碑本身有效
    // (该 key 是模块内部状态;测试直接按字面量清,不为此导出内部实现)
    window.localStorage.removeItem("selenespace:workspace:presets-seeded:v1");

    ensurePresetsSeeded();

    expect(loadLayout("onchain")).toBeNull();
  });

  it("订阅者在保存/删除/改名时收到通知", () => {
    const seen: string[] = [];
    const off = subscribeLayouts(() => seen.push("changed"));

    const saved = saveLayout({
      id: "notify-1",
      name: "通知测试",
      panels: [{ id: "factors" }],
      columns: 1,
      schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
      createdAt: "2026-01-01T00:00:00Z",
      updatedAt: "2026-01-01T00:00:00Z",
    });
    renameLayout("notify-1", "改名");
    deleteLayout("notify-1");
    off();
    // 退订后不再收到
    saveLayout(saved);

    expect(seen).toHaveLength(3);
  });

  it("applyMergedLayouts 把合并结果落盘", () => {
    saveLayout({
      id: "only-local",
      name: "只有本地有",
      panels: [{ id: "factors" }],
      columns: 1,
      schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
      createdAt: "2026-01-01T00:00:00Z",
      updatedAt: "2026-02-01T00:00:00Z",
    });
    applyMergedLayouts({
      layouts: [
        {
          id: "from-remote",
          name: "从远端来",
          panels: [{ id: "alpha_mine" }],
          columns: 2,
          schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
          createdAt: "2026-01-01T00:00:00Z",
          updatedAt: "2026-02-01T00:00:00Z",
        },
      ],
      tombstones: { gone: "2026-03-01T00:00:00Z" },
    });

    expect(listLayouts().map((l) => l.id)).toEqual(["from-remote"]);
    expect(readTombstones().gone).toBe("2026-03-01T00:00:00Z");
  });

  it("墓碑超上限时保留最近删除的", () => {
    const many: Record<string, string> = {};
    for (let i = 0; i < MAX_LAYOUT_TOMBSTONES + 20; i++) {
      // 递增时间戳,越大越新
      many[`ws-${i}`] = new Date(Date.UTC(2026, 0, 1) + i * 1000).toISOString();
    }
    writeTombstones(many);

    const kept = readTombstones();
    expect(Object.keys(kept)).toHaveLength(MAX_LAYOUT_TOMBSTONES);
    // 最新的那条一定在,最旧的一定被淘汰
    expect(kept[`ws-${MAX_LAYOUT_TOMBSTONES + 19}`]).toBeTruthy();
    expect(kept["ws-0"]).toBeUndefined();
  });

  it("墓碑存储损坏 → 读成空对象,不抛", () => {
    window.localStorage.setItem(WORKSPACE_LAYOUT_TOMBSTONES_KEY, "{坏 JSON");
    expect(() => readTombstones()).not.toThrow();
    expect(readTombstones()).toEqual({});
  });

  it("墓碑不是对象(数组)→ 读成空对象", () => {
    window.localStorage.setItem(WORKSPACE_LAYOUT_TOMBSTONES_KEY, "[]");
    expect(readTombstones()).toEqual({});
  });
});

describe("跨设备同步 — mergeLayouts", () => {
  const layout = (
    id: string,
    updatedAt: string,
    name = id,
  ): WorkspaceLayout => ({
    id,
    name,
    panels: [{ id: "factors" }],
    columns: 1,
    schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt,
  });

  it("两边都有同 id → updatedAt 较新者胜", () => {
    const merged = mergeLayouts(
      { layouts: [layout("a", "2026-02-01T00:00:00Z", "本地")], tombstones: {} },
      { layouts: [layout("a", "2026-03-01T00:00:00Z", "远端")], tombstones: {} },
    );
    expect(merged.layouts).toHaveLength(1);
    expect(merged.layouts[0].name).toBe("远端");
  });

  it("本地更新 → 本地胜(不能被远端旧版本覆盖)", () => {
    const merged = mergeLayouts(
      { layouts: [layout("a", "2026-05-01T00:00:00Z", "本地")], tombstones: {} },
      { layouts: [layout("a", "2026-03-01T00:00:00Z", "远端")], tombstones: {} },
    );
    expect(merged.layouts[0].name).toBe("本地");
  });

  it("updatedAt 相等 → 本地胜(避免无意义改写)", () => {
    const ts = "2026-03-01T00:00:00Z";
    const merged = mergeLayouts(
      { layouts: [layout("a", ts, "本地")], tombstones: {} },
      { layouts: [layout("a", ts, "远端")], tombstones: {} },
    );
    expect(merged.layouts[0].name).toBe("本地");
  });

  it("只有一侧有的布局都保留", () => {
    const merged = mergeLayouts(
      { layouts: [layout("local-only", "2026-03-01T00:00:00Z")], tombstones: {} },
      { layouts: [layout("remote-only", "2026-03-01T00:00:00Z")], tombstones: {} },
    );
    expect(merged.layouts.map((l) => l.id).sort()).toEqual([
      "local-only",
      "remote-only",
    ]);
  });

  it("远端墓碑删掉本地布局(另一台机器删的)", () => {
    const merged = mergeLayouts(
      { layouts: [layout("a", "2026-03-01T00:00:00Z")], tombstones: {} },
      { layouts: [], tombstones: { a: "2026-04-01T00:00:00Z" } },
    );
    expect(merged.layouts).toHaveLength(0);
    expect(merged.tombstones.a).toBe("2026-04-01T00:00:00Z");
  });

  it("本地墓碑删掉远端布局(本机删的,不能从云端复活)", () => {
    const merged = mergeLayouts(
      { layouts: [], tombstones: { a: "2026-04-01T00:00:00Z" } },
      { layouts: [layout("a", "2026-03-01T00:00:00Z")], tombstones: {} },
    );
    expect(merged.layouts).toHaveLength(0);
    expect(merged.tombstones.a).toBe("2026-04-01T00:00:00Z");
  });

  it("删除比更新新 → 删除胜(即使远端布局的 updatedAt 更晚)", () => {
    const merged = mergeLayouts(
      { layouts: [], tombstones: { a: "2026-06-01T00:00:00Z" } },
      { layouts: [layout("a", "2026-05-01T00:00:00Z")], tombstones: {} },
    );
    expect(merged.layouts).toHaveLength(0);
  });

  it("本地在墓碑之后重建了同 id → 重建胜,墓碑被清", () => {
    const merged = mergeLayouts(
      {
        layouts: [layout("a", "2026-07-01T00:00:00Z", "重建")],
        tombstones: { a: "2026-06-01T00:00:00Z" },
      },
      { layouts: [], tombstones: {} },
    );
    expect(merged.layouts.map((l) => l.id)).toEqual(["a"]);
    expect(merged.tombstones.a).toBeUndefined();
  });

  it("远端独有的墓碑要保留(否则那条布局会从第三方设备复活)", () => {
    const merged = mergeLayouts(
      { layouts: [], tombstones: {} },
      { layouts: [], tombstones: { "from-other-device": "2026-06-01T00:00:00Z" } },
    );
    expect(merged.tombstones["from-other-device"]).toBe(
      "2026-06-01T00:00:00Z",
    );
  });

  it("合并结果不依赖入参顺序(除本地胜的平局规则外)", () => {
    const local = { layouts: [layout("a", "2026-02-01T00:00:00Z")], tombstones: {} };
    const remote = { layouts: [layout("b", "2026-02-01T00:00:00Z")], tombstones: {} };
    const forward = mergeLayouts(local, remote).layouts.map((l) => l.id).sort();
    const backward = mergeLayouts(remote, local).layouts.map((l) => l.id).sort();
    expect(forward).toEqual(backward);
  });
});
