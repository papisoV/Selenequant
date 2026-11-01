import { describe, it, expect } from "vitest";
import {
  clampRowsForFreeze,
  closeAtFromRows,
  countStaleBars,
  emaStep,
  pickRenderPath,
  seedMacdState,
  stepMacd,
  tickChangesBar,
  sma,
  computeBoll,
  computeRsi,
  computeKdj,
  computeAtr,
  alertLinesFor,
  drawnLevelPrice,
} from "@/components/watch/kf-chart";
import type { KfRow } from "@/components/watch/kf-chart";

function row(time: number, close = 100): KfRow {
  return { time, open: close, high: close, low: close, close };
}

// 1h bar 序列:00:00 01:00 02:00 03:00 (epoch 秒)
const T0 = 1720000000;
const ROWS = [row(T0, 10), row(T0 + 3600, 20), row(T0 + 7200, 30), row(T0 + 10800, 40)];

describe("clampRowsForFreeze — W3-A 冻结 clamp", () => {
  it("frozenAsOf null/undefined → 原样返回(不冻结)", () => {
    expect(clampRowsForFreeze(ROWS, null)).toBe(ROWS);
    expect(clampRowsForFreeze(ROWS, undefined)).toBe(ROWS);
  });

  it("全部行 time <= frozenAsOf → 原样返回(无新 bar)", () => {
    const asOf = T0 + 10800;
    expect(clampRowsForFreeze(ROWS, asOf)).toBe(ROWS);
  });

  it("有新 bar → 过滤保留更旧行(右侧新 bar 被丢弃)", () => {
    const asOf = T0 + 7200; // 冻结到 02:00,03:00 是新 bar
    expect(clampRowsForFreeze(ROWS, asOf)).toEqual([ROWS[0], ROWS[1], ROWS[2]]);
  });

  it("空 rows → 原样返回(不抛)", () => {
    expect(clampRowsForFreeze([], T0)).toEqual([]);
  });

  it("全部行 time > frozenAsOf → 返回空(冻结瞬间图上无 AI 所见数据)", () => {
    expect(clampRowsForFreeze(ROWS, T0 - 1)).toEqual([]);
  });
});

describe("countStaleBars — W3-B 保鲜差距计数", () => {
  it("staleAsOf null/undefined → 0(徽章不显示)", () => {
    expect(countStaleBars(ROWS, null)).toBe(0);
    expect(countStaleBars(ROWS, undefined)).toBe(0);
  });

  it("无新 bar → 0", () => {
    expect(countStaleBars(ROWS, T0 + 10800)).toBe(0);
    expect(countStaleBars(ROWS, T0 + 99999)).toBe(0);
  });

  it("有新 bar → 尾部连续新 bar 计数", () => {
    expect(countStaleBars(ROWS, T0 + 7200)).toBe(1);
    expect(countStaleBars(ROWS, T0 + 3600)).toBe(2);
    expect(countStaleBars(ROWS, T0 - 1)).toBe(4);
  });

  it("空 rows → 0(不抛)", () => {
    expect(countStaleBars([], T0)).toBe(0);
  });
});

describe("closeAtFromRows — W3-D 活 bar 收盘倒计时基准", () => {
  it("基准取末根(活 bar):末根开盘时刻(秒→ms)+ 周期", () => {
    // ROWS 末根 03:00,1h → 04:00 收盘。基准必须是末根而非更早行 —
    // 若误用索引更早的行(或冻结锚),收盘已成过去 → 倒计时静默不显示。
    expect(closeAtFromRows(ROWS, "1h")).toBe((T0 + 10800) * 1000 + 3_600_000);
  });

  it("单根 rows 也用该根", () => {
    expect(closeAtFromRows([row(T0, 10)], "1h")).toBe(T0 * 1000 + 3_600_000);
  });

  it("空 rows → null(无数据显示,不编造)", () => {
    expect(closeAtFromRows([], "1h")).toBeNull();
  });

  it("PA 面板可选周期 30m 也有基准(防静默消失)", () => {
    // pa-panel SUPPORTED_TFS 含 30m 并经 tfOverride 传进本组件;表里缺 30m 会让
    // PA 在该周期上永远不显示倒计时(组件返回 null,零报错)。
    expect(closeAtFromRows(ROWS, "30m")).toBe((T0 + 10800) * 1000 + 1_800_000);
  });

  it("周期未收录(2h/3d)→ null", () => {
    expect(closeAtFromRows(ROWS, "2h")).toBeNull();
    expect(closeAtFromRows(ROWS, "3d")).toBeNull();
  });

  it("周期缺省 → null", () => {
    expect(closeAtFromRows(ROWS, null)).toBeNull();
    expect(closeAtFromRows(ROWS, undefined)).toBeNull();
  });
});

describe("WS tick 直达路径的递推数学 — 递推 O(1) ≡ 全量重算", () => {
  const closes = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 5) * 10 + i * 0.3);

  it("emaStep 递推 ≡ ema() 全量（末点）", () => {
    const full = emaFull(closes, 20);
    let v = full[0];
    for (let i = 1; i < closes.length; i++) v = emaStep(v, closes[i], 20);
    expect(v).toBeCloseTo(full[full.length - 1], 10);
  });

  it("seedMacdState + stepMacd 递推 ≡ computeMacd 全量（末点 dif/dea/hist）", () => {
    // 全量基线
    const e12 = emaFull(closes, 12);
    const e26 = emaFull(closes, 26);
    const dif = closes.map((_, i) => e12[i] - e26[i]);
    const dea = emaFull(dif, 9);
    // 递推：先播种到倒数第二根，再 step 最后一根
    const st = seedMacdState(closes.slice(0, -1))!;
    const { dif: d1, hist: h1 } = stepMacd(st, closes[closes.length - 1]);
    expect(d1).toBeCloseTo(dif[dif.length - 1], 10);
    expect(st.dea).toBeCloseTo(dea[dea.length - 1], 10);
    expect(h1).toBeCloseTo(2 * (dif[dif.length - 1] - dea[dea.length - 1]), 10);
  });

  it("seedMacdState 序列太短 → null", () => {
    expect(seedMacdState([1, 2, 3])).toBeNull();
  });
});

describe("tickChangesBar — WS tick 是否改变图上末根", () => {
  it("同 ts 同 OHLC → false（跳过 update）", () => {
    const last = { time: 1000, open: 1, high: 2, low: 0.5, close: 1.5 };
    expect(tickChangesBar(last, { ts: 1000, open: 1, high: 2, low: 0.5, close: 1.5 })).toBe(false);
  });

  it("close 变 / high-low 突破 / 新 ts → true", () => {
    const last = { time: 1000, open: 1, high: 2, low: 0.5, close: 1.5 };
    expect(tickChangesBar(last, { ts: 1000, open: 1, high: 2, low: 0.5, close: 1.6 })).toBe(true);
    expect(tickChangesBar(last, { ts: 1000, open: 1, high: 2.5, low: 0.5, close: 1.5 })).toBe(true);
    expect(tickChangesBar(last, { ts: 2000, open: 1, high: 2, low: 0.5, close: 1.5 })).toBe(true);
  });
});

describe("pickRenderPath — 渲染路径判定（toggle bug 结构性守卫）", () => {
  const prev = { length: 4, lastTs: T0 + 10800 };

  it("首次加载 → full", () => {
    expect(pickRenderPath({ length: 0, lastTs: 0 }, ROWS, false)).toBe("full");
  });

  it("数据与指标均未变 → skip", () => {
    expect(pickRenderPath(prev, ROWS, false)).toBe("skip");
  });

  it("长度与末根未变但指标变化 → full 重建", () => {
    expect(pickRenderPath(prev, ROWS, true)).toBe("full");
  });

  it("尾部新增一根（指标未变）→ append 增量", () => {
    expect(pickRenderPath(prev, [...ROWS, row(T0 + 14400, 50)], false)).toBe("append");
  });

  it("★toggle bug 守卫:尾部新增一根且指标变化 → full（不得走 append 提前 return）", () => {
    expect(pickRenderPath(prev, [...ROWS, row(T0 + 14400, 50)], true)).toBe("full");
  });

  it("历史追加（首根变新末根不变）→ full（isHistoryAppend 视区保持由调用方处理）", () => {
    expect(pickRenderPath(prev, [row(T0 - 3600, 5), ...ROWS], false)).toBe("full");
  });

  it("多根变化/回填 → full", () => {
    expect(pickRenderPath(prev, ROWS.slice(0, 2), false)).toBe("full");
    expect(pickRenderPath(prev, [...ROWS, row(T0 + 14400, 50), row(T0 + 18000, 60)], false)).toBe("full");
  });

  // ── 换标的/换周期残留的守卫（2026-10-15）────────────────────────────────
  // 交易所对任何标的/周期都返回"最近 N 根、末根对齐当前时刻",所以换图时
  // (length, lastTs) 天然相同 —— 实测 BTC 1h 与 ETH 1h 都是 n=1500、
  // 首 1786669200000、末 1792065600000。以下用例锁死:形状相同但身份不同
  // **必须** full,否则 setData 不执行、画布留在旧图(tooltip 同样读旧 rows)。
  it("★key 变了但形状完全相同 → full（换标的/换周期残留守卫）", () => {
    const withKey = { ...prev, key: "BTCUSDT@1h@binance" };
    expect(pickRenderPath(withKey, ROWS, false, "ETHUSDT@1h@binance")).toBe("full");
    // 同标的换周期同样撞形状(两周期都返回全量根数)
    expect(pickRenderPath(withKey, ROWS, false, "BTCUSDT@4h@binance")).toBe("full");
    // 同标的同周期换交易所亦然
    expect(pickRenderPath(withKey, ROWS, false, "BTCUSDT@1h@okx")).toBe("full");
  });

  it("key 相同且形状未变 → 仍走 skip（不因加了身份位而丢掉增量优化）", () => {
    const withKey = { ...prev, key: "BTCUSDT@1h@binance" };
    expect(pickRenderPath(withKey, ROWS, false, "BTCUSDT@1h@binance")).toBe("skip");
    // append 同样保持
    expect(
      pickRenderPath(withKey, [...ROWS, row(T0 + 14400, 50)], false, "BTCUSDT@1h@binance"),
    ).toBe("append");
  });

  it("key 不传 → 维持旧行为（向后兼容不关心身份的调用方）", () => {
    expect(pickRenderPath(prev, ROWS, false)).toBe("skip");
  });

  it("首次加载优先级高于身份位（prev 为空时 key 不同也是 full）", () => {
    expect(pickRenderPath({ length: 0, lastTs: 0, key: "" }, ROWS, false, "ETHUSDT@1h@binance")).toBe("full");
  });
});

// 测试本地复刻 kf-chart 内部 ema（不导出，避免为测试扩公开面）
function emaFull(values: number[], period: number): number[] {
  if (values.length === 0) return [];
  const k = 2 / (period + 1);
  const out: number[] = [values[0]];
  for (let i = 1; i < values.length; i++) {
    out.push(values[i] * k + out[out.length - 1] * (1 - k));
  }
  return out;
}

// ── 指标 picker 数学（0923）：实现 ≡ 独立参考实现 ─────────────────────────
describe("picker 指标数学 ≡ 参考实现", () => {
  const closes = Array.from({ length: 80 }, (_, i) => 100 + Math.sin(i / 4) * 8 + i * 0.5);
  const rows80: KfRow[] = closes.map((c, i) => {
    const o = i === 0 ? c - 1 : closes[i - 1];
    return { time: T0 + i * 3600, open: o, high: Math.max(o, c) + 1, low: Math.min(o, c) - 1, close: c };
  });

  it("sma 末点 ≡ 朴素窗口均值；首点从 index=period-1 起", () => {
    const s = sma(closes, 20);
    expect(s.start).toBe(19);
    expect(s.values.length).toBe(closes.length - 19);
    const naive = closes.slice(-20).reduce((a, b) => a + b, 0) / 20;
    expect(s.values[s.values.length - 1]).toBeCloseTo(naive, 10);
    // 中段抽查
    const mid = closes.slice(10, 30).reduce((a, b) => a + b, 0) / 20;
    expect(s.values[10]).toBeCloseTo(mid, 10);
  });

  it("sma 序列短于周期 → 空", () => {
    expect(sma([1, 2, 3], 20).values).toEqual([]);
  });

  it("BOLL：mid ≡ SMA20，upper/lower ≡ mid ± 2σ（总体标准差）", () => {
    const b = computeBoll(closes, 20, 2);
    const s = sma(closes, 20);
    expect(b.start).toBe(19);
    for (const k of [0, 5, 30, b.mid.length - 1]) {
      const i = 19 + k;
      const win = closes.slice(i - 19, i + 1);
      const m = win.reduce((a, x) => a + x, 0) / 20;
      const sd = Math.sqrt(win.reduce((a, x) => a + (x - m) ** 2, 0) / 20);
      expect(b.mid[k]).toBeCloseTo(m, 10);
      expect(b.upper[k]).toBeCloseTo(m + 2 * sd, 10);
      expect(b.lower[k]).toBeCloseTo(m - 2 * sd, 10);
    }
    void s;
  });

  it("RSI(14)：全涨 → 100；首值 ≡ Wilder 简单均值定义；从 index=14 起", () => {
    const up = Array.from({ length: 30 }, (_, i) => 100 + i);
    expect(computeRsi(up, 14).values.every((v) => v === 100)).toBe(true);
    const r = computeRsi(closes, 14);
    expect(r.start).toBe(14);
    expect(r.values.length).toBe(closes.length - 14);
    // 手工复算首值
    let g = 0, l = 0;
    for (let i = 1; i <= 14; i++) {
      const d = closes[i] - closes[i - 1];
      if (d >= 0) g += d; else l -= d;
    }
    g /= 14; l /= 14;
    expect(r.values[0]).toBeCloseTo(l === 0 ? 100 : 100 - 100 / (1 + g / l), 10);
  });

  it("KDJ(9,3,3)：首点从 index=8 起；K₀ 由 RSV 递推；J = 3K − 2D", () => {
    const kd = computeKdj(rows80);
    expect(kd.start).toBe(8);
    expect(kd.k.length).toBe(rows80.length - 8);
    // 手工复算全部点
    let kP = 50, dP = 50;
    for (let i = 8; i < rows80.length; i++) {
      let hh = -Infinity, ll = Infinity;
      for (let j = i - 8; j <= i; j++) {
        hh = Math.max(hh, rows80[j].high);
        ll = Math.min(ll, rows80[j].low);
      }
      const rsv = hh === ll ? 50 : ((rows80[i].close - ll) / (hh - ll)) * 100;
      kP = (kP * 2 + rsv) / 3;
      dP = (dP * 2 + kP) / 3;
      const t = i - 8;
      expect(kd.k[t]).toBeCloseTo(kP, 10);
      expect(kd.d[t]).toBeCloseTo(dP, 10);
      expect(kd.j[t]).toBeCloseTo(3 * kP - 2 * dP, 10);
    }
  });

  it("KDJ 序列短于周期 → 空数组", () => {
    const short = rows80.slice(0, 5);
    const kd = computeKdj(short);
    expect(kd.k).toEqual([]);
    expect(kd.start).toBe(8);
  });

  it("ATR(14)：首值 ≡ 前 14 个 TR 均值；Wilder 递推；从 index=14 起", () => {
    const at = computeAtr(rows80);
    expect(at.start).toBe(14);
    expect(at.values.length).toBe(rows80.length - 14);
    const tr = (i: number) => {
      const pc = rows80[i - 1].close;
      return Math.max(rows80[i].high - rows80[i].low, Math.abs(rows80[i].high - pc), Math.abs(rows80[i].low - pc));
    };
    let atr = 0;
    for (let i = 1; i <= 14; i++) atr += tr(i);
    atr /= 14;
    expect(at.values[0]).toBeCloseTo(atr, 10);
    for (let i = 15; i < rows80.length; i++) {
      atr = (atr * 13 + tr(i)) / 14;
      expect(at.values[i - 14]).toBeCloseTo(atr, 10);
    }
  });

  it("ATR/RSI 序列短于周期 → 空", () => {
    const short = rows80.slice(0, 10);
    expect(computeAtr(short).values).toEqual([]);
    expect(computeRsi(short.map((r) => r.close), 14).values).toEqual([]);
  });
});

// ── 批3 手画预警线 ────────────────────────────────────────────────────────
describe("drawnLevelPrice — 从 levels 取手画线价位", () => {
  it("正常数值 → 返回该值", () => {
    expect(drawnLevelPrice({ price: 63000 })).toBe(63000);
  });

  it("缺 price 槽 / null / undefined → null（不画、不发）", () => {
    expect(drawnLevelPrice({})).toBeNull();
    expect(drawnLevelPrice({ price: null })).toBeNull();
    expect(drawnLevelPrice(null)).toBeNull();
    expect(drawnLevelPrice(undefined)).toBeNull();
  });

  it("非数值 / 非有限数 → null（NaN、Infinity 不得被当成价位画出去）", () => {
    expect(drawnLevelPrice({ price: "63000" })).toBeNull();
    expect(drawnLevelPrice({ price: NaN })).toBeNull();
    expect(drawnLevelPrice({ price: Infinity })).toBeNull();
  });

  it("只认 price 槽：PA 的 entry/SL/TP 不算手画线（否则决策线会被当预警删掉）", () => {
    expect(drawnLevelPrice({ entry: 100, stop_loss: 95, take_profit: 110 })).toBeNull();
  });
});

describe("alertLinesFor — 按品种+周期挑线", () => {
  const rules = [
    { id: 1, symbol: "BTC/USDT", interval: "1h", levels: { price: 63000 } },
    { id: 2, symbol: "BTC/USDT", interval: "4h", levels: { price: 64000 } },
    { id: 3, symbol: "ETH/USDT", interval: "1h", levels: { price: 3000 } },
    { id: 4, symbol: "BTC/USDT", interval: "1h", levels: { entry: 100 } },
  ];

  it("只留同品种同周期的线", () => {
    expect(alertLinesFor(rules, "BTC/USDT", "1h")).toEqual([{ id: 1, price: 63000 }]);
  });

  it("周期不同不算（1h 的线不该出现在 4h 图上）", () => {
    expect(alertLinesFor(rules, "BTC/USDT", "4h")).toEqual([{ id: 2, price: 64000 }]);
  });

  it("★ symbol 大小写无关：后端存统一形式，页面可能拿到 bare/小写", () => {
    expect(alertLinesFor(rules, "btc/usdt", "1h")).toEqual([{ id: 1, price: 63000 }]);
    expect(alertLinesFor(rules, "BTC/USDT", "1h")).toEqual([{ id: 1, price: 63000 }]);
  });

  it("★ 无 price 槽的规则被剔除（PA 决策规则不该被画成手画线）", () => {
    const ids = alertLinesFor(rules, "BTC/USDT", "1h").map((l) => l.id);
    expect(ids).not.toContain(4);
  });

  it("没有匹配 → 空数组", () => {
    expect(alertLinesFor(rules, "SOL/USDT", "1h")).toEqual([]);
    expect(alertLinesFor([], "BTC/USDT", "1h")).toEqual([]);
  });
});
