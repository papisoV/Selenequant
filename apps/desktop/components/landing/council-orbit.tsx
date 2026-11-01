"use client";

/**
 * 调查会轨道全景 —— 旧 Landing 轨道/雷达词汇的真实数据重建。
 *
 * 机制可视化(与真实 investigate_council 一致):
 * - 中心 = 被调查的问题(q-804aa2c92a2c);
 * - 三颗卫星 = 三个视角,沿轨道缓慢公转(材料纪律不同 → 卫星色不同);
 * - 卫星上的小数字 = 该视角真实引用的证据条数(记录派恒为 0,纪律使然);
 * - 外圈雷达扫描 = 视角事件在真实库里已跑过 48 次(eventsRun)。
 *
 * 动效纪律:纯 CSS 旋转(lp-orbit-spin),无内联 opacity;SSR 静态完整可见;
 * prefers-reduced-motion 时全部静止但内容仍在。
 * 裁剪:旋转臂由内层画布 overflow-hidden 收敛(375px 防溢出),
 * 底部事件读数放在画布外层,不被裁掉。
 */

import { COUNCIL, type CouncilPerspective } from "@/lib/landing/real-data";
import { useLocale, useUiText } from "@/components/i18n/use-ui-text"

const SAT_TONE: Record<
  CouncilPerspective["key"],
  { dot: string; text: string; border: string }
> = {
  empiricist: { dot: "bg-research", text: "text-ink", border: "border-research/40" },
  recordist: { dot: "bg-memory", text: "text-ink", border: "border-memory/40" },
  challenger: { dot: "bg-neg", text: "text-ink", border: "border-neg/40" },
};

export function CouncilOrbit() {
  const tr = useUiText()
  const locale = useLocale()

  const c = COUNCIL;
  // 三颗卫星均布 120°;画布 320px,轨道半径 40%(= 外环 inset-[10%] 环线)
  const angles = [0, 120, 240];

  return (
    <div className="mx-auto w-fit select-none">
      <div
        className="relative size-80 overflow-hidden"
        role="img"
        aria-label={tr("调查会示意:问题 {q} 被三个视角({names})独立调查,主持人裁决", {
          q: tr(c.questionTitle),
          names: c.perspectives.map((p) => tr(p.label)).join(locale === "en" ? ", " : "、"),
        })}
      >
        {/* 轨道环 ×2 */}
        <div className="lp-orbit-ring inset-[10%]" aria-hidden />
        <div className="lp-orbit-ring inset-[26%] opacity-60" aria-hidden />

        {/* 雷达扫描(外环内) */}
        <div className="lp-sweep absolute inset-[10%] rounded-full" aria-hidden />

        {/* 中心:问题节点 */}
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
          <div className="flex flex-col items-center">
            <span
              className="relative flex size-3"
              style={{ "--pulse-rgb": "247, 160, 125" } as React.CSSProperties}
            >
              <span className="lp-pulse-dot absolute inline-flex size-3 rounded-full bg-gold/40" />
              <span className="relative inline-flex size-3 rounded-full bg-gold" />
            </span>
            <span className="mt-2 max-w-36 text-center text-[0.75rem] leading-tight text-ink-2">
              {tr(c.questionTitle)}
            </span>
            <span className="num mt-1 font-mono text-[0.6875rem] text-ink-3">
              {c.questionId}
            </span>
          </div>
        </div>

        {/* 三视角卫星:arm 旋转,卫星本体反向转保持水平(反向转与 arm 同 delay 才能抵消) */}
        {c.perspectives.map((p, i) => {
          const tone = SAT_TONE[p.key];
          const delay = `${-(i * (52 / 3))}s`;
          // 取 3 位小数:V8 的 sin/cos 在 SSR(Node)与浏览器水合间可能差 1 ULP,
          // 直接插值会渲染出 15.358983848622465% vs 15.359% 而触发 hydration 失配。
          const round = (n: number) => Math.round(n * 1000) / 1000;
          const left = round(50 + 40 * Math.sin((angles[i] * Math.PI) / 180));
          const top = round(50 - 40 * Math.cos((angles[i] * Math.PI) / 180));
          return (
            <div
              key={p.key}
              className="lp-orbit-arm"
              style={
                { "--orbit-t": "52s", animationDelay: delay } as React.CSSProperties
              }
              aria-hidden
            >
              <div
                className="lp-orbit-sat"
                style={{ left: `${left}%`, top: `${top}%` }}
              >
                <div
                  className={`num flex items-center gap-1.5 rounded-full border ${tone.border} bg-bg/90 px-2 py-1 font-mono text-[0.6875rem] ${tone.text}`}
                  style={{ animationDelay: delay }}
                >
                  <span className={`size-1.5 rounded-full ${tone.dot}`} />
                  {tr(p.label)}
                  <span className="text-ink-3">{p.citedIds.length}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* 底部读数(画布外,不被裁剪):真实事件计数 */}
      <div className="num mt-2 text-center font-mono text-[0.6875rem] text-ink-3">
        {tr("视角事件")} {c.eventsRun} {tr("次 · 成功")} {c.eventsOk} {tr("次 · 失败降级继续")}
      </div>
    </div>
  );
}
