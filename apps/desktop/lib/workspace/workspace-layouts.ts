/**
 * Workspace 布局持久化 — localStorage CRUD
 *
 * Schema v2: PanelSlot 加 w/h 可选(resize 后存像素值,渲染时转 grid-column span N)
 * Schema v1 → v2 迁移通过独立 ensureMigrated() 函数(不在 readStorage 内,避免死循环)
 * 不污染 zustand 内存态(useWorkflowStore),独立 localStorage 层
 */

export const WORKSPACE_LAYOUT_STORAGE_KEY = "selenespace:workspace:layouts:v1";
export const WORKSPACE_LAYOUT_SCHEMA_VERSION = 2;

/** 删除墓碑:{layoutId → 删除时刻 ISO}。
 *  跨设备同步必须有它:否则一台机器删掉的布局会被另一台机器传回来。
 *  与 layouts 分开存,因为它的生命周期与布局集合不同(删掉的 id 不再有布局)。 */
export const WORKSPACE_LAYOUT_TOMBSTONES_KEY =
  "selenespace:workspace:layout-tombstones:v1";

/** 墓碑上限 —— 与 settings_service 的同名上限对齐,防止本地无界增长 */
export const MAX_LAYOUT_TOMBSTONES = 200;

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

/** 存储读取带状态 — 区分「用户主动删空」和「键不存在/内容损坏」,二者对预设补种含义相反 */
type StorageState = "ok" | "absent" | "corrupt";

function probeStorage(): { layouts: WorkspaceLayout[]; state: StorageState } {
  if (typeof window === "undefined") return { layouts: [], state: "absent" };
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(WORKSPACE_LAYOUT_STORAGE_KEY);
  } catch {
    return { layouts: [], state: "corrupt" };
  }
  if (!raw) return { layouts: [], state: "absent" };
  try {
    const parsed = JSON.parse(raw) as StoredLayouts | null;
    if (!parsed || !Array.isArray(parsed.layouts)) {
      return { layouts: [], state: "corrupt" };
    }
    return { layouts: parsed.layouts, state: "ok" };
  } catch {
    return { layouts: [], state: "corrupt" };
  }
}

function readStorage(): StoredLayouts {
  return { layouts: probeStorage().layouts };
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

// -- 变更通知 --------------------------------------------------------------
//
// 同步层要「本地一改就推后端」,但又不能把 fetch 塞进这个纯存储模块
// (单测与 SSR 都不想因此触网)。故只暴露订阅:写入后通知,同步层自己决定去抖。

type LayoutsListener = () => void;
const listeners = new Set<LayoutsListener>();

/** 订阅布局变更;返回退订函数 */
export function subscribeLayouts(listener: LayoutsListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function emitChange(): void {
  for (const listener of listeners) {
    try {
      listener();
    } catch (err) {
      // 一个订阅者抛错不该打断其他订阅者,更不该让写入失败
      console.error("workspace-layouts: listener failed", err);
    }
  }
}

// -- 墓碑 ------------------------------------------------------------------

export type LayoutTombstones = Record<string, string>;

export function readTombstones(): LayoutTombstones {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(WORKSPACE_LAYOUT_TOMBSTONES_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return {};
    }
    const cleaned: LayoutTombstones = {};
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (key && typeof value === "string") cleaned[key] = value;
    }
    return cleaned;
  } catch {
    return {};
  }
}

export function writeTombstones(tombstones: LayoutTombstones): void {
  if (typeof window === "undefined") return;
  try {
    // 只留最近删除的若干条(ISO 字符串可直接排序)
    const entries = Object.entries(tombstones).sort((a, b) =>
      b[1].localeCompare(a[1]),
    );
    window.localStorage.setItem(
      WORKSPACE_LAYOUT_TOMBSTONES_KEY,
      JSON.stringify(Object.fromEntries(entries.slice(0, MAX_LAYOUT_TOMBSTONES))),
    );
  } catch (err) {
    console.error("workspace-layouts: writeTombstones failed", err);
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
  // 重新保存 = 撤销删除,墓碑必须一起清掉,否则同步时会被自己的墓碑删掉
  const tombstones = readTombstones();
  if (layout.id in tombstones) {
    delete tombstones[layout.id];
    writeTombstones(tombstones);
  }
  emitChange();
  return next;
}

export function deleteLayout(id: string): void {
  const stored = readStorage();
  stored.layouts = stored.layouts.filter((l) => l.id !== id);
  writeStorage(stored);
  const tombstones = readTombstones();
  tombstones[id] = new Date().toISOString();
  writeTombstones(tombstones);
  emitChange();
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
  emitChange();
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

/**
 * 预设种子状态 — presetId → 上次由本模块写入的内容签名。
 *
 * 没有它就无法区分「用户编辑过的预设」和「我们种下的预设」,而升级时需要
 * 刷新后者、保留前者。用户在种下之后删除的预设也靠这里的签名判定,不再复活。
 */
const PRESETS_SEED_STATE_KEY = "selenespace:workspace:presets-seeded:v1";

interface PresetsSeedState {
  signatures: Record<string, string>;
}

/** 布局内容签名 — 只含用户可见内容,不含 createdAt/updatedAt/schemaVersion(保存时必然变动) */
type LayoutContent = Pick<WorkspaceLayout, "id" | "name" | "panels" | "columns">;

function layoutSignature(l: LayoutContent): string {
  const panels = l.panels.map((p) => {
    const props = p.props ?? null;
    const propEntries = props
      ? Object.keys(props)
          .sort()
          .map((k) => [k, props[k]])
      : null;
    return [p.id, propEntries, p.w ?? null, p.h ?? null];
  });
  return JSON.stringify([l.id, l.name, l.columns ?? 2, panels]);
}

/**
 * 历史上出现过的预设内容 — 用于识别「用户没动过」的老预设。
 * 只登记与当前目录不同的版本;内容未变过的预设靠「与当前签名相等」自然识别。
 */
const LEGACY_PRESET_SIGNATURES: Record<string, ReadonlySet<string>> = {
  // trade-watch v1(2723fa02 之前):2 栏 2 面板,无 watch_ob
  "trade-watch": new Set([
    layoutSignature({
      id: "trade-watch",
      name: "盯盘 + 下单",
      columns: 2,
      panels: [{ id: "watch_kf" }, { id: "order_form" }],
    }),
  ]),
};

/**
 * 曾经出现在预设目录里的 id — 老存储(尚无种子状态)缺了它们只可能是用户删的,
 * 不是目录新增。onchain 于 2723fa02 才加入,故不在此列。
 */
const EVER_SEEDED_PRESET_IDS = new Set(["signal", "research", "trade-watch"]);

function readSeedState(): PresetsSeedState | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(PRESETS_SEED_STATE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PresetsSeedState> | null;
    if (!parsed || typeof parsed.signatures !== "object" || parsed.signatures === null) {
      return null;
    }
    return { signatures: { ...parsed.signatures } };
  } catch {
    return null;
  }
}

function writeSeedState(state: PresetsSeedState): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PRESETS_SEED_STATE_KEY, JSON.stringify(state));
  } catch (err) {
    console.error("workspace-layouts: writeSeedState failed", err);
  }
}

/**
 * 访问 /workspace 时对齐预设布局(可重复调用,稳态零写入)。
 *
 * 规则:
 * - 全新安装 → 写入全部预设
 * - 用户编辑过的预设 → 原样保留
 * - 用户没动过、但预设目录已更新的预设 → 刷新内容,保留 createdAt
 * - 用户删过的预设 → 不复活
 * - 目录新增的预设 → 补上
 * - 存储里只有用户自建布局(从未种过预设)→ 不注入预设
 */
export function ensurePresetsSeeded(): void {
  if (typeof window === "undefined") return;
  const { layouts: stored, state } = probeStorage();
  const presets = getPresetLayouts();
  const seedState = readSeedState();

  // 存储不可读(键不存在/内容损坏)→ 无论种子状态如何都该补种,否则用户面对空白工作台;
  // 存储完好但为空 = 用户把布局全删了,此时尊重删除(种子状态在就不复活,见下方循环)
  if (state !== "ok" || (stored.length === 0 && seedState === null)) {
    writeStorage({ layouts: presets });
    writeSeedState({
      signatures: Object.fromEntries(presets.map((p) => [p.id, layoutSignature(p)])),
    });
    return;
  }

  const presetIds = new Set(presets.map((p) => p.id));
  // 无种子状态的老存储:只有存过预设的才算「预设玩家」;
  // 只有用户自建布局的存储视为用户自己的布局世界,不注入预设(也不写种子状态)
  const presetStore =
    seedState !== null || stored.some((l) => presetIds.has(l.id));
  if (!presetStore) return;

  const byId = new Map(stored.map((l) => [l.id, l] as const));
  const tombstones = readTombstones();
  const signatures: Record<string, string> = { ...(seedState?.signatures ?? {}) };
  const refreshed = new Map<string, WorkspaceLayout>();
  const appended: WorkspaceLayout[] = [];
  let layoutsChanged = false;
  let signaturesChanged = seedState === null;

  for (const preset of presets) {
    const currentSig = layoutSignature(preset);
    const existing = byId.get(preset.id);

    if (!existing) {
      // 墓碑 = 在任意设备上被删过,不复活(跨设备同步的删除意图)
      if (tombstones[preset.id] !== undefined) continue;
      // 签名里记着 = 用户删过,不复活;没记过 = 目录新增的预设,补上
      if (seedState?.signatures[preset.id] !== undefined) continue;
      // 老存储(无种子状态)缺了曾经种过的预设 = 用户删的。补记签名,否则下次
      // 有了种子状态后会把它当「新增预设」复活
      if (seedState === null && EVER_SEEDED_PRESET_IDS.has(preset.id)) {
        signatures[preset.id] = currentSig;
        continue;
      }
      appended.push(preset);
      signatures[preset.id] = currentSig;
      layoutsChanged = true;
      signaturesChanged = true;
      continue;
    }

    const storedSig = layoutSignature(existing);
    if (storedSig === currentSig) {
      // 已是最新 —— 只补记签名,不动 updatedAt,避免每次访问都写盘
      if (signatures[preset.id] !== currentSig) {
        signatures[preset.id] = currentSig;
        signaturesChanged = true;
      }
      continue;
    }

    const untouched =
      seedState?.signatures[preset.id] === storedSig ||
      LEGACY_PRESET_SIGNATURES[preset.id]?.has(storedSig) === true;
    if (!untouched) continue; // 用户编辑过,保留

    refreshed.set(preset.id, {
      ...preset,
      createdAt: existing.createdAt,
      updatedAt: new Date().toISOString(),
    });
    signatures[preset.id] = currentSig;
    layoutsChanged = true;
    signaturesChanged = true;
  }

  if (layoutsChanged) {
    writeStorage({
      layouts: stored.map((l) => refreshed.get(l.id) ?? l).concat(appended),
    });
    emitChange();
  }
  if (signaturesChanged) writeSeedState({ signatures });
}

// -- 跨设备同步合并 --------------------------------------------------------

export interface LayoutSyncState {
  layouts: WorkspaceLayout[];
  tombstones: LayoutTombstones;
}

/**
 * 合并本地与远端布局集合,返回同步后的权威状态。
 *
 * 纯函数(不碰 localStorage)以便把冲突规则钉死在单测里。规则:
 * 1. 墓碑只压制「比删除更旧的」那份布局。删除是显式意图,不能靠时钟比较来
 *    翻案;但用户删完又重建同一个 id(updatedAt 晚于墓碑)时以重建为准,
 *    否则本地永远存不回这个 id。
 * 2. 同 id 都有 → updatedAt 较新者胜(相等时本地胜,避免无意义改写)。
 * 3. 只有一侧有 → 保留。
 * 4. 已被重建超越的墓碑在结果里清掉,否则会无限累积。
 *
 * 注意 lastUsedLayoutId 不在此列 —— 它记录的是「这台设备上次看哪个」,
 * 跨设备同步反而会让每台机器都跳到别人的页面。
 */
export function mergeLayouts(
  local: LayoutSyncState,
  remote: LayoutSyncState,
): LayoutSyncState {
  const tombstones: LayoutTombstones = { ...remote.tombstones, ...local.tombstones };

  const pick = (a: WorkspaceLayout, b: WorkspaceLayout): WorkspaceLayout =>
    (a.updatedAt ?? "") >= (b.updatedAt ?? "") ? a : b;

  /** 墓碑是否应该压掉这份布局 */
  const buried = (l: WorkspaceLayout): boolean => {
    const deletedAt = tombstones[l.id];
    return deletedAt !== undefined && (l.updatedAt ?? "") <= deletedAt;
  };

  const localById = new Map(local.layouts.map((l) => [l.id, l] as const));
  const merged = new Map<string, WorkspaceLayout>();
  for (const remoteLayout of remote.layouts) {
    if (buried(remoteLayout)) continue;
    const localLayout = localById.get(remoteLayout.id);
    merged.set(
      remoteLayout.id,
      localLayout ? pick(localLayout, remoteLayout) : remoteLayout,
    );
  }
  for (const localLayout of local.layouts) {
    if (buried(localLayout)) continue;
    if (!merged.has(localLayout.id)) merged.set(localLayout.id, localLayout);
  }

  // 只清「已被重建超越」的墓碑。远端墓碑可能对应本机从未有过的 id(别的设备删的),
  // 这些必须保留 —— 否则那条布局下次从第三方设备同步回来会复活。
  const prunedTombstones: LayoutTombstones = {};
  for (const [id, deletedAt] of Object.entries(tombstones)) {
    const live = localById.get(id) ?? remote.layouts.find((l) => l.id === id);
    if (live && (live.updatedAt ?? "") > deletedAt) continue;
    prunedTombstones[id] = deletedAt;
  }

  return { layouts: Array.from(merged.values()), tombstones: prunedTombstones };
}

/** 把合并结果写回 localStorage 并广播(同步层在拉取远端后调用) */
export function applyMergedLayouts(state: LayoutSyncState): void {
  writeStorage({ layouts: state.layouts });
  writeTombstones(state.tombstones);
  emitChange();
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
