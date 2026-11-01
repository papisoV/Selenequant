"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import {
  putTieredConfig,
  TIERED_TRAILING_DEFAULTS,
  type TieredTrailingConfig,
} from "@/lib/api/settings";
import { LlmSettingsPanel } from "@/components/system/llm-settings-panel";
import { ExchangeSettingsPanel } from "@/components/system/exchange-settings-panel";
import { NetworkSettingsPanel } from "@/components/system/network-settings-panel";
import { PaHeartbeatPanel } from "@/components/system/pa-heartbeat-panel";
import { LLMBudgetPanel } from "@/components/system/llm-budget-panel";
import { ResearchLoopsPanel } from "@/components/system/research-loops-panel";
import { AccountPanel } from "@/components/system/account-panel";
import { SettingsNav, type SettingsNavItem } from "@/components/system/settings-nav";
import {
  formatFractionAsPercent,
  fractionToPercent,
  percentToFraction,
} from "@/lib/format";
import {
  DEDICATED_PANEL_GROUPS,
  READONLY_GROUP_ORDER,
  filterReadonlyEntries,
  humanizeSettingKey,
  settingsGroupLabel,
  sortGroupKeys,
} from "@/lib/settings-groups";
import { useUiText } from "@/components/i18n/use-ui-text"

interface SettingsGroup {
  group: string;
  items: [string, string][];
}

const DEBUG_FLAG_KEY = "selenespace-debug";

/** 面板锚点 id —— nav chip 与 `scrollIntoView` 共用,集中一处避免字符串漂移。 */
const SECTION_IDS = {
  network: "set-network",
  exchange: "set-exchange",
  llm: "set-llm",
  account: "set-account",
  tiered: "set-tiered",
  paHeartbeat: "set-pa-heartbeat",
  llmBudget: "set-llm-budget",
  researchLoops: "set-research-loops",
  advanced: "set-advanced",
  debug: "set-debug",
} as const;

const groupSectionId = (key: string) => `set-group-${key}`;

/**
 * 导航 chip 的定义,顺序即页面顺序。
 *
 * `aliases` 是**搜索关键词**,不是展示文案:用户搜 "proxy"/"token"/"binance"
 * 时各面板标题里并没有这些词。中文关键词大多省略 —— 标题本身已含
 * (搜"代理"直接命中"代理"),加进来只会让两张表各自漂移。
 */
interface PanelDef {
  id: string;
  /** 中文源串,渲染处 `tr()`。 */
  label: string;
  aliases: string[];
}

const NETWORK_PANEL: PanelDef = {
  id: SECTION_IDS.network, label: "代理", aliases: ["network", "proxy", "http", "端口"],
};
const EXCHANGE_PANEL: PanelDef = {
  id: SECTION_IDS.exchange, label: "交易所", aliases: ["exchange", "api key", "binance", "okx", "testnet", "测试网"],
};
const LLM_PANEL: PanelDef = {
  id: SECTION_IDS.llm, label: "LLM", aliases: ["agent", "model", "provider", "base url"],
};
const ACCOUNT_PANEL: PanelDef = {
  id: SECTION_IDS.account, label: "账户", aliases: ["account", "注销", "导出", "delete", "export"],
};
const TIERED_PANEL: PanelDef = {
  id: SECTION_IDS.tiered, label: "四档止盈", aliases: ["tiered", "止盈", "止损", "回撤", "stop"],
};
const PA_HEARTBEAT_PANEL: PanelDef = {
  id: SECTION_IDS.paHeartbeat, label: "PA 心跳", aliases: ["heartbeat", "诊断", "无人值守"],
};
const LLM_BUDGET_PANEL: PanelDef = {
  id: SECTION_IDS.llmBudget, label: "LLM 用量", aliases: ["budget", "token", "用量", "上限"],
};
const RESEARCH_LOOPS_PANEL: PanelDef = {
  id: SECTION_IDS.researchLoops, label: "研究循环", aliases: ["research", "worker", "续研", "无人值守", "sensor"],
};
const DEBUG_PANEL: PanelDef = {
  id: SECTION_IDS.debug, label: "调试", aliases: ["debug", "浮窗"],
};

const PANEL_SECTIONS = [
  NETWORK_PANEL,
  EXCHANGE_PANEL,
  LLM_PANEL,
  ACCOUNT_PANEL,
  TIERED_PANEL,
  PA_HEARTBEAT_PANEL,
  LLM_BUDGET_PANEL,
  RESEARCH_LOOPS_PANEL,
] as const;

/** 分区锚点属性 —— `data-settings-section` 供 IntersectionObserver 选取。 */
function sectionAnchor(id: string, visible: boolean) {
  return {
    id,
    "data-settings-section": "",
    "data-filtered": visible ? "false" : "true",
  } as const;
}

function DebugToggle() {
  const tr = useUiText()

  const [on, setOn] = useState(false);
  useEffect(() => {
    try {
      setOn(localStorage.getItem(DEBUG_FLAG_KEY) === "1");
    } catch {
      setOn(false);
    }
  }, []);
  const toggle = () => {
    const next = !on;
    setOn(next);
    try {
      if (next) localStorage.setItem(DEBUG_FLAG_KEY, "1");
      else localStorage.removeItem(DEBUG_FLAG_KEY);
    } catch {
      // ignore
    }
  };
  return (
    <div className="set-row flex justify-between items-center text-[11px] py-1 border-b border-border-soft">
      <span className="set-key text-ink-3">{tr("Realtime Debug 浮窗")}</span>
      <button
        onClick={toggle}
        className={`set-btn set-val font-mono px-2 rounded border ${
          on
            ? "border-gold text-gold bg-surface-3"
            : "border-border-soft text-ink-4 bg-surface-2"
        }`}
        title={tr("显示 market/paper/live 三路 WebSocket 连接状态浮窗")}
      >
        {on ? "ON" : "OFF"}
      </button>
    </div>
  );
}

interface TieredFieldSpec {
  key: keyof TieredTrailingConfig;
  label: string;
  /** 存储单位:比例字段是小数(0.035),秒字段就是秒。 */
  min: number;
  max: number;
  step: number;
  /**
   * `"pct"` = 存储小数比例、界面百分比。滑块/数字框在工作在百分比空间,
   * 提交前换算回比例 —— 存储契约不变,后端仍收到 0.035。
   */
  unit: "pct" | "seconds";
}

const TIERED_FIELDS: TieredFieldSpec[] = [
  { key: "tiered_hard_stop_pct", label: "硬止损比例", min: 0.005, max: 0.1, step: 0.005, unit: "pct" },
  { key: "tiered_low_tier_threshold", label: "T1 触发阈值", min: 0.005, max: 0.05, step: 0.0025, unit: "pct" },
  { key: "tiered_first_tier_threshold", label: "T2 触发阈值", min: 0.01, max: 0.1, step: 0.005, unit: "pct" },
  { key: "tiered_second_tier_threshold", label: "T3 触发阈值", min: 0.02, max: 0.2, step: 0.01, unit: "pct" },
  { key: "tiered_low_tier_drop_pct", label: "T1 回撤比例", min: 0.1, max: 0.9, step: 0.05, unit: "pct" },
  { key: "tiered_first_tier_drop_pct", label: "T2 回撤比例", min: 0.1, max: 0.7, step: 0.05, unit: "pct" },
  { key: "tiered_second_tier_drop_pct", label: "T3 回撤比例", min: 0.05, max: 0.5, step: 0.05, unit: "pct" },
  { key: "tiered_min_emit_interval_seconds", label: "最小emit间隔(秒)", min: 5, max: 300, step: 5, unit: "seconds" },
];

function TieredTrailingForm() {
  const tr = useUiText()

  const { data } = useApi<{ risk?: Record<string, number> } | null>("/api/v1/settings");
  const [local, setLocal] = useState<TieredTrailingConfig>(TIERED_TRAILING_DEFAULTS);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  useEffect(() => {
    const risk = data?.risk;
    if (!risk) return;
    setLocal({
      tiered_hard_stop_pct: Number(risk.tiered_hard_stop_pct ?? 0.02),
      tiered_low_tier_threshold: Number(risk.tiered_low_tier_threshold ?? 0.015),
      tiered_first_tier_threshold: Number(risk.tiered_first_tier_threshold ?? 0.03),
      tiered_second_tier_threshold: Number(risk.tiered_second_tier_threshold ?? 0.06),
      tiered_low_tier_drop_pct: Number(risk.tiered_low_tier_drop_pct ?? 0.5),
      tiered_first_tier_drop_pct: Number(risk.tiered_first_tier_drop_pct ?? 0.3),
      tiered_second_tier_drop_pct: Number(risk.tiered_second_tier_drop_pct ?? 0.2),
      tiered_min_emit_interval_seconds: Number(risk.tiered_min_emit_interval_seconds ?? 30),
    });
  }, [data]);

  const handleFieldChange = (key: keyof TieredTrailingConfig, next: number) => {
    setLocal((prev) => ({ ...prev, [key]: next }));
  };

  const handleSave = async () => {
    setSaving(true);
    setSavedMsg(null);
    try {
      await putTieredConfig(local);
      setSavedMsg("已保存");
    } catch {
      setSavedMsg("保存失败");
    } finally {
      setSaving(false);
      setTimeout(() => setSavedMsg(null), 2000);
    }
  };

  const handleReset = () => {
    setLocal(TIERED_TRAILING_DEFAULTS);
  };

  return (
    <div className="set-group rounded border border-border-soft bg-surface-2 p-2">
      <h2 className="set-group-title text-[12px] font-medium text-gold mb-1.5">
        {tr("四档止盈 (Tiered Trailing)")}
      </h2>
      <div className="space-y-1.5">
        {TIERED_FIELDS.map((spec) => {
          const value = local[spec.key];
          // 百分比字段在「显示 / 滑块 / 数字框」三处一起换成百分比空间,
          // 否则同一行会同时出现 0.035 和 3.5%。存储值仍是小数比例。
          const isPct = spec.unit === "pct";
          const uiValue = isPct ? fractionToPercent(value, 4) : value;
          const uiMin = isPct ? fractionToPercent(spec.min, 4) : spec.min;
          const uiMax = isPct ? fractionToPercent(spec.max, 4) : spec.max;
          const uiStep = isPct ? fractionToPercent(spec.step, 4) : spec.step;
          const commit = (next: number) =>
            handleFieldChange(spec.key, isPct ? percentToFraction(next) : next);
          return (
            <div key={spec.key} className="space-y-0.5">
              <div className="flex justify-between items-center text-[11px]">
                <span className="text-ink-3">{tr(spec.label)}</span>
                <span className="text-ink font-mono">
                  {isPct ? formatFractionAsPercent(value) : value}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <input
                  type="range"
                  min={uiMin}
                  max={uiMax}
                  step={uiStep}
                  value={uiValue}
                  onChange={(e) => commit(parseFloat(e.target.value))}
                  aria-label={tr("{label}（滑块）", { label: tr(spec.label) })}
                  className="set-range flex-1 accent-gold"
                />
                <input
                  type="number"
                  min={uiMin}
                  max={uiMax}
                  step={uiStep}
                  value={uiValue}
                  onChange={(e) => commit(parseFloat(e.target.value) || 0)}
                  aria-label={tr(spec.label)}
                  className="set-input w-16 text-center"
                />
                <span className="text-[11px] text-ink-4 w-3" aria-hidden="true">
                  {isPct ? "%" : ""}
                </span>
              </div>
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-1.5 mt-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="set-btn flex-1 rounded border border-gold bg-surface-3 px-2 text-[11px] text-gold disabled:opacity-50"
        >
          {saving ? tr("保存中...") : tr("保存")}
        </button>
        <button
          type="button"
          onClick={handleReset}
          className="set-btn rounded border border-border-soft bg-surface-3 px-2 text-[11px] text-ink-4"
        >
          {tr("重置默认")}
        </button>
        {savedMsg && (
          <span className="text-[11px] text-ink-3">{tr(savedMsg)}</span>
        )}
      </div>
    </div>
  );
}

// 设置只读行是 <span> 文本，塞不进对象。后端 risk_alerts.rules / dex.* / skills.enabled
// 都是嵌套对象，String() 会落成 "[object Object]"（5 处），逐层摊平成 k=v 可读串。
function formatScalar(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) {
    return v.length ? v.map(formatScalar).join(", ") : "—";
  }
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    return entries.length ? entries.map(([k, vv]) => `${k}=${formatScalar(vv)}`).join(" · ") : "—";
  }
  return String(v);
}

/** 大小写不敏感子串匹配;空查询恒真。 */
function matchesQuery(haystack: string, needle: string): boolean {
  return needle === "" || haystack.toLowerCase().includes(needle);
}

// 设计稿 L4820 renderPageSet — 设置
// 后端 /api/v1/settings 返回结构未知；空时展示空状态
export function SetPage() {
  const tr = useUiText()

  const { data } = useApi<Record<string, unknown> | null>("/api/v1/settings");

  // llm 段由 <LlmSettingsPanel /> 专门渲染,exchange 段由 <ExchangeSettingsPanel />
  // 专门渲染(否则 api_key_hint 等字段会显示成裸英文 key,用户读不懂)
  // pa_heartbeat 同理:裸 enabled/daily_max 行既看不懂也改不了
  // llm_budget 是单个 token 数字,但它的含义(0=不限、只管无人值守)必须
  // 和实测用量并排才读得懂 —— 由 <LLMBudgetPanel /> 专门渲染
  const groups: SettingsGroup[] = useMemo(() => {
    const skip = new Set(DEDICATED_PANEL_GROUPS);
    if (!data || typeof data !== "object") return [];
    return sortGroupKeys(Object.keys(data).filter((group) => !skip.has(group))).map(
      (group) => {
        const val = data[group];
        // 段内已有专门编辑器的键不再进只读区(risk 段:八个 tiered_* 由
        // 「四档止盈」表单编辑),否则同一个值一处可改、一处只读。
        const entries: [string, string][] = (val && typeof val === "object")
          ? Object.entries(val as Record<string, unknown>).map(
              ([k, v]) => [k, formatScalar(v)] as [string, string],
            )
          : [[group, formatScalar(val)] as [string, string]];
        return { group, items: filterReadonlyEntries(group, entries) };
      },
    );
  }, [data]);

  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const searching = query.trim() !== "";

  /** 只读组命中:组名命中 → 整组可见;否则只留命中的行。 */
  const matchGroup = useCallback(
    (g: SettingsGroup) => {
      const label = settingsGroupLabel(g.group);
      if (matchesQuery(label, needle) || matchesQuery(tr(label), needle)) return g.items;
      return g.items.filter(
        ([k, v]) =>
          matchesQuery(k, needle) ||
          matchesQuery(humanizeSettingKey(k), needle) ||
          matchesQuery(v, needle),
      );
    },
    [needle, tr],
  );

  const panelVisible = useCallback(
    (p: PanelDef) =>
      !searching ||
      matchesQuery(p.label, needle) ||
      matchesQuery(tr(p.label), needle) ||
      p.aliases.some((a) => matchesQuery(a, needle)),
    [needle, searching, tr],
  );

  const visibleGroups = groups
    .map((g) => ({ group: g.group, items: matchGroup(g) }))
    .filter((g) => g.items.length > 0);
  const nothingMatched =
    searching &&
    visibleGroups.length === 0 &&
    !PANEL_SECTIONS.some(panelVisible) &&
    !panelVisible(DEBUG_PANEL);

  /**
   * 高级配置折叠态(默认收起)。搜索命中只读组时**强制展开** —— 否则用户
   * 在搜索结果里看到一片空白,会以为设置项不存在。
   */
  const [advancedOpen, setAdvancedOpen] = useState(false);

  /**
   * 跳转:先清搜索(被过滤掉的面板 scroll 不到)。只读组在折叠区内,需先展开
   * 才有布局,故记下待跳目标,等清搜索/展开后的那次提交再滚动。
   *
   * 用自增序号而非"用完就清空":清空要写 state,而 effect 里同步 setState 会
   * 连带触发一轮渲染;更实际的是,清空后连点同一个 chip 不会再滚。
   */
  const [pendingJump, setPendingJump] = useState<{ id: string; seq: number } | null>(null);
  const jumpSeq = useRef(0);
  const gridRef = useRef<HTMLDivElement | null>(null);
  /**
   * 程序化滚动后的 scrollTop。观察器一旦发现用户自己滚了(scrollTop 变了)
   * 就解除锁定。为什么要锁:后面的组滚不到顶会被夹在内容末尾,此时"占比
   * 最大"的永远是更靠上的那一个 —— 点「哨兵」,高亮却跳到「风控告警」。
   */
  const scrollLock = useRef<number | null>(null);

  const jump = (id: string) => {
    setQuery("");
    if (id.startsWith("set-group-")) setAdvancedOpen(true);
    jumpSeq.current += 1;
    setPendingJump({ id, seq: jumpSeq.current });
    // 用户点了哪个 chip,高亮立刻跟到哪个,不等观察器投票。
    setActiveId(id);
  };

  // 滚动必须等到清搜索 / 展开之后的提交:被过滤掉的面板此刻才重新挂载,
  // 折叠区此刻才有布局 —— 在点击当时同步滚动会滚到 null。
  useLayoutEffect(() => {
    if (!pendingJump) return;
    document.getElementById(pendingJump.id)?.scrollIntoView?.({ block: "start" });
    scrollLock.current = gridRef.current?.scrollTop ?? 0;
  }, [pendingJump]);

  // 视口占比过阈值的分区 → chip 高亮。被过滤掉的分区不参与。
  const [activeId, setActiveId] = useState<string>(SECTION_IDS.network);
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const root = gridRef.current;
    if (!root) return;
    const ratios = new Map<string, number>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const el = e.target as HTMLElement;
          if (el.dataset.filtered === "true" || el.offsetParent === null) {
            ratios.delete(el.id);
            continue;
          }
          ratios.set(el.id, e.isIntersecting ? e.intersectionRatio : 0);
        }
        // 节点被条件渲染卸载后仍会留在 Map 里,其陈旧占比会压过真正的当前分区。
        for (const id of ratios.keys()) {
          if (!document.getElementById(id)) ratios.delete(id);
        }
        // 程序化跳转后,用户还没自己滚 —— 此时"占比最大"的往往是滚不动的那一个
        // (末段分区滚不到顶会被夹住),不能让观察器把刚点亮的 chip 抢走。
        if (scrollLock.current !== null) {
          if (root.scrollTop === scrollLock.current) return;
          scrollLock.current = null;
        }
        // 同屏多个分区过阈值时取文档序最靠上的一个:Map 的插入序会随重新
        // observe 变化,不可作判据。
        let bestId = "";
        let bestRatio = 0.15;
        for (const el of Array.from(root.querySelectorAll<HTMLElement>("[data-settings-section]"))) {
          const ratio = ratios.get(el.id) ?? 0;
          if (ratio > bestRatio) {
            bestRatio = ratio;
            bestId = el.id;
          }
        }
        if (bestId) setActiveId(bestId);
      },
      { root, threshold: [0.15] },
    );
    root.querySelectorAll<HTMLElement>("[data-settings-section]").forEach((el) => io.observe(el));
    return () => io.disconnect();
    // 过滤是条件渲染,分区节点会整体卸载重挂,必须重新 observe。
  }, [searching, advancedOpen]);

  const navItems: SettingsNavItem[] = useMemo(
    () => [
      ...PANEL_SECTIONS.map((p) => ({ id: p.id, label: p.label })),
      ...READONLY_GROUP_ORDER.map((key) => ({
        id: groupSectionId(key),
        label: settingsGroupLabel(key),
      })),
      { id: DEBUG_PANEL.id, label: DEBUG_PANEL.label },
    ],
    [],
  );

  const advancedBody = (
    <div className="mt-1.5 grid gap-2 min-[900px]:grid-cols-2">
      {visibleGroups.map((g) => (
        <section
          key={g.group}
          {...sectionAnchor(groupSectionId(g.group), true)}
          className="min-w-0 rounded border border-border-soft bg-surface-3/40 p-1.5"
        >
          <h2 className="set-group-title text-[12px] font-medium text-gold mb-1.5">
            {tr(settingsGroupLabel(g.group))}
          </h2>
          {g.items.map(([k, v]) => (
            <div
              key={k}
              className="set-row flex justify-between items-center gap-2 text-[11px] py-1 border-b border-border-soft"
            >
              <span className="set-key text-ink-3 shrink-0">{humanizeSettingKey(k)}</span>
              <span className="set-val text-ink font-mono text-right break-all">{v}</span>
            </div>
          ))}
        </section>
      ))}
    </div>
  );

  return (
    <div
      className="v17-page-grid grid gap-2 grid-cols-1 min-[900px]:grid-cols-2"
      ref={gridRef}
    >
      {/* 视觉隐藏但留在 a11y 树里:页面此前零 heading,读屏用户无法按标题跳转。 */}
      <h1 className="sr-only">{tr("设置")}</h1>

      <SettingsNav
        items={navItems}
        activeId={activeId}
        onJump={jump}
        query={query}
        onQueryChange={setQuery}
      />

      {searching && nothingMatched && (
        <div className="min-w-0 min-[900px]:col-span-2 text-center text-ink-4 text-[11px] py-3">
          {tr("无匹配设置项")}
        </div>
      )}
      {!searching && groups.length === 0 && data !== null && (
        <div className="min-w-0 min-[900px]:col-span-2 text-center text-ink-4 text-[11px] py-3">
          {tr("暂无数据")}
        </div>
      )}

      {/* 代理排第一:它是「数据拉不到」时的第一嫌疑,原先埋在列表第 13 位(76% 屏深处)。
          必须放在只读组之前 —— 否则后端多返回一个只读段,它就被顶下去一位。 */}
      {panelVisible(NETWORK_PANEL) && (
        <section
          {...sectionAnchor(NETWORK_PANEL.id, true)}
          className="min-w-0 min-[900px]:col-span-2"
        >
          <NetworkSettingsPanel />
        </section>
      )}
      {panelVisible(EXCHANGE_PANEL) && (
        <section {...sectionAnchor(EXCHANGE_PANEL.id, true)} className="min-w-0">
          <ExchangeSettingsPanel />
        </section>
      )}
      {panelVisible(LLM_PANEL) && (
        <section {...sectionAnchor(LLM_PANEL.id, true)} className="min-w-0">
          <LlmSettingsPanel />
        </section>
      )}
      {/* K46(判定表 r5):注销状态=账户设置内状态行,me-deletion-status 并入 AccountPanel(已含注销请求/状态/取消) */}
      {panelVisible(ACCOUNT_PANEL) && (
        <section
          {...sectionAnchor(ACCOUNT_PANEL.id, true)}
          className="min-w-0 min-[900px]:col-span-2"
        >
          <AccountPanel />
        </section>
      )}
      {panelVisible(TIERED_PANEL) && (
        <section {...sectionAnchor(TIERED_PANEL.id, true)} className="min-w-0">
          <TieredTrailingForm />
        </section>
      )}
      {panelVisible(PA_HEARTBEAT_PANEL) && (
        <section {...sectionAnchor(PA_HEARTBEAT_PANEL.id, true)} className="min-w-0">
          <PaHeartbeatPanel />
        </section>
      )}
      {panelVisible(LLM_BUDGET_PANEL) && (
        <section {...sectionAnchor(LLM_BUDGET_PANEL.id, true)} className="min-w-0">
          <LLMBudgetPanel />
        </section>
      )}
      {panelVisible(RESEARCH_LOOPS_PANEL) && (
        <section {...sectionAnchor(RESEARCH_LOOPS_PANEL.id, true)} className="min-w-0">
          <ResearchLoopsPanel />
        </section>
      )}

      {/* 只读段:后端返回什么就显示什么。默认折叠 —— 平铺时常用项要滚三屏才看得全。 */}
      {searching
        ? visibleGroups.length > 0 && (
            <section
              {...sectionAnchor(SECTION_IDS.advanced, true)}
              className="min-w-0 min-[900px]:col-span-2 set-group rounded border border-border-soft bg-surface-2 p-2"
            >
              <h2 className="set-group-title text-[12px] font-medium text-gold">
                {tr("高级配置（只读）")}
              </h2>
              {advancedBody}
            </section>
          )
        : groups.length > 0 && (
            <details
              {...sectionAnchor(SECTION_IDS.advanced, true)}
              open={advancedOpen}
              onToggle={(e) => setAdvancedOpen(e.currentTarget.open)}
              className="min-w-0 min-[900px]:col-span-2 set-group rounded border border-border-soft bg-surface-2 p-2"
            >
              <summary className="set-group-title text-[12px] font-medium text-gold cursor-pointer select-none">
                {tr("高级配置（只读）")}
              </summary>
              {advancedBody}
            </details>
          )}

      {panelVisible(DEBUG_PANEL) && (
        <section {...sectionAnchor(DEBUG_PANEL.id, true)} className="min-w-0">
          <div className="set-group rounded border border-border-soft bg-surface-2 p-2">
            <h2 className="set-group-title text-[12px] font-medium text-gold mb-1.5">
              {tr("调试")}
            </h2>
            <DebugToggle />
          </div>
        </section>
      )}
    </div>
  );
}
