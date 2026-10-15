"use client";

import { useEffect, useRef, useState } from "react";
import { apiPost } from "@/lib/api-client";
import { toastSuccess, toastError, toastInfo } from "@/lib/toast";
import { clickable } from "@/lib/a11y/clickable";

// 设计稿 L3662-3677 Kill Switch 两部确认动画
// 第一次 click → confirming state（红色闪烁 + "再点一次确认"）
// 3 秒内第二次 click → POST /trading/kill-switch
// 3 秒外或 ESC → 取消

export function VheroKill() {
  const [confirming, setConfirming] = useState(false);
  const [triggered, setTriggered] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  useEffect(() => {
    return clearTimer;
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && confirming) {
        setConfirming(false);
        clearTimer();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirming]);

  const handleClick = async () => {
    if (triggered) return;

    if (!confirming) {
      setConfirming(true);
      clearTimer();
      timerRef.current = setTimeout(() => {
        setConfirming(false);
      }, 3000);
      return;
    }

    // 第二次 click — 3 秒内
    clearTimer();
    setConfirming(false);
    setTriggered(true);
    try {
      await apiPost("/api/v1/trading/kill-switch", { reason: "manual-ui-trigger" });
      toastSuccess("Kill Switch 已触发：所有挂单取消 + 平仓");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toastError(`Kill Switch 触发失败: ${msg}`);
      setTriggered(false);
    }
  };

  return (
    <div
      className={`vhero-kill ${confirming ? "confirming" : ""} ${triggered ? "triggered" : ""}`}
      title={
        triggered
          ? "Kill Switch 已触发 — POST /trading/kill-switch/reset 重置"
          : confirming
          ? "再点一次确认（3 秒内）· ESC 取消"
          : "Kill Switch — 一键停止所有交易（两部确认）"
      }
      {...clickable(handleClick)}
    >
      <svg viewBox="0 0 14 14" fill="none">
        <path d="M4 4 L10 10 M10 4 L4 10" stroke="currentColor" strokeWidth="1.8" />
        <circle cx="7" cy="7" r="6" stroke="currentColor" strokeWidth="1" fill="none" />
      </svg>
      <span className="vk-label">{triggered ? "TRIG" : confirming ? "确认?" : "KILL"}</span>
    </div>
  );
}
