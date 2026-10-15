import { describe, it, expect, beforeAll, beforeEach, afterEach } from "vitest";
import {
  WORKSPACE_LAYOUT_STORAGE_KEY,
  WORKSPACE_LAYOUT_SCHEMA_VERSION,
  MAX_COLUMNS,
  exportLayoutToJSON,
  importLayoutFromJSON,
  saveLayout,
  loadLayout,
  type WorkspaceLayout,
} from "../workspace-layouts";

// 测试用 localStorage mock(不污染真实存储)— 与 workspace-layouts.test.ts 同模式
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

// 测试 fixture:合法 v2 布局
const sampleLayout: WorkspaceLayout = {
  id: "test-1",
  name: "测试布局",
  panels: [
    { id: "alpha_mine" },
    { id: "factors" },
  ],
  columns: 2,
  schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
  createdAt: "2026-08-04T00:00:00Z",
  updatedAt: "2026-08-04T00:00:00Z",
};

describe("P8 导出 exportLayoutToJSON", () => {
  it("#1 导出含 schemaVersion + exportedAt(无 appVersion)", () => {
    const json = exportLayoutToJSON(sampleLayout);
    const parsed = JSON.parse(json);
    expect(parsed.schemaVersion).toBe(WORKSPACE_LAYOUT_SCHEMA_VERSION);
    expect(typeof parsed.exportedAt).toBe("string");
    expect(parsed.exportedAt).toMatch(/^\d{4}-\d{2}-\d{2}T/); // ISO 时间戳
    expect(parsed.appVersion).toBeUndefined(); // revision #1: 无 appVersion
  });

  it("#2 导出保留 panels 结构", () => {
    const json = exportLayoutToJSON(sampleLayout);
    const parsed = JSON.parse(json);
    expect(parsed.panels).toHaveLength(2);
    expect(parsed.panels[0].id).toBe("alpha_mine");
    expect(parsed.panels[1].id).toBe("factors");
    expect(parsed.id).toBe("test-1");
    expect(parsed.name).toBe("测试布局");
    expect(parsed.columns).toBe(2);
  });
});

describe("P8 导入 importLayoutFromJSON", () => {
  it("#3 合法 v2 导入 → ok(无冲突时 id 保留)", () => {
    const json = exportLayoutToJSON(sampleLayout);
    const result = importLayoutFromJSON(json);
    expect(result.ok).not.toBeNull();
    expect(result.ok?.id).toBe("test-1"); // 无冲突,保留原 id
    expect(result.ok?.name).toBe("测试布局");
    expect(result.ok?.panels).toHaveLength(2);
    expect(result.ok?.schemaVersion).toBe(WORKSPACE_LAYOUT_SCHEMA_VERSION);
  });

  it("#4 非法 JSON → error", () => {
    const result = importLayoutFromJSON("not a json {");
    expect(result.ok).toBeNull();
    expect(result.error).toContain("JSON");
  });

  it("#5 schemaVersion=99 → error(仅支持 1/2)", () => {
    const json = JSON.stringify({ ...sampleLayout, schemaVersion: 99 });
    const result = importLayoutFromJSON(json);
    expect(result.ok).toBeNull();
    expect(result.error).toContain("schemaVersion");
  });

  it("#6 未知 panel.id → error", () => {
    const json = JSON.stringify({
      ...sampleLayout,
      panels: [{ id: "unknown_panel" }],
    });
    const result = importLayoutFromJSON(json);
    expect(result.ok).toBeNull();
    expect(result.error).toContain("PANEL_REGISTRY");
  });

  it("#7 props 白名单 strip — 只保留 symbol/source,其他字段剔除", () => {
    const json = JSON.stringify({
      ...sampleLayout,
      panels: [
        {
          id: "watch_vchart",
          props: {
            symbol: "BTCUSDT", // 允许
            source: "binance", // 允许
            evilScript: "alert(1)", // 应剔除
            extraField: "should-be-stripped", // 应剔除
          },
        },
      ],
    });
    const result = importLayoutFromJSON(json);
    expect(result.ok).not.toBeNull();
    const props = result.ok?.panels[0].props;
    expect(props).toBeDefined();
    expect(props?.symbol).toBe("BTCUSDT");
    expect(props?.source).toBe("binance");
    expect(props?.evilScript).toBeUndefined();
    expect(props?.extraField).toBeUndefined();
  });

  it("#8 id 冲突重命名 — id 加 -imported-,name 加 (导入)", () => {
    // 先存一个 test-1
    saveLayout(sampleLayout);
    // 再导入同 id 的布局,应触发冲突重命名
    const json = exportLayoutToJSON(sampleLayout);
    const result = importLayoutFromJSON(json);
    expect(result.ok).not.toBeNull();
    expect(result.ok?.id).toMatch(/^test-1-imported-/);
    expect(result.ok?.name).toBe("测试布局 (导入)");
  });

  it("#9 v1 向后兼容 — 接受 v1 数据,re-tag 为 v2", () => {
    const v1Json = JSON.stringify({
      id: "v1-layout",
      name: "v1 布局",
      panels: [{ id: "alpha_mine" }],
      columns: 2,
      schemaVersion: 1, // v1
      createdAt: "2026-08-01T00:00:00Z",
      updatedAt: "2026-08-01T00:00:00Z",
    });
    const result = importLayoutFromJSON(v1Json);
    expect(result.ok).not.toBeNull();
    expect(result.ok?.id).toBe("v1-layout");
    expect(result.ok?.schemaVersion).toBe(WORKSPACE_LAYOUT_SCHEMA_VERSION); // re-tag 为 v2
  });

  it("#10 columns 越界 → 回退默认 2", () => {
    const json = JSON.stringify({ ...sampleLayout, columns: 99 });
    const result = importLayoutFromJSON(json);
    expect(result.ok).not.toBeNull();
    expect(result.ok?.columns).toBe(2); // 越界回退
  });

  it("#11 import 同一文件 3 次产生 3 个独立布局(revision #4)", () => {
    // 先存一个 test-1(触发冲突路径)
    saveLayout(sampleLayout);
    const json = exportLayoutToJSON(sampleLayout);
    const ids: string[] = [];
    for (let i = 0; i < 3; i++) {
      const r = importLayoutFromJSON(json);
      expect(r.ok).not.toBeNull();
      saveLayout(r.ok!);
      ids.push(r.ok!.id);
    }
    // 3 个不同的 id
    expect(new Set(ids).size).toBe(3);
    // 全部带 -imported- 前缀
    ids.forEach((id) => expect(id).toMatch(/^test-1-imported-/));
    // 原布局 test-1 仍在,panels 数量不变
    const original = loadLayout("test-1");
    expect(original).not.toBeNull();
    expect(original!.panels).toHaveLength(2);
    expect(original!.name).toBe("测试布局"); // 原名不带 (导入)
  });

  it("#12 prototype 不被污染(revision #5 + #8 Object.create(null))", () => {
    // 手工构造恶意 JSON 字符串(不用 JSON.stringify — 它会丢 __proto__)
    const malicious = `{
      "id": "evil",
      "name": "恶意",
      "schemaVersion": 2,
      "panels": [
        {
          "id": "watch_vchart",
          "props": {
            "__proto__": { "polluted": "yes" },
            "constructor": { "prototype": { "polluted2": "yes" } }
          }
        }
      ]
    }`;
    const result = importLayoutFromJSON(malicious);
    expect(result.ok).not.toBeNull();
    // 原型链未被污染
    expect(({} as any).polluted).toBeUndefined();
    expect(({} as any).polluted2).toBeUndefined();
    expect(({} as any).toString).toBe(Object.prototype.toString);
  });

  it("#13 空 panels 拒绝(revision #9)", () => {
    const json = JSON.stringify({ ...sampleLayout, panels: [] });
    const result = importLayoutFromJSON(json);
    expect(result.ok).toBeNull();
    expect(result.error).toContain("至少一个面板");
  });

  it("#14 冲突导入后原布局 bytes 不变(revision #10)", () => {
    // 存 sampleLayout
    saveLayout(sampleLayout);
    const before = loadLayout("test-1");
    expect(before).not.toBeNull();
    // 导入同 id 的布局(触发冲突重命名,不应覆盖原布局)
    const json = exportLayoutToJSON(sampleLayout);
    const result = importLayoutFromJSON(json);
    expect(result.ok).not.toBeNull();
    saveLayout(result.ok!);
    // 验证原布局未变
    const after = loadLayout("test-1");
    expect(after?.panels).toEqual(before?.panels);
    expect(after?.columns).toBe(before?.columns);
    expect(after?.createdAt).toBe(before?.createdAt); // createdAt 保留
    expect(after?.name).toBe(before?.name);
  });

  it("#15 MAX_COLUMNS = 3 常量导出(revision #7)", () => {
    expect(MAX_COLUMNS).toBe(3);
  });
});
