"use client";

import React from "react";
import { cn } from "@/lib/utils";

export function parseMarkdownToReact(text: string, isGenerating?: boolean): React.ReactNode[] {
  const elements: React.ReactNode[] = [];

  // Extract and render tool_call tags
  const toolCallMatches = text.match(/<tool_call>[\s\S]*?<\/tool_call>/g);
  if (toolCallMatches && toolCallMatches.length > 0) {
    elements.push(
      <details key="tool-calls" className="my-3 rounded-lg border border-primary/30 bg-primary/5">
        <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-primary hover:bg-primary/10 rounded-t-lg">
          🔧 工具调用 ({toolCallMatches.length}) - 点击查看 AI 的思考过程
        </summary>
        <div className="px-3 py-2 space-y-2 text-xs">
          {toolCallMatches.map((match, idx) => {
            try {
              const jsonStr = match.replace(/<\/?tool_call>/g, "").trim();
              const toolCall = JSON.parse(jsonStr);
              return (
                <div key={idx} className="bg-muted/50 rounded p-2 font-mono">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-primary font-bold">→ {toolCall.name}</span>
                  </div>
                  <div className="text-muted-foreground text-[11px] pl-3">
                    {JSON.stringify(toolCall.arguments, null, 2)}
                  </div>
                </div>
              );
            } catch {
              return (
                <div key={idx} className="bg-muted/50 rounded p-2 font-mono text-[11px] text-muted-foreground">
                  {match}
                </div>
              );
            }
          })}
        </div>
      </details>
    );
  }

  const cleanedText = text.replace(/<tool_call>[\s\S]*?<\/tool_call>/g, "");
  const lines = cleanedText.split("\n");
  let i = 0;

  let inReasoning = false;
  let reasoningLines: string[] = [];

  const flushReasoning = (isCompleted: boolean) => {
    if (reasoningLines.length > 0) {
      elements.push(
        <div key={`reasoning-${i}`} className={cn(
          "my-3 p-3 rounded-xl border-l-[3px] text-[13px] font-mono whitespace-pre-wrap leading-relaxed shadow-sm",
          isCompleted
            ? "bg-amber-500/5 border-amber-500/50 text-amber-700/80 dark:text-amber-400/80"
            : "bg-blue-500/5 border-blue-500/50 text-blue-700/80 dark:text-blue-400/80 animate-pulse"
        )}>
          <div className="flex items-center gap-1.5 mb-1.5 opacity-90 font-bold">
            <span className="text-sm">🤔</span>
            {isCompleted ? "Internal Reasoning" : "Thinking..."}
          </div>
          {reasoningLines.join("\n")}
        </div>
      );
      reasoningLines = [];
    }
  };

  while (i < lines.length) {
    let line = lines[i];

    if (inReasoning) {
      if (line.includes("</reasoning>")) {
        reasoningLines.push(line.replace("</reasoning>", ""));
        flushReasoning(true);
        inReasoning = false;
      } else {
        reasoningLines.push(line);
      }
      i++;
      continue;
    }

    if (line.includes("<reasoning>")) {
      inReasoning = true;
      const contentAfter = line.substring(line.indexOf("<reasoning>") + "<reasoning>".length);
      if (contentAfter.includes("</reasoning>")) {
        reasoningLines.push(contentAfter.replace("</reasoning>", ""));
        flushReasoning(true);
        inReasoning = false;
      } else {
        reasoningLines.push(contentAfter);
      }
      i++;
      continue;
    }

    // Headers
    if (line.startsWith("### ")) {
      elements.push(<h3 key={`h3-${i}`} className="text-sm font-bold mt-5 mb-2 text-primary flex items-center gap-2"><div className="w-1.5 h-1.5 rounded-full bg-primary" />{parseInlineMarkdown(line.replace(/^###\s*/, ""))}</h3>);
      i++;
      continue;
    }
    if (line.startsWith("## ")) {
      elements.push(<h2 key={`h2-${i}`} className="text-base font-bold mt-5 mb-2 text-foreground border-b border-border pb-1"><span className="text-primary mr-1">#</span>{parseInlineMarkdown(line.replace(/^##\s*/, ""))}</h2>);
      i++;
      continue;
    }
    if (line.startsWith("# ")) {
      elements.push(<h1 key={`h1-${i}`} className="text-lg font-black mt-6 mb-4 bg-gradient-to-r from-primary to-blue-500 bg-clip-text text-transparent">{parseInlineMarkdown(line.replace(/^#\s*/, ""))}</h1>);
      i++;
      continue;
    }

    // Blockquote
    if (line.startsWith("> ")) {
      elements.push(
        <blockquote key={`quote-${i}`} className="border-l-4 border-primary/50 bg-primary/5 px-4 py-2 my-3 text-muted-foreground italic rounded-r-lg text-[13px]">
          {parseInlineMarkdown(line.replace(/^>\s*/, ""))}
        </blockquote>
      );
      i++;
      continue;
    }

    // Unordered List
    if (line.match(/^[-*]\s/)) {
      const listItems: string[] = [];
      while (i < lines.length && lines[i].match(/^[-*]\s/)) {
        listItems.push(lines[i].replace(/^[-*]\s/, ""));
        i++;
      }
      elements.push(
        <ul key={`ul-${i}`} className="space-y-1.5 my-3">
          {listItems.map((item, idx) => (
            <li key={`uli-${i}-${idx}`} className="flex items-start gap-2 text-[13px]">
              <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-primary/60 shrink-0" />
              <span className="flex-1 leading-relaxed">{parseInlineMarkdown(item)}</span>
            </li>
          ))}
        </ul>
      );
      continue;
    }

    // Ordered List
    if (line.match(/^\d+\.\s/)) {
      const listItems: string[] = [];
      const startI = i;
      while (i < lines.length && lines[i].match(/^\d+\.\s/)) {
        listItems.push(lines[i].replace(/^\d+\.\s/, ""));
        i++;
      }
      elements.push(
        <ol key={`ol-${startI}`} className="space-y-1.5 my-3 list-decimal list-inside text-primary font-medium text-[13px]">
          {listItems.map((item, idx) => (
            <li key={`oli-${startI}-${idx}`} className="leading-relaxed">
              <span className="text-foreground font-normal ml-1">{parseInlineMarkdown(item)}</span>
            </li>
          ))}
        </ol>
      );
      continue;
    }

    // Tables
    if (line.includes("|") && line.trim().startsWith("|")) {
      const tableLines: string[] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim().startsWith("|")) {
        if (!lines[i].match(/^[\s|:-]+$/)) {
          tableLines.push(lines[i]);
        }
        i++;
      }
      if (tableLines.length > 0) {
        const rows = tableLines.map((l) => l.split("|").map((c) => c.trim()).filter((c) => c));
        elements.push(
          <div key={`table-${i}`} className="overflow-x-auto my-3 rounded-lg border border-border">
            <table className="min-w-full border-collapse text-xs">
              <thead className="bg-muted/50 border-b border-border">
                <tr>
                  {rows[0].map((cell, idx) => (
                    <th key={`th-${i}-${idx}`} className="px-3 py-2 text-left font-semibold text-foreground">
                      {parseInlineMarkdown(cell)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.slice(1).map((row, rowIdx) => (
                  <tr key={`tr-${i}-${rowIdx}`} className="hover:bg-muted/30 transition-colors">
                    {row.map((cell, cellIdx) => (
                      <td key={`td-${i}-${rowIdx}-${cellIdx}`} className="px-3 py-2 text-muted-foreground">
                        {parseInlineMarkdown(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }
      continue;
    }

    // Empty Lines
    if (line.trim() === "") {
      elements.push(<div key={`br-${i}`} className="h-1.5" />);
      i++;
      continue;
    }

    // Paragraph
    elements.push(<p key={`p-${i}`} className="my-1.5 text-[14px] leading-relaxed relative flex items-center flex-wrap">
      {parseInlineMarkdown(line)}
      {isGenerating && i === lines.length - 1 && !inReasoning && (
        <span className="inline-block w-2 h-4 bg-primary shadow-[0_0_8px_rgba(var(--primary),0.6)] rounded-sm animate-[pulse_0.8s_cubic-bezier(0.4,0,0.6,1)_infinite] ml-1 align-middle translate-y-px" />
      )}
    </p>);
    i++;
  }

  // Flush any unterminated reasoning
  if (inReasoning) {
    flushReasoning(false);
  }

  // If entirely empty but generating, show standalone cursor
  if (elements.length === 0 && isGenerating) {
    return [<span key="only-cursor" className="inline-block w-2 h-4 bg-primary shadow-[0_0_8px_rgba(var(--primary),0.6)] rounded-sm animate-[pulse_0.8s_cubic-bezier(0.4,0,0.6,1)_infinite] align-middle mt-1" />];
  }

  return elements;
}

function parseInlineMarkdown(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  let remaining = text;
  let key = 0;

  while (remaining) {
    const boldMatch = remaining.match(/\*\*(.+?)\*\*/);
    if (boldMatch) {
      const before = remaining.slice(0, boldMatch.index);
      if (before) parts.push(before);
      parts.push(<strong key={key++} className="font-semibold text-primary">{boldMatch[1]}</strong>);
      remaining = remaining.slice((boldMatch.index || 0) + boldMatch[0].length);
      continue;
    }

    const italicMatch = remaining.match(/\*(.+?)\*/);
    if (italicMatch) {
      const before = remaining.slice(0, italicMatch.index);
      if (before) parts.push(before);
      parts.push(<em key={key++} className="italic text-foreground/80">{italicMatch[1]}</em>);
      remaining = remaining.slice((italicMatch.index || 0) + italicMatch[0].length);
      continue;
    }

    const codeMatch = remaining.match(/`(.+?)`/);
    if (codeMatch) {
      const before = remaining.slice(0, codeMatch.index);
      if (before) parts.push(before);
      parts.push(<code key={key++} className="bg-primary/10 text-primary px-1.5 py-0.5 rounded text-[12px] font-mono border border-primary/20">{codeMatch[1]}</code>);
      remaining = remaining.slice((codeMatch.index || 0) + codeMatch[0].length);
      continue;
    }

    parts.push(remaining);
    break;
  }

  return parts.length === 1 ? parts[0] : <>{parts}</>;
}
