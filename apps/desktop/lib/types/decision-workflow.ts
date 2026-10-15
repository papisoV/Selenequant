// PA = Price Action analysis (decision workflow UI) — AGPL-clean naming
// TypeScript mirrors of backend Pydantic models in
// packages/decision-workflow/selene_space_decision_workflow/models.py

export interface NodeAnswer {
  node_id: string
  question: string
  answer: string
  bar_range: string | null
  reason: string
  branch: string
  program_prefill_nodes: string[]
}

export interface NodeOverride {
  node_id: string
  new_answer: string
  reason: string
  bar_range: string | null
}

export type GateResult = "proceed" | "wait" | "unknown"
export type TerminalOutcome = "trade" | "wait" | "reject" | "proceed"

export interface Stage1Result {
  state: string
  gate_result: GateResult
  trace: NodeAnswer[]
  diagnosis_summary: Record<string, unknown>
  node_overrides: NodeOverride[]
}

export interface Stage2Result {
  decision: string
  terminal_outcome: TerminalOutcome
  order: Record<string, unknown> | null
  trace: NodeAnswer[]
  decision_summary: Record<string, unknown>
  node_overrides: NodeOverride[]
}

export interface RecordMeta {
  timestamp: string
  input_id: string
  session_id: string
}

export interface StageRecord {
  messages: Record<string, unknown>[]
  response: Record<string, unknown>
  diagnosis_or_decision: Record<string, unknown>
  trace: NodeAnswer[]
}

export interface AnalysisRecord {
  meta: RecordMeta
  input_data: Record<string, unknown>
  stage1: StageRecord
  stage2: StageRecord | null
  strategy_files_used: string[]
  usage_total: Record<string, number>
}

export interface AnalyzeRequest {
  tree_id: string
  input_data: Record<string, unknown>
  input_id?: string
  session_id?: string
}

export type AnalyzeResponse = AnalysisRecord

export interface TreeInfo {
  tree_id: string
  title: string
  description: string
  node_count: number
}

export interface ApiErrorShape {
  detail: string
  stage?: string
  field?: string
  reason?: string
  errors?: unknown[]
}
