"use client"

import React, { useState, useCallback, useRef, useEffect, useMemo } from "react"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { YamlEditor } from "./yaml-editor"
import { validateStrategyYaml, buildRepairPrompt } from "./yaml-repair"
import { ParamPanel } from "./param-panel"
import { cn } from "@/lib/utils"
import { parseRichMarkdownToReact } from "./markdown-rich"
import {
  Send,
  Trash2,
  Loader2,
  FileCode,
  BarChart3,
  Lightbulb,
  MessageSquareText,
  SlidersHorizontal,
  Sparkles,
  ChevronRight,
  Plus,
  History,
  MessageCircle,
  X,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"
import {
  sendAgentChat,
  getBacktest,
  createChatSession,
  listChatSessions,
  getChatSessionMessages,
  saveChatSessionMessages,
  deleteChatSession,
  updateChatSessionTitle,
} from "./api-bridge"
import { useToolApproval } from "@/lib/hooks/use-tool-approval"
import type { AgentChatContext, AgentChatResult, BacktestData, StrategyData, ChatSession } from "./types"
import { useUiText } from "@/components/i18n/use-ui-text"
import { formatPending } from "@/lib/i18n/types"

export interface AiSidebarProps {
  strategyId: string
  strategyName: string
  strategy: StrategyData | null
  symbol?: string
  timeframe?: string
  onStrategyChange: (updated: Partial<StrategyData>) => void
  onApplyYaml?: (yamlContent: string) => void
  onCollapse?: () => void
  onBacktestResult?: (result: BacktestData) => void
  onStrategyRefresh?: () => void
}

interface QuickReplyOption {
  id: string
  label: string
  value: string
  description?: string
}

interface ChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
  reasoningSteps?: ReasoningStep[]
  quickReplies?: QuickReplyOption[]
}

interface ReasoningStep {
  type: "thinking" | "tool_call" | "tool_result" | "reflection"
  data: Record<string, unknown>
  timestamp: number
}

const skillDefs: { id: string; label: string; icon: LucideIcon; prompt: string }[] = [
  {
    id: "strategy-coder",
    label: "策略编码",
    icon: FileCode,
    prompt: "Help me write a trading strategy based on {name}",
  },
  {
    id: "backtest-explain",
    label: "回测解读",
    icon: BarChart3,
    prompt: "Explain the latest backtest results for {name}",
  },
  {
    id: "strategy-draft",
    label: "策略建议",
    icon: Lightbulb,
    prompt: "Suggest improvements for {name} strategy",
  },
]

type AiSidebarTab = "chat" | "yaml" | "params"

function newMessageId(): string {
  return `${Date.now().toString()}-${Math.random().toString(36).slice(2, 9)}`
}

export function deltaTextFromData(data: unknown): string {
  if (typeof data === "string") return data
  if (data && typeof data === "object" && "text" in data) {
    const v = (data as { text: unknown }).text
    if (typeof v === "string") return v
  }
  return ""
}

export function extractYamlBlock(content: string): string | null {
  const match = content.match(/```[yY][aA][mM][lL]\s*\r?\n([\s\S]*?)```/)
  return match?.[1]?.trim() || null
}

const BACKTEST_ID_RE = /Backtest Complete\s*[—–-]\s*ID:\s*(bt-[a-f0-9]+)/i
const BACKTEST_ID_FALLBACK_RE = /\b(bt-[a-f0-9]{6,})\b/

export function extractBacktestId(toolOutput: string): string | null {
  const m = toolOutput.match(BACKTEST_ID_RE)
  if (m?.[1]) return m[1]
  const fb = toolOutput.match(BACKTEST_ID_FALLBACK_RE)
  return fb?.[1] ?? null
}

export function normalizeQuickReplyId(raw: string, fallback: number): string {
  return raw.trim() || String(fallback + 1)
}

export function extractStructuredQuickReplies(data: unknown): QuickReplyOption[] {
  if (!data || typeof data !== "object") return []
  const source = data as Record<string, unknown>
  const rawOptions =
    source.quick_replies ??
    source.quickReplies ??
    source.reply_options ??
    source.replyOptions ??
    source.suggested_replies ??
    source.suggestedReplies ??
    source.options

  if (!Array.isArray(rawOptions)) return []

  return rawOptions
    .map((item, index) => {
      if (typeof item === "string") {
        return { id: String(index + 1), label: item, value: item }
      }
      if (!item || typeof item !== "object") return null
      const option = item as Record<string, unknown>
      const label = String(option.label ?? option.title ?? option.text ?? "").trim()
      const value = String(option.value ?? option.message ?? option.prompt ?? label).trim()
      const id = normalizeQuickReplyId(String(option.id ?? option.key ?? option.code ?? ""), index)
      const description = option.description != null ? String(option.description) : undefined
      if (!label || !value) return null
      return { id, label, value, description }
    })
    .filter((item): item is QuickReplyOption => item !== null)
    .slice(0, 6)
}

export function extractQuickReplies(content: string): { cleanedContent: string; quickReplies: QuickReplyOption[] } {
  const lines = content.split("\n")
  const quickReplies: QuickReplyOption[] = []
  const cleaned: string[] = []
  let inOptionSection = false

  for (const line of lines) {
    const trimmed = line.trim()
    if (
      !inOptionSection &&
      /如果你愿意|你可以直接回复|你回复数字即可|下一步可以直接帮你做其中一个|你回复\s*[A-Z]/i.test(trimmed)
    ) {
      inOptionSection = true
      cleaned.push(line)
      continue
    }

    const numbered = trimmed.match(/^([1-9A-Z])\s*[).、:-]?\s+(.+)$/)
    if (inOptionSection && numbered) {
      const fullLabel = numbered[2].trim().replace(/\*\*/g, "")
      quickReplies.push({
        id: normalizeQuickReplyId(numbered[1], quickReplies.length),
        label: fullLabel,
        value: fullLabel,
      })
      continue
    }

    const bulleted = trimmed.match(/^[-*]\s+(.+)$/)
    if (inOptionSection && bulleted) {
      const label = bulleted[1].trim().replace(/\*\*/g, "")
      quickReplies.push({
        id: String(quickReplies.length + 1),
        label,
        value: label,
      })
      continue
    }

    cleaned.push(line)
  }

  return {
    cleanedContent: cleaned.join("\n").trim(),
    quickReplies: quickReplies.slice(0, 6),
  }
}

export function cleanReasoningText(raw: string): string {
  return raw
    .replace(/<\/?reasoning>/gi, "")
    .replace(/^(?:Step|Round)\s*\d+\s*[:：]?\s*(?:Think|Reflect|Act|Observe|Analyze|Decide)?\s*/gim, "")
    .trim()
}

export function formatReasoningSummary(step: ReasoningStep): string {
  if (step.type === "thinking" || step.type === "reflection") {
    return cleanReasoningText(String(step.data.content ?? step.data.summary ?? ""))
  }
  if (step.type === "tool_call") {
    const tool = String(step.data.tool ?? step.data.name ?? "unknown")
    const args = step.data.arguments ?? step.data.input ?? {}
    return `${tool}(${JSON.stringify(args)})`
  }
  const output = cleanReasoningText(String(step.data.output ?? step.data.result ?? step.data.content ?? ""))
  return output.length > 120 ? `${output.slice(0, 120)}…` : output
}

// P2 同化:emoji 图标(🧠⚙️📊💡)与 sky/violet 彩色词汇已废弃,统一走平台语义
// token(pos/warn/neg/gold),图标用字形编号与 STEP_LABELS 对应,克制不抢戏。
const STEP_TONES: Record<ReasoningStep["type"], { icon: string; tone: string }> = {
  thinking: { icon: "思", tone: "border-gold/20 bg-gold/10 text-gold" },
  tool_call: { icon: "调", tone: "border-accent/20 bg-accent/10 text-accent" },
  tool_result: { icon: "果", tone: "border-pos/20 bg-pos/10 text-pos" },
  reflection: { icon: "省", tone: "border-warn/20 bg-warn/10 text-warn" },
}

const STEP_LABELS: Record<ReasoningStep["type"], string> = {
  thinking: "思考",
  tool_call: "工具调用",
  tool_result: "工具结果",
  reflection: "反思",
}

function ReasoningProgress({
  steps,
  isComplete,
}: {
  steps: ReasoningStep[]
  isComplete: boolean
}) {
  const tr = useUiText()

  const [isOpen, setIsOpen] = useState(!isComplete)

  useEffect(() => {
    if (isComplete) {
      setIsOpen(false)
      return
    }
    setIsOpen(true)
  }, [isComplete, steps.length])

  if (!steps.length) return null

  const latest = steps[steps.length - 1]
  const latestSummary = formatReasoningSummary(latest) || STEP_LABELS[latest.type]

  return (
    <div className="mb-3 overflow-hidden rounded-lg border border-primary/15 bg-background/60 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]">
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full border-b border-border/70 bg-primary/5 px-3 py-2 text-left transition-colors hover:bg-primary/8"
      >
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-primary/80">
              {tr("推理进度")}
            </p>
            <p className="mt-1 truncate text-[11px] text-foreground/90">{latestSummary}</p>
          </div>
          <div className="flex items-center gap-2">
            <div className="rounded-full border border-primary/15 bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary">
              {steps.length} {tr("步")}
            </div>
            <span className="text-xs text-muted-foreground">{isOpen ? tr("收起") : tr("展开")}</span>
          </div>
        </div>
      </button>
      {isOpen && (
        <div className="space-y-2 px-3 py-3">
          {steps.map((step, idx) => {
            const visual = STEP_TONES[step.type]
            const label = STEP_LABELS[step.type]
            const summary = formatReasoningSummary(step)
            const isLatest = idx === steps.length - 1
            return (
              <div key={`${step.type}-${idx}-${step.timestamp}`} className="flex gap-3">
                <div className="flex flex-col items-center">
                  <div
                    className={cn(
                      "flex h-6 w-6 shrink-0 items-center justify-center rounded-md border text-[10px] font-medium shadow-sm",
                      visual.tone,
                      isLatest && "ring-2 ring-primary/20"
                    )}
                  >
                    {visual.icon}
                  </div>
                  {idx < steps.length - 1 && <div className="mt-1 h-full w-px bg-border/80" />}
                </div>
                <div className="min-w-0 flex-1 pb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] font-medium text-foreground">{label}</span>
                    {isLatest && (
                      <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[9px] font-medium text-primary">
                        {tr("当前")}
                      </span>
                    )}
                  </div>
                  {summary ? (
                    <p className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-5 text-muted-foreground">
                      {summary}
                    </p>
                  ) : null}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function AssistantQuickReplies({
  options,
  disabled,
  onSelect,
}: {
  options: QuickReplyOption[]
  disabled: boolean
  onSelect: (value: string) => void
}) {
  const tr = useUiText()

  if (!options.length) return null

  return (
    <div className="mt-4 space-y-2">
      <div className="flex items-center gap-2">
        <Sparkles className="h-3.5 w-3.5 text-primary" />
        <p className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">{tr("快捷选择")}</p>
      </div>
      <div className="space-y-2">
        {options.map((option) => (
          <button
            key={`${option.id}-${option.value}`}
            type="button"
            onClick={() => onSelect(option.value)}
            disabled={disabled}
            className="group w-full rounded-lg border border-border/70 bg-background/60 px-3 py-2.5 text-left text-[11px] font-medium text-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 disabled:pointer-events-none disabled:opacity-50"
          >
            <div className="flex items-center gap-3">
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-md bg-primary/10 text-[10px] font-semibold text-primary">
                {option.id}
              </span>
              <div className="min-w-0 flex-1">
                <div>{option.label}</div>
                {option.description ? (
                  <p className="mt-1 text-[10px] leading-4 text-muted-foreground">{option.description}</p>
                ) : null}
              </div>
              <ChevronRight className="h-3.5 w-3.5 text-muted-foreground transition-colors group-hover:text-primary" />
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function AssistantMessageContent({
  content,
  generating,
  reasoningSteps,
  quickReplies,
  onQuickReplySelect,
  onApplyYaml,
  applyLabel,
}: {
  content: string
  generating: boolean
  reasoningSteps?: ReasoningStep[]
  quickReplies?: QuickReplyOption[]
  onQuickReplySelect: (value: string) => void
  onApplyYaml?: (yamlContent: string) => void
  applyLabel: string
}) {
  const yamlBlock = extractYamlBlock(content)
  const { cleanedContent: rawCleaned, quickReplies: extractedQuickReplies } = useMemo(
    () => extractQuickReplies(content),
    [content]
  )
  const cleanedContent = useMemo(() => cleanReasoningText(rawCleaned), [rawCleaned])
  const mergedQuickReplies = quickReplies?.length ? quickReplies : extractedQuickReplies
  const isComplete = useMemo(() => {
    if (!reasoningSteps?.length) return false
    if (!cleanedContent.trim()) return false
    const latest = reasoningSteps[reasoningSteps.length - 1]
    return latest.type === "reflection" || !generating
  }, [cleanedContent, generating, reasoningSteps])

  return (
    <div className="space-y-3">
      <ReasoningProgress steps={reasoningSteps ?? []} isComplete={isComplete} />
      {!!cleanedContent && (
        <div className={cn(
          "strategy-ai-richtext",
          "text-[11px] leading-5",
          "[&_strong]:font-semibold [&_em]:italic"
        )}>
          {parseRichMarkdownToReact(cleanedContent, generating)}
        </div>
      )}
      <AssistantQuickReplies
        options={mergedQuickReplies ?? []}
        disabled={generating}
        onSelect={onQuickReplySelect}
      />
      {yamlBlock && onApplyYaml && (
        <Button
          size="sm"
          variant="outline"
          className="h-8 rounded-lg border-primary/20 bg-primary/5 text-[11px] hover:bg-primary/10"
          onClick={() => onApplyYaml(yamlBlock)}
        >
          {applyLabel}
        </Button>
      )}
    </div>
  )
}

function AssistantEmptyState({
  title,
  subtitle,
}: {
  title: string
  subtitle: string
}) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-primary/15 bg-primary/10 shadow-sm">
        <Sparkles className="h-5 w-5" />
      </div>
      <h3 className="mt-3 text-[11px] font-semibold text-foreground">{title}</h3>
      <p className="mt-1 max-w-[260px] text-[10px] leading-4 text-muted-foreground">{subtitle}</p>
    </div>
  )
}

// P2 同化:eyebrow 小字帽(英文装饰性 noise)删除,标题即层级;22px 圆角归平台 6px 梯。
function PanelSection({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-border/70 bg-background/35">
      <div className="shrink-0 border-b border-border/70 px-4 py-3">
        <h3 className="text-[11px] font-semibold text-foreground">{title}</h3>
        <p className="mt-1 text-[10px] leading-4 text-muted-foreground">{description}</p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">{children}</div>
    </div>
  )
}

/** 推理类事件(思考/工具调用/工具结果/反思)。 */
const REASONING_EVENT_TYPES = new Set([
  "thinking",
  "tool_call",
  "tool_result",
  "reflection",
])

/** 事件是否属于推理类。 */
export function isReasoningEvent(type: string): boolean {
  return REASONING_EVENT_TYPES.has(type)
}

/** 把流式事件转成一条 ReasoningStep(timestamp 由调用方补)。纯函数。 */
export function reasoningStepFromEvent(event: {
  type: string
  data: unknown
}): Omit<ReasoningStep, "timestamp"> {
  return {
    type: event.type as ReasoningStep["type"],
    data:
      typeof event.data === "object" && event.data !== null
        ? (event.data as Record<string, unknown>)
        : { content: String(event.data ?? "") },
  }
}

/** 把 API 返回的会话消息映射成 ChatMessage[]。纯函数。 */
export function mapApiMessages(raw: unknown[]): ChatMessage[] {
  return raw.map((m) => {
    const msg = m as { id: string; role: string; content: string; extra?: unknown }
    return {
      id: msg.id,
      role: msg.role as "user" | "assistant",
      content: msg.content,
      reasoningSteps: Array.isArray(msg.extra)
        ? (msg.extra as unknown as ReasoningStep[])
        : undefined,
    }
  })
}

export function AiSidebar({
  strategyId,
  strategyName,
  strategy,
  onStrategyRefresh,
  symbol,
  timeframe,
  onStrategyChange,
  onApplyYaml,
  onBacktestResult,
  onCollapse,
}: AiSidebarProps) {
  const tr = useUiText()

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState("")
  const [generating, setGenerating] = useState(false)
  const [activeTab, setActiveTab] = useState<AiSidebarTab>("chat")
  const [sessionId, setSessionId] = useState<string | undefined>(undefined)
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const sendingRef = useRef(false)
  const mountedRef = useRef(true)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [heartbeatText, setHeartbeatText] = useState<string>("")
  const [lastFailedMessage, setLastFailedMessage] = useState<string>("")
  const [reconnecting, setReconnecting] = useState(false)
  // US-104 自修复环:修复轮进行中(显示"校验失败,正在自动修复…")+ 已重试标记(限 1 次)
  const [yamlRepairing, setYamlRepairing] = useState(false)
  const yamlRepairAttemptedRef = useRef(false)
  const lastAssistantIdRef = useRef<string>("")
  const lastCleanContentRef = useRef<string>("")
  const lastCleanStepsRef = useRef<ReasoningStep[]>([])

  const messagesRef = useRef<ChatMessage[]>([])
  const sessionIdRef = useRef<string | undefined>(undefined)
  const skipNextSaveRef = useRef(false)
  messagesRef.current = messages
  sessionIdRef.current = sessionId

  // B0 确认门:与 FAB 面/对话页/终端共用同一份审批状态机(此前本面 onEvent
  // 完全没处理 tool_approval_request → 审批卡永不出现,写工具静默挂起到超时)。
  const {
    pendingApprovals,
    approvingIds,
    approvalError,
    handleApprovalEvent,
    approve,
    reset: resetApproval,
  } = useToolApproval({ getSessionId: () => sessionIdRef.current })

  const lastSavedTitleRef = useRef<string>("")

  const flushSave = useCallback(() => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
    const sid = sessionIdRef.current
    const msgs = messagesRef.current
    if (!sid || msgs.length === 0) return
    const payload = msgs.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      extra: m.reasoningSteps ?? {},
    }))
    void saveChatSessionMessages(sid, payload)
    const firstUser = msgs.find((m) => m.role === "user")
    if (firstUser) {
      const title = firstUser.content.slice(0, 40) + (firstUser.content.length > 40 ? "…" : "")
      if (title !== lastSavedTitleRef.current) {
        lastSavedTitleRef.current = title
        void updateChatSessionTitle(sid, title)
      }
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    const handleBeforeUnload = () => flushSave()
    window.addEventListener("beforeunload", handleBeforeUnload)
    return () => {
      mountedRef.current = false
      window.removeEventListener("beforeunload", handleBeforeUnload)
      flushSave()
    }
  }, [flushSave])

  useEffect(() => {
    let cancelled = false
    async function load() {
      const listRes = await listChatSessions(strategyId)
      if (cancelled) return
      const list = listRes.data ?? []
      setSessions(list)

      if (list.length > 0) {
        const latest = list[0]
        setSessionId(latest.id)
        const msgRes = await getChatSessionMessages(latest.id)
        if (cancelled) return
        skipNextSaveRef.current = true
        setMessages(mapApiMessages(msgRes.data ?? []))
      } else {
        setSessionId(undefined)
        skipNextSaveRef.current = true
        setMessages([])
      }
    }
    void load()
    return () => { cancelled = true }
  }, [strategyId])

  useEffect(() => {
    if (skipNextSaveRef.current) {
      skipNextSaveRef.current = false
      return
    }
    if (!sessionId || messages.length === 0) return
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => {
      flushSave()
    }, 1500)
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current) }
  }, [messages, sessionId, flushSave])

  const scrollToBottom = useCallback(() => {
    const el = messagesEndRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, generating, scrollToBottom])

  const refreshSessionList = useCallback(async () => {
    const res = await listChatSessions(strategyId)
    if (mountedRef.current && res.data) setSessions(res.data)
  }, [strategyId])

  const handleNewConversation = useCallback(async () => {
    setTimeout(() => flushSave(), 150)
    skipNextSaveRef.current = true
    setMessages([])
    setShowHistory(false)
    resetApproval()
    const tempId = `temp-${Date.now()}`
    setSessionId(tempId)
    const res = await createChatSession(strategyId, "")
    if (res.data) {
      setSessionId(res.data.id)
      setSessions((prev) => [res.data!, ...prev])
    }
  }, [strategyId, flushSave, resetApproval])

  const handleSwitchSession = useCallback((targetId: string) => {
    if (targetId === sessionId) {
      setShowHistory(false)
      return
    }
    setTimeout(() => flushSave(), 150)
    setSessionId(targetId)
    skipNextSaveRef.current = true
    setMessages([])
    setShowHistory(false)
    resetApproval()
    getChatSessionMessages(targetId).then((msgRes) => {
      if (!mountedRef.current) return
      skipNextSaveRef.current = true
      setMessages(mapApiMessages(msgRes.data ?? []))
    })
  }, [sessionId, flushSave, resetApproval])

  const handleDeleteSession = useCallback((targetId: string) => {
    setSessions((prev) => prev.filter((s) => s.id !== targetId))
    if (targetId === sessionId) {
      setSessionId(undefined)
      setMessages([])
      resetApproval()
    }
    void deleteChatSession(targetId)
  }, [sessionId, resetApproval])

  const handleClear = useCallback(() => {
    const sid = sessionId
    setMessages([])
    setSessionId(undefined)
    setActiveTab("chat")
    resetApproval()
    if (sid) {
      setSessions((prev) => prev.filter((s) => s.id !== sid))
      void deleteChatSession(sid)
    }
  }, [sessionId, resetApproval])

  const sendWithText = useCallback(
    async (messageText: string, opts?: { resume?: boolean }) => {
      const text = messageText.trim()
      if (!text || sendingRef.current) return
      sendingRef.current = true

      const isResume = !!opts?.resume

      let effectiveSessionId = sessionId
      if (!isResume && !effectiveSessionId) {
        const res = await createChatSession(strategyId, text.slice(0, 40))
        if (res.data) {
          effectiveSessionId = res.data.id
          setSessionId(effectiveSessionId)
          setSessions((prev) => {
            if (prev.some((s) => s.id === res.data!.id)) return prev
            return [res.data! as ChatSession, ...prev]
          })
        }
      }

      let assistantId: string
      if (isResume && lastAssistantIdRef.current) {
        assistantId = lastAssistantIdRef.current
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: lastCleanContentRef.current, reasoningSteps: lastCleanStepsRef.current }
              : m
          )
        )
      } else {
        const userId = newMessageId()
        assistantId = newMessageId()
        setMessages((prev) => [
          ...prev,
          { id: userId, role: "user", content: text },
          { id: assistantId, role: "assistant", content: "", reasoningSteps: [] },
        ])
        setInput("")
        if (textareaRef.current) textareaRef.current.style.height = ""
      }
      setGenerating(true)
      // 修复轮开始 → 退出"正在自动修复"提示态(轮次本身由 attemptedRef 限流)
      if (yamlRepairing) setYamlRepairing(false)

      const context: AgentChatContext = {
        intent: "strategy_chat",
        strategy_id: strategyId,
        strategy_name: strategyName,
        symbol,
        timeframe,
      }

      let saveToolFired = false

      const onEvent = (event: { type: string; data: unknown }) => {
        if (!mountedRef.current) return

        if (event.type === "heartbeat") {
          const hbData = event.data as Record<string, unknown> | undefined
          const step = String(hbData?.step ?? "处理中")
          setHeartbeatText(step)
          return
        }

        // B0 确认门:审批请求/结果/结束统一交给共享状态机(与 FAB 面、对话页、
        // 终端同源)。不 return —— tool_result 下面还要归约进推理链。
        if (
          event.type === "tool_approval_request" ||
          event.type === "tool_result" ||
          event.type === "done"
        ) {
          handleApprovalEvent(
            event.type,
            (typeof event.data === "object" && event.data !== null
              ? event.data
              : {}) as Record<string, unknown>,
          )
        }

        if (isReasoningEvent(event.type)) {
          setHeartbeatText("")
          const step: ReasoningStep = {
            ...reasoningStepFromEvent(event),
            timestamp: Date.now(),
          }
          const structuredQuickReplies = extractStructuredQuickReplies(event.data)
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    reasoningSteps: [...(m.reasoningSteps ?? []), step],
                    quickReplies: structuredQuickReplies.length ? structuredQuickReplies : m.quickReplies,
                  }
                : m
            )
          )

          if (event.type === "tool_result") {
            const toolData = event.data as Record<string, unknown> | undefined
            const toolName = String(toolData?.tool ?? "")
            const toolOutput = String(toolData?.output ?? "")
            if (toolName === "backtest_run" && onBacktestResult) {
              const btId = extractBacktestId(toolOutput)
              if (btId) {
                getBacktest(btId).then((res) => {
                  if (res.data && mountedRef.current) {
                    onBacktestResult(res.data)
                  }
                })
              }
            }
            if (
              (toolName === "save_strategy_version" || toolName === "deploy_strategy" || toolName === "stop_strategy") &&
              onStrategyRefresh
            ) {
              saveToolFired = true
              onStrategyRefresh()
            }
          }
          return
        }

        if (event.type === "text_delta" || event.type === "text") {
          setHeartbeatText("")
          const chunk = deltaTextFromData(event.data)
          if (!chunk) return
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m
              const nextContent = m.content + chunk
              const { quickReplies } = extractQuickReplies(nextContent)
              return { ...m, content: nextContent, quickReplies: m.quickReplies?.length ? m.quickReplies : quickReplies }
            })
          )
          return
        }
        if (event.type === "reconnecting") {
          setReconnecting(true)
          const rd = event.data as Record<string, unknown> | undefined
          setHeartbeatText(`重连中 (${rd?.attempt ?? ""}/${rd?.maxAttempts ?? ""})...`)
          return
        }
        if (event.type === "reconnected") {
          setReconnecting(false)
          setHeartbeatText("已重连，继续处理...")
          return
        }
        if (event.type === "error") {
          setHeartbeatText("")
          setReconnecting(false)
          const err =
            typeof event.data === "string"
              ? event.data
              : event.data != null
                ? String(event.data)
                : "Error"
          setMessages((prev) => {
            const current = prev.find((m) => m.id === assistantId)
            lastCleanContentRef.current = current?.content ?? ""
            lastCleanStepsRef.current = current?.reasoningSteps ?? []
            lastAssistantIdRef.current = assistantId
            return prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    content: m.content + (m.content ? "\n\n" : "") + `⚠️ ${err}`,
                  }
                : m
            )
          })
          setLastFailedMessage(text)
          setGenerating(false)
          return
        }
        if (event.type === "done") {
          setHeartbeatText("")
          setReconnecting(false)
          const doneData = event.data as Record<string, unknown> | undefined
          const errorMsg = doneData?.error
          if (errorMsg) {
            setMessages((prev) => {
              const current = prev.find((m) => m.id === assistantId)
              lastCleanContentRef.current = current?.content ?? ""
              lastCleanStepsRef.current = current?.reasoningSteps ?? []
              lastAssistantIdRef.current = assistantId
              return prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      content: m.content + (m.content ? "\n\n" : "") + `⚠️ ${String(errorMsg)}`,
                    }
                  : m
              )
            })
            setLastFailedMessage(text)
          } else {
            setLastFailedMessage("")
          }
          setGenerating(false)
          setTimeout(() => flushSave(), 50)

          // US-104 自修复环: apply 前先验证;失败把错误喂回同一会话让 AI 修一轮
          // (最多 1 次);双失败在 chat 里诚实展示两条错误,不静默丢弃 YAML。
          if (!errorMsg && onApplyYaml) {
            const last = messagesRef.current.find((m) => m.id === assistantId)
            const yaml = last?.content ? extractYamlBlock(last.content) : null
            if (yaml) {
              const verdict = validateStrategyYaml(yaml)
              if (verdict.ok) {
                onApplyYaml(yaml)
              } else if (!yamlRepairAttemptedRef.current) {
                yamlRepairAttemptedRef.current = true
                setYamlRepairing(true)
                // done 事件在外层 sendAgentChat await 返回前触发,sendingRef 仍为 true;
                // 直接 sendWithText 会被入口 guard 吞掉,推迟到宏任务让 finally 先复位。
                setTimeout(() => {
                  void sendWithText(buildRepairPrompt(yaml, verdict.error ?? tr("YAML 校验失败")))
                }, 0)
              } else {
                // 修复过一轮仍失败 → 诚实展示,不再自动重试
                setMessages((prev) => [
                  ...prev,
                  {
                    id: newMessageId(),
                    role: "assistant",
                    content: tr("⚠️ 自动修复仍未通过校验:{err}\n\nYAML 未被应用。你可以切换到「高级 · YAML」标签手动修正。", {
                      err: verdict.error ?? tr("未知错误"),
                    }),
                  },
                ])
              }
            }
          }

          if (saveToolFired && onStrategyRefresh) {
            setTimeout(() => onStrategyRefresh(), 300)
          }
        }
      }

      const controller = new AbortController()
      abortRef.current = controller
      const timeout = setTimeout(() => controller.abort(), 10 * 60 * 1000)

      try {
        const result: AgentChatResult = await sendAgentChat(
          text, onEvent, context, effectiveSessionId, controller.signal,
          // B0 门会让后端 producer 阻塞等人裁决(流不关),审批卡此刻已可点。
          // session id 随 X-Session-ID 响应头先到 —— 立刻回报,否则新会话首轮的
          // getSessionId() 读到 undefined,点「批准」静默 no-op。
          {
            ...(isResume ? { resume: true } : {}),
            onSessionId: (sid) => {
              sessionIdRef.current = sid
              if (mountedRef.current) setSessionId(sid)
            },
          },
        )
        if (mountedRef.current && result.sessionId) setSessionId(result.sessionId)
      } finally {
        clearTimeout(timeout)
        abortRef.current = null
        sendingRef.current = false
        if (mountedRef.current) {
          setGenerating(false)
          setHeartbeatText("")
          setReconnecting(false)
        }
      }
    },
    [strategyId, strategyName, symbol, timeframe, sessionId, onBacktestResult, onStrategyRefresh, onApplyYaml, refreshSessionList, flushSave]
  )

  const handleSend = useCallback(() => {
    void sendWithText(input)
  }, [input, sendWithText])

  const handleQuickReplySelect = useCallback(
    (value: string) => {
      void sendWithText(value)
    },
    [sendWithText]
  )

  const handleSkillClick = useCallback(
    (skill: (typeof skillDefs)[number]) => {
      const prompt = skill.prompt.replace(/\{name\}/g, strategyName)
      setInput(prompt)
      setActiveTab("chat")
      void sendWithText(prompt)
    },
    [strategyName, sendWithText]
  )

  return (
    <Tabs
      value={activeTab}
      onValueChange={(value) => setActiveTab(value as AiSidebarTab)}
      className="flex h-full min-h-0 flex-col overflow-hidden"
    >
      <div className="shrink-0 border-b border-border/60 px-2.5 py-1.5">
        <div className="flex items-center gap-1.5">
          <Sparkles className="h-4 w-4 shrink-0" />
          <span className="truncate text-[11px] font-semibold text-foreground">{tr("AI 助手")}</span>
          {symbol && <span className="text-[10px] text-muted-foreground">{symbol}</span>}
          {timeframe && <span className="text-[10px] text-muted-foreground">{timeframe}</span>}
          {sessionId && <span className="h-1.5 w-1.5 rounded-full bg-pos" title="Live" />}
          <div className="ml-auto flex items-center gap-0.5">
            <button
              type="button"
              onClick={() => void handleNewConversation()}
              disabled={generating}
              className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
              title={tr("新对话")}
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => { setShowHistory((v) => !v); void refreshSessionList() }}
              className={cn("rounded-md p-1 transition-colors", showHistory ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground")}
              title={tr("历史记录")}
            >
              <History className="h-3.5 w-3.5" />
            </button>
            {onCollapse && (
              <button
                type="button"
                onClick={onCollapse}
                className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        {showHistory && (
          <div className="mt-1.5 max-h-44 overflow-y-auto rounded-lg border border-border/70 bg-card/90 p-1.5">
            {sessions.length === 0 ? (
              <p className="py-2 text-center text-[11px] text-muted-foreground">{tr("暂无历史记录")}</p>
            ) : (
              <div className="space-y-0.5">
                {sessions.map((s) => (
                  <div
                    key={s.id}
                    className={cn(
                      "group flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-left transition-colors",
                      s.id === sessionId ? "bg-primary/10 text-foreground" : "cursor-pointer hover:bg-muted/60"
                    )}
                  >
                    <button
                      type="button"
                      className="flex min-w-0 flex-1 items-center gap-1.5"
                      onClick={() => void handleSwitchSession(s.id)}
                    >
                      <MessageCircle className="h-3 w-3 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1 truncate text-[11px]">
                        {s.title || tr("未命名会话")}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="hidden h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-destructive/10 hover:text-destructive group-hover:flex"
                      onClick={(e) => { e.stopPropagation(); void handleDeleteSession(s.id) }}
                    >
                      <X className="h-2.5 w-2.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <TabsList className="mt-1.5 grid h-8 w-full grid-cols-3 rounded-lg bg-muted/50 p-0.5">
          <TabsTrigger value="chat" className="gap-1 rounded-md text-[11px] data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm">
            <MessageSquareText className="h-3 w-3" />
            {tr("对话")}
          </TabsTrigger>
          <TabsTrigger value="params" className="gap-1 whitespace-nowrap rounded-md text-[11px] data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm">
            <SlidersHorizontal className="h-3 w-3" />
            {tr("参数")}
          </TabsTrigger>
          <TabsTrigger value="yaml" className="gap-1 whitespace-nowrap rounded-md text-[11px] data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm">
            <FileCode className="h-3 w-3" />
            {tr("高级 · YAML")}
          </TabsTrigger>
        </TabsList>
      </div>

      <TabsContent value="chat" className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-2.5" ref={messagesEndRef}>
          <div className="space-y-2.5">
            {messages.length === 0 ? (
              <AssistantEmptyState
                title={tr("AI 助手")}
                subtitle={tr("直接描述你想构建、解释或优化的策略，助手会结合当前策略上下文继续完成。")}
              />
            ) : (
              messages.map((msg) => {
                const isLatestAssistant = msg.role === "assistant" && msg.id === messages[messages.length - 1]?.id

                return (
                  <div key={msg.id} className={cn("flex", msg.role === "user" ? "justify-end" : "justify-start")}>
                    <div
                      className={cn(
                        "max-w-[92%] rounded-lg border px-3 py-2 shadow-sm",
                        msg.role === "user"
                          ? "border-primary/20 bg-primary/10 text-foreground"
                          : "border-border/70 bg-background/65 text-muted-foreground"
                      )}
                    >
                      {msg.role === "user" ? (
                        <p className="whitespace-pre-wrap text-[11px] leading-5 text-foreground">{msg.content}</p>
                      ) : (
                        <AssistantMessageContent
                          content={msg.content}
                          generating={generating && isLatestAssistant}
                          reasoningSteps={msg.reasoningSteps}
                          quickReplies={msg.quickReplies}
                          onQuickReplySelect={handleQuickReplySelect}
                          onApplyYaml={onApplyYaml}
                          applyLabel={tr("应用更改")}
                        />
                      )}
                    </div>
                  </div>
                )
              })
            )}

            {generating && (
              <div className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-[11px] ${
                reconnecting
                  ? "border-warn/40 bg-warn/5 text-warn"
                  : "border-border/70 bg-background/40 text-muted-foreground"
              }`}>
                <Loader2 className="h-3 w-3 animate-spin" />
                <span>{heartbeatText ? tr(heartbeatText) : tr("思考中...")}</span>
              </div>
            )}

            {/* US-104:YAML 校验失败,自动修复轮进行中 */}
            {yamlRepairing && (
              <div className="flex items-center gap-2 rounded-lg border border-warn/40 bg-warn/5 px-3 py-2 text-[11px] text-warn">
                <Loader2 className="h-3 w-3 animate-spin" />
                <span>{tr("校验失败，正在自动修复…")}</span>
              </div>
            )}

            {!generating && lastFailedMessage && (
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 rounded-lg border-warn/30 bg-warn/5 text-[11px] text-warn hover:bg-warn/10"
                  onClick={() => {
                    const msg = lastFailedMessage
                    setLastFailedMessage("")
                    void sendWithText(msg, { resume: true })
                  }}
                >
                  {tr("↻ 继续")}
                </Button>
              </div>
            )}

            {/* B0 确认门:写工具被后端拦下,等人裁决。此前本面不渲染 →
                工具调用静默挂起到 TTL 超时。 */}
            {pendingApprovals.length > 0 && (
              <div className="rounded-lg border border-warn/40 bg-warn/5 px-3 py-2.5">
                <p className="mb-1.5 text-[11px] font-medium text-warn">
                  {tr("Agent 请求执行以下操作，需要确认")}
                </p>
                {approvalError && (
                  <p className="mb-1.5 text-[10px] text-destructive">{formatPending(approvalError, tr)}</p>
                )}
                <div className="space-y-2">
                  {pendingApprovals.map((p) => (
                    <div key={p.toolCallId} className="rounded-md border border-border/70 bg-background/60 px-2.5 py-2">
                      <p className="text-[11px] font-medium text-foreground">{p.toolName}</p>
                      <p className="mt-0.5 break-all font-mono text-[10px] leading-4 text-muted-foreground line-clamp-3">
                        {Object.keys(p.arguments).length > 0
                          ? JSON.stringify(p.arguments)
                          : tr("（无参数）")}
                      </p>
                      <div className="mt-2 flex gap-2">
                        <Button
                          size="sm"
                          className="h-6 px-2.5 text-[11px]"
                          disabled={approvingIds.has(p.toolCallId)}
                          onClick={() => void approve(p.toolCallId, true)}
                        >
                          {tr("批准")}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 px-2.5 text-[11px] text-muted-foreground hover:text-destructive"
                          disabled={approvingIds.has(p.toolCallId)}
                          onClick={() => void approve(p.toolCallId, false)}
                        >
                          {tr("拒绝")}
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="shrink-0 border-t border-border/70 bg-card/80 px-2.5 py-2 backdrop-blur-sm">
          <div className="mb-1.5 flex flex-wrap gap-1.5">
            {skillDefs.map((skill) => {
              const Icon = skill.icon
              return (
                <button
                  key={skill.id}
                  type="button"
                  onClick={() => handleSkillClick(skill)}
                  disabled={generating}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border/70 bg-background/50 px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-primary disabled:pointer-events-none disabled:opacity-50"
                >
                  <Icon className="h-3 w-3" />
                  {skill.label}
                </button>
              )
            })}
          </div>
          <div className="flex items-end gap-2 rounded-lg border border-border/70 bg-background/55 px-2 py-1.5">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => {
                setInput(e.target.value)
                const el = e.target
                el.style.height = "0"
                el.style.height = `${Math.min(el.scrollHeight, 160)}px`
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  handleSend()
                }
              }}
              placeholder={tr("描述你想做的策略...")}
              className="max-h-[160px] min-h-[32px] flex-1 resize-none overflow-y-auto border-0 bg-transparent px-1 py-1 text-[11px] leading-[1.6] text-foreground shadow-none outline-none placeholder:text-muted-foreground focus-visible:ring-0"
              disabled={generating}
              rows={1}
            />
            <Button
              size="icon"
              className="mb-0.5 h-8 w-8 shrink-0 rounded-lg"
              onClick={handleSend}
              disabled={!input.trim() || generating}
            >
              {generating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
            </Button>
          </div>
        </div>
      </TabsContent>

      <TabsContent value="yaml" className="mt-0 min-h-0 flex-1 overflow-hidden px-3 pb-3">
        <PanelSection
          title={tr("高级 · YAML 配置")}
          description={tr("查看生成的策略结构并直接编辑。推荐用参数面板修改，YAML 仅供高级用户直接编辑结构。")}
        >
          {strategy ? <YamlEditor strategy={strategy} onChange={onStrategyChange} /> : null}
        </PanelSection>
      </TabsContent>

      <TabsContent value="params" className="mt-0 min-h-0 flex-1 overflow-hidden px-3 pb-3">
        <PanelSection
          title={tr("参数面板")}
          description={tr("调整策略设置和规则，使用与助手相同的工作区框架。")}
        >
          {strategy ? <ParamPanel strategy={strategy} onChange={onStrategyChange} /> : null}
        </PanelSection>
      </TabsContent>
    </Tabs>
  )
}
