"use client";

import { create } from "zustand";

// SettingsStore — 用户偏好(显示/时区等)
// 持久化:localStorage key "selenespace-settings"
// 消费者:KfChart(时区)、SetPage(设置 UI)
//
// timezone 取值:
//   "local"  — 跟随浏览器系统时区(默认)
//   "utc"    — 显示 UTC 时间
//   string   — IANA 时区名(如 "Asia/Shanghai"、"America/New_York")
//               通过 Intl.DateTimeFormat 应用,lightweight-charts 时间轴 + tooltip 都用

const STORAGE_KEY = "selenespace-settings";

export type TimezoneMode = "local" | "utc" | string;

interface SettingsState {
  timezone: TimezoneMode;
  setTimezone: (tz: TimezoneMode) => void;
}

interface PersistedSettings {
  timezone?: TimezoneMode;
}

function readInitial(): PersistedSettings {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    return JSON.parse(raw) as PersistedSettings;
  } catch {
    return {};
  }
}

function persist(s: PersistedSettings) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    // ignore storage error
  }
}

const initial = readInitial();

export const useSettingsStore = create<SettingsState>((set) => ({
  timezone: initial.timezone ?? "local",
  setTimezone: (tz) => {
    persist({ timezone: tz });
    set({ timezone: tz });
  },
}));

// 把 lightweight-charts 内部 UTC 秒转成"用户时区下应当显示的 UTC 秒"
// 原理:lightweight-charts 不做时区转换,轴上显示的就是 time 字段的"UTC 解释"
// 所以我们把原始时间戳按用户时区偏移一下,轴上用 UTC 方法读出来就是用户时区的时间
//
// local: 用浏览器 getTimezoneOffset
// utc:   偏移 0
// IANA:  用 Intl.DateTimeFormat 计算该时区相对 UTC 的偏移
export function tzOffsetSec(timezone: TimezoneMode, refMs: number = Date.now()): number {
  if (timezone === "utc") return 0;
  if (timezone === "local") {
    return -(new Date().getTimezoneOffset() * 60);
  }
  try {
    // IANA 时区:用 Intl 算偏移
    const dtf = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hour12: false,
    });
    const parts = dtf.formatToParts(new Date(refMs));
    const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0);
    const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
    return Math.round((asUtc - refMs) / 1000);
  } catch {
    // 无效时区,fallback 到 local
    return -(new Date().getTimezoneOffset() * 60);
  }
}
