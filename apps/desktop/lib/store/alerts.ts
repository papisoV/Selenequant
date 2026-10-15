import { create } from "zustand";
import {
  saveAlertRuleLocal,
  deleteAlertRuleLocal,
  syncAlertRules,
  type AlertRule,
  type AlertRuleDraft,
} from "@/lib/api/intelligence";

export const ALERTS_STORAGE_KEY = "selenespace:alerts:v1";

interface AlertsState {
  rules: AlertRule[];
  draft: AlertRuleDraft | null;
  hydrated: boolean;
  syncError: string | null;
  hydrate: () => void;
  setDraft: (draft: AlertRuleDraft | null) => void;
  addRule: (draft: AlertRuleDraft) => AlertRule;
  updateRule: (id: string, patch: Partial<AlertRule>) => void;
  deleteRule: (id: string) => void;
  toggleRule: (id: string) => void;
  commitDraft: (draft: AlertRuleDraft) => AlertRule;
  syncFromBackend: () => Promise<void>;
}

function readInitial(): AlertRule[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(ALERTS_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as AlertRule[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function persist(rules: AlertRule[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ALERTS_STORAGE_KEY, JSON.stringify(rules));
  } catch (err) {
    console.error("alerts: persist failed", err);
  }
}

export const useAlertsStore = create<AlertsState>((set, get) => ({
  rules: [],
  draft: null,
  hydrated: false,
  syncError: null,

  hydrate: () => {
    if (get().hydrated) return;
    set({ rules: readInitial(), hydrated: true });
  },

  setDraft: (draft) => set({ draft }),

  addRule: (draft) => {
    const rule = saveAlertRuleLocal(draft);
    const rules = [...get().rules, rule];
    set({ rules });
    persist(rules);
    return rule;
  },

  updateRule: (id, patch) => {
    const rules = get().rules.map((r) => (r.id === id ? { ...r, ...patch } : r));
    set({ rules });
    persist(rules);
  },

  deleteRule: (id) => {
    deleteAlertRuleLocal(id);
    const rules = get().rules.filter((r) => r.id !== id);
    set({ rules });
    persist(rules);
  },

  toggleRule: (id) => {
    const rules = get().rules.map((r) =>
      r.id === id ? { ...r, enabled: !r.enabled } : r,
    );
    set({ rules });
    persist(rules);
  },

  commitDraft: (draft) => get().addRule(draft),

  syncFromBackend: async () => {
    try {
      const data = await syncAlertRules();
      const backend = data?.rules ?? [];
      if (backend.length === 0) return;
      // 本地优先:后端只补充本地没有的(按 id 去重,非 concat 重复)
      const existing = new Set(get().rules.map((r) => r.id));
      const fresh = backend.filter((r) => !existing.has(r.id));
      if (fresh.length === 0) return;
      const rules = [...get().rules, ...fresh];
      set({ rules, syncError: null });
      persist(rules);
    } catch {
      set({ syncError: "后端规则同步失败(代理断)" });
    }
  },
}));
