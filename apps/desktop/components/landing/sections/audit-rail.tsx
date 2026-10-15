"use client";

/**
 * ⑧ 审计栏 —— 原「库里的真东西」整节降为可展开的审计带。
 *
 * 重构理由(2026-10-03 叙事弧复盘):真实 COUNT 表放在 Memory 之后当独立章节,
 * 是全页的叙事谷底(峰值在调查会,谷底在 SQL 表,收尾太平)。改为 <details> 折叠:
 * 默认 ≤120px 一行声明,展开才有完整计数表 —— 证据在手边,不打断叙事。
 *
 * 纪律:零值如实显示(执行环节 0 就写 0,不隐藏);
 * 不冒充产品截图;数字全部来自 lib/landing/real-data.ts 的真实 COUNT
 * (刷新:`python scripts/refresh-landing-snapshot.py --write`)。
 */

import { DB_OBJECTS, SKILL_GROUPS, SCALE_FACTS } from "@/lib/landing/real-data";
import { Reveal } from "../primitives";

const NONZERO = DB_OBJECTS.filter((o) => !o.zero);
const ZERO = DB_OBJECTS.filter((o) => o.zero);

export function AuditRail() {
  return (
    <section id="audit" className="lp-hair-b relative">
      <div className="lp-container py-8">
        <Reveal y={8}>
          <details className="group">
            <summary className="flex cursor-pointer list-none items-baseline gap-3 [&::-webkit-details-marker]:hidden">
              <span className="num text-[0.8125rem] tracking-[0.03em] text-ink-3">
                审计
              </span>
              <span className="text-[0.9375rem] text-ink-2">
                库里共 {SCALE_FACTS.backtests} 条真实回测、{SCALE_FACTS.questions}{" "}
                个持久研究问题、{SCALE_FACTS.evidence} 项证据。决策记录落了 2 条,
                但执行环节(live_orders / fills)仍是 0,如实报 0。
              </span>
              <span className="num ml-auto shrink-0 font-mono text-[0.75rem] text-ink-3">
                <span className="group-open:hidden">[ 展开全表 ]</span>
                <span className="hidden group-open:inline">[ 收起 ]</span>
              </span>
            </summary>

            <div className="mt-6 border-t border-[var(--lp-hair)]">
              {NONZERO.map((o) => (
                <div
                  key={o.table}
                  className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-6 gap-y-1 border-b border-[var(--lp-hair)] py-4 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)_auto]"
                >
                  <span className="num font-mono text-[0.8125rem] text-ink-3">
                    {o.table}
                  </span>
                  <span className="order-last col-span-2 text-[0.9375rem] text-ink-2 sm:order-none sm:col-span-1">
                    {o.note}
                  </span>
                  <span className="num text-right font-mono text-[1.25rem] font-semibold leading-none text-pos">
                    {o.count}
                  </span>
                </div>
              ))}

              {ZERO.map((o) => (
                <div
                  key={o.table}
                  className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-6 gap-y-1 border-b border-[var(--lp-hair)] py-4 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)_auto]"
                >
                  <span className="num font-mono text-[0.8125rem] text-ink-3">
                    {o.table}
                  </span>
                  <span className="order-last col-span-2 text-[0.9375rem] text-ink-3 sm:order-none sm:col-span-1">
                    {o.note}(不掩饰,如实标出)
                  </span>
                  <span className="num text-right font-mono text-[1.25rem] font-semibold leading-none text-ink-3">
                    {o.count}
                  </span>
                </div>
              ))}

              <div className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-2">
                <span className="num font-mono text-[0.8125rem] text-ink-3">
                  ~/.selenespace/data/selenespace.db
                </span>
                <span className="text-[0.875rem] text-ink-3">
                  · 本地 SQLite · 直接 COUNT · 可自行 sqlite3 核对
                </span>
              </div>

              <div className="mt-8">
                <div className="flex items-baseline gap-3">
                  <span className="text-[0.875rem] font-medium text-ink-2">
                    内置 Skill
                  </span>
                  <span className="h-px flex-1 bg-[var(--lp-hair)]" />
                  <span className="num text-[0.8125rem] text-ink-3">
                    运行时加载两组,共 16 个
                  </span>
                </div>
                <div className="mt-5 grid grid-cols-1 gap-8 sm:grid-cols-2">
                  {SKILL_GROUPS.map((g) => (
                    <div key={g.label}>
                      <div className="flex items-baseline gap-2">
                        <span className="text-[0.9375rem] font-medium text-ink-2">
                          {g.label}
                        </span>
                        <span className="num font-mono text-[0.75rem] text-ink-3">
                          {g.dir}
                        </span>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {g.items.map((s) => (
                          <span
                            key={s}
                            className="num rounded border border-[var(--lp-hair)] px-2 py-1 font-mono text-[0.75rem] text-ink-3"
                          >
                            {s}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </details>
        </Reveal>
      </div>
    </section>
  );
}
