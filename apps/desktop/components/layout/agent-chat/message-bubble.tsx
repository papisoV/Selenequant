"use client";

import React, { useState } from "react";
import { cn } from "@/lib/utils";
import { parseMarkdownToReact } from "./markdown-renderer";
import { ReasoningChain } from "./reasoning-chain";
import type { ReasoningStep } from "./reasoning-chain";
import { ChevronDown, Database } from "lucide-react";
import { useUiText } from "@/components/i18n/use-ui-text"

export interface ChatMessage {
  id: string;
  role: "assistant" | "user";
  content: string;
  timestamp: number;
  reasoningSteps?: ReasoningStep[];
  /** 透明性批2-9: 本条回答注入的记忆命中条数(DONE 事件 memory_used) */
  memoryUsed?: number;
}

function MemoryProvenanceLine({ count }: { count: number }) {
  const tr = useUiText()

  const [open, setOpen] = useState(false);
  return (
    <div className="mb-1 text-[10px] text-muted-foreground" data-testid="memory-provenance">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 rounded px-1 py-0.5 hover:text-foreground"
      >
        <Database className="h-3 w-3" />
        {tr("已参考")} {count} {tr("条历史教训")}
        <ChevronDown className={cn("h-3 w-3 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <p className="mt-0.5 rounded border border-border-soft bg-surface px-2 py-1 leading-4">
          {tr("回答前按问题关键词检索了研究结论 / 历史策略失败 / 历史决策教训三类记忆库, 命中")} {count} {tr("条注入到上下文。命中内容可在 Research → 记忆页查看。")}
        </p>
      )}
    </div>
  );
}

export function MessageBubble({ msg, isGenerating }: { msg: ChatMessage; isGenerating?: boolean }) {
  const tr = useUiText()
  const isUser = msg.role === "user";
  const hasReasoning = !isUser && msg.reasoningSteps && msg.reasoningSteps.length > 0;
  const hasContent = msg.content.trim().length > 0;

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div className={cn("max-w-[85%]", isUser ? "" : "")}>
        {!isUser && (msg.memoryUsed ?? 0) > 0 && <MemoryProvenanceLine count={msg.memoryUsed!} />}
        {hasReasoning && (
          <ReasoningChain steps={msg.reasoningSteps!} isComplete={msg.content.length > 0} />
        )}
        {(!hasReasoning || hasContent || isUser) && (
          <div
            className={cn(
              "rounded-2xl px-4 py-2.5 text-sm leading-relaxed break-words",
              isUser
                ? "bg-primary text-primary-foreground rounded-br-md"
                : "bg-muted text-foreground rounded-bl-md"
            )}
          >
            {isUser ? msg.content : parseMarkdownToReact(msg.content, isGenerating, tr)}
          </div>
        )}
      </div>
    </div>
  );
}

export function TypingIndicator() {
  return (
    <div className="flex justify-start">
      <div className="bg-muted rounded-2xl rounded-bl-md px-4 py-3 flex gap-1.5 items-center">
        <span className="h-2 w-2 rounded-full bg-muted-foreground/60 animate-bounce" />
        <span className="h-2 w-2 rounded-full bg-muted-foreground/60 animate-bounce [animation-delay:150ms]" />
        <span className="h-2 w-2 rounded-full bg-muted-foreground/60 animate-bounce [animation-delay:300ms]" />
      </div>
    </div>
  );
}
