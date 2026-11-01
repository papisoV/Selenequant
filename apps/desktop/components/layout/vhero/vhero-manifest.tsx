"use client";

import { clickable } from "@/lib/a11y/clickable";
import { useUiText } from "@/components/i18n/use-ui-text"

interface Props {
  extended: boolean;
  onClick?: () => void;
}

export function VheroManifest({ extended, onClick }: Props) {
  const tr = useUiText()

  return (
    <div
      className={`vhero-manifest ${extended ? "extended" : ""}`}
      title={extended ? tr("切换到 BASE manifest") : tr("切换到 EXTENDED manifest")}
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
