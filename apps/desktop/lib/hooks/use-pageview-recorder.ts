"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { getCategoryFromPath, type PageCategory } from "@/lib/data/page-categories";

// pageview 停留记录 — 纯本地 IndexedDB（零后端请求）。
// 每次路由变化结算上一条 dwell_ms 并写入 {ts,slug,scene,dwell_ms}。
// 90 天滚动截断，防库无限膨胀。供 nav-audit / 行为分析离线读取。

const DB_NAME = "selenespace-pageview";
const STORE = "views";
const MAX_AGE_MS = 90 * 24 * 60 * 60 * 1000;

export interface PageviewRecord {
  ts: number;
  slug: string;
  scene: PageCategory;
  dwell_ms: number;
}

function openDb(): Promise<IDBDatabase | null> {
  return new Promise((resolve) => {
    if (typeof indexedDB === "undefined") return resolve(null);
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE, { autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null); // 隐私模式等场景静默降级
  });
}

function put(db: IDBDatabase, record: PageviewRecord): void {
  try {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).add(record);
    tx.oncomplete = () => db.close();
  } catch {
    // ignore
  }
}

async function pruneOld(db: IDBDatabase): Promise<void> {
  const cutoff = Date.now() - MAX_AGE_MS;
  const tx = db.transaction(STORE, "readwrite");
  const store = tx.objectStore(STORE);
  const cursorReq = store.openCursor();
  cursorReq.onsuccess = () => {
    const cursor = cursorReq.result;
    if (!cursor) return;
    const rec = cursor.value as PageviewRecord;
    if (!rec || typeof rec.ts !== "number" || rec.ts < cutoff) cursor.delete();
    cursor.continue();
  };
  tx.oncomplete = () => db.close();
}

function slugFromPath(pathname: string): string {
  const clean = pathname.split(/[?#]/)[0].replace(/^\/+/, "");
  return clean || "landing";
}

export function usePageviewRecorder(): void {
  const pathname = usePathname();
  const enteredAtRef = useRef<number>(Date.now());
  const currentRef = useRef<{ slug: string; scene: PageCategory } | null>(null);

  useEffect(() => {
    const now = Date.now();
    const prev = currentRef.current;
    const dwell = now - enteredAtRef.current;

    // 路由变化:结算上一页停留,开新页计时
    if (prev && dwell > 0) {
      const record: PageviewRecord = { ts: enteredAtRef.current, slug: prev.slug, scene: prev.scene, dwell_ms: dwell };
      void openDb().then((db) => {
        if (!db) return;
        if (Math.random() < 0.1) void pruneOld(db); // 10% 概率滚动截断,省 cursor 开销
        put(db, record);
      });
    }

    enteredAtRef.current = now;
    currentRef.current = {
      slug: slugFromPath(pathname),
      scene: getCategoryFromPath(pathname) ?? "system",
    };
  }, [pathname]);
}
