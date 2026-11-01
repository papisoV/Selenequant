import type { Locale, TranslateParams } from "../types"
import { formatMessage } from "../index"
import { CHROME_TEXT_EN } from "./chrome"
import { DATA_LAYER_TEXT_EN } from "./data-layer"
import { DRAWING_TEXT_EN } from "./drawing"
import { RESEARCH_TEXT_EN } from "./research"
import { RESEARCH_TEXT_EN_B3B } from "./research-b3b"
import { WATCH_TEXT_EN } from "./watch"
import { TRADE_TEXT_EN } from "./trade"
import { STRATEGY_TEXT_EN } from "./strategy"
import { SYSTEM_TEXT_EN } from "./system"
import { LANDING_TEXT_EN } from "./landing"

/**
 * 标识符式文案翻译表 —— 按【中文源串】查表，而非按命名 key。
 *
 * 为什么不用 MessageKey：页面标题(184 条)与导航链名(16 条)定义在
 * `lib/page-manifest.ts` / `lib/navigation-chains.ts`，那两份文件正被
 * `shipan` 副本改动。把它们改成 key 引用 = 立刻冲突。改为在【渲染点】
 * 包一层 `tr(p.title)`，数据文件一个字符不动：
 *   - 零冲突：不动数据层
 *   - 优雅降级：别处新增的页面查不到译文时原样返回源串，不会崩
 * 代价：源串被改写时这里会静默失配 —— 由 `ui-text.test.tsx` 的覆盖锁兜底
 * (遍历 CHAINS/ALL_PAGES 断言每条都有 en 译文，改动即变红)。
 *
 * zh-CN 表刻意留空：源串本身即中文，未命中即原样返回。
 *
 * ── 文件分域（2026-10-16 拆分）────────────────────────────────────
 * 全站约 4,297 条文案，单文件会远超项目「单文件 ≤800 行」规则 → 按域拆分，
 * 各批只改自己的文件（并发副本冲突面最小）。本文件只做**合并**：
 *   chrome      框架层（侧栏/顶栏/错误页）
 *   data-layer  数据文件文案（页面标题/导航链/分类）
 *   drawing     手绘对象树（components/watch/drawing-tools）          ← 本次新增
 *   research    components/research + components/agent  ← B2/B3
 *   watch       components/watch                        ← B4
 *   trade       components/trade                        ← B4
 *   strategy    components/strategy                     ← B5
 *   system      components/system                       ← B5
 *   landing     components/landing                      ← B5
 * 逻辑值映射另表：`lib/i18n/values.ts`（⚠️ A 类，绝不可混入本表）。
 *
 * ⚠️ 同一条源串若同时出现在两个域文件里，后者覆盖前者（对象展开序）。
 * 覆盖锁测试的「陈旧条目」检查按合并后的表跑，故重复不会漏检，但会让
 * 「该条属于哪个域」变得含糊 —— 新增译文时只写所属域的文件。
 */
const UI_TEXT_EN: Record<string, string> = {
  ...CHROME_TEXT_EN,
  ...DATA_LAYER_TEXT_EN,
  ...DRAWING_TEXT_EN,
  ...RESEARCH_TEXT_EN,
  ...RESEARCH_TEXT_EN_B3B,
  ...WATCH_TEXT_EN,
  ...TRADE_TEXT_EN,
  ...STRATEGY_TEXT_EN,
  ...SYSTEM_TEXT_EN,
  ...LANDING_TEXT_EN,
}

/** 各 locale 的源串→译文表；zh-CN 留空（源串即中文，未命中原样返回）。 */
const UI_TEXT: Record<Locale, Record<string, string>> = {
  en: UI_TEXT_EN,
  "zh-CN": {},
}

/**
 * 把数据层/JSX 里的中文源串翻译成目标 locale；查不到时原样返回源串。
 * 纯函数、无 React 依赖 —— 可在 provider 之外（如 global-error.tsx）直接调用。
 * 支持 `{param}` 占位，与 `formatMessage` 同一套语义。
 */
export function translateUiText(
  locale: Locale,
  source: string,
  params?: TranslateParams,
): string {
  const template = UI_TEXT[locale]?.[source] ?? source
  return params ? formatMessage(template, params) : template
}

/** 供覆盖锁测试遍历：en 表覆盖的所有源串。 */
export const UI_TEXT_EN_SOURCES: readonly string[] = Object.keys(UI_TEXT_EN)

/** 供测试/工具按域取表（诊断「这条属于哪个域」用）。 */
export const UI_TEXT_DOMAINS = {
  chrome: CHROME_TEXT_EN,
  "data-layer": DATA_LAYER_TEXT_EN,
  drawing: DRAWING_TEXT_EN,
  research: RESEARCH_TEXT_EN,
  "research-b3b": RESEARCH_TEXT_EN_B3B,
  watch: WATCH_TEXT_EN,
  trade: TRADE_TEXT_EN,
  strategy: STRATEGY_TEXT_EN,
  system: SYSTEM_TEXT_EN,
  landing: LANDING_TEXT_EN,
} as const
