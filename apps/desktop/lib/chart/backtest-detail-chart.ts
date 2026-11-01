import type { EChartsOption } from "echarts";
import { cursorMarkLine } from "./time-cursor";

export interface BacktestDetailChartColors {
  gold: string;
  ink3: string;
  destructive: string;
  borderSoft: string;
}

export interface BacktestDetailChartInput {
  timestamps: number[];
  equityCurve: number[];
  buyHoldCurve?: number[];
  drawdownCurve?: number[];
  exposureCurve?: number[];
  colors: BacktestDetailChartColors;
  /** 跨图共享时间游标(ms)。null/undefined 不画。 */
  cursorMs?: number | null;
  /** 游标竖线色(中性导引色)。缺省时用 colors.ink3。 */
  cursorColor?: string;
}

/**
 * 只更新游标的轻量补丁 —— 悬停期间每次 mousemove 都重建整份 option 太贵。
 * ECharts 无 id 时按**下标**合并 series,故长度与本图 series 数一致即等价于
 * "把每条 series 的 markLine 换掉",其余配置原样保留。
 */
export function cursorSeriesPatch(seriesCount: number, ms: number | null, color: string) {
  return Array.from({ length: seriesCount }, () => ({ markLine: cursorMarkLine(ms, color) }));
}

// 轴标签:9px 在细带上糊成一团,抬到 10px(宿主高度同步放大后才可读)。
// 颜色是灰,与方向轴无关,locale 翻转不需要跟随 —— 保持中性常量。
// margin:刻度与轴线的呼吸位,10px 字下 8px 仍会贴线,给到 10。
const AXIS_LABEL = { fontSize: 10, color: "#8b8f94", margin: 10 } as const;

export function buildBacktestDetailOption(
  input: BacktestDetailChartInput
): EChartsOption {
  const { colors } = input;
  const n = input.equityCurve.length;

  // 时间戳缺失/长度不齐 → ordinal 轴回退,不造假时间
  const useTime = input.timestamps.length === n && n > 0;
  const xData: number[] = useTime ? input.timestamps : input.equityCurve.map((_, i) => i);
  const xAxis = {
    type: useTime ? ("time" as const) : ("category" as const),
    data: useTime ? undefined : xData,
    axisLabel: AXIS_LABEL,
    axisLine: { lineStyle: { color: colors.borderSoft } },
  };

  const series: EChartsOption["series"] = [
    {
      name: "权益",
      type: "line",
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
      data: input.equityCurve.map((v, i) => [xData[i], v]),
      lineStyle: { width: 1.5, color: colors.gold },
      itemStyle: { color: colors.gold },
    },
  ];

  if (input.buyHoldCurve && input.buyHoldCurve.length === n) {
    series.push({
      name: "基准买入持有",
      type: "line",
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
      data: input.buyHoldCurve.map((v, i) => [xData[i], v]),
      lineStyle: { width: 1, color: colors.ink3, type: "dashed" },
      itemStyle: { color: colors.ink3 },
    });
  }

  if (input.drawdownCurve && input.drawdownCurve.length === n) {
    series.push({
      name: "回撤",
      type: "line",
      xAxisIndex: 1,
      yAxisIndex: 1,
      showSymbol: false,
      data: input.drawdownCurve.map((v, i) => [xData[i], v]),
      lineStyle: { width: 1, color: colors.destructive },
      itemStyle: { color: colors.destructive },
      areaStyle: { color: colors.destructive, opacity: 0.18 },
    });
  }

  if (input.exposureCurve && input.exposureCurve.length === n) {
    series.push({
      name: "敞口",
      type: "line",
      xAxisIndex: 2,
      yAxisIndex: 2,
      showSymbol: false,
      data: input.exposureCurve.map((v, i) => [xData[i], v]),
      lineStyle: { width: 1, color: colors.ink3 },
      itemStyle: { color: colors.ink3 },
    });
  }

  // 共享时间游标:每条 series 各挂一条 markLine,三段网格同时标出同一时刻。
  // 放在 series 构造之后统一注入,避免四处重复字面量。
  const cursorColor = input.cursorColor ?? colors.ink3;
  const cursorMs = input.cursorMs ?? null;
  const seriesWithCursor = (series as { markLine?: unknown }[]).map((s) => ({
    ...s,
    markLine: cursorMarkLine(cursorMs, cursorColor),
  }));

  return {
    backgroundColor: "transparent",
    animation: false,
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    // tooltip 只做一件事:让 ECharts 在悬停时给出 updateAxisPointer 事件
    // (共享游标的数据源)。内容用 axis 形式,视觉上等价于只有一条轴指示线。
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: cursorColor, width: 1, type: "dashed" } },
      backgroundColor: "rgba(15,20,25,0.92)",
      borderColor: colors.borderSoft,
      textStyle: { fontSize: 10, color: "#e5e8eb" },
    },
    legend: {
      top: 0,
      textStyle: { fontSize: 10, color: colors.ink3 },
      itemWidth: 12,
      itemHeight: 8,
    },
    // 三格竖排:主图(权益+基准)/回撤/敞口。用 px 顶距而非百分比 ——
    // 百分比在矮宿主上会把图例和轴标签挤到负数区域(9px 时代的老问题)。
    // 宿主 420px:图例占 26,主图 210,回撤 70,敞口 40,各格间留 22px 呼吸位,
    // 末尾 12px 间隙 + 18px dataZoom 滑块。回撤格抬到 70 是因为它的 % 标签
    // 与轴名需要纵向空间,54px 时数字会互相叠。
    grid: [
      { left: 64, right: 16, top: 26, height: 210 },
      { left: 64, right: 16, top: 258, height: 70 },
      { left: 64, right: 16, top: 350, height: 40 },
    ],
    // ⚠️ gridIndex 必须逐轴显式绑定:series 的 xAxisIndex/yAxisIndex 只选轴,
    //    轴自己不声明 gridIndex 时 ECharts 一律落到 grid 0 —— 回撤/敞口会画进
    //    主图里(整片红色区域盖住权益线),而 1/2 号网格永远空白。这是"图挤成一团"
    //    的真因,与宿主高度无关。
    xAxis: [
      { ...xAxis, gridIndex: 0 },
      { ...xAxis, gridIndex: 1, show: false },
      { ...xAxis, gridIndex: 2, show: false },
    ],
    yAxis: [
      {
        type: "value",
        gridIndex: 0,
        scale: true,
        axisLabel: AXIS_LABEL,
        splitLine: { lineStyle: { color: colors.borderSoft } },
      },
      {
        type: "value",
        gridIndex: 1,
        // 回撤以百分数展示(QD 同款);selene 存的是小数比例
        axisLabel: { ...AXIS_LABEL, formatter: (v: number) => `${(v * 100).toFixed(1)}%` },
        splitLine: { show: false },
      },
      {
        type: "value",
        gridIndex: 2,
        scale: true,
        axisLabel: { ...AXIS_LABEL, formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1, 2] },
      {
        type: "slider",
        xAxisIndex: [0, 1, 2],
        bottom: 0,
        height: 18,
        borderColor: colors.borderSoft,
        textStyle: { fontSize: 10, color: colors.ink3 },
      },
    ],
    series: seriesWithCursor,
  };
}
