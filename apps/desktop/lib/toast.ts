"use client";

import { useEffect, useState } from "react";

type ToastLevel = "info" | "success" | "warn" | "error";

interface ToastItem {
  id: number;
  level: ToastLevel;
  msg: string;
}

let nextId = 1;
let pushExternal: ((level: ToastLevel, msg: string) => void) | null = null;

export function toast(level: ToastLevel, msg: string) {
  if (pushExternal) {
    pushExternal(level, msg);
    return;
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent("selene-toast", { detail: { level, msg } })
    );
  }
}

export function toastSuccess(msg: string) {
  toast("success", msg);
}
export function toastInfo(msg: string) {
  toast("info", msg);
}
export function toastWarn(msg: string) {
  toast("warn", msg);
}
export function toastError(msg: string) {
  toast("error", msg);
}

export function useToastContainer(): {
  toasts: ToastItem[];
  remove: (id: number) => void;
} {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ level: ToastLevel; msg: string }>).detail;
      const id = nextId++;
      setToasts((prev) => [...prev, { id, level: detail.level, msg: detail.msg }]);
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 3000);
    };
    window.addEventListener("selene-toast", handler);
    pushExternal = (level, msg) => {
      const id = nextId++;
      setToasts((prev) => [...prev, { id, level, msg }]);
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 3000);
    };
    return () => {
      window.removeEventListener("selene-toast", handler);
      pushExternal = null;
    };
  }, []);

  return {
    toasts,
    remove: (id) => setToasts((prev) => prev.filter((t) => t.id !== id)),
  };
}
