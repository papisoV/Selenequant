"use client";

import { useContextStore } from "@/lib/store/context";
import { useApi } from "@/lib/hooks/use-api";
import { usePaperStore } from "@/lib/store/paper";
import { WsStatusIndicator } from "@/components/layout/ws-status-banner";
import { routeSymbol } from "@/lib/market/route-symbol";
import { sourceLabel } from "@/lib/market/source-registry";
import { useUiText } from "@/components/i18n/use-ui-text";
import type { Scene } from "@/lib/page-manifest";

const SCENE_LABEL: Record<Scene, string> = {
  watch: "盯盘",
  research: "研策",
  trade: "执行",
  system: "系统",
  workspace: "工作台",
};

interface RunnerStatusLite {
  running?: boolean;
  deployments?: Array<{ strategy_id?: string; name?: string; status?: string }>;
}

interface LatestSignal {
  signal?: { side?: string; symbol?: string };
  side?: string;
  symbol?: string;
  ts?: number;
  timestamp?: number;
}interface Props {
  scene: Scene;
}

function formatSignalTime(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

export function CtxBar({ scene }: Props) {
  const tr = useUiText();
  const { symBase, pageFilter } = useContextStore();
  const exchange = pageFilter.exchange === "all" ? "binance" : pageFilter.exchange;
  // 品种徽章真值：按 symBase 路由市场（ambiguous 兜底按 crypto 显示，与 kf 默认分支一致）
  const routed = routeSymbol(symBase.toUpperCase());
  const srcMarket = routed === "ambiguous" ? "crypto" : routed;

  const { data: runnerStatus } = useApi<RunnerStatusLite>(
    "/api/v1/strategies/runner/status"
  );
  const activeStrategy = runnerStatus?.deployments?.[0];
  const { data: latestSignal } = useApi<LatestSignal>(
    `/api/v1/signals/current?symbol=${encodeURIComponent(symBase)}/USDT`
  );
  const positions = usePaperStore((s) => s.positions);

  const strategyName =
    activeStrategy?.name ?? activeStrategy?.strategy_id ?? "—";

  const sigSide =
    latestSignal?.signal?.side ?? latestSignal?.side ?? "";
  const sigTs =
    latestSignal?.ts ?? latestSignal?.timestamp ?? 0;
  const signalText = sigSide
    ? `${sigSide} ${formatSignalTime(sigTs)}`.trim()
    : "—";

  const pos = positions.find((p) =>
    p.symbol?.toUpperCase().startsWith(symBase.toUpperCase())
  );
  const posSide = pos?.pos_side ?? pos?.side ?? "";
  const posQty = pos?.quantity_base ?? pos?.quantity ?? 0;
  const positionText =
    pos && posSide && posQty
      ? `${posSide === "long" || posSide === "buy" ? tr("多") : posSide === "short" || posSide === "sell" ? tr("空") : posSide} ${posQty} ${symBase}`
      : "—";

  return (
    <div className="ctx-bar">
      <div className="ctx-item">
        <span className="ctx-lab">{tr("品种")}</span>
        <span className="ctx-val gold">{srcMarket === "crypto" ? `${symBase}-PERP` : symBase}</span>
        <span className="ctx-val ctx-src" data-testid="ctx-src-badge">
          {srcMarket === "crypto" ? tr("{exchange} · 永续", { exchange }) : sourceLabel(srcMarket, tr)}
        </span>
      </div>
      <div className="ctx-sep" />
      <div className="ctx-item">
        <span className="ctx-lab">{tr("策略")}</span>
        <span className="ctx-val">{strategyName}</span>
      </div>
      <div className="ctx-sep" />
      <div className="ctx-item">
        <span className="ctx-lab">{tr("最近信号")}</span>
        <span className="ctx-val pos">{signalText}</span>
      </div>
      <div className="ctx-sep" />
      <div className="ctx-item">
        <span className="ctx-lab">{tr("当前持仓")}</span>
        <span className="ctx-val pos">{positionText}</span>
      </div>
      <div className="ctx-spacer" />
      <WsStatusIndicator />
      <div className="ctx-sep" />
      <div className="ctx-item">
        <span className="ctx-lab">{tr("场景")}</span>
        <span className="ctx-val">{tr(SCENE_LABEL[scene])}</span>
      </div>
    </div>
  );
}
