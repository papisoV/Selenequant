"use client";

import { useMemo } from "react";
import { useI18nContext } from "@/components/i18n/i18n-provider";
import { readChartTokens, type ChartTheme } from "@/lib/chart/theme";

/**
 * 图表 token 契约 hook —— canvas 图表(lightweight-charts / echarts)经此读色。
 *
 * locale 源 = `useI18nContext().locale`(单一事实源)。
 * ⚠️ **不用 MutationObserver**:`i18n-provider.tsx` 的 effect 已在 locale 变化时
 *    写 `documentElement.dataset.locale`,那是派生值;用 observer 监听它 = 在
 *    React 已有响应式之上重建第二事实源。
 *
 * 返回对象按 locale memo —— locale 变化时返回**新引用**,消费方把它放进
 * effect/useMemo 依赖即可触发重绘。
 */
export function useChartTheme(): ChartTheme {
  const { locale } = useI18nContext();
  return useMemo(() => readChartTokens(), [locale]);
}
