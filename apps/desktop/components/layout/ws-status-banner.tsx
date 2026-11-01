"use client";

import { useMarketStore } from "@/lib/store/market";
import { useUiText } from "@/components/i18n/use-ui-text"

// 行情流状态位（CtxBar 内嵌，替代原全屏盖顶横幅）
// 订阅 market store 的 marketsStreamState（use-markets-ws.ts:493 已同步）
// 不直接调 useMarketsWs（会建第 4 条 WS 连接，违反 P1-2a 前置耐受边界）
// live → 绿点"实时" / recovering → 金点"重连" / degraded → 金点"延迟"
// 冷启动噪音门：live/recovering 首次见到 live 之前不显示。
// degraded 不受门控——degraded 意味着 WS 长时间无消息（含从未连上），
// 是强异常信号，静音它 = 2026-09-30 WS 全瘫两周零报警的盲区根源。
export function WsStatusIndicator() {
  const tr = useUiText()

  const streamState = useMarketStore((s) => s.marketsStreamState);
  const everLive = useMarketStore((s) => s.marketsEverLive);

  if (streamState === "degraded") {
    return (
      <>
        <div className="ctx-sep" />
        <div
          className="ctx-item ws-status-item"
          role="status"
          aria-live="polite"
          title={tr("行情流已断，正在用 REST 轮询，数据有延迟")}
        >
          <span className="conn-dot warn" aria-hidden="true" />
          <span className="ctx-val ws-status-warn">{tr("延迟")}</span>
        </div>
      </>
    );
  }

  if (!everLive) return null;

  const reconnecting = streamState === "recovering";
  const dotClass = reconnecting ? "warn" : "live";
  const label = reconnecting ? "重连" : "实时";
  const tip = reconnecting ? "行情流重连中" : "行情流实时";

  return (
    <>
      <div className="ctx-sep" />
      <div
        className="ctx-item ws-status-item"
        role="status"
        aria-live="polite"
        title={tr(tip)}
      >
        <span className={`conn-dot ${dotClass}`} aria-hidden="true" />
        <span className={`ctx-val ${reconnecting ? "ws-status-warn" : ""}`}>
          {tr(label)}
        </span>
      </div>
    </>
  );
}

export type { MarketsStreamState } from "@/lib/store/market";
