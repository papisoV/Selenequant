"use client";

/**
 * 值班台(Research Control Surface 第一刀)。
 *
 * 语义:自上次以来,研究世界发生了什么变化,哪些值得 Director 介入。
 * 不是通知中心:无未读数、无推送;三层确定性排序(后端 collect_changes 已排好):
 *  tier1 常驻 = 待 Director 处理(拍板/观察窗到期/系统提议),每次打开都在
 *  tier2 流   = 判断性变化(revision/状态轴)
 *  tier3 折叠 = 进展记录(perspective/新证据),默认收起不霸屏
 *
 * 锚点闭环:mount 读 localStorage → 以锚点(缺省回看 7 天)请求 /changes →
 * 加载成功写 server_now;unmount 补写最后一次 server_now(防久坐后锚点过期)。
 * epistemic 徽章:研究记录 vs 经验推断 vs 探针(RECORDED)+ 引证据,
 * 「是事实还是推断」直觉可辨;CONSEQUENCE 是后端确定性模板,前端不脑补因果。
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";

import {
  fetchChanges,
  type ChangeItem,
  type ChangesResult,
} from "@/lib/research/changes";
import {
  BACKLOOK_DAYS,
  readLastVisited,
  writeLastVisited,
} from "@/lib/research/last-visit";
import {
  getGraphIntegrity,
  type GraphIntegrityReport,
} from "@/lib/api/research-graph";

const KIND_LABEL: Record<string, string> = {
  revision: "判断更新",
  decision_pending: "待拍板",
  window_expired: "观察窗到期",
  proposal: "系统提议",
  created: "问题创建",
  closed: "问题关闭",
  reopened: "问题重开",
  superseded: "被取代",
  superseding: "取代了旧问题",
  forked: "分叉",
  investigate_perspective: "视角发言",
  investigate_finished: "调查完成",
  evidence: "新证据",
};

const SOURCE_LABEL: Record<string, string> = {
  research_record: "研究记录",
  learned_experience: "经验推断",
};

const POLL_INTERVAL_MS = 30_000;

// 后端给的是 UTC ISO;裸切前 16 位会让人误读成本地时间,转成浏览器本地时区显示。
function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 16).replace("T", " ");
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function ChangeRow({ item }: { item: ChangeItem }) {
  const badges = [
    SOURCE_LABEL[item.epistemic.source],
    item.epistemic.evidence_mode === "RECORDED" ? "探针" : null,
    item.epistemic.cites_evidence ? "引证据" : null,
  ].filter(Boolean) as string[];

  const inner = (
    <>
      <div className="flex items-center gap-2 text-[11px] text-ink-4">
        <span>{KIND_LABEL[item.kind] ?? item.kind}</span>
        <span>{fmtTime(item.occurred_at)}</span>
        {badges.map((b) => (
          <span key={b} className="rounded bg-surface-3 px-1">
            {b}
          </span>
        ))}
        <span>by {item.actor}</span>
      </div>
      <div className="text-sm text-ink">{item.question_title}</div>
      {item.consequence ? (
        <div className="text-xs text-ink-3">{item.consequence}</div>
      ) : null}
    </>
  );

  const cls =
    "block rounded border border-border bg-surface-2 px-3 py-2 hover:bg-surface-3";

  if (!item.question_id) {
    return (
      <div className={cls} data-testid="desk-change">
        {inner}
      </div>
    );
  }
  return (
    <Link
      href={`/research/questions?q=${item.question_id}`}
      className={cls}
      data-testid="desk-change"
    >
      {inner}
    </Link>
  );
}

function Section({
  title,
  items,
  tone,
}: {
  title: string;
  items: ChangeItem[];
  tone: "accent" | "plain";
}) {
  if (items.length === 0) return null;
  return (
    <section data-testid={`desk-tier${tone === "accent" ? 1 : 2}`}>
      <h2
        className={`mb-2 text-xs ${tone === "accent" ? "text-accent" : "text-ink-3"}`}
      >
        {title}
      </h2>
      <div className="flex flex-col gap-2">
        {items.map((c) => (
          <ChangeRow key={c.id} item={c} />
        ))}
      </div>
    </section>
  );
}

export function DeskPage() {
  const [data, setData] = useState<ChangesResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [firstVisit, setFirstVisit] = useState(false);
  const [t3Open, setT3Open] = useState(false);
  const [integrity, setIntegrity] = useState<GraphIntegrityReport | null>(null);
  const [integrityFailed, setIntegrityFailed] = useState(false);
  const serverNowRef = useRef<string | null>(null);

  useEffect(() => {
    const anchor = readLastVisited();
    setFirstVisit(anchor === null);
    // 锚点纪律:轮询恒用 mount 时的同一 since——「自上次以来」=「自打开值班台以来」,
    // 锚点只在 unmount 推进(写最新 server_now),轮询不推进。
    const since =
      anchor ?? new Date(Date.now() - BACKLOOK_DAYS * 86400_000).toISOString();
    let cancelled = false;
    const load = () =>
      fetchChanges(since).then((res) => {
        if (cancelled) return;
        if (res) {
          serverNowRef.current = res.server_now;
          setData(res);
          // 首次加载成功才写锚点(轮询成功不写,unmount 统一写 serverNowRef)
          writeLastVisited(res.server_now);
        } else {
          setError("加载失败");
        }
      });
    load();
    const timer = setInterval(load, POLL_INTERVAL_MS);
    // integrity 扫全表,一次性 fetch 不进 5s/30s 轮询(恒定健康度 ≠ 时间变化)
    getGraphIntegrity()
      .then((r) => {
        if (!cancelled) setIntegrity(r);
      })
      .catch(() => {
        if (!cancelled) setIntegrityFailed(true);
      });
    return () => {
      cancelled = true;
      clearInterval(timer);
      if (serverNowRef.current) writeLastVisited(serverNowRef.current);
    };
  }, []);

  const t1 = data?.changes.filter((c) => c.tier === 1) ?? [];
  const t2 = data?.changes.filter((c) => c.tier === 2) ?? [];
  const t3 = data?.changes.filter((c) => c.tier === 3) ?? [];

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-6">
      <h1 className="text-lg text-ink">
        {firstVisit ? `最近 ${BACKLOOK_DAYS} 天的变化` : "自上次以来的变化"}
      </h1>

      {error ? <div className="text-sm text-ink-3">{error}</div> : null}

      {!data && !error ? (
        <div className="text-sm text-ink-4">加载中⋯</div>
      ) : null}

      {data && data.changes.length === 0 ? (
        <div className="rounded border border-border bg-surface-2 px-4 py-6 text-sm text-ink-3">
          <div className="text-ink">这段时间没有变化</div>
          <div className="mt-1 text-xs">
            有新回答、需要拍板的决定或新证据时,会出现在这里。
          </div>
        </div>
      ) : null}

      {data && data.changes.length > 0 ? (
        <>
          <Section
            title="待 Director 处理"
            items={t1}
            tone="accent"
          />
          <Section title="判断变化" items={t2} tone="plain" />

          {t3.length > 0 ? (
            <section data-testid="desk-tier3">
              <button
                type="button"
                onClick={() => setT3Open((v) => !v)}
                className="inline-flex min-h-[24px] items-center text-xs text-ink-3 hover:text-ink-2"
              >
                {t3Open ? "收起进展记录" : `展开进展记录(${t3.length})`}
              </button>
              {t3Open ? (
                <div className="mt-2 flex flex-col gap-2">
                  {t3.map((c) => (
                    <ChangeRow key={c.id} item={c} />
                  ))}
                </div>
              ) : null}
            </section>
          ) : null}
        </>
      ) : null}

      {/* 账本完整性(刀5):恒定健康度,独立 fetch 不轮询 */}
      <section data-testid="desk-integrity" className="mt-4 border-t border-border-soft pt-3">
        {integrityFailed ? (
          <p className="text-xs text-ink-4">完整性查询失败</p>
        ) : integrity ? (
          <>
            <h2
              className={`text-xs ${integrity.ok ? "text-ink-3" : "text-neg"}`}
            >
              账本完整性
              {integrity.ok
                ? " ✓"
                : ` · ${integrity.violations.length} 条违规`}
            </h2>
            <p className="mt-1 font-mono text-[10px] text-ink-4">
              {Object.entries(integrity.counts)
                .map(([k, v]) => `${k}=${v}`)
                .join(" · ")}
            </p>
            {integrity.violations.length > 0 && (
              <ul className="mt-1.5 space-y-1">
                {integrity.violations.map((v) => (
                  <li
                    key={v.object_id + v.rule}
                    className="text-[11px] text-neg"
                    data-testid="integrity-violation"
                  >
                    <span className="font-mono text-[10px]">{v.rule}</span>
                    <span className="ml-2 font-mono text-[10px] text-ink-3">
                      {v.object_id}
                    </span>
                    <span className="ml-2 text-ink-2">{v.detail}</span>
                  </li>
                ))}
              </ul>
            )}
            {integrity.scope_pressure.length > 0 && (
              <p className="mt-1.5 text-[10px] text-gold" data-testid="scope-pressure">
                scope_pressure:{integrity.scope_pressure.length} 条(建议扩问,非错误)
              </p>
            )}
          </>
        ) : null}
      </section>
    </div>
  );
}
