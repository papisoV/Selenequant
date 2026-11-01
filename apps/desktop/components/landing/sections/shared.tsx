"use client";

/**
 * Landing 共用件 —— CTA 按钮与箭头。
 * 与产品同源:accent 只给主操作,次操作用细边框;不另造营销皮肤。
 * 图标沿用项目既有 lucide-react(不引入第二套图标族,见项目约定)。
 */

import Link from "next/link";
import { ArrowRight } from "lucide-react";

export function Arrow({ className = "" }: { className?: string }) {
  return <ArrowRight className={className} strokeWidth={2} />;
}

export function ButtonPrimary({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className="group inline-flex items-center gap-2 rounded-md bg-primary px-5 py-2.5 text-[0.9375rem] font-semibold text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
    >
      {children}
      <Arrow className="size-4 transition-transform group-hover:translate-x-0.5" />
    </Link>
  );
}

export function ButtonGhost({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      className="inline-flex items-center gap-2 rounded-md border border-[var(--lp-hair-strong)] px-5 py-2.5 text-[0.9375rem] text-ink-2 transition-colors hover:border-accent/50 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
    >
      {children}
    </a>
  );
}
