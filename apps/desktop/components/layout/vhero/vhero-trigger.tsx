"use client";

import { clickable } from "@/lib/a11y/clickable";

interface Props {
  kind: "asset" | "risk";
  label: string;
  value: string;
  valueClass?: "gold" | "pos" | "neg" | "warn";
  active?: boolean;
  onClick?: () => void;
}

export function VheroTrigger({ kind, label, value, valueClass, active, onClick }: Props) {
  const icon =
    kind === "asset" ? (
      <svg viewBox="0 0 12 12" fill="none" className="trig-icon">
        <circle cx="6" cy="6" r="5" stroke="currentColor" strokeWidth="1" />
        <path d="M6 3 v6 M3 6 h6" stroke="currentColor" strokeWidth="1" />
      </svg>
    ) : (
      <svg viewBox="0 0 12 12" fill="none" className="trig-icon">
        <path d="M6 1 L11 10 L1 10 Z" stroke="currentColor" strokeWidth="1" fill="none" />
        <path d="M6 5 v3" stroke="currentColor" strokeWidth="1" />
        <circle cx="6" cy="9" r="0.5" fill="currentColor" />
      </svg>
    );

  return (
    <div
      className={`vhero-trigger ${active ? "active" : ""} ${kind === "risk" ? "risk-flash" : ""}`}
      aria-expanded={active}
      {...clickable(onClick)}
    >
      {icon}
      <span className="trig-label">{label}</span>
      <span className={`trig-value ${valueClass ?? ""}`}>{value}</span>
      <svg viewBox="0 0 4 8" fill="none" className="trig-arrow">
        <path d="M1 1 L3 4 L1 7" stroke="currentColor" strokeWidth="1" />
      </svg>
    </div>
  );
}
