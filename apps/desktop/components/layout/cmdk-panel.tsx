"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ALL_PAGES } from "@/lib/page-manifest";
import { useApi, fetcher } from "@/lib/hooks/use-api";
import { apiPost } from "@/lib/api-client";
import useSWR from "swr";
import { useSymbolFavorites, toPair } from "@/lib/hooks/use-symbol-favorites";
import { useRecentSymbols, type RecentSymbol } from "@/lib/hooks/use-recent-symbols";
import { useMarketStore, type SymbolPick } from "@/lib/store/market";
import { routeSymbol, normalizeSymbol, type SymbolMarket } from "@/lib/market/route-symbol";
import { sourceLabel } from "@/lib/market/source-registry";

// 全局 ⌘K 面板 — 符号+页面双搜
// 默认"交易对"模式:空态置顶 自选/最近 + 热门;输入 → 后端 /chart/symbols 聚合搜索 5 市场
// 回车选中符号 → recordRecent + setSymBase + setSymbolPick → 跳超级图表
// Tab/点 chip 切"页面"模式(保留原 fuzzy 跳页)
// ArrowDown/Up 导航 + Enter 选中 + ESC 关闭

const MARKET_LABELS: Record<string, string> = {
  crypto: "加密",
  us: "美股",
  ashare: "A股",
  taiwan: "台股",
  commodities: "大宗",
};

const NON_AMBIGUOUS = new Set<SymbolMarket>(["crypto", "us", "commodities", "ashare", "taiwan"]);

type PickMarket = Exclude<SymbolMarket, "ambiguous">;

// 平台感知修饰键：Windows/Linux 显示 Ctrl，macOS 显示 ⌘（键盘监听已 metaKey||ctrlKey 兼容）
const MOD_LABEL =
  typeof navigator !== "undefined" && /mac/i.test(navigator.platform) ? "⌘" : "Ctrl";

// 选中行高亮：品牌金 10% 淡 tint（背景 tint，非 side-stripe —— 绝对禁）
const ACTIVE_ROW_BG = "rgba(227, 182, 80, 0.10)";

function fuzzyMatch(query: string, target: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  if (t.includes(q)) return true;
  let ti = 0;
  for (let qi = 0; qi < q.length; qi++) {
    const next = t.indexOf(q[qi], ti);
    if (next === -1) return false;
    ti = next + 1;
  }
  return true;
}

interface SymbolRow {
  key: string;
  symbol: string;
  market: PickMarket;
  name: string;
  favorite: boolean;
}

interface SymbolsResponse {
  query: string;
  items: Array<{ symbol: string; market: string; name: string }>;
}

// 透明性批3-11: ⌘K lifecycle 动作组 — needs_input mission 四动作拍板
type CmdkMode = "symbol" | "page" | "action";

interface MissionLite {
  id: string;
  title: string;
  question: string;
  phase: string;
}

interface DecisionAction {
  action: "approve" | "more_evidence" | "redirect" | "park";
  label: string;
}

const DECISION_ACTIONS: DecisionAction[] = [
  { action: "approve", label: "批准 — 进入执行" },
  { action: "more_evidence", label: "要求更多证据" },
  { action: "redirect", label: "重新定向" },
  { action: "park", label: "停放" },
];

function marketOf(symbol: string): PickMarket {
  const m = routeSymbol(symbol);
  return NON_AMBIGUOUS.has(m) ? (m as PickMarket) : "crypto";
}

// 最近/自选存的 market 值可能是旧逻辑写入的脏数据（刀4 前 BTC/USDT 被存成 "us"）：
// 符号能确定性路由时以重算为准，ambiguous 才回退存储值
function trustMarket(stored: string | undefined, symbol: string): PickMarket {
  const m = routeSymbol(symbol);
  if (NON_AMBIGUOUS.has(m)) return m as PickMarket;
  return (stored as PickMarket) ?? "us";
}

export function CmdkPanel() {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<CmdkMode>("symbol");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [marketFilter, setMarketFilter] = useState<PickMarket | "all">("all");
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  const { favorites, toggleFavorite, isFavorite } = useSymbolFavorites();
  const { recents, recordRecent } = useRecentSymbols();
  const setSymBase = useMarketStore((s) => s.setSymBase);
  const setSymbolPick = useMarketStore((s) => s.setSymbolPick);

  // 透明性批3-11: 研究动作模式 — needs_input missions + 选中 mission 的动作面板
  const [needsYou, setNeedsYou] = useState<MissionLite[] | null>(null);
  const [selectedMission, setSelectedMission] = useState<MissionLite | null>(null);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [decisionNote, setDecisionNote] = useState("");
  const [decisionDone, setDecisionDone] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== "action" || needsYou !== null) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await fetcher<{ missions: MissionLite[] }>(
          "/api/v1/research/missions?phase=needs_input&limit=20"
        );
        if (!cancelled) setNeedsYou(data?.missions ?? []);
      } catch {
        if (!cancelled) setNeedsYou([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [mode, needsYou]);

  const submitDecision = async (action: DecisionAction["action"]) => {
    if (!selectedMission || decisionBusy) return;
    // redirect 必须给理由(后端 422 门槛的前端镜像)
    if (action === "redirect" && !decisionNote.trim()) return;
    setDecisionBusy(true);
    try {
      await apiPost(
        `/api/v1/research/missions/${encodeURIComponent(selectedMission.id)}/decision`,
        { action, note: decisionNote.trim() || null }
      );
      setDecisionDone(`${selectedMission.title} — ${action}`);
      setNeedsYou(null); // 回到列表态重新拉取
      setSelectedMission(null);
      setDecisionNote("");
    } catch {
      setDecisionDone(null);
    } finally {
      setDecisionBusy(false);
    }
  };

  // 输入防抖 200ms 后才触发后端搜索
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query.trim()), 200);
    return () => clearTimeout(t);
  }, [query]);

  // keepPreviousData：换查询词时保留上一结果，列表不闪空
  const symbolsApi = useSWR<SymbolsResponse | null>(
    `/api/v1/chart/symbols?q=${encodeURIComponent(debouncedQuery)}`,
    () => fetcher<SymbolsResponse>(`/api/v1/chart/symbols?q=${encodeURIComponent(debouncedQuery)}`),
    { keepPreviousData: true, revalidateOnFocus: false, dedupingInterval: 5000 }
  );

  const pageResults = useMemo(() => {
    const visible = ALL_PAGES.filter((p) => !p.absorbedBy);
    const q = query.trim();
    if (!q) return visible.slice(0, 12);
    return visible.filter(
      (p) => fuzzyMatch(q, p.slug) || fuzzyMatch(q, p.title) || fuzzyMatch(q, p.scene)
    ).slice(0, 12);
  }, [query]);

  const groups = useMemo<Array<{ label: string; rows: SymbolRow[] }>>(() => {
    const seen = new Set<string>();
    const groups: Array<{ label: string; rows: SymbolRow[] }> = [];

    // 输入了查询词时，自选/最近也要参与匹配过滤 —— 否则回车会选中"列表第一行"
    // 即第一个自选（历史脏数据 300750.SZ），而用户搜的却是别的品种（1013 实测根因）。
    const q = query.trim().toUpperCase();
    const matches = (sym: string, name?: string) => {
      if (!q) return true;
      const bare = normalizeSymbol(sym);
      return (
        bare.toUpperCase().includes(q) ||
        sym.toUpperCase().includes(q) ||
        (name ?? "").toUpperCase().includes(q)
      );
    };

    if (marketFilter === "all" && favorites.length > 0) {
      const favRows = favorites
        .filter((sym) => matches(sym))
        .map((sym) => {
          seen.add(normalizeSymbol(sym));
          return {
            // 展示与交接都用归一化后的裸符号：自选里可能存着 "300750.SZ"（脏数据），
            // 后端只认 "300750"，不归一化会路由到 us 且取不到数。
            key: `fav-${sym}`,
            symbol: normalizeSymbol(sym),
            market: marketOf(sym),
            name: normalizeSymbol(sym),
            favorite: true,
          };
        });
      if (favRows.length > 0) groups.push({ label: "自选", rows: favRows });
    }

    if (marketFilter === "all" && recents.length > 0) {
      const recRows = recents
        .filter((r: RecentSymbol) => !seen.has(normalizeSymbol(r.symbol)) && matches(r.symbol, r.name))
        .map((r: RecentSymbol) => {
          seen.add(normalizeSymbol(r.symbol));
          return {
            key: `rec-${r.symbol}`,
            symbol: normalizeSymbol(r.symbol),
            market: trustMarket(r.market, r.symbol),
            name: r.name ?? normalizeSymbol(r.symbol),
            favorite: isFavorite(r.symbol),
          };
        });
      if (recRows.length > 0) groups.push({ label: "最近", rows: recRows });
    }

    const searchItems = (symbolsApi.data?.items ?? [])
      .filter((it) => !seen.has(normalizeSymbol(it.symbol)))
      // 台股已下架（受众太小，2026-10-01 拍板）：后端仍可能返回 taiwan 条目，此处过滤
      .filter((it) => it.market !== "taiwan")
      .filter((it) => marketFilter === "all" || (it.market as PickMarket) === marketFilter)
      .map((it) => ({
        key: `sr-${it.market}-${it.symbol}`,
        symbol: it.symbol,
        market: (it.market as PickMarket) ?? "us",
        name: it.name,
        favorite: isFavorite(it.symbol),
      }));
    if (searchItems.length > 0) {
      groups.push({ label: query.trim() ? "搜索" : "热门", rows: searchItems });
    }

    // 类符号输入且结果里无精确匹配 → 合成一行（加自选 + 回车跳图），
    // 修"输入后端列表没有的交易对无法加自选"断链。
    // 行存裸符号：toggleFavorite 内部 toPair 归一化，selectSymbol/setSymBase 也吃裸符号
    const qRaw = query.trim();
    const looksLikeSymbol = qRaw.length >= 2 && /^[A-Za-z0-9/.\-]+$/.test(qRaw);
    const qPair = toPair(qRaw);
    const qBare = normalizeSymbol(qRaw);
    // 归一化后比较：搜索项返回的是裸符号（"300750"），用户可能输入带后缀的形态
    const exactHit =
      searchItems.some((it) => normalizeSymbol(it.symbol) === qBare || it.symbol === qPair) ||
      favorites.some((f) => normalizeSymbol(f) === qBare || f === qPair);
    if (looksLikeSymbol && !exactHit) {
      groups.push({
        label: "添加自选",
        rows: [{ key: `raw-${qPair}`, symbol: qBare, market: marketOf(qRaw), name: "加入自选并打开图表", favorite: false }],
      });
    }

    return groups;
  }, [favorites, recents, symbolsApi.data, query, isFavorite, marketFilter]);

  const flat = useMemo(() => groups.flatMap((g) => g.rows), [groups]);

  useEffect(() => {
    setActiveIdx(0);
  }, [query, mode, groups]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
        return;
      }
      if (e.key === "Escape" && open) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    const onOpen = () => setOpen(true);
    window.addEventListener("cmdk-open", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("cmdk-open", onOpen);
    };
  }, [open]);

  useEffect(() => {
    if (open) {
      setMode("symbol");
      setQuery("");
      setDebouncedQuery("");
      setActiveIdx(0);
      setSelectedMission(null);
      setDecisionDone(null);
      setDecisionNote("");
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const selectSymbol = (row: SymbolRow) => {
    recordRecent({ symbol: row.symbol, market: row.market, name: row.name });
    setSymBase(row.symbol);
    const pick: SymbolPick = { symbol: row.symbol, market: row.market };
    setSymbolPick(pick);
    setOpen(false);
    router.push("/watch/kf");
  };

  const retrySearch = () => symbolsApi.mutate?.();
  // fetcher 失败返回 null（后端不可达/非 200）；有 items 的 200 响应是对象，可区分"失败"与"无命中"
  const searchFailed = symbolsApi.data == null && !symbolsApi.isLoading;

  const navigate = (idx: number) => {
    const target = flat[idx];
    if (target) selectSymbol(target);
    // 符号模式无命中时不再静默回落页面搜索 —— 切页面是显式动作（Tab/点 chip）
  };

  const onInputKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const list = mode === "symbol" ? flat : pageResults;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, Math.max(list.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Tab") {
      e.preventDefault();
      setMode((m) => (m === "symbol" ? "page" : m === "page" ? "action" : "symbol"));
      setActiveIdx(0);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (mode === "symbol") {
        navigate(activeIdx);
      } else {
        const page = pageResults[activeIdx];
        if (page) {
          setOpen(false);
          router.push(`/${page.scene}/${page.slug}`);
        }
      }
    }
  };

  if (!open) return null;

  const activeOptionId =
    mode === "symbol"
      ? flat[activeIdx]
        ? `cmdk-sym-opt-${activeIdx}`
        : undefined
      : pageResults[activeIdx]
        ? `cmdk-page-opt-${activeIdx}`
        : undefined;
  return (
    <div
      className="cmdk-overlay"
      onClick={() => setOpen(false)}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        zIndex: 200,
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        paddingTop: 120,
      }}
    >
      <div
        className="cmdk-panel"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 600,
          maxHeight: 440,
          background: "var(--color-surface-1)",
          border: "1px solid var(--color-border-soft)",
          borderRadius: 4,
          boxShadow: "0 12px 48px rgba(0,0,0,0.7)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <div
          className="cmdk-input-row"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 14px",
            borderBottom: "1px solid var(--color-border-soft)",
          }}
        >
          <span style={{ color: "var(--color-ink-3)", fontSize: 12, fontWeight: 600 }}>{MOD_LABEL}</span>
          <input
            ref={inputRef}
            role="combobox"
            aria-label={mode === "symbol" ? "搜索交易对" : mode === "page" ? "搜索页面" : "研究决策"}
            aria-expanded
            aria-controls="cmdk-list"
            aria-activedescendant={activeOptionId}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onInputKey}
            placeholder={
              mode === "symbol"
                ? "搜索交易对，如 BTC / AAPL / 600519 / AU0…"
                : mode === "page"
                  ? "搜索页面…"
                  : "研究决策 — 需要你拍板的调查"
            }
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              outline: "none",
              color: "var(--color-ink)",
              fontSize: 13,
            }}
          />
          <div style={{ display: "flex", gap: 4 }}>
            {(["symbol", "page", "action"] as const).map((m) => (
              <button
                key={m}
                type="button"
                data-testid={`mode-chip-${m}`}
                onClick={() => {
                  setMode(m);
                  setActiveIdx(0);
                }}
                style={{
                  fontSize: 10,
                  color: mode === m ? "var(--color-gold)" : "var(--color-ink-3)",
                  border: "1px solid var(--color-border-soft)",
                  borderRadius: 4,
                  padding: "1px 5px",
                  background: mode === m ? "rgba(227, 182, 80, 0.20)" : "transparent",
                  cursor: "pointer",
                }}
              >
                {m === "symbol" ? "交易对" : m === "page" ? "页面" : "研究"}
              </button>
            ))}
          </div>
          <span
            style={{
              fontSize: 10,
              color: "var(--color-ink-3)",
              border: "1px solid var(--color-border-soft)",
              borderRadius: 4,
              padding: "1px 5px",
            }}
          >
            ESC
          </span>
        </div>
        {mode === "symbol" && (
          <div
            className="cmdk-market-filter"
            style={{
              display: "flex",
              gap: 4,
              padding: "6px 14px",
              borderBottom: "1px solid var(--color-border-soft)",
              flexWrap: "wrap",
            }}
          >
            {(
              [
                ["all", "全部"],
                ["crypto", "加密"],
                ["us", "美股"],
                ["ashare", "A股"],
                ["commodities", "大宗"],
              ] as Array<[PickMarket | "all", string]>
            ).map(([m, label]) => (
              <button
                key={m}
                type="button"
                data-testid={`market-filter-${m}`}
                onClick={() => {
                  setMarketFilter(m);
                  setActiveIdx(0);
                }}
                style={{
                  fontSize: 10,
                  color: marketFilter === m ? "var(--color-gold)" : "var(--color-ink-3)",
                  border: "1px solid var(--color-border-soft)",
                  borderRadius: 4,
                  padding: "1px 6px",
                  background: marketFilter === m ? "rgba(227, 182, 80, 0.20)" : "transparent",
                  cursor: "pointer",
                }}
              >
                {label}
              </button>
            ))}
          </div>
        )}
        <div className="cmdk-list" style={{ flex: 1, overflowY: "auto", padding: 4 }}>
          {mode === "symbol" ? (
            <>
              <div role="listbox" id="cmdk-list" aria-label="搜索结果" style={{ display: "contents" }}>
                {groups.map((g) => (
                  <div key={g.label} role="group" aria-label={g.label}>
                    <div
                      style={{
                        padding: "6px 10px 3px",
                        fontSize: 10,
                        color: "var(--color-ink-3)",
                        letterSpacing: 0.5,
                      }}
                    >
                      {g.label}
                    </div>
                    {g.rows.map((row, ri) => {
                      const idx = groups.slice(0, groups.indexOf(g)).reduce((n, x) => n + x.rows.length, 0) + ri;
                      return (
                        <div
                          key={row.key}
                          id={`cmdk-sym-opt-${idx}`}
                          role="option"
                          aria-selected={idx === activeIdx}
                          data-testid="symbol-row"
                          onClick={() => selectSymbol(row)}
                          onMouseEnter={() => setActiveIdx(idx)}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            padding: "5px 10px",
                            borderRadius: 4,
                            cursor: "pointer",
                            background: idx === activeIdx ? ACTIVE_ROW_BG : "transparent",
                            transition: "background-color 0.12s ease-out",
                            fontSize: 11,
                          }}
                        >
                          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <span
                              style={{
                                fontSize: 10,
                                color: "var(--color-gold)",
                                letterSpacing: 0.5,
                                width: 28,
                              }}
                            >
                              {MARKET_LABELS[row.market] ?? row.market}
                            </span>
                            <span style={{ color: "var(--color-ink)", fontFamily: "monospace" }}>
                              {row.symbol}
                            </span>
                            {row.name && row.name !== row.symbol && (
                              <span style={{ color: "var(--color-ink-2)", fontSize: 10 }}>{row.name}</span>
                            )}
                            <span
                              data-testid="sym-src-badge"
                              style={{
                                fontSize: 9,
                                color: "var(--color-ink-3)",
                                border: "1px solid var(--color-border-soft)",
                                borderRadius: 9999,
                                padding: "0 5px",
                              }}
                            >
                              {sourceLabel(row.market)}
                            </span>
                          </span>
                          <button
                            type="button"
                            data-testid="star-toggle"
                            aria-pressed={row.favorite}
                            aria-label={row.favorite ? `从自选移除 ${row.symbol}` : `加入自选 ${row.symbol}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleFavorite(row.symbol);
                            }}
                            style={{
                              fontSize: 11,
                              color: row.favorite ? "var(--color-gold)" : "var(--color-ink-2)",
                              background: "transparent",
                              border: "none",
                              padding: 0,
                              lineHeight: 1,
                              cursor: "pointer",
                            }}
                          >
                            {row.favorite ? "★" : "☆"}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
              {searchFailed && (
                <div
                  role="status"
                  style={{
                    padding: "14px",
                    textAlign: "center",
                    color: "var(--color-ink-2)",
                    fontSize: 11,
                    display: "flex",
                    flexDirection: "column",
                    gap: 8,
                    alignItems: "center",
                  }}
                >
                  <span>搜索服务暂不可用</span>
                  <button
                    type="button"
                    onClick={retrySearch}
                    style={{
                      fontSize: 11,
                      color: "var(--color-gold)",
                      border: "1px solid var(--color-border-soft)",
                      borderRadius: 4,
                      padding: "2px 10px",
                      background: "rgba(227, 182, 80, 0.20)",
                      cursor: "pointer",
                    }}
                  >
                    重试
                  </button>
                </div>
              )}
              {flat.length === 0 && !searchFailed && (
                <div
                  style={{
                    padding: "20px 14px",
                    textAlign: "center",
                    color: "var(--color-ink-2)",
                    fontSize: 11,
                  }}
                >
                  {symbolsApi.isLoading && debouncedQuery
                    ? "加载中…"
                    : "没有匹配的品种，按 Tab 切到页面搜索"}
                </div>
              )}
            </>
          ) : mode === "page" ? (
            <>
              <div role="listbox" id="cmdk-list" aria-label="页面结果" style={{ display: "contents" }}>
                {pageResults.map((p, i) => (
                  <div
                    key={`${p.scene}-${p.slug}`}
                    id={`cmdk-page-opt-${i}`}
                    role="option"
                    aria-selected={i === activeIdx}
                    onClick={() => {
                      setOpen(false);
                      router.push(`/${p.scene}/${p.slug}`);
                    }}
                    onMouseEnter={() => setActiveIdx(i)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "6px 10px",
                      borderRadius: 4,
                      cursor: "pointer",
                      background: i === activeIdx ? ACTIVE_ROW_BG : "transparent",
                      transition: "background-color 0.12s ease-out",
                      fontSize: 11,
                    }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span
                        style={{
                          fontSize: 10,
                          color: "var(--color-gold)",
                          letterSpacing: 0.5,
                        }}
                      >
                        {p.scene}
                      </span>
                      <span style={{ color: "var(--color-ink)" }}>{p.title}</span>
                      <span style={{ color: "var(--color-ink-3)", fontSize: 10 }}>/{p.slug}</span>
                    </span>
                    {p.shortcut && (
                      <span
                        style={{
                          fontSize: 10,
                          color: "var(--color-ink-3)",
                          border: "1px solid var(--color-border-soft)",
                          borderRadius: 4,
                          padding: "1px 4px",
                        }}
                      >
                        {p.shortcut}
                      </span>
                    )}
                  </div>
                ))}
              </div>
              {pageResults.length === 0 && (
                <div
                  style={{
                    padding: "20px 14px",
                    textAlign: "center",
                    color: "var(--color-ink-2)",
                    fontSize: 11,
                  }}
                >
                  无匹配结果
                </div>
              )}
            </>
          ) : mode === "action" ? (
            /* 透明性批3-11: 研究动作 — needs_input 拍板面板 */
            <div style={{ padding: "8px 10px" }} data-testid="action-mode">
              {decisionDone ? (
                <div style={{ padding: "14px 6px", fontSize: 12, color: "var(--color-ink)" }} data-testid="decision-done">
                  已拍板:{decisionDone}。决策已写入 mission 活动流,可回放。
                </div>
              ) : selectedMission ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "var(--color-ink)" }}>
                    {selectedMission.title}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--color-ink-2)" }}>{selectedMission.question}</div>
                  {DECISION_ACTIONS.map((a) => (
                    <button
                      key={a.action}
                      type="button"
                      data-testid={`decision-${a.action}`}
                      disabled={decisionBusy || (a.action === "redirect" && !decisionNote.trim())}
                      onClick={() => submitDecision(a.action)}
                      style={{
                        textAlign: "left",
                        fontSize: 11,
                        padding: "6px 10px",
                        borderRadius: 4,
                        border: "1px solid var(--color-border-soft)",
                        background: "transparent",
                        color: a.action === "approve" ? "var(--color-gold)" : "var(--color-ink)",
                        cursor: "pointer",
                      }}
                    >
                      {a.label}
                    </button>
                  ))}
                  <textarea
                    value={decisionNote}
                    onChange={(e) => setDecisionNote(e.target.value)}
                    rows={2}
                    placeholder="决策备注(redirect 必填理由)"
                    data-testid="decision-note"
                    style={{
                      fontSize: 11,
                      borderRadius: 4,
                      border: "1px solid var(--color-border-soft)",
                      background: "transparent",
                      color: "var(--color-ink)",
                      padding: "6px 8px",
                      resize: "none",
                    }}
                  />
                </div>
              ) : needsYou === null ? (
                <div style={{ padding: "14px 6px", fontSize: 11, color: "var(--color-ink-2)", textAlign: "center" }}>
                  加载中…
                </div>
              ) : needsYou.length === 0 ? (
                <div style={{ padding: "14px 6px", fontSize: 11, color: "var(--color-ink-2)", textAlign: "center" }}>
                  当前没有需要拍板的调查(诚实空态)。
                </div>
              ) : (
                needsYou.map((m) => (
                  <div
                    key={m.id}
                    role="option"
                    aria-selected={false}
                    data-testid="action-mission-row"
                    onClick={() => setSelectedMission(m)}
                    style={{
                      padding: "8px 10px",
                      borderRadius: 4,
                      cursor: "pointer",
                      fontSize: 11,
                      color: "var(--color-ink)",
                    }}
                  >
                    {m.title}
                    <span style={{ color: "var(--color-ink-3)", marginLeft: 6, fontSize: 10 }}>
                      {m.phase}
                    </span>
                  </div>
                ))
              )}
            </div>
          ) : null}
        </div>
        <div
          className="cmdk-foot"
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "6px 14px",
            borderTop: "1px solid var(--color-border-soft)",
            fontSize: 10,
            color: "var(--color-ink-3)",
          }}
        >
          <span>↑↓ 导航 · Tab 切模式 · Enter 选中</span>
          <span>
            {mode === "symbol"
              ? `${flat.length} 个符号`
              : mode === "page"
                ? `${pageResults.length} 个页面`
                : `${needsYou?.length ?? 0} 个待拍板`}
          </span>
        </div>
      </div>
    </div>
  );
}
