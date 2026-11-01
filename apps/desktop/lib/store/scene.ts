"use client";

import { create } from "zustand";
import type { Scene } from "@/lib/page-manifest";

// 场景切换 + 订阅生命周期管理
// 替代设计稿第 8361-8417 行场景切换的 WS 订阅/退订逻辑

interface SceneState {
  current: Scene;
  currentPage: string;
  setScene: (scene: Scene) => void;
  setPage: (page: string) => void;
}

export const useSceneStore = create<SceneState>((set) => ({
  current: "watch",
  currentPage: "kf",
  setScene: (scene) => set({ current: scene }),
  setPage: (page) => set({ currentPage: page }),
}));
