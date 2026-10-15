/**
 * 日历日 → ISO 时刻(日粒度)。
 *
 * 语义:**UTC 日历日的日终 23:59:59**。不是本地日历日 —— 选 "2026-09-27"
 * 得到的是 UTC 09-27T23:59:59Z(= UTC+8 的 09-28 07:59)。
 *
 * 为什么按 UTC:库内所有时间戳都是 UTC(见 _parse_iso 的 naive→UTC 兜底),
 * 按 UTC 日终取回放边界,与数据同族。跨时区存在至多一个时区偏移的边界模糊
 * —— 对"回放某天账本知道什么"这个粗粒度问题,可接受。
 *
 * 先例:question-surface.tsx 的 RevisitBar 已用同一算法(复访日),
 * 本模块把它提出来供两处共用,避免同式两份漂移。
 */
export const localDateToIso = (yyyyMmDd: string): string => {
  const [y, m, d] = yyyyMmDd.split("-").map(Number);
  return new Date(Date.UTC(y, (m ?? 1) - 1, d ?? 1, 23, 59, 59)).toISOString();
};
