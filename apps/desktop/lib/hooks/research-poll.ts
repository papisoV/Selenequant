/**
 * Research 模块统一轮询节奏(审计 2026-10-21:同页 3 种节拍、全模块 8 种)。
 *
 * 两档:
 *  - LIVE:调查/任务在几分钟内推进,5s 才看得见视角逐个亮起(today 活动流、
 *    missions 进行中、question 等待 worker)。
 *  - SLOW:状态本身分钟级变化(today 聚合、memory 计数、heartbeat 状态)。
 *
 * 任务型轮询(进度条 2-3s、事务等待)不归此管,等待结束即停。
 * SWR 的 refreshInterval 自带 document.hidden 暂停;手写 setInterval 的页面
 * 必须配 pauseWhenHidden,否则后台标签页也在打后端。
 */
export const RESEARCH_POLL_LIVE_MS = 5_000;
export const RESEARCH_POLL_NORMAL_MS = 30_000;
export const RESEARCH_POLL_SLOW_MS = 60_000;

/**
 * 手写 setInterval 轮询的隐藏页暂停。document.hidden 时回调直接跳过
 * (不清 timer,恢复前台即续),与 SWR refreshInterval 行为对齐。
 */
export function pollWhenVisible(fn: () => void): void {
  if (typeof document !== "undefined" && document.hidden) return;
  fn();
}
