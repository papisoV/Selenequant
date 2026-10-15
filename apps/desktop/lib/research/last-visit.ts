"use client";

// desk 页「自上次以来」锚点:纯 localStorage(无后端同步,单用户桌面应用)。
// 读:mount 时取上次访问时刻;写:数据加载成功后 + unmount 时,写响应携带的
// server_now(防客户端时钟漂移)。首访返回 null,由调用方回看 BACKLOOK_DAYS。

export const LAST_VISIT_KEY = "selene.desk.lastVisitedAt";

export const BACKLOOK_DAYS = 7;

export function readLastVisited(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(LAST_VISIT_KEY);
    if (!raw) return null;
    // 校验是合法 ISO 时间戳,坏数据当作首访
    return Number.isNaN(Date.parse(raw)) ? null : raw;
  } catch {
    return null;
  }
}

export function writeLastVisited(iso: string): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(LAST_VISIT_KEY, iso);
  } catch {
    // ignore quota errors
  }
}
