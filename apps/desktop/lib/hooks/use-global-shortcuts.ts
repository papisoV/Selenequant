"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// 双键快捷键 — 不带修饰键,避免与 ⌘1-5 / ⌘K 冲突
// 第一键 G(group),第二键代表目标场景/页面
// 输入框聚焦时不触发(检查 tagName)
// 800ms 内未按下一键则清空 buffer
// 键格式:"g"+第二个字母(无空格),如 "gm" → /watch/mk
const SHORTCUTS: Record<string, string> = {
  // 场景首页
  gk: "/watch/kf",
  gr: "/research/today",
  gt: "/trade/pos",
  gs: "/system/dash",
  // 高频页面
  gm: "/watch/mk",
  gg: "/watch/gex",
  gl: "/watch/derivatives-hub",
  gv: "/watch/vchart",
  gd: "/trade/trd",
  gp: "/trade/pos",
  gf: "/research/strat",
  go: "/research/rsch",
  // A股看盘台
  ga: "/watch/ashare-board",
};

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  return false;
}

export function useGlobalShortcuts() {
  const router = useRouter();
  useEffect(() => {
    let buffer = "";
    let timer: ReturnType<typeof setTimeout> | null = null;

    const handler = (e: KeyboardEvent) => {
      // 不拦截带修饰键的(留给 ⌘K / ⌘1-5 / Cmd+C 等)
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      // 输入框聚焦时不触发
      if (isEditableTarget(e.target)) return;
      // 忽略非字母键(F1-F12, Tab, Arrow, etc.)
      if (e.key.length !== 1) return;

      buffer = (buffer + e.key.toLowerCase()).slice(-2);
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        buffer = "";
      }, 800);

      const path = SHORTCUTS[buffer];
      if (path) {
        e.preventDefault();
        router.push(path);
        buffer = "";
        if (timer) {
          clearTimeout(timer);
          timer = null;
        }
      }
    };

    window.addEventListener("keydown", handler);
    return () => {
      window.removeEventListener("keydown", handler);
      if (timer) clearTimeout(timer);
    };
  }, [router]);
}
