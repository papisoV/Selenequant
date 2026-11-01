"use client"

import React from "react"

// Minimal rich markdown renderer — replaces old parseRichMarkdownToReact
// Supports: **bold**, *italic*, `code`, ```code blocks```, - lists, numbered lists, line breaks

export function parseRichMarkdownToReact(
  content: string,
  _generating?: boolean
): React.ReactNode {
  if (!content) return null

  const lines = content.split("\n")
  const elements: React.ReactNode[] = []

  let i = 0
  while (i < lines.length) {
    const line = lines[i]

    // Code block
    if (line.trimStart().startsWith("```")) {
      const lang = line.trim().slice(3)
      const codeLines: string[] = []
      i++
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        codeLines.push(lines[i])
        i++
      }
      i++ // skip closing ```
      elements.push(
        <pre
          key={`code-${elements.length}`}
          className="my-2 overflow-x-auto rounded-lg border border-border/60 bg-muted/30 p-3 text-[12px] font-mono leading-5 text-foreground"
        >
          {lang && (
            <div className="mb-1 text-[10px] text-muted-foreground">{lang}</div>
          )}
          <code>{codeLines.join("\n")}</code>
        </pre>
      )
      continue
    }

    // Unordered list
    if (/^\s*[-*]\s/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*]\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s/, ""))
        i++
      }
      elements.push(
        <ul key={`ul-${elements.length}`} className="my-1 ml-4 list-disc space-y-0.5 text-[13px]">
          {items.map((item, j) => (
            <li key={j}>{renderInline(item)}</li>
          ))}
        </ul>
      )
      continue
    }

    // Ordered list
    if (/^\s*\d+[.)]\s/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*\d+[.)]\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s/, ""))
        i++
      }
      elements.push(
        <ol key={`ol-${elements.length}`} className="my-1 ml-4 list-decimal space-y-0.5 text-[13px]">
          {items.map((item, j) => (
            <li key={j}>{renderInline(item)}</li>
          ))}
        </ol>
      )
      continue
    }

    // Empty line
    if (line.trim() === "") {
      elements.push(<div key={`br-${elements.length}`} className="h-2" />)
      i++
      continue
    }

    // Regular paragraph
    elements.push(
      <p key={`p-${elements.length}`} className="text-[13px] leading-6">
        {renderInline(line)}
      </p>
    )
    i++
  }

  return <>{elements}</>
}

function renderInline(text: string): React.ReactNode {
  // Split on inline patterns: **bold**, *italic*, `code`
  const parts: React.ReactNode[] = []
  let remaining = text
  let key = 0

  const patterns = [
    { regex: /\*\*(.+?)\*\*/g, wrap: (m: string) => <strong key={key++}>{m}</strong> },
    { regex: /\*(.+?)\*/g, wrap: (m: string) => <em key={key++}>{m}</em> },
    { regex: /`(.+?)`/g, wrap: (m: string) => (
      <code key={key++} className="rounded bg-muted/50 px-1 py-0.5 text-[12px] font-mono">{m}</code>
    )},
  ]

  while (remaining.length > 0) {
    let earliestMatch: { index: number; length: number; content: string; wrap: (m: string) => React.ReactNode } | null = null

    for (const p of patterns) {
      p.regex.lastIndex = 0
      const m = p.regex.exec(remaining)
      if (m && m.index >= 0 && (!earliestMatch || m.index < earliestMatch.index)) {
        earliestMatch = { index: m.index, length: m[0].length, content: m[1], wrap: p.wrap }
      }
    }

    if (!earliestMatch) {
      parts.push(remaining)
      break
    }

    if (earliestMatch.index > 0) {
      parts.push(remaining.slice(0, earliestMatch.index))
    }
    parts.push(earliestMatch.wrap(earliestMatch.content))
    remaining = remaining.slice(earliestMatch.index + earliestMatch.length)
  }

  return parts.length === 1 ? parts[0] : <>{parts}</>
}
