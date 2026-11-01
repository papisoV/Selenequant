"use client"

import React from "react"
import { LightweightAreaChart } from "./lightweight-area-chart"

interface EquityCurveChartProps {
  data: number[]
  timestamps?: number[]
  baselineData?: number[]
  baselineLabel?: string
  height?: number
  className?: string
  ariaLabel?: string
}

export function EquityCurveChart(props: EquityCurveChartProps) {
  return <LightweightAreaChart {...props} mode="equity" />
}

interface DrawdownChartProps {
  data: number[]
  timestamps?: number[]
  height?: number
  className?: string
  ariaLabel?: string
}

export function DrawdownChart(props: DrawdownChartProps) {
  return <LightweightAreaChart {...props} mode="drawdown" />
}
