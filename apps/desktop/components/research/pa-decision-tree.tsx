"use client";

// B5 决策树可视化 — 真决策树 txt 经 py parser 解析后的交互渲染。
// 数据源:GET /api/v1/decision-workflow/trees/trading_decision/graph(单一真源,
// 前端不自己解析 txt)。纯前端只消费 JSON,交互范式自研。

import React, { useState } from "react";
import { useApi } from "@/lib/hooks/use-api";
import { cn } from "@/lib/utils";
import { Loader2, GitBranch, ChevronDown, ChevronRight } from "lucide-react";

interface TreeBranch {
  label: string;
  target_ref: string | null;
  terminal_outcome: string | null;
  tag: string | null;
  tag_action: string | null;
}

interface TreeNode {
  node_id: string;
  section: string;
  question: string;
  node_type: string;
  branch_type: string;
  branches: TreeBranch[];
}

interface TreeGraph {
  tree_id: string;
  entry_node_id: string;
  terminals: string[];
  nodes: Record<string, TreeNode>;
}

const TERMINAL_TONES: Record<string, string> = {
  trade: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  wait: "border-amber-500/40 bg-amber-500/10 text-amber-300",
  reject: "border-red-500/40 bg-red-500/10 text-red-300",
  proceed: "border-sky-500/40 bg-sky-500/10 text-sky-300",
};

const BRANCH_TONES: Record<string, string> = {
  是: "border-emerald-500/25 bg-emerald-500/8 text-emerald-200 hover:border-emerald-500/50",
  否: "border-red-500/25 bg-red-500/8 text-red-200 hover:border-red-500/50",
  中性: "border-amber-500/25 bg-amber-500/8 text-amber-200 hover:border-amber-500/50",
};

function branchTone(label: string): string {
  const key = label.replace(/\[.+\]/, "");
  return BRANCH_TONES[key] ?? "border-border/60 bg-background/40 text-foreground hover:border-primary/40";
}

function NodeCard({
  node,
  graph,
  activeNodeId,
  onNavigate,
  depth,
}: {
  node: TreeNode;
  graph: TreeGraph;
  activeNodeId: string;
  onNavigate: (nodeId: string) => void;
  depth: number;
}) {
  const isActive = node.node_id === activeNodeId;
  return (
    <div
      className={cn(
        "min-w-[280px] max-w-[360px] rounded-2xl border p-3 transition-colors",
        isActive ? "border-primary/50 bg-primary/5" : "border-border/70 bg-background/40"
      )}
    >
      <div className="flex items-center gap-1.5">
        <span className="rounded bg-muted/70 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          §{node.node_id}
        </span>
        <span className="truncate text-[10px] text-muted-foreground" title={node.section}>
          {node.section}
        </span>
        {node.node_id === graph.entry_node_id && (
          <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[10px] text-primary">入口</span>
        )}
      </div>
      <p className="mt-1.5 text-[12px] leading-5 text-foreground">{node.question}</p>
      <div className="mt-2 flex flex-col gap-1.5">
        {node.branches.map((b, i) => {
          if (b.terminal_outcome) {
            return (
              <div
                key={i}
                className={cn(
                  "flex items-center justify-between rounded-lg border px-2 py-1.5 text-[11px]",
                  TERMINAL_TONES[b.terminal_outcome] ?? "border-border/60 bg-background/40 text-foreground"
                )}
              >
                <span>{b.label}</span>
                <span className="font-medium">终止 → {b.terminal_outcome}</span>
              </div>
            );
          }
          if (b.target_ref) {
            const target = graph.nodes[b.target_ref];
            return (
              <button
                key={i}
                type="button"
                onClick={() => onNavigate(b.target_ref!)}
                className={cn(
                  "flex items-center justify-between rounded-lg border px-2 py-1.5 text-[11px] transition-colors",
                  branchTone(b.label)
                )}
              >
                <span>{b.label}</span>
                <span className="flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
                  §{b.target_ref}
                  {(target?.question ?? "").slice(0, 12)}…
                </span>
              </button>
            );
          }
          return (
            <div
              key={i}
              className="flex items-center justify-between rounded-lg border border-border/60 bg-background/30 px-2 py-1.5 text-[11px] text-muted-foreground"
            >
              <span>{b.label}</span>
              <span className="text-[10px]">{b.tag ? `标记 ${b.tag} ${b.tag_action ?? ""}` : "→ 继续"}</span>
            </div>
          );
        })}
      </div>
      {depth > 0 && (
        <p className="mt-2 text-[10px] text-muted-foreground/60">深度 {depth} · 从入口可达</p>
      )}
    </div>
  );
}

export function PaDecisionTreeView({ treeId }: { treeId: string }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [activeNodeId, setActiveNodeId] = useState<string | null>(null);

  const { data, error, isLoading } = useApi<TreeGraph>(
    `/api/v1/decision-workflow/trees/${encodeURIComponent(treeId)}/graph`
  );

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-[12px] text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" /> 加载决策树…
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-[12px] text-destructive">
        决策树加载失败(后端未启动或树不存在)
      </div>
    );
  }
  if (Object.keys(data.nodes).length === 0) {
    return (
      <div className="rounded-xl border border-border/60 bg-background/40 px-3 py-2.5 text-[12px] text-muted-foreground">
        该树没有节点
      </div>
    );
  }

  const active = activeNodeId ? data.nodes[activeNodeId] : null;

  return (
    <div className="h-full overflow-y-auto pb-[300px]">
      <div className="flex items-center gap-2 px-1 pb-2">
        <GitBranch className="h-4 w-4 text-primary" />
        <span className="text-[13px] font-semibold text-foreground">决策树 · {treeId}</span>
        <span className="rounded bg-muted/60 px-1.5 py-0.5 text-[10px] text-muted-foreground">
          {Object.keys(data.nodes).length} 节点 · 终止词 {data.terminals.join("/")}
        </span>
      </div>

      {/* 全结构平铺(深度分列) */}
      <div className="flex gap-3 overflow-x-auto px-1 pb-3">
        {(() => {
          // Group nodes by BFS depth into columns.
          const depthOf = new Map<string, number>();
          const seen = new Set<string>([data.entry_node_id]);
          let frontier = [data.entry_node_id];
          let d = 0;
          while (frontier.length) {
            const next: string[] = [];
            for (const nid of frontier) {
              depthOf.set(nid, d);
              const node = data.nodes[nid];
              if (!node) continue;
              for (const b of node.branches) {
                if (b.target_ref && !seen.has(b.target_ref)) {
                  seen.add(b.target_ref);
                  next.push(b.target_ref);
                }
              }
            }
            frontier = next;
            d += 1;
          }
          const cols: { nodeId: string; depth: number }[][] = [];
          for (const [nid, dep] of depthOf) {
            cols[dep] = cols[dep] ?? [];
            cols[dep].push({ nodeId: nid, depth: dep });
          }
          return cols.map((col, di) => (
            <div key={di} className="flex shrink-0 flex-col gap-2">
              <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">深度 {di}</p>
              {col.map(({ nodeId }) => (
                <NodeCard
                  key={nodeId}
                  node={data.nodes[nodeId]}
                  graph={data}
                  activeNodeId={activeNodeId ?? ""}
                  onNavigate={(id) => setActiveNodeId(id)}
                  depth={di}
                />
              ))}
            </div>
          ));
        })()}
      </div>

      {/* 交互路径回放:点分支按钮从入口走一遍 */}
      <div className="mt-1 rounded-2xl border border-border/70 bg-background/40 p-3">
        <p className="flex items-center gap-1.5 text-[12px] font-medium text-foreground">
          <ChevronRight className="h-3.5 w-3.5 text-primary" /> 路径试走 — 点分支从入口走到终止
        </p>
        <div className="mt-2 space-y-2">
          {(() => {
            const path: { nodeId: string; branchLabel: string | null; terminal: string | null }[] = [];
            let cur: string | null = data.entry_node_id;
            const visited = new Set<string>();
            while (cur && !visited.has(cur)) {
              visited.add(cur);
              const node: TreeNode | undefined = data.nodes[cur];
              if (!node) break;
              const chosen: TreeBranch | undefined = node.branches.find(
                (b: TreeBranch) => expanded.has(`${cur}:${b.label}`)
              );
              if (!chosen) {
                path.push({ nodeId: cur, branchLabel: null, terminal: null });
                break;
              }
              path.push({
                nodeId: cur,
                branchLabel: chosen.label,
                terminal: chosen.terminal_outcome ?? null,
              });
              if (chosen.terminal_outcome) break;
              cur = chosen.target_ref;
            }
            const finalTerminal = path.length ? path[path.length - 1].terminal : null;
            return (
              <>
                {path.map((step, i) => {
                  const node = data.nodes[step.nodeId];
                  return (
                    <div key={i}>
                      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                        {i > 0 && <ChevronDown className="h-3 w-3" />}
                        <span className="font-mono">§{step.nodeId}</span>
                        {step.branchLabel && (
                          <span className="rounded bg-muted/60 px-1.5 py-0.5 text-[10px]">
                            走了「{step.branchLabel}」
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 pl-5 text-[12px] leading-5 text-foreground">{node?.question}</p>
                      {/* 该节点所有分支:选中的高亮,未选的可点 */}
                      <div className="mt-1 flex flex-wrap gap-1.5 pl-5">
                        {node?.branches.map((b, bi) => {
                          const key = `${step.nodeId}:${b.label}`;
                          const isChosen = expanded.has(key);
                          return (
                            <button
                              key={bi}
                              type="button"
                              onClick={() =>
                                setExpanded((prev) => {
                                  const next = new Set(prev);
                                  // 同一节点互斥选择:清掉兄弟分支再选当前
                                  node.branches.forEach((sib) => {
                                    next.delete(`${step.nodeId}:${sib.label}`);
                                  });
                                  if (!isChosen) next.add(key);
                                  return next;
                                })
                              }
                              className={cn(
                                "rounded-md border px-2 py-1 text-[11px] transition-colors",
                                isChosen
                                  ? b.terminal_outcome
                                    ? TERMINAL_TONES[b.terminal_outcome] ??
                                      "border-primary/50 bg-primary/15 text-primary"
                                    : "border-primary/50 bg-primary/15 text-primary"
                                  : branchTone(b.label)
                              )}
                            >
                              {b.terminal_outcome
                                ? `${b.label} → 终止 ${b.terminal_outcome}`
                                : `${b.label} → §${b.target_ref}`}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
                {finalTerminal && (
                  <div
                    className={cn(
                      "mt-2 rounded-lg border px-3 py-2 text-[12px] font-medium",
                      TERMINAL_TONES[finalTerminal] ?? "border-border/60 bg-background/40 text-foreground"
                    )}
                  >
                    本次试走结论 → {finalTerminal}
                  </div>
                )}
              </>
            );
          })()}
        </div>
        {active && (
          <div className="mt-3 border-t border-border/60 pt-2 text-[11px] text-muted-foreground">
            最近查看:§{active.node_id} — {active.question}
          </div>
        )}
      </div>
    </div>
  );
}
