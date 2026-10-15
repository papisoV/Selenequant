"use client";

import { useEffect, useState } from "react";
import { ApiError, fetchBare } from "@/lib/api-client";

/**
 * 失败归因五分类(件 1 前端)— replay"学到"行 + workspace rule 批量面共用。
 * 后端 research_missions.py attribution 端点全部返回 bare JSON(不经 envelope):
 * - GET  /research/attribution/categories              → {categories: FailureCategory[]}
 * - GET  /research/strategy/{id}/state/{n}/attribution → {attribution: AttributionRow} | 404
 * - GET  .../attribution/suggest                       → {suggestion: AttributionSuggest|null, reason?}
 * - POST .../attribution                               → {attribution};UNIQUE 冲突 409(改判走 PUT)
 * - POST /research/strategy/{id}/state/attribution/rules → {inserted: number[], rule_episode_ns: number[]}
 */

export interface FailureCategory {
  id: string;
  code: string;
  content: string;
  first_seen_at: string;
  occurrence_count: number;
}

export interface AttributionRow {
  category_code: string;
  suggested_by: string;
  rationale: string;
}

export interface AttributionSuggest {
  category_code: string;
  rationale: string;
  confidence: number | null;
}

// categories 全 session 一次拉取,两个落点共用(单一事实源)
let categoriesPromise: Promise<FailureCategory[]> | null = null;
// suggest session 缓存:同 (strategy_id, episode_n) 二次打开不重复调 LLM
const suggestCache = new Map<string, AttributionSuggest | null>();

function suggestKey(strategyId: string, episodeN: number): string {
  return `${strategyId}#${episodeN}`;
}

export function loadFailureCategories(): Promise<FailureCategory[]> {
  categoriesPromise ??= fetchBare<{ categories: FailureCategory[] }>(
    "/api/v1/research/attribution/categories",
  )
    .then((r) => r.categories)
    .catch(() => []);
  return categoriesPromise;
}

export async function loadEpisodeAttribution(
  strategyId: string,
  episodeN: number,
): Promise<AttributionRow | null> {
  try {
    const r = await fetchBare<{ attribution: AttributionRow }>(
      `/api/v1/research/strategy/${strategyId}/state/${episodeN}/attribution`,
    );
    return r.attribution;
  } catch {
    // 404 = 无归因行;其他查询失败同样按"无归因"降级 — 确认/改判的落盘错误仍会在 POST/PUT 显式报出
    return null;
  }
}

export async function loadSuggestion(
  strategyId: string,
  episodeN: number,
): Promise<AttributionSuggest | null> {
  const key = suggestKey(strategyId, episodeN);
  if (suggestCache.has(key)) return suggestCache.get(key) ?? null;
  try {
    const r = await fetchBare<{ suggestion: AttributionSuggest | null }>(
      `/api/v1/research/strategy/${strategyId}/state/${episodeN}/attribution/suggest`,
    );
    suggestCache.set(key, r.suggestion ?? null);
    return r.suggestion ?? null;
  } catch {
    suggestCache.set(key, null); // LLM 不可用:本 session 该 key 不再重试
    return null;
  }
}

/* ---------- replay"学到"行归因块 ---------- */

export function AttributionBlock({
  strategyId,
  episodeN,
  drift,
}: {
  strategyId: string;
  episodeN: number;
  drift: boolean;
}) {
  const [cats, setCats] = useState<FailureCategory[]>([]);
  const [row, setRow] = useState<AttributionRow | null>(null);
  const [rowLoaded, setRowLoaded] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [selSource, setSelSource] = useState<"llm" | "director">("director");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (drift) return; // bundle 级徽章,无需行查询/建议
    let alive = true;
    setRow(null);
    setRowLoaded(false);
    setSelected(null);
    setSelSource("director");
    setRationale("");
    setErr(null);
    setDismissed(false);
    loadFailureCategories().then((c) => {
      if (alive) setCats(c);
    });
    loadEpisodeAttribution(strategyId, episodeN).then((r) => {
      if (!alive) return;
      setRow(r);
      setRowLoaded(true);
      if (r === null) {
        loadSuggestion(strategyId, episodeN).then((s) => {
          if (!alive || s === null) return;
          setSelected(s.category_code);
          setSelSource("llm");
          setRationale(s.rationale);
        });
      }
    });
    return () => {
      alive = false;
    };
  }, [strategyId, episodeN, drift]);

  const contentOf = (code: string) => cats.find((c) => c.code === code)?.content ?? code;

  if (drift) {
    return (
      <div className="mt-1 flex flex-wrap items-baseline gap-1.5">
        <span className="inline-flex shrink-0 items-center rounded border border-warn/40 bg-warn/10 px-1 py-px text-[8.5px] font-semibold tracking-wide text-warn">
          已归因: execution(rule)
        </span>
        <span className="text-[9px] text-ink-4">
          本 run 全部亏损 episode 已按漂移硬规则批量归因(bundle 级,非单笔行;在 workspace 触发 rule 批插)
        </span>
      </div>
    );
  }

  if (row) {
    return (
      <div className="mt-1 flex flex-col gap-0.5">
        <span className="inline-flex w-fit shrink-0 items-center rounded border border-accent/50 bg-accent/10 px-1 py-px text-[8.5px] font-semibold tracking-wide text-accent">
          已归因: {contentOf(row.category_code)}({row.suggested_by})
        </span>
        {row.rationale && (
          <p className="num text-[9px] leading-relaxed text-ink-4">{row.rationale}</p>
        )}
      </div>
    );
  }

  if (!rowLoaded || dismissed) {
    return dismissed ? (
      <p className="mt-1 text-[9px] text-ink-4">暂不归因 — 该笔不计入归因统计。</p>
    ) : null;
  }

  async function confirm() {
    if (!selected || busy) return;
    setBusy(true);
    setErr(null);
    const body = { category_code: selected, suggested_by: selSource, rationale };
    const path = `/api/v1/research/strategy/${strategyId}/state/${episodeN}/attribution`;
    try {
      // 归因端点是 bare JSON(不在 BARE_ENDPOINT_PATTERNS),fetchBare 直调;
      // apiPost 会按 envelope 解包把 bare 响应拆坏
      try {
        await fetchBare(path, { method: "POST", body: JSON.stringify(body) });
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          await fetchBare(path, { method: "PUT", body: JSON.stringify(body) }); // 已有归因 → 改判覆盖
        } else {
          throw e;
        }
      }
      setRow({ category_code: selected, suggested_by: selSource, rationale });
    } catch {
      setErr("归因落盘失败 — 确认后端已启动");
    }
    setBusy(false);
  }

  return (
    <div className="mt-1.5 flex flex-col gap-1.5 border-t border-dashed border-border pt-1.5">
      <div className="flex flex-wrap items-center gap-1">
        {cats.map((c) => {
          const on = selected === c.code;
          return (
            <button
              key={c.code}
              type="button"
              disabled={busy}
              title={c.content}
              onClick={() => {
                setSelected(c.code);
                setSelSource("director");
              }}
              className={`rounded border px-1.5 py-px text-[9px] transition-colors disabled:opacity-50 ${
                on
                  ? "border-accent bg-accent/10 font-semibold text-accent"
                  : "border-border bg-surface-3 text-ink-2 hover:border-accent/50 hover:text-accent"
              }`}
            >
              {on ? `✓ ${c.content}` : c.content}
            </button>
          );
        })}
      </div>
      <textarea
        value={rationale}
        onChange={(e) => setRationale(e.target.value)}
        rows={2}
        placeholder="归因说明(主因 + 伴生因,可留空)"
        className="w-full resize-none rounded border border-border bg-surface-2 px-2 py-1 text-[10px] text-ink placeholder:text-ink-5 focus:border-accent focus:outline-none"
      />
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          disabled={!selected || busy}
          onClick={confirm}
          className="rounded bg-accent px-2.5 py-1 text-[10px] font-medium text-accent-foreground transition-colors hover:bg-accent/85 disabled:opacity-50"
        >
          {busy ? "落盘中…" : "确认归因"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => setDismissed(true)}
          className="rounded border border-border bg-surface-3 px-2 py-1 text-[10px] text-ink-2 transition-colors hover:border-accent/50 disabled:opacity-50"
        >
          暂不归因
        </button>
        {err && <span className="text-[9px] text-neg">{err}</span>}
        {!err && (
          <span className="text-[8.5px] text-ink-4">
            {selSource === "llm" ? "已采纳 LLM 建议(suggested_by=llm)" : "手选归因(suggested_by=director)"}
          </span>
        )}
      </div>
    </div>
  );
}

/* ---------- workspace"共性根因"rule 批量归因按钮 ---------- */

export function RuleBatchAttributionButton({ strategyId }: { strategyId: string }) {
  const [phase, setPhase] = useState<"idle" | "busy" | "done" | "err">("idle");
  const [inserted, setInserted] = useState<number | null>(null);

  async function run() {
    if (phase === "busy") return;
    setPhase("busy");
    try {
      // bare JSON 端点:fetchBare 直调(apiPost 的 envelope 解包会拆坏响应)
      const r = await fetchBare<{ inserted: number[]; rule_episode_ns: number[] }>(
        `/api/v1/research/strategy/${strategyId}/state/attribution/rules`,
        { method: "POST", body: JSON.stringify({}) },
      );
      setInserted(r.inserted.length);
      setPhase("done");
    } catch {
      setPhase("err");
    }
  }

  if (phase === "done") {
    return (
      <span className="inline-flex shrink-0 items-center rounded border border-warn/40 bg-warn/10 px-1 py-px text-[8.5px] font-semibold tracking-wide text-warn">
        已归因: execution(rule){inserted != null ? ` · ${inserted} 笔` : ""}
      </span>
    );
  }
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5">
      <button
        type="button"
        disabled={phase === "busy"}
        onClick={run}
        className="rounded border border-warn/45 bg-warn/10 px-1.5 py-px text-[9px] font-semibold text-warn transition-colors hover:bg-warn/20 disabled:opacity-50"
      >
        {phase === "busy" ? "批量归因中…" : "批量归因 · rule"}
      </button>
      {phase === "err" && <span className="text-[9px] text-neg">批量归因失败 — 确认后端已启动</span>}
    </span>
  );
}
