// ICT 叠加层的**显示参数** —— 每个开关管"画不画",这里管"画成什么样"。
//
// 为什么单独成文件、单独 key:
//   - `tools.ictSwings` 这类布尔是页面级临时态(与 其他指标开关 同寿命);
//     这里是**用户的使用习惯**(像磁吸档位):换标的、重开应用都该保持。
//   - 与 `selenespace-fib-drawings`(对象存档)、`selenespace-drawing-prefs`
//     (绘图手感) 三者互不干扰,坏掉一边不拖垮另一边。
//
// 参数合法性以**后端为准**(chart.py 的 Query 约束),这里只是镜像:
// 越界的值在 parse 时收敛到默认,别让一个手改的 localStorage 把整条叠加层打成 422。

export interface IctSwingSettings {
  /** 图元上限。超出按「越新越重要」截断(见 buildSwingMarkers)。 */
  maxCount: number;
  /** 三档递归标记各自是否画。默认只划线级(st 是噪声)。 */
  tiers: { st: boolean; it: boolean; lt: boolean };
  /** 只画某一侧的结构点。 */
  side: "both" | "high" | "low";
}

export interface IctEventSettings {
  /** 破位判定:收盘穿破 / 影线穿破。 */
  breakOn: "close" | "wick";
  maxCount: number;
  kinds: { BOS: boolean; MSS: boolean };
  side: "both" | "up" | "down";
}

export interface IctKillzoneSettings {
  /** 后端 KILLZONES 的键。空集不合法(后端会 400),parse 时兜回默认。 */
  sessions: { london: boolean; new_york: boolean; asia: boolean };
  /** 把帧时钟平移到时段本地时。ICT 惯例是纽约本地 = -5。 */
  tzOffsetHours: number;
  /** 底带不透明度,0.05–0.4。只影响渲染,不影响检测。 */
  opacity: number;
}

export interface IctDealingRangeSettings {
  /** swing 半径,与后端 pd_period 同步。 */
  period: number;
  premiumThreshold: number;
  discountThreshold: number;
  /** 三条分位线各自是否画。 */
  lines: { premium: boolean; equilibrium: boolean; discount: boolean };
}

export interface IctDisplaySettings {
  swings: IctSwingSettings;
  events: IctEventSettings;
  killzone: IctKillzoneSettings;
  dealingRange: IctDealingRangeSettings;
}

export const DEFAULT_ICT_DISPLAY: IctDisplaySettings = {
  swings: { maxCount: 40, tiers: { st: false, it: true, lt: true }, side: "both" },
  events: { breakOn: "close", maxCount: 12, kinds: { BOS: true, MSS: true }, side: "both" },
  killzone: {
    sessions: { london: true, new_york: true, asia: false },
    tzOffsetHours: -5,
    opacity: 0.13,
  },
  dealingRange: {
    period: 10,
    premiumThreshold: 0.6667,
    discountThreshold: 0.3333,
    lines: { premium: true, equilibrium: true, discount: true },
  },
};

/** 单独的 localStorage key。与绘图对象、绘图偏好三者互不干扰。 */
export const ICT_DISPLAY_KEY = "selenespace-ict-display";

// ---------------------------------------------------------------------------
// 解析:坏一个字段只回退那一个,不整包回默认
// ---------------------------------------------------------------------------

const clampInt = (v: unknown, lo: number, hi: number, fallback: number): number => {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(hi, Math.max(lo, Math.round(n)));
};

const clampFloat = (v: unknown, lo: number, hi: number, fallback: number): number => {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(hi, Math.max(lo, n));
};

const bool = (v: unknown, fallback: boolean): boolean =>
  typeof v === "boolean" ? v : fallback;

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object";
}

function parseSwingSettings(raw: unknown): IctSwingSettings {
  const d = DEFAULT_ICT_DISPLAY.swings;
  if (!isRecord(raw)) return { ...d, tiers: { ...d.tiers } };
  const tiers = isRecord(raw.tiers) ? raw.tiers : {};
  const side = raw.side === "high" || raw.side === "low" ? raw.side : "both";
  return {
    maxCount: clampInt(raw.maxCount, 5, 200, d.maxCount),
    tiers: {
      st: bool(tiers.st, d.tiers.st),
      it: bool(tiers.it, d.tiers.it),
      lt: bool(tiers.lt, d.tiers.lt),
    },
    side,
  };
}

function parseEventSettings(raw: unknown): IctEventSettings {
  const d = DEFAULT_ICT_DISPLAY.events;
  if (!isRecord(raw)) return { ...d, kinds: { ...d.kinds } };
  const kinds = isRecord(raw.kinds) ? raw.kinds : {};
  const side = raw.side === "up" || raw.side === "down" ? raw.side : "both";
  return {
    breakOn: raw.breakOn === "wick" ? "wick" : "close",
    maxCount: clampInt(raw.maxCount, 1, 80, d.maxCount),
    kinds: { BOS: bool(kinds.BOS, d.kinds.BOS), MSS: bool(kinds.MSS, d.kinds.MSS) },
    side,
  };
}

function parseKillzoneSettings(raw: unknown): IctKillzoneSettings {
  const d = DEFAULT_ICT_DISPLAY.killzone;
  if (!isRecord(raw)) return { ...d, sessions: { ...d.sessions } };
  const sessions = isRecord(raw.sessions) ? raw.sessions : {};
  const parsed: IctKillzoneSettings = {
    sessions: {
      london: bool(sessions.london, d.sessions.london),
      new_york: bool(sessions.new_york, d.sessions.new_york),
      asia: bool(sessions.asia, d.sessions.asia),
    },
    tzOffsetHours: clampInt(raw.tzOffsetHours, -12, 14, d.tzOffsetHours),
    opacity: clampFloat(raw.opacity, 0.05, 0.4, d.opacity),
  };
  // 一个时段都不开会让后端 400(它把空集视为调用方错误)。这里兜回默认,
  // 与"用户把三个勾都取消"的意图冲突时,宁可显示默认伦敦+纽约也不要整层消失。
  if (!parsed.sessions.london && !parsed.sessions.new_york && !parsed.sessions.asia) {
    parsed.sessions = { ...d.sessions };
  }
  return parsed;
}

function parseDealingRangeSettings(raw: unknown): IctDealingRangeSettings {
  const d = DEFAULT_ICT_DISPLAY.dealingRange;
  if (!isRecord(raw)) return { ...d, lines: { ...d.lines } };
  const lines = isRecord(raw.lines) ? raw.lines : {};
  const premium = clampFloat(raw.premiumThreshold, 0.01, 0.99, d.premiumThreshold);
  const discount = clampFloat(raw.discountThreshold, 0.01, 0.99, d.discountThreshold);
  // 后端要求 premium > discount 才有意义;顺序反了就把两者对调而不是丢配置。
  const [lo, hi] = discount <= premium ? [discount, premium] : [premium, discount];
  return {
    period: clampInt(raw.period, 2, 100, d.period),
    premiumThreshold: hi,
    discountThreshold: lo,
    lines: {
      premium: bool(lines.premium, d.lines.premium),
      equilibrium: bool(lines.equilibrium, d.lines.equilibrium),
      discount: bool(lines.discount, d.lines.discount),
    },
  };
}

export function parseIctDisplay(raw: unknown): IctDisplaySettings {
  if (!isRecord(raw)) {
    return {
      swings: { ...DEFAULT_ICT_DISPLAY.swings, tiers: { ...DEFAULT_ICT_DISPLAY.swings.tiers } },
      events: { ...DEFAULT_ICT_DISPLAY.events, kinds: { ...DEFAULT_ICT_DISPLAY.events.kinds } },
      killzone: {
        ...DEFAULT_ICT_DISPLAY.killzone,
        sessions: { ...DEFAULT_ICT_DISPLAY.killzone.sessions },
      },
      dealingRange: {
        ...DEFAULT_ICT_DISPLAY.dealingRange,
        lines: { ...DEFAULT_ICT_DISPLAY.dealingRange.lines },
      },
    };
  }
  return {
    swings: parseSwingSettings(raw.swings),
    events: parseEventSettings(raw.events),
    killzone: parseKillzoneSettings(raw.killzone),
    dealingRange: parseDealingRangeSettings(raw.dealingRange),
  };
}

export function parseIctDisplayJson(text: string | null): IctDisplaySettings {
  if (!text) return parseIctDisplay(null);
  try {
    return parseIctDisplay(JSON.parse(text));
  } catch {
    return parseIctDisplay(null);
  }
}

export function serializeIctDisplay(s: IctDisplaySettings): string {
  return JSON.stringify(s);
}

/** 时段集合 → 后端接受的逗号串;保持稳定顺序(伦敦→纽约→亚洲),让 SWR key 稳定。 */
export function sessionsParam(s: IctKillzoneSettings["sessions"]): string {
  const out: string[] = [];
  if (s.london) out.push("london");
  if (s.new_york) out.push("new_york");
  if (s.asia) out.push("asia");
  return out.join(",") || "london,new_york";
}
