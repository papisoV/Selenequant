import { create } from "zustand";
import { ApiError } from "@/lib/api-client";
import {
  createAlertRule,
  deleteAlertRule,
  saveAlertRuleLocal,
  seedAlertRules,
  syncAlertRules,
  updateAlertRule,
  type AlertRule,
  type AlertRuleDraft,
} from "@/lib/api/intelligence";

export const ALERTS_STORAGE_KEY = "selenespace:alerts:v1";

/**
 * 本地视图里的规则。
 *
 * ``localOnly`` 是纯客户端的「后端还没有这条」标记,不是后端字段 —— 它不能
 * 挂在 ``AlertRule``(API 响应类型)上,否则那个类型会声明一个后端永远不会
 * 填的字段。
 */
export type StoredAlertRule = AlertRule & { localOnly?: boolean };

interface AlertsState {
  rules: StoredAlertRule[];
  draft: AlertRuleDraft | null;
  hydrated: boolean;
  syncError: string | null;
  hydrate: () => void;
  setDraft: (draft: AlertRuleDraft | null) => void;
  addRule: (draft: AlertRuleDraft) => StoredAlertRule;
  updateRule: (id: string, patch: Partial<AlertRule>) => void;
  deleteRule: (id: string) => void;
  toggleRule: (id: string) => void;
  syncFromBackend: () => Promise<void>;
}

function readInitial(): StoredAlertRule[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(ALERTS_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as StoredAlertRule[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function persist(rules: StoredAlertRule[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ALERTS_STORAGE_KEY, JSON.stringify(rules));
  } catch (err) {
    console.error("alerts: persist failed", err);
  }
}

/** 写失败时给用户看的话。规则仍在本地,不丢用户输入。 */
const WRITE_ERROR = "后端写入失败 —— 规则暂存本地,重启后不会生效";
const READ_ERROR = "后端规则同步失败(直连断开)";

// 列表同步的重入护栏:页面挂载同时还有别的入口调 hydrate/sync 时只跑一份。
let _syncInFlight: Promise<void> | null = null;

// 每条规则在飞的写请求。对同一条规则的后续写必须排在它后面 —— 否则
// 「刚建完就切开关」会拿一个后端还不存在的本地 id 去 PUT,换回 404。
const _pendingWrites = new Map<string, Promise<unknown>>();

/**
 * 登记一条在飞的写,并在它结束时只清理「它自己」。
 *
 * 后发起的写会覆盖同 id 的登记;若清理时不比对身份,先结束的那条会把后来者
 * 的记录一起删掉,之后对该规则的写就再也排不上队。
 */
function track(ruleId: string, write: Promise<unknown>): void {
  _pendingWrites.set(ruleId, write);
  void write.catch(() => {}).then(() => {
    if (_pendingWrites.get(ruleId) === write) _pendingWrites.delete(ruleId);
  });
}

/**
 * 等「这条规则的写」和「首次迁移」都结束。
 *
 * 迁移期间的写必须排队:同一条规则若被 seed 上传的同时又被 PUT/POST,后端会
 * 出现两份同名规则,而本地只留一份 —— 列表看起来正常,监控却触发两次。
 */
async function ready(ruleId?: string): Promise<void> {
  const writes = ruleId ? _pendingWrites.get(ruleId) : undefined;
  await Promise.all([writes ?? Promise.resolve(), _syncInFlight ?? Promise.resolve()]);
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

  // 写路径统一是「乐观 + 回写」:先改界面并落 localStorage,再以后端返回的
  // 权威形态替换。后端不可用时保留本地并标 syncError —— 静默丢弃用户刚填的
  // 规则,比写失败本身更糟。
  // `localOnly` 是持久的「后端还不知道我」标记,必须能活过刷新:否则下次进
  // 页面时,同步会把这条没写上行的规则当成后端已删除的残留抹掉。
  addRule: (draft) => {
    const local: StoredAlertRule = { ...saveAlertRuleLocal(draft), localOnly: true };
    set({ rules: [...get().rules, local], syncError: null });
    persist(get().rules);

    const write = ready(local.id)
      .then(() => createAlertRule(draft))
      .then((stored) => {
        // 写期间被删掉了:本地不留,但后端刚建的那条必须删,否则它会继续触发
        // 一条界面上已经不存在的规则。
        if (!get().rules.some((r) => r.id === local.id)) {
          void deleteAlertRule(stored.id).catch(() => {});
          return;
        }
        replaceRule(stored, local.id, set, get);
        set({ syncError: null });
      })
      .catch(() => set({ syncError: WRITE_ERROR }));

    track(local.id, write);
    return local;
  },

  updateRule: (id, patch) => {
    const next = get().rules.map((r) => (r.id === id ? { ...r, ...patch } : r));
    set({ rules: next, syncError: null });
    persist(next);

    const updated = next.find((r) => r.id === id);
    if (!updated) return;

    const write = ready(id).then(() => pushRule(updated, set, get));
    track(id, write);
  },

  deleteRule: (id) => {
    const target = get().rules.find((r) => r.id === id);
    const next = get().rules.filter((r) => r.id !== id);
    set({ rules: next, syncError: null });
    persist(next);

    // 后端本来就没有的规则,删掉是纯粹的本地操作 —— 发 DELETE 只会换回一个
    // 404 和一条「后端删除失败」的假警报。
    if (!target || target.localOnly) return;

    const write = ready(id)
      .then(() => deleteAlertRule(id))
      .catch(() => {
        set({ syncError: "后端删除失败 —— 该规则在后端仍在运行" });
      });
    track(id, write);
  },

  toggleRule: (id) => {
    const current = get().rules.find((r) => r.id === id);
    if (current) get().updateRule(id, { enabled: !current.enabled });
  },

  syncFromBackend: async () => {
    if (_syncInFlight) return _syncInFlight;

    const run = (async () => {
      // 在任何 await 之前取快照:迁移只处理「进页面时已存在」的规则。同步期间
      // 新建的规则由 addRule 自己 POST,不会再被当成待迁移的旧规则上传一遍。
      const atStart = get().rules;
      try {
        const data = await syncAlertRules();

        if (data?.seeded !== true) {
          // 一次性迁移。本地是空列表也照样发 —— seed 同时打上「已迁移」标记;
          // 不打这个标记的话,用户清空所有规则后会被再次「迁移」回来。
          // localOnly 的规则不参与:后端还没有它们,由各自的 POST 负责。
          const legacy = atStart.filter((r) => !r.localOnly);
          const seeded = await seedAlertRules(legacy);

          // 后端逐条校验,不合格的旧规则会被跳过(最典型:price/indicator
          // 规则没有 symbol,新 schema 要求必填)。跳过就跳过,但不能让它们
          // 从界面上消失 —— 那是用户写的规则,得留在列表里等他改。
          const skipped = seeded?.skipped ?? [];
          const rejected = legacy.filter((r) =>
            skipped.some((s) => s.id === r.id),
          );
          if (rejected.length > 0) {
            adopt(
              seeded?.rules,
              set,
              get,
              rejected,
              // 不带条数:这句要能被 tr() 查到(en 表按整串作键),而条数在
              // 界面上已经由每条规则自己的「未同步」标记说清楚了。
              "有旧规则不满足新格式,未迁移 —— 请逐条编辑后保存",
            );
          } else {
            adopt(seeded?.rules, set, get);
          }
          return;
        }

        // 已迁过 → 后端为真相源。本地有而后端没有的,是「删了没删掉」的残留,
        // 必须跟着消失,否则界面会显示一条后端根本不会触发的规则。唯一例外是
        // 写失败的规则:那是用户输入,不能因为一次断线就消失。
        adopt(data.rules, set, get);
      } catch {
        set({ syncError: READ_ERROR });
      }
    })();

    _syncInFlight = run;
    try {
      await run;
    } finally {
      _syncInFlight = null;
    }
  },
}));

/**
 * 采纳后端列表,但保住「后端还没有」的本地规则。
 *
 * 两类:写失败/正在写的(localOnly),以及迁移时被后端拒收的旧规则 —— 后者
 * 也标成 localOnly,因为它确实不在后端,界面上要如实显示「不会触发」,等用户
 * 改好再保存才会真正上行。
 */
function adopt(
  backend: AlertRule[] | undefined,
  set: (partial: Partial<AlertsState>) => void,
  get: () => AlertsState,
  extraLocal: StoredAlertRule[] = [],
  syncError: string | null = null,
): void {
  if (!backend) return;
  const unsynced = [
    ...get().rules.filter((r) => r.localOnly),
    ...extraLocal.map((r) => ({ ...r, localOnly: true })),
  ];
  const seen = new Set(backend.map((r) => r.id));
  const kept = unsynced.filter((r) => !seen.has(r.id));
  const rules = [...backend, ...kept];
  set({ rules, syncError });
  persist(rules);
}

/**
 * 把一条完整规则推到后端。
 *
 * PUT 是整条替换(后端不合并),所以发的是整条规则而不是补丁。404 说明后端
 * 没有这条 —— 它只存在于本地 —— 于是退回 POST,由后端分配 id。
 */
async function pushRule(
  rule: StoredAlertRule,
  set: (partial: Partial<AlertsState>) => void,
  get: () => AlertsState,
): Promise<void> {
  const { id, localOnly: _localOnly, ...draft } = rule;
  const body = draft as AlertRuleDraft;
  try {
    const stored = await updateAlertRule(id, body);
    replaceRule(stored, id, set, get);
    set({ syncError: null });
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) {
      set({ syncError: WRITE_ERROR });
      return;
    }
    try {
      // 不带本地 id 发给 POST:后端已有一份用同一 id 的规则时,INSERT OR
      // REPLACE 会把它覆盖掉,所以换一个新 id 反而更安全。
      const stored = await createAlertRule(body);
      replaceRule(stored, id, set, get);
      set({ syncError: null });
    } catch {
      set({ syncError: WRITE_ERROR });
    }
  }
}

function replaceRule(
  stored: StoredAlertRule,
  previousId: string,
  set: (partial: Partial<AlertsState>) => void,
  get: () => AlertsState,
): void {
  const rules = get().rules.map((r) => (r.id === previousId ? stored : r));
  set({ rules });
  persist(rules);
}
