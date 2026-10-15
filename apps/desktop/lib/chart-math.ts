/**
 * Client-side chart math: derive drawdown / returns / histograms from an
 * equity curve (number[] of portfolio values). Pure functions, no DOM.
 */

export function computeUnderwaterFromEquity(equityCurve: number[]): number[] {
  if (equityCurve.length < 2) return []
  let peak = equityCurve[0]
  return equityCurve.map((v) => {
    if (v > peak) peak = v
    return peak > 0 ? v / peak - 1 : 0
  })
}

export function computeReturnsFromEquity(equityCurve: number[]): number[] {
  const out: number[] = []
  for (let i = 1; i < equityCurve.length; i++) {
    const prev = equityCurve[i - 1]
    out.push(prev !== 0 ? equityCurve[i] / prev - 1 : 0)
  }
  return out
}

export function buildHistogram(
  values: number[],
  bins = 30,
): { bin_center: number; count: number }[] {
  const valid = values.filter((v) => Number.isFinite(v))
  if (valid.length === 0) return []
  const min = Math.min(...valid)
  const max = Math.max(...valid)
  const width = max - min || 1
  const buckets: number[] = Array.from({ length: bins }, () => 0)
  for (const v of valid) {
    let idx = Math.floor(((v - min) / width) * bins)
    if (idx === bins) idx = bins - 1
    if (idx < 0) idx = 0
    buckets[idx]++
  }
  return buckets.map((count, i) => {
    const center = min + width * ((i + 0.5) / bins)
    return { bin_center: center, count }
  })
}
