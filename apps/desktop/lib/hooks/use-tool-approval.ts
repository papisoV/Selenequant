"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { approveToolCall } from "@/lib/api/chat";
import type { PendingMessage } from "@/lib/i18n/types";

// B0 确认门的前端半边 —— 所有 agent 面共用**这一份**,避免各写一遍再漂移。
//
// B2 的教训:两份 SSE 归约实现必然分叉(page 面当时就漏了整个审批分支,
// tool_approval_request 无人接 → 审批卡永不出现 → 工具调用静默挂起直到超时)。
// 所以这里只承载「待审批卡的生命周期」,渲染留给各面(设计语言不同)。

export interface PendingApproval {
  toolCallId: string;
  toolName: string;
  arguments: Record<string, unknown>;
}

export interface UseToolApprovalOptions {
  /**
   * 读当前会话 id —— 传 **getter,不是值**。
   *
   * 理由:审批卡在 SSE 流**尚未结束**时就要可点(后端 producer 正 `await`
   * 那个 Future,见 services/local-api/app/api/v1/agent.py:942-980),而调用方的
   * sessionId state 通常要等流收尾才落。闭包捕获值会拿到 `undefined`,
   * 点「批准」静默 no-op —— 人以为点了,审批永不到达后端。getter 每次读最新。
   */
  getSessionId: () => string | undefined;
  /** 会话尚未建立时的提示文案。 */
  noSessionMessage?: string;
}

export interface UseToolApprovalResult {
  pendingApprovals: PendingApproval[];
  approvingIds: Set<string>;
  /** 裁决失败的**源串**(渲染面用 formatPending 取当前 locale 文案)。 */
  approvalError: PendingMessage | null;
  /** 把每个 SSE 事件喂进来;只消化 tool_approval_request / tool_result / done。 */
  handleApprovalEvent: (type: string, data: Record<string, unknown>) => void;
  /** 裁决一个待审批工具调用。 */
  approve: (toolCallId: string, approve: boolean) => Promise<void>;
  /** 清空(新对话 / 载入历史会话时)。 */
  reset: () => void;
}

export function useToolApproval({
  getSessionId,
  noSessionMessage = "会话尚未建立，请稍后重试",
}: UseToolApprovalOptions): UseToolApprovalResult {
  const [pendingApprovals, setPendingApprovals] = useState<PendingApproval[]>([]);
  const [approvingIds, setApprovingIds] = useState<Set<string>>(new Set());
  const [approvalError, setApprovalError] = useState<PendingMessage | null>(null);

  // 本轮已见的 tool_call_id:重连重放时去重(同一请求可能被重发)。
  const seenIdsRef = useRef<Set<string>>(new Set());

  // getSessionId 常是内联箭头函数,用 ref 固定,免得 approve 身份每渲染漂移。
  // 在 effect 里赋值而非渲染期 —— 点击发生在渲染很久之后,读到的必然是最新值。
  const getSessionIdRef = useRef(getSessionId);
  useEffect(() => {
    getSessionIdRef.current = getSessionId;
  }, [getSessionId]);

  const clearMatching = useCallback((d: Record<string, unknown>) => {
    const toolCallId = typeof d.tool_call_id === "string" ? d.tool_call_id : "";
    const toolName = typeof d.name === "string" ? d.name : "";
    setPendingApprovals((prev) => {
      if (toolCallId) {
        const rest = prev.filter((p) => p.toolCallId !== toolCallId);
        if (rest.length < prev.length) return rest;
      }
      // 退路:老事件流不带 tool_call_id 时按工具名清。同轮两次同名调用会
      // 互相误清 —— 但那种情况后端一定带 id(react.py:379),上面已精确命中。
      return toolName ? prev.filter((p) => p.toolName !== toolName) : prev;
    });
  }, []);

  const handleApprovalEvent = useCallback(
    (type: string, data: Record<string, unknown>) => {
      if (type === "tool_approval_request") {
        const toolCallId = typeof data.tool_call_id === "string" ? data.tool_call_id : "";
        if (!toolCallId || seenIdsRef.current.has(toolCallId)) return;
        seenIdsRef.current.add(toolCallId);
        setPendingApprovals((prev) => [
          ...prev,
          {
            toolCallId,
            toolName: typeof data.tool_name === "string" ? data.tool_name : "unknown",
            arguments:
              typeof data.arguments === "object" && data.arguments !== null
                ? (data.arguments as Record<string, unknown>)
                : {},
          },
        ]);
      } else if (type === "tool_result") {
        // 被 gate 的工具结果回带 tool_call_id(react.py:379)→ 精确清卡,
        // 不再让审批卡滞留到 done。
        clearMatching(data);
      } else if (type === "done") {
        seenIdsRef.current.clear();
        setPendingApprovals([]);
        setApprovalError(null);
      }
    },
    [clearMatching],
  );

  const approve = useCallback(
    async (toolCallId: string, doApprove: boolean) => {
      const sid = getSessionIdRef.current();
      if (!sid) {
        setApprovalError(noSessionMessage);
        return;
      }
      setApprovingIds((prev) => new Set(prev).add(toolCallId));
      setApprovalError(null);
      const result = await approveToolCall(sid, toolCallId, doApprove);
      if (result.ok || result.errorCode === "no_pending_approval") {
        // no_pending_approval = 已被处理(重放/超时),本地静默清除即可。
        setPendingApprovals((prev) => prev.filter((p) => p.toolCallId !== toolCallId));
      } else {
        setApprovalError(
          result.errorCode === "forbidden"
            ? "无权操作该会话的审批"
            : { source: "审批请求失败（{code}）", params: { code: result.errorCode ?? "?" } },
        );
      }
      setApprovingIds((prev) => {
        const next = new Set(prev);
        next.delete(toolCallId);
        return next;
      });
    },
    [noSessionMessage],
  );

  const reset = useCallback(() => {
    seenIdsRef.current.clear();
    setPendingApprovals([]);
    setApprovingIds(new Set());
    setApprovalError(null);
  }, []);

  return {
    pendingApprovals,
    approvingIds,
    approvalError,
    handleApprovalEvent,
    approve,
    reset,
  };
}
