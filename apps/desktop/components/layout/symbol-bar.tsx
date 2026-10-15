"use client";

import { useContextStore, type LayoutPreset } from "@/lib/store/context";
import { clickable } from "@/lib/a11y/clickable";

const TFS = ["1m", "5m", "15m", "1h", "4h", "1d", "1w"];

const PRESETS: { id: LayoutPreset; label: string }[] = [
  { id: "default", label: "默认" },
  { id: "compact", label: "紧凑" },
  { id: "focus", label: "专注" },
  { id: "grid2", label: "2 宫格" },
  { id: "grid4", label: "4 宫格" },
  { id: "charts6", label: "6 图" },
  { id: "charts9", label: "9 图" },
  { id: "minimal", label: "极简" },
  { id: "pro", label: "专业" },
];

// 设计稿 L8466-8467 布局预设按钮（9 preset + 重置）
// 各页面读 layoutPreset 调整布局（kf 的 1/2/4 宫格等）

function fmtNum(n: number) {
  return n.toLocaleString("en-US");
}

export function SymbolBar() {
  const { pageFilter, setPageFilter, layoutPreset, setLayoutPreset, tick } = useContextStore();
  const pnlCls = tick.pnlToday >= 0 ? "pos" : "neg";
  return (
    <div className="symbol-bar">
      <div
        className="sb-search"
        {...clickable(() => window.dispatchEvent(new CustomEvent("cmdk-open")))}
        title="搜索品种(⌘K)"
      >
        <span>🔍</span>搜索品种…
        <span className="kbd">⌘K</span>
      </div>
      <div className="sb-intervals">
        {TFS.map((tf) => (
          <div
            key={tf}
            className={`sb-int ${pageFilter.tf === tf ? "active" : ""}`}
            aria-pressed={pageFilter.tf === tf}
            {...clickable(() => setPageFilter({ tf }))}
          >
            {tf.toUpperCase()}
          </div>
        ))}
      </div>
      <div className="sb-spacer" />
      <div className="sb-metrics">
        <div className="sb-metric">
          <span className="sb-metric-lab">净值</span>
          <span
            className={`sb-metric-val gold num ${tick.flash === "equity" ? "flash" : ""}`}
          >
            {fmtNum(tick.equity)}
          </span>
        </div>
        <div className="sb-metric">
          <span className="sb-metric-lab">今日</span>
          <span
            className={`sb-metric-val ${pnlCls} num ${tick.flash === "pnl" ? "flash" : ""}`}
          >
            {tick.pnlToday >= 0 ? "+" : ""}{fmtNum(tick.pnlToday)}
          </span>
        </div>
        <div className="sb-metric">
          <span className="sb-metric-lab">夏普</span>
          <span className="sb-metric-val num">—</span>
        </div>
      </div>
      <div className="sb-actions">
        <div
          className="sb-btn"
          title="命令面板(⌘K)"
          {...clickable(() => window.dispatchEvent(new CustomEvent("cmdk-open")))}
        >
          ⌘K
        </div>
        <div className="sb-btn" title="Kill Switch 功能开发中" style={{ color: "var(--color-ink-4)", cursor: "not-allowed" }}>Kill</div>
      </div>
      <div className="preset-bar">
        {PRESETS.map((p) => (
          <div
            key={p.id}
            className={`preset-btn ${layoutPreset === p.id ? "active" : ""}`}
            aria-pressed={layoutPreset === p.id}
            title={`布局: ${p.label}`}
            {...clickable(() => setLayoutPreset(p.id))}
          >
            {p.label}
          </div>
        ))}
        <div className="preset-divider" />
        <div
          className="preset-reset"
          title="重置布局"
          {...clickable(() => setLayoutPreset("default"))}
        >
          重置
        </div>
      </div>
    </div>
  );
}
