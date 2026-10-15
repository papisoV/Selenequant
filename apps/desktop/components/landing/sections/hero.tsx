"use client";

/**
 * ① HERO —— 研究闭环轨道(hero-as-orbit)。
 *
 * 2026-10-07 P3:随 SeleneQuant 品牌换证重做。旧版是"细线 + 一颗流动光点"的
 * 线性 rail(审计底稿风格),与印记的**倾斜轨道 / 星芒 / 冷光**语言不同构。
 * 本版把闭环画成一条倾斜椭圆轨道:六节点落在环上,光点沿轨道公转,
 * 中心一枚四角星芒(呼应印记)——hero 的主视觉 = 印记的轨道语言放大成整页母题。
 *
 * 数据不变:六节点仍是 funding 四连判决真链(全部真 id,2026-10-02 快照),
 * 判决语义色不变(NO-GO 红 / 翻案 cyan)。用途色走 landing 作用域 token(身份桃)。
 *
 * 纪律同全站:SSR 静态完整可见(轨道/节点/标签都在);光点用 SMIL animateMotion
 * 沿 path 前进(与 memory-flow 同一 idiom,零 JS 循环);prefers-reduced-motion
 * 时隐藏光点、轨道静止;窄屏(<768)隐藏环上标签,改由下方图例承载(防 375px 糊)。
 *
 * 2026-10-07 动效返工 v1:环"生硬"。实测根因是 animateMotion 的默认 calcMode="paced"
 * (沿弧长匀速)——椭圆上等角度≠等弧长,六节点里有两段弧只有其余四段的 0.57 倍,
 * 于是站点间隔一长一短地交替(实测 3.08 / 1.75 / 3.17 s),本应平等的六步被节拍
 * 说成不平等。详见下方 ORBIT_KEY_* 常量处的注释。
 *
 * 2026-10-07 动效返工 v2:仍"僵硬"。v1 只治了节拍不均,没治**单调**。诊断:
 * 这个 hero 是"一个舞台上只有一个演员"—— 环、星芒、六个节点全静止,只有光点在走,
 * 结构上等于一个加载转圈(spinner);而且光点是对称圆点,没有方向感,读作"珠子滑轨"
 * 而不是"东西在飞"。本版改为**三层速度谱**(11s 环境呼吸 / 16s 公转 / 2.67s 过站应答),
 * 三层互质,合成周期 LCM(16,11)=176s 内不重复,节拍器感随之消失;彗星改定向
 * (rotate="auto" + 尾迹渐变),进出站用 keySplines 缓入缓出。详见 landing-scale.css
 * 的"活体动效谱"一节。
 */

import type { CSSProperties } from "react";

import { Reveal } from "../primitives";
import { ButtonPrimary, ButtonGhost } from "./shared";

/** 闭环六节点:funding 四连判决真链切片(全部真 id,2026-10-02 快照) */
const LOOP_NODES = [
  {
    step: "QUESTION",
    hue: "var(--color-primary)",
    id: "q-024cfb997e06",
    text: "funding 拥挤度对 BTC/ETH/SOL 收益有预测力吗?",
    date: "2026-09-22",
  },
  {
    step: "EXPERIMENT",
    hue: "var(--color-accent)",
    id: "exp-a8ba6b84e92b",
    text: "四轮 IC sweep:4-bar / 多日视界 / 入场否决 / carry+反转",
    date: "09-22 → 09-26",
  },
  {
    step: "EVIDENCE",
    hue: "var(--color-research)",
    id: "ev-df9effd9f8ab",
    text: "7d 视界全 symbol 过 0.10 线,方向一致",
    date: "2026-09-25",
  },
  {
    step: "DECISION",
    hue: "var(--color-ink)",
    id: "dec-6a48bd7d61c3",
    text: "V52 不加 funding 规则;不开多日赛道",
    date: "2026-10-03",
  },
  {
    step: "OUTCOME",
    hue: "var(--color-pos)",
    id: "outcome_pending",
    text: "观察窗开启:ETH funding 反转 IC 是否持续增强",
    date: "至 2026-11-02",
  },
  {
    step: "MEMORY",
    hue: "var(--color-memory)",
    id: "rev-c0515ec80093",
    text: "四连判决写入结论,等下一次回答来引用",
    date: "2026-09-26",
  },
] as const;

/* 轨道几何:倾斜椭圆。cx/cy 画布中心,rx/ry 半轴,tilt 倾角(呼应印记斜环)。 */
const CX = 480;
const CY = 212;
const RX = 358;
const RY = 148;
const TILT_DEG = -16;
const PHI = (TILT_DEG * Math.PI) / 180;
const COS_P = Math.cos(PHI);
const SIN_P = Math.sin(PHI);

/**
 * 闭环轨道 path:从正上 QUESTION 出发顺时针绕一圈(照 480 64 是 CY-RY)。
 * 起点刻意落在 QUESTION 节点上,且行进方向 = 六步的叙事方向(问→实验→证据→
 * 判决→结果→记忆),光点因此顺时针依次经过每一站,与环上节点的排布同向。
 */
const ORBIT_PATH_D = "M 480 64 A 358 148 0 1 1 480 360 A 358 148 0 1 1 480 64";

/**
 * 六节点沿轨道的**弧长**占比(0..1),顺序即行进顺序。用 SVG getPointAtLength
 * 实测,不是按角度算 —— 椭圆上等角度的六段弧长并不相等(358×148 的长短轴比
 * 2.42),其中 EXPERIMENT→EVIDENCE、OUTCOME→MEMORY 两段只有另四段的 0.57 倍。
 */
const ORBIT_KEY_POINTS = "0;0.1948;0.3053;0.5;0.6948;0.8053;1";

/**
 * 与 keyPoints 一一对应的时刻:一圈六等分,每站恒定 16/6 ≈ 2.67s。
 * animateMotion 的默认 calcMode 是 "paced"(沿弧长匀速),于是弧长不等就直接
 * 变成**节拍不等** —— 实测到站间隔 3.08 / 1.75 s 一长一短地交替,本该平等的
 * 六步被动画说成不平等,这正是"环生硬"的来源。显式给 linear + keyPoints +
 * keyTimes,把"每站停留同样长的时间"还给这一步。
 */
const ORBIT_KEY_TIMES = "0;0.1667;0.3333;0.5;0.6667;0.8333;1";

/** 六节点均布于环上(从正上 QUESTION 起,每 60° 一个),按倾角旋转到画布坐标。 */
const ORBIT_NODES = LOOP_NODES.map((n, i) => {
  const th = ((-90 + i * 60) * Math.PI) / 180;
  const dx = RX * Math.cos(th);
  const dy = RY * Math.sin(th);
  const x = CX + dx * COS_P - dy * SIN_P;
  const y = CY + dx * SIN_P + dy * COS_P;
  // 标签锚点:按水平位置分左/中/右,中位再按上/下错开,避免压环
  // (显式标注字面量联合:对象属性会把字面量拓宽成 string,SVG textAnchor 只收联合)
  const anchor: "end" | "start" | "middle" =
    x < CX - 70 ? "end" : x > CX + 70 ? "start" : "middle";
  const lx = anchor === "end" ? x - 15 : anchor === "start" ? x + 15 : x;
  const ly = anchor === "middle" ? (y < CY ? y - 13 : y + 25) : y + 4;
  return { ...n, x, y, anchor, lx, ly };
});

/** 中心四角星芒(与印记同构)。 */
const STAR =
  "M480 178 C483.5 203 489 208.5 514 212 C489 215.5 483.5 221 480 246 C476.5 221 471 215.5 446 212 C471 208.5 476.5 203 480 178 Z";

/**
 * 彗星尾迹(定向后画在 -x,即前进方向的反侧)。两档大小对应两颗光点。
 * 用 objectBoundingBox 渐变从尾(透明)到头(亮),渐变因此自动跟随各自 bbox。
 */
const COMET_TAIL_LG = "M0 -3.4 C -7 -3 -15 -1.7 -21 0 C -15 1.7 -7 3 0 3.4 Z";
const COMET_TAIL_SM = "M0 -2.2 C -5 -1.9 -11 -1.1 -15 0 C -11 1.1 -5 1.9 0 2.2 Z";

/**
 * 每段弧的缓动:六段一致用 ease-in-out(0.42,0,0.58,1)。配合 keyPoints/keyTimes
 * 的"每站等时",读出的是"在站台停一下 → 加速滑过弧 → 减速进站",而不是匀速珠子。
 * keySplines 的段数 = keyTimes 数 - 1 = 6。
 */
const ORBIT_KEY_SPLINES = Array(6).fill("0.42 0 0.58 1").join(";");

export function Hero() {
  return (
    <section id="hero" className="relative">
      <div className="lp-field" />
      <div className="lp-grid" />

      <div className="lp-container relative pb-24 pt-24 lg:pb-32 lg:pt-28">
        <Reveal y={12}>
          <p className="num font-mono text-[0.8125rem] text-ink-3">
            一个在运转的研究团队 · 非演示数据
          </p>

          <h1 className="lp-display mt-6 max-w-[14em] text-ink">
            提问、实验、判决、等待现实检验:
            <span className="text-ink-2">循环不停止。</span>
          </h1>

          <div className="mt-9 flex flex-wrap items-center gap-3.5">
            <ButtonPrimary href="/research/today">打开研究库</ButtonPrimary>
            <ButtonGhost href="#the-loop">看它如何研究</ButtonGhost>
            <p className="lp-meta ml-1">
              AGPL-3.0 开源 · 本机 SQLite · 历史不出机器
            </p>
          </div>
        </Reveal>

        {/* 主视觉:研究闭环轨道(倾斜椭圆 · 六节点 · 公转光点 · 中心星芒) */}
        <Reveal delay={0.12} y={16}>
          <div className="mt-14">
            <svg
              viewBox="0 0 960 430"
              className="lp-hero-orbit"
              role="img"
              aria-label={`研究闭环轨道:问题(q-024cfb997e06)经实验、证据、判决、结果,沉淀为记忆(rev-c0515ec80093),再进入下一次判断。全部节点可回源到库内 id,数据快照 2026-10-02。`}
            >
              <defs>
                {/* 轨道渐变:蓝→紫→暖桃,与印记同源 */}
                <linearGradient
                  id="lp-orbit-grad"
                  x1="120"
                  y1="360"
                  x2="840"
                  y2="64"
                  gradientUnits="userSpaceOnUse"
                >
                  <stop offset="0" stopColor="#309cfb" />
                  <stop offset="0.5" stopColor="#a78bfa" />
                  <stop offset="1" stopColor="#f7a07d" />
                </linearGradient>
                {/* 中心核径向光 */}
                <radialGradient id="lp-orbit-core">
                  <stop offset="0" stopColor="rgba(148,190,255,0.28)" />
                  <stop offset="1" stopColor="rgba(148,190,255,0)" />
                </radialGradient>
                {/* 星芒填充:中心亮 → 芒尖稍冷,让四角有"光衰"而非纯色块 */}
                <radialGradient id="lp-orbit-star-fill" cx="0.5" cy="0.5" r="0.5">
                  <stop offset="0" stopColor="#ffffff" />
                  <stop offset="0.45" stopColor="#eaf2ff" />
                  <stop offset="1" stopColor="rgba(196,216,255,0.72)" />
                </radialGradient>
                {/* 彗星尾迹:objectBoundingBox 上从尾(0)到头(1)由透明转亮 */}
                <linearGradient id="lp-comet-tail-lg" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0" stopColor="rgba(234,242,255,0)" />
                  <stop offset="1" stopColor="rgba(234,242,255,0.72)" />
                </linearGradient>
                <linearGradient id="lp-comet-tail-sm" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0" stopColor="rgba(234,242,255,0)" />
                  <stop offset="1" stopColor="rgba(234,242,255,0.5)" />
                </linearGradient>
                <path id="lp-orbit-path" d={ORBIT_PATH_D} fill="none" />
              </defs>

              {/* 轨道环(整体按倾角旋转;光点同组内随环公转)*/}
              <g transform={`rotate(${TILT_DEG} ${CX} ${CY})`}>
                <use href="#lp-orbit-path" className="lp-orbit-halo" stroke="url(#lp-orbit-grad)" strokeWidth={7} />
                <use href="#lp-orbit-path" className="lp-orbit-line" stroke="url(#lp-orbit-grad)" strokeWidth={1.6} />
                {/* 公转光点 ×2(相位错开半圈 = 3 站,读作"组织在持续运转")。
                    v2 改动:① rotate="auto" 让光点朝切线方向(有"飞行"方向感,
                    对称圆点读作珠子滑轨);② 加尾迹 path,朝 -x(即身后)拖出,
                    渐变从尾透明到头亮 —— 光点从"珠子"变成"彗星";③ calcMode
                    keySplines 让每段弧 ease-in-out(进出站减速),而不是匀速滑过。
                    显式 linear + keyPoints/keyTimes:按站点等时,不按弧长匀速。 */}
                <g className="lp-orbit-comet">
                  <path d={COMET_TAIL_LG} fill="url(#lp-comet-tail-lg)" aria-hidden />
                  <circle r={9} fill="#eaf2ff" opacity={0.2} />
                  <circle r={4.6} fill="#eaf2ff" />
                  <animateMotion
                    dur="16s"
                    begin="0s"
                    repeatCount="indefinite"
                    calcMode="spline"
                    keyPoints={ORBIT_KEY_POINTS}
                    keyTimes={ORBIT_KEY_TIMES}
                    keySplines={ORBIT_KEY_SPLINES}
                    rotate="auto"
                  >
                    <mpath href="#lp-orbit-path" />
                  </animateMotion>
                </g>
                <g className="lp-orbit-comet">
                  <path d={COMET_TAIL_SM} fill="url(#lp-comet-tail-sm)" aria-hidden />
                  <circle r={7} fill="#eaf2ff" opacity={0.14} />
                  <circle r={3.4} fill="#eaf2ff" opacity={0.8} />
                  <animateMotion
                    dur="16s"
                    begin="8s"
                    repeatCount="indefinite"
                    calcMode="spline"
                    keyPoints={ORBIT_KEY_POINTS}
                    keyTimes={ORBIT_KEY_TIMES}
                    keySplines={ORBIT_KEY_SPLINES}
                    rotate="auto"
                  >
                    <mpath href="#lp-orbit-path" />
                  </animateMotion>
                </g>
              </g>

              {/* 中心核光 + 星芒。两层各挂一条 11s 环境呼吸(与 16s 公转互质),
                  让"仪器在通电运转"这件事独立于光点存在 —— 这是把 hero 从
                  "只有一个演员的舞台"救出来的关键一层。reduced-motion 时静止。 */}
              <circle
                cx={CX}
                cy={CY}
                r={120}
                className="lp-orbit-breathe"
                fill="url(#lp-orbit-core)"
                aria-hidden
              />
              <path
                d={STAR}
                className="lp-orbit-star lp-orbit-breathe lp-orbit-breathe-star"
                fill="url(#lp-orbit-star-fill)"
                aria-hidden
              />

              {/* 六节点:点 + 步骤标签 + 真 id(标签锚点按环上位置分左/中/右)。
                  节点外加一圈"到站波纹",光点经过时该站亮一下 —— 让环与节点
                  发生关系,而不只是光点独自绕圈。相位由 --i 决定:一圈六等分,
                  每站 i * 16/6 秒,与光点的 keyTimes 同拍。 */}
              {ORBIT_NODES.map((n, i) => (
                <g key={n.step}>
                  <circle
                    cx={n.x}
                    cy={n.y}
                    r={9}
                    className="lp-orbit-ripple"
                    fill="none"
                    stroke={n.hue}
                    strokeWidth={1.5}
                    style={{ "--i": i } as CSSProperties}
                  />
                  {/* 节点本体(点 + 底 + 环)整组过站应答;标签不在此组内 ——
                      文字跟拍缩放会被读成"字在抖",是 bug 不是动效。 */}
                  <g className="lp-orbit-node" style={{ "--i": i } as CSSProperties}>
                    <circle cx={n.x} cy={n.y} r={9} fill="var(--color-bg)" opacity={0.85} />
                    <circle cx={n.x} cy={n.y} r={4.5} fill={n.hue} />
                    <circle cx={n.x} cy={n.y} r={9} fill="none" stroke={n.hue} strokeWidth={1} opacity={0.45} />
                  </g>
                  <text
                    className="lp-orbit-label"
                    x={n.lx}
                    y={n.ly}
                    textAnchor={n.anchor}
                    fill={n.hue}
                    fontSize={13}
                    fontWeight={600}
                    letterSpacing="0.04em"
                    fontFamily="var(--font-mono, monospace)"
                  >
                    {n.step}
                  </text>
                  <text
                    className="lp-orbit-label"
                    x={n.lx}
                    y={n.ly + 14}
                    textAnchor={n.anchor}
                    fill="rgba(138,147,158,0.85)"
                    fontSize={10}
                    fontFamily="var(--font-mono, monospace)"
                  >
                    {n.id}
                  </text>
                </g>
              ))}
            </svg>

            {/* 窄屏图例(环上标签在 <768 隐藏,此处补回六步;桌面隐藏)*/}
            <div className="mt-6 flex flex-wrap justify-center gap-x-4 gap-y-2 md:hidden">
              {LOOP_NODES.map((n) => (
                <span key={n.step} className="num flex items-center gap-1.5 font-mono text-[0.6875rem]">
                  <span className="size-2 rounded-full" style={{ backgroundColor: n.hue }} />
                  <span style={{ color: n.hue }}>{n.step}</span>
                </span>
              ))}
            </div>

            {/* 诚实戳:真循环 + 快照 + 可回源 */}
            <p className="num mt-8 border-t border-[var(--lp-hair)] pt-4 text-center font-mono text-[0.75rem] text-ink-4">
              一次真实研究循环(funding 四连判决)· 数据快照 2026-10-02 ·
              每步可回源到库内 id
            </p>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
