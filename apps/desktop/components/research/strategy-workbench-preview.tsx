"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { AiSidebar } from "@/components/strategy/ai-sidebar";
import type { StrategyData } from "@/components/strategy/types";
import { useUiText } from "@/components/i18n/use-ui-text"

/**
 * 工作台 — 策略对象的操作深度视图(AiSidebar:chat/yaml/params 三 tab)。
 * 数据走真实链路 GET /api/v1/strategies/{id};
 * 404 = 该 id 不在 strategies 表(驾驶舱 subject 可能是回测/Alpha 起源),此时给诚实提示。
 */
export function StrategyWorkbenchPreview({ id, onExit }: { id: string; onExit: () => void }) {
  const tr = useUiText()

  const [strategy, setStrategy] = useState<StrategyData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    // 裸 fetch 走 Next 代理(实测 129ms),不进 api-client 的 12s GET 超时/inflight 去重
    // ——api-client 两条链(代理 base+直连)对本请求都偶发 12s abort(存量坑)。
    // 代理偶发慢/AbortError,失败重试 2 次(间隔 1.5s)。
    const ctl = new AbortController();
    const kill = setTimeout(() => ctl.abort(), 30_000);
    const load = async (attempt: number): Promise<void> => {
      if (!alive) return;
      try {
        const r = await fetch(`/api/v1/strategies/${id}`, { signal: ctl.signal });
        const body = (await r.json()) as { data?: StrategyData | null };
        if (!alive) return;
        if (body?.data) {
          setStrategy(body.data);
          setError(null);
          return;
        }
        throw new Error("策略详情为空");
      } catch (e: unknown) {
        if (!alive) return;
        if (attempt < 2) {
          await new Promise((res) => setTimeout(res, 1500));
          return load(attempt + 1);
        }
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    void load(0);
    return () => {
      alive = false;
      ctl.abort();
    };
  }, [id]);

  const handleStrategyChange = useCallback((updated: Partial<StrategyData>) => {
    setStrategy((prev) => (prev ? { ...prev, ...updated } : prev));
  }, []);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[900px] flex-col gap-2 p-4">
        <button
          type="button"
          onClick={onExit}
          className="flex w-fit items-center gap-1.5 text-[11px] text-ink-3 transition-colors hover:text-ink"
        >
          <ArrowLeft className="h-3 w-3" /> {tr("返回驾驶舱")}
        </button>
        <div className="rounded-lg border border-accent/30 bg-accent/5 px-3 py-2 text-[11px] leading-relaxed text-ink-2">
          {tr("工作台 — AI 侧栏 / YAML / 参数面板,数据来自真实")} <span className="num">GET /api/v1/strategies/{id}</span>。
        </div>
        {error ? (
          <div className="rounded-lg border border-neg/40 bg-surface-2 p-3.5 text-[11px] leading-relaxed text-ink-2">
            {tr("策略详情加载失败(")}{error}{tr(")— 该 id 可能不在 strategies 表(驾驶舱 subject 未必是 strategy 工厂对象)。")}
          </div>
        ) : (
          <div className="h-[720px] overflow-hidden rounded-lg border border-border">
            <AiSidebar
              strategyId={id}
              strategyName={strategy?.name ?? id}
              strategy={strategy}
              onStrategyChange={handleStrategyChange}
              onCollapse={onExit}
            />
          </div>
        )}
      </div>
    </div>
  );
}
