// 图上绘图对象的数据模型 —— 纯函数层(零 React / 零图表库依赖,jsdom 可测)。
//
// 本模块只回答一件事:**一条手绘对象由哪些数字定义,以及从这些数字能推出什么**。
// 渲染(交给 drawing-scene 画到叠加层)和持久化(交给 localStorage 后端)都从这里取数,不各自
// 重新推导,避免"图上看到的"和"存下来的"是两套算法。
//
// 坐标系约定(两条,不要混):
// - **锚点**用「时间(s) + 价格」:与周期无关。1h 上画的腿在 5m 图上按时间重锚,
//   这正是跨周期复用同一组线的前提。
// - **档位**只存比例(0..1 及延伸位),不存价格。价格永远由两个锚点现算 ——
//   存价格会让改动一个锚点后其余线全部失真。

/** 一条斐波那契网格的两个锚点。 */
export interface FibAnchor {
  /** 起点(腿的开始/结束由画法决定,本层不做方向判断)。epoch 秒,UTC。 */
  time: number;
  price: number;
}

/** 图的周期,用于判断"这个对象能不能在当前图上显示"。 */
export type ChartInterval = string;

/**
 * 单个档位的样式覆盖。只允许改"看得见的那几样",不改几何 ——
 * 档位的价格永远由比例和两个锚点现算,覆盖样式绝不能把它挪走。
 *
 * 未提供的字段沿用该档位的默认桶(见 fib-style.ts `styleForLevel`)。
 */
export interface LevelOverride {
  /** CSS 十六进制色,如 `#ff6b6b`。非该形状的串在解析时被丢弃。 */
  color?: string;
  /** 线宽(px)。超出行情图可读区间的值在写入时被夹住。 */
  width?: number;
  /** 虚线与否。延伸位默认虚线,可被这里改写。 */
  dashed?: boolean;
  /** 档位旁的自定义文字(如 "TP1")。空串 = 用默认的比例标签。 */
  text?: string;
}

/** 一条样式覆盖 = 比例 + 覆盖字段。 */
export interface LevelStyleEntry extends LevelOverride {
  coeff: number;
}

/**
 * 网格的几何模式 —— 同一个锚点模型装全部绘图工具,省掉一套平行的数据层。
 *
 * ### 斐波那契家族(有档位)
 * - `price`:档位是**腿的价格比例**,画成腿区间的水平线(经典 retracement);
 * - `time`:档位是**腿时长的倍数**,画成按 1/2/3/5/8… 右铺的竖线(fib time zone)。
 *   两者 `levels` 语义不同(比例 vs 倍数),档位族分列在
 *   fib-levels.ts / fib-time-levels.ts,互不引用。
 *
 * ### 基础几何(无档位,`levels` 恒为空)
 * - `trend`:两个锚点一条线段(TradingView 的 Trend Line);
 * - `horizontal`:水平线,只取**终点**价格,横贯可视区(Horizontal Line);
 * - `vertical`:竖线,只取**终点**时间,上下贯通(Vertical Line);
 * - `rect`:两锚点为对角线的矩形(Rectangle)。
 *
 * 基础几何复用两个锚点而不是另起一套点模型,是因为交互、命中、拖动、
 * 持久化全部已经按"两个锚点"写好了 —— 换点模型会让这些全部重写一遍。
 * 端点在退化方向上的自由度由各模式自己解释(水平线忽略时间、竖线忽略价格)。
 */
export type FibGridMode = "price" | "time" | "trend" | "horizontal" | "vertical" | "rect";

/** 带档位的模式(斐波那契家族)。其余模式的 `levels` 为空。 */
export function hasLevels(mode: FibGridMode): boolean {
  return mode === "price" || mode === "time";
}

/** 一条斐波那契网格。
 *
 * `levels` 存比例而不是价格。0 和 1 是腿两端;内部按 trader 自己的档位族
 * (见 lib/chart/fib-levels.ts)。大于 1 / 小于 0 的是延伸位。
 *
 * `mode: "time"` 时 `levels` 改存时间倍数,渲染成竖线(见 fib-time-levels.ts)。
 */
export interface FibGrid {
  id: string;
  /** 所属对象树分组;null = 顶层。 */
  groupId: string | null;
  start: FibAnchor;
  end: FibAnchor;
  /**
   * 几何模式。缺省(旧数据)按 `"price"` 解析 —— v1/v2 存档全部是价格网格。
   */
  mode: FibGridMode;
  /** 比例位(mode=price)或时间倍数(mode=time),升序或降序皆可,渲染前统一排序。 */
  levels: readonly number[];
  /** 用户手动勾选"自动扩展"后由引擎写入的派生位;手绘对象本身不含延伸位。 */
  autoExtend: boolean;
  /**
   * 档位线向左侧延伸到可视区边缘。默认 false = 线段(只画两锚点之间),
   * 与 TradingView 的 `extendLeft` 同义。
   *
   * 注意与 `autoExtend` 区分:那个加的是**价格档位**(1.272…2.618),
   * 这个改的是**线的横向长度**。两者互不相关。
   */
  extendLeft: boolean;
  /** 档位线向右侧延伸到可视区边缘。默认 false = 线段。见 `extendLeft`。 */
  extendRight: boolean;
  /** 可见性。隐藏的对象仍存在,只是不画。 */
  visible: boolean;
  /** 锁定的对象不可拖动(防误触搬走整张网格)。 */
  locked: boolean;
  /** 画这条线时所在的周期。仅作标注,不参与几何计算。 */
  timeframe: ChartInterval;
  /** 创建时的标的。跨标的默认不显示(不同计价单位,价位无意义)。 */
  symbol: string;
  /** 用户给这组线起的名字;空串 = 未命名。 */
  label: string;
  /**
   * 逐档样式覆盖。缺省 / 空数组 = 全部走默认桶。
   * 只覆盖存在的那几项,没列出的档位不受影响。
   */
  levelStyles?: readonly LevelStyleEntry[];
}

/**
 * 取某个档位的样式覆盖。比较用严格相等:比例来自同一个 `levels` 数组里的数,
 * 不存在"0.618 与 0.6180000001"这种近邻需求。
 */
export function levelOverride(grid: FibGrid, level: number): LevelStyleEntry | undefined {
  return grid.levelStyles?.find((s) => s.coeff === level);
}

/**
 * 把比例位换算成价格。腿从 `start.price` 走到 `end.price`,
 * 比例 0 → start.price,1 → end.price。延伸位按同一线性关系外推。
 */
export function priceAtLevel(
  startPrice: number,
  endPrice: number,
  level: number,
): number {
  return startPrice + (endPrice - startPrice) * level;
}

/**
 * 一个网格全部档位的价格,与 `levels` 同序(调用方先自行排序)。
 *
 * 不在这里排序,是因为渲染层要按自身顺序画;排序只应发生一次,
 * 由 `normalizeLevels` 负责。
 */
export function gridPrices(grid: FibGrid): number[] {
  return grid.levels.map((lv) => priceAtLevel(grid.start.price, grid.end.price, lv));
}

/** 档位去重 + 升序。NaN/Infinity 剔除 —— 坏数据不该画出一条走向无穷的线。 */
export function normalizeLevels(levels: readonly number[]): number[] {
  const seen = new Set<number>();
  const out: number[] = [];
  for (const lv of levels) {
    if (!Number.isFinite(lv)) continue;
    if (seen.has(lv)) continue;
    seen.add(lv);
    out.push(lv);
  }
  return out.sort((a, b) => a - b);
}

/** 两个锚点是否退化成一条水平线(价格相等)或一个点。退化网格没有可读的档位。 */
export function isDegenerate(grid: FibGrid): boolean {
  return grid.start.price === grid.end.price || grid.start.time === grid.end.time;
}

/** 腿的方向:价格从 start 到 end 是上行 +1、下行 -1、持平 0。 */
export function legDirection(grid: FibGrid): -1 | 0 | 1 {
  const d = grid.end.price - grid.start.price;
  if (d > 0) return 1;
  if (d < 0) return -1;
  return 0;
}

/**
 * 网格的横向覆盖区间(epoch 秒)。用于决定网格是否落在当前视口内,
 * 以及是否需要延伸到最后价。
 */
export function gridTimeRange(grid: FibGrid): { from: number; to: number } {
  return {
    from: Math.min(grid.start.time, grid.end.time),
    to: Math.max(grid.start.time, grid.end.time),
  };
}

/**
 * 当前周期下是否应显示这条网格。
 *
 * 规则(刻意保守):
 * - 标的不同 → 不显示。价位单位不同,画上去纯属误导。
 * - 标的相同 → 显示,不管画在哪个周期。这是"高周期画的线在低周期按时间重锚"
 *   的实现点:锚点是时间 + 价格,与周期无关。
 */
export function isVisibleOnChart(
  grid: FibGrid,
  symbol: string,
): boolean {
  if (!grid.visible) return false;
  return grid.symbol === symbol;
}
