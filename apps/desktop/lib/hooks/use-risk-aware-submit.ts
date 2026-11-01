"use client";

import { useCallback, useRef, useState } from "react";
import { apiPost } from "@/lib/api-client";
import { toastError } from "@/lib/toast";
import { useUiText } from "@/components/i18n/use-ui-text";
import type { RiskAlert as RiskAlertDialogAlert } from "@/components/trading/risk-alert-dialog";

// 复用 RiskAlertDialog 的 RiskAlert 类型，保证 alerts prop 直接可传
export type RiskAlert = RiskAlertDialogAlert;

// 内部判定用的最小 shape；TResult 只需满足 has_risk_alerts 字段即可，
// risk_alerts 由调用方按后端契约定义（兼容 rule_id 可选/必填差异）
interface RiskAwareResult {
  has_risk_alerts?: boolean;
  risk_alerts?: unknown[];
}

interface UseRiskAwareSubmitOptions<TParams, TResult> {
  url: string;
  onPending?: () => void;
  onSuccess?: (data: TResult) => void;
  onError?: (err: Error) => void;
}

interface PendingState<TParams> {
  params: TParams;
  ts: number;
}

const PENDING_TTL_MS = 30_000;

export function useRiskAwareSubmit<
  TParams extends object,
  TResult extends RiskAwareResult = RiskAwareResult
>(opts: UseRiskAwareSubmitOptions<TParams, TResult>) {
  const { url, onPending, onSuccess, onError } = opts;
  const tr = useUiText();

  const [submitting, setSubmitting] = useState(false);
  const [riskAlerts, setRiskAlerts] = useState<RiskAlert[]>([]);
  const [showRiskDialog, setShowRiskDialog] = useState(false);

  // ref 不触发重渲染，confirm 时读取最新值
  const pendingRef = useRef<PendingState<TParams> | null>(null);
  // state 触发弹框（仅用于驱动 UI；confirm 用 ref 兜底防 stale closure）
  const [, setPendingState] = useState<PendingState<TParams> | null>(null);

  const runRequest = useCallback(
    async (params: TParams) => {
      setSubmitting(true);
      try {
        const data = await apiPost<TResult>(url, params);
        if (data?.has_risk_alerts && Array.isArray(data.risk_alerts) && data.risk_alerts.length > 0) {
          // 后端返回的 risk_alerts shape 可能比 RiskAlertDialog.RiskAlert 宽，
          // 但运行时只用 level/message/rule_id；强制断言为 RiskAlert[] 供组件渲染
          setRiskAlerts(data.risk_alerts as RiskAlert[]);
          setShowRiskDialog(true);
          const pending: PendingState<TParams> = { params, ts: Date.now() };
          pendingRef.current = pending;
          setPendingState(pending);
          onPending?.();
          return;
        }
        onSuccess?.(data);
      } catch (e) {
        const err = e instanceof Error ? e : new Error(String(e));
        onError?.(err);
      } finally {
        setSubmitting(false);
      }
    },
    [url, onPending, onSuccess, onError]
  );

  const submit = useCallback(
    async (params: TParams) => {
      // 每次 submit 重置风控状态
      setRiskAlerts([]);
      setShowRiskDialog(false);
      pendingRef.current = null;
      setPendingState(null);
      await runRequest(params);
    },
    [runRequest]
  );

  const confirm = useCallback(() => {
    const pending = pendingRef.current;
    if (!pending) {
      toastError(tr("参数已过期请重新下单"));
      setShowRiskDialog(false);
      return;
    }
    const age = Date.now() - pending.ts;
    if (age > PENDING_TTL_MS) {
      toastError(tr("参数已过期请重新下单"));
      pendingRef.current = null;
      setPendingState(null);
      setShowRiskDialog(false);
      return;
    }
    setShowRiskDialog(false);
    // 强制覆盖 confirm_risk：调用方无权传，防 pre-mortem 场景 A
    const confirmedParams = { ...pending.params, confirm_risk: true } as TParams;
    pendingRef.current = null;
    setPendingState(null);
    void runRequest(confirmedParams);
  }, [runRequest, tr]);

  const cancel = useCallback(() => {
    pendingRef.current = null;
    setPendingState(null);
    setShowRiskDialog(false);
  }, []);

  return {
    submit,
    riskAlerts,
    showRiskDialog,
    confirm,
    cancel,
    submitting,
  };
}
