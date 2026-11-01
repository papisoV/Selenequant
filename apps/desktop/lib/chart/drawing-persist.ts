// 绘图对象的持久化边界 —— 纯函数(零 React / 零浏览器 API)。
//
// localStorage 里躺的是**不受信数据**:用户手改、旧版本写入、别的标签页写坏,
// 都可能塞进来 NaN / 缺字段 / 非数组。这一层负责把任何输入收敛成合法的
// FibGrid[],而不是让坏数据一路流到画布上变成"一条走向无穷的线"。
//
// 校验策略刻意保守:单个对象坏了就丢这一个,不整包丢弃 —— 用户画了 20 条,
// 不该因为其中一条的时间戳是字符串就全没了。

import { type FibGrid, type FibGridMode, hasLevels, type LevelStyleEntry, normalizeLevels } from "@/lib/chart/fib-object";
import { clampLineWidth } from "@/lib/chart/fib-style";
import { HAND_DRAWN_LEVELS } from "@/lib/chart/fib-levels";
import { defaultTimeLevels } from "@/lib/chart/fib-time-levels";

/**
 * 持久化格式版本。将来改结构时用它做迁移。
 *
 * v2:FibGrid 增加 extendLeft/extendRight(档位线的横向延伸开关)。
 * v3:FibGrid 增加 mode(price / time)。缺省按 "price" 解析 ——
 *     v1/v2 存档全部是价格网格,无需迁移函数。
 * v4:mode 扩到六种(另加 trend/horizontal/vertical/rect)。基础图形
 *     `levels` 恒为空,旧存档里不会出现这四种值,同样无需迁移。
 * 解析端对缺字段一律取默认值,所以旧数据只需能被读入,版本号只用来让写出的格式可辨识。
 */
export const DRAWINGS_SCHEMA_VERSION = 4;

export interface PersistedDrawings {
  version: number;
  grids: FibGrid[];
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function bool(v: unknown, fallback: boolean): boolean {
  return typeof v === "boolean" ? v : fallback;
}

function parseAnchor(raw: unknown): { time: number; price: number } | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  if (!isFiniteNumber(o.time) || !isFiniteNumber(o.price)) return null;
  return { time: o.time, price: o.price };
}

const MODES: readonly FibGridMode[] = ["price", "time", "trend", "horizontal", "vertical", "rect"];

/**
 * 模式解析。未知值一律按价格网格处理 —— 那是唯一在 mode 字段出现之前
 * 就存在的形态,也是"绘图"这个词在旧版里的全部含义。
 */
function parseMode(raw: unknown): FibGridMode {
  return MODES.find((m) => m === raw) ?? "price";
}

/** `#rgb` / `#rrggbb` / `#rrggbbaa` —— canvas 只认这几种,别的形状直接丢。 */
const HEX_COLOR = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;

/**
 * 逐档样式覆盖解析。坏项**单独丢弃**,不影响同一条网格的其它档位 ——
 * 与"单条坏对象不整包丢"同一个策略。
 */
function parseLevelStyles(raw: unknown): LevelStyleEntry[] {
  if (!Array.isArray(raw)) return [];
  const out: LevelStyleEntry[] = [];
  const seen = new Set<number>();
  for (const item of raw as unknown[]) {
    if (!item || typeof item !== "object") continue;
    const o = item as Record<string, unknown>;
    if (!isFiniteNumber(o.coeff) || seen.has(o.coeff)) continue;
    seen.add(o.coeff);

    const entry: LevelStyleEntry = { coeff: o.coeff };
    if (typeof o.color === "string" && HEX_COLOR.test(o.color)) entry.color = o.color;
    if (isFiniteNumber(o.width)) entry.width = clampLineWidth(o.width);
    if (typeof o.dashed === "boolean") entry.dashed = o.dashed;
    if (typeof o.text === "string") entry.text = o.text.slice(0, 24);
    out.push(entry);
  }
  return out;
}

/**
 * 单个对象解析。无法修复的返回 null。
 *
 * `levels` 缺省时,斐波那契家族回落到各自的档位族(而不是空数组)—— 一条
 * "没有档位"的斐波那契网格对用户毫无意义,宁可给它默认档位让用户自己删。
 * 基础图形(趋势线 / 水平线 / 竖线 / 矩形)本来就没有档位,缺省即空。
 */
export function parseGrid(raw: unknown): FibGrid | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;

  const id = str(o.id).trim();
  if (!id) return null;

  const start = parseAnchor(o.start);
  const end = parseAnchor(o.end);
  if (!start || !end) return null;

  // 模式决定 levels 的语义,必须先于 levels 解析。未知值一律按价格网格处理 ——
  // 那是唯一在 mode 字段出现之前就存在的形态。
  const mode = parseMode(o.mode);

  const rawLevels = Array.isArray(o.levels) ? (o.levels as unknown[]) : null;
  const numericLevels = (rawLevels ?? []).filter(isFiniteNumber);
  // 三种"没有可用档位"的情况,意图完全不同,不能一把抓成回落:
  //   - 字段不存在 / 不是数组  → 旧存档或坏壳,给该模式的默认族;
  //   - 是数组但**空数组**      → 用户把这一档一档删光了(对象树支持增删),
  //                              必须尊重,否则重开应用档位会自己长回来;
  //   - 是数组、非空、却没有一个数是数字 → 数据坏了,回落默认族救人。
  // 基础图形(趋势线 / 水平线 / 竖线 / 矩形)本来就没有档位,缺省即空。
  const explicitlyEmpty = rawLevels !== null && rawLevels.length === 0;
  const sourceLevels = numericLevels.length > 0
    ? numericLevels
    : explicitlyEmpty || !hasLevels(mode)
      ? []
      : mode === "time"
        ? defaultTimeLevels()
        : HAND_DRAWN_LEVELS;
  const levels = normalizeLevels(sourceLevels);

  const groupId = typeof o.groupId === "string" && o.groupId.trim() ? o.groupId : null;
  return {
    id,
    groupId,
    start,
    end,
    mode,
    levels,
    autoExtend: bool(o.autoExtend, false),
    extendLeft: bool(o.extendLeft, false),
    extendRight: bool(o.extendRight, false),
    visible: bool(o.visible, true),
    locked: bool(o.locked, false),
    timeframe: str(o.timeframe, "1h"),
    symbol: str(o.symbol),
    label: str(o.label),
    // 只在真有覆盖时才写这个字段:旧测试/旧数据的往返相等性靠"缺省即不存在"维持,
    // 恒定塞一个空数组会让 `parse(serialize(x))` 与 x 不等。
    ...(() => {
      const levelStyles = parseLevelStyles(o.levelStyles);
      return levelStyles.length > 0 ? { levelStyles } : {};
    })(),
  };
}

/** 解析整包。重复 id 只保留第一个 —— 否则选中/拖动会同时作用于两条。 */
export function parseDrawings(raw: unknown): FibGrid[] {
  if (!raw || typeof raw !== "object") return [];
  const o = raw as Record<string, unknown>;
  const list = Array.isArray(o.grids) ? o.grids : Array.isArray(raw) ? (raw as unknown[]) : [];

  const seen = new Set<string>();
  const out: FibGrid[] = [];
  for (const item of list) {
    const grid = parseGrid(item);
    if (!grid) continue;
    if (seen.has(grid.id)) continue;
    seen.add(grid.id);
    out.push(grid);
  }
  return out;
}

/** 序列化。写盘的永远是当前 schema 版本。 */
export function serializeDrawings(grids: readonly FibGrid[]): PersistedDrawings {
  return {
    version: DRAWINGS_SCHEMA_VERSION,
    grids: grids.map((g) => ({
      ...g,
      levels: [...g.levels],
      // 引用拷贝会让 store 后续改样式时绕过 set() 直接改到"已序列化的快照",
      // 这里与 levels 同样深拷一层。
      ...(g.levelStyles ? { levelStyles: g.levelStyles.map((s) => ({ ...s })) } : {}),
    })),
  };
}

/** 从 JSON 字符串解析;任何异常都收敛为空数组,不抛。 */
export function parseDrawingsJson(text: string | null): FibGrid[] {
  if (!text) return [];
  try {
    return parseDrawings(JSON.parse(text));
  } catch {
    return [];
  }
}
