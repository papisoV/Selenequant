import type { Locale } from "./types"

/**
 * 逻辑值 → 展示文案映射表 —— 处理「中文当逻辑值」的一类(见
 * `scripts/i18n-scan-risky.mjs` 的 A 类清单)。
 *
 * 为什么需要它:项目里有一批中文串是**数据契约值**,不是展示文案。例如
 * `adaptPosition()` 把后端 `side: "buy"` 映射成前端的 `"多"` 存进 `PosRow.side`,
 * 随后 `OPEN_POS.find(p => p.side === "多")` 又拿它做查找键
 * (`bottom-drawer.tsx:87/418`)。这类值:
 *   - **不能**包 `tr()`:包了就是 `"Long" === "多"` → 恒 false,逻辑静默失效
 *   - **也不能**不处理:直接渲染就把中文漏进 en 界面
 *
 * 解法:逻辑照旧用中文值,只在**渲染那一刻**过一层 `tl()`(translate logic
 * value)。与 `translateUiText` 分离的理由 —— 两者的输入域不同,混在一张表里
 * 会让"某个中文串到底能不能包 tr()"这个判断变得不可检。
 *
 * ⚠️ 新增此类值时:先跑 `node scripts/i18n-scan-risky.mjs` 拿到 A 类清单,
 * 逐个确认是协议值后**登记到这里**,不要在组件里手写 `locale === "en" ? ... : ...`。
 */

/** 每个 locale 的「逻辑值 → 展示文案」表;zh-CN 留空(逻辑值本身即中文展示)。 */
const LOGIC_DISPLAY: Record<Locale, Record<string, string>> = {
  en: {
    // 持仓/委托方向(bottom-drawer.tsx / paper-page.tsx / trd-page.tsx)
    多: "Long",
    空: "Short",
    买: "Buy",
    卖: "Sell",
    // 事件类别(bottom-drawer.tsx 抽屉筛选)
    信号: "Signal",
    告警: "Alert",
    爆仓: "Liquidation",
    新闻: "News",
    // 交易/决策状态
    演示: "Demo",
    已写入: "Written",
    // landing 判决(the-loop.tsx)
    翻案: "Reversed",
    "veto 不成立": "Veto Not Upheld",
    // 情绪/强度档(serenity-panel.tsx)—— evidence_label 是后端协议值(_evidence_label
    // 由 evidence_strength 阈值产出),组件里 `=== "强"` 拿它上色、`tl()` 只翻展示。
    强: "Strong",
    中: "Medium",
    弱: "Weak",
  },
  "zh-CN": {},
}

/**
 * 把逻辑值译成展示文案。未登记的值原样返回 —— 与 `translateUiText` 同样的
 * 优雅降级语义(zh-CN 下全部原样,因为逻辑值本身就是中文展示)。
 *
 * 典型用法:
 *   const tl = useLogicText()
 *   <span>{tl(row.side)}</span>   // 逻辑仍用 row.side === "多" 比较
 */
export function translateLogicValue(locale: Locale, value: string): string {
  return LOGIC_DISPLAY[locale]?.[value] ?? value
}

/** 中文字符检测 —— 供调用方判断某个值是否值得过 tl()。 */
const CJK_RE = /[一-鿿㐀-䶿]/

/** 该值是否为「需要映射的中文逻辑值」。 */
export function isChineseLogicValue(value: string): boolean {
  return CJK_RE.test(value)
}

/** 供测试遍历:已登记的 en 逻辑值。 */
export const LOGIC_DISPLAY_EN_SOURCES: readonly string[] = Object.keys(
  LOGIC_DISPLAY.en,
)
