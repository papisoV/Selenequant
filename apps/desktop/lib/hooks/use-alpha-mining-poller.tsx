"use client";

import { useEffect } from "react";
import { useAlphaMiningStore } from "@/lib/store/alpha-mining";

// 全局单点轮询:侧边栏徽标与挖矿面板共享同一份 sessions,
// 避免两处各起一个 setInterval 重复打 GET /alpha/mining。
// 节奏:有活跃任务 5s / 空闲 15s;标签页隐藏时暂停(回来立即补一次)。

const ACTIVE_MS = 5000;
const IDLE_MS = 15000;

export function AlphaMiningPoller() {
  const refresh = useAlphaMiningStore((s) => s.refresh);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const schedule = () => {
      if (cancelled) return;
      const hasRunning = useAlphaMiningStore.getState().sessions.some(
        (s) => s.status === "starting" || s.status === "running" || s.status === "stopping",
      );
      timer = setTimeout(tick, hasRunning ? ACTIVE_MS : IDLE_MS);
    };

    const tick = async () => {
      if (cancelled) return;
      if (typeof document !== "undefined" && document.hidden) {
        // 隐藏时不轮询,但仍排下一次(visibilitychange 会提前唤醒)。
        schedule();
        return;
      }
      await refresh();
      schedule();
    };

    const onVisible = () => {
      if (cancelled) return;
      if (timer) clearTimeout(timer);
      void tick();
    };

    void tick();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh]);

  return null;
}
