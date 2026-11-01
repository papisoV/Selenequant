"use client"

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"

// Lightweight YAML editor without external js-yaml dependency.
// Serializes strategy to YAML-like text using a minimal serializer.
// Parses on blur with a minimal line-based parser (supports flat maps + nested one level).
// For complex YAML, falls back to JSON object literal parsing.

import type { StrategyData } from "./types"
import { useUiText } from "@/components/i18n/use-ui-text"

export interface YamlEditorProps {
  strategy: StrategyData
  onChange: (updated: Partial<StrategyData>) => void
}

function buildEditablePayload(strategy: StrategyData) {
  return {
    name: strategy.name ?? "",
    type: strategy.type ?? "",
    description: strategy.description ?? "",
    symbols: strategy.symbols ?? [],
    interval: strategy.interval ?? "",
    direction: strategy.direction ?? "neutral",
    parameters: strategy.parameters ?? {},
    entry_rules: strategy.entry_rules ?? {},
    exit_rules: strategy.exit_rules ?? {},
    risk_params: strategy.risk_params ?? {},
    tags: strategy.tags ?? [],
  }
}

function isDirection(v: unknown): v is NonNullable<StrategyData["direction"]> {
  return v === "long_only" || v === "short_only" || v === "neutral"
}

function indent(level: number): string {
  return "  ".repeat(level)
}

function valueToYaml(value: unknown, level: number): string {
  if (value === null || value === undefined) return "null"
  if (typeof value === "string") return value
  if (typeof value === "number" || typeof value === "boolean") return String(value)
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]"
    return "\n" + value.map((v) => `${indent(level)}- ${valueToYaml(v, level + 1)}`).join("\n")
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) return "{}"
    return "\n" + entries.map(([k, v]) => `${indent(level)}${k}: ${valueToYaml(v, level + 1)}`).join("\n")
  }
  return String(value)
}

function serializeToYaml(obj: Record<string, unknown>): string {
  return Object.entries(obj)
    .map(([k, v]) => `${k}:${valueToYaml(v, 1)}`)
    .join("\n")
}

/** 行号数组 [1..n], n 为文本行数。纯函数。 */
export function lineNumbersFor(text: string): number[] {
  const n = text.split("\n").length
  return Array.from({ length: n }, (_, i) => i + 1)
}



/** Ctrl/Cmd+S 触发保存回调的 keydown 处理器。 */
export function useSaveShortcut(onSave: () => void) {
  return useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "s" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault()
        onSave()
      }
    },
    [onSave]
  )
}

export function coerceScalar(cv: string): unknown {
  if (cv === "null" || cv === "~") return null
  if (cv === "true") return true
  if (cv === "false") return false
  if (cv === "[]") return []
  if (cv === "{}") return {}
  if (/^-?\d+\.?\d*$/.test(cv)) return Number(cv)
  if ((cv.startsWith("[") && cv.endsWith("]")) || (cv.startsWith("{") && cv.endsWith("}"))) {
    try { return JSON.parse(cv) } catch { return cv }
  }
  return cv
}

export function tryParseYaml(text: string): Record<string, unknown> {
  // Minimal parser: try JSON first (in case user pasted JSON), then line-based YAML
  try {
    const json = JSON.parse(text)
    if (json && typeof json === "object" && !Array.isArray(json)) {
      return json as Record<string, unknown>
    }
  } catch {
    // not JSON, continue to YAML parse
  }

  const result: Record<string, unknown> = {}
  const lines = text.split("\n")
  // US-104: nested block lists (e.g. `symbols:\n  - BTC/USDT`) — the previous
  // parser silently skipped them, so AI-generated YAML (mostly block style)
  // validated as "missing symbols" or, worse, applied with symbols dropped.
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (!line.trim() || line.trim().startsWith("#")) continue
    const match = line.match(/^(\s*)([\w_-]+)\s*:\s*(.*)$/)
    if (!match) continue
    const indentLevel = match[1].length
    const key = match[2]
    const rawVal = match[3].trim()

    if (indentLevel === 0) {
      if (rawVal === "") {
        // nested block — collect the child items (list entries or map keys)
        const items: string[] = []
        const childMap: Record<string, unknown> = {}
        let j = i + 1
        while (j < lines.length) {
          const child = lines[j]
          if (!child.trim() || child.trim().startsWith("#")) {
            j++
            continue
          }
          const childMatch = child.match(/^(\s+)([\w_-]+)\s*:\s*(.*)$/)
          const listItem = child.match(/^\s+-\s*(.*)$/)
          if (childMatch && childMatch[1].length > 0 && !listItem) {
            // deeper-indented map entry under this key
            childMap[childMatch[2]] = coerceScalar(childMatch[3].trim())
            j++
            continue
          }
          if (listItem) {
            items.push(listItem[1].trim())
            j++
            continue
          }
          break
        }
        if (items.length > 0) {
          result[key] = items
        } else if (Object.keys(childMap).length > 0) {
          result[key] = childMap
        } else {
          result[key] = null
        }
        i = j - 1
        continue
      }
      result[key] = coerceScalar(rawVal)
    }
  }
  return result
}

export function parsedToPartial(loaded: Record<string, unknown>): Partial<StrategyData> {
  const out: Partial<StrategyData> = {}
  if (typeof loaded.name === "string") out.name = loaded.name
  if (typeof loaded.type === "string") out.type = loaded.type
  if (typeof loaded.description === "string") out.description = loaded.description
  if (typeof loaded.interval === "string") out.interval = loaded.interval
  if (isDirection(loaded.direction)) out.direction = loaded.direction
  if (Array.isArray(loaded.symbols)) {
    out.symbols = loaded.symbols.filter((x): x is string => typeof x === "string")
  }
  if (Array.isArray(loaded.tags)) {
    out.tags = loaded.tags.filter((x): x is string => typeof x === "string")
  }
  if (loaded.parameters !== undefined && typeof loaded.parameters === "object" && !Array.isArray(loaded.parameters)) {
    out.parameters = { ...(loaded.parameters as Record<string, unknown>) }
  }
  if (loaded.entry_rules !== undefined && typeof loaded.entry_rules === "object" && !Array.isArray(loaded.entry_rules)) {
    out.entry_rules = { ...(loaded.entry_rules as Record<string, unknown>) }
  }
  if (loaded.exit_rules !== undefined && typeof loaded.exit_rules === "object" && !Array.isArray(loaded.exit_rules)) {
    out.exit_rules = { ...(loaded.exit_rules as Record<string, unknown>) }
  }
  if (loaded.risk_params !== undefined && typeof loaded.risk_params === "object" && !Array.isArray(loaded.risk_params)) {
    out.risk_params = { ...(loaded.risk_params as Record<string, unknown>) }
  }
  return out
}

export function YamlEditor({ strategy, onChange }: YamlEditorProps) {
  const tr = useUiText()

  const snapshot = useMemo(() => buildEditablePayload(strategy), [strategy])
  const snapshotKey = useMemo(() => JSON.stringify(snapshot), [snapshot])

  const [yamlText, setYamlText] = useState(() => serializeToYaml(snapshot))
  const [parseError, setParseError] = useState<string | null>(null)

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const gutterRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setYamlText(serializeToYaml(buildEditablePayload(strategy)))
    setParseError(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshotKey, strategy])

  const lineNumbers = useMemo(() => lineNumbersFor(yamlText), [yamlText])

  const syncGutterScroll = useCallback(() => {
    const ta = textareaRef.current
    const g = gutterRef.current
    if (ta && g) g.scrollTop = ta.scrollTop
  }, [])

  const handleParse = useCallback(() => {
    try {
      const loaded = tryParseYaml(yamlText)
      const partial = parsedToPartial(loaded)
      setParseError(null)
      onChange(partial)
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setParseError(`YAML 解析错误: ${message}`)
    }
  }, [yamlText, onChange])

  const onKeyDown = useSaveShortcut(handleParse)

  return (
    <div className="space-y-2">
      <p className="text-[10px] text-muted-foreground">{tr("编辑 YAML，失焦或 Ctrl/Cmd+S 时应用更改")}</p>
      <div className="flex min-h-[400px] w-full overflow-hidden rounded-lg border border-border bg-muted/30">
        <div
          ref={gutterRef}
          className="w-9 shrink-0 overflow-y-auto overflow-x-hidden border-r border-border py-4 pl-2 pr-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          aria-hidden
        >
          <div className="font-mono text-[10px] leading-5 text-muted-foreground">
            {lineNumbers.map((num) => (
              <div key={num} className="h-5 text-right tabular-nums">
                {num}
              </div>
            ))}
          </div>
        </div>
        <textarea
          ref={textareaRef}
          data-testid="yaml-editor-textarea"
          className="min-h-[400px] w-full flex-1 resize-y border-0 bg-transparent p-4 font-mono text-xs leading-5 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          value={yamlText}
          onChange={(e) => setYamlText(e.target.value)}
          onBlur={handleParse}
          onKeyDown={onKeyDown}
          onScroll={syncGutterScroll}
          spellCheck={false}
        />
      </div>
      {parseError && <p className="text-xs text-red-400">{parseError}</p>}
    </div>
  )
}
