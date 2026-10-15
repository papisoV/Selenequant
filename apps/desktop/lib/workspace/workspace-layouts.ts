/**
 * Workspace 布局持久化 — localStorage CRUD
 *
 * Schema v2: PanelSlot 加 w/h 可选(resize 后存像素值,渲染时转 grid-column span N)
 * Schema v1 → v2 迁移通过独立 ensureMigrated() 函数(不在 readStorage 内,避免死循环)
 * 不污染 zustand 内存态(useWorkflowStore),独立 localStorage 层
 */

export const WORKSPACE_LAYOUT_STORAGE_KEY = "selenespace:workspace:layouts:v1";
export const WORKSPACE_LAYOUT_SCHEMA_VERSION = 2;

/** grid track 最小宽度(px)— JS 计算 span N 用,CSS minmax(480px, 1fr) 引用同值 */
export const GRID_TRACK_MIN = 480;

/** 布局最大列数(导入校验 + UI 共用) */
export const MAX_COLUMNS = 3;

/**
 * 合法 PanelId 集合 — 与 PanelId union (L18-26) 同源
 * 注:不从 panel-registry.tsx 派生(那里反向 import 本文件的 type,会成 runtime cycle)
 * PanelId union 是单一真相,改 union 必须同步改这里(tsc 会强校验)
 */
const VALID_PANEL_IDS: Set<PanelId> = new Set<PanelId>([
  "alpha_mine",
  "factors",
  "watch_vchart",
  "watch_gex",
  "watch_liqmap",
  "watch_sig",
  "watch_kf",
  "watch_ob",
  "order_form",
  "onchain_whale",
  "onchain_sm",
  "alert_rules",
]);

/** 记住上次访问的布局 id(独立 key,不污染 layouts 列表 schema) */
export const WORKSPACE_LAST_USED_LAYOUT_KEY = "selenespace:workspace:last-used-layout:v1";

export type PanelId =
  | "alpha_mine"
  | "factors"
  | "watch_vchart"
  | "watch_gex"
  | "watch_liqmap"
  | "watch_sig"
  | "watch_kf"
  | "watch_ob"
  | "order_form"
  | "onchain_whale"
  | "onchain_sm"
  | "alert_rules";

export interface PanelSlot {
  id: PanelId;
  /** 传入 panel 的 props(如 symbol、source 等) */
  props?: Record<string, unknown>;
  /** panel 宽度(px)— resize 后存,渲染时转 grid-column span N */
  w?: number;
  /** panel 高度(px)— resize 后存,渲染时设为 minHeight */
  h?: number;
}

export interface WorkspaceLayout {
  id: string;
  name: string;
  panels: PanelSlot[];
  /** 列数(1-3),默认 2 */
  columns?: number;
  schemaVersion: number;
  createdAt: string;
  updatedAt: string;
}

interface StoredLayouts {
  layouts: WorkspaceLayout[];
}

function readStorage(): StoredLayouts {
  if (typeof window === "undefined") return { layouts: [] };
  try {
    const raw = window.localStorage.getItem(WORKSPACE_LAYOUT_STORAGE_KEY);
    if (!raw) return { layouts: [] };
    const parsed = JSON.parse(raw) as StoredLayouts;
    if (!Array.isArray(parsed.layouts)) return { layouts: [] };
    return parsed;
  } catch {
    return { layouts: [] };
  }
}

function writeStorage(data: StoredLayouts): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      WORKSPACE_LAYOUT_STORAGE_KEY,
      JSON.stringify(data),
    );
  } catch (err) {
    console.error("workspace-layouts: writeStorage failed", err);
  }
}

/**
 * v1 → v2 迁移:noop 函数(w/h 可选,v1 数据读出来 w/h = undefined)
 * 导出供单测调用,实际迁移逻辑在 ensureMigrated 内
 */
export function migrateLayoutsV1ToV2(stored: StoredLayouts): StoredLayouts {
  return {
    layouts: stored.layouts.map((l) => ({
      ...l,
      schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
      panels: l.panels.map((p) => ({ ...p })),
    })),
  };
}

/**
 * 确保存储数据已迁移到当前 schema 版本
 * 独立函数(不在 readStorage 内),用 writeStorage 直接写回,不调 saveLayout(避免死循环)
 * 在 listLayouts/loadLayout 入口调用,迁移只跑一次(第二次读 schemaVersion 已是 2 跳过)
 */
export function ensureMigrated(): void {
  if (typeof window === "undefined") return;
  const stored = readStorage();
  const needsMigration = stored.layouts.some(
    (l) => l.schemaVersion === undefined || l.schemaVersion < WORKSPACE_LAYOUT_SCHEMA_VERSION,
  );
  if (!needsMigration) return;
  const migrated = migrateLayoutsV1ToV2(stored);
  writeStorage(migrated);
}

export function listLayouts(): WorkspaceLayout[] {
  ensureMigrated();
  return readStorage().layouts;
}

export function loadLayout(id: string): WorkspaceLayout | null {
  ensureMigrated();
  return readStorage().layouts.find((l) => l.id === id) ?? null;
}

/** 读上次访问的布局 id(无则返回 null,/workspace 首页跳转用) */
export function getLastUsedLayoutId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(WORKSPACE_LAST_USED_LAYOUT_KEY);
  } catch {
    return null;
  }
}

/** 写上次访问的布局 id(/workspace/[layoutId] 进入时调用) */
export function setLastUsedLayoutId(id: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(WORKSPACE_LAST_USED_LAYOUT_KEY, id);
  } catch (err) {
    console.error("workspace-layouts: setLastUsedLayoutId failed", err);
  }
}

export function saveLayout(layout: WorkspaceLayout): WorkspaceLayout {
  const stored = readStorage();
  const now = new Date().toISOString();
  const idx = stored.layouts.findIndex((l) => l.id === layout.id);
  const next: WorkspaceLayout = {
    ...layout,
    schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
    updatedAt: now,
    createdAt: idx >= 0 ? stored.layouts[idx].createdAt : now,
  };
  if (idx >= 0) {
    stored.layouts[idx] = next;
  } else {
    stored.layouts.push(next);
  }
  writeStorage(stored);
  return next;
}

export function deleteLayout(id: string): void {
  const stored = readStorage();
  stored.layouts = stored.layouts.filter((l) => l.id !== id);
  writeStorage(stored);
}

export function renameLayout(id: string, name: string): WorkspaceLayout | null {
  const stored = readStorage();
  const idx = stored.layouts.findIndex((l) => l.id === id);
  if (idx < 0) return null;
  stored.layouts[idx] = {
    ...stored.layouts[idx],
    name,
    updatedAt: new Date().toISOString(),
  };
  writeStorage(stored);
  return stored.layouts[idx];
}

export function duplicateLayout(
  sourceId: string,
  newId: string,
  newName: string,
): WorkspaceLayout | null {
  const src = loadLayout(sourceId);
  if (!src) return null;
  return saveLayout({
    id: newId,
    name: newName,
    panels: src.panels,
    columns: src.columns,
    schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  });
}

/** 生成唯一 id(基于时间戳 + 随机后缀) */
export function generateLayoutId(): string {
  return `ws-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** 4 个预设布局(signal/research/trade-watch/onchain) */
export function getPresetLayouts(): WorkspaceLayout[] {
  const now = new Date().toISOString();
  return [
    {
      id: "signal",
      name: "信号 → 下单",
      panels: [
        { id: "watch_vchart" },
        { id: "order_form" },
      ],
      columns: 2,
      schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
      createdAt: now,
      updatedAt: now,
    },
    {
      id: "research",
      name: "研究双栏",
      panels: [
        { id: "alpha_mine" },
        { id: "factors" },
      ],
      columns: 2,
      schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
      createdAt: now,
      updatedAt: now,
    },
    {
      id: "trade-watch",
      name: "盯盘 + 盘口 + 下单",
      panels: [
        { id: "watch_kf" },
        { id: "watch_ob" },
        { id: "order_form" },
      ],
      columns: 3,
      schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
      createdAt: now,
      updatedAt: now,
    },
    {
      id: "onchain",
      name: "链上情报",
      panels: [
        { id: "onchain_whale" },
        { id: "onchain_sm" },
      ],
      columns: 2,
      schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
      createdAt: now,
      updatedAt: now,
    },
  ];
}

/** 首次访问 /workspace 时初始化预设布局 */
export function ensurePresetsSeeded(): void {
  const stored = readStorage();
  if (stored.layouts.length > 0) return;
  const presets = getPresetLayouts();
  writeStorage({ layouts: presets });
}

/** 导入校验白名单:仅这 2 字段是 8 个 panel 共用 props(symbol=标的,source=数据源) */
const ALLOWED_PANEL_PROPS = new Set(["symbol", "source"]);

/** 导出布局为 JSON 字符串(含 schemaVersion + exportedAt,无 appVersion)
 *  导出 payload:布局字段 + schemaVersion(强盖 v2)+ exportedAt(ISO 时间戳)
 *  导入端会校验 schemaVersion + 白名单字段,exportedAt 不参与校验 */
export function exportLayoutToJSON(layout: WorkspaceLayout): string {
  const payload = {
    ...layout,
    schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
    exportedAt: new Date().toISOString(),
  };
  return JSON.stringify(payload, null, 2);
}

/** 导入布局 JSON:校验 + 清洗 + 冲突重命名
 *  返回 { ok: WorkspaceLayout } 或 { ok: null, error }
 *  不调 saveLayout(由调用方决定持久化),仅返回校验后的 layout 对象 */
export function importLayoutFromJSON(json: string): {
  ok: WorkspaceLayout | null;
  error?: string;
} {
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    return { ok: null, error: "JSON 解析失败,文件格式错误" };
  }

  if (typeof parsed !== "object" || parsed === null) {
    return { ok: null, error: "根节点不是对象" };
  }
  const obj = parsed as Record<string, unknown>;

  // schemaVersion: v1/v2 接受(v1 结构兼容 v2 — per migrateLayoutsV1ToV2 L82-90 是 noop spread,
  // v1 数据读出来 w/h=undefined,只需 re-tag 为 v2),其他拒
  const sv = obj.schemaVersion;
  if (sv !== 1 && sv !== 2) {
    return { ok: null, error: `不支持的 schemaVersion: ${sv}(仅支持 1/2)` };
  }

  // 必填字段校验
  if (typeof obj.id !== "string" || !obj.id.trim()) {
    return { ok: null, error: "id 缺失或非字符串" };
  }
  if (typeof obj.name !== "string" || !obj.name.trim()) {
    return { ok: null, error: "name 缺失或非字符串" };
  }
  if (!Array.isArray(obj.panels)) {
    return { ok: null, error: "panels 缺失或非数组" };
  }
  // 空 panels 拒绝(避免 UI 渲染空布局崩)
  if ((obj.panels as unknown[]).length === 0) {
    return { ok: null, error: "布局必须包含至少一个面板" };
  }

  const cleanedPanels: PanelSlot[] = [];
  for (let i = 0; i < obj.panels.length; i++) {
    const p = obj.panels[i] as Record<string, unknown> | null;
    if (typeof p?.id !== "string" || !VALID_PANEL_IDS.has(p.id as PanelId)) {
      return {
        ok: null,
        error: `panels[${i}].id "${p?.id}" 不在 PANEL_REGISTRY`,
      };
    }
    const slot: PanelSlot = { id: p.id as PanelId };
    if (p.props && typeof p.props === "object") {
      // Object.create(null) 防 prototype pollution(即使白名单漏放 __proto__,也不污染原型链)
      const safeProps: Record<string, unknown> = Object.create(null);
      for (const [k, v] of Object.entries(
        p.props as Record<string, unknown>,
      )) {
        if (ALLOWED_PANEL_PROPS.has(k)) safeProps[k] = v;
      }
      const keys = Object.keys(safeProps);
      if (keys.length > 0) slot.props = safeProps;
    }
    if (typeof p.w === "number" && p.w > 0) slot.w = p.w;
    if (typeof p.h === "number" && p.h > 0) slot.h = p.h;
    cleanedPanels.push(slot);
  }

  // columns 可选(1-MAX_COLUMNS,引用常量)
  let columns = 2;
  if (
    typeof obj.columns === "number" &&
    obj.columns >= 1 &&
    obj.columns <= MAX_COLUMNS
  ) {
    columns = obj.columns;
  }

  // 冲突重命名:id 已存在则生成新 id + 名称加 (导入)(非破坏,用户可手动删重复)
  // 注:加随机后缀避免同毫秒内多次导入产生相同 id(Date.now() 精度不足)
  const existing = loadLayout(obj.id);
  const finalId = existing
    ? `${obj.id}-imported-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
    : obj.id;
  const finalName = existing ? `${obj.name} (导入)` : obj.name;

  const now = new Date().toISOString();
  return {
    ok: {
      id: finalId,
      name: finalName,
      panels: cleanedPanels,
      columns,
      schemaVersion: WORKSPACE_LAYOUT_SCHEMA_VERSION,
      createdAt: now,
      updatedAt: now,
    },
  };
}
