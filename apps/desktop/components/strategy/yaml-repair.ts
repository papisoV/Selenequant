// US-104 AI 自修复环: AI 生成的 YAML 在 apply 前先验证;失败把错误文本追加回
// 同一会话让 AI 修一轮(最多 1 次),双失败把两条错误诚实展示给用户。
// 验证逻辑复用 yaml-editor 的解析器(同一条应用路径,避免双解析器漂移)。
import { tryParseYaml, parsedToPartial } from "./yaml-editor"
import type { StrategyData } from "./types"

export interface YamlValidationResult {
  ok: boolean
  /** 解析/校验错误全文(喂给 AI 修复用);ok=true 时为 null */
  error: string | null
  /** 解析成功后的策略片段 */
  partial?: Partial<StrategyData>
}

const REQUIRED_STRING_FIELDS: Array<{ key: keyof StrategyData; label: string }> = [
  { key: "name", label: "name" },
  { key: "type", label: "type" },
  { key: "interval", label: "interval" },
]

export function validateStrategyYaml(yaml: string): YamlValidationResult {
  if (!yaml.trim()) {
    return { ok: false, error: "YAML 为空" }
  }
  let loaded: Record<string, unknown>
  try {
    loaded = tryParseYaml(yaml)
  } catch (e) {
    return {
      ok: false,
      error: `YAML 解析失败: ${e instanceof Error ? e.message : String(e)}`,
    }
  }
  const partial = parsedToPartial(loaded)
  const problems: string[] = []
  for (const { key, label } of REQUIRED_STRING_FIELDS) {
    const v = partial[key]
    if (typeof v !== "string" || !v.trim()) {
      problems.push(`缺少必填字段 ${label}`)
    }
  }
  if (!Array.isArray(partial.symbols) || partial.symbols.length === 0) {
    problems.push("缺少必填字段 symbols(至少一个交易对)")
  }
  if (problems.length > 0) {
    return { ok: false, error: `YAML 结构校验失败: ${problems.join("; ")}` }
  }
  return { ok: true, error: null, partial }
}

/** 修复请求消息模板(发给同一个 agent 会话) */
export function buildRepairPrompt(yaml: string, error: string): string {
  return [
    "你上一条回复里的 YAML 无法应用到策略:",
    error,
    "原始 YAML:",
    "```yaml",
    yaml,
    "```",
    "请修正上述问题,只输出修正后的完整 YAML 代码块,不要输出其他解释。",
  ].join("\n")
}
