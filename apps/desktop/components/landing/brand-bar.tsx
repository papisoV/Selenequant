"use client";

/**
 * ① 品牌抬头(brand letterhead)—— 不是 SaaS 导航条。
 *
 * 2026-10-04 R7:用户指出整页没有一处显示 selenequant / SeleneQuant 名字标识。
 * 诊断:页面顶部一无所有,品牌名只在页脚出现一次小字。
 *
 * 设计决定(避开品类反射):
 * - 不做「左 logo + 中导航 + 右 CTA 按钮」的 SaaS 顶栏 —— 那是对本页的误读:
 *   右侧 ProgressRail 已承担章节导航,且整页是 audit-ledger 车道,不是产品首页。
 * - 改做**审计底稿抬头**:左 = 月痕 + 词标(身份),右 = 文档戳(真读数 + 版本)。
 *   无导航链接、无营销按钮 —— 抬头只声明"这是什么文档",导航在右侧轨道。
 * - 月痕形状与 README docs/assets/logo.svg 同一枚(Selene = 月神 crescent);
 *   此处用 inline SVG + currentColor 而非 README 的 <text> 版 —— 后者在 web 上
 *   依赖 Helvetica/SF Mono,本项目全站自托管字体、构建期零外网,会字体退化。
 * - 粘滞:滚动时细边框 + 半透明底,不遮内容;SSR 静态可见,零新增动画。
 */

import Image from "next/image";
import { Github } from "lucide-react";
import { SCALE_FACTS } from "@/lib/landing/real-data";
import { LocaleSwitcherCompact } from "@/components/i18n/locale-switcher";
import { useUiText } from "@/components/i18n/use-ui-text"

/** 印记:SeleneQuant 轨道新月徽(官方原图)。
 *  来源 `<local-path-redacted> Orbital Crescent Emblem.png`(1254² 真透明底),
 *  经 scripts 裁切主体 + 缩放到 256² → public/logo-mark.png。
 *  —— 不做手绘重描:原图的发光渐变与立体月牙是设计资产,重画只会失真。
 *  透明底 → 可直接叠在半透明毛玻璃抬头,无深色方块。 */
function MoonMark({ className = "" }: { className?: string }) {
  return (
    <Image
      src="/logo-mark.png"
      alt=""
      aria-hidden
      width={512}
      height={512}
      priority
      className={className}
    />
  );
}

export function BrandBar() {
  const tr = useUiText()

  return (
    <header className="sticky top-0 z-30 border-b border-[var(--lp-hair)] bg-bg/80 backdrop-blur-sm">
      <div className="lp-container flex items-center gap-3 py-3.5">
        {/* 左:月痕 + 词标(身份) */}
        <a
          href="#hero"
          className="group flex items-center gap-2.5 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
        >
          <MoonMark className="size-8 shrink-0" />
          <span className="whitespace-nowrap text-[1.0625rem] font-semibold tracking-[-0.01em] text-ink">
            {"Selene"}
            <span className="text-primary">Quant</span>
          </span>
        </a>

        {/* 右:文档戳 —— 用途说明 + 真实计数(可回库核对) */}
        <div className="ml-auto flex items-center gap-4">
          <span className="num hidden font-mono text-[0.75rem] text-ink-3 sm:inline">
            {tr("AI 量化研究团队")}
          </span>
          <span
            aria-hidden
            className="hidden h-3.5 w-px bg-[var(--lp-hair-strong)] sm:inline-block"
          />
          <span className="num font-mono text-[0.75rem] text-ink-3">
            <span className="text-ink-2">{SCALE_FACTS.questions}</span> {tr("问")}
            {" · "}
            <span className="text-ink-2">{SCALE_FACTS.evidence}</span> {tr("证据")}
          </span>
          <a
            href="https://github.com/papisoV/selenequant"
            target="_blank"
            rel="noopener noreferrer"
            aria-label={tr("GitHub 仓库")}
            className="inline-flex min-h-6 items-center text-ink-3 transition-colors hover:text-ink-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 focus-visible:rounded"
          >
            <Github className="size-4" strokeWidth={1.75} />
          </a>
          <LocaleSwitcherCompact />
        </div>
      </div>
    </header>
  );
}
