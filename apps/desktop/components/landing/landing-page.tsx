import { Hero } from "@/components/landing/sections/hero";
import { BrandBar } from "@/components/landing/brand-bar";
import { TickerRail } from "@/components/landing/sections/ticker-rail";
import { Council } from "@/components/landing/sections/council";
import { TheLoop } from "@/components/landing/sections/the-loop";
import { BacktestWall } from "@/components/landing/sections/backtest-wall";
import { Bench } from "@/components/landing/sections/bench";
import { NotAChatbot } from "@/components/landing/sections/not-a-chatbot";
import { Memory } from "@/components/landing/sections/memory";
import { AuditRail } from "@/components/landing/sections/audit-rail";
import { OpenLocal } from "@/components/landing/sections/open-local";
import { CtaSection, Footer } from "@/components/landing/sections/cta-footer";
import { ProgressRail } from "@/components/landing/progress-rail";
import { SectionDivider } from "@/components/landing/primitives";

/**
 * Landing 页 —— "Selene 产品机制的一次真实演示",不是功能宣传页。
 *
 * IA(2026-10-03 二次定,按"先看它真在跑什么,再看它跟别家差在哪"排序):
 *   ① HERO        大主张 + 真实回测曲线 + 版本链
 *   ② 刻度条      真实规模读数 + 真回测收益(跑马灯)
 *   ③ 调查会      AI 自主研究:三视角独立查 → 当场分歧 → 主持人裁决
 *   ④ 研究回路    一个判断被四次实验改变(含一次翻案)
 *   ⑤ 回测墙      它真在跑量化研究,含跑输的那些
 *   ⑤b 实验台     回路脚下的仪器面:市场源 / 49 内置模板(七族密度条)/ 调参目标
 *   ⑥ 不是什么    机制差异:一次性对话 vs 持久问题
 *   ⑦ 记忆        反向索引:这条知识改变了哪一次回答(分界句是本段落点)
 *   ⑧ 审计栏      折叠的真实 COUNT 带(可展开核对,不打断叙事)
 *   ⑨ 开源与本地
 *   ⑩ CTA / Footer
 *
 * 尺度:landing-scale.css 把 rem 基准复位到 16px(产品本体是 11px 终端密度)。
 */
export function LandingPage() {
  return (
    <main className="landing-root min-h-screen bg-bg text-ink antialiased">
      <BrandBar />
      <ProgressRail />
      <Hero />
      <TickerRail />
      <Council />
      <SectionDivider variant="glow" hue="rgba(247, 160, 125, 0.10)" />
      <TheLoop />
      <SectionDivider variant="glow" hue="rgba(34, 211, 238, 0.1)" />
      <BacktestWall />
      <SectionDivider variant="dot" hue="rgba(48, 156, 251, 0.09)" />
      <Bench />
      <SectionDivider variant="dot" hue="rgba(48, 156, 251, 0.09)" />
      <NotAChatbot />
      <SectionDivider variant="dot" />
      <Memory />
      <SectionDivider variant="dot" hue="rgba(167, 139, 250, 0.1)" />
      <AuditRail />
      <OpenLocal />
      <CtaSection />
      <Footer />
    </main>
  );
}
