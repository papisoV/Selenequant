export interface DcaConfig {
  /** 每期投入金额 */
  amountPerPeriod: number;
  /** 期数 */
  periodCount: number;
  /** 周期标签(日/周/月) */
  periodLabel: string;
}

export interface DcaScheduleRow {
  period: number;
  /** 累计投入 */
  invested: number;
  /** 当期价格 */
  price: number;
  /** 当期获得单位 */
  units: number;
  /** 累计单位 */
  totalUnits: number;
  /** 累计成本均价 = 累计投入 / 累计单位 */
  avgCost: number;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}

/** DCA 计划表:逐期追加,累计均价收敛 */
export function buildDcaSchedule(
  config: DcaConfig,
  priceSequence?: number[],
): DcaScheduleRow[] {
  const { amountPerPeriod, periodCount } = config;
  const rows: DcaScheduleRow[] = [];
  let invested = 0;
  let totalUnits = 0;
  for (let i = 0; i < periodCount; i++) {
    const price = priceSequence?.[i] ?? 1;
    const units = amountPerPeriod / price;
    invested += amountPerPeriod;
    totalUnits += units;
    rows.push({
      period: i + 1,
      invested: round2(invested),
      price,
      units: round4(units),
      totalUnits: round4(totalUnits),
      avgCost: round4(invested / totalUnits),
    });
  }
  return rows;
}

/** 估算成本均价(纯函数) */
export function estimateAvgCost(totalInvested: number, totalUnits: number): number {
  if (!totalUnits || totalUnits <= 0) return 0;
  return round4(totalInvested / totalUnits);
}

/** DCA 演示行情(固定价格序列,便于测试断言) */
export const DEMO_DCA_PRICES = [100, 50, 100, 80, 120, 75, 90, 110, 85, 95];
