"use client";

import { useEffect, useMemo, useState } from "react";
import { useUiText } from "@/components/i18n/use-ui-text";
import { useMonitorFeed } from "@/lib/hooks/use-monitor-feed";
import { useApi } from "@/lib/hooks/use-api";
import {
  CUSTOM_KEY,
  DIR_FILTERS,
  FEED_FILTER_STORAGE_KEY,
  FEED_TABS,
  SIZE_TIERS,
  filterFeed,
  resolveMinUsd,
  type FeedDir,
  type FeedDirFilter,
  type FeedKind,
  type FeedSettings,
  type FeedTab,
} from "@/lib/monitor-feed";

interface SystemEventRow {
  id: string;
  timestamp: number;
  kind: string;
  source: string | null;
  severity: string | null;
  title: string | null;
}

const EMPTY_TEXT: Record<FeedTab, string> = {
  all: "暂无实时数据",
  liquidation: "暂无爆单",
  large_trade: "暂无主力大单",
  anomaly: "暂无异动",
  system: "暂无系统事件",
};

const DEFAULT_SETTINGS: FeedSettings = { sizeKey: "all", customMinUsd: "" };

function loadSettings(): FeedSettings {
  if (typeof window === "undefined") return DEFAULT_SETTINGS;
  try {
    const raw = window.localStorage.getItem(FEED_FILTER_STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const p = JSON.parse(raw) as Partial<FeedSettings>;
    return {
      sizeKey: typeof p.sizeKey === "string" ? p.sizeKey : "all",
      customMinUsd: typeof p.customMinUsd === "string" ? p.customMinUsd : "",
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

// 爆单→红警示,主力买→绿,主力卖→红,异动按方向着色(涨停/翘板→绿,炸板/跌停→红,放量→ink-3)
function dirClass(kind: FeedKind, dir: FeedDir): string {
  if (kind === "liquidation") return "liq";
  if (kind === "large_trade") return dir === "buy" ? "up" : "down";
  if (kind === "anomaly") return dir === "buy" ? "up" : dir === "sell" ? "down" : "";
  return "";
}

export function FeedBar() {
  const { items, source, connected, clearFeed } = useMonitorFeed();
  const tr = useUiText();
  const [tab, setTab] = useState<FeedTab>("all");
  const [settings, setSettings] = useState<FeedSettings>(DEFAULT_SETTINGS);
  const [hydrated, setHydrated] = useState(false); // 本地设置恢复完成前不落盘,避免默认值覆盖上次设置
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [dir, setDir] = useState<FeedDirFilter>("all");
  const [symbol, setSymbol] = useState("");
  const [motion, setMotion] = useState(false); // 默认静止,点播放才滚动
  const [paused, setPaused] = useState(false); // marquee 模式内 hover 暂停 CSS 动画

  // 透明性批3-12: 系统事件 tab — 仅激活时拉 GET /events/recent(轮询 30s)
  const { data: sysEvents } = useApi<SystemEventRow[]>(
    tab === "system" ? "/api/v1/events/recent?limit=30" : null,
    { refreshInterval: 30_000 }
  );

  // 挂载时恢复上次的筛选设置(先于持久化 effect,确保不把默认值写回)
  useEffect(() => {
    setSettings(loadSettings());
    setHydrated(true);
  }, []);

  // 金额设置变更 → 持久化(hydrated 前不写,StrictMode 双执行下也不会覆盖)
  useEffect(() => {
    if (!hydrated) return;
    try {
      window.localStorage.setItem(FEED_FILTER_STORAGE_KEY, JSON.stringify(settings));
    } catch {
      /* localStorage 不可用时静默降级 */
    }
  }, [settings, hydrated]);

  const minUsd = resolveMinUsd(settings.sizeKey, settings.customMinUsd);
  const visible = useMemo(
    () => filterFeed(items, { tab, minUsd, dir, symbol }),
    [items, tab, minUsd, dir, symbol],
  );
  // 条目在入队时已按当时的 locale 拼好 text;切语言后重算一遍,
  // 否则队列里存活的最多 50 条会停在旧语言(依赖 tr 才能跟着切)。
  const shown = useMemo(
    () => visible.map((f) => ({ ...f, text: f.render ? f.render(tr) : f.text })),
    [visible, tr],
  );

  const hasFilter = tab !== "all" || settings.sizeKey !== "all" || dir !== "all" || symbol !== "";
  const emptyText = hasFilter ? tr("无符合条件的记录") : tr(EMPTY_TEXT[tab]);
  const updateSetting = (patch: Partial<FeedSettings>) =>
    setSettings((s) => ({ ...s, ...patch }));

  return (
    <div className="feed-bar" role="complementary" aria-label={tr("实时动态")}>
      <div className="feed-bar-label">
        <span className={`pulse${connected ? "" : " off"}`} />
        {tr("动态 · 实时")}
        {source === "demo" && <span className="feed-demo-badge">{tr("演示")}</span>}
      </div>

      <div className="feed-tabs">
        {FEED_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={`feed-tab${tab === t.key ? " active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {tr(t.label)}
          </button>
        ))}
      </div>

      <div className="feed-filters">
        <div className="feed-seg">
          {SIZE_TIERS.map((t) => (
            <button
              key={t.key}
              type="button"
              className={`feed-seg-btn${settings.sizeKey === t.key ? " active" : ""}`}
              onClick={() => updateSetting({ sizeKey: t.key })}
            >
              {tr(t.label)}
            </button>
          ))}
        </div>
        <div className="feed-seg">
          {DIR_FILTERS.map((d) => (
            <button
              key={d.key}
              type="button"
              className={`feed-seg-btn${dir === d.key ? " active" : ""}`}
              onClick={() => setDir(d.key)}
            >
              {tr(d.label)}
            </button>
          ))}
        </div>
        <input
          className="feed-symbol-input"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          placeholder={tr("币种 ETH")}
          maxLength={10}
          spellCheck={false}
        />
      </div>

      <div className={`feed-scroll${tab === "system" ? "" : motion ? " marquee" : ""}`}>
        {tab === "system" ? (
          !sysEvents || sysEvents.length === 0 ? (
            <div className="feed-empty">{sysEvents ? tr("暂无系统事件") : tr("系统事件加载中…")}</div>
          ) : (
            <div className="feed-static" tabIndex={0} role="log" aria-label={tr("系统事件")}>
              {sysEvents.map((ev) => (
                <span key={ev.id} className={`feed-item ${ev.severity === "warning" ? "liq" : ""}`} data-testid="system-event-row">
                  <span className="feed-time">
                    {new Date(ev.timestamp).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}
                  </span>
                  {ev.title ?? ev.kind}
                </span>
              ))}
            </div>
          )
        ) : shown.length === 0 ? (
          <div className="feed-empty">{emptyText}</div>
        ) : motion ? (
          <div
            className={`feed-marquee${paused ? " paused" : ""}`}
            onMouseEnter={() => setPaused(true)}
            onMouseLeave={() => setPaused(false)}
          >
            {[0, 1].map((copy) => (
              <div className="feed-marquee-track" key={copy} aria-hidden={copy === 1}>
                {shown.map((f) => (
                  <span key={`${copy}-${f.id}`} className={`feed-item ${dirClass(f.kind, f.dir)}`}>
                    <span className="feed-time">{f.time}</span>
                    {f.text}
                  </span>
                ))}
              </div>
            ))}
          </div>
        ) : (
          <div className="feed-static" tabIndex={0} role="log" aria-label={tr("行情快讯")}>
            {shown.map((f) => (
              <span key={f.id} className={`feed-item ${dirClass(f.kind, f.dir)}`}>
                <span className="feed-time">{f.time}</span>
                {f.text}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="feed-bar-actions">
        <button
          type="button"
          className="fba-btn"
          title={motion ? tr("停止滚动") : tr("滚动播放")}
          onClick={() => {
            setMotion((m) => !m);
            setPaused(false);
          }}
        >
          {motion ? (
            <svg width="12" height="12" viewBox="0 0 12 12">
              <rect x="3" y="2" width="2.5" height="8" fill="currentColor" />
              <rect x="6.5" y="2" width="2.5" height="8" fill="currentColor" />
            </svg>
          ) : (
            <svg width="12" height="12" viewBox="0 0 12 12">
              <path d="M3 2 L10 6 L3 10 Z" fill="currentColor" />
            </svg>
          )}
        </button>
        <button
          type="button"
          className={`fba-btn feed-settings-btn${settingsOpen ? " active" : ""}`}
          title={tr("筛选设置")}
          onClick={() => setSettingsOpen((o) => !o)}
        >
          <svg width="12" height="12" viewBox="0 0 12 12">
            <circle cx="6" cy="6" r="2" fill="currentColor" />
            <path
              d="M6 1.5v1.4M6 9.1v1.4M1.5 6h1.4M9.1 6h1.4M2.8 2.8l1 1M8.2 8.2l1 1M2.8 9.2l1-1M8.2 3.8l1-1"
              stroke="currentColor"
              strokeWidth="1.2"
              strokeLinecap="round"
            />
          </svg>
        </button>
        <button
          type="button"
          className="fba-btn"
          title={tr("清空")}
          onClick={() => clearFeed()}
        >
          <svg width="12" height="12" viewBox="0 0 12 12">
            <path d="M2 2 L10 10 M10 2 L2 10" stroke="currentColor" strokeWidth="1.5" />
          </svg>
        </button>
      </div>

      {settingsOpen && (
        <>
          <div className="feed-settings-backdrop" onClick={() => setSettingsOpen(false)} />
          <div className="feed-settings-panel">
            <div className="fsp-label">{tr("金额档位")}</div>
            <div className="feed-seg fsp-seg">
              {SIZE_TIERS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  className={`feed-seg-btn${settings.sizeKey === t.key ? " active" : ""}`}
                  onClick={() => updateSetting({ sizeKey: t.key })}
                >
                  {tr(t.label)}
                </button>
              ))}
            </div>
            <div className="fsp-row">
              <span className="fsp-label">{tr("自定义 ≥")}</span>
              <input
                className={`fsp-input${settings.sizeKey === CUSTOM_KEY ? " active" : ""}`}
                inputMode="numeric"
                value={settings.customMinUsd}
                onChange={(e) => {
                  const v = e.target.value.replace(/[^\d]/g, "");
                  updateSetting({ customMinUsd: v, sizeKey: v ? CUSTOM_KEY : "all" });
                }}
                placeholder={tr("如 250000 = $250K")}
                maxLength={12}
                spellCheck={false}
              />
            </div>
            <div className="fsp-hint">{tr("输入数字即按 ≥ 该金额过滤；清空回\"全部\"。")}</div>
          </div>
        </>
      )}
    </div>
  );
}
