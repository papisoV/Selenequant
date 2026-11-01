"use client";

import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import type { Scene } from "@/lib/page-manifest";
import { SCENE_DEFAULT_PAGE } from "@/lib/page-manifest";
import { useApi } from "@/lib/hooks/use-api";
import { AssetDrawer } from "@/components/layout/drawers/asset-drawer";
import { RiskDrawer } from "@/components/layout/drawers/risk-drawer";
import { VheroAvatar } from "./vhero-avatar";
import { VheroTrigger } from "./vhero-trigger";
import { VheroConn } from "./vhero-conn";
import { VheroTime } from "./vhero-time";
import { VheroManifest } from "./vhero-manifest";
import { VheroKill } from "./vhero-kill";
import { clickable } from "@/lib/a11y/clickable";
import { useUiText } from "@/components/i18n/use-ui-text"

// 后端 /api/v1/portfolio 返回 [{symbol, side, size, entry_price, current_price, unrealized_pnl}]
interface PortfolioItem {
  symbol?: string;
  side?: string;
  size?: number;
  entry_price?: number;
  current_price?: number;
  unrealized_pnl?: number;
}

const SCENES: { id: Scene; tip: string; svg: React.ReactNode }[] = [
  {
    id: "watch",
    tip: "盯盘",
    svg: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="10" />
        <polyline points="12 6 12 12 16 14" />
      </svg>
    ),
  },
  {
    id: "research",
    tip: "研策",
    svg: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
        <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
      </svg>
    ),
  },
  {
    id: "trade",
    tip: "执行",
    svg: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
        <polyline points="16 7 22 7 22 13" />
      </svg>
    ),
  },
  {
    id: "system",
    tip: "系统",
    svg: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
      </svg>
    ),
  },
];

export type DrawerKind = "asset" | "risk" | null;

export function VHero() {
  const tr = useUiText()

  const pathname = usePathname();
  const router = useRouter();
  const current = (pathname.split("/")[1] as Scene) ?? "watch";
  const [activeDrawer, setActiveDrawer] = useState<DrawerKind>(null);
  const [extended, setExtended] = useState(false);
  // portfolio 端点返回 positions 列表,用 unrealized_pnl 求和作资产估值
  const { data: portfolio } = useApi<PortfolioItem[] | null>("/api/v1/portfolio");
  const { totalEquity, totalPnlPct } = (() => {
    if (portfolio && Array.isArray(portfolio) && portfolio.length > 0) {
      let totalSize = 0;
      let totalPnl = 0;
      portfolio.forEach((p) => {
        const size = Number(p.size ?? 0);
        const px = p.current_price != null ? Number(p.current_price) : 0;
        totalSize += Math.abs(size) * px;
        totalPnl += Number(p.unrealized_pnl ?? 0);
      });
      const pnlPct = totalSize > 0 ? (totalPnl / totalSize) * 100 : 0;
      return {
        totalEquity: `$${(totalSize / 1000).toFixed(1)}K`,
        totalPnlPct: `${pnlPct.toFixed(1)}%`,
      };
    }
    return { totalEquity: "—", totalPnlPct: "—" };
  })();

  const goScene = (s: Scene) => {
    const slug = SCENE_DEFAULT_PAGE[s];
    router.push(`/${s}/${slug}`);
  };

  const toggleDrawer = (kind: DrawerKind) =>
    setActiveDrawer((cur) => (cur === kind ? null : kind));

  return (
    <>
      <nav className="vhero bd-r" aria-label={tr("账户与状态")}>
        <VheroAvatar />

        <VheroTrigger
          kind="asset"
          label={tr("资产")}
          value={totalEquity}
          valueClass="gold"
          active={activeDrawer === "asset"}
          onClick={() => toggleDrawer("asset")}
        />
        <VheroTrigger
          kind="risk"
          label={tr("风险")}
          value={totalPnlPct}
          valueClass="warn"
          active={activeDrawer === "risk"}
          onClick={() => toggleDrawer("risk")}
        />

        <VheroConn />

        <VheroTime />

        <div className="vhero-nav">
          {SCENES.map((s) => (
            <div
              key={s.id}
              className={`nav-icon ${current === s.id ? "active" : ""}`}
              title={tr(s.tip)}
              aria-current={current === s.id ? "page" : undefined}
              {...clickable(() => goScene(s.id))}
            >
              {s.svg}
              <span className="nav-tip">{tr(s.tip)}</span>
            </div>
          ))}
        </div>

        <VheroManifest extended={extended} onClick={() => setExtended((v) => !v)} />
        <VheroKill />
      </nav>

      <AssetDrawer show={activeDrawer === "asset"} onClose={() => setActiveDrawer(null)} />
      <RiskDrawer show={activeDrawer === "risk"} onClose={() => setActiveDrawer(null)} />
    </>
  );
}
