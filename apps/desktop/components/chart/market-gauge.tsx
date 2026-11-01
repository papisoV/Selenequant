"use client";

import { useUiText } from "@/components/i18n/use-ui-text";

interface Props {
  value: number;
}

export function MarketGauge({ value }: Props) {
  const tr = useUiText();
  const label = value >= 75 ? "极度贪婪" : value >= 55 ? "贪婪" : value >= 45 ? "中性" : value >= 25 ? "恐惧" : "极度恐惧";
  const color =
    value >= 75
      ? "var(--color-neg)"
      : value >= 55
        ? "var(--color-gold)"
        : value >= 45
          ? "var(--color-ink-3)"
          : "var(--color-pos)";
  const angle = -90 + (value / 100) * 180;
  const r = 14;
  const rad = (angle * Math.PI) / 180;
  const x = 16 + r * Math.cos(rad);
  const y = 16 + r * Math.sin(rad);

  return (
    <div className="market-gauge">
      <svg viewBox="0 0 32 24">
        <path d="M2 22 A 14 14 0 0 1 30 22" fill="none" stroke="var(--color-surface-3)" strokeWidth="2" />
        <path
          d="M2 22 A 14 14 0 0 1 30 22"
          fill="none"
          stroke={color}
          strokeWidth="2"
          strokeDasharray={`${(value / 100) * 44} 100`}
        />
        <line x1="16" y1="16" x2={x} y2={y} stroke={color} strokeWidth="1.5" strokeLinecap="round" />
        <circle cx="16" cy="16" r="1.5" fill={color} />
      </svg>
      <span className="market-gauge-val" style={{ color }}>
        {value}
      </span>
      <div className="market-gauge-tip">
        Fear & Greed Index · {value} {tr(label)}
      </div>
    </div>
  );
}
