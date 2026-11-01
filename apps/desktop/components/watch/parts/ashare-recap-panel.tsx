"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useUiText } from "@/components/i18n/use-ui-text";
import { ApiError } from "@/lib/api-client";
import { SessionStamp } from "@/lib/watch/session-stamp";
import {
  streamAshareRecap,
  type RecapEventMeta,
} from "@/lib/api/ashare-recap";

// AI 大盘复盘 —— SSE 流式正文。
//
// 契约(services/local-api/app/api/v1/ashare_overview.py):
//   meta(一行摘要 + 情绪)→ delta* → done;任一阶段可先发 error。
//
// 三条硬规则,全部是"宁可显示没有,也不编一段":
//   1. **不自动开流**。一次复盘是真实的 LLM 调用;进页面就跑等于替用户花钱,
//      而且冷路径装配要 15–25 秒。必须由点击发起。
//   2. **error 原样呈现服务端文案**。未配置密钥时服务端发的是
//      "未配置 LLM 密钥:请先在设置中配置模型后重试"(reason=llm_not_configured)——
//      这句话是可操作的指引;改写成"AI 暂不可用"就把用户卡死在原地。
//   3. **中途失败保留已到正文**。已流出的字是真实产出,清空它再显示错误,
//      用户会以为"什么都没发生"。
//
// 免责声明由服务端保证(缺则补 delta),客户端不补任何文字。

type Status = "idle" | "streaming" | "done" | "error";

interface State {
  status: Status;
  meta: RecapEventMeta | null;
  text: string;
  /** 服务端原话;null = 尚无错误。 */
  errorMessage: string | null;
  /** 服务端 reason(如 llm_not_configured),用于给可操作提示。 */
  errorReason: string | null;
}

const IDLE: State = {
  status: "idle",
  meta: null,
  text: "",
  errorMessage: null,
  errorReason: null,
};

export function AshareRecapPanel({ asOf }: { asOf: string | null }) {
  const tr = useUiText();
  const [state, setState] = useState<State>(IDLE);
  const abortRef = useRef<AbortController | null>(null);
  // 卸载时中止在途流:否则 React 会对已卸载组件 setState,且连接一直挂着。
  useEffect(() => () => abortRef.current?.abort(), []);

  const start = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState({ ...IDLE, status: "streaming" });
    try {
      await streamAshareRecap(
        { date: asOf ?? undefined },
        (ev) => {
          setState((prev) => {
            if (ev.type === "meta") {
              return { ...prev, meta: ev };
            }
            if (ev.type === "delta") {
              return { ...prev, text: prev.text + ev.content };
            }
            if (ev.type === "error") {
              return {
                ...prev,
                status: "error",
                errorMessage: ev.message,
                errorReason: ev.reason ?? null,
              };
            }
            return { ...prev, status: "done" };
          });
        },
        controller.signal,
      );
      setState((prev) =>
        prev.status === "error" ? prev : { ...prev, status: "done" },
      );
    } catch (err) {
      // 主动取消不算错误:用户点了「停止」,不该看到一个红色失败态。
      if (controller.signal.aborted) {
        setState((prev) => (prev.status === "streaming" ? { ...prev, status: "idle" } : prev));
        return;
      }
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : tr("未知错误");
      setState((prev) =>
        prev.status === "error"
          ? prev
          : { ...prev, status: "error", errorMessage: message },
      );
    }
  }, [asOf, tr]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState((prev) => ({ ...prev, status: "idle" }));
  }, []);

  const busy = state.status === "streaming";
  const hasText = state.text.length > 0;

  return (
    <section
      data-testid="ashare-recap-panel"
      className="rounded border border-border-soft bg-surface px-3 py-2"
    >
      <div className="flex items-center justify-between border-b border-border-soft pb-1 mb-2">
        <span className="flex items-baseline gap-2">
          <span className="text-[10px] font-medium text-ink">{tr("AI 大盘复盘")}</span>
          {state.meta?.summary ? (
            <span
              data-testid="recap-summary"
              className="num text-[9px] text-ink-4"
            >
              {state.meta.summary}
            </span>
          ) : null}
        </span>
        <span className="flex items-center gap-2 text-[9px] text-ink-4">
          <SessionStamp asOf={state.meta?.as_of} />
          {busy ? (
            <button
              type="button"
              onClick={stop}
              className="rounded border border-border-soft px-1.5 py-0.5 text-ink-3 transition-colors hover:border-gold-soft hover:text-ink"
            >
              {tr("停止")}
            </button>
          ) : (
            <button
              type="button"
              onClick={start}
              className="rounded border border-gold-soft px-1.5 py-0.5 text-gold transition-colors hover:bg-gold/10"
            >
              {hasText || state.status === "error" ? tr("重新生成") : tr("生成复盘")}
            </button>
          )}
        </span>
      </div>

      {state.status === "idle" && !hasText ? (
        <p className="py-2 text-[11px] leading-relaxed text-ink-3">
          {tr(
            "基于当前大盘截面生成一段复盘正文。点击后才会调用模型(一次真实调用,约数十秒)。",
          )}
        </p>
      ) : null}

      {busy && !hasText ? (
        <p className="flex items-center gap-1.5 py-2 text-[11px] text-ink-3">
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 rounded-full bg-gold motion-safe:animate-pulse"
          />
          {tr("正在生成…")}
        </p>
      ) : null}

      {hasText ? (
        <div
          data-testid="recap-text"
          className="max-w-[72ch] whitespace-pre-wrap text-[11px] leading-relaxed text-ink-2"
        >
          {state.text}
        </div>
      ) : null}

      {busy && hasText ? (
        // 流式光标:仅动效,不作状态唯一载体(旁边还有「停止」按钮)。
        <span
          aria-hidden
          className="ml-0.5 inline-block h-2.5 w-[3px] translate-y-[2px] bg-gold motion-safe:animate-pulse"
        />
      ) : null}

      {state.status === "error" ? (
        <div
          data-testid="recap-error"
          role="alert"
          className="mt-1.5 rounded border border-border-soft bg-surface-2 px-2 py-1.5"
        >
          <p className="text-[10px] leading-relaxed text-warn">
            {state.errorMessage ?? tr("复盘失败")}
          </p>
          {state.errorReason === "llm_not_configured" ? (
            <a
              href="/system/set"
              className="mt-1 inline-block text-[9px] text-gold underline-offset-2 hover:underline"
            >
              {tr("前往设置配置模型 →")}
            </a>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
