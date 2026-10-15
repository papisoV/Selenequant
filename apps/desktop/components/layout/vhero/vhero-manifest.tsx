"use client";

import { clickable } from "@/lib/a11y/clickable";

interface Props {
  extended: boolean;
  onClick?: () => void;
}

export function VheroManifest({ extended, onClick }: Props) {
  return (
    <div
      className={`vhero-manifest ${extended ? "extended" : ""}`}
      title={extended ? "切换到 BASE manifest" : "切换到 EXTENDED manifest"}
      aria-pressed={extended}
      {...clickable(onClick)}
    >
      <svg viewBox="0 0 12 12" fill="none">
        <path d="M2 6 L10 6 M6 2 L6 10" stroke="currentColor" strokeWidth="1.2" />
        <circle cx="6" cy="6" r="5" stroke="currentColor" strokeWidth="0.8" fill="none" />
      </svg>
      <span className="vm-label">{extended ? "EXT" : "BASE"}</span>
    </div>
  );
}
