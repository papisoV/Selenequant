"use client";

import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import {
  getCategoryFromPath,
  CATEGORY_LABELS,
  CATEGORY_COLORS,
  type PageCategory,
} from "@/lib/data/page-categories";

/**
 * 分类色条 — 在 SSR 骨架 / loading 阶段顶部显示当前页面的数据分类。
 * - 顶部 4px 色条（按 category 配色）
 * - 色条下方小字：分类名（如 "交易" / "行情"）
 *
 * 仅在能识别出 category 时渲染；根路径或未匹配返回 null。
 * 不替代 MockPreviewLoading，作为其上层视觉提示。
 */
export function CategoryLoadingBanner() {
  const pathname = usePathname();
  const category: PageCategory | null = pathname ? getCategoryFromPath(pathname) : null;
  if (!category) return null;

  const colors = CATEGORY_COLORS[category];
  const label = CATEGORY_LABELS[category];

  return (
    <div className={`fixed top-0 left-0 right-0 z-[60] ${colors.border} border-t-4 ${colors.bg}`}>
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25, ease: "easeOut" }}
        className="flex items-center justify-center gap-2 py-0.5 text-[10px] font-mono"
      >
        <span className={`size-1.5 rounded-full ${colors.border.replace("border-", "bg-")} animate-pulse`} />
        <span className={`${colors.text} font-medium uppercase tracking-wider`}>{category}</span>
        <span className="text-white/40">·</span>
        <span className="text-white/60">{label}</span>
      </motion.div>
    </div>
  );
}
