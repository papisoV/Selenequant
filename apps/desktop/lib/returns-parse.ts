// Shared returns parsing for the analytics panels. Single source of truth —
// analytics-panel and tearsheet-panel used to carry their own copies.

export interface ReturnsSeries {
  index: string[];
  columns: { ret: number[] };
}

/**
 * Parse one number per line into a ReturnsSeries with daily timestamps
 * starting 2024-01-01. Returns null on empty input or on the first invalid
 * line. `onInvalid` (when given) is called with the 1-based line number and
 * raw text of the offending line so callers can surface a friendly error.
 */
export function parseReturnsSeries(
  text: string,
  onInvalid?: (line: number, raw: string) => void,
): ReturnsSeries | null {
  if (!text.trim()) return null;
  const lines = text.trim().split(/\r?\n/);
  const nums: number[] = [];
  const idx: string[] = [];
  const start = new Date("2024-01-01T00:00:00Z");
  for (let i = 0; i < lines.length; i++) {
    const v = Number(lines[i]);
    if (!Number.isFinite(v)) {
      onInvalid?.(i + 1, lines[i]);
      return null;
    }
    nums.push(v);
    const d = new Date(start.getTime() + i * 86400_000);
    idx.push(d.toISOString().slice(0, 10));
  }
  return { index: idx, columns: { ret: nums } };
}

/** Deterministic sample returns (same PRNG as the original panels). */
export function genSampleReturns(n: number): ReturnsSeries {
  const arr: number[] = new Array(n);
  let seed = 42;
  for (let i = 0; i < n; i++) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    const u = seed / 0x7fffffff;
    const v = (seed >> 8) / 0x7fffffff;
    arr[i] = 0.001 + 0.02 * (u - 0.5) + (v - 0.5) * 0.01;
  }
  const index: string[] = [];
  const start = new Date("2024-01-01T00:00:00Z");
  for (let i = 0; i < n; i++) {
    const d = new Date(start.getTime() + i * 86400_000);
    index.push(d.toISOString().slice(0, 10));
  }
  return { index, columns: { ret: arr } };
}
