/**
 * 设置页分组元数据 —— 后端 section key 与「人读组名」的唯一映射。
 *
 * 为什么单独成文件:`set-page.tsx` 既是渲染器又是分组表,改一行布局就要翻
 * 整页;而这张表被「组名导航 / 搜索 / 折叠」三处共用,分散在两处必然漂移。
 *
 * 键取自 `settings_service._defaults()`(15 段)+ `get_settings()` 注入的
 * `security`。**新增后端 section 时这里必须补一行**,否则该段会以
 * `humanizeSettingKey()` 兜底(英文 key 首字母大写),即回到"裸 JSON key"的
 * 老毛病 —— 单元测试 `settings-groups.test.ts` 按后端默认段清单逐条断言。
 */

/** 只读分组:后端返回什么就显示什么,当前无专门编辑器。 */
export const GROUP_LABELS: Record<string, string> = {
  general: "通用",
  risk: "风控",
  risk_alerts: "风控告警",
  grid: "网格",
  dex: "DEX",
  sentinel: "哨兵",
  skills: "技能",
  ai: "AI",
  favorites: "自选",
  workspace_layouts: "工作台布局",
  security: "安全与密钥",
};

/** 高级配置内的排列顺序(与后端 `_defaults()` 的书写顺序一致)。 */
export const READONLY_GROUP_ORDER: string[] = [
  "general",
  "risk",
  "risk_alerts",
  "grid",
  "dex",
  "sentinel",
  "skills",
  "ai",
  "favorites",
  "workspace_layouts",
  "security",
];

/** 折叠区内的组(其余组若有专门面板,由调用方另行渲染)。 */
export const ADVANCED_GROUPS = new Set(Object.keys(GROUP_LABELS));

/**
 * 有专门编辑器、**不**进高级只读区的后端段。
 *
 * 与 `GROUP_LABELS` 分工:那些段由 `<NetworkSettingsPanel />` 等组件逐字段渲染,
 * 若同时出现在只读区,同一个值会在页面上出现两次(一次可改、一次只读),
 * 用户分不清哪个说了算。`settings-groups.test.ts` 用它来断言
 * 「后端每一段要么有分组名,要么有专门面板」—— 两个集合都不在 = 裸 key。
 */
export const DEDICATED_PANEL_GROUPS: string[] = [
  "exchange",
  "llm",
  "network",
  "pa_heartbeat",
  "llm_budget",
  "mission_worker",
  "research_autorenew",
  "research_sensor",
];

/**
 * 有专门编辑器、但**整段**仍要留在只读区的后端键(按段 → 键)。
 *
 * `risk` 段不是一个粒度:`tiered_*` 八个键由「四档止盈」表单逐字段编辑,
 * 其余四个字符串键(`max_position_pct` 等)至今没有可编辑入口。整段跳过会
 * 让后者从页面上消失,所以这里只滤掉有编辑器的键 —— 同一个值不再一处可改
 * 一处只读,用户也不会被迫猜哪个说了算。
 */
export const DEDICATED_PANEL_KEYS: Record<string, readonly string[]> = {
  risk: [
    "tiered_hard_stop_pct",
    "tiered_low_tier_threshold",
    "tiered_first_tier_threshold",
    "tiered_second_tier_threshold",
    "tiered_low_tier_drop_pct",
    "tiered_first_tier_drop_pct",
    "tiered_second_tier_drop_pct",
    "tiered_min_emit_interval_seconds",
  ],
};

/**
 * 只读区该显示的 `[键, 值]` 行:滤掉 `group` 段里已有专门编辑器的键。
 * 未登记的段原样返回。
 */
export function filterReadonlyEntries(
  group: string,
  entries: [string, string][],
): [string, string][] {
  const editable = DEDICATED_PANEL_KEYS[group];
  if (!editable || editable.length === 0) return entries;
  const skip = new Set(editable);
  return entries.filter(([key]) => !skip.has(key));
}

/**
 * snake_case / camelCase → 空格分隔首字母大写。
 *
 * 只作**兜底**:后端多返回一个本表没登记的段时,至少别把 `mission_worker`
 * 原样怼给用户。有译文的值一律走 `tr()`,不经过这里。
 */
export function humanizeSettingKey(key: string): string {
  return key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** 组名(中文源串,调用方负责 `tr()`)。 */
export function settingsGroupLabel(key: string): string {
  return GROUP_LABELS[key] ?? humanizeSettingKey(key);
}

/**
 * 把后端返回的分组按 `READONLY_GROUP_ORDER` 排序;顺序表里没有的(后端新增段)
 * 排在已知组之后、保持后端原始顺序,不让它们把已排好的组挤乱。
 */
export function sortGroupKeys(keys: string[]): string[] {
  const rank = (k: string) => {
    const i = READONLY_GROUP_ORDER.indexOf(k);
    return i === -1 ? READONLY_GROUP_ORDER.length : i;
  };
  return [...keys].sort((a, b) => rank(a) - rank(b));
}
