"use client";

import { useState } from "react";
import Link from "next/link";
import { useApiPage } from "@/lib/hooks/use-api-page";
import { errorHintText } from "./strategy-parts";

/**
 * MEMORY 正典页 — Research State 投影(slug "mem", /research/mem)。
 * "组织学到的,不是组织存过的":证据链走到 Episode/Evidence 的条目 = 既有知识;
 * 走不到的 = 待检验线索(折叠)。行为后果 = cited_json 反向索引(已改变 N 次回答)。
 * 全部字段读 GET /api/v1/research/graph/memory,页面零生成物。
 */

type EpisodeRow = {
  kind: "episode";
  id: string;
  title: string;
  subject_type: string;
  subject_id: string;
  boundary_start: string;
  boundary_end: string;
  recorded: Record<string, unknown>;
  occurred_at: string;
  created_at: string;
};

type EvidenceRow = {
  kind: "evidence";
  id: string;
  episode_id: string;
  mode: "RECORDED" | "RECONSTRUCTED" | "AFTER_THE_FACT";
  payload: Record<string, unknown>;
  source_ref: string;
  observed_at: string;
  created_at: string;
};

type ChainRow = EpisodeRow | EvidenceRow;

type UsedBy = {
  revision_id: string;
  seq: number;
  question_id: string;
  content: string;
  actor?: string | null;
  created_at: string;
};

type Entry = {
  kind: "hypothesis" | "revision";
  id: string;
  text: string | null;
  status: string | null;
  falsified: boolean;
  created_at: string;
  question_id: string | null;
  seq: number | null;
  origin: string | null;
  chain: ChainRow[];
  used_by: UsedBy[];
  used_count: number;
  last_used_at: string | null;
};

export type MemoryResponse = {
  knowledge: Entry[];
  clues: Entry[];
  recently_used: UsedBy | null;
  counts: { knowledge: number; clues: number };
};

const KIND_LABEL: Record<Entry["kind"], string> = {
  hypothesis: "假设",
  revision: "结论",
};

const ORIGIN_LABEL: Record<string, string> = {
  selene: "AI 组织",
  user: "人写",
  system: "系统",
};

const USED_ACTOR: Record<string, string> = {
  selene: "· AI",
  user: "· 人",
  system: "· 系统",
};

function relTime(iso: string | null): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.floor((Date.now() - t) / 1000);
  if (s < 0) return iso;
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

function ChainChip({ row }: { row: ChainRow }) {
  const isEp = row.kind === "episode";
  return (
    <span
      title={
        isEp
          ? `${row.subject_type}:${row.subject_id} · ${row.boundary_start} ~ ${row.boundary_end}`
          : `mode=${row.mode} · observed_at=${row.observed_at}`
      }
      className={`inline-flex items-center gap-1 rounded px-1.5 py-px text-[9px] ${
        isEp
          ? "bg-gold/10 text-gold"
          : "bg-pos/10 text-pos"
      }`}
    >
      {isEp ? "ep" : "ev"} · {row.id}
    </span>
  );
}

function UsedLine({ e }: { e: Entry }) {
  const n = e.used_count;
  return (
    <div className="flex items-baseline gap-2 border-t border-border-soft pt-1.5 text-[9.5px]">
      {n > 0 ? (
        <span className="text-pos">
          ✓ 已改变 {n} 次回答
          {e.last_used_at && (
            <span className="ml-1.5 text-ink-4">最近 {relTime(e.last_used_at)}</span>
          )}
        </span>
      ) : (
        <span className="text-ink-4">✓ 已改变 0 次回答</span>
      )}
    </div>
  );
}

function EntryCard({
  e,
  onExpand,
}: {
  e: Entry;
  onExpand: (id: string) => void;
}) {
  const isHyp = e.kind === "hypothesis";
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface-1">
      <div className="flex flex-col gap-1 px-3.5 py-2.5">
        {/* 头行:种类 + 固化时间(证伪态附加 neg badge) */}
        <div className="flex items-baseline gap-2 text-[9px] tracking-[0.08em] text-ink-4">
          <span>{KIND_LABEL[e.kind]}</span>
          <span>固化于 {relTime(e.created_at)}</span>
          {isHyp && e.status && (
            <span className="rounded bg-surface-2 px-1 py-px text-ink-3">{e.status}</span>
          )}
          {e.origin && ORIGIN_LABEL[e.origin] && (
            <span className="rounded bg-surface-2 px-1 py-px text-ink-3">
              {ORIGIN_LABEL[e.origin]}
            </span>
          )}
          {e.falsified && (
            <span className="rounded bg-destructive/15 px-1 py-px text-destructive">已证伪</span>
          )}
          {e.question_id && (
            <span className="ml-auto shrink-0 text-ink-4" title={e.question_id}>
              Q · {e.question_id}
            </span>
          )}
        </div>
        {/* 结论先行 */}
        <div className="text-[12px] leading-relaxed text-ink">
          {e.text ?? <span className="text-ink-4">—</span>}
        </div>
        {/* 证据链 chips */}
        {e.chain.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            {e.chain.map((c) => (
              <ChainChip key={c.kind + c.id} row={c} />
            ))}
          </div>
        )}
        <UsedLine e={e} />
        <button
          type="button"
          onClick={() => onExpand(e.id)}
          className="w-fit text-[9.5px] text-ink-4 underline decoration-border-soft underline-offset-2 transition-colors hover:text-ink-2"
        >
          展开证据链 →
        </button>
      </div>
    </div>
  );
}

function EpisodeCard({ row }: { row: EpisodeRow }) {
  return (
    <div className="rounded-lg border border-gold/30 bg-gold/5 px-3.5 py-2.5">
      <div className="mb-1 flex items-baseline gap-2 text-[9px] tracking-[0.08em] text-gold">
        <span>EPISODE · 已发生,不可改</span>
        <span className="text-ink-4">
          {row.boundary_start} ~ {row.boundary_end}
        </span>
      </div>
      <div className="text-[11.5px] leading-relaxed text-ink">{row.title}</div>
      <div className="mt-1 text-[9px] text-ink-4">
        {row.subject_type} · {row.subject_id}
      </div>
    </div>
  );
}

function EvidenceCard({ row }: { row: EvidenceRow }) {
  const obs = Object.entries(row.payload).filter(
    ([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean",
  );
  return (
    <div className="rounded-lg border border-pos/25 bg-pos/5 px-3.5 py-2.5">
      <div className="mb-1 flex items-baseline gap-2 text-[9px] tracking-[0.08em] text-pos">
        <span>EVIDENCE · {row.mode}</span>
        <span className="ml-auto text-ink-4">{row.observed_at}</span>
      </div>
      {obs.length > 0 ? (
        <div className="flex flex-col gap-0.5">
          {obs.map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-2 text-[10.5px]">
              <span className="w-[90px] shrink-0 text-[9px] text-ink-4">{k}</span>
              <span className="text-ink-2">{String(v)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-[10px] text-ink-4">无标量观察字段</div>
      )}
      {row.source_ref && (
        <div className="mt-1 text-[9px] text-ink-4">source: {row.source_ref}</div>
      )}
    </div>
  );
}

function DetailPanel({ e, onClose }: { e: Entry; onClose: () => void }) {
  const episodes = e.chain.filter((c): c is EpisodeRow => c.kind === "episode");
  const evidences = e.chain.filter((c): c is EvidenceRow => c.kind === "evidence");
  return (
    <div className="rounded-lg border border-border bg-surface-2 px-3.5 py-3">
      <div className="mb-2 flex items-baseline gap-2">
        <span className="text-[10px] font-semibold text-ink-2">当前形态 · {e.id}</span>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto text-[9.5px] text-ink-4 hover:text-ink-2"
        >
          收起 ×
        </button>
      </div>
      {episodes.map((r) => (
        <EpisodeCard key={r.id} row={r} />
      ))}
      {episodes.length > 0 && evidences.length > 0 && (
        <div className="my-1.5 border-l border-dashed border-border pl-3 text-[9px] text-ink-4">
          ↓ 观察到
        </div>
      )}
      {evidences.map((r) => (
        <EvidenceCard key={r.id} row={r} />
      ))}
      {e.chain.length === 0 && (
        <div className="text-[10px] text-ink-4">该条目暂无 Episode / Evidence 证据链。</div>
      )}
      {/* 行为后果时间线 */}
      <div className="mt-3 border-t border-border-soft pt-2">
        <div className="mb-1 text-[9px] tracking-[0.08em] text-ink-4">行为后果</div>
        {e.used_by.length > 0 ? (
          <div className="flex flex-col gap-1">
            {e.used_by.map((u) => (
              <Link
                key={u.revision_id}
                href={u.question_id ? `/research/questions?q=${encodeURIComponent(u.question_id)}` : "/research/questions"}
                className="group flex items-baseline gap-2 text-[10px] hover:bg-surface-2"
                title={u.question_id ? `打开提问页查看 v${u.seq}` : undefined}
              >
                <span className="w-[52px] shrink-0 text-[9px] text-ink-4" title={u.revision_id}>
                  v{u.seq}
                </span>
                <span className="text-ink-2">{u.content}</span>
                <span className="ml-auto shrink-0 text-[9px] text-ink-4">
                  {u.actor && USED_ACTOR[u.actor] && <span>{USED_ACTOR[u.actor]} </span>}
                  {relTime(u.created_at)}
                  <span className="ml-1 text-accent opacity-0 transition-opacity group-hover:opacity-100">→</span>
                </span>
              </Link>
            ))}
          </div>
        ) : (
          <div className="text-[10px] text-ink-4">尚无下游回答引用此条。</div>
        )}
      </div>
    </div>
  );
}

export function MemorySurfacePage() {
  const { useApi } = useApiPage({ distinguish: true });
  const api = useApi<MemoryResponse>("/api/v1/research/graph/memory", {
    refreshInterval: 60000,
  });
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [showClues, setShowClues] = useState(false);

  if (api.error) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-ink-3">
        {errorHintText(api.error.status)}
      </div>
    );
  }
  if (api.isLoading && !api.data) {
    return <div className="p-4 text-[11px] text-ink-3">加载中…</div>;
  }
  const data = api.data;
  if (!data) return null;

  const knowledge = data.knowledge ?? [];
  const clues = data.clues ?? [];
  const ru = data.recently_used;
  const expanded = expandedId ? knowledge.find((e) => e.id === expandedId) : undefined;

  return (
    <div className="h-full overflow-y-auto pb-[300px]">
      <div className="mx-auto flex max-w-[1120px] flex-col gap-2.5 px-4 pb-6 pt-3.5">
        {/* header:badge + 副题 + 最近一次被用到 */}
        <div className="flex items-baseline gap-2.5">
          <span className="rounded bg-gold/15 px-1.5 py-px text-[10px] font-semibold text-gold">
            记忆
          </span>
          <span className="text-[11px] text-ink-2">组织学到的,不是组织存过的</span>
          {ru && (
            <span className="ml-auto text-[9.5px] text-ink-4" title={ru.content}>
              最近一次被用到:v{ru.seq} · {relTime(ru.created_at)}
            </span>
          )}
        </div>

        {/* layer-strip 图例 */}
        <div className="flex items-baseline gap-3 text-[9.5px] text-ink-4">
          <span>
            既有知识 <b className="num text-ink-2">{data.counts?.knowledge ?? knowledge.length}</b>
          </span>
          <span className="text-border">|</span>
          <span>
            待检验线索 <b className="num text-ink-2">{data.counts?.clues ?? clues.length}</b>
          </span>
          <span className="ml-auto flex items-center gap-1">
            <span className="rounded bg-gold/10 px-1 text-gold">ep</span> Episode
          </span>
          <span className="flex items-center gap-1">
            <span className="rounded bg-pos/10 px-1 text-pos">ev</span> Evidence
          </span>
        </div>

        {/* 既有知识条目池 */}
        {knowledge.length === 0 ? (
          <div className="rounded-lg border border-border bg-surface-1 px-3.5 py-3 text-[10.5px] text-ink-4">
            尚无既有知识 — 当证据链走到 Episode / Evidence,条目会浮上来。
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {knowledge.map((e) =>
              expanded?.id === e.id ? (
                <DetailPanel key={e.id} e={expanded} onClose={() => setExpandedId(null)} />
              ) : (
                <EntryCard key={e.id} e={e} onExpand={setExpandedId} />
              ),
            )}
          </div>
        )}

        {/* 待检验线索:渐进披露折叠条 */}
        {clues.length > 0 && (
          <div className="rounded-lg border border-border-soft bg-surface-1">
            <button
              type="button"
              onClick={() => setShowClues((v) => !v)}
              className="flex w-full items-baseline gap-2 px-3.5 py-2 text-left text-[10px] text-ink-3"
            >
              待检验线索 · {clues.length} 条
              <span className="ml-auto text-[9px] text-ink-4">{showClues ? "收起 ▲" : "展开 ▼"}</span>
            </button>
            {showClues && (
              <div className="flex flex-col gap-1.5 px-3.5 pb-2.5">
                {clues.map((e) => (
                  <div key={e.id} className="flex items-baseline gap-2 text-[10.5px]">
                    <span className="shrink-0 text-[9px] text-ink-4">
                      {KIND_LABEL[e.kind]}
                    </span>
                    <span className="text-ink-2">{e.text ?? "—"}</span>
                    <span className="ml-auto shrink-0 text-[9px] text-ink-4">
                      {relTime(e.created_at)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
